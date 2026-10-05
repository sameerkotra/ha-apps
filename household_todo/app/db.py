"""Thin sqlite3 wrapper — deliberately no ORM. Every call gets its own
short-lived connection (sqlite3.connect is cheap); WAL mode lets reads and
writes overlap.

`PRAGMA foreign_keys = ON` is issued on EVERY connection: SQLite doesn't
enforce ON DELETE CASCADE / SET NULL otherwise, and the setting isn't stored
in the file (SPEC §5).
"""
import json
import logging
import os
import re
import sqlite3
import threading
import uuid

from . import config
from .common import app_bus, backup_core, db_core

logger = logging.getLogger("db")

os.makedirs(config.DATA_DIR, exist_ok=True)


def normalize_address(address: str | None) -> str:
    """Collapse any run of whitespace (spaces, tabs, newlines) into a single
    space and fold case, so "123 Main St" / "123  Main   St" / "123 Main\nSt"
    / "123 MAIN ST" all compare equal (§8h — a place's address is unique
    once normalized this way). Registered as the SQL function normalize_addr
    on every connection below, so the same rule is enforced both in the
    uniqueness index and in the friendlier app-level pre-check."""
    return " ".join((address or "").split()).lower()

_import_lock = threading.Lock()

# Everyone's daily digest time until they pick their own.
DEFAULT_DIGEST_TIME = "08:00"
_TIME_RE = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")  # serialises a DB-file swap against concurrent writers


def new_id() -> str:
    return uuid.uuid4().hex


# deterministic=True is required for use inside an index (idx_places_
# address_norm below); it's true in the ordinary sense too (same input,
# same output, no I/O).
_FUNCTIONS = (("normalize_addr", 1, normalize_address, True),)


def _connect() -> sqlite3.Connection:
    return db_core.connect(config.DB_PATH, timeout=10, pragmas=("foreign_keys = ON",), functions=_FUNCTIONS)


def get_conn():
    """Commits when the block ends, rolls back on an error, always closes (app/common/db_core.py)."""
    return db_core.transaction(_connect)


