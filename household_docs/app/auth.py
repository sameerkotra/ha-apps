"""Identity (SPEC §3.2). Home Assistant's Supervisor signs the person in and passes them through Ingress as
X-Remote-User-Id / -Name / -Display-Name. Those headers are trusted only because main.py refuses anything that
didn't come from the ingress proxy. Admins are matched by user id or login name only (never display name).

New people get access straight away while `new_people_access` is on (the default); an admin can turn anyone
off. Turned-off people see "An admin has turned off Household Docs for you"; every content route answers them
404, as if nothing existed. Admins get no content access as admins.
"""
from datetime import timedelta

from fastapi import Depends, HTTPException, Request

from . import config, db, kids, settings
from .common import auth_core

TURNED_OFF = "An admin has turned off Household Docs for you."


def is_admin(user_id: str | None, username: str | None) -> bool:
    return auth_core.is_admin(user_id, username, config.ADMIN_NAMES)


def display_name_only(user_id, username, display_name) -> bool:
    return auth_core.display_name_only(user_id, username, display_name, config.ADMIN_NAMES)


def user_dict(row, admin: bool = False, display_only: bool = False) -> dict:
    return {"id": row["id"], "name": row["name"], "username": row["username"], "is_admin": admin,
            "display_name_only": display_only, "disabled": bool(row["disabled"]), "folder": row["folder"],
            "is_child": bool(row["is_child"]) if "is_child" in row.keys() else False}


def _stale(ts) -> bool:
    t = config.parse_iso(ts)
    return t is None or config.utcnow() - t > timedelta(minutes=2)


def identify(uid: str | None, username: str | None, display_name: str | None) -> dict:
    """The real caller, created on first sight (with access when `new_people_access` is on); name kept current."""
    auth_core.require_user_id(uid, config.APP_TITLE)
    uid = uid.strip()[:128]
    username = (username or "").strip()[:100] or None
    display = (display_name or username or "Home Assistant user").strip()[:100] or "Home Assistant user"
    now = config.now_iso()
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        if row is None:
            disabled = 0 if settings.get("new_people_access", conn) else 1
            conn.execute("INSERT INTO users (id, name, username, disabled, created_at, last_seen, seen_from) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?)", (uid, display, username, disabled, now, now, now))
            row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        elif row["name"] != display or row["username"] != username or _stale(row["last_seen"]):
            conn.execute("UPDATE users SET name = ?, username = ?, last_seen = ? WHERE id = ?", (display, username, now, uid))
            row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    return user_dict(row, is_admin(uid, username), display_name_only(uid, username, display_name))


async def get_current_user(request: Request) -> dict:
    ident = auth_core.identity(request)          # the X-Remote-User-* headers (app/common/auth_core.py)
    return identify(ident.user_id, ident.username, ident.display_name)


async def require_user(request: Request, current: dict = Depends(get_current_user)) -> dict:
    """People with access. Turned-off people get 404 from every content route (SPEC §3.2). A child (§17.20) gets
    403 from the routes Kids' space leaves out (kids.py; the rest is checked where the content is known)."""
    if current["disabled"]:
        raise HTTPException(404, TURNED_OFF)
    if current["is_child"]:
        route = request.scope.get("route")
        if kids.blocked(request.method, getattr(route, "path", request.url.path)):
            raise HTTPException(403, kids.MESSAGE)
    return current


async def require_admin(current: dict = Depends(get_current_user)) -> dict:
    """Admins manage people and settings even if their own access is off; being an admin never opens anyone's
    documents."""
    auth_core.require_admin_flag(current["is_admin"], "Only admins can do this. See admin_users on the app's "
                                                      "Configuration tab.")
    return current
