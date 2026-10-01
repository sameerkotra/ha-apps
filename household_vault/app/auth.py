"""Identity (SPEC §4). Home Assistant's Supervisor authenticates the person and
passes them through Ingress as X-Remote-User-Id / -Name / -Display-Name.
They're trusted only because main.py refuses anything that didn't come from
the ingress proxy. Admins are matched by user id or login name only.

New people are **disabled** until an admin enables and sets them up.
"""
from datetime import timedelta

from fastapi import Depends, Header, HTTPException

from . import config, db


def is_admin(user_id: str, username: str | None) -> bool:
    return bool({str(n).strip().lower() for n in (user_id, username) if n} & config.ADMIN_NAMES)


def display_name_only(user_id, username, display_name) -> bool:
    return (not is_admin(user_id, username) and bool(display_name)
            and display_name.strip().lower() in config.ADMIN_NAMES)


def _stale(ts: str | None) -> bool:
    seen = config.parse_iso(ts)
    return seen is None or config.utcnow() - seen > timedelta(minutes=5)


def user_dict(row, display_only=False, admin=False) -> dict:
    return {"id": row["id"], "name": row["name"], "username": row["username"], "is_admin": admin,
            "display_name_only": display_only, "disabled": bool(row["disabled"]), "status": row["status"],
            "auto_lock_minutes": row["auto_lock_minutes"], "search_notes": bool(row["search_notes"]),
            "public_key": row["public_key"]}


async def get_current_user(
    x_remote_user_id: str | None = Header(default=None),
    x_remote_user_name: str | None = Header(default=None),
    x_remote_user_display_name: str | None = Header(default=None),
) -> dict:
    """The real caller, created (disabled) on first sight."""
    if not x_remote_user_id:
        raise HTTPException(401, "No Home Assistant user identified. Open Household Vault from its panel "
                                 "in the Home Assistant sidebar.")
    uid, username = x_remote_user_id, x_remote_user_name
    display = (x_remote_user_display_name or username or "Home Assistant user").strip()[:100]
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
    return user_dict(row, display_name_only(uid, username, x_remote_user_display_name), is_admin(uid, username))


async def require_user(current: dict = Depends(get_current_user)) -> dict:
    """Enabled people. Disabled people get 403 everywhere except /api/whoami and /api/me."""
    if current["disabled"]:
        raise HTTPException(403, "An admin hasn't given you access to Household Vault yet (or has turned it off).")
    return current


async def require_admin(current: dict = Depends(get_current_user)) -> dict:
    """Admins manage users even if their own vault access is off."""
    if not current["is_admin"]:
        raise HTTPException(403, "Only admins can do this. See the admin_users option on the app's Configuration tab.")
    return current
