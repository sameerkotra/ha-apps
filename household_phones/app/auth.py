"""Who is asking: Home Assistant's identity headers, set by the Supervisor's ingress proxy (the only way in — the
guard in main.py refuses every other address). There is no login screen.

Admins are the people on the `admin_users` option, by user id or login name (never display name). A child is marked
by an admin (Admin → People); an admin is never a child.
"""
import logging

from fastapi import Request

from . import config, db
from .common import auth_core

logger = logging.getLogger("auth")
_warned_display_name_only: set[str] = set()


def no_admins() -> bool:
    return auth_core.no_admin(config.ADMIN_USERS)


def is_admin(user_id: str, display_name: str, username: str | None) -> bool:
    if auth_core.is_admin(user_id, username, config.ADMIN_USERS):
        return True
    if auth_core.display_name_listed(display_name, config.ADMIN_USERS) and user_id not in _warned_display_name_only:
        _warned_display_name_only.add(user_id)
        logger.warning("%r is listed in admin_users by display name only, which isn't accepted — list their user id "
                       "%s%s instead, then restart the app.", display_name, user_id,
                       f" or login name {username!r}" if username else "")
    return False


def user_dict(row) -> dict:
    admin = is_admin(row["id"], row["name"], row["username"])
    return {"id": row["id"], "name": row["name"], "username": row["username"], "is_admin": admin,
            "is_child": bool(row["is_child"]) and not admin}


def user_from(conn) -> dict | None:
    """The person behind a request (their row made, or its name refreshed when it changed — nothing is written
    otherwise), or None when Home Assistant sent no user id. Blocking (SQLite): call it from a thread."""
    ident = auth_core.identity(conn)
    if not ident.user_id:
        return None
    name = (ident.display_name or ident.username or "Home Assistant User").strip()[:80] or "Home Assistant User"
    with db.get_conn() as c:
        row = c.execute("SELECT * FROM users WHERE id = ?", (ident.user_id,)).fetchone()
        if row is None:
            c.execute("INSERT INTO users (id, name, username) VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                      "name = excluded.name, username = excluded.username", (ident.user_id, name, ident.username))
        elif row["name"] != name or row["username"] != ident.username:
            c.execute("UPDATE users SET name = ?, username = ? WHERE id = ?", (name, ident.username, ident.user_id))
        row = c.execute("SELECT * FROM users WHERE id = ?", (ident.user_id,)).fetchone()
    user = user_dict(row)
    user["ingress_path"] = ident.ingress_path
    return user


def user_by_id(conn, user_id: str | None) -> dict | None:
    if not user_id:
        return None
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return user_dict(row) if row else None


# Plain functions, not async: FastAPI runs them in its thread pool, so the SQLite read never blocks the event loop.
def get_current_user(request: Request) -> dict:
    user = user_from(request)
    if user is None:
        auth_core.require_user_id(None, config.APP_TITLE)
    return user


def require_admin(request: Request) -> dict:
    user = get_current_user(request)
    auth_core.require_admin_flag(user["is_admin"], "Only admins can do this. See admin_users on the app's "
                                                   "Configuration tab.")
    return user
