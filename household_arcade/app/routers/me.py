"""Who you are: `GET /api/me` (what the app needs to start) and
`GET /api/whoami` (the "How the app sees you" card).

The whoami data echoes only the two identity headers' values and COUNTS
(admin_users entries), never the lists themselves, so a non-admin can't learn
who the admins are.
"""
from fastapi import APIRouter, Depends, Request

from .. import config, db, games, ha_notify, limits, settings
from ..auth import get_current_user, is_admin_identity

router = APIRouter(prefix="/api", tags=["me"])


def prefs_json(row, default_look: str) -> dict:
    look = row["look"] if row["look"] in games.LOOKS else None
    return {
        "look": look,                              # None = the household default
        "effectiveLook": look or default_look,
        "sound": bool(row["sound"]),
        "handedness": row["handedness"],
        "reduceMotion": bool(row["reduce_motion"]),
        "receiveNotifications": bool(row["receive_notifications"]),
    }


def low_time_children(conn) -> list[dict]:
    """For admins: children with 5 minutes or less left today (and some time
    used), so "Add 15 minutes" is one tap from Home."""
    out = []
    for r in conn.execute("SELECT id, name, username FROM users WHERE is_child = 1 AND disabled = 0 "
                          "ORDER BY name COLLATE NOCASE"):
        if is_admin_identity(r["id"], r["username"]):
            continue
        st = limits.status(conn, {"id": r["id"], "is_child": True})
        if st["leftSeconds"] is not None and st["leftSeconds"] <= limits.WARN_SECONDS and st["usedSeconds"] > 0:
            out.append({"id": r["id"], "name": r["name"], "leftSeconds": st["leftSeconds"]})
    return out


@router.get("/me")
def me(current: dict = Depends(get_current_user)):
    s = settings.all()
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (current["id"],)).fetchone()
        status = limits.status(conn, current)
        low = low_time_children(conn) if current["is_admin"] else []
    lb = s["leaderboard"]
    lb_names = "full"
    if current["is_child"] and status["limits"]:
        lb_names = status["limits"]["leaderboard"]
    return {
        "id": current["id"],
        "name": current["name"],
        "username": current["username"],
        "isAdmin": current["is_admin"],
        "isChild": current["is_child"],
        "disabled": current["disabled"],
        "noAdmins": len(config.ADMIN_NAMES) == 0,
        "nameSent": bool(current["username"]),
        "prefs": prefs_json(row, s["default_look"]),
        "looks": [{"id": k, "label": v} for k, v in games.LOOKS.items()],
        "defaultLook": s["default_look"],
        "leaderboard": lb and lb_names != "hidden",
        "leaderboardNames": lb_names,
        "playTime": {k: v for k, v in status.items() if k != "limits"},
        "limits": status["limits"],
        "today": config.today().isoformat(),
        "timeZone": config.timezone_name(),
        "version": config.APP_VERSION,
        "lowTimeChildren": low,
    }


@router.get("/whoami")
def whoami(request: Request, current: dict = Depends(get_current_user)):
    return {
        "haUserId": current["id"],
        "haUsername": current["username"],
        "haDisplayName": current["name"],
        "isAdmin": current["is_admin"],
        "nameSent": bool(current["username"]),
        "viaIngress": "x-ingress-path" in request.headers,
        "adminEntries": len(config.ADMIN_NAMES),
        "notifyLinked": bool(ha_notify.services_for(current)),
        "noAdmins": len(config.ADMIN_NAMES) == 0,
    }
