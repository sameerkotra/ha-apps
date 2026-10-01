from fastapi import APIRouter, Depends, Request

from .. import config
from ..auth import display_name_listed, get_current_user, no_admins

router = APIRouter(prefix="/api/me", tags=["me"])
whoami_router = APIRouter(prefix="/api", tags=["me"])


@router.get("")
async def me(user: dict = Depends(get_current_user)):
    """Who is signed in. `no_admins` is true while the admin_users option is
    empty (the frontend then shows the "No admin yet" banner to everyone,
    naming `username` — the login name to add, or the user id if HA didn't
    send one)."""
    return {"id": user["id"], "name": user["name"], "is_admin": user["is_admin"],
            "username": user["username"] or user["id"], "no_admins": no_admins()}


@whoami_router.get("/today")
async def today():
    """The app's own idea of "today", in Home Assistant's time zone (falls
    back to UTC if HA's zone couldn't be read — see config.py).
    The frontend uses this instead of the browser's own clock so the date
    bar, history buckets and food-log entries always land on the same
    calendar day the household is actually in, regardless of what time zone
    whatever device happens to be viewing this in is set to."""
    return {"today": str(config.today()), "timezone": config.timezone_name()}


@whoami_router.get("/whoami")
async def whoami(request: Request, user: dict = Depends(get_current_user)):
    """The "How the app sees you" page: exactly what Home Assistant sent and
    whether it matched the admin_users list. Always the REAL signed-in
    person (get_current_user, never the "acting as" user). Shows only the
    person's own identity values and a COUNT of list entries, never the list,
    and never the request's headers wholesale."""
    return {
        "haUserId": user["id"],
        "haUsername": user["username"],
        "haDisplayName": user["name"],
        "nameSent": bool(user["username"]),
        "viaIngress": "x-ingress-path" in request.headers,
        "isAdmin": user["is_admin"],
        "displayNameOnly": (not user["is_admin"]) and display_name_listed(user["name"]),
        "adminEntries": len(config.ADMIN_USERS),
        "noAdmins": no_admins(),
    }
