"""Admin mode (spec/SPEC.md §5, §8): All phones (the monitored phones, and the Available devices an admin adds to
monitoring), a phone's settings, App settings, People (children), the alerts' Test buttons, a test forward, and Storage
(backup and restore)."""
import os

from fastapi import APIRouter, Body, Depends, File, HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool

from .. import actions, alerts, app_messages, auth, bedtime, config, db, health, lost, monitor, phones, settings, timeline, views
from ..auth import require_admin
from ..common import backup_core, ha_client, ha_notify, people_admin

router = APIRouter(prefix="/api")

PROBLEM_ORDER = {"offline": 0, "app_not_reporting": 1, "cant_check": 2, "late": 3, "paused": 5, "dnm": 5, "online": 6,
                 "unknown": 7}
KIND_GUESS = {"phone": "Phone", "tablet": "Tablet", "ignored": "Watch or other device"}


def available(conn, p, s: dict) -> dict:
    """A device no admin added (Available devices): what discovery knows, with its kind guess and owner."""
    return {"id": p["id"], "name": s["name"], "owner": s["owner"], "ownerId": p["owner_id"], "kind": p["kind"],
            "kindGuess": KIND_GUESS.get(p["kind"], p["kind"]), "model": p["model"], "manufacturer": p["manufacturer"],
            "osName": p["os_name"], "inRegistry": bool(p["in_registry"]), "nameOwn": p["name"],
            "intervalOwn": p["interval_min"], "offlineAlert": monitor.spell_view(p, config.clock())}


@router.get("/admin/phones")
def all_phones(admin: dict = Depends(require_admin), history: bool = False):
    """All phones: the summary strip, not used lately, the monitored phones (problems first) and tablets, and the
    Available devices no admin has added yet (watches and other devices last). `history`: also each monitored phone's
    24-hour online strip (`lines`, by phone id; none for a phone hidden from this admin, §9)."""
    now = config.clock()
    unused_s = settings.get("unused_days") * 86400
    with db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM phones WHERE retired = 0 ORDER BY first_seen").fetchall()
        best = health.newest(conn)
        items = [(p, views.summary(conn, p, now, viewer=admin, best=best)) for p in rows]
        avail = [available(conn, p, s) for p, s in items if not p["monitored"]]
        lost = db.state_get(conn, "lost_contact")
        lines = {p["id"]: timeline.strip(conn, p["id"], now) for p, s in items
                 if history and p["monitored"] and not s.get("hidden")}
    def order(x):
        return (PROBLEM_ORDER.get(x[1]["status"], 9), x[1]["name"].lower())
    items = [(p, s) for p, s in items if p["monitored"]]
    paused = sum(1 for p, s in items if s.get("paused") or s["status"] in ("paused", "dnm"))
    strip = {"phones": len(items),
             "online": sum(1 for p, s in items if s["status"] == "online"),
             "offline": sum(1 for p, s in items if s["status"] in ("offline", "app_not_reporting")),
             "late": sum(1 for p, s in items if s["status"] in ("late", "cant_check")), "paused": paused}
    unused = [s for p, s in items if not p["keep_unused"] and now - (p["last_checkin"] or p["first_seen"]) > unused_s]
    avail.sort(key=lambda a: (a["kind"] == "ignored", not a["inRegistry"], a["name"].lower()))
    ordered = [s for p, s in sorted(items, key=order)]
    return {"summary": strip, "lostContact": lost, "unused": unused, "available": avail,
            "people": by_person(ordered), "lostMode": [{"id": s["id"], "name": s["name"], **s["lost"]}
                                                      for s in ordered if s.get("lost")],
            "phones": [s for p, s in sorted(items, key=order) if p["kind"] != "tablet"],
            "tablets": [s for p, s in sorted(items, key=order) if p["kind"] == "tablet"],
            "checkins": "events" if monitor.reported_events_seen(now) else "polling",
            "lines": lines if history else None,
            "haLoaded": phones.LIVE.loaded_at is not None, "connected": app_messages.connected_apps()}


