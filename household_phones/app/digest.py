"""Offline alerts for you (spec/SPEC.md §5 "Alerts"): each admin chooses how they get the alerts about phones that
can't be reached — right away (the default, as before), in a summary once or twice a day, or not at all.

- **What it covers:** the Offline and App not reporting alerts and their Back online, for an admin who gets them *as an
  admin* (the alert's recipients are Admins, or The owner and admins). An admin who gets one as the phone's owner
  (recipients The owner, or The owner and admins, about their own phone) gets it right away as before. Lost contact,
  battery and the other alerts are not affected. Tests (`only=`) always go out.
- **The summary:** at each chosen time, in Home Assistant's time zone, one message listing every monitored phone that
  is Offline or App not reporting now — only for the alerts that are on and go to admins, only once the phone's own
  Alert-when-offline time has passed, never while its alerts or watching are paused or in its owner's do-not-monitor
  hours. Nothing is sent when none is ("all online"). It goes the way alerts are delivered (App settings), at the
  time chosen even in the admin's quiet hours. No Back online for summary admins: the next summary simply doesn't
  list the phone.
- **Once per time:** the last time handled is kept per person (`users.offline_digest_at`), so a restart neither
  resends nor skips one; a time missed while the app was stopped is caught up only within 2 hours, after the start-up
  grace (phones aren't Offline during it).

Stored on the person's row: `offline_mode` ('now' / 'summary' / 'off'), `offline_times` ("08:00" or "08:00,20:00").
"""
import logging
import re
from datetime import timedelta

from . import alerts, auth, config, db, dnm, monitor, phones, settings

logger = logging.getLogger("digest")

MODES = ("now", "summary", "off")
COVERED = ("offline", "app", "back")       # the alerts each admin's choice applies to
CATCH_UP_S = 2 * 3600                      # a time missed while the app was stopped: sent only this soon after it
HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
DEFAULT_TIMES = ["08:00", "20:00"]


def prefs(row) -> dict:
    """{mode, times} of a users row (the defaults when the columns are missing)."""
    keys = row.keys() if hasattr(row, "keys") else []
    mode = row["offline_mode"] if "offline_mode" in keys and row["offline_mode"] in MODES else "now"
    times = [t for t in ((row["offline_times"] if "offline_times" in keys else "") or "").split(",") if HHMM.match(t)]
    return {"mode": mode, "times": times}


def clean(body: dict) -> tuple[str, list[str]]:
    """(mode, times) from the page; ValueError with a readable message."""
    mode = body.get("mode")
    if mode not in MODES:
        raise ValueError("Choose Right away, a summary, or Off.")
    raw = body.get("times")
    if raw is None:
        raw = []
    if not isinstance(raw, list) or not all(isinstance(t, str) for t in raw):
        raise ValueError("Times are a list like [\"08:00\"].")
    times = [t.strip() for t in raw]
    for t in times:
        if not HHMM.match(t):
            raise ValueError("Use times like 08:00 (24-hour).")
    if mode == "summary" and not 1 <= len(times) <= 2:
        raise ValueError("A summary goes once or twice a day: give one or two times.")
    if len(times) > 2:
        raise ValueError("At most two times a day.")
    if len(set(times)) != len(times):
        raise ValueError("Give two different times.")
    return mode, sorted(times)


def label(p: dict) -> str:
    if p["mode"] == "summary" and p["times"]:
        return "a summary at " + " and ".join(p["times"])
    return {"off": "off", "summary": "a summary"}.get(p["mode"], "right away")


def admins_get(alert: str) -> bool:
    """Does this alert (on) go to admins as admins (App settings)?"""
    to = settings.get(alerts.ALERTS[alert][2])
    return alerts.enabled(alert) and to in ("admins", "both")


def applies() -> bool:
    """Does the choice matter now: do Offline or App not reporting alerts go to admins?"""
    return admins_get("offline") or admins_get("app")


def split(conn, people: list, alert: str, phone, to: str) -> tuple[list, list]:
    """The recipients of one alert → (who gets it now, the names of the admins whose summary has it instead).
    Admins who chose a summary or Off are dropped from Offline / App not reporting / Back online alerts, unless they
    get it as the phone's owner."""
    if alert not in COVERED:
        return people, []
    keep, later = [], []
    for u in people:
        own = to in ("owner", "both") and phone is not None and phone["owner_id"] == u["id"]
        if not u.get("is_admin") or own:
            keep.append(u)
            continue
        r = conn.execute("SELECT * FROM users WHERE id = ?", (u["id"],)).fetchone()
        mode = prefs(r)["mode"] if r else "now"
        if mode == "now":
            keep.append(u)
        elif mode == "summary" and alert != "back":
            later.append(u["name"])
    return keep, later


# --------------------------------------------------------------------------------------------- the summary

