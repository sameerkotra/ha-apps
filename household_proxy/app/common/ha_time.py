# Shared file: edit common/python/ha_time.py and run tools/sync_common.py; don't edit this copy. sha256=e1b96d54c3c25813db9e9aee9e58861b0431f91020c519d7160d57ac11584907
"""Home Assistant's time zone, and "now" / "today" in it (shared by the household apps:
common/python/ha_time.py, copied into each app's app/common/ by tools/sync_common.py).

The container's own clock is UTC (standard for Docker images); a household's "today" is Home
Assistant's zone (Settings → System → General), read from GET /api/config at start-up.

The app's `config.py` owns one `Zone` and keeps its own `now()` / `today()` (so tests can still
replace `config.utcnow`, `config.now` or `config.today`):

    from .common import ha_time
    _zone = ha_time.Zone(logger)
    def set_timezone(name): return _zone.set(name)
    def now(): return _zone.now(utcnow())

and loads it at start-up (an `on_config(cfg)` hook picks other GET /config fields: currency, home
location):

    await ha_time.load(config._zone, on_config=...)        # logs which zone is used
    ha_time.start_blocking(config._zone, on_config=...)    # the same from start-up code that isn't async

Inside Home Assistant (`inside_home_assistant()`), start-up also makes sure the zone is right later on:

- Home Assistant's own API often isn't answering yet when an app starts (after a reboot the apps start
  before Home Assistant has). Until it answers, the zone is the one the Supervisor gives every app's
  container in `TZ` (Home Assistant's zone too), never UTC.
- A background thread keeps asking until Home Assistant answers (every 30 seconds for ten minutes, then
  every ten), and after that every six hours, so a changed zone is picked up without a restart.
- The process's own zone (`TZ`, `time.tzset()`) follows, so a stray `date.today()` / `datetime.now()`
  and the log's timestamps are in Home Assistant's zone too.

Outside Home Assistant (tests, a computer) nothing runs in the background and the process is untouched.

Uses the shared `ha_client` (and through it the app's `config` for the Core API address and token,
unless `base_url=` / `token=` are given). Stored timestamps are never touched: they stay UTC.
"""
import logging
import os
import threading
import time
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from . import ha_client

logger = logging.getLogger("ha_time")

DEFAULT_ZONE = "UTC"
OPTIONS_FILE = "/data/options.json"          # every Home Assistant app has it; a test or a computer doesn't
RETRY_SECONDS = 30                           # while Home Assistant hasn't answered: this often …
RETRY_TRIES = 20                             # … this many times (ten minutes),
SLOW_RETRY_SECONDS = 600                     # then every ten minutes
REFRESH_SECONDS = 6 * 3600                   # once it has answered: again every six hours

_watchers: dict[int, threading.Thread] = {}  # id(zone) -> its keeper thread (one per zone)
_watch_lock = threading.Lock()


def _known(name: str | None):
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except Exception:   # ZoneInfoNotFoundError, ValueError, …
        return None


class Zone:
    """The app's current time zone: `tz` (a tzinfo) and `name` (IANA, e.g. "Europe/Berlin")."""

    def __init__(self, log: logging.Logger | None = None, name: str = DEFAULT_ZONE, tz=None):
        self.log = log or logger
        self.name = name
        if tz is None:
            tz = _known(name) or timezone.utc   # no tz database at all: UTC
        self.tz = tz
        self.from_home_assistant = False        # True once Home Assistant itself said which zone

    def set(self, name: str | None) -> bool:
        """Switch to `name`. Returns False (and keeps the current zone) if it isn't a known zone —
        e.g. the container has no tz database (python:alpine needs `apk add tzdata`)."""
        if not name:
            return False
        try:
            tz = ZoneInfo(name)
        except Exception as e:  # ZoneInfoNotFoundError, ValueError, ...
            self.log.warning("Unknown time zone %r (%s); staying on %s", name, e, self.name)
            return False
        self.tz, self.name = tz, name
        return True

    def now(self, utc: datetime | None = None) -> datetime:
        """`utc` (default: the current time) as an aware datetime in this zone."""
        return (utc or datetime.now(timezone.utc)).astimezone(self.tz)

    def today(self, utc: datetime | None = None) -> date:
        return self.now(utc).date()


