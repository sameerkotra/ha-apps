"""Home Assistant sensors (SPEC §7), only while the App settings switch
"Home Assistant sensors" (`ha_sensors`) is on — off on a new install:

- `sensor.household_arcade_<game>_record` — the household record score (best
  of any mode), with who set it, when and the mode as attributes, plus every
  mode's record;
- `sensor.household_arcade_<person>_played_today` — minutes played today; for
  a child also minutes left, the daily limit and whether quiet hours are on;
- `binary_sensor.household_arcade_<person>_playing` — on while that person is
  in a game (a play session with a heartbeat in the last 90 seconds).

A person switched off on Admin → Users gets "unavailable" on their two
sensors (they aren't counted any more). The records are worked out again only
when a score was added or removed (or someone switched off, on or renamed);
heartbeats post at most every BEAT_PUSH_SECONDS (the loop posts every 30 s
anyway).

`<person>` is the person's login name (or display name) made into an entity
id. Entities posted through the Core API aren't stored by Home Assistant, so
the loop re-posts everything every few minutes and on every change; turning
the switch off posts "unavailable" to every entity once, so Home Assistant
shows them as unavailable, and nothing is posted after that.

Best effort: nothing here ever fails an API request. Blocking functions run in
a thread, never while holding a DB connection across a network call.
"""
import logging
import threading
import time
from datetime import timedelta

from starlette.concurrency import run_in_threadpool

from . import auth, config, db, games, ha_client, limits, scores, settings
from .common import ha_people, sensor_publisher

logger = logging.getLogger("ha_sensors")

TICK_SECONDS = 30
FULL_EVERY_SECONDS = 300
PLAYING_WINDOW_SECONDS = 90
PREFIX = "household_arcade_"
BEAT_PUSH_SECONDS = 10           # a heartbeat posts the sensors at most this often (start, end and scores always do)

_records_cache: dict = {"key": None, "value": None}
_push_lock = threading.Lock()
_last_push = [0.0]

# What was last posted (entity_id -> (state, attributes)): only changes are posted between full re-posts.
SENSORS = sensor_publisher.Publisher(post=lambda eid, state, attrs: ha_client.post_state(eid, state, attrs))
_last = SENSORS.memory


def enabled() -> bool:
    return bool(settings.get("ha_sensors")) and ha_client.has_token()


def person_slugs(conn) -> dict[str, str]:
    """user id -> entity slug, unique (a clash gets _2, _3 … in first-seen order)."""
    out, used = {}, set()
    for r in conn.execute("SELECT id, name, username FROM users ORDER BY created_at, id"):
        base = ha_people.slugify(r["username"] or r["name"]) or ha_people.slugify(r["id"])[:12] or "person"
        slug, n = base, 2
        while slug in used:
            slug, n = f"{base}_{n}", n + 1
        used.add(slug)
        out[r["id"]] = slug
    return out


