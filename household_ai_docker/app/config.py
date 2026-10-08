"""Household AI on its own Docker host: settings come from environment variables (the docker-compose.yml), the paths
live under /data (a volume), and the daytime window follows the container's time zone (the TZ variable).

There is no Home Assistant here: no Supervisor, no ingress, no login. The settings page (WEB_PORT, 8080) is open to
anyone who can reach it — keep that port to your own network. Every other setting is an App setting
(app/settings.py, stored in SQLite).
"""
import logging
import os
import socket
from datetime import date, datetime, timezone

from .common import ha_time

logger = logging.getLogger("config")

DATA_DIR = os.environ.get("DATA_DIR", "/data")
DB_PATH = os.path.join(DATA_DIR, "household_ai.db")
MODELS_DIR = os.environ.get("MODELS_DIR", os.path.join(DATA_DIR, "models"))

# No admin list: the page has no login (README.md). Kept so the shared code finds the names it reads.
ADMIN_USERS: set[str] = set()
SUPERVISOR_TOKEN = ""
SUPERVISOR_API = ""
SUPERVISOR_CORE_API = ""

APP_TITLE = "Household AI"
APP_VERSION = "1.0.1-docker"
SLUG = "household_ai"
STANDALONE = True

# The model server: Ollama, a child process listening on loopback only.
OLLAMA_BIN = os.environ.get("OLLAMA_BIN", "ollama")
OLLAMA_PORT = int(os.environ.get("OLLAMA_PORT", "11435"))
OLLAMA_URL = f"http://127.0.0.1:{OLLAMA_PORT}"
# The gateway other apps use; 0 = don't start it (tests start their own).
GATEWAY_PORT = int(os.environ.get("GATEWAY_PORT", "11434"))
GATEWAY_HOST = os.environ.get("GATEWAY_HOST", "0.0.0.0")
# The port the gateway is published on, on the Docker host (docker run -p <this>:11434): shown on the page as the
# address to paste into the other apps.
PUBLIC_GATEWAY_PORT = int(os.environ.get("PUBLIC_GATEWAY_PORT", "11434"))
# The settings page's port inside the container (the Dockerfile's CMD uses it).
WEB_PORT = int(os.environ.get("WEB_PORT", "8080"))
# Where the recommended models and "Other model…" come from.
REGISTRY_URL = os.environ.get("OLLAMA_REGISTRY_URL", "https://registry.ollama.ai").rstrip("/")


def own_hostname() -> str:
    """The container's host name (callers.py looks for Home Assistant's app names next to it; on a Docker host it
    finds none, and callers are told apart by address or access key). HOUSEHOLD_AI_HOSTNAME overrides it."""
    return os.environ.get("HOUSEHOLD_AI_HOSTNAME") or socket.gethostname()


# The container's time zone (TZ, e.g. Europe/London); UTC when it isn't set or isn't known.
ZONE = ha_time.Zone(logger)
ZONE.set(os.environ.get("TZ") or "UTC")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def now() -> datetime:
    """Now, in the container's time zone (TZ)."""
    return ZONE.now(utcnow())


def today() -> date:
    return now().date()
