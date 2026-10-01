"""GET /api/whoami — always the *real* caller (SPEC §6, §8e).

Also the data behind the "How the app sees you" card: exactly which name and
id Home Assistant sent, whether they matched `admin_users`, whether an admin
assigned them a notify service (Admin → Users), and only a COUNT of
`admin_users` entries and of people with a notify service — never the lists
themselves, so a non-admin can't learn who the admins are. Echoes nothing but the identity
headers' values (never the request's headers wholesale).
"""
from fastapi import APIRouter, Depends, Request

from .. import config, db, ha_notify, settings
from ..auth import get_acting_user

router = APIRouter(prefix="/api", tags=["me"])


@router.get("/whoami")
def whoami(request: Request, acting: dict = Depends(get_acting_user)):
    real = acting["real"]
    return {
        "haUserId": real["id"],
        "haUsername": real["username"],
        "haDisplayName": real["name"],
        "isAdmin": real["is_admin"],
        "actingAs": {"id": acting["id"], "name": acting["name"]} if acting["acting"] else None,
        # diagnostics
        "nameSent": bool(real["username"]),
        "viaIngress": "x-ingress-path" in request.headers,
        "adminEntries": len(config.ADMIN_NAMES),
        "notifyEntries": _notify_people(),
        "notifyLinked": bool(ha_notify.services_for(real)),
        # nobody is listed in admin_users yet: every page shows "No admin yet" (nobody is auto-promoted)
        "noAdmins": len(config.ADMIN_NAMES) == 0,
        "maintenance": {"enabled": bool(settings.get("maintenance_enabled"))},
        "driveTimes": {"enabled": settings.drive_times_enabled()},
    }


def _notify_people() -> int:
    """How many people have at least one notify service assigned (count only)."""
    with db.get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(DISTINCT user_id) FROM user_notify"
        ).fetchone()[0]
