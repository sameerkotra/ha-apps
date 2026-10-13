"""Background jobs (SPEC §10): every tick (20 s) held pushes, due reminders,
poll closing and the app-messages outbox (APP_MESSAGES_SPEC §4); every minute home/away; every 5 minutes the chat files folder; hourly unsent uploads; nightly retention,
_deleted purge, missing files, orphan thumbnails."""
import json
import logging
from datetime import timedelta

from . import (app_messages, avatars, chats, config, db, disappearing, files, notifier, presence, scheduled, settings,
               shared_folders)
from .common import backup_core
from .live import hub

logger = logging.getLogger("housekeeping")
NIGHTLY_KEY = "nightly_last_run"           # app_settings: the local day the nightly jobs last ran (survives restarts)


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
    by_conv: dict = {}
    with db.get_conn() as conn:
        rows = conn.execute(
            f"SELECT m.id, m.conversation_id FROM messages m JOIN conversations c ON c.id = m.conversation_id "
            f"WHERE m.created_at < ? AND c.kind IN {kinds} AND m.pinned_at IS NULL", (cutoff,)).fetchall()
        if not rows:
            return 0
        ids = [r["id"] for r in rows]
        for r in rows:
            by_conv.setdefault(r["conversation_id"], []).append(r["id"])
        paths = []
        now = config.now_iso()
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            q = ",".join("?" * len(chunk))
            atts = conn.execute(f"SELECT id, rel_path, size, conversation_id FROM attachments WHERE message_id IN ({q})",
                                chunk).fetchall()
            paths += [(a["id"], a["rel_path"]) for a in atts]
            if keep_files:
                # the files stay in the chat's folder: still counted in its size and on the Storage page
                conn.executemany("INSERT OR IGNORE INTO kept_files (rel_path, conversation_id, size, kept_at) "
                                 "VALUES (?, ?, ?, ?)", [(a["rel_path"], a["conversation_id"], a["size"], now) for a in atts
                                                        if files.DISAPPEARING not in a["rel_path"].split("/")])
            # replies to them say "Original message no longer available", as for disappearing messages
            conn.execute(f"UPDATE messages SET reply_gone = 1 WHERE reply_to IN ({q})", chunk)
            conn.execute(f"DELETE FROM messages WHERE id IN ({q})", chunk)
        conn.execute("UPDATE conversations SET last_message_id = (SELECT MAX(id) FROM messages m WHERE m.conversation_id = conversations.id)")
        recipients = {cid: chats.member_ids(conn, cid) for cid in by_conv}
        db.audit(conn, "retention", None, None, None)
    for aid, rel in paths:
        files.remove_thumb(aid)
        if not keep_files or files.DISAPPEARING in rel.split("/"):
            files.remove_now(rel)
    for cid, mids in by_conv.items():          # open pages take them away, as for disappearing messages
        hub.publish(recipients[cid], "message_removed", {"conversationId": cid, "messageIds": mids})
        hub.publish(recipients[cid], "unread", {"conversationId": cid})
    logger.info("Retention removed %s old messages.", len(ids))
    return len(ids)


TEMP_PREFIXES = (".backup-", ".export-", ".restore-")     # what downloads and restores stage in /data
TEMP_MAX_AGE = 6 * 3600


def remove_stale_temp(max_age: float = TEMP_MAX_AGE) -> int:
    """Backups, chat downloads and restores half made when the app stopped (or never fetched) don't stay in /data:
    all of them at start-up (max_age 0), the ones over six hours old every hour."""
    return sum(backup_core.remove_stale(config.DATA_DIR, p, max_age) for p in TEMP_PREFIXES)


def nightly() -> None:
    retention()
    remove_unsent_uploads()
    files.purge_deleted(30)
    files.check_missing()
    with db.get_conn() as conn:
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
    # disappearing messages expire every tick; the folder sweep and the full clean every 5 minutes
    _safe(disappearing.run_expiry, n % 15 == 2)
    for job in (scheduled.run_due, notifier.flush, due_reminders, close_due_polls, hub.expire,
                app_messages.run_outbox):
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
        _safe(remove_stale_temp)
        remove_unsent_uploads()
        with db.get_conn() as conn:
            notifier.purge_tokens(conn)
    nightly_due()


def nightly_due() -> bool:
    """The nightly jobs, once a day from 03:30 (Home Assistant's time). The day they last ran is kept in the
    database, so a restart in the night (or one that was off at 03:30) doesn't skip them: they run on the
    first tick after 03:30 of a day they haven't run. On the very first start they wait for the next night."""
    local = config.local_now()
    today = local.date().isoformat()
    if (local.hour, local.minute) < (3, 30):
        return False
    with db.get_conn() as conn:
        raw = db.get_setting(conn, NIGHTLY_KEY)
        if raw is None:
            db.set_setting(conn, NIGHTLY_KEY, json.dumps(today))
            return False
    try:
        last = json.loads(raw)
    except ValueError:
        last = None
    if last == today:
        return False
    with db.get_conn() as conn:          # first: a nightly that fails isn't retried every 20 seconds
        db.set_setting(conn, NIGHTLY_KEY, json.dumps(today))
    try:
        nightly()
    except Exception:
        logger.exception("Nightly job failed")
    return True
