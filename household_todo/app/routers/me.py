"""GET /api/whoami — always the *real* caller (SPEC §6, §8e).

Also the data behind the "How the app sees you" card: exactly which name and
id Home Assistant sent, whether they matched `admin_users`, whether an admin
assigned them a notify service (Admin → Users), and only a COUNT of
`admin_users` entries and of people with a notify service — never the lists
themselves, so a non-admin can't learn who the admins are. Echoes nothing but the identity
headers' values (never the request's headers wholesale).
"""
from fastapi import APIRouter, Depends, Request

from .. import config, db, settings
from ..common import ha_notify, whoami as whoami_core
from ..auth import get_acting_user

router = APIRouter(prefix="/api", tags=["me"])


@router.get("/whoami")
def whoami(request: Request, acting: dict = Depends(get_acting_user)):
    real = acting["real"]
    linked = bool(ha_notify.services_for(real))
    hint = ("No phone is linked to you yet. In Home Assistant: Settings → People → you → Track device (your phone with "
            "the Companion app). Or add a service under Admin → Users." if real["is_admin"] else
            "No phone is linked to you yet. In Home Assistant: Settings → People → you → Track device (your phone with "
            "the Companion app) — or ask an admin. It's picked up within 5 minutes.")
    return whoami_core.build(
        request, user_id=real["id"], username=real["username"], display_name=real["name"],
        is_admin=real["is_admin"], admin_entries=len(config.ADMIN_NAMES), notify_linked=linked,
        extras=[whoami_core.notify_row(linked, label="Reminder service linked", hint=hint)],
        # the rest is for the app's own code (state.me): who an admin is acting as, and what's switched on
        actingAs={"id": acting["id"], "name": acting["name"]} if acting["acting"] else None,
        notifyEntries=_notify_people(),
        maintenance={"enabled": bool(settings.get("maintenance_enabled"))},
        driveTimes={"enabled": settings.drive_times_enabled()},
        page=config.SIDEBAR_PAGE,      # the sidebar page: links open a tab or a list there (deeplink.js)
    )


def _notify_people() -> int:
    """How many people have at least one notify service assigned (count only)."""
    with db.get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(DISTINCT user_id) FROM user_notify"
        ).fetchone()[0]
