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
    """The person behind a request or WebSocket (their row made, or its name refreshed when it changed — nothing is
    written otherwise), or None when Home Assistant sent no user id. Blocking (SQLite): call it from a thread."""
    ident = auth_core.identity(conn)
    if not ident.user_id:
        return None
    name = (ident.display_name or ident.username or "Home Assistant User").strip()[:80] or "Home Assistant User"
    with db.get_conn() as c:
        row = c.execute("SELECT name, username, enabled FROM users WHERE id = ?", (ident.user_id,)).fetchone()
        if row is None:
            c.execute("INSERT INTO users (id, name, username) VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                      "name = excluded.name, username = excluded.username", (ident.user_id, name, ident.username))
            enabled = True
        else:
            if row["name"] != name or row["username"] != ident.username:
                c.execute("UPDATE users SET name = ?, username = ? WHERE id = ?", (name, ident.username, ident.user_id))
            enabled = bool(row["enabled"])
    return {"id": ident.user_id, "name": name, "username": ident.username,
            "is_admin": is_admin(ident.user_id, name, ident.username), "enabled": enabled,
            "ingress_path": ident.ingress_path}


def user_by_id(user_id: str) -> dict | None:
    """A known person as the database has them now (an open websocket checking it may still stay open)."""
    with db.get_conn() as c:
        row = c.execute("SELECT id, name, username, enabled FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None:
        return None
    return {"id": row["id"], "name": row["name"], "username": row["username"],
            "is_admin": is_admin(row["id"], row["name"], row["username"]), "enabled": bool(row["enabled"])}


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
    require_enabled(user)                         # an admin another admin turned off has no admin pages either
    return user


def require_enabled(user: dict) -> None:
    if not user["enabled"]:
        raise HTTPException(403, "An admin has turned you off in this app.")


# --------------------------------------------------------------------------------------------- proxied requests
# A page through the proxy makes many requests (scripts, images, API calls): the person's row is refreshed at most
# every CACHE_S seconds, not on each one. Admin → People changes empty the cache at once.
CACHE_S = 30
_cache: dict[str, tuple[float, dict]] = {}
_listeners: list = []


def on_forget(fn) -> None:
    """Call fn() (from any thread) whenever people change in Admin → People (open websockets check again)."""
    _listeners.append(fn)


def forget_cached() -> None:
    _cache.clear()
    for fn in _listeners:
        try:
            fn()
        except Exception:                          # noqa: BLE001 — a listener never stops the change
            logger.exception("a people listener failed")


def peek_user(conn) -> dict | None:
    """The person from the cache only (no database: safe on the event loop), or None if they aren't in it."""
    ident = auth_core.identity(conn)
    if not ident.user_id:
        return None
    hit = _cache.get(ident.user_id)
    if hit and hit[0] > time.monotonic() and hit[1]["username"] == ident.username:
        return dict(hit[1], ingress_path=ident.ingress_path)
    return None


def cached_user(conn) -> dict | None:
    """The person, from the cache or the database (blocking: from a thread)."""
    hit = peek_user(conn)
    if hit is not None:
        return hit
    ident = auth_core.identity(conn)
    if not ident.user_id:
        return None
    user = user_from(conn)
    if user is not None:
        if len(_cache) > 500:
            _cache.clear()
        _cache[ident.user_id] = (time.monotonic() + CACHE_S, user)
    return user