def by_person(items: list[dict]) -> list[dict]:
    """All phones grouped by person (§15.8): each owner's monitored devices (phones and tablets together, problems
    first as given), owners by name, devices without an owner last."""
    groups: dict = {}
    for s in items:
        key = s.get("ownerId") or ""
        g = groups.setdefault(key, {"ownerId": s.get("ownerId"), "owner": s.get("owner") or "No owner", "phones": []})
        g["phones"].append(s["id"])
    return sorted(groups.values(), key=lambda g: (g["ownerId"] is None, g["owner"].lower()))


@router.put("/admin/phones/{pid}/bedtime")
def set_bedtime(pid: str, body: dict = Body(...), admin: dict = Depends(require_admin)):
    """A child's bedtime: {windows: [{days, start, end}], parents: [user ids], dnd: bool} (§15.4)."""
    with db.get_conn() as conn:
        p = phones.get(conn, pid)
        if p is None or not p["monitored"]:
            raise HTTPException(404, "No such monitored phone.")
        try:
            bedtime.set_bedtime(conn, p, admin, body, config.clock())
        except actions.Refused as e:
            raise HTTPException(e.status, e.text)
    from .api import phone
    return phone(pid, admin)


@router.post("/admin/refresh")
def refresh(admin: dict = Depends(require_admin)):
    """Read Home Assistant's devices again now."""
    res = phones.refresh()
    if not res["ok"]:
        raise HTTPException(502, "Home Assistant's devices couldn't be read — is the app running inside Home Assistant?")
    monitor.WAKE.set()
    return all_phones(admin)


@router.put("/admin/phones/{pid}")
def edit_phone(pid: str, body: dict = Body(...), admin: dict = Depends(require_admin)):
    """A monitored phone's settings: {name?, kind?: phone|tablet, intervalMin?: null|5|10|15, ownerId?: null|id,
    notifyService?: null|text, offlineAlertAfterMin?: null|minutes}. Adding to and stopping monitoring have their own
    routes."""
    body = body if isinstance(body, dict) else {}
    keys = {"name", "kind", "intervalMin", "ownerId", "notifyService", "offlineAlertAfterMin"}
    if set(body) - keys:
        raise HTTPException(422, f"Unknown field(s): {', '.join(sorted(set(body) - keys))}.")
    with db.get_conn() as conn:
        p = phones.get(conn, pid)
        if p is None:
            raise HTTPException(404, "No such phone.")
        if not p["monitored"]:
            raise HTTPException(409, "Add it to monitoring first.")
        sets, notes = _phone_fields(conn, p, body, admin)
        if "notifyService" in body:
            svc = body["notifyService"]
            if svc in (None, ""):
                sets["notify_service"] = None
            else:
                svc = ha_notify.bare(str(svc).strip())
                if not phones.NOTIFY_RE.match(svc):
                    raise HTTPException(422, "A notify service like mobile_app_ashas_pixel.")
                sets["notify_service"] = svc
            sets["request_error"] = None
            sets["request_error_kind"] = None
        if sets:
            conn.execute("UPDATE phones SET " + ", ".join(f"{k} = ?" for k in sets) + " WHERE id = ?", (*sets.values(), pid))
        for n in notes:
            phones.history(conn, pid, "owner", n, by=admin["id"])
    monitor.WAKE.set()
    from .api import phone
    return phone(pid, admin)


