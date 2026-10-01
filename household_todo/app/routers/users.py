"""People (SPEC §6).

Two different needs, two different routes:

- `GET /api/users` — the household member list every signed-in person needs
  for assignment pickers, filters and the admin "Acting as" select. Only
  what those need: id, display name, disabled.
- `/api/admin/users…` and `PATCH /api/users/{id}` — managing people (Admin →
  Users): enable/disable, and which Home Assistant notify services each
  person's reminders go to.
  Admin-only.
"""
import time

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from .. import config, db, ha_notify, ha_people
from ..auth import get_acting_user, require_admin

router = APIRouter(prefix="/api", tags=["users"])

MAX_SERVICES_PER_USER = 10
TEST_INTERVAL_SECONDS = 10
_last_test: dict[str, float] = {}


@router.get("/users")
def list_users(acting: dict = Depends(get_acting_user)):
    """Household members for pickers — everyone, including disabled ones
    (assignment dropdowns hide them, the Acting-as select shows them)."""
    with db.get_conn() as conn:
        rows = conn.execute("SELECT id, name, disabled FROM users ORDER BY name COLLATE NOCASE").fetchall()
    return [{"id": r["id"], "name": r["name"], "disabled": bool(r["disabled"])} for r in rows]


def ha_person_json(user_id: str) -> dict:
    """What Home Assistant says about the person (Settings → People): their person and phones."""
    p = ha_people.person_for(user_id)
    return {"known": ha_people.known(), "person": p["entityId"] if p else None, "personName": p["name"] if p else None,
            "phones": [{"label": ph["label"], "service": f"notify.{ph['service']}" if ph["service"] else None,
                        "tracker": ph["tracker"]} for ph in (p or {}).get("phones", [])]}


def _admin_user_json(conn, row) -> dict:
    user = {"id": row["id"]}
    return {
        "id": row["id"],
        "name": row["name"],
        "username": row["username"],
        "disabled": bool(row["disabled"]),
        "createdAt": row["created_at"],
        "notify": ha_notify.assigned_services(conn, user),     # extra services added here
        "ha": ha_person_json(row["id"]),                       # phones from Home Assistant
    }


