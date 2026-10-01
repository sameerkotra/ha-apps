"""App settings (Admin → App settings), stored in the `app_settings` table.

Admins change them inside the app, without a restart. Only `admin_users`
stays on the app's Configuration tab — it decides who may open this page; see
config.py.

Keys (DEFAULTS below):
- `ai_provider`   "" (AI not set up) | "ollama" | "openai" | "anthropic"
- `ai_url`        the provider's address; blank = the provider's standard one
                  (Ollama has none, so it must be typed)
- `ai_model`      model name, e.g. llama3.1:8b, gpt-4o-mini, claude-sonnet-4-5
- `ai_api_key`    SECRET: never returned by the API or shown on a page; only
                  ever sent to the provider in a request header. Blanked in
                  the database download; a restore keeps this install's key.
- `ai_max_tokens` longest answer (tokens) — Anthropic requires a limit
- `expose_daily_calories_sensor`  publish each person's calories to HA

- `get(key)` / `all()` read through a small in-memory cache. It is dropped on
  every write and whenever db.init_db() runs (startup, restore), so readers
  on hot paths (every AI call) don't open a DB connection each time.
- `update(partial, user)` validates the merged result and writes only the
  keys that changed. Unknown keys or bad values raise SettingsError. A blank
  `ai_api_key` keeps the saved key; `clear_ai_api_key: true` removes it.
"""
import ipaddress
import json
import logging
import re
import threading
from urllib.parse import urlsplit

from pydantic import (BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, ValidationError,
                      field_validator)

from . import config, db

logger = logging.getLogger("settings")

PROVIDERS = {"ollama": "Ollama", "openai": "OpenAI-compatible", "anthropic": "Anthropic Claude"}
DEFAULT_URLS = {"ollama": "", "openai": "https://api.openai.com/v1", "anthropic": "https://api.anthropic.com"}

DEFAULTS = {
    "ai_provider": "",
    "ai_url": "",
    "ai_model": "",
    "ai_api_key": "",
    "ai_max_tokens": 4096,
    "expose_daily_calories_sensor": True,
}
SECRET_KEYS = ("ai_api_key",)
# Every key applies live (read at the moment it's used).
META = {key: {"restartRequired": False} for key in DEFAULTS}

NOT_SET_UP = "AI isn't set up — an admin can set it up in Admin → App settings."

_KEY_CHARS = re.compile(r"^[\x21-\x7e]+$")     # printable ASCII, no spaces: safe in a header


class SettingsError(ValueError):
    """Bad or unknown setting — the API turns it into a 422."""


class AppSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    ai_provider: StrictStr
    ai_url: StrictStr = Field(max_length=500)
    ai_model: StrictStr = Field(max_length=200)
    ai_api_key: StrictStr = Field(max_length=500)
    ai_max_tokens: StrictInt = Field(ge=256, le=200000)
    # Strict: true/false only (no "yes", 1, …).
    expose_daily_calories_sensor: StrictBool

    @field_validator("ai_provider")
    @classmethod
    def _check_provider(cls, v: str) -> str:
        if v and v not in PROVIDERS:
            raise ValueError("must be one of: " + ", ".join(PROVIDERS) + " (or empty for no AI)")
        return v

    @field_validator("ai_url")
    @classmethod
    def _check_url(cls, v: str) -> str:
        return normalize_url(v) if v else ""

    @field_validator("ai_model")
    @classmethod
    def _check_model(cls, v: str) -> str:
        if any(c.isspace() for c in v):
            raise ValueError("must be a model name without spaces, e.g. llama3.1:8b")
        return v

    @field_validator("ai_api_key")
    @classmethod
    def _check_key(cls, v: str) -> str:
        if v and not _KEY_CHARS.match(v):
            # Never echo the value: it is (probably) a key.
            raise ValueError("must be the access key exactly as the provider shows it (no spaces or line breaks)")
        return v


