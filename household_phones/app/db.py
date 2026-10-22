"""The app's SQLite database, /data/phones.db (spec/SPEC.md §13).

People (from Home Assistant's identity headers), the phones found in Home Assistant with their settings and status,
each phone's history, the Check-now log, the time and target chat of each forward (never its text), alerts waiting
for a quiet time to end, and the App settings. Each request gets its own short-lived connection; WAL lets reads and
writes overlap. `secure_delete` overwrites deleted rows, so nothing removed lingers in the file's free pages.
"""
import os
import sqlite3
import threading

from . import config
from .common import backup_core, db_core

os.makedirs(config.DATA_DIR, exist_ok=True)

_import_lock = threading.Lock()  # serializes a database-file swap against concurrent writers


def _connect() -> sqlite3.Connection:
    return db_core.connect(config.DB_PATH, timeout=10,
                           pragmas=("foreign_keys = ON", "synchronous = NORMAL", "secure_delete = ON"))


def get_conn():
    """Commits when the block ends, rolls back on an error, always closes (app/common/db_core.py)."""
    return db_core.transaction(_connect)


SCHEMA = """
PRAGMA journal_mode = WAL;

-- Everyone who has opened the app (Home Assistant identity). is_child: set by an admin (Admin → People); a child's
-- phone can be watched without the child pausing it (§9). quiet_*: the person's own alert quiet hours ("" = the
-- household's from App settings).
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    username TEXT,
    is_child INTEGER NOT NULL DEFAULT 0,
    quiet_start TEXT NOT NULL DEFAULT '',
    quiet_end TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
-- Extra notify services an admin adds for a person under Admin → People (common/people_admin.py).
CREATE TABLE IF NOT EXISTS user_notify (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    service TEXT NOT NULL,
    created_at TEXT NOT NULL,
    created_by TEXT,
    PRIMARY KEY (user_id, service)
);

-- A Home Assistant Companion-app device (§2). Times are epoch seconds unless the column says _at TEXT.
CREATE TABLE IF NOT EXISTS phones (
    id TEXT PRIMARY KEY,                  -- the app's own short id (links: #/check/<id>)
    device_id TEXT UNIQUE,                -- Home Assistant's device registry id (NULL once retired)
    ha_name TEXT NOT NULL DEFAULT '',     -- the device's name in Home Assistant
    name TEXT,                            -- display name set here (NULL: "<owner>'s <model>")
    manufacturer TEXT, model TEXT, os_name TEXT, os_version TEXT, app_version TEXT,
    kind TEXT NOT NULL DEFAULT 'phone' CHECK (kind IN ('phone', 'tablet', 'ignored')),
    kind_confirmed INTEGER NOT NULL DEFAULT 0,
    monitored INTEGER NOT NULL DEFAULT 0,  -- an admin added it to monitoring (§2): nothing happens to it otherwise
    monitored_since REAL,                 -- when it was added (the grace before it can be Offline)
    welcome INTEGER NOT NULL DEFAULT 0,   -- 1: show the owner "Your phone is now monitored" until they close it
    interval_min INTEGER,                 -- NULL: the household's check-in interval
    owner_id TEXT,                        -- Home Assistant user id
    owner_by_admin INTEGER NOT NULL DEFAULT 0,
    notify_service TEXT,                  -- override of mobile_app_<device> (bare name)
    first_seen REAL NOT NULL,
    in_registry INTEGER NOT NULL DEFAULT 1,
    retired INTEGER NOT NULL DEFAULT 0,
    replaced_by TEXT,
    keep_unused INTEGER NOT NULL DEFAULT 0,
    -- check-ins and the "please report" requests (§3)
    last_checkin REAL,
    last_spontaneous REAL,                -- the last check-in that wasn't an answer to a request
    requests_sent INTEGER NOT NULL DEFAULT 0,
    last_request_at REAL,
    request_error TEXT,                   -- NULL, or why the last request failed
    request_error_kind TEXT,              -- 'cant_check' / 'signed_out'
    answers_requests INTEGER,             -- learnt: NULL unknown, 1 seen answering a request
    overdue_from REAL,                    -- Check now got no reply: watched as overdue from then
    status TEXT NOT NULL DEFAULT 'unknown',
    status_reason TEXT,
    status_since REAL,
    late_since REAL,
    -- pauses (§3.1, §9): kind 'alerts' (status stays visible) or 'watching' (adults: nothing is asked or shown)
    pause_kind TEXT CHECK (pause_kind IN ('alerts', 'watching')),
    pause_until REAL,                     -- NULL with pause_until_online
    pause_until_online INTEGER NOT NULL DEFAULT 0,
    pause_reason TEXT,
    pause_by TEXT,
    dnm_windows TEXT,                     -- the owner's do-not-monitor hours: JSON [{days: [0-6, Mon=0], start, end,
                                          -- pauseForwarding}] in Home Assistant's time zone (§3.1)
    share_place INTEGER NOT NULL DEFAULT 0,
    stop_requested_at REAL,
    -- what the alerts remember
    battery INTEGER,                      -- the battery level at the last check-in
    charging INTEGER,
    last_reboot TEXT,
    low_battery_sent INTEGER NOT NULL DEFAULT 0,
    charge_reminded_on TEXT,              -- the date of the last charging reminder
    -- the offline alert's delay (§5): NULL = the household's (App settings), else minutes (0 = at once)
    offline_alert_after_min INTEGER,
    -- the current unreachable spell (Offline / App not reporting until it is back online): when it began, the time
    -- counted towards the delay so far (not do-not-monitor hours or pauses), when that count was last brought up to
    -- date (NULL: not counting now), and whether its Offline / App-not-reporting alert has gone out
    offline_since REAL,
    offline_counted_s REAL NOT NULL DEFAULT 0,
    offline_counted_at REAL,
    offline_alerted INTEGER NOT NULL DEFAULT 0,
    app_alerted INTEGER NOT NULL DEFAULT 0,
    -- where the last genuine check-in came from (the phone page's Check-in details, §3), and when the phone said it
    -- was shutting down (Last update trigger: ACTION_SHUTDOWN; NULL once it reports again)
    checkin_entity TEXT,
    checkin_kind TEXT,
    shutdown_at REAL,
    -- forwarding (§6): only the owner switches it on
    fwd_on INTEGER NOT NULL DEFAULT 0,
    fwd_target TEXT NOT NULL DEFAULT 'my_room' CHECK (fwd_target IN ('my_room', 'chat')),
    fwd_chat_id TEXT,
    fwd_chat_name TEXT,
    fwd_chat_kind TEXT,
    fwd_members TEXT,                     -- JSON list of the chat's member ids (asked from Chat), for Check-now rights
    fwd_members_at REAL,
    fwd_disappear TEXT,                   -- '1h' / '1d' / '7d' / '30d' (NULL: the household's default)
    fwd_apps TEXT,                        -- JSON list of package names (NULL: the household's default)
    fwd_filter TEXT NOT NULL DEFAULT 'codes' CHECK (fwd_filter IN ('codes', 'words', 'all')),
    fwd_words TEXT,                       -- JSON list
    fwd_all_confirmed INTEGER NOT NULL DEFAULT 0,
    fwd_quiet_start TEXT NOT NULL DEFAULT '',
    fwd_quiet_end TEXT NOT NULL DEFAULT '',
    fwd_quiet_mode TEXT NOT NULL DEFAULT 'hold' CHECK (fwd_quiet_mode IN ('hold', 'drop')),
    fwd_error TEXT,                       -- why Household Chat refused the last forward (NULL: fine)
    fwd_ok_at REAL,                       -- when Chat last took a forward
    redacted_at REAL,                     -- Android hid a code's text (§6.1): a checklist item until fixed
    -- phone actions (§15): what the owner lets others use (JSON list of action ids; NULL = the defaults), whether
    -- Home Assistant automations may ring it, exact location in Lost mode, and the Lost-mode notification's text
    act_allow TEXT,
    act_from_ha INTEGER NOT NULL DEFAULT 0,
    lost_exact INTEGER NOT NULL DEFAULT 0,
    lost_contact TEXT,
    lost_message TEXT,
    -- Lost mode (§15.2) while it is on: since when, by whom, until, how often it rings and asks for the location,
    -- where the place is posted (JSON), the token of its "I found it" action, and when each last happened
    lost_since REAL,
    lost_by TEXT,
    lost_by_name TEXT,
    lost_until REAL,
    lost_ring_min INTEGER,
    lost_loc_min INTEGER,
    lost_chat TEXT,
    lost_token TEXT,
    lost_last_ring REAL,
    lost_last_loc REAL,
    lost_wake_at REAL,                    -- when the app last woke the screen itself (not the owner unlocking it)
    lost_error TEXT,
    -- Find my phone (§15.1): the "Found it" action's token, when it rang, and what to put back (JSON: volume, dnd)
    find_token TEXT,
    find_at REAL,
    find_restore TEXT,
    -- children's bedtime (§15.4), set by admins: windows (JSON, as do-not-monitor hours), parents (JSON user ids),
    -- Do Not Disturb at bedtime, the screen-in-use count, the night already alerted, DND switched on by the app
    bed_windows TEXT,
    bed_parents TEXT,
    bed_dnd INTEGER NOT NULL DEFAULT 0,
    bed_screen_since REAL,
    bed_alerted_on TEXT,
    bed_dnd_set INTEGER NOT NULL DEFAULT 0,
    -- storage and battery health alerts (§15.5)
    storage_low_sent INTEGER NOT NULL DEFAULT 0,
    battery_health TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- Each phone's history (kept N days): status changes, restarts, Check now, pauses, alerts, battery at each change.
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY,
    phone_id TEXT NOT NULL REFERENCES phones(id) ON DELETE CASCADE,
    at REAL NOT NULL,
    kind TEXT NOT NULL,                   -- status / restart / check / pause / alert / forward_on / owner / …
    text TEXT NOT NULL,
    by_user TEXT,
    battery INTEGER
);
CREATE INDEX IF NOT EXISTS history_phone ON history (phone_id, at);

-- Check now (§4): who asked, when, and the result. No forwarded text.
CREATE TABLE IF NOT EXISTS checks (
    id TEXT PRIMARY KEY,
    phone_id TEXT NOT NULL REFERENCES phones(id) ON DELETE CASCADE,
    started REAL NOT NULL,
    by_user TEXT,
    by_name TEXT NOT NULL,
    via TEXT NOT NULL DEFAULT 'app',      -- app / chat / assistant / home_assistant
    state TEXT NOT NULL DEFAULT 'waiting' CHECK (state IN ('waiting', 'online', 'no_reply', 'cant_ask')),
    reason TEXT,
    replied REAL,
    finished REAL,
    joined INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS checks_phone ON checks (phone_id, started);

-- Phone actions (§15): who used which on a phone, when, through what, and the result (the owner sees this log).
CREATE TABLE IF NOT EXISTS actions (
    id INTEGER PRIMARY KEY,
    phone_id TEXT NOT NULL REFERENCES phones(id) ON DELETE CASCADE,
    at REAL NOT NULL,
    action TEXT NOT NULL,                 -- find / screen_on / flashlight / … / lost_start / lost_end / bedtime_dnd …
    detail TEXT,
    by_user TEXT,
    by_name TEXT NOT NULL,
    via TEXT NOT NULL DEFAULT 'app',      -- app / assistant / home_assistant / lost / bedtime / phone
    ok INTEGER NOT NULL DEFAULT 1,
    result TEXT
);
CREATE INDEX IF NOT EXISTS actions_phone ON actions (phone_id, at);

-- Forwards (§6): the time and the target chat only — never the text, title or sender.
CREATE TABLE IF NOT EXISTS forwards (
    phone_id TEXT NOT NULL REFERENCES phones(id) ON DELETE CASCADE,
    at REAL NOT NULL,
    chat_name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'message'  -- message / redacted / summary / test
);
CREATE INDEX IF NOT EXISTS forwards_phone ON forwards (phone_id, at);

-- When each alert last went out for a phone (the cooldown), and alerts held by the phone's quiet times.
CREATE TABLE IF NOT EXISTS alert_state (
    phone_id TEXT NOT NULL REFERENCES phones(id) ON DELETE CASCADE,
    alert TEXT NOT NULL,
    last_sent REAL,
    held INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (phone_id, alert)
);
-- Alerts for one person waiting for their quiet hours to end. The message is the alert's own text (no forwards).
CREATE TABLE IF NOT EXISTS alert_queue (
    id INTEGER PRIMARY KEY,
    user_id TEXT NOT NULL,
    phone_id TEXT,
    alert TEXT NOT NULL,
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    due REAL NOT NULL
);

-- Requests to Household Chat whose answers the page polls (the chats an owner may forward into).
CREATE TABLE IF NOT EXISTS bus_requests (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    phone_id TEXT,
    kind TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending',
    result TEXT,
    error TEXT,
    created REAL NOT NULL
);

-- The app's own facts (start-up time, lost contact): key → JSON.
CREATE TABLE IF NOT EXISTS app_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Admin → App settings (app/settings.py). value is JSON.
CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by TEXT
);
"""

