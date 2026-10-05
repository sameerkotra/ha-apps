# Shared file: edit common/python/sensor_publisher.py and run tools/sync_common.py; don't edit this copy. sha256=278192de22d7cdac2a6973faa8ad27b65ab77c9402922c013d70870bba6adebb
"""Sensors an app publishes into Home Assistant itself (shared by the household apps:
common/python/sensor_publisher.py, copied into each app's app/common/ by tools/sync_common.py).
Standard library only; the default HTTP calls are the shared `ha_client` (POST / DELETE
/api/states/<entity_id>). Each app keeps its own entity ids, states, attributes, intervals,
settings and log lines; this module is the mechanics around them.

Home Assistant doesn't keep states posted this way across its own restarts, so an app re-posts its
sensors every few minutes, posts a changed one straight away, and removes them (or marks them
unavailable) when its switch is turned off.

    SENSORS = sensor_publisher.Publisher()           # or post=/delete= hooks: (entity_id, state, attrs) -> bool
    SENSORS.post("sensor.x", "3", {...})             # True / False; None = same as last time, not sent
    SENSORS.post("sensor.x", "3", {...}, force=True) # send even when unchanged (a full re-post)
    SENSORS.publish(items, force=True, stop_after=1) # {"pushed", "failed"}; items = [(entity_id, state, attrs)]
    SENSORS.remove(entity_ids)                       # {"removed", "failed"} (DELETE; forgets each one removed)
    SENSORS.forget()                                 # forget what was posted (the next post sends again)
    SENSORS.unchanged(name, key, max_age=900)        # the memory on its own, for a caller's own fingerprint

    refresh = sensor_publisher.Refresh(300)          # "re-post everything" timing, kept in the loop
    if refresh.due(config.today()): ...; refresh.done(config.today())
    refresh.reset()                                  # e.g. switched off: due again as soon as it's back on

    await sensor_publisher.run(30, tick, log=logger) # call `await tick()` every 30 s until cancelled

What "changed" means is the `key` function: by default the state and every attribute
(`state_and_attributes`); `state_only` compares the state alone. `clock` is time.monotonic unless
given (pass `lambda: time.monotonic()` from the app's module when its tests replace its `time`).
"""
import asyncio
import logging
import time

logger = logging.getLogger("sensor_publisher")


def state_and_attributes(state, attributes: dict):
    """The default change key: the state and every attribute (order-independent)."""
    return state, repr(sorted((attributes or {}).items()))


def state_only(state, attributes: dict):
    return state


def _ha_post(entity_id: str, state, attributes: dict) -> bool:
    from . import ha_client
    return ha_client.post_state(entity_id, state, attributes)


def _ha_delete(entity_id: str) -> bool:
    from . import ha_client
    return ha_client.delete_state(entity_id)


class Publisher:
    """What was posted (entity id -> change key and when), and the calls that post and remove."""

    def __init__(self, *, post=None, delete=None, key=state_and_attributes, clock=None):
        self._post = post or _ha_post
        self._delete = delete or _ha_delete
        self.key = key
        self._clock = clock or time.monotonic
        self.memory: dict = {}        # name -> (key, clock() when remembered)

    # ---- memory ----
    def unchanged(self, name, key, max_age: float | None = None) -> bool:
        """True when `key` is what was remembered for `name` (and, with max_age, not longer ago than that)."""
        last = self.memory.get(name)
        if last is None or last[0] != key:
            return False
        return max_age is None or self._clock() - last[1] < max_age

    def remember(self, name, key) -> None:
        self.memory[name] = (key, self._clock())

    def forget(self, name=None) -> None:
        """Forget one entity (or everything): its next post is sent even if unchanged."""
        if name is None:
            self.memory.clear()
        else:
            self.memory.pop(name, None)

    def names(self) -> list:
        return list(self.memory)

    # ---- Home Assistant ----
    def post(self, entity_id: str, state, attributes: dict, *, force: bool = False):
        """Post one entity unless it is unchanged (None then). Remembered only when the post worked."""
        key = self.key(state, attributes)
        if not force and self.unchanged(entity_id, key):
            return None
        ok = self._post(entity_id, state, attributes)
        if ok:
            self.remember(entity_id, key)
        return ok

    def delete(self, entity_id: str) -> bool:
        """Remove one entity; forgotten when that worked."""
        ok = self._delete(entity_id)
        if ok:
            self.forget(entity_id)
        return ok

    def publish(self, items, *, force: bool = False, stop_after: int | None = None) -> dict:
        """Post each (entity_id, state, attributes) in order; unchanged ones are skipped unless `force`.
        With `stop_after`, gives up after that many failures in a row (Home Assistant is probably
        unreachable; the next run tries again)."""
        result = {"pushed": 0, "failed": 0}
        streak = 0
        for entity_id, state, attributes in items:
            ok = self.post(entity_id, state, attributes, force=force)
            if ok is None:
                continue
            if ok:
                result["pushed"] += 1
                streak = 0
            else:
                result["failed"] += 1
                streak += 1
                if stop_after and streak >= stop_after:
                    break
        return result

    def remove(self, entity_ids) -> dict:
        """Delete each entity in order (a switch turned off)."""
        result = {"removed": 0, "failed": 0}
        for entity_id in entity_ids:
            if self.delete(entity_id):
                result["removed"] += 1
            else:
                result["failed"] += 1
        return result


class Refresh:
    """When to re-post everything: at once the first time (or after reset()), when the date changes,
    and every `every` seconds (a number, or a function read each time for a live setting)."""

    def __init__(self, every, *, clock=None):
        self._every = every
        self._clock = clock or time.monotonic
        self._last = None
        self._date = None

    def every(self) -> float:
        return self._every() if callable(self._every) else self._every

    def due(self, today=None) -> bool:
        return (self._last is None or self._date != today
                or self._clock() - self._last >= self.every())

    def done(self, today=None) -> None:
        self._last, self._date = self._clock(), today

    def reset(self) -> None:
        self._last = None


async def run(tick_seconds: float, tick, *, log: logging.Logger | None = None,
              error: str = "Sensor sync tick failed") -> None:
    """`await tick()` every `tick_seconds` until cancelled; a failed tick is logged (log.exception(error))
    and the loop carries on."""
    log = log or logger
    while True:
        try:
            await tick()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception(error)
        await asyncio.sleep(tick_seconds)
