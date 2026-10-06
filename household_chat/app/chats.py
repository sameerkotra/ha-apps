"""Chats and messages: the rules of SPEC §4, §5.2, §15–§17 in one place.

Routers call these with an open connection and an `Outbox`; the outbox's
live events and notifications go out only after the transaction commits
(`flush()`), and never while a DB connection is held.
"""
import json
import re
import sqlite3
import threading
import time
from collections import deque
from datetime import timedelta

from fastapi import HTTPException

from . import config, db, files, settings
from .live import hub

MAX_BODY = 8000
MAX_FILES = 10
MAX_GROUP_MEMBERS = 20
MAX_GROUPS = 100
MAX_PINS = 10
PAGE = 50
PERSONAL_NAME = "My room"
NOT_IN_PERSONAL = "Not possible in your personal room."


# ---------- outbox ----------
class Outbox:
    """Live events and notification jobs, released after commit."""

    def __init__(self):
        self.events: list = []
        self.jobs: list = []

    def event(self, user_ids, name: str, data: dict, event_id: int | None = None) -> None:
        self.events.append((list(user_ids), name, data, event_id))

    def job(self, fn, *args) -> None:
        self.jobs.append((fn, args))

    def flush(self) -> None:
        for uids, name, data, eid in self.events:
            hub.publish(uids, name, data, eid)
        jobs, self.jobs = self.jobs, []
        self.events = []
        if jobs:
            def run():
                for fn, args in jobs:
                    try:
                        fn(*args)
                    except Exception:
                        import logging
                        logging.getLogger("chats").exception("A background job failed")
            threading.Thread(target=run, daemon=True, name="outbox").start()


# ---------- rate limits (SPEC §4: 30 messages and 20 uploads a minute) ----------
class RateLimit:
    def __init__(self, limit: int, window: float = 60):
        self.limit, self.window = limit, window
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()

    def take(self, key: str, n: int = 1) -> None:
        now = time.monotonic()
        with self._lock:
            q = self._hits.setdefault(key, deque())
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) + n > self.limit:
                raise HTTPException(429, "That's a lot at once — wait a moment and try again.")
            q.extend([now] * n)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


message_limit = RateLimit(30)
upload_limit = RateLimit(20)


# ---------- lookups ----------
def user_row(conn, uid: str):
    return conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()


def shown_name(row) -> str:
    if row is None:
        return "Someone"
    return f"{row['name']} (no longer here)" if row["disabled"] else row["name"]


def conv_row(conn, cid: str):
    return conn.execute("SELECT * FROM conversations WHERE id = ?", (cid,)).fetchone()


def member_row(conn, cid: str, uid: str):
    return conn.execute("SELECT * FROM members WHERE conversation_id = ? AND user_id = ?", (cid, uid)).fetchone()


def access(conn, cid: str, user: dict):
    """(conversation, membership) — 404 for anyone who isn't a current member, admins included."""
    conv = conv_row(conn, cid) if isinstance(cid, str) else None
    m = member_row(conn, cid, user["id"]) if conv else None
    if conv is None or m is None:
        raise HTTPException(404, "That chat doesn't exist.")
    return conv, m


def member_ids(conn, cid: str, enabled_only: bool = True) -> list:
    q = ("SELECT m.user_id FROM members m JOIN users u ON u.id = m.user_id WHERE m.conversation_id = ?"
         + (" AND u.disabled = 0" if enabled_only else ""))
    return [r["user_id"] for r in conn.execute(q, (cid,))]


def message_row(conn, mid):
    try:
        mid = int(mid)
    except (TypeError, ValueError):
        return None
    return conn.execute("SELECT * FROM messages WHERE id = ?", (mid,)).fetchone()


def message_access(conn, mid, user: dict):
    """(message, conversation, membership); 404 unless the message is in a chat you're in and visible to you."""
    msg = message_row(conn, mid)
    if msg is None:
        raise HTTPException(404, "That message doesn't exist.")
    conv = conv_row(conn, msg["conversation_id"])
    m = member_row(conn, msg["conversation_id"], user["id"])
    if m is None or msg["id"] <= m["joined_message_id"]:
        raise HTTPException(404, "That message doesn't exist.")
    return msg, conv, m


def is_manager(conv, m, user: dict | None = None) -> bool:
    """Group owner or group admin — never a child account (SPEC §16.5)."""
    if user is not None and user.get("is_child"):
        return False
    return conv["kind"] == "group" and m["role"] in ("owner", "admin")


def require_adult(user: dict, what: str = "do this") -> None:
    if user.get("is_child"):
        raise HTTPException(403, f"Children can't {what}.")


def not_personal(conv) -> None:
    if conv["kind"] == "personal":
        raise HTTPException(409, NOT_IN_PERSONAL)


def other_member(conn, conv, uid: str):
    r = conn.execute("SELECT u.* FROM members m JOIN users u ON u.id = m.user_id WHERE m.conversation_id = ? "
                     "AND m.user_id != ?", (conv["id"], uid)).fetchone()
    return r


def can_post(conn, conv, uid: str) -> bool:
    if conv["kind"] == "direct":
        other = other_member(conn, conv, uid)
        return other is not None and not other["disabled"] and not children_blocked(conn, uid, other["id"])
    return True


def require_post(conn, conv, uid: str) -> None:
    if not can_post(conn, conv, uid):
        raise HTTPException(409, "They no longer have access to Household Chat, so this chat is read-only.")


# ---------- text helpers ----------
_MARKS = re.compile(r"```|[*_~`]")


def strip_marks(text: str) -> str:
    """Message text without formatting marks (for notifications, previews, search results)."""
    t = _MARKS.sub("", text or "")
    t = re.sub(r"^\s*(?:[-•]|\d+\.|>)\s+", "", t, flags=re.M)
    return re.sub(r"\s+", " ", t).strip()


def short(text: str, n: int = 140) -> str:
    text = text or ""
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


