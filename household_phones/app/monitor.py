"""Check-ins, the "please report" requests and each phone's status (spec/SPEC.md §3, §3.1).

- A **check-in** is a genuine report from the phone: a `state_changed` or `state_reported` event on one of its
  sensors (`on_state_event`, `classify`), or — when Home Assistant doesn't send `state_reported` — a newer
  `last_reported` seen by the once-a-minute poll (`poll_states`). Not: restores and entities added (no old state),
  unavailable / unknown, anything during Home Assistant's (re)start grace, events without a timestamp, attribute-only
  tracker rewrites, and — after the phone said it was shutting down — anything but a real change. Each counted and
  ignored update is kept (the last 20, `diagnostics`) for the phone page's Check-in details.
- **Requests**: a phone silent for interval + "ask after" is sent `command_update_sensors`; again after "wait for a
  reply"; Offline after the set number of requests (interval + 12 minutes at the defaults). iPhones are never asked.
- **Status** (`compute`, a pure function the tests drive): online / late / offline / app_not_reporting / cant_check,
  plus paused (alerts or watching), dnm (the owner's do-not-monitor hours, dnm.py) and unknown (not monitored: no
  admin added it).
- **False alarms**: the start-up grace (also after a do-not-monitor window and after an admin adds a phone), lost
  contact with most phones at once, Can't check when the request itself failed, pauses.
- **Not monitored** devices are left alone entirely: never asked, no check-ins written, no alerts, no history.
- **The offline alert's delay** (`alert_delay_min`, §5): a phone can be Offline for a while before its Offline (or
  App not reporting) alert goes out. Each unreachable spell is kept on the phone's row (`offline_since`, the time
  counted so far, whether its alerts went out), so a restart resumes the count; do-not-monitor hours and pauses
  don't count (`_spell`).

`tick()` runs every 30 seconds (and soon after a check-in): it decides under one database connection, then sends
requests and alerts with none open.
"""
import logging
import math
import threading
import time

from . import alerts, config, db, dnm, phones, settings

logger = logging.getLogger("monitor")

STARTED = {"at": None}                     # when this app started watching (the start-up grace)
WAKE = threading.Event()
_last_write: dict = {}                     # phone id -> last check-in written (events come in bursts)
_lock = threading.Lock()
REPORTED = {"last": None}                  # when a state_reported event last came (None: never)
REPORTED_FRESH_S = 30 * 60
WRITE_EVERY_S = 20
STATUS_LABEL = {"online": "Online", "late": "Late", "offline": "Offline", "app_not_reporting": "App not reporting",
                "cant_check": "Can't check", "paused": "Paused", "unknown": "Unknown", "dnm": "Not monitored"}
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
    """Is this device monitored at all (an admin added it, and it isn't retired)?"""
    return bool(p["monitored"]) and not p["retired"]


def base_time(p, t: Timing) -> float:
    """From when the phone counts as silent: its last check-in, or when an admin added it if that is later."""
    base = max([x for x in (p["last_checkin"], p["monitored_since"]) if x] or [p["first_seen"]])
    if p["overdue_from"]:                     # Check now got no reply: watched as overdue from then (§4)
        base = min(base, p["overdue_from"] - t.online_for)
    return base


def dnm_now(p, now: float) -> tuple[dict | None, float | None]:
    """(the do-not-monitor window on now, or None; seconds since the last one ended, or None)."""
    on = dnm.active(p, now)
    return on, (None if on else dnm.since_end(p, now))


def compute(p, now: float, s: dict, started: float, *, sensor_off: bool = False,
            dnm_state: tuple | None = None) -> tuple[str, str | None]:
    """(status, reason) of one phone. The machine's own status: 'paused' only while watching is paused (an alerts
    pause is an overlay the pages add, `display`); 'dnm' during the owner's do-not-monitor hours. `dnm_state`:
    dnm_now()'s answer (worked out here when not given)."""
    if not runs(p):
        return "unknown", "not_monitored"
    if watching_paused(p, now):
        return "paused", "watching"
    on, after = dnm_state if dnm_state is not None else dnm_now(p, now)
    if on:
        return "dnm", None
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
    elif st == "online" and answering_only(p, now, t, after):
        st, reason = "app_not_reporting", "answers_only"
    if st == "offline" and (now - started < t.grace or in_grace_after(p, now, t, after)):
        st = "late"                           # the start-up grace: never Offline yet (§3.1)
    return st, reason


