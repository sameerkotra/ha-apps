"""Places — a shared address book that tasks refer to (SPEC §8h).

Every place is shared with the whole household: anyone can add, edit or
delete one, and any task in any list can refer to it.
"""
import sqlite3

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response

from .. import config, db, drive_time, geocode, settings, taskview
from ..auth import get_acting_user

router = APIRouter(prefix="/api", tags=["places"])


def _name(value) -> str:
    if not isinstance(value, str) or not (1 <= len(value.strip()) <= 60):
        raise HTTPException(422, "A place name must be 1–60 characters.")
    return value.strip()


def _address(value) -> str:
    if not isinstance(value, str) or not (1 <= len(value.strip()) <= 300):
        raise HTTPException(422, "An address must be 1–300 characters.")
    return value.strip()


def _phone(value) -> str | None:
    """Optional; no format enforced (households enter numbers in every
    country's own convention) — just a sane length cap. None/omitted/blank
    all mean "no phone number"."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise HTTPException(422, "A phone number must be text.")
    value = value.strip()
    if not value:
        return None
    if len(value) > 30:
        raise HTTPException(422, "A phone number must be 30 characters or fewer.")
    return value


def _check_address_free(conn, address: str, exclude_id: str | None = None) -> None:
    """Raises 409 if `address` normalizes (§8h: whitespace-collapsed,
    case-folded — see db.normalize_address) to the same thing as an
    existing place's address. Backstopped by idx_places_address_norm at the
    DB level (db._migrate) so a race between two simultaneous creates still
    can't slip a true duplicate in; this is just the friendlier, named
    error for the common case of hitting Save twice or not noticing an
    existing entry."""
    sql = "SELECT id, name FROM places WHERE normalize_addr(address) = normalize_addr(?)"
    params: tuple = (address,)
    if exclude_id:
        sql += " AND id != ?"
        params += (exclude_id,)
    row = conn.execute(sql, params).fetchone()
    if row:
        raise HTTPException(
            409,
            f"That address is already saved as “{row['name']}”. Use that place instead, "
            "or edit it if the address needs to change.",
        )


def _usage(conn, place_id: str, acting_id: str) -> int:
    return conn.execute(
        f"SELECT COUNT(*) FROM tasks t JOIN lists l ON l.id = t.list_id WHERE t.place_id = ? AND {taskview.VISIBLE_SQL}",
        (place_id, acting_id),
    ).fetchone()[0]


def _json(row, usage: int) -> dict:
    # estimated drive time from home, in minutes (spec §7.3); null until computed,
    # if the feature isn't configured or failed, or while the "Drive times" switch is off
    minutes = row["drive_minutes"] if settings.drive_times_enabled() else None
    return {
        "id": row["id"], "name": row["name"], "address": row["address"], "phone": row["phone"],
        "driveMinutes": minutes,
        # avoid_tolls: was the estimate computed while avoiding tolls, and did
        # that actually find a toll-free route? (null when there's no estimate)
        "driveAvoidTolls": None if minutes is None else row["drive_mode"] == "no_tolls",
        "driveTollsAvoided": None if minutes is None else bool(row["drive_tolls_avoided"]),
        "usageCount": usage,
    }


def _load(conn, place_id: str):
    row = conn.execute("SELECT * FROM places WHERE id = ?", (place_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Place not found.")
    return row


@router.get("/places")
def get_places(q: str | None = Query(default=None), acting: dict = Depends(get_acting_user)):
    with db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM places ORDER BY name COLLATE NOCASE").fetchall()
        needle = (q or "").strip().lower()
        if needle:
            rows = [r for r in rows if needle in r["name"].lower() or needle in r["address"].lower()]
        return [_json(r, _usage(conn, r["id"], acting["id"])) for r in rows]


@router.post("/places", status_code=201)
def create_place(body: dict = Body(...), acting: dict = Depends(get_acting_user)):
    name, address, phone = _name(body.get("name")), _address(body.get("address")), _phone(body.get("phone"))
    with db.get_conn() as conn:
        _check_address_free(conn, address)
        place_id = db.new_id()
        try:
            conn.execute(
                "INSERT INTO places (id, name, address, phone, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (place_id, name, address, phone, acting["id"], config.now_iso()),
            )
        except sqlite3.IntegrityError:
            # Lost a race with a simultaneous create of the same address —
            # the pre-check above already covers the ordinary case.
            _check_address_free(conn, address)
            raise
        return _json(_load(conn, place_id), 0)


@router.patch("/places/{place_id}")
def update_place(place_id: str, body: dict = Body(...), acting: dict = Depends(get_acting_user)):
    with db.get_conn() as conn:
        _load(conn, place_id)
        updates = {}
        if "name" in body:
            updates["name"] = _name(body["name"])
        if "address" in body:
            updates["address"] = _address(body["address"])
            _check_address_free(conn, updates["address"], exclude_id=place_id)
            # A changed address invalidates any cached coordinates/drive time
            # (§8n) — NULL puts it back in line for drive_time.py's next pass.
            updates["lat"] = None
            updates["lon"] = None
            updates["drive_minutes"] = None
            updates["drive_checked_at"] = None
            updates["drive_mode"] = None
            updates["drive_tolls_avoided"] = None
        if "phone" in body:
            updates["phone"] = _phone(body["phone"])
        if updates:
            try:
                conn.execute(f"UPDATE places SET {', '.join(k + ' = ?' for k in updates)} WHERE id = ?", [*updates.values(), place_id])
            except sqlite3.IntegrityError:
                _check_address_free(conn, updates["address"], exclude_id=place_id)
                raise
        return _json(_load(conn, place_id), _usage(conn, place_id, acting["id"]))


@router.delete("/places/{place_id}", status_code=204)
def delete_place(place_id: str, acting: dict = Depends(get_acting_user)):
    with db.get_conn() as conn:
        _load(conn, place_id)
        conn.execute("DELETE FROM places WHERE id = ?", (place_id,))   # tasks lose the reference (SET NULL)
    return Response(status_code=204)


@router.post("/places/{place_id}/recalculate-drive-time")
def recalculate_drive_time(place_id: str, acting: dict = Depends(get_acting_user)):
    """On-demand version of drive_time.py's background pass, for one place,
    right away — the "🔄 Recalculate" button on the Places tab (§8n).
    Bypasses the background pass's staleness/retry cooldowns entirely, since
    someone pressing this button has already decided it's worth trying now
    (e.g. right after fixing a typo'd address, or just to check)."""
    with db.get_conn() as conn:
        address = _load(conn, place_id)["address"]   # 404 for an unknown place, before anything else
    if not settings.drive_times_enabled():            # nothing is looked up while the switch is off
        raise HTTPException(
            409, "Drive times are turned off — an admin can turn them on under Admin → App settings.")
    if settings.home_address() and not geocode.feature_enabled():
        geocode.retry_home_soon()
        geocode.ensure_home_blocking()   # e.g. home_address was just changed (no DB connection open)
    if not geocode.feature_enabled():
        raise HTTPException(
            400,
            "Estimated drive time isn't set up yet — an admin can set the home address under "
            "Admin → App settings (the routing and address-lookup servers already default to the "
            "public OpenStreetMap services)."
            + (" The current home address couldn't be found on the map — check it." if settings.home_address() else ""),
        )
    # The lookups happen with no database connection open — they can take
    # several seconds (throttled geocoding, plus a retry without a unit number).
    lat, lon, minutes, tolls_avoided = drive_time.compute_for_address(address)
    with db.get_conn() as conn:
        _load(conn, place_id)   # it may have been deleted while we were waiting
        conn.execute(
            "UPDATE places SET lat = ?, lon = ?, drive_minutes = ?, drive_checked_at = ?, "
            "drive_mode = ?, drive_tolls_avoided = ? WHERE id = ?",
            (lat, lon, minutes, config.now_iso(), settings.drive_mode(), tolls_avoided, place_id),
        )
        conn.commit()   # keep this attempt even if we raise below
        result = _json(_load(conn, place_id), _usage(conn, place_id, acting["id"]))
    if minutes is None:
        raise HTTPException(
            502,
            "Could not calculate a drive time for this address — double-check it's correct "
            "and complete, or try again in a moment.",
        )
    return result
