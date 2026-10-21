"""Check-ins, the "please report" requests and each phone's status (spec/SPEC.md §3, §3.1).

- A **check-in** is any report from the phone: a `state_changed` or `state_reported` event on one of its entities
  (`on_state_event`), or — when Home Assistant doesn't send `state_reported` — a newer `last_reported` seen by the
  once-a-minute poll (`poll_states`). The fallback switches itself on and off (`reported_events_seen`).
- **Requests**: a phone silent for interval + "ask after" is sent `command_update_sensors`; again after "wait for a
  reply"; Offline after the set number of requests (interval + 12 minutes at the defaults). iPhones are never asked.
- **Status** (`compute`, a pure function the tests drive): online / late / offline / app_not_reporting / cant_check,
  plus the overlays paused (alerts or watching) and unknown (not watched, just found).
- **False alarms**: the start-up grace, lost contact with most phones at once, Can't check when the request itself
  failed, pauses and daily quiet times.

`tick()` runs every 30 seconds (and soon after a check-in): it decides under one database connection, then sends
requests and alerts with none open.
"""
import logging
import math
import threading
import time

from . import alerts, config, db, phones, settings

logger = logging.getLogger("monitor")

STARTED = {"at": None}                     # when this app started watching (the start-up grace)
WAKE = threading.Event()
_last_write: dict = {}                     # phone id -> last check-in written (events come in bursts)
_lock = threading.Lock()
REPORTED = {"last": None}                  # when a state_reported event last came (None: never)
REPORTED_FRESH_S = 30 * 60
WRITE_EVERY_S = 20
STATUS_LABEL = {"online": "Online", "late": "Late", "offline": "Offline", "app_not_reporting": "App not reporting",
                "cant_check": "Can't check", "paused": "Paused", "unknown": "Unknown"}
REASON_TEXT = {
    "signed_out": "The Companion app seems signed out or uninstalled — open it on the phone and sign in.",
    "sensor_off": "A needed sensor is off on the phone (Battery level) — Companion app → Settings → Manage sensors.",
    "answers_only": "It answers requests but never reports by itself — set battery use for the Home Assistant app to "
                    "Unrestricted (Android: App info → Battery).",
}


def started_at() -> float:
    if STARTED["at"] is None:
        STARTED["at"] = config.clock()
    return STARTED["at"]


class Timing:
    def __init__(self, interval_min: int, s: dict):
        self.interval = interval_min * 60
        self.ask = s["ask_after_min"] * 60
        self.wait = s["wait_reply_min"] * 60
        self.requests = s["requests"]
        self.online_for = self.interval + self.ask
        self.offline_after = self.online_for + self.requests * self.wait
        self.grace = self.interval + s["startup_grace_min"] * 60


def snapshot() -> dict:
    keys = ("interval_min", "ask_after_min", "wait_reply_min", "requests", "startup_grace_min", "lost_contact_on",
            "lost_contact_window_min", "lost_contact_min_phones", "check_wait_s")
    return {k: settings.get(k) for k in keys}


def timing(p, s: dict) -> Timing:
    return Timing(p["interval_min"] or s["interval_min"], s)


def watching_paused(p, now: float) -> bool:
    return p["pause_kind"] == "watching" and (p["pause_until"] is None or p["pause_until"] > now)


def alerts_paused(p, now: float) -> bool:
    if p["pause_kind"] != "alerts":
        return False
    return bool(p["pause_until_online"]) or p["pause_until"] is None or p["pause_until"] > now


def runs(p) -> bool:
    """Is this device watched at all (a watched phone or tablet, not ignored or retired)?"""
    return bool(p["watched"]) and p["kind"] != "ignored" and not p["retired"]


def base_time(p, t: Timing) -> float:
    base = p["last_checkin"] or p["first_seen"]
    if p["overdue_from"]:                     # Check now got no reply: watched as overdue from then (§4)
        base = min(base, p["overdue_from"] - t.online_for)
    return base


