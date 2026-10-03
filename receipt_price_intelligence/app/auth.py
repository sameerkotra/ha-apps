"""Who is making this request, and are they an administrator?

The app is reached only through Home Assistant's ingress, whose proxy adds ``X-Remote-User-Id``,
``X-Remote-User-Name`` and ``X-Remote-User-Display-Name`` to every request. Headers alone prove
nothing (any container on Home Assistant's network could send them), so ``ingress_gate`` first
refuses every request whose TCP source isn't the ingress proxy (172.30.32.2) or this container
itself, and every API request without a Home Assistant user.

Administrators are the people on the app's ``admin_users`` option (Configuration tab), by Home
Assistant user id or login name, without regard to case. The list is read when the app starts.
Nobody becomes an administrator any other way.

Every signed-in person can view and add data; administrators also manage homes, App settings,
backups and the web debug page.
"""

from __future__ import annotations

import json
import os
import time
from contextvars import ContextVar
from dataclasses import dataclass

from fastapi import HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.exc import IntegrityError
from starlette.responses import JSONResponse

from app.db import get_db_session
from app.db.models import User
from app.logging_config import get_logger

logger = get_logger("auth")

INGRESS_HOSTS = frozenset({"172.30.32.2", "127.0.0.1", "::1"})
OPTIONS_PATH = os.environ.get("OPTIONS_PATH", "/data/options.json")
_CACHE_SECONDS = 60


def _clean(name: str) -> str:
    return str(name).strip().strip("\"'").strip()


def load_admin_users() -> list[str]:
    """``admin_users`` from the app's options (a list; a comma-separated string is accepted too)."""
    raw: object = os.environ.get("ADMIN_USERS")
    if raw is None:
        try:
            with open(OPTIONS_PATH, encoding="utf-8") as f:
                raw = json.load(f).get("admin_users") or []
        except (OSError, ValueError, AttributeError):
            raw = []
    items = raw.split(",") if isinstance(raw, str) else raw if isinstance(raw, list) else []
    return [n for n in (_clean(x) for x in items if isinstance(x, (str, int))) if n]


ADMIN_USERS: list[str] = load_admin_users()
_ADMIN_FOLDED = {n.casefold() for n in ADMIN_USERS}


def is_admin_identity(user_id: str | None, username: str | None) -> bool:
    return bool((user_id and user_id.strip().casefold() in _ADMIN_FOLDED)
                or (username and username.strip().casefold() in _ADMIN_FOLDED))


def no_admin_yet() -> bool:
    """First run: admin_users is empty, so nobody can open the Admin pages."""
    return not _ADMIN_FOLDED


@dataclass(frozen=True)
class CurrentUser:
    id: str
    username: str | None
    display_name: str
    is_admin: bool
    anonymous: bool = False      # kept for older callers; always False now


_current_user: ContextVar[CurrentUser | None] = ContextVar("current_user", default=None)
_cache: dict[str, tuple[tuple, CurrentUser, float]] = {}


def invalidate_user_cache() -> None:
    _cache.clear()


def get_current_user() -> CurrentUser:
    user = _current_user.get()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Open this app from Home Assistant")
    return user


def get_current_user_id() -> str:
    """ID of the user making the current request."""
    return get_current_user().id


def require_admin() -> CurrentUser:
    """FastAPI dependency: only administrators may continue."""
    user = get_current_user()
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Only an administrator can do this (the app's admin_users option)")
    return user


def identify(request: Request) -> tuple[str, str | None, str | None] | None:
    """(user id, login name, display name) from the ingress headers, else None."""
    user_id = (request.headers.get("x-remote-user-id") or "").strip()
    if not user_id:
        return None
    return user_id, request.headers.get("x-remote-user-name"), request.headers.get("x-remote-user-display-name")


def _sync_user(user_id: str, username: str | None, display_name: str | None) -> CurrentUser:
    """Create or refresh the person's row (receipts and lists refer to it)."""
    fingerprint = (user_id, username, display_name)
    cached = _cache.get(user_id)
    if cached and cached[0] == fingerprint and time.time() - cached[2] < _CACHE_SECONDS:
        return cached[1]

    label = display_name or username or user_id
    admin = is_admin_identity(user_id, username)
    with get_db_session()() as db:
        for attempt in (1, 2):
            try:
                user = db.get(User, user_id)
                if user is None:
                    user = User(id=user_id, display_name=label, role="ADMIN" if admin else "USER")
                    db.add(user)
                else:
                    if display_name and user.display_name != display_name:
                        user.display_name = display_name
                    # The column only mirrors admin_users now (for backups and older copies of the app).
                    role = "ADMIN" if admin else "USER"
                    if user.role != role:
                        user.role = role
                db.commit()
                break
            except IntegrityError:  # two first requests raced to create the same user
                db.rollback()
                if attempt == 2:
                    raise
        result = CurrentUser(id=user.id, username=username, display_name=user.display_name or label, is_admin=admin)

    _cache[user_id] = (fingerprint, result, time.time())
    return result


async def ingress_gate(request: Request, call_next):
    """Every request must come through Home Assistant's ingress; API requests need its user."""
    host = request.client.host if request.client else ""
    if host not in INGRESS_HOSTS:
        return JSONResponse(status_code=403, content={"detail": "Forbidden: open this app from Home Assistant"})
    if not request.url.path.startswith("/api/"):
        return await call_next(request)

    ident = identify(request)
    if ident is None:
        return JSONResponse(status_code=401, content={"detail": "Open this app from Home Assistant to sign in"})
    try:
        user = await run_in_threadpool(_sync_user, *ident)
    except Exception as e:  # noqa: BLE001
        logger.error("Could not resolve user: %s", e, exc_info=True)
        return JSONResponse(status_code=500, content={"detail": "Could not resolve user"})

    token = _current_user.set(user)
    try:
        return await call_next(request)
    finally:
        _current_user.reset(token)


# The name the rest of the app registers.
user_middleware = ingress_gate


def sync_roles() -> None:
    """At start-up: make the role column match admin_users (by user id; a login-name match is
    filled in when that person next opens the app)."""
    with get_db_session()() as db:
        for user in db.query(User).all():
            role = "ADMIN" if is_admin_identity(user.id, None) else "USER"
            if user.role != role:
                user.role = role
        db.commit()
    invalidate_user_cache()