# ---------- system messages ----------
def system_text(conn, body: str) -> str:
    try:
        e = json.loads(body)
    except ValueError:
        return ""
    names = {}

    def name(uid):
        if uid not in names:
            names[uid] = shown_name(user_row(conn, uid)) if uid else "Someone"
        return names[uid]
    k = e.get("event")
    a, t = e.get("actor"), e.get("target")
    return {
        "created": f"{name(a)} created the group",
        "added": f"{name(a)} added {name(t)}",
        "joined": f"{name(t)} joined",
        "left": f"{name(t)} left",
        "removed": f"{name(a)} removed {name(t)}",
        "disabled": f"{name(t)} no longer has access",
        "renamed": f"{name(a)} renamed the group to “{e.get('name', '')}”",
        "icon": f"{name(a)} changed the group's icon",
        "description": f"{name(a)} changed the description",
        "owner": f"{name(t)} is now the group's owner",
        "admin_on": f"{name(a)} made {name(t)} a group admin",
        "admin_off": f"{name(t)} is no longer a group admin",
        "pinned": f"{name(a)} pinned a message",
        "unpinned": f"{name(a)} unpinned a message",
        "history": f"{name(a)} changed whether new members see earlier messages",
        "disappear_on": f"{name(a)} turned on disappearing messages: {duration_label(e.get('seconds'))}",
        "disappear_off": f"{name(a)} turned off disappearing messages",
        "photo": f"{name(a)} changed the group photo",
        "photo_removed": f"{name(a)} removed the group photo",
        "folder_added": f"{name(a)} shared the folder “{e.get('label', '')}”",
        "folder_removed": f"The shared folder “{e.get('label', '')}” was removed",
        "folder_files": folder_files_text(e),
    }.get(k, "")


DURATIONS = {3600: "1 hour", 86400: "1 day", 604800: "7 days", 2592000: "30 days"}


def duration_label(seconds) -> str:
    return DURATIONS.get(seconds, f"{seconds} seconds")


def folder_files_text(e: dict) -> str:
    names = e.get("names") or []
    n = e.get("count", len(names))
    shown = ", ".join(names[:5]) + (f" and {n - 5} more" if n > 5 else "")
    return f"{n} new file{'' if n == 1 else 's'} in “{e.get('label', '')}”: {shown}"


def add_system(conn, out: Outbox, conv, event: dict) -> int | None:
    if conv["kind"] == "personal":
        return None
    now = config.now_iso()
    cur = conn.execute("INSERT INTO messages (conversation_id, user_id, kind, body, created_at) VALUES (?, NULL, 'system', ?, ?)",
                       (conv["id"], json.dumps(event), now))
    mid = cur.lastrowid
    conn.execute("UPDATE conversations SET last_message_id = ?, last_activity_at = ? WHERE id = ?", (mid, now, conv["id"]))
    msg = message_row(conn, mid)
    publish_message(conn, out, conv, msg)
    return mid


# ---------- serialisation ----------
def attachment_out(a, viewer_id: str | None = None, heard: set | None = None) -> dict:
    return {"id": a["id"], "name": a["original_name"], "mime": a["mime"], "size": a["size"],
            "originalSize": a["original_size"], "width": a["width"], "height": a["height"],
            "duration": a["duration"], "voice": bool(a["voice"]), "missing": bool(a["missing"]),
            "image": files.is_image(a["mime"]), "uploadedBy": a["uploaded_by"],
            "adminDeleted": bool(a["admin_deleted_at"]),
            "heard": (a["id"] in heard) if heard is not None else None}


def poll_out(conn, mid: int) -> dict | None:
    p = conn.execute("SELECT * FROM polls WHERE message_id = ?", (mid,)).fetchone()
    if p is None:
        return None
    opts = conn.execute("SELECT * FROM poll_options WHERE message_id = ? ORDER BY position", (mid,)).fetchall()
    votes: dict = {}
    for v in conn.execute("SELECT option_id, user_id FROM poll_votes WHERE message_id = ? ORDER BY created_at", (mid,)):
        votes.setdefault(v["option_id"], []).append(v["user_id"])
    closed = p["closed_at"] is not None or poll_expired(p)
    return {"question": p["question"], "multi": bool(p["multi"]), "closesAt": p["closes_at"],
            "closed": closed, "closedAt": p["closed_at"],
            "options": [{"id": o["id"], "text": o["text"], "votes": votes.get(o["id"], [])} for o in opts]}


def poll_expired(p) -> bool:
    t = config.parse_iso(p["closes_at"])
    return t is not None and t <= config.utcnow()


# ---------- cards from other apps (§15.11, APP_MESSAGES_SPEC §6.3) ----------
CARD_TYPES = ("note", "checklist", "sheet", "folder", "file")
CARD_ICONS = {"note": "📝", "checklist": "✅", "sheet": "🧮", "folder": "📁", "file": "📄"}
CARD_WORDS = {"note": "a note", "checklist": "a checklist", "sheet": "a sheet", "folder": "a folder", "file": "a file"}
_APP_RE = re.compile(r"^[a-z0-9_]{1,40}$")
_TARGET_RE = re.compile(r"^/(doc|folder|file)/[A-Za-z0-9_-]{1,64}$")


def panel_ok(panel, app) -> bool:
    """Is `panel` the sending app's own sidebar page, "/<repository hash or local>_<its slug>" (APP_MESSAGES_SPEC
    §6.5)? Nothing else — not /config, not another app's page, no scheme, no "//"."""
    if not isinstance(panel, str) or not isinstance(app, str) or not _APP_RE.match(app):
        return False
    return re.fullmatch(r"/[a-z0-9]{1,16}_" + re.escape(app), panel) is not None


def target_ok(target) -> bool:
    """A Docs route: "/doc/<id>", "/folder/<id>" or "/file/<id>" (ids of A–Z a–z 0–9 _ -)."""
    return isinstance(target, str) and _TARGET_RE.match(target) is not None


def card_link(panel, target, app) -> str | None:
    """The sending app's page path for a card ("/<full slug>/doc/<id>"), or None when it isn't well formed — the
    card then says to open the app from the sidebar."""
    if not panel_ok(panel, app):
        return None
    if not target:
        return panel
    if not target_ok(target):
        return None
    return panel + target


def cards_out(conn, ids) -> dict:
    """{message id: card} for card messages; each card links with its own page path, checked again here (§15.11)."""
    if not ids:
        return {}
    q = ",".join("?" * len(ids))
    rows = conn.execute(f"SELECT * FROM app_cards WHERE message_id IN ({q})", list(ids)).fetchall()
    out = {}
    for r in rows:
        out[r["message_id"]] = {"app": r["app"], "badge": r["badge"], "type": r["item_type"], "itemId": r["item_id"],
                                "title": r["title"], "owner": r["owner_name"],
                                "href": card_link(r["panel"], r["target"], r["app"]),
                                "sharedWithMembers": bool(r["shared_with_members"])}
    return out


def card_row(conn, mid: int):
    return conn.execute("SELECT * FROM app_cards WHERE message_id = ?", (mid,)).fetchone()


def card_icon(conn, msg) -> str:
    c = card_row(conn, msg["id"])
    return CARD_ICONS.get(c["item_type"], "📄") if c else "📄"


