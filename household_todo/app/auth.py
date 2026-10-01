"""There is no login screen. Home Assistant's Supervisor authenticates the
person before it ever proxies a request to us (Ingress) and identifies them
with three headers:

    X-Remote-User-Id            stable Home Assistant user id
    X-Remote-User-Name          HA username (may be absent for some auth providers)
    X-Remote-User-Display-Name  HA display name

These are trusted completely — which is only safe because config.yaml never
adds a `ports:` mapping, so Ingress is the only way into the container.

Three dependencies (SPEC §7):

    get_current_user  the real caller (created on first sight, with a "My Tasks" list)
    get_acting_user   whose data a request reads and writes: the caller, or — for
                      an admin passing ?as_user=<id> — another known user
    require_admin     403 unless the real caller is in `admin_users` — guards the
                      whole Admin area (App settings, Users, Storage)
"""
import logging

from fastapi import Depends, Header, HTTPException, Query, Request

from . import config, db

logger = logging.getLogger("auth")

_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _is_admin(user_id: str, username: str | None) -> bool:
    """Admin only by the two identifiers Supervisor sets: the user id and the
    login name. The display name is deliberately NOT matched — it is the one
    thing a person may be able to edit about themselves, and matching it could
    let them rename themselves into admin."""
    candidates = {str(n).strip().lower() for n in (user_id, username) if n}
    return bool(candidates & config.ADMIN_NAMES)


async def get_current_user(
    x_remote_user_id: str | None = Header(default=None),
    x_remote_user_name: str | None = Header(default=None),
    x_remote_user_display_name: str | None = Header(default=None),
) -> dict:
    user_id = x_remote_user_id
    username = x_remote_user_name
    display_name = x_remote_user_display_name or x_remote_user_name

    if not user_id:
        raise HTTPException(
            401,
            "No Home Assistant user identified. Open Household Todo from its panel "
            "in the Home Assistant sidebar.",
        )

    display_name = display_name or "Home Assistant User"
    with db.get_conn() as conn:
        exists = conn.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone()
        if exists:
            conn.execute("UPDATE users SET name = ?, username = ? WHERE id = ?", (display_name, username, user_id))
        else:
            now = config.now_iso()
            conn.execute(
                "INSERT INTO users (id, name, username, created_at, disabled) VALUES (?, ?, ?, ?, 0)",
                (user_id, display_name, username, now),
            )
            # a brand-new user gets their personal "My Tasks" list (§5)
            pos_row = conn.execute(
                "SELECT MAX(position) AS m FROM lists WHERE kind = 'personal' AND owner_user_id = ?", (user_id,)
            ).fetchone()
            conn.execute(
                "INSERT INTO lists (id, name, kind, owner_user_id, created_at, position) "
                "VALUES (?, 'My Tasks', 'personal', ?, ?, ?)",
                (db.new_id(), user_id, now, 0 if pos_row["m"] is None else pos_row["m"] + 1),
            )

    return {
        "id": user_id,
        "name": display_name,
        "username": username,
        "is_admin": _is_admin(user_id, username),
    }


async def get_acting_user(
    request: Request,
    as_user: str | None = Query(default=None),
    current: dict = Depends(get_current_user),
) -> dict:
    """Who the request reads/writes for. A non-admin's `as_user` is ignored —
    they always act as themselves. An admin's `as_user` naming another known
    user switches to them; an unknown id is a 404 (never a silent fallback,
    so an admin is never left writing as themselves while believing
    otherwise). This server-side check is the enforcement; hiding the
    switcher in the UI is a convenience."""
    result = dict(current, real=current, acting=False)
    if not as_user or as_user == current["id"] or not current["is_admin"]:
        return result

    with db.get_conn() as conn:
        row = conn.execute("SELECT id, name, username FROM users WHERE id = ?", (as_user,)).fetchone()
    if not row:
        raise HTTPException(404, "That user isn't known to this app.")

    acting = {
        "id": row["id"], "name": row["name"], "username": row["username"],
        "is_admin": current["is_admin"],   # admin authority stays with the real caller
        "real": current, "acting": True,
    }
    if request.method in _WRITE_METHODS:
        logger.info("AUDIT %s acting as %s: %s %s", current["name"], row["name"], request.method, request.url.path)
    return acting


async def get_real_user_for_prefs(
    as_user: str | None = Query(default=None),
    current: dict = Depends(get_current_user),
) -> dict:
    """The prefs routes always use the real caller; an admin whose `as_user`
    names someone else gets a 409 so they can never change another person's
    notifications or send a test push to their phone (§8e)."""
    if as_user and as_user != current["id"] and current["is_admin"]:
        raise HTTPException(409, "Reminder settings can only be changed as yourself — switch back to yourself first.")
    return current


async def require_admin(current: dict = Depends(get_current_user)) -> dict:
    if not current["is_admin"]:
        raise HTTPException(403, "Only designated admins can do this. See the admin_users option on the app's Configuration tab.")
    return current
