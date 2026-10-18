"""Thin sqlite3 wrapper — deliberately not using an ORM to keep the
dependency footprint minimal. Each request gets its own short-lived
connection (sqlite3.connect is cheap); WAL mode lets reads and writes
overlap without a global lock.
"""
import json
import os
import sqlite3
import threading
from contextlib import contextmanager

from . import config
from .common import backup_core, db_core

os.makedirs(config.DATA_DIR, exist_ok=True)

_import_lock = threading.Lock()  # serializes a DB-file swap against concurrent writers
# Every database use (requests, the Household Assistant's calls, the sensor job) runs inside GATE.work(); a restore
# runs inside GATE.pause(), so it waits for them to finish and nothing writes into the file being replaced.
GATE = db_core.MaintenanceGate()
GATE_TIMEOUT = 60                 # how long a request waits for a restore before giving up


def _connect() -> sqlite3.Connection:
    # synchronous = NORMAL: safe under WAL (SCHEMA) — a power cut can lose the last commits, never the file
    return db_core.connect(config.DB_PATH, timeout=10, pragmas=("foreign_keys = ON", "synchronous = NORMAL"))


def _raw_conn():
    """A transaction outside the gate: only for the restore itself (which holds the pause) and init_db."""
    return db_core.transaction(_connect)


_inside = threading.local()       # how deep this thread is in get_conn blocks


@contextmanager
def _work():
    """GATE.work(), once per thread: a connection opened inside another one's block (e.g. an App setting read
    while a request holds its connection) must not wait for a restore that is itself waiting for that request."""
    depth = getattr(_inside, "depth", 0)
    if depth:
        _inside.depth = depth + 1
        try:
            yield
        finally:
            _inside.depth = depth
        return
    with GATE.work(timeout=GATE_TIMEOUT):
        _inside.depth = 1
        try:
            yield
        finally:
            _inside.depth = 0


@contextmanager
def get_conn():
    """Commits when the block ends, rolls back on an error, always closes (app/common/db_core.py); waits while a
    restore is swapping the file (GATE)."""
    with _work(), db_core.transaction(_connect) as conn:
        yield conn


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

-- Symptoms (the Symptoms tab): what someone felt, when, how bad. Their own only, like the food log.
CREATE TABLE IF NOT EXISTS symptom_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    date TEXT NOT NULL,            -- YYYY-MM-DD, Home Assistant's time zone
    time TEXT NOT NULL,            -- HH:MM
    symptom TEXT NOT NULL,         -- e.g. "Bloating" (as typed, trimmed)
    severity INTEGER NOT NULL DEFAULT 3,   -- 1 (mild) … 5 (severe)
    note TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_symptom_logs_user_date ON symptom_logs(user_id, date);

-- AI requests (estimate, chat) per signed-in person per day, for the daily limit (App settings → AI limits).
-- Kept apart from anything a person can delete, so deleting entries never resets the count.
CREATE TABLE IF NOT EXISTS ai_usage (
    user_id TEXT NOT NULL,
    day TEXT NOT NULL,             -- YYYY-MM-DD in Home Assistant's time zone
    requests INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, day)
);
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
    """Startup, and inside a restore (which holds GATE's pause, so this uses a connection outside the gate)."""
    global generation
    with _raw_conn() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)
    generation += 1


# Columns older databases lack: (table, column, definition), added in this order.
MIGRATIONS = [
    ("users", "enabled", "INTEGER NOT NULL DEFAULT 1"),
    ("saved_foods", "notes", "TEXT"),
    ("users", "assistant_ok", "INTEGER NOT NULL DEFAULT 1"),   # no longer used: App settings decides for everyone
    # (no longer used: App settings' "Publish daily calories" decides for everyone) Was "Publish my calories to Home
    # Assistant" (Goals): off for people who open the app from now on; people who
    # were already here get the household setting's value (_migrate), so nothing changes on the upgrade.
    ("users", "publish_sensor", "INTEGER NOT NULL DEFAULT 0"),
    # Calories are optional: an entry without them stores 0 (so every sum stays a sum of the known ones) and
    # no_calories = 1, and the API gives calories: null. (SQLite can't drop a NOT NULL without rebuilding the table.)
    ("food_logs", "no_calories", "INTEGER NOT NULL DEFAULT 0"),
    ("saved_foods", "no_calories", "INTEGER NOT NULL DEFAULT 0"),
    ("food_logs", "eaten_at", "TEXT"),        # HH:MM when it was eaten (Patterns); NULL on entries from before
    ("food_logs", "tags", "TEXT"),            # food tags, lower case, comma-separated ("dairy,spicy")
    ("saved_foods", "tags", "TEXT"),
]


