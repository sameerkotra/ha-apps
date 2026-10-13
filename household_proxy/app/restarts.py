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
- One sequence per switch: sites sharing a switch are restarted once, together; it counts for each site that is down,
  and every site on the switch is marked restarting (or, when it has given up, kept from being checked) until it has
  started again. None of them is restarted while any of them is paused or outside its restart window.
- No restart loops: after a restart, a site needs `down_after` failed checks in a row again before the next one; a new
  incident's first restart waits at least the App settings gap after the site's last restart, twice as long for each
  further restart within 24 hours; and a site is restarted automatically at most `max_restarts_per_day` times in 24
  hours (App settings), whatever its incidents.
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
    was = "up" if row["state"] == "waiting" and not row["incident_id"] else row["state"]
    went_down = upd.get("state") == "down" and was in (None, "up")
    if went_down:
        upd.update(restart_due=int(row["max_restarts"] > 0), restarts_used=0, blocked_reason=None)
    elif was == "waiting":
        if upd.get("fails_in_row", 0) < row["down_after"]:
            # one failed check after a restart isn't enough: down_after in a row, at the usual interval
            upd.update(wait_until=None)
            return
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


DAY_S = 86400
MAX_GAP_S = 12 * 3600


def restart_counts(conn, now: float) -> dict:
    """{site id: (power cuts in the last 24 hours, of which automatic)}."""
    return {r[0]: (r[1], r[2] or 0) for r in conn.execute(
        "SELECT site_id, COUNT(*), SUM(manual = 0) FROM restart_log WHERE at > ? GROUP BY site_id", (now - DAY_S,))}


def required_gap(recent: int) -> int:
    """How long a new incident's first restart waits after the site's last one: the App setting after one restart in
    24 hours, then twice as long for each further one (at least 5 minutes, at most 12 hours)."""
    gap = int(settings.get("restart_gap_min")) * 60
    if recent <= 1:
        return gap
    return int(min(max(gap, 300) * 2 ** (recent - 1), MAX_GAP_S))


def _minutes(seconds: int) -> str:
    return f"{seconds // 60} minutes" if seconds < 7200 else f"{seconds // 3600} hours"


def sibling_blocks(row, rows, now: float) -> str | None:
    """Why the switch this site shares may not be used now: a site on it is paused or outside its restart window (the
    power goes off for every device on the switch). None: nothing stops it."""
    from . import watchdog
    for r in [row] + [x for x in rows if x["id"] != row["id"] and x["restart_entity"] == row["restart_entity"]]:
        mine = r["id"] == row["id"]
        if watchdog.paused(r, now):
            return "paused" if mine else f"paused: {r['name']}, on the same switch, is paused"
        if not in_window(r, now):
            return (f"outside its restart window ({r['window_start']}–{r['window_end']})" if mine else
                    f"outside its restart window ({r['name']}, on the same switch: "
                    f"{r['window_start']}–{r['window_end']})")
    return None


def rule_blocks(row, rows, now: float, counts: dict | None = None) -> str | None:
    """Why a wanted restart of this site may not happen now (None: it may)."""
    from . import watchdog
    why = sibling_blocks(row, rows, now)
    if why:
        return why
    if counts is None:
        with db.get_conn() as conn:
            counts = restart_counts(conn, now)
    recent, automatic = counts.get(row["id"], (0, 0))
    cap = int(settings.get("max_restarts_per_day"))
    if automatic >= cap:
        return (f"the most automatic restarts per day ({cap}, App settings) are used up — it keeps going down, so "
                f"it needs a look (Restart now and Try again still work)")
    gap = required_gap(recent)
    if row["restarts_used"] == 0 and row["last_restart_at"] and now - row["last_restart_at"] < gap:
        return f"it was restarted less than {_minutes(gap)} ago"
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
    from .watchdog import ROUND_COLUMNS            # named columns: never the icon's bytes
    return conn.execute(f"SELECT {ROUND_COLUMNS} FROM sites WHERE {where}", args).fetchall()


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


def _claim(entity: str) -> bool:
    """Reserve a switch for one sequence before any waiting, so two can never run at once."""
    if entity in _tasks:
        return False
    _tasks[entity] = "claimed"
    return True


def _release(entity: str, task=None) -> None:
    if _tasks.get(entity) in ("claimed", task):
        _tasks.pop(entity, None)