def _phone_fields(conn, p, body: dict, admin: dict) -> tuple[dict, list]:
    """The columns to set from {name, kind, intervalMin, ownerId, offlineAlertAfterMin} (shared by Edit and Add to
    monitoring), and the history notes. HTTPException 422."""
    sets, notes = {}, []
    if "name" in body:
        name = " ".join(str(body["name"] or "").split())[:60]
        sets["name"] = name or None
    if "kind" in body:
        if body["kind"] not in ("phone", "tablet"):
            raise HTTPException(422, "kind is phone or tablet.")
        if body["kind"] != p["kind"]:
            sets["kind"] = body["kind"]
            notes.append(f"Kind: {body['kind']}.")
        sets["kind_confirmed"] = 1
    if "intervalMin" in body:
        if body["intervalMin"] not in (None, 5, 10, 15):
            raise HTTPException(422, "The interval is 15, 10 or 5 minutes, or the household's.")
        sets["interval_min"] = body["intervalMin"]
    if "offlineAlertAfterMin" in body:
        v = body["offlineAlertAfterMin"]
        if v is not None and (isinstance(v, bool) or v not in dict(settings.OFFLINE_DELAYS)):
            raise HTTPException(422, "Alert when offline: the household's default or one of the listed times.")
        if v != p["offline_alert_after_min"]:
            sets["offline_alert_after_min"] = v
            notes.append("Offline alert: " + ("the household's default" if v is None else
                                              f"after {settings.delay_label(v).lower()}") + f" (set by {admin['name']}).")
    if "ownerId" in body:
        oid = body["ownerId"]
        known = oid is None or (isinstance(oid, str) and (oid == p["owner_id"] or conn.execute(
            "SELECT 1 FROM users WHERE id = ?", (oid,)).fetchone()))
        if not known:
            raise HTTPException(422, "Choose someone who has opened the app.")
        if oid != p["owner_id"]:
            sets.update(owner_id=oid, owner_by_admin=1, fwd_on=0)       # forwarding is the owner's own choice
            notes.append(f"Owner set to {phones.owner_name(conn, oid) or 'nobody'} by {admin['name']}.")
    return sets, notes


@router.post("/admin/phones/{pid}/monitor")
def add_to_monitoring(pid: str, body: dict = Body(...), admin: dict = Depends(require_admin)):
    """Add to monitoring (§2): {ownerId, name, kind: phone|tablet, intervalMin, offlineAlertAfterMin?} in one go. From
    then on it is asked to report when overdue, its alerts go out and its history is kept; its owner gets My phone, and
    decides about forwarding there."""
    body = body if isinstance(body, dict) else {}
    keys = {"name", "kind", "intervalMin", "ownerId", "offlineAlertAfterMin"}
    if set(body) - keys:
        raise HTTPException(422, f"Unknown field(s): {', '.join(sorted(set(body) - keys))}.")
    if body.get("kind") not in ("phone", "tablet"):
        raise HTTPException(422, "Choose Phone or Tablet.")
    now = config.clock()
    with db.get_conn() as conn:
        p = phones.get(conn, pid)
        if p is None:
            raise HTTPException(404, "No such phone.")
        if p["monitored"]:
            raise HTTPException(409, "It is already monitored.")
        sets, notes = _phone_fields(conn, p, body, admin)
        owner = sets.get("owner_id", p["owner_id"])
        sets.update(monitored=1, monitored_since=now, kind_confirmed=1, welcome=1 if owner else 0, status="unknown",
                    status_reason=None, status_since=now, late_since=None, requests_sent=0, overdue_from=None,
                    request_error=None, request_error_kind=None, stop_requested_at=None, low_battery_sent=0,
                    offline_since=None, offline_counted_s=0, offline_counted_at=None, offline_alerted=0, app_alerted=0,
                    last_reboot=phones.last_reboot(pid) or p["last_reboot"])      # no "restarted" alert for the past
        level, chg = phones.battery(pid), phones.charging(pid)
        if level is not None:
            sets.update(battery=level, charging=None if chg is None else int(chg))
        conn.execute("UPDATE phones SET " + ", ".join(f"{k} = ?" for k in sets) + " WHERE id = ?", (*sets.values(), pid))
        phones.history(conn, pid, "owner", f"Added to monitoring by {admin['name']}.", by=admin["id"], now=now)
        for n in notes:
            phones.history(conn, pid, "owner", n, by=admin["id"], now=now)
    monitor.WAKE.set()
    from .api import phone
    return phone(pid, admin)


