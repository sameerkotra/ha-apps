"""One read-only binary_sensor per schedule item, pushed to Home Assistant
through the Supervisor Core API (SPEC §7.2).

Entities made this way aren't stored by Home Assistant — they vanish on an HA
restart — so a background loop re-pushes everything: on the first tick after
startup, whenever the local date changes, and every `sensor_refresh_minutes` (default 5). The schedule
routes also push (create/edit/exception) or delete one entity immediately.

An item is published only while the global `expose_schedule_sensors` setting
AND its own `expose_sensor` switch are on. Both global settings are
App settings and re-read on every tick: turning exposure off
removes every entity on the next tick (or straight away, from the settings
route) and stops publishing; turning it back on re-publishes everything. A timed item's sensor is
on only between its start and end time, so on the ticks between full syncs
the loop recomputes every published timed item and pushes the ones whose
state differs from the last one pushed (kept in memory; a restart simply
re-pushes everything on the first tick).

Every push is best effort. Nothing here ever fails an API request.
"""
import asyncio
import logging
import time

from starlette.concurrency import run_in_threadpool

from . import config, db, ha_client, schedule_logic, settings

logger = logging.getLogger("ha_sensors")

TICK_SECONDS = 60
MAX_CONSECUTIVE_FAILURES = 3

_streak_warned = False

# entity_id -> "on"/"off" last pushed successfully. Only used to notice a
# timed item's window opening or closing between full syncs.
_last_state: dict[str, str] = {}
# Entities of unpublished items (expose_sensor off) already confirmed gone in
# HA this run. A full sync deletes any others once, so a failed delete right
# after "Publish" was switched off doesn't leave a stale sensor behind.
_cleared_unpublished: set[str] = set()


def exposed() -> bool:
    """The global App setting (live)."""
    return bool(settings.get("expose_schedule_sensors"))


def _enabled() -> bool:
    if not exposed():
        return False
    if not ha_client.has_token():
        ha_client.warn_no_token_once("Home Assistant sensor sync")
        return False
    return True


def _post(entity: str, state: str, attrs: dict) -> bool:
    ok = ha_client.post_state(entity, state, attrs)
    if ok:
        _last_state[entity] = state
        _cleared_unpublished.discard(entity)
    return ok


def _published_items(conn, where: str = "") -> list[dict]:
    """Items whose own switch is on (the global one is checked by _enabled)."""
    return [dict(r) for r in conn.execute(
        f"SELECT * FROM schedule_items WHERE expose_sensor = 1 {where} ORDER BY created_at")]


def push_item_blocking(item_id: str) -> bool:
    """Push one item's current state. Blocking; returns success. An item
    whose `expose_sensor` switch is off is never pushed."""
    if not _enabled():
        return False
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM schedule_items WHERE id = ?", (item_id,)).fetchone()
        if not row or not row["expose_sensor"]:
            return False
        item = dict(row)
        excs = schedule_logic.load_exceptions(conn, item_id).get(item_id, [])
        users, _ = schedule_logic.lookups(conn)
    now = config.now()
    entity, state, attrs = schedule_logic.sensor_payload(item, excs, now.date(), now, users)
    ok = _post(entity, state, attrs)
    if not ok:
        logger.warning("Could not push %s to Home Assistant", entity)
    return ok


def delete_entity_blocking(slug: str) -> bool:
    """Remove an item's entity (best effort): the item was deleted, or its
    `expose_sensor` switch was turned off. Blocking."""
    if not _enabled():
        return False
    eid = schedule_logic.entity_id(slug)
    _last_state.pop(eid, None)
    ok = ha_client.delete_state(eid)
    if ok:
        _cleared_unpublished.add(eid)
    return ok


