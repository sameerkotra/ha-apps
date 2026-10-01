"""Reminders (§9): your own reminder settings, the 🔔 shortcuts, and —
for admins — where each user's reminders go: their phones from Home Assistant
(Settings → People → Track device, read by ha_people) plus any extra notify
services assigned here."""
import time

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import Field

from .. import features, config, db, graph as graph_mod, ha_client, ha_notify, reminders
from ..auth import require_admin, require_user
from ..common import Strict
from ..history import Batch

router = APIRouter(prefix="/api", tags=["reminders"], dependencies=[Depends(features.required("reminders"))])

MAX_SERVICES_PER_USER = 5
TEST_INTERVAL_SECONDS = 10
_last_test: dict[str, float] = {}


# ---------- your own settings ----------
class PrefsIn(Strict):
    enabled: bool | None = None
    time: str | None = None
    birthdays: bool | None = None
    anniversaries: bool | None = None
    remembrance: bool | None = None
    leadDays: int | None = Field(default=None, ge=0, le=reminders.MAX_LEAD_DAYS)
    scope: str | None = Field(default=None, pattern="^(close|all)$")
    tithi: bool | None = None
    tithiLeadDays: int | None = Field(default=None, ge=0, le=reminders.MAX_TITHI_LEAD_DAYS)
    milestones: bool | None = None


NO_PHONE = ("No phone is linked to you yet. In Home Assistant, go to Settings → People → you → Track device and "
            "pick your phone (the Home Assistant Companion app); it's picked up here within 5 minutes. An admin can "
            "also add another notify service under Admin → Users.")


def _reachable(conn, user: dict) -> list[str]:
    """Everything that reaches `user` as "notify.x": their phones from Home Assistant, then the extras."""
    return [f"notify.{s}" for s in ha_notify.services_for(user, conn)]


def _prefs_out(conn, user: dict) -> dict:
    prefs = reminders.get_prefs(conn, user["id"])
    services = _reachable(conn, user)
    g = graph_mod.get(conn)
    me = user["me_person_id"] if user["me_person_id"] in g.people else None
    close = reminders.close_family(g, me)
    warning = None
    if prefs["enabled"] and not services:
        warning = "Your reminders are on, but nothing is sent. " + NO_PHONE
    elif prefs["enabled"] and prefs["scope"] == "close" and not me:
        warning = "“Close family” needs “This is me”. Set it, or choose everyone with 🔔 on."
    return dict(prefs, services=services, canEnable=bool(services), meSet=bool(me),
                peopleOn=sum(1 for p in g.people.values() if p.remind),
                closeFamily={"total": len(close), "off": sum(1 for pid in close if not g.people[pid].remind)} if me else None,
                timezone=config.timezone_name(), warning=warning)


