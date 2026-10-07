"""App settings (Admin → App settings), stored in the `app_settings` table (SPEC.md §7).

Admins change them inside the app, without a restart. Only `admin_users` stays on the app's Configuration tab.

- Server: `server_on` (off until an admin turns it on; nothing is downloaded on its own).
- When models stay loaded (§6.2): `keep_model` ("" = the first text model downloaded, "none" = no model kept;
  chosen on the Models list, so hidden here), `day_start` / `day_end` (HH:MM, Home Assistant's time zone),
  `day_unload_min` (models other than the kept one, in the daytime), `night_unload_min` (every model, outside the
  window; 0 = right after each answer), `preload`, `max_loaded` (1–2).
- Limits (§6.1): `threads` (0 = automatic), `context`, `max_answer_tokens`, `max_run_min`, `queue`.
- Callers: `answer_first` (caller names whose requests go first; ticked on the callers list, so hidden here),
  `require_key`.

`max_loaded` and `context` are the model server's own settings: changing them restarts it (the page says so).
"""
import logging
import re

from pydantic import StrictStr

from . import config, db
from .common import settings_core
from .common.settings_core import Group, Setting, SettingsError  # noqa: F401  (SettingsError: the routes' name)

logger = logging.getLogger("settings")

_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
MODEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,150}(:[A-Za-z0-9._-]{1,80})?$")
CALLER_NAME = re.compile(r"^(addr:[0-9a-fA-F.:]{2,45}|[a-z0-9_]{1,64})$")

# Changing one of these restarts the model server (they are its environment, SPEC.md §3).
SERVER_KEYS = frozenset({"max_loaded", "context"})


def _check_time(v: str) -> str:
    if not _TIME.match(v):
        raise ValueError("must be a time of day as HH:MM, e.g. 05:00")
    return v


def _check_keep_model(v: str) -> str:
    if v and v != "none" and not MODEL_NAME.match(v):
        raise ValueError("must be a model name such as qwen2.5:3b, or none")
    return v


def _check_callers(v: list) -> list:
    out = []
    for c in v:
        c = c.strip()
        if not CALLER_NAME.match(c):
            raise ValueError(f"{c!r} is not a caller name")
        if c not in out:
            out.append(c)
    if len(out) > 50:
        raise ValueError("at most 50 callers")
    return out


GROUPS = [
    Group("schedule", "🕒 When models stay loaded",
          "Loading a model takes seconds to a minute; keeping it loaded costs its memory. The model chosen with "
          "Keep loaded on the Models list stays in memory during the day; every model unloads sooner at night."),
    Group("limits", "⚖️ Limits", "One model answers one request at a time, on the CPU. These keep it fair and "
          "keep Home Assistant responsive."),
    Group("access", "🔑 Access"),
]

SETTINGS = [
    Setting("server_on", False, "Model server on", strict=True, hidden=True),
    Setting("keep_model", "", "Keep loaded", group="schedule", strict=True, max_length=240, hidden=True,
            validators=[_check_keep_model]),
    Setting("day_start", "05:00", "Daytime starts", group="schedule", type=StrictStr, max_length=5,
            validators=[_check_time], placeholder="05:00",
            help="In Home Assistant's time zone. A window that passes midnight (22:00–06:00) is allowed."),
    Setting("day_end", "22:00", "Daytime ends", group="schedule", type=StrictStr, max_length=5,
            validators=[_check_time], placeholder="22:00"),
    Setting("day_unload_min", 10, "During the day, unload other models after", group="schedule", min=0, max=1440,
            strict=True, unit="min",
            help="For models other than the kept one, which stays loaded all day. 0 = right after each answer."),
    Setting("night_unload_min", 10, "At night, unload after", group="schedule", min=0, max=1440, strict=True,
            unit="min", help="Every model, the kept one included, outside the daytime window. 0 = right after each "
                             "answer."),
    Setting("preload", True, "Load at the start of the day", group="schedule", strict=True,
            help="At the window's start the kept model is loaded before anyone asks, so the first question of the "
                 "morning doesn't wait for it."),
    Setting("max_loaded", 1, "Models loaded at once", group="schedule", min=1, max=2, strict=True,
            help="2 keeps the kept text model loaded while a vision model reads a receipt — only when the memory "
                 "allows both. Changing it restarts the model server."),
    Setting("threads", 0, "Threads", group="limits", min=0, max=256, strict=True,
            help="0 = automatic: all cores but one. Applies to apps set to the Ollama provider."),
    Setting("context", 8192, "Context length", group="limits", min=2048, max=32768, strict=True, unit="tokens",
            help="The most text a request may carry. Longer needs more memory. Changing it restarts the model "
                 "server."),
    Setting("max_answer_tokens", 2048, "Longest answer", group="limits", min=256, max=8192, strict=True,
            unit="tokens", help="An app asking for more gets this many."),
    Setting("max_run_min", 15, "Longest run", group="limits", min=1, max=60, strict=True, unit="min",
            help="A request still running after this is stopped (the app gets an error), so a stuck answer can't "
                 "hold the CPU."),
    Setting("queue", 4, "Queue length", group="limits", min=1, max=50, strict=True, unit="waiting",
            help="Requests that may wait while one runs. More are told to try again shortly. Each app may have at "
                 "most 2 waiting."),
    Setting("answer_first", ["household_assistant"], "Answer first", group="access", kind="list", hidden=True,
            validators=[_check_callers]),
    Setting("require_key", False, "Require an access key", group="access", strict=True,
            help="Off: any app on Home Assistant's internal network may use the model (never download, delete or "
                 "replace models). On: every app needs a key from the Access keys list. Turn it on if you install "
                 "an app you don't trust, or when the model is published on your network."),
]


def _now_iso() -> str:
    return config.utcnow().isoformat(timespec="seconds")


REGISTRY = settings_core.Registry(
    SETTINGS, groups=GROUPS, connect=db.get_conn, model_config={"str_strip_whitespace": True},
    format_error=settings_core.by_key,
    unknown_message=lambda keys: f"Unknown setting(s): {', '.join(sorted(keys))}",
    not_object_message="Expected a JSON object of settings.", load_check="isinstance", now=_now_iso,
    generation=lambda: db.generation, logger=logger)

DEFAULTS = REGISTRY.defaults


def invalidate() -> None:
    REGISTRY.invalidate()


def all() -> dict:  # noqa: A001
    return REGISTRY.all()


def get(key: str):
    return REGISTRY.get(key)


def payload() -> dict:
    return REGISTRY.payload(REGISTRY.all())


def update(partial: dict, user: dict | None) -> list[str]:
    """Validate {current values + partial} and store the keys that changed; returns them. Raises SettingsError."""
    who = (user.get("id") or user.get("name")) if user else None
    return REGISTRY.update(partial, who)[1]
