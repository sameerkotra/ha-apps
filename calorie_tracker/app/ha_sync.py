"""Publishes ONLY a per-user daily-calorie-total sensor to Home Assistant.
Nothing else (no macros, no weight, no food names) ever leaves this app
towards Home Assistant's entity system — that's a deliberate privacy
boundary, not just a default.

Uses the Supervisor's Core API proxy (http://supervisor/core/api/), which
requires `homeassistant_api: true` in config.yaml and the auto-injected
SUPERVISOR_TOKEN, through the shared client (app/common/ha_client.py), off the
event loop via the threadpool.
"""
import logging

from starlette.concurrency import run_in_threadpool

from . import config, settings
from .common import ha_client, ha_time, sensor_publisher

logger = logging.getLogger("ha_sync")

_warned_no_token = False
# Set when the switch was turned off but some DELETEs failed (HA down); the
# periodic job retries them.
_removal_pending = False


def exposed() -> bool:
    """Admin → App settings → "Publish daily calories to Home Assistant" (the household's master switch),
    read live on every push. Each person also has their own "Publish my calories to Home Assistant"
    (users.publish_sensor, Goals page): a sensor exists only for people who turned theirs on."""
    return bool(settings.get("expose_daily_calories_sensor"))


def entity_id_for(user_slug: str) -> str:
    return f"sensor.calorie_tracker_{user_slug}_daily_calories"


def _has_token() -> bool:
    global _warned_no_token
    if config.SUPERVISOR_TOKEN:
        return True
    if not _warned_no_token:
        logger.warning(
            "SUPERVISOR_TOKEN not set — skipping HA sensor sync. This is "
            "expected outside of Home Assistant; inside HA it means "
            "homeassistant_api isn't granted in config.yaml."
        )
        _warned_no_token = True
    return False


def _request(method: str, entity_id: str, payload: dict | None = None) -> int | None:
    """One Core API call for `entity_id`; returns the HTTP status, or None if
    HA couldn't be reached. Blocking."""
    def failed(e):
        logger.warning("HA sensor sync (%s %s) failed: %s", method, entity_id, e)

    status, _ = ha_client.request(method, f"/states/{entity_id}", payload, timeout=10, on_error=failed)
    return status


def _post(entity_id: str, state_value, attributes: dict) -> bool:
    status = _request("POST", entity_id, {"state": state_value, "attributes": attributes})
    if status is not None and 200 <= status < 300:
        return True
    if status is not None:
        logger.warning("HA sensor sync failed for %s: HTTP %s", entity_id, status)
    return False


def _delete(entity_id: str) -> bool:
    """DELETE /api/states/<entity_id>. 404 (never published / already gone)
    counts as removed."""
    status = _request("DELETE", entity_id)
    if status is not None and (200 <= status < 300 or status == 404):
        return True
    if status is not None:
        logger.warning("Could not remove %s from Home Assistant: HTTP %s", entity_id, status)
    return False


# Remembers the entity ids pushed successfully by this process — so turning the
# sensor switch off also removes one whose user was renamed since (new slug).
SENSORS = sensor_publisher.Publisher(post=_post, delete=_delete)
_published = SENSORS.memory


def _sync_push_state(entity_id: str, state_value, attributes: dict, force: bool = False) -> bool:
    """Post one sensor; an unchanged value isn't sent again (the periodic job re-sends everything)."""
    if not _has_token():
        return False
    return SENSORS.post(entity_id, state_value, attributes, force=force) is not False


def _payload(display_name: str, calories: float) -> tuple:
    attributes = {
        "unit_of_measurement": "kcal",
        "friendly_name": f"{display_name} Daily Calories",
        "icon": "mdi:food-apple",
    }
    return round(calories, 1), attributes


def _today_totals(user_id: str | None = None) -> list[dict]:
    """[{id, name, total, publish, slug}] for every user (or one), today's calories and whether they publish them.
    Opens and closes its own connection — callers do their network calls after."""
    from . import db  # local import to avoid a circular import at module load
    from .auth import _slugify, sensor_slugs

    today = str(config.today())
    where, args = ("WHERE u.id = ?", (today, user_id)) if user_id else ("", (today,))
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT u.id, u.name, u.publish_sensor AS publish, COALESCE(SUM(f.calories), 0) AS total FROM users u "
            "LEFT JOIN food_logs f ON f.user_id = u.id AND f.date = ? "
            f"{where} GROUP BY u.id ORDER BY u.name",
            args,
        ).fetchall()
        slugs = sensor_slugs(conn)
    return [{**dict(r), "publish": bool(r["publish"]), "slug": slugs.get(r["id"]) or _slugify(r["name"])}
            for r in rows]