@router.get("/reminders/prefs")
def get_prefs(user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        return _prefs_out(conn, user)


@router.put("/reminders/prefs")
def put_prefs(body: PrefsIn, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        prefs = reminders.get_prefs(conn, user["id"])
        for k in body.model_fields_set:
            v = getattr(body, k)
            if v is None:
                raise HTTPException(422, f"{k} can't be empty.")
            prefs[k] = v
        try:
            prefs["time"] = reminders.valid_time(prefs["time"])
        except ValueError as e:
            raise HTTPException(422, str(e))
        if "enabled" in body.model_fields_set and prefs["enabled"] and not ha_notify.services_for(user, conn):
            raise HTTPException(409, NO_PHONE)
        if "scope" in body.model_fields_set and prefs["scope"] == "close":
            me = user["me_person_id"]
            if not me or not conn.execute("SELECT 1 FROM people WHERE id = ? AND deleted_at IS NULL", (me,)).fetchone():
                raise HTTPException(422, "“Close family” needs “This is me” — set it in Settings first.")
        reminders.save_prefs(conn, user["id"], prefs)
        return _prefs_out(conn, user)


def _rate_limit(key: str) -> None:
    now = time.monotonic()
    last = _last_test.get(key)
    if last is not None and now - last < TEST_INTERVAL_SECONDS:
        raise HTTPException(429, "Please wait a few seconds before sending another test.")
    _last_test[key] = now


def _send_test(services: list[str], message: str) -> dict:
    """Blocking; no DB connection may be open. 502 if nothing was accepted."""
    sent = ha_notify.send_to_services([ha_notify.bare(s) for s in services], reminders.TITLE, message)
    results = {f"notify.{k}": v for k, v in sent.items()}
    if not any(results.values()):
        if not ha_client.has_token():
            raise HTTPException(502, "This app can't reach Home Assistant (no Supervisor token).")
        hint = ha_notify.explain_failure(ha_notify.bare(services[0]))
        raise HTTPException(502, "Home Assistant didn't accept the test notification"
                                 + (f" — {hint}" if hint else " — check the service name."))
    return {"results": results}


@router.post("/reminders/test")
def test_mine(user: dict = Depends(require_user)):
    """One short notification to each of your phones and services (sync route: runs in a thread)."""
    with db.get_conn() as conn:
        services = _reachable(conn, user)
    if not services:
        raise HTTPException(400, NO_PHONE)
    _rate_limit(user["id"])
    return _send_test(services, f"Test from Family Tree: reminders for {user['name']} will arrive here.")


# ---------- the 🔔 switch, several people at once ----------
@router.get("/reminders/close-family")
def close_family_preview(user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        g = graph_mod.get(conn)
    me = user["me_person_id"] if user["me_person_id"] in g.people else None
    close = reminders.close_family(g, me)
    return {"meSet": bool(me), "total": len(close), "off": sum(1 for pid in close if not g.people[pid].remind)}


def _set_remind(conn, user: dict, ids, on: bool, label: str) -> dict:
    ids = [pid for pid in dict.fromkeys(ids)]
    rows = {r["id"]: r for r in conn.execute(
        f"SELECT id, remind FROM people WHERE deleted_at IS NULL AND id IN ({', '.join('?' * len(ids))})", ids)} if ids else {}
    change = [pid for pid in ids if pid in rows and bool(rows[pid]["remind"]) != on]
    if not change:
        return {"count": 0, "batchId": None}
    b = Batch(conn, user["id"], label.format(n=len(change), people="person" if len(change) == 1 else "people"))
    for pid in change:
        b.update("people", pid, {"remind": 1 if on else 0})
    b.touch(*change)
    return {"count": len(change), "batchId": b.id}


@router.post("/reminders/close-family")
def close_family_on(user: dict = Depends(require_user)):
    """Turn 🔔 on for everyone within 3 steps of "This is me" — one undoable batch."""
    with db.get_conn() as conn:
        g = graph_mod.get(conn)
        me = user["me_person_id"] if user["me_person_id"] in g.people else None
        if not me:
            raise HTTPException(422, "Set “This is me” first — close family is worked out from you.")
        ids = sorted(reminders.close_family(g, me))
        return _set_remind(conn, user, ids, True, "Turned reminders on for close family ({n} {people})")


class RemindPeople(Strict):
    ids: list[str] = Field(min_length=1, max_length=50)
    remind: bool


@router.put("/reminders/people")
def set_people(body: RemindPeople, user: dict = Depends(require_user)):
    """The Upcoming view's bell for a couple: both partners in one batch."""
    with db.get_conn() as conn:
        found = conn.execute(f"SELECT COUNT(*) FROM people WHERE deleted_at IS NULL AND id IN "
                             f"({', '.join('?' * len(body.ids))})", body.ids).fetchone()[0]
        if found != len(set(body.ids)):
            raise HTTPException(404, "Someone in that list isn't in the tree (they may have been deleted).")
        return _set_remind(conn, user, body.ids, body.remind,
                           "Turned reminders " + ("on" if body.remind else "off") + " for {n} {people}")


# ---------- admin: extra notify services per user (phones come from Home Assistant) ----------
def _load_user(conn, user_id: str):
    row = conn.execute("SELECT id, name FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row:
        raise HTTPException(404, "That user isn't known to Family Tree.")
    return row


def _service(value) -> str:
    try:
        return ha_notify.normalize_service(value)
    except ValueError as e:
        raise HTTPException(422, str(e))


def _user_notify_out(conn, row) -> dict:
    return {"id": row["id"], "notify": ha_notify.assigned_services(conn, {"id": row["id"]})}


@router.get("/admin/notify-services")
def admin_notify_services(refresh: bool = Query(default=False), admin: dict = Depends(require_admin)):
    """What Home Assistant can notify (actions and entities), cached 60 s. If HA
    can't be asked: 200 with available=false, and the page lets you type a name."""
    try:
        return {"available": True, "error": None, **ha_notify.list_notify_services_blocking(force=refresh)}
    except ha_notify.NotifyListError as e:
        return {"available": False, "error": f"Couldn't read the notify services from Home Assistant: {e}",
                "services": [], "entities": []}


@router.post("/admin/users/{user_id}/notify", status_code=201)
def admin_add_notify(user_id: str, body: dict = Body(...), admin: dict = Depends(require_admin)):
    service = _service(body.get("service"))
    with db.get_conn() as conn:
        row = _load_user(conn, user_id)
        current = ha_notify.assigned_services(conn, {"id": user_id})
        if service not in current and len(current) >= MAX_SERVICES_PER_USER:
            raise HTTPException(422, f"At most {MAX_SERVICES_PER_USER} notify services per person.")
        conn.execute("INSERT OR IGNORE INTO user_notify (user_id, service, added_by, added_at) VALUES (?, ?, ?, ?)",
                     (user_id, service, admin["id"], config.now_iso()))
        return _user_notify_out(conn, row)


@router.delete("/admin/users/{user_id}/notify/{service}")
def admin_remove_notify(user_id: str, service: str, admin: dict = Depends(require_admin)):
    service = _service(service)
    with db.get_conn() as conn:
        row = _load_user(conn, user_id)
        if not conn.execute("DELETE FROM user_notify WHERE user_id = ? AND service = ?", (user_id, service)).rowcount:
            raise HTTPException(404, f"{service} isn't assigned to {row['name']}.")
        return _user_notify_out(conn, row)


@router.post("/admin/users/{user_id}/notify-test")
def admin_test_notify(user_id: str, body: dict | None = Body(default=None), admin: dict = Depends(require_admin)):
    """A test to every phone (from Home Assistant) and extra service of the user
    (or just {"service": ...}); doesn't need their own reminder switches."""
    only = (body or {}).get("service")
    with db.get_conn() as conn:
        row = _load_user(conn, user_id)
        services = _reachable(conn, {"id": user_id})                 # phones + extras
    # the DB connection is closed before Home Assistant is called
    if only is not None:
        only = _service(only)
        if only not in services:
            raise HTTPException(422, f"{only} isn't one of {row['name']}'s.")
        services = [only]
    if not services:
        raise HTTPException(400, f"{row['name']} has no phone linked in Home Assistant (Settings → People) "
                                 "and no extra notify service here.")
    _rate_limit("admin:" + user_id)
    return _send_test(services, f"Test notification from Family Tree for {row['name']}, sent by an admin.")
