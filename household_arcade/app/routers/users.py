"""Admin → Users (SPEC §6): everyone who has opened the app, switch people
on/off, mark children and set their limits, extra time today, play history,
and extra notify services. All admin-only.

Admins can't be marked as children: the mark is refused for anyone on the
admin_users list, and limits never apply to an admin even if they were marked
before being listed.
"""
import time
from datetime import timedelta

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Query

from .. import auth, config, db, games, ha_sensors, limits
from ..common import ha_notify, ha_people, people_admin
from ..auth import require_admin

router = APIRouter(prefix="/api/admin", tags=["users"])

MAX_SERVICES_PER_USER = 10
TEST_INTERVAL_SECONDS = 10
HISTORY_DAYS = 14
_LIMITER = people_admin.TestLimiter(TEST_INTERVAL_SECONDS)
_last_test: dict[str, float] = _LIMITER.last


def _who(admin: dict) -> str:
    return admin.get("username") or admin["id"]


def _load_user(conn, user_id: str):
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row:
        raise HTTPException(404, "That person isn't known to this app.")
    return row


def _user_json(conn, row) -> dict:
    admin = auth.is_admin_identity(row["id"], row["username"])
    child = bool(row["is_child"]) and not admin
    return {
        "id": row["id"], "name": row["name"], "username": row["username"],
        "disabled": bool(row["disabled"]), "isAdmin": admin, "isChild": child,
        "createdAt": row["created_at"], "lastSeen": row["last_seen"],
        "limits": limits.load_limits(conn, row["id"]) if child else None,
        "playTime": {k: v for k, v in limits.status(conn, {"id": row["id"], "is_child": child}).items()
                     if k != "limits"},
        "notify": ha_notify.assigned_services(conn, {"id": row["id"]}),
        "ha": people_admin.ha_person_json(row["id"]),            # phones from Home Assistant
    }


@router.get("/users")
def list_users(refresh: bool = Query(default=False), admin: dict = Depends(require_admin)):
    people_admin.refresh_people(refresh)
    with db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM users ORDER BY name COLLATE NOCASE").fetchall()
        return {"users": [_user_json(conn, r) for r in rows],
                "games": [{"id": g, "name": games.name(g)} for g in games.GAME_IDS]}


@router.get("/users/{user_id}")
def get_user(user_id: str, admin: dict = Depends(require_admin)):
    with db.get_conn() as conn:
        return _user_json(conn, _load_user(conn, user_id))


@router.patch("/users/{user_id}")
def update_user(user_id: str, background: BackgroundTasks, body: dict = Body(...),
                admin: dict = Depends(require_admin)):
    """{disabled?: bool, isChild?: bool}."""
    if not isinstance(body, dict) or not body or set(body) - {"disabled", "isChild"}:
        raise HTTPException(422, "Send disabled and/or isChild.")
    for k, v in body.items():
        if not isinstance(v, bool):
            raise HTTPException(422, f"{k} must be true or false.")
    with db.get_conn() as conn:
        row = _load_user(conn, user_id)
        if body.get("isChild") and auth.is_admin_identity(row["id"], row["username"]):
            raise HTTPException(409, f"{row['name']} is an admin, and admins can't be marked as children.")
        if "disabled" in body:
            if body["disabled"] and row["id"] == admin["id"]:
                raise HTTPException(409, "You can't switch yourself off.")
            conn.execute("UPDATE users SET disabled = ? WHERE id = ?", (1 if body["disabled"] else 0, user_id))
        if "isChild" in body:
            conn.execute("UPDATE users SET is_child = ? WHERE id = ?", (1 if body["isChild"] else 0, user_id))
            if body["isChild"] and not conn.execute("SELECT 1 FROM child_limits WHERE user_id = ?",
                                                    (user_id,)).fetchone():
                limits.save_limits(conn, user_id, dict(limits.EMPTY_LIMITS), _who(admin))
        out = _user_json(conn, _load_user(conn, user_id))
    background.add_task(ha_sensors.changed_blocking)
    return out


@router.get("/users/{user_id}/limits")
def get_limits(user_id: str, admin: dict = Depends(require_admin)):
    with db.get_conn() as conn:
        _load_user(conn, user_id)
        return limits.load_limits(conn, user_id)


@router.put("/users/{user_id}/limits")
def put_limits(user_id: str, background: BackgroundTasks, body: dict = Body(...),
               admin: dict = Depends(require_admin)):
    try:
        clean = limits.clean_limits(body)
    except ValueError as e:
        raise HTTPException(422, str(e))
    with db.get_conn() as conn:
        row = _load_user(conn, user_id)
        if not row["is_child"]:
            raise HTTPException(409, f"Mark {row['name']} as a child first.")
        limits.save_limits(conn, user_id, clean, _who(admin))
        out = _user_json(conn, row)
    background.add_task(ha_sensors.changed_blocking)
    return out