SCHEMA = """
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,           -- Home Assistant user id (X-Remote-User-Id)
    name TEXT NOT NULL,
    username TEXT,                 -- HA username (matched by admin_users)
    created_at TEXT NOT NULL,
    disabled INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS lists (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('personal', 'shared')),
    owner_user_id TEXT REFERENCES users(id),  -- set only when kind='personal'; NULL for shared
    created_at TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    list_id TEXT NOT NULL REFERENCES lists(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    notes TEXT,
    due_date TEXT,
    due_time TEXT,
    priority TEXT CHECK (priority IN ('low', 'medium', 'high')),
    type_id TEXT REFERENCES task_types(id) ON DELETE SET NULL,
    place_id TEXT REFERENCES places(id) ON DELETE SET NULL,
    assigned_to TEXT REFERENCES users(id),
    completion_required INTEGER NOT NULL DEFAULT 0 CHECK (completion_required IN (0, 1)),
    completed INTEGER NOT NULL DEFAULT 0,
    completed_at TEXT,
    completed_by TEXT REFERENCES users(id),
    created_by TEXT NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    url TEXT,                               -- optional http(s) link; also ALTERed in by _migrate()
    source TEXT                             -- set when another household app added it ("Docs": shown as
                                            -- "from Docs", app_messages.py); also ALTERed in by _migrate()
);

CREATE INDEX IF NOT EXISTS idx_tasks_list ON tasks(list_id);
CREATE INDEX IF NOT EXISTS idx_tasks_due ON tasks(due_date);
CREATE INDEX IF NOT EXISTS idx_tasks_assigned ON tasks(assigned_to);
CREATE INDEX IF NOT EXISTS idx_tasks_completed ON tasks(completed, completed_at);

CREATE TABLE IF NOT EXISTS user_prefs (
    user_id TEXT PRIMARY KEY REFERENCES users(id),
    notifications_enabled INTEGER NOT NULL DEFAULT 0,
    lead_days INTEGER NOT NULL DEFAULT 0 CHECK (lead_days BETWEEN 0 AND 7),
    notify_on_assign INTEGER NOT NULL DEFAULT 1,
    digest_time TEXT                                -- "HH:MM", each person's own digest time;
                                                     -- NULL (never set) counts as 08:00 and is
                                                     -- filled in by _migrate()
);

CREATE TABLE IF NOT EXISTS notification_log (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    kind TEXT NOT NULL CHECK (kind IN ('digest')),
    sent_on TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (user_id, kind, sent_on)
);

CREATE INDEX IF NOT EXISTS idx_tasks_list_pos ON tasks(list_id, position);

-- Per-user "5 hours before / 1 hour before" style offsets applied to that
-- user's own tasks with a due time. A brand-new table
-- rather than columns on user_prefs: this file has no ALTER-TABLE migration
-- path, only CREATE TABLE IF NOT EXISTS, so evolving an existing table's
-- shape means adding a new table, never a new column.
CREATE TABLE IF NOT EXISTS user_reminder_offsets (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    minutes_before INTEGER NOT NULL CHECK (minutes_before > 0 AND minutes_before <= 10080),
    created_at TEXT NOT NULL,
    UNIQUE (user_id, minutes_before)
);
CREATE INDEX IF NOT EXISTS idx_reminder_offsets_user ON user_reminder_offsets(user_id);

-- Per-user weekly-summary schedule: each person can pick their own day of
-- week and time, independent of everyone else's.
CREATE TABLE IF NOT EXISTS user_weekly_prefs (
    user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    enabled INTEGER NOT NULL DEFAULT 0,
    day_of_week INTEGER NOT NULL DEFAULT 7 CHECK (day_of_week BETWEEN 1 AND 7),
    time TEXT NOT NULL DEFAULT '18:00'
);

CREATE TABLE IF NOT EXISTS weekly_summary_log (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    sent_on TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (user_id, sent_on)
);

-- One row per (task, user, offset, due date/time) actually sent. Keying on
-- the due date/time too means an edited due date makes a fresh reminder
-- eligible, instead of staying suppressed by a log row that no longer
-- describes the task.
CREATE TABLE IF NOT EXISTS task_reminder_log (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id),
    minutes_before INTEGER NOT NULL,
    due_date TEXT NOT NULL,
    due_time TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (task_id, user_id, minutes_before, due_date, due_time)
);
CREATE INDEX IF NOT EXISTS idx_task_reminder_log_task ON task_reminder_log(task_id);

CREATE TABLE IF NOT EXISTS schedule_items (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    notes TEXT,
    rule TEXT NOT NULL,
    anchor_date TEXT NOT NULL,
    lead_days INTEGER NOT NULL DEFAULT 1 CHECK (lead_days BETWEEN 0 AND 7),
    icon TEXT,
    entity_slug TEXT NOT NULL UNIQUE,
    created_by TEXT REFERENCES users(id),
    created_at TEXT NOT NULL,
    -- personal schedule items (SPEC §5.4). Also ALTERed in by
    -- _migrate(), in this same order, so fresh and migrated DBs match.
    assigned_to TEXT REFERENCES users(id),              -- NULL = household item
    start_time TEXT,                                    -- 'HH:MM'; NULL = all-day item
    end_time TEXT,                                      -- 'HH:MM'; set iff start_time is, and later
    place_id TEXT REFERENCES places(id) ON DELETE SET NULL,
    visibility TEXT NOT NULL DEFAULT 'household' CHECK (visibility IN ('household', 'private')),
    expose_sensor INTEGER NOT NULL DEFAULT 1,           -- per-item switch for the HA binary_sensor
    url TEXT                                            -- optional http(s) link (also in _migrate())
);

CREATE TABLE IF NOT EXISTS schedule_exceptions (
    id TEXT PRIMARY KEY,
    item_id TEXT NOT NULL REFERENCES schedule_items(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('skip', 'move', 'add')),
    date TEXT NOT NULL,
    to_date TEXT,
    reason TEXT,
    created_by TEXT REFERENCES users(id),
    created_at TEXT NOT NULL,
    CHECK ((kind = 'move') = (to_date IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_sched_exc_orig
    ON schedule_exceptions(item_id, date) WHERE kind IN ('skip', 'move');
CREATE UNIQUE INDEX IF NOT EXISTS idx_sched_exc_add
    ON schedule_exceptions(item_id, date) WHERE kind = 'add';

-- One row per "N before" reminder sent for one occurrence of a timed
-- schedule item. Keyed on the occurrence's date and start time, so moving
-- an occurrence or changing the item's times re-arms it (like
-- task_reminder_log).
CREATE TABLE IF NOT EXISTS schedule_reminder_log (
    id TEXT PRIMARY KEY,
    item_id TEXT NOT NULL REFERENCES schedule_items(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id),
    minutes_before INTEGER NOT NULL,
    date TEXT NOT NULL,
    start_time TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (item_id, user_id, minutes_before, date, start_time)
);
CREATE INDEX IF NOT EXISTS idx_sched_reminder_log_item ON schedule_reminder_log(item_id);

CREATE TABLE IF NOT EXISTS task_types (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    icon TEXT,
    color TEXT,
    position INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS places (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    address TEXT NOT NULL,
    phone TEXT,                    -- optional (§8h)
    lat REAL,                      -- geocoded from `address` (§8n); NULL until
    lon REAL,                      -- computed, or if geocoding never succeeds
    drive_minutes INTEGER,         -- driving time in minutes from home (§8n);
                                    -- NULL until computed, or if routing failed
    drive_checked_at TEXT,         -- last computation attempt (success or not);
                                    -- reset to NULL whenever `address` changes
    drive_mode TEXT,               -- 'no_tolls' / 'fastest': the avoid_tolls setting
                                    -- the cached estimate was computed under
    drive_tolls_avoided INTEGER,   -- 1 if that route really has no toll roads; 0 if
                                    -- tolls were unavoidable (or not being avoided)
    created_by TEXT REFERENCES users(id),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_items (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    text TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0,
    position INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_task_items_task ON task_items(task_id, position);
-- App settings (Admin → App settings; settings.py). One row per key
-- that has been set; a missing key uses settings.DEFAULTS.
CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,          -- JSON
    updated_at TEXT NOT NULL,
    updated_by TEXT                -- HA login name / id of the admin, NULL for imports
);

-- notify services assigned to a person by an admin (Admin → Users),
-- in addition to their phones from Home Assistant. Keyed by the HA user id — stable, and
-- not something a person can edit about themselves (the display name never
-- counts). A person may have several services; reminders go to all of them.
CREATE TABLE IF NOT EXISTS user_notify (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    service TEXT NOT NULL,         -- "notify.<name>", name matching ^[a-z0-9_]+$
    created_at TEXT NOT NULL,
    created_by TEXT,               -- admin's login name / id; NULL for the import
    PRIMARY KEY (user_id, service)
);

CREATE INDEX IF NOT EXISTS idx_tasks_type ON tasks(type_id);
CREATE INDEX IF NOT EXISTS idx_tasks_place ON tasks(place_id);

-- Maintenance (SPEC §14). Recurring upkeep: repeats every N days/weeks/months/years after it
-- was last done ('interval'), or on fixed dates using the schedule rule grammar ('calendar').
CREATE TABLE IF NOT EXISTS maint_items (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    icon TEXT,                          -- an emoji
    category TEXT NOT NULL,
    notes TEXT,
    howto TEXT,
    url TEXT,
    mode TEXT NOT NULL CHECK (mode IN ('interval', 'calendar')),
    every_n INTEGER,                    -- interval
    every_unit TEXT CHECK (every_unit IN ('day', 'week', 'month', 'year')),
    rule TEXT,                          -- calendar
    anchor_date TEXT,
    last_done TEXT,                     -- interval: when it was last done (NULL = never)
    start_date TEXT,                    -- interval, never done: when it is first due
    cleared_through TEXT,               -- calendar: the last occurrence marked done
    lead_days INTEGER NOT NULL DEFAULT 7 CHECK (lead_days BETWEEN 0 AND 60),
    overdue_every INTEGER NOT NULL DEFAULT 7 CHECK (overdue_every IN (0, 3, 7, 14)),
    place_id TEXT REFERENCES places(id) ON DELETE SET NULL,
    assigned_to TEXT REFERENCES users(id),
    recipients_mode TEXT NOT NULL DEFAULT 'default' CHECK (recipients_mode IN ('default', 'custom')),
    paused INTEGER NOT NULL DEFAULT 0,
    snooze_due TEXT,                    -- the due date a snooze applies to
    snooze_to TEXT,
    expose_sensor INTEGER NOT NULL DEFAULT 0,
    entity_slug TEXT NOT NULL UNIQUE,
    suggestion_key TEXT,                -- 'b:<builtin key>' or 'c:<custom id>' it was added from
    folder TEXT,                        -- its folder in the maintenance files folder, once it has files
    created_by TEXT REFERENCES users(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS maint_recipients (
    item_id TEXT NOT NULL REFERENCES maint_items(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    PRIMARY KEY (item_id, user_id)
);

-- Every Mark done, kept for good. prev_state (JSON) is what Undo puts back.
CREATE TABLE IF NOT EXISTS maint_done (
    id TEXT PRIMARY KEY,
    item_id TEXT NOT NULL REFERENCES maint_items(id) ON DELETE CASCADE,
    done_on TEXT NOT NULL,
    done_by TEXT REFERENCES users(id),
    note TEXT,
    cost_cents INTEGER,
    due_was TEXT,
    prev_state TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_maint_done_item ON maint_done(item_id, done_on);

-- Files uploaded through the app into the maintenance files folder. Exactly one owner: an item (manuals,
-- warranty papers), a Mark done record (receipt, photo) or a one-off job (a task in the Maintenance list).
-- No foreign keys: a purged job's files stay in the folder; the rows go.
CREATE TABLE IF NOT EXISTS maint_files (
    id TEXT PRIMARY KEY,
    item_id TEXT,
    done_id TEXT,
    task_id TEXT,
    rel_path TEXT NOT NULL,             -- relative to the files folder
    name TEXT NOT NULL,
    size INTEGER NOT NULL,
    mime TEXT,
    thumb TEXT,                         -- relative path of a small preview, for photos
    uploaded_by TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_maint_files_item ON maint_files(item_id);
CREATE INDEX IF NOT EXISTS idx_maint_files_done ON maint_files(done_id);
CREATE INDEX IF NOT EXISTS idx_maint_files_task ON maint_files(task_id);

-- Suggestions an admin added (the built-in ones are in maint_catalog.py).
CREATE TABLE IF NOT EXISTS maint_suggestions (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    icon TEXT,
    category TEXT NOT NULL,
    needs TEXT,
    every_n INTEGER,
    every_unit TEXT,
    seasons TEXT,                       -- JSON list, or NULL for "every N … after last done"
    why TEXT,
    howto TEXT,
    who TEXT NOT NULL DEFAULT 'diy',
    minutes INTEGER,
    created_by TEXT,
    created_at TEXT NOT NULL
);

-- "Not for us": suggestions hidden for the household ('b:<key>' / 'c:<id>').
CREATE TABLE IF NOT EXISTS maint_hidden (
    key TEXT PRIMARY KEY,
    hidden_by TEXT,
    created_at TEXT NOT NULL
);

-- What was sent, so nothing repeats after a restart: kind soon|today|overdue|job|season.
CREATE TABLE IF NOT EXISTS maint_notify_log (
    id TEXT PRIMARY KEY,
    ref TEXT NOT NULL,                  -- item id, task id, or season key ('2026-autumn')
    user_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    due_date TEXT NOT NULL,
    sent_on TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (ref, user_id, kind, due_date, sent_on)
);
"""

