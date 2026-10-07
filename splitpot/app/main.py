import asyncio
import base64
import json
import logging
import os
import time
import re
import sqlite3
import threading
import urllib.error
import urllib.request
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Literal, Optional

import tempfile

from fastapi import APIRouter, BackgroundTasks, Body, Depends, FastAPI, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError, field_validator, model_validator

from . import app_messages, config
from .common import auth_core, backup_core, csv_export, db_core, ha_time, sensor_publisher, settings_core, web_security
from .common import ha_client as ha_core, ha_notify, ha_people, people_admin
from .common import housekeeping as jobs_core
from .common import whoami as whoami_core

jobs_core.setup_logging()
logger = logging.getLogger("splitpot")

# ---------- storage ----------
DATA_DIR = Path(os.environ.get("DATA_DIR", "./data"))
DB_FILE = DATA_DIR / "splitpot.db"
_lock = threading.Lock()  # serializes writes across requests/threads


# ---------- Home Assistant app integration ----------
# SUPERVISOR_TOKEN is injected
# automatically by the Supervisor into every app container.
HA_OPTIONS_FILE = Path("/data/options.json")


def load_ha_options() -> dict:
    """The one app option, admin_users. Sensor sync, the sync interval and
    the currency are App settings (stored in SQLite); options.json is never
    read for them, so stray keys of those names there are ignored."""
    defaults = {
        "admin_users": [],
    }
    if HA_OPTIONS_FILE.exists():
        try:
            user_opts = json.loads(HA_OPTIONS_FILE.read_text())
            for key in defaults:
                if key in user_opts:
                    defaults[key] = user_opts[key]
        except (json.JSONDecodeError, OSError):
            pass
    return defaults


HA_OPTIONS = load_ha_options()
SUPERVISOR_TOKEN = config.SUPERVISOR_TOKEN or None   # the shared HA modules read config.SUPERVISOR_TOKEN

# The `currency` App setting (an ISO 4217 code like "USD" or "EUR") drives
# both the HA sensors' unit and how amounts are written in the app and the
# activity log. It's read at the moment it's needed (get_setting), so a
# change in Admin → App settings applies without a restart.
_CURRENCY_SYMBOLS = {
    "USD": "$", "CAD": "CA$", "AUD": "A$", "NZD": "NZ$", "MXN": "MX$", "EUR": "€", "GBP": "£",
    "INR": "₹", "JPY": "¥", "CNY": "CN¥", "KRW": "₩", "CHF": "CHF ", "SEK": "SEK ", "NOK": "NOK ",
    "DKK": "DKK ", "PLN": "zł ", "BRL": "R$", "ZAR": "R ", "SGD": "S$", "HKD": "HK$", "AED": "AED ",
}


def money(amount: float, currency: Optional[str] = None) -> str:
    """An amount for the activity log, in the App settings currency."""
    code = currency or get_setting("currency")
    symbol = _CURRENCY_SYMBOLS.get(code, code + " ")
    return f"{symbol}{amount:,.2f}"

# Whether balances are pushed to Home Assistant as sensor entities is the
# `ha_sync_enabled` App setting, read live —
# see sensor_sync_enabled() below. Pushing also needs the SUPERVISOR_TOKEN
# the Supervisor injects (absent when running outside Home Assistant).


# ---------- time zone ----------
# The container's own clock is UTC (standard for Docker images); "this
# week" / "this month" on the dashboard need the household's real calendar
# instead, or an expense added Sunday night (or the last night of a month)
# could get filed under next week/month from the household's point of view.
# The real zone comes from Home Assistant itself (GET /config, fetched once
# at startup — see the lifespan below); if that can't be read, it stays UTC.
_ZONE = ha_time.Zone(logger, tz=timezone.utc)      # app/common/ha_time.py
SUPERVISOR_CORE_API = "http://supervisor/core/api"


def set_timezone(name: str) -> bool:
    """Switch week_range()/month_range() to `name` (an IANA zone like
    'Europe/Berlin'). Returns False (and keeps the current zone) if `name`
    isn't recognized."""
    return _ZONE.set(name)


def tz():
    """The household's time zone (Home Assistant's; UTC until it's read)."""
    return _ZONE.tz


def local_now() -> datetime:
    """Current time as an aware datetime in Home Assistant's time zone."""
    return _ZONE.now()


async def load_ha_timezone() -> None:
    """Home Assistant's configured time zone (GET /config), read once at startup;
    if it can't be read, the app stays on UTC."""
    await ha_time.load(_ZONE, log=logger, details=True, base_url=SUPERVISOR_CORE_API, token=SUPERVISOR_TOKEN or "")

# Home Assistant user ids / login names allowed into the Admin area (App
# settings, Users, Storage) and to delete. Empty by default (deny by
# default) rather than trying to infer real HA admin status, which would
# need extra Supervisor API scope — a static allowlist in the app options is
# enough for a household-sized, rarely-changing set of people.
ADMIN_NAMES = auth_core.admin_names(HA_OPTIONS.get("admin_users") or [])     # app/common/auth_core.py


_warned_display_name_only: set = set()


def display_name_listed(request: Request) -> bool:
    return auth_core.display_name_listed(auth_core.identity(request).display_name, ADMIN_NAMES)


def is_admin(request: Request) -> bool:
    """Admin by user id or login name only — the two identifiers Supervisor
    sets that nobody picks for themselves. Display names are deliberately
    NOT matched (the same rule as Household Todo and Calorie Tracker):
    they aren't unique or stable, so matching them could make someone an
    admin just by having an admin's name. Someone listed only by display name
    gets a one-time warning in the log naming the id to list instead."""
    ident = auth_core.identity(request)
    if auth_core.is_admin(ident.user_id, ident.username, ADMIN_NAMES):
        return True
    uid = (ident.user_id or "").strip().lower()
    username = (ident.username or "").strip().lower()
    if display_name_listed(request) and uid not in _warned_display_name_only:
        _warned_display_name_only.add(uid)
        logger.warning(
            "%r is listed in admin_users by display name only, which isn't accepted — list their "
            "user id %s%s instead, then restart the app.",
            ident.display_name, uid,
            f" or login name {username!r}" if username else "",
        )
    return False


def no_admin_yet() -> bool:
    """True while admin_users is empty (a fresh install): nobody is an
    administrator until someone is listed and the app restarted."""
    return auth_core.no_admin(ADMIN_NAMES)


def require_admin(request: Request) -> None:
    """Route dependency for anything actually admin-gated (403s a non-admin)
    — as opposed to just branching what the frontend SHOWS someone, which
    doesn't block the request on its own."""
    auth_core.require_admin_flag(is_admin(request), "Only designated admins can do this. See the admin_users option "
                                                     "in the app's Configuration tab.")


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    return s or "user"


BALANCE_SENSOR_PREFIX = "sensor.splitpot_balance_"


def ha_connected() -> bool:
    """True when the Supervisor gave us a token for the Core API proxy."""
    return bool(SUPERVISOR_TOKEN)


def sensor_sync_enabled() -> bool:
    """The live `ha_sync_enabled` App setting AND a Home Assistant connection."""
    return ha_connected() and bool(get_setting("ha_sync_enabled"))


