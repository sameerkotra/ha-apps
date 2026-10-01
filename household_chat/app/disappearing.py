"""Disappearing messages (SPEC §15.8): when a message's time is up it is deleted completely.

The row goes (and with it, by ON DELETE CASCADE: reactions, poll, stars,
reminders, acknowledgements, attachment rows and its search entry); replies
keep their text with "Original message no longer available"; its files and
previews are removed from /share at once (never to _deleted). `secure_delete`
is on for every connection, and the write-ahead log is checkpointed after a
pass that deleted anything. The pass runs every tick, and at startup and after
a restore *before* anything is served.
"""
import logging
import sqlite3

from . import chats, config, db, files
from .live import hub

logger = logging.getLogger("disappearing")


def run_expiry() -> int:
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
            # merge the search index so the deleted words don't linger in old index segments
            conn.execute("INSERT INTO messages_fts(messages_fts) VALUES ('optimize')")
    for aid, rel in removed_files:
        files.remove_now(rel)
        files.remove_thumb(aid)
    swept = files.sweep_disappearing(config.utcnow().strftime("%Y%m%dT%H%MZ"))
    if by_conv:
        checkpoint()
        for cid, mids in by_conv.items():
            hub.publish(recipients[cid], "message_removed", {"conversationId": cid, "messageIds": mids})
            hub.publish(recipients[cid], "unread", {"conversationId": cid})
        logger.info("%s disappearing message(s) deleted.", sum(len(v) for v in by_conv.values()))
    return sum(len(v) for v in by_conv.values()) + swept


def checkpoint() -> None:
    try:
        c = sqlite3.connect(config.DB_PATH, timeout=30)
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
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
