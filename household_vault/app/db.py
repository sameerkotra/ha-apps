"""SQLite metadata (SPEC §6). Vault contents are never stored here — only
names, members, versions and sealed (encrypted) keys."""
import os
import sqlite3
import uuid

from . import config
from .common import db_core

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  username TEXT,
  ha_person TEXT,
  disabled INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'none' CHECK (status IN ('none','temporary','active')),
  public_key TEXT,
  private_key_wrapped TEXT,
  auto_lock_minutes INTEGER NOT NULL DEFAULT 5 CHECK (auto_lock_minutes BETWEEN 1 AND 60),
  search_notes INTEGER NOT NULL DEFAULT 0,
  breach_check INTEGER NOT NULL DEFAULT 0,
  download_reminder_days INTEGER NOT NULL DEFAULT 0,
  unlock_failures INTEGER NOT NULL DEFAULT 0,
  unlock_blocked_until TEXT,
  last_seen TEXT,
  last_unlock TEXT,
  previous_unlock TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS vaults (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('personal','household','shared','emergency')),
  password_mode TEXT NOT NULL CHECK (password_mode IN ('chosen','random')),
  name TEXT NOT NULL,
  owner_user_id TEXT REFERENCES users(id),
  rekey_pending INTEGER NOT NULL DEFAULT 0,
  password_change_suggested INTEGER NOT NULL DEFAULT 0,
  key_epoch INTEGER NOT NULL DEFAULT 1,
  version INTEGER NOT NULL DEFAULT 0,
  size INTEGER NOT NULL DEFAULT 0,
  sha256 TEXT,
  updated_at TEXT,
  updated_by TEXT REFERENCES users(id),
  created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_personal ON vaults(owner_user_id) WHERE kind = 'personal';
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_household ON vaults(kind) WHERE kind = 'household';
CREATE TABLE IF NOT EXISTS vault_members (
  vault_id TEXT NOT NULL REFERENCES vaults(id) ON DELETE CASCADE,
  user_id TEXT NOT NULL REFERENCES users(id),
  role TEXT NOT NULL CHECK (role IN ('owner','manager','editor','viewer')),
  added_by TEXT REFERENCES users(id),
  added_at TEXT NOT NULL,
  PRIMARY KEY (vault_id, user_id)
);
CREATE TABLE IF NOT EXISTS vault_keys (
  vault_id TEXT NOT NULL REFERENCES vaults(id) ON DELETE CASCADE,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  sealed TEXT NOT NULL,
  key_epoch INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (vault_id, user_id)
);
CREATE TABLE IF NOT EXISTS vault_versions (
  vault_id TEXT NOT NULL REFERENCES vaults(id) ON DELETE CASCADE,
  version INTEGER NOT NULL,
  size INTEGER NOT NULL,
  sha256 TEXT NOT NULL,
  key_epoch INTEGER NOT NULL,
  created_by TEXT REFERENCES users(id),
  created_at TEXT NOT NULL,
  PRIMARY KEY (vault_id, version)
);
CREATE TABLE IF NOT EXISTS audit_log (
  id TEXT PRIMARY KEY,
  vault_id TEXT,
  user_id TEXT,
  actor_id TEXT,
  action TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(user_id, created_at);
CREATE TABLE IF NOT EXISTS app_settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
-- notify services an admin assigned to each person (as in Household Todo)
CREATE TABLE IF NOT EXISTS user_notify (
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  service TEXT NOT NULL,           -- "notify.<name>"
  created_at TEXT NOT NULL,
  created_by TEXT,
  PRIMARY KEY (user_id, service)
);
-- emergency access (SPEC §12.4)
CREATE TABLE IF NOT EXISTS emergency_contacts (
  owner_id TEXT NOT NULL REFERENCES users(id),
  contact_id TEXT NOT NULL REFERENCES users(id),
  emergency_vault_id TEXT NOT NULL REFERENCES vaults(id) ON DELETE CASCADE,
  wait_days INTEGER NOT NULL CHECK (wait_days BETWEEN 1 AND 30),
  sealed TEXT NOT NULL,            -- the Emergency vault password sealed to the contact's public key
  key_epoch INTEGER NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('ready','requested','denied','granted')),
  requested_at TEXT, decided_at TEXT, created_at TEXT NOT NULL,
  PRIMARY KEY (owner_id, contact_id)
);
-- expiry reminders — just enough to remind while the vault is locked
CREATE TABLE IF NOT EXISTS reminders (
  vault_id TEXT NOT NULL REFERENCES vaults(id) ON DELETE CASCADE,
  item_id TEXT NOT NULL,
  title TEXT NOT NULL,
  expires_on TEXT NOT NULL,        -- YYYY-MM-DD
  remind_days INTEGER NOT NULL,
  stage INTEGER NOT NULL DEFAULT 0, -- 1 = "expires in N days" sent, 2 = "expires today / expired" sent
  PRIMARY KEY (vault_id, item_id)
);
-- recently used items, per person (item ids only)
CREATE TABLE IF NOT EXISTS recent_items (
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  vault_id TEXT NOT NULL,
  item_id TEXT NOT NULL,
  used_at TEXT NOT NULL,
  PRIMARY KEY (user_id, vault_id, item_id)
);
-- the guest Wi-Fi shown on the HA dashboard (opt-in; stored unencrypted on purpose)
CREATE TABLE IF NOT EXISTS guest_wifi (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  vault_id TEXT NOT NULL REFERENCES vaults(id) ON DELETE CASCADE,
  item_id TEXT NOT NULL,
  ssid TEXT NOT NULL,
  password TEXT NOT NULL,
  security TEXT NOT NULL,
  show_password INTEGER NOT NULL DEFAULT 0,
  published_by TEXT,
  updated_at TEXT NOT NULL
);
-- personal copies: /data/copies/<HA user name>.kdbx (copies.py, SPEC §12.10)
CREATE TABLE IF NOT EXISTS user_copies (
  user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  file TEXT NOT NULL,
  built_at TEXT,
  vaults TEXT,                                  -- JSON: the vaults it holds
  missing TEXT,                                 -- JSON: remembered vaults that weren't open when it was written
  stale INTEGER NOT NULL DEFAULT 1
);
-- passkey quick unlock (SPEC §12.5)
CREATE TABLE IF NOT EXISTS quick_unlock (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  credential_id TEXT NOT NULL,
  label TEXT NOT NULL,
  prf_salt TEXT NOT NULL,
  wrapped TEXT NOT NULL,
  key_epoch INTEGER NOT NULL,
  failures INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  last_used TEXT
);
"""


def new_id() -> str:
    return uuid.uuid4().hex


def _connect() -> sqlite3.Connection:
    return db_core.connect(config.DB_PATH, timeout=30, pragmas=("foreign_keys = ON",))


def get_conn():
    """Commits when the block ends, rolls back on an error, always closes (app/common/db_core.py)."""
    return db_core.transaction(_connect)


def init_db() -> None:
    os.makedirs(os.path.dirname(config.DB_PATH) or ".", exist_ok=True)
    os.makedirs(config.VAULT_DIR, exist_ok=True)
    conn = _connect()
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.commit()
    finally:
        conn.close()


# Columns that older databases lack, added at start-up: (table, column, definition)
MIGRATIONS = [
    ("users", "breach_check", "INTEGER NOT NULL DEFAULT 0"),
    ("users", "download_reminder_days", "INTEGER NOT NULL DEFAULT 0"),
    ("users", "security_alerts", "INTEGER NOT NULL DEFAULT 1"),
    ("users", "expiry_alerts", "INTEGER NOT NULL DEFAULT 1"),
    ("users", "hide_lock_seconds", "INTEGER NOT NULL DEFAULT -1"),
    ("users", "last_typed_unlock", "TEXT"),
    ("users", "master_epoch", "INTEGER NOT NULL DEFAULT 1"),
    ("vaults", "mirror_fp", "TEXT"),
]


def _migrate(conn) -> None:
    db_core.add_missing_columns(conn, MIGRATIONS)


def get_setting(conn, key: str, default=None):
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn, key: str, value: str) -> None:
    conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (key, value))


def audit(conn, action: str, user_id: str | None = None, vault_id: str | None = None, actor_id: str | None = None):
    """No contents, ever: an action, who it's about, who did it, and when."""
    conn.execute("INSERT INTO audit_log (id, vault_id, user_id, actor_id, action, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                 (new_id(), vault_id, user_id, actor_id or user_id, action, config.now_iso()))