def compute(p, now: float, s: dict, started: float, *, sensor_off: bool = False) -> tuple[str, str | None]:
    """(status, reason) of one phone. The machine's own status: 'paused' only while watching is paused (an alerts
    pause is an overlay the pages add, `display`)."""
    if not runs(p):
        return "unknown", "not_watched"
    if watching_paused(p, now):
        return "paused", "watching"
    t = timing(p, s)
    since = now - base_time(p, t)
    if since <= t.online_for:
        st, reason = "online", None
    elif since < t.offline_after:
        st, reason = "late", None
    else:
        st, reason = "offline", None
    if st != "online" and p["request_error_kind"] == "cant_check":
        st, reason = "cant_check", None
    if st != "online" and p["request_error_kind"] == "signed_out":
        st, reason = "app_not_reporting", "signed_out"
    elif st == "offline" and sensor_off:
        st, reason = "app_not_reporting", "sensor_off"
    elif st == "online" and answering_only(p, now, t):
        st, reason = "app_not_reporting", "answers_only"
    if st == "offline" and now - started < t.grace:
        st = "late"                           # the start-up grace: never Offline yet (§3.1)
    return st, reason


def answering_only(p, now: float, t: Timing) -> bool:
    """It has answered requests but not reported by itself for a long while (battery optimisation, force-stop)."""
    window = max(3 * t.interval, 3600)
    if not p["answers_requests"] or now - p["first_seen"] < window:
        return False
    spont = p["last_spontaneous"]
    return spont is None or now - spont > window


def display(p, now: float) -> dict:
    """What the pages show: the status, with an alerts pause on top."""
    st = p["status"] or "unknown"
    label = "Not watched" if p["status_reason"] == "not_watched" else STATUS_LABEL.get(st, st)
    out = {"status": st, "label": label, "reason": p["status_reason"],
           "reasonText": REASON_TEXT.get(p["status_reason"] or "") or (p["request_error"] if st == "cant_check" else None),
           "paused": None}
    if p["pause_kind"] and (watching_paused(p, now) or alerts_paused(p, now)):
        out["paused"] = {"kind": p["pause_kind"], "until": p["pause_until"], "untilText": until_text(p),
                         "reason": p["pause_reason"]}
        if p["pause_kind"] == "alerts":
            out["label"] = f"Paused · {STATUS_LABEL.get(st, st)}"
    return out


def until_text(p) -> str:
    if p["pause_until_online"]:
        return "until it's back online"
    if p["pause_until"] is None:
        return ""
    d = config.local(p["pause_until"])
    same_day = d.date() == config.now().date()
    return f"until {d.strftime('%H:%M') if same_day else d.strftime('%a %H:%M')}"


def in_phone_quiet(p, now: float) -> bool:
    return alerts.in_quiet(p["quiet_start"], p["quiet_end"], config.local(now))


# --------------------------------------------------------------------------------------------- check-ins

def checkin(pid: str, ts: float, now: float | None = None, *, force: bool = False) -> bool:
    """Record a report from the phone at `ts`. Writes at most every WRITE_EVERY_S per phone unless a request or a
    Check now is waiting for it. True when written."""
    now = config.clock() if now is None else now
    ts = min(ts, now)
    with _lock:
        last = _last_write.get(pid)
        if not force and last is not None and ts <= last[0] + WRITE_EVERY_S and not last[1]:
            return False
    with db.get_conn() as conn:
        p = conn.execute("SELECT * FROM phones WHERE id = ?", (pid,)).fetchone()
        if p is None:
            return False
        waiting = conn.execute("SELECT 1 FROM checks WHERE phone_id = ? AND state = 'waiting'", (pid,)).fetchone()
        if p["last_checkin"] is not None and ts <= p["last_checkin"] and not waiting:
            with _lock:
                _last_write[pid] = (p["last_checkin"], False)
            return False
        s = snapshot()
        t = timing(p, s)
        answer = p["last_request_at"] is not None and 0 <= ts - p["last_request_at"] <= t.wait + 60
        conn.execute("UPDATE phones SET last_checkin = MAX(COALESCE(last_checkin, 0), ?), requests_sent = 0, "
                     "request_error = NULL, request_error_kind = NULL, overdue_from = NULL, "
                     "answers_requests = CASE WHEN ? THEN 1 ELSE answers_requests END, "
                     "last_spontaneous = CASE WHEN ? THEN last_spontaneous ELSE MAX(COALESCE(last_spontaneous, 0), ?) END "
                     "WHERE id = ?", (ts, answer, answer, ts, pid))
        if p["pause_kind"] == "alerts" and p["pause_until_online"]:
            conn.execute("UPDATE phones SET pause_kind = NULL, pause_until = NULL, pause_until_online = 0, "
                         "pause_reason = NULL, pause_by = NULL WHERE id = ?", (pid,))
            phones.history(conn, pid, "pause", "Back online: alerts resumed.", now=now)
        conn.execute("UPDATE checks SET state = 'online', replied = ?, finished = ? WHERE phone_id = ? AND "
                     "state = 'waiting' AND started <= ?", (ts, now, pid, ts + 2))
        pending = p["last_request_at"] is not None and p["requests_sent"] > 0
    with _lock:
        _last_write[pid] = (ts, False)
    if pending or waiting or (p["status"] not in ("online", "unknown", "paused")):
        WAKE.set()
    return True


