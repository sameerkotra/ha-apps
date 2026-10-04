"""App option (Configuration tab), environment, and Home Assistant's time zone.

The only app option is `admin_users` (read from /data/options.json at
startup — the Supervisor writes that file from config.yaml's schema plus the
Configuration tab). Everything else is set in the app under Admin → App
settings (settings.py) and Admin → Users; any other key in options.json is
ignored.

"Today" everywhere in this app is Home Assistant's time zone, not the
container's (usually UTC): use `today()` / `now()` from here, never
`date.today()`. Home Assistant's zone is read at startup (ha_client); if it
can't be read, the app stays on UTC.
"""
import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

logger = logging.getLogger("config")

APP_VERSION = "1.4.4"

_OPTIONS_PATH = os.environ.get("OPTIONS_PATH", "/data/options.json")


def read_options_file() -> dict:
    """The raw contents of options.json ({} if missing or unreadable)."""
    if os.path.isfile(_OPTIONS_PATH):
        try:
            with open(_OPTIONS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            logger.warning("Could not read %s; using defaults", _OPTIONS_PATH)
    return {}


_options = read_options_file()

# Home Assistant login names / user ids allowed to open Admin. Deny by default:
# an empty list means nobody is an admin (every page then shows "No admin yet").
ADMIN_NAMES = {str(n).strip().strip("\"'").strip().lower()
               for n in (_options.get("admin_users") or []) if str(n).strip().strip("\"'").strip()}
_dev_admins = os.environ.get("DEV_ADMINS")
if _dev_admins:
    ADMIN_NAMES |= {n.strip().lower() for n in _dev_admins.split(",") if n.strip()}

DATA_DIR = os.environ.get("DATA_DIR", "/data")
DB_PATH = os.path.join(DATA_DIR, "arcade.db")

# The background loops (sensors, housekeeping, people) start in main.py's
# lifespan unless BACKGROUND_LOOPS=0 (the tests drive the passes directly).
BACKGROUND_LOOPS = os.environ.get("BACKGROUND_LOOPS", "1").strip().lower() not in ("0", "false", "no", "off")

SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
SUPERVISOR_CORE_API = os.environ.get("SUPERVISOR_CORE_API", "http://supervisor/core/api").rstrip("/")

# The app's panel in Home Assistant, for notifications to open (the container's
# host name is the app's slug with - for _, e.g. local-household-arcade →
# /hassio/ingress/local_household_arcade).
_host = os.environ.get("HOSTNAME", "")
INGRESS_PANEL = ("/hassio/ingress/" + _host.replace("-", "_")) if _host and _host.replace("-", "").isalnum() \
    and _host.endswith("household-arcade") else None

# ---------------------------------------------------------------------------
# Time. `utcnow` is a module-level function so tests can replace it.
# ---------------------------------------------------------------------------
_DEFAULT_TZ_NAME = "UTC"
try:
    _tz: ZoneInfo | timezone = ZoneInfo(_DEFAULT_TZ_NAME)
except Exception:  # ZoneInfoNotFoundError if the tz database is somehow missing
    _tz = timezone.utc
_tz_name = _DEFAULT_TZ_NAME


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def set_timezone(name: str) -> bool:
    """Set the zone `today()` / `now()` use. Returns False (and keeps the
    current zone) if `name` is not a known zone."""
    global _tz, _tz_name
    try:
        _tz = ZoneInfo(name)
        _tz_name = name
        return True
    except Exception as e:  # ZoneInfoNotFoundError, ValueError, ...
        logger.warning("Unknown time zone %r (%s); staying on %s", name, e, _tz_name)
        return False


def timezone_name() -> str:
    return _tz_name


def tz():
    return _tz


def now() -> datetime:
    """Current time as an aware datetime in Home Assistant's time zone."""
    return utcnow().astimezone(_tz)


def today() -> date:
    return now().date()


def parse_ts(iso_timestamp: str) -> datetime | None:
    """A stored ISO timestamp -> an aware datetime (UTC if it had no zone), or None."""
    try:
        dt = datetime.fromisoformat(iso_timestamp)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def to_local_date(iso_timestamp: str) -> date | None:
    dt = parse_ts(iso_timestamp)
    return dt.astimezone(_tz).date() if dt else None


def now_iso() -> str:
    """UTC timestamp for *_at columns."""
    return utcnow().isoformat(timespec="seconds")


def local_day_bounds_utc(d: date) -> tuple[str, str]:
    """[start, end) of the local calendar day `d`, as UTC ISO strings (for
    comparing with stored *_at columns)."""
    start = datetime(d.year, d.month, d.day, tzinfo=_tz)
    end = start + timedelta(days=1)
    # normalise through UTC so DST days have the right length
    end = datetime(end.year, end.month, end.day, tzinfo=_tz)
    return (start.astimezone(timezone.utc).isoformat(timespec="seconds"),
            end.astimezone(timezone.utc).isoformat(timespec="seconds"))