def normalize_url(v: str) -> str:
    """http(s) URL with a host, no credentials/query/fragment, no spaces;
    trailing '/' stripped (the client appends '/api/...', '/chat/completions', …)."""
    v = v.strip()
    if any(c.isspace() for c in v):
        raise ValueError("must not contain spaces")
    try:
        parts = urlsplit(v)
        _ = parts.port   # raises ValueError for a non-numeric / out-of-range port
    except ValueError:
        raise ValueError("is not a valid URL")
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("must be an http:// or https:// URL, e.g. http://192.168.1.10:11434")
    if parts.username is not None or parts.password is not None:
        raise ValueError("must not contain a user name or password")
    if parts.query or parts.fragment:
        raise ValueError("must not contain a query string or #fragment")
    return v.rstrip("/")


def readable(e: ValidationError) -> str:
    msgs = []
    for err in e.errors():
        loc = ".".join(str(p) for p in err.get("loc", ())) or "settings"
        msg = err.get("msg", "invalid value")
        if msg.startswith("Value error, "):
            msg = msg[len("Value error, "):]
        msgs.append(f"{loc}: {msg}")
    return "; ".join(msgs)


def _validate(values: dict) -> dict:
    try:
        validated = AppSettings(**values).model_dump()
    except ValidationError as e:
        raise SettingsError(readable(e))
    if validated["ai_provider"] == "ollama" and not validated["ai_url"]:
        raise SettingsError("ai_url: Ollama needs its address, e.g. http://192.168.1.10:11434")
    return validated


# ---------- cache ----------
_lock = threading.Lock()
_cache: dict | None = None
_cache_generation = -1
_writes = 0   # bumped by invalidate(), so a read that raced a write isn't cached


def invalidate() -> None:
    global _cache, _writes
    with _lock:
        _cache = None
        _writes += 1


def _load_rows() -> dict:
    with db.get_conn() as conn:
        rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
    stored = {}
    for r in rows:
        try:
            stored[r["key"]] = json.loads(r["value"])
        except (TypeError, ValueError):
            logger.warning("Ignoring unreadable app setting %r", r["key"])
    return stored


def _snapshot() -> dict:
    """{"values": {...}} — cached until a write/init_db."""
    global _cache, _cache_generation
    with _lock:
        if _cache is not None and _cache_generation == db.generation:
            return _cache
        gen, writes = db.generation, _writes
    stored = _load_rows()
    values = dict(DEFAULTS)
    for key in DEFAULTS:
        if key in stored and isinstance(stored[key], type(DEFAULTS[key])):
            values[key] = stored[key]
    snap = {"values": values}
    with _lock:
        if writes == _writes:
            _cache, _cache_generation = snap, gen
    return snap


def all() -> dict:  # noqa: A001 — the design's name for it
    """Every value, the access key included — for server-side use only."""
    return dict(_snapshot()["values"])


def get(key: str):
    return _snapshot()["values"][key]


# ---------- AI helpers ----------

def ai_provider() -> str:
    return get("ai_provider")


def ai_url(provider: str | None = None, url: str | None = None) -> str:
    """The address, or the provider's standard one when left blank."""
    provider = ai_provider() if provider is None else provider
    url = get("ai_url") if url is None else url
    return (url or DEFAULT_URLS.get(provider, "")).rstrip("/")


def api_key_hint(key: str | None = None) -> str:
    """'…abcd' for the page: enough to recognise a key, never the key."""
    key = get("ai_api_key") if key is None else key
    return f"…{key[-4:]}" if len(key) >= 12 else ("saved" if key else "")


def ai_configured() -> bool:
    provider = ai_provider()
    if provider not in PROVIDERS or not ai_url() or not get("ai_model"):
        return False
    return provider != "anthropic" or bool(get("ai_api_key"))


def ai_host() -> str:
    return urlsplit(ai_url()).hostname or ""


