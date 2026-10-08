"""App options (Configuration tab), environment, and Home Assistant's timezone.

The only app option is `admin_users` (read from /data/options.json at
startup — the Supervisor writes that file from config.yaml's schema plus the
Configuration tab). Everything else is set in the app under Admin → App
settings (settings.py); any other key in options.json is ignored. No bashio
dependency — it is a small JSON file.

"Today" everywhere in this app is Home Assistant's timezone, not the
container's (which is usually UTC): use `today()` / `now()` from here, never
`date.today()`. Home Assistant's own zone is read at startup (ha_client); if
it can't be read, this stays on UTC.
"""
import json
import logging
import os
from datetime import date, datetime, timedelta, timezone

from .common import auth_core, ha_time

logger = logging.getLogger("config")

_OPTIONS_PATH = os.environ.get("OPTIONS_PATH", "/data/options.json")


def read_options_file() -> dict:
    """The raw contents of options.json ({} if missing or unreadable). Only
    `admin_users` is used."""
    return auth_core.read_options(_OPTIONS_PATH, log=logger, errors=(json.JSONDecodeError, OSError))


_options = read_options_file()

# Home Assistant login names / user ids allowed to open the Admin area
# (App settings, Users, Storage) and to act as another user (§4, §7, §8e).
# Deny by default. Stays an app option (Configuration tab): it's how the first admin is known.
# DEV_ADMINS (comma-separated) adds more for tests and local development (app/common/auth_core.py).
ADMIN_NAMES = auth_core.admin_names(_options.get("admin_users") or []) | auth_core.env_admins("DEV_ADMINS")

DATA_DIR = os.environ.get("DATA_DIR", "/data")

# The four background loops (reminders, sensors, housekeeping, drive time)
# start in main.py's lifespan unless BACKGROUND_LOOPS=0. The test suite sets
# it to 0 (tests/_env.py): each test deletes and recreates the database, and
# a loop's first tick running in a worker thread at that moment is what made
# the suite flaky ("disk I/O error" / "no such table"). Tests drive the
# passes directly instead.
BACKGROUND_LOOPS = os.environ.get("BACKGROUND_LOOPS", "1").strip().lower() not in ("0", "false", "no", "off")
DB_PATH = os.path.join(DATA_DIR, "household.db")

SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
SUPERVISOR_CORE_API = os.environ.get("SUPERVISOR_CORE_API", "http://supervisor/core/api").rstrip("/")
SUPERVISOR_CORE_WS = os.environ.get("SUPERVISOR_CORE_WS", "ws://supervisor/core/websocket")
SUPERVISOR_API = os.environ.get("SUPERVISOR_API", "http://supervisor").rstrip("/")
# The app's sidebar page ("/<full slug>", open to everyone): what a phone notification and the Household Assistant's
# links open. Set at start-up (app_messages.start: the Supervisor's addons/self/info, else the host name); None when
# the app isn't in the sidebar. (Before 2.5.0 notifications opened /hassio/ingress/<slug>, the admin's Settings →
# Apps page, which isn't open to everyone.)
SIDEBAR_PAGE = None

# Said to the other household apps in `hello` (app_messages.py); equals config.yaml's version.
APP_VERSION = "2.5.0"
APP_TITLE = "Household Todo"

# Maintenance: read from Home Assistant's GET /config at start-up (ha_client). A negative latitude
# means the southern hemisphere's seasons; the currency labels costs. /share is where the files folder may be.
LATITUDE = None
CURRENCY = ""
SHARE_ROOT = os.environ.get("SHARE_ROOT", "/share")

# §8m — fixed, not an app option.
COMPLETED_RETENTION_DAYS = 60

# ---------------------------------------------------------------------------
# Time. `utcnow` is a module-level function so tests can replace it.
# ---------------------------------------------------------------------------
# Home Assistant's zone (app/common/ha_time.py), read at startup by ha_client.load_timezone().
ZONE = ha_time.Zone(logger)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def set_timezone(name: str) -> bool:
    """Set the zone `today()` / `now()` use. Returns False (and keeps the
    current zone) if `name` is not a known zone — e.g. the container has no
    tz database (python:3.12-alpine needs `apk add tzdata`)."""
    return ZONE.set(name)


def timezone_name() -> str:
    return ZONE.name


def now() -> datetime:
    """Current time as an aware datetime in Home Assistant's timezone."""
    return ZONE.now(utcnow())


def today() -> date:
    return now().date()


def to_local_date(iso_timestamp: str) -> date | None:
    """A stored UTC ISO timestamp -> the local calendar date, or None."""
    try:
        dt = datetime.fromisoformat(iso_timestamp)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ZONE.tz).date()


def now_iso() -> str:
    """UTC timestamp for created_at / completed_at columns."""
    return utcnow().isoformat(timespec="seconds")


def days_ago_iso(days: int) -> str:
    return (utcnow() - timedelta(days=days)).isoformat(timespec="seconds")