@router.post("/users/{user_id}/extra-time")
def extra_time(user_id: str, background: BackgroundTasks, body: dict = Body(...),
               admin: dict = Depends(require_admin)):
    """{minutes: 15 | 30 | 60} — for today only."""
    minutes = body.get("minutes") if isinstance(body, dict) else None
    if minutes not in limits.EXTRA_CHOICES or isinstance(minutes, bool):
        raise HTTPException(422, "Extra time is 15, 30 or 60 minutes.")
    with db.get_conn() as conn:
        row = _load_user(conn, user_id)
        if not row["is_child"]:
            raise HTTPException(409, f"{row['name']} isn't marked as a child.")
        conn.execute("INSERT INTO extra_time (user_id, date, minutes, given_by, created_at) VALUES (?, ?, ?, ?, ?)",
                     (user_id, config.today().isoformat(), minutes, _who(admin), config.now_iso()))
        out = _user_json(conn, row)
    background.add_task(ha_sensors.changed_blocking)
    return out


@router.get("/users/{user_id}/history")
def history(user_id: str, days: int = Query(default=HISTORY_DAYS, ge=1, le=90),
            admin: dict = Depends(require_admin)):
    """What they played, when and for how long (practice included), newest
    first, plus minutes played per day."""
    since = config.local_day_bounds_utc(config.today() - timedelta(days=days - 1))[0]
    with db.get_conn() as conn:
        row = _load_user(conn, user_id)
        sessions = conn.execute(
            "SELECT p.*, s.score FROM play_sessions p LEFT JOIN scores s ON s.session_id = p.id "
            "WHERE p.user_id = ? AND p.started_at >= ? ORDER BY p.started_at DESC LIMIT 300",
            (user_id, since)).fetchall()
        extras = conn.execute("SELECT date, minutes, given_by FROM extra_time WHERE user_id = ? AND date >= ? "
                              "ORDER BY created_at DESC", (user_id, (config.today() - timedelta(days=days - 1))
                                                           .isoformat())).fetchall()
    per_day: dict[str, float] = {}
    items = []
    for s in sessions:
        d = config.to_local_date(s["started_at"]).isoformat()
        per_day[d] = per_day.get(d, 0) + s["active_seconds"]
        items.append({"id": s["id"], "game": s["game"], "gameName": games.name(s["game"]),
                      "mode": s["mode"], "modeLabel": games.mode_label(s["game"], s["mode"]),
                      "practice": bool(s["practice"]), "startedAt": s["started_at"], "endedAt": s["ended_at"],
                      "seconds": round(s["active_seconds"]), "score": s["score"]})
    return {"name": row["name"], "sessions": items,
            "days": [{"date": d, "minutes": round(v / 60)} for d, v in sorted(per_day.items(), reverse=True)],
            "extraTime": [dict(e) for e in extras]}


# ---------------------------------------------------------------------------
# Notify services (as in the other household apps)
# ---------------------------------------------------------------------------

@router.get("/notify-services")
def admin_notify_services(refresh: bool = Query(default=False), admin: dict = Depends(require_admin)):
    return people_admin.notify_services(refresh)


_service = people_admin.clean_service


@router.post("/users/{user_id}/notify", status_code=201)
def admin_add_notify(user_id: str, body: dict = Body(...), admin: dict = Depends(require_admin)):
    service = _service(body.get("service") if isinstance(body, dict) else None)
    with db.get_conn() as conn:
        row = _load_user(conn, user_id)
        people_admin.add_service(conn, user_id, service, _who(admin), config.now_iso(), limit=MAX_SERVICES_PER_USER)
        return _user_json(conn, row)


@router.delete("/users/{user_id}/notify/{service}")
def admin_remove_notify(user_id: str, service: str, admin: dict = Depends(require_admin)):
    service = _service(service)
    with db.get_conn() as conn:
        row = _load_user(conn, user_id)
        people_admin.remove_service(conn, user_id, service, f"{service} isn't assigned to {row['name']}.")
        return _user_json(conn, row)


@router.post("/users/{user_id}/notify/test")
def admin_test_notify(user_id: str, admin: dict = Depends(require_admin)):
    """A short test notification to every phone and service the person has."""
    with db.get_conn() as conn:
        row = _load_user(conn, user_id)
        services = ha_notify.services_for({"id": row["id"]}, conn)
    if not services:
        raise HTTPException(400, f"{row['name']} has no phone linked in Home Assistant (Settings → People) "
                                 "and no extra notify service here.")
    _LIMITER.check(user_id)
    sent = ha_notify.send_to_services(services, "Household Arcade",
                                      f"Test notification from Household Arcade for {row['name']}, sent by an admin.")
    results = people_admin.test_results(sent)
    people_admin.require_one_sent(results, services[0], ha_notify.ha_client.has_token)
    return {"results": results}


# ---------------------------------------------------------------------------
# Level progress (SPEC §14): what a person has cleared, and Start over for them
# ---------------------------------------------------------------------------

@router.get("/users/{user_id}/progress")
def user_progress(user_id: str, admin: dict = Depends(require_admin)):
    from .. import progress
    with db.get_conn() as conn:
        _load_user(conn, user_id)
        return {"progress": progress.mine(conn, user_id)}


@router.delete("/users/{user_id}/progress/{game}/{mode}")
def reset_user_progress(user_id: str, game: str, mode: str, admin: dict = Depends(require_admin)):
    from .. import progress
    if not games.continues(game, mode):
        raise HTTPException(404, "That game and mode don't carry on from the next level.")
    with db.get_conn() as conn:
        _load_user(conn, user_id)
        progress.reset(conn, user_id, game, mode)
    return {"status": "ok"}
