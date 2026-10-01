"""One message: edit, delete, react, pin, star, remind, forward; polls (SPEC §5.2, §15.2, §15.5, §16.1–§16.3)."""
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field

from .. import chats, config, db, files, notifier
from ..auth import require_user
from .common import Strict

router = APIRouter(prefix="/api", tags=["messages"])
MAX_REMINDERS = 50


def _one(conn, mid, user):
    msg, conv, m = chats.message_access(conn, mid, user)
    return msg, conv, m


def _out(conn, mid, user, m):
    return chats.messages_out(conn, [chats.message_row(conn, mid)], user["id"], m["joined_message_id"])[0]


class EditIn(Strict):
    body: str = Field(max_length=20000)
    mentions: list[str] = Field(default_factory=list, max_length=50)


@router.patch("/messages/{mid}")
def edit(mid: int, body: EditIn, user: dict = Depends(require_user)):
    out = chats.Outbox()
    with db.get_conn() as conn:
        msg, conv, m = _one(conn, mid, user)
        chats.edit_message(conn, out, msg, conv, user, body.body, body.mentions)
        res = _out(conn, mid, user, m)
    out.flush()
    return res


@router.delete("/messages/{mid}")
def delete(mid: int, user: dict = Depends(require_user)):
    out = chats.Outbox()
    with db.get_conn() as conn:
        msg, conv, m = _one(conn, mid, user)
        chats.delete_message(conn, out, msg, conv, m, user)
        res = _out(conn, mid, user, m)
    out.flush()
    return res


# ---------- reactions ----------
def _emoji(e: str) -> str:
    e = (e or "").strip()
    if not e or len(e) > 12 or any((c.isascii() and (c.isalnum() or c in "<>&\"'/\\ ")) for c in e):
        raise HTTPException(422, "A reaction is one emoji.")
    return e


@router.put("/messages/{mid}/reactions/{emoji}")
def react(mid: int, emoji: str, user: dict = Depends(require_user)):
    e = _emoji(emoji)
    out = chats.Outbox()
    with db.get_conn() as conn:
        msg, conv, m = _one(conn, mid, user)
        chats.not_personal(conv)
        if msg["deleted_at"] or msg["kind"] == "system":
            raise HTTPException(409, "You can't react to that.")
        chats.require_post(conn, conv, user["id"])
        n = conn.execute("SELECT COUNT(DISTINCT emoji) FROM reactions WHERE message_id = ?", (msg["id"],)).fetchone()[0]
        exists = conn.execute("SELECT 1 FROM reactions WHERE message_id = ? AND emoji = ?", (msg["id"], e)).fetchone()
        if n >= 20 and not exists:
            raise HTTPException(409, "That message has enough different reactions.")
        conn.execute("INSERT OR IGNORE INTO reactions (message_id, user_id, emoji, created_at) VALUES (?, ?, ?, ?)",
                     (msg["id"], user["id"], e, config.now_iso()))
        chats.publish_message(conn, out, conv, chats.message_row(conn, mid), "message_updated")
        res = _out(conn, mid, user, m)
    out.flush()
    return res


@router.delete("/messages/{mid}/reactions/{emoji}")
def unreact(mid: int, emoji: str, user: dict = Depends(require_user)):
    out = chats.Outbox()
    with db.get_conn() as conn:
        msg, conv, m = _one(conn, mid, user)
        chats.not_personal(conv)
        conn.execute("DELETE FROM reactions WHERE message_id = ? AND user_id = ? AND emoji = ?", (msg["id"], user["id"], emoji))
        chats.publish_message(conn, out, conv, chats.message_row(conn, mid), "message_updated")
        res = _out(conn, mid, user, m)
    out.flush()
    return res


# ---------- pins ----------
@router.put("/messages/{mid}/pin")
def pin(mid: int, user: dict = Depends(require_user)):
    out = chats.Outbox()
    with db.get_conn() as conn:
        msg, conv, m = _one(conn, mid, user)
        chats.require_adult(user, "pin messages")
        if msg["deleted_at"] or msg["kind"] == "system":
            raise HTTPException(409, "That message can't be pinned.")
        chats.require_post(conn, conv, user["id"])
        if not msg["pinned_at"]:
            n = conn.execute("SELECT COUNT(*) FROM messages WHERE conversation_id = ? AND pinned_at IS NOT NULL",
                             (conv["id"],)).fetchone()[0]
            if n >= chats.MAX_PINS:
                raise HTTPException(409, f"A chat can have {chats.MAX_PINS} pinned messages — unpin one first.")
            conn.execute("UPDATE messages SET pinned_at = ?, pinned_by = ? WHERE id = ?", (config.now_iso(), user["id"], msg["id"]))
            chats.publish_message(conn, out, conv, chats.message_row(conn, mid), "message_updated")
            chats.add_system(conn, out, conv, {"event": "pinned", "actor": user["id"]})
        res = _out(conn, mid, user, m)
    out.flush()
    return res


