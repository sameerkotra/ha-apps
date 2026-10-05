"""App settings (Admin → App settings). Stored in `app_settings` (as "1"/"0" and numbers, next to the app's
own rows such as the tuned Argon2 values); validated; apply without a restart (read on every use, no cache).
Built on the shared registry (common/python/settings_core.py)."""
from . import db
from .common import settings_core
from .common.settings_core import Group, Setting, SettingsError  # noqa: F401

GROUPS = [
    Group("security", "Security"),
    Group("data", "Reminders and copies"),
]

SETTINGS = [
    Setting("clipboard_clear_seconds", 30, "Clear the clipboard after (seconds, 0 = never)", group="security",
            min=0, max=600),
    Setting("min_master_password_length", 12, "Shortest master password", group="security", min=8, max=64),
    Setting("allow_breach_check", False, "Allow the breach check (needs internet)", group="security"),
    Setting("session_max_hours", 12, "Longest unlocked session (hours)", group="security", min=1, max=72),
    Setting("reminder_titles", True,
            "Name the item in expiry reminders (its name is then kept readable, like the date)", group="data"),
    Setting("personal_copies", False,
            "Keep one file per person in /data/copies (all their passwords, locked with their master password; "
            "rewritten after every change)", group="data"),
]


def _encode(v) -> str:
    return "1" if v is True else "0" if v is False else str(v)


def _decode(text: str, key: str):
    return (text == "1") if isinstance(DEFAULTS[key], bool) else int(text)


REGISTRY = settings_core.Registry(
    SETTINGS, groups=GROUPS, connect=db.get_conn, format_error=lambda e: str(e).splitlines()[0] or "Invalid value.",
    unknown_message=lambda keys: f"Unknown setting: {', '.join(keys)}", encode=_encode, decode=_decode,
    load_check="type", write_all=True, write=lambda conn, key, value, who, now: db.set_setting(conn, key, value),
    cache_ttl=0, log=lambda *a: None)

AppSettings = REGISTRY.model
DEFAULTS = REGISTRY.defaults
LABELS = REGISTRY.labels


def all_values(conn=None) -> dict:
    return REGISTRY.all(conn)


def get(key: str):
    return all_values()[key]


def update(values: dict) -> dict:
    return REGISTRY.update(values)[0]


def payload(values: dict | None = None, **extra) -> dict:
    return REGISTRY.payload(values, **extra)
