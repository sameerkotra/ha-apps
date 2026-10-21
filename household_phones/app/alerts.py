"""Alerts about phones (spec/SPEC.md §5 "Alerts", §3.1): who gets them, the cooldown, quiet hours, and how they are
delivered — the recipients' phones (never the phone the alert is about, except the charging reminder when chosen),
a Home Assistant notification, or Household Chat (each recipient's 📌 My room, posted as them).

`send()` decides under one short database connection, then delivers with none open (Home Assistant calls are
blocking: from a thread). An alert's text names the phone, its status and battery — never a forwarded message.
"""
import logging
from datetime import datetime, timedelta

from . import auth, config, db, dnm, phones, settings
from .common import ha_notify

logger = logging.getLogger("alerts")

ALERTS = {
    "offline": ("Offline", "alert_offline", "alert_offline_to"),
    "app": ("App not reporting", "alert_app", "alert_app_to"),
    "lost_contact": ("Lost contact with all phones", "alert_lost_contact", None),
    "back": ("Back online", "alert_back", "alert_back_to"),
    "restart": ("Restarted", "alert_restart", "alert_restart_to"),
    "battery": ("Battery low", "alert_battery", "alert_battery_to"),
    "charge": ("Charging reminder", "alert_charge", "alert_charge_to"),
    "request": ("Stop monitoring request", None, None),
}


def _mins(hhmm: str) -> int | None:
    try:
        h, m = hhmm.split(":")
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return None


def in_quiet(start: str, end: str, when: datetime) -> bool:
    """Is the local time `when` inside start–end (which may run over midnight)? Empty times: never."""
    a, b = _mins(start), _mins(end)
    if a is None or b is None or a == b:
        return False
    t = when.hour * 60 + when.minute
    return a <= t < b if a < b else (t >= a or t < b)


