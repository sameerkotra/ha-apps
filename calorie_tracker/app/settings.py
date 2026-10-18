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
import logging
import re
from urllib.parse import urlsplit

from pydantic import StrictStr

from . import config, db
from .common import settings_core
from .common.settings_core import Group, Setting, SettingsError  # noqa: F401  (SettingsError: the routes' name)

logger = logging.getLogger("settings")

PROVIDERS = {"ollama": "Ollama", "openai": "OpenAI-compatible", "anthropic": "Anthropic Claude"}
DEFAULT_URLS = {"ollama": "", "openai": "https://api.openai.com/v1", "anthropic": "https://api.anthropic.com"}

NOT_SET_UP = "AI isn't set up — an admin can set it up in Admin → App settings."

_KEY_CHARS = re.compile(r"^[\x21-\x7e]+$")     # printable ASCII, no spaces: safe in a header


def _check_provider(v: str) -> str:
    if v and v not in PROVIDERS:
        raise ValueError("must be one of: " + ", ".join(PROVIDERS) + " (or empty for no AI)")
    return v


def _check_url(v: str) -> str:
    return normalize_url(v) if v else ""


def _check_model(v: str) -> str:
    if any(c.isspace() for c in v):
        raise ValueError("must be a model name without spaces, e.g. llama3.1:8b")
    return v


def _check_key(v: str) -> str:
    if v and not _KEY_CHARS.match(v):
        # Never echo the value: it is (probably) a key.
        raise ValueError("must be the access key exactly as the provider shows it (no spaces or line breaks)")
    return v


DEFAULT_SYMPTOMS = ("Bloating, Stomach pain, Heartburn, Nausea, Gas, Diarrhoea, Constipation, Headache, Fatigue, "
                    "Brain fog, Rash, Itching")
DEFAULT_FOOD_TAGS = ("dairy, gluten, wheat, eggs, nuts, soy, spicy, fried, sugar, caffeine, alcohol, onion, garlic, "
                     "beans, raw veg, fruit, processed")


def word_list(text: str) -> list[str]:
    """A comma-separated setting as its words: trimmed, single spaces, no blanks or repeats (case-insensitive)."""
    out, seen = [], set()
    for w in (text or "").split(","):
        w = " ".join(w.split())
        if w and w.lower() not in seen:
            seen.add(w.lower())
            out.append(w)
    return out


def _check_word_list(v: str) -> str:
    words = word_list(v)
    if any(len(w) > 60 for w in words):
        raise ValueError("each one can be at most 60 characters (separate them with commas)")
    if len(words) > 60:
        raise ValueError("at most 60 of them")
    return ", ".join(words)


GROUPS = [
    Group("ai", "🤖 AI", "Used by \"✨ Estimate with AI\" and the AI Assistant. Leave the provider on \"Not set up\" to "
          "run without AI — everything else works."),
    Group("limits", "🚦 AI limits", "Keep one person (or a stuck page) from running up a cloud bill or tying up your "
          "AI server. At most two AI requests run at a time for the whole household."),
    Group("symptoms", "🩺 Symptoms and patterns", "Each person logs their own symptoms on the Symptoms page; nobody "
          "else sees them (an admin who switches to someone sees their data, as with food). Patterns shows which foods "
          "and food tags came before a symptom more often than on other days — together, not the cause."),
    Group("ha", "🏠 Home Assistant"),
    Group("assistant", "💬 Household Assistant", "The Household Assistant app answers questions from what the household "
          "apps know. Calorie Tracker tells a person only their own day: calories and macros against their goals."),
]