def post_card(conn, out: Outbox, conv, user: dict, card: dict, bus_id: str | None = None) -> int:
    """A card another household app asked to post for `user` (checked by app_messages.py like a message
    they type): as their message, in the chat's disappearing setting, live and with notifications."""
    require_post(conn, conv, user["id"])
    mid = insert_message(conn, conv, user["id"], kind="card", body=card["title"],
                         expires_in=resolve_expiry(conv, user, None))
    conn.execute("INSERT INTO app_cards (message_id, app, badge, item_type, item_id, title, owner_name, panel, target, "
                 "shared_with_members, bus_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (mid, card["app"], card["badge"], card["type"], card["item_id"], card["title"], card.get("owner_name"),
                  card.get("panel"), card.get("target"), 1 if card.get("share_with_members") else 0, bus_id,
                  config.now_iso()))
    publish_message(conn, out, conv, message_row(conn, mid))
    if conv["kind"] != "personal":
        from . import notifier
        out.job(notifier.new_message, mid)
    return mid


# ---------- call notes (§15.12) ----------
def call_row(conn, mid: int):
    return conn.execute("SELECT * FROM calls WHERE message_id = ?", (mid,)).fetchone()


def call_seconds(c) -> int | None:
    a, e = config.parse_iso(c["answered_at"]), config.parse_iso(c["ended_at"])
    return max(0, round((e - a).total_seconds())) if a and e else None


def call_text(c) -> str:
    """The note as anyone may see it (previews, exports); the page words it for the caller or the person called."""
    if c is None:
        return "📞 Call"
    return call_label(c["outcome"], call_seconds(c), c["kind"] if "kind" in c.keys() else "audio",
                      bool(c["is_group"]) if "is_group" in c.keys() else False)


def call_label(outcome: str | None, seconds: int | None, kind: str = "audio", group: bool = False) -> str:
    icon = "📹" if kind == "video" else "📞"
    what = ("Group video call" if kind == "video" else "Group call") if group else ("Video call" if kind == "video" else "Call")
    if outcome == "answered":
        secs = seconds or 0
        return f"{icon} {what} · " + (f"{secs // 60} min" if secs >= 60 else f"{secs} s")
    low = what[0].lower() + what[1:]
    return {"missed": f"{icon} Missed {low}", "busy": f"{icon} Missed {low}", "declined": f"{icon} Declined {low}",
            "failed": f"{icon} {what} couldn't connect"}.get(outcome, f"{icon} {what}")


def call_members(conn, call_id: str) -> list:
    return [dict(r) for r in conn.execute("SELECT user_id, state FROM call_members WHERE call_id = ? ORDER BY joined_at", (call_id,))]


def calls_out(conn, ids) -> dict:
    if not ids:
        return {}
    q = ",".join("?" * len(ids))
    out = {}
    for c in conn.execute(f"SELECT * FROM calls WHERE message_id IN ({q})", list(ids)):
        members = call_members(conn, c["id"])
        out[c["message_id"]] = {"id": c["id"], "outcome": c["outcome"], "callerId": c["caller_id"],
                                "calleeId": c["callee_id"] or None, "seconds": call_seconds(c), "startedAt": c["started_at"],
                                "kind": c["kind"], "group": bool(c["is_group"]),
                                "members": [{"id": m["user_id"], "name": shown_name(user_row(conn, m["user_id"])), "state": m["state"]}
                                            for m in members]}
    return out


def messages_out(conn, rows, viewer_id: str | None = None, visible_from: int = 0) -> list:
    """Messages as the page shows them. `viewer_id` adds their own stars/reminders/heard marks;
    `visible_from` hides reply quotes of messages from before they joined."""
    if not rows:
        return []
    ids = [r["id"] for r in rows]
    qmarks = ",".join("?" * len(ids))
    atts: dict = {}
    for a in conn.execute(f"SELECT * FROM attachments WHERE message_id IN ({qmarks}) ORDER BY created_at, rowid", ids):
        atts.setdefault(a["message_id"], []).append(a)
    reacts: dict = {}
    for r in conn.execute(f"SELECT message_id, emoji, user_id FROM reactions WHERE message_id IN ({qmarks}) "
                          f"ORDER BY created_at", ids):
        reacts.setdefault(r["message_id"], {}).setdefault(r["emoji"], []).append(r["user_id"])
    stars, reminders, heard = set(), {}, set()
    if viewer_id:
        stars = {r["message_id"] for r in conn.execute(
            f"SELECT message_id FROM stars WHERE user_id = ? AND message_id IN ({qmarks})", [viewer_id, *ids])}
        for r in conn.execute(f"SELECT id, message_id, due_at FROM reminders WHERE user_id = ? AND done_at IS NULL "
                              f"AND message_id IN ({qmarks}) ORDER BY due_at", [viewer_id, *ids]):
            reminders.setdefault(r["message_id"], {"id": r["id"], "dueAt": r["due_at"]})
        heard = {r["attachment_id"] for r in conn.execute(
            f"SELECT h.attachment_id FROM heard h JOIN attachments a ON a.id = h.attachment_id "
            f"WHERE h.user_id = ? AND a.message_id IN ({qmarks})", [viewer_id, *ids])}
    reply_ids = {r["reply_to"] for r in rows if r["reply_to"]}
    replies = {}
    if reply_ids:
        rq = ",".join("?" * len(reply_ids))
        for r in conn.execute(f"SELECT m.*, (SELECT COUNT(*) FROM attachments a WHERE a.message_id = m.id) AS n "
                              f"FROM messages m WHERE m.id IN ({rq})", list(reply_ids)):
            replies[r["id"]] = r
    cards = cards_out(conn, [r["id"] for r in rows if r["kind"] == "card"])
    calls = calls_out(conn, [r["id"] for r in rows if r["kind"] == "call"])
    acks: dict = {}
    if any(r["announcement"] for r in rows):
        for a in conn.execute(f"SELECT message_id, user_id FROM announcement_acks WHERE message_id IN ({qmarks}) ORDER BY at", ids):
            acks.setdefault(a["message_id"], []).append(a["user_id"])
    users: dict = {}

    def uname(uid):
        if uid not in users:
            users[uid] = shown_name(user_row(conn, uid))
        return users[uid]
    out = []
    for r in rows:
        d = {"id": r["id"], "conversationId": r["conversation_id"], "userId": r["user_id"], "kind": r["kind"],
             "author": uname(r["user_id"]) if r["user_id"] else None,
             "body": r["body"] if r["kind"] != "system" else system_text(conn, r["body"]),
             "createdAt": r["created_at"], "editedAt": r["edited_at"], "deleted": r["deleted_at"] is not None,
             "forwarded": bool(r["forwarded"]), "via": r["via"],
             "mentions": json.loads(r["mentions"]) if r["mentions"] else [], "mentionAll": bool(r["mention_all"]),
             "pinned": r["pinned_at"] is not None, "pinnedBy": r["pinned_by"],
             "attachments": [attachment_out(a, viewer_id, heard if viewer_id else None) for a in atts.get(r["id"], [])],
             "reactions": [{"emoji": e, "userIds": u} for e, u in reacts.get(r["id"], {}).items()],
             "starred": r["id"] in stars, "reminder": reminders.get(r["id"]),
             "replyTo": None, "poll": poll_out(conn, r["id"]) if r["kind"] == "poll" else None,
             "card": cards.get(r["id"]) if r["kind"] == "card" and not r["deleted_at"] else None,
             "call": calls.get(r["id"]) if r["kind"] == "call" and not r["deleted_at"] else None,
             "expiresAt": r["expires_at"],
             "announcement": announcement_out(conn, r, acks.get(r["id"], [])) if r["announcement"] else None}
        if r["reply_gone"] and not r["reply_to"]:
            d["replyTo"] = {"gone": True}
        if r["reply_to"]:
            q = replies.get(r["reply_to"])
            if q is None or q["id"] <= visible_from:
                d["replyTo"] = {"id": r["reply_to"], "hidden": True}
            else:
                d["replyTo"] = {"id": q["id"], "userId": q["user_id"], "author": uname(q["user_id"]) if q["user_id"] else None,
                                "text": "" if q["deleted_at"] else short(q["body"] if q["kind"] == "card" else strip_marks(q["body"]), 140),
                                "deleted": q["deleted_at"] is not None, "files": q["n"], "kind": q["kind"]}
        out.append(d)
    return out


