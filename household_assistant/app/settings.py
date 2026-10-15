"""App settings (Admin → App settings), stored in the `app_settings` table (HOUSEHOLD_ASSISTANT_SPEC.md §8.2).

Admins change them inside the app, without a restart. Only `admin_users` stays on the app's Configuration tab.

- AI: `ai_on` (Use AI: off = no model is ever called, every question is a quick answer — §17.10), `ai_provider`
  ("" | "ollama" | "openai" | "anthropic"), `ai_url`, `ai_model`, `ai_api_key` (SECRET: never
  returned or shown, only sent to the provider in a header; blanked in backups, kept on restore), `ai_max_tokens`,
  `ai_tool_calls` ("auto": the model's native tool calling where it has it; "json": always the JSON plan).
- Quick answers (§17): `quick_on` (questions answered by rules, without a model, first), `quick_unsure` ("ai": a
  question the rules can't place goes to the model; "choices": the page shows what it can answer), `quick_learn_taps`,
  `quick_learn_ai`, `quick_ai_now` (use what the model taught at once), `quick_timeout` (seconds before "… is slow").
  `quick_on` and `ai_on` can't both be off.
- People: `assist_answers` (off: questions from Home Assistant's Assist, via the companion integration),
  `children_may_ask` (off).
- Limits: `questions_per_hour` (per person, 30), `questions_per_day` (the household, 200),
  `question_timeout` (seconds, 300; 60–900), `keep_days` (30).
- Privacy: `recorder_excluded` (the admin's tick that `household_apps` is out of the recorder), `shared_open`
  ("What was shared" open by default).
- Briefings: `briefing_speakers` (the speakers a briefing may be read aloud on; empty = every one of Home
  Assistant's), `quiet_from` / `quiet_until` (HH:MM; no reading aloud between them; both empty = never quiet).
- Household apps: `bus_signing_key` (SECRET: the app-messages signing key, the same in every household app; empty =
  unsigned, as before).

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
AI_OFF = "That needs the AI, which an admin has turned off."
NOTHING_ON = ("quick_on: Quick answers can't be off while Use AI is off — nothing could answer. Turn one of them on.")

_KEY_CHARS = re.compile(r"^[\x21-\x7e]+$")     # printable ASCII, no spaces: safe in a header
_SPEAKER_RE = re.compile(r"^media_player\.[a-z0-9_]{1,100}$")
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
MAX_SPEAKERS = 50


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


def _check_speakers(v: str) -> str:
    """ "media_player.kitchen, media_player.lounge" (commas, spaces or new lines between them), tidied."""
    names = [x for x in re.split(r"[\s,;]+", v or "") if x]
    bad = [x for x in names if not _SPEAKER_RE.match(x)]
    if bad:
        raise ValueError(f"must be media players, e.g. media_player.kitchen (not {bad[0][:40]!r})")
    names = list(dict.fromkeys(names))
    if len(names) > MAX_SPEAKERS:
        raise ValueError(f"at most {MAX_SPEAKERS} speakers")
    return ", ".join(names)


def _check_time(v: str) -> str:
    v = (v or "").strip()
    if v and not _TIME_RE.match(v):
        raise ValueError("must be a time as HH:MM (24-hour), e.g. 22:00, or empty")
    return v


def allowed_speakers() -> set[str]:
    """The speakers an admin allows briefings on; empty = every one of Home Assistant's."""
    return {x for x in get("briefing_speakers").split(", ") if x}


GROUPS = [
    Group("ai", "🤖 AI", "The model that answers what Quick answers can't: it plans which apps to ask and writes the "
          "answer. Without one (or with Use AI off) the assistant answers the common questions only. Nothing is sent "
          "to an outside service unless you choose one."),
    Group("quick", "⚡ Quick answers", "The common questions — today's tasks, the shopping list, bills, birthdays, "
          "who owes whom — answered in a second or two by rules, without a model: the app's own words, with links. "
          "Admin → Quick answers shows how a question is understood and lets you add your own phrases."),
    Group("people", "👥 People"),
    Group("limits", "⏱️ Limits"),
    Group("privacy", "🔒 Privacy"),
    Group("briefings", "🌅 Briefings", "Where and when a briefing may be read aloud. Phones get it whatever is set here."),
    Group("apps", "🔗 Household apps"),
]