# Columns added after their table was first made (older databases and restored backups get them at start-up).
MIGRATIONS: dict = {"phones": [
    ("monitored", "INTEGER NOT NULL DEFAULT 0"),
    ("monitored_since", "REAL"),
    ("welcome", "INTEGER NOT NULL DEFAULT 0"),
    ("dnm_windows", "TEXT"),
    ("offline_alert_after_min", "INTEGER"),          # 1.2: the offline alert's delay
    ("offline_since", "REAL"),
    ("offline_counted_s", "REAL NOT NULL DEFAULT 0"),
    ("offline_counted_at", "REAL"),
    ("offline_alerted", "INTEGER NOT NULL DEFAULT 0"),
    ("app_alerted", "INTEGER NOT NULL DEFAULT 0"),
    ("checkin_entity", "TEXT"),                      # where the last genuine check-in came from
    ("checkin_kind", "TEXT"),
    ("shutdown_at", "REAL"),                         # the phone said it was shutting down
] + [(c, t) for c, t in (                            # 1.4: phone actions, Lost mode, bedtime, storage and health
    ("act_allow", "TEXT"), ("act_from_ha", "INTEGER NOT NULL DEFAULT 0"), ("lost_exact", "INTEGER NOT NULL DEFAULT 0"),
    ("lost_contact", "TEXT"), ("lost_message", "TEXT"), ("lost_since", "REAL"), ("lost_by", "TEXT"),
    ("lost_by_name", "TEXT"), ("lost_until", "REAL"), ("lost_ring_min", "INTEGER"), ("lost_loc_min", "INTEGER"),
    ("lost_chat", "TEXT"), ("lost_token", "TEXT"), ("lost_last_ring", "REAL"), ("lost_last_loc", "REAL"),
    ("lost_wake_at", "REAL"), ("lost_error", "TEXT"), ("find_token", "TEXT"), ("find_at", "REAL"),
    ("find_restore", "TEXT"), ("bed_windows", "TEXT"), ("bed_parents", "TEXT"), ("bed_dnd", "INTEGER NOT NULL DEFAULT 0"),
    ("bed_screen_since", "REAL"), ("bed_alerted_on", "TEXT"), ("bed_dnd_set", "INTEGER NOT NULL DEFAULT 0"),
    ("storage_low_sent", "INTEGER NOT NULL DEFAULT 0"), ("battery_health", "TEXT"))]}

