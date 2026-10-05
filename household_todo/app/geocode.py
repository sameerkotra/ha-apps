"""Geocoding (OpenStreetMap Nominatim) and driving-time estimation (an
OSRM-compatible routing server) for the optional "estimated drive time from
home" feature (spec §7.3).

The whole feature sits behind the "Drive times" App setting
(`drive_times_enabled`, off for a new install): while it is off nothing here
sends anything — `ensure_home_blocking()` forgets the home location without
a lookup, `feature_enabled()` is False, and drive_time.py's warmer and the
Places tab's Recalculate do nothing. With it on, `home_address` is the only
setting that has to be set — `nominatim_url` and `osrm_url` default to the
public OpenStreetMap services (settings.py), overridable for a self-hosted
instance of either. All are read live: a new server URL is used on the next
request, and a new home address drops the cached home location (and every
place's cached drive time, see routers/admin.py) and is geocoded again in
the background. Leaving `home_address` unset, or home never successfully
geocoding, silently skips the feature everywhere it's used — no errors,
nothing shown. Stdlib `urllib` only, same blocking-function-run-in-a-
threadpool pattern as ha_client.py.

Nominatim's usage policy caps automated use at one request per second and
asks for an identifying User-Agent (no API key needed for this volume);
`_throttle()` enforces the former process-wide and NOMINATIM_USER_AGENT
sets the latter — both apply equally to a self-hosted instance, which won't
mind the extra caution. A place's geocoded coordinates and computed drive
time are cached on its `places` row — see drive_time.py, which decides
*when* to (re)compute in the background, and routers/places.py's
"recalculate" endpoint, which calls the same two `*_blocking` functions here
(geocode_blocking, route_blocking) on demand, for one place, right away.
Home is geocoded by `ensure_home_blocking()`: at startup, after
home_address (or the switch) changes, and — if it failed — again at most
once an hour from the drive-time loop.
"""
import json
import logging
import re
import threading
import time
import urllib.request  # noqa: F401 - tests replace urllib.request.urlopen through this module

from starlette.concurrency import run_in_threadpool

from . import settings
from .common import geo

logger = logging.getLogger("geocode")

NOMINATIM_USER_AGENT = "HouseholdTodo-HomeAssistantAddon/1.0 (self-hosted Home Assistant add-on)"
_MIN_INTERVAL_SECONDS = 1.05  # Nominatim's usage policy: at most 1 request/second

_THROTTLE = geo.Throttle(_MIN_INTERVAL_SECONDS)

# Set by ensure_home_blocking(); None means "not configured", "not geocoded
# yet" or "the last attempt failed" — either way, the feature is off.
HOME_LATLON: tuple[float, float] | None = None
HOME_RETRY_SECONDS = 3600
_home_lock = threading.Lock()
_home_for: str | None = None          # the address HOME_LATLON / the last attempt was for
_home_failed_at: float | None = None
_home_generation = 0                  # bumped whenever the home location is dropped or changes
_warned_for: str | None = None


def _throttle() -> None:
    """Blocks the calling thread just long enough to keep Nominatim calls at
    most 1/second process-wide, however many threads call geocode_blocking
    concurrently."""
    _THROTTLE.wait()


# Matches a trailing (or comma-separated) suite/unit/apartment designator —
# "#150", "Suite 200", "Ste. 4B", "Unit 12", "Apt 3", "Building C", "Floor 2",
# "Rm 101" — so it can be stripped and the lookup retried. OSM's address
# data is usually only granular to the street address, so one of these on an
# otherwise-correct address (e.g. an office park suite number) is a common
# reason Nominatim comes back with zero results for an address that's
# perfectly real.
_UNIT_RE = re.compile(
    r"(?:^|[,\s])(?:#\s*[\w-]+"
    r"|(?:suite|ste|unit|apt|apartment|bldg|building|fl|floor|rm|room)\.?\s*[\w-]+)",
    re.IGNORECASE,
)


def _strip_unit(address: str) -> str | None:
    """Removes one suite/unit/apartment designator from `address` (see
    _UNIT_RE). Returns the simplified, whitespace/comma-cleaned string, or
    None if there was nothing to strip — so the caller knows a retry would
    just repeat the same query."""
    simplified = _UNIT_RE.sub(" ", address, count=1)
    simplified = re.sub(r"\s+", " ", simplified)  # collapse the gap left behind
    simplified = re.sub(r"\s*,\s*,+", ",", simplified)  # collapse an emptied unit's comma
    simplified = re.sub(r"\s+,", ",", simplified).strip(" ,")
    return simplified if simplified and simplified != address.strip() else None


def _geocode_once(address: str) -> tuple[float, float] | None:
    """One throttled Nominatim lookup, no retry. Blocking."""
    _throttle()
    url = geo.search_url(settings.nominatim_url(), address)
    try:
        body = geo.fetch(url, headers={"User-Agent": NOMINATIM_USER_AGENT}, timeout=10)
    except Exception as e:  # URLError, timeout, connection reset, ...
        logger.debug("Geocoding %r failed: %s", address, e)
        return None
    try:
        return geo.first_result(body)
    except (ValueError, KeyError, IndexError, TypeError):
        logger.debug("Geocoding %r returned no usable result", address)
        return None