# Every key applies live (read at the moment it's used); none needs a restart.
SETTINGS = [
    Setting("ai_on", True, "Use AI", group="ai", strict=True,
            help="Off: the assistant never calls a model — not for questions, learning or a connection test. Every "
                 "question gets a quick answer, a Did you mean, or the questions it can answer. The model's settings "
                 "below stay saved. The briefings don't use a model either way."),
    Setting("ai_provider", "", "Provider", group="ai", enabled_if="ai_on", type=StrictStr, validators=[_check_provider],
            choices=[("", "Not set up (no AI)"), ("ollama", "Ollama (your own computer or server)"),
                     ("openai", "OpenAI-compatible (OpenAI, OpenRouter, Groq, LM Studio, …)"),
                     ("anthropic", "Anthropic Claude")],
            help="Ollama runs models on your own hardware, so nothing leaves your network. OpenAI-compatible covers "
                 "OpenAI and any service or local server that speaks its API. Claude is Anthropic's API."),
    Setting("ai_url", "", "Address", group="ai", enabled_if="ai_on",
            kind="url", strict=True, max_length=500, validators=[_check_url],
            placeholder="http://192.168.1.10:11434",
            help="Ollama: its address, e.g. http://192.168.1.10:11434 (required). OpenAI-compatible: the API base — "
                 "blank = https://api.openai.com/v1 (LM Studio: e.g. http://192.168.1.10:1234/v1). Claude: blank = "
                 "https://api.anthropic.com."),
    Setting("ai_model", "", "Model", group="ai", enabled_if="ai_on",
            strict=True, max_length=200, validators=[_check_model],
            placeholder="e.g. llama3.1:8b, gpt-4o-mini, claude-sonnet-4-5",
            help="The model's exact name. Test connection lists the models the provider offers."),
    # SECRET: never returned by the API or shown on a page; only ever sent to the provider in a request
    # header. Blanked in the database download; a restore keeps this install's key.
    Setting("ai_api_key", "", "Access key", group="ai", enabled_if="ai_on", secret=True, strict=True, max_length=500,
            validators=[_check_key], placeholder="Paste the key", page={"clearFlag": "clear_ai_api_key"},
            help="Needed for Claude and for OpenAI and most cloud services; not for Ollama or LM Studio. Stored in "
                 "the app's database, never shown again, never logged, and left out of database downloads. Leave the "
                 "box empty to keep the saved key."),
    Setting("ai_max_tokens", 4096, "Longest answer (tokens)", group="ai", enabled_if="ai_on",
            min=256, max=200000, strict=True,
            help="The most the model may write in one answer. Claude requires a limit; the other providers ignore it."),
    Setting("ai_tool_calls", "auto", "Tool calls", group="ai", enabled_if="ai_on",
            choices=[("auto", "The model's own tool calling where it has it (recommended)"),
                     ("json", "Always ask the model for a JSON plan")],
            help="Most current models (Claude, GPT, Qwen 2.5, Llama 3.1 and newer) choose tools natively, which is "
                 "more reliable. A model without it is noticed on its first question and asked for a JSON plan "
                 "from then on. Choose the JSON plan if a model calls tools badly."),
    Setting("quick_on", True, "Quick answers", group="quick", strict=True,
            help="Common questions are answered by rules first, in a second or two, without the model."),
    Setting("quick_unsure", "ai", "When a question isn't understood", group="quick", enabled_if="quick_on",
            choices=[("ai", "Ask the AI"), ("choices", "Show what I can answer")],
            help="Ask the AI needs Use AI on and a model set up; otherwise the questions it can answer are shown."),
    Setting("quick_learn_taps", True, "Learn from choices", group="quick", enabled_if="quick_on", strict=True,
            help="When someone picks one of the Did you mean choices, the household's way of asking is remembered "
                 "(not children's)."),
    Setting("quick_learn_ai", True, "Learn from the AI", group="quick", enabled_if="ai_on", strict=True,
            help="When the AI answers a question with one app's answer, that way of asking is remembered — used once "
                 "it has been seen twice."),
    Setting("quick_ai_now", False, "Use what the AI learned straight away", group="quick", enabled_if="ai_on",
            strict=True,
            help="Use a phrase the AI taught after the first time instead of the second."),
    Setting("quick_timeout", 5, "Quick call timeout (seconds)", group="quick", enabled_if="quick_on",
            min=2, max=20, strict=True,
            help="After this long, the page says which app is slow to answer, and waits up to 20 seconds."),
    Setting("assist_answers", False, "Answer Assist (Home Assistant's voice assistant)", group="people",
            strict=True,
            help="With the Household Assistant integration installed in Home Assistant, Assist can pass questions "
                 "here and speak the answers. Each question is asked as the Home Assistant user speaking (or the "
                 "integration's chosen person, for voice satellites), with all the same checks. The answer goes "
                 "back over Home Assistant's event bus, like the apps' answers: keep household_apps out of the "
                 "recorder."),
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
    Setting("briefing_speakers", "", "Speakers briefings may use", group="briefings", strict=True, max_length=6000,
            validators=[_check_speakers], placeholder="media_player.kitchen, media_player.lounge",
            help="The media players a person may choose to hear their briefing on, separated by commas. Empty: every "
                 "speaker Home Assistant has. A speaker taken off this list is no longer read aloud on."),
    Setting("quiet_from", "", "Quiet hours from", group="briefings", strict=True, max_length=5,
            validators=[_check_time], placeholder="22:00",
            help="From this time (HH:MM) briefings aren't read aloud on speakers; they still go to phones. Empty with "
                 "Quiet hours until: never quiet."),
    Setting("quiet_until", "", "Quiet hours until", group="briefings", strict=True, max_length=5,
            validators=[_check_time], placeholder="07:00",
            help="Until this time (HH:MM). Quiet hours may run past midnight, e.g. 22:00 to 07:00."),
    # SECRET: never returned or shown; blanked in backups.
    Setting("bus_signing_key", "", "App-messages signing key", group="apps", secret=True, max_length=200,
            page={"clearFlag": "clear_bus_signing_key"},
            help="The same key in every household app (Admin → App settings there), and in the Household Assistant "
                 "integration's options for Assist. Without one, any app on Home Assistant's network can send "
                 "messages as any other. Leave the box empty to keep the saved key."),
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
    """A blank secret keeps the saved one; clear_<secret>=true removes it. A saved access key goes only to the
    provider and address it was typed for: changing either needs the key typed again (or cleared)."""
    for key in ("ai_api_key", "bus_signing_key"):
        if key in partial:
            new_key = partial[key]
            if new_key is None or (isinstance(new_key, str) and not new_key.strip()):
                partial.pop(key)                  # blank box = keep the saved key
        if flags.get(f"clear_{key}"):
            partial[key] = ""
    if current.get("ai_api_key") and "ai_api_key" not in partial and key_target_changed(current, partial):
        raise SettingsError(RETYPE_KEY)
    return partial


RETYPE_KEY = ("ai_api_key: the saved access key was for another provider or address. Type the key again for the new "
              "one, or remove it.")


def _target(provider: str, url: str) -> tuple[str, str]:
    """Where a key goes: the provider and the host:port of its address (the standard one when blank)."""
    full = ai_url(provider, url)
    try:
        parts = urlsplit(full)
        return provider, f"{parts.scheme}://{(parts.hostname or '').lower()}:{parts.port or ''}"
    except ValueError:
        return provider, full


def key_target_changed(current: dict, new: dict) -> bool:
    """True when `new` (a partial: missing keys keep `current`'s) sends the key somewhere else."""
    provider = new.get("ai_provider", current.get("ai_provider")) or ""
    url = new.get("ai_url", current.get("ai_url")) or ""
    if isinstance(url, str):
        url = url.strip()
    return _target(provider, str(url)) != _target(current.get("ai_provider") or "", current.get("ai_url") or "")


def _check(merged: dict, partial: dict, current: dict) -> None:
    if not merged["quick_on"] and not merged["ai_on"]:
        raise SettingsError(NOTHING_ON)
    if merged["ai_provider"] == "ollama" and not merged["ai_url"]:
        raise SettingsError("ai_url: Ollama needs its address, e.g. http://192.168.1.10:11434")


def _now_iso() -> str:
    return config.utcnow().isoformat(timespec="seconds")


REGISTRY = settings_core.Registry(
    SETTINGS, groups=GROUPS, connect=db.get_conn, model_config={"str_strip_whitespace": True},
    format_error=readable, unknown_message=lambda keys: f"Unknown setting(s): {', '.join(sorted(keys))}",
    not_object_message="Expected a JSON object of settings.",
    flags={"clear_ai_api_key": "clear_ai_api_key: must be true or false",
           "clear_bus_signing_key": "clear_bus_signing_key: must be true or false"},
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


def ai_in_use() -> bool:
    """A model may be called: Use AI is on and one is set up (§17.10)."""
    return bool(get("ai_on")) and ai_configured()


def quick_on() -> bool:
    return bool(get("quick_on"))


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
    return bool(get("ai_on")) and ai_provider() in PROVIDERS and is_external_host(ai_host())


# ---------- API payload & writes ----------

def payload() -> dict:
    """The GET/PUT /api/admin/settings response shape. The access key itself
    is never included — only whether one is saved and its last 4 characters."""
    values = REGISTRY.all()
    key = values["ai_api_key"]
    return REGISTRY.payload(
        values,
        secrets={"ai_api_key": {"saved": bool(key), "hint": api_key_hint(key)},
                 "bus_signing_key": {"saved": bool(values["bus_signing_key"]), "hint": ""}},
        providers={k: {"label": PROVIDERS[k], "defaultUrl": DEFAULT_URLS[k]} for k in PROVIDERS})


def update(partial: dict, user: dict | None) -> list[str]:
    """Validate {current values + partial} and store the keys that changed.
    Returns the list of changed keys. A blank `ai_api_key` keeps the saved
    key; `clear_ai_api_key: true` removes it; a new provider or address with a
    saved key needs the key again. Raises SettingsError."""
    who = (user.get("id") or user.get("name")) if user else None
    return REGISTRY.update(partial, who)[1]
