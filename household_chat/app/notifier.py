"""Phone notifications (SPEC §7, §15.1): who is told what, batching, quiet
hours, "already looking", preview levels, and the Reply / Mark as read buttons.

Everything here runs in a background thread after the message is committed.
It reads what it needs, closes the connection, then calls Home Assistant
(never while holding a connection). Held pushes (batching, quiet hours) are
kept in memory and saved to the database (app_settings `notify_held`) on every
housekeeping tick that changed them and when the app stops, so a restart in
someone's quiet hours doesn't lose their summary; `flush()` runs from the
housekeeping loop.
"""
import json
import logging
import secrets
import threading
import time
from datetime import timedelta

from . import chats, config, db, settings
from .common import ha_notify
from .live import hub

logger = logging.getLogger("notifier")
BATCH_SECONDS = 60
TOKEN_HOURS = 24
PREVIEW_ORDER = {"none": 0, "sender": 1, "full": 2}

_lock = threading.Lock()
_last_sent: dict = {}      # (user, chat) → monotonic time of the last push
_held: dict = {}           # (user, chat) → {"ids": [...], "until": monotonic or None (quiet), "quiet": bool}
_changed = threading.Event()           # _held changed since it was last saved
HELD_KEY = "notify_held"
MAX_HELD_IDS = 500                     # per person and chat (a summary only counts them)

deliver = ha_notify.send_to_services     # tests replace this


def reset() -> None:
    with _lock:
        _last_sent.clear()
        _held.clear()
    _changed.clear()


def _hold(key, mid: int, quiet: bool, until=None) -> None:
    """Add a message to what's held for (user, chat). Call with _lock held."""
    h = _held.setdefault(key, {"ids": [], "until": until, "quiet": quiet})
    if quiet:
        h["quiet"] = True
    h["ids"].append(mid)
    del h["ids"][:-MAX_HELD_IDS]
    _changed.set()


def save_held(force: bool = False) -> None:
    """What's held, into the database (times as wall-clock, so they mean the same after a restart)."""
    if not force and not _changed.is_set():
        return
    with _lock:
        _changed.clear()
        now_m, now_w = time.monotonic(), time.time()
        data = [[u, c, list(h["ids"]), None if h["until"] is None else now_w + (h["until"] - now_m), bool(h["quiet"])]
                for (u, c), h in _held.items()]
    try:
        with db.get_conn() as conn:
            db.set_setting(conn, HELD_KEY, json.dumps(data))
    except Exception:
        _changed.set()
        logger.exception("Saving the held notifications failed")


def load_held() -> int:
    """At start-up: what was held when the app stopped. → how many chats' worth."""
    with db.get_conn() as conn:
        raw = db.get_setting(conn, HELD_KEY)
    try:
        data = json.loads(raw) if raw else []
    except ValueError:
        data = []
    n = 0
    now_m, now_w = time.monotonic(), time.time()
    with _lock:
        for item in data if isinstance(data, list) else []:
            if not (isinstance(item, list) and len(item) == 5 and all(isinstance(x, str) for x in item[:2])
                    and isinstance(item[2], list) and all(isinstance(i, int) for i in item[2])):
                continue
            until = item[3] if isinstance(item[3], (int, float)) else None
            _held[(item[0], item[1])] = {"ids": item[2][-MAX_HELD_IDS:], "quiet": bool(item[4]),
                                         "until": None if until is None else now_m + (until - now_w)}
            n += 1
    return n


def forget_held() -> None:
    """After a restore: what was held belongs to the database that was replaced."""
    reset()
    with db.get_conn() as conn:
        conn.execute("DELETE FROM app_settings WHERE key = ?", (HELD_KEY,))


# ---------- rules ----------
def in_quiet_hours(user, now=None) -> bool:
    start, end = user["quiet_start"], user["quiet_end"]
    if not start or not end or start == end:
        return False
    t = (now or config.local_now()).strftime("%H:%M")
    return start <= t < end if start < end else (t >= start or t < end)


