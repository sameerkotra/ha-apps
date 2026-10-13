"""Disappearing messages (SPEC §15.8): when a message's time is up it is deleted completely.

The row goes (and with it, by ON DELETE CASCADE: reactions, poll, stars,
reminders, acknowledgements, attachment rows and its search entry); replies
keep their text with "Original message no longer available"; its files and
previews are removed from /share at once (never to _deleted). `secure_delete`
is on for every connection. The pass runs every tick, and at startup and after
a restore *before* anything is served.

After a pass that deleted anything, the search index gets a small merge and the
write-ahead log a passive checkpoint (cheap); the full clean — the whole index
merged (`optimize`) and `wal_checkpoint(TRUNCATE)`, so no old copy of a deleted
message lingers anywhere — runs with the `full` pass: every 5 minutes (with the
sweep of the _disappearing folders, which lists every chat's folder), at
startup and after a restore.
"""
import logging
import sqlite3
import threading

from . import chats, config, db, files
from .live import hub

logger = logging.getLogger("disappearing")
_dirty = threading.Event()          # deleted since the last full clean


def run_expiry(full: bool = True) -> int:
    now = config.now_iso()
    removed_files = []
    by_conv: dict = {}
    with db.get_conn() as conn:
        rows = conn.execute("SELECT id, conversation_id FROM messages WHERE expires_at IS NOT NULL AND expires_at <= ?",
                            (now,)).fetchall()
        if rows:
            ids = [r["id"] for r in rows]
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                q = ",".join("?" * len(chunk))
                removed_files += [(a["id"], a["rel_path"]) for a in
                                  conn.execute(f"SELECT id, rel_path FROM attachments WHERE message_id IN ({q})", chunk)]
                conn.execute(f"UPDATE messages SET reply_gone = 1 WHERE reply_to IN ({q})", chunk)
                conn.execute(f"DELETE FROM messages WHERE id IN ({q})", chunk)
            for r in rows:
                by_conv.setdefault(r["conversation_id"], []).append(r["id"])
            for cid in by_conv:
                conn.execute("UPDATE conversations SET last_message_id = (SELECT MAX(id) FROM messages WHERE conversation_id = ?) "
                             "WHERE id = ?", (cid, cid))
            recipients = {cid: chats.member_ids(conn, cid) for cid in by_conv}
    for aid, rel in removed_files:
        files.remove_now(rel)
        files.remove_thumb(aid)
    swept = files.sweep_disappearing(config.utcnow().strftime("%Y%m%dT%H%MZ")) if full else 0
    if by_conv:
        _dirty.set()
    if full and _dirty.is_set():
        clean(full=True)
    elif by_conv:
        clean(full=False)
    if by_conv:
        for cid, mids in by_conv.items():
            hub.publish(recipients[cid], "message_removed", {"conversationId": cid, "messageIds": mids})
            hub.publish(recipients[cid], "unread", {"conversationId": cid})
        logger.info("%s disappearing message(s) deleted.", sum(len(v) for v in by_conv.values()))
    return sum(len(v) for v in by_conv.values()) + swept


def clean(full: bool) -> None:
    """Deleted words out of the search index and old page copies out of the write-ahead log: all of them
    (`full`), or a small step (an incremental merge, a passive checkpoint) between full cleans."""
    try:
        with db.get_conn() as conn:
            if full:
                conn.execute("INSERT INTO messages_fts(messages_fts) VALUES ('optimize')")
            else:
                conn.execute("INSERT INTO messages_fts(messages_fts, rank) VALUES ('merge', -200)")
    except sqlite3.Error:
        logger.warning("The search index couldn't be merged; it's tried again on the next pass.")
        return
    checkpoint("TRUNCATE" if full else "PASSIVE")
    if full:
        _dirty.clear()


def checkpoint(mode: str = "TRUNCATE") -> None:
    try:
        c = sqlite3.connect(config.DB_PATH, timeout=30)
        c.execute(f"PRAGMA wal_checkpoint({'PASSIVE' if mode == 'PASSIVE' else 'TRUNCATE'})")
        c.close()
    except sqlite3.Error:
        logger.warning("The write-ahead log couldn't be checkpointed; old copies stay until the next pass.")


def strip_from_copy(path: str) -> set:
    """For the app's own backup: a copy of the database without any disappearing message — sent, or
    scheduled to be sent as one. → the files (relative paths) the backup must leave out."""
    import json
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    c.execute("PRAGMA secure_delete = ON")
    skip = set()
    for r in c.execute("SELECT s.id, s.payload, c.disappear_seconds FROM scheduled s JOIN conversations c ON c.id = s.conversation_id").fetchall():
        try:
            later = json.loads(r["payload"]).get("expiresIn")
        except ValueError:
            later = None
        if later or (later is None and r["disappear_seconds"]):
            skip |= {a["rel_path"] for a in c.execute("SELECT rel_path FROM attachments WHERE scheduled_id = ?", (r["id"],))}
            c.execute("DELETE FROM attachments WHERE scheduled_id = ?", (r["id"],))
            c.execute("DELETE FROM scheduled WHERE id = ?", (r["id"],))
    c.execute("UPDATE messages SET reply_gone = 1 WHERE reply_to IN (SELECT id FROM messages WHERE expires_at IS NOT NULL)")
    c.execute("DELETE FROM messages WHERE expires_at IS NOT NULL")
    c.execute("INSERT INTO messages_fts(messages_fts) VALUES ('optimize')")
    c.execute("UPDATE conversations SET last_message_id = (SELECT MAX(id) FROM messages m WHERE m.conversation_id = conversations.id)")
    c.commit()
    c.execute("VACUUM")
    c.close()
    return skip
