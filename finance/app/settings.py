"""App settings edited on Admin → App settings (SPEC.md sections 21–23).

Stored in the app_settings table and read on every use, so a change takes effect at once without
restarting the app. The AI settings live only here (they're not app options). An older
install's leftover ollama_url / ollama_model, or the OLLAMA_URL / OLLAMA_MODEL environment (local
development), fill in the address and model once if they are still empty.

A new option: add a Setting to SETTINGS and its key to a GROUPS entry; the page shows it.
"""
from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from urllib.parse import urlparse

from .db import get_db


@dataclass(frozen=True)
class Setting:
    key: str
    label: str
    kind: str          # "url" | "text" | "bool" | "price" | "int" | "choice" | "secret"
    default: str
    help: str = ""
    choices: tuple = ()      # (value, label) pairs for "choice"


PROVIDER_CHOICES = (("ollama", "Ollama (on your network)"), ("openai", "OpenAI-compatible"),
                    ("anthropic", "Anthropic Claude"))
DEFAULT_URLS = {"ollama": "", "openai": "https://api.openai.com/v1", "anthropic": "https://api.anthropic.com"}

SETTINGS: dict[str, Setting] = {s.key: s for s in [
    Setting("ai_provider", "Provider", "choice", "ollama",
            "Ollama runs on your own network. OpenAI-compatible covers OpenAI, OpenRouter, Groq, LM Studio, vLLM and "
            "similar. Claude is Anthropic's API.", PROVIDER_CHOICES),
    Setting("ai_url", "Address", "url", "",
            "Ollama: e.g. http://192.168.1.10:11434. OpenAI-compatible: the API base, blank = https://api.openai.com/v1 "
            "(OpenRouter: https://openrouter.ai/api/v1). Claude: blank = https://api.anthropic.com."),
    Setting("ai_model", "Model", "text", "",
            "Reads PDFs (it must understand images), and does everything else unless a text model is set below. "
            "Test connection lists the models the provider offers."),
    Setting("ai_api_key", "Access key", "secret", "",
            "Needed for Claude and for OpenAI and most cloud services; not for Ollama. Stored in the app's database, "
            "never shown again or logged, and left out of database downloads."),
    Setting("ai_text_model", "Text model (optional)", "text", "",
            "A cheaper or faster model for suggesting categories and writing SQL. Blank = use the model above."),
    Setting("ai_max_output_tokens", "Longest answer (tokens)", "int", "16000",
            "The most the model may write for one PDF (Claude needs a limit; OpenAI-compatible and Ollama ignore it). "
            "Long statements need more; the app lowers it if the model allows less."),
    Setting("show_ai_usage", "Show AI token usage on debug pages", "bool", "1",
            "Tokens sent and received, time taken and (if prices are set) cost, for every AI request."),
    Setting("price_input_per_million", "Price per million input tokens", "price", "0",
            "Leave 0 for a local model (it costs nothing but time). For a paid service, its price per 1M input tokens."),
    Setting("price_output_per_million", "Price per million output tokens", "price", "0",
            "The service's price per 1M output tokens."),
    Setting("feature_utilities", "Utilities", "bool", "0",
            "Utility bills: upload them under Upload, see them under Bills. Off hides them everywhere (nothing is deleted)."),
    Setting("feature_tolls", "Tolls", "bool", "0",
            "Toll road statements (PDF): upload them and analyze cost by car, tag and trip. Off hides them everywhere (nothing is deleted)."),
]}

_SEEDED_FROM_ENV = {"ai_url": "OLLAMA_URL", "ai_model": "OLLAMA_MODEL"}

# How the page groups them (title, hint, keys).
GROUPS: list[tuple[str, str, list[str]]] = [
    ("AI", "Used to read PDFs, suggest categories and write SQL. The next request uses a change — nothing "
     "that is already running is interrupted.",
     ["ai_provider", "ai_url", "ai_model", "ai_api_key", "ai_text_model", "ai_max_output_tokens"]),
    ("AI usage on debug pages", "Every request to the model is recorded with the upload it belongs to, and added to "
     "the running total on Admin → AI usage. The prices are used for the cost on both.",
     ["show_ai_usage", "price_input_per_million", "price_output_per_million"]),
    ("Features", "Parts of the app that not everyone needs.", ["feature_utilities", "feature_tolls"]),
]


def seed_from_addon_options() -> None:
    """Fill the AI address / model from the environment if the table has no value yet."""
    with get_db() as conn:
        for key, env in _SEEDED_FROM_ENV.items():
            value = os.environ.get(env, "").strip()
            if value:
                conn.execute("INSERT OR IGNORE INTO app_settings (key, value, updated_by) VALUES (?, ?, 'add-on options')",
                             (key, value))


def _stored(key: str) -> str | None:
    try:
        with get_db() as conn:
            row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    except Exception:
        return None
    return row[0] if row is not None else None


def get(key: str) -> str:
    value = _stored(key)
    if value is not None:
        return value
    env = _SEEDED_FROM_ENV.get(key)
    return os.environ.get(env, "") if env else SETTINGS[key].default


