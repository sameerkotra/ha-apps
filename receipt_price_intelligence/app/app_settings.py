"""App settings (Admin → App settings).

Everything an administrator can change lives in the ``app_settings`` table of the app's own database
(one row per changed setting: ``key``, the value as JSON, when and by whom), not in the app's
Configuration tab, where only ``admin_users`` is left. A setting without a row uses its default.

Values are validated as a whole (``AppSettings``) before anything is written, read through a small
cache that every write clears, and apply straight away: the rest of the app reads them through
``get_settings()`` each time it needs one (see ``app.config``).

Secrets (API keys, the mail password) are write-only: the page is told whether one is set, never what
it is.

The table is reached with plain ``sqlite3`` so the settings can be read before the database layer is
set up (the database layer itself reads ``log_level``).
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

_URL = re.compile(r"^https?://[^\s/]+\S*$", re.I)
_NOTIFY = re.compile(r"^notify\.[a-z0-9_]+$")
_TODO = re.compile(r"^todo\.[a-z0-9_]+$")
_TRACKER = re.compile(r"^(person|device_tracker)\.[a-z0-9_]+$")
_FIELD_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,63}$")
_FOLDER = re.compile(r"^/(share|media)(/[^\x00]*)?$")


def _url(v: str, what: str = "an address") -> str:
    v = (v or "").strip().rstrip("/")
    if v and not _URL.match(v):
        raise ValueError(f"must be {what} starting with http:// or https://")
    return v


def _pattern(v: str, rx: re.Pattern, example: str) -> str:
    v = (v or "").strip()
    if v and not rx.match(v):
        raise ValueError(f"must look like {example}")
    return v


class AppSettings(BaseModel):
    """Validation for every App setting (unknown keys are rejected)."""

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    # Model (reading receipts)
    model_url: str = ""
    model_name: str = ""
    model_api_format: Literal["ollama", "openai_compatible"] = "ollama"
    model_api_key: str = ""
    model_timeout_seconds: int = Field(300, ge=10, le=3600)
    model_temperature: float = Field(0.0, ge=0, le=2)
    model_num_ctx: int = Field(16384, ge=0, le=262144)
    model_max_output_tokens: int = Field(8192, ge=256, le=65536)
    model_think: bool = False
    extraction_concurrency: int = Field(1, ge=1, le=8)

    # Receipts
    default_currency: str = "USD"
    receipt_date_order: Literal["auto", "MDY", "DMY"] = "auto"
    receipt_archive_path: str = ""
    draft_retention_hours: int = Field(72, ge=1, le=8760)

    # Importing receipts
    import_folder: str = ""
    import_home: str = Field("", max_length=255)
    imap_host: str = Field("", max_length=255)
    imap_port: int = Field(993, ge=1, le=65535)
    imap_user: str = Field("", max_length=255)
    imap_password: str = Field("", max_length=1024)
    imap_folder: str = Field("INBOX", max_length=255)
    imap_ssl: bool = True

    # Maps and trips
    unit_system: Literal["imperial", "metric"] = "imperial"
    geocoder_url: str = "https://nominatim.openstreetmap.org"
    routing_url: str = "https://router.project-osrm.org"
    overpass_url: str = "https://overpass-api.de/api/interpreter"
    map_contact_email: str = Field("", max_length=255)
    trip_plan_keep_hours: int = Field(12, ge=0, le=72)

    # Web search
    web_search_provider: Literal["custom", "tavily", "parallel", "searxng"] = "custom"
    web_search_url: str = ""
    web_search_api_key: str = Field("", max_length=1024)
    web_search_token: str = Field("", max_length=1024)
    web_search_timeout_seconds: int = Field(120, ge=5, le=600)
    web_search_concurrency: int = Field(3, ge=1, le=5)
    web_search_cache_days: int = Field(3, ge=0, le=30)
    web_search_min_interval_seconds: int = Field(4, ge=0, le=60)
    web_query_field: str = "query"
    web_url_field: str = "url"
    web_auto_hours: bool = False
    web_deals_days: int = Field(3, ge=0, le=60)
    web_price_freshness_hours: int = Field(6, ge=1, le=72)
    web_prices_days: int = Field(0, ge=0, le=60)

    # Best prices
    recommendation_window_days: int = Field(90, ge=7, le=365)
    minimum_savings_percent: float = Field(5.0, ge=0, le=100)
    alert_min_saving_percent: float = Field(15.0, ge=0, le=100)
    recommendation_run_hour: int = Field(3, ge=0, le=23)

    # Home Assistant and notifications
    ha_sensors: bool = True
    alerts_enabled: bool = True
    alert_notify_service: str = ""
    restock_notify: bool = True
    restock_auto_add: bool = False
    price_drop_percent: int = Field(10, ge=0, le=90)
    overcharge_notify: bool = True
    shopping_list_todo_entity: str = ""
    shopping_tracker_entity: str = ""
    shopping_notify_service: str = ""
    shopping_nearby_meters: int = Field(250, ge=0, le=3000)

    # Advanced
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @field_validator("model_url", "web_search_url", "geocoder_url", "routing_url", "overpass_url")
    @classmethod
    def _urls(cls, v: str) -> str:
        return _url(v)

    @field_validator("model_name", "imap_host", "imap_user", "imap_folder", "model_api_key", "web_search_api_key",
                     "web_search_token")
    @classmethod
    def _trimmed(cls, v: str) -> str:
        v = (v or "").strip()
        if any(c in v for c in "\r\n\x00"):
            raise ValueError("must be on one line")
        return v

    @field_validator("model_name")
    @classmethod
    def _model_name(cls, v: str) -> str:
        if len(v) > 255:
            raise ValueError("is too long")
        return v

    @field_validator("default_currency")
    @classmethod
    def _currency(cls, v: str) -> str:
        v = (v or "").strip().upper()
        if not re.fullmatch(r"[A-Z]{3}", v):
            raise ValueError("must be a 3-letter currency code such as USD, EUR or GBP")
        return v

    @field_validator("receipt_archive_path", "import_folder")
    @classmethod
    def _folder(cls, v: str) -> str:
        v = (v or "").strip().rstrip("/")
        if v and (not _FOLDER.match(v) or "/../" in v + "/" or "/./" in v + "/"):
            raise ValueError("must be a folder under /share or /media, for example /share/receipts")
        return v

    @field_validator("map_contact_email")
    @classmethod
    def _email(cls, v: str) -> str:
        v = (v or "").strip()
        if v and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", v):
            raise ValueError("must be an email address")
        return v

    @field_validator("web_query_field", "web_url_field")
    @classmethod
    def _field_name(cls, v: str) -> str:
        v = (v or "").strip()
        if not _FIELD_NAME.match(v):
            raise ValueError("must be a field name such as query, q or url")
        return v

    @field_validator("alert_notify_service", "shopping_notify_service")
    @classmethod
    def _notify(cls, v: str) -> str:
        return _pattern(v, _NOTIFY, "notify.mobile_app_your_phone")

    @field_validator("shopping_list_todo_entity")
    @classmethod
    def _todo(cls, v: str) -> str:
        return _pattern(v, _TODO, "todo.shopping_list")

    @field_validator("shopping_tracker_entity")
    @classmethod
    def _tracker(cls, v: str) -> str:
        return _pattern(v, _TRACKER, "person.someone or device_tracker.a_phone")

    @field_validator("import_home")
    @classmethod
    def _home(cls, v: str) -> str:
        return (v or "").strip()


DEFAULTS: dict[str, Any] = AppSettings().model_dump()
SECRETS = frozenset({"model_api_key", "imap_password", "web_search_api_key", "web_search_token"})

# The page is drawn from this: groups in order, each field's label, help and kind. "choices" fields are
# a drop-down, "secret" fields are write-only. Every setting applies straight away (no restart).
GROUPS: list[tuple[str, str, str]] = [
    ("model", "Reading receipts", "The vision model that reads receipt photos and PDFs. Nothing can be scanned until it is set."),
    ("receipts", "Receipts", ""),
    ("import", "Importing receipts", "Receipts can also arrive from a folder or a mailbox. Both are checked in the background."),
    ("maps", "Maps and trips", "Address search and driving routes, for the trip planner and stores near home."),
    ("web", "Web search", "Store hours, online prices and deals are looked up on the web, when a search service is set."),
    ("prices", "Best prices", ""),
    ("ha", "Home Assistant and notifications",
     "Defaults for everyone. Each person's choices on the Notify page take precedence once saved there."),
    ("advanced", "Advanced", ""),
]

_CHOICES = {
    "model_api_format": [("ollama", "Ollama"), ("openai_compatible", "OpenAI-compatible (vLLM, LM Studio, llama.cpp…)")],
    "receipt_date_order": [("auto", "Automatic (from the currency)"), ("MDY", "Month first (MDY)"), ("DMY", "Day first (DMY)")],
    "unit_system": [("imperial", "Miles, gallons, mpg"), ("metric", "Kilometres, litres, L/100 km")],
    "web_search_provider": [("custom", "Your own search server"), ("tavily", "Tavily"), ("parallel", "Parallel"),
                            ("searxng", "SearXNG")],
    "log_level": [("DEBUG", "Debug"), ("INFO", "Info"), ("WARNING", "Warning"), ("ERROR", "Error")],
}

# key: (group, label, help)
FIELDS: dict[str, tuple[str, str, str]] = {
    "model_url": ("model", "Model server URL",
                  "Address of your vision model server, for example http://192.0.2.10:11434 for Ollama or "
                  "http://192.0.2.10:8000/v1 for an OpenAI-compatible server."),
    "model_name": ("model", "Model name", "The vision-capable model to read receipts with."),
    "model_api_format": ("model", "Model API format", "Ollama, or an OpenAI-compatible server such as vLLM, LM Studio or llama.cpp."),
    "model_api_key": ("model", "Model API key", "Only needed if your server requires one."),
    "model_timeout_seconds": ("model", "Model timeout (seconds)",
                              "How long to wait for the model to answer. Large models on home hardware can take a few minutes."),
    "model_temperature": ("model", "Model temperature", "Keep at 0 for the most literal reading of the receipt."),
    "model_num_ctx": ("model", "Context window (Ollama)",
                      "Tokens of context to request from Ollama. Receipt photos need several thousand. 0 uses the server default."),
    "model_max_output_tokens": ("model", "Maximum reply length", "Upper limit on tokens the model may write for one receipt."),
    "model_think": ("model", "Let the model think first", "For reasoning models on Ollama. Slower, occasionally more accurate."),
    "extraction_concurrency": ("model", "Receipts read at the same time",
                               "How many receipts the model reads in parallel. Keep at 1 unless your model server has capacity to spare."),
    "default_currency": ("receipts", "Default currency", "Used when a receipt does not show one, for example USD."),
    "receipt_date_order": ("receipts", "Date order on receipts",
                           "How to read a date like 03/04/25. Automatic uses the receipt's currency (USD and CAD month "
                           "first, others day first)."),
    "receipt_archive_path": ("receipts", "Receipt archive folder",
                             "Keep every scanned receipt's original photo or PDF in this folder, for example /share/receipts "
                             "or /media/receipts. Approved receipts are filed under <year>/<date> <store> <total>; rejected "
                             "scans are removed. Empty: no copies are kept."),
    "draft_retention_hours": ("receipts", "Keep unsaved receipts (hours)",
                              "How long receipts waiting for review, and their photos, are kept before being deleted."),
    "import_folder": ("import", "Folder to import receipts from",
                      "A folder under /share or /media watched for photos and PDFs. Files become receipts to review, then "
                      "move to a processed folder. Empty: off."),
    "import_home": ("import", "Home for imported receipts",
                    "The name of the home imported receipts go to. Needed when there is more than one home."),
    "imap_host": ("import", "Mail server (IMAP)",
                  "Read emailed receipts from this server. PDF and image attachments of unread emails are imported and "
                  "the emails are marked as read. Empty: off."),
    "imap_port": ("import", "Mail server port", "Usually 993 with SSL."),
    "imap_user": ("import", "Mail user name", "The mailbox to read."),
    "imap_password": ("import", "Mail password", "For most webmail services, use an app password."),
    "imap_folder": ("import", "Mail folder", "Which folder to read, for example INBOX or Receipts."),
    "imap_ssl": ("import", "Use SSL for mail", "Leave on unless your mail server does not support it."),
    "unit_system": ("maps", "Units for the trip planner", ""),
    "geocoder_url": ("maps", "Address search server",
                     "A Nominatim server that finds map coordinates for addresses. The default is the free OpenStreetMap "
                     "one; your home's and stores' addresses are sent to it."),
    "routing_url": ("maps", "Routing server",
                    "An OSRM server for driving distances and times. The default is the free public demo server; "
                    "coordinates of your home and stores are sent to it."),
    "overpass_url": ("maps", "Nearby store search server",
                     "An Overpass (OpenStreetMap) server used by Find stores near home. Your home's location, rounded to "
                     "about a kilometre, is sent to it. Empty turns the search off."),
    "map_contact_email": ("maps", "Contact email for map requests",
                          "Optional. OpenStreetMap's usage policy asks apps to identify themselves, so this is sent with "
                          "address searches."),
    "trip_plan_keep_hours": ("maps", "Keep trip plans (hours)",
                             "A trip plan is shown again instead of being worked out anew while nothing it uses has "
                             "changed. 0 turns it off."),
    "web_search_provider": ("web", "Web search provider",
                            "Your own search server (POST /search and POST /fetch), Tavily or Parallel (an API key), or "
                            "your own SearXNG (json must be enabled in its settings)."),
    "web_search_url": ("web", "Search server address",
                       "For your own server or SearXNG, for example http://192.0.2.10:8080 (not localhost: it must be "
                       "reachable from Home Assistant). Only store names, towns and product names are sent."),
    "web_search_api_key": ("web", "Web search API key",
                           "For Tavily or Parallel, your API key. For SearXNG, only if your instance needs a bearer token."),
    "web_search_token": ("web", "Search server token", "Only if your own search server needs a bearer token."),
    "web_search_timeout_seconds": ("web", "Search server timeout (seconds)",
                                   "How long to wait for one answer from the search server."),
    "web_search_concurrency": ("web", "Search requests at once",
                               "How many requests a lookup can have in flight at the same time."),
    "web_search_cache_days": ("web", "Keep search results (days)",
                              "Search results are reused for this many days instead of searching again. 0 turns it off."),
    "web_search_min_interval_seconds": ("web", "Seconds between searches",
                                        "Searches sent to SearXNG or your own server are at least this far apart, since "
                                        "search engines block bursts."),
    "web_query_field": ("web", "Search request field name", "The field that holds the search words in POST /search (usually query or q)."),
    "web_url_field": ("web", "Fetch request field name", "The field that holds the web address in POST /fetch (usually url)."),
    "web_auto_hours": ("web", "Apply store hours found online automatically",
                       "When on, confident hours found for a store that has none are used straight away. When off they "
                       "are only suggested."),
    "web_deals_days": ("web", "Look for deals every (days)", "0 means only when someone asks."),
    "web_price_freshness_hours": ("web", "Reuse an online price for (hours)",
                                  "A price checked online within this many hours is reused instead of checked again."),
    "web_prices_days": ("web", "Check online prices every (days)",
                        "How often the online price of your most bought items is checked. This asks the model many "
                        "questions, so it is off (0) by default."),
    "recommendation_window_days": ("prices", "Price history window (days)", "How far back the daily best-price check looks."),
    "minimum_savings_percent": ("prices", "Minimum saving to suggest (%)",
                                "A cheaper store is only suggested if it saves at least this much."),
    "alert_min_saving_percent": ("prices", "Smallest saving worth an alert (%)",
                                 "A cheaper store or a price drop only raises an alert above this percentage."),
    "recommendation_run_hour": ("prices", "Daily check hour",
                                "Hour of the day (0-23, Home Assistant's time zone) the best-price check runs."),
    "ha_sensors": ("ha", "Publish sensors to Home Assistant",
                   "Sensors such as spend this month, possible savings and items due, updated after each receipt and "
                   "every day."),
    "alerts_enabled": ("ha", "Raise alerts",
                       "Alerts for price targets, big price drops, much cheaper stores and items due for restock. Each is "
                       "also a Home Assistant event (receipt_price_intelligence_alert) for automations."),
    "alert_notify_service": ("ha", "Notification service",
                             "Where alerts are sent, for example notify.mobile_app_your_phone. Empty: Home Assistant "
                             "persistent notifications."),
    "restock_notify": ("ha", "Notify what is due to restock", "Once a day, the items you are due to buy again."),
    "restock_auto_add": ("ha", "Add due items to the shopping list", "Once a day, by themselves."),
    "price_drop_percent": ("ha", "Price drop alert (%)",
                           "Notify when a shopping list item is at least this much cheaper than usual. 0 turns it off."),
    "overcharge_notify": ("ha", "Notify possible overcharges",
                          "When a receipt is approved, lines that cost noticeably more than the store posted that week."),
    "shopping_list_todo_entity": ("ha", "Shopping list to-do entity",
                                  "A Home Assistant to-do list kept in step with the shopping list, for example "
                                  "todo.shopping_list. Empty: not synced."),
    "shopping_tracker_entity": ("ha", "Track this person or phone",
                                "A person. or device_tracker. entity checked every minute; at a store where list items "
                                "are cheapest, a notification is sent. Empty: off."),
    "shopping_notify_service": ("ha", "Notify service for “cheapest here”",
                                "For example notify.mobile_app_your_phone. Empty uses the notification service above."),
    "shopping_nearby_meters": ("ha", "Distance that counts as at a store (meters)", "0 turns “cheapest here” off."),
    "log_level": ("advanced", "Log level", "Use Debug only while troubleshooting."),
}

assert set(FIELDS) == set(DEFAULTS), set(FIELDS) ^ set(DEFAULTS)

_RANGES = {}
for _key, _info in AppSettings.model_fields.items():
    for _m in _info.metadata:
        for _attr, _name in (("ge", "min"), ("le", "max")):
            if getattr(_m, _attr, None) is not None:
                _RANGES.setdefault(_key, {})[_name] = getattr(_m, _attr)


def _kind(key: str) -> str:
    if key in SECRETS:
        return "secret"
    if key in _CHOICES:
        return "choice"
    value = DEFAULTS[key]
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    return "text"


def meta() -> dict[str, dict[str, Any]]:
    out = {}
    for key, (group, label, help_text) in FIELDS.items():
        m: dict[str, Any] = {"group": group, "label": label, "help": help_text, "kind": _kind(key), "restartRequired": False}
        if key in _CHOICES:
            m["choices"] = [{"value": v, "label": lbl} for v, lbl in _CHOICES[key]]
        m.update(_RANGES.get(key, {}))
        out[key] = m
    return out


# --------------------------------------------------------------------------- #
# Storage
# --------------------------------------------------------------------------- #

class SettingsError(ValueError):
    """A bad settings update; the message is shown to the admin as it is (422)."""


_db_path: str | None = None
_cache: dict[str, Any] | None = None
_generation = 0
_lock = threading.Lock()
_write_lock = threading.Lock()


def configure(db_path: str) -> None:
    """Where the database is (set once by ``app.config`` from DATABASE_PATH)."""
    global _db_path
    if db_path != _db_path:
        _db_path = db_path
        invalidate()


def _connect() -> sqlite3.Connection:
    import os
    if not _db_path:
        from app.config import get_settings   # sets the database path (circular at import time only)
        get_settings()
    if not _db_path:
        raise RuntimeError("The database path is not known")
    folder = os.path.dirname(_db_path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    conn = sqlite3.connect(_db_path, timeout=10)
    conn.execute("PRAGMA busy_timeout=5000")
    ensure_table(conn)
    return conn


def ensure_table(conn: sqlite3.Connection) -> None:
    """The table is shared with settings the app saves itself (the Notifications page's ``notifications`` row), which
    is why only the keys in DEFAULTS are read or written here."""
    conn.execute("CREATE TABLE IF NOT EXISTS app_settings (key VARCHAR(80) PRIMARY KEY, value TEXT NOT NULL, "
                 "updated_at DATETIME NOT NULL, updated_by VARCHAR(255))")
    columns = {row[1] for row in conn.execute("PRAGMA table_info(app_settings)")}
    if "updated_by" not in columns:   # the table from before 1.0.0
        conn.execute("ALTER TABLE app_settings ADD COLUMN updated_by VARCHAR(255)")
        conn.commit()


def _ours(rows: list[tuple]) -> list[tuple]:
    return [r for r in rows if r[0] in DEFAULTS]


def _validate_one(key: str, value: Any) -> Any:
    return getattr(AppSettings.model_validate({**DEFAULTS, key: value}), key)


def _stored(conn: sqlite3.Connection) -> dict[str, Any]:
    """Defaults overlaid with the valid stored rows (a bad row is ignored, with a warning)."""
    values = dict(DEFAULTS)
    for key, raw in conn.execute("SELECT key, value FROM app_settings").fetchall():
        if key not in DEFAULTS:
            continue
        try:
            values[key] = _validate_one(key, json.loads(raw))
        except (json.JSONDecodeError, ValidationError):
            import logging
            logging.getLogger("app_settings").warning("Ignoring an invalid stored %s setting; using %r",
                                                      key, "(secret)" if key in SECRETS else values[key])
    return values


def invalidate() -> None:
    global _cache, _generation
    with _lock:
        _cache = None
        _generation += 1


def values() -> dict[str, Any]:
    """The effective settings (a copy)."""
    global _cache
    with _lock:
        if _cache is not None:
            return dict(_cache)
        generation = _generation
    try:
        conn = _connect()
    except (RuntimeError, sqlite3.Error, OSError):
        return dict(DEFAULTS)
    try:
        current = _stored(conn)
    except sqlite3.Error:
        return dict(DEFAULTS)
    finally:
        conn.close()
    with _lock:
        if generation == _generation:
            _cache = current
    return dict(current)


def _error_text(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors():
        key = str(err["loc"][0]) if err.get("loc") else ""
        msg = str(err.get("msg", "invalid value")).removeprefix("Value error, ")
        label = FIELDS.get(key, ("", key, ""))[1]
        parts.append(f"{label}: {msg}" if label else msg)
    return "; ".join(parts) or "Invalid settings"


_hooks: list = []


def on_change(fn) -> None:
    """Call ``fn(changed_keys, new_values)`` after every successful update."""
    _hooks.append(fn)


def update(partial: dict, user: str | None) -> dict[str, Any]:
    """Validate the merged result, write only what changed, and return the new values.

    Raises SettingsError on unknown keys or bad values; nothing is written then."""
    if not isinstance(partial, dict):
        raise SettingsError("Send the settings as a JSON object")
    unknown = sorted(k for k in partial if k not in DEFAULTS)
    if unknown:
        raise SettingsError(f"Unknown setting{'s' if len(unknown) > 1 else ''}: {', '.join(unknown)}")
    with _write_lock:
        conn = _connect()
        try:
            current = _stored(conn)
            try:
                merged = AppSettings.model_validate({**current, **partial}).model_dump()
            except ValidationError as e:
                raise SettingsError(_error_text(e)) from None
            changed = [k for k in DEFAULTS if merged[k] != current[k]]
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            for key in changed:
                conn.execute("INSERT INTO app_settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, ?) "
                             "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at, "
                             "updated_by = excluded.updated_by", (key, json.dumps(merged[key]), now, user))
            conn.commit()
        finally:
            conn.close()
        invalidate()
    if changed:
        import logging
        logging.getLogger("app_settings").info(
            "%s changed the app settings: %s", user or "Someone",
            ", ".join(k if k in SECRETS else f"{k}={merged[k]!r}" for k in changed))
        for fn in list(_hooks):
            try:
                fn(changed, merged)
            except Exception:  # noqa: BLE001 - a hook must not undo a saved change
                logging.getLogger("app_settings").exception("Settings hook failed")
    return merged


def public(vals: dict[str, Any]) -> dict[str, Any]:
    """``vals`` with secrets blanked (whether each is set is in ``secrets_set``)."""
    return {k: ("" if k in SECRETS else v) for k, v in vals.items()}


def payload() -> dict[str, Any]:
    vals = values()
    return {"values": public(vals), "defaults": public(DEFAULTS), "meta": meta(),
            "groups": [{"id": g, "label": label, "help": help_text} for g, label, help_text in GROUPS],
            "secretsSet": {k: bool(vals[k]) for k in sorted(SECRETS)}}


# --------------------------------------------------------------------------- #
# Backups: a restored database keeps the current settings unless it has its own
# --------------------------------------------------------------------------- #

def export_rows(path: str) -> list[tuple]:
    """The stored rows of the database at ``path`` ([] if it has none)."""
    try:
        conn = sqlite3.connect(path, timeout=10)
        try:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(app_settings)")}
            by = "updated_by" if "updated_by" in columns else "NULL"
            return _ours(conn.execute(f"SELECT key, value, updated_at, {by} FROM app_settings").fetchall())
        finally:
            conn.close()
    except sqlite3.Error:
        return []


def adopt_rows(path: str, rows: list[tuple]) -> bool:
    """Write ``rows`` into the database at ``path`` if it has no settings of its own. True if written."""
    conn = sqlite3.connect(path, timeout=10)
    try:
        ensure_table(conn)
        keys = [r[0] for r in conn.execute("SELECT key FROM app_settings").fetchall()]
        if any(k in DEFAULTS for k in keys) or not rows:
            return False
        conn.executemany("INSERT OR IGNORE INTO app_settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, ?)", rows)
        conn.commit()
        return True
    finally:
        conn.close()
        invalidate()
