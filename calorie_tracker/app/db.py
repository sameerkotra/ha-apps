"""Thin sqlite3 wrapper — deliberately not using an ORM to keep the
dependency footprint minimal. Each request gets its own short-lived
connection (sqlite3.connect is cheap); WAL mode lets reads and writes
overlap without a global lock.
"""
import os
import sqlite3
import tempfile
import threading
from contextlib import contextmanager

from . import config

os.makedirs(config.DATA_DIR, exist_ok=True)

_import_lock = threading.Lock()  # serializes a DB-file swap against concurrent writers


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def get_conn():
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


SCHEMA = """
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,           -- Home Assistant user id (from X-Remote-User-Id)
    name TEXT NOT NULL,            -- Home Assistant display name
    enabled INTEGER NOT NULL DEFAULT 1,  -- 0 = hidden from the in-app switcher
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS goals (
    user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    calorie_goal REAL NOT NULL DEFAULT 2000,
    protein_goal REAL NOT NULL DEFAULT 150,
    carb_goal REAL NOT NULL DEFAULT 200,
    fat_goal REAL NOT NULL DEFAULT 65,
    starting_weight_kg REAL,
    target_weight_kg REAL
);

CREATE TABLE IF NOT EXISTS weight_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    weight_kg REAL NOT NULL,
    note TEXT
);

CREATE TABLE IF NOT EXISTS saved_foods (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    serving_size REAL NOT NULL DEFAULT 1,
    serving_unit TEXT NOT NULL DEFAULT 'serving',
    calories REAL NOT NULL,
    protein REAL NOT NULL DEFAULT 0,
    carbs REAL NOT NULL DEFAULT 0,
    fat REAL NOT NULL DEFAULT 0,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS food_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    meal_type TEXT NOT NULL DEFAULT 'snack',
    food_name TEXT NOT NULL,
    servings REAL NOT NULL DEFAULT 1,
    calories REAL NOT NULL,
    protein REAL NOT NULL DEFAULT 0,
    carbs REAL NOT NULL DEFAULT 0,
    fat REAL NOT NULL DEFAULT 0,
    saved_food_id INTEGER REFERENCES saved_foods(id) ON DELETE SET NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_food_logs_user_date ON food_logs(user_id, date);
CREATE INDEX IF NOT EXISTS idx_weight_logs_user_date ON weight_logs(user_id, date);

-- Admin → App settings (app/settings.py). One row per key an admin changed;
-- a key with no row uses settings.DEFAULTS. value is JSON. The ai_api_key
-- row is blanked in downloaded backups.
CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,          -- JSON
    updated_at TEXT NOT NULL,
    updated_by TEXT               -- HA user id / name of the admin
);
"""

# Bumped by every init_db() — i.e. at startup and after a restore swaps the
# whole file. settings.py compares it to drop its in-memory cache, so values
# from a restored backup take effect immediately.
generation = 0


def init_db():
    global generation
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)
    generation += 1


def _migrate(conn: sqlite3.Connection):
    """Additive, idempotent migrations for installs that predate a column.
    No migration framework dependency — this app's schema is small enough
    that a couple of guarded ALTER TABLEs are simpler and clearer."""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
    if "enabled" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1")

    sf_cols = {row["name"] for row in conn.execute("PRAGMA table_info(saved_foods)")}
    if "notes" not in sf_cols:
        conn.execute("ALTER TABLE saved_foods ADD COLUMN notes TEXT")

    # Older databases stored the AI server as ollama_url / ollama_model. They
    # become ai_provider = "ollama", ai_url and ai_model (an ai_* value that
    # is already there wins), so an install — or a restored backup — keeps
    # talking to the same Ollama server. Values are JSON strings either way.
    old = {r["key"]: r for r in conn.execute(
        "SELECT key, value, updated_at, updated_by FROM app_settings WHERE key IN ('ollama_url', 'ollama_model')")}
    if old:
        first = next(iter(old.values()))
        conn.execute("INSERT OR IGNORE INTO app_settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, ?)",
                     ("ai_provider", '"ollama"', first["updated_at"], first["updated_by"]))
        for old_key, new_key in (("ollama_url", "ai_url"), ("ollama_model", "ai_model")):
            r = old.get(old_key)
            if r is not None:
                conn.execute("INSERT OR IGNORE INTO app_settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, ?)",
                             (new_key, r["value"], r["updated_at"], r["updated_by"]))
        conn.execute("DELETE FROM app_settings WHERE key IN ('ollama_url', 'ollama_model')")


def backup_to_tempfile(blank_settings: tuple = ()) -> str:
    """Online-backup the live database into a fresh temp .db file and return
    its path. Deliberately not a plain file copy of config.DB_PATH: this DB
    runs in WAL mode (see the schema's `PRAGMA journal_mode = WAL`), so
    recently-committed data can still be sitting in the accompanying -wal
    file rather than the main .db file on disk — a raw copy could silently
    hand back a stale/incomplete snapshot. sqlite3's own online backup API
    (Connection.backup()) produces one complete, consistent file regardless
    of WAL state, safely even against a database still taking writes.

    `blank_settings`: app_settings keys whose value is blanked in the copy
    (the AI access key — a backup file never carries it).

    Caller owns the returned temp file and is responsible for deleting it
    once it's done (e.g. via a FastAPI BackgroundTask after streaming it).
    """
    fd, tmp_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    src = _connect()
    try:
        dst = sqlite3.connect(tmp_path)
        try:
            src.backup(dst)
            if blank_settings:
                dst.execute(f"UPDATE app_settings SET value = '\"\"' WHERE key IN ({','.join('?' * len(blank_settings))})",
                            tuple(blank_settings))
                dst.commit()
        finally:
            dst.close()
    finally:
        src.close()
    return tmp_path


REQUIRED_TABLES = {"users", "goals", "weight_logs", "saved_foods", "food_logs"}


def validate_backup_file(path: str) -> None:
    """Raises ValueError with a user-facing message if `path` isn't safe to
    import as a replacement database: not valid SQLite, fails an integrity
    check, or is missing a table this app relies on (e.g. someone uploaded
    an unrelated .db file, or Splitpot's backup by mistake)."""
    try:
        conn = sqlite3.connect(path)
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise ValueError(f"That file failed a database integrity check ({integrity}) — refusing to import it.")
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            missing = REQUIRED_TABLES - tables
            if missing:
                raise ValueError(f"That doesn't look like a Calorie Tracker database (missing tables: {', '.join(sorted(missing))}).")
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        raise ValueError("That file isn't a valid SQLite database.")


def import_from_tempfile(tmp_path: str) -> None:
    """Swaps the live database for an already-validated file (see
    validate_backup_file — call it first; this function trusts its
    caller). Serialized against concurrent writers via _import_lock, and
    checkpoints + drops the current -wal/-shm sidecars first so nothing
    tries to replay stale WAL frames against the freshly-swapped-in file."""
    with _import_lock:
        try:
            with get_conn() as c:
                c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:
            pass
        os.replace(tmp_path, config.DB_PATH)
        for ext in ("-wal", "-shm"):
            sidecar = config.DB_PATH + ext
            if os.path.exists(sidecar):
                os.remove(sidecar)
        # An older backup may lack columns or settings rows the app now
        # expects (_migrate) — bring it up to date now rather than serving
        # errors until the next restart.
        init_db()


def row_to_dict(row: sqlite3.Row) -> dict:
    return dict(row) if row is not None else None


def rows_to_list(rows) -> list:
    return [dict(r) for r in rows]
