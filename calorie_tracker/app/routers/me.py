from fastapi import APIRouter, BackgroundTasks, Depends, Request
from pydantic import BaseModel, ConfigDict, StrictBool

from .. import config, db, ha_sync, settings
from ..auth import display_name_listed, get_current_user, no_admins
from ..common import whoami as whoami_core

router = APIRouter(prefix="/api/me", tags=["me"])
whoami_router = APIRouter(prefix="/api", tags=["me"])


@router.get("")
def me(user: dict = Depends(get_current_user)):
    """Who is signed in. `noAdmin` is true while the admin_users option is
    empty (the frontend then shows the "No admin yet" banner to everyone,
    naming `username` — the login name to add, or the user id if HA didn't
    send one)."""
    return {"id": user["id"], "name": user["name"], "is_admin": user["is_admin"],
            "username": user["username"] or user["id"], "noAdmin": no_admins(),
            "page": config.INGRESS_PANEL,       # the sidebar page: links open a tab or a day there (deeplink.js)
            **_assistant(user["id"])}


def _assistant(uid: str) -> dict:
    """Whether the admin lets the Household Assistant ask, and this person's own switch (tools.py); the same for
    the daily-calories sensor: the household switch and "Publish my calories to Home Assistant"."""
    with db.get_conn() as conn:
        row = conn.execute("SELECT assistant_ok, publish_sensor FROM users WHERE id = ?", (uid,)).fetchone()
    return {"assistant": bool(settings.get("assistant_answers")), "assistantOk": bool(row["assistant_ok"]) if row else True,
            "sensor": bool(settings.get("expose_daily_calories_sensor")),
            "publishSensor": bool(row["publish_sensor"]) if row else False}


class AssistantIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assistantOk: StrictBool


@router.put("/assistant")
def set_assistant(body: AssistantIn, user: dict = Depends(get_current_user)):
    """The person's own "Let the Household Assistant answer for me" — always the signed-in person, never "acting as"."""
    with db.get_conn() as conn:
        conn.execute("UPDATE users SET assistant_ok = ? WHERE id = ?", (1 if body.assistantOk else 0, user["id"]))
    return _assistant(user["id"])


class SensorIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    publishSensor: StrictBool


@router.put("/sensor")
def set_sensor(body: SensorIn, background: BackgroundTasks, user: dict = Depends(get_current_user)):
    """The person's own "Publish my calories to Home Assistant" — always the signed-in person, never "acting as".
    On: their sensor is published now (while the household switch is on); off: it is removed from Home Assistant."""
    with db.get_conn() as conn:
        conn.execute("UPDATE users SET publish_sensor = ? WHERE id = ?", (1 if body.publishSensor else 0, user["id"]))
    background.add_task(ha_sync.push_for_user_blocking if body.publishSensor else ha_sync.remove_for_user_blocking,
                        user["id"])
    return _assistant(user["id"])


@whoami_router.get("/today")
def today():
    """The app's own idea of "today", in Home Assistant's time zone (falls
    back to UTC if HA's zone couldn't be read — see config.py).
    The frontend uses this instead of the browser's own clock so the date
    bar, history buckets and food-log entries always land on the same
    calendar day the household is actually in, regardless of what time zone
    whatever device happens to be viewing this in is set to."""
    return {"today": str(config.today()), "timezone": config.timezone_name()}


@whoami_router.get("/whoami")
def whoami(request: Request, user: dict = Depends(get_current_user)):
    """The "How the app sees you" page: exactly what Home Assistant sent and
    whether it matched the admin_users list. Always the REAL signed-in
    person (get_current_user, never the "acting as" user). Shows only the
    person's own identity values and a COUNT of list entries, never the list,
    and never the request's headers wholesale."""
    return whoami_core.build(
        request, user_id=user["id"], username=user["username"], display_name=user["name"],
        is_admin=user["is_admin"], admin_entries=len(config.ADMIN_USERS),
        display_name_only=(not user["is_admin"]) and display_name_listed(user["name"]))