def wants(conn, user, m, conv, msg) -> bool:
    """Level, per-chat override and mute (SPEC §7 rule 2)."""
    if msg["announcement"]:
        # announcements reach everyone except people whose level is off, even through a mute (§15.7)
        return user["notify_level"] != "off"
    muted = config.parse_iso(m["muted_until"])
    if m["notify"] == "off" or (muted and muted > config.utcnow()):
        return False
    if msg["kind"] == "call":           # a missed call is for you, like a direct message (§15.12)
        return user["notify_level"] != "off"
    uid = user["id"]
    mentioned = bool(msg["mention_all"]) or (msg["mentions"] and uid in json.loads(msg["mentions"]))
    direct = conv["kind"] == "direct"
    reply_to_mine = False
    if msg["reply_to"]:
        q = chats.message_row(conn, msg["reply_to"])
        reply_to_mine = q is not None and q["user_id"] == uid
    if m["notify"] == "all":
        return True
    if m["notify"] == "mentions":
        return direct or mentioned or reply_to_mine
    level = user["notify_level"]
    if level == "all":
        return True
    if level == "direct_mentions":
        return direct or mentioned or reply_to_mine
    return False


def preview_level(user, conn=None) -> str:
    app = settings.get("notify_preview", conn)
    mine = user["notify_preview"]
    return app if PREVIEW_ORDER[app] < PREVIEW_ORDER[mine] else mine


def title_for(conn, conv, sender_name: str) -> str:
    if conv["kind"] == "direct":
        return sender_name
    return conv["name"] or "Group"


def text_for(conn, msg, level: str, sender_name: str, group: bool) -> str:
    if msg["kind"] == "call" and level != "none":     # only missed calls are sent (§15.12)
        c = chats.call_row(conn, msg["id"])
        icon = "📹" if c and c["kind"] == "video" else "📞"
        what = "video call" if c and c["kind"] == "video" else "call"
        return f"{icon} Missed group {what} from {sender_name}" if c and c["is_group"] else f"{icon} Missed {what} from {sender_name}"
    if level == "none":
        return f"New message in {config.APP_TITLE}"
    if level == "sender":
        return f"New message from {sender_name}"
    if msg["expires_at"]:
        return f"{sender_name} sent a disappearing message"
    if msg["announcement"]:
        return f"📢 {sender_name}: {chats.short(chats.strip_marks(msg['body']), 200) or 'Announcement'}"
    if msg["kind"] == "poll":
        body = f"started a poll: {chats.short(msg['body'], 150)}"
        return f"{sender_name} {body}"
    if msg["kind"] == "card":           # shared from another app (§15.11): the title only, never content
        c = chats.card_row(conn, msg["id"])
        what = chats.CARD_WORDS.get(c["item_type"], "a document") if c else "a document"
        return f"{sender_name} shared {what} “{chats.short(msg['body'], 150)}”"
    body = chats.short(chats.strip_marks(msg["body"]), 200)
    if not body:
        a = conn.execute("SELECT original_name, voice FROM attachments WHERE message_id = ? LIMIT 1", (msg["id"],)).fetchone()
        body = ("🎤 Voice message" if a["voice"] else f"📎 {a['original_name']}") if a else "Message"
    return f"{sender_name}: {body}" if group else body


def new_token(conn, uid: str, cid: str) -> str:
    token = secrets.token_hex(16)
    conn.execute("INSERT INTO notify_tokens (token, user_id, conversation_id, created_at) VALUES (?, ?, ?, ?)",
                 (token, uid, cid, config.now_iso()))
    return token


def push_data(conn, uid: str, cid: str) -> dict:
    link = config.chat_link(cid)
    data = {"url": link, "clickAction": link, "tag": f"hchat_{cid}", "group": "household_chat"}
    if settings.get("notification_reply", conn):
        t = new_token(conn, uid, cid)
        data["actions"] = [
            {"action": f"HCHAT_REPLY_{t}", "title": "Reply", "behavior": "textInput",
             "textInputButtonTitle": "Send", "textInputPlaceholder": "Message"},
            {"action": f"HCHAT_READ_{t}", "title": "Mark as read"},
        ]
    return data