def mark_waiting(pid: str) -> None:
    """A request or Check now went out: the next report is written at once."""
    with _lock:
        last = _last_write.get(pid)
        _last_write[pid] = ((last or (0, False))[0], True)


def event_time(new_state: dict | None, now: float | None) -> float | None:
    """When Home Assistant last heard from the entity (`last_reported`, else `last_updated`); `now` without either."""
    if isinstance(new_state, dict):
        for k in ("last_reported", "last_updated"):
            ts = config.parse_iso(new_state.get(k))
            if ts:
                return ts
    return now


def on_state_event(event: dict) -> None:
    """A state_changed / state_reported event (the WebSocket thread). Only the phones' own entities count."""
    data = event.get("data") if isinstance(event, dict) else None
    if not isinstance(data, dict):
        return
    eid = data.get("entity_id")
    with phones.LIVE.lock:
        pid = phones.LIVE.entity_phone.get(eid)
    if pid is None:
        return
    now = config.clock()
    etype = event.get("event_type")
    new = data.get("new_state")
    if etype == "state_reported":
        REPORTED["last"] = now
    if etype == "state_changed" and eid.endswith("_last_notification") and isinstance(new, dict):
        from . import forwarding
        forwarding.submit(pid, new, (data.get("old_state") or {}).get("state") if isinstance(data.get("old_state"), dict) else None)
    if isinstance(new, dict):
        with phones.LIVE.lock:
            phones.LIVE.states[eid] = phones.clean_state(eid, new)
    elif etype == "state_changed" and new is None:
        return                                             # the entity was removed: not a report
    checkin(pid, event_time(new, now), now)


def reported_events_seen(now: float) -> bool:
    return REPORTED["last"] is not None and now - REPORTED["last"] < REPORTED_FRESH_S


def poll_states(source=None, now: float | None = None, *, force: bool = False) -> int:
    """The fallback (§3.1 phase-1 check): every minute, a newer `last_reported` (or `last_updated`) of any of a
    phone's entities counts as a check-in. Skipped while state_reported events arrive. Returns check-ins found."""
    from . import ha_source
    now = config.clock() if now is None else now
    if not force and reported_events_seen(now):
        return 0
    states = (source or ha_source.SOURCE).states(max_age=30)
    if states is None:
        return 0
    newest: dict = {}
    with phones.LIVE.lock:
        for st in states:
            eid = st.get("entity_id") if isinstance(st, dict) else None
            pid = phones.LIVE.entity_phone.get(eid)
            if pid is None:
                continue
            phones.LIVE.states[eid] = phones.clean_state(eid, st)
            ts = event_time(st, None)
            if ts is not None:
                newest[pid] = max(newest.get(pid, 0), ts)
    n = 0
    for pid, ts in newest.items():
        n += checkin(pid, ts, now)
    return n


# --------------------------------------------------------------------------------------------- requests

def send_request(pid: str, source=None, now: float | None = None, *, count: bool = True) -> "object":
    """Send "please report" to one phone and record the outcome. Blocking. Returns ha_source.CommandResult."""
    from . import ha_source
    now = config.clock() if now is None else now
    with db.get_conn() as conn:
        p = conn.execute("SELECT * FROM phones WHERE id = ?", (pid,)).fetchone()
    service = phones.notify_service(p) if p else None
    if p is None or phones.is_ios(p):
        return ha_source.CommandResult(False, "cant_check", "iPhones can't be asked to report.")
    if not service:
        res = ha_source.CommandResult(False, "signed_out", "Home Assistant has no notify service for this phone.")
    else:
        mark_waiting(pid)
        res = (source or ha_source.SOURCE).send_command(service)
    with db.get_conn() as conn:
        if res.ok:
            conn.execute("UPDATE phones SET last_request_at = ?, requests_sent = requests_sent + ?, request_error = NULL, "
                         "request_error_kind = NULL WHERE id = ?", (now, 1 if count else 0, pid))
        else:
            conn.execute("UPDATE phones SET last_request_at = ?, request_error = ?, request_error_kind = ? WHERE id = ?",
                         (now, res.reason, res.kind, pid))
    return res


