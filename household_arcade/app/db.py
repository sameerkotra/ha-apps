"""SQLite at /data/arcade.db (SPEC §9): schema, numbered migrations, backups.

Raw sqlite3, no ORM. Every call gets its own short-lived connection; WAL mode
lets reads and writes overlap; `PRAGMA foreign_keys = ON` on every connection.

Migrations are numbered and run in order, each exactly once, recorded in
`schema_version`. A database from an older version (an older backup being
imported, or an install being updated) is brought up to date by `init_db()`,
which runs at startup and right after an import. A migration is never edited
once released: a change is a new, higher-numbered one.
"""
import logging
import os
import re
import sqlite3
import tempfile
import threading
import uuid
from contextlib import contextmanager

from . import config

logger = logging.getLogger("db")

os.makedirs(config.DATA_DIR, exist_ok=True)

_import_lock = threading.Lock()


def new_id() -> str:
    return uuid.uuid4().hex


def _connect(path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or config.DB_PATH, timeout=10)
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


# ---------------------------------------------------------------------------
# Migrations
# ---------------------------------------------------------------------------
_M1_CORE = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,                 -- Home Assistant user id (X-Remote-User-Id)
    name TEXT NOT NULL,                  -- display name
    username TEXT,                       -- HA login name (matched by admin_users)
    disabled INTEGER NOT NULL DEFAULT 0, -- switched off on Admin → Users: can't play, not on the leaderboard
    look TEXT,                           -- NULL = the household default (App settings)
    sound INTEGER NOT NULL DEFAULT 0,    -- off by default
    handedness TEXT NOT NULL DEFAULT 'right' CHECK (handedness IN ('right', 'left')),
    reduce_motion INTEGER NOT NULL DEFAULT 0,
    receive_notifications INTEGER NOT NULL DEFAULT 1,
    last_seen TEXT,
    created_at TEXT NOT NULL
);

-- Extra notify services an admin assigns (Admin → Users); the shared ha_notify.py reads it.
CREATE TABLE IF NOT EXISTS user_notify (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    service TEXT NOT NULL,
    created_at TEXT NOT NULL,
    created_by TEXT,
    PRIMARY KEY (user_id, service)
);

-- One row per game started (practice too): play time for limits and history,
-- and what a score must belong to.
CREATE TABLE IF NOT EXISTS play_sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    game TEXT NOT NULL,
    mode TEXT NOT NULL,
    practice INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    last_beat_at TEXT NOT NULL,
    active_seconds REAL NOT NULL DEFAULT 0,   -- reported by the browser, capped by wall time
    ended_at TEXT,
    scored INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON play_sessions(user_id, started_at);
CREATE INDEX IF NOT EXISTS idx_sessions_open ON play_sessions(ended_at);

-- Finished games. Practice games and games under 3 seconds are never stored.
CREATE TABLE IF NOT EXISTS scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    game TEXT NOT NULL,
    mode TEXT NOT NULL,
    score INTEGER NOT NULL,
    level INTEGER NOT NULL DEFAULT 1,
    seconds INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    app_version TEXT NOT NULL,
    session_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_scores_board ON scores(game, mode, score DESC);
CREATE INDEX IF NOT EXISTS idx_scores_user ON scores(user_id, ended_at);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,          -- JSON
    updated_at TEXT NOT NULL,
    updated_by TEXT
);
"""

_M2_CHILDREN = """
ALTER TABLE users ADD COLUMN is_child INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS child_limits (
    user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    minutes_school INTEGER,              -- NULL = no limit
    minutes_weekend INTEGER,
    quiet_school_from TEXT,              -- "HH:MM"; both NULL = no quiet hours
    quiet_school_to TEXT,
    quiet_weekend_from TEXT,
    quiet_weekend_to TEXT,
    allowed_games TEXT,                  -- JSON list of game ids; NULL = all games
    leaderboard TEXT NOT NULL DEFAULT 'full' CHECK (leaderboard IN ('full', 'first', 'hidden')),
    updated_at TEXT NOT NULL,
    updated_by TEXT
);