# ---------- a new message ----------
def new_message(mid: int) -> None:
    sends = []
    with db.get_conn() as conn:
        msg = chats.message_row(conn, mid)
        if msg is None or msg["deleted_at"] or msg["kind"] == "system":
            return
        missed = None
        if msg["kind"] == "call":       # a call note: news only for those who missed it (§15.12)
            c = chats.call_row(conn, mid)
            missed = {m["user_id"] for m in chats.call_members(conn, c["id"]) if m["state"] in ("missed", "busy")} if c else set()
            if not missed:
                return
        conv = chats.conv_row(conn, msg["conversation_id"])
        if conv is None or conv["kind"] == "personal":
            return
        sender = chats.user_row(conn, msg["user_id"])
        sender_name = chats.shown_name(sender)
        rows = conn.execute("SELECT m.*, u.* FROM members m JOIN users u ON u.id = m.user_id WHERE m.conversation_id = ? "
                            "AND u.disabled = 0 AND m.user_id != ?", (conv["id"], msg["user_id"])).fetchall()
        now = time.monotonic()
        for r in rows:
            uid = r["user_id"]
            if msg["id"] <= r["joined_message_id"] or not wants(conn, r, r, conv, msg):
                continue
            if missed is not None and uid not in missed:
                continue
            services = ha_notify.services_for({"id": uid}, conn)
            if not services or hub.is_looking(uid, conv["id"]):
                continue
            key = (uid, conv["id"])
            with _lock:
                held = _held.get(key)
                if in_quiet_hours(r):
                    _hold(key, mid, True)
                    continue
                if not msg["announcement"] and (held is not None or now - _last_sent.get(key, -1e9) < BATCH_SECONDS):
                    _hold(key, mid, False, _last_sent.get(key, now) + BATCH_SECONDS)
                    continue
                _last_sent[key] = now
            level = preview_level(r, conn)
            sends.append((services, title_for(conn, conv, sender_name),
                          text_for(conn, msg, level, sender_name, conv["kind"] == "group"), push_data(conn, uid, conv["id"])))
    for services, title, text, data in sends:
        deliver(services, title, text, data)


def drop_held(uid: str, cid: str) -> None:
    with _lock:
        if _held.pop((uid, cid), None) is not None:
            _changed.set()


def cancel_held(uid: str, cid: str, up_to: int) -> None:
    with _lock:
        h = _held.get((uid, cid))
        if h and max(h["ids"]) <= up_to:
            _held.pop((uid, cid), None)
            _changed.set()


def flush() -> int:
    """Release batched pushes whose minute is up, and quiet-hour ones once quiet hours end; then save what's
    still held if it changed."""
    try:
        return _flush()
    finally:
        save_held()


def _flush() -> int:
    now = time.monotonic()
    with _lock:
        due = {k: v for k, v in _held.items() if (v["until"] is not None and now >= v["until"]) or v["quiet"]}
    if not due:
        return 0
    sends = []
    with db.get_conn() as conn:
        for (uid, cid), h in due.items():
            user = chats.user_row(conn, uid)
            m = chats.member_row(conn, cid, uid)
            conv = chats.conv_row(conn, cid)
            if user is None or user["disabled"] or m is None or conv is None:
                with _lock:
                    _held.pop((uid, cid), None)
                    _changed.set()
                continue
            if h["quiet"] and in_quiet_hours(user):
                continue
            with _lock:
                _held.pop((uid, cid), None)
                _changed.set()
                _last_sent[(uid, cid)] = now
            ids = [i for i in h["ids"] if i > m["last_read_id"] and i > m["joined_message_id"]]
            msgs = [x for x in (chats.message_row(conn, i) for i in ids) if x is not None and not x["deleted_at"]]
            if not msgs:
                continue
            services = ha_notify.services_for({"id": uid}, conn)
            if not services:
                continue
            level = preview_level(user, conn)
            senders = {x["user_id"] for x in msgs}
            one_sender = chats.shown_name(chats.user_row(conn, msgs[0]["user_id"])) if len(senders) == 1 else None
            if len(msgs) == 1:
                text = text_for(conn, msgs[0], level, one_sender, conv["kind"] == "group")
            elif level == "none":
                text = f"New messages in {config.APP_TITLE}"
            elif one_sender:
                text = f"{one_sender}: {len(msgs)} new messages"
            else:
                text = f"{len(msgs)} new messages"
            title = title_for(conn, conv, one_sender or chats.display_name_of(conn, conv, uid))
            sends.append((services, title, text, push_data(conn, uid, cid)))
    for services, title, text, data in sends:
        deliver(services, title, text, data)
    return len(sends)


def send_to_user(uid: str, title: str, text: str, cid: str | None = None) -> bool:
    """A single push to one person (reminders, "everyone has voted"). False if they have no service."""
    with db.get_conn() as conn:
        user = chats.user_row(conn, uid)
        if user is None or user["disabled"]:
            return False
        services = ha_notify.services_for({"id": uid}, conn)
        link = config.chat_link(cid) if cid else config.INGRESS_URL
        data = {"url": link, "clickAction": link}
        if cid:
            data["tag"] = f"hchat_r_{cid}"
    if not services:
        return False
    deliver(services, title, text, data)
    return True


