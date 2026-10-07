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
import logging
import re

from . import config, db
from .common import settings_core
from .common.settings_core import Group, Setting, SettingsError  # noqa: F401  (SettingsError: the routes' name)

logger = logging.getLogger("settings")

# Public OpenStreetMap services — a blank osrm_url / nominatim_url falls back
# to these.
DEFAULT_OSRM_URL = "https://router.project-osrm.org"
DEFAULT_NOMINATIM_URL = "https://nominatim.openstreetmap.org"

_URL_RE = re.compile(r"^https?://[^\s/?#]+[^\s]*$", re.IGNORECASE)


def _ids(v: list[str]) -> list[str]:
    out = []
    for x in v:
        x = x.strip()
        if not x or len(x) > 200:
            raise ValueError("must be a list of user ids")
        if x not in out:
            out.append(x)
    return out


def _features(v: list[str]) -> list[str]:
    from .maint_catalog import FEATURES
    bad = [x for x in v if x not in FEATURES]
    if bad:
        raise ValueError("unknown feature(s): " + ", ".join(bad))
    return [k for k in FEATURES if k in v]          # canonical order, no duplicates


def _http_url(v: str) -> str:
    # blank = use the public default
    if v and not _URL_RE.match(v):
        raise ValueError("must be an http:// or https:// URL, or blank for the default")
    return v.rstrip("/") if v else v


GROUPS = [
    Group("sensors", "Home Assistant sensors"),
    Group("drive", "Drive time"),
    Group("reminders", "Reminders"),
    Group("maintenance", "Maintenance"),
    Group("assistant", "Household Assistant", "The Household Assistant app answers questions from what the household "
          "apps know. Todo tells it a person's own tasks, lists and schedule, as they see them here."),
]

# Validation ranges for every setting. Strict types: "5" is not a number and 1 is not a boolean.
SETTINGS = [
    Setting("expose_schedule_sensors", True, "Expose schedule items to Home Assistant", group="sensors",
            help="Publish each schedule item as binary_sensor.household_todo_<name>. Turning this off removes them from "
                 "Home Assistant."),
    Setting("sensor_refresh_minutes", 5, "Sensor refresh (minutes)", group="sensors", min=1, max=1440,
            help="How often every sensor is re-published. Changes you make are pushed immediately; this is the safety "
                 "net after a Home Assistant restart."),
    # Drive times send the home address and every place's address to the address
    # lookup and routing servers (public OpenStreetMap services by default), so a
    # new install starts with them off. db._migrate turns this on for an older
    # database that already uses them (a home address or a computed drive time).
    Setting("drive_times_enabled", False, "Drive times (uses OpenStreetMap services)", group="drive",
            help="Estimated drive time from home to each saved place, and a “leave by” time on tasks and reminders "
                 "that have a place and a time."),
    Setting("home_address", "", "Home address", group="drive", max_length=300, show_if="drive_times_enabled",
            placeholder="e.g. 12 Example Street, Springfield",
            help="Drive times are estimated from here to every saved place. Leave blank and nothing is looked up."),
    Setting("osrm_url", DEFAULT_OSRM_URL, "Routing server (OSRM)", group="drive", kind="url", max_length=500,
            validators=[_http_url], show_if="drive_times_enabled", placeholder=DEFAULT_OSRM_URL,
            help="Blank uses the public OSRM demo server. Change it if you run your own."),
    Setting("nominatim_url", DEFAULT_NOMINATIM_URL, "Address lookup server (Nominatim)", group="drive", kind="url",
            max_length=500, validators=[_http_url], show_if="drive_times_enabled", placeholder=DEFAULT_NOMINATIM_URL,
            help="Blank uses OpenStreetMap's public Nominatim service. Change it if you run your own."),
    Setting("avoid_tolls", True, "Avoid toll roads", group="drive", show_if="drive_times_enabled",
            help="Estimate on routes without toll roads, falling back to the fastest route when there isn't one. "
                 "Changing it re-calculates every place."),
    # Reminders: a place's address and phone (with Directions / Call buttons) in notifications (§8.1)
    Setting("notify_place_details", True, "Place details in reminders", group="reminders",
            help="Reminders and “assigned to you” notifications for a task or schedule item with a place show its "
                 "address and phone, with Directions and Call buttons on the phone; the daily digest and weekly summary "
                 "add the address under the item. Off: only the place's name is sent."),
    # Maintenance (Admin → Maintenance; only the files folder is on App settings)
    Setting("maintenance_enabled", False, "Maintenance", group="maintenance", hidden=True),
    # HA user ids: who gets maintenance notifications by default
    Setting("maintenance_recipients", [], "Who gets maintenance notifications", group="maintenance",
            type=list[str], max_length=100, validators=[_ids], hidden=True),
    # maint_catalog.FEATURES keys: what the house has
    Setting("maintenance_profile", [], "Home profile", group="maintenance", type=list[str], max_length=60,
            validators=[_features], hidden=True),
    # sensor.household_todo_maintenance_overdue
    Setting("maintenance_sensor", False, "Overdue maintenance sensor", group="maintenance", hidden=True),
    # a folder inside /share; "" = attaching files is off
    Setting("maintenance_files_path", "", "Maintenance files folder", group="maintenance", max_length=400,
            placeholder="/share/household/maintenance",
            help="Manuals, receipts and photos for Maintenance are kept in a folder inside /share (a network share "
                 "mounted in Home Assistant works too). Saving creates the folder and what the app needs. Changing it "
                 "later never moves files. Blank turns attaching files off."),
    Setting("assistant_answers", True, "Answer the Household Assistant", group="assistant",
            help="Lets the Household Assistant tell people their tasks, lists and what's coming up on the schedule, "
                 "and add a task when they tap to confirm it. Each person can turn it off on Settings."),
]

