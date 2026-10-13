"""Household AI on its own Docker host: settings come from environment variables (the docker-compose.yml), the paths
live under /data (a volume), and the daytime window follows the container's time zone (the TZ variable).

There is no Home Assistant here: no Supervisor, no ingress, no Home Assistant login. The settings page (WEB_PORT, 8080)
is published on this machine only (127.0.0.1 in docker-compose.yml); ADMIN_PASSWORD, when set, makes the page ask for
it (HTTP Basic), so it can be opened from other computers. Every other setting is an App setting (app/settings.py,
stored in SQLite).
"""
import logging
import os
import socket
from datetime import date, datetime, timezone

from . import hosts
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
APP_VERSION = "1.0.5-docker"
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
# Downloads come from Ollama's library and Hugging Face only, unless ALLOW_ANY_REGISTRY=1 (app/models.py).
ALLOWED_REGISTRIES: frozenset | None = (None if os.environ.get("ALLOW_ANY_REGISTRY", "").strip() == "1"
                                        else frozenset({"registry.ollama.ai", "hf.co", "huggingface.co"}))

# The settings page's password (HTTP Basic, any user name): empty = no password, for a page published on this machine
# only (docker-compose.yml's 127.0.0.1:8080). The model server never sees it (server.SECRET_ENV).
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

# No Home Assistant network here: callers are never refused for their address (keys protect the gateway).
INSIDE_NETWORK = None
# Requests one address may have waiting without keys: every household app reaches this machine from Home Assistant's
# one address, so they share these places (with keys, each app has its own 2).
ADDR_PER_CALLER = 6


def own_hostname() -> str:
    """The container's host name (callers.py looks for Home Assistant's app names next to it; on a Docker host it
    finds none, and callers are told apart by address or access key). HOUSEHOLD_AI_HOSTNAME overrides it."""
    return os.environ.get("HOUSEHOLD_AI_HOSTNAME") or socket.gethostname()


# Names the settings page and the gateway answer to besides IP addresses, localhost, names without a dot and local
# names (.local, .lan, .home.arpa, .internal): a web page elsewhere that points its own name at this machine (DNS
# rebinding) is refused (app/hosts.py). ALLOWED_HOSTS adds names, comma- or space-separated ("nas.example.com",
# ".example.com" for every name under it, "*" for any name).
ALLOWED_HOSTS = frozenset(hosts.parse_list(os.environ.get("ALLOWED_HOSTS"))
                          | hosts.parse_list(own_hostname()))
HOST_HINT = (": add it to ALLOWED_HOSTS (docker-compose.yml), or under App settings → Access → Other names for this "
             "machine, if it is this machine's name.")


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
