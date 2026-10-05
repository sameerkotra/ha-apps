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

import contextlib
import logging
import re
import sqlite3
import threading
from typing import Any

from .common import settings_core
from .common.settings_core import Group, Setting, SettingsError  # noqa: F401  (SettingsError: the routes' name)

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


def _urls(v: str) -> str:
    return _url(v)


def _trimmed(v: str) -> str:
    v = (v or "").strip()
    if any(c in v for c in "\r\n\x00"):
        raise ValueError("must be on one line")
    return v


def _model_name(v: str) -> str:
    if len(v) > 255:
        raise ValueError("is too long")
    return v


def _currency(v: str) -> str:
    v = (v or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", v):
        raise ValueError("must be a 3-letter currency code such as USD, EUR or GBP")
    return v


def _folder(v: str) -> str:
    v = (v or "").strip().rstrip("/")
    if v and (not _FOLDER.match(v) or "/../" in v + "/" or "/./" in v + "/"):
        raise ValueError("must be a folder under /share or /media, for example /share/receipts")
    return v


def _email(v: str) -> str:
    v = (v or "").strip()
    if v and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", v):
        raise ValueError("must be an email address")
    return v


def _field_name(v: str) -> str:
    v = (v or "").strip()
    if not _FIELD_NAME.match(v):
        raise ValueError("must be a field name such as query, q or url")
    return v


def _notify(v: str) -> str:
    return _pattern(v, _NOTIFY, "notify.mobile_app_your_phone")


def _todo(v: str) -> str:
    return _pattern(v, _TODO, "todo.shopping_list")


def _tracker(v: str) -> str:
    return _pattern(v, _TRACKER, "person.someone or device_tracker.a_phone")


def _home(v: str) -> str:
    return (v or "").strip()


# key: (default, {Setting keywords}) — in the order the page shows them, grouped as in GROUPS below.
_SPEC: dict[str, tuple[Any, dict]] = {
    # Model (reading receipts)
    "model_url": ("", {"kind": "text", "validators": [_urls]}),
    "model_name": ("", {"validators": [_trimmed, _model_name]}),
    "model_api_format": ("ollama", {}),
    "model_api_key": ("", {"validators": [_trimmed]}),
    "model_timeout_seconds": (300, {"min": 10, "max": 3600}),
    "model_temperature": (0.0, {"min": 0, "max": 2}),
    "model_num_ctx": (16384, {"min": 0, "max": 262144}),
    "model_max_output_tokens": (8192, {"min": 256, "max": 65536}),
    "model_think": (False, {}),
    "extraction_concurrency": (1, {"min": 1, "max": 8}),
    # Receipts
    "default_currency": ("USD", {"validators": [_currency]}),
    "receipt_date_order": ("auto", {}),
    "receipt_archive_path": ("", {"validators": [_folder]}),
    "draft_retention_hours": (72, {"min": 1, "max": 8760}),
    # Importing receipts
    "import_folder": ("", {"validators": [_folder]}),
    "import_home": ("", {"max_length": 255, "validators": [_home]}),
    "imap_host": ("", {"max_length": 255, "validators": [_trimmed]}),
    "imap_port": (993, {"min": 1, "max": 65535}),
    "imap_user": ("", {"max_length": 255, "validators": [_trimmed]}),
    "imap_password": ("", {"max_length": 1024}),
    "imap_folder": ("INBOX", {"max_length": 255, "validators": [_trimmed]}),
    "imap_ssl": (True, {}),
    # Maps and trips
    "unit_system": ("imperial", {}),
    "geocoder_url": ("https://nominatim.openstreetmap.org", {"kind": "text", "validators": [_urls]}),
    "routing_url": ("https://router.project-osrm.org", {"kind": "text", "validators": [_urls]}),
    "overpass_url": ("https://overpass-api.de/api/interpreter", {"kind": "text", "validators": [_urls]}),
    "map_contact_email": ("", {"max_length": 255, "validators": [_email]}),
    "trip_plan_keep_hours": (12, {"min": 0, "max": 72}),
    # Web search
    "web_search_provider": ("custom", {}),
    "web_search_url": ("", {"kind": "text", "validators": [_urls]}),
    "web_search_api_key": ("", {"max_length": 1024, "validators": [_trimmed]}),
    "web_search_token": ("", {"max_length": 1024, "validators": [_trimmed]}),
    "web_search_timeout_seconds": (120, {"min": 5, "max": 600}),
    "web_search_concurrency": (3, {"min": 1, "max": 5}),
    "web_search_cache_days": (3, {"min": 0, "max": 30}),
    "web_search_min_interval_seconds": (4, {"min": 0, "max": 60}),
    "web_query_field": ("query", {"validators": [_field_name]}),
    "web_url_field": ("url", {"validators": [_field_name]}),
    "web_auto_hours": (False, {}),
    "web_deals_days": (3, {"min": 0, "max": 60}),
    "web_price_freshness_hours": (6, {"min": 1, "max": 72}),
    "web_prices_days": (0, {"min": 0, "max": 60}),
    # Best prices
    "recommendation_window_days": (90, {"min": 7, "max": 365}),
    "minimum_savings_percent": (5.0, {"min": 0, "max": 100}),
    "alert_min_saving_percent": (15.0, {"min": 0, "max": 100}),
    "recommendation_run_hour": (3, {"min": 0, "max": 23}),
    # Home Assistant and notifications
    "ha_sensors": (True, {}),
    "alerts_enabled": (True, {}),
    "alert_notify_service": ("", {"validators": [_notify]}),
    "restock_notify": (True, {}),
    "restock_auto_add": (False, {}),
    "price_drop_percent": (10, {"min": 0, "max": 90}),
    "overcharge_notify": (True, {}),
    "shopping_list_todo_entity": ("", {"validators": [_todo]}),
    "shopping_tracker_entity": ("", {"validators": [_tracker]}),
    "shopping_notify_service": ("", {"validators": [_notify]}),
    "shopping_nearby_meters": (250, {"min": 0, "max": 3000}),
    # Advanced
    "log_level": ("INFO", {}),
}

_SECRET_KEYS = frozenset({"model_api_key", "imap_password", "web_search_api_key", "web_search_token"})

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

assert set(FIELDS) == set(_SPEC), set(FIELDS) ^ set(_SPEC)

SETTINGS = [Setting(key, default, FIELDS[key][1], help=FIELDS[key][2], group=FIELDS[key][0], secret=key in _SECRET_KEYS,
                    choices=_CHOICES.get(key), **kw)
            for key, (default, kw) in _SPEC.items()]


# --------------------------------------------------------------------------- #
# Storage
# --------------------------------------------------------------------------- #

_db_path: str | None = None
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


@contextlib.contextmanager
def _connection():
    conn = _connect()
    try:
        yield conn
    finally:
        conn.close()


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


def _log(user, changed, before, after) -> None:
    logging.getLogger("app_settings").info(
        "%s changed the app settings: %s", user or "Someone",
        ", ".join(k if k in SECRETS else f"{k}={after[k]!r}" for k in changed))


REGISTRY = settings_core.Registry(
    SETTINGS, groups=[Group(g, label, help_text) for g, label, help_text in GROUPS], connect=_connection,
    model_config={"strict": True, "allow_inf_nan": False}, format_error=settings_core.by_label(
        {k: v[1] for k, v in FIELDS.items()}, empty="Invalid settings"),
    not_object_message="Send the settings as a JSON object", write_lock=_write_lock, log=_log,
    fallback_on_error=True, logger=logging.getLogger("app_settings"))

AppSettings = REGISTRY.model
DEFAULTS: dict[str, Any] = REGISTRY.defaults
SECRETS = REGISTRY.secrets
_hooks = REGISTRY.on_change


def meta() -> dict[str, dict[str, Any]]:
    return REGISTRY.meta()


def invalidate() -> None:
    REGISTRY.invalidate()


def values() -> dict[str, Any]:
    """The effective settings (a copy)."""
    return REGISTRY.all()


def on_change(fn) -> None:
    """Call ``fn(changed_keys, new_values)`` after every successful update."""
    _hooks.append(fn)


def update(partial: dict, user: str | None) -> dict[str, Any]:
    """Validate the merged result, write only what changed, and return the new values.

    Raises SettingsError on unknown keys or bad values; nothing is written then."""
    return REGISTRY.update(partial, user)[0]


def public(vals: dict[str, Any]) -> dict[str, Any]:
    """``vals`` with secrets blanked (whether each is set is in ``secrets_set``)."""
    return REGISTRY.public(vals)


def payload() -> dict[str, Any]:
    return REGISTRY.payload()


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