# --------------------------------------------------------------------------------------------- the tick

def _lost_contact(rows, statuses: dict, now: float, s: dict) -> tuple[bool, int, int, float | None]:
    """(lost, silent, watched, since): half or more of the watched phones (at least the minimum) went quiet within
    the same window."""
    active = [p for p in rows if runs(p) and not watching_paused(p, now) and not alerts_paused(p, now)
              and not phones.is_ios(p)]
    total = len(active)
    times = sorted(statuses[p["id"]][2] for p in active
                   if statuses[p["id"]][0] in ("late", "offline", "cant_check") and statuses[p["id"]][2])
    window = s["lost_contact_window_min"] * 60
    best, since = 0, None
    j = 0
    for i in range(len(times)):
        while times[i] - times[j] > window:
            j += 1
        if i - j + 1 > best:
            best, since = i - j + 1, times[j]
    need = max(s["lost_contact_min_phones"], math.ceil(total / 2))
    lost = bool(s["lost_contact_on"]) and total >= s["lost_contact_min_phones"] and best >= need
    return lost, len(times), total, since


def tick(now: float | None = None, source=None, *, again: bool = True) -> dict:
    """One round: statuses, requests, alerts. Blocking. Returns what it did (for the tests). When a request failed,
    the statuses are worked out once more at once (Can't check, App not reporting)."""
    now = config.clock() if now is None else now
    s = snapshot()
    started = started_at()
    to_request, to_alert, out = [], [], {"changed": [], "requests": [], "alerts": []}
    with db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM phones WHERE retired = 0").fetchall()
        # expired pauses
        for p in rows:
            if p["pause_kind"] and not p["pause_until_online"] and p["pause_until"] is not None and p["pause_until"] <= now:
                conn.execute("UPDATE phones SET pause_kind = NULL, pause_until = NULL, pause_reason = NULL, "
                             "pause_by = NULL WHERE id = ?", (p["id"],))
                phones.history(conn, p["id"], "pause", "Pause ended.", now=now)
        rows = conn.execute("SELECT * FROM phones WHERE retired = 0").fetchall()
        statuses = {}
        for p in rows:
            off = phones.sensor_enabled(p["id"], "battery_level") is False
            st, reason = compute(p, now, s, started, sensor_off=off)
            late_since = p["late_since"]
            if st == "online" or st in ("unknown", "paused"):
                late_since = None
            elif late_since is None:
                late_since = now
            statuses[p["id"]] = (st, reason, late_since)
        lost, silent, total, since = _lost_contact(rows, statuses, now, s)
        was_lost = db.state_get(conn, "lost_contact")
        if lost and not was_lost:
            db.state_set(conn, "lost_contact", {"since": since, "silent": silent, "total": total, "at": now})
            if alerts.enabled("lost_contact"):
                to_alert.append(("lost_contact", None, f"Home Assistant has lost contact with the phones ({silent} of "
                                                       f"{total} silent since {config.hhmm(since)}). The problem is "
                                                       "probably at home: Home Assistant's internet or the push service."))
        elif lost:
            db.state_set(conn, "lost_contact", dict(was_lost, silent=silent, total=total))
        elif was_lost:
            db.state_set(conn, "lost_contact", None)
        for p in rows:
            st, reason, late_since = statuses[p["id"]]
            pid = p["id"]
            t = timing(p, s)
            battery = p["battery"]
            changed = st != p["status"] or reason != p["status_reason"]
            name = phones.display_name(conn, p)
            if changed:
                conn.execute("UPDATE phones SET status = ?, status_reason = ?, status_since = ?, late_since = ? WHERE id = ?",
                             (st, reason, now, late_since, pid))
                if not (p["status"] == "unknown" and st == "unknown"):
                    text = f"{STATUS_LABEL.get(st, st)}" + (f" — {REASON_TEXT[reason]}" if reason in REASON_TEXT else "")
                    phones.history(conn, pid, "status", text, battery=battery, now=now)
                out["changed"].append((pid, p["status"], st))
            elif late_since != p["late_since"]:
                conn.execute("UPDATE phones SET late_since = ? WHERE id = ?", (late_since, pid))
            muted = alerts_paused(p, now) or watching_paused(p, now) or not runs(p)
            quiet = in_phone_quiet(p, now)
            # -- alerts on a change
            if changed and not muted:
                if st == "offline" and alerts.enabled("offline"):
                    if lost:                  # lost contact: held until contact returns (§3.1)
                        alerts.set_held(conn, pid, "offline", True)
                    elif quiet:
                        alerts.set_held(conn, pid, "offline", True)
                    else:
                        to_alert.append(("offline", pid, offline_text(conn, p, name)))
                if st == "online" and p["status"] in ("offline", "app_not_reporting") and alerts.enabled("back"):
                    to_alert.append(("back", pid, f"{name} is back online" + (f" (battery {battery}%)" if battery is not None else "") + "."))
                if st == "online":
                    alerts.set_held(conn, pid, "offline", False)
            # -- App not reporting, once it has lasted longer than the Offline time
            if st == "app_not_reporting" and not muted and alerts.enabled("app"):
                since_st = p["status_since"] if not changed else now
                r = conn.execute("SELECT last_sent FROM alert_state WHERE phone_id = ? AND alert = 'app'", (pid,)).fetchone()
                if since_st is not None and now - since_st >= t.offline_after and not (r and r["last_sent"] and r["last_sent"] >= since_st):
                    to_alert.append(("app", pid, app_text(p, name, reason)))
            # -- held offline alerts: released when the phone's quiet time ends / contact returns, if still offline
            if not muted and st == "offline" and not lost and not quiet and alerts.held(conn, pid, "offline"):
                alerts.set_held(conn, pid, "offline", False)
                to_alert.append(("offline", pid, offline_text(conn, p, name)))
            if st != "offline" and alerts.held(conn, pid, "offline") and not lost:
                alerts.set_held(conn, pid, "offline", False)
            # -- requests
            if runs(p) and not watching_paused(p, now) and not phones.is_ios(p) and st in ("late", "offline", "cant_check"):
                if st == "cant_check":
                    due = p["last_request_at"] is None or now - p["last_request_at"] >= t.wait
                else:
                    due = p["requests_sent"] < t.requests and (p["requests_sent"] == 0 or p["last_request_at"] is None
                                                               or now - p["last_request_at"] >= t.wait)
                if due:
                    to_request.append(pid)
        # -- what the sensors say (restarts, battery), and the nightly charging reminder
        for p in rows:
            if runs(p) and not watching_paused(p, now):
                to_alert += observe(conn, p, now)
        to_alert += charge_reminders(conn, rows, now, statuses)
    for pid in to_request:
        res = send_request(pid, source, now)
        out["requests"].append((pid, res.ok))
    for alert, pid, text in to_alert:
        extra = {"also_phone": bool(settings.get("charge_to_phone"))} if alert == "charge" else {}
        r = alerts.send(alert, pid, text, now=now, **extra)
        out["alerts"].append((alert, pid, r["skipped"] is None))
    out["lost"] = lost
    if again and any(not ok for _, ok in out["requests"]):
        second = tick(now, source, again=False)
        out["changed"] += second["changed"]
        out["alerts"] += second["alerts"]
    return out


