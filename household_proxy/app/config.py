"""The app's options (/data/options.json, written by the Supervisor from config.yaml) and Home Assistant's time zone.

`admin_users` is the only option; sites and every other setting live in the app (SQLite). Times shown to people
follow Home Assistant's own time zone (read at start-up), never the container's UTC clock: use config.now().
"""
import logging
import os
from datetime import date, datetime, timezone

from .common import auth_core, ha_time

logger = logging.getLogger("config")

DATA_DIR = os.environ.get("DATA_DIR", "/data")
DB_PATH = os.path.join(DATA_DIR, "proxy.db")
OPTIONS_PATH = os.environ.get("OPTIONS_PATH", os.path.join(DATA_DIR, "options.json"))

_options = auth_core.read_options(OPTIONS_PATH, log=logger)
_raw_admins = _options.get("admin_users") or []
if not isinstance(_raw_admins, list):
    _raw_admins = []
# Home Assistant login names or user ids of the app's admins (any case); DEV_ADMIN_USERS adds more for tests.
ADMIN_USERS = auth_core.admin_names(_raw_admins) | auth_core.env_admins("DEV_ADMIN_USERS")

SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
# Home Assistant's Core API through the Supervisor (the shared ha_client, ha_time, ha_people and ha_notify use it).
SUPERVISOR_CORE_API = os.environ.get("SUPERVISOR_CORE_API", "http://supervisor/core/api").rstrip("/")

APP_TITLE = "Household Proxy"
APP_VERSION = "0.6.2"           # config.yaml's version
SLUG = "household_proxy"

ZONE = ha_time.Zone(logger)       # app/common/ha_time.py


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def now() -> datetime:
    """Now, in Home Assistant's time zone."""
    return ZONE.now(utcnow())


def today() -> date:
    return now().date()


def iso(dt: datetime | None = None) -> str:
    """A UTC timestamp as stored in the database ("2026-10-11T18:25:00Z")."""
    return (dt or utcnow()).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