def _stored_setting(conn, key: str, default):
    """An App setting's stored value read straight from the table (settings.py's cache isn't usable here)."""
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    if row is None:
        return default
    try:
        return json.loads(row["value"])
    except ValueError:
        return default


def _migrate(conn: sqlite3.Connection):
    """Additive, idempotent migrations for installs that predate a column.
    No migration framework dependency — this app's schema is small enough
    that a couple of guarded ALTER TABLEs are simpler and clearer."""
    added = db_core.add_missing_columns(conn, MIGRATIONS)
    if "users.publish_sensor" in added:
        # The daily-calories sensor used to be one switch for everyone: keep publishing for exactly the people it
        # published for (everyone, while that switch was on).
        on = _stored_setting(conn, "expose_daily_calories_sensor", True) is True
        conn.execute("UPDATE users SET publish_sensor = ?", (1 if on else 0,))

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


def backup_to_tempfile(after=None) -> str:
    """Online-backup the live database into a fresh temp .db file and return
    its path. Deliberately not a plain file copy of config.DB_PATH: this DB
    runs in WAL mode (see the schema's `PRAGMA journal_mode = WAL`), so
    recently-committed data can still be sitting in the accompanying -wal
    file rather than the main .db file on disk — a raw copy could silently
    hand back a stale/incomplete snapshot. sqlite3's own online backup API
    (Connection.backup()) produces one complete, consistent file regardless
    of WAL state, safely even against a database still taking writes.

    `after(conn)`: changes the copy before it's closed — the download passes
    settings.REGISTRY.scrub_secrets, so a backup file never carries the AI
    access key.

    Caller owns the returned temp file and is responsible for deleting it
    once it's done (e.g. via a FastAPI BackgroundTask after streaming it).
    """
    return db_core.snapshot_to_tempfile(_connect, after=after, prefix=SNAPSHOT_PREFIX)   # swept if never sent


SNAPSHOT_PREFIX = "calorie-tracker-backup-"


REQUIRED_TABLES = {"users", "goals", "weight_logs", "saved_foods", "food_logs"}


def validate_backup_file(path: str) -> None:
    """Raises ValueError with a user-facing message if `path` isn't safe to
    import as a replacement database: not valid SQLite, fails an integrity
    check, or is missing a table this app relies on (e.g. someone uploaded
    an unrelated .db file, or Splitpot's backup by mistake)."""
    db_core.validate_file(path, REQUIRED_TABLES, app_name="Calorie Tracker")


def import_from_tempfile(tmp_path: str) -> None:
    """Swaps the live database for an already-validated file (see
    validate_backup_file — call it first; this function trusts its
    caller). Serialized against concurrent writers via _import_lock, and
    checkpoints + drops the current -wal/-shm sidecars first so nothing
    tries to replay stale WAL frames against the freshly-swapped-in file."""
    # An older backup may lack columns or settings rows the app now
    # expects (_migrate) — init_db brings it up to date now rather than
    # serving errors until the next restart.
    with GATE.pause(timeout=GATE_TIMEOUT):    # waits for every request and job using the database, holds new ones
        backup_core.restore_file(tmp_path, config.DB_PATH, _raw_conn, migrate=init_db, lock=_import_lock)


def row_to_dict(row: sqlite3.Row) -> dict:
    return dict(row) if row is not None else None


def rows_to_list(rows) -> list:
    return [dict(r) for r in rows]