def full_sync_blocking() -> dict:
    """Push every published item; items with `expose_sensor` off are
    skipped. Aborts after 3 consecutive failures; logs one warning per
    failure streak. Returns {pushed, failed}."""
    global _streak_warned
    result = {"pushed": 0, "failed": 0}
    if not _enabled():
        return result
    with db.get_conn() as conn:
        items = _published_items(conn)
        unpublished = [r["entity_slug"] for r in conn.execute("SELECT entity_slug FROM schedule_items WHERE expose_sensor = 0")]
        excs = schedule_logic.load_exceptions(conn)
        users, _ = schedule_logic.lookups(conn)
    for slug in unpublished:
        eid = schedule_logic.entity_id(slug)
        if eid not in _cleared_unpublished and ha_client.delete_state(eid):   # a 404 counts as gone
            _cleared_unpublished.add(eid)
    now = config.now()
    consecutive = 0
    for item in items:
        entity, state, attrs = schedule_logic.sensor_payload(item, excs.get(item["id"], []), now.date(), now, users)
        if _post(entity, state, attrs):
            result["pushed"] += 1
            consecutive = 0
            _streak_warned = False
        else:
            result["failed"] += 1
            consecutive += 1
            if not _streak_warned:
                logger.warning("Sensor sync to Home Assistant is failing (%s)", entity)
                _streak_warned = True
            if consecutive >= MAX_CONSECUTIVE_FAILURES:
                break
    return result


def push_timed_changes_blocking() -> int:
    """Between full syncs: push each published timed item whose computed
    state differs from the last one pushed, so its sensor turns on and off
    within a tick of the start and end times. Returns how many were pushed;
    a failed push isn't recorded, so it is retried on the next tick."""
    if not _enabled():
        return 0
    with db.get_conn() as conn:
        items = _published_items(conn, "AND start_time IS NOT NULL AND end_time IS NOT NULL")
        if not items:
            return 0
        excs = schedule_logic.load_exceptions(conn)
        users, _ = schedule_logic.lookups(conn)
    now = config.now()
    pushed = 0
    for item in items:
        entity, state, attrs = schedule_logic.sensor_payload(item, excs.get(item["id"], []), now.date(), now, users)
        if _last_state.get(entity) == state:
            continue
        if _post(entity, state, attrs):
            pushed += 1
        else:
            logger.warning("Could not push %s to Home Assistant", entity)
            break   # HA is probably unreachable; the next tick tries again
    return pushed


def remove_all_entities_blocking() -> int:
    """`expose_schedule_sensors` off: best-effort DELETE for every known
    item's entity so stale sensors don't linger. Also forgets what was
    pushed, so switching exposure back on re-publishes everything."""
    if exposed() or not ha_client.has_token():
        return 0
    with db.get_conn() as conn:
        slugs = [r["entity_slug"] for r in conn.execute("SELECT entity_slug FROM schedule_items")]
        maint = [r["entity_slug"] for r in conn.execute("SELECT entity_slug FROM maint_items WHERE expose_sensor = 1")]
    from .maintenance import ENTITY_PREFIX as MAINT_PREFIX
    removed = 0
    for eid in [schedule_logic.entity_id(s) for s in slugs] + [MAINT_PREFIX + s for s in maint]:
        if ha_client.delete_state(eid):
            removed += 1
            _last_state.pop(eid, None)
    _last_state.clear()
    _cleared_unpublished.clear()
    return removed


def apply_exposure_change_blocking() -> dict:
    """Called by the App settings route right after expose_schedule_sensors
    changed, so the change is visible in Home Assistant at once rather than
    on the next tick: off -> remove every entity; on -> full sync."""
    if exposed():
        return full_sync_blocking()
    return {"removed": remove_all_entities_blocking()}


# ---------------------------------------------------------------------------
# Maintenance: one overdue-count sensor (Admin → Maintenance) and, per item, an optional
# binary_sensor that is on while the item is due or overdue (only while schedule sensors are exposed too).
# ---------------------------------------------------------------------------
_overdue_pushed: bool | None = None     # None = unknown (after a start): off means "delete once"