def notify_or_hold(uid: str, cid: str, mid: int, title: str, text: str) -> None:
    """One push now — or, in the person's quiet hours, held and sent when they end."""
    with db.get_conn() as conn:
        user = chats.user_row(conn, uid)
    if user is None or user["disabled"]:
        return
    if in_quiet_hours(user):
        with _lock:
            _hold((uid, cid), mid, True)
        return
    send_to_user(uid, title, text, cid)


def reply_follow_up(uid: str, cid: str, text: str) -> None:
    """After a Reply from the phone: the chat's notification again (same tag, so it replaces the one replied to),
    quietly, showing the reply and carrying a new token for the next Reply."""
    with db.get_conn() as conn:
        user = chats.user_row(conn, uid)
        conv = chats.conv_row(conn, cid)
        if user is None or user["disabled"] or conv is None or chats.member_row(conn, cid, uid) is None:
            return
        services = ha_notify.services_for({"id": uid}, conn)
        if not services:
            return
        level = preview_level(user, conn)
        title = chats.display_name_of(conn, conv, uid)
        body = f"You: {chats.short(text, 200)}" if level == "full" else "You replied"
        data = push_data(conn, uid, cid)
    data.update({"alert_once": True, "push": {"interruption-level": "passive"}})
    deliver(services, title, body, data)


def purge_tokens(conn) -> None:
    cutoff = config.iso(config.utcnow() - timedelta(hours=TOKEN_HOURS))
    conn.execute("DELETE FROM notify_tokens WHERE created_at < ?", (cutoff,))


# ---------- the phone's Reply / Mark as read buttons (§15.1) ----------
def handle_action(action: str, reply_text: str | None) -> str:
    """An HCHAT_ action from Home Assistant's event bus. → what happened (for tests and the log)."""
    if not isinstance(action, str) or not action.startswith("HCHAT_"):
        return "ignored"
    kind, _, token = action[len("HCHAT_"):].partition("_")
    if kind not in ("REPLY", "READ", "DECLINE") or not token:
        return "ignored"
    out = chats.Outbox()
    with db.get_conn() as conn:
        t = conn.execute("SELECT * FROM notify_tokens WHERE token = ?", (token,)).fetchone()
        if t is None:
            return "unknown token"
        made = config.parse_iso(t["created_at"])
        if made is None or config.utcnow() - made > timedelta(hours=TOKEN_HOURS):
            return "expired"
        if kind in ("READ", "DECLINE"):
            # once only: Mark as read and Decline end that notification, so a replayed action finds nothing.
            if conn.execute("DELETE FROM notify_tokens WHERE token = ?", (token,)).rowcount == 0:
                return "unknown token"
        if kind != "DECLINE" and not settings.get("notification_reply", conn):
            return "off"
        user = chats.user_row(conn, t["user_id"])
        conv = chats.conv_row(conn, t["conversation_id"])
        m = chats.member_row(conn, t["conversation_id"], t["user_id"]) if conv else None
        if user is None or user["disabled"] or m is None:
            return "refused"
        if kind == "DECLINE":           # a ringing call's Decline button (§15.12): ended below, after this connection
            result = "decline"
        elif kind == "READ":
            chats.mark_read(conn, out, conv, m, user["id"], conv["last_message_id"] or 0)
            result = "read"
        else:
            text = (reply_text or "").strip()
            if not text or len(text) > 2000 or not chats.can_post(conn, conv, user["id"]):
                return "refused"
            # Reply uses its token up too (a replayed action does nothing); the phone gets the conversation's
            # notification again with new buttons, so the back-and-forth on the lock screen goes on
            if conn.execute("DELETE FROM notify_tokens WHERE token = ?", (token,)).rowcount == 0:
                return "unknown token"
            chats.message_limit.take(user["id"])
            udict = {"id": user["id"], "name": user["name"], "is_admin": False, "is_child": bool(user["is_child"])}
            mid = chats.post_message(conn, out, conv, m, udict, text, via="notification")
            chats.mark_read(conn, out, conv, chats.member_row(conn, conv["id"], user["id"]), user["id"], mid)
            out.job(reply_follow_up, user["id"], conv["id"], text)
            result = "replied"
    if result == "decline":
        from . import calls
        return calls.decline_from_phone(t["user_id"], t["conversation_id"])
    out.flush()
    return result
