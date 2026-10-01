"""Background cache-warmer for the "estimated drive time from home" feature (spec §7.3):
geocodes each place's address and computes its driving time from home via
geocode.py, caching both on the `places` row so nothing on a request path
(task list, notification send) ever waits on an external HTTP call — they
just read whatever is already cached.

A no-op tick whenever the feature is off or isn't configured (the "Drive
times" App setting off, `home_address` unset or not geocoded — see
geocode.feature_enabled()); nothing is sent then. Each tick first calls
geocode.ensure_home_blocking(), so a new home_address from App settings is
picked up (and a failed one retried hourly) without a restart. Network
lookups happen with no DB connection open.
Started from main.py's lifespan alongside the other three background loops;
cancelled cleanly on shutdown.
"""
import asyncio
import logging

from starlette.concurrency import run_in_threadpool

from . import config, db, geocode, settings

logger = logging.getLogger("drive_time")

TICK_SECONDS = 60
STALE_AFTER_DAYS = 30     # refresh a successful computation once a month (home/roads can change)
RETRY_AFTER_HOURS = 1     # retry a failed geocode/route an hour later, not every tick
PLACES_PER_TICK = 1       # keeps well under Nominatim's 1-request/second usage policy


def _needs_recompute_sql() -> tuple[str, tuple]:
    retry_before = config.days_ago_iso(RETRY_AFTER_HOURS / 24)
    stale_before = config.days_ago_iso(STALE_AFTER_DAYS)
    return (
        """
        SELECT id, address FROM places
        WHERE drive_checked_at IS NULL
           OR (drive_minutes IS NULL AND drive_checked_at < ?)
           OR drive_checked_at < ?
           OR drive_mode IS NULL OR drive_mode != ?      -- avoid_tolls changed since (or predates it)
        ORDER BY drive_checked_at IS NOT NULL, drive_checked_at
        LIMIT ?
        """,
        (retry_before, stale_before, settings.drive_mode(), PLACES_PER_TICK),
    )


def compute_for_address(address: str, geocode_fn=None, route_fn=None):
    """-> (lat, lon, minutes, tolls_avoided) for one address, any of which
    may be None on failure. Shared by the background pass below and the
    Places tab's "Recalculate" endpoint. Blocking (network calls). Sends
    nothing (all None) while the "Drive times" switch is off."""
    if not settings.drive_times_enabled():
        return None, None, None, None
    geocode_fn = geocode_fn or geocode.geocode_blocking
    route_fn = route_fn or geocode.route_blocking
    latlon = geocode_fn(address)
    if latlon is None:
        return None, None, None, None
    route = route_fn(settings.osrm_url(), geocode.HOME_LATLON, latlon, avoid_tolls=bool(settings.get("avoid_tolls")))
    if route is None:
        return latlon[0], latlon[1], None, None
    minutes, tolls_avoided = route
    return latlon[0], latlon[1], minutes, int(bool(tolls_avoided))


def run_pass_blocking(geocode_fn=None, route_fn=None) -> int:
    """(Re)computes up to PLACES_PER_TICK places that need it. Returns how
    many were updated (0 if the feature isn't configured/enabled, or nothing
    needed refreshing). `geocode_fn`/`route_fn` default to geocode.py's real
    network calls; tests inject fakes. The lookups run with no DB connection
    open; a result computed against a home location that has since changed
    is dropped (the place is simply picked up again)."""
    if not geocode.feature_enabled():
        return 0
    geocode_fn = geocode_fn or geocode.geocode_blocking
    route_fn = route_fn or geocode.route_blocking
    sql, params = _needs_recompute_sql()
    with db.get_conn() as conn:
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    updated = 0
    for row in rows:
        gen, mode = geocode.home_generation(), settings.drive_mode()
        lat, lon, minutes, tolls_avoided = compute_for_address(row["address"], geocode_fn, route_fn)
        if gen != geocode.home_generation() or mode != settings.drive_mode():
            break   # home or avoid_tolls changed meanwhile — this result is stale
        with db.get_conn() as conn:
            conn.execute(
                "UPDATE places SET lat = ?, lon = ?, drive_minutes = ?, drive_checked_at = ?, "
                "drive_mode = ?, drive_tolls_avoided = ? WHERE id = ? AND address = ?",
                (lat, lon, minutes, config.now_iso(), mode, tolls_avoided, row["id"], row["address"]),
            )
        updated += 1
    return updated


async def loop() -> None:
    while True:
        try:
            await run_in_threadpool(geocode.ensure_home_blocking)
            await run_in_threadpool(run_pass_blocking)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("drive-time pass failed")
        await asyncio.sleep(TICK_SECONDS)
