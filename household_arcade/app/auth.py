"""There is no login screen. Home Assistant's Supervisor authenticates the
person before it proxies a request to us (Ingress) and identifies them with:

    X-Remote-User-Id            stable Home Assistant user id
    X-Remote-User-Name          HA login name (may be absent for some auth providers)
    X-Remote-User-Display-Name  HA display name

These are trusted only because main.py refuses every request that doesn't
come from the Supervisor's ingress proxy (or loopback), and config.yaml
publishes no ports.

    get_current_user  the caller (created on first sight)
    require_admin     403 unless the caller is in `admin_users`
"""
import logging

from fastapi import Depends, Request

from . import config, db
from .common import auth_core

logger = logging.getLogger("auth")

_warned_display_name: set[str] = set()


def is_admin_identity(user_id: str | None, username: str | None) -> bool:
    """Admin only by the user id or the login name (case-insensitive). The
    display name is deliberately NOT matched: a person may be able to edit it."""
    return auth_core.is_admin(user_id, username, config.ADMIN_NAMES)


def _warn_display_name_only(user_id: str, username: str | None, display_name: str | None) -> None:
    if auth_core.display_name_only(user_id, username, display_name, config.ADMIN_NAMES) \
            and user_id not in _warned_display_name:
        _warned_display_name.add(user_id)
        logger.warning("An admin_users entry matches %r only by display name, which is never used. "
                       "Use the user id %s or the login name instead.", display_name, user_id)


# The only addresses a request may come from: the Supervisor's ingress proxy, or loopback (main.py).
INGRESS_ALLOWED_HOSTS = auth_core.INGRESS_HOSTS


def load_user(x_remote_user_id: str | None, x_remote_user_name: str | None,
              x_remote_user_display_name: str | None) -> dict:
    """The person behind the identity headers (created on first sight). Raises 401 without a user id.
    Used by get_current_user for every request and by the live WebSocket, which has no Header() dependency."""
    user_id = (x_remote_user_id or "").strip()
    auth_core.require_user_id(user_id, "Household Arcade")
    username = (x_remote_user_name or "").strip() or None
    display_name = (x_remote_user_display_name or "").strip() or username or "Home Assistant User"
    _warn_display_name_only(user_id, username, x_remote_user_display_name)
    now = config.now_iso()
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if row:
            conn.execute("UPDATE users SET name = ?, username = ?, last_seen = ? WHERE id = ?",
                         (display_name, username, now, user_id))
        else:
            conn.execute("INSERT INTO users (id, name, username, created_at, last_seen) VALUES (?, ?, ?, ?, ?)",
                         (user_id, display_name, username, now, now))
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    admin = is_admin_identity(user_id, username)
    return {
        "id": user_id,
        "name": display_name,
        "username": username,
        "is_admin": admin,
        "disabled": bool(row["disabled"]),
        # limits never apply to an admin, even one marked as a child before being listed
        "is_child": bool(row["is_child"]) and not admin,
    }


async def get_current_user(request: Request) -> dict:
    ident = auth_core.identity(request)          # the X-Remote-User-* headers (app/common/auth_core.py)
    return load_user(ident.user_id, ident.username, ident.display_name)


async def require_admin(current: dict = Depends(get_current_user)) -> dict:
    auth_core.require_admin_flag(current["is_admin"], "Only designated admins can do this. See the admin_users "
                                                       "option on the app's Configuration tab.")
    return current