CREATE TABLE IF NOT EXISTS extra_time (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    date TEXT NOT NULL,                  -- local date it applies to
    minutes INTEGER NOT NULL,
    given_by TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_extra_user ON extra_time(user_id, date);

-- Dates when children get weekend limits (App settings).
CREATE TABLE IF NOT EXISTS holidays (
    date TEXT PRIMARY KEY,
    name TEXT NOT NULL DEFAULT ''
);

-- Notifications sent once per day (e.g. a child's "5 minutes left"), so a
-- restart doesn't repeat them.
CREATE TABLE IF NOT EXISTS notify_log (
    kind TEXT NOT NULL,
    user_id TEXT NOT NULL,
    sent_on TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (kind, user_id, sent_on)
);
"""

_M3_LEVELS = """
-- Level lists (levels.py): built-in levels and those the AI model made.
CREATE TABLE IF NOT EXISTS levels (
    id TEXT PRIMARY KEY,
    game TEXT NOT NULL,                  -- the level list: brick, snake (Maze)
    number INTEGER NOT NULL,             -- 1, 2, 3 … in the list; never reused
    name TEXT NOT NULL,
    data TEXT NOT NULL,                  -- JSON, checked by levels.validate
    source TEXT NOT NULL CHECK (source IN ('builtin', 'ai')),
    status TEXT NOT NULL CHECK (status IN ('ready', 'waiting', 'retired')),
    difficulty INTEGER NOT NULL DEFAULT 0,
    fingerprint TEXT NOT NULL,           -- the same for a layout and its mirror image
    model TEXT,
    build_id TEXT,
    created_at TEXT NOT NULL,
    approved_by TEXT,
    UNIQUE (game, number)
);

-- Builds of more levels (Admin → Levels, or automatic).
CREATE TABLE IF NOT EXISTS level_builds (
    id TEXT PRIMARY KEY,
    game TEXT NOT NULL,
    requested_by TEXT NOT NULL,          -- a person's name, or "auto"
    count INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'done', 'failed')),
    made INTEGER NOT NULL DEFAULT 0,
    rejected INTEGER NOT NULL DEFAULT 0,
    requests INTEGER NOT NULL DEFAULT 0,
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    model TEXT,
    error TEXT,
    log TEXT,                            -- JSON list of short lines (why levels were turned down)
    created_at TEXT NOT NULL,
    started_at TEXT,
    ended_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_builds_game ON level_builds(game, created_at);

-- How many levels a game was given when it began, so a result's level can be checked.
ALTER TABLE play_sessions ADD COLUMN level_count INTEGER
"""

_M4_SAVES = """
-- Built-in levels are known by their key (brick:1 …), not their number, so levels can be reordered.
ALTER TABLE levels ADD COLUMN builtin_key TEXT;
UPDATE levels SET builtin_key = game || ':' || number WHERE source = 'builtin' AND builtin_key IS NULL;

-- A game put aside to finish later: at most one per person and game.
CREATE TABLE IF NOT EXISTS saved_games (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    game TEXT NOT NULL,
    mode TEXT NOT NULL,
    practice INTEGER NOT NULL DEFAULT 0,
    state TEXT NOT NULL,                 -- JSON from the game's rules (data only)
    state_version INTEGER NOT NULL,      -- the game's save format; another one can't be continued
    levels TEXT,                         -- JSON: the level list the game plays
    score INTEGER NOT NULL,
    level INTEGER NOT NULL,
    seconds INTEGER NOT NULL,            -- played so far
    level_count INTEGER,
    started_at TEXT NOT NULL,            -- when the game first began
    saved_at TEXT NOT NULL,
    session_id TEXT,                     -- the session that saved it last
    app_version TEXT NOT NULL,
    PRIMARY KEY (user_id, game)
);

-- A continued game: the seconds it had already been played, and which save it came from.
ALTER TABLE play_sessions ADD COLUMN base_seconds REAL NOT NULL DEFAULT 0;
ALTER TABLE play_sessions ADD COLUMN resumed INTEGER NOT NULL DEFAULT 0;
ALTER TABLE play_sessions ADD COLUMN first_started_at TEXT
"""

# Every request to the AI model (Admin → AI usage, SPEC §11.10).
_M5_AI_CALLS = """
CREATE TABLE IF NOT EXISTS ai_calls (
    id TEXT PRIMARY KEY,
    at TEXT NOT NULL,                    -- UTC ISO time the request was made
    purpose TEXT NOT NULL,               -- build | test
    game TEXT,                           -- the level list, for builds
    build_id TEXT,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    ok INTEGER NOT NULL,
    error TEXT,
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    ms INTEGER NOT NULL DEFAULT 0,
    levels_made INTEGER NOT NULL DEFAULT 0,
    levels_rejected INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_ai_calls_at ON ai_calls(at);
"""

# (number, description, SQL). Append only.
MIGRATIONS: list[tuple[int, str, str]] = [
    (1, "people, play sessions, scores, app settings", _M1_CORE),
    (2, "children: limits, extra time, holidays", _M2_CHILDREN),
    (3, "level lists and level builds", _M3_LEVELS),
    (4, "reorderable levels, saved games", _M4_SAVES),
    (5, "AI usage", _M5_AI_CALLS),
]
LATEST = MIGRATIONS[-1][0]

# What a backup must contain to be importable: the oldest schema (migration 1).
REQUIRED_TABLES = {"users", "scores", "play_sessions", "app_settings", "schema_version"}

# Bumped every time the database file is (re)initialised — at startup and
# after an import — so caches of DB-backed values (settings.py) reload.
_generation = 0


def generation() -> int:
    return _generation


def schema_version(conn) -> int:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    return row["v"] or 0


def _column_exists_already(conn, stmt: str) -> bool:
    words = stmt.split()
    if len(words) >= 6 and [w.upper() for w in words[:2]] == ["ALTER", "TABLE"] \
            and [w.upper() for w in words[3:5]] == ["ADD", "COLUMN"]:
        cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({words[2]})")}
        return words[5] in cols
    return False


def migrate(conn, upto: int | None = None) -> int:
    """Run every migration newer than the database's version (up to `upto`,
    for tests). Returns the version reached."""
    current = schema_version(conn)
    for number, desc, sql in MIGRATIONS:
        if number <= current or (upto is not None and number > upto):
            continue
        logger.info("Database migration %d: %s", number, desc)
        plain = re.sub(r"--[^\n]*", "", sql)          # comments may contain ';'
        for stmt in [s.strip() for s in plain.split(";") if s.strip()]:
            if _column_exists_already(conn, stmt):
                continue          # a migration interrupted half-way is safe to run again
            conn.execute(stmt)
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (number,))
        current = number
    return current


def init_db() -> None:
    global _generation
    from . import levels      # levels needs db; imported here to keep the import order simple
    with get_conn() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        migrate(conn)
        levels.seed_builtins(conn)
        # a build the app was running when it stopped won't finish
        conn.execute("UPDATE level_builds SET status = 'failed', error = 'The app restarted before it finished.', "
                     "ended_at = ? WHERE status IN ('queued', 'running')", (config.now_iso(),))
    _generation += 1


# ---------------------------------------------------------------------------
# Backup / import (Admin → Storage)
# ---------------------------------------------------------------------------

def backup_to_tempfile() -> str:
    """Online-backup the live database into a fresh temp .db file and return
    its path (never a plain copy: recent commits may still be in the -wal
    file). The caller deletes it."""
    fd, tmp_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    src = _connect()
    try:
        dst = sqlite3.connect(tmp_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return tmp_path


def validate_backup_file(path: str) -> None:
    """Raises ValueError with a readable message if `path` isn't safe to
    import: not SQLite, damaged, another app's database, or from a newer
    version of this app."""
    try:
        conn = sqlite3.connect(path)
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise ValueError(f"That file failed a database integrity check ({integrity}) — refusing to import it.")
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            missing = REQUIRED_TABLES - tables
            if missing:
                raise ValueError("That doesn't look like a Household Arcade database "
                                 f"(missing tables: {', '.join(sorted(missing))}).")
            version = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] or 0
            if version > LATEST:
                raise ValueError("That backup was made by a newer version of Household Arcade. "
                                 "Update the app first, then import it.")
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        raise ValueError("That file isn't a valid SQLite database.")


def import_from_tempfile(tmp_path: str) -> None:
    """Swap the live database for an already-validated file, then migrate it
    to the current schema straight away."""
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
        init_db()