def announcement_out(conn, r, acked: list) -> dict:
    """Who has tapped "Got it" and who hasn't; closed once everyone has, or the sender closed it."""
    everyone = [x["user_id"] for x in conn.execute(
        "SELECT m.user_id FROM members m JOIN users u ON u.id = m.user_id WHERE m.conversation_id = ? AND u.disabled = 0 "
        "AND m.joined_message_id < ? AND m.user_id != ?", (r["conversation_id"], r["id"], r["user_id"] or ""))]
    pending = [u for u in everyone if u not in acked]
    return {"acks": acked, "pending": pending, "closed": bool(r["announcement_closed_at"]) or not pending,
            "reminded": bool(r["announcement_reminded_at"])}


def preview_of(conn, msg) -> str:
    if msg is None:
        return ""
    if msg["kind"] == "system":
        return system_text(conn, msg["body"])
    if msg["deleted_at"]:
        return "Message deleted"
    if msg["kind"] == "poll":
        return "📊 " + short(msg["body"], 100)
    if msg["kind"] == "card":
        return card_icon(conn, msg) + " " + short(msg["body"], 100)
    if msg["kind"] == "call":
        return call_text(call_row(conn, msg["id"]))
    text = short(strip_marks(msg["body"]), 100)
    if text:
        return text
    a = conn.execute("SELECT original_name, voice FROM attachments WHERE message_id = ? LIMIT 1", (msg["id"],)).fetchone()
    if a:
        return "🎤 Voice message" if a["voice"] else "📎 " + a["original_name"]
    return ""


def publish_message(conn, out: Outbox, conv, msg, name: str = "message") -> None:
    """Send a message (new or changed) to every member who may see it, re-checked now."""
    rows = conn.execute("SELECT m.user_id, m.joined_message_id FROM members m JOIN users u ON u.id = m.user_id "
                        "WHERE m.conversation_id = ? AND u.disabled = 0", (conv["id"],)).fetchall()
    buckets: dict = {}
    for r in rows:
        if msg["id"] > r["joined_message_id"]:
            buckets.setdefault(r["joined_message_id"], []).append(r["user_id"])
    for vf, uids in buckets.items():
        data = messages_out(conn, [msg], None, vf)[0]
        out.event(uids, name, data, msg["id"] if name == "message" else None)


# ---------- conversations ----------
def display_name_of(conn, conv, viewer_id: str) -> str:
    if conv["kind"] == "personal":
        return PERSONAL_NAME
    if conv["kind"] == "direct":
        other = other_member(conn, conv, viewer_id)
        return shown_name(other) if other else "Direct chat"
    return conv["name"] or "Group"


def members_out(conn, cid: str) -> list:
    rows = conn.execute("SELECT m.*, u.name, u.disabled, u.avatar_file, u.avatar_version, u.is_child FROM members m JOIN users u ON u.id = m.user_id "
                        "WHERE m.conversation_id = ? ORDER BY CASE m.role WHEN 'owner' THEN 0 WHEN 'admin' THEN 1 ELSE 2 END, "
                        "u.name COLLATE NOCASE", (cid,)).fetchall()
    return [{"id": r["user_id"], "name": shown_name(r), "role": r["role"], "disabled": bool(r["disabled"]),
             "avatar": r["avatar_version"] if r["avatar_file"] else None, "lastReadId": r["last_read_id"], "isChild": bool(r["is_child"])} for r in rows]


def unread_counts(conn, cid: str, m, uid: str) -> tuple[int, int]:
    # a call note is unread only when it's a call you missed (§15.12)
    base = ("FROM messages WHERE conversation_id = ? AND id > ? AND id > ? AND deleted_at IS NULL "
            "AND kind != 'system' AND (user_id IS NULL OR user_id != ?) "
            "AND (kind != 'call' OR id IN (SELECT c.message_id FROM calls c JOIN call_members cm ON cm.call_id = c.id "
            "WHERE cm.user_id = ? AND cm.state IN ('missed', 'busy')))")
    args = (cid, m["last_read_id"], m["joined_message_id"], uid, uid)
    n = conn.execute("SELECT COUNT(*) " + base, args).fetchone()[0]
    if not n:
        return 0, 0
    at = conn.execute("SELECT COUNT(*) " + base + " AND (mention_all = 1 OR instr(COALESCE(mentions, ''), ?) > 0)",
                      (*args, f'"{uid}"')).fetchone()[0]
    return n, at