def entities(conn) -> list[tuple[str, str, dict]]:
    """Every entity this app publishes, with its current state and attributes."""
    out = []
    key = (db.generation(),) + scores.records_key(conn)        # a restored backup is another database
    if _records_cache["key"] != key or _records_cache["value"] is None:
        _records_cache["value"], _records_cache["key"] = scores.records(conn), key
    recs = _records_cache["value"]
    for game in games.GAME_IDS:
        r = recs[game]
        out.append((f"sensor.{PREFIX}{game}_record", str(r["score"] if r["score"] is not None else 0), {
            "friendly_name": f"{games.name(game)} record", "icon": "mdi:trophy",
            "person": r["name"], "mode": games.mode_label(game, r["mode"]) if r["mode"] else None, "set_at": r["at"],
            "modes": {games.mode_label(game, m): v for m, v in r["modes"].items()}}))
    slugs = person_slugs(conn)
    cutoff = (config.utcnow() - timedelta(seconds=PLAYING_WINDOW_SECONDS)).isoformat(timespec="seconds")
    playing = {r["user_id"] for r in conn.execute(
        "SELECT user_id FROM play_sessions WHERE ended_at IS NULL AND last_beat_at >= ?", (cutoff,))}
    for u in conn.execute("SELECT * FROM users ORDER BY created_at, id"):
        slug = slugs[u["id"]]
        if u["disabled"]:            # switched off: shown as unavailable, not as a stale number
            out.append((f"sensor.{PREFIX}{slug}_played_today", "unavailable",
                        {"friendly_name": f"{u['name']} played today", "icon": "mdi:gamepad-variant"}))
            out.append((f"binary_sensor.{PREFIX}{slug}_playing", "unavailable",
                        {"friendly_name": f"{u['name']} playing", "icon": "mdi:controller"}))
            continue
        child = bool(u["is_child"]) and not auth.is_admin_identity(u["id"], u["username"])
        st = limits.status(conn, {"id": u["id"], "is_child": child})
        attrs = {"friendly_name": f"{u['name']} played today", "icon": "mdi:gamepad-variant",
                 "unit_of_measurement": "min", "child": child}
        if child:
            attrs.update({"minutes_left": None if st["leftSeconds"] is None else st["leftSeconds"] // 60,
                          "limit_minutes": st["limitMinutes"], "extra_minutes": st["extraMinutes"],
                          "quiet_hours": st["quiet"]})
        out.append((f"sensor.{PREFIX}{slug}_played_today", str(st["usedSeconds"] // 60), attrs))
        out.append((f"binary_sensor.{PREFIX}{slug}_playing", "on" if u["id"] in playing else "off",
                    {"friendly_name": f"{u['name']} playing", "icon": "mdi:controller"}))
    return out


def push_blocking(force: bool = False) -> dict:
    """Post every entity whose state changed (all of them with `force`). Stops at the first failure
    (Home Assistant is probably unreachable; the next tick tries again)."""
    if not enabled():
        return {"pushed": 0, "failed": 0}
    with db.get_conn() as conn:
        items = entities(conn)
    _last_push[0] = time.monotonic()
    return SENSORS.publish(items, force=force, stop_after=1)


def mark_unavailable_blocking() -> int:
    """The switch was turned off: post "unavailable" to every entity once."""
    if settings.get("ha_sensors") or not ha_client.has_token():
        return 0
    with db.get_conn() as conn:
        items = entities(conn)
    n = 0
    for eid, _state, attrs in items:
        if ha_client.post_state(eid, "unavailable", {"friendly_name": attrs.get("friendly_name")}):
            n += 1
    SENSORS.forget()
    return n


def apply_switch_change_blocking() -> dict:
    if settings.get("ha_sensors"):
        return push_blocking(force=True)
    return {"unavailable": mark_unavailable_blocking()}


def person_entities(slug: str) -> list[str]:
    return [f"sensor.{PREFIX}{slug}_played_today", f"binary_sensor.{PREFIX}{slug}_playing"]


def after_person_deleted_blocking(slugs_before: dict[str, str]) -> None:
    """After a person's data was deleted (Admin → Users → Data): their two sensors are removed from Home Assistant
    when they were forgotten (and anyone else's whose entity name moved up, e.g. alice_2 → alice), then everything
    is posted again — the record sensors are worked out again, so a record that was theirs passes to the next best.
    `slugs_before`: person_slugs() read before the delete. Best effort."""
    try:
        if not enabled():
            return
        with db.get_conn() as conn:
            after = person_slugs(conn)
        gone = [eid for slug in sorted(set(slugs_before.values()) - set(after.values())) for eid in person_entities(slug)]
        if gone:
            SENSORS.remove(gone)
        push_blocking()
    except Exception:
        logger.exception("Sensor update after a delete failed")


def changed_blocking() -> None:
    """After a game starts or ends, a score is saved or deleted, or limits change."""
    try:
        push_blocking()
    except Exception:
        logger.exception("Sensor update failed")


def beat_blocking() -> None:
    """After a heartbeat: only when nothing was posted for BEAT_PUSH_SECONDS (several people playing would otherwise
    post every few seconds); the loop posts every TICK_SECONDS anyway."""
    if not enabled():
        return
    with _push_lock:
        if time.monotonic() - _last_push[0] < BEAT_PUSH_SECONDS:
            return
        _last_push[0] = time.monotonic()
    changed_blocking()


async def loop() -> None:
    full_sync = sensor_publisher.Refresh(FULL_EVERY_SECONDS, clock=lambda: time.monotonic())

    async def tick():
        if enabled():
            full = full_sync.due(config.today())
            await run_in_threadpool(push_blocking, full)
            if full:
                full_sync.done(config.today())
        else:
            full_sync.reset()

    await sensor_publisher.run(TICK_SECONDS, tick, log=logger)
