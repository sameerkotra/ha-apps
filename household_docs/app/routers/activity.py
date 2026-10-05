"""Staying up to date (SPEC §17.4, §17.8, §17.9): 🕑 Activity, follows, Home Assistant sensors and the storage
report."""
from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Query

from .. import activity, db, follows, ha_sensors, storage_report
from ..auth import require_admin, require_user
from ..store import fileio

router = APIRouter(prefix="/api", tags=["activity"])


@router.get("/activity")
def get_activity(person: str | None = Query(default=None, max_length=128), place: str | None = Query(default=None, max_length=70),
                 type: str | None = Query(default=None, max_length=20), before: str | None = Query(default=None, max_length=40),
                 limit: int = Query(default=activity.PAGE, ge=1, le=200), user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        return activity.feed(conn, user, person=person or None, place=place or None, type_=type or None,
                             before=before or None, limit=limit)


@router.get("/follows")
def get_follows(user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        return {"follows": follows.listing(conn, user)}


@router.post("/follows", status_code=201)
def add_follow(body: dict = Body(...), user: dict = Depends(require_user)):
    target = body.get("target") if isinstance(body, dict) else None
    with db.get_conn() as conn:
        return follows.follow(conn, user, target)


@router.delete("/follows/{target}")
def remove_follow(target: str, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        return follows.unfollow(conn, user, target)


# ---------------------------------------------------------------- Home Assistant sensors (§17.9)
@router.get("/nodes/{ref}/ha-sensor")
def get_sensor(ref: str, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        return ha_sensors.info(conn, user, ref[:70])


@router.put("/nodes/{ref}/ha-sensor")
def put_sensor(ref: str, background: BackgroundTasks, body: dict = Body(...), user: dict = Depends(require_user)):
    if not isinstance(body, dict):
        raise HTTPException(422, "Send {name, cell, unit, hideItems}.")
    with db.get_conn() as conn:
        out = ha_sensors.turn_on(conn, user, ref[:70], body)
    background.add_task(ha_sensors.tick)
    return out


@router.delete("/nodes/{ref}/ha-sensor")
def delete_sensor(ref: str, background: BackgroundTasks, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        out = ha_sensors.turn_off(conn, user, ref[:70])
    background.add_task(ha_sensors.tick)
    return out


# ---------------------------------------------------------------- the storage report (§17.8)
@router.get("/reports/storage")
def storage(place: str | None = Query(default=None, max_length=70), years: int = Query(default=2, ge=1, le=20),
            user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        return storage_report.person_report(conn, user, place, years)


@router.post("/reports/duplicates/keep")
def keep_one(body: dict = Body(...), user: dict = Depends(require_user)):
    keep = body.get("keepId") if isinstance(body, dict) else None
    if not isinstance(keep, str) or not keep:
        raise HTTPException(422, "Say which copy to keep (keepId).")
    fileio.ensure_writable()
    with db.get_conn() as conn:
        return storage_report.keep_one(conn, user, keep[:64])


@router.get("/admin/storage")
def admin_storage(admin: dict = Depends(require_admin)):
    with db.get_conn() as conn:
        return storage_report.admin_report(conn)


@router.post("/admin/storage/empty-trash")
def admin_empty_trash(admin: dict = Depends(require_admin)):
    with db.get_conn() as conn:
        n = storage_report.empty_all_trash(conn)
        db.audit(conn, "trash_emptied_all", admin["id"])
    return {"ok": True, "removed": n}
