"""When models stay loaded: the daytime window and the keep_alive each request gets (SPEC.md §6.2).

Ollama keeps a model loaded for the `keep_alive` of the last request that used it (seconds; -1 = until told
otherwise, 0 = unload right after). The gateway replaces every caller's keep_alive with `keep_alive()` below,
and the schedule loop (server.Schedule) loads the kept model at the start of the day and shortens every
model's time at its end. The pure rules live here so they can be tested with any clock.
"""
from datetime import datetime

FOREVER = -1


def minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def in_daytime(now: datetime, start: str, end: str) -> bool:
    """`now` (Home Assistant's local time) inside [start, end). A window that passes midnight (22:00–06:00) is
    allowed; start == end means the whole day."""
    t = now.hour * 60 + now.minute
    s, e = minutes(start), minutes(end)
    if s == e:
        return True
    if s < e:
        return s <= t < e
    return t >= s or t < e


def norm_model(name: str) -> str:
    """Ollama's full name: "qwen2.5" → "qwen2.5:latest"."""
    name = (name or "").strip()
    if not name:
        return name
    last = name.rsplit("/", 1)[-1]
    return name if ":" in last else name + ":latest"


def kept_model(values: dict, text_models: list[str]) -> str | None:
    """The model kept loaded in the daytime: the setting, or the first text model downloaded when it is blank;
    None for "none" (or nothing downloaded)."""
    v = values.get("keep_model") or ""
    if v == "none":
        return None
    if v:
        return norm_model(v)
    return text_models[0] if text_models else None


def keep_alive(model: str, values: dict, now: datetime, kept: str | None) -> int:
    """Seconds Ollama keeps `model` loaded after this request (-1: forever)."""
    if in_daytime(now, values["day_start"], values["day_end"]):
        if kept and norm_model(model) == kept:
            return FOREVER
        return int(values["day_unload_min"]) * 60
    return int(values["night_unload_min"]) * 60


def night_keep_alive(idle_seconds: float, values: dict) -> int:
    """At the window's end: what's left of the night rule for a model idle this long (0 = unload now)."""
    left = int(values["night_unload_min"]) * 60 - int(idle_seconds)
    return max(0, left)