# What a backup must contain to be restorable. Deliberately the oldest set:
# schedule_reminder_log is created by init_db() right after a
# restore, so older backups stay importable.
REQUIRED_TABLES = {
    "users", "lists", "tasks", "user_prefs", "notification_log",
    "schedule_items", "schedule_exceptions", "task_types", "places", "task_items",
    "user_reminder_offsets", "user_weekly_prefs", "weekly_summary_log", "task_reminder_log",
}

DEFAULT_TASK_TYPES = ["Appointment", "Doctor appointment", "Errand", "Chore", "Bill"]

# `PRAGMA user_version` marks that first-run seeding has happened, so a type
# (or the Household list) the household deleted is never brought back.
_SEEDED_VERSION = 1

# Bumped every time the database file is (re)initialised — at startup and
# after a restore — so caches of DB-backed values (settings.py) know to reload.
_generation = 0


def generation() -> int:
    return _generation


def init_db() -> None:
    global _generation
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)
        _seed_first_run(conn)
        app_bus.migrate(conn)          # messages between the household apps (bus_outbox, bus_seen, bus_apps)
    _generation += 1


# Columns older databases lack: (table, column, definition), added in this order.
MIGRATIONS = [
    ("places", "phone", "TEXT"),
    ("places", "lat", "REAL"),
    ("places", "lon", "REAL"),
    ("places", "drive_minutes", "INTEGER"),
    ("places", "drive_checked_at", "TEXT"),
    ("places", "drive_mode", "TEXT"),
    ("places", "drive_tolls_avoided", "INTEGER"),
    ("user_prefs", "digest_time", "TEXT"),
    ("user_prefs", "maintenance_notify", "INTEGER NOT NULL DEFAULT 1"),   # a recipient can mute maintenance
    ("lists", "role", "TEXT"),                                           # 'maintenance' marks the Maintenance list
    ("tasks", "url", "TEXT"),                                            # optional link
    ("tasks", "source", "TEXT"),                                         # "Docs": added by another app
    # personal schedule items. The defaults keep every existing item
    # exactly as it was: household, all-day, no place, published.
    ("schedule_items", "assigned_to", "TEXT REFERENCES users(id)"),
    ("schedule_items", "start_time", "TEXT"),
    ("schedule_items", "end_time", "TEXT"),
    ("schedule_items", "place_id", "TEXT REFERENCES places(id) ON DELETE SET NULL"),
    ("schedule_items", "visibility", "TEXT NOT NULL DEFAULT 'household' CHECK (visibility IN ('household', 'private'))"),
    ("schedule_items", "expose_sensor", "INTEGER NOT NULL DEFAULT 1"),
    ("schedule_items", "url", "TEXT"),                                   # optional link
]


