"""There is no login screen in this app. Home Assistant's Supervisor
authenticates the person before it ever proxies a request to us (via
Ingress), and identifies who they are using three headers it adds to
every proxied request:

    X-Remote-User-Id            stable Home Assistant user id
    X-Remote-User-Name          HA username (may be absent for some auth providers)
    X-Remote-User-Display-Name  HA display name

We trust these headers completely — which is only safe because this
app has no `ports:` mapping and isn't on host networking, so Ingress
is the *only* path into the container. See DOCS.md's "Who can see what" section
for the full threat-model discussion.
"""
import logging
import re

from fastapi import Depends, Header, HTTPException, Query

from . import config, db

logger = logging.getLogger("auth")
_warned_display_name_only: set[str] = set()


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return slug or "user"


def display_name_listed(display_name: str | None) -> bool:
    return bool(display_name) and display_name.strip().lower() in config.ADMIN_USERS


def no_admins() -> bool:
    """True while the admin_users option is empty: nobody can open App
    settings yet, so every page shows the "No admin yet" banner. Nobody is
    ever promoted automatically."""
    return not config.ADMIN_USERS


def _is_admin(user_id: str, display_name: str, username: str | None) -> bool:
    """On the admin_users list by user id or login name (any case) — the
    two identifiers Supervisor sets and nobody can pick for themselves.
    Display names are deliberately NOT matched: they aren't unique and
    aren't a stable identity, so matching them could let someone become an
    admin just by having — or taking — an admin's name. Same rule as
    Household Todo and Splitpot. Someone listed only by display name gets a
    one-time log warning (and a hint on "How the app sees you") telling the
    HA admin which id to list instead."""
    candidates = {n.strip().lower() for n in (user_id, username) if n}
    if candidates & config.ADMIN_USERS:
        return True
    if display_name_listed(display_name) and user_id not in _warned_display_name_only:
        _warned_display_name_only.add(user_id)
        logger.warning(
            "%r is listed in admin_users by display name only, which isn't accepted — "
            "list their user id %s%s instead, then restart the app.",
            display_name, user_id, f" or login name {username!r}" if username else "",
        )
    return False


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
            "No Home Assistant user identified. Open Calorie Tracker from its panel "
            "in the Home Assistant sidebar.",
        )

    display_name = display_name or "Home Assistant User"
    is_admin = _is_admin(user_id, display_name, username)

    with db.get_conn() as conn:
        conn.execute(
            """
            INSERT INTO users (id, name) VALUES (?, ?)
            ON CONFLICT(id) DO UPDATE SET name = excluded.name
            """,
            (user_id, display_name),
        )
        conn.execute(
            "INSERT OR IGNORE INTO goals (user_id) VALUES (?)", (user_id,)
        )

    return {
        "id": user_id,
        "name": display_name,
        "slug": _slugify(display_name),
        "is_admin": is_admin,
        # kept for the "How the app sees you" page (routers/me.py)
        "username": username,
    }


async def get_acting_user(
    as_user: str | None = Query(default=None, alias="as_user"),
    current: dict = Depends(get_current_user),
) -> dict:
    """Resolves who the request should actually read/write data for.

    Only users listed in the `admin_users` app option can act as
    someone else — everyone else always acts as themselves, full stop,
    regardless of what `as_user` says. This check happens here, server-side,
    on every data request; the frontend hiding the switcher UI for
    non-admins is a convenience, not the actual enforcement.
    """
    if not as_user or as_user == current["id"]:
        return current

    if not current["is_admin"]:
        # Not an error — a non-admin passing as_user (e.g. a stale URL/tab)
        # just silently acts as themselves rather than being blocked.
        return current

    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT id, name FROM users WHERE id = ?", (as_user,)
        ).fetchone()

    if not row:
        return current

    return {"id": row["id"], "name": row["name"], "slug": _slugify(row["name"]), "is_admin": current["is_admin"]}


async def require_admin(current: dict = Depends(get_current_user)) -> dict:
    if not current["is_admin"]:
        raise HTTPException(403, "Only admins can do this. See admin_users on the app's Configuration tab.")
    return current