def _off_entities(conn) -> dict:
    """{entity: [site ids]} of the switches this app turned off and hasn't seen back on."""
    out = {}
    for r in conn.execute("SELECT entity, site_ids FROM switched_off"):
        try:
            out[r["entity"]] = [int(i) for i in json.loads(r["site_ids"])]
        except (ValueError, TypeError):
            out[r["entity"]] = []
    return out


def _snapshot(now: float, rows=None):
    """One look at the database for a round (in a worker thread): the checked sites (unless the watchdog passes the
    ones it has just read), the switches still off, and the restarts of the last 24 hours."""
    with db.get_conn() as conn:
        if rows is None:
            rows = _rows(conn)
        off = _off_entities(conn)
        # a site left "restarting" with no sequence and no switch still off (its switch was changed meanwhile):
        # it waits as after a restart, then is checked again
        covered = {i for ids in off.values() for i in ids}
        for r in rows:
            if r["state"] == "restarting" and r["id"] not in covered and r["restart_entity"] not in _tasks:
                _set(conn, [r["id"]], state="waiting", restart_phase=None, switched_off_at=None, fails_in_row=0,
                     wait_until=now + r["wait_after_s"], next_check_at=now + r["wait_after_s"])
        counts = restart_counts(conn, now) if any(r["restart_due"] for r in rows) else {}
    return rows, off, counts


async def advance(now: float, rows=None) -> None:
    """Start the restarts that are wanted and allowed; make sure no switch this app turned off stays off."""
    rows, off, counts = await asyncio.to_thread(_snapshot, now, rows)
    # never left off: a switch this app turned off, with no sequence running, is turned on first
    for entity in off:
        if _claim(entity):
            await _start(entity, _ensure_on(entity))
    wanted: dict[str, list] = {}
    for r in rows:
        if r["restart_due"] and r["restart_entity"] and r["state"] == "down" and r["restart_entity"] not in _tasks \
                and r["restart_entity"] not in off:
            wanted.setdefault(r["restart_entity"], []).append(r)
    for entity, group in wanted.items():
        if not _claim(entity):
            continue
        started = False
        try:
            started = await _consider(entity, group, rows, now, counts)
        finally:
            if not started:
                _release(entity)


async def _consider(entity: str, group: list, rows: list, now: float, counts: dict | None = None) -> bool:
    """Start the restart of `entity` for the sites in `group` (the ones on it that are down and want one) if the rules
    allow it for at least one of them — it then counts for each; whether it started."""
    allowed, blocked = [], {}
    for r in group:
        why = rule_blocks(r, rows, now, counts)
        (blocked.setdefault(why, []) if why else allowed).append(r)
    if not allowed:
        for why, rs in blocked.items():
            await _block(rs, why)
        return False
    if await reference_down():
        await _block(group, "the network looks down, not the device (the reference address doesn't answer)")
        return False
    st = await _state(entity)
    if st in UNAVAILABLE:
        await _block(group, f"Home Assistant can't reach the switch {entity}")
        return False
    await _start(entity, _sequence(entity, [r["id"] for r in group]))
    return True


async def _start(entity: str, coro) -> None:
    """Run a sequence on a switch already claimed (_claim)."""
    if INLINE:
        try:
            await coro
        finally:
            _release(entity)
        return
    task = asyncio.get_running_loop().create_task(coro)
    _tasks[entity] = task
    task.add_done_callback(lambda t: _release(entity, t))


COUNTED, ALONG, HELD = "counted", "along", "held"


def _kind(r, counted: set) -> str:
    """How a restart touches a site on the switch: COUNTED (it is down: the restart counts in its incident), ALONG (it
    was up: it is restarting too, then waits, then is checked as usual), HELD (it gave up: its state stays, but it isn't
    checked until it has started again)."""
    if r["id"] in counted or (r["incident_id"] and r["state"] in ("down", "waiting", "restarting")):
        return COUNTED
    return HELD if r["state"] == "needs_attention" else ALONG


