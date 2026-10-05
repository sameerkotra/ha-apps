# Shared file: edit common/python/ha_time.py and run tools/sync_common.py; don't edit this copy. sha256=7f7f43262b6f9e55a28c29d7d40966e0bc9238f957c834f3a8bb510e2e32f709
"""Home Assistant's time zone, and "now" / "today" in it (shared by the household apps:
common/python/ha_time.py, copied into each app's app/common/ by tools/sync_common.py).

The container's own clock is UTC (standard for Docker images); a household's "today" is Home
Assistant's zone (Settings → System → General), read from GET /api/config once at start-up. Until
it's read — or if it can't be — the zone stays UTC.

The app's `config.py` owns one `Zone` and keeps its own `now()` / `today()` (so tests can still
replace `config.utcnow`, `config.now` or `config.today`):

    from .common import ha_time
    _zone = ha_time.Zone(logger)
    def set_timezone(name): return _zone.set(name)
    def now(): return _zone.now(utcnow())

and loads it at start-up (an `on_config(cfg)` hook picks other GET /config fields: currency, home
location):

    await ha_time.load(config._zone, on_config=...)        # logs which zone is used

Uses the shared `ha_client` (and through it the app's `config` for the Core API address and token,
unless `base_url=` / `token=` are given). Stored timestamps are never touched: they stay UTC.
"""
import logging
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from . import ha_client

logger = logging.getLogger("ha_time")

DEFAULT_ZONE = "UTC"


class Zone:
    """The app's current time zone: `tz` (a tzinfo) and `name` (IANA, e.g. "Europe/Berlin")."""

    def __init__(self, log: logging.Logger | None = None, name: str = DEFAULT_ZONE, tz=None):
        self.log = log or logger
        self.name = name
        if tz is None:
            try:
                tz = ZoneInfo(name)
            except Exception:   # ZoneInfoNotFoundError if the tz database is somehow missing
                tz = timezone.utc
        self.tz = tz

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
    return name if name and zone.set(name) else None


async def load(zone: Zone, on_config=None, *, log=None, details: bool = False, **kw) -> None:
    """At start-up: load_blocking() in a thread, then log the zone in use (`log`: the app's logger;
    `details`: also log there why Home Assistant's configuration couldn't be read)."""
    from starlette.concurrency import run_in_threadpool

    log = log or logger
    name = await run_in_threadpool(lambda: load_blocking(zone, on_config, log=log if details else None, **kw))
    if name:
        log.info("Using Home Assistant's time zone: %s", name)
    else:
        log.warning("Could not read Home Assistant's time zone — staying on the %s default.", zone.name)
