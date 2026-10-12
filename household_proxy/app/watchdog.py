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

clock = time.time
SENSORS = sensor_publisher.Publisher()
_refresh = {"at": 0.0}
_running: set[int] = set()            # sites with a check in flight


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

    def __repr__(self):
        return f"Result(ok={self.ok}, ms={self.ms}, error={self.error!r})"


async def check_http(site: sites.Site, row) -> Result:
    """A GET of the check path (or the start path), redirects not followed, the site's TLS rules and extra headers;
    up = an answer within the timeout with a status below 500 (or only 2xx/3xx when chosen)."""
    path = row["check_path"] or site.start_path or "/"
    started = time.monotonic()
    try:
        async with probe._client(site, row["check_timeout_s"]) as c:
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


def apply(site_id: int, res: Result, now: float) -> list[tuple]:
    """Record one check's result and move the site's state; the events to tell people about."""
    from . import restarts
    events = []
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
        if row is None or row["check_kind"] is None:
            return []
        conn.execute("INSERT INTO checks (site_id, at, ok, ms, error) VALUES (?, ?, ?, ?, ?)",
                     (site_id, iso(now), int(res.ok), res.ms, res.error))
        upd = {"last_check": iso(now), "last_ms": res.ms, "last_error": res.error,
               "next_check_at": now + row["check_every_s"]}
        state, fails = row["state"], row["fails_in_row"]
        if res.ok:
            upd["fails_in_row"] = 0
            if state != "up":
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


async def tick() -> None:
    """One round: the checks that are due (at most MAX_PARALLEL at a time), restart sequences that are waiting, the
    alerts, the sensors."""
    from . import restarts
    now = clock()
    with db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM sites WHERE check_kind IS NOT NULL").fetchall()
        for r in rows:
            if r["next_check_at"] is None:
                conn.execute("UPDATE sites SET next_check_at = ? WHERE id = ?",
                             (now + _spread(r["id"], r["check_every_s"]), r["id"]))
    due = [r for r in rows if r["next_check_at"] is not None and r["next_check_at"] <= now and not paused(r, now)
           and r["id"] not in _running and restarts.may_check(r, now)]
    by_id = {s.id: s for s in sites.all_sites().values()}
    sem = asyncio.Semaphore(MAX_PARALLEL)

    async def one(r):
        site = by_id.get(r["id"])
        if site is None:
            return
        _running.add(r["id"])
        try:
            async with sem:
                res = await run_check(site, r)
            events = await asyncio.to_thread(apply, r["id"], res, clock())
            for kind, info in events:
                await asyncio.to_thread(alert, r["id"], kind, info)
        finally:
            _running.discard(r["id"])

    await asyncio.gather(*(one(r) for r in due))
    await restarts.advance(clock())
    await asyncio.to_thread(publish_sensors)


async def check_now(site_id: int) -> Result | None:
    """Admin → Check now: one check at once, recorded like any other."""
    site = sites.by_id(site_id)
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
    if site is None or row is None or row["check_kind"] is None:
        return None
    res = await run_check(site, row)
    for kind, info in await asyncio.to_thread(apply, site_id, res, clock()):
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
        rows = conn.execute("SELECT * FROM sites WHERE check_kind IS NOT NULL AND expose_sensor = 1").fetchall()
        out = []
        for r in rows:
            inc = conn.execute("SELECT restarts FROM incidents WHERE id = ?", (r["incident_id"],)).fetchone() \
                if r["incident_id"] else None
            last = iso(r["last_restart_at"]) if r["last_restart_at"] else None
            state = "on" if r["state"] == "up" else ("off" if r["state"] else "unknown")
            status = "paused" if paused(r, clock()) else (r["state"] or "not_checked")
            out.append((entity_id(r["slug"]), state, {
                "friendly_name": r["name"], "device_class": "connectivity", "status": status,
                "since": r["state_since"], "last_check": r["last_check"], "response_ms": r["last_ms"],
                "restarts": inc["restarts"] if inc else 0, "last_restart": last}))
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
    """The last 24 hours in half hours: 'up', 'down', 'partial' or '' (no checks)."""
    now = clock()
    size = hours * 3600 / buckets
    start = now - hours * 3600
    counts = [[0, 0] for _ in range(buckets)]
    for r in conn.execute("SELECT at, ok FROM checks WHERE site_id = ? AND at >= ?", (site_id, iso(start))):
        t = epoch(r["at"])
        if t is None:
            continue
        i = min(buckets - 1, max(0, int((t - start) / size)))
        counts[i][0] += r["ok"]
        counts[i][1] += 1
    return ["" if t == 0 else "up" if ok == t else "down" if ok == 0 else "partial" for ok, t in counts]


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
