"""App settings: stored in the database and edited by admins under
Admin → App settings. Every reader calls `get(key)` at the moment it needs
the value, so a change applies without a restart. (The daily digest time is
not here: each person has their own, `user_prefs.digest_time`.)

`admin_users` is deliberately NOT here — it stays on the app's
Configuration tab, because it is how the first admin is known (and you have to
be an admin to open App settings).

Values are stored as JSON in `app_settings` (db.SCHEMA), one row per key
that differs from nothing — a key without a row uses DEFAULTS. Reads go
through a small in-memory cache that is dropped on every write and whenever
the database file is (re)initialised (a restore swaps the whole file).
"""
import json
import logging
import re
import threading

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from . import config, db

logger = logging.getLogger("settings")

# Public OpenStreetMap services — a blank osrm_url / nominatim_url falls back
# to these.
DEFAULT_OSRM_URL = "https://router.project-osrm.org"
DEFAULT_NOMINATIM_URL = "https://nominatim.openstreetmap.org"

DEFAULTS: dict = {
    "expose_schedule_sensors": True,
    "sensor_refresh_minutes": 5,
    # Drive times send the home address and every place's address to the address
    # lookup and routing servers (public OpenStreetMap services by default), so a
    # new install starts with them off. db._migrate turns this on for an older
    # database that already uses them (a home address or a computed drive time).
    "drive_times_enabled": False,
    "home_address": "",
    "osrm_url": DEFAULT_OSRM_URL,
    "nominatim_url": DEFAULT_NOMINATIM_URL,
    "avoid_tolls": True,
    # Maintenance (Admin → Maintenance; the files folder is in App settings)
    "maintenance_enabled": False,
    "maintenance_recipients": [],       # HA user ids: who gets maintenance notifications by default
    "maintenance_profile": [],          # maint_catalog.FEATURES keys: what the house has
    "maintenance_sensor": False,        # sensor.household_todo_maintenance_overdue
    "maintenance_files_path": "",       # a folder inside /share; "" = attaching files is off
}
KEYS = tuple(DEFAULTS)

# Every key applies live (see the readers); none needs a restart.
META: dict = {k: {"restartRequired": False} for k in KEYS}

_URL_RE = re.compile(r"^https?://[^\s/?#]+[^\s]*$", re.IGNORECASE)


class SettingsError(ValueError):
    """A readable 422 message."""


class AppSettings(BaseModel):
    """Validation ranges for every setting. Strict
    types: "5" is not a number and 1 is not a boolean."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, strict=True)

    expose_schedule_sensors: bool
    sensor_refresh_minutes: int = Field(ge=1, le=1440)
    drive_times_enabled: bool
    home_address: str = Field(max_length=300)
    osrm_url: str = Field(max_length=500)
    nominatim_url: str = Field(max_length=500)
    avoid_tolls: bool
    maintenance_enabled: bool
    maintenance_recipients: list[str] = Field(max_length=100)
    maintenance_profile: list[str] = Field(max_length=60)
    maintenance_sensor: bool
    maintenance_files_path: str = Field(max_length=400)

    @field_validator("maintenance_recipients")
    @classmethod
    def _ids(cls, v: list[str]) -> list[str]:
        out = []
        for x in v:
            x = x.strip()
            if not x or len(x) > 200:
                raise ValueError("must be a list of user ids")
            if x not in out:
                out.append(x)
        return out

    @field_validator("maintenance_profile")
    @classmethod
    def _features(cls, v: list[str]) -> list[str]:
        from .maint_catalog import FEATURES
        bad = [x for x in v if x not in FEATURES]
        if bad:
            raise ValueError("unknown feature(s): " + ", ".join(bad))
        return [k for k in FEATURES if k in v]          # canonical order, no duplicates

    @field_validator("osrm_url", "nominatim_url")
    @classmethod
    def _http_url(cls, v: str) -> str:
        # blank = use the public default
        if v and not _URL_RE.match(v):
            raise ValueError("must be an http:// or https:// URL, or blank for the default")
        return v.rstrip("/") if v else v


_LABELS = {
    "expose_schedule_sensors": "Expose schedule items to Home Assistant",
    "sensor_refresh_minutes": "Sensor refresh (minutes)",
    "drive_times_enabled": "Drive times (uses OpenStreetMap services)",
    "home_address": "Home address",
    "osrm_url": "Routing server (OSRM)",
    "nominatim_url": "Address lookup server (Nominatim)",
    "avoid_tolls": "Avoid toll roads",
    "maintenance_enabled": "Maintenance",
    "maintenance_recipients": "Who gets maintenance notifications",
    "maintenance_profile": "Home profile",
    "maintenance_sensor": "Overdue maintenance sensor",
    "maintenance_files_path": "Maintenance files folder",
}


def _readable(e: ValidationError) -> str:
    parts = []
    for err in e.errors():
        key = str(err["loc"][0]) if err.get("loc") else "settings"
        msg = err.get("msg", "invalid value")
        msg = msg.replace("Value error, ", "")
        parts.append(f"{_LABELS.get(key, key)}: {msg}")
    return "; ".join(parts) or "Invalid settings."


def validate(values: dict) -> dict:
    """Full set of values -> normalised dict. Raises SettingsError."""
    try:
        return AppSettings(**values).model_dump()
    except ValidationError as e:
        raise SettingsError(_readable(e)) from None
    except TypeError as e:  # e.g. a non-string key
        raise SettingsError(str(e)) from None


# ---------------------------------------------------------------------------
# cache
# ---------------------------------------------------------------------------
_lock = threading.Lock()
_cache: dict | None = None
_cache_gen: int = -1
_version = 0        # bumped by every invalidate(); a load that raced a write isn't cached


def invalidate() -> None:
    global _cache, _version
    with _lock:
        _cache = None
        _version += 1


def _load() -> dict:
    """DEFAULTS overlaid with the stored rows. A stored value that no longer
    validates (hand-edited DB, a restore from a future version) is ignored
    with a warning rather than taking the app down."""
    values = {k: (list(v) if isinstance(v, list) else v) for k, v in DEFAULTS.items()}
    try:
        with db.get_conn() as conn:
            rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
    except Exception:   # table missing mid-restore, etc. — defaults are safe
        logger.exception("Could not read app settings; using defaults")
        return values
    for r in rows:
        if r["key"] not in DEFAULTS:
            continue
        try:
            candidate = dict(values, **{r["key"]: json.loads(r["value"])})
            values = validate(candidate)
        except (ValueError, SettingsError) as e:
            logger.warning("Ignoring stored setting %s: %s", r["key"], e)
    return values


def all() -> dict:  # noqa: A001 — the design's name for it
    """All current values. Cached; the cache is only filled if no write (and
    no database swap) happened while the rows were being read, so a reader
    racing an admin's save can never pin the old values."""
    global _cache, _cache_gen
    gen = db.generation()
    with _lock:
        if _cache is not None and _cache_gen == gen:
            return dict(_cache)
        ver = _version
    values = _load()
    with _lock:
        if _version == ver and db.generation() == gen:
            _cache, _cache_gen = values, gen
    return dict(values)


