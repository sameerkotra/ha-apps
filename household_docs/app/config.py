"""App option, environment, paths and time (SPEC §3, §4).

The only app option is `admin_users` (/data/options.json, written by the Supervisor from config.yaml's
schema and the Configuration tab). Everything else is an App setting (settings.py).

Paths:
- DATA_DIR (default /data): the database, docs.db.
- SHARE_DIR (default /share): Home Assistant's share folder. Documents live in a folder inside it (the
  docs folder, §5.1). People see paths as "/share/…" whatever SHARE_DIR is (`share_display`,
  `share_abs`), so tests and the smoke harness can point SHARE_DIR at a temp folder.

Stored times are UTC ISO strings; "today" is Home Assistant's time zone (read at start-up).
"""
import logging
import os
from datetime import datetime, timedelta, timezone

from .common import auth_core, ha_time

logger = logging.getLogger("config")

APP_VERSION = "1.4.0"
APP_TITLE = "Household Docs"

DATA_DIR = os.environ.get("DATA_DIR", "/data")
DB_PATH = os.environ.get("DB_PATH", os.path.join(DATA_DIR, "docs.db"))
SHARE_DIR = os.environ.get("SHARE_DIR", "/share")
OPTIONS_PATH = os.environ.get("OPTIONS_PATH", os.path.join(DATA_DIR, "options.json"))

# What people see as the start of every path (the folder as Home Assistant names it).
SHARE_PREFIX = "/share"
DEFAULT_DOCS_PATH = "/share/household_docs"

SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
SUPERVISOR_CORE_API = os.environ.get("SUPERVISOR_CORE_API", "http://supervisor/core/api").rstrip("/")
SUPERVISOR_CORE_WS = os.environ.get("SUPERVISOR_CORE_WS", "ws://supervisor/core/websocket")
# The Supervisor's own API: GET /addons/self/info tells the app its full slug and whether it has a sidebar page
# (APP_MESSAGES_SPEC §6.5 — "Open in Docs" links from Household Chat).
SUPERVISOR_API = os.environ.get("SUPERVISOR_API", "http://supervisor").rstrip("/")
# Where phone notifications open: set at start-up by app_messages.learn_panel to the app's sidebar page
# ("/a1b2c3d4_household_docs"); this bare value is only for outside Home Assistant (tests, development).
INGRESS_URL = "/household_docs"

# Background loops (housekeeping, people) start in main.py's lifespan unless BACKGROUND_LOOPS=0 (tests).
BACKGROUND_LOOPS = os.environ.get("BACKGROUND_LOOPS", "1").strip().lower() not in ("0", "false", "no", "off")


def read_options(path: str | None = None) -> dict:
    return auth_core.read_options(path or OPTIONS_PATH, log=logger)


_options = read_options()
# Deny by default: with admin_users empty nobody is an admin ("No admin yet" on every page).
# DEV_ADMINS (comma-separated) adds more for tests and local development (app/common/auth_core.py).
ADMIN_NAMES = (auth_core.admin_names(_options.get("admin_users") or [], strip_quotes=True)
               | auth_core.env_admins("DEV_ADMINS"))


# ---------- /share paths ----------
def share_root() -> str:
    """The real path of Home Assistant's /share (SHARE_DIR)."""
    return os.path.realpath(SHARE_DIR)


def share_abs(display: str) -> str:
    """"/share/a/b" → the real location under SHARE_DIR (not resolved; callers realpath-check it)."""
    rel = display[len(SHARE_PREFIX):].strip("/") if display.startswith(SHARE_PREFIX) else display.strip("/")
    return os.path.join(share_root(), rel) if rel else share_root()


def share_rel(display: str) -> str:
    """"/share/a/b" → "a/b" (relative to /share)."""
    return display[len(SHARE_PREFIX):].strip("/") if display.startswith(SHARE_PREFIX) else display.strip("/")


def share_display(rel: str) -> str:
    """"a/b" (relative to /share) → "/share/a/b"."""
    rel = (rel or "").strip("/")
    return SHARE_PREFIX + ("/" + rel if rel else "")


# ---------- time ----------
ZONE = ha_time.Zone(logger)
# Home Assistant's currency (Settings → System → General), read with the time zone at start-up: sheets'
# Currency format uses it (SPEC §8.1). EUR until it's known.
CURRENCY = {"code": "EUR"}


def set_currency(code) -> None:
    if isinstance(code, str) and 2 <= len(code.strip()) <= 5 and code.strip().isalpha():
        CURRENCY["code"] = code.strip().upper()


def currency() -> str:
    return CURRENCY["code"]



def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def set_timezone(name: str) -> bool:
    return ZONE.set(name)


def timezone_name() -> str:
    return ZONE.name


def now() -> datetime:
    return ZONE.now(utcnow())


def now_iso() -> str:
    return utcnow().isoformat(timespec="seconds")


def iso_of(ts: float) -> str:
    """A file time (seconds since the epoch) as the stored UTC ISO text."""
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


def parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def ago_iso(**kw) -> str:
    return (utcnow() - timedelta(**kw)).isoformat(timespec="seconds")