def geocode_blocking(address: str) -> tuple[float, float] | None:
    """`address` -> (lat, lon) via Nominatim's free public search API, or
    None on any failure (no results, network error, bad response). If the
    address as given comes back with no results, retries once with a
    suite/unit/apartment designator stripped out (e.g. "12 Example Way #150,
    Springfield" -> "12 Example Way, Springfield") — see
    _strip_unit. Blocking — call via run_in_threadpool from async code."""
    address = (address or "").strip()
    if not address:
        return None
    result = _geocode_once(address)
    if result is not None:
        return result
    simplified = _strip_unit(address)
    if simplified is not None:
        logger.debug("Retrying geocode of %r without unit/suite as %r", address, simplified)
        result = _geocode_once(simplified)
    return result


def _route_minutes(url: str) -> int | None:
    """One OSRM /route request -> whole minutes (rounded, minimum 1), or None
    on any failure — including OSRM's own errors, e.g. "NoRoute" when every
    route needs a toll road, or "InvalidValue" from a server whose profile
    can't exclude tolls (both arrive as HTTP 400, raised by urlopen)."""
    try:
        body = geo.fetch(url, timeout=10)
    except Exception as e:
        logger.debug("Routing request %s failed: %s", url, e)
        return None
    try:
        data = json.loads(body)
        if data.get("code") != "Ok" or not data.get("routes"):
            return None
        return max(1, round(data["routes"][0]["duration"] / 60))
    except (ValueError, KeyError, IndexError, TypeError):
        logger.debug("Unexpected response from routing server: %r", body[:200])
        return None


def route_blocking(
    osrm_url: str, origin: tuple[float, float], dest: tuple[float, float], avoid_tolls: bool = False
) -> tuple[int, bool] | None:
    """Estimated driving time between two (lat, lon) points via an
    OSRM-compatible server's `/route/v1/driving` endpoint, as
    `(minutes, tolls_avoided)`, or None on failure. OSRM's own API takes
    coordinates as lon,lat — the (lat, lon) tuples here are the app's usual
    order and are swapped when building the URL.

    With `avoid_tolls`, asks for a route with `exclude=toll` first. If there
    isn't one (tolls are unavoidable) or the server can't exclude tolls, it
    falls back to the normal fastest route and reports tolls_avoided=False,
    so a place still gets an estimate rather than none at all."""
    if not osrm_url:
        return None
    url = f"{osrm_url}/route/v1/driving/{geo.lonlat([origin, dest])}?overview=false"
    if avoid_tolls:
        minutes = _route_minutes(url + "&exclude=toll")
        if minutes is not None:
            return minutes, True
        logger.debug("No toll-free route found (or tolls can't be excluded); using the fastest route")
    minutes = _route_minutes(url)
    return None if minutes is None else (minutes, False)


def home_generation() -> int:
    """Changes whenever the home location is dropped or replaced, so a
    drive-time computation that started against the old home can tell its
    result is stale."""
    return _home_generation


def reset_home() -> None:
    """home_address changed (App settings): forget the old location at once so
    nothing is computed against it; ensure_home_blocking() geocodes the new
    one."""
    global HOME_LATLON, _home_for, _home_failed_at, _home_generation
    with _home_lock:
        HOME_LATLON = None
        _home_for = None
        _home_failed_at = None
        _home_generation += 1


def retry_home_soon() -> None:
    """A server URL changed: if home failed to geocode, try again on the next
    call instead of waiting out the hour."""
    global _home_failed_at
    _home_failed_at = None


def ensure_home_blocking() -> tuple[float, float] | None:
    """Geocode the current home_address setting if it hasn't been (or has
    changed since, or the last attempt failed more than an hour ago).
    Returns HOME_LATLON. Blocking (network) — never call it while holding a
    DB connection. The lock is only held to read/store the state, not
    during the lookup."""
    global HOME_LATLON, _home_for, _home_failed_at, _home_generation, _warned_for
    # The "Drive times" switch off counts as "no home address": nothing is looked up.
    address = settings.home_address() if settings.drive_times_enabled() else ""
    with _home_lock:
        if not address:
            if HOME_LATLON is not None or _home_for is not None:
                HOME_LATLON, _home_for, _home_failed_at = None, None, None
                _home_generation += 1
            return None
        if address == _home_for:
            if HOME_LATLON is not None:
                return HOME_LATLON
            if _home_failed_at is not None and time.monotonic() - _home_failed_at < HOME_RETRY_SECONDS:
                return None
        gen = _home_generation
    latlon = geocode_blocking(address)
    with _home_lock:
        if gen != _home_generation or settings.home_address() != address or not settings.drive_times_enabled():
            return HOME_LATLON                  # reset or changed while we were asking — theirs wins
        if latlon != HOME_LATLON or address != _home_for:
            _home_generation += 1
        HOME_LATLON, _home_for = latlon, address
        _home_failed_at = None if latlon is not None else time.monotonic()
        warn = latlon is None and _warned_for != address
        if warn:
            _warned_for = address
    if warn:
        logger.warning(
            "Could not geocode home_address %r — estimated drive times are off until this succeeds "
            "(retried hourly, or fix the address in Admin → App settings).", address)
    elif latlon is not None:
        logger.info("Home address geocoded; estimated drive times are on")
    return latlon


def load_home_blocking() -> None:
    """At startup (best effort)."""
    ensure_home_blocking()


async def load_home() -> None:
    await run_in_threadpool(load_home_blocking)


def feature_enabled() -> bool:
    """The "Drive times" switch is on and home geocoded successfully (the
    routing server always has a URL — a blank setting means the public
    default). Cheap and safe to call from a hot path."""
    return settings.drive_times_enabled() and bool(settings.osrm_url()) and HOME_LATLON is not None
