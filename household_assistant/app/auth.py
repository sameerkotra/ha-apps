"""Who is asking: Home Assistant's identity headers, set by the Supervisor's ingress proxy (the only way in —
main.py refuses every other address). There is no login screen and no "act as": every question, answer and
action belongs to the signed-in person, admins included (HOUSEHOLD_ASSISTANT_SPEC.md §7.2).

Admins are the people on the `admin_users` option, by user id or login name (never display name).
"""
import logging

from fastapi import Request

from . import config, db
from .common import auth_core

logger = logging.getLogger("auth")
_warned_display_name_only: set[str] = set()


def no_admins() -> bool:
    return auth_core.no_admin(config.ADMIN_USERS)


def _is_admin(user_id: str, display_name: str, username: str | None) -> bool:
    if auth_core.is_admin(user_id, username, config.ADMIN_USERS):
        return True
    if auth_core.display_name_listed(display_name, config.ADMIN_USERS) and user_id not in _warned_display_name_only:
        _warned_display_name_only.add(user_id)
        logger.warning("%r is listed in admin_users by display name only, which isn't accepted — list their user id "
                       "%s%s instead, then restart the app.", display_name, user_id,
                       f" or login name {username!r}" if username else "")
    return False


async def get_current_user(request: Request) -> dict:
    ident = auth_core.identity(request)
    auth_core.require_user_id(ident.user_id, config.APP_TITLE)
    name = ident.display_name or ident.username or "Home Assistant User"
    with db.get_conn() as conn:
        conn.execute("INSERT INTO users (id, name) VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET name = excluded.name",
                     (ident.user_id, name))
        row = conn.execute("SELECT enabled, is_child FROM users WHERE id = ?", (ident.user_id,)).fetchone()
    return {"id": ident.user_id, "name": name, "username": ident.username,
            "is_admin": _is_admin(ident.user_id, name, ident.username),
            "enabled": bool(row["enabled"]), "is_child": bool(row["is_child"])}


async def require_admin(request: Request) -> dict:
    user = await get_current_user(request)
    auth_core.require_admin_flag(user["is_admin"], "Only admins can do this. See admin_users on the app's "
                                                   "Configuration tab.")
    return user
