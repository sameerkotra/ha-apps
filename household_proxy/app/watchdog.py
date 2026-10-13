"""Monitoring (spec/SPEC.md §13): checks on a schedule, each site's state, incidents, alerts to the chosen people,
and a binary sensor per site in Home Assistant. Auto-restart (§13.3) is in restarts.py, called from here.

States (§13.2): not checked (NULL) · up · down · restarting · waiting (after a restart) · needs_attention (gave up),
plus paused (paused_until in the future). A site goes down only after `down_after` failed checks in a row, and up
again after one good check.

Everything time-related reads `clock()` (epoch seconds), so the tests can move time by hand; `tick()` does one round
(the background loop calls it every TICK_S seconds).
"""
import asyncio
import calendar
import json
import logging
import socket
import struct
import time
import zlib

import httpx

from . import config, db, netguard, probe, sidebar, sites
from .common import ha_notify, ha_people, sensor_publisher

logger = logging.getLogger("watchdog")

TICK_S = 2
MAX_PARALLEL = 8
UNTIL_RESUMED = 1e18
SENSOR_REFRESH_S = 300
INCIDENT_DAYS = 90
RAW_CHECK_DAYS = 7
STRIP_CACHE_S = 60

clock = time.time
SENSORS = sensor_publisher.Publisher()
INLINE = False                        # the tests: a round waits for its checks (else they run on as tasks)
_refresh = {"at": 0.0}
_running: set[int] = set()            # sites with a check in flight
_tasks: set = set()                   # the checks running as tasks (kept so they aren't collected early)

# What a round reads of each checked site (named: never the icon's bytes).
ROUND_COLUMNS = ("id, slug, name, check_kind, check_path, check_every_s, check_timeout_s, down_after, ok_codes, "
                 "paused_until, state, state_since, fails_in_row, incident_id, next_check_at, restart_entity, "
                 "off_for_s, wait_after_s, max_restarts, window_start, window_end, restart_due, restarts_used, "
                 "restart_phase, wait_until, last_restart_at, blocked_reason, expose_sensor")