def push_maintenance_blocking() -> dict:
    """Bring Home Assistant in line with Maintenance now. Best effort; never raises."""
    global _overdue_pushed
    from . import maintenance as mt
    out = {"pushed": 0, "removed": 0}
    if not ha_client.has_token():
        return out
    try:
        on = mt.enabled()
        today = config.today()
        with db.get_conn() as conn:
            items = mt.all_items(conn)
        statuses = {it["id"]: mt.status_of(it, today) for it in items}
        if on and settings.get("maintenance_sensor"):
            overdue = [it for it in items if statuses[it["id"]][0] == "overdue"]
            due = [it for it in items if statuses[it["id"]][0] == "due"]
            attrs = {"friendly_name": "Maintenance overdue", "icon": "mdi:tools", "unit_of_measurement": "items",
                     "overdue": [it["name"] for it in overdue], "due_soon": [it["name"] for it in due],
                     "due_soon_count": len(due)}
            if ha_client.post_state(mt.OVERDUE_SENSOR, str(len(overdue)), attrs):
                _overdue_pushed = True
                out["pushed"] += 1
        elif _overdue_pushed is not False:
            if ha_client.delete_state(mt.OVERDUE_SENSOR):
                _overdue_pushed = False
                out["removed"] += 1
        publish = on and exposed()
        for it in items:
            eid = mt.entity_id(it)
            if publish and it["expose_sensor"]:
                st, d = statuses[it["id"]]
                state = "on" if st in ("due", "overdue") else "off"
                attrs = {"friendly_name": it["name"], "icon": "mdi:tools", "status": st,
                         "due_date": d.isoformat() if d else None,
                         "days_until": (d - today).days if d else None, "repeat": mt.repeat_label(it)}
                if _post(eid, state, attrs):
                    out["pushed"] += 1
            elif it["expose_sensor"] and eid in _last_state:
                if ha_client.delete_state(eid):
                    _last_state.pop(eid, None)
                    out["removed"] += 1
    except Exception:
        logger.exception("Maintenance sensor sync failed")
    return out


def delete_maintenance_entity_blocking(slug: str) -> bool:
    from . import maintenance as mt
    if not ha_client.has_token():
        return False
    eid = mt.ENTITY_PREFIX + slug
    _last_state.pop(eid, None)
    return ha_client.delete_state(eid)


async def loop() -> None:
    """Started from main.py's lifespan; cancelled on shutdown. Re-reads both
    App settings (expose_schedule_sensors, sensor_refresh_minutes) every
    tick."""
    last_date = None
    last_sync = None
    was_exposed = None        # None = first tick
    maint_date, maint_sync = None, None
    while True:
        try:
            today = config.today()
            if maint_sync is None or maint_date != today or time.monotonic() - maint_sync >= settings.sensor_refresh_seconds():
                await run_in_threadpool(push_maintenance_blocking)
                maint_date, maint_sync = today, time.monotonic()
            if not exposed():
                if was_exposed is not False:   # startup with it off, or just switched off
                    n = await run_in_threadpool(remove_all_entities_blocking)
                    if n:
                        logger.info("expose_schedule_sensors is off: removed %d sensor(s)", n)
                was_exposed = False
                last_sync = None                # re-publish everything once it's back on
            else:
                was_exposed = True
                today = config.today()
                due = (
                    last_sync is None
                    or last_date != today
                    or time.monotonic() - last_sync >= settings.sensor_refresh_seconds()
                )
                if due:
                    res = await run_in_threadpool(full_sync_blocking)
                    last_date, last_sync = today, time.monotonic()
                    if res["pushed"] or res["failed"]:
                        logger.debug("sensor sync: %s", res)
                else:
                    await run_in_threadpool(push_timed_changes_blocking)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Sensor sync tick failed")
        await asyncio.sleep(TICK_SECONDS)
