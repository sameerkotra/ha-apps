"""SQLite storage (/data/family.db, WAL). One schema, created idempotently at
startup; `_migrate()` adds anything newer to an older database.

Media files are NOT here — they live under the media_path on /share (media.py).
"""
import logging
import os
import sqlite3
import tempfile
import threading
import uuid
from contextlib import contextmanager

from . import config

logger = logging.getLogger("db")

_import_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  username TEXT,
  ha_person TEXT,
  disabled INTEGER NOT NULL DEFAULT 0,
  me_person_id TEXT REFERENCES people(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL,
  last_seen TEXT,
  export_last TEXT,
  kin_lang TEXT CHECK (kin_lang IN ('en','te','hi')),
  name_display TEXT CHECK (name_display IN ('en','script','both'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_me ON users(me_person_id) WHERE me_person_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS people (
  id TEXT PRIMARY KEY,
  given_names TEXT,
  surname TEXT,
  birth_surname TEXT,
  nickname TEXT,
  other_names TEXT,
  gender TEXT NOT NULL DEFAULT 'unknown' CHECK (gender IN ('female','male','other','unknown')),
  deceased INTEGER NOT NULL DEFAULT 0,
  photo_media_id TEXT REFERENCES media(id) ON DELETE SET NULL,
  photo_region_id TEXT REFERENCES media_regions(id) ON DELETE SET NULL,
  merged_into TEXT REFERENCES people(id),                      -- soft-deleted by a merge (§13.4)
  given_local TEXT, surname_local TEXT,                        -- names in Telugu / Hindi script (§13.7)
  name_order TEXT CHECK (name_order IN ('given_first','surname_first')),   -- NULL = App setting
  biography TEXT,
  never_export INTEGER NOT NULL DEFAULT 0,
  remind INTEGER NOT NULL DEFAULT 0,
  gedcom_id TEXT,
  gedcom_extra TEXT,
  created_by TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  deleted_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_people_surname ON people(surname);

CREATE TABLE IF NOT EXISTS families (
  id TEXT PRIMARY KEY,
  partner1_id TEXT REFERENCES people(id),
  partner2_id TEXT REFERENCES people(id),
  kind TEXT NOT NULL DEFAULT 'married' CHECK (kind IN ('married','partners','unknown')),
  ended TEXT CHECK (ended IN ('divorced','separated','widowed')),
  gedcom_id TEXT,
  gedcom_extra TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  deleted_at TEXT,
  CHECK (partner1_id IS NOT NULL OR partner2_id IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS idx_fam_p1 ON families(partner1_id);
CREATE INDEX IF NOT EXISTS idx_fam_p2 ON families(partner2_id);

CREATE TABLE IF NOT EXISTS family_children (
  family_id TEXT NOT NULL REFERENCES families(id) ON DELETE CASCADE,
  child_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
  position INTEGER NOT NULL DEFAULT 0,
  relation TEXT NOT NULL DEFAULT 'birth' CHECK (relation IN ('birth','adopted','step','foster','unknown')),
  PRIMARY KEY (family_id, child_id)
);
CREATE INDEX IF NOT EXISTS idx_fc_child ON family_children(child_id);

CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY,
  person_id TEXT REFERENCES people(id) ON DELETE CASCADE,
  family_id TEXT REFERENCES families(id) ON DELETE CASCADE,
  type TEXT NOT NULL,
  title TEXT,
  date_text TEXT,
  date_y INTEGER, date_m INTEGER, date_d INTEGER,
  date_approx INTEGER NOT NULL DEFAULT 0,
  sort_key TEXT,
  place TEXT,
  description TEXT,
  gedcom_extra TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  CHECK ((person_id IS NULL) <> (family_id IS NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_birth ON events(person_id) WHERE type = 'birth';
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_death ON events(person_id) WHERE type = 'death';
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_marriage ON events(family_id) WHERE type = 'marriage';
CREATE INDEX IF NOT EXISTS idx_events_person ON events(person_id);
CREATE INDEX IF NOT EXISTS idx_events_family ON events(family_id);

CREATE TABLE IF NOT EXISTS media (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('photo','document')),
  title TEXT,
  description TEXT,
  date_text TEXT,
  content_type TEXT NOT NULL,
  size INTEGER NOT NULL,
  sha256 TEXT NOT NULL,
  width INTEGER,
  height INTEGER,
  created_by TEXT,
  created_at TEXT NOT NULL,
  deleted_at TEXT
);
CREATE TABLE IF NOT EXISTS media_links (
  id TEXT PRIMARY KEY,
  media_id TEXT NOT NULL REFERENCES media(id) ON DELETE CASCADE,
  person_id TEXT REFERENCES people(id) ON DELETE CASCADE,
  family_id TEXT REFERENCES families(id) ON DELETE CASCADE,
  event_id TEXT REFERENCES events(id) ON DELETE CASCADE,
  CHECK ((person_id IS NOT NULL) + (family_id IS NOT NULL) + (event_id IS NOT NULL) = 1)
);
CREATE INDEX IF NOT EXISTS idx_ml_media ON media_links(media_id);
-- Photo tagging (§13.2): a box on a photo around one person, as
-- fractions (0–1) of the width and height. A profile photo can be a region.
CREATE TABLE IF NOT EXISTS media_regions (
  id TEXT PRIMARY KEY,
  media_id TEXT NOT NULL REFERENCES media(id) ON DELETE CASCADE,
  person_id TEXT REFERENCES people(id) ON DELETE CASCADE,
  x REAL NOT NULL, y REAL NOT NULL, w REAL NOT NULL, h REAL NOT NULL,
  created_by TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mr_media ON media_regions(media_id);
CREATE INDEX IF NOT EXISTS idx_mr_person ON media_regions(person_id);
CREATE INDEX IF NOT EXISTS idx_ml_person ON media_links(person_id);

CREATE TABLE IF NOT EXISTS stories (
  id TEXT PRIMARY KEY,
  person_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  created_by TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_stories_person ON stories(person_id);

-- History (§7): one user action = one batch; each row write = one change.
CREATE TABLE IF NOT EXISTS batches (
  id TEXT PRIMARY KEY,
  user_id TEXT,
  label TEXT NOT NULL,
  undo_of TEXT,
  undone_by TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_batches_created ON batches(created_at);
CREATE TABLE IF NOT EXISTS changes (
  id TEXT PRIMARY KEY,
  batch_id TEXT NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
  seq INTEGER NOT NULL,
  user_id TEXT,
  entity TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  op TEXT NOT NULL CHECK (op IN ('create','update','delete','restore','link','unlink')),
  before TEXT,
  after TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_changes_batch ON changes(batch_id);
CREATE INDEX IF NOT EXISTS idx_changes_entity ON changes(entity, entity_id);
CREATE TABLE IF NOT EXISTS batch_people (
  batch_id TEXT NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
  person_id TEXT NOT NULL,
  PRIMARY KEY (batch_id, person_id)
);
CREATE INDEX IF NOT EXISTS idx_bp_person ON batch_people(person_id);

CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);

-- App settings (Admin → App settings; settings.py, features.py). Values are JSON;
-- updated_by is the admin's HA user id, NULL for the one-time import.
CREATE TABLE IF NOT EXISTS app_settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  updated_by TEXT
);

-- Reminders (§9). A reminder about someone needs BOTH their own
-- people.remind switch and the user's own reminder_prefs.enabled.
CREATE TABLE IF NOT EXISTS reminder_prefs (
  user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  enabled INTEGER NOT NULL DEFAULT 0,
  send_time TEXT NOT NULL DEFAULT '08:00',
  birthdays INTEGER NOT NULL DEFAULT 1,
  anniversaries INTEGER NOT NULL DEFAULT 1,
  remembrance INTEGER NOT NULL DEFAULT 0,
  lead_days INTEGER NOT NULL DEFAULT 0 CHECK (lead_days BETWEEN 0 AND 14),
  scope TEXT NOT NULL DEFAULT 'all' CHECK (scope IN ('close','all'))
);
CREATE TABLE IF NOT EXISTS reminder_log (
  user_id TEXT NOT NULL,
  sent_on TEXT NOT NULL,
  PRIMARY KEY (user_id, sent_on)
);
-- Notify services per user ("notify.x"), assigned by admins in Admin → Users.
CREATE TABLE IF NOT EXISTS user_notify (
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  service TEXT NOT NULL,
  added_by TEXT,
  added_at TEXT NOT NULL,
  PRIMARY KEY (user_id, service)
);

-- Relationship names (§13.1): the family's own words. Seed terms live
-- in kin.py; a row here overrides one (or adds a key), and deleting it is
-- "Reset to default". Every change is a history batch.
CREATE TABLE IF NOT EXISTS kin_terms (
  lang TEXT NOT NULL CHECK (lang IN ('te','hi')),
  kin_key TEXT NOT NULL,
  term TEXT NOT NULL,
  note TEXT,
  source TEXT NOT NULL DEFAULT 'custom' CHECK (source IN ('seed','custom')),
  updated_by TEXT,
  updated_at TEXT,
  PRIMARY KEY (lang, kin_key)
);

-- Places map (§13.3): where each place text is, cached. Only the
-- place text is ever sent to the geocoder.
CREATE TABLE IF NOT EXISTS place_geo (
  place_key TEXT PRIMARY KEY,
  place TEXT NOT NULL,
  lat REAL, lon REAL,
  source TEXT NOT NULL CHECK (source IN ('geocoder','manual','failed')),
  tries INTEGER NOT NULL DEFAULT 0,
  checked_at TEXT NOT NULL,
  updated_by TEXT
);

-- ---------- details, sources, contacts, tithi, quiz, presets ----------
-- Custom fields (§13.8): admins define, everyone fills in. Values are history entities.
CREATE TABLE IF NOT EXISTS custom_fields (
  id TEXT PRIMARY KEY,
  label TEXT NOT NULL UNIQUE,
  kind TEXT NOT NULL CHECK (kind IN ('text','choice','place','date')),
  choices TEXT,
  applies_to TEXT NOT NULL DEFAULT 'person' CHECK (applies_to IN ('person','family')),
  position INTEGER NOT NULL DEFAULT 0,
  on_card INTEGER NOT NULL DEFAULT 0,
  export_default INTEGER NOT NULL DEFAULT 1,
  archived INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS custom_values (
  id TEXT PRIMARY KEY,
  field_id TEXT NOT NULL REFERENCES custom_fields(id),
  person_id TEXT REFERENCES people(id) ON DELETE CASCADE,
  family_id TEXT REFERENCES families(id) ON DELETE CASCADE,
  value TEXT NOT NULL,
  updated_by TEXT,
  updated_at TEXT NOT NULL,
  CHECK ((person_id IS NULL) <> (family_id IS NULL))
);
CREATE INDEX IF NOT EXISTS idx_cv_person ON custom_values(person_id);
CREATE INDEX IF NOT EXISTS idx_cv_family ON custom_values(family_id);

-- Sources and citations (§13.9)
CREATE TABLE IF NOT EXISTS sources (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('certificate','document','book','website','photo','interview','other')),
  author TEXT, date_text TEXT, repository TEXT, url TEXT, note TEXT,
  media_id TEXT REFERENCES media(id),
  told_by_person_id TEXT REFERENCES people(id),
  gedcom_id TEXT,
  created_by TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT,
  deleted_at TEXT
);
CREATE TABLE IF NOT EXISTS citations (
  id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL REFERENCES sources(id),
  person_id TEXT REFERENCES people(id) ON DELETE CASCADE,
  family_id TEXT REFERENCES families(id) ON DELETE CASCADE,
  event_id TEXT REFERENCES events(id) ON DELETE CASCADE,
  fact TEXT,
  page TEXT,
  quality INTEGER CHECK (quality BETWEEN 0 AND 3),
  note TEXT,
  created_at TEXT NOT NULL,
  CHECK ((person_id IS NOT NULL) + (family_id IS NOT NULL) + (event_id IS NOT NULL) = 1)
);
CREATE INDEX IF NOT EXISTS idx_cit_source ON citations(source_id);
CREATE INDEX IF NOT EXISTS idx_cit_person ON citations(person_id);
CREATE INDEX IF NOT EXISTS idx_cit_event ON citations(event_id);

-- Contact details for living relatives (§13.11)
CREATE TABLE IF NOT EXISTS contacts (
  id TEXT PRIMARY KEY,
  person_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('phone','whatsapp','email','address','other')),
  label TEXT,
  value TEXT NOT NULL,
  position INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_contacts_person ON contacts(person_id);

-- Duplicates (§13.4): "not the same person" dismissals
CREATE TABLE IF NOT EXISTS not_duplicates (
  a TEXT NOT NULL, b TEXT NOT NULL, by_user TEXT, created_at TEXT NOT NULL,
  PRIMARY KEY (a, b), CHECK (a < b)
);

-- Tithi (§13.12) and milestones (§13.15)
CREATE TABLE IF NOT EXISTS event_tithi (
  event_id TEXT PRIMARY KEY REFERENCES events(id) ON DELETE CASCADE,
  masa INTEGER NOT NULL CHECK (masa BETWEEN 1 AND 12),
  paksha TEXT NOT NULL CHECK (paksha IN ('shukla','krishna')),
  tithi INTEGER NOT NULL CHECK (tithi BETWEEN 1 AND 15),
  source TEXT NOT NULL CHECK (source IN ('entered','computed'))
);
CREATE TABLE IF NOT EXISTS tithi_dates (
  event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  year INTEGER NOT NULL,
  date TEXT NOT NULL,
  overridden_by TEXT,
  computed_at TEXT NOT NULL,
  PRIMARY KEY (event_id, year)
);
CREATE TABLE IF NOT EXISTS milestones (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('age','anniversary','full_moons')),
  value INTEGER NOT NULL,
  label_en TEXT NOT NULL, label_te TEXT, label_hi TEXT,
  lead_days TEXT NOT NULL DEFAULT '[90,30,7,0]',
  enabled INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS reminder_extra_log (
  user_id TEXT NOT NULL, item TEXT NOT NULL, sent_on TEXT NOT NULL,
  PRIMARY KEY (user_id, item, sent_on)
);

-- Photo quiz and kids mode (§13.13)
CREATE TABLE IF NOT EXISTS quiz_stats (
  player_person_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
  person_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
  mode TEXT NOT NULL,
  correct INTEGER NOT NULL DEFAULT 0,
  wrong INTEGER NOT NULL DEFAULT 0,
  last_seen TEXT,
  PRIMARY KEY (player_person_id, person_id, mode)
);
CREATE TABLE IF NOT EXISTS kid_sessions (
  device_id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id),
  player_person_id TEXT NOT NULL REFERENCES people(id),
  modes TEXT NOT NULL,
  scope TEXT NOT NULL DEFAULT 'close',
  deceased INTEGER NOT NULL DEFAULT 0,
  started_at TEXT NOT NULL,
  ends_at TEXT,
  failed_unlocks INTEGER NOT NULL DEFAULT 0,
  unlock_blocked_until TEXT,
  challenge TEXT
);

-- Export presets (§13.6.1), shared by everyone
CREATE TABLE IF NOT EXISTS export_presets (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  format TEXT,
  options TEXT NOT NULL,
  created_by TEXT,
  updated_at TEXT NOT NULL
);
"""

REQUIRED_TABLES = {"users", "people", "families", "family_children", "events", "media",
                   "media_links", "batches", "changes", "settings"}


def new_id() -> str:
    return uuid.uuid4().hex


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=15)
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


def _columns(conn, table: str) -> set:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _migrate(conn: sqlite3.Connection) -> None:
    """Additive migrations for databases made by older versions."""
    users = _columns(conn, "users")
    if "last_seen" not in users:
        conn.execute("ALTER TABLE users ADD COLUMN last_seen TEXT")
    if "export_last" not in users:
        conn.execute("ALTER TABLE users ADD COLUMN export_last TEXT")
    if "never_export" not in _columns(conn, "people"):
        conn.execute("ALTER TABLE people ADD COLUMN never_export INTEGER NOT NULL DEFAULT 0")
    if "kin_lang" not in users:                                        # NULL = App setting
        conn.execute("ALTER TABLE users ADD COLUMN kin_lang TEXT CHECK (kin_lang IN ('en','te','hi'))")
    people = _columns(conn, "people")
    for col, ddl in (("given_local", "TEXT"), ("surname_local", "TEXT"),
                     ("name_order", "TEXT CHECK (name_order IN ('given_first','surname_first'))")):
        if col not in people:
            conn.execute(f"ALTER TABLE people ADD COLUMN {col} {ddl}")
    if "name_display" not in users:
        conn.execute("ALTER TABLE users ADD COLUMN name_display TEXT CHECK (name_display IN ('en','script','both'))")
    if "merged_into" not in _columns(conn, "people"):                  # (§13.4)
        conn.execute("ALTER TABLE people ADD COLUMN merged_into TEXT REFERENCES people(id)")
    if "time" not in _columns(conn, "events"):                          # birth / death time "HH:MM"
        conn.execute("ALTER TABLE events ADD COLUMN time TEXT")
    if "kid_pin_hash" not in _columns(conn, "users"):                  # (§13.13)
        conn.execute("ALTER TABLE users ADD COLUMN kid_pin_hash TEXT")
    media_cols = _columns(conn, "media")
    for col, ddl in (("edit", "TEXT"), ("unsorted", "INTEGER NOT NULL DEFAULT 0"),      # (§13.19, §13.10)
                     ("orig_name", "TEXT"), ("orig_sha256", "TEXT"), ("edit_version", "INTEGER NOT NULL DEFAULT 0")):
        if col not in media_cols:
            conn.execute(f"ALTER TABLE media ADD COLUMN {col} {ddl}")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_media_orig ON media(orig_sha256)")
    prefs = _columns(conn, "reminder_prefs")
    for col, ddl in (("tithi", "INTEGER NOT NULL DEFAULT 1"),                             # (§13.12, §13.15)
                     ("tithi_lead_days", "INTEGER NOT NULL DEFAULT 7 CHECK (tithi_lead_days BETWEEN 0 AND 30)"),
                     ("milestones", "INTEGER NOT NULL DEFAULT 1")):
        if col not in prefs:
            conn.execute(f"ALTER TABLE reminder_prefs ADD COLUMN {col} {ddl}")
    if "photo_region_id" not in _columns(conn, "people"):
        conn.execute("ALTER TABLE people ADD COLUMN photo_region_id TEXT REFERENCES media_regions(id) ON DELETE SET NULL")
    if "remind" not in _columns(conn, "people"):                       # off for everyone
        conn.execute("ALTER TABLE people ADD COLUMN remind INTEGER NOT NULL DEFAULT 0")
    # feature switches: on where the module already has data (features.py)
    from . import features
    features.migrate(conn, config.now_iso())


def get_setting(conn, key: str, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn, key: str, value: str) -> None:
    conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))


# ---------- backup / restore ----------
def backup_to_file(path: str) -> None:
    """Consistent snapshot through sqlite3's backup API (never a file copy: WAL)."""
    src = _connect()
    try:
        dst = sqlite3.connect(path)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def validate_db_file(path: str) -> None:
    """ValueError with a user-facing message if `path` isn't a Family Tree DB."""
    try:
        conn = sqlite3.connect(path)
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise ValueError(f"The database in that backup failed an integrity check ({integrity}).")
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            missing = REQUIRED_TABLES - tables
            if missing:
                raise ValueError("That doesn't look like a Family Tree backup "
                                 f"(missing tables: {', '.join(sorted(missing))}).")
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        raise ValueError("The database in that backup isn't a valid SQLite file.")


def replace_db(tmp_path: str) -> None:
    """Swap the live DB for an already-validated file, then migrate it."""
    with _import_lock:
        try:
            with get_conn() as c:
                c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:
            pass
        os.replace(tmp_path, config.DB_PATH)
        for ext in ("-wal", "-shm"):
            if os.path.exists(config.DB_PATH + ext):
                os.remove(config.DB_PATH + ext)
        init_db()


def temp_path_in_data(suffix: str) -> str:
    fd, path = tempfile.mkstemp(suffix=suffix, dir=config.DATA_DIR)
    os.close(fd)
    return path

