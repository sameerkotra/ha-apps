"""The app's option (/data/options.json, written by the Supervisor from config.yaml), its environment and Home
Assistant's time zone.

`admin_users` is the only option; everything else lives in the app (Admin → App settings, SQLite). Times shown to
people follow Home Assistant's own time zone (read at start-up), never the container's UTC clock: use config.now().
The status machine works in epoch seconds from `clock()`, which the tests replace.
"""
import logging
import os
import time
from datetime import date, datetime, timezone

from .common import auth_core, ha_time

logger = logging.getLogger("config")

APP_TITLE = "Household Phones"
APP_VERSION = "1.4.1"           # config.yaml's version
SLUG = "household_phones"

DATA_DIR = os.environ.get("DATA_DIR", "/data")
DB_PATH = os.path.join(DATA_DIR, "phones.db")
OPTIONS_PATH = os.environ.get("OPTIONS_PATH", os.path.join(DATA_DIR, "options.json"))

_options = auth_core.read_options(OPTIONS_PATH, log=logger)
_raw_admins = _options.get("admin_users") or []
if not isinstance(_raw_admins, list):
    _raw_admins = []
# Home Assistant login names or user ids of the app's admins (any case); DEV_ADMIN_USERS adds more for tests.
ADMIN_USERS = auth_core.admin_names(_raw_admins, strip_quotes=True) | auth_core.env_admins("DEV_ADMIN_USERS")

SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
# Home Assistant's Core API through the Supervisor (the shared ha_client, ha_time, ha_people and ha_notify use it).
SUPERVISOR_CORE_API = os.environ.get("SUPERVISOR_CORE_API", "http://supervisor/core/api").rstrip("/")
SUPERVISOR_CORE_WS = os.environ.get("SUPERVISOR_CORE_WS", "ws://supervisor/core/websocket")

# The background loops (the watcher, sensors, housekeeping) start in main.py's lifespan unless
# BACKGROUND_LOOPS=0 (the tests drive each pass by hand).
BACKGROUND_LOOPS = os.environ.get("BACKGROUND_LOOPS", "1").strip().lower() not in ("0", "false", "no", "off")

ZONE = ha_time.Zone(logger)       # app/common/ha_time.py


def clock() -> float:
    """Now, in epoch seconds (the tests replace it)."""
    return time.time()


def utcnow() -> datetime:
    return datetime.fromtimestamp(clock(), timezone.utc)


def now() -> datetime:
    """Now, in Home Assistant's time zone."""
    return ZONE.now(utcnow())


def today() -> date:
    return now().date()


def local(ts: float | None) -> datetime | None:
    """An epoch time in Home Assistant's time zone."""
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, timezone.utc).astimezone(ZONE.tz)


def hhmm(ts: float | None) -> str:
    """"14:02" in Home Assistant's time zone ("" for none)."""
    d = local(ts)
    return d.strftime("%H:%M") if d else ""


def iso(ts: float | None = None) -> str:
    """A UTC timestamp as kept in the database ("2026-10-11T18:25:00Z")."""
    return datetime.fromtimestamp(clock() if ts is None else ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(text) -> float | None:
    """A stored or Home Assistant timestamp → epoch seconds, or None."""
    if not isinstance(text, str) or not text:
        return None
    try:
        d = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.timestamp()
