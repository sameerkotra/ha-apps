"""App options (the app's Configuration tab), environment and Home Assistant's time zone.

Options come from /data/options.json (written by the Supervisor from
config.yaml's schema plus the Configuration tab). Read once at import:
restart the app after changing an option. Only `admin_users` lives there
now; everything else is an App setting (settings.py, Admin → App settings),
which applies without a restart.

"Today" is Home Assistant's time zone, never the container's: use `today()` /
`now()` from here. If HA's zone can't be read the fallback is UTC.
"""
import logging
import os
from datetime import date, datetime, timezone

from .common import auth_core, ha_time

logger = logging.getLogger("config")

DATA_DIR = os.environ.get("DATA_DIR", "/data")
DB_PATH = os.environ.get("DB_PATH", os.path.join(DATA_DIR, "family.db"))
OPTIONS_PATH = os.environ.get("OPTIONS_PATH", os.path.join(DATA_DIR, "options.json"))

SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
SUPERVISOR_CORE_API = os.environ.get("SUPERVISOR_CORE_API", "http://supervisor/core/api")

_DEFAULTS = {
    "admin_users": [],
}


def read_options(path: str | None = None) -> dict:
    """The raw options.json (without defaults); {} if missing or unreadable.
    Only `admin_users` is read from it; anything else in it is ignored."""
    data = auth_core.read_options(path or OPTIONS_PATH, log=logger)
    return {k: v for k, v in data.items() if v is not None}


_options = dict(_DEFAULTS, **read_options())

# admin_users: HA user ids or login names (never display names), lower-cased.
# DEV_ADMINS (comma-separated) adds more for tests and local development (app/common/auth_core.py).
ADMIN_NAMES = auth_core.admin_names(_options.get("admin_users") or []) | auth_core.env_admins("DEV_ADMINS")

# trash_days, max_upload_mb and media_path are App settings: read settings.get().
# The photo folder is the `media_path` App setting. This is only its
# default; the MEDIA_PATH environment variable changes it for tests and local
# development (never set inside Home Assistant).
MEDIA_PATH_DEFAULT = os.path.normpath(os.environ.get("MEDIA_PATH") or "/share/family_tree")
# Tests and local development may point media anywhere; inside HA it must be under /share.
ALLOW_ANY_MEDIA_PATH = os.environ.get("ALLOW_ANY_MEDIA_PATH") == "1"

# ---------- time ----------
# Home Assistant's zone (app/common/ha_time.py), read at startup by ha_client.load_timezone().
ZONE = ha_time.Zone(logger)


def set_timezone(name: str) -> bool:
    return ZONE.set(name)


# Home Assistant's home location (for sunrise and tithi days, §13.12). Until it's
# read, Hyderabad — where the default calendar conventions come from.
_location = (17.385, 78.4867)
_location_from_ha = False


def set_location(lat: float, lon: float) -> None:
    global _location, _location_from_ha
    if -90 <= lat <= 90 and -180 <= lon <= 180:
        _location, _location_from_ha = (lat, lon), True


def location() -> tuple[float, float]:
    return _location


def location_known() -> bool:
    return _location_from_ha


def tz():
    return ZONE.tz


def timezone_name() -> str:
    return ZONE.name


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def now() -> datetime:
    return ZONE.now(utcnow())


def today() -> date:
    return now().date()


def now_iso() -> str:
    return utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