def conversation_out(conn, conv, m, uid: str) -> dict:
    last = message_row(conn, conv["last_message_id"]) if conv["last_message_id"] else None
    if last is not None and last["id"] <= m["joined_message_id"]:
        last = None
    unread, at = (0, 0) if conv["kind"] == "personal" else unread_counts(conn, conv["id"], m, uid)
    muted_until = config.parse_iso(m["muted_until"])
    other = other_member(conn, conv, uid) if conv["kind"] == "direct" else None
    return {
        "id": conv["id"], "kind": conv["kind"], "name": display_name_of(conn, conv, uid),
        "groupName": conv["name"], "icon": conv["icon"], "description": conv["description"] or "",
        "isHousehold": bool(conv["is_household"]), "otherUserId": other["id"] if other else None,
        "role": m["role"], "readOnly": not can_post(conn, conv, uid),
        "lastMessage": {"id": last["id"], "preview": preview_of(conn, last), "at": last["created_at"],
                        "by": last["user_id"], "author": shown_name(user_row(conn, last["user_id"])) if last["user_id"] else None}
        if last else None,
        "lastActivityAt": conv["last_activity_at"] or conv["created_at"],
        "unread": unread, "mentionUnread": at, "lastReadId": m["last_read_id"],
        "notify": m["notify"], "mutedUntil": m["muted_until"],
        "muted": bool(muted_until and muted_until > config.utcnow()) or m["notify"] == "off",
        "pinned": bool(m["pinned"]), "newMembersSeeHistory": bool(conv["new_members_see_history"]),
        "memberCount": conn.execute("SELECT COUNT(*) FROM members WHERE conversation_id = ?", (conv["id"],)).fetchone()[0],
        "disappearSeconds": conv["disappear_seconds"],
        "draft": (lambda d: {"body": d["body"], "updatedAt": d["updated_at"]} if d else None)(
            conn.execute("SELECT body, updated_at FROM drafts WHERE user_id = ? AND conversation_id = ?", (uid, conv["id"])).fetchone()),
    }


def list_conversations(conn, uid: str) -> list:
    rows = conn.execute("SELECT c.*, m.role, m.joined_message_id, m.last_read_id, m.notify, m.muted_until, m.pinned, "
                        "m.user_id, m.joined_at, m.conversation_id FROM members m JOIN conversations c ON c.id = m.conversation_id "
                        "WHERE m.user_id = ?", (uid,)).fetchall()
    out = []
    for r in rows:
        if r["kind"] == "direct" and r["last_message_id"] is None and r["created_by"] != uid:
            continue            # made on "open": the other person sees it once there's a message
        out.append(conversation_out(conn, r, r, uid))
    out.sort(key=lambda c: (c["kind"] != "personal", not c["pinned"], _neg(c["lastActivityAt"])))
    return out


def _neg(ts: str) -> str:
    return "".join(chr(0x10FFFF - ord(ch)) for ch in (ts or ""))


def conversation_members_event(conn, out: Outbox, conv, extra_uids=()) -> None:
    """Tell members (and anyone who just left) that the chat's details changed."""
    uids = set(member_ids(conn, conv["id"])) | set(extra_uids)
    out.event(uids, "conversation", {"id": conv["id"]})


# ---------- creating chats ----------
def ensure_personal(conn, uid: str) -> str:
    r = conn.execute("SELECT id FROM conversations WHERE kind = 'personal' AND created_by = ?", (uid,)).fetchone()
    if r:
        if not member_row(conn, r["id"], uid):
            conn.execute("INSERT INTO members (conversation_id, user_id, role, joined_at) VALUES (?, ?, 'owner', ?)",
                         (r["id"], uid, config.now_iso()))
        return r["id"]
    cid = db.new_id()
    now = config.now_iso()
    conn.execute("INSERT INTO conversations (id, kind, folder, created_by, created_at, last_activity_at) "
                 "VALUES (?, 'personal', '', ?, ?, ?)", (cid, uid, now, now))
    conn.execute("INSERT INTO members (conversation_id, user_id, role, joined_at) VALUES (?, ?, 'owner', ?)", (cid, uid, now))
    conn.execute("UPDATE conversations SET folder = ? WHERE id = ?", (files.folder_name(conn, conv_row(conn, cid)), cid))
    return cid


def household(conn):
    return conn.execute("SELECT * FROM conversations WHERE is_household = 1").fetchone()


def _new_group(conn, creator: str, name: str, icon: str | None, description: str | None, is_household=False) -> str:
    cid = db.new_id()
    now = config.now_iso()
    conn.execute("INSERT INTO conversations (id, kind, name, icon, description, folder, created_by, created_at, "
                 "last_activity_at, is_household) VALUES (?, 'group', ?, ?, ?, '', ?, ?, ?, ?)",
                 (cid, name, icon, description, creator, now, now, 1 if is_household else 0))
    conn.execute("INSERT INTO members (conversation_id, user_id, role, joined_at) VALUES (?, ?, 'owner', ?)", (cid, creator, now))
    conn.execute("UPDATE conversations SET folder = ? WHERE id = ?", (files.folder_name(conn, conv_row(conn, cid)), cid))
    return cid


def join(conn, out: Outbox, conv, uid: str, role: str = "member", actor: str | None = None) -> None:
    if member_row(conn, conv["id"], uid):
        return
    joined_from = 0 if conv["new_members_see_history"] else (conv["last_message_id"] or 0)
    conn.execute("INSERT INTO members (conversation_id, user_id, role, joined_at, joined_message_id, last_read_id) "
                 "VALUES (?, ?, ?, ?, ?, ?)", (conv["id"], uid, role, config.now_iso(), joined_from, conv["last_message_id"] or 0))
    add_system(conn, out, conv, {"event": "added" if actor and actor != uid else "joined", "actor": actor, "target": uid})
    conversation_members_event(conn, out, conv_row(conn, conv["id"]), [uid])


def enable_user(conn, out: Outbox, uid: str, actor: str) -> None:
    """Enabled: personal room, and the Household group (made with the first enabled person)."""
    conn.execute("UPDATE users SET disabled = 0 WHERE id = ?", (uid,))
    ensure_personal(conn, uid)
    hh = household(conn)
    if hh is None:
        cid = _new_group(conn, uid, "Household", "🏠", None, is_household=True)
        add_system(conn, out, conv_row(conn, cid), {"event": "created", "actor": uid})
    elif not member_row(conn, hh["id"], uid):
        join(conn, out, hh, uid, "member")
    for r in conn.execute("SELECT c.* FROM conversations c JOIN members m ON m.conversation_id = c.id "
                          "WHERE m.user_id = ? AND c.kind = 'direct'", (uid,)).fetchall():
        conversation_members_event(conn, out, r)
    db.audit(conn, "person_enabled", uid, None, actor)


