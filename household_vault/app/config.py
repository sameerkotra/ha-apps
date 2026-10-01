"""App options (the app's Configuration tab) and environment.

Options come from /data/options.json (written by the Supervisor). Only
`admin_users` lives there; everything else is an App setting (settings.py).
"""
import json
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger("config")

DATA_DIR = os.environ.get("DATA_DIR", "/data")
DB_PATH = os.environ.get("DB_PATH", os.path.join(DATA_DIR, "vault.db"))
VAULT_DIR = os.environ.get("VAULT_DIR", os.path.join(DATA_DIR, "vaults"))
OPTIONS_PATH = os.environ.get("OPTIONS_PATH", os.path.join(DATA_DIR, "options.json"))

SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
SUPERVISOR_CORE_API = os.environ.get("SUPERVISOR_CORE_API", "http://supervisor/core/api")

# Argon2 memory for vaults with a person-chosen password, in KiB. 64 MiB in Home
# Assistant; tests and local development make it small (VAULT_KDF_MEMORY_KIB).
KDF_MEMORY_KIB = int(os.environ.get("VAULT_KDF_MEMORY_KIB", str(64 * 1024)))
KDF_TARGET_SECONDS = float(os.environ.get("VAULT_KDF_TARGET_SECONDS", "0.7"))


def read_options(path: str | None = None) -> dict:
    path = path or OPTIONS_PATH
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        logger.warning("Could not read %s; using defaults", path)
        return {}
    return data if isinstance(data, dict) else {}


_options = read_options()
ADMIN_NAMES = {str(n).strip().lower() for n in (_options.get("admin_users") or []) if str(n).strip()}
_dev_admins = os.environ.get("DEV_ADMINS")
if _dev_admins:
    ADMIN_NAMES |= {n.strip().lower() for n in _dev_admins.split(",") if n.strip()}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    """UTC with milliseconds ("2026-09-26T17:59:58.123Z"), so events in the same second keep their order."""
    t = utcnow()
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


def parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(ts, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None
