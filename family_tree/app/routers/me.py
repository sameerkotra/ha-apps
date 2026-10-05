"""Who you are: /me, "This is me", the whoami page, and the user list."""
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from .. import config, db, features, graph as graph_mod, kin, media, reminders, settings
from ..common import ha_notify, ha_people, people_admin, whoami as whoami_core
from ..auth import get_current_user, require_admin, require_user
from ..models import Strict

router = APIRouter(prefix="/api", tags=["me"])


class MePerson(Strict):
    personId: str | None = None


class UserPatch(Strict):
    disabled: bool | None = None
    mePersonId: str | None = None


def _person_name(conn, pid):
    if not pid:
        return None
    g = graph_mod.get(conn)
    p = g.people.get(pid)
    return p.name if p else None


@router.get("/me")
def me(user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        return {"id": user["id"], "name": user["name"], "isAdmin": user["is_admin"],
                "mePersonId": user["me_person_id"], "mePersonName": _person_name(conn, user["me_person_id"]),
                "timezone": config.timezone_name(), "today": config.today().isoformat(),
                "maxUploadMb": settings.get("max_upload_mb"), "trashDays": settings.get("trash_days"),
                "kinLang": user["kin_lang"], "kinLangEffective": kin.user_lang(user),
                "kinLangApp": settings.get("relationship_language") if features.on("kin_names") else "en",
                "mapEnabled": features.on("map"), "features": features.states(), "noAdmin": not config.ADMIN_NAMES,
                "nameDisplay": user["name_display"], "nameOrderApp": settings.get("name_order"),
                "kidPinSet": bool(conn.execute("SELECT kid_pin_hash FROM users WHERE id = ?", (user["id"],)).fetchone()[0]),
                "media": {"online": media.is_online(), "reason": media.status()["reason"]}}


def _claim(conn, user_id: str, pid: str | None) -> None:
    if pid:
        row = conn.execute("SELECT deleted_at FROM people WHERE id = ?", (pid,)).fetchone()
        if not row or row["deleted_at"]:
            raise HTTPException(404, "That person isn't in the tree.")
        other = conn.execute("SELECT name FROM users WHERE me_person_id = ? AND id != ?", (pid, user_id)).fetchone()
        if other:
            raise HTTPException(409, f"{other['name']} has already said that's them.")
    conn.execute("UPDATE users SET me_person_id = ? WHERE id = ?", (pid, user_id))


@router.put("/me/person")
def set_me(body: MePerson, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        _claim(conn, user["id"], body.personId)
        return {"mePersonId": body.personId, "mePersonName": _person_name(conn, body.personId)}


@router.get("/whoami")
def whoami(request: Request, user: dict = Depends(get_current_user)):
    """The 'How the app sees you' page (common/whoami.py). Works for disabled users too; counts only."""
    with db.get_conn() as conn:
        phones = len(ha_notify.services_for(user, conn))     # phones from HA + extras
        me_name = _person_name(conn, user["me_person_id"])
        rows = [whoami_core.row("This is me", me_name,
                                action=None if me_name else {"label": "not set — set it", "target": "settings"})]
        # the Reminders row: always for someone whose access is off (they can't see the features), else when it's on
        if user["disabled"] or features.on("reminders"):
            if phones:
                on = "on" if reminders.get_prefs(conn, user["id"])["enabled"] else "off"
                n = sum(1 for p in graph_mod.get(conn).people.values() if p.remind)
                rows.append(whoami_core.row("Reminders", f"{on} · {'1 phone or service' if phones == 1 else f'{phones} phones or services'}"
                                                         f" · {n} {'person' if n == 1 else 'people'} with 🔔 on"))
            else:
                rows.append(whoami_core.row(
                    "Reminders", "No phone linked yet",
                    "No phone is linked to you yet. In Home Assistant: Settings → People → you → Track device (your phone "
                    "with the Companion app). It's picked up within 5 minutes; or ask an admin."))
        rows.append(whoami_core.row("Account status", "Turned off by an admin" if user["disabled"] else "Enabled",
                                    tone="danger" if user["disabled"] else None))
        return whoami_core.build(
            request, user_id=user["id"], username=user["username"], display_name=user["name"],
            is_admin=user["is_admin"], admin_entries=len(config.ADMIN_NAMES),
            display_name_only=user["display_name_only"], notify_linked=phones, extras=rows,
            disabled=user["disabled"])                       # the page shows only the whoami card then


@router.get("/users")
def users(user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        rows = conn.execute("SELECT id, name, me_person_id FROM users WHERE disabled = 0 ORDER BY name COLLATE NOCASE").fetchall()
        return [{"id": r["id"], "name": r["name"], "mePersonId": r["me_person_id"],
                 "mePersonName": _person_name(conn, r["me_person_id"])} for r in rows]


ha_person_json = people_admin.ha_person_json          # their person and phones (common/people_admin.py)


@router.get("/admin/users")
def admin_users(refresh: bool = Query(default=False), admin: dict = Depends(require_admin)):
    """Admin → Users, with each person's phones from Home Assistant and any extra notify services.
    `refresh=1` reads Home Assistant's people again first (no DB connection is open meanwhile)."""
    people_admin.refresh_people(refresh)
    with db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM users ORDER BY disabled, name COLLATE NOCASE").fetchall()
        return [{"id": r["id"], "name": r["name"], "username": r["username"], "haPerson": r["ha_person"],
                 "disabled": bool(r["disabled"]), "mePersonId": r["me_person_id"],
                 "mePersonName": _person_name(conn, r["me_person_id"]), "lastSeen": r["last_seen"],
                 "isAdmin": bool({str(x).lower() for x in (r["id"], r["username"]) if x} & config.ADMIN_NAMES),
                 "notify": ha_notify.assigned_services(conn, {"id": r["id"]}),     # extra services added here
                 "ha": ha_person_json(r["id"]),                                    # phones from Home Assistant
                 "remindersOn": reminders.get_prefs(conn, r["id"])["enabled"]}
                for r in rows]


@router.patch("/users/{uid}")
def patch_user(uid: str, body: UserPatch, admin: dict = Depends(require_admin)):
    with db.get_conn() as conn:
        row = conn.execute("SELECT id FROM users WHERE id = ?", (uid,)).fetchone()
        if not row:
            raise HTTPException(404, "That user isn't known to Family Tree.")
        sent = body.model_fields_set
        if "disabled" in sent:
            if uid == admin["id"] and body.disabled:
                raise HTTPException(409, "You can't turn off your own access.")
            conn.execute("UPDATE users SET disabled = ? WHERE id = ?", (1 if body.disabled else 0, uid))
        if "mePersonId" in sent:
            _claim(conn, uid, body.mePersonId)
    return {"ok": True}