@router.post("/admin/phones/{pid}/unmonitor")
def stop_monitoring(pid: str, body: dict = Body(...), admin: dict = Depends(require_admin)):
    """Stop monitoring {keepHistory: bool}: it goes back to Available devices. Forwarding stops (the owner decides
    again if it is added back). keepHistory false deletes its history, Check-now log and forward times too."""
    keep = (body or {}).get("keepHistory") if isinstance(body, dict) else None
    if not isinstance(keep, bool):
        raise HTTPException(422, "Say whether to keep its history (keepHistory: true or false).")
    lost.stop(pid, None, "monitoring stopped")
    actions.restore(pid, "monitoring stopped", by_name="Household Phones")
    now = config.clock()
    with db.get_conn() as conn:
        p = phones.get(conn, pid)
        if p is None:
            raise HTTPException(404, "No such phone.")
        conn.execute("UPDATE phones SET monitored = 0, monitored_since = NULL, welcome = 0, fwd_on = 0, fwd_error = NULL, "
                     "pause_kind = NULL, pause_until = NULL, pause_until_online = 0, pause_reason = NULL, pause_by = NULL, "
                     "stop_requested_at = NULL, status = 'unknown', status_reason = 'not_monitored', status_since = ?, "
                     "late_since = NULL, requests_sent = 0, overdue_from = NULL, request_error = NULL, "
                     "request_error_kind = NULL, offline_since = NULL, offline_counted_s = 0, offline_counted_at = NULL, "
                     "offline_alerted = 0, app_alerted = 0 WHERE id = ?", (now, pid))
        conn.execute("DELETE FROM alert_queue WHERE phone_id = ?", (pid,))
        conn.execute("DELETE FROM alert_state WHERE phone_id = ?", (pid,))
        if keep:
            phones.history(conn, pid, "owner", f"Monitoring stopped by {admin['name']} (history kept).", by=admin["id"],
                           now=now)
        else:
            for t in ("history", "checks", "forwards", "actions", "status_log"):
                conn.execute(f"DELETE FROM {t} WHERE phone_id = ?", (pid,))
            timeline.forget()
            conn.execute("UPDATE phones SET last_checkin = NULL, last_spontaneous = NULL, last_request_at = NULL, "
                         "battery = NULL, charging = NULL, answers_requests = NULL, redacted_at = NULL, "
                         "checkin_entity = NULL, checkin_kind = NULL, shutdown_at = NULL WHERE id = ?", (pid,))
            monitor.forget(pid)
    monitor.WAKE.set()
    return all_phones(admin)


@router.post("/admin/phones/{pid}/keep")
def keep(pid: str, admin: dict = Depends(require_admin)):
    with db.get_conn() as conn:
        if not conn.execute("UPDATE phones SET keep_unused = 1 WHERE id = ? AND retired = 0", (pid,)).rowcount:
            raise HTTPException(404, "No such phone.")
    return all_phones(admin)


@router.delete("/admin/phones/{pid}")
def remove(pid: str, admin: dict = Depends(require_admin)):
    """Remove a device (Not used lately): its history goes with it. It comes back if Home Assistant still has it."""
    with db.get_conn() as conn:
        if not conn.execute("DELETE FROM phones WHERE id = ?", (pid,)).rowcount:
            raise HTTPException(404, "No such phone.")
    return all_phones(admin)


# --------------------------------------------------------------------------------------------- tests