def disable_user(conn, out: Outbox, uid: str, actor: str) -> None:
    """No access at once; out of every group (owners hand over); direct chats become read-only; rooms kept."""
    conn.execute("UPDATE users SET disabled = 1 WHERE id = ?", (uid,))
    for r in conn.execute("SELECT c.* FROM conversations c JOIN members m ON m.conversation_id = c.id "
                          "WHERE m.user_id = ? AND c.kind = 'group'", (uid,)).fetchall():
        remove_member(conn, out, r, uid, "disabled", actor)
    for r in conn.execute("SELECT c.* FROM conversations c JOIN members m ON m.conversation_id = c.id "
                          "WHERE m.user_id = ? AND c.kind = 'direct'", (uid,)).fetchall():
        conversation_members_event(conn, out, r)
    db.audit(conn, "person_disabled", uid, None, actor)
    hub.close_user(uid)
    from . import calls
    out.job(calls.end_for_user, uid, "failed")


def set_child(conn, out: Outbox, uid: str, child: bool, actor: str) -> None:
    """Child accounts (§16.5): a child holds no group role, so owner/admin roles pass to adults."""
    conn.execute("UPDATE users SET is_child = ? WHERE id = ?", (1 if child else 0, uid))
    if child:
        for r in conn.execute("SELECT c.*, m.role AS my_role FROM conversations c JOIN members m ON m.conversation_id = c.id "
                              "WHERE m.user_id = ? AND c.kind = 'group' AND m.role IN ('owner','admin')", (uid,)).fetchall():
            conn.execute("UPDATE members SET role = 'member' WHERE conversation_id = ? AND user_id = ?", (r["id"], uid))
            if r["my_role"] == "owner":
                hand_over(conn, out, r)
            conversation_members_event(conn, out, r)
    db.audit(conn, "child_on" if child else "child_off", uid, None, actor)
    hub.publish([uid], "settings", {})


def create_group(conn, out: Outbox, creator: dict, name: str, icon: str | None, member_ids_in: list,
                 description: str | None) -> str:
    require_adult(creator, "create groups")
    if settings.get("who_can_create_groups", conn) == "admins" and not creator["is_admin"]:
        raise HTTPException(403, "Only admins can create groups.")
    if conn.execute("SELECT COUNT(*) FROM conversations WHERE kind = 'group'").fetchone()[0] >= MAX_GROUPS:
        raise HTTPException(409, f"There are already {MAX_GROUPS} groups.")
    ids = [u for u in dict.fromkeys(member_ids_in) if u != creator["id"]]
    if len(ids) + 1 > MAX_GROUP_MEMBERS:
        raise HTTPException(422, f"A group can have at most {MAX_GROUP_MEMBERS} people.")
    for u in ids:
        r = user_row(conn, u)
        if r is None or r["disabled"]:
            raise HTTPException(422, "Everyone in a group must have access to Household Chat.")
    cid = _new_group(conn, creator["id"], name, icon, description)
    conv = conv_row(conn, cid)
    add_system(conn, out, conv, {"event": "created", "actor": creator["id"]})
    for u in ids:
        now = config.now_iso()
        conn.execute("INSERT INTO members (conversation_id, user_id, role, joined_at) VALUES (?, ?, 'member', ?)", (cid, u, now))
    conversation_members_event(conn, out, conv)
    return cid


def children_blocked(conn, a_id: str, b_id: str) -> bool:
    """Two children may only message each other directly if an admin allows it (SPEC §16.5)."""
    a, b = user_row(conn, a_id), user_row(conn, b_id)
    return bool(a and b and a["is_child"] and b["is_child"] and not settings.get("children_can_message_each_other", conn))


def get_or_create_direct(conn, me: str, other_id: str) -> str:
    if other_id == me:
        return ensure_personal(conn, me)
    other = user_row(conn, other_id)
    if other is None or other["disabled"]:
        raise HTTPException(404, "That person doesn't have access to Household Chat.")
    if children_blocked(conn, me, other_id):
        raise HTTPException(403, "Children can't start direct chats with each other.")
    key = "|".join(sorted([me, other_id]))
    r = conn.execute("SELECT id FROM conversations WHERE direct_key = ?", (key,)).fetchone()
    if r:
        for u in (me, other_id):
            if not member_row(conn, r["id"], u):
                conn.execute("INSERT INTO members (conversation_id, user_id, role, joined_at) VALUES (?, ?, 'member', ?)",
                             (r["id"], u, config.now_iso()))
        return r["id"]
    cid = db.new_id()
    now = config.now_iso()
    try:
        conn.execute("INSERT INTO conversations (id, kind, direct_key, folder, created_by, created_at, last_activity_at) "
                     "VALUES (?, 'direct', ?, '', ?, ?, ?)", (cid, key, me, now, now))
    except sqlite3.IntegrityError:          # both opened it at the same moment
        return conn.execute("SELECT id FROM conversations WHERE direct_key = ?", (key,)).fetchone()["id"]
    for u in (me, other_id):
        conn.execute("INSERT INTO members (conversation_id, user_id, role, joined_at) VALUES (?, ?, 'member', ?)", (cid, u, now))
    conn.execute("UPDATE conversations SET folder = ? WHERE id = ?", (files.folder_name(conn, conv_row(conn, cid)), cid))
    return cid


# ---------- membership changes ----------
def hand_over(conn, out: Outbox, conv) -> None:
    """The oldest group admin becomes owner, else the oldest member."""
    if conn.execute("SELECT 1 FROM members WHERE conversation_id = ? AND role = 'owner'", (conv["id"],)).fetchone():
        return
    r = conn.execute("SELECT m.user_id FROM members m JOIN users u ON u.id = m.user_id WHERE m.conversation_id = ? "
                     "AND u.disabled = 0 ORDER BY u.is_child, CASE m.role WHEN 'admin' THEN 0 ELSE 1 END, m.joined_at LIMIT 1",
                     (conv["id"],)).fetchone()
    if r:
        conn.execute("UPDATE members SET role = 'owner' WHERE conversation_id = ? AND user_id = ?", (conv["id"], r["user_id"]))
        add_system(conn, out, conv, {"event": "owner", "target": r["user_id"]})


def remove_member(conn, out: Outbox, conv, uid: str, how: str, actor: str | None) -> None:
    """how: left / removed / disabled. Their stars and reminders in this chat go too."""
    m = member_row(conn, conv["id"], uid)
    if m is None:
        return
    conn.execute("DELETE FROM members WHERE conversation_id = ? AND user_id = ?", (conv["id"], uid))
    conn.execute("DELETE FROM stars WHERE user_id = ? AND message_id IN (SELECT id FROM messages WHERE conversation_id = ?)",
                 (uid, conv["id"]))
    conn.execute("DELETE FROM reminders WHERE user_id = ? AND message_id IN (SELECT id FROM messages WHERE conversation_id = ?)",
                 (uid, conv["id"]))
    conn.execute("DELETE FROM notify_tokens WHERE user_id = ? AND conversation_id = ?", (uid, conv["id"]))
    from . import notifier
    notifier.drop_held(uid, conv["id"])
    add_system(conn, out, conv, {"event": how, "actor": actor, "target": uid})
    if m["role"] == "owner":
        hand_over(conn, out, conv)
    out.event([uid], "conversation", {"id": conv["id"], "removed": True})
    conversation_members_event(conn, out, conv)


