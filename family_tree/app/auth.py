"""Identity (§4). There's no login screen: Home Assistant's Supervisor
authenticates the person and passes them to us through Ingress with

    X-Remote-User-Id            stable HA user id (required)
    X-Remote-User-Name          login name (may be missing)
    X-Remote-User-Display-Name  display name (shown, never used for matching)

These are trusted only because main.py refuses any request that didn't come
from the ingress proxy. Admins are matched by user id or login name only.
"""
from datetime import datetime, timedelta, timezone

from fastapi import Depends, Header, HTTPException

from . import config, db, kin


def _is_admin(user_id: str, username: str | None) -> bool:
    return bool({str(n).strip().lower() for n in (user_id, username) if n} & config.ADMIN_NAMES)


def _display_name_only(user_id, username, display_name) -> bool:
    """True when only the display name matches admin_users — which never grants
    admin, and is exactly the mistake the whoami page explains."""
    return (not _is_admin(user_id, username) and bool(display_name)
            and display_name.strip().lower() in config.ADMIN_NAMES)


def _stale(ts: str | None) -> bool:
    if not ts:
        return True
    try:
        seen = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    return config.utcnow() - seen > timedelta(minutes=5)


async def get_current_user(
    x_remote_user_id: str | None = Header(default=None),
    x_remote_user_name: str | None = Header(default=None),
    x_remote_user_display_name: str | None = Header(default=None),
) -> dict:
    """The real caller, created on first sight (enabled by default)."""
    if not x_remote_user_id:
        raise HTTPException(401, "No Home Assistant user identified. Open Family Tree from its panel "
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
    return {
        "id": uid, "name": display, "username": username,
        "is_admin": _is_admin(uid, username),
        "display_name_only": _display_name_only(uid, username, x_remote_user_display_name),
        "disabled": bool(row["disabled"]),
        "me_person_id": row["me_person_id"],
        "kin_lang": row["kin_lang"],
        "name_display": row["name_display"] or "en",
    }


async def require_user(current: dict = Depends(get_current_user)) -> dict:
    """Every tree route: an enabled user. Disabled users get 403 everywhere
    except /api/whoami, so they can still see why."""
    if current["disabled"]:
        raise HTTPException(403, "An admin has turned off your access to Family Tree.")
    kin.set_current(kin.user_lang(current))      # relationship names in this user's language (§13.1)
    return current


async def require_admin(current: dict = Depends(require_user)) -> dict:
    if not current["is_admin"]:
        raise HTTPException(403, "Only admins can do this. See the admin_users option on the app's Configuration tab.")
    return current
