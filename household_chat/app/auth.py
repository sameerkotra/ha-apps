"""Identity (SPEC §4). Home Assistant's Supervisor authenticates the person and
passes them through Ingress as X-Remote-User-Id / -Name / -Display-Name.
They're trusted only because main.py refuses anything that didn't come from
the ingress proxy. Admins are matched by user id or login name only.

Everyone is **disabled** until an admin enables them.
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
    return seen is None or config.utcnow() - seen > timedelta(minutes=2)


def user_dict(row, admin=False, display_only=False) -> dict:
    return {"id": row["id"], "name": row["name"], "username": row["username"], "is_admin": admin,
            "display_name_only": display_only, "disabled": bool(row["disabled"]),
            "is_child": bool(row["is_child"]) if "is_child" in row.keys() else False}


def identify(uid: str | None, username: str | None, display_name: str | None) -> dict:
    """The real caller, created (disabled) on first sight; name kept current."""
    if not uid:
        raise HTTPException(401, "No Home Assistant user identified. Open Household Chat from its panel "
                                 "in the Home Assistant sidebar.")
    display = (display_name or username or "Home Assistant user").strip()[:100] or "Home Assistant user"
    now = config.now_iso()
    renamed = False
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        if row is None:
            conn.execute("INSERT INTO users (id, name, username, created_at, last_seen) VALUES (?, ?, ?, ?, ?)",
                         (uid, display, username, now, now))
            row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        elif row["name"] != display or row["username"] != username or _stale(row["last_seen"]):
            renamed = row["name"] != display
            conn.execute("UPDATE users SET name = ?, username = ?, last_seen = ? WHERE id = ?",
                         (display, username, now, uid))
            row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        if renamed:
            from . import files
            files.refresh_folders_of_user(conn, uid)
    return user_dict(row, is_admin(uid, username), display_name_only(uid, username, display_name))


async def get_current_user(
    x_remote_user_id: str | None = Header(default=None),
    x_remote_user_name: str | None = Header(default=None),
    x_remote_user_display_name: str | None = Header(default=None),
) -> dict:
    return identify(x_remote_user_id, x_remote_user_name, x_remote_user_display_name)


async def require_user(current: dict = Depends(get_current_user)) -> dict:
    """Enabled people. Disabled people get 403 everywhere except /api/whoami and /api/me."""
    if current["disabled"]:
        raise HTTPException(403, "An admin hasn't given you access to Household Chat yet (or has turned it off).")
    return current


async def require_admin(current: dict = Depends(get_current_user)) -> dict:
    """Admins manage people and settings even if their own chat access is off. Being an
    admin never opens a chat they aren't in (SPEC §4)."""
    if not current["is_admin"]:
        raise HTTPException(403, "Only admins can do this. See admin_users in the app's Configuration tab.")
    return current
