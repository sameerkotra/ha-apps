"""Auto-restart (spec/SPEC.md §13.3): a stuck device is power-cycled with the Home Assistant switch (a smart plug) an
admin chose for its site — off, a few seconds, on — as many times as allowed, then the app gives up and says so.

One incident: the site goes down → a restart is wanted (`restart_due`) → when the safety rules allow, the sequence
runs: switch.turn_off, wait for "off" (≤ 10 s), wait *Off for*, switch.turn_on, wait for "on" (≤ 10 s) → *Waiting
after restart*: no checks until *Wait after restart* has passed, then one check: up → the incident closes; down → the
next restart, or *Needs attention* when all are used (checks go on, no more restarts until it is up or an admin
presses Try again).

Safety rules:
- Never left off: `restart_phase` is written before each step. At start-up, and whenever the app finds a site still
  marked "off" with no sequence running (e.g. Home Assistant answers again), the switch is turned on first; a failed
  turn-on is retried every 30 s for 10 minutes, then admins are told — and it keeps being retried after that.
- Not when the network is the problem: before each restart the reference address (App settings) must answer, and
  when at least 3 sites are checked, more than half of them must not have gone down within the same 2 minutes.
- A switch Home Assistant can't reach (unavailable/unknown) → no restart, "couldn't restart".
- One sequence per switch: sites sharing a switch are restarted once, together; it counts for each.
- No restart loops: an incident's first restart waits at least the App settings gap after the site's last restart.
- Only during the site's restart window, if it has one; never while paused.
- Only switch.turn_off / switch.turn_on, only on the entity the admin chose (and confirmed doesn't power Home
  Assistant, the router or the network switch).
"""
import asyncio
import json
import logging
import re
from datetime import datetime, timezone

from . import config, db, netguard, settings, sites

logger = logging.getLogger("restarts")

SWITCH_RE = re.compile(r"^switch\.[a-z0-9_]{1,100}$")
WINDOW_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
STATE_WAIT_S = 10
RETRY_EVERY_S = 30
RETRY_FOR_S = 600
NETWORK_WINDOW_S = 120
UNAVAILABLE = (None, "unavailable", "unknown")

sleep = asyncio.sleep                 # the tests replace it (and the watchdog's clock) to move time by hand
INLINE = False                        # the tests: run sequences inside the round instead of as background tasks
_tasks: dict[str, asyncio.Task] = {}  # switch entity -> its running sequence


def _clock() -> float:
    from . import watchdog
    return watchdog.clock()


# --------------------------------------------------------------------------------------------- Home Assistant

def ha_state(entity: str) -> str | None:
    """The switch's state as Home Assistant has it ("on", "off", "unavailable", …), or None if it can't be read."""
    from .common import ha_client
    status, body = ha_client.request("GET", f"/states/{entity}")
    if status != 200:
        return None
    try:
        return json.loads(body).get("state")
    except (ValueError, AttributeError):
        return None


def ha_call(service: str, entity: str) -> bool:
    """switch.turn_off / switch.turn_on on exactly this entity."""
    from .common import ha_client
    if service not in ("turn_off", "turn_on") or not SWITCH_RE.match(entity):
        raise ValueError("not allowed")
    status, _ = ha_client.request("POST", f"/services/switch/{service}", {"entity_id": entity})
    return status in (200, 201)


def switches() -> list[dict] | None:
    """Home Assistant's switch entities with their name and state (Admin → Sites), or None if HA can't be asked."""
    from .common import ha_client
    states = ha_client.fetch_states_blocking(max_age=10, what="switches")
    if states is None:
        return None
    return sorted(({"entity_id": s["entity_id"], "name": (s.get("attributes") or {}).get("friendly_name")
                    or s["entity_id"], "state": s.get("state")}
                   for s in states if isinstance(s, dict) and str(s.get("entity_id", "")).startswith("switch.")),
                  key=lambda x: x["name"].lower())


def ha_logbook(name: str, message: str, entity: str) -> None:
    """A line in Home Assistant's logbook for the switch (best effort)."""
    from .common import ha_client
    if ha_client.has_token():
        ha_client.request("POST", "/services/logbook/log", {"name": name, "message": message, "entity_id": entity})


async def _state(entity: str) -> str | None:
    return await asyncio.to_thread(ha_state, entity)


async def _call(service: str, entity: str) -> bool:
    try:
        return await asyncio.to_thread(ha_call, service, entity)
    except Exception:                                     # noqa: BLE001 — treated as "didn't work"
        logger.exception("switch.%s %s failed", service, entity)
        return False