# ---------- posting ----------
def _mentions(conn, conv, uid: str, body: str, requested) -> tuple[list, bool]:
    if conv["kind"] == "personal":
        return [], False
    ids = set(member_ids(conn, conv["id"])) - {uid}
    got = [u for u in dict.fromkeys(requested or []) if isinstance(u, str) and u in ids]
    everyone = conv["kind"] == "group" and "@everyone" in (body or "").lower()
    return got, everyone


def resolve_expiry(conv, user: dict, expires_in) -> int | None:
    """Seconds until a new message disappears, or None (SPEC §15.8). `expires_in`: None = the chat's
    setting, 0 = never (not allowed in a disappearing chat), or one of the four durations."""
    chat = conv["disappear_seconds"]
    if expires_in is None:
        return chat
    if user.get("is_child"):
        raise HTTPException(403, "Children can't send disappearing messages of their own.")
    if expires_in == 0:
        if chat:
            raise HTTPException(422, "Messages in this chat always disappear — choose a time.")
        return None
    if expires_in not in DURATIONS:
        raise HTTPException(422, "Disappear after 1 hour, 1 day, 7 days or 30 days.")
    return expires_in


def can_announce(conn, conv, m, user: dict) -> bool:
    if conv["kind"] == "personal" or user.get("is_child"):
        return False
    if user.get("is_admin"):
        return True
    return settings.get("who_can_announce", conn) == "admins_and_group_admins" and is_manager(conv, m, user)


def insert_message(conn, conv, uid: str, *, kind: str, body: str, reply_to=None, mentions=None, mention_all=False,
                   forwarded=False, via=None, expires_in: int | None = None, announcement: bool = False) -> int:
    now = config.now_iso()
    expires_at = config.iso(config.utcnow() + timedelta(seconds=expires_in)) if expires_in else None
    cur = conn.execute(
        "INSERT INTO messages (conversation_id, user_id, kind, body, reply_to, mentions, mention_all, forwarded, via, created_at, "
        "expires_at, announcement) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (conv["id"], uid, kind, body, reply_to, json.dumps(mentions) if mentions else None, 1 if mention_all else 0,
         1 if forwarded else 0, via, now, expires_at, 1 if announcement else 0))
    mid = cur.lastrowid
    conn.execute("UPDATE conversations SET last_message_id = ?, last_activity_at = ? WHERE id = ?", (mid, now, conv["id"]))
    # your own message is read by you
    conn.execute("UPDATE members SET last_read_id = ? WHERE conversation_id = ? AND user_id = ? AND last_read_id < ?",
                 (mid, conv["id"], uid, mid))
    return mid


def post_message(conn, out: Outbox, conv, m, user: dict, body: str, reply_to=None, attachment_ids=(), mentions=None,
                 via=None, expires_in=None, announcement: bool = False, scheduled_id: str | None = None) -> int:
    body = (body or "").strip("\n")
    if len(body) > MAX_BODY:
        raise HTTPException(422, f"A message can be at most {MAX_BODY} characters.")
    attachment_ids = list(dict.fromkeys(attachment_ids or []))
    if len(attachment_ids) > MAX_FILES:
        raise HTTPException(422, f"At most {MAX_FILES} files per message.")
    if not body.strip() and not attachment_ids:
        raise HTTPException(422, "Type a message or add a file.")
    require_post(conn, conv, user["id"])
    rt = None
    if reply_to is not None:
        q = message_row(conn, reply_to)
        if q is None or q["conversation_id"] != conv["id"] or q["id"] <= m["joined_message_id"] or q["kind"] == "system":
            raise HTTPException(422, "You can only reply to a message in this chat.")
        rt = q["id"]
    atts = []
    for aid in attachment_ids:
        a = conn.execute("SELECT * FROM attachments WHERE id = ?", (aid,)).fetchone()
        if (a is None or a["message_id"] is not None or a["conversation_id"] != conv["id"] or a["uploaded_by"] != user["id"]
                or (a["scheduled_id"] or None) != scheduled_id):
            raise HTTPException(422, "A file wasn't uploaded to this chat (or was already sent). Add it again.")
        atts.append(a)
    if announcement and not can_announce(conn, conv, m, user):
        raise HTTPException(403, "You can't post announcements here.")
    secs = resolve_expiry(conv, user, expires_in)
    ment, everyone = _mentions(conn, conv, user["id"], body, mentions)
    mid = insert_message(conn, conv, user["id"], kind="file" if atts else "text", body=body, reply_to=rt,
                         mentions=ment, mention_all=everyone, via=via, expires_in=secs, announcement=announcement)
    expires_at = message_row(conn, mid)["expires_at"]
    for a in atts:
        conn.execute("UPDATE attachments SET message_id = ?, scheduled_id = NULL WHERE id = ?", (mid, a["id"]))
        if expires_at:
            files.move_to_disappearing(conn, a["id"], conv, expires_at)
    conn.execute("DELETE FROM drafts WHERE user_id = ? AND conversation_id = ?", (user["id"], conv["id"]))
    msg = message_row(conn, mid)
    publish_message(conn, out, conv, msg)
    if conv["kind"] != "personal":
        from . import notifier
        out.job(notifier.new_message, mid)
    return mid


def check_poll(question: str, options: list, closes_at: str | None) -> tuple:
    q = " ".join((question or "").split())
    opts = [" ".join((o or "").split()) for o in options]
    if not q or len(q) > 200:
        raise HTTPException(422, "The question is 1–200 characters.")
    if not 2 <= len(opts) <= 10 or any(not o or len(o) > 100 for o in opts):
        raise HTTPException(422, "A poll has 2–10 options of 1–100 characters.")
    if len({o.lower() for o in opts}) != len(opts):
        raise HTTPException(422, "Two options are the same.")
    closes = None
    if closes_at:
        t = config.parse_iso(closes_at)
        if t is None or t <= config.utcnow() + timedelta(minutes=1) or t > config.utcnow() + timedelta(days=366):
            raise HTTPException(422, "Close the poll at a time in the future (within a year).")
        closes = config.iso(t)
    return q, opts, closes


