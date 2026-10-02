import asyncio
import csv
import io
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
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Literal, Optional
from zoneinfo import ZoneInfo

import tempfile

from fastapi import APIRouter, Body, Depends, FastAPI, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from starlette.background import BackgroundTask

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
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
SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN")

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
_DEFAULT_TZ_NAME = "UTC"
_tz: ZoneInfo | timezone = timezone.utc
_tz_name = _DEFAULT_TZ_NAME


def set_timezone(name: str) -> bool:
    """Switch week_range()/month_range() to `name` (an IANA zone like
    'Europe/Berlin'). Returns False (and keeps the current zone) if `name`
    isn't recognized."""
    global _tz, _tz_name
    try:
        _tz = ZoneInfo(name)
        _tz_name = name
        return True
    except Exception as e:  # ZoneInfoNotFoundError, ValueError, ...
        logger.warning("Unknown time zone %r (%s); staying on %s", name, e, _tz_name)
        return False


def local_now() -> datetime:
    """Current time as an aware datetime in Home Assistant's time zone."""
    return datetime.now(timezone.utc).astimezone(_tz)


def fetch_ha_timezone() -> str | None:
    """Home Assistant's configured time zone name (GET /config), or None if
    it can't be read. Blocking — call via asyncio.to_thread."""
    if not SUPERVISOR_TOKEN:
        return None
    req = urllib.request.Request(
        "http://supervisor/core/api/config",
        headers={"Authorization": f"Bearer {SUPERVISOR_TOKEN}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read()).get("time_zone")
    except (urllib.error.URLError, OSError, json.JSONDecodeError, AttributeError) as e:
        logger.warning("Could not read Home Assistant's time zone: %s", e)
        return None

# Home Assistant user ids / login names allowed into the Admin area (App
# settings, Users, Storage) and to delete. Empty by default (deny by
# default) rather than trying to infer real HA admin status, which would
# need extra Supervisor API scope — a static allowlist in the app options is
# enough for a household-sized, rarely-changing set of people.
ADMIN_NAMES = {str(n).strip().lower() for n in (HA_OPTIONS.get("admin_users") or []) if str(n).strip()}


_warned_display_name_only: set = set()


def display_name_listed(request: Request) -> bool:
    name = (request.headers.get("x-remote-user-display-name") or "").strip().lower()
    return bool(name) and name in ADMIN_NAMES


def is_admin(request: Request) -> bool:
    """Admin by user id or login name only — the two identifiers Supervisor
    sets that nobody picks for themselves. Display names are deliberately
    NOT matched (the same rule as Household Todo and Calorie Tracker):
    they aren't unique or stable, so matching them could make someone an
    admin just by having an admin's name. Someone listed only by display name
    gets a one-time warning in the log naming the id to list instead."""
    uid = (request.headers.get("x-remote-user-id") or "").strip().lower()
    username = (request.headers.get("x-remote-user-name") or "").strip().lower()
    if {c for c in (uid, username) if c} & ADMIN_NAMES:
        return True
    if display_name_listed(request) and uid not in _warned_display_name_only:
        _warned_display_name_only.add(uid)
        logger.warning(
            "%r is listed in admin_users by display name only, which isn't accepted — list their "
            "user id %s%s instead, then restart the app.",
            request.headers.get("x-remote-user-display-name"), uid,
            f" or login name {username!r}" if username else "",
        )
    return False


def no_admin_yet() -> bool:
    """True while admin_users is empty (a fresh install): nobody is an
    administrator until someone is listed and the app restarted."""
    return not ADMIN_NAMES


def require_admin(request: Request) -> None:
    """Route dependency for anything actually admin-gated (403s a non-admin)
    — as opposed to just branching what the frontend SHOWS someone, which
    doesn't block the request on its own."""
    if not is_admin(request):
        raise HTTPException(403, "Only designated admins can do this. See the admin_users option in the app's Configuration tab.")


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
    pushed = 0
    for entity_id, state, attributes in states:   # network calls after the connection is closed
        pushed += ha_set_state(entity_id, state, attributes)
    return pushed


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
    removed = sum(ha_delete_state(eid) for eid in sorted(candidates))
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


@contextmanager
def get_conn():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_FILE, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()


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

            CREATE INDEX IF NOT EXISTS idx_group_members_group ON group_members(group_id);
            CREATE INDEX IF NOT EXISTS idx_expenses_group ON expenses(group_id);
            CREATE INDEX IF NOT EXISTS idx_expense_splits_expense ON expense_splits(expense_id);
            CREATE INDEX IF NOT EXISTS idx_expenses_date ON expenses(date);
            CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at);
            """
        )
        # Older databases may lack columns added to the tables above
        # (CREATE TABLE IF NOT EXISTS won't retroactively add columns).
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
        if "ha_entity_id" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN ha_entity_id TEXT")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_ha_entity ON users(ha_entity_id) "
            "WHERE ha_entity_id IS NOT NULL"
        )
        event_cols = [r["name"] for r in conn.execute("PRAGMA table_info(events)").fetchall()]
        if "actor" not in event_cols:
            conn.execute("ALTER TABLE events ADD COLUMN actor TEXT")
        if "disabled" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN disabled INTEGER NOT NULL DEFAULT 0")
        if "ha_user_id" not in cols:   # the HA login behind each Person
            conn.execute("ALTER TABLE users ADD COLUMN ha_user_id TEXT")
        split_cols = [r["name"] for r in conn.execute("PRAGMA table_info(expense_splits)").fetchall()]
        if "percent" not in split_cols:
            conn.execute("ALTER TABLE expense_splits ADD COLUMN percent REAL")
        group_cols = [r["name"] for r in conn.execute("PRAGMA table_info(groups)").fetchall()]
        if "is_default" not in group_cols:
            conn.execute("ALTER TABLE groups ADD COLUMN is_default INTEGER NOT NULL DEFAULT 0")
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
SETTINGS_DEFAULTS = {"ha_sync_enabled": True, "sync_interval_minutes": 5, "currency": "USD"}
SETTINGS_LABELS = {
    "ha_sync_enabled": "Sync balances to Home Assistant",
    "sync_interval_minutes": "Sync interval (minutes)",
    "currency": "Currency code",
}
# All apply live: money()/the sensors read the currency when they run, the
# sync loop re-reads ha_sync_enabled and the interval every tick, and the
# settings route pushes or removes the sensors straight away.
SETTINGS_META = {key: {"restartRequired": False} for key in SETTINGS_DEFAULTS}

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


class AppSettings(BaseModel):
    """Validation for every App setting (unknown keys are rejected)."""
    model_config = ConfigDict(extra="forbid")

    ha_sync_enabled: bool = Field(True, strict=True)
    sync_interval_minutes: int = Field(5, ge=1, le=60, strict=True)
    currency: str = "USD"

    @field_validator("currency", mode="before")
    @classmethod
    def _currency_code(cls, v):
        if not isinstance(v, str):
            raise ValueError(_CURRENCY_HELP)
        code = v.strip().upper()
        if not re.fullmatch(r"[A-Z]{3}", code):
            raise ValueError(_CURRENCY_HELP)
        if code not in ISO_4217_CODES:
            raise ValueError(f"{code} isn't an ISO 4217 currency code")
        return code


class SettingsError(ValueError):
    """A bad settings update; the message is shown to the admin as-is (422)."""


def _settings_error_text(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors():
        key = str(err["loc"][0]) if err.get("loc") else ""
        msg = str(err.get("msg", "invalid value")).removeprefix("Value error, ")
        label = SETTINGS_LABELS.get(key, key)
        parts.append(f"{label}: {msg}" if label else msg)
    return "; ".join(parts) or "Invalid settings"


def _validate_one(key: str, value):
    """`value` validated as setting `key` (raises ValidationError)."""
    return getattr(AppSettings.model_validate({**SETTINGS_DEFAULTS, key: value}), key)


def _stored_settings(conn) -> dict:
    """The effective values: defaults overlaid with valid stored rows."""
    values = dict(SETTINGS_DEFAULTS)
    for row in conn.execute("SELECT key, value FROM app_settings").fetchall():
        key = row["key"]
        if key not in SETTINGS_DEFAULTS:
            continue
        try:
            values[key] = _validate_one(key, json.loads(row["value"]))
        except (json.JSONDecodeError, ValidationError):
            logger.warning("Ignoring an invalid stored %s setting (%r); using %r", key, row["value"], values[key])
    return values


# Read on hot paths (every money() call, every sync tick), so cached in
# memory and invalidated on every write (and whenever init_db runs, e.g.
# after a restore). The generation counter stops a read that raced a write
# from caching the old values.
_settings_cache: Optional[dict] = None
_settings_generation = 0
_settings_cache_lock = threading.Lock()


def invalidate_settings_cache() -> None:
    global _settings_cache, _settings_generation
    with _settings_cache_lock:
        _settings_cache = None
        _settings_generation += 1


def settings_all() -> dict:
    global _settings_cache
    with _settings_cache_lock:
        if _settings_cache is not None:
            return dict(_settings_cache)
        generation = _settings_generation
    with get_conn() as conn:
        values = _stored_settings(conn)
    with _settings_cache_lock:
        if generation == _settings_generation:
            _settings_cache = values
    return dict(values)


def get_setting(key: str):
    return settings_all()[key]


def update_settings(partial: dict, user: Optional[str]) -> dict:
    """Validate the merged result, write only the keys that changed, and
    return the new values. Raises SettingsError (-> 422) on unknown keys or
    bad values; nothing is written in that case."""
    if not isinstance(partial, dict):
        raise SettingsError("Send the settings as a JSON object")
    unknown = sorted(k for k in partial if k not in SETTINGS_DEFAULTS)
    if unknown:
        raise SettingsError(f"Unknown setting{'s' if len(unknown) > 1 else ''}: {', '.join(unknown)}")
    with _lock, get_conn() as conn:
        current = _stored_settings(conn)
        try:
            merged = AppSettings.model_validate({**current, **partial}).model_dump()
        except ValidationError as e:
            raise SettingsError(_settings_error_text(e)) from None
        changed = [k for k in SETTINGS_DEFAULTS if merged[k] != current[k]]
        now = now_iso()
        for key in changed:
            conn.execute(
                "INSERT INTO app_settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at, "
                "updated_by = excluded.updated_by",
                (key, json.dumps(merged[key]), now, user),
            )
        if changed:
            def describe(k):
                if k == "ha_sync_enabled":
                    return f"sensor sync {'on' if merged[k] else 'off'}"
                if k == "sync_interval_minutes":
                    return f"sync interval {current[k]} → {merged[k]} min"
                return f"currency {current[k]} → {merged[k]}"
            what = ", ".join(describe(k) for k in changed)
            log_event(conn, "settings_changed", f"{user or 'Someone'} changed the app settings: {what}", actor=user)
        conn.commit()
        invalidate_settings_cache()
    return merged


def settings_payload() -> dict:
    return {
        "values": settings_all(),
        "defaults": dict(SETTINGS_DEFAULTS),
        "meta": {k: dict(v) for k, v in SETTINGS_META.items()},
    }


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


def serialize_group(conn, group_id: str) -> dict:
    g = conn.execute("SELECT id, name, created_at, is_default FROM groups WHERE id = ?", (group_id,)).fetchone()
    if not g:
        raise HTTPException(404, "Group not found")

    member_ids = group_member_ids(conn, group_id)

    expense_rows = conn.execute(
        "SELECT id, description, amount, paid_by, split_type, date FROM expenses "
        "WHERE group_id = ? ORDER BY date DESC",
        (group_id,),
    ).fetchall()

    expenses = []
    for e in expense_rows:
        splits = fetch_expense_splits(conn, e["id"])
        expenses.append(
            {
                "id": e["id"],
                "groupId": group_id,
                "description": e["description"],
                "amount": e["amount"],
                "paidBy": e["paid_by"],
                "paidByName": user_name(conn, e["paid_by"]),
                "splitType": e["split_type"],
                "date": e["date"],
                "splits": [{**s, "name": user_name(conn, s["userId"])} for s in splits],
            }
        )

    net, transfers = compute_balances(member_ids, expenses)

    return {
        "id": g["id"],
        "name": g["name"],
        "createdAt": g["created_at"],
        "isDefault": bool(g["is_default"]),
        "memberIds": member_ids,
        "members": [{"id": m, "name": user_name(conn, m)} for m in member_ids],
        "expenses": expenses,
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
    net = {k: round(v, 2) for k, v in net.items()}

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
        return d.replace(tzinfo=_tz)
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
    start = datetime(year, month, 1, tzinfo=_tz)
    end = datetime(year + 1, 1, 1, tzinfo=_tz) if month == 12 else datetime(year, month + 1, 1, tzinfo=_tz)
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


# ---------- app ----------
@asynccontextmanager
async def lifespan(app: FastAPI):
    name = await asyncio.to_thread(fetch_ha_timezone)
    if name and set_timezone(name):
        logger.info("Using Home Assistant's time zone: %s", name)
    else:
        logger.warning("Could not read Home Assistant's time zone — staying on the %s default.", _tz_name)

    sync_task = None
    if ha_connected():   # sync on/off is checked live inside the loop
        sync_task = asyncio.create_task(periodic_sync())
    yield
    if sync_task:
        sync_task.cancel()


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
_INGRESS_ALLOWED_HOSTS = {"172.30.32.2", "127.0.0.1", "::1"}


@app.middleware("http")
async def require_ha_ingress_auth(request: Request, call_next):
    client_host = request.client.host if request.client else None
    if client_host not in _INGRESS_ALLOWED_HOSTS:
        return JSONResponse(status_code=403, content={"detail": "Forbidden — access only via Home Assistant"})
    if request.url.path.startswith("/api/") and not request.headers.get("x-remote-user-id"):
        return JSONResponse(status_code=401, content={"detail": "Not authenticated with Home Assistant"})
    return await call_next(request)


@app.middleware("http")
async def no_cache_html_shell(request: Request, call_next):
    """The HTML shell (index.html, served for "/") must never be cached by
    the browser — it's what points at the versioned style.css?v=.../app.js?v=...
    below, so a stale cached copy of JUST this one file is enough to make a
    rebuilt app look like the update never took effect, even after a
    normal refresh. Static assets themselves aren't touched here: their
    cache-busting query string (bumped alongside config.yaml's version) is
    what invalidates them, so they can keep normal caching."""
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.endswith(".html"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
    return response


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
            "SELECT u.id, u.name, u.created_at, u.disabled, u.ha_entity_id, "
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


@router.get("/admin/users")
def admin_list_users(_admin: None = Depends(require_admin)):
    """Admin → Users: the same people plus what an admin needs to manage them."""
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "createdAt": r["created_at"],
            "disabled": bool(r["disabled"]),
            "haEntityId": r["ha_entity_id"],
            "groupCount": r["group_count"],
        }
        for r in synced_user_rows()
    ]


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


@router.get("/groups/{group_id}")
def get_group(group_id: str):
    with get_conn() as conn:
        return serialize_group(conn, group_id)


@router.post("/groups/{group_id}/members")
def add_member(group_id: str, payload: MemberAdd, request: Request):
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
        return serialize_group(conn, group_id)


_CSV_FORMULA = ("=", "+", "-", "@", "\t", "\r")


def _csv_text(v) -> str:
    """Text for a CSV cell. A leading = + - @ would make a spreadsheet run it as a formula, so it gets a '."""
    s = "" if v is None else str(v)
    return "'" + s if s.startswith(_CSV_FORMULA) else s


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
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Date", "Type", "Description", "Amount", "Currency", "Paid by", "Split"]
               + [f"{m['name']} share" for m in members])
    split_label = {"equal": "Equally", "custom": "Exact amounts", "percent": "Percentages", "payment": "Payment"}
    for e in sorted(g["expenses"], key=lambda x: (x["date"] or "", x["description"] or "")):
        shares = {s["userId"]: s["amount"] for s in e["splits"]}
        w.writerow([e["date"], "Payment" if e["splitType"] == "payment" else "Expense", _csv_text(e["description"]),
                    f"{e['amount']:.2f}", currency, _csv_text(e["paidByName"]), split_label.get(e["splitType"], e["splitType"])]
                   + [f"{shares[m['id']]:.2f}" if m["id"] in shares else "" for m in members])
    safe = re.sub(r'[^A-Za-z0-9 ._-]+', "-", g["name"]).strip(" .-")[:60] or "group"
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return Response(buf.getvalue().encode("utf-8-sig"), media_type="text/csv; charset=utf-8",
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
def set_group_default(group_id: str, payload: GroupDefaultUpdate, request: Request):
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
        return serialize_group(conn, group_id)


# -- expenses --
@router.post("/groups/{group_id}/expenses", status_code=201)
def add_expense(group_id: str, payload: ExpenseCreate, request: Request):
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
def edit_expense(expense_id: str, payload: ExpenseCreate, request: Request):
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
        same_day = not payload.date or parse_date(row["date"]).astimezone(_tz).date().isoformat() == payload.date[:10]
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
        result = serialize_group(conn, row["group_id"])
    push_balances_to_ha()
    return result


@router.post("/groups/{group_id}/payments", status_code=201)
def record_payment(group_id: str, payload: PaymentCreate, request: Request):
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
        result = serialize_group(conn, group_id)
    push_balances_to_ha()
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
    return {
        "userId": splitpot_user_for(request),
        "haUserId": request.headers.get("x-remote-user-id"),
        "haUsername": request.headers.get("x-remote-user-name"),
        "haDisplayName": request.headers.get("x-remote-user-display-name"),
        "nameSent": bool(request.headers.get("x-remote-user-name")),
        "viaIngress": "x-ingress-path" in request.headers,
        "sensorSyncEnabled": sensor_sync_enabled(),
        "haConnected": ha_connected(),
        "isAdmin": is_admin(request),
        "displayNameOnly": (not is_admin(request)) and display_name_listed(request),
        "adminEntries": len(ADMIN_NAMES),
        # First run: admin_users is empty, so nobody can open App settings.
        # The frontend shows a "No admin yet" banner on every page. Nobody is
        # ever promoted automatically.
        "noAdminYet": no_admin_yet(),
    }


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
        dst = sqlite3.connect(tmp_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
    filename = f"splitpot-backup-{local_now().strftime('%Y%m%d-%H%M%S')}.db"
    return FileResponse(
        tmp_path,
        media_type="application/vnd.sqlite3",
        filename=filename,
        background=BackgroundTask(os.remove, tmp_path),
    )


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
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(suffix=".db", dir=DATA_DIR)
    try:
        with os.fdopen(fd, "wb") as out:
            while chunk := await file.read(1024 * 1024):
                out.write(chunk)

        try:
            check_conn = sqlite3.connect(tmp_path)
            try:
                integrity = check_conn.execute("PRAGMA integrity_check").fetchone()[0]
                if integrity != "ok":
                    raise HTTPException(400, f"That file failed a database integrity check ({integrity}) — refusing to import it.")
                tables = {r[0] for r in check_conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                missing = set(['users', 'groups', 'group_members', 'expenses', 'expense_splits', 'events']) - tables
                if missing:
                    raise HTTPException(400, f"That doesn't look like a Splitpot database (missing tables: {', '.join(sorted(missing))}).")
                backup_has_settings = "app_settings" in tables
            finally:
                check_conn.close()
        except sqlite3.DatabaseError:
            raise HTTPException(400, "That file isn't a valid SQLite database.")

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
            try:
                with get_conn() as c:
                    c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:
                pass
            os.replace(tmp_path, DB_FILE)
            for ext in ("-wal", "-shm"):
                sidecar = Path(str(DB_FILE) + ext)
                if sidecar.exists():
                    sidecar.unlink()
            # An older backup may lack columns the app now uses (see
            # init_db's migrations) — bring it up to date now instead of
            # serving errors until the next restart.
            init_db()
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
app.mount("/", StaticFiles(directory="public", html=True), name="static")