async def _wait_for(entity: str, want: str) -> bool:
    for _ in range(STATE_WAIT_S):
        if await _state(entity) == want:
            return True
        await sleep(1)
    return False


# --------------------------------------------------------------------------------------------- the watchdog's hooks

def on_up(conn, row, upd) -> None:
    upd.update(restart_due=0, restarts_used=0, wait_until=None, blocked_reason=None)


def on_failed(conn, row, upd, now, events) -> None:
    """After a failed check (the watchdog has already moved the state)."""
    if not row["restart_entity"]:
        return
    went_down = upd.get("state") == "down" and row["state"] in (None, "up")
    if went_down:
        upd.update(restart_due=int(row["max_restarts"] > 0), restarts_used=0, blocked_reason=None)
    elif row["state"] == "waiting":
        if row["restarts_used"] < row["max_restarts"]:
            upd.update(state="down", restart_due=1, wait_until=None)
        else:
            from . import watchdog
            upd.update(state="needs_attention", state_since=watchdog.iso(now), restart_due=0, wait_until=None)
            if row["incident_id"]:
                conn.execute("UPDATE incidents SET outcome = 'gave_up' WHERE id = ?", (row["incident_id"],))
            events.append(("gave_up", {"restarts": row["restarts_used"]}))


def may_check(row, now) -> bool:
    """No checks while restarting, nor while waiting after a restart."""
    if row["state"] == "restarting":
        return False
    return not (row["wait_until"] and row["wait_until"] > now)


# --------------------------------------------------------------------------------------------- the safety rules

def _local_hm(now: float) -> str:
    return config.ZONE.now(datetime.fromtimestamp(now, timezone.utc)).strftime("%H:%M")


def in_window(row, now: float) -> bool:
    start, end = row["window_start"], row["window_end"]
    if not start or not end:
        return True
    t = _local_hm(now)
    return start <= t < end if start <= end else (t >= start or t < end)


def _epoch(text):
    from . import watchdog
    return watchdog.epoch(text)


def rule_blocks(row, rows, now: float) -> str | None:
    """Why a wanted restart of this site may not happen now (None: it may)."""
    from . import watchdog
    if watchdog.paused(row, now):
        return "paused"
    if not in_window(row, now):
        return f"outside its restart window ({row['window_start']}–{row['window_end']})"
    gap = int(settings.get("restart_gap_min")) * 60
    if row["restarts_used"] == 0 and row["last_restart_at"] and now - row["last_restart_at"] < gap:
        return f"it was restarted less than {gap // 60} minutes ago"
    checked = [r for r in rows if not watchdog.paused(r, now)]
    if len(checked) >= 3:
        mine = _epoch(row["state_since"]) or now
        together = [r for r in checked if r["state"] in ("down", "restarting", "waiting", "needs_attention")
                    and abs((_epoch(r["state_since"]) or now) - mine) <= NETWORK_WINDOW_S]
        if len(together) > len(checked) / 2:
            return "the network looks down, not the device (most checked sites went down together)"
    return None


async def reference_down() -> bool:
    """The reference address (App settings) doesn't answer — a TCP connection to host:port, or to port 53, 80 or 443
    of a host given alone. False when none is set."""
    ref = settings.get("monitor_reference")
    if not ref:
        return False
    try:
        _, host, port = netguard.parse_monitor_target(ref)
        addrs = await netguard.resolve(host, port or 0, False)
    except netguard.TargetError:
        return True
    for p in ([port] if port else [53, 80, 443]):
        try:
            _, w = await asyncio.wait_for(asyncio.open_connection(addrs[0], p), 3)
            w.close()
            return False
        except ConnectionRefusedError:
            return False                   # something answered (refusing counts: the network is up)
        except (OSError, asyncio.TimeoutError):
            continue
    return True


# --------------------------------------------------------------------------------------------- sequences

def _rows(conn, where: str = "check_kind IS NOT NULL", args=()):
    return conn.execute(f"SELECT * FROM sites WHERE {where}", args).fetchall()


def _set(conn, site_ids, **cols) -> None:
    conn.executemany(f"UPDATE sites SET {', '.join(f'{k} = ?' for k in cols)} WHERE id = ?",
                     [(*cols.values(), i) for i in site_ids])


async def _alert(site_id: int, kind: str, info: dict) -> None:
    from . import watchdog
    await asyncio.to_thread(watchdog.alert, site_id, kind, info)