def iso(epoch: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def epoch(text: str | None) -> float | None:
    if not text:
        return None
    try:
        return float(calendar.timegm(time.strptime(text, "%Y-%m-%dT%H:%M:%SZ")))
    except ValueError:
        return None


# --------------------------------------------------------------------------------------------- the checks

class Result:
    def __init__(self, ok: bool, ms: int | None = None, error: str | None = None):
        self.ok, self.ms, self.error = ok, ms, error
        self.recorded = True             # False: the site was restarting or waiting, so it didn't count

    def __repr__(self):
        return f"Result(ok={self.ok}, ms={self.ms}, error={self.error!r})"


# One client per site for web page checks (made again when the site's address or certificate changes), without
# keep-alive: each check is a fresh connection, as a browser coming to the device would make.
_check_clients: dict = {}            # site id -> (event loop, what it was made for, client)


def _check_client(site: sites.Site, timeout: float) -> httpx.AsyncClient:
    loop = asyncio.get_running_loop()
    want = (site.version, timeout)
    hit = _check_clients.get(site.id)
    if hit is not None and hit[0] is loop and hit[1] == want:
        return hit[2]
    new = probe._client(site, timeout, limits=httpx.Limits(max_keepalive_connections=0))
    _check_clients[site.id] = (loop, want, new)
    if hit is not None and hit[0] is loop:
        loop.create_task(hit[2].aclose())
    for sid in [k for k, v in _check_clients.items() if v[0].is_closed()]:
        _check_clients.pop(sid, None)            # made in a loop that has ended (the tests start one per round)
    return new


async def close_clients() -> None:
    loop = asyncio.get_running_loop()
    for sid, (lp, _, c) in list(_check_clients.items()):
        if lp is loop:
            _check_clients.pop(sid, None)
            try:
                await c.aclose()
            except Exception:                    # noqa: BLE001
                pass


async def check_http(site: sites.Site, row) -> Result:
    """A GET of the check path (or the start path), redirects not followed, the site's TLS rules and extra headers;
    up = an answer within the timeout with a status below 500 (or only 2xx/3xx when chosen)."""
    path = row["check_path"] or site.start_path or "/"
    started = time.monotonic()
    try:
        c = _check_client(site, row["check_timeout_s"])
        req = c.build_request("GET", site.origin + path, headers=list(site.headers))
        r = await c.send(req, stream=True)
        await r.aclose()
    except (httpx.HTTPError, netguard.TargetError, OSError) as e:
        kind, words = probe._problem(e)
        return Result(False, None, {"timeout": "timed out", "refused": "refused", "dns": "name not found",
                                    "certificate": "certificate not accepted"}.get(kind, words[:120]))
    ms = round((time.monotonic() - started) * 1000)
    ok = r.status_code < 500 if row["ok_codes"] == "below500" else 200 <= r.status_code < 400
    return Result(ok, ms, None if ok else f"status {r.status_code}")


async def check_tcp(site: sites.Site, row) -> Result:
    port = site.port or (443 if site.scheme == "https" else 80)
    started = time.monotonic()
    writer = None
    try:
        addrs = await netguard.resolve(site.host, port, site.allow_public)
        _, writer = await asyncio.wait_for(asyncio.open_connection(addrs[0], port), row["check_timeout_s"])
    except asyncio.TimeoutError:
        return Result(False, None, "timed out")
    except netguard.TargetError as e:
        return Result(False, None, str(e)[:120])
    except ConnectionRefusedError:
        return Result(False, None, "refused")
    except OSError as e:
        low = str(e).lower()
        return Result(False, None, "refused" if "refused" in low or "connect call failed" in low else
                      "unreachable" if "unreachable" in low else str(e)[:120])
    finally:
        if writer is not None:
            writer.close()
    return Result(True, round((time.monotonic() - started) * 1000))


def _icmp_socket(family: int):
    """An unprivileged ICMP socket (Linux "ping sockets"). PermissionError when the container doesn't allow it."""
    proto = socket.IPPROTO_ICMP if family == socket.AF_INET else socket.IPPROTO_ICMPV6
    return socket.socket(family, socket.SOCK_DGRAM, proto)


def _echo_request(family: int, seq: int) -> bytes:
    kind = 8 if family == socket.AF_INET else 128
    payload = b"household-proxy"
    head = struct.pack("!BBHHH", kind, 0, 0, 0, seq)
    csum = _checksum(head + payload)
    return struct.pack("!BBHHH", kind, 0, csum, 0, seq) + payload


def _checksum(data: bytes) -> int:
    if len(data) % 2:
        data += b"\0"
    s = sum(struct.unpack(f"!{len(data) // 2}H", data))
    s = (s >> 16) + (s & 0xFFFF)
    s += s >> 16
    return ~s & 0xFFFF


async def check_ping(site: sites.Site, row, count: int = 3) -> Result:
    """3 ICMP echoes; up if any is answered within the timeout."""
    try:
        addrs = await netguard.resolve(site.host, 0, site.allow_public)
    except netguard.TargetError as e:
        return Result(False, None, str(e)[:120])
    ip = addrs[0]
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    try:
        sock = _icmp_socket(family)
    except OSError:
        return Result(False, None, "ping isn't allowed in this app's container — use a Port check instead")
    sock.setblocking(False)
    loop = asyncio.get_running_loop()
    reply = 0 if family == socket.AF_INET else 129
    started = time.monotonic()
    try:
        for seq in range(1, count + 1):
            await loop.sock_sendto(sock, _echo_request(family, seq), (ip, 0))
        deadline = started + row["check_timeout_s"]
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                return Result(False, None, "no ping reply")
            try:
                data = await asyncio.wait_for(loop.sock_recv(sock, 2048), left)
            except asyncio.TimeoutError:
                return Result(False, None, "no ping reply")
            if data and data[0] == reply:
                return Result(True, round((time.monotonic() - started) * 1000))
    except OSError as e:
        return Result(False, None, str(e)[:120])
    finally:
        sock.close()


CHECKS = {"http": check_http, "tcp": check_tcp, "ping": check_ping}


async def run_check(site: sites.Site, row) -> Result:
    try:
        return await CHECKS[row["check_kind"]](site, row)
    except Exception as e:                       # noqa: BLE001 — a check never stops the loop
        logger.exception("check of %s failed", site.slug)
        return Result(False, None, f"check failed: {e.__class__.__name__}")


# --------------------------------------------------------------------------------------------- the states

def _spread(site_id: int, every: int) -> float:
    """Where in its interval a site's first check falls, so checks don't all run at once."""
    return (zlib.crc32(str(site_id).encode()) % 1000) / 1000 * min(every, 60)


def apply(site_id: int, res: Result, now: float, started: float | None = None) -> list[tuple]:
    """Record one check's result and move the site's state; the events to tell people about. A result that comes in
    while the site is restarting or waiting after a restart — or from a check that started before the latest restart —
    is not recorded (res.recorded = False): it says nothing about the device as it is now."""
    from . import restarts
    events = []
    with db.get_conn() as conn:
        row = conn.execute(f"SELECT {ROUND_COLUMNS} FROM sites WHERE id = ?", (site_id,)).fetchone()
        if row is None or row["check_kind"] is None:
            return []
        stale = started is not None and row["last_restart_at"] and row["last_restart_at"] >= started
        if not restarts.may_check(row, now) or stale:
            res.recorded = False
            return []
        _strips.pop(site_id, None)
        conn.execute("INSERT INTO checks (site_id, at, ok, ms, error) VALUES (?, ?, ?, ?, ?)",
                     (site_id, iso(now), int(res.ok), res.ms, res.error))
        upd = {"last_check": iso(now), "last_ms": res.ms, "last_error": res.error,
               "next_check_at": now + row["check_every_s"]}
        state, fails = row["state"], row["fails_in_row"]
        if state == "waiting" and not row["incident_id"]:
            state = "up"            # after a Restart now on a healthy site: the usual "down after N" rule applies
        if res.ok:
            upd["fails_in_row"] = 0
            if row["state"] != "up":        # (waiting after a Restart now on a healthy site included)
                upd.update(state="up", state_since=iso(now))
                if row["incident_id"]:
                    inc = conn.execute("SELECT * FROM incidents WHERE id = ?", (row["incident_id"],)).fetchone()
                    outcome = "up_after_restart" if inc and inc["restarts"] else "up"
                    conn.execute("UPDATE incidents SET ended_at = ?, outcome = ? WHERE id = ?",
                                 (iso(now), outcome, row["incident_id"]))
                    started = epoch(inc["started_at"]) if inc else None
                    events.append(("back_up", {"minutes": round((now - started) / 60) if started else None,
                                               "restarts": inc["restarts"] if inc else 0}))
                    upd["incident_id"] = None
                restarts.on_up(conn, row, upd)
        else:
            fails += 1
            upd["fails_in_row"] = fails
            if state in (None, "up") and fails >= row["down_after"]:
                inc = conn.execute("INSERT INTO incidents (site_id, started_at, cause) VALUES (?, ?, ?)",
                                   (site_id, iso(now), res.error)).lastrowid
                upd.update(state="down", state_since=iso(now), incident_id=inc)
                events.append(("down", {"cause": res.error}))
            restarts.on_failed(conn, row, upd, now, events)
        conn.execute(f"UPDATE sites SET {', '.join(f'{k} = ?' for k in upd)} WHERE id = ?", (*upd.values(), site_id))
    if any(k in upd for k in ("state", "incident_id")):
        sites.invalidate()
    return events


def paused(row, now: float) -> bool:
    return bool(row["paused_until"] and row["paused_until"] > now)


def _round_rows(now: float):
    """The checked sites (named columns), giving a first check time to new ones; and whether any switch is still
    marked off (a round has work even with no checked site left)."""
    with db.get_conn() as conn:
        rows = conn.execute(f"SELECT {ROUND_COLUMNS} FROM sites WHERE check_kind IS NOT NULL").fetchall()
        for r in rows:
            if r["next_check_at"] is None:
                conn.execute("UPDATE sites SET next_check_at = ? WHERE id = ?",
                             (now + _spread(r["id"], r["check_every_s"]), r["id"]))
        off = conn.execute("SELECT 1 FROM switched_off LIMIT 1").fetchone() is not None
    return rows, off


async def _one(site: sites.Site, row) -> None:
    """One scheduled check: run, record, alert (the site is in _running until it ends)."""
    try:
        started = clock()
        res = await run_check(site, row)
        events = await asyncio.to_thread(apply, row["id"], res, clock(), started)
        for kind, info in events:
            await asyncio.to_thread(alert, row["id"], kind, info)
    finally:
        _running.discard(row["id"])


async def tick() -> None:
    """One round: start the checks that are due (at most MAX_PARALLEL running at a time; each runs on as a task, so a
    slow one never holds up the others or the round), start the restarts that are wanted, publish the sensors."""
    from . import restarts
    now = clock()
    rows, any_off = await asyncio.to_thread(_round_rows, now)
    if not rows and not any_off and not SENSORS.names():
        return                                   # nothing is checked: nothing to do
    due = sorted((r for r in rows if r["next_check_at"] is not None and r["next_check_at"] <= now
                  and not paused(r, now) and r["id"] not in _running and restarts.may_check(r, now)),
                 key=lambda r: r["next_check_at"])[:max(0, MAX_PARALLEL - len(_running))]
    started = []
    if due:
        known = sites.peek()
        if known is None:
            known = await asyncio.to_thread(sites.all_sites)
        by_id = {s.id: s for s in known.values()}
        for r in due:
            site = by_id.get(r["id"])
            if site is None:
                continue
            _running.add(r["id"])
            t = asyncio.get_running_loop().create_task(_one(site, r))
            _tasks.add(t)
            t.add_done_callback(_tasks.discard)
            started.append(t)
    if INLINE and started:
        await asyncio.gather(*started)
        rows = None                              # the checks moved states: the restarts read them afresh
    await restarts.advance(clock(), rows)
    await asyncio.to_thread(publish_sensors)


async def check_now(site_id: int) -> Result | None:
    """Admin → Check now: one check at once, recorded like any other — unless the site is restarting or waiting after
    a restart, or a scheduled check of it is running: then the answer is shown but not recorded."""
    from . import restarts
    known = sites.peek()
    if known is None:
        known = await asyncio.to_thread(sites.all_sites)
    site = next((s for s in known.values() if s.id == site_id), None)

    def read():
        with db.get_conn() as conn:
            return conn.execute(f"SELECT {ROUND_COLUMNS} FROM sites WHERE id = ?", (site_id,)).fetchone()
    row = await asyncio.to_thread(read)
    if site is None or row is None or row["check_kind"] is None:
        return None
    if site_id in _running or not restarts.may_check(row, clock()):
        res = await run_check(site, row)
        res.recorded = False
        return res
    _running.add(site_id)
    try:
        started = clock()
        res = await run_check(site, row)
        events = await asyncio.to_thread(apply, site_id, res, clock(), started)
    finally:
        _running.discard(site_id)
    for kind, info in events:
        await asyncio.to_thread(alert, site_id, kind, info)
    await asyncio.to_thread(publish_sensors)
    return res


async def check_preview(site: sites.Site, check: dict) -> Result:
    """The Test for a monitor-only form: one check, not recorded."""
    row = {"check_kind": check["kind"], "check_path": check.get("path") or None,
           "check_timeout_s": check.get("timeout_s", 5), "ok_codes": check.get("ok_codes", "below500")}
    return await run_check(site, row)


def pause(site_id: int, hours: float | None) -> bool:
    """Pause checks (and restarts) for `hours`, or until resumed (None); resume with hours=0."""
    until = None if hours == 0 else (UNTIL_RESUMED if hours is None else clock() + hours * 3600)
    with db.get_conn() as conn:
        n = conn.execute("UPDATE sites SET paused_until = ?, next_check_at = NULL WHERE id = ?",
                         (until, site_id)).rowcount
    sites.invalidate()
    return bool(n)


# --------------------------------------------------------------------------------------------- alerts (§13.4)

TITLES = {
    "down": "{name} is down",
    "restarting": "{name}: restarting",
    "back_up": "{name} is back up",
    "gave_up": "{name} needs attention",
    "couldnt_restart": "{name}: couldn't restart",
}


def _local(epoch_s: float) -> str:
    from datetime import datetime, timezone
    return config.ZONE.now(datetime.fromtimestamp(epoch_s, timezone.utc)).strftime("%H:%M")


def message(kind: str, info: dict) -> str:
    if kind == "down":
        return f"Not answering ({info.get('cause') or 'no answer'}) since {_local(clock())}."
    if kind == "restarting" and info.get("with"):
        why = "which an admin is restarting" if info.get("manual") else "which isn't answering"
        return f"Turning it off and on again: it shares a switch with {info['with']}, {why}."
    if kind == "restarting":
        return f"Turning it off and on again (restart {info.get('n')} of {info.get('of')})."
    if kind == "back_up":
        bits = []
        if info.get("minutes") is not None:
            bits.append(f"down for {info['minutes']} min")
        if info.get("restarts"):
            bits.append(f"after {info['restarts']} restart{'s' if info['restarts'] > 1 else ''}")
        return ("Answering again" + (" — " + ", ".join(bits) if bits else "") + ".")
    if kind == "gave_up":
        return f"Still down after {info.get('restarts')} restarts. No more restarts until it is up or an admin " \
               f"chooses Try again."
    if kind == "couldnt_restart":
        return f"Not restarted: {info.get('why')}."
    return ""


def recipients(conn, site_id: int) -> list[str]:
    """The notify services (bare names) for a site's alerts: each chosen person's phones from Home Assistant, minus
    people who muted it, plus the site's extra service."""
    row = conn.execute("SELECT alert_service FROM sites WHERE id = ?", (site_id,)).fetchone()
    people = [r["user_id"] for r in conn.execute(
        "SELECT user_id FROM site_alert_people WHERE site_id = ? AND user_id NOT IN "
        "(SELECT user_id FROM alert_mutes WHERE site_id = ?)", (site_id, site_id))]
    out = []
    for uid in people:
        out += ha_people.phones_for(uid)
    if row and row["alert_service"]:
        out.append(ha_notify.bare(row["alert_service"]))
    return list(dict.fromkeys(out))


def send(service: str, title: str, text: str, data: dict) -> bool:
    return ha_notify.send_notify(service, title, text, data)


def alert(site_id: int, kind: str, info: dict) -> int:
    """Tell the site's chosen people (and its extra service) about an event, if the site wants that event. Being
    alerted gives no access: the message names the site and the event, and its link opens the Monitor page, which
    shows the site only to people allowed to see it. Returns how many services were sent to."""
    with db.get_conn() as conn:
        row = conn.execute("SELECT slug, name, alert_events FROM sites WHERE id = ?", (site_id,)).fetchone()
        if row is None or kind not in json.loads(row["alert_events"] or "[]"):
            return 0
        services = recipients(conn, site_id)
    link = f"{sidebar.PAGE}/monitor/{row['slug']}" if sidebar.PAGE else None
    data = {"tag": f"household_proxy_{row['slug']}", "group": "household_proxy"}
    if link:
        data.update(url=link, clickAction=link)
    title = TITLES[kind].format(name=row["name"])
    text = message(kind, info)
    sent = 0
    for svc in services:
        try:
            sent += bool(send(svc, title, text, data))
        except Exception:                        # noqa: BLE001 — one service failing never stops the others
            logger.exception("alert to %s failed", svc)
    logger.info("%s: %s (sent to %d of %d)", row["slug"], kind, sent, len(services))
    return sent


# --------------------------------------------------------------------------------------------- sensors (§13.5)

def entity_id(slug: str) -> str:
    return "binary_sensor.household_proxy_" + slug.replace("-", "_")


def sensor_items() -> list[tuple]:
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT s.slug, s.name, s.state, s.state_since, s.last_check, s.last_ms, s.paused_until, "
            "s.last_restart_at, COALESCE(i.restarts, 0) AS restarts FROM sites s "
            "LEFT JOIN incidents i ON i.id = s.incident_id "
            "WHERE s.check_kind IS NOT NULL AND s.expose_sensor = 1").fetchall()
    out = []
    now = clock()
    for r in rows:
        last = iso(r["last_restart_at"]) if r["last_restart_at"] else None
        state = "on" if r["state"] == "up" else ("off" if r["state"] else "unknown")
        status = "paused" if paused(r, now) else (r["state"] or "not_checked")
        out.append((entity_id(r["slug"]), state, {
            "friendly_name": r["name"], "device_class": "connectivity", "status": status,
            "since": r["state_since"], "last_check": r["last_check"], "response_ms": r["last_ms"],
            "restarts": r["restarts"], "last_restart": last}))
    return out


