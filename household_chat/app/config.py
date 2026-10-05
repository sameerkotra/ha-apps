"""App options, paths and time (SPEC §3).

Options come from /data/options.json (written by the Supervisor). Only
`admin_users` lives there; everything else is an App setting (settings.py).
"""
import logging
import os
import re
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
APP_VERSION = "2.3.4"
APP_TITLE = "Household Chat"


# ---------- the app's page in Home Assistant (where phone notifications open) ----------
# Home Assistant gives every app shown in the sidebar a page at /<full slug>. The full slug carries the repository's
# id (a1b2c3d4_household_chat; local_household_chat for a local copy), so it depends on how the app was installed.
# /hassio/ingress/<slug> is older Home Assistant's address and a 404 on current versions (APP_MESSAGES_SPEC §6.5).
SLUG = "household_chat"
_FULL_SLUG = re.compile(r"([0-9a-f]{8}|local)_household_chat")


def full_slug_from_host(host: str | None) -> str | None:
    """The container's host name is the full slug with - for _ (a1b2c3d4-household-chat)."""
    slug = (host or "").strip().lower().replace("-", "_")
    return slug if _FULL_SLUG.fullmatch(slug) else None


def page_url(slug: str | None, in_sidebar: bool = True) -> str:
    """/<full slug> (the sidebar page, open to everyone); /app/<full slug> when the app isn't in the sidebar (its
    Settings → Apps page); /household_chat outside Home Assistant (tests, development)."""
    if not slug or not _FULL_SLUG.fullmatch(slug):
        return "/" + SLUG
    return "/" + slug if in_sidebar else "/app/" + slug


# start-up refines this from the Supervisor (ha_client.learn_page_blocking)
INGRESS_URL = page_url(full_slug_from_host(os.environ.get("HOSTNAME")))

SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
SUPERVISOR_CORE_API = os.environ.get("SUPERVISOR_CORE_API", "http://supervisor/core/api")
SUPERVISOR_CORE_WS = os.environ.get("SUPERVISOR_CORE_WS", "ws://supervisor/core/websocket")
SUPERVISOR_API = os.environ.get("SUPERVISOR_API", "http://supervisor").rstrip("/")


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
