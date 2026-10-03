"""App settings (Admin → App settings, SPEC §8): stored in the database and
read at the moment they are used, so a change applies without a restart.

`admin_users` is deliberately NOT here — it stays on the app's Configuration
tab, because it is how the first admin is known. Holidays are their own table
(db `holidays`, edited on the same page through /api/admin/holidays).

Values are JSON in `app_settings`, one row per key that has been set; a key
without a row uses DEFAULTS. Reads go through a small cache that is dropped on
every write and whenever the database file is (re)initialised.
"""
import json
import logging
import re
import threading

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from . import config, db, games

logger = logging.getLogger("settings")

KEEP_SCORES_CHOICES = (0, 1, 2, 5)          # years; 0 = forever

DEFAULTS: dict = {
    "disabled_games": [],                   # game ids switched off (new games start on)
    "default_look": games.DEFAULT_LOOK,
    "brick_powerups": True,
    "leaderboard": True,
    "school_days": [1, 2, 3, 4, 5],         # ISO weekdays, Monday = 1
    "limit_warnings": False,
    "limit_warning_admins": [],             # HA user ids of the admins to tell
    "notify_records": False,
    "ha_sensors": False,
    "keep_scores_years": 0,
    # AI-made levels (SPEC §11): the same AI settings as Finance Dashboard's
    "ai_levels_enabled": False,
    "ai_provider": "ollama",
    "ai_url": "",
    "ai_model": "",
    "ai_api_key": "",
    "ai_max_output_tokens": 4000,
    "ai_levels_auto": True,
    "ai_levels_ahead": 2,
    "ai_levels_batch": 5,
    "ai_levels_daily_limit": 20,
    "ai_levels_review": False,
    "ai_price_in": 0.0,
    "ai_price_out": 0.0,
}
# Never sent to the browser: the page is told only whether it is set.
SECRETS = {"ai_api_key"}
PROVIDERS = ("ollama", "openai", "anthropic")
PROVIDER_LABELS = {"ollama": "Ollama (on your network)", "openai": "OpenAI-compatible", "anthropic": "Anthropic Claude"}
DEFAULT_URLS = {"ollama": "", "openai": "https://api.openai.com/v1", "anthropic": "https://api.anthropic.com"}
KEYS = tuple(DEFAULTS)
META: dict = {k: {"restartRequired": False} for k in KEYS}

LABELS = {
    "disabled_games": "Games",
    "default_look": "Default look",
    "brick_powerups": "Brick Breaker power-ups",
    "leaderboard": "Leaderboard",
    "school_days": "School days",
    "limit_warnings": "Limit warnings to parents",
    "limit_warning_admins": "Who gets limit warnings",
    "notify_records": "Notify new records",
    "ha_sensors": "Home Assistant sensors",
    "keep_scores_years": "Keep scores for",
    "ai_levels_enabled": "AI levels",
    "ai_provider": "Provider",
    "ai_url": "Address",
    "ai_model": "Model",
    "ai_api_key": "Access key",
    "ai_max_output_tokens": "Most the model may write",
    "ai_levels_auto": "Build ahead automatically",
    "ai_levels_ahead": "Start building this many levels before the end",
    "ai_levels_batch": "Levels per build",
    "ai_levels_daily_limit": "Most levels built a day",
    "ai_levels_review": "Check new levels before they're played",
    "ai_price_in": "Price per million input tokens",
    "ai_price_out": "Price per million output tokens",
}


class SettingsError(ValueError):
    """A readable 422 message."""


class AppSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, strict=True)

    disabled_games: list[str] = Field(max_length=100)
    default_look: str
    brick_powerups: bool
    leaderboard: bool
    school_days: list[int] = Field(max_length=7)
    limit_warnings: bool
    limit_warning_admins: list[str] = Field(max_length=50)
    notify_records: bool
    ha_sensors: bool
    keep_scores_years: int
    ai_levels_enabled: bool
    ai_provider: str
    ai_url: str = Field(max_length=500)
    ai_model: str = Field(max_length=200)
    ai_api_key: str = Field(max_length=1000)
    ai_max_output_tokens: int = Field(ge=256, le=64000)
    ai_levels_auto: bool
    ai_levels_ahead: int = Field(ge=1, le=5)
    ai_levels_batch: int = Field(ge=1, le=20)
    ai_levels_daily_limit: int = Field(ge=0, le=500)
    ai_levels_review: bool
    ai_price_in: float = Field(ge=0, le=1000)
    ai_price_out: float = Field(ge=0, le=1000)

    @field_validator("ai_provider")
    @classmethod
    def _provider(cls, v):
        if v not in PROVIDERS:
            raise ValueError("must be one of " + ", ".join(PROVIDERS))
        return v

    @field_validator("ai_url")
    @classmethod
    def _url(cls, v):
        v = v.rstrip("/")
        if v and not re.match(r"^https?://[^\s/]+\S*$", v, re.I):
            raise ValueError("must start with http:// or https://")
        return v

    @field_validator("ai_model", "ai_api_key")
    @classmethod
    def _one_line(cls, v):
        if any(c in v for c in "\r\n\x00"):
            raise ValueError("must be on one line")
        return v

    @field_validator("disabled_games")
    @classmethod
    def _games(cls, v):
        bad = [g for g in v if g not in games.GAMES]
        if bad:
            raise ValueError("unknown game(s): " + ", ".join(bad))
        return [g for g in games.GAME_IDS if g in v]

    @field_validator("default_look")
    @classmethod
    def _look(cls, v):
        if v not in games.LOOKS:
            raise ValueError("must be one of " + ", ".join(games.LOOKS))
        return v

    @field_validator("school_days")
    @classmethod
    def _days(cls, v):
        if any(d < 1 or d > 7 for d in v):
            raise ValueError("days are 1 (Monday) to 7 (Sunday)")
        return sorted(set(v))

    @field_validator("limit_warning_admins")
    @classmethod
    def _ids(cls, v):
        out = []
        for x in v:
            x = x.strip()
            if not x or len(x) > 200:
                raise ValueError("must be a list of user ids")
            if x not in out:
                out.append(x)
        return out

    @field_validator("keep_scores_years")
    @classmethod
    def _keep(cls, v):
        if v not in KEEP_SCORES_CHOICES:
            raise ValueError("must be 0 (forever), 1, 2 or 5 years")
        return v


