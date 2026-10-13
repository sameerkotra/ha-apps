"""Who is asking: Home Assistant's identity headers, set by the Supervisor's ingress proxy (the only way in —
main.py refuses every other address). There is no login screen and no "act as": every question, answer and
action belongs to the signed-in person, admins included (HOUSEHOLD_ASSISTANT_SPEC.md §7.2).

`get_current_user` is a plain dependency (run in the threadpool): it reads the person's row and writes it only when
it is new or their name changed.

Admins are the people on the `admin_users` option, by user id or login name (never display name).
"""
import logging

from fastapi import Request

from . import config, db
from .common import auth_core, ha_people

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


def get_current_user(request: Request) -> dict:
    """The signed-in person. A plain (sync) dependency, so FastAPI runs it in its threadpool and the SQLite work
    never blocks the event loop. One read per request; the row is written only when it is new or the name changed."""
    ident = auth_core.identity(request)
    auth_core.require_user_id(ident.user_id, config.APP_TITLE)
    name = ident.display_name or ident.username or "Home Assistant User"
    with db.get_conn() as conn:
        row = conn.execute("SELECT name, enabled, is_child FROM users WHERE id = ?", (ident.user_id,)).fetchone()
        if row is None:
            # reviewed = 0: shown as new on Admin → People until an admin looks at them
            conn.execute("INSERT INTO users (id, name, reviewed) VALUES (?, ?, 0) ON CONFLICT(id) DO UPDATE SET "
                         "name = excluded.name", (ident.user_id, name))
            row = conn.execute("SELECT name, enabled, is_child FROM users WHERE id = ?", (ident.user_id,)).fetchone()
        elif row["name"] != name:
            conn.execute("UPDATE users SET name = ? WHERE id = ?", (name, ident.user_id))
    return {"id": ident.user_id, "name": name, "username": ident.username,
            "is_admin": _is_admin(ident.user_id, name, ident.username),
            "enabled": bool(row["enabled"]), "is_child": bool(row["is_child"])}


UNKNOWN_PERSON = ("I don't know who is asking: that Home Assistant user isn't one of the household's people. Open the "
                  "Household Assistant once from the sidebar, then ask again.")


def user_by_id(user_id: str, name: str | None = None) -> dict:
    """The person a bus message asks for (Assist): their row. A row is made only for someone the app already knows
    (they opened it) or Home Assistant's people list links to this user id — never for any id a message names.
    Raises PermissionError (UNKNOWN_PERSON) otherwise."""
    with db.get_conn() as conn:
        row = conn.execute("SELECT name, enabled, is_child FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            person = ha_people.person_for(user_id)
            if person is None:
                raise PermissionError(UNKNOWN_PERSON)
            name = (person.get("name") or name or "").strip()[:80] or "Home Assistant User"
            conn.execute("INSERT INTO users (id, name, reviewed) VALUES (?, ?, 0) ON CONFLICT(id) DO NOTHING",
                         (user_id, name))
            row = conn.execute("SELECT name, enabled, is_child FROM users WHERE id = ?", (user_id,)).fetchone()
    return {"id": user_id, "name": row["name"], "username": None, "is_admin": _is_admin(user_id, row["name"], None),
            "enabled": bool(row["enabled"]), "is_child": bool(row["is_child"])}


def require_admin(request: Request) -> dict:
    user = get_current_user(request)
    auth_core.require_admin_flag(user["is_admin"], "Only admins can do this. See admin_users on the app's "
                                                   "Configuration tab.")
    return user