def create_poll(conn, out: Outbox, conv, user: dict, q: str, opts: list, multi: bool, closes: str | None,
                expires_in=None, announcement: bool = False) -> int:
    not_personal(conv)
    require_post(conn, conv, user["id"])
    m = member_row(conn, conv["id"], user["id"])
    if announcement and not can_announce(conn, conv, m, user):
        raise HTTPException(403, "You can't post announcements here.")
    mid = insert_message(conn, conv, user["id"], kind="poll", body=q, expires_in=resolve_expiry(conv, user, expires_in),
                         announcement=announcement)
    conn.execute("INSERT INTO polls (message_id, question, multi, closes_at) VALUES (?, ?, ?, ?)",
                 (mid, q, 1 if multi else 0, closes))
    for i, o in enumerate(opts):
        conn.execute("INSERT INTO poll_options (message_id, position, text) VALUES (?, ?, ?)", (mid, i, o))
    publish_message(conn, out, conv, message_row(conn, mid))
    from . import notifier
    out.job(notifier.new_message, mid)
    return mid


def edit_message(conn, out: Outbox, msg, conv, user: dict, body: str, mentions=None) -> None:
    if msg["user_id"] != user["id"] or msg["kind"] not in ("text", "file"):
        raise HTTPException(403, "Only the person who wrote a message can edit it.")
    if msg["deleted_at"]:
        raise HTTPException(409, "That message was deleted.")
    window = settings.get("edit_window_minutes", conn)
    sent = config.parse_iso(msg["created_at"])
    if window == 0 or sent is None or (config.utcnow() - sent).total_seconds() > window * 60:
        raise HTTPException(409, "It's too late to edit that message." if window else "Messages can't be edited.")
    body = (body or "").strip("\n")
    if len(body) > MAX_BODY:
        raise HTTPException(422, f"A message can be at most {MAX_BODY} characters.")
    has_files = conn.execute("SELECT 1 FROM attachments WHERE message_id = ?", (msg["id"],)).fetchone()
    if not body.strip() and not has_files:
        raise HTTPException(422, "A message can't be empty — delete it instead.")
    require_post(conn, conv, user["id"])
    ment, everyone = _mentions(conn, conv, user["id"], body, mentions)
    conn.execute("UPDATE messages SET body = ?, mentions = ?, mention_all = ?, edited_at = ? WHERE id = ?",
                 (body, json.dumps(ment) if ment else None, 1 if everyone else 0, config.now_iso(), msg["id"]))
    publish_message(conn, out, conv, message_row(conn, msg["id"]), "message_updated")


def delete_message(conn, out: Outbox, msg, conv, m, user: dict) -> None:
    if msg["kind"] == "system":
        raise HTTPException(403, "System messages can't be deleted.")
    if msg["deleted_at"]:
        return
    if msg["user_id"] != user["id"] and not is_manager(conv, m, user):
        raise HTTPException(403, "You can delete your own messages; group owners and group admins can delete any.")
    for a in conn.execute("SELECT * FROM attachments WHERE message_id = ?", (msg["id"],)).fetchall():
        if files.DISAPPEARING in a["rel_path"].split("/"):
            files.remove_now(a["rel_path"])          # a disappearing file never goes to _deleted
        else:
            files.move_to_deleted(a["rel_path"])
        files.remove_thumb(a["id"])
    conn.execute("DELETE FROM attachments WHERE message_id = ?", (msg["id"],))
    for t in ("reactions", "stars", "reminders", "poll_votes", "poll_options", "polls", "app_cards", "calls"):
        conn.execute(f"DELETE FROM {t} WHERE message_id = ?", (msg["id"],))
    conn.execute("UPDATE messages SET body = '', mentions = NULL, mention_all = 0, deleted_at = ?, pinned_at = NULL, "
                 "pinned_by = NULL WHERE id = ?", (config.now_iso(), msg["id"]))
    publish_message(conn, out, conv, message_row(conn, msg["id"]), "message_updated")


# ---------- reading ----------
def page(conn, conv, m, viewer_id: str, before=None, after=None, around=None, limit: int = PAGE) -> dict:
    vf = m["joined_message_id"]
    base = "SELECT * FROM messages WHERE conversation_id = ? AND id > ?"
    if around is not None:
        older = conn.execute(base + " AND id <= ? ORDER BY id DESC LIMIT ?", (conv["id"], vf, int(around), limit // 2 + 1)).fetchall()
        newer = conn.execute(base + " AND id > ? ORDER BY id ASC LIMIT ?", (conv["id"], vf, int(around), limit // 2)).fetchall()
        rows = list(reversed(older)) + list(newer)
        more_before = len(older) > limit // 2
        more_after = len(newer) >= limit // 2
    elif after is not None:
        rows = conn.execute(base + " AND id > ? ORDER BY id ASC LIMIT ?", (conv["id"], vf, int(after), limit + 1)).fetchall()
        more_after = len(rows) > limit
        rows = rows[:limit]
        more_before = True
    else:
        q = base + (" AND id < ?" if before is not None else "") + " ORDER BY id DESC LIMIT ?"
        args = (conv["id"], vf, int(before), limit + 1) if before is not None else (conv["id"], vf, limit + 1)
        rows = conn.execute(q, args).fetchall()
        more_before = len(rows) > limit
        rows = list(reversed(rows[:limit]))
        more_after = False
    reads = [] if conv["kind"] == "personal" else [
        {"userId": r["user_id"], "lastReadId": r["last_read_id"]}
        for r in conn.execute("SELECT m.user_id, m.last_read_id FROM members m JOIN users u ON u.id = m.user_id "
                              "WHERE m.conversation_id = ? AND u.disabled = 0", (conv["id"],))]
    return {"messages": messages_out(conn, rows, viewer_id, vf), "moreBefore": more_before, "moreAfter": more_after,
            "reads": reads}


def mark_read(conn, out: Outbox, conv, m, uid: str, up_to: int) -> int:
    up_to = min(int(up_to), conv["last_message_id"] or 0)
    if up_to <= m["last_read_id"]:
        return m["last_read_id"]
    conn.execute("UPDATE members SET last_read_id = ? WHERE conversation_id = ? AND user_id = ?", (up_to, conv["id"], uid))
    if conv["kind"] != "personal":
        out.event(member_ids(conn, conv["id"]), "read", {"conversationId": conv["id"], "userId": uid, "lastReadId": up_to})
        from . import notifier
        notifier.cancel_held(uid, conv["id"], up_to)
    out.event([uid], "unread", {"conversationId": conv["id"]})
    return up_to