async def _sequence(entity: str, site_ids: list[int], manual: bool = False) -> None:
    """One restart of the device behind `entity`, counted for every site in `site_ids` (and for any other site on the
    switch that is down); every site on the switch is marked, so none of them is checked while the power is off."""
    from . import watchdog
    now = _clock()
    with db.get_conn() as conn:
        counted = set(site_ids)
        marks = ",".join("?" * len(site_ids)) or "NULL"
        rows = conn.execute(f"SELECT * FROM sites WHERE id IN ({marks}) OR (restart_entity = ? AND check_kind IS NOT "
                            f"NULL) ORDER BY id", (*site_ids, entity)).fetchall()
        if not manual and not any(r["id"] in counted and r["state"] == "down" and r["restart_due"] for r in rows):
            return                         # it came back up (or was changed) meanwhile: nothing to restart for
        if not rows:
            return
        off_for = max(r["off_for_s"] for r in rows if r["id"] in counted or r["restart_entity"] == entity)
        kinds = {r["id"]: _kind(r, counted) for r in rows}
        named = ", ".join(r["name"] for r in rows if kinds[r["id"]] == COUNTED) or rows[0]["name"]
        notes = []
        for r in rows:
            kind = kinds[r["id"]]
            cols = dict(restart_phase="off", switched_off_at=now, last_restart_at=now)
            if kind == COUNTED:
                used = r["restarts_used"] + 1
                cols.update(state="restarting", state_since=watchdog.iso(now), restart_due=0, blocked_reason=None,
                            restarts_used=used, wait_until=None)
                notes.append((r["id"], {"n": used, "of": max(r["max_restarts"], used), "manual": manual}))
            elif kind == ALONG:
                cols.update(state="restarting", state_since=watchdog.iso(now), wait_until=None, fails_in_row=0)
                notes.append((r["id"], {"with": named, "manual": manual}))
            else:
                cols.update(wait_until=now + off_for + 2 * STATE_WAIT_S + r["wait_after_s"])
                notes.append((r["id"], {"with": named, "manual": manual}))
            _set(conn, [r["id"]], **cols)
            if r["incident_id"] and kind != ALONG:
                conn.execute("UPDATE incidents SET restarts = restarts + 1 WHERE id = ?", (r["incident_id"],))
            conn.execute("INSERT INTO restart_log (site_id, at, manual) VALUES (?, ?, ?)",
                         (r["id"], now, int(manual or r["id"] not in counted)))
        conn.execute("INSERT OR REPLACE INTO switched_off (entity, site_ids, at) VALUES (?, ?, ?)",
                     (entity, json.dumps([r["id"] for r in rows]), now))
    sites.invalidate()
    for sid, info in notes:
        await _alert(sid, "restarting", info)
    logger.info("restarting %s for %s", entity, [r["slug"] for r in rows])
    try:
        await asyncio.to_thread(ha_logbook, "Household Proxy", "restarted " + ", ".join(r["name"] for r in rows) +
                                (" (by an admin)" if manual else " (not answering)"), entity)
    except Exception:                                     # noqa: BLE001 — the logbook line is optional
        logger.debug("logbook line failed", exc_info=True)
    if not await _call("turn_off", entity):
        with db.get_conn() as conn:
            conn.execute("DELETE FROM switched_off WHERE entity = ?", (entity,))
            for r in rows:
                kind = kinds[r["id"]]
                # last_restart_at stays: the gap rule then holds back the next try for a while
                _set(conn, [r["id"]], restart_phase=None, switched_off_at=None, state=r["state"],
                     state_since=r["state_since"], restarts_used=r["restarts_used"], wait_until=r["wait_until"],
                     fails_in_row=r["fails_in_row"],
                     restart_due=int(r["restart_due"] or (kind == COUNTED and not manual and r["state"] == "down")),
                     blocked_reason="couldn't turn the switch off" if kind == COUNTED else r["blocked_reason"])
                if r["incident_id"] and kind != ALONG:
                    conn.execute("UPDATE incidents SET restarts = MAX(restarts - 1, 0) WHERE id = ?",
                                 (r["incident_id"],))
                conn.execute("DELETE FROM restart_log WHERE site_id = ? AND at = ?", (r["id"], now))
        sites.invalidate()
        for r in rows:
            if kinds[r["id"]] == COUNTED:
                await _alert(r["id"], "couldnt_restart", {"why": f"Home Assistant couldn't turn {entity} off"})
        return
    ids = [r["id"] for r in rows]
    try:
        await _wait_for(entity, "off")
        await sleep(off_for)
    finally:
        on = await _turn_on(entity, ids)
    if not on:
        return
    await _wait_for(entity, "on")
    now = _clock()
    with db.get_conn() as conn:
        conn.execute("DELETE FROM switched_off WHERE entity = ?", (entity,))
        for r in rows:
            wait = r["wait_after_s"]
            if kinds[r["id"]] == HELD:
                _set(conn, [r["id"]], restart_phase=None, switched_off_at=None, wait_until=now + wait,
                     next_check_at=now + wait)
            else:
                _set(conn, [r["id"]], restart_phase=None, switched_off_at=None, state="waiting",
                     state_since=watchdog.iso(now), wait_until=now + wait, next_check_at=now + wait, fails_in_row=0)
    sites.invalidate()