@router.post("/admin/test-alert")
def test_alert(body: dict = Body(...), admin: dict = Depends(require_admin)):
    """A sample of one alert, to you, the way alerts are delivered (App settings)."""
    alert = (body or {}).get("alert") if isinstance(body, dict) else None
    if alert not in alerts.ALERTS or alert == "request":
        raise HTTPException(422, "Choose an alert.")
    samples = {"offline": "Asha's Pixel is offline: no check-in since 14:02, battery 9% then.",
               "app": "Asha's Pixel: the HA app stopped reporting — signed out?",
               "lost_contact": "Home Assistant couldn't send requests to 5 of 6 phones since 14:02 — check Home "
                               "Assistant's internet / Nabu Casa.",
               "back": "Asha's Pixel is back online (battery 40%).",
               "restart": "Asha's Pixel restarted: battery was 3%, probably ran out.",
               "battery": "Asha's Pixel is at 12% and not charging.",
               "charge": "Asha's Pixel is at 25% and not charging."}
    r = alerts.send(alert, None, samples[alert] + " (This is a test.)", test=True, only=[admin])
    if not r["sent"]:
        raise HTTPException(409, "Nothing reached you: no phone is linked to you in Home Assistant (Settings → People → "
                                 "Track device) and no other delivery is chosen (App settings → Deliver alerts by).")
    return {"sent": r["sent"]}


@router.post("/admin/test-forward")
def test_forward(admin: dict = Depends(require_admin)):
    """"📱 Test forward" into your own 📌 My room, disappearing after 1 hour — through the same path as a forward."""
    if not app_messages.chat_ready():
        raise HTTPException(409, "Household Chat isn't available — it isn't installed, or it hasn't said hello in the "
                                 "last day (is it running?).")
    ok = app_messages.post_forward("test", {"requested_by": admin["id"], "my_room": True,
                                            "label": f"📱 Household Phones · test · {config.hhmm(config.clock())}",
                                            "text": "A test forward. Forwarded messages look like this and disappear "
                                                    "after the time each owner chooses.", "disappear_after": "1h"})
    if not ok:
        raise HTTPException(502, "It couldn't be sent.")
    return {"ok": True}


@router.get("/admin/connected")
def connected(admin: dict = Depends(require_admin)):
    return app_messages.connected_apps()


# --------------------------------------------------------------------------------------------- settings

@router.get("/admin/settings")
def get_settings(admin: dict = Depends(require_admin)):
    return settings.payload()


@router.put("/admin/settings")
def put_settings(body: dict = Body(...), admin: dict = Depends(require_admin)):
    try:
        settings.update(body, admin)
    except settings.SettingsError as e:
        raise HTTPException(422, str(e))
    monitor.WAKE.set()
    return settings.payload()


# --------------------------------------------------------------------------------------------- people

@router.get("/admin/people")
def admin_people(refresh: bool = False, admin: dict = Depends(require_admin)):
    people_admin.refresh_people(refresh)
    with db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM users ORDER BY name COLLATE NOCASE").fetchall()
        notify = {r["id"]: ha_notify.assigned_services(conn, {"id": r["id"]}) for r in rows}
        owned = {r["id"]: conn.execute("SELECT COUNT(*) FROM phones WHERE owner_id = ? AND retired = 0",
                                       (r["id"],)).fetchone()[0] for r in rows}
    out = []
    for r in rows:
        u = auth.user_dict(r)
        out.append({"id": u["id"], "name": u["name"], "username": u["username"], "you": u["id"] == admin["id"],
                    "isAdmin": u["is_admin"], "isChild": u["is_child"], "phones": owned[u["id"]],
                    "notify": notify[u["id"]], "ha": people_admin.ha_person_json(u["id"])})
    return {"people": out}