def offline_text(conn, p, name: str) -> str:
    seen = config.hhmm(p["last_checkin"]) if p["last_checkin"] else "never"
    text = f"{name} is offline: no check-in since {seen}"
    if p["battery"] is not None:
        text += f", battery {p['battery']}% then" + (" (charging)" if p["charging"] else "")
    text += "."
    if settings.get("alert_last_place") and p["share_place"]:
        z = phones.zone(p["id"])
        if z:
            text += f" Last seen at {z}."
    return text


def app_text(p, name: str, reason: str | None) -> str:
    short = {"signed_out": "signed out?", "sensor_off": "a sensor is off on the phone",
             "answers_only": "battery optimisation?"}.get(reason or "", "")
    return f"{name}: the HA app stopped reporting" + (f" — {short}" if short else "") + "."


def observe(conn, p, now: float) -> list:
    """Restarts and the battery, from the phone's latest states."""
    out = []
    pid = p["id"]
    level, chg, reboot = phones.battery(pid), phones.charging(pid), phones.last_reboot(pid)
    name = phones.display_name(conn, p)
    muted = alerts_paused(p, now)
    if reboot and p["last_reboot"] and reboot != p["last_reboot"]:
        before = p["battery"]
        if before is not None and before <= 5:
            guess = f"battery was {before}%, probably ran out"
        elif conn.execute("SELECT 1 FROM history WHERE phone_id = ? AND kind = 'update' AND at >= ?",
                          (pid, now - 6 * 3600)).fetchone():
            guess = "probably an update"
        else:
            guess = None
        phones.history(conn, pid, "restart", "Restarted" + (f" ({guess})" if guess else "") + ".", battery=before, now=now)
        if alerts.enabled("restart") and not muted:
            out.append(("restart", pid, f"{name} restarted" + (f": {guess}" if guess else "") + "."))
    if level is not None and level != p["battery"]:
        conn.execute("UPDATE phones SET battery = ?, charging = ? WHERE id = ?", (level, None if chg is None else int(chg), pid))
    elif chg is not None and (p["charging"] is None or bool(p["charging"]) != chg):
        conn.execute("UPDATE phones SET charging = ? WHERE id = ?", (int(chg), pid))
    if reboot and reboot != p["last_reboot"]:
        conn.execute("UPDATE phones SET last_reboot = ? WHERE id = ?", (reboot, pid))
    pct = settings.get("battery_low_pct")
    if level is not None:
        if chg or level >= pct + 5:
            if p["low_battery_sent"]:
                conn.execute("UPDATE phones SET low_battery_sent = 0 WHERE id = ?", (pid,))
        elif level < pct and not chg and not p["low_battery_sent"] and alerts.enabled("battery") and not muted:
            if in_phone_quiet(p, now):
                alerts.set_held(conn, pid, "battery", True)
            else:
                conn.execute("UPDATE phones SET low_battery_sent = 1 WHERE id = ?", (pid,))
                out.append(("battery", pid, f"{name} is at {level}% and not charging."))
        if not in_phone_quiet(p, now) and alerts.held(conn, pid, "battery"):
            alerts.set_held(conn, pid, "battery", False)
            if level < pct and not chg and not p["low_battery_sent"]:
                conn.execute("UPDATE phones SET low_battery_sent = 1 WHERE id = ?", (pid,))
                out.append(("battery", pid, f"{name} is at {level}% and not charging."))
    return out


