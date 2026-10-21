"""Sensors in Home Assistant (optional, off by default; spec/SPEC.md §7 "Sensors in Home Assistant").

- `binary_sensor.phones_<phone>_online` (device_class connectivity): on while the phone is Online, with its status,
  battery and last check-in as attributes — never a phone whose owner paused being watched or is in their
  do-not-monitor hours (then: unavailable). Only monitored phones.
- `sensor.phones_online_count`: monitored phones online, with the total.

**Check-now buttons:** a `button.` entity can't be made through Home Assistant's REST API (a state posted there has
no press action), so Check now from a dashboard or an automation is an **event** instead:
`household_phones_check_now` with `{phone: <the phone's id or the <phone> part of its entity id>}`, which this app
listens for while this switch is on; the result comes back as `household_phones_check_result`
`{phone, check, result: online | no_reply | cant_ask}`. DOCS.md has a script to put on a dashboard.

Home Assistant forgets these states when it restarts, so they are re-posted every 5 minutes (shared
sensor_publisher); turned off, they are removed.
"""
import logging

from . import config, db, dnm, monitor, phones, settings
from .common import ha_people, sensor_publisher

logger = logging.getLogger("ha_sensors")

SENSORS = sensor_publisher.Publisher()
REFRESH = sensor_publisher.Refresh(300)
_published: set = set()


def slug(conn, p) -> str:
    return ha_people.slugify(phones.display_name(conn, p))[:40] or p["id"]


def items(now: float | None = None) -> list:
    now = config.clock() if now is None else now
    out, online, total = [], 0, 0
    with db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM phones WHERE retired = 0 AND monitored = 1").fetchall()
        for p in rows:
            eid = f"binary_sensor.phones_{slug(conn, p)}_online"
            name = phones.display_name(conn, p)
            hidden = "paused" if monitor.watching_paused(p, now) else "not_monitored" if dnm.active(p, now) else None
            if hidden:
                out.append((eid, "unavailable", {"friendly_name": f"{name} online", "device_class": "connectivity",
                                                 "status": hidden}))
                continue
            total += 1
            on = p["status"] == "online"
            online += on
            out.append((eid, "on" if on else "off", {
                "friendly_name": f"{name} online", "device_class": "connectivity", "status": p["status"],
                "battery": p["battery"], "phone": p["id"],
                "last_check_in": config.iso(p["last_checkin"]) if p["last_checkin"] else None}))
    out.append(("sensor.phones_online_count", str(online), {"friendly_name": "Phones online", "total": total,
                                                             "unit_of_measurement": "phones", "icon": "mdi:cellphone-check"}))
    return out


def tick() -> dict:
    if not settings.get("ha_sensors"):
        if _published:
            SENSORS.remove(sorted(_published))
            _published.clear()
            REFRESH.reset()
        return {"pushed": 0, "failed": 0}
    its = items()
    force = REFRESH.due(config.today())
    stale = _published - {e for e, _, _ in its}
    if stale:
        SENSORS.remove(sorted(stale))
    r = SENSORS.publish(its, force=force, stop_after=3)
    _published.clear()
    _published.update(e for e, _, _ in its)
    if force and not r["failed"]:
        REFRESH.done(config.today())
    return r


async def loop() -> None:
    await sensor_publisher.run(30, _tick_async, log=logger)


async def _tick_async():
    from starlette.concurrency import run_in_threadpool
    await run_in_threadpool(tick)
