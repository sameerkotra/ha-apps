"""Each person's own Settings (SPEC §8): look, sound, left/right-handed
controls, reduce motion, receive notifications. `GET/PUT /api/prefs`."""
from fastapi import APIRouter, Body, Depends, HTTPException

from .. import db, games, settings
from ..auth import get_current_user
from .me import game_prefs, prefs_json

router = APIRouter(prefix="/api", tags=["prefs"])

_COLUMNS = {"look": "look", "sound": "sound", "handedness": "handedness", "reduceMotion": "reduce_motion",
            "receiveNotifications": "receive_notifications"}

import json
import re

_OPTION_RE = re.compile(r"^[a-z][a-z0-9_]{0,19}$")
_CHOICE_RE = re.compile(r"^[A-Za-z0-9_ .-]{1,20}$")
MAX_GAME_PREFS_BYTES = 2000


def _merge_game_prefs(old: dict, change) -> dict:
    """`change` is {game: {option: choice}}; it's merged into the person's saved choices. 422 for an unknown game
    or a malformed option or choice (the games' own option lists are checked in the browser)."""
    if not isinstance(change, dict):
        raise HTTPException(422, "gamePrefs must be an object of games.")
    out = {g: dict(v) for g, v in old.items() if isinstance(v, dict)}
    for game, opts in change.items():
        if game not in games.GAMES:
            raise HTTPException(422, "Unknown game.")
        if not isinstance(opts, dict):
            raise HTTPException(422, "A game's choices must be an object.")
        for k, v in opts.items():
            if not isinstance(k, str) or not _OPTION_RE.match(k) or not isinstance(v, str) or not _CHOICE_RE.match(v):
                raise HTTPException(422, "That choice isn't valid.")
            out.setdefault(game, {})[k] = v
    if len(json.dumps(out)) > MAX_GAME_PREFS_BYTES:
        raise HTTPException(422, "Too many game choices are saved.")
    return out


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
    unknown = sorted(set(body) - set(_COLUMNS) - {"gamePrefs"})
    if unknown:
        raise HTTPException(422, "Unknown setting(s): " + ", ".join(unknown))
    values = {}
    for k, v in body.items():
        if k == "gamePrefs":
            continue
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
        if "gamePrefs" in body:
            row = conn.execute("SELECT game_prefs FROM users WHERE id = ?", (current["id"],)).fetchone()
            values["game_prefs"] = json.dumps(_merge_game_prefs(game_prefs(row), body["gamePrefs"]))
        for col, v in values.items():
            conn.execute(f"UPDATE users SET {col} = ? WHERE id = ?", (v, current["id"]))
        row = conn.execute("SELECT * FROM users WHERE id = ?", (current["id"],)).fetchone()
    return prefs_json(row, settings.get("default_look"))
