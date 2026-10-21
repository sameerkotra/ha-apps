"""Admin mode (spec/SPEC.md §5, §8): All phones, a phone's settings, App settings, People (children), the alerts'
Test buttons, a test forward, and Storage (backup and restore)."""
import os

from fastapi import APIRouter, Body, Depends, File, HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool

from .. import alerts, app_messages, auth, config, db, monitor, phones, settings, views
from ..auth import require_admin
from ..common import backup_core, ha_client, ha_notify, people_admin

router = APIRouter(prefix="/api")

PROBLEM_ORDER = {"offline": 0, "app_not_reporting": 1, "cant_check": 2, "late": 3, "paused": 5, "online": 6, "unknown": 7}


@router.get("/admin/phones")
def all_phones(admin: dict = Depends(require_admin)):
    """All phones: the summary strip, new devices, not used lately, phones (problems first), tablets, ignored."""
    now = config.clock()
    unused_s = settings.get("unused_days") * 86400
    with db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM phones WHERE retired = 0 ORDER BY first_seen").fetchall()
        items = [(p, views.summary(conn, p, now, viewer=admin)) for p in rows]
        lost = db.state_get(conn, "lost_contact")
    def order(x):
        return (PROBLEM_ORDER.get(x[1]["status"], 9), x[1]["name"].lower())
    watched = [s for p, s in items if p["kind"] != "ignored" and p["watched"]]
    paused = sum(1 for s in watched if s.get("paused") or s["status"] == "paused")
    strip = {"phones": len([s for p, s in items if p["kind"] == "phone"]),
             "online": sum(1 for s in watched if s["status"] == "online"),
             "offline": sum(1 for s in watched if s["status"] in ("offline", "app_not_reporting")),
             "late": sum(1 for s in watched if s["status"] in ("late", "cant_check")), "paused": paused}
    new = [s for p, s in items if not p["kind_confirmed"]]
    unused = [s for p, s in items if not p["keep_unused"] and now - (p["last_checkin"] or p["first_seen"]) > unused_s]
    return {"summary": strip, "lostContact": lost,
            "new": new, "unused": unused,
            "phones": [s for p, s in sorted(items, key=order) if p["kind"] == "phone"],
            "tablets": [s for p, s in sorted(items, key=order) if p["kind"] == "tablet"],
            "ignored": [s for p, s in items if p["kind"] == "ignored"],
            "checkins": "events" if monitor.reported_events_seen(now) else "polling",
            "haLoaded": phones.LIVE.loaded_at is not None, "connected": app_messages.connected_apps()}


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
    """{name?, kind?, watched?, intervalMin?: null|5|10|15, ownerId?: null|id, notifyService?: null|text, confirm?}"""
    body = body if isinstance(body, dict) else {}
    keys = {"name", "kind", "watched", "intervalMin", "ownerId", "notifyService", "confirm"}
    if set(body) - keys:
        raise HTTPException(422, f"Unknown field(s): {', '.join(sorted(set(body) - keys))}.")
    with db.get_conn() as conn:
        p = phones.get(conn, pid)
        if p is None:
            raise HTTPException(404, "No such phone.")
        sets, notes = {}, []
        if "name" in body:
            name = " ".join(str(body["name"] or "").split())[:60]
            sets["name"] = name or None
        if "kind" in body:
            if body["kind"] not in phones.KINDS:
                raise HTTPException(422, "kind is phone, tablet or ignored.")
            if body["kind"] != p["kind"]:
                sets["kind"] = body["kind"]
                notes.append(f"Kind: {body['kind']}.")
                if "watched" not in body:
                    sets["watched"] = 1 if body["kind"] == "phone" else (p["watched"] if body["kind"] == "tablet" else 0)
            sets["kind_confirmed"] = 1
        if body.get("confirm") is True:
            sets["kind_confirmed"] = 1
        if "watched" in body:
            if not isinstance(body["watched"], bool):
                raise HTTPException(422, "watched is true or false.")
            if int(body["watched"]) != p["watched"]:
                sets["watched"] = int(body["watched"])
                notes.append("Watched." if body["watched"] else f"Stopped watching (by {admin['name']}).")
                if not body["watched"]:
                    sets["stop_requested_at"] = None
        if "intervalMin" in body:
            if body["intervalMin"] not in (None, 5, 10, 15):
                raise HTTPException(422, "The interval is 15, 10 or 5 minutes, or the household's.")
            sets["interval_min"] = body["intervalMin"]
        if "ownerId" in body:
            oid = body["ownerId"]
            if oid is not None and (not isinstance(oid, str) or not conn.execute("SELECT 1 FROM users WHERE id = ?", (oid,)).fetchone()):
                raise HTTPException(422, "Choose someone who has opened the app.")
            if oid != p["owner_id"]:
                sets.update(owner_id=oid, owner_by_admin=1, fwd_on=0)       # forwarding is the owner's own choice
                notes.append(f"Owner set to {phones.owner_name(conn, oid) or 'nobody'} by {admin['name']}.")
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
               "lost_contact": "Home Assistant has lost contact with the phones (5 of 6 silent since 14:02).",
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
        if child:            # a child can't pause being watched (§9)
            conn.execute("UPDATE phones SET pause_kind = NULL, pause_until = NULL WHERE owner_id = ? AND "
                         "pause_kind = 'watching'", (uid,))
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