ALL_DAYS = [0, 1, 2, 3, 4, 5, 6]


def migrate_1_1(conn, added: list) -> dict:
    """Databases from 1.0.0 (and their backups), once: phones aren't monitored by default any more, and the daily
    quiet times become do-not-monitor hours. Returns what it did (the tests).

    - **Monitored:** only the devices an admin had explicitly taken on — confirmed under New devices, edited (name,
      owner, interval, notify service) or marked as replacing another — and that were still watched. Devices that were
      watched only because discovery guessed "phone" become not monitored.
    - **Quiet times** (offline and battery alerts held) become a do-not-monitor window on every day, same times
      (not on a child's phone: children can't set do-not-monitor hours).
    """
    import json
    out = {"monitored": 0, "unmonitored": 0, "windows": 0}
    cols = db_core.columns(conn, "phones")
    if "phones.monitored" in added and "watched" in cols:
        rows = conn.execute("SELECT id, watched, kind, kind_confirmed, owner_by_admin, name, interval_min, "
                            "notify_service FROM phones WHERE retired = 0").fetchall()
        for p in rows:
            explicit = bool(p["kind_confirmed"] or p["owner_by_admin"] or p["name"] or p["interval_min"]
                            or p["notify_service"])
            keep = bool(p["watched"]) and p["kind"] != "ignored" and explicit
            conn.execute("UPDATE phones SET monitored = ?, monitored_since = ?, status = CASE WHEN ? THEN status "
                         "ELSE 'unknown' END, status_reason = CASE WHEN ? THEN status_reason ELSE 'not_monitored' END "
                         "WHERE id = ?", (int(keep), None, int(keep), int(keep), p["id"]))
            out["monitored" if keep else "unmonitored"] += 1
    if "phones.dnm_windows" in added and "quiet_start" in cols:
        children = "SELECT id FROM users WHERE is_child = 1"
        for p in conn.execute("SELECT id, quiet_start, quiet_end FROM phones WHERE quiet_start != '' AND quiet_end != '' "
                              f"AND (owner_id IS NULL OR owner_id NOT IN ({children}))").fetchall():
            if p["quiet_start"] != p["quiet_end"]:
                conn.execute("UPDATE phones SET dnm_windows = ? WHERE id = ?", (json.dumps(
                    [{"days": ALL_DAYS, "start": p["quiet_start"], "end": p["quiet_end"], "pauseForwarding": False}]),
                    p["id"]))
                out["windows"] += 1
        conn.execute("UPDATE phones SET quiet_start = '', quiet_end = ''")
        conn.execute("DELETE FROM alert_state WHERE held = 1 AND alert = 'battery'")
    return out

