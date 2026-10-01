"""SQLite at /data/chat.db (SPEC §5.1): schema, migrations, small helpers.

Message text is stored readable, not encrypted at rest (SPEC §4.1).
Files live in /share (files.py); the database only records where.
"""
import contextlib
import os
import sqlite3
import threading
import uuid

from . import config

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
  kind TEXT NOT NULL CHECK (kind IN ('text','file','system','poll')),
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
    conn = sqlite3.connect(config.DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    # deleted rows are overwritten, not just marked free (disappearing messages, SPEC §15.8)
    conn.execute("PRAGMA secure_delete = ON")
    return conn


@contextlib.contextmanager
def get_conn():
    """A connection that commits when the block ends (and rolls back on an error)."""
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _columns(conn, table: str) -> set:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


MIGRATIONS = {
    "users": [("is_child", "INTEGER NOT NULL DEFAULT 0"),
              # the photo is a copy of their person.* entity's picture (avatars.py)
              ("avatar_src", "TEXT"), ("avatar_version", "INTEGER NOT NULL DEFAULT 0")],
    "conversations": [("disappear_seconds", "INTEGER")],
    "messages": [("expires_at", "TEXT"), ("announcement", "INTEGER NOT NULL DEFAULT 0"),
                 ("announcement_closed_at", "TEXT"), ("announcement_reminded_at", "TEXT"),
                 ("reply_gone", "INTEGER NOT NULL DEFAULT 0")],
    "attachments": [("admin_deleted_at", "TEXT"), ("scheduled_id", "TEXT")],
}


def _migrate(conn) -> None:
    """Columns that older databases lack (CREATE TABLE IF NOT EXISTS never adds them)."""
    for table, cols in MIGRATIONS.items():
        have = _columns(conn, table)
        for name, decl in cols:
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_msg_expires ON messages(expires_at) WHERE expires_at IS NOT NULL")
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


def init_db() -> None:
    os.makedirs(os.path.dirname(config.DB_PATH) or ".", exist_ok=True)
    conn = _connect()
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        _migrate(conn)
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
