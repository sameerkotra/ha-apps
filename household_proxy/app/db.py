"""The app's SQLite database, /data/proxy.db (spec/SPEC.md §8).

People (from Home Assistant's identity headers), the sites and who may open them, each site's extra headers (secret:
never sent to a browser, blanked in downloaded backups) and rewrite rules, the optional access log, and the App
settings. Each request gets its own short-lived connection; WAL lets reads and writes overlap.
"""
import os
import sqlite3
import threading

from . import config
from .common import backup_core, db_core

os.makedirs(config.DATA_DIR, exist_ok=True)

_import_lock = threading.Lock()  # serializes a database-file swap against concurrent writers


def _connect() -> sqlite3.Connection:
    return db_core.connect(config.DB_PATH, timeout=10, pragmas=("foreign_keys = ON",))


def get_conn():
    """Commits when the block ends, rolls back on an error, always closes (app/common/db_core.py)."""
    return db_core.transaction(_connect)


SCHEMA = """
PRAGMA journal_mode = WAL;

-- Everyone who has opened the app (Home Assistant identity). enabled = 0: opens no site.
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    username TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- A site: a target on the home network, opened through the proxy at /s/<slug>/ (spec §2–§5).
CREATE TABLE IF NOT EXISTS sites (
    id INTEGER PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    icon TEXT NOT NULL DEFAULT '🌐',
    target TEXT NOT NULL,                 -- scheme://host[:port], no path
    start_path TEXT NOT NULL DEFAULT '/',
    access TEXT NOT NULL DEFAULT 'admins' CHECK (access IN ('admins', 'everyone', 'chosen')),
    trusted_ok_by TEXT,                   -- who confirmed "trusted software only" (§4.3), and when
    trusted_ok_at TEXT,
    spa_mode INTEGER NOT NULL DEFAULT 0,
    show_bar INTEGER NOT NULL DEFAULT 1,
    keep_host INTEGER NOT NULL DEFAULT 0, -- send the browser's Host instead of the target's
    strip_csp INTEGER NOT NULL DEFAULT 0, -- drop the site's own Content-Security-Policy
    pass_user INTEGER NOT NULL DEFAULT 0, -- add Remote-User / X-Forwarded-User (header sign-in)
    allow_public INTEGER NOT NULL DEFAULT 0,
    tls_pem TEXT,                         -- a trusted (self-signed) certificate: only this one is accepted
    tls_fingerprint TEXT,                 -- its SHA-256, for the page
    timeout_s INTEGER NOT NULL DEFAULT 0, -- 0: the App setting
    access_log INTEGER NOT NULL DEFAULT 0,
    position INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS site_people (
    site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL,
    PRIMARY KEY (site_id, user_id)
);

-- Extra request headers added by the proxy (e.g. Authorization). value is secret: never in a response or a log,
-- blanked in downloaded backups.
CREATE TABLE IF NOT EXISTS site_headers (
    site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (site_id, name)
);

-- Optional access log (off by default per site): no bodies, no header values, no query strings.
CREATE TABLE IF NOT EXISTS access_log (
    site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    at TEXT NOT NULL,
    user_id TEXT,
    method TEXT NOT NULL,
    path TEXT NOT NULL,
    status INTEGER NOT NULL,
    bytes INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS access_log_site ON access_log (site_id, at);

-- Admin → App settings (app/settings.py). value is JSON.
CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by TEXT
);
"""

# Columns added after their table was first made (older databases and restored backups get them at start-up).
MIGRATIONS: dict = {}

# Bumped by every init_db() (start-up, restore): caches (settings, sites) drop what they hold when it changes.
generation = 0


def init_db():
    global generation
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        db_core.add_missing_columns(conn, MIGRATIONS)
    generation += 1


def backup_to_tempfile(after=None) -> str:
    """A complete, consistent copy of the live database (WAL included) in a temp file; the caller deletes it."""
    return db_core.snapshot_to_tempfile(_connect, after=after)


REQUIRED_TABLES = {"users", "sites", "site_people", "site_headers", "app_settings"}


def validate_backup_file(path: str) -> None:
    """ValueError with a readable message unless `path` is this app's database."""
    db_core.validate_file(path, REQUIRED_TABLES, app_name=config.APP_TITLE)


def import_from_tempfile(tmp_path: str) -> None:
    """Swap the live database for an already-validated file, then bring it up to date."""
    backup_core.restore_file(tmp_path, config.DB_PATH, get_conn, migrate=init_db, lock=_import_lock)


def blank_header_values(conn) -> None:
    """For a downloaded backup's copy: the extra headers' values (credentials) are emptied, names kept."""
    conn.execute("UPDATE site_headers SET value = ''")
    conn.commit()