# Every key applies live (read at the moment it's used); none needs a restart.
SETTINGS = [
    Setting("ai_provider", "", "Provider", group="ai", type=StrictStr, validators=[_check_provider],
            choices=[("", "Not set up (no AI)"), ("ollama", "Ollama (your own computer or server)"),
                     ("openai", "OpenAI-compatible (OpenAI, OpenRouter, Groq, LM Studio, …)"),
                     ("anthropic", "Anthropic Claude")],
            help="Ollama runs models on your own hardware, so nothing leaves your network. OpenAI-compatible covers "
                 "OpenAI and any service or local server that speaks its API. Claude is Anthropic's API."),
    Setting("ai_url", "", "Address", group="ai", kind="url", strict=True, max_length=500, validators=[_check_url],
            placeholder="http://192.168.1.10:11434",
            help="Ollama: its address, e.g. http://192.168.1.10:11434 (required). OpenAI-compatible: the API base — "
                 "blank = https://api.openai.com/v1 (LM Studio: e.g. http://192.168.1.10:1234/v1). Claude: blank = "
                 "https://api.anthropic.com."),
    Setting("ai_model", "", "Model", group="ai", strict=True, max_length=200, validators=[_check_model],
            placeholder="e.g. llama3.1:8b, gpt-4o-mini, claude-sonnet-4-5",
            help="The model's exact name. Test connection lists the models the provider offers."),
    # SECRET: never returned by the API or shown on a page; only ever sent to the provider in a request
    # header. Blanked in the database download; a restore keeps this install's key.
    Setting("ai_api_key", "", "Access key", group="ai", secret=True, strict=True, max_length=500,
            validators=[_check_key], placeholder="Paste the key", page={"clearFlag": "clear_ai_api_key"},
            help="Needed for Claude and for OpenAI and most cloud services; not for Ollama or LM Studio. Stored in "
                 "the app's database, never shown again, never logged, and left out of database downloads. Leave the "
                 "box empty to keep the saved key."),
    Setting("ai_max_tokens", 2000, "Longest answer (tokens)", group="ai", min=256, max=200000, strict=True,
            help="The most the model may write in one answer (an estimate or a chat reply), for every provider. "
                 "2000 is plenty for both; raise it for a model that thinks out loud before answering."),
    Setting("ai_per_minute", 20, "AI requests per person per minute", group="limits", min=0, max=1000, strict=True,
            help="Estimates and chat messages one person can ask for in a minute; more get \"Too many AI "
                 "requests\". 0 = no limit."),
    Setting("ai_per_day", 200, "AI requests per person per day", group="limits", min=0, max=100000, strict=True,
            help="Estimates and chat messages one person can ask for in a day (Home Assistant's day). Deleting "
                 "entries doesn't give any back. 0 = no limit."),
    Setting("symptom_list", DEFAULT_SYMPTOMS, "Symptoms to pick from", group="symptoms", strict=True,
            max_length=1000, validators=[_check_word_list],
            help="Shown as one-tap buttons on the Symptoms page, separated by commas. Anyone can also type their "
                 "own; the ones a person uses most are offered to them first."),
    Setting("food_tag_list", DEFAULT_FOOD_TAGS, "Food tags to pick from", group="symptoms", strict=True,
            max_length=1000, validators=[_check_word_list],
            help="Offered when logging or saving a food, separated by commas. Anyone can add their own tags too. "
                 "Patterns compares tags as well as foods, so “dairy” covers milk, paneer and yoghurt at once."),
    Setting("pattern_hours", 6, "Look back before a symptom (hours)", group="symptoms", min=1, max=48, strict=True,
            help="Patterns counts what was eaten in this many hours before each symptom."),
    Setting("pattern_min", 3, "Times a symptom needs before Patterns compares", group="symptoms", min=2, max=50,
            strict=True, help="Fewer than this and the symptom is listed, but not compared — too few to say anything."),
    # Strict: true/false only (no "yes", 1, …).
    Setting("expose_daily_calories_sensor", True, "Publish daily calories to Home Assistant", group="ha", strict=True,
            help="The household's switch for the sensor.calorie_tracker_<user>_daily_calories sensors: only each "
                 "person's calories logged today — never macros, weight or food names — and only for people who "
                 "turned on \"Publish my calories to Home Assistant\" on their Goals page (on for everyone who used "
                 "the app before that switch existed, off for people new since). Turning this off removes every "
                 "sensor from Home Assistant; on publishes them right away."),
    Setting("assistant_answers", True, "Answer the Household Assistant", group="assistant", strict=True,
            help="Lets the Household Assistant tell each person their own day — calories, macros and meals against "
                 "their goals — never anyone else's, and never weight."),
    Setting("bus_signing_key", "", "App-messages signing key", group="assistant", secret=True, max_length=200,
            help="The same key in every household app (Admin → App settings there). Without one, any app on Home "
                 "Assistant's network can send messages as any other."),
]


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