REGISTRY = settings_core.Registry(
    SETTINGS, groups=GROUPS, connect=db.get_conn, model_config={"str_strip_whitespace": True, "strict": True},
    unknown_message=lambda keys: "Unknown setting(s): " + ", ".join(map(str, sorted(keys))),
    not_object_message="Send an object of settings to change.", now=config.now_iso, generation=db.generation,
    fallback_on_error=True, logger=logger)

AppSettings = REGISTRY.model
DEFAULTS: dict = REGISTRY.defaults
KEYS = REGISTRY.keys
# Every key applies live (see the readers); none needs a restart.
META: dict = REGISTRY.meta()


def validate(values: dict) -> dict:
    """Full set of values -> normalised dict. Raises SettingsError."""
    return REGISTRY.validate(values)


def invalidate() -> None:
    REGISTRY.invalidate()


def all() -> dict:  # noqa: A001 — the design's name for it
    """All current values. Cached; the cache is only filled if no write (and
    no database swap) happened while the rows were being read, so a reader
    racing an admin's save can never pin the old values."""
    return REGISTRY.all()


def get(key: str):
    return REGISTRY.get(key)


def update(partial, user: dict | None) -> tuple[dict, set[str]]:
    """Validate the merged result and write only the keys that changed.
    Returns (new values, changed keys). Unknown keys / bad values raise
    SettingsError (the route turns it into a 422)."""
    who = (user.get("username") or user.get("id")) if user else None
    _, changed = REGISTRY.update(partial, who)
    return all(), set(changed)


def payload() -> dict:
    """GET/PUT /api/admin/settings response."""
    return REGISTRY.payload()


# ---------------------------------------------------------------------------
# derived values used by the features
# ---------------------------------------------------------------------------

def osrm_url() -> str:
    return (get("osrm_url") or "").rstrip("/") or DEFAULT_OSRM_URL


def nominatim_url() -> str:
    return (get("nominatim_url") or "").rstrip("/") or DEFAULT_NOMINATIM_URL


def notify_place_details() -> bool:
    """Reminders carry a task's or item's place address and phone, with Directions / Call buttons (§8.1)."""
    return bool(get("notify_place_details"))


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