def _load_user(conn, user_id: str):
    row = conn.execute("SELECT id, name, username, disabled, created_at FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row:
        raise HTTPException(404, "User not found.")
    return row


@router.get("/admin/users")
def admin_list_users(refresh: bool = Query(default=False), admin: dict = Depends(require_admin)):
    """Admin → Users: everyone who has ever opened the app, with their phones from Home Assistant and
    any extra notify services. `refresh=1` reads Home Assistant's people again first (no DB connection
    is open meanwhile)."""
    if refresh:
        ha_people.refresh_blocking(True)
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, name, username, disabled, created_at FROM users ORDER BY name COLLATE NOCASE").fetchall()
        return {"users": [_admin_user_json(conn, r) for r in rows]}


@router.patch("/users/{user_id}")
def update_user(user_id: str, body: dict = Body(...), admin: dict = Depends(require_admin)):
    disabled = body.get("disabled")
    if not isinstance(disabled, bool):
        raise HTTPException(422, "disabled must be true or false.")
    with db.get_conn() as conn:
        cur = conn.execute("UPDATE users SET disabled = ? WHERE id = ?", (1 if disabled else 0, user_id))
        if cur.rowcount == 0:
            raise HTTPException(404, "User not found.")
        row = conn.execute("SELECT id, name, disabled, created_at FROM users WHERE id = ?", (user_id,)).fetchone()
    return {"id": row["id"], "name": row["name"], "disabled": bool(row["disabled"]), "createdAt": row["created_at"]}


# ---------------------------------------------------------------------------
# Notify services
# ---------------------------------------------------------------------------

@router.get("/admin/notify-services")
def admin_notify_services(refresh: bool = Query(default=False), admin: dict = Depends(require_admin)):
    """The notify services Home Assistant offers, as "notify.<name>", sorted:
    `services` are notify actions (GET /api/services, domain notify),
    `entities` notify entities (newer Companion-app style). Cached for 60 s;
    `refresh=1` skips the cache.

    If Home Assistant can't be asked, still 200 but `available: false` and a
    readable `error` — the page then falls back to typing a name (an HTTP
    error here would only be noise in the browser console on every visit).
    A sync route (runs in the threadpool); no DB connection is open while
    Home Assistant is asked."""
    try:
        return {"available": True, "error": None, **ha_notify.list_notify_services_blocking(force=refresh)}
    except ha_notify.NotifyListError as e:
        return {"available": False, "error": f"Couldn't read the notify services from Home Assistant: {e}",
                "services": [], "entities": []}


def _service(value) -> str:
    try:
        return ha_notify.normalize_service(value)
    except ValueError as e:
        raise HTTPException(422, str(e))


def _who(admin: dict) -> str:
    return admin.get("username") or admin["id"]


@router.put("/admin/users/{user_id}/notify")
def admin_set_notify(user_id: str, body: dict = Body(...), admin: dict = Depends(require_admin)):
    """Replace the person's services: {"services": ["notify.x", ...]}."""
    services = body.get("services")
    if not isinstance(services, list):
        raise HTTPException(422, "services must be a list like [\"notify.mobile_app_phone\"].")
    clean = sorted({_service(s) for s in services})
    if len(clean) > MAX_SERVICES_PER_USER:
        raise HTTPException(422, f"At most {MAX_SERVICES_PER_USER} notify services per person.")
    with db.get_conn() as conn:
        row = _load_user(conn, user_id)
        conn.execute("DELETE FROM user_notify WHERE user_id = ?", (user_id,))
        for s in clean:
            conn.execute("INSERT INTO user_notify (user_id, service, created_at, created_by) VALUES (?, ?, ?, ?)",
                         (user_id, s, config.now_iso(), _who(admin)))
        return _admin_user_json(conn, row)


@router.post("/admin/users/{user_id}/notify", status_code=201)
def admin_add_notify(user_id: str, body: dict = Body(...), admin: dict = Depends(require_admin)):
    """Add one service: {"service": "notify.x"}. Adding one the person
    already has is harmless (201, unchanged)."""
    service = _service(body.get("service"))
    with db.get_conn() as conn:
        row = _load_user(conn, user_id)
        current = ha_notify.assigned_services(conn, {"id": row["id"]})
        if service not in current and len(current) >= MAX_SERVICES_PER_USER:
            raise HTTPException(422, f"At most {MAX_SERVICES_PER_USER} notify services per person.")
        conn.execute("INSERT OR IGNORE INTO user_notify (user_id, service, created_at, created_by) VALUES (?, ?, ?, ?)",
                     (user_id, service, config.now_iso(), _who(admin)))
        return _admin_user_json(conn, row)


@router.delete("/admin/users/{user_id}/notify/{service}")
def admin_remove_notify(user_id: str, service: str, admin: dict = Depends(require_admin)):
    """Remove one service. Removing one the person doesn't have is a 404."""
    service = _service(service)
    with db.get_conn() as conn:
        row = _load_user(conn, user_id)
        n = conn.execute("DELETE FROM user_notify WHERE user_id = ? AND service = ?", (user_id, service)).rowcount
        if not n:
            raise HTTPException(404, f"{service} isn't assigned to {row['name']}.")
        return _admin_user_json(conn, row)


@router.post("/admin/users/{user_id}/notify/test")
def admin_test_notify(user_id: str, body: dict | None = Body(default=None), admin: dict = Depends(require_admin)):
    """Send a short test notification to the person through the normal notify
    path — every assigned service, or just `{"service": "notify.x"}` (which
    must be one of theirs). Doesn't need the person's own reminder switches;
    one test per person per 10 s. Returns {"results": {"notify.x": true}};
    502 (with Home Assistant's hint) if none was accepted."""
    only = (body or {}).get("service")
    with db.get_conn() as conn:
        row = _load_user(conn, user_id)
        services = [f"notify.{s}" for s in ha_notify.services_for({"id": row["id"]}, conn)]   # phones + extras
    # the DB connection is closed before Home Assistant is called
    if only is not None:
        only = _service(only)
        if only not in services:
            raise HTTPException(422, f"{only} isn't one of {row['name']}'s.")
        services = [only]
    if not services:
        raise HTTPException(400, f"{row['name']} has no phone linked in Home Assistant (Settings → People) "
                                 "and no extra notify service here.")
    now = time.monotonic()
    last = _last_test.get(user_id)
    if last is not None and now - last < TEST_INTERVAL_SECONDS:
        raise HTTPException(429, "Please wait a few seconds before sending another test.")
    _last_test[user_id] = now
    sent = ha_notify.send_to_services(
        [ha_notify.bare(s) for s in services], "Household Todo",
        f"Test notification from Household Todo for {row['name']}, sent by an admin.")
    results = {f"notify.{k}": v for k, v in sent.items()}
    if not any(results.values()):
        if not ha_notify.ha_client.has_token():
            raise HTTPException(502, "This app can't reach Home Assistant (no Supervisor token).")
        hint = ha_notify.explain_failure(ha_notify.bare(services[0]))
        raise HTTPException(502, "Home Assistant didn't accept the test notification"
                                 + (f" — {hint}" if hint else " — check the service name."))
    return {"results": results}
