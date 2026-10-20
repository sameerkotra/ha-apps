"""The app's options (/data/options.json, written by the Supervisor from config.yaml), its paths and ports, and
Home Assistant's time zone (the daytime window follows it: SPEC.md §6.2).

`admin_users` is the only option; every other setting is an App setting (app/settings.py, stored in SQLite).
"""
import ipaddress
import logging
import os
import socket
from datetime import date, datetime, timezone

from .common import auth_core, ha_time

logger = logging.getLogger("config")

DATA_DIR = os.environ.get("DATA_DIR", "/data")
DB_PATH = os.path.join(DATA_DIR, "household_ai.db")
MODELS_DIR = os.environ.get("MODELS_DIR", os.path.join(DATA_DIR, "models"))
OPTIONS_PATH = os.environ.get("OPTIONS_PATH", os.path.join(DATA_DIR, "options.json"))

_options = auth_core.read_options(OPTIONS_PATH, log=logger)
_raw_admins = _options.get("admin_users") or []
if not isinstance(_raw_admins, list):
    _raw_admins = []
# Home Assistant login names or user ids of the app's admins (any case); DEV_ADMIN_USERS adds more for tests.
ADMIN_USERS = auth_core.admin_names(_raw_admins) | auth_core.env_admins("DEV_ADMIN_USERS")

SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
SUPERVISOR_API = os.environ.get("SUPERVISOR_API", "http://supervisor").rstrip("/")
# The Core API through the Supervisor's proxy: app/common/ha_client.py reads Home Assistant's time zone there.
SUPERVISOR_CORE_API = os.environ.get("SUPERVISOR_CORE_API", f"{SUPERVISOR_API}/core/api")

APP_TITLE = "Household AI"
APP_VERSION = "1.1.1"
SLUG = "household_ai"

# The model server: Ollama, a child process listening on loopback only (SPEC.md §3).
OLLAMA_BIN = os.environ.get("OLLAMA_BIN", "ollama")
OLLAMA_PORT = int(os.environ.get("OLLAMA_PORT", "11435"))
OLLAMA_URL = f"http://127.0.0.1:{OLLAMA_PORT}"
# The gateway other apps use (SPEC.md §4); 0 = don't start it (tests start their own).
GATEWAY_PORT = int(os.environ.get("GATEWAY_PORT", "11434"))
GATEWAY_HOST = os.environ.get("GATEWAY_HOST", "0.0.0.0")
# Where the recommended models and "Other model…" come from (download sizes, SPEC.md §3).
REGISTRY_URL = os.environ.get("OLLAMA_REGISTRY_URL", "https://registry.ollama.ai").rstrip("/")
# Downloads from any registry (an admin's choice on the Models tab); the Docker build limits them.
ALLOWED_REGISTRIES: frozenset | None = None

# The gateway answers only to this machine's names (DNS rebinding, app/hosts.py): addresses, names without a dot (the
# apps use "<prefix>-household-ai"), localhost and local names (.local, .lan, .home.arpa, .internal). More names: the
# *Other names for this machine* App setting. (The Docker build adds its ALLOWED_HOSTS variable here.)
ALLOWED_HOSTS: frozenset = frozenset()
HOST_HINT = (": use the app's host name or the machine's IP address, or add the name under Household AI → App "
             "settings → Access → Other names for this machine.")

# Home Assistant's internal network (the Supervisor's, 172.30.32.0/23): the apps and Home Assistant itself. With keys
# off and port 11434 published on the LAN, callers from anywhere else are refused (gateway.outsider_refused).
INSIDE_NETWORK = (ipaddress.ip_network(os.environ.get("INSIDE_NETWORK", "172.30.32.0/23")),)
# Requests one unknown address may have waiting without keys (every household app has its own address here).
ADDR_PER_CALLER = 2


def own_hostname() -> str:
    """This app's host name on the Supervisor's internal network, e.g. "a1b2c3d4-household-ai" (the container's
    host name). HOUSEHOLD_AI_HOSTNAME overrides it for tests."""
    return os.environ.get("HOUSEHOLD_AI_HOSTNAME") or socket.gethostname()


ZONE = ha_time.Zone(logger)       # app/common/ha_time.py


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def now() -> datetime:
    """Now, in Home Assistant's time zone."""
    return ZONE.now(utcnow())


def today() -> date:
    return now().date()