def _migrate(conn: sqlite3.Connection) -> None:
    """Additive, idempotent migrations for installs that predate a column or
    index — this schema otherwise only ever uses CREATE TABLE/INDEX IF NOT
    EXISTS, which can't add a column to an already-existing table. No
    migration framework dependency; mirrors Calorie Tracker's db._migrate."""
    db_core.add_missing_columns(conn, MIGRATIONS)

    # Each person has their own digest time. In older databases a NULL
    # digest_time meant "the household's reminder_time"; fill it with that
    # value (if one is stored in app_settings) so nobody's digest time jumps,
    # else 08:00, then drop the old setting. Idempotent: later runs find no
    # NULLs and no row.
    fill = DEFAULT_DIGEST_TIME
    row = conn.execute("SELECT value FROM app_settings WHERE key = 'reminder_time'").fetchone()
    if row:
        try:
            v = json.loads(row["value"])
            if isinstance(v, str) and _TIME_RE.match(v):
                fill = v
        except ValueError:
            pass
    conn.execute("UPDATE user_prefs SET digest_time = ? WHERE digest_time IS NULL", (fill,))
    # Leftovers of an older one-time import from options.json.
    conn.execute("DELETE FROM app_settings WHERE key IN ('reminder_time', '_imported')")
    conn.execute("DROP TABLE IF EXISTS user_notify_pending")

    # The "Drive times" switch (App settings). An older database that already
    # uses drive times — a home address, or a place with a computed estimate —
    # keeps them on; anything else (a new install) starts with them off. The
    # second insert records that decision once, so it is never re-made later
    # (e.g. after an admin turned the switch off, or typed a home address).
    now = config.now_iso()
    conn.execute(
        "INSERT OR IGNORE INTO app_settings (key, value, updated_at, updated_by) "
        "SELECT 'drive_times_enabled', 'true', ?, NULL "
        "WHERE EXISTS (SELECT 1 FROM app_settings WHERE key = 'home_address' AND value NOT IN ('\"\"', 'null')) "
        "OR EXISTS (SELECT 1 FROM places WHERE drive_minutes IS NOT NULL)",
        (now,),
    )
    conn.execute(
        "INSERT OR IGNORE INTO app_settings (key, value, updated_at, updated_by) "
        "VALUES ('drive_times_enabled', 'false', ?, NULL)",
        (now,),
    )

    try:
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_places_address_norm "
            "ON places (normalize_addr(address))"
        )
    except sqlite3.IntegrityError:
        # An install from before this check existed already has two places
        # that normalize to the same address. Leave them as they are rather
        # than blocking startup — new duplicates are still caught by the
        # app-level check in routers/places.py either way; only the DB-level
        # backstop is missing until the existing pair is cleaned up by hand.
        logger.warning(
            "Could not create the places-address uniqueness index — two "
            "existing places already share a normalized address."
        )