def _readable(e: ValidationError) -> str:
    parts = []
    for err in e.errors():
        key = str(err["loc"][0]) if err.get("loc") else "settings"
        msg = err.get("msg", "invalid value").replace("Value error, ", "")
        parts.append(f"{LABELS.get(key, key)}: {msg}")
    return "; ".join(parts) or "Invalid settings."


def validate(values: dict) -> dict:
    try:
        return AppSettings(**values).model_dump()
    except ValidationError as e:
        raise SettingsError(_readable(e)) from None
    except TypeError as e:
        raise SettingsError(str(e)) from None


def _copy_defaults() -> dict:
    return {k: (list(v) if isinstance(v, list) else v) for k, v in DEFAULTS.items()}


_lock = threading.Lock()
_cache: dict | None = None
_cache_gen = -1
_version = 0


def invalidate() -> None:
    global _cache, _version
    with _lock:
        _cache = None
        _version += 1


def _load() -> dict:
    values = _copy_defaults()
    try:
        with db.get_conn() as conn:
            rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
    except Exception:
        logger.exception("Could not read app settings; using defaults")
        return values
    for r in rows:
        if r["key"] not in DEFAULTS:
            continue
        try:
            values = validate(dict(values, **{r["key"]: json.loads(r["value"])}))
        except (ValueError, SettingsError) as e:
            logger.warning("Ignoring stored setting %s: %s", r["key"], e)
    return values


def all() -> dict:  # noqa: A001
    global _cache, _cache_gen
    gen = db.generation()
    with _lock:
        if _cache is not None and _cache_gen == gen:
            return {k: (list(v) if isinstance(v, list) else v) for k, v in _cache.items()}
        ver = _version
    values = _load()
    with _lock:
        if _version == ver and db.generation() == gen:
            _cache, _cache_gen = values, gen
    return {k: (list(v) if isinstance(v, list) else v) for k, v in values.items()}


def get(key: str):
    return all()[key]


def update(partial, user: dict | None) -> tuple[dict, set[str]]:
    """Validate the merged result and write only the keys that changed."""
    if not isinstance(partial, dict):
        raise SettingsError("Send an object of settings to change.")
    unknown = sorted(str(k) for k in partial if k not in DEFAULTS)
    if unknown:
        raise SettingsError("Unknown setting(s): " + ", ".join(unknown))
    current = all()
    new = validate({**current, **partial})
    changed = {k for k in KEYS if new[k] != current[k]}
    if changed:
        who = (user.get("username") or user.get("id")) if user else None
        with db.get_conn() as conn:
            for k in sorted(changed):
                conn.execute(
                    "INSERT INTO app_settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at, "
                    "updated_by = excluded.updated_by",
                    (k, json.dumps(new[k]), config.now_iso(), who))
        invalidate()
        logger.info("App settings changed by %s: %s", who or "?", ", ".join(sorted(changed)))
    return all(), changed


def public(values: dict) -> dict:
    """Values for the browser: secrets blanked."""
    return {k: ("" if k in SECRETS else v) for k, v in values.items()}


def payload() -> dict:
    values = all()
    return {"values": public(values), "defaults": public(_copy_defaults()),
            "meta": {k: dict(v) for k, v in META.items()},
            "secretsSet": {k: bool(values[k]) for k in sorted(SECRETS)},
            "providers": [{"id": p, "label": PROVIDER_LABELS[p], "defaultUrl": DEFAULT_URLS[p]} for p in PROVIDERS]}


def ai_url() -> str:
    """The address to use: the one set, else the provider's usual one (Ollama has none)."""
    return (get("ai_url") or DEFAULT_URLS.get(get("ai_provider"), "")).rstrip("/")


def ai_problem() -> str | None:
    """Why AI levels can't be built right now, or None."""
    v = all()
    if not v["ai_levels_enabled"]:
        return "AI levels are off (Admin → App settings)."
    if not v["ai_model"]:
        return "No AI model is set (Admin → App settings)."
    if not ai_url():
        return "Ollama needs its address (Admin → App settings)."
    if v["ai_provider"] == "anthropic" and not v["ai_api_key"]:
        return "Anthropic Claude needs an access key (Admin → App settings)."
    return None


# ---------------------------------------------------------------------------
# derived values
# ---------------------------------------------------------------------------

def game_enabled(game: str) -> bool:
    return games.exists(game) and game not in get("disabled_games")


def enabled_games() -> list[str]:
    off = set(get("disabled_games"))
    return [g for g in games.GAME_IDS if g not in off]


def modes_for(game: str) -> list[dict]:
    """The modes shown for a game: Brick Breaker's power-ups mode is hidden
    while the power-ups setting is off."""
    modes = [dict(m) for m in games.GAMES[game]["modes"]]
    if game == "brick" and not get("brick_powerups"):
        modes = [m for m in modes if m["id"] != "powerups"]
    return modes


def mode_allowed(game: str, mode: str) -> bool:
    return any(m["id"] == mode for m in modes_for(game))