def publish_sensors(force: bool = False) -> None:
    items = sensor_items()
    now = clock()
    if now - _refresh["at"] >= SENSOR_REFRESH_S:
        force, _refresh["at"] = True, now
    wanted = {e for e, _, _ in items}
    gone = [n for n in SENSORS.names() if n not in wanted]
    if gone:
        SENSORS.remove(gone)
    SENSORS.publish(items, force=force)


# --------------------------------------------------------------------------------------------- history

def housekeeping() -> None:
    """Checks older than a week become hourly totals; totals older than the App setting and incidents older than
    90 days go."""
    from . import settings
    keep = int(settings.get("checks_keep_days"))
    cut = iso(clock() - RAW_CHECK_DAYS * 86400)
    with db.get_conn() as conn:
        conn.execute("INSERT INTO check_hours (site_id, hour, ok, total) "
                     "SELECT site_id, substr(at, 1, 13), SUM(ok), COUNT(*) FROM checks WHERE at < ? "
                     "GROUP BY site_id, substr(at, 1, 13) ON CONFLICT(site_id, hour) DO UPDATE SET "
                     "ok = ok + excluded.ok, total = total + excluded.total", (cut,))
        conn.execute("DELETE FROM checks WHERE at < ?", (cut,))
        conn.execute("DELETE FROM check_hours WHERE hour < ?", (iso(clock() - keep * 86400)[:13],))
        conn.execute("DELETE FROM incidents WHERE started_at < ? AND ended_at IS NOT NULL",
                     (iso(clock() - INCIDENT_DAYS * 86400),))


