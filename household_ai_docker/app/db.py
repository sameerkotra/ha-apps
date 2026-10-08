"""The app's SQLite database, /data/household_ai.db (SPEC.md §7).

No prompts, images or answers are ever stored: only the access keys (as SHA-256 hashes, used only while
*Require an access key* is on), usage per caller per model per day (kept 30 days), the callers seen (name,
address, last use) and the App settings. The models themselves live in /data/models and are left out of
Home Assistant's backups (config.yaml's backup_exclude); this database is in them.

Each request gets its own short-lived connection; WAL lets reads and writes overlap.
"""
import os
import sqlite3
import threading

from . import config
from .common import backup_core, db_core

os.makedirs(config.DATA_DIR, exist_ok=True)

_import_lock = threading.Lock()  # serializes a database-file swap against concurrent writers


def _connect() -> sqlite3.Connection:
    return db_core.connect(config.DB_PATH, timeout=10)


def get_conn():
    """Commits when the block ends, rolls back on an error, always closes (app/common/db_core.py)."""
    return db_core.transaction(_connect)


SCHEMA = """
PRAGMA journal_mode = WAL;

-- Access keys (only used with "Require an access key" on). The key itself is shown once and never stored.
-- models: JSON list of model names the key may use, NULL = all.
CREATE TABLE IF NOT EXISTS model_keys (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    key_hash TEXT NOT NULL UNIQUE,
    models TEXT,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    last_used_at TEXT,
    revoked_at TEXT
);

-- Usage per caller per model per day (Home Assistant's time zone). caller: a key id with keys on, else the
-- app's slug when its host name matched (SPEC.md §4), else "addr:<address>".
CREATE TABLE IF NOT EXISTS model_usage (
    day TEXT NOT NULL,
    caller TEXT NOT NULL,
    model TEXT NOT NULL,
    requests INTEGER NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    seconds REAL NOT NULL,
    waited REAL NOT NULL,
    PRIMARY KEY (day, caller, model)
);

-- Every caller seen: the page's "Use it in other apps" rows.
CREATE TABLE IF NOT EXISTS model_callers (
    caller TEXT PRIMARY KEY,
    address TEXT,
    first_seen_at TEXT NOT NULL,
    last_used_at TEXT NOT NULL
);

-- Admin → App settings (app/settings.py). value is JSON.
CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by TEXT
);
"""

# Bumped by every init_db() (start-up, restore): settings.py drops its cache when it changes.
generation = 0


def init_db():
    global generation
    with get_conn() as conn:
        conn.executescript(SCHEMA)
    generation += 1


def backup_to_tempfile(after=None) -> str:
    """A complete, consistent copy of the live database (WAL included) in a temp file; the caller deletes it."""
    return db_core.snapshot_to_tempfile(_connect, after=after)


REQUIRED_TABLES = {"model_keys", "model_usage", "app_settings"}


def validate_backup_file(path: str) -> None:
    """ValueError with a readable message unless `path` is this app's database."""
    db_core.validate_file(path, REQUIRED_TABLES, app_name="Household AI")


def import_from_tempfile(tmp_path: str) -> None:
    """Swap the live database for an already-validated file, then bring it up to date."""
    backup_core.restore_file(tmp_path, config.DB_PATH, get_conn, migrate=init_db, lock=_import_lock)