def get_bool(key: str) -> bool:
    return get(key) == "1"


def get_price(key: str) -> float:
    try:
        return max(0.0, float(get(key) or 0))
    except ValueError:
        return 0.0


def get_int(key: str) -> int:
    try:
        return max(0, int(get(key) or 0))
    except ValueError:
        return int(SETTINGS[key].default or 0)


def ai_provider() -> str:
    value = get("ai_provider")
    return value if value in DEFAULT_URLS else "ollama"


def ai_url() -> str:
    """The address, or the provider's standard one when left blank."""
    return (get("ai_url").strip() or DEFAULT_URLS[ai_provider()]).rstrip("/")


def ai_model() -> str:
    return get("ai_model").strip()


def ai_text_model() -> str:
    return get("ai_text_model").strip()


def has_api_key() -> bool:
    return bool(get("ai_api_key"))


def api_key_hint() -> str:
    """'…abcd' for the page: enough to recognise a key, never the key."""
    key = get("ai_api_key")
    return f"…{key[-4:]}" if len(key) >= 12 else ("saved" if key else "")


def ai_configured() -> bool:
    if not (ai_url() and ai_model()):
        return False
    return ai_provider() != "anthropic" or has_api_key()


def feature(name: str) -> bool:
    """Optional parts of the app: "utilities" or "tolls"."""
    return get_bool(f"feature_{name}")


def ai_host() -> str:
    return urlparse(ai_url()).hostname or ""


def ai_is_external() -> bool:
    """True when the AI address is outside the home network (a public host), so uploaded PDFs
    leave it. Private, loopback and link-local addresses, localhost, single-label names and
    .local / .lan / .home / .internal / .localdomain / .home.arpa (and the reserved .localhost /
    .test / .invalid) names count as the home network."""
    host = ai_host().lower().strip("[]")
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


def clean(key: str, raw: str) -> tuple[str | None, str | None]:
    """(value to store, error). Values are checked the same way whatever the form sent."""
    s = SETTINGS[key]
    raw = (raw or "").strip()
    if s.kind == "bool":
        return ("1" if raw in ("1", "on", "true") else "0"), None
    if s.kind == "price":
        try:
            value = float(raw or 0)
        except ValueError:
            return None, f"{s.label} must be a number."
        if value < 0 or value != value or value == float("inf"):
            return None, f"{s.label} can't be negative."
        return f"{value:g}", None
    if s.kind == "int":
        try:
            value = int(raw or s.default)
        except ValueError:
            return None, f"{s.label} must be a whole number."
        if not 256 <= value <= 200000:
            return None, f"{s.label} must be between 256 and 200000."
        return str(value), None
    if s.kind == "choice":
        if raw not in {v for v, _ in s.choices}:
            return None, f"Choose a {s.label.lower()}."
        return raw, None
    if s.kind == "url":
        if raw and not raw.lower().startswith(("http://", "https://")):
            return None, f"{s.label} must start with http:// or https://"
        return raw.rstrip("/")[:300], None
    if s.kind == "secret":
        return raw[:500], None
    return raw[:200], None


def save(values: dict[str, str], user_id: str) -> list[str]:
    """Store every known key present in `values` (a bool that is missing means unticked). A secret
    left blank keeps the saved one; values["clear_<key>"] == "1" removes it. Returns errors
    (nothing is saved if there are any)."""
    cleaned, errors = {}, []
    for key, s in SETTINGS.items():
        if s.kind == "secret":
            if values.get(f"clear_{key}") == "1":
                cleaned[key] = ""
            elif (values.get(key) or "").strip():
                cleaned[key], _ = clean(key, values[key])
            continue
        if key in values or s.kind == "bool":
            value, err = clean(key, values.get(key, ""))
            if err:
                errors.append(err)
            else:
                cleaned[key] = value
    provider = cleaned.get("ai_provider", ai_provider())
    url = cleaned.get("ai_url", get("ai_url"))
    if provider == "ollama" and "ai_url" in cleaned and not url:
        errors.append("Ollama needs its address (e.g. http://192.168.1.10:11434).")
    if errors:
        return errors
    with get_db() as conn:
        for key, value in cleaned.items():
            conn.execute(
                "INSERT INTO app_settings (key, value, updated_at, updated_by) VALUES (?, ?, datetime('now'), ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at, "
                "updated_by = excluded.updated_by", (key, value, user_id))
    return []


def all_values() -> dict[str, str]:
    """For the page. The access key itself is never included."""
    return {key: ("" if s.kind == "secret" else get(key)) for key, s in SETTINGS.items()}


def meta() -> dict[str, dict]:
    """{key: {"updated_at", "updated_by"}} for the keys stored in the table."""
    with get_db() as conn:
        rows = conn.execute("SELECT key, updated_at, updated_by FROM app_settings").fetchall()
    return {r["key"]: {"updated_at": r["updated_at"], "updated_by": r["updated_by"]} for r in rows}