# Bumped by every init_db() (start-up, restore): caches (settings) drop what they hold when it changes.
generation = 0


def init_db():
    global generation
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        added = db_core.add_missing_columns(conn, MIGRATIONS)
        if added:
            migrate_1_1(conn, added)
    generation += 1


SNAPSHOT_PREFIX = "household-phones-backup-"       # copies a browser never finished downloading are swept hourly


def backup_to_tempfile(after=None) -> str:
    """A complete, consistent copy of the live database (WAL included) in a temp file; the caller deletes it."""
    return db_core.snapshot_to_tempfile(_connect, prefix=SNAPSHOT_PREFIX, after=after)


REQUIRED_TABLES = {"users", "phones", "history", "app_settings"}


def validate_backup_file(path: str) -> None:
    """ValueError with a readable message unless `path` is this app's database."""
    db_core.validate_file(path, REQUIRED_TABLES, app_name=config.APP_TITLE)


def import_from_tempfile(tmp_path: str) -> None:
    """Swap the live database for an already-validated file, then bring it up to date."""
    backup_core.restore_file(tmp_path, config.DB_PATH, get_conn, migrate=init_db, lock=_import_lock)


def state_get(conn, key: str, default=None):
    import json
    row = conn.execute("SELECT value FROM app_state WHERE key = ?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def state_set(conn, key: str, value) -> None:
    import json
    conn.execute("INSERT INTO app_state (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (key, json.dumps(value)))