def is_external_host(host: str) -> bool:
    """True when `host` is outside the home network (a public host). Private,
    loopback and link-local addresses, localhost, single-label names and
    .local / .lan / .home / .internal / .localdomain / .home.arpa (and the
    reserved .localhost / .test / .invalid) names count as the home network."""
    host = (host or "").lower().strip("[]").rstrip(".")
    if not host:
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        if host == "localhost" or "." not in host:
            return False
        return not host.endswith((".local", ".lan", ".home", ".internal", ".localdomain", ".home.arpa",
                                  ".localhost", ".test", ".invalid"))
    return not (ip.is_private or ip.is_loopback or ip.is_link_local)


def ai_is_external() -> bool:
    return ai_provider() in PROVIDERS and is_external_host(ai_host())


# ---------- API payload & writes ----------

def payload() -> dict:
    """The GET/PUT /api/admin/settings response shape. The access key itself
    is never included — only whether one is saved and its last 4 characters."""
    snap = _snapshot()
    values = {k: v for k, v in snap["values"].items() if k not in SECRET_KEYS}
    key = snap["values"]["ai_api_key"]
    return {
        "values": values,
        "defaults": {k: v for k, v in DEFAULTS.items() if k not in SECRET_KEYS},
        "meta": {k: dict(v) for k, v in META.items()},
        "secrets": {"ai_api_key": {"saved": bool(key), "hint": api_key_hint(key)}},
        "providers": {k: {"label": PROVIDERS[k], "defaultUrl": DEFAULT_URLS[k]} for k in PROVIDERS},
    }


def _now_iso() -> str:
    return config.utcnow().isoformat(timespec="seconds")


def merged_with(partial: dict) -> dict:
    """{current values + partial}, validated, applying the secret rules: a
    blank ai_api_key keeps the saved key, clear_ai_api_key=true removes it.
    Raises SettingsError. Stores nothing."""
    if not isinstance(partial, dict):
        raise SettingsError("Expected a JSON object of settings.")
    partial = dict(partial)
    clear = partial.pop("clear_ai_api_key", False)
    if not isinstance(clear, bool):
        raise SettingsError("clear_ai_api_key: must be true or false")
    unknown = sorted(k for k in partial if k not in DEFAULTS)
    if unknown:
        raise SettingsError(f"Unknown setting(s): {', '.join(unknown)}")
    current = all()
    if "ai_api_key" in partial:
        new_key = partial["ai_api_key"]
        if new_key is None or (isinstance(new_key, str) and not new_key.strip()):
            partial.pop("ai_api_key")             # blank box = keep the saved key
    if clear:
        partial["ai_api_key"] = ""
    return _validate({**current, **partial})


def update(partial: dict, user: dict | None) -> list[str]:
    """Validate {current values + partial} and store the keys that changed.
    Returns the list of changed keys."""
    current = all()
    validated = merged_with(partial)
    changed = [k for k in DEFAULTS if validated[k] != current[k]]
    if changed:
        who = None
        if user:
            who = user.get("id") or user.get("name")
        now = _now_iso()
        with db.get_conn() as conn:
            for k in changed:
                conn.execute(
                    """INSERT INTO app_settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, ?)
                       ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                           updated_at = excluded.updated_at, updated_by = excluded.updated_by""",
                    (k, json.dumps(validated[k]), now, who),
                )
        invalidate()
        # Key names only — never a value (one of them may be the access key).
        logger.info("App settings changed by %s: %s", who, ", ".join(changed))
    return changed


def restore_secrets(saved: dict) -> None:
    """After a database restore: put back this install's access key when the
    restored file has none (a downloaded backup never carries it)."""
    fill = {k: v for k, v in saved.items() if k in SECRET_KEYS and v and not get(k)}
    if not fill:
        return
    now = _now_iso()
    with db.get_conn() as conn:
        for k, v in fill.items():
            conn.execute(
                """INSERT INTO app_settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, 'kept on restore')
                   ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                       updated_at = excluded.updated_at, updated_by = excluded.updated_by""",
                (k, json.dumps(v), now),
            )
    invalidate()
