"""App settings (Admin → App settings), stored in the `app_settings` table (HOUSEHOLD_ASSISTANT_SPEC.md §8.2).

Admins change them inside the app, without a restart. Only `admin_users` stays on the app's Configuration tab.

- AI: `ai_provider` ("" | "ollama" | "openai" | "anthropic"), `ai_url`, `ai_model`, `ai_api_key` (SECRET: never
  returned or shown, only sent to the provider in a header; blanked in backups, kept on restore), `ai_max_tokens`.
- People: `children_may_ask` (off).
- Limits: `questions_per_hour` (per person, 30), `questions_per_day` (the household, 200),
  `question_timeout` (seconds, 300; 60–900), `keep_days` (30).
- Privacy: `recorder_excluded` (the admin's tick that `household_apps` is out of the recorder), `shared_open`
  ("What was shared" open by default).

Which apps the assistant may ask is not here: it is the `assist_apps` table (Admin → Apps).
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

NOT_SET_UP = "The assistant's AI isn't set up — an admin can set it up in Admin → App settings."

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


GROUPS = [
    Group("ai", "🤖 AI", "The model that plans which apps to ask and writes the answers. The assistant does nothing "
          "until it is set up. Nothing is sent to an outside service unless you choose one."),
    Group("people", "👥 People"),
    Group("limits", "⏱️ Limits"),
    Group("privacy", "🔒 Privacy"),
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
    Setting("ai_max_tokens", 4096, "Longest answer (tokens)", group="ai", min=256, max=200000, strict=True,
            help="The most the model may write in one answer. Claude requires a limit; the other providers ignore it."),
    Setting("children_may_ask", False, "Children may ask", group="people", strict=True,
            help="People marked as children on Admin → People may ask too — and then only about their own things, "
                 "from the apps that let children ask."),
    Setting("questions_per_hour", 30, "Questions per person per hour", group="limits", min=1, max=500, strict=True),
    Setting("questions_per_day", 200, "Questions per day, the whole household", group="limits", min=1, max=5000,
            strict=True),
    Setting("question_timeout", 300, "Longest a question may take (seconds)", group="limits", min=60, max=900,
            strict=True,
            help="From the question to the answer, waking up a local model not counted. A model on the Home "
                 "Assistant machine's CPU needs 300 or more; a cloud model answers well within 60. When the model "
                 "runs out of time after the apps answered, the apps' own words are shown instead."),
    Setting("keep_days", 30, "Keep questions for (days)", group="limits", min=1, max=365, strict=True,
            help="Each person's questions and answers are deleted after this many days. Anyone can clear their own "
                 "at any time."),
    Setting("recorder_excluded", False, "I have excluded household_apps from Home Assistant's recorder",
            group="privacy", strict=True,
            help="The apps' answers travel over Home Assistant's event bus as household_apps events. Add "
                 "recorder: exclude: event_types: [household_apps] to configuration.yaml so they aren't kept in its "
                 "history (see the documentation). The app can't check this; tick it once done."),
    Setting("shared_open", False, "Show \"What was shared\" open", group="privacy", strict=True,
            help="Under each answer, the exact tool calls and what each app returned. Closed by default."),
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


def _prepare(partial: dict, current: dict, flags: dict) -> dict:
    """A blank ai_api_key keeps the saved key; clear_ai_api_key=true removes it."""
    if "ai_api_key" in partial:
        new_key = partial["ai_api_key"]
        if new_key is None or (isinstance(new_key, str) and not new_key.strip()):
            partial.pop("ai_api_key")             # blank box = keep the saved key
    if flags.get("clear_ai_api_key"):
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