def quiet_end(start: str, end: str, when: datetime) -> datetime:
    """The next end of the quiet time after `when` (local)."""
    b = _mins(end) or 0
    out = when.replace(hour=b // 60, minute=b % 60, second=0, microsecond=0)
    if out <= when:
        out += timedelta(days=1)
    return out


def enabled(alert: str) -> bool:
    key = ALERTS[alert][1]
    return bool(settings.get(key)) if key else True


def recipients(conn, to: str, phone) -> list[dict]:
    """The people an alert goes to: admins (who have opened the app), the phone's owner, or both."""
    out: dict = {}
    if to in ("admins", "both"):
        for r in conn.execute("SELECT * FROM users ORDER BY name"):
            u = auth.user_dict(r)
            if u["is_admin"]:
                out[u["id"]] = u
    if to in ("owner", "both") and phone is not None and phone["owner_id"]:
        r = conn.execute("SELECT * FROM users WHERE id = ?", (phone["owner_id"],)).fetchone()
        out[phone["owner_id"]] = auth.user_dict(r) if r else {"id": phone["owner_id"], "name": "Owner",
                                                               "username": None, "is_admin": False, "is_child": False}
    return list(out.values())


def _quiet_for(conn, user: dict) -> tuple[str, str]:
    r = conn.execute("SELECT quiet_start, quiet_end FROM users WHERE id = ?", (user["id"],)).fetchone()
    if r and r["quiet_start"] and r["quiet_end"]:
        return r["quiet_start"], r["quiet_end"]
    return settings.get("quiet_start") or "", settings.get("quiet_end") or ""


def cooling(conn, phone_id: str | None, alert: str, now: float) -> bool:
    if phone_id is None:
        return False
    r = conn.execute("SELECT last_sent FROM alert_state WHERE phone_id = ? AND alert = ?", (phone_id, alert)).fetchone()
    return bool(r and r["last_sent"] and now - r["last_sent"] < settings.get("alert_cooldown_min") * 60)


def mark_sent(conn, phone_id: str | None, alert: str, now: float) -> None:
    if phone_id is None:
        return
    conn.execute("INSERT INTO alert_state (phone_id, alert, last_sent, held) VALUES (?,?,?,0) ON CONFLICT(phone_id, "
                 "alert) DO UPDATE SET last_sent = excluded.last_sent, held = 0", (phone_id, alert, now))


def set_held(conn, phone_id: str, alert: str, held: bool) -> None:
    conn.execute("INSERT INTO alert_state (phone_id, alert, held) VALUES (?,?,?) ON CONFLICT(phone_id, alert) DO "
                 "UPDATE SET held = excluded.held", (phone_id, alert, 1 if held else 0))


def held(conn, phone_id: str, alert: str) -> bool:
    r = conn.execute("SELECT held FROM alert_state WHERE phone_id = ? AND alert = ?", (phone_id, alert)).fetchone()
    return bool(r and r["held"])


def send(alert: str, phone_id: str | None, message: str, *, to: str | None = None, now: float | None = None,
         test: bool = False, also_phone: bool = False, only: list | None = None) -> dict:
    """Send one alert. `to` defaults to the alert's App setting; `only`: these people instead (a test). Returns
    {"sent": [names], "queued": [names], "failed": [names], "skipped": reason|None}. Blocking."""
    now = config.clock() if now is None else now
    title = f"📱 {ALERTS[alert][0]}" + (" (test)" if test else "")
    delivery = list(settings.get("alert_delivery") or ["phones"])
    if alert == "lost_contact":
        delivery = sorted(set(delivery) | {"ha", "phones"})
    plan, queued, own_service = [], [], None
    with db.get_conn() as conn:
        phone = conn.execute("SELECT * FROM phones WHERE id = ?", (phone_id,)).fetchone() if phone_id else None
        if not test and phone is not None and (not phone["monitored"] or phone["retired"]):
            return {"sent": [], "queued": [], "failed": [], "skipped": "not monitored"}
        if not test and phone is not None and dnm.active(phone, now):
            return {"sent": [], "queued": [], "failed": [], "skipped": "do-not-monitor hours"}
        if not test and cooling(conn, phone_id, alert, now):
            return {"sent": [], "queued": [], "failed": [], "skipped": "cooldown"}
        people = only if only is not None else recipients(conn, to or (ALERTS[alert][2] and settings.get(ALERTS[alert][2]))
                                                          or "admins", phone)
        if phone is not None:
            own_service = phones.notify_service(phone)
        local_now = config.local(now)
        for u in people:
            qs, qe = _quiet_for(conn, u)
            if not test and in_quiet(qs, qe, local_now):
                due = quiet_end(qs, qe, local_now).timestamp()
                conn.execute("INSERT INTO alert_queue (user_id, phone_id, alert, title, message, due) VALUES (?,?,?,?,?,?)",
                             (u["id"], phone_id, alert, title, message, due))
                queued.append(u["name"])
                continue
            services = ha_notify.services_for(u, conn) if "phones" in delivery else []
            services = [s for s in services if s != own_service]           # never the phone it is about
            plan.append((u, services))
        if not test:
            mark_sent(conn, phone_id, alert, now)
    sent, failed = [], []
    for u, services in plan:
        ok = False
        if services:
            res = ha_notify.send_to_services(services, title, message)
            ok = any(res.values())
        if "chat" in delivery:
            from . import app_messages
            ok = app_messages.post_alert(u["id"], title, message) or ok
        (sent if ok else failed).append(u["name"])
    if also_phone and own_service:
        if ha_notify.send_notify(own_service, title, message):
            sent.append("the phone itself")
    if "ha" in delivery:
        from . import ha_source
        if ha_source.SOURCE.ha_notification(title, message, f"household_phones_{alert}_{phone_id or 'all'}"):
            sent.append("Home Assistant")
    if phone_id and not test:
        with db.get_conn() as conn:
            who = ", ".join(sent + [f"{n} (later: quiet hours)" for n in queued]) or "nobody (no phone to reach)"
            phones.history(conn, phone_id, "alert", f"{ALERTS[alert][0]} alert: {who}.", now=now)
    if failed:
        logger.info("Alert %s for %s reached nobody for %d people.", alert, phone_id or "all", len(failed))
    return {"sent": sent, "queued": queued, "failed": failed, "skipped": None}


def release_queue(now: float | None = None) -> int:
    """Alerts whose recipient's quiet hours have ended. Blocking."""
    now = config.clock() if now is None else now
    with db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM alert_queue WHERE due <= ?", (now,)).fetchall()
        conn.execute("DELETE FROM alert_queue WHERE due <= ?", (now,))
        users = {r["user_id"]: conn.execute("SELECT * FROM users WHERE id = ?", (r["user_id"],)).fetchone() for r in rows}
    n = 0
    for r in rows:
        u = users.get(r["user_id"])
        if u is None:
            continue
        u = auth.user_dict(u)
        own = None
        if r["phone_id"]:
            with db.get_conn() as conn:
                ph = conn.execute("SELECT * FROM phones WHERE id = ?", (r["phone_id"],)).fetchone()
            if ph is not None and (not ph["monitored"] or dnm.active(ph, now)):
                continue                      # not monitored (now): dropped, never sent later (§3.1)
            own = phones.notify_service(ph) if ph else None
        services = [s for s in ha_notify.services_for(u) if s != own]
        if services and any(ha_notify.send_to_services(services, r["title"], r["message"]).values()):
            n += 1
    return n
