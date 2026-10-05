"""Identity (SPEC §4). Home Assistant's Supervisor authenticates the person and
passes them through Ingress as X-Remote-User-Id / -Name / -Display-Name.
They're trusted only because main.py refuses anything that didn't come from
the ingress proxy. Admins are matched by user id or login name only.

New people are **disabled** until an admin enables and sets them up.
"""
from datetime import timedelta

from fastapi import Depends, HTTPException, Request

from . import config, db
from .common import auth_core


def is_admin(user_id: str, username: str | None) -> bool:
    return auth_core.is_admin(user_id, username, config.ADMIN_NAMES)


def display_name_only(user_id, username, display_name) -> bool:
    return auth_core.display_name_only(user_id, username, display_name, config.ADMIN_NAMES)


def _stale(ts: str | None) -> bool:
    seen = config.parse_iso(ts)
    return seen is None or config.utcnow() - seen > timedelta(minutes=5)


def user_dict(row, display_only=False, admin=False) -> dict:
    return {"id": row["id"], "name": row["name"], "username": row["username"], "is_admin": admin,
            "display_name_only": display_only, "disabled": bool(row["disabled"]), "status": row["status"],
            "auto_lock_minutes": row["auto_lock_minutes"], "search_notes": bool(row["search_notes"]),
            "public_key": row["public_key"]}


async def get_current_user(request: Request) -> dict:
    """The real caller, created (disabled) on first sight."""
    ident = auth_core.identity(request)          # the X-Remote-User-* headers (app/common/auth_core.py)
    auth_core.require_user_id(ident.user_id, "Household Vault")
    uid, username = ident.user_id, ident.username
    display = (ident.display_name or username or "Home Assistant user").strip()[:100]
    now = config.now_iso()
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        if row is None:
            conn.execute("INSERT INTO users (id, name, username, created_at, last_seen) VALUES (?, ?, ?, ?, ?)",
                         (uid, display, username, now, now))
            row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        elif row["name"] != display or row["username"] != username or _stale(row["last_seen"]):
            conn.execute("UPDATE users SET name = ?, username = ?, last_seen = ? WHERE id = ?",
                         (display, username, now, uid))
            row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    return user_dict(row, display_name_only(uid, username, ident.display_name), is_admin(uid, username))


async def require_user(current: dict = Depends(get_current_user)) -> dict:
    """Enabled people. Disabled people get 403 everywhere except /api/whoami and /api/me."""
    if current["disabled"]:
        raise HTTPException(403, "An admin hasn't given you access to Household Vault yet (or has turned it off).")
    return current


async def require_admin(current: dict = Depends(get_current_user)) -> dict:
    """Admins manage users even if their own vault access is off."""
    auth_core.require_admin_flag(current["is_admin"], "Only admins can do this. See the admin_users option on "
                                                       "the app's Configuration tab.")
    return current