def inside_home_assistant() -> bool:
    """True in an app container that Home Assistant's Supervisor started (it has options and a token)."""
    return bool(os.environ.get("SUPERVISOR_TOKEN")) and os.path.isfile(OPTIONS_FILE)


def supervisor_zone() -> str | None:
    """The zone the Supervisor put in the container's `TZ` (Home Assistant's own), if it's a known one."""
    name = (os.environ.get("TZ") or "").strip().lstrip(":")
    return name if _known(name) else None


def apply_to_process(name: str) -> bool:
    """Make `name` the process's own zone too (`date.today()`, `datetime.now()`, log times)."""
    if not _known(name):
        return False
    os.environ["TZ"] = name
    if hasattr(time, "tzset"):
        time.tzset()
    return True


def load_blocking(zone: Zone, on_config=None, *, base_url: str | None = None, token: str | None = None,
                  timeout: float = 10, log=None) -> str | None:
    """Read Home Assistant's configuration and switch `zone` to its time zone. Returns the zone's name,
    or None when it couldn't be read or isn't known (the zone is then unchanged). `on_config(cfg)` gets
    the whole GET /config answer first. Blocking."""
    cfg = ha_client.fetch_config_blocking(base_url=base_url, token=token, timeout=timeout, log=log)
    if cfg is None:
        return None
    if on_config is not None:
        on_config(cfg)
    name = cfg.get("time_zone")
    if name and zone.set(name):
        zone.from_home_assistant = True
        return name
    return None


def _keep_current(zone: Zone, on_config, log, kw) -> None:
    tries = 0
    while True:
        if zone.from_home_assistant:
            wait = REFRESH_SECONDS
        else:
            tries += 1
            wait = RETRY_SECONDS if tries <= RETRY_TRIES else SLOW_RETRY_SECONDS
        time.sleep(wait)
        before = zone.name
        try:
            name = load_blocking(zone, on_config, **kw)
        except Exception:                        # never let the keeper die
            log.exception("Reading Home Assistant's time zone failed")
            continue
        if name:
            apply_to_process(name)
            if name != before:
                log.info("Using Home Assistant's time zone: %s (was %s)", name, before)


def keep_current(zone: Zone, on_config=None, *, log=None, **kw) -> bool:
    """Start (once per zone) the thread that keeps `zone` on Home Assistant's zone. Inside Home
    Assistant only; returns whether it runs."""
    if not inside_home_assistant():
        return False
    with _watch_lock:
        t = _watchers.get(id(zone))
        if t is not None and t.is_alive():
            return True
        t = threading.Thread(target=_keep_current, args=(zone, on_config, log or logger, kw),
                             name="ha-time-zone", daemon=True)
        _watchers[id(zone)] = t
        t.start()
    return True


def start_blocking(zone: Zone, on_config=None, *, log=None, details: bool = False, **kw) -> str | None:
    """At start-up: read Home Assistant's zone; inside Home Assistant fall back to the Supervisor's `TZ`
    while it doesn't answer, make the process follow, and keep it current. Logs the zone in use and
    returns Home Assistant's zone name (None when it didn't answer)."""
    log = log or logger
    name = load_blocking(zone, on_config, log=log if details else None, **kw)
    inside = inside_home_assistant()
    if name:
        log.info("Using Home Assistant's time zone: %s", name)
    elif inside and zone.set(supervisor_zone()):
        log.warning("Home Assistant didn't say its time zone yet — using %s (from the Supervisor) and asking "
                    "again shortly.", zone.name)
    else:
        log.warning("Could not read Home Assistant's time zone — staying on the %s default.", zone.name)
    if inside:
        apply_to_process(zone.name)
        keep_current(zone, on_config, log=log, **kw)
    return name


async def load(zone: Zone, on_config=None, *, log=None, details: bool = False, **kw) -> None:
    """start_blocking() in a thread (`log`: the app's logger; `details`: also log there why Home
    Assistant's configuration couldn't be read)."""
    from starlette.concurrency import run_in_threadpool

    await run_in_threadpool(lambda: start_blocking(zone, on_config, log=log, details=details, **kw))