def in_grace_after(p, now: float, t: Timing, after: float | None) -> bool:
    """Within interval + start-up grace after a do-not-monitor window ended, or after an admin added the phone."""
    if after is not None and after < t.grace:
        return True
    return bool(p["monitored_since"]) and now - p["monitored_since"] < t.grace


def answering_only(p, now: float, t: Timing, after: float | None = None) -> bool:
    """It has answered requests but not reported by itself for a long while (battery optimisation, force-stop).
    Time spent in do-not-monitor hours, or before an admin added it, doesn't count."""
    window = max(3 * t.interval, 3600)
    if not p["answers_requests"] or now - p["first_seen"] < window:
        return False
    if (after is not None and after < window) or (p["monitored_since"] and now - p["monitored_since"] < window):
        return False
    spont = p["last_spontaneous"]
    return spont is None or now - spont > window


def display(p, now: float) -> dict:
    """What the pages show: the status, with an alerts pause on top."""
    st = p["status"] or "unknown"
    label = "Not monitored" if not runs(p) else STATUS_LABEL.get(st, st)
    on = dnm.active(p, now) if runs(p) else None
    if on:
        st, label = "dnm", f"Not monitored (until {on['untilText']})"
    elif st == "dnm":
        st, label = "unknown", "Unknown"          # the window just ended: the next round works it out
    out = {"status": st, "label": label, "reason": p["status_reason"],
           "reasonText": REASON_TEXT.get(p["status_reason"] or "") or (p["request_error"] if st == "cant_check" else None),
           "paused": None}
    if p["pause_kind"] and (watching_paused(p, now) or alerts_paused(p, now)):
        out["paused"] = {"kind": p["pause_kind"], "until": p["pause_until"], "untilText": until_text(p),
                         "reason": p["pause_reason"]}
        if p["pause_kind"] == "alerts":
            out["label"] = f"Paused · {STATUS_LABEL.get(st, st)}"
    return out


def alert_delay_min(p) -> int:
    """How long this phone must be offline before its Offline / App not reporting alert: its own, else the household's."""
    own = p["offline_alert_after_min"]
    return int(own) if own is not None else int(settings.get("offline_alert_after_min") or 0)