@router.put("/admin/people/{uid}")
def admin_person(uid: str, body: dict = Body(...), admin: dict = Depends(require_admin)):
    child = (body or {}).get("isChild") if isinstance(body, dict) else None
    if not isinstance(child, bool) or set(body) - {"isChild"}:
        raise HTTPException(422, "Send isChild: true or false.")
    with db.get_conn() as conn:
        r = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        if r is None:
            raise HTTPException(404, "No such person.")
        if child and auth.user_dict(r)["is_admin"]:
            raise HTTPException(409, f"{r['name']} is an admin, and admins can't be marked as children.")
        conn.execute("UPDATE users SET is_child = ? WHERE id = ?", (int(child), uid))
        if child:            # a child can't pause being watched, nor have do-not-monitor hours (§9)
            conn.execute("UPDATE phones SET pause_kind = NULL, pause_until = NULL WHERE owner_id = ? AND "
                         "pause_kind = 'watching'", (uid,))
            conn.execute("UPDATE phones SET dnm_windows = NULL WHERE owner_id = ?", (uid,))
    return admin_people(admin=admin)


MAX_SERVICES_PER_USER = 5
_tests = people_admin.TestLimiter(5)


@router.get("/admin/notify-services")
def notify_services(refresh: bool = False, admin: dict = Depends(require_admin)):
    return people_admin.notify_services(refresh)


def _known_person(conn, uid: str):
    row = conn.execute("SELECT id, name FROM users WHERE id = ?", (uid,)).fetchone()
    if row is None:
        raise HTTPException(404, "No such person.")
    return row


@router.post("/admin/people/{uid}/notify", status_code=201)
def add_notify(uid: str, body: dict = Body(...), admin: dict = Depends(require_admin)):
    svc = people_admin.clean_service(body.get("service") if isinstance(body, dict) else None)
    with db.get_conn() as conn:
        _known_person(conn, uid)
        return {"notify": people_admin.add_service(conn, uid, svc, admin.get("username") or admin["id"], config.iso(),
                                                   limit=MAX_SERVICES_PER_USER)}


@router.delete("/admin/people/{uid}/notify/{svc}")
def remove_notify(uid: str, svc: str, admin: dict = Depends(require_admin)):
    svc = people_admin.clean_service(svc)
    with db.get_conn() as conn:
        _known_person(conn, uid)
        return {"notify": people_admin.remove_service(conn, uid, svc, "That service isn't assigned to them.")}


@router.post("/admin/people/{uid}/notify/test")
def test_notify(uid: str, admin: dict = Depends(require_admin)):
    with db.get_conn() as conn:
        row = _known_person(conn, uid)
        services = ha_notify.services_for({"id": uid}, conn)
    if not services:
        raise HTTPException(409, f"{row['name']} has no phone linked in Home Assistant (Settings → People → "
                                 f"{row['name']} → Track device) and no extra notify service here.")
    _tests.check(uid)
    results = ha_notify.send_to_services(services, "Household Phones", f"Test for {row['name']} — phone alerts will "
                                         "arrive like this.")
    people_admin.require_one_sent(results, services[0], ha_client.has_token)
    return {"results": people_admin.test_results(results, as_list=True)}


# --------------------------------------------------------------------------------------------- storage

@router.get("/admin-storage-download-db")
def download_db(admin: dict = Depends(require_admin)):
    """A full copy of the database (the App-messages signing key is blanked in it). It holds no forwarded text:
    the app never keeps any."""
    tmp_path = db.backup_to_tempfile(after=settings.REGISTRY.scrub_secrets)
    return backup_core.send_file(tmp_path, backup_core.file_name("household-phones-backup", ".db", now=config.now()))


@router.post("/admin-storage-import-db")
async def import_db(file: UploadFile = File(...), admin: dict = Depends(require_admin)):
    """Replaces the whole database with a downloaded backup (validated first); this install's secret settings are
    kept."""
    saved = await run_in_threadpool(settings.REGISTRY.saved_secrets)
    tmp_path = await backup_core.receive(file, config.DATA_DIR)
    try:
        try:
            db.validate_backup_file(tmp_path)
        except ValueError as e:
            raise HTTPException(400, str(e))
        await run_in_threadpool(db.import_from_tempfile, tmp_path)
        await run_in_threadpool(settings.REGISTRY.keep_secrets, saved)
        settings.REGISTRY.invalidate()
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    await run_in_threadpool(phones.refresh)
    return {"status": "ok"}
