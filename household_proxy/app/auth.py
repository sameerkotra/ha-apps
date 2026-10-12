"""Who is asking: Home Assistant's identity headers, set by the Supervisor's ingress proxy (the only way in — the
gate in main.py refuses every other address). There is no login screen.

Admins are the people on the `admin_users` option, by user id or login name (never display name).
"""
import logging
import time

from fastapi import HTTPException, Request

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


def user_from(conn) -> dict | None:
    """The person behind a request or WebSocket (their row made or refreshed), or None when Home Assistant sent
    no user id."""
    ident = auth_core.identity(conn)
    if not ident.user_id:
        return None
    name = (ident.display_name or ident.username or "Home Assistant User").strip()[:80] or "Home Assistant User"
    with db.get_conn() as c:
        c.execute("INSERT INTO users (id, name, username) VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                  "name = excluded.name, username = excluded.username", (ident.user_id, name, ident.username))
        row = c.execute("SELECT enabled FROM users WHERE id = ?", (ident.user_id,)).fetchone()
    return {"id": ident.user_id, "name": name, "username": ident.username,
            "is_admin": is_admin(ident.user_id, name, ident.username), "enabled": bool(row["enabled"]),
            "ingress_path": ident.ingress_path}


async def get_current_user(request: Request) -> dict:
    user = user_from(request)
    if user is None:
        auth_core.require_user_id(None, config.APP_TITLE)
    return user


async def require_admin(request: Request) -> dict:
    user = await get_current_user(request)
    auth_core.require_admin_flag(user["is_admin"], "Only admins can do this. See admin_users on the app's "
                                                   "Configuration tab.")
    return user


def require_enabled(user: dict) -> None:
    if not user["enabled"]:
        raise HTTPException(403, "An admin has turned you off in this app.")


# --------------------------------------------------------------------------------------------- proxied requests
# A page through the proxy makes many requests (scripts, images, API calls): the person's row is refreshed at most
# every CACHE_S seconds, not on each one. Admin → People changes empty the cache at once.
CACHE_S = 30
_cache: dict[str, tuple[float, dict]] = {}


def forget_cached() -> None:
    _cache.clear()


def cached_user(conn) -> dict | None:
    ident = auth_core.identity(conn)
    if not ident.user_id:
        return None
    hit = _cache.get(ident.user_id)
    if hit and hit[0] > time.monotonic() and hit[1]["username"] == ident.username:
        return dict(hit[1], ingress_path=ident.ingress_path)
    user = user_from(conn)
    if user is not None:
        if len(_cache) > 500:
            _cache.clear()
        _cache[ident.user_id] = (time.monotonic() + CACHE_S, user)
    return user