def uptime(conn, site_id: int, seconds: int) -> float | None:
    """Share of good checks over the last `seconds` (raw checks, plus hourly totals beyond a week)."""
    since = iso(clock() - seconds)
    ok, total = conn.execute("SELECT COALESCE(SUM(ok), 0), COUNT(*) FROM checks WHERE site_id = ? AND at >= ?",
                             (site_id, since)).fetchone()
    h_ok, h_total = conn.execute("SELECT COALESCE(SUM(ok), 0), COALESCE(SUM(total), 0) FROM check_hours "
                                 "WHERE site_id = ? AND hour >= ?", (site_id, since[:13])).fetchone()
    total += h_total
    return round(100 * (ok + h_ok) / total, 2) if total else None


def strip(conn, site_id: int, hours: int = 24, buckets: int = 48) -> list[str]:
    """The last 24 hours in half hours: 'up', 'down', 'partial' or '' (no checks). Counted by SQLite, one row per half
    hour (not one per check)."""
    now = clock()
    size = hours * 3600 / buckets
    start = now - hours * 3600
    counts = [[0, 0] for _ in range(buckets)]
    for b, ok, total in conn.execute(
            "SELECT CAST((CAST(strftime('%s', at) AS REAL) - ?) / ? AS INTEGER) AS b, SUM(ok), COUNT(*) "
            "FROM checks WHERE site_id = ? AND at >= ? AND strftime('%s', at) IS NOT NULL GROUP BY b",
            (start, size, site_id, iso(start))):
        i = min(buckets - 1, max(0, b))
        counts[i][0] += ok
        counts[i][1] += total
    return ["" if t == 0 else "up" if ok == t else "down" if ok == 0 else "partial" for ok, t in counts]


_strips: dict = {}                   # site id -> (until, strip): kept a minute, dropped when a check is recorded


def strip_cached(conn, site_id: int) -> list[str]:
    now = clock()
    hit = _strips.get(site_id)
    if hit is not None and hit[0] > now:
        return hit[1]
    out = strip(conn, site_id)
    if len(_strips) > 500:
        _strips.clear()
    _strips[site_id] = (now + STRIP_CACHE_S, out)
    return out


async def loop() -> None:
    """The background loop: a round every TICK_S seconds, whether or not anyone has the page open."""
    from . import restarts
    await asyncio.to_thread(restarts.recover)
    while True:
        try:
            await tick()
        except Exception:                        # noqa: BLE001
            logger.exception("monitoring round failed")
        await asyncio.sleep(TICK_S)
