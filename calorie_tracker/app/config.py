"""Reads the app's user-configured options from /data/options.json,
which the Supervisor writes based on config.yaml's schema/options and
whatever the user set on the app's Configuration tab.

No bashio dependency — this is simple enough to just parse directly.

`admin_users` is the only option; every other setting is an App setting
(app/settings.py, stored in SQLite). Any other key in options.json is
ignored.

Also owns Home Assistant's time zone (see the bottom of this file):
"today" for food logs, history buckets and the HA daily-calories sensor must
follow the household's real calendar day, not the container's own clock
(which is UTC in Docker) — use config.today()/config.now(), never
date.today()/datetime.now().
"""
import json
import logging
import os
from calendar import monthrange
from datetime import date, datetime, timezone

from .common import auth_core, ha_time

logger = logging.getLogger("config")

DATA_DIR = os.environ.get("DATA_DIR", "/data")
DB_PATH = os.path.join(DATA_DIR, "calorie.db")
# The Supervisor writes options.json into /data, i.e. DATA_DIR in production.
# Deriving it from DATA_DIR (rather than hard-coding /data) keeps tests and a
# local dev server from ever reading a real Home Assistant options file.
OPTIONS_PATH = os.path.join(DATA_DIR, "options.json")

_DEFAULTS = {
    "admin_users": [],
}


def _load() -> dict:
    if os.path.isfile(OPTIONS_PATH):
        try:
            with open(OPTIONS_PATH, "r") as f:
                data = json.load(f)
            merged = dict(_DEFAULTS)
            merged.update({k: v for k, v in data.items() if k in _DEFAULTS and v is not None})
            return merged
        except (json.JSONDecodeError, OSError, AttributeError):
            pass
    return dict(_DEFAULTS)


_options = _load()

# Home Assistant login names or user ids of the app's admins (case-insensitive):
# they can use the in-app user switcher and open the Admin page. Empty by
# default — deny by default rather than trying to infer real HA admin status,
# which would require granting this app Supervisor admin-role access
# (hassio_role: admin — "access to every API call"). That's a much bigger
# permission grant than a UI gate justifies, so instead the real HA admin (the
# only person who can reach this app's Configuration tab) lists who's allowed
# here. While the list is empty every page shows a "No admin yet" banner.
_raw_admins = _options.get("admin_users") or []
if not isinstance(_raw_admins, list):
    _raw_admins = []
# (app/common/auth_core.py; DEV_ADMIN_USERS adds more, comma-separated, for tests and local development)
ADMIN_USERS = auth_core.admin_names(_raw_admins) | auth_core.env_admins("DEV_ADMIN_USERS")

SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
SUPERVISOR_CORE_API = "http://supervisor/core/api"
SUPERVISOR_CORE_WS = os.environ.get("SUPERVISOR_CORE_WS", "ws://supervisor/core/websocket")   # the app bus
SUPERVISOR_API = os.environ.get("SUPERVISOR_API", "http://supervisor").rstrip("/")

APP_TITLE = "Calorie Tracker"
APP_VERSION = "2.2.2"           # config.yaml's version: the app bus says it in its hello
# The app's sidebar page ("/<full slug>") for links in the Household Assistant's answers; set at start-up
# (app_messages.start, from the Supervisor or the container's host name).
INGRESS_PANEL = None

# ---------------------------------------------------------------------------
# Time zone. The container's own clock is UTC (standard for Docker images),
# which would make "today" wrong for a household anywhere else — most
# visibly right around local midnight, where a log entry made in the
# evening could get silently filed under tomorrow's date. `now()`/`today()`
# below are the only correct way to ask "what day is it"; never use
# date.today()/datetime.now() directly.
#
# The real time zone comes from Home Assistant itself (GET /config, fetched
# once at startup by ha_sync.load_timezone() — see main.py's lifespan), the
# same source of truth Household Todo uses. If that fetch fails (no token,
# HA unreachable), the app stays on UTC.
# ---------------------------------------------------------------------------
ZONE = ha_time.Zone(logger)       # app/common/ha_time.py


def set_timezone(name: str) -> bool:
    """Switch `now()`/`today()` to `name` (an IANA zone like
    'Europe/Berlin'). Returns False (and keeps the current zone) if `name`
    isn't a recognized zone."""
    return ZONE.set(name)


def timezone_name() -> str:
    return ZONE.name


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def now() -> datetime:
    """Current time as an aware datetime in Home Assistant's time zone."""
    return ZONE.now(utcnow())


def today() -> date:
    return now().date()


def add_months(d: date, months: int) -> date:
    """d moved by whole calendar months (the day clamped to the month's
    length) — timedelta has no months, and this avoids python-dateutil."""
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, min(d.day, monthrange(year, month)[1]))
