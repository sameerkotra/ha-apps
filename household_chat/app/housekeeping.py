"""Background jobs (SPEC §10): every tick (20 s) held pushes, due reminders and
poll closing; every minute home/away; every 5 minutes the chat files folder; hourly unsent uploads; nightly retention,
_deleted purge, missing files, orphan thumbnails."""
import logging
from datetime import timedelta

from . import avatars, chats, config, db, disappearing, files, notifier, presence, scheduled, settings, shared_folders
from .live import hub

logger = logging.getLogger("housekeeping")
_state = {"nightly": None}


def due_reminders() -> int:
    now = config.now_iso()
    sent = 0
    with db.get_conn() as conn:
        rows = conn.execute("SELECT r.*, m.conversation_id, m.deleted_at, m.user_id AS author_id FROM reminders r "
                            "JOIN messages m ON m.id = r.message_id WHERE r.done_at IS NULL AND r.due_at <= ? "
                            "ORDER BY r.due_at LIMIT 100", (now,)).fetchall()
        jobs = []
        for r in rows:
            user = chats.user_row(conn, r["user_id"])
            if (user is None or user["disabled"] or r["deleted_at"]
                    or chats.member_row(conn, r["conversation_id"], r["user_id"]) is None):
                conn.execute("DELETE FROM reminders WHERE id = ?", (r["id"],))
                continue
            if notifier.in_quiet_hours(user):
                continue
            conn.execute("UPDATE reminders SET done_at = ? WHERE id = ?", (now, r["id"]))
            level = notifier.preview_level(user, conn)
            author = chats.shown_name(chats.user_row(conn, r["author_id"])) if r["author_id"] else ""
            msg = chats.message_row(conn, r["message_id"])
            if level == "full" and msg["expires_at"]:
                text = f"⏰ Reminder: a message from {author}"
            elif level == "full":
                text = f"⏰ Reminder: {author} — {chats.preview_of(conn, msg)}"
            elif level == "sender":
                text = f"⏰ Reminder: a message from {author}"
            else:
                text = "⏰ Reminder about a message"
            jobs.append((r["user_id"], text, r["conversation_id"], r["message_id"]))
    for uid, text, cid, mid in jobs:
        hub.publish([uid], "reminder", {"conversationId": cid, "messageId": mid})
        try:
            notifier.send_to_user(uid, config.APP_TITLE, text, cid)
        except Exception:
            logger.exception("Sending a reminder failed")
        sent += 1
    return sent


def close_due_polls() -> int:
    out = chats.Outbox()
    now = config.now_iso()
    with db.get_conn() as conn:
        rows = conn.execute("SELECT p.message_id FROM polls p WHERE p.closed_at IS NULL AND p.closes_at IS NOT NULL "
                            "AND p.closes_at <= ?", (now,)).fetchall()
        for r in rows:
            conn.execute("UPDATE polls SET closed_at = ? WHERE message_id = ?", (now, r["message_id"]))
            msg = chats.message_row(conn, r["message_id"])
            chats.publish_message(conn, out, chats.conv_row(conn, msg["conversation_id"]), msg, "message_updated")
    out.flush()
    return len(rows)


def remove_unsent_uploads(hours: int = 24) -> int:
    cutoff = config.iso(config.utcnow() - timedelta(hours=hours))
    with db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM attachments WHERE message_id IS NULL AND scheduled_id IS NULL AND created_at < ?",
                            (cutoff,)).fetchall()
        for a in rows:
            conn.execute("DELETE FROM attachments WHERE id = ?", (a["id"],))
    for a in rows:
        files.remove_now(a["rel_path"])
        files.remove_thumb(a["id"])
    return len(rows)


def retention() -> int:
    days = settings.get("message_retention_days")
    if not days:
        return 0
    keep_files = settings.get("keep_files_on_retention")
    personal_too = settings.get("retention_includes_personal")
    cutoff = config.iso(config.utcnow() - timedelta(days=days))
    kinds = "('group','direct','personal')" if personal_too else "('group','direct')"
    with db.get_conn() as conn:
        ids = [r["id"] for r in conn.execute(
            f"SELECT m.id FROM messages m JOIN conversations c ON c.id = m.conversation_id WHERE m.created_at < ? "
            f"AND c.kind IN {kinds} AND m.pinned_at IS NULL", (cutoff,))]
        if not ids:
            return 0
        paths = []
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            q = ",".join("?" * len(chunk))
            paths += [(a["id"], a["rel_path"]) for a in conn.execute(f"SELECT id, rel_path FROM attachments WHERE message_id IN ({q})", chunk)]
            conn.execute(f"DELETE FROM messages WHERE id IN ({q})", chunk)
        conn.execute("UPDATE conversations SET last_message_id = (SELECT MAX(id) FROM messages m WHERE m.conversation_id = conversations.id)")
        db.audit(conn, "retention", None, None, None)
    for aid, rel in paths:
        files.remove_thumb(aid)
        if not keep_files:
            files.remove_now(rel)
    logger.info("Retention removed %s old messages.", len(ids))
    return len(ids)


def nightly() -> None:
    retention()
    remove_unsent_uploads()
    files.purge_deleted(30)
    with db.get_conn() as conn:
        files.check_missing(conn)
        files.remove_orphan_thumbs(conn)
        notifier.purge_tokens(conn)
        conn.execute("PRAGMA optimize")
    files.remove_stale_parts()


def _safe(fn, *args):
    try:
        return fn(*args)
    except Exception:
        logger.exception("%s failed", getattr(fn, "__name__", "A job"))


def tick(n: int) -> None:
    for job in (disappearing.run_expiry, scheduled.run_due, notifier.flush, due_reminders, close_due_polls, hub.expire):
        _safe(job)
    if n % 3 == 0:
        try:
            presence.refresh_blocking()
        except Exception:
            logger.exception("Home/away refresh failed")
    if n % 15 == 1:
        _safe(files.check)          # every 5 minutes: is the chat files folder still connected?
    if n % 15 == 4:
        _safe(shared_folders.scan_all)
    if n % 30 == 10:
        _safe(avatars.sync_blocking)          # every 10 minutes: people's photos follow Home Assistant
    if n % 180 == 7:
        remove_unsent_uploads()
        with db.get_conn() as conn:
            notifier.purge_tokens(conn)
    local = config.local_now()
    today = local.date().isoformat()
    if (local.hour, local.minute) >= (3, 30) and _state["nightly"] != today:
        if _state["nightly"] is not None:
            try:
                nightly()
            except Exception:
                logger.exception("Nightly job failed")
        _state["nightly"] = today