def _seed_first_run(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version >= _SEEDED_VERSION:
        return
    now = config.now_iso()
    if conn.execute("SELECT COUNT(*) FROM lists").fetchone()[0] == 0:
        conn.execute(
            "INSERT INTO lists (id, name, kind, owner_user_id, created_at, position) "
            "VALUES (?, 'Household', 'shared', NULL, ?, 0)",
            (new_id(), now),
        )
    if conn.execute("SELECT COUNT(*) FROM task_types").fetchone()[0] == 0:
        for pos, name in enumerate(DEFAULT_TASK_TYPES):
            conn.execute(
                "INSERT INTO task_types (id, name, position, created_at) VALUES (?, ?, ?, ?)",
                (new_id(), name, pos, now),
            )
    conn.execute(f"PRAGMA user_version = {_SEEDED_VERSION}")


def backup_to_tempfile() -> str:
    """Online-backup the live database into a fresh temp .db file and return
    its path. Deliberately not a plain file copy: the DB runs in WAL mode, so
    recent commits may still be in the -wal file. sqlite3's backup API yields
    one complete, consistent file. The caller deletes the temp file."""
    return db_core.snapshot_to_tempfile(_connect)


def validate_backup_file(path: str) -> None:
    """Raises ValueError with a user-facing message if `path` isn't safe to
    import: not SQLite, fails an integrity check, or lacks a table this app
    needs (e.g. some other app's database)."""
    # PRAGMA integrity_check evaluates every index, including the
    # expression index on normalize_addr(address) — without this
    # registered, SQLite raises "unknown function" and every backup
    # gets misreported as "not a valid SQLite database".
    def setup(conn):
        conn.create_function("normalize_addr", 1, normalize_address, deterministic=True)

    db_core.validate_file(path, REQUIRED_TABLES, app_name="Household Todo", setup=setup)


def import_from_tempfile(tmp_path: str) -> None:
    """Swap the live database for an already-validated file (call
    validate_backup_file first). Serialised by _import_lock; checkpoints and
    drops the current -wal/-shm sidecars so nothing replays stale WAL frames
    against the new file."""
    # A backup made by an older version may predate columns/tables added
    # since (db._migrate) — init_db brings it up to the current schema right
    # away instead of serving errors until the next restart.
    backup_core.restore_file(tmp_path, config.DB_PATH, get_conn, migrate=init_db, lock=_import_lock)