def charge_reminders(conn, rows, now: float, statuses: dict | None = None) -> list:
    if not settings.get("alert_charge"):
        return []
    local = config.local(now)
    at = alerts._mins(settings.get("charge_time"))
    mins = local.hour * 60 + local.minute
    if at is None or not (at <= mins < at + 60):
        return []
    today = local.date().isoformat()
    out = []
    for p in rows:
        if not runs(p) or p["charge_reminded_on"] == today or watching_paused(p, now) or alerts_paused(p, now):
            continue
        level, chg = phones.battery(p["id"]), phones.charging(p["id"])
        conn.execute("UPDATE phones SET charge_reminded_on = ? WHERE id = ?", (today, p["id"]))
        if level is not None and level < settings.get("charge_pct") and chg is False and \
                (statuses[p["id"]][0] if statuses else p["status"]) == "online":
            out.append(("charge", p["id"], f"{phones.display_name(conn, p)} is at {level}% and not charging."))
    return out


async def loop() -> None:
    """Every 30 seconds (sooner after a check-in a request was waiting for), until cancelled."""
    import asyncio
    from starlette.concurrency import run_in_threadpool
    while True:
        try:
            await run_in_threadpool(tick)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("The watcher's round failed")
        for _ in range(30):
            if WAKE.is_set():
                WAKE.clear()
                break
            await asyncio.sleep(1)


async def poll_loop() -> None:
    import asyncio
    from starlette.concurrency import run_in_threadpool
    while True:
        try:
            await run_in_threadpool(poll_states)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Polling the phones' states failed")
        await asyncio.sleep(60)


def reset() -> None:
    """Tests."""
    with _lock:
        _last_write.clear()
    STARTED["at"] = None
    REPORTED["last"] = None
    WAKE.clear()


__all__ = ["compute", "tick", "checkin", "on_state_event", "poll_states", "time"]