def _ha_call(method: str, path: str, body=None, timeout: float = 5):
    """One Core API request via the Supervisor's proxy: (status, parsed
    JSON or None). Raises urllib.error.URLError/OSError on failure (an HTTP
    error status arrives as urllib.error.HTTPError, a URLError subclass).
    Blocking — never call it while holding _lock or an open DB connection."""
    req = urllib.request.Request(
        f"http://supervisor/core/api/{path}",
        data=None if body is None else json.dumps(body).encode("utf-8"),
        method=method,
        headers={"Authorization": f"Bearer {SUPERVISOR_TOKEN}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        try:
            return resp.status, (json.loads(raw) if raw else None)
        except json.JSONDecodeError:
            return resp.status, None


def ha_set_state(entity_id: str, state, attributes: dict) -> bool:
    """POST a single entity state to Home Assistant Core. Fails silently
    (logged, not raised) — a Home Assistant hiccup should never break the
    expense-splitting app itself. Returns success."""
    if not ha_connected():
        return False
    try:
        _ha_call("POST", f"states/{entity_id}", {"state": state, "attributes": attributes})
        return True
    except (urllib.error.URLError, OSError) as e:
        logger.warning("HA sensor sync failed for %s: %s", entity_id, e)
        return False


def ha_delete_state(entity_id: str) -> bool:
    """DELETE /api/states/<entity_id> (best effort). A 404 counts as gone."""
    if not ha_connected():
        return False
    try:
        _ha_call("DELETE", f"states/{entity_id}")
        return True
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return True
        logger.warning("Could not remove %s from Home Assistant: %s", entity_id, e)
    except (urllib.error.URLError, OSError) as e:
        logger.warning("Could not remove %s from Home Assistant: %s", entity_id, e)
    return False


# Posts and removes the balance sensors (through ha_set_state / ha_delete_state, looked up at call time).
SENSORS = sensor_publisher.Publisher(post=lambda eid, state, attrs: ha_set_state(eid, state, attrs),
                                     delete=lambda eid: ha_delete_state(eid))


def balance_sensor_states(conn) -> list:
    """(entity_id, state, attributes) for every balance sensor, using the
    currency as it is set right now (so a change in App settings shows up as
    the sensors' unit on the very next push)."""
    currency = get_setting("currency")
    return [
        (
            f"sensor.splitpot_balance_{slugify(n['name'])}",
            round(n["amount"], 2),
            {
                "friendly_name": f"{n['name']} Splitpot Balance",
                "unit_of_measurement": currency,
                "icon": "mdi:cash-plus" if n["amount"] >= 0 else "mdi:cash-minus",
            },
        )
        for n in compute_overall_net(conn)
    ]


def push_balances_to_ha() -> int:
    """Push every balance sensor, if sensor sync is on (live setting).
    Returns how many were pushed."""
    if not sensor_sync_enabled():
        return 0
    with get_conn() as conn:
        states = balance_sensor_states(conn)
    return SENSORS.publish(states, force=True)["pushed"]   # network calls after the connection is closed


def remove_balance_sensors() -> int:
    """Sensor sync switched off: remove every balance sensor this app
    created from Home Assistant (best effort). Candidates are the
    sensor.splitpot_balance_* entities Home Assistant currently has (which
    also catches ones left over from a renamed user) plus one per known
    user, in case the state list can't be read. Returns how many are gone.
    Entities posted through the REST API aren't kept by Home Assistant
    across its own restarts anyway; this just stops them lingering until then."""
    if not ha_connected():
        return 0
    with get_conn() as conn:
        names = [r["name"] for r in conn.execute("SELECT name FROM users").fetchall()]
    candidates = {BALANCE_SENSOR_PREFIX + slugify(n) for n in names}
    try:   # connection closed above: no network while holding it
        _, states = _ha_call("GET", "states", timeout=10)
        candidates |= {
            st.get("entity_id", "") for st in (states or [])
            if isinstance(st, dict) and str(st.get("entity_id", "")).startswith(BALANCE_SENSOR_PREFIX)
        }
    except (urllib.error.URLError, OSError) as e:
        logger.warning("Could not list Home Assistant's states to find old balance sensors: %s", e)
    removed = SENSORS.remove(sorted(candidates))["removed"]
    if removed:
        logger.info("Sensor sync is off: removed %d balance sensor(s) from Home Assistant", removed)
    return removed


def fetch_ha_persons() -> list:
    """Read Home Assistant's `person.*` entities via the Core API proxy,
    keeping only ones linked to an actual HA user account (attributes.user_id
    present) — this deliberately excludes tracked-but-loginless persons like
    kids' device trackers or pets. Returns [] if unreachable (e.g. local
    testing without a real Supervisor) rather than raising."""
    if not SUPERVISOR_TOKEN:
        return []
    req = urllib.request.Request(
        "http://supervisor/core/api/states",
        headers={"Authorization": f"Bearer {SUPERVISOR_TOKEN}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            states = json.loads(resp.read())
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        logger.warning("Could not fetch person entities from Home Assistant: %s", e)
        return []

    persons = []
    for s in states:
        entity_id = s.get("entity_id", "")
        if not entity_id.startswith("person."):
            continue
        attrs = s.get("attributes", {})
        if not attrs.get("user_id"):
            continue  # not linked to a real HA user account — skip
        name = attrs.get("friendly_name") or entity_id.split(".", 1)[1].replace("_", " ").title()
        persons.append({"entity_id": entity_id, "name": name, "user_id": str(attrs["user_id"])})
    return persons


PERSONS_CACHE_SECONDS = 300
_persons_cache: tuple[float, list] | None = None
_persons_cache_lock = threading.Lock()


def cached_ha_persons() -> list:
    """fetch_ha_persons(), re-fetched at most every PERSONS_CACHE_SECONDS.
    GET /api/states returns EVERY entity in Home Assistant, so it's too
    heavy to repeat on each Users list load. Call this OUTSIDE the write
    lock: holding it would stall every other write for up to the request's
    5-second timeout whenever HA is slow."""
    global _persons_cache
    with _persons_cache_lock:
        if _persons_cache and time.monotonic() - _persons_cache[0] < PERSONS_CACHE_SECONDS:
            return _persons_cache[1]
    persons = fetch_ha_persons()
    if persons:   # don't cache an outage as "nobody exists"
        with _persons_cache_lock:
            _persons_cache = (time.monotonic(), persons)
    return persons


def sync_users_from_ha(conn, persons: Optional[list] = None) -> None:
    """Add/update Splitpot's users from Home Assistant's Persons.
    Never removes anyone — if someone is later removed from HA, their past
    expenses and group membership stay intact, they just won't be offered
    for new ones since the fetch above won't include them anymore."""
    for p in (fetch_ha_persons() if persons is None else persons):
        ha_user = (p.get("user_id") or "").strip().lower() or None
        row = conn.execute(
            "SELECT id, name, ha_user_id FROM users WHERE ha_entity_id = ?", (p["entity_id"],)
        ).fetchone()
        if row:
            if row["name"] != p["name"]:
                conn.execute("UPDATE users SET name = ? WHERE id = ?", (p["name"], row["id"]))
            if ha_user and row["ha_user_id"] != ha_user:
                # The HA login linked to this Person — what the app uses to
                # pick the signed-in person for "Acting as" and "Paid by".
                conn.execute("UPDATE users SET ha_user_id = ? WHERE id = ?", (ha_user, row["id"]))
        else:
            try:
                conn.execute(
                    "INSERT INTO users (id, name, created_at, ha_entity_id, ha_user_id) VALUES (?, ?, ?, ?, ?)",
                    (new_id(), p["name"], now_iso(), p["entity_id"], ha_user),
                )
            except sqlite3.IntegrityError:
                pass  # extremely unlikely name/entity race — skip, next sync will retry
    conn.commit()


def _connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return db_core.connect(DB_FILE, timeout=10, pragmas=("foreign_keys = ON",))


def get_conn():
    """A connection that is closed at the end of the block (app/common/db_core.py); the code inside
    commits itself."""
    return db_core.closing(_connect)


# Columns older databases lack (CREATE TABLE IF NOT EXISTS won't retroactively add columns):
# (table, column, definition), added in this order.
MIGRATIONS = [
    ("users", "ha_entity_id", "TEXT"),
    ("events", "actor", "TEXT"),
    ("users", "disabled", "INTEGER NOT NULL DEFAULT 0"),
    ("users", "ha_user_id", "TEXT"),                      # the HA login behind each Person
    ("expense_splits", "percent", "REAL"),
    ("groups", "is_default", "INTEGER NOT NULL DEFAULT 0"),
    ("users", "receive_notifications", "INTEGER NOT NULL DEFAULT 1"),   # the person's own opt-out (My settings)
    ("users", "assistant_ok", "INTEGER NOT NULL DEFAULT 1"),            # "Let the Household Assistant answer for me"
]


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                ha_entity_id TEXT,
                disabled INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS groups (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                is_default INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS group_members (
                group_id TEXT NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                position INTEGER NOT NULL,
                PRIMARY KEY (group_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS expenses (
                id TEXT PRIMARY KEY,
                group_id TEXT NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
                description TEXT NOT NULL,
                amount REAL NOT NULL,
                paid_by TEXT NOT NULL REFERENCES users(id),
                split_type TEXT NOT NULL,
                date TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS expense_splits (
                expense_id TEXT NOT NULL REFERENCES expenses(id) ON DELETE CASCADE,
                user_id TEXT NOT NULL REFERENCES users(id),
                amount REAL NOT NULL,
                percent REAL,              -- 'percent' expenses only, else NULL
                PRIMARY KEY (expense_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                message TEXT NOT NULL,
                group_id TEXT,
                actor TEXT,
                created_at TEXT NOT NULL
            );

            -- Admin → App settings. value is JSON; updated_by is the
            -- admin who saved it.
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                updated_by TEXT
            );

            -- Admin → Users: extra notify services per person ("notify.<name>"), on top of the phones
            -- Home Assistant links to them (common/ha_notify.py, people_admin.py).
            CREATE TABLE IF NOT EXISTS user_notify (
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                service TEXT NOT NULL,
                created_at TEXT NOT NULL,
                created_by TEXT,
                PRIMARY KEY (user_id, service)
            );

            CREATE INDEX IF NOT EXISTS idx_group_members_group ON group_members(group_id);
            CREATE INDEX IF NOT EXISTS idx_expenses_group ON expenses(group_id);
            CREATE INDEX IF NOT EXISTS idx_expense_splits_expense ON expense_splits(expense_id);
            CREATE INDEX IF NOT EXISTS idx_expenses_date ON expenses(date);
            -- the group ledger, newest first, a page at a time (keyset on date + id)
            CREATE INDEX IF NOT EXISTS idx_expenses_group_date ON expenses(group_id, date, id);
            CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at);
            """
        )
        # Older databases may lack columns added to the tables above (MIGRATIONS).
        db_core.add_missing_columns(conn, MIGRATIONS)
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_ha_entity ON users(ha_entity_id) "
            "WHERE ha_entity_id IS NOT NULL"
        )
        # At most one default group, enforced at write time (see set_group_default);
        # this partial unique index is a backstop against that ever slipping.
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_groups_one_default ON groups(is_default) "
            "WHERE is_default = 1"
        )
        conn.execute("PRAGMA journal_mode = WAL")
        conn.commit()
    # A fresh or just-restored database may hold different settings.
    invalidate_settings_cache()


# ---------- App settings (Admin → App settings) ----------
# Household-wide settings in the app_settings table, editable by admins
# without restarting the app. Only admin_users is an app option.
# All apply live: money()/the sensors read the currency when they run, the
# sync loop re-reads ha_sync_enabled and the interval every tick, and the
# settings route pushes or removes the sensors straight away.
# The registry (common/python/settings_core.py) validates, stores and caches them
# and gives the GET/PUT /api/admin/settings payload the shared page draws.

# ISO 4217 currency codes (active, plus ANG/SLL/ZWL which are still in use
# during their changeovers). Precious metals, fund and testing codes are left
# out: nobody splits the groceries in XAU.
ISO_4217_CODES = frozenset("""
AED AFN ALL AMD ANG AOA ARS AUD AWG AZN BAM BBD BDT BGN BHD BIF BMD BND BOB BOV BRL BSD
BTN BWP BYN BZD CAD CDF CHE CHF CHW CLF CLP CNY COP COU CRC CUC CUP CVE CZK DJF DKK DOP
DZD EGP ERN ETB EUR FJD FKP GBP GEL GHS GIP GMD GNF GTQ GYD HKD HNL HTG HUF IDR ILS INR
IQD IRR ISK JMD JOD JPY KES KGS KHR KMF KPW KRW KWD KYD KZT LAK LBP LKR LRD LSL LYD MAD
MDL MGA MKD MMK MNT MOP MRU MUR MVR MWK MXN MXV MYR MZN NAD NGN NIO NOK NPR NZD OMR PAB
PEN PGK PHP PKR PLN PYG QAR RON RSD RUB RWF SAR SBD SCR SDG SEK SGD SHP SLE SLL SOS SRD
SSP STN SVC SYP SZL THB TJS TMT TND TOP TRY TTD TWD TZS UAH UGX USD USN UYI UYU UYW UZS
VED VES VND VUV WST XAF XCD XCG XOF XPF YER ZAR ZMW ZWG ZWL
""".split())

_CURRENCY_HELP = "must be a 3-letter ISO 4217 code such as USD, EUR, GBP or INR"


def _currency_code(v):
    if not isinstance(v, str):
        raise ValueError(_CURRENCY_HELP)
    code = v.strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", code):
        raise ValueError(_CURRENCY_HELP)
    if code not in ISO_4217_CODES:
        raise ValueError(f"{code} isn't an ISO 4217 currency code")
    return code


SETTINGS = [
    settings_core.Setting(
        "ha_sync_enabled", True, "Sync balances to Home Assistant", group="ha", strict=True,
        help="Publishes each user's overall balance as a sensor (sensor.splitpot_balance_<name>) for dashboards and "
             "automations. Turning it off removes those sensors from Home Assistant; turning it on publishes them "
             "straight away."),
    settings_core.Setting(
        "sync_interval_minutes", 5, "Sync interval (minutes)", group="ha", min=1, max=60, strict=True,
        enabled_if="ha_sync_enabled", page={"label": "Sensor sync interval (minutes)"},   # (messages: "Sync interval")
        help="How often the balance sensors are refreshed in the background, on top of the instant update after every "
             "change. A new interval applies from the next sync cycle. Only used while sensor sync is on."),
    settings_core.Setting(
        "currency", "USD", "Currency code", group="money", before=_currency_code,
        page={"maxLength": 3, "suggestions": ["USD", "EUR", "GBP", "INR", "CAD", "AUD", "NZD", "CHF", "JPY", "CNY",
                                               "SEK", "NOK", "DKK", "PLN", "MXN", "BRL", "ZAR", "SGD", "HKD", "AED",
                                               "KRW"]},
        help="ISO 4217 code such as USD, EUR, GBP or INR. Used for every amount in the app and the activity log, and as "
             "the balance sensors' unit."),
    settings_core.Setting(
        "notify_charges", False, "Notify people about new charges", group="notify", strict=True,
        help="When someone adds a charge, everyone in it — whoever paid and everyone with a share — gets a phone "
             "notification through Home Assistant, except whoever added it: who added what, the amount, the group and "
             "their share. Phones come from Home Assistant (Settings → People → the person → Track device); extra "
             "notify services can be added under Admin → Users. Each person can turn them off under My settings."),
    settings_core.Setting(
        "notify_payments", True, "Also notify settle-up payments", group="notify", strict=True,
        enabled_if="notify_charges",
        help="When a settle-up payment is recorded, the two people in it are told too (not whoever recorded it)."),
    settings_core.Setting(
        "assistant_answers", False, "Answer the Household Assistant", group="assistant", strict=True,
        help="Lets the Household Assistant tell a person the balances and recent expenses of the groups they are in — "
             "who owes whom, and what was spent, by whom and how much. Off until an admin turns it on; each person can "
             "still turn it off for themselves under My settings."),
]
SETTINGS_GROUPS = [settings_core.Group("money", "Money"), settings_core.Group("ha", "Home Assistant"),
                   settings_core.Group("notify", "Notifications",
                                       "Phone notifications through Home Assistant. Edits and deletions are never "
                                       "notified; the activity log on the Dashboard shows them."),
                   settings_core.Group("assistant", "Household Assistant",
                                       "The Household Assistant app answers questions from what the household apps "
                                       "know. Money is private, so Splitpot doesn't answer it until you turn this on.")]


def _settings_log_event(conn, changed, current, merged, user):
    def describe(k):
        if k == "ha_sync_enabled":
            return f"sensor sync {'on' if merged[k] else 'off'}"
        if k == "sync_interval_minutes":
            return f"sync interval {current[k]} → {merged[k]} min"
        if k == "notify_charges":
            return f"charge notifications {'on' if merged[k] else 'off'}"
        if k == "notify_payments":
            return f"settle-up notifications {'on' if merged[k] else 'off'}"
        return f"currency {current[k]} → {merged[k]}"
    what = ", ".join(describe(k) for k in changed)
    log_event(conn, "settings_changed", f"{user or 'Someone'} changed the app settings: {what}", actor=user)


SETTINGS_REGISTRY = settings_core.Registry(
    SETTINGS, groups=SETTINGS_GROUPS, connect=lambda: get_conn(),
    format_error=settings_core.by_label({s.key: s.label for s in SETTINGS}, empty="Invalid settings"),
    not_object_message="Send the settings as a JSON object", write_lock=_lock, on_write=_settings_log_event,
    now=lambda: now_iso(), log=lambda *a: None, logger=logger)
SETTINGS_DEFAULTS = SETTINGS_REGISTRY.defaults
SETTINGS_LABELS = SETTINGS_REGISTRY.labels
SETTINGS_META = SETTINGS_REGISTRY.meta()
AppSettings = SETTINGS_REGISTRY.model
SettingsError = settings_core.SettingsError


def invalidate_settings_cache() -> None:
    SETTINGS_REGISTRY.invalidate()


def settings_all() -> dict:
    return SETTINGS_REGISTRY.all()


def get_setting(key: str):
    return SETTINGS_REGISTRY.get(key)


def update_settings(partial: dict, user: Optional[str]) -> dict:
    """Validate the merged result, write only the keys that changed, and
    return the new values. Raises SettingsError (-> 422) on unknown keys or
    bad values; nothing is written in that case."""
    return SETTINGS_REGISTRY.update(partial, user)[0]


def settings_payload() -> dict:
    return SETTINGS_REGISTRY.payload()


# ---------- background sensor sync ----------
# The loop wakes at least every SYNC_TICK_SECONDS and re-reads the interval
# each time, so a changed sync_interval_minutes applies from the next cycle
# (shortening it doesn't have to wait out the old, longer sleep).
SYNC_TICK_SECONDS = 30
SYNC_STARTUP_DELAY_SECONDS = 10


def sync_interval_seconds() -> int:
    return int(get_setting("sync_interval_minutes")) * 60


def seconds_until_next_sync(last_run: float, now: float) -> float:
    """How long until the next sensor push is due, given when the last one
    ran (both time.monotonic() values), using the interval as set right now."""
    return max(0.0, last_run + sync_interval_seconds() - now)


async def periodic_sync(sleep=asyncio.sleep, clock=time.monotonic) -> None:
    """Started from the lifespan whenever there is a Home Assistant
    connection (the on/off switch is a live App setting, so it can be turned
    on later). Every tick (at most SYNC_TICK_SECONDS) it re-reads
    ha_sync_enabled and the interval:
    - on: push the sensors when due (at once after startup or after being
      switched back on);
    - off: remove the balance sensors once (at startup with sync off, or
      right after it was switched off), then stay idle."""
    await sleep(SYNC_STARTUP_DELAY_SECONDS)  # give the app (and HA Core) a moment to settle
    last_run = None       # clock() of the last push; None = push on the next "on" tick
    was_on = None         # None = first tick
    while True:
        try:
            if not get_setting("ha_sync_enabled"):
                if was_on is not False:
                    await asyncio.to_thread(remove_balance_sensors)
                was_on, last_run = False, None
            else:
                was_on = True
                if last_run is None or seconds_until_next_sync(last_run, clock()) <= 0:
                    last_run = clock()
                    await asyncio.to_thread(push_balances_to_ha)
        except Exception:   # never let one bad cycle end the loop
            logger.exception("Background sensor sync failed")
        wait = SYNC_TICK_SECONDS if last_run is None else seconds_until_next_sync(last_run, clock())
        await sleep(min(max(wait, 1), SYNC_TICK_SECONDS))

def new_id() -> str:
    return str(uuid.uuid4())


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------- small helpers ----------
def user_name(conn, uid: str) -> str:
    row = conn.execute("SELECT name FROM users WHERE id = ?", (uid,)).fetchone()
    return row["name"] if row else "Unknown"


def group_name_or_404(conn, group_id: str) -> str:
    row = conn.execute("SELECT name FROM groups WHERE id = ?", (group_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Group not found")
    return row["name"]


def group_member_ids(conn, group_id: str) -> List[str]:
    rows = conn.execute(
        "SELECT user_id FROM group_members WHERE group_id = ? ORDER BY position", (group_id,)
    ).fetchall()
    return [r["user_id"] for r in rows]


def fetch_expense_splits(conn, expense_id: str):
    """Split rows in the order they were entered; `percent` is included for
    split-by-percentage expenses only."""
    rows = conn.execute(
        "SELECT user_id, amount, percent FROM expense_splits WHERE expense_id = ? ORDER BY rowid", (expense_id,)
    ).fetchall()
    out = []
    for r in rows:
        item = {"userId": r["user_id"], "amount": r["amount"]}
        if r["percent"] is not None:
            item["percent"] = r["percent"]
        out.append(item)
    return out


def ha_actor_name(request: Request) -> str:
    """The Home Assistant user performing this request, per the identity
    headers HA's ingress attaches to every authenticated session."""
    return (
        request.headers.get("x-remote-user-display-name")
        or request.headers.get("x-remote-user-name")
        or "Someone"
    )


def log_event(conn, event_type: str, message: str, group_id: Optional[str] = None, actor: Optional[str] = None) -> None:
    """Record an entry in the activity log. Call inside the same transaction
    as the change it describes, before commit(), so the log stays in sync."""
    conn.execute(
        "INSERT INTO events (id, type, message, group_id, actor, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (new_id(), event_type, message, group_id, actor, now_iso()),
    )


# ---------- the group ledger, a page at a time ----------
# Newest first by (date, id) — `date` sorts as a string (a bare YYYY-MM-DD below that day's timestamps) and the
# id breaks ties, so the order is total and a cursor (the last row's date and id) points at the same place however
# many entries are added meanwhile. Balances and counts never come from a page: they are worked out from every
# entry, in SQL.
LEDGER_PAGE = 20          # what the group page loads first, and per "Load more"
LEDGER_MAX_LIMIT = 500


def encode_cursor(date: str, expense_id: str) -> str:
    """The position after a ledger row, opaque to the browser."""
    return base64.urlsafe_b64encode(json.dumps([date, expense_id]).encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[str, str]:
    """(date, id) from encode_cursor, or a 400."""
    try:
        date, eid = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        if isinstance(date, str) and isinstance(eid, str) and date and eid:
            return date, eid
    except (ValueError, TypeError, UnicodeDecodeError):
        pass
    raise HTTPException(400, "That ledger position isn't valid — reload the group.")


def _expense_json(conn, group_id: str, e, names: dict) -> dict:
    splits = fetch_expense_splits(conn, e["id"])
    return {
        "id": e["id"],
        "groupId": group_id,
        "description": e["description"],
        "amount": e["amount"],
        "paidBy": e["paid_by"],
        "paidByName": names.get(e["paid_by"], "Unknown"),
        "splitType": e["split_type"],
        "date": e["date"],
        "splits": [{**s, "name": names.get(s["userId"], "Unknown")} for s in splits],
    }


def ledger_page(conn, group_id: str, limit: Optional[int] = None, cursor: Optional[str] = None):
    """(entries, next cursor) — the group's expenses and payments newest first, starting after `cursor`;
    at most `limit` of them (None = all, and no next cursor)."""
    sql = "SELECT id, description, amount, paid_by, split_type, date FROM expenses WHERE group_id = ?"
    args: list = [group_id]
    if cursor:
        date, eid = decode_cursor(cursor)
        sql += " AND (date < ? OR (date = ? AND id < ?))"
        args += [date, date, eid]
    sql += " ORDER BY date DESC, id DESC"
    if limit is not None:
        sql += " LIMIT ?"
        args.append(limit + 1)                 # one more: is there a next page?
    rows = conn.execute(sql, args).fetchall()
    more = limit is not None and len(rows) > limit
    rows = rows[:limit] if limit is not None else rows
    names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM users")}
    entries = [_expense_json(conn, group_id, r, names) for r in rows]
    return entries, (encode_cursor(rows[-1]["date"], rows[-1]["id"]) if more else None)


def ledger_total(conn, group_id: str) -> int:
    """Every entry in the group's ledger (expenses and payments)."""
    return conn.execute("SELECT COUNT(*) AS c FROM expenses WHERE group_id = ?", (group_id,)).fetchone()["c"]


def group_net(conn, group_id: str, member_ids: List[str]) -> dict:
    """Each person's net in the group from EVERY entry (paid − shares), in SQL, in whole cents (so the sum is exact
    whatever the order), then as 2-dp amounts. Members first in their order, then anyone else with an entry."""
    cents = {m: 0 for m in member_ids}
    for r in conn.execute("SELECT paid_by AS u, SUM(CAST(ROUND(amount * 100) AS INTEGER)) AS c FROM expenses "
                          "WHERE group_id = ? GROUP BY paid_by ORDER BY paid_by", (group_id,)):
        cents[r["u"]] = cents.get(r["u"], 0) + r["c"]
    for r in conn.execute("SELECT s.user_id AS u, SUM(CAST(ROUND(s.amount * 100) AS INTEGER)) AS c "
                          "FROM expense_splits s JOIN expenses e ON e.id = s.expense_id "
                          "WHERE e.group_id = ? GROUP BY s.user_id ORDER BY s.user_id", (group_id,)):
        cents[r["u"]] = cents.get(r["u"], 0) - r["c"]
    return {k: round(v / 100, 2) for k, v in cents.items()}


def serialize_group(conn, group_id: str, limit: Optional[int] = None, cursor: Optional[str] = None) -> dict:
    """The group. `expenses` is the whole ledger, or with `limit` its newest `limit` entries (`nextCursor` then
    points at the rest: GET /groups/{id}/ledger). Balances and `ledgerTotal` always cover every entry."""
    g = conn.execute("SELECT id, name, created_at, is_default FROM groups WHERE id = ?", (group_id,)).fetchone()
    if not g:
        raise HTTPException(404, "Group not found")

    member_ids = group_member_ids(conn, group_id)
    expenses, next_cursor = ledger_page(conn, group_id, limit, cursor)
    net, transfers = settle_up(group_net(conn, group_id, member_ids))

    return {
        "id": g["id"],
        "name": g["name"],
        "createdAt": g["created_at"],
        "isDefault": bool(g["is_default"]),
        "memberIds": member_ids,
        "members": [{"id": m, "name": user_name(conn, m)} for m in member_ids],
        "expenses": expenses,
        "ledgerTotal": ledger_total(conn, group_id),
        "nextCursor": next_cursor,
        "balances": {
            "net": [{"userId": uid, "name": user_name(conn, uid), "amount": amt} for uid, amt in net.items()],
            "transfers": [
                {**t, "fromName": user_name(conn, t["from"]), "toName": user_name(conn, t["to"])}
                for t in transfers
            ],
        },
    }


# ---------- balance math ----------
# Given a group's member ids and its expenses (with splits), compute each
# member's net balance (positive = is owed money, negative = owes money),
# then simplify into a minimal set of settle-up transfers greedily.
def compute_balances(member_ids: List[str], expenses: list[dict]):
    net = {m: 0.0 for m in member_ids}
    for e in expenses:
        net[e["paidBy"]] = net.get(e["paidBy"], 0.0) + e["amount"]
        for s in e["splits"]:
            net[s["userId"]] = net.get(s["userId"], 0.0) - s["amount"]
    return settle_up({k: round(v, 2) for k, v in net.items()})


def settle_up(net: dict):
    """(net, transfers): the greedy settle-up of 2-dp nets — largest debtor pays largest creditor."""
    debtors = sorted(
        ({"userId": k, "amount": -v} for k, v in net.items() if v < -0.001),
        key=lambda x: -x["amount"],
    )
    creditors = sorted(
        ({"userId": k, "amount": v} for k, v in net.items() if v > 0.001),
        key=lambda x: -x["amount"],
    )

    transfers = []
    i = j = 0
    while i < len(debtors) and j < len(creditors):
        pay = min(debtors[i]["amount"], creditors[j]["amount"])
        transfers.append(
            {"from": debtors[i]["userId"], "to": creditors[j]["userId"], "amount": round(pay, 2)}
        )
        debtors[i]["amount"] -= pay
        creditors[j]["amount"] -= pay
        if debtors[i]["amount"] < 0.005:
            i += 1
        if creditors[j]["amount"] < 0.005:
            j += 1

    return net, transfers


# ---------- period helpers (for the weekly / monthly dashboard views) ----------
def parse_date(s: str) -> datetime:
    """Stored expense dates are either a full UTC timestamp (added "today")
    or a bare YYYY-MM-DD picked in the date field, which means that calendar
    day in the household's own time zone — local midnight, not UTC midnight
    (which would be the previous evening in the Americas and file it under
    the wrong week)."""
    if len(s) == 10:
        d = datetime.strptime(s, "%Y-%m-%d")
        return d.replace(tzinfo=tz())
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def week_range(offset: int = 0):
    """Monday-start week, in the household's own time zone (see local_now()
    above) — offset=0 is the current week, -1 is last week, etc. Using the
    household's zone rather than UTC matters right at the boundary: without
    it, an expense added Sunday night could get filed under next week."""
    now = local_now()
    monday_this_week = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    start = monday_this_week + timedelta(weeks=offset)
    end = start + timedelta(days=7)
    label = f"{start.strftime('%b %-d')} – {(end - timedelta(days=1)).strftime('%b %-d, %Y')}"
    return start, end, label


def month_range(offset: int = 0):
    """Calendar month, in the household's own time zone (see local_now()
    above). offset=0 is the current month, -1 is last month, etc."""
    now = local_now()
    total = now.year * 12 + (now.month - 1) + offset
    year, month0 = divmod(total, 12)
    month = month0 + 1
    zone = tz()
    start = datetime(year, month, 1, tzinfo=zone)
    end = datetime(year + 1, 1, 1, tzinfo=zone) if month == 12 else datetime(year, month + 1, 1, tzinfo=zone)
    label = start.strftime("%B %Y")
    return start, end, label


def weekday_breakdown(expenses: list[dict], start: datetime) -> list[dict]:
    labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    totals = [0.0] * 7
    for e in expenses:
        idx = (parse_date(e["date"]) - start).days
        if 0 <= idx < 7:
            totals[idx] += e["amount"]
    return [{"label": labels[i], "amount": round(totals[i], 2)} for i in range(7)]


def group_breakdown(expenses: list[dict]) -> list[dict]:
    totals: dict = {}
    for e in expenses:
        totals[e["groupName"]] = totals.get(e["groupName"], 0.0) + e["amount"]
    return sorted(
        ({"label": label, "amount": round(amt, 2)} for label, amt in totals.items()),
        key=lambda x: -x["amount"],
    )


# ---------- models ----------
class GroupCreate(BaseModel):
    name: str
    memberIds: List[str] = Field(default_factory=list)


class MemberAdd(BaseModel):
    userId: str


class GroupDefaultUpdate(BaseModel):
    isDefault: bool


MAX_AMOUNT = 1_000_000


class SplitItem(BaseModel):
    userId: str
    # >= 0 and finite: JSON allows Infinity/NaN, and a single infinite
    # amount would break every balance (and the JSON of every response
    # that includes it) for the whole household.
    # splitType "custom" needs `amount`; "percent" needs `percent` (0–100).
    amount: Optional[float] = Field(None, ge=0, le=MAX_AMOUNT, allow_inf_nan=False)
    percent: Optional[float] = Field(None, ge=0, le=100, allow_inf_nan=False)


class ExpenseCreate(BaseModel):
    description: str = Field(max_length=200)
    amount: float = Field(gt=0, le=MAX_AMOUNT, allow_inf_nan=False)
    paidBy: str
    splitType: Optional[Literal["equal", "custom", "percent"]] = "equal"
    participants: Optional[List[str]] = None
    splits: Optional[List[SplitItem]] = None
    date: Optional[str] = None   # YYYY-MM-DD in the household's time zone; omitted = now


class PaymentCreate(BaseModel):
    """A settle-up payment: `fromUserId` paid `toUserId` back. Stored as a
    one-person "expense" (split_type 'payment') so the existing balance maths
    handles it — but excluded from spending totals everywhere."""
    fromUserId: str
    toUserId: str
    amount: float = Field(gt=0, le=MAX_AMOUNT, allow_inf_nan=False)
    date: Optional[str] = None


# ---------- expense validation (shared by add / edit / payment) ----------
def resolve_expense_date(value: Optional[str]) -> str:
    """None/"" or today's local date -> a full timestamp (now); another
    YYYY-MM-DD -> stored as-is (a local calendar day, see parse_date).
    Rejects anything unparseable or more than a day in the future: stored
    verbatim, it would crash every dashboard/transactions request that
    tried to parse it."""
    if not value:
        return now_iso()
    try:
        d = datetime.strptime(value.strip()[:10], "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(400, "Date must look like YYYY-MM-DD")
    today = local_now().date()
    if d > today + timedelta(days=1):
        raise HTTPException(400, "Date can't be in the future")
    if d.year < 2000:
        raise HTTPException(400, "Date is too far in the past")
    return now_iso() if d == today else d.isoformat()


def build_splits(member_ids: List[str], payload: "ExpenseCreate") -> tuple[list, str]:
    """Validated split rows + split type for an expense. Everyone involved
    must be a member of the group, and nobody may appear twice — a duplicate
    would hit the (expense_id, user_id) primary key as a 500, and a
    non-member would be silently charged a share of a group they're not in."""
    members = set(member_ids)
    amt = payload.amount
    if payload.paidBy not in members:
        raise HTTPException(400, "Payer must be a group member")
    if payload.splitType in ("custom", "percent"):
        if not payload.splits:
            raise HTTPException(400, "Provide custom split amounts" if payload.splitType == "custom"
                                else "Provide a percentage for each person")
        ids = [s.userId for s in payload.splits]
        if len(set(ids)) != len(ids):
            raise HTTPException(400, "Each person can only appear once in a split")
        if not set(ids) <= members:
            raise HTTPException(400, "Everyone in a split must be a member of this group")
    if payload.splitType == "percent":
        if any(s.percent is None for s in payload.splits):
            raise HTTPException(400, "Provide a percentage for each person")
        splits = percent_shares(amt, [(s.userId, s.percent) for s in payload.splits])
        return splits, "percent"
    if payload.splitType == "custom":
        if any(s.amount is None for s in payload.splits):
            raise HTTPException(400, "Provide custom split amounts")
        total = sum(s.amount for s in payload.splits)
        if abs(total - amt) > 0.02:
            raise HTTPException(400, f"Splits must add up to {amt:.2f}")
        splits = [{"userId": s.userId, "amount": round(s.amount, 2)} for s in payload.splits if s.amount > 0]
        if not splits:
            raise HTTPException(400, "At least one person needs a share greater than 0")
        return splits, "custom"
    participants = list(dict.fromkeys(payload.participants or member_ids))   # dedupe, keep order
    if not participants:
        raise HTTPException(400, "Pick at least one person to split with")
    if not set(participants) <= members:
        raise HTTPException(400, "Everyone in a split must be a member of this group")
    share = round(amt / len(participants), 2)
    splits = []
    for idx, uid in enumerate(participants):
        amount = round(amt - share * (len(participants) - 1), 2) if idx == len(participants) - 1 else share
        splits.append({"userId": uid, "amount": amount})
    return splits, "equal"


def percent_shares(amount: float, rows: list) -> list:
    """Split `amount` by percentages ([(userId, percent), ...], percent
    0–100). Percentages are rounded to 2 decimals and must then add up to
    exactly 100.00 (400 otherwise). Each share is amount × percent / 100 in
    whole cents; the cents lost to rounding down go one each to the largest
    remainders (ties: list order), so the shares add up EXACTLY to the amount
    and none is negative. All in integers (cents, hundredths of a percent) to
    stay clear of float drift. 0% rows are dropped; at least one must be > 0.
    Returns [{userId, amount, percent}] in list order."""
    cents = round(amount * 100)
    hundredths = [(uid, round(pct * 100)) for uid, pct in rows]   # 33.335 -> 3334 (= 33.34%)
    if sum(h for _, h in hundredths) != 10000:
        raise HTTPException(400, "Percentages must add up to 100%")
    kept = [(uid, h) for uid, h in hundredths if h > 0]
    if not kept:
        raise HTTPException(400, "At least one person needs a percentage greater than 0")
    base = [cents * h // 10000 for _, h in kept]
    remainder = [cents * h % 10000 for _, h in kept]
    short = cents - sum(base)                 # 0 <= short < len(kept)
    for i in sorted(range(len(kept)), key=lambda i: (-remainder[i], i))[:short]:
        base[i] += 1
    return [{"userId": uid, "amount": base[i] / 100, "percent": h / 100} for i, (uid, h) in enumerate(kept)]


def write_splits(conn, expense_id: str, splits: list) -> None:
    conn.execute("DELETE FROM expense_splits WHERE expense_id = ?", (expense_id,))
    for sp in splits:
        conn.execute(
            "INSERT INTO expense_splits (expense_id, user_id, amount, percent) VALUES (?, ?, ?, ?)",
            (expense_id, sp["userId"], sp["amount"], sp.get("percent")),
        )


# ---------- phone notifications (Admin → App settings → Notifications) ----------
# Through Home Assistant, with the shared common/ha_notify.py: a person is reached on the phones Home Assistant
# links to them (Settings → People → Track device; common/ha_people.py, matched by their HA user id,
# users.ha_user_id) plus any extra notify services an admin adds under Admin → Users (user_notify, keyed by
# Splitpot's own users.id). Someone with neither — e.g. a Person whose login isn't known yet — just isn't told.
# Sent from a background task after the response, with no DB connection open while Home Assistant is called;
# failures are logged, never raised.
NOTIFY_TITLE = "Splitpot"
MAX_SERVICES_PER_USER = 10
TEST_INTERVAL_SECONDS = 10
_TEST_LIMITER = people_admin.TestLimiter(TEST_INTERVAL_SECONDS)


def person_services(conn, row) -> list[str]:
    """Bare notify names (what ha_notify.send_notify takes) that reach this Splitpot person: their phones from
    Home Assistant, then the extra services assigned here, without duplicates. Reads only the cached people."""
    phones = ha_people.phones_for(row["ha_user_id"]) if row["ha_user_id"] else []
    extra = [ha_notify.bare(s) for s in ha_notify.assigned_services(conn, {"id": row["id"]})]
    return list(dict.fromkeys(phones + extra))


def linked_user_id(ha_user_id: Optional[str]) -> Optional[str]:
    """The Splitpot person (users.id, disabled or not) whose Home Assistant login this is — by users.ha_user_id,
    else through the cached Person list. Never by name. May ask Home Assistant (cached): no connection is held."""
    uid = (ha_user_id or "").strip().lower()
    if not uid:
        return None
    with get_conn() as conn:
        row = conn.execute("SELECT id FROM users WHERE ha_user_id = ?", (uid,)).fetchone()
    if row:
        return row["id"]
    entity = next((p["entity_id"] for p in cached_ha_persons() if (p.get("user_id") or "").strip().lower() == uid), None)
    if not entity:
        return None
    with get_conn() as conn:
        row = conn.execute("SELECT id FROM users WHERE ha_entity_id = ?", (entity,)).fetchone()
    return row["id"] if row else None


def notify_link(group_id: str) -> Optional[dict]:
    """Tapping the notification opens the app on the group (iOS reads `url`, Android `clickAction`)."""
    if not config.INGRESS_PANEL:
        return None
    url = f"{config.INGRESS_PANEL}#/group/{group_id}"
    return {"url": url, "clickAction": url}


def send_notices(notices: list, data: Optional[dict]) -> int:
    """[(users.id, message)] → how many notifications Home Assistant accepted. Skips anyone disabled, switched
    off under My settings, or with no phone or service."""
    sent = 0
    for uid, message in notices:
        with get_conn() as conn:
            row = conn.execute("SELECT id, ha_user_id, disabled, receive_notifications FROM users WHERE id = ?",
                               (uid,)).fetchone()
            if not row or row["disabled"] or not row["receive_notifications"]:
                continue
            services = person_services(conn, row)
        if not services:                       # the connection is closed before Home Assistant is called
            continue
        results = ha_notify.send_to_services(services, NOTIFY_TITLE, message, data)
        sent += sum(1 for ok in results.values() if ok)
    return sent


def _entry_for_notice(expense_id: str):
    """(expense row + group name, splits, names) for a just-added entry, or None if it's gone."""
    with get_conn() as conn:
        e = conn.execute("SELECT e.id, e.group_id, e.description, e.amount, e.paid_by, e.split_type, "
                         "g.name AS group_name FROM expenses e JOIN groups g ON g.id = e.group_id WHERE e.id = ?",
                         (expense_id,)).fetchone()
        if not e:
            return None
        splits = fetch_expense_splits(conn, expense_id)
        names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM users")}
    return e, splits, names


def charge_notices(e, splits, names, actor_id: Optional[str], actor: str) -> list:
    """Who hears about a new charge, and what: whoever paid and everyone with a share, except the person who
    added it. "Asha added 'Dinner' ($90.00) to Trip, paid by Ravi. Your share: $30.00." """
    shares = {s["userId"]: s["amount"] for s in splits}
    involved = list(dict.fromkeys([e["paid_by"]] + [s["userId"] for s in splits]))
    head = f"{actor} added '{e['description']}' ({money(e['amount'])}) to {e['group_name']}"
    out = []
    for uid in involved:
        if uid == actor_id:
            continue
        if uid == e["paid_by"]:
            text = f"{head}, paid by you. " + (f"Your share: {money(shares[uid])}." if uid in shares
                                               else "You're not in the split.")
        else:
            text = f"{head}, paid by {names.get(e['paid_by'], 'someone')}. Your share: {money(shares[uid])}."
        out.append((uid, text))
    return out


def payment_notices(e, splits, names, actor_id: Optional[str], actor: str) -> list:
    """The two people in a settle-up payment, except whoever recorded it."""
    if not splits:
        return []
    payer, payee = e["paid_by"], splits[0]["userId"]
    amount, group = money(e["amount"]), e["group_name"]
    payer_name, payee_name = names.get(payer, "Someone"), names.get(payee, "someone")
    out = []
    if payee != actor_id:
        out.append((payee, f"{payer_name} paid you {amount} in {group} (a settle-up payment)." if actor_id == payer
                    else f"{actor} recorded {payer_name} paying you {amount} in {group}."))
    if payer != actor_id:
        out.append((payer, f"{payee_name} recorded your payment of {amount} to them in {group}." if actor_id == payee
                    else f"{actor} recorded you paying {payee_name} {amount} in {group}."))
    return out


def notify_new_charge(expense_id: str, actor_ha_id: Optional[str], actor: str) -> int:
    """Background task after a charge is added (when "Notify people about new charges" is on)."""
    try:
        if not get_setting("notify_charges") or not ha_core.has_token():
            return 0
        found = _entry_for_notice(expense_id)
        if not found:
            return 0
        e, splits, names = found
        notices = charge_notices(e, splits, names, linked_user_id(actor_ha_id), actor)
        return send_notices(notices, notify_link(e["group_id"]))
    except Exception:          # a notification problem must never surface anywhere
        logger.exception("Sending the new-charge notifications failed")
        return 0


def notify_payment(expense_id: str, actor_ha_id: Optional[str], actor: str) -> int:
    """Background task after a settle-up payment is recorded (both notification switches on)."""
    try:
        if not (get_setting("notify_charges") and get_setting("notify_payments")) or not ha_core.has_token():
            return 0
        found = _entry_for_notice(expense_id)
        if not found:
            return 0
        e, splits, names = found
        notices = payment_notices(e, splits, names, linked_user_id(actor_ha_id), actor)
        return send_notices(notices, notify_link(e["group_id"]))
    except Exception:
        logger.exception("Sending the settle-up notifications failed")
        return 0


# ---------- app ----------
@asynccontextmanager
async def lifespan(app: FastAPI):
    await load_ha_timezone()
    if ha_connected():
        # The phones Home Assistant links to each person (ha_people), and a warning for any notify service that
        # Home Assistant can't deliver to. Best effort: Splitpot works without them.
        try:
            await asyncio.to_thread(ha_people.refresh_blocking, True)
            await asyncio.to_thread(ha_notify.check_targets_blocking)
        except Exception:
            logger.exception("Reading the people and notify services from Home Assistant failed")

    try:
        await asyncio.to_thread(app_messages.start)     # the household apps bus (the Household Assistant asks)
    except Exception:
        logger.exception("Starting the app bus failed")
    jobs = jobs_core.Jobs()
    if ha_connected():   # sync on/off is checked live inside the loop
        jobs.add("sensor_sync", periodic_sync)
        jobs.add("ha_people", ha_people.loop)
    jobs.start()
    yield
    await jobs.stop()
    await asyncio.to_thread(app_messages.stop)


app = FastAPI(title="Splitpot", lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    """422s as one readable sentence (the frontend shows `detail` as-is).
    Also avoids echoing the rejected input back: FastAPI's default handler
    does, and for a body like {"amount": Infinity} that echo can't be
    encoded as JSON, turning a clean 422 into a 500."""
    parts = []
    for err in exc.errors():
        field = ".".join(str(x) for x in err.get("loc", ()) if x != "body")
        parts.append(f"{field}: {err.get('msg', 'invalid value')}" if field else err.get("msg", "invalid value"))
    return JSONResponse(status_code=422, content={"detail": "; ".join(parts) or "Invalid request"})

# This app runs only as a Home Assistant app — every request must arrive through
# HA's own authenticated Ingress proxy. Two checks, both mandatory:
#   1. The TCP source must be HA's ingress proxy (172.30.32.2) or loopback
#      (for the app's own internal healthcheck). Anything else — e.g.
#      another app/container on the same Docker network trying to call
#      our API directly and skip ingress auth entirely — is rejected.
#   2. HA's ingress always attaches X-Remote-User-Id for an authenticated
#      session, so we require it too as an explicit, auditable proof that
#      this specific request belongs to a logged-in HA user, not just
#      "some traffic that happened to originate from the right IP".
# Static assets are exempt from check 2 so the login-gated page can still
# load its own CSS/JS; the API itself stays fully gated.
# (the allowlist — the proxy and loopback — is app/common/auth_core.INGRESS_HOSTS)


@app.middleware("http")
async def require_ha_ingress_auth(request: Request, call_next):
    blocked = auth_core.refuse_outsiders(request) or web_security.refuse_cross_site(request)
    if blocked is not None:
        return blocked
    if request.url.path.startswith("/api/") and not auth_core.identity(request).user_id:
        return JSONResponse(status_code=401, content={"detail": "Not authenticated with Home Assistant"})
    return await call_next(request)


# Scripts only from the app itself (no inline scripts or handlers); a few inline style attributes remain;
# the page's two typefaces come from Google Fonts.
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
       "img-src 'self' data: blob:; connect-src 'self'; font-src 'self' https://fonts.gstatic.com; object-src 'none'; "
       "base-uri 'self'; form-action 'self'; frame-ancestors 'self'")

# The HTML shell (index.html, served for "/") must never be cached by
# the browser — it's what points at the versioned style.css?v=.../app.js?v=...
# below, so a stale cached copy of JUST this one file is enough to make a
# rebuilt app look like the update never took effect, even after a
# normal refresh. Static assets themselves aren't touched here: their
# cache-busting query string (bumped alongside config.yaml's version) is
# what invalidates them, so they can keep normal caching.
HEADERS = web_security.SecurityHeaders(CSP, pragma=True)
HEADERS.install(app)


router = APIRouter(prefix="/api")


# -- users --
# Two lists: GET /users is the picker list everyone needs (group member
# chips, Paid by, Acting as, "(disabled)" markers); managing users (the
# Admin → Users page and enabling/disabling someone) is admin-only.
def synced_user_rows() -> list:
    persons = cached_ha_persons()   # network call — deliberately outside the write lock
    with _lock, get_conn() as conn:
        sync_users_from_ha(conn, persons)
        return conn.execute(
            "SELECT u.id, u.name, u.created_at, u.disabled, u.ha_entity_id, u.ha_user_id, u.receive_notifications, "
            "(SELECT COUNT(*) FROM group_members gm WHERE gm.user_id = u.id) AS group_count "
            "FROM users u ORDER BY u.disabled, u.name COLLATE NOCASE"
        ).fetchall()


@router.get("/users")
def list_users():
    """Picker list — for everyone."""
    return [
        {"id": r["id"], "name": r["name"], "createdAt": r["created_at"], "disabled": bool(r["disabled"])}
        for r in synced_user_rows()
    ]


def _admin_user_json(conn, r) -> dict:
    return {
        "id": r["id"],
        "name": r["name"],
        "createdAt": r["created_at"],
        "disabled": bool(r["disabled"]),
        "haEntityId": r["ha_entity_id"],
        "groupCount": r["group_count"],
        "receiveNotifications": bool(r["receive_notifications"]),   # their own choice (My settings)
        "notify": ha_notify.assigned_services(conn, {"id": r["id"]}),    # extra services added here
        "ha": people_admin.ha_person_json(r["ha_user_id"]),            # their person and phones in Home Assistant
    }


@router.get("/admin/users")
def admin_list_users(refresh: bool = Query(default=False), _admin: None = Depends(require_admin)):
    """Admin → Users: the same people plus what an admin needs to manage them (with their phones from Home
    Assistant and extra notify services). `?refresh=1` ("Check Home Assistant again") reads HA's people first."""
    people_admin.refresh_people(refresh)        # network: no lock or connection held
    rows = synced_user_rows()
    with get_conn() as conn:
        return [_admin_user_json(conn, r) for r in rows]


# -- Admin → Users: extra notify services and "Send a test" (common/people_admin.py, as in the other apps) --
def _admin_user_row(conn, user_id: str):
    row = conn.execute(
        "SELECT u.id, u.name, u.created_at, u.disabled, u.ha_entity_id, u.ha_user_id, u.receive_notifications, "
        "(SELECT COUNT(*) FROM group_members gm WHERE gm.user_id = u.id) AS group_count FROM users u WHERE u.id = ?",
        (user_id,)).fetchone()
    if not row:
        raise HTTPException(404, "User not found")
    return row


def _admin_who(request: Request) -> str:
    ident = auth_core.identity(request)
    return ident.username or ident.user_id or "admin"


@router.get("/admin/notify-services")
def admin_notify_services(refresh: bool = Query(default=False), _admin: None = Depends(require_admin)):
    """The notify actions and entities Home Assistant has (for the Add pick-list); available=false if it can't
    be asked."""
    return people_admin.notify_services(refresh)


@router.post("/admin/users/{user_id}/notify", status_code=201)
def admin_add_notify(user_id: str, request: Request, body: dict = Body(...), _admin: None = Depends(require_admin)):
    service = people_admin.clean_service(body.get("service") if isinstance(body, dict) else None)
    with _lock, get_conn() as conn:
        _admin_user_row(conn, user_id)
        people_admin.add_service(conn, user_id, service, _admin_who(request), now_iso(), limit=MAX_SERVICES_PER_USER)
        conn.commit()
        return _admin_user_json(conn, _admin_user_row(conn, user_id))


@router.delete("/admin/users/{user_id}/notify/{service}")
def admin_remove_notify(user_id: str, service: str, _admin: None = Depends(require_admin)):
    service = people_admin.clean_service(service)
    with _lock, get_conn() as conn:
        row = _admin_user_row(conn, user_id)
        people_admin.remove_service(conn, user_id, service, f"{service} isn't assigned to {row['name']}.")
        conn.commit()
        return _admin_user_json(conn, row)


@router.post("/admin/users/{user_id}/notify/test")
def admin_test_notify(user_id: str, _admin: None = Depends(require_admin)):
    """A short test notification to every phone and service the person has (at most one every few seconds)."""
    with get_conn() as conn:
        row = _admin_user_row(conn, user_id)
        services = person_services(conn, row)
    if not services:
        raise HTTPException(400, f"{row['name']} has no phone linked in Home Assistant (Settings → People) "
                                 "and no extra notify service here.")
    _TEST_LIMITER.check(user_id)
    sent = ha_notify.send_to_services(services, NOTIFY_TITLE,
                                      f"Test notification from Splitpot for {row['name']}, sent by an admin.")
    results = people_admin.test_results(sent)
    people_admin.require_one_sent(results, services[0], ha_core.has_token)
    return {"results": results}


# -- My settings: each person's own choices --
class MyPrefsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    receiveNotifications: Optional[StrictBool] = None
    assistantOk: Optional[StrictBool] = None

    @model_validator(mode="after")
    def _something(self):
        if self.receiveNotifications is None and self.assistantOk is None:
            raise ValueError("send receiveNotifications or assistantOk")
        return self


def _my_prefs(user_id: Optional[str]) -> dict:
    s = settings_all()
    out = {"linked": False, "name": None, "receiveNotifications": None, "reachable": False,
           "notifyCharges": bool(s["notify_charges"]), "notifyPayments": bool(s["notify_payments"]),
           "haConnected": ha_connected(), "assistant": bool(s["assistant_answers"]), "assistantOk": None}
    if user_id:
        with get_conn() as conn:
            row = conn.execute("SELECT id, name, ha_user_id, receive_notifications, assistant_ok FROM users "
                               "WHERE id = ?", (user_id,)).fetchone()
            if row:
                out.update(linked=True, name=row["name"], receiveNotifications=bool(row["receive_notifications"]),
                           reachable=bool(person_services(conn, row)), assistantOk=bool(row["assistant_ok"]))
    return out


@router.get("/me/prefs")
def get_my_prefs(request: Request):
    """The signed-in person's own settings (matched by their Home Assistant login, never by name), plus whether
    notifications are switched on for the household and whether a phone reaches them."""
    return _my_prefs(linked_user_id(auth_core.identity(request).user_id))


@router.put("/me/prefs")
def put_my_prefs(payload: MyPrefsUpdate, request: Request):
    user_id = linked_user_id(auth_core.identity(request).user_id)
    if not user_id:
        raise HTTPException(404, "Home Assistant doesn't link your login to a person in Splitpot yet "
                                 "(Settings → People → you → Allow person to login).")
    with _lock, get_conn() as conn:
        if payload.receiveNotifications is not None:
            conn.execute("UPDATE users SET receive_notifications = ? WHERE id = ?",
                         (1 if payload.receiveNotifications else 0, user_id))
        if payload.assistantOk is not None:
            conn.execute("UPDATE users SET assistant_ok = ? WHERE id = ?", (1 if payload.assistantOk else 0, user_id))
        conn.commit()
    return _my_prefs(user_id)


class UserUpdate(BaseModel):
    disabled: bool


@router.patch("/users/{user_id}")
def update_user(user_id: str, payload: UserUpdate, request: Request, _admin: None = Depends(require_admin)):
    """Enable/disable a user in the pickers. Admin-only: this doesn't lock
    anyone out of their own Home Assistant login or delete anything, but it
    does change what every household member sees, so it's gated the same
    way the DB backup below is."""
    actor = ha_actor_name(request)
    with _lock, get_conn() as conn:
        row = conn.execute("SELECT name, disabled FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            raise HTTPException(404, "User not found")
        conn.execute("UPDATE users SET disabled = ? WHERE id = ?", (1 if payload.disabled else 0, user_id))
        if bool(row["disabled"]) != payload.disabled:
            verb = "disabled" if payload.disabled else "enabled"
            log_event(conn, f"user_{verb}", f"{actor} {verb} {row['name']}", actor=actor)
        conn.commit()
        updated = conn.execute(
            "SELECT id, name, created_at, disabled FROM users WHERE id = ?", (user_id,)
        ).fetchone()
    return {
        "id": updated["id"],
        "name": updated["name"],
        "createdAt": updated["created_at"],
        "disabled": bool(updated["disabled"]),
    }


# -- groups --
@router.get("/groups")
def list_groups():
    with get_conn() as conn:
        groups = conn.execute("SELECT id, name, created_at, is_default FROM groups ORDER BY created_at").fetchall()
        out = []
        for g in groups:
            member_ids = group_member_ids(conn, g["id"])
            expense_count = conn.execute(
                "SELECT COUNT(*) AS c FROM expenses WHERE group_id = ? AND split_type != 'payment'", (g["id"],)
            ).fetchone()["c"]
            out.append(
                {
                    "id": g["id"],
                    "name": g["name"],
                    "createdAt": g["created_at"],
                    "isDefault": bool(g["is_default"]),
                    "memberIds": member_ids,
                    "memberNames": [user_name(conn, m) for m in member_ids],
                    "expenseCount": expense_count,
                }
            )
        return out


@router.post("/groups", status_code=201)
def create_group(payload: GroupCreate, request: Request):
    name = payload.name.strip()
    if not name:
        raise HTTPException(400, "Group name is required")
    payload.memberIds = list(dict.fromkeys(payload.memberIds))   # dedupe, keep order
    if len(payload.memberIds) < 1:
        raise HTTPException(400, "Pick at least one member")
    actor = ha_actor_name(request)
    with _lock, get_conn() as conn:
        known = conn.execute(
            f"SELECT COUNT(*) AS c FROM users WHERE id IN ({','.join('?' for _ in payload.memberIds)})",
            payload.memberIds,
        ).fetchone()["c"]
        if known != len(payload.memberIds):
            raise HTTPException(400, "One or more selected people don't exist")
        disabled_count = conn.execute(
            f"SELECT COUNT(*) AS c FROM users WHERE disabled = 1 AND id IN "
            f"({','.join('?' for _ in payload.memberIds)})",
            payload.memberIds,
        ).fetchone()["c"]
        if disabled_count:
            raise HTTPException(400, "One or more selected people are disabled")
        group_id = new_id()
        created_at = now_iso()
        conn.execute(
            "INSERT INTO groups (id, name, created_at) VALUES (?, ?, ?)", (group_id, name, created_at)
        )
        for pos, uid in enumerate(payload.memberIds):
            conn.execute(
                "INSERT INTO group_members (group_id, user_id, position) VALUES (?, ?, ?)",
                (group_id, uid, pos),
            )
        member_word = "member" if len(payload.memberIds) == 1 else "members"
        log_event(
            conn,
            "group_created",
            f"{actor} created group '{name}' with {len(payload.memberIds)} {member_word}",
            group_id=group_id,
            actor=actor,
        )
        conn.commit()
    return {"id": group_id, "name": name, "createdAt": created_at, "memberIds": payload.memberIds}


# `limit` (on every route that answers with the group): only the newest `limit` ledger entries, plus `nextCursor`
# for the rest. Without it the whole ledger, as before.
@router.get("/groups/{group_id}")
def get_group(group_id: str, limit: Optional[int] = Query(None, ge=1, le=LEDGER_MAX_LIMIT)):
    with get_conn() as conn:
        return serialize_group(conn, group_id, limit)


@router.get("/groups/{group_id}/ledger")
def get_group_ledger(group_id: str, limit: int = Query(LEDGER_PAGE, ge=1, le=LEDGER_MAX_LIMIT),
                     cursor: Optional[str] = Query(None, max_length=1000)):
    """The next page of the group's ledger ("Load more"): entries after `cursor` (from the group's or the previous
    page's `nextCursor`; none = from the newest), newest first. `nextCursor` is null on the last page."""
    with get_conn() as conn:
        group_name_or_404(conn, group_id)
        entries, next_cursor = ledger_page(conn, group_id, limit, cursor)
        return {"expenses": entries, "nextCursor": next_cursor, "total": ledger_total(conn, group_id)}


@router.post("/groups/{group_id}/members")
def add_member(group_id: str, payload: MemberAdd, request: Request, limit: Optional[int] = Query(None, ge=1, le=LEDGER_MAX_LIMIT)):
    actor = ha_actor_name(request)
    with _lock, get_conn() as conn:
        group_name = group_name_or_404(conn, group_id)
        u = conn.execute("SELECT id, disabled FROM users WHERE id = ?", (payload.userId,)).fetchone()
        if not u:
            raise HTTPException(400, "Unknown user")
        if u["disabled"]:
            raise HTTPException(400, "This person is disabled")
        u_name = user_name(conn, payload.userId)
        already = conn.execute(
            "SELECT 1 FROM group_members WHERE group_id = ? AND user_id = ?", (group_id, payload.userId)
        ).fetchone()
        if not already:
            next_pos = conn.execute(
                "SELECT COALESCE(MAX(position), -1) + 1 AS p FROM group_members WHERE group_id = ?",
                (group_id,),
            ).fetchone()["p"]
            conn.execute(
                "INSERT INTO group_members (group_id, user_id, position) VALUES (?, ?, ?)",
                (group_id, payload.userId, next_pos),
            )
            log_event(
                conn,
                "member_added",
                f"{actor} added {u_name} to {group_name}",
                group_id=group_id,
                actor=actor,
            )
            conn.commit()
        return serialize_group(conn, group_id, limit)


# Text for a CSV cell. A leading = + - @ would make a spreadsheet run it as a formula, so it gets a '.
_csv_text = csv_export.text


@router.get("/groups/{group_id}/export.csv")
def export_group_csv(group_id: str):
    """Every expense and settle-up payment in one group, oldest first, with each member's share in its own
    column — for a spreadsheet. Anyone who can open the group can export it."""
    with get_conn() as conn:
        g = serialize_group(conn, group_id)
    currency = get_setting("currency")
    members = list(g["members"])
    known = {m["id"] for m in members}
    for e in g["expenses"]:                      # people who left the group but still have shares
        for s in e["splits"]:
            if s["userId"] not in known:
                known.add(s["userId"])
                members.append({"id": s["userId"], "name": s["name"]})
    header = (["Date", "Type", "Description", "Amount", "Currency", "Paid by", "Split"]
              + [_csv_text(f"{m['name']} share") for m in members])
    split_label = {"equal": "Equally", "custom": "Exact amounts", "percent": "Percentages", "payment": "Payment"}
    rows = []
    for e in sorted(g["expenses"], key=lambda x: (x["date"] or "", x["description"] or "")):
        shares = {s["userId"]: s["amount"] for s in e["splits"]}
        rows.append([e["date"], "Payment" if e["splitType"] == "payment" else "Expense", _csv_text(e["description"]),
                     f"{e['amount']:.2f}", currency, _csv_text(e["paidByName"]), split_label.get(e["splitType"], e["splitType"])]
                    + [f"{shares[m['id']]:.2f}" if m["id"] in shares else "" for m in members])
    safe = re.sub(r'[^A-Za-z0-9 ._-]+', "-", g["name"]).strip(" .-")[:60] or "group"
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return Response(csv_export.to_bytes(header, rows, bom=True), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{safe} - {stamp}.csv"',
                             "Cache-Control": "no-store"})


@router.delete("/groups/{group_id}", status_code=204)
def delete_group(group_id: str, request: Request, _admin: None = Depends(require_admin)):
    """Admin-only: deleting a group permanently removes every
    expense and payment in it, with no undo."""
    actor = ha_actor_name(request)
    with _lock, get_conn() as conn:
        group_name = group_name_or_404(conn, group_id)
        conn.execute("DELETE FROM groups WHERE id = ?", (group_id,))
        log_event(conn, "group_deleted", f"{actor} deleted group '{group_name}'", actor=actor)
        conn.commit()
    push_balances_to_ha()
    return Response(status_code=204)


@router.put("/groups/{group_id}/default")
def set_group_default(group_id: str, payload: GroupDefaultUpdate, request: Request, limit: Optional[int] = Query(None, ge=1, le=LEDGER_MAX_LIMIT)):
    """Marks (or unmarks) this group as the one the app opens straight into
    instead of the Dashboard. At most one group can be default at a time —
    setting one clears any other, enforced here and backstopped by a partial
    unique index (idx_groups_one_default) on the table."""
    actor = ha_actor_name(request)
    with _lock, get_conn() as conn:
        group_name = group_name_or_404(conn, group_id)
        if payload.isDefault:
            conn.execute("UPDATE groups SET is_default = 0 WHERE id != ?", (group_id,))
            conn.execute("UPDATE groups SET is_default = 1 WHERE id = ?", (group_id,))
            log_event(conn, "group_default_set", f"{actor} set '{group_name}' as the default group", group_id=group_id, actor=actor)
        else:
            conn.execute("UPDATE groups SET is_default = 0 WHERE id = ?", (group_id,))
            log_event(conn, "group_default_cleared", f"{actor} removed '{group_name}' as the default group", group_id=group_id, actor=actor)
        conn.commit()
        return serialize_group(conn, group_id, limit)


# -- expenses --
@router.post("/groups/{group_id}/expenses", status_code=201)
def add_expense(group_id: str, payload: ExpenseCreate, request: Request, background: BackgroundTasks):
    actor = ha_actor_name(request)
    description = payload.description.strip()
    if not description:
        raise HTTPException(400, "Description is required")
    date = resolve_expense_date(payload.date)
    with _lock, get_conn() as conn:
        group_name = group_name_or_404(conn, group_id)
        final_splits, split_type = build_splits(group_member_ids(conn, group_id), payload)
        expense_id = new_id()
        rounded_amt = round(payload.amount, 2)
        conn.execute(
            "INSERT INTO expenses (id, group_id, description, amount, paid_by, split_type, date) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (expense_id, group_id, description, rounded_amt, payload.paidBy, split_type, date),
        )
        write_splits(conn, expense_id, final_splits)
        paid_by_name = user_name(conn, payload.paidBy)
        event_message = f"{actor} added '{description}' ({money(rounded_amt)}) to {group_name}"
        if actor.lower() != paid_by_name.lower():
            event_message += f", paid by {paid_by_name}"
        log_event(conn, "expense_added", event_message, group_id=group_id, actor=actor)
        conn.commit()

    push_balances_to_ha()
    # after the response is sent: adding a charge never waits for (or fails because of) a notification
    background.add_task(notify_new_charge, expense_id, auth_core.identity(request).user_id, actor)
    return {
        "id": expense_id,
        "groupId": group_id,
        "description": description,
        "amount": rounded_amt,
        "paidBy": payload.paidBy,
        "splitType": split_type,
        "splits": final_splits,
        "date": date,
    }


@router.put("/expenses/{expense_id}")
def edit_expense(expense_id: str, payload: ExpenseCreate, request: Request, limit: Optional[int] = Query(None, ge=1, le=LEDGER_MAX_LIMIT)):
    """Edit an expense in place — description, amount, payer, split and date
    are all replaced (same body and validation as adding one). Settle-up
    payments aren't edited here: delete and re-record them instead."""
    actor = ha_actor_name(request)
    description = payload.description.strip()
    if not description:
        raise HTTPException(400, "Description is required")
    with _lock, get_conn() as conn:
        row = conn.execute(
            "SELECT group_id, description, amount, split_type, date FROM expenses WHERE id = ?", (expense_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Expense not found")
        if row["split_type"] == "payment":
            raise HTTPException(400, "Payments can't be edited — delete it and record it again")
        # Keep the stored timestamp when the day didn't change.
        same_day = not payload.date or parse_date(row["date"]).astimezone(tz()).date().isoformat() == payload.date[:10]
        date = row["date"] if same_day else resolve_expense_date(payload.date)
        final_splits, split_type = build_splits(group_member_ids(conn, row["group_id"]), payload)
        rounded_amt = round(payload.amount, 2)
        conn.execute(
            "UPDATE expenses SET description = ?, amount = ?, paid_by = ?, split_type = ?, date = ? WHERE id = ?",
            (description, rounded_amt, payload.paidBy, split_type, date, expense_id),
        )
        write_splits(conn, expense_id, final_splits)
        group_name = group_name_or_404(conn, row["group_id"])
        log_event(
            conn,
            "expense_edited",
            f"{actor} edited '{row['description']}' ({money(row['amount'])}) in {group_name}"
            + ("" if (description, rounded_amt) == (row["description"], row["amount"])
               else f" → '{description}' ({money(rounded_amt)})"),
            group_id=row["group_id"],
            actor=actor,
        )
        conn.commit()
        result = serialize_group(conn, row["group_id"], limit)
    push_balances_to_ha()
    return result


@router.post("/groups/{group_id}/payments", status_code=201)
def record_payment(group_id: str, payload: PaymentCreate, request: Request, background: BackgroundTasks,
                   limit: Optional[int] = Query(None, ge=1, le=LEDGER_MAX_LIMIT)):
    """Record that one member paid another back ("settle up"). Balances move
    exactly as if the payer had covered an expense entirely for the
    recipient, which cancels out what they owed."""
    actor = ha_actor_name(request)
    if payload.fromUserId == payload.toUserId:
        raise HTTPException(400, "Pick two different people")
    date = resolve_expense_date(payload.date)
    with _lock, get_conn() as conn:
        group_name = group_name_or_404(conn, group_id)
        members = set(group_member_ids(conn, group_id))
        if payload.fromUserId not in members or payload.toUserId not in members:
            raise HTTPException(400, "Both people must be members of this group")
        amt = round(payload.amount, 2)
        from_name, to_name = user_name(conn, payload.fromUserId), user_name(conn, payload.toUserId)
        expense_id = new_id()
        conn.execute(
            "INSERT INTO expenses (id, group_id, description, amount, paid_by, split_type, date) "
            "VALUES (?, ?, ?, ?, ?, 'payment', ?)",
            (expense_id, group_id, f"{from_name} paid {to_name}", amt, payload.fromUserId, date),
        )
        write_splits(conn, expense_id, [{"userId": payload.toUserId, "amount": amt}])
        log_event(
            conn,
            "payment_recorded",
            f"{actor} recorded {from_name} paying {to_name} {money(amt)} in {group_name}",
            group_id=group_id,
            actor=actor,
        )
        conn.commit()
        result = serialize_group(conn, group_id, limit)
    push_balances_to_ha()
    background.add_task(notify_payment, expense_id, auth_core.identity(request).user_id, actor)
    return result


@router.delete("/expenses/{expense_id}", status_code=204)
def delete_expense(expense_id: str, request: Request, _admin: None = Depends(require_admin)):
    """Admin-only: deleting an expense or a settle-up payment can't
    be undone. Everyone can still add and edit expenses and record payments."""
    actor = ha_actor_name(request)
    with _lock, get_conn() as conn:
        row = conn.execute(
            "SELECT description, amount, group_id FROM expenses WHERE id = ?", (expense_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Expense not found")
        conn.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))
        if row:
            group_row = conn.execute("SELECT name FROM groups WHERE id = ?", (row["group_id"],)).fetchone()
            group_name = group_row["name"] if group_row else "a group"
            log_event(
                conn,
                "expense_deleted",
                f"{actor} deleted '{row['description']}' ({money(row['amount'])}) from {group_name}",
                group_id=row["group_id"],
                actor=actor,
            )
        conn.commit()
    push_balances_to_ha()
    return Response(status_code=204)


@router.get("/health")
def health():
    return {"ok": True}


# -- activity log --
@router.get("/events")
def list_events(limit: int = Query(50, ge=1, le=300)):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT e.id, e.type, e.message, e.group_id, e.actor, e.created_at, g.name AS group_name "
            "FROM events e LEFT JOIN groups g ON g.id = e.group_id "
            "ORDER BY e.created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {
                "id": r["id"],
                "type": r["type"],
                "message": r["message"],
                "groupId": r["group_id"],
                "groupName": r["group_name"],
                "actor": r["actor"],
                "createdAt": r["created_at"],
            }
            for r in rows
        ]


# -- weekly / monthly transactions view --
@router.get("/transactions")
def list_transactions(period: Literal["week", "month"] = "week", offset: int = 0):
    with get_conn() as conn:
        start, end, label = week_range(offset) if period == "week" else month_range(offset)

        rows = conn.execute(
            "SELECT e.id, e.group_id, g.name AS group_name, e.description, e.amount, "
            "e.paid_by, e.date "
            "FROM expenses e JOIN groups g ON g.id = e.group_id "
            "WHERE e.split_type != 'payment' "   # paying someone back isn't spending
            "ORDER BY e.date DESC"
        ).fetchall()

        expenses = []
        for r in rows:
            dt = parse_date(r["date"])
            if start <= dt < end:
                expenses.append(
                    {
                        "id": r["id"],
                        "groupId": r["group_id"],
                        "groupName": r["group_name"],
                        "description": r["description"],
                        "amount": r["amount"],
                        "paidBy": r["paid_by"],
                        "paidByName": user_name(conn, r["paid_by"]),
                        "date": r["date"],
                    }
                )

        total = round(sum(e["amount"] for e in expenses), 2)
        breakdown = weekday_breakdown(expenses, start) if period == "week" else group_breakdown(expenses)

        return {
            "period": period,
            "offset": offset,
            "label": label,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "total": total,
            "count": len(expenses),
            "expenses": expenses,
            "breakdown": breakdown,
        }


# -- dashboard summary --
def compute_overall_net(conn) -> list:
    """Each person's net balance summed across every group they belong to.
    Intentionally NOT run through the debt-simplifying transfer algorithm
    globally — two people who never shared an expense could get matched up
    incorrectly if we netted across unrelated groups."""
    overall: dict = {}
    names: dict = {}
    groups = conn.execute("SELECT id FROM groups").fetchall()
    for g in groups:
        member_ids = group_member_ids(conn, g["id"])
        expense_rows = conn.execute(
            "SELECT id, paid_by, amount FROM expenses WHERE group_id = ?", (g["id"],)
        ).fetchall()
        expenses = []
        for e in expense_rows:
            expenses.append(
                {
                    "paidBy": e["paid_by"],
                    "amount": e["amount"],
                    "splits": fetch_expense_splits(conn, e["id"]),
                }
            )
        net, _ = compute_balances(member_ids, expenses)
        for uid, amt in net.items():
            overall[uid] = overall.get(uid, 0.0) + amt
            if uid not in names:
                names[uid] = user_name(conn, uid)

    return sorted(
        (
            {"userId": uid, "name": names[uid], "amount": round(amt, 2)}
            for uid, amt in overall.items()
        ),
        key=lambda x: -x["amount"],
    )


def build_dashboard_data(conn) -> dict:
    people_count = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
    groups_count = conn.execute("SELECT COUNT(*) AS c FROM groups").fetchone()["c"]
    expenses_count = conn.execute("SELECT COUNT(*) AS c FROM expenses WHERE split_type != 'payment'").fetchone()["c"]

    overall_net = compute_overall_net(conn)

    week_start, week_end, _ = week_range(0)
    month_start, month_end, _ = month_range(0)
    all_dates = conn.execute("SELECT amount, date FROM expenses WHERE split_type != 'payment'").fetchall()
    week_count = month_count = 0
    week_sum = month_sum = 0.0
    for r in all_dates:
        dt = parse_date(r["date"])
        if week_start <= dt < week_end:
            week_sum += r["amount"]
            week_count += 1
        if month_start <= dt < month_end:
            month_sum += r["amount"]
            month_count += 1

    return {
        "peopleCount": people_count,
        "groupsCount": groups_count,
        "expensesCount": expenses_count,
        "overallNet": overall_net,
        "week": {"total": round(week_sum, 2), "count": week_count},
        "month": {"total": round(month_sum, 2), "count": month_count},
    }


@router.get("/dashboard")
def dashboard():
    with get_conn() as conn:
        return build_dashboard_data(conn)


# -- Home Assistant integration --
def splitpot_user_for(request: Request) -> Optional[str]:
    """The Splitpot person (users.id) for whoever is signed in to Home
    Assistant, or None. Matched by HA user id (Person → attributes.user_id,
    stored in users.ha_user_id), which is unique and stable; if that's not
    known yet (e.g. Home Assistant hasn't been reachable), by display name — but only
    when exactly one enabled person has that name. Disabled people are never
    returned. Used to preselect "Acting as" and "Paid by"."""
    uid = (request.headers.get("x-remote-user-id") or "").strip().lower()
    display = (request.headers.get("x-remote-user-display-name") or "").strip()
    with get_conn() as conn:
        if uid:
            row = conn.execute(
                "SELECT id FROM users WHERE ha_user_id = ? AND disabled = 0", (uid,)
            ).fetchone()
            if row:
                return row["id"]
            # Not stored yet: the Person list (cached) may still know the link.
            entity = next((p["entity_id"] for p in cached_ha_persons()
                           if (p.get("user_id") or "").strip().lower() == uid), None)
            if entity:
                row = conn.execute(
                    "SELECT id FROM users WHERE ha_entity_id = ? AND disabled = 0", (entity,)
                ).fetchone()
                if row:
                    return row["id"]
        if display:
            rows = conn.execute(
                "SELECT id FROM users WHERE name = ? COLLATE NOCASE AND disabled = 0", (display,)
            ).fetchall()
            if len(rows) == 1:
                return rows[0]["id"]
    return None


@router.get("/whoami")
def whoami(request: Request):
    """Every request reaching here has already passed the ingress auth
    middleware, so these headers (attached by HA's Supervisor) are always
    present for a real request."""
    # Also drives the "How the app sees you" page. Only the caller's own
    # identity values and a COUNT of admin_users entries go out — never the
    # list itself and never the request's headers wholesale. This is always the
    # real signed-in person, not whoever the "Acting as" picker is set to.
    admin = is_admin(request)
    me = linked_user_id(request.headers.get("x-remote-user-id"))
    linked = False
    if me:
        with get_conn() as conn:
            row = conn.execute("SELECT id, ha_user_id FROM users WHERE id = ?", (me,)).fetchone()
            linked = bool(row and person_services(conn, row))
    return whoami_core.build(
        request, user_id=request.headers.get("x-remote-user-id"), username=request.headers.get("x-remote-user-name"),
        display_name=request.headers.get("x-remote-user-display-name"), is_admin=admin,
        admin_entries=len(ADMIN_NAMES), display_name_only=(not admin) and display_name_listed(request),
        notify_linked=linked, extras=[whoami_core.notify_row(linked)],
        # for the app's own code: the Splitpot person this HA user is, and the sensor switches
        userId=splitpot_user_for(request), sensorSyncEnabled=sensor_sync_enabled(), haConnected=ha_connected())


@router.get("/config")
def app_config():
    """Display settings the frontend needs before rendering any amount."""
    return {"currency": get_setting("currency")}


@router.get("/admin/settings")
def get_admin_settings(_admin: None = Depends(require_admin)):
    """Admin → App settings."""
    return settings_payload()


@router.put("/admin/settings")
def put_admin_settings(request: Request, payload: dict = Body(...), _admin: None = Depends(require_admin)):
    """Any subset of the settings; 422 with a readable message on unknown
    keys or bad values (and nothing is saved then)."""
    before = settings_all()
    try:
        after = update_settings(payload, ha_actor_name(request))
    except SettingsError as e:
        raise HTTPException(422, str(e))
    # Make the change visible in Home Assistant now rather than on the next
    # tick. update_settings has closed its connection and released the lock,
    # so these network calls hold neither.
    if before["ha_sync_enabled"] and not after["ha_sync_enabled"]:
        remove_balance_sensors()
    elif after["ha_sync_enabled"] and (not before["ha_sync_enabled"] or after["currency"] != before["currency"]):
        push_balances_to_ha()   # switched on, or the sensors' unit changed
    return settings_payload()


@router.get("/admin/backup-db")
def admin_backup_db(_admin: None = Depends(require_admin)):
    """Admin-only: downloads a full, consistent snapshot of the app's
    entire SQLite database — for a manual backup or to open with an
    external SQLite tool.

    Deliberately not a plain file copy of DB_FILE: sqlite3's own online
    backup API (Connection.backup()) produces one complete, internally
    consistent file even while the database is actively taking writes,
    which a raw copy can't guarantee."""
    fd, tmp_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    with _lock, get_conn() as src:
        db_core.snapshot(src, tmp_path)
    return backup_core.send_file(tmp_path, backup_core.file_name("splitpot-backup", ".db", now=local_now()))


@router.post("/admin/import-db")
async def admin_storage_import_db(file: UploadFile = File(...), _admin: None = Depends(require_admin)):
    """Admin-only: restores the entire database from a .db file previously
    produced by the Download button above — meant for "I reinstalled the
    app / lost the volume, here's my last backup" recovery, not routine
    use. This REPLACES every group, expense and person currently stored;
    there is no merge and no undo once it succeeds.

    The upload is written to a scratch file and validated (readable
    SQLite, passes an integrity check, has every table this app expects)
    before anything about the live database is touched, so a garbage or
    wrong-app upload is rejected with an error instead of destroying the
    current data. Only once validation passes does it swap the file in —
    under the same write lock every other mutation uses, with the old
    WAL/SHM sidecar files removed so nothing tries to replay stale WAL
    frames against the freshly-swapped file (see db in WAL mode, above).

    The scratch file is created inside DATA_DIR (not the default /tmp)
    specifically so the final os.replace() below is a same-filesystem
    rename: /tmp and /data are typically separate mounts in the app's
    container (Supervisor bind-mounts /data as its own volume), and
    os.replace() can't do a cross-device move — it fails with
    "OSError: [Errno 18] Cross-device link" if the two paths aren't on
    the same filesystem."""
    tmp_path = await backup_core.receive(file, DATA_DIR)
    try:
        try:
            tables = db_core.validate_file(
                tmp_path, {"users", "groups", "group_members", "expenses", "expense_splits", "events"},
                app_name="Splitpot")
        except ValueError as e:
            raise HTTPException(400, str(e))
        backup_has_settings = "app_settings" in tables

        with _lock:
            # A backup from an older database has no App settings; keep the
            # current ones rather than silently falling back to the defaults.
            carry_settings = []
            if not backup_has_settings:
                try:
                    with get_conn() as c:
                        carry_settings = [tuple(r) for r in c.execute(
                            "SELECT key, value, updated_at, updated_by FROM app_settings").fetchall()]
                except sqlite3.Error:
                    carry_settings = []
            # An older backup may lack columns the app now uses (see
            # init_db's migrations) — init_db brings it up to date now
            # instead of serving errors until the next restart.
            backup_core.restore_file(tmp_path, DB_FILE, get_conn, migrate=init_db)
            if carry_settings:
                with get_conn() as c:
                    c.executemany(
                        "INSERT OR IGNORE INTO app_settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, ?)",
                        carry_settings,
                    )
                    c.commit()
                invalidate_settings_cache()
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    return {"status": "ok"}


app.include_router(router)
init_db()

# Serve the frontend. Must be mounted last so it doesn't swallow /api routes.
app.mount("/", StaticFiles(directory=str(Path(__file__).parent / "static"), html=True), name="static")