def push_for_user_blocking(user_id: str) -> bool | None:
    """After a change to someone's day (a background task, after the answer): today's total for that person,
    if the household switch and their own are on. Never raises."""
    try:
        if not exposed() or not _has_token():
            return None
        rows = _today_totals(user_id)
        if not rows or not rows[0]["publish"]:
            return None
        u = rows[0]
        return _sync_push_state(entity_id_for(u["slug"]), *_payload(u["name"], u["total"]))
    except Exception:
        logger.exception("Updating the calories sensor failed")
        return False


async def push_for_user(user_id: str, user_slug: str | None = None, display_name: str | None = None):
    """push_for_user_blocking in the threadpool (the slug and name are worked out from the database)."""
    return await run_in_threadpool(push_for_user_blocking, user_id)


def remove_for_user_blocking(user_id: str) -> None:
    """Someone turned "Publish my calories" off: their sensor goes from Home Assistant now (best effort; the
    periodic job removes any left over)."""
    global _removal_pending
    if not _has_token():
        return
    rows = _today_totals(user_id)
    ids = {entity_id_for(r["slug"]) for r in rows}
    result = SENSORS.remove(sorted(ids))
    if result["failed"]:
        _removal_pending = True


def _sync_push_all() -> dict:
    if not exposed() or not _has_token():
        return {"pushed": 0, "failed": 0}
    people = _today_totals()                   # DB connection closed before any HTTP call
    items = [(entity_id_for(u["slug"]), *_payload(u["name"], u["total"])) for u in people if u["publish"]]
    # sensors this process posted for someone who has since turned theirs off (or was renamed)
    current = {i[0] for i in items}
    stale = [e for e in SENSORS.names() if e not in current]
    if stale:
        SENSORS.remove(stale)
    return SENSORS.publish(items, force=True)


async def push_all() -> dict:
    """Push every known user's sensor (disabled users included). Used by the
    periodic job and right after the switch is turned on."""
    return await run_in_threadpool(_sync_push_all)


def _sync_remove_all() -> dict:
    """Switch turned off: best-effort DELETE of every sensor this app
    publishes (each known user's current slug, plus anything this process
    pushed under an older name). Failures are retried by the periodic job."""
    global _removal_pending
    from . import db
    from .auth import _slugify, sensor_slugs

    if exposed() or not _has_token():
        return {"removed": 0, "failed": 0}
    with db.get_conn() as conn:
        names = [r["name"] for r in conn.execute("SELECT name FROM users")]
        slugs = sensor_slugs(conn)
    # each person's sensor, and the shared one a name collision used to give (before the unique endings)
    entity_ids = sorted({entity_id_for(s) for s in slugs.values()} | {entity_id_for(_slugify(n)) for n in names}
                        | set(SENSORS.names()))
    result = SENSORS.remove(entity_ids)
    _removal_pending = result["failed"] > 0
    return result


async def remove_all() -> dict:
    return await run_in_threadpool(_sync_remove_all)


async def apply_exposure_change() -> dict:
    """Called right after expose_daily_calories_sensor changed in App
    settings: on → publish everyone now; off → remove the sensors now."""
    global _removal_pending
    if exposed():
        _removal_pending = False
        return await push_all()
    return await remove_all()


async def periodic_sync() -> dict:
    """One tick of main.py's periodic job: on → push everyone who publishes (so the value
    rolls over to 0 after midnight); off → retry a removal that failed."""
    global _removal_pending
    if exposed():
        _removal_pending = False
        return await push_all()
    if _removal_pending:
        return await remove_all()
    return {}


def seconds_to_midnight(now=None) -> float:
    """Seconds until a few seconds after the next local midnight (Home Assistant's zone)."""
    from datetime import datetime, timedelta
    now = now or config.now()
    tomorrow = (now + timedelta(days=1)).date()
    midnight = datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=now.tzinfo)
    return max(1.0, (midnight - now).total_seconds() + 5)


async def midnight_loop(sleep=None) -> None:
    """Just after each local midnight, re-send everyone's sensor so a new day starts at 0 straight away
    (the 15-minute job alone could leave yesterday's total showing for a while)."""
    import asyncio
    sleep = sleep or asyncio.sleep
    while True:
        await sleep(seconds_to_midnight())
        try:
            await periodic_sync()
        except Exception as e:
            logger.warning("Midnight sensor sync failed: %s", e)


# ---------------------------------------------------------------------------
# Home Assistant's time zone (config.py owns the resulting state; see there)
# ---------------------------------------------------------------------------

async def load_timezone() -> None:
    """At startup: fetch HA's time zone once and apply it; fall back to the
    UTC default already set in config.py if it can't be read."""
    await ha_time.load(config.ZONE, log=logger, details=True)