def duration_text(seconds: float, short: bool = False) -> str:
    """"10 hours", "2 hours 15 minutes", "45 minutes" (short: "10 h", "2 h 15 min", "45 min")."""
    m = int(max(0, seconds) // 60)
    h, mm = divmod(m, 60)
    if short:
        return " ".join(x for x in (f"{h} h" if h else "", f"{mm} min" if mm or not h else "") if x)
    def unit(n, word):
        return f"{n} {word}" + ("" if n == 1 else "s")
    return " ".join(x for x in (unit(h, "hour") if h else "", unit(mm, "minute") if mm or not h else "") if x)


def _counted_between(p, a: float, b: float) -> float:
    """Seconds between `a` and `b` that count towards the delay. Ticks are 30 seconds apart; a longer gap (the app
    was stopped) is sampled minute by minute so do-not-monitor hours and pauses in it are left out."""
    span = b - a
    if span <= 0:
        return 0.0
    if span <= 120:
        return span
    span = min(span, 14 * 86400)
    step = 60.0 if span <= 2 * 86400 else 300.0
    n = int(span // step)
    ok = sum(1 for i in range(n) if not (dnm.active(p, a + (i + 0.5) * step) or alerts_paused(p, a + (i + 0.5) * step)
                                         or watching_paused(p, a + (i + 0.5) * step)))
    return ok * step + (span - n * step)


def _spell(conn, p, st: str, now: float, counting: bool) -> dict:
    """Bring the phone's unreachable spell up to date and store it: it begins when the phone becomes Offline or App
    not reporting, ends when it is back online (or isn't monitored), and in between counts only the time `counting`
    (not in do-not-monitor hours, not paused). Late, Can't check and the start-up grace in between neither end nor
    reset it. Returns {since, counted, alerted, appAlerted} (since None: no spell)."""
    since, counted, at = p["offline_since"], p["offline_counted_s"] or 0.0, p["offline_counted_at"]
    alerted, app_alerted = bool(p["offline_alerted"]), bool(p["app_alerted"])
    if st == "online" or not runs(p):
        if since is not None:
            conn.execute("UPDATE phones SET offline_since = NULL, offline_counted_s = 0, offline_counted_at = NULL, "
                         "offline_alerted = 0, app_alerted = 0 WHERE id = ?", (p["id"],))
        return {"since": None, "counted": 0.0, "alerted": False, "appAlerted": False}
    if since is None:
        if st not in ("offline", "app_not_reporting"):
            return {"since": None, "counted": 0.0, "alerted": False, "appAlerted": False}
        since, counted, at, alerted, app_alerted = now, 0.0, (now if counting else None), False, False
    elif counting:
        counted += _counted_between(p, at, now) if at is not None else 0.0
        at = now
    else:
        at = None
    conn.execute("UPDATE phones SET offline_since = ?, offline_counted_s = ?, offline_counted_at = ?, offline_alerted = ?, "
                 "app_alerted = ? WHERE id = ?", (since, counted, at, int(alerted), int(app_alerted), p["id"]))
    return {"since": since, "counted": counted, "alerted": alerted, "appAlerted": app_alerted}


def _mark_spell(conn, pid: str, col: str) -> None:
    conn.execute(f"UPDATE phones SET {col} = 1 WHERE id = ? AND offline_since IS NOT NULL", (pid,))


def spell_view(p, now: float) -> dict:
    """The offline alert's delay for the admin's phone page: the choice, and the count while it runs."""
    own, delay = p["offline_alert_after_min"], alert_delay_min(p)
    hh = int(settings.get("offline_alert_after_min") or 0)
    out = {"own": own, "delayMin": delay, "defaultMin": hh, "defaultLabel": settings.delay_label(hh),
           "choices": [{"value": v, "label": lbl} for v, lbl in settings.OFFLINE_DELAYS], "counting": None}
    st = p["status"]
    if delay and p["offline_since"] is not None and runs(p):
        alert = "app" if st == "app_not_reporting" else "offline"
        done = p["app_alerted"] if alert == "app" else p["offline_alerted"]
        if not done and alerts.enabled(alert):
            counted = (p["offline_counted_s"] or 0) + (max(0.0, now - p["offline_counted_at"])
                                                       if p["offline_counted_at"] is not None else 0)
            label = STATUS_LABEL.get(st, st) if st in ("offline", "app_not_reporting") else "Offline"
            out["counting"] = {"countedMin": int(counted // 60), "paused": p["offline_counted_at"] is None,
                               "text": f"{label} {duration_text(counted, True)} — alert after "
                                       f"{duration_text(delay * 60, True)}"
                                       + (" (not counting now)" if p["offline_counted_at"] is None else "")}
    return out


def until_text(p) -> str:
    if p["pause_until_online"]:
        return "until it's back online"
    if p["pause_until"] is None:
        return ""
    d = config.local(p["pause_until"])
    same_day = d.date() == config.now().date()
    return f"until {d.strftime('%H:%M') if same_day else d.strftime('%a %H:%M')}"


# --------------------------------------------------------------------------------------------- what counts

HA_GRACE_S = 120                           # after Home Assistant (re)starts or the event connection comes back
HA_STARTING_MAX_S = 15 * 60
HA = {"until": None, "starting_at": None, "why": None}
SHUTDOWN_HOLD_S = 60                       # reports in the same burst as "shutting down" still count
RING_N = 20
NOT_A_VALUE = ("unavailable", "unknown")
SENSOR_DOMAINS = ("sensor", "binary_sensor")
KIND_TEXT = {"state_reported": "reported, unchanged", "state_changed": "changed", "attributes": "attributes changed",
             "poll": "newer last_reported, fallback poll", "shutdown": "shutting down", "direct": "check-in"}
_diag: dict = {}                           # phone id -> {"counted": deque, "ignored": deque} (memory only)
_shutdown: dict = {}                       # phone id -> when it said it was shutting down, or None (phones.shutdown_at)
_poll_seen: dict = {}                      # entity id -> the last_reported the fallback poll saw last


def ha_restarting(now: float) -> bool:
    """Inside the grace after Home Assistant (re)started: its restores and rewrites aren't the phones' reports."""
    if HA["starting_at"] is not None and now - HA["starting_at"] < HA_STARTING_MAX_S:
        return True
    return HA["until"] is not None and now < HA["until"]


def ha_started(now: float | None = None, why: str = "Home Assistant started") -> None:
    now = config.clock() if now is None else now
    HA.update(until=max(HA["until"] or 0, now + HA_GRACE_S), starting_at=None, why=why)


def on_ha_event(event: dict) -> None:
    """homeassistant_start / _started / _stop (the WebSocket thread)."""
    etype = event.get("event_type") if isinstance(event, dict) else None
    now = config.clock()
    if etype in ("homeassistant_start", "homeassistant_stop"):
        HA.update(starting_at=now, why="Home Assistant starting" if etype.endswith("start") else "Home Assistant stopping")
    elif etype == "homeassistant_started":
        ha_started(now)


def on_ws_connect(first: bool) -> None:
    """Every (re)connection of the event WebSocket: a reconnect usually means Home Assistant restarted, so the next
    HA_GRACE_S seconds don't count. On the first connection the grace ends at once if Home Assistant is already
    running (its restores happened before this app was listening)."""
    ha_started(why="connected to Home Assistant" if first else "Home Assistant restarted (reconnected)")
    if not first:
        return

    def check():
        from .common import ha_client
        cfg = ha_client.fetch_config_blocking(timeout=10)
        if isinstance(cfg, dict) and cfg.get("state") == "RUNNING" and HA["starting_at"] is None:
            HA["until"] = None
    threading.Thread(target=check, daemon=True, name="phones-ha-state").start()


def _label(pid: str | None, eid: str | None) -> str:
    """The entity's name for the diagnostics: never anything from the Last notification sensor but its name."""
    if not eid:
        return "—"
    if "notification" in eid and "count" not in eid:
        return "Last notification sensor"
    with phones.LIVE.lock:
        for e in phones.LIVE.phone_entities.get(pid, []):
            if e["entity_id"] == eid and e.get("name"):
                return str(e["name"])[:60]
        st = phones.LIVE.states.get(eid) or {}
    name = (st.get("attributes") or {}).get("friendly_name")
    return str(name)[:60] if isinstance(name, str) and name else eid


def _key(pid: str, eid: str) -> str | None:
    with phones.LIVE.lock:
        for e in phones.LIVE.phone_entities.get(pid, []):
            if e["entity_id"] == eid:
                return e["key"]
    return None


def note(pid: str, which: str, eid: str | None, kind: str | None, at: float, reason: str | None = None) -> None:
    """Remember a counted check-in or an ignored update (the last RING_N of each per phone). Consecutive ignored
    updates for the same reason fold into one entry with a count."""
    from collections import deque
    with _lock:
        d = _diag.setdefault(pid, {"counted": deque(maxlen=RING_N), "ignored": deque(maxlen=RING_N)})
        ring = d[which]
        if which == "ignored" and ring and ring[-1]["reason"] == reason:
            ring[-1].update(at=at, entity=eid, kind=kind, n=ring[-1]["n"] + 1)
            return
        ring.append({"at": at, "entity": eid, "kind": kind, "reason": reason, "n": 1})


def diagnostics(p, now: float) -> dict:
    """The phone page's "Check-in details" (admins): the last genuine check-in and where it came from, the last
    RING_N counted check-ins and ignored updates. Entity names, event kinds, times and reasons only — never a state."""
    pid = p["id"]
    with _lock:
        d = _diag.get(pid) or {"counted": [], "ignored": []}
        counted, ignored = list(d["counted"]), list(d["ignored"])

    def row(x):
        return {"at": x["at"], "atText": config.hhmm(x["at"]), "entity": x["entity"], "name": _label(pid, x["entity"]),
                "kind": KIND_TEXT.get(x["kind"] or "", x["kind"]), "reason": x["reason"], "n": x["n"]}
    last = None
    if p["last_checkin"]:
        eid = p["checkin_entity"]
        last = {"at": p["last_checkin"], "atText": config.hhmm(p["last_checkin"]), "entity": eid,
                "name": _label(pid, eid) if eid else None, "kind": KIND_TEXT.get(p["checkin_kind"] or "", p["checkin_kind"])}
        last["text"] = f"Last check-in {last['atText']}" + (f" — from {last['name']} ({last['kind']})" if eid else "")
    sd = p["shutdown_at"]
    return {"last": last, "counted": [row(x) for x in reversed(counted)], "ignored": [row(x) for x in reversed(ignored)],
            "shutdown": {"at": sd, "atText": config.hhmm(sd)} if sd else None,
            "haRestart": {"untilText": config.hhmm(HA["until"]), "why": HA["why"]} if ha_restarting(now) and HA["until"] else None,
            "source": "events" if reported_events_seen(now) else "polling"}


def last_checkin_text(p) -> str | None:
    """"last check-in 14:02 from Battery level (changed)" for the history's status rows."""
    if not p["last_checkin"]:
        return None
    text = f"last check-in {config.hhmm(p['last_checkin'])}"
    if p["checkin_entity"]:
        text += f" from {_label(p['id'], p['checkin_entity'])} ({KIND_TEXT.get(p['checkin_kind'] or '', p['checkin_kind'])})"
    return text


def _shutdown_at(pid: str) -> float | None:
    with _lock:
        if pid in _shutdown:
            return _shutdown[pid]
    with db.get_conn() as conn:
        r = conn.execute("SELECT shutdown_at FROM phones WHERE id = ?", (pid,)).fetchone()
    at = r["shutdown_at"] if r else None
    with _lock:
        _shutdown[pid] = at
    return at


def _set_shutdown(pid: str, at: float | None) -> None:
    with db.get_conn() as conn:
        conn.execute("UPDATE phones SET shutdown_at = ? WHERE id = ?", (at, pid))
    with _lock:
        _shutdown[pid] = at


def is_shutdown(key: str | None, state) -> bool:
    """The Last update trigger sensor saying the phone is shutting down (android.intent.action.ACTION_SHUTDOWN)."""
    return key == "last_update" and isinstance(state, str) and "shutdown" in state.lower()


# --------------------------------------------------------------------------------------------- check-ins

def checkin(pid: str, ts: float | None, now: float | None = None, *, force: bool = False,
            src: dict | None = None) -> bool:
    """Record a genuine report from the phone at `ts` (`src`: {entity, kind, change} for the diagnostics and the
    shutdown rule). No timestamp, no check-in. Writes at most every WRITE_EVERY_S per phone unless a request or a
    Check now is waiting for it; a Check now is answered only by a report made after it was sent. True when written."""
    now = config.clock() if now is None else now
    if ts is None:
        return False
    ts = min(ts, now)
    src = src or {}
    eid, kind = src.get("entity"), src.get("kind") or "direct"
    sd = _shutdown_at(pid)
    if kind == "shutdown":
        _set_shutdown(pid, ts)
        force = True
    elif sd is not None and ts > sd + SHUTDOWN_HOLD_S:
        # Any genuine report after the shutdown burst means the phone is back on. Home Assistant's own rewrites never
        # get here (classify / the poll's restart grace filter them), and a phone left locked mostly re-sends
        # unchanged values, so "only a change counts" kept a phone that was back on looking Offline until unlocked.
        _set_shutdown(pid, None)
    with _lock:
        last = _last_write.get(pid)
        if not force and last is not None and ts <= last[0] + WRITE_EVERY_S and not last[1]:
            return False
    with db.get_conn() as conn:
        p = conn.execute("SELECT * FROM phones WHERE id = ?", (pid,)).fetchone()
        if p is None or not runs(p) or dnm.active(p, now):
            return False                      # not monitored (now): nothing is written
        if p["last_checkin"] is not None and ts <= p["last_checkin"]:
            if ts < p["last_checkin"] - 1 and src:
                note(pid, "ignored", eid, kind, now, "older than the last check-in")
            with _lock:
                _last_write[pid] = (p["last_checkin"], bool(last and last[1]))
            return False
        waiting = conn.execute("SELECT started FROM checks WHERE phone_id = ? AND state = 'waiting'", (pid,)).fetchall()
        s = snapshot()
        t = timing(p, s)
        answer = p["last_request_at"] is not None and 0 < ts - p["last_request_at"] <= t.wait + 60
        conn.execute("UPDATE phones SET last_checkin = MAX(COALESCE(last_checkin, 0), ?), requests_sent = 0, "
                     "request_error = NULL, request_error_kind = NULL, overdue_from = NULL, "
                     "checkin_entity = ?, checkin_kind = ?, "
                     "answers_requests = CASE WHEN ? THEN 1 ELSE answers_requests END, "
                     "last_spontaneous = CASE WHEN ? THEN last_spontaneous ELSE MAX(COALESCE(last_spontaneous, 0), ?) END "
                     "WHERE id = ?", (ts, eid, kind, answer, answer, ts, pid))
        if p["pause_kind"] == "alerts" and p["pause_until_online"]:
            conn.execute("UPDATE phones SET pause_kind = NULL, pause_until = NULL, pause_until_online = 0, "
                         "pause_reason = NULL, pause_by = NULL WHERE id = ?", (pid,))
            phones.history(conn, pid, "pause", "Back online: alerts resumed.", now=now)
        # a Check now is answered only by a report made after it was sent (§4)
        conn.execute("UPDATE checks SET state = 'online', replied = ?, finished = ? WHERE phone_id = ? AND "
                     "state = 'waiting' AND started < ?", (ts, now, pid, ts))
        pending = p["last_request_at"] is not None and p["requests_sent"] > 0
    if any(w["started"] >= ts for w in waiting):
        note(pid, "ignored", eid, kind, now, "before the request (not an answer to Check now)")
    note(pid, "counted", eid, kind, ts)
    with _lock:
        still = any(w["started"] >= ts for w in waiting)
        _last_write[pid] = (ts, still)
    if pending or waiting or (p["status"] not in ("online", "unknown", "paused")):
        WAKE.set()
    return True


def forget(pid: str) -> None:
    """The phone's history was deleted: its check-in details go too."""
    with _lock:
        _diag.pop(pid, None)
        _shutdown.pop(pid, None)
        _last_write.pop(pid, None)


def mark_waiting(pid: str) -> None:
    """A request or Check now went out: the next report is written at once."""
    with _lock:
        last = _last_write.get(pid)
        _last_write[pid] = ((last or (0, False))[0], True)


def event_time(new_state: dict | None, now: float | None = None) -> float | None:
    """When Home Assistant last heard from the entity (`last_reported`, else `last_updated`); None without either —
    never "now" (`now` is ignored, kept for old callers)."""
    if isinstance(new_state, dict):
        for k in ("last_reported", "last_updated"):
            ts = config.parse_iso(new_state.get(k))
            if ts:
                return ts
    return None


def classify(etype: str | None, data: dict, now: float) -> tuple[float | None, str | None, str | None, bool]:
    """Is this state event a genuine report from the phone? → (ts, kind, why_not, changed). Home Assistant writes the
    Companion app's states with an empty context (no user, no parent), exactly like its own rewrites, so the context
    can't tell them apart (spec §3); these rules do."""
    eid = str(data.get("entity_id") or "")
    new, old = data.get("new_state"), data.get("old_state")
    if not isinstance(new, dict):
        return None, None, "removed", False
    if etype == "state_changed" and not isinstance(old, dict):
        return None, None, "added or restored by Home Assistant", False
    state = new.get("state")
    if state in NOT_A_VALUE:
        return None, None, str(state), False
    if ha_restarting(now):
        return None, None, "HA restart", False
    if etype == "state_changed" and old.get("state") in NOT_A_VALUE:
        return None, None, "back from unavailable (restored by Home Assistant)", False
    ts = event_time(new)
    if ts is None:
        return None, None, "no timestamp", False
    changed = etype == "state_changed" and old.get("state") != state
    kind = "state_reported" if etype == "state_reported" else ("state_changed" if changed else "attributes")
    domain = eid.split(".", 1)[0]
    if domain not in SENSOR_DOMAINS and not (domain == "device_tracker" and changed):
        return None, kind, "not a sensor update (Home Assistant can rewrite it)", False
    return ts, kind, None, changed


def on_state_event(event: dict) -> None:
    """A state_changed / state_reported event (the WebSocket thread). Only genuine reports of the phones' own
    entities count (`classify`); every other one is noted with the reason (Check-in details)."""
    data = event.get("data") if isinstance(event, dict) else None
    if not isinstance(data, dict):
        return
    now = config.clock()
    etype = event.get("event_type")
    if etype == "state_reported":
        REPORTED["last"] = now                # Home Assistant sends them: the fallback poll stands down
    eid = data.get("entity_id")
    with phones.LIVE.lock:
        pid = phones.LIVE.entity_phone.get(eid)
    if pid is None:
        return
    new = data.get("new_state")
    old = data.get("old_state")
    if (etype == "state_changed" and eid.endswith("_last_notification") and isinstance(new, dict)
            and isinstance(old, dict)                                     # not added or restored after a restart
            and old.get("state") != "unavailable"                        # nor the first value after being unavailable
            and new.get("state") not in ("unavailable", "unknown", None)):
        from . import forwarding                                          # a restored last message is never re-sent
        forwarding.submit(pid, new, old.get("state"))
    if isinstance(new, dict):
        with phones.LIVE.lock:
            phones.LIVE.states[eid] = phones.clean_state(eid, new)
    ts, kind, why, changed = classify(etype, data, now)
    if why is not None:
        note(pid, "ignored", eid, kind or etype, now, why)
        return
    if is_shutdown(_key(pid, eid), new.get("state")):
        kind = "shutdown"
    checkin(pid, ts, now, src={"entity": eid, "kind": kind, "change": changed})


def reported_events_seen(now: float) -> bool:
    return REPORTED["last"] is not None and now - REPORTED["last"] < REPORTED_FRESH_S


def events_flowing(now: float) -> bool:
    """state_reported events arrive, and the event connection is up (the fallback poll isn't needed)."""
    if not reported_events_seen(now):
        return False
    from . import app_messages
    ws = app_messages.WS["client"]
    return ws is None or ws.connected.is_set()


def poll_states(source=None, now: float | None = None, *, force: bool = False) -> int:
    """The fallback (§3.1 phase-1 check), only while state_reported events don't arrive: every minute, a newer
    `last_reported` (or `last_updated`) of a phone's sensor counts as a check-in — except during Home Assistant's
    start-up grace (Home Assistant restoring them after a restart). The Companion app sends all its sensors in one
    batch, so many sensors moving to the same instant is a normal report, not a restart. Returns check-ins found."""
    from . import ha_source
    now = config.clock() if now is None else now
    if not force and events_flowing(now):
        return 0
    states = (source or ha_source.SOURCE).states(max_age=30)
    if states is None:
        return 0
    jumps: dict = {}                          # phone id -> [(ts, entity)] that moved since the last poll
    timed: dict = {}                          # phone id -> sensors with a time
    with phones.LIVE.lock:
        for st in states:
            eid = st.get("entity_id") if isinstance(st, dict) else None
            pid = phones.LIVE.entity_phone.get(eid)
            if pid is None:
                continue
            phones.LIVE.states[eid] = phones.clean_state(eid, st)
            ts = event_time(st)
            if ts is None or eid.split(".", 1)[0] not in SENSOR_DOMAINS:
                continue
            timed[pid] = timed.get(pid, 0) + 1
            before = _poll_seen.get(eid)
            _poll_seen[eid] = ts
            if st.get("state") in NOT_A_VALUE or (before is not None and ts <= before):
                continue
            jumps.setdefault(pid, []).append((ts, eid))
    n = 0
    for pid, moved in jumps.items():
        ts, eid = max(moved)
        if ha_restarting(now):
            note(pid, "ignored", eid, "poll", now, "HA restart")
            continue
        n += checkin(pid, ts, now, src={"entity": eid, "kind": "poll", "change": False})
    return n


# --------------------------------------------------------------------------------------------- requests

def send_request(pid: str, source=None, now: float | None = None, *, count: bool = True) -> "object":
    """Send "please report" to one phone and record the outcome. Blocking. Returns ha_source.CommandResult."""
    from . import ha_source
    now = config.clock() if now is None else now
    with db.get_conn() as conn:
        p = conn.execute("SELECT * FROM phones WHERE id = ?", (pid,)).fetchone()
    service = phones.notify_service(p) if p else None
    if p is not None and (not runs(p) or dnm.active(p, now)):
        return ha_source.CommandResult(False, "cant_check", "Not monitored right now.")   # never asked (§2, §3.1)
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

def _lost_contact(rows, statuses: dict, now: float, s: dict, dnm_states: dict | None = None
                  ) -> tuple[bool, int, int, float | None]:
    """(lost, silent, monitored, since): half or more of the monitored phones (at least the minimum) went quiet within
    the same window. Phones in do-not-monitor hours, or just out of them (the grace), don't count."""
    dnm_states = dnm_states or {}

    def counted(p):
        on, after = dnm_states.get(p["id"], (None, None))
        return not on and not in_grace_after(p, now, timing(p, s), after)
    active = [p for p in rows if runs(p) and not watching_paused(p, now) and not alerts_paused(p, now)
              and not phones.is_ios(p) and counted(p)]
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
        statuses, dnm_states = {}, {}
        for p in rows:
            off = phones.sensor_enabled(p["id"], "battery_level") is False
            dnm_states[p["id"]] = dnm_now(p, now) if runs(p) else (None, None)
            st, reason = compute(p, now, s, started, sensor_off=off, dnm_state=dnm_states[p["id"]])
            late_since = p["late_since"]
            if st in ("online", "unknown", "paused", "dnm"):
                late_since = None
            elif late_since is None:
                late_since = now
            statuses[p["id"]] = (st, reason, late_since)
        lost, silent, total, since = _lost_contact(rows, statuses, now, s, dnm_states)
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
                if not (p["status"] == "unknown" and st == "unknown") and st != "dnm" and runs(p):
                    text = f"{STATUS_LABEL.get(st, st)}" + (f" — {REASON_TEXT[reason]}" if reason in REASON_TEXT else "")
                    if st in ("late", "offline") and reason not in REASON_TEXT and last_checkin_text(p):
                        text += f" — {last_checkin_text(p)}"
                    phones.history(conn, pid, "status", text, battery=battery, now=now)
                out["changed"].append((pid, p["status"], st))
            elif late_since != p["late_since"]:
                conn.execute("UPDATE phones SET late_since = ? WHERE id = ?", (late_since, pid))
            dnm_on = st == "dnm"
            muted = alerts_paused(p, now) or watching_paused(p, now) or not runs(p) or dnm_on
            delay = alert_delay_min(p)
            spell = _spell(conn, p, st, now, counting=not muted)
            due = bool(delay) and spell["since"] is not None and spell["counted"] >= delay * 60
            if dnm_on or not runs(p):         # do-not-monitor hours: nothing held for later either (§3.1)
                for a in ("offline", "battery"):
                    if alerts.held(conn, pid, a):
                        alerts.set_held(conn, pid, a, False)
            # -- alerts on a change
            if changed and not muted:
                if st == "offline" and alerts.enabled("offline") and not delay:
                    _mark_spell(conn, pid, "offline_alerted")
                    if lost:                  # lost contact: held until contact returns (§3.1)
                        alerts.set_held(conn, pid, "offline", True)
                    else:
                        to_alert.append(("offline", pid, offline_text(conn, p, name)))
                # back online: after Offline / App not reporting; with a delay, only if that alert went out
                was = p["offline_alerted"] or p["app_alerted"]
                if st == "online" and alerts.enabled("back") and (
                        (not delay and p["status"] in ("offline", "app_not_reporting")) or (delay and was)):
                    to_alert.append(("back", pid, f"{name} is back online" + (f" (battery {battery}%)" if battery is not None else "") + "."))
                if st == "online":
                    alerts.set_held(conn, pid, "offline", False)
            # -- the delayed Offline alert: once the spell has counted the phone's delay (§5)
            if delay and st == "offline" and not muted and due and not spell["alerted"] and alerts.enabled("offline"):
                _mark_spell(conn, pid, "offline_alerted")
                if lost:
                    alerts.set_held(conn, pid, "offline", True)
                else:
                    to_alert.append(("offline", pid, offline_text(conn, p, name, spell, now)))
            # -- App not reporting, once it has lasted longer than the Offline time (and the delay, if one is set)
            if st == "app_not_reporting" and not muted and alerts.enabled("app") and (
                    not delay or (due and not spell["appAlerted"])):
                since_st = p["status_since"] if not changed else now
                r = conn.execute("SELECT last_sent FROM alert_state WHERE phone_id = ? AND alert = 'app'", (pid,)).fetchone()
                if since_st is not None and now - since_st >= t.offline_after and not (r and r["last_sent"] and r["last_sent"] >= since_st):
                    _mark_spell(conn, pid, "app_alerted")
                    to_alert.append(("app", pid, app_text(p, name, reason, spell if delay else None, now)))
            # -- held offline alerts: released when contact returns, if still offline
            if not muted and st == "offline" and not lost and alerts.held(conn, pid, "offline"):
                alerts.set_held(conn, pid, "offline", False)
                to_alert.append(("offline", pid, offline_text(conn, p, name, spell if delay else None, now)))
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
            if statuses[p["id"]][0] == "dnm":
                quietly_note_restart(conn, p)
            elif runs(p) and not watching_paused(p, now):
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


def offline_text(conn, p, name: str, spell: dict | None = None, now: float | None = None) -> str:
    """The Offline alert. With a delay (`spell`): how long — "Asha's Pixel has been offline for 10 hours (since 05:02,
    battery was 4%)"."""
    if spell and spell.get("since") is not None and now is not None:
        bits = [f"since {config.hhmm(spell['since'])}"]
        if p["battery"] is not None:
            bits.append(f"battery was {p['battery']}%" + (" and charging" if p["charging"] else ""))
        text = f"{name} has been offline for {duration_text(now - spell['since'])} ({', '.join(bits)})."
    else:
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


def app_text(p, name: str, reason: str | None, spell: dict | None = None, now: float | None = None) -> str:
    short = {"signed_out": "signed out?", "sensor_off": "a sensor is off on the phone",
             "answers_only": "battery optimisation?"}.get(reason or "", "")
    text = f"{name}: the HA app stopped reporting" + (f" — {short}" if short else "") + "."
    if spell and spell.get("since") is not None and now is not None:
        text += f" Unreachable for {duration_text(now - spell['since'])} (since {config.hhmm(spell['since'])})."
    return text


def quietly_note_restart(conn, p) -> None:
    """During do-not-monitor hours: a restart is remembered without a history row or an alert, so none goes out
    about it once the hours end."""
    reboot = phones.last_reboot(p["id"])
    if reboot and reboot != p["last_reboot"]:
        conn.execute("UPDATE phones SET last_reboot = ? WHERE id = ?", (reboot, p["id"]))


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
        _diag.clear()
        _shutdown.clear()
        _poll_seen.clear()
    HA.update(until=None, starting_at=None, why=None)
    STARTED["at"] = None
    REPORTED["last"] = None
    WAKE.clear()


__all__ = ["compute", "tick", "checkin", "on_state_event", "poll_states", "time"]