async def _turn_on(entity: str, site_ids: list[int] | None = None) -> bool:
    """switch.turn_on, retried every 30 s for 10 minutes; admins are told if it still fails (and the app keeps
    trying: the sites stay marked "off")."""
    with db.get_conn() as conn:
        if site_ids is None:
            site_ids = _off_entities(conn).get(entity, [])
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
            ids = _off_entities(conn).get(entity, [])
            conn.execute("DELETE FROM switched_off WHERE entity = ?", (entity,))
            for i in ids:
                r = conn.execute("SELECT * FROM sites WHERE id = ?", (i,)).fetchone()
                if r is None:
                    continue                      # the site was deleted meanwhile: the switch is on again anyway
                upd = {"restart_phase": None, "switched_off_at": None, "wait_until": now + r["wait_after_s"],
                       "next_check_at": now + r["wait_after_s"]}
                if r["state"] == "restarting":
                    upd.update(state="waiting", state_since=watchdog.iso(now), fails_in_row=0)
                _set(conn, [i], **upd)
        sites.invalidate()


def recover() -> None:
    """At start-up: sites left mid-restart are put back as "restarting", so the first round turns their switch on
    before anything else."""
    with db.get_conn() as conn:
        # (a site that had given up keeps its state: it was only kept from being checked)
        n = conn.execute("UPDATE sites SET state = 'restarting' WHERE restart_phase IS NOT NULL AND "
                         "(state IS NULL OR state != 'needs_attention')").rowcount
    if n:
        logger.warning("%d site(s) were left mid-restart: their switches will be turned on first", n)


# --------------------------------------------------------------------------------------------- by hand (admins)

async def restart_now(site_id: int) -> str | None:
    """Admin → Restart now (the page asks first): counts in the current incident if there is one (and for the other
    sites on the switch that are down). The switch must be reachable and no site on it paused; the network rules,
    the restart windows and the daily most don't apply (an admin decided). Returns a reason it can't, or None."""
    from . import watchdog
    now = _clock()
    with db.get_conn() as conn:
        r = conn.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
        if r is None or not r["restart_entity"]:
            return "This site has no switch to restart it with."
        entity = r["restart_entity"]
        if entity in _off_entities(conn):
            return "That switch is still off from an earlier restart; the app is turning it back on."
        others = conn.execute("SELECT * FROM sites WHERE restart_entity = ? AND id != ? AND check_kind IS NOT NULL",
                              (entity, site_id)).fetchall()
    for o in others:
        if watchdog.paused(o, now):
            return f"{o['name']} is on the same switch and is paused for maintenance: resume it first."
    down = [o["id"] for o in others if o["state"] == "down" and o["restart_due"]]
    if not _claim(entity):
        return "A restart of this switch is already running."
    try:
        if await _state(entity) in UNAVAILABLE:
            _release(entity)
            return f"Home Assistant can't reach the switch {entity}."
    except BaseException:
        _release(entity)
        raise
    await _start(entity, _sequence(entity, [site_id] + down, manual=True))
    return None


def try_again(site_id: int) -> bool:
    """Admin → Try again after giving up: the restart count starts over (the daily count too)."""
    with db.get_conn() as conn:
        # last_restart_at cleared: the gap between incidents doesn't hold back an admin's Try again
        n = conn.execute("UPDATE sites SET state = 'down', restart_due = 1, restarts_used = 0, blocked_reason = NULL, "
                         "last_restart_at = NULL, fails_in_row = 0 "
                         "WHERE id = ? AND state = 'needs_attention' AND restart_entity IS NOT NULL",
                         (site_id,)).rowcount
        if n:
            conn.execute("DELETE FROM restart_log WHERE site_id = ?", (site_id,))
    sites.invalidate()
    return bool(n)
