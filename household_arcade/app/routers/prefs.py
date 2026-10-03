"""Each person's own Settings (SPEC §8): look, sound, left/right-handed
controls, reduce motion, receive notifications. `GET/PUT /api/prefs`."""
from fastapi import APIRouter, Body, Depends, HTTPException

from .. import db, games, settings
from ..auth import get_current_user
from .me import prefs_json

router = APIRouter(prefix="/api", tags=["prefs"])

_COLUMNS = {"look": "look", "sound": "sound", "handedness": "handedness", "reduceMotion": "reduce_motion",
            "receiveNotifications": "receive_notifications"}


@router.get("/prefs")
def get_prefs(current: dict = Depends(get_current_user)):
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (current["id"],)).fetchone()
    return prefs_json(row, settings.get("default_look"))


@router.put("/prefs")
def put_prefs(body: dict = Body(...), current: dict = Depends(get_current_user)):
    """Any subset of the keys; unknown keys or bad values are a 422."""
    if not isinstance(body, dict):
        raise HTTPException(422, "Send an object.")
    unknown = sorted(set(body) - set(_COLUMNS))
    if unknown:
        raise HTTPException(422, "Unknown setting(s): " + ", ".join(unknown))
    values = {}
    for k, v in body.items():
        if k == "look":
            if v is not None and v not in games.LOOKS:
                raise HTTPException(422, "Unknown look.")
            values["look"] = v
        elif k == "handedness":
            if v not in ("right", "left"):
                raise HTTPException(422, "handedness must be right or left.")
            values["handedness"] = v
        else:
            if not isinstance(v, bool):
                raise HTTPException(422, f"{k} must be true or false.")
            values[_COLUMNS[k]] = 1 if v else 0
    with db.get_conn() as conn:
        for col, v in values.items():
            conn.execute(f"UPDATE users SET {col} = ? WHERE id = ?", (v, current["id"]))
        row = conn.execute("SELECT * FROM users WHERE id = ?", (current["id"],)).fetchone()
    return prefs_json(row, settings.get("default_look"))
