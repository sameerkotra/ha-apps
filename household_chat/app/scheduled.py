"""Send later (SPEC §16.7): a message waits in `scheduled` (visible only to its author) and is posted
by the housekeeping loop when it's time. Its files are uploaded straight away and kept pending
(`attachments.scheduled_id`) until then. It survives restarts; it's cancelled, with a notice, if the
author is no longer a member (or disabled, or can't post there) when it's due."""
import json
import logging
from datetime import timedelta

from fastapi import HTTPException

from . import chats, config, db, files, notifier

logger = logging.getLogger("scheduled")
MAX_PER_PERSON = 20


def check_time(send_at: str):
    t = config.parse_iso(send_at)
    now = config.utcnow()
    if t is None or t < now + timedelta(seconds=50) or t > now + timedelta(days=366):
        raise HTTPException(422, "Choose a time at least a minute from now and within a year.")
    return config.iso(t)


def item_out(conn, r, viewer_id: str) -> dict:
    p = json.loads(r["payload"])
    atts = conn.execute("SELECT * FROM attachments WHERE scheduled_id = ? ORDER BY created_at", (r["id"],)).fetchall()
    conv = chats.conv_row(conn, r["conversation_id"])
    return {"id": r["id"], "conversationId": r["conversation_id"],
            "conversationName": chats.display_name_of(conn, conv, viewer_id) if conv else "",
            "sendAt": r["send_at"], "createdAt": r["created_at"], "body": p.get("body", ""),
            "poll": p.get("poll"), "announcement": bool(p.get("announcement")), "expiresIn": p.get("expiresIn"),
            "replyTo": p.get("replyTo"), "attachments": [chats.attachment_out(a) for a in atts]}


def free_attachments(conn, sid: str) -> list:
    rows = conn.execute("SELECT id, rel_path FROM attachments WHERE scheduled_id = ? AND message_id IS NULL", (sid,)).fetchall()
    conn.execute("DELETE FROM attachments WHERE scheduled_id = ? AND message_id IS NULL", (sid,))
    return [(r["id"], r["rel_path"]) for r in rows]


def remove_files(paths: list) -> None:
    for aid, rel in paths:
        files.remove_now(rel)
        files.remove_thumb(aid)


def send(conn, out, r) -> int:
    """Post it now as its author. Raises HTTPException when that's no longer possible."""
    user_row = chats.user_row(conn, r["user_id"])
    if user_row is None or user_row["disabled"]:
        raise HTTPException(409, "You no longer have access.")
    from .auth import is_admin
    user = {"id": user_row["id"], "name": user_row["name"], "is_child": bool(user_row["is_child"]),
            "is_admin": is_admin(user_row["id"], user_row["username"])}
    conv = chats.conv_row(conn, r["conversation_id"])
    m = chats.member_row(conn, r["conversation_id"], r["user_id"]) if conv else None
    if conv is None or m is None:
        raise HTTPException(409, "You're no longer in that chat.")
    p = json.loads(r["payload"])
    if p.get("poll"):
        poll = p["poll"]
        closes_at = poll.get("closesAt")
        if closes_at and config.parse_iso(closes_at) and config.parse_iso(closes_at) <= config.utcnow() + timedelta(minutes=1):
            closes_at = None                       # its closing time has passed: send it open
        q, opts, closes = chats.check_poll(poll.get("question"), poll.get("options") or [], closes_at)
        mid = chats.create_poll(conn, out, conv, user, q, opts, bool(poll.get("multi")), closes, p.get("expiresIn"),
                                bool(p.get("announcement")))
    else:
        att_ids = [a["id"] for a in conn.execute("SELECT id FROM attachments WHERE scheduled_id = ? ORDER BY created_at",
                                                 (r["id"],))]
        reply = p.get("replyTo")
        if reply is not None:
            q = chats.message_row(conn, reply)
            if q is None or q["conversation_id"] != conv["id"] or q["id"] <= m["joined_message_id"] or q["kind"] == "system":
                reply = None                   # the message it replied to is gone: send it without the quote
        mid = chats.post_message(conn, out, conv, m, user, p.get("body", ""), reply, att_ids, p.get("mentions"),
                                 expires_in=p.get("expiresIn"), announcement=bool(p.get("announcement")),
                                 scheduled_id=r["id"])
    conn.execute("DELETE FROM scheduled WHERE id = ?", (r["id"],))
    return mid


def run_due() -> int:
    now = config.now_iso()
    done = 0
    with db.get_conn() as conn:
        due = [r["id"] for r in conn.execute("SELECT id FROM scheduled WHERE send_at <= ? ORDER BY send_at LIMIT 50", (now,))]
    for sid in due:
        out = chats.Outbox()
        cancelled = None
        try:
            with db.get_conn() as conn:
                r = conn.execute("SELECT * FROM scheduled WHERE id = ?", (sid,)).fetchone()
                if r is None:
                    continue
                send(conn, out, r)
            done += 1
        except HTTPException as e:
            with db.get_conn() as conn:
                r = conn.execute("SELECT * FROM scheduled WHERE id = ?", (sid,)).fetchone()
                if r is None:
                    continue
                conv = chats.conv_row(conn, r["conversation_id"])
                name = chats.display_name_of(conn, conv, r["user_id"]) if conv else "a chat"
                paths = free_attachments(conn, sid)
                conn.execute("DELETE FROM scheduled WHERE id = ?", (sid,))
                cancelled = (r["user_id"], name, str(e.detail), r["conversation_id"])
            remove_files(paths)
        except Exception:
            logger.exception("Sending a scheduled message failed")
            continue
        out.flush()
        if cancelled:
            uid, name, why, cid = cancelled
            from .live import hub
            hub.publish([uid], "scheduled", {"cancelled": True, "conversationName": name, "reason": why})
            try:
                notifier.send_to_user(uid, config.APP_TITLE, f"Your scheduled message to {name} wasn't sent: {why}", cid)
            except Exception:
                logger.exception("Couldn't tell someone their scheduled message was cancelled")
    return done
