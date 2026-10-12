"""App settings (Admin → App settings), stored in the `app_settings` table (spec/SPEC.md §5.4).

Admins change them inside the app, without a restart. Sites have their own form (Admin → Sites); only
`admin_users` stays on the app's Configuration tab.
"""
import logging
import threading

from . import config, db
from .common import settings_core
from .common.settings_core import Group, Setting, SettingsError  # noqa: F401  (SettingsError: the routes' name)

logger = logging.getLogger("settings")

GROUPS = [
    Group("proxy", "🌐 Proxy", "How pages are passed between the browser and the sites."),
    Group("logs", "📜 Logs", "Each site's access log is off unless switched on in its form."),
]

SETTINGS = [
    Setting("request_timeout_s", 60, "Request timeout", group="proxy", min=5, max=600, unit="seconds",
            help="How long to wait for a site to start answering. Websockets and streamed answers have no limit "
                 "once they are open. A site can have its own timeout."),
    Setting("max_upload_mb", 100, "Largest upload passed on", group="proxy", min=1, max=4096, unit="MB",
            help="Bigger uploads are refused before they reach the site."),
    Setting("access_log_days", 7, "Keep access logs", group="logs", min=1, max=90, unit="days"),
]

readable = settings_core.by_key

REGISTRY = settings_core.Registry(
    SETTINGS, groups=GROUPS, connect=db.get_conn, model_config={"str_strip_whitespace": True},
    format_error=readable, unknown_message=lambda keys: f"Unknown setting(s): {', '.join(sorted(keys))}",
    not_object_message="Expected a JSON object of settings.", load_check="isinstance",
    now=lambda: config.iso(), generation=lambda: db.generation, secret_values="omit",
    write_lock=threading.Lock(), logger=logger)

DEFAULTS = REGISTRY.defaults


def get(key: str):
    return REGISTRY.get(key)


def payload() -> dict:
    return REGISTRY.payload(REGISTRY.all())


def update(partial: dict, user: dict | None) -> list[str]:
    """Validate {current values + partial} and store what changed; the changed keys. Raises SettingsError."""
    who = user.get("id") if user else None
    return REGISTRY.update(partial, who)[1]