readable = settings_core.by_key           # "<key>: <message>; …" (also the test-connection route's 422)


def effective_host(provider, url) -> str:
    """The host an AI request would go to for this provider and address (blank address = the provider's own)."""
    provider = provider if isinstance(provider, str) else ""
    url = url.strip() if isinstance(url, str) else ""
    try:
        return (urlsplit(url or DEFAULT_URLS.get(provider, "")).hostname or "").lower()
    except ValueError:
        return ""


def key_would_move(partial: dict, current: dict) -> bool:
    """True when `partial` sends the requests somewhere else (another host or provider) without a new key typed:
    the saved key was given for the saved address, so it must not follow to a new one."""
    provider = partial.get("ai_provider", current.get("ai_provider"))
    url = partial.get("ai_url", current.get("ai_url"))
    moved = (effective_host(provider, url) != effective_host(current.get("ai_provider"), current.get("ai_url"))
             or provider != current.get("ai_provider"))
    return moved and bool(current.get("ai_api_key"))


def _prepare(partial: dict, current: dict, flags: dict) -> dict:
    """A blank ai_api_key keeps the saved key; clear_ai_api_key=true removes it; a new address or provider without
    a new key removes it too (the key never follows to another server)."""
    if "ai_api_key" in partial:
        new_key = partial["ai_api_key"]
        if new_key is None or (isinstance(new_key, str) and not new_key.strip()):
            partial.pop("ai_api_key")             # blank box = keep the saved key
    if flags.get("clear_ai_api_key"):
        partial["ai_api_key"] = ""
    if "ai_api_key" not in partial and key_would_move(partial, current):
        partial["ai_api_key"] = ""
    return partial


def _check(merged: dict, partial: dict, current: dict) -> None:
    if merged["ai_provider"] == "ollama" and not merged["ai_url"]:
        raise SettingsError("ai_url: Ollama needs its address, e.g. http://192.168.1.10:11434")


def _now_iso() -> str:
    return config.utcnow().isoformat(timespec="seconds")


REGISTRY = settings_core.Registry(
    SETTINGS, groups=GROUPS, connect=db.get_conn, model_config={"str_strip_whitespace": True},
    format_error=readable, unknown_message=lambda keys: f"Unknown setting(s): {', '.join(sorted(keys))}",
    not_object_message="Expected a JSON object of settings.",
    flags={"clear_ai_api_key": "clear_ai_api_key: must be true or false"},
    prepare=_prepare, check=_check, load_check="isinstance", now=_now_iso,
    generation=lambda: db.generation, secret_values="omit", logger=logger)

AppSettings = REGISTRY.model
DEFAULTS = REGISTRY.defaults
SECRET_KEYS = tuple(REGISTRY.secrets)
META = REGISTRY.meta()


def invalidate() -> None:
    REGISTRY.invalidate()


def all() -> dict:  # noqa: A001 — the design's name for it
    """Every value, the access key included — for server-side use only."""
    return REGISTRY.all()


def get(key: str):
    return REGISTRY.get(key)


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
    values = REGISTRY.all()
    key = values["ai_api_key"]
    return REGISTRY.payload(
        values,
        secrets={"ai_api_key": {"saved": bool(key), "hint": api_key_hint(key)}},
        providers={k: {"label": PROVIDERS[k], "defaultUrl": DEFAULT_URLS[k]} for k in PROVIDERS})


def update(partial: dict, user: dict | None) -> list[str]:
    """Validate {current values + partial} and store the keys that changed.
    Returns the list of changed keys. A blank `ai_api_key` keeps the saved
    key; `clear_ai_api_key: true` removes it. Raises SettingsError."""
    who = (user.get("id") or user.get("name")) if user else None
    return REGISTRY.update(partial, who)[1]