async def _block(rows_, reason: str) -> None:
    """Record why the restart isn't happening; tell people once per reason (not for paused or the window)."""
    tell = []
    with db.get_conn() as conn:
        for r in rows_:
            if r["blocked_reason"] != reason:
                _set(conn, [r["id"]], blocked_reason=reason)
                if not reason.startswith(("paused", "outside its restart window", "it was restarted")):
                    tell.append(r["id"])
    for sid in tell:
        await _alert(sid, "couldnt_restart", {"why": reason})


async def advance(now: float) -> None:
    """Start the restarts that are wanted and allowed; make sure no switch this app turned off stays off."""
    with db.get_conn() as conn:
        rows = _rows(conn)
    # never left off: a switch marked off with no sequence running is turned on first
    for entity in {r["restart_entity"] for r in rows if r["restart_phase"] and r["restart_entity"]}:
        if entity not in _tasks:
            await _start(entity, _ensure_on(entity))
    wanted: dict[str, list] = {}
    for r in rows:
        if r["restart_due"] and r["restart_entity"] and r["state"] == "down" and r["restart_entity"] not in _tasks:
            wanted.setdefault(r["restart_entity"], []).append(r)
    for entity, group in wanted.items():
        allowed, blocked = [], {}
        for r in group:
            why = rule_blocks(r, rows, now)
            (blocked.setdefault(why, []) if why else allowed).append(r)
        for why, rs in blocked.items():
            await _block(rs, why)
        if not allowed:
            continue
        if await reference_down():
            await _block(allowed, "the network looks down, not the device (the reference address doesn't answer)")
            continue
        st = await _state(entity)
        if st in UNAVAILABLE:
            await _block(allowed, f"Home Assistant can't reach the switch {entity}")
            continue
        await _start(entity, _sequence(entity, [r["id"] for r in allowed]))


async def _start(entity: str, coro) -> None:
    if INLINE:
        _tasks[entity] = None                             # marks it running
        try:
            await coro
        finally:
            _tasks.pop(entity, None)
        return
    task = asyncio.get_running_loop().create_task(coro)
    _tasks[entity] = task
    task.add_done_callback(lambda t: _tasks.pop(entity, None))


async def _sequence(entity: str, site_ids: list[int], manual: bool = False) -> None:
    """One restart of the device behind `entity`, counted for every site in `site_ids`."""
    from . import watchdog
    now = _clock()
    with db.get_conn() as conn:
        rows = [r for r in (conn.execute("SELECT * FROM sites WHERE id = ?", (i,)).fetchone() for i in site_ids) if r]
        if not rows:
            return
        off_for = max(r["off_for_s"] for r in rows)
        notes = []
        for r in rows:
            used = r["restarts_used"] + 1
            _set(conn, [r["id"]], state="restarting", state_since=watchdog.iso(now), restart_phase="off",
                 switched_off_at=now, restart_due=0, blocked_reason=None, restarts_used=used, last_restart_at=now,
                 wait_until=None)
            if r["incident_id"]:
                conn.execute("UPDATE incidents SET restarts = restarts + 1 WHERE id = ?", (r["incident_id"],))
            notes.append((r["id"], used, max(r["max_restarts"], used)))
    sites.invalidate()
    for sid, n, of in notes:
        await _alert(sid, "restarting", {"n": n, "of": of, "manual": manual})
    logger.info("restarting %s for %s", entity, [r["slug"] for r in rows])
    try:
        await asyncio.to_thread(ha_logbook, "Household Proxy", "restarted " + ", ".join(r["name"] for r in rows) +
                                (" (by an admin)" if manual else " (not answering)"), entity)
    except Exception:                                     # noqa: BLE001 — the logbook line is optional
        logger.debug("logbook line failed", exc_info=True)
    if not await _call("turn_off", entity):
        with db.get_conn() as conn:
            for r in rows:
                _set(conn, [r["id"]], restart_phase=None, switched_off_at=None, state=r["state"] or "down",
                     state_since=r["state_since"], restart_due=int(not manual and r["state"] == "down"),
                     restarts_used=r["restarts_used"], blocked_reason="couldn't turn the switch off")
                if r["incident_id"]:
                    conn.execute("UPDATE incidents SET restarts = MAX(restarts - 1, 0) WHERE id = ?",
                                 (r["incident_id"],))
        sites.invalidate()
        for r in rows:
            await _alert(r["id"], "couldnt_restart", {"why": f"Home Assistant couldn't turn {entity} off"})
        return
    try:
        await _wait_for(entity, "off")
        await sleep(off_for)
    finally:
        on = await _turn_on(entity, site_ids)
    if not on:
        return
    await _wait_for(entity, "on")
    now = _clock()
    with db.get_conn() as conn:
        for r in rows:
            wait = r["wait_after_s"]
            _set(conn, [r["id"]], restart_phase=None, switched_off_at=None, state="waiting",
                 state_since=watchdog.iso(now), wait_until=now + wait, next_check_at=now + wait)
    sites.invalidate()