def get(key: str):
    return all()[key]


def update(partial, user: dict | None) -> tuple[dict, set[str]]:
    """Validate the merged result and write only the keys that changed.
    Returns (new values, changed keys). Unknown keys / bad values raise
    SettingsError (the route turns it into a 422)."""
    if not isinstance(partial, dict):
        raise SettingsError("Send an object of settings to change.")
    unknown = sorted(k for k in partial if k not in DEFAULTS)
    if unknown:
        raise SettingsError("Unknown setting(s): " + ", ".join(map(str, unknown)))
    current = all()
    new = validate({**current, **partial})
    changed = {k for k in KEYS if new[k] != current[k]}
    if changed:
        who = None
        if user:
            who = user.get("username") or user.get("id")
        with db.get_conn() as conn:
            for k in sorted(changed):
                conn.execute(
                    "INSERT INTO app_settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at, "
                    "updated_by = excluded.updated_by",
                    (k, json.dumps(new[k]), config.now_iso(), who),
                )
        invalidate()
        logger.info("App settings changed by %s: %s", who or "?", ", ".join(sorted(changed)))
    return all(), changed


def payload() -> dict:
    """GET/PUT /api/admin/settings response."""
    return {
        "values": all(),
        "defaults": {k: (list(v) if isinstance(v, list) else v) for k, v in DEFAULTS.items()},
        "meta": {k: dict(v) for k, v in META.items()},
    }


# ---------------------------------------------------------------------------
# derived values used by the features
# ---------------------------------------------------------------------------

def osrm_url() -> str:
    return (get("osrm_url") or "").rstrip("/") or DEFAULT_OSRM_URL


def nominatim_url() -> str:
    return (get("nominatim_url") or "").rstrip("/") or DEFAULT_NOMINATIM_URL


def drive_times_enabled() -> bool:
    """The "Drive times" switch. Off: no address is looked up or routed, the
    background warmer does nothing and no drive time is shown or sent."""
    return bool(get("drive_times_enabled"))


def home_address() -> str:
    return (get("home_address") or "").strip()


def drive_mode() -> str:
    """Which kind of route a cached drive time was computed for. Stored on
    each place (places.drive_mode) so flipping avoid_tolls re-queues every
    place for a fresh estimate instead of mixing the two."""
    return "no_tolls" if get("avoid_tolls") else "fastest"


def sensor_refresh_seconds() -> int:
    return int(get("sensor_refresh_minutes")) * 60
