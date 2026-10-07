"""SQLite at /data/chat.db (SPEC §5.1): schema, migrations, small helpers.

Message text is stored readable, not encrypted at rest (SPEC §4.1).
Files live in /share (files.py); the database only records where.
"""
import os
import sqlite3
import threading
import uuid

from . import config
from .common import app_bus, db_core

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,                       -- HA user id
  name TEXT NOT NULL,
  username TEXT,
  ha_person TEXT,                            -- the person.* linked to their login (from the sync)
  disabled INTEGER NOT NULL DEFAULT 1,       -- everyone starts disabled
  avatar_file TEXT,
  presence_entity TEXT,                      -- person.* an admin chose for home/away + photo instead of ha_person (§15.3)
  notify_level TEXT NOT NULL DEFAULT 'direct_mentions' CHECK (notify_level IN ('all','direct_mentions','off')),
  notify_preview TEXT NOT NULL DEFAULT 'full' CHECK (notify_preview IN ('full','sender','none')),
  quiet_start TEXT, quiet_end TEXT,          -- "22:00"/"07:00" in HA's time zone
  hide_online INTEGER NOT NULL DEFAULT 0,
  assistant_ok INTEGER NOT NULL DEFAULT 1,   -- "Let the Household Assistant answer for me" (tools.py)
  last_seen TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS user_notify (
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  service TEXT NOT NULL, created_at TEXT NOT NULL, created_by TEXT,
  PRIMARY KEY (user_id, service)
);
CREATE TABLE IF NOT EXISTS conversations (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('direct','group','personal')),
  name TEXT,
  icon TEXT,
  description TEXT,
  direct_key TEXT UNIQUE,
  folder TEXT NOT NULL,
  created_by TEXT REFERENCES users(id),
  created_at TEXT NOT NULL,
  last_message_id INTEGER,
  last_activity_at TEXT,
  is_household INTEGER NOT NULL DEFAULT 0,
  new_members_see_history INTEGER NOT NULL DEFAULT 1
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_household ON conversations(is_household) WHERE is_household = 1;
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_personal ON conversations(created_by) WHERE kind = 'personal';
CREATE TABLE IF NOT EXISTS members (
  conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  user_id TEXT NOT NULL REFERENCES users(id),
  role TEXT NOT NULL CHECK (role IN ('owner','admin','member')),
  joined_at TEXT NOT NULL,
  joined_message_id INTEGER NOT NULL DEFAULT 0,     -- history visible after this message id
  last_read_id INTEGER NOT NULL DEFAULT 0,
  notify TEXT NOT NULL DEFAULT 'default' CHECK (notify IN ('default','all','mentions','off')),
  muted_until TEXT,
  pinned INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (conversation_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_members_user ON members(user_id);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  user_id TEXT REFERENCES users(id),
  kind TEXT NOT NULL CHECK (kind IN ('text','file','system','poll','card','call')),   -- 'card': shared from another app (§15.11); 'call': a call note (§15.12)
  body TEXT NOT NULL DEFAULT '',
  reply_to INTEGER REFERENCES messages(id) ON DELETE SET NULL,
  mentions TEXT,                             -- JSON list of user ids (server-checked)
  mention_all INTEGER NOT NULL DEFAULT 0,
  forwarded INTEGER NOT NULL DEFAULT 0,
  via TEXT,                                  -- 'notification' for replies from the phone
  created_at TEXT NOT NULL,
  edited_at TEXT,
  deleted_at TEXT,
  pinned_at TEXT,
  pinned_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conversation_id, id);
CREATE INDEX IF NOT EXISTS idx_msg_pinned ON messages(conversation_id) WHERE pinned_at IS NOT NULL;
CREATE TABLE IF NOT EXISTS attachments (
  id TEXT PRIMARY KEY,
  message_id INTEGER REFERENCES messages(id) ON DELETE CASCADE,   -- NULL while pending (uploaded, not sent)
  conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  rel_path TEXT NOT NULL,
  original_name TEXT NOT NULL,
  mime TEXT NOT NULL,
  size INTEGER NOT NULL,
  original_size INTEGER,                     -- before the photo was made smaller (§15.4)
  sha256 TEXT NOT NULL,
  width INTEGER, height INTEGER,
  duration REAL,                             -- voice messages (§15.6)
  voice INTEGER NOT NULL DEFAULT 0,
  uploaded_by TEXT,
  created_at TEXT NOT NULL,
  missing INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_att_msg ON attachments(message_id);
CREATE INDEX IF NOT EXISTS idx_att_conv ON attachments(conversation_id);
CREATE TABLE IF NOT EXISTS heard (
  attachment_id TEXT NOT NULL REFERENCES attachments(id) ON DELETE CASCADE,
  user_id TEXT NOT NULL, at TEXT NOT NULL,
  PRIMARY KEY (attachment_id, user_id)
);
CREATE TABLE IF NOT EXISTS reactions (
  message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
  user_id TEXT NOT NULL, emoji TEXT NOT NULL, created_at TEXT NOT NULL,
  PRIMARY KEY (message_id, user_id, emoji)
);
CREATE TABLE IF NOT EXISTS polls (
  message_id INTEGER PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
  question TEXT NOT NULL, multi INTEGER NOT NULL DEFAULT 0,
  closes_at TEXT, closed_at TEXT, closed_by TEXT, all_voted_sent INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS poll_options (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
  position INTEGER NOT NULL, text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS poll_votes (
  message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
  option_id INTEGER NOT NULL REFERENCES poll_options(id) ON DELETE CASCADE,
  user_id TEXT NOT NULL, created_at TEXT NOT NULL,
  PRIMARY KEY (option_id, user_id)
);
-- a card another household app posted for a person (§15.11, APP_MESSAGES_SPEC §6.3): never any document content
CREATE TABLE IF NOT EXISTS app_cards (
  message_id INTEGER PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
  app TEXT NOT NULL,                         -- the sending app's slug (household_docs)
  badge TEXT NOT NULL,                       -- "Docs": shown as "from Docs"
  item_type TEXT NOT NULL CHECK (item_type IN ('note','checklist','sheet','folder','file')),
  item_id TEXT NOT NULL,                     -- the other app's id for it
  title TEXT NOT NULL,
  owner_name TEXT,
  panel TEXT,                                -- the other app's page in Home Assistant ("/<full slug>"), if it said
  target TEXT,                               -- the item's route inside that page ("/doc/<id>")
  shared_with_members INTEGER NOT NULL DEFAULT 0,
  bus_id TEXT,                               -- the app message it came in
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_app_cards_app ON app_cards(app, message_id);
-- voice calls (§15.12): one row per call that rang; its note in the chat is message_id (kind 'call')
CREATE TABLE IF NOT EXISTS calls (
  id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  caller_id TEXT NOT NULL, callee_id TEXT NOT NULL,
  started_at TEXT NOT NULL,                  -- when it started ringing
  answered_at TEXT, ended_at TEXT,
  outcome TEXT CHECK (outcome IN ('answered','missed','declined','busy','failed')),   -- NULL while it's on
  message_id INTEGER REFERENCES messages(id) ON DELETE CASCADE,
  kind TEXT NOT NULL DEFAULT 'audio',        -- audio | video
  is_group INTEGER NOT NULL DEFAULT 0        -- a group call: callee_id is '' and call_members says who was in it
);
CREATE INDEX IF NOT EXISTS idx_calls_msg ON calls(message_id);
CREATE TABLE IF NOT EXISTS call_members (
  call_id TEXT NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
  user_id TEXT NOT NULL,
  state TEXT NOT NULL,                       -- left (was in it) | missed | declined | busy | failed
  joined_at TEXT, left_at TEXT,
  PRIMARY KEY (call_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_call_members_user ON call_members(user_id);
-- relay usage (§15.13): what each phone sent and received through the call relay, as the phone measured it
CREATE TABLE IF NOT EXISTS call_usage (
  call_id TEXT NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
  user_id TEXT NOT NULL,
  relay_bytes INTEGER NOT NULL DEFAULT 0,
  month TEXT NOT NULL,                       -- "2026-10" in Home Assistant's time zone
  updated_at TEXT NOT NULL,
  PRIMARY KEY (call_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_call_usage_month ON call_usage(month);
CREATE TABLE IF NOT EXISTS stars (
  user_id TEXT NOT NULL, message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL, PRIMARY KEY (user_id, message_id)
);
CREATE TABLE IF NOT EXISTS reminders (
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
  message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
  due_at TEXT NOT NULL, done_at TEXT, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(due_at) WHERE done_at IS NULL;
CREATE TABLE IF NOT EXISTS notify_tokens (
  token TEXT PRIMARY KEY, user_id TEXT NOT NULL,
  conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS announcement_acks (
  message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
  user_id TEXT NOT NULL, at TEXT NOT NULL, PRIMARY KEY (message_id, user_id)
);
CREATE TABLE IF NOT EXISTS scheduled (
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
  conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  payload TEXT NOT NULL, send_at TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_scheduled_due ON scheduled(send_at);
CREATE TABLE IF NOT EXISTS drafts (
  user_id TEXT NOT NULL, conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  body TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY (user_id, conversation_id)
);
-- shared folders (§12): one row per /share folder, linked into any number of chats (groups, direct
-- chats, personal rooms), each link with its own access
CREATE TABLE IF NOT EXISTS folders (
  id TEXT PRIMARY KEY,
  path TEXT NOT NULL UNIQUE,                 -- relative to /share
  label TEXT NOT NULL,
  announce INTEGER NOT NULL DEFAULT 1,
  created_by TEXT, created_at TEXT NOT NULL, last_scan_at TEXT
);
CREATE TABLE IF NOT EXISTS folder_links (
  id TEXT PRIMARY KEY,                       -- what members use (/api/folders/{id}/…)
  folder_id TEXT NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
  conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  mode TEXT NOT NULL CHECK (mode IN ('ro','rw')),
  created_by TEXT, created_at TEXT NOT NULL,
  UNIQUE (folder_id, conversation_id)
);
CREATE INDEX IF NOT EXISTS idx_folder_links_conv ON folder_links(conversation_id);
CREATE TABLE IF NOT EXISTS folder_files (
  folder_id TEXT NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
  rel TEXT NOT NULL, size INTEGER NOT NULL, mtime REAL NOT NULL,
  PRIMARY KEY (folder_id, rel)
);
CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT, updated_by TEXT);
CREATE TABLE IF NOT EXISTS audit_log (
  id TEXT PRIMARY KEY, user_id TEXT, actor_id TEXT, conversation_id TEXT,
  action TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(body, content='messages', content_rowid='id');
CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages WHEN new.kind != 'system' BEGIN
  INSERT INTO messages_fts(rowid, body) VALUES (new.id, new.body);
END;
CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages WHEN old.kind != 'system' BEGIN
  INSERT INTO messages_fts(messages_fts, rowid, body) VALUES ('delete', old.id, old.body);
END;
CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE OF body ON messages WHEN old.kind != 'system' BEGIN
  INSERT INTO messages_fts(messages_fts, rowid, body) VALUES ('delete', old.id, old.body);
  INSERT INTO messages_fts(rowid, body) VALUES (new.id, new.body);
END;
"""


# Set while an admin restore swaps the database file: requests get 503 and background jobs wait.
RESTORING = threading.Event()


def new_id() -> str:
    return uuid.uuid4().hex


def _connect() -> sqlite3.Connection:
    # secure_delete: deleted rows are overwritten, not just marked free (disappearing messages, SPEC §15.8)
    return db_core.connect(config.DB_PATH, timeout=30, check_same_thread=False,
                           pragmas=("foreign_keys = ON", "busy_timeout = 30000", "secure_delete = ON"))


def get_conn():
    """A connection that commits when the block ends (and rolls back on an error) — app/common/db_core.py."""
    return db_core.transaction(_connect)


MIGRATIONS = {
    "users": [("is_child", "INTEGER NOT NULL DEFAULT 0"),
              # the photo is a copy of their person.* entity's picture (avatars.py)
              ("avatar_src", "TEXT"), ("avatar_version", "INTEGER NOT NULL DEFAULT 0"),
              ("assistant_ok", "INTEGER NOT NULL DEFAULT 1")],
    "conversations": [("disappear_seconds", "INTEGER")],
    "messages": [("expires_at", "TEXT"), ("announcement", "INTEGER NOT NULL DEFAULT 0"),
                 ("announcement_closed_at", "TEXT"), ("announcement_reminded_at", "TEXT"),
                 ("reply_gone", "INTEGER NOT NULL DEFAULT 0")],
    "attachments": [("admin_deleted_at", "TEXT"), ("scheduled_id", "TEXT")],
    "calls": [("kind", "TEXT NOT NULL DEFAULT 'audio'"), ("is_group", "INTEGER NOT NULL DEFAULT 0")],
}


def _migrate(conn) -> None:
    """Columns that older databases lack (CREATE TABLE IF NOT EXISTS never adds them)."""
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    db_core.add_missing_columns(conn, {t: cols for t, cols in MIGRATIONS.items() if t in tables})
    conn.execute("CREATE INDEX IF NOT EXISTS idx_msg_expires ON messages(expires_at) WHERE expires_at IS NOT NULL")
    # calls from before group calls: their members from the two columns
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'calls'").fetchone():
        return
    conn.execute("CREATE TABLE IF NOT EXISTS call_members (call_id TEXT NOT NULL REFERENCES calls(id) ON DELETE CASCADE, "
                 "user_id TEXT NOT NULL, state TEXT NOT NULL, joined_at TEXT, left_at TEXT, PRIMARY KEY (call_id, user_id))")
    conn.execute("INSERT OR IGNORE INTO call_members (call_id, user_id, state, joined_at, left_at) "
                 "SELECT id, caller_id, 'left', started_at, ended_at FROM calls WHERE ended_at IS NOT NULL")
    conn.execute("INSERT OR IGNORE INTO call_members (call_id, user_id, state, joined_at, left_at) "
                 "SELECT id, callee_id, CASE WHEN outcome = 'answered' THEN 'left' WHEN outcome = 'declined' THEN 'declined' "
                 "WHEN outcome = 'busy' THEN 'busy' WHEN outcome = 'failed' THEN 'failed' ELSE 'missed' END, answered_at, ended_at "
                 "FROM calls WHERE ended_at IS NOT NULL AND callee_id != ''")
    _migrate_shared_folders(conn)


def _migrate_shared_folders(conn) -> None:
    """Older databases kept one `shared_folders` row per group. Now a folder is shared once and linked into
    chats: the same path in several groups becomes one folder with several links. Link ids keep the old
    row ids, so announcements already posted still open the right folder."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'shared_folders'").fetchone():
        return
    rows = conn.execute("SELECT * FROM shared_folders ORDER BY created_at, id").fetchall()
    by_path = {}
    for r in rows:
        fid = by_path.get(r["path"])
        if fid is None:
            fid = by_path[r["path"]] = r["id"]
            conn.execute("INSERT OR IGNORE INTO folders (id, path, label, announce, created_by, created_at, last_scan_at) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?)", (fid, r["path"], r["label"], r["announce"], r["created_by"],
                                                          r["created_at"], r["last_scan_at"]))
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'shared_folder_files'").fetchone():
                conn.execute("INSERT OR IGNORE INTO folder_files (folder_id, rel, size, mtime) "
                             "SELECT ?, rel, size, mtime FROM shared_folder_files WHERE folder_id = ?", (fid, r["id"]))
        conn.execute("INSERT OR IGNORE INTO folder_links (id, folder_id, conversation_id, mode, created_by, created_at) "
                     "VALUES (?, ?, ?, ?, ?, ?)", (r["id"], fid, r["conversation_id"], r["mode"], r["created_by"], r["created_at"]))
    conn.execute("DROP TABLE IF EXISTS shared_folder_files")
    conn.execute("DROP TABLE shared_folders")


# messages.kind's CHECK in older databases, and today's (NEW_KINDS)
OLD_KINDS = "CHECK (kind IN ('text','file','system','poll'))"                    # before 2.2.0
CARD_KINDS = "CHECK (kind IN ('text','file','system','poll','card'))"            # 2.2.x
NEW_KINDS = "CHECK (kind IN ('text','file','system','poll','card','call'))"      # 2.3.0: call notes


def _allow_new_kinds(conn) -> bool:
    """Databases from before 2.3.0 have a messages.kind CHECK without 'call' (before 2.2.0 also without
    'card'). SQLite can't change a CHECK in place, so the table is rebuilt as SQLite documents it (https://sqlite.org/lang_altertable.html,
    "other kinds of table schema changes"): foreign keys off; in ONE transaction a copy of the table with the
    new CHECK (the stored CREATE statement itself, so every column — also those added later by ALTER — keeps
    its place and definition), every row copied with its id, the old table dropped, the copy renamed, its
    indexes and triggers made again exactly as they were, the AUTOINCREMENT counter kept (ids of deleted
    messages are never reused), and `PRAGMA foreign_key_check` must find no problem that wasn't there before,
    else everything is rolled back; foreign keys back on. The rows that point at messages (attachments, reactions, polls, …) keep
    their ids, so nothing else changes; the search index (messages_fts) keeps its rowids. → True if rebuilt."""
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'messages'").fetchone()
    sql = row[0]
    if NEW_KINDS in sql:
        return False
    old = next((k for k in (CARD_KINDS, OLD_KINDS) if k in sql), None)
    if old is None:
        raise RuntimeError("The messages table isn't the expected one; not changing it.")
    create = sql.replace(old, NEW_KINDS, 1)
    head = create[:create.index("(")]
    create = head.replace("messages", "messages_new", 1) + create[len(head):]
    if conn.in_transaction:
        conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            extras = [r[0] for r in conn.execute(
                "SELECT sql FROM sqlite_master WHERE tbl_name = 'messages' AND type IN ('index', 'trigger') "
                "AND sql IS NOT NULL ORDER BY type, name")]
            seq = conn.execute("SELECT seq FROM sqlite_sequence WHERE name = 'messages'").fetchone()
            before = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            problems = {tuple(r) for r in conn.execute("PRAGMA foreign_key_check")}   # already there: not ours
            conn.execute(create)
            conn.execute("INSERT INTO messages_new SELECT * FROM messages")
            conn.execute("DROP TABLE messages")
            conn.execute("ALTER TABLE messages_new RENAME TO messages")
            for stmt in extras:
                conn.execute(stmt)
            if seq is not None:
                if not conn.execute("UPDATE sqlite_sequence SET seq = MAX(seq, ?) WHERE name = 'messages'",
                                    (seq[0],)).rowcount:
                    conn.execute("INSERT INTO sqlite_sequence (name, seq) VALUES ('messages', ?)", (seq[0],))
            if conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] != before:
                raise RuntimeError("Copying the messages lost rows.")
            if {tuple(r) for r in conn.execute("PRAGMA foreign_key_check")} - problems:
                raise RuntimeError("The rebuilt messages table breaks a foreign key.")
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
    return True


_allow_card_messages = _allow_new_kinds      # its name before 2.3.0


def init_db() -> None:
    os.makedirs(os.path.dirname(config.DB_PATH) or ".", exist_ok=True)
    conn = _connect()
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.commit()
        _allow_new_kinds(conn)
        app_bus.migrate(conn)          # messages between the household apps (bus_outbox, bus_seen, bus_apps)
        conn.commit()
    finally:
        conn.close()


def get_setting(conn, key: str):
    r = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    return r["value"] if r else None


def set_setting(conn, key: str, value: str, by: str | None = None) -> None:
    conn.execute("INSERT INTO app_settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at, "
                 "updated_by = excluded.updated_by", (key, value, config.now_iso(), by))


def audit(conn, action: str, user_id: str | None = None, conversation_id: str | None = None,
          actor_id: str | None = None) -> None:
    """Never any content: an action, who it's about, which chat, who did it, and when."""
    conn.execute("INSERT INTO audit_log (id, user_id, actor_id, conversation_id, action, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                 (new_id(), user_id, actor_id or user_id, conversation_id, action, config.now_iso()))