async def _turn_on(entity: str, site_ids: list[int] | None = None) -> bool:
    """switch.turn_on, retried every 30 s for 10 minutes; admins are told if it still fails (and the app keeps
    trying: the sites stay marked "off")."""
    with db.get_conn() as conn:
        if site_ids is None:
            site_ids = [r["id"] for r in _rows(conn, "restart_entity = ? AND restart_phase IS NOT NULL", (entity,))]
        _set(conn, site_ids, restart_phase="on")
    started = _clock()
    while True:
        if await _call("turn_on", entity):
            return True                           # the caller clears restart_phase once the sites move on
        if _clock() - started >= RETRY_FOR_S:
            logger.error("couldn't turn %s back on for 10 minutes", entity)
            for sid in site_ids:
                await _alert(sid, "couldnt_restart", {"why": f"Home Assistant couldn't turn {entity} back on — "
                                                              "it may still be off"})
            await _tell_admins(entity)
            return False
        await sleep(RETRY_EVERY_S)


async def _tell_admins(entity: str) -> None:
    """Admins hear about a switch left off even if they chose not to hear about the site."""
    from . import watchdog
    from .common import ha_people
    with db.get_conn() as conn:
        services = []
        for uid in sites.admin_ids(conn):
            services += ha_people.phones_for(uid)
    for svc in dict.fromkeys(services):
        await asyncio.to_thread(watchdog.send, svc, "Household Proxy: a switch may be off",
                                f"{entity} was turned off for a restart and couldn't be turned back on.", {})


async def _ensure_on(entity: str) -> None:
    """A switch marked off with no sequence running (the app restarted, or Home Assistant was away): turn it on, then
    let its sites be checked again."""
    logger.warning("%s was left off by an interrupted restart: turning it on", entity)
    if await _turn_on(entity):
        now = _clock()
        with db.get_conn() as conn:
            from . import watchdog
            rows = _rows(conn, "restart_entity = ? AND state = 'restarting'", (entity,))
            for r in rows:
                _set(conn, [r["id"]], state="waiting", state_since=watchdog.iso(now), restart_phase=None,
                     switched_off_at=None, wait_until=now + r["wait_after_s"], next_check_at=now + r["wait_after_s"])
            conn.execute("UPDATE sites SET restart_phase = NULL, switched_off_at = NULL WHERE restart_entity = ?",
                         (entity,))
        sites.invalidate()


def recover() -> None:
    """At start-up: sites left mid-restart are put back as "restarting", so the first round turns their switch on
    before anything else."""
    with db.get_conn() as conn:
        n = conn.execute("UPDATE sites SET state = 'restarting' WHERE restart_phase IS NOT NULL").rowcount
    if n:
        logger.warning("%d site(s) were left mid-restart: their switches will be turned on first", n)


# --------------------------------------------------------------------------------------------- by hand (admins)

async def restart_now(site_id: int) -> str | None:
    """Admin → Restart now (the page asks first): counts in the current incident if there is one. The switch must be
    reachable; the network rules don't apply (an admin decided). Returns a reason it can't, or None."""
    with db.get_conn() as conn:
        r = conn.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
    if r is None or not r["restart_entity"]:
        return "This site has no switch to restart it with."
    entity = r["restart_entity"]
    if entity in _tasks:
        return "A restart of this switch is already running."
    if await _state(entity) in UNAVAILABLE:
        return f"Home Assistant can't reach the switch {entity}."
    await _start(entity, _sequence(entity, [site_id], manual=True))
    return None


def try_again(site_id: int) -> bool:
    """Admin → Try again after giving up: the restart count starts over."""
    with db.get_conn() as conn:
        n = conn.execute("UPDATE sites SET state = 'down', restart_due = 1, restarts_used = 0, blocked_reason = NULL "
                         "WHERE id = ? AND state = 'needs_attention' AND restart_entity IS NOT NULL",
                         (site_id,)).rowcount
    sites.invalidate()
    return bool(n)