def _since_text(ts: float | None, now: float) -> str:
    d, n = config.local(ts), config.local(now)
    if d is None:
        return ""
    if d.date() == n.date():
        return d.strftime("%H:%M")
    if (n.date() - d.date()).days < 7:
        return d.strftime("%a %H:%M")
    return d.strftime("%d %b %H:%M")


def listed(conn, now: float) -> list[dict]:
    """The phones a summary lists now (see the module's note), Offline first, then by name."""
    statuses = [st for st, a in (("offline", "offline"), ("app_not_reporting", "app")) if admins_get(a)]
    if not statuses:
        return []
    rows = conn.execute(f"SELECT * FROM phones WHERE monitored = 1 AND retired = 0 AND status IN "
                        f"({','.join('?' * len(statuses))})", statuses).fetchall()
    out = []
    for p in rows:
        if monitor.alerts_paused(p, now) or monitor.watching_paused(p, now) or dnm.active(p, now):
            continue
        if monitor.alert_delay_min(p) and not (p["offline_alerted"] or p["app_alerted"]):
            continue                          # its own Alert-when-offline time hasn't passed yet
        since = p["offline_since"] or p["status_since"] or p["last_checkin"]
        name = phones.display_name(conn, p)
        owner = phones.first_name(phones.owner_name(conn, p["owner_id"])) if p["owner_id"] else None
        out.append({"id": p["id"], "name": name, "owner": owner if owner and owner.lower() not in name.lower() else None,
                    "status": p["status"], "since": since, "battery": p["battery"], "charging": bool(p["charging"])})
    out.sort(key=lambda x: (x["status"] != "offline", x["name"].lower()))
    return out


def text(items: list[dict], now: float, lost: bool = False) -> str:
    n = len(items)
    lines = [f"{n} phone{'' if n == 1 else 's'} can't be reached ({config.hhmm(now)}):"]
    for x in items:
        bits = [x["name"] + (f" ({x['owner']})" if x["owner"] else "")]
        state = monitor.STATUS_LABEL.get(x["status"], x["status"])
        if x["since"]:
            state += f" for {monitor.duration_text(now - x['since'], True)}, since {_since_text(x['since'], now)}"
        bits.append(state)
        if x["battery"] is not None:
            bits.append(f"battery {x['battery']}%" + (" charging" if x["charging"] else "") + " at the last check-in")
        lines.append("• " + " — ".join(bits))
    if lost:
        lines.append("Home Assistant has lost contact with the phones: the problem may be at home.")
    return "\n".join(lines)


def due_slot(times: list[str], last: float | None, now: float) -> float | None:
    """The latest chosen time at or before now (today or yesterday, local) if it wasn't handled and isn't more than
    2 hours ago."""
    local = config.local(now)
    best = None
    for t in times:
        m = alerts._mins(t)
        if m is None:
            continue
        for back in (0, 1):
            ts = (local - timedelta(days=back)).replace(hour=m // 60, minute=m % 60, second=0, microsecond=0).timestamp()
            if ts <= now and (best is None or ts > best):
                best = ts
    if best is None or now - best > CATCH_UP_S or (last is not None and last >= best):
        return None
    return best


def run(now: float | None = None) -> list:
    """Send the summaries that are due (from the watcher's round). Blocking. Returns [(user id, sent?)] (tests)."""
    now = config.clock() if now is None else now
    s = monitor.snapshot()
    if now - monitor.started_at() < (s["interval_min"] + s["startup_grace_min"]) * 60:
        return []                             # the start-up grace: no phone is Offline yet
    plan = []
    with db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM users WHERE offline_mode = 'summary'").fetchall()
        items, lost = None, None
        for r in rows:
            u = auth.user_dict(r)
            if not u["is_admin"]:
                continue
            slot = due_slot(prefs(r)["times"], r["offline_digest_at"], now)
            if slot is None:
                continue
            conn.execute("UPDATE users SET offline_digest_at = ? WHERE id = ?", (slot, u["id"]))
            if items is None:
                items = listed(conn, now)
                lost = bool(db.state_get(conn, "lost_contact"))
            plan.append((u, text(items, now, lost) if items else None))
    out = []
    for u, msg in plan:
        if msg is None:                       # all online: nothing to send
            out.append((u["id"], False))
            continue
        r = alerts.send("summary", None, msg, now=now, only=[u], quiet=False)
        out.append((u["id"], bool(r["sent"])))
    return out


def test_text(now: float | None = None) -> str:
    now = config.clock() if now is None else now
    with db.get_conn() as conn:
        items = listed(conn, now)
        lost = bool(db.state_get(conn, "lost_contact"))
    body = text(items, now, lost) if items else "All phones are online — at a summary time nothing would be sent."
    return body + "\n(This is a test.)"
