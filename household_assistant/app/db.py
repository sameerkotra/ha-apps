"""The assistant's SQLite database, /data/assistant.db (HOUSEHOLD_ASSISTANT_SPEC.md §8.1).

It holds no household data of its own: the people who may ask, their questions and the answers (kept 30 days),
the tool calls made for each answer (what each app returned, shown under "What was shared"), the catalogue each
app offered, the App settings, the daily usage counts, and the app bus's own tables (app_bus.migrate).

Each request gets its own short-lived connection; WAL lets reads and writes overlap.
"""
import os
import sqlite3
import threading

from . import config
from .common import app_bus, backup_core, db_core

os.makedirs(config.DATA_DIR, exist_ok=True)

_import_lock = threading.Lock()  # serializes a database-file swap against concurrent writers


def _connect() -> sqlite3.Connection:
    return db_core.connect(config.DB_PATH, timeout=10, pragmas=("foreign_keys = ON",))


def get_conn():
    """Commits when the block ends, rolls back on an error, always closes (app/common/db_core.py)."""
    return db_core.transaction(_connect)


SCHEMA = """
PRAGMA journal_mode = WAL;

-- Everyone who has opened the app (Home Assistant identity). enabled = 0: may not ask. is_child: asks only
-- when "Children may ask" is on, and then only `person` tools.
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    is_child INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS questions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    asked_at TEXT NOT NULL,
    text TEXT NOT NULL,
    answer TEXT,
    state TEXT NOT NULL CHECK (state IN ('planning','calling','answering','done','failed','stopped')),
    error TEXT,
    rounds INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER,
    output_tokens INTEGER,
    seconds REAL
);
CREATE INDEX IF NOT EXISTS questions_user ON questions (user_id, asked_at);

-- One row per tool call made for a question, and per action the model proposed ('proposed' until tapped).
CREATE TABLE IF NOT EXISTS calls (
    id TEXT PRIMARY KEY,
    question_id TEXT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    app TEXT NOT NULL,
    tool TEXT NOT NULL,
    args TEXT NOT NULL,
    say TEXT,                       -- an action's proposal in words ("Add milk to Shopping")
    bus_id TEXT,                    -- the assist.tool.call message's id
    round INTEGER NOT NULL DEFAULT 0,
    confirmed_at TEXT,
    sent_at TEXT,
    answered_at TEXT,
    state TEXT NOT NULL CHECK (state IN ('proposed','sent','ok','nack','timeout')),
    result TEXT,                    -- the app's result (JSON), as it came
    reason TEXT                     -- a refusal: "<reason>[:<detail>]"
);
CREATE INDEX IF NOT EXISTS calls_question ON calls (question_id);
CREATE INDEX IF NOT EXISTS calls_bus ON calls (bus_id);

-- What each app offered (assist.tools.list): the app's own switch (`app_on`), the admin's switch here
-- (`enabled`), and a problem to show on the admin page.
CREATE TABLE IF NOT EXISTS assist_apps (
    app TEXT PRIMARY KEY,
    name TEXT,
    app_version TEXT,
    learned_at TEXT,
    app_on INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1,
    error TEXT
);
CREATE TABLE IF NOT EXISTS assist_tools (
    app TEXT NOT NULL,
    name TEXT NOT NULL,
    app_version TEXT,
    spec TEXT NOT NULL,
    learned_at TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (app, name)
);

-- Admin → Usage: counts per day (UTC), kept when people clear their questions.
CREATE TABLE IF NOT EXISTS usage_days (
    day TEXT PRIMARY KEY,
    questions INTEGER NOT NULL DEFAULT 0,
    calls INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0
);

-- Admin → App settings (app/settings.py). value is JSON; the ai_api_key row is blanked in downloaded backups.
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
        app_bus.migrate(conn)
    generation += 1


def backup_to_tempfile(after=None) -> str:
    """A complete, consistent copy of the live database (WAL included) in a temp file; the caller deletes it."""
    return db_core.snapshot_to_tempfile(_connect, after=after)


REQUIRED_TABLES = {"users", "questions", "calls", "assist_tools", "app_settings"}


def validate_backup_file(path: str) -> None:
    """ValueError with a readable message unless `path` is this app's database."""
    db_core.validate_file(path, REQUIRED_TABLES, app_name="Household Assistant")


def import_from_tempfile(tmp_path: str) -> None:
    """Swap the live database for an already-validated file, then bring it up to date."""
    backup_core.restore_file(tmp_path, config.DB_PATH, get_conn, migrate=init_db, lock=_import_lock)
