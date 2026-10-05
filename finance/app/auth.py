"""HA ingress auth (SPEC.md sections 2-3).

No local login. Identity comes from the X-Remote-User-Id / X-Remote-User-Name
headers Supervisor adds to every ingress request, and is only trusted together
with X-Ingress-Path. Those headers are only believable because security.py
refuses every connection that doesn't come from Supervisor's ingress proxy.

Admin status is a static list (the admin_users app option, passed in as
ADMIN_USERS), matched case-insensitively against either the user id or the
display name.

Whose data a request works on (get_acting_user): an admin picks anyone with
?as_user=. A regular user works on their own data, unless the admin has given
them shared access to other users (user_access, SPEC.md section 20):
then they open straight into the first of those users' data and may switch,
with ?as_user=, only between their own data and those users'.
"""
import os
import time
from dataclasses import dataclass
from fastapi import Request, HTTPException, Depends

from .common import auth_core
from .db import get_db

# Entries lose surrounding spaces and quotes (app/common/auth_core.py).
ADMIN_USERS = set(auth_core.admin_entries(os.environ.get("ADMIN_USERS", "").split(","), strip_quotes=True))
# Compared without regard to case: HA user names are case-insensitive, and a person typing "Jane.Doe"
# into the app's admin_users list should not silently end up with no admin rights.
_ADMIN_FOLDED = {u.casefold() for u in ADMIN_USERS}


def is_admin_identity(user_id: str, name: str) -> bool:
    """Whether a request's X-Remote-User-Id or X-Remote-User-Name is on the app's admin_users list."""
    return auth_core.is_admin(user_id, name, _ADMIN_FOLDED, fold=str.casefold)


@dataclass
class User:
    id: str
    name: str
    is_admin: bool
    home_id: str = ""   # whose data a page opens into without ?as_user= (self unless shared access)


_KNOWN_USER_REFRESH_SECONDS = 600
_known_user_seen: dict[str, tuple[str, float]] = {}


def _remember_known_user(user_id: str, name: str) -> None:
    """Keeps the admin user-switch dropdown's directory (known_users) current —
    there is no live call to Home Assistant's user list. Written at most every
    10 minutes per user (or at once when the display name changes), not on
    every request: the Uploads page polls status every few seconds, and a write
    per poll only competed with the background worker for the database.
    Never allowed to break authentication."""
    now = time.monotonic()
    seen = _known_user_seen.get(user_id)
    if seen is not None and seen[0] == name and now - seen[1] < _KNOWN_USER_REFRESH_SECONDS:
        return
    try:
        with get_db() as conn:
            conn.execute(
                "INSERT INTO known_users (id, name, last_seen_at) VALUES (?, ?, datetime('now')) "
                "ON CONFLICT(id) DO UPDATE SET name = excluded.name, last_seen_at = excluded.last_seen_at",
                (user_id, name),
            )
        _known_user_seen[user_id] = (name, now)
    except Exception:
        pass


def get_current_user(request: Request) -> User:
    ident = auth_core.identity(request)            # the X-Remote-User-* / X-Ingress-Path headers
    ingress_path = ident.ingress_path
    user_id = ident.user_id

    if not ingress_path or not user_id:
        # No genuine ingress context, or Supervisor didn't identify anyone
        # (e.g. an auth provider that doesn't populate this — see HA's own
        # docs). Fail closed: no identity, no access. Never guess.
        raise HTTPException(status_code=401, detail="Not authenticated via Home Assistant ingress")

    name = ident.username if ident.username is not None else user_id
    is_admin = is_admin_identity(user_id, name)
    user = User(id=user_id, name=name, is_admin=is_admin)

    _remember_known_user(user_id, name)

    # On request.state (not a route's context) so base.html's admin-only
    # user switch appears on every page and follows the REAL admin, even
    # while they act as someone else (whose User has is_admin=False).
    request.state.real_user_id = user.id
    # A fresh install has nobody on admin_users, so nobody could open App settings: every page says how to fix it.
    request.state.no_admin = auth_core.no_admin(ADMIN_USERS)
    request.state.is_real_admin = is_admin
    request.state.known_users = []
    request.state.shared_owners = []
    user.home_id = user.id
    if not is_admin:
        owners = shared_owners(user.id)
        if owners:
            request.state.shared_owners = owners
            user.home_id = owners[0]["id"]
    # The banner's "your own data" link: ?as_user=<self> only when this person opens into someone else's.
    request.state.own_data_qs = f"?as_user={user.id}" if user.home_id != user.id else ""
    if is_admin:
        try:
            with get_db() as conn:
                request.state.known_users = conn.execute(
                    "SELECT id, name FROM known_users ORDER BY name COLLATE NOCASE"
                ).fetchall()
        except Exception:
            pass  # dropdown just renders empty (plus the "you" fallback base.html adds) — never blocks the page

    return user


def shared_owners(viewer_id: str) -> list[dict]:
    """The users whose data `viewer_id` was given access to, oldest grant first ([] on any error)."""
    try:
        with get_db() as conn:
            rows = conn.execute(
                "SELECT a.owner_id AS id, COALESCE(k.name, a.owner_id) AS name FROM user_access a "
                "LEFT JOIN known_users k ON k.id = a.owner_id WHERE a.viewer_id = ? "
                "ORDER BY a.granted_at, a.owner_id", (viewer_id,)).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_acting_user(
    request: Request,
    current: User = Depends(get_current_user),
) -> User:
    """The user whose DATA this request should operate on.

    A regular user: their own data, or — only if the admin gave them shared
    access — one of the granted owners' (the first one when there's no
    as_user). An as_user naming anyone else is ignored, so a regular user's
    request parameters can never widen access beyond what the admin granted.
    An admin: ?as_user=<id> switches scope to that user."""
    if not current.is_admin:
        owners = getattr(request.state, "shared_owners", [])
        if not owners:
            return current
        target_id = request.query_params.get("as_user") or current.home_id
        if target_id == current.id:
            return current
        owner = next((o for o in owners if o["id"] == target_id), owners[0])
        return User(id=owner["id"], name=owner["name"], is_admin=False, home_id=current.home_id)

    target_id = request.query_params.get("as_user")
    if not target_id or target_id == current.id:
        return current

    # Acting as the target: data is scoped to them; the name (from
    # known_users, else the raw id) is only for the "Viewing as" banner.
    target_name = target_id
    try:
        with get_db() as conn:
            row = conn.execute("SELECT name FROM known_users WHERE id = ?", (target_id,)).fetchone()
        if row is not None:
            target_name = row["name"]
    except Exception:
        pass

    return User(id=target_id, name=target_name, is_admin=False, home_id=current.id)


def require_admin(current: User = Depends(get_current_user)) -> User:
    auth_core.require_admin_flag(current.is_admin, "Admin only")
    return current