@router.delete("/messages/{mid}/pin")
def unpin(mid: int, user: dict = Depends(require_user)):
    out = chats.Outbox()
    with db.get_conn() as conn:
        msg, conv, m = _one(conn, mid, user)
        chats.require_adult(user, "unpin messages")
        if msg["pinned_at"]:
            chats.require_post(conn, conv, user["id"])
            conn.execute("UPDATE messages SET pinned_at = NULL, pinned_by = NULL WHERE id = ?", (msg["id"],))
            chats.publish_message(conn, out, conv, chats.message_row(conn, mid), "message_updated")
            chats.add_system(conn, out, conv, {"event": "unpinned", "actor": user["id"]})
        res = _out(conn, mid, user, m)
    out.flush()
    return res


# ---------- stars (private) ----------
@router.put("/messages/{mid}/star")
def star(mid: int, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        msg, conv, m = _one(conn, mid, user)
        if msg["deleted_at"] or msg["kind"] == "system":
            raise HTTPException(409, "That message can't be starred.")
        conn.execute("INSERT OR IGNORE INTO stars (user_id, message_id, created_at) VALUES (?, ?, ?)",
                     (user["id"], msg["id"], config.now_iso()))
        return _out(conn, mid, user, m)


@router.delete("/messages/{mid}/star")
def unstar(mid: int, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        msg, conv, m = _one(conn, mid, user)
        conn.execute("DELETE FROM stars WHERE user_id = ? AND message_id = ?", (user["id"], msg["id"]))
        return _out(conn, mid, user, m)


# ---------- remind me (private) ----------
class RemindIn(Strict):
    preset: str | None = Field(default=None, pattern="^(1h|evening|tomorrow|nextweek)$")
    at: str | None = Field(default=None, max_length=40)


def remind_time(preset: str | None, at: str | None):
    now = config.local_now()
    if preset == "1h":
        return now + timedelta(hours=1)
    if preset == "evening":
        t = now.replace(hour=18, minute=0, second=0, microsecond=0)
        return t if t > now + timedelta(minutes=1) else t + timedelta(days=1)
    if preset == "tomorrow":
        return (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    if preset == "nextweek":
        days = 7 - now.weekday()
        return (now + timedelta(days=days)).replace(hour=9, minute=0, second=0, microsecond=0)
    t = config.parse_iso(at)
    if t is None:
        raise HTTPException(422, "Choose when to be reminded.")
    return t


@router.post("/messages/{mid}/remind")
def remind(mid: int, body: RemindIn, user: dict = Depends(require_user)):
    due = remind_time(body.preset, body.at)
    if due <= config.utcnow() + timedelta(seconds=30) or due > config.utcnow() + timedelta(days=366):
        raise HTTPException(422, "Choose a time in the future (within a year).")
    with db.get_conn() as conn:
        msg, conv, m = _one(conn, mid, user)
        if msg["deleted_at"] or msg["kind"] == "system":
            raise HTTPException(409, "You can't set a reminder on that.")
        n = conn.execute("SELECT COUNT(*) FROM reminders WHERE user_id = ? AND done_at IS NULL", (user["id"],)).fetchone()[0]
        if n >= MAX_REMINDERS:
            raise HTTPException(409, f"You have {MAX_REMINDERS} reminders waiting — delete some first.")
        conn.execute("DELETE FROM reminders WHERE user_id = ? AND message_id = ? AND done_at IS NULL", (user["id"], msg["id"]))
        conn.execute("INSERT INTO reminders (id, user_id, message_id, due_at, created_at) VALUES (?, ?, ?, ?, ?)",
                     (db.new_id(), user["id"], msg["id"], config.iso(due), config.now_iso()))
        return _out(conn, mid, user, m)


@router.delete("/messages/{mid}/remind")
def unremind(mid: int, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        msg, conv, m = _one(conn, mid, user)
        conn.execute("DELETE FROM reminders WHERE user_id = ? AND message_id = ? AND done_at IS NULL", (user["id"], msg["id"]))
        return _out(conn, mid, user, m)


# ---------- forward ----------
class ForwardIn(Strict):
    messageIds: list[int] = Field(min_length=1, max_length=20)
    conversationIds: list[str] = Field(min_length=1, max_length=10)


@router.post("/messages/forward")
def forward(body: ForwardIn, user: dict = Depends(require_user)):
    out = chats.Outbox()
    made = 0
    with db.get_conn() as conn:
        sources = []
        for mid in dict.fromkeys(body.messageIds):
            msg, conv, m = _one(conn, mid, user)
            if msg["deleted_at"] or msg["kind"] not in ("text", "file", "poll"):
                raise HTTPException(409, "That message can't be forwarded.")
            if msg["expires_at"]:
                raise HTTPException(409, "Disappearing messages can't be forwarded.")
            sources.append((msg, conv))
        sources.sort(key=lambda r: r[0]["id"])
        targets = []
        for cid in dict.fromkeys(body.conversationIds):
            conv, m = chats.access(conn, cid, user)
            chats.require_post(conn, conv, user["id"])
            if user.get("is_child") and conv["kind"] == "direct" and any(sc["kind"] == "group" for _, sc in sources):
                other = chats.other_member(conn, conv, user["id"])
                if other is not None and other["is_child"]:
                    raise HTTPException(403, "Children can't forward from a group to another child.")
            targets.append(conv)
        chats.message_limit.take(user["id"], len(sources) * len(targets))
        for conv in targets:
            for src, _ in sources:
                if src["kind"] == "poll":
                    opts = [o["text"] for o in conn.execute("SELECT text FROM poll_options WHERE message_id = ? ORDER BY position",
                                                            (src["id"],))]
                    text = "📊 " + src["body"] + "\n" + "\n".join("- " + o for o in opts)
                else:
                    text = src["body"]
                atts = conn.execute("SELECT * FROM attachments WHERE message_id = ? AND missing = 0 ORDER BY created_at, rowid",
                                    (src["id"],)).fetchall()
                copies = []
                for a in atts:
                    c = files.copy_into(a, conv)
                    if c:
                        copies.append((a, c))
                if not text.strip() and not copies:
                    continue
                mid = chats.insert_message(conn, conv, user["id"], kind="file" if copies else "text", body=text[:chats.MAX_BODY],
                                           forwarded=True, expires_in=conv["disappear_seconds"])
                expires_at = chats.message_row(conn, mid)["expires_at"]
                for a, c in copies:
                    conn.execute(
                        "INSERT INTO attachments (id, message_id, conversation_id, rel_path, original_name, mime, size, original_size, "
                        "sha256, width, height, duration, voice, uploaded_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (db.new_id(), mid, conv["id"], c["rel_path"], a["original_name"], a["mime"], a["size"], a["original_size"],
                         a["sha256"], a["width"], a["height"], a["duration"], a["voice"], user["id"], config.now_iso()))
                if expires_at:
                    for (na,) in conn.execute("SELECT id FROM attachments WHERE message_id = ?", (mid,)).fetchall():
                        files.move_to_disappearing(conn, na, conv, expires_at)
                chats.publish_message(conn, out, conv, chats.message_row(conn, mid))
                if conv["kind"] != "personal":
                    out.job(notifier.new_message, mid)
                made += 1
    out.flush()
    return {"forwarded": made}


# ---------- announcements (§15.7) ----------
def _announcement(conn, mid, user):
    msg, conv, m = _one(conn, mid, user)
    if not msg["announcement"] or msg["deleted_at"]:
        raise HTTPException(404, "That announcement doesn't exist.")
    return msg, conv, m


@router.post("/messages/{mid}/ack")
def ack(mid: int, user: dict = Depends(require_user)):
    """Got it."""
    out = chats.Outbox()
    with db.get_conn() as conn:
        msg, conv, m = _announcement(conn, mid, user)
        if msg["user_id"] != user["id"]:
            conn.execute("INSERT OR IGNORE INTO announcement_acks (message_id, user_id, at) VALUES (?, ?, ?)",
                         (msg["id"], user["id"], config.now_iso()))
            chats.publish_message(conn, out, conv, chats.message_row(conn, mid), "message_updated")
        res = _out(conn, mid, user, m)
    out.flush()
    return res


@router.post("/messages/{mid}/announcement-close")
def close_announcement(mid: int, user: dict = Depends(require_user)):
    """The sender takes it down before everyone has acknowledged it."""
    out = chats.Outbox()
    with db.get_conn() as conn:
        msg, conv, m = _announcement(conn, mid, user)
        if msg["user_id"] != user["id"]:
            raise HTTPException(403, "Only the person who posted it can take it down.")
        conn.execute("UPDATE messages SET announcement_closed_at = ? WHERE id = ? AND announcement_closed_at IS NULL",
                     (config.now_iso(), msg["id"]))
        chats.publish_message(conn, out, conv, chats.message_row(conn, mid), "message_updated")
        res = _out(conn, mid, user, m)
    out.flush()
    return res


@router.post("/messages/{mid}/announcement-reminder")
def remind_announcement(mid: int, user: dict = Depends(require_user)):
    """One reminder to everyone who hasn't tapped Got it."""
    out = chats.Outbox()
    with db.get_conn() as conn:
        msg, conv, m = _announcement(conn, mid, user)
        if msg["user_id"] != user["id"]:
            raise HTTPException(403, "Only the person who posted it can send a reminder.")
        if msg["announcement_reminded_at"]:
            raise HTTPException(409, "A reminder was already sent.")
        info = chats.announcement_out(conn, msg, [r["user_id"] for r in conn.execute(
            "SELECT user_id FROM announcement_acks WHERE message_id = ?", (msg["id"],))])
        if info["closed"]:
            raise HTTPException(409, "Everyone has seen it (or it was taken down).")
        conn.execute("UPDATE messages SET announcement_reminded_at = ? WHERE id = ?", (config.now_iso(), msg["id"]))
        title = conv["name"] or chats.display_name_of(conn, conv, user["id"])
        text = f"📢 Reminder from {user['name']}: {chats.short(chats.strip_marks(msg['body']), 150) or 'an announcement'}"
        if msg["expires_at"]:
            text = f"📢 Reminder from {user['name']} about an announcement"
        for uid in info["pending"]:
            out.job(notifier.notify_or_hold, uid, conv["id"], msg["id"], title, text)
        chats.publish_message(conn, out, conv, chats.message_row(conn, mid), "message_updated")
        res = _out(conn, mid, user, m)
    out.flush()
    return res


# ---------- polls ----------
class VoteIn(Strict):
    optionIds: list[int] = Field(max_length=10)


def _poll(conn, mid, user):
    msg, conv, m = _one(conn, mid, user)
    p = conn.execute("SELECT * FROM polls WHERE message_id = ?", (msg["id"],)).fetchone()
    if msg["kind"] != "poll" or p is None or msg["deleted_at"]:
        raise HTTPException(404, "That poll doesn't exist.")
    return msg, conv, m, p


@router.put("/polls/{mid}/vote")
def vote(mid: int, body: VoteIn, user: dict = Depends(require_user)):
    out = chats.Outbox()
    tell_creator = None
    with db.get_conn() as conn:
        msg, conv, m, p = _poll(conn, mid, user)
        if p["closed_at"] or chats.poll_expired(p):
            raise HTTPException(409, "That poll is closed.")
        chats.require_post(conn, conv, user["id"])
        valid = {r["id"] for r in conn.execute("SELECT id FROM poll_options WHERE message_id = ?", (msg["id"],))}
        ids = list(dict.fromkeys(body.optionIds))
        if any(i not in valid for i in ids):
            raise HTTPException(422, "That isn't one of the poll's options.")
        if len(ids) > 1 and not p["multi"]:
            raise HTTPException(422, "This poll takes one answer.")
        conn.execute("DELETE FROM poll_votes WHERE message_id = ? AND user_id = ?", (msg["id"], user["id"]))
        now = config.now_iso()
        for i in ids:
            conn.execute("INSERT INTO poll_votes (message_id, option_id, user_id, created_at) VALUES (?, ?, ?, ?)",
                         (msg["id"], i, user["id"], now))
        chats.publish_message(conn, out, conv, chats.message_row(conn, mid), "message_updated")
        if not p["all_voted_sent"] and msg["user_id"]:
            everyone = set(chats.member_ids(conn, conv["id"]))
            voted = {r["user_id"] for r in conn.execute("SELECT DISTINCT user_id FROM poll_votes WHERE message_id = ?", (msg["id"],))}
            if everyone and everyone <= voted:
                conn.execute("UPDATE polls SET all_voted_sent = 1 WHERE message_id = ?", (msg["id"],))
                tell_creator = (msg["user_id"], chats.display_name_of(conn, conv, msg["user_id"]), msg["body"], conv["id"])
        res = _out(conn, mid, user, m)
    if tell_creator:
        uid, cname, q, cid = tell_creator
        out.job(notifier.send_to_user, uid, cname, f"Everyone has voted: {chats.short(q, 120)}", cid)
    out.flush()
    return res


@router.post("/polls/{mid}/close")
def close_poll(mid: int, user: dict = Depends(require_user)):
    out = chats.Outbox()
    with db.get_conn() as conn:
        msg, conv, m, p = _poll(conn, mid, user)
        if msg["user_id"] != user["id"] and not chats.is_manager(conv, m, user):
            raise HTTPException(403, "Only the person who started the poll (or a group admin) can close it.")
        if not p["closed_at"]:
            conn.execute("UPDATE polls SET closed_at = ?, closed_by = ? WHERE message_id = ?", (config.now_iso(), user["id"], msg["id"]))
            chats.publish_message(conn, out, conv, chats.message_row(conn, mid), "message_updated")
        res = _out(conn, mid, user, m)
    out.flush()
    return res
