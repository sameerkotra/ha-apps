"""App options, paths and time (SPEC §3).

Options come from /data/options.json (written by the Supervisor). Only
`admin_users` lives there; everything else is an App setting (settings.py).
"""
import logging
import os
from datetime import datetime, timezone

from .common import auth_core, ha_time

logger = logging.getLogger("config")

DATA_DIR = os.environ.get("DATA_DIR", "/data")
DB_PATH = os.environ.get("DB_PATH", os.path.join(DATA_DIR, "chat.db"))
SHARE_DIR = os.environ.get("SHARE_DIR", "/share/household_chat")
# Home Assistant's whole /share folder: shared folders (SPEC §12) share a subfolder of it into chats
SHARE_ROOT = os.environ.get("SHARE_ROOT", os.path.dirname(SHARE_DIR.rstrip("/")) or "/share")
AVATAR_DIR = os.path.join(DATA_DIR, "avatars")
OPTIONS_PATH = os.environ.get("OPTIONS_PATH", os.path.join(DATA_DIR, "options.json"))
APP_VERSION = "2.3.1"
APP_TITLE = "Household Chat"
INGRESS_URL = "/hassio/ingress/household_chat"

SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
SUPERVISOR_CORE_API = os.environ.get("SUPERVISOR_CORE_API", "http://supervisor/core/api")
SUPERVISOR_CORE_WS = os.environ.get("SUPERVISOR_CORE_WS", "ws://supervisor/core/websocket")


def read_options(path: str | None = None) -> dict:
    return auth_core.read_options(path or OPTIONS_PATH, log=logger)


_options = read_options()
# DEV_ADMINS (comma-separated) adds more for tests and local development (app/common/auth_core.py).
ADMIN_NAMES = auth_core.admin_names(_options.get("admin_users") or []) | auth_core.env_admins("DEV_ADMINS")


# ---------- time ----------
# Stored times are UTC ISO strings with milliseconds. Home Assistant's time zone
# (read at startup, ha_client.load_time_zone_blocking) is used for quiet hours, reminder
# shortcuts ("this evening") and the nightly job.
ZONE = ha_time.Zone(logger, tz=timezone.utc)      # app/common/ha_time.py


def set_time_zone(name: str | None) -> bool:
    return ZONE.set(name)


def tz():
    return ZONE.tz


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def local_now() -> datetime:
    return ZONE.now()


def iso(t: datetime) -> str:
    t = t.astimezone(timezone.utc)
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


def now_iso() -> str:
    """UTC with milliseconds ("2026-09-26T17:59:58.123Z"), so events in the same second keep their order."""
    return iso(utcnow())


def parse_iso(ts: str | None) -> datetime | None:
    """Our own stored format, plus any ISO 8601 time with an offset (from the browser or HA)."""
    if not ts or not isinstance(ts, str):
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(ts, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=tz())
