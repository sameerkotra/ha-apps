"""The app's options (/data/options.json, written by the Supervisor from config.yaml) and Home Assistant's time zone.

`admin_users` is the only option; every other setting is an App setting (app/settings.py, stored in SQLite).
"today" for the model's prompt follows Home Assistant's own time zone (read once at start-up), never the
container's UTC clock: use config.today() / config.now().
"""
import json
import logging
import os
from datetime import date, datetime, timezone

from .common import auth_core, ha_time

logger = logging.getLogger("config")

DATA_DIR = os.environ.get("DATA_DIR", "/data")
DB_PATH = os.path.join(DATA_DIR, "assistant.db")
OPTIONS_PATH = os.path.join(DATA_DIR, "options.json")


def _load() -> dict:
    try:
        with open(OPTIONS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


_raw_admins = _load().get("admin_users") or []
if not isinstance(_raw_admins, list):
    _raw_admins = []
# Home Assistant login names or user ids of the app's admins (any case); DEV_ADMIN_USERS adds more for tests.
ADMIN_USERS = auth_core.admin_names(_raw_admins) | auth_core.env_admins("DEV_ADMIN_USERS")

SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
SUPERVISOR_CORE_API = "http://supervisor/core/api"
SUPERVISOR_CORE_WS = os.environ.get("SUPERVISOR_CORE_WS", "ws://supervisor/core/websocket")   # the app bus
SUPERVISOR_API = os.environ.get("SUPERVISOR_API", "http://supervisor").rstrip("/")

APP_TITLE = "Household Assistant"
APP_VERSION = "1.1.0"           # config.yaml's version: the app bus says it in its hello
SLUG = "household_assistant"

ZONE = ha_time.Zone(logger)       # app/common/ha_time.py


def set_timezone(name: str) -> bool:
    return ZONE.set(name)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def now() -> datetime:
    """Now, in Home Assistant's time zone."""
    return ZONE.now(utcnow())


def today() -> date:
    return now().date()
