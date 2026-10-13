"""Who may use the settings page on a Docker host. There is no Home Assistant login here:

- Without ADMIN_PASSWORD (the default), whoever can open the page is its admin. docker-compose.yml publishes the
  page on this machine only (127.0.0.1:8080), so that is someone at this machine (or through an SSH tunnel).
- With ADMIN_PASSWORD set, every request to the page and its API (but /api/health) needs it, with HTTP Basic — the
  browser asks once (any user name). Ten wrong passwords in a minute from one address are answered 429 for a minute.

The gateway on 11434 has its own protection: the Host check, and access keys when *Require an access key* is on.
"""
import base64
import hmac

from fastapi import Request
from fastapi.responses import JSONResponse

from . import config, keys

LOCAL = {"id": "local", "name": "Admin", "username": "admin", "is_admin": True}
REALM = 'Basic realm="Household AI", charset="UTF-8"'
OPEN_PATHS = {"/api/health"}
FAILURES = keys.Failures()


def no_admins() -> bool:
    return False


async def get_current_user(request: Request) -> dict:
    return dict(LOCAL)


async def require_admin(request: Request) -> dict:
    return dict(LOCAL)


def basic_password(header: str | None) -> str | None:
    """The password of an `Authorization: Basic …` header (the user name is ignored); None without one."""
    scheme, _, value = (header or "").partition(" ")
    if scheme.lower() != "basic" or not value.strip():
        return None
    try:
        text = base64.b64decode(value.strip(), validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    _, sep, password = text.partition(":")
    return password if sep else None


def password_refused(request: Request) -> JSONResponse | None:
    """None when the request may go on: no ADMIN_PASSWORD, an open path, or the right password."""
    wanted = config.ADMIN_PASSWORD
    if not wanted or request.url.path in OPEN_PATHS:
        return None
    given = basic_password(request.headers.get("authorization"))
    if given is not None and hmac.compare_digest(given.encode("utf-8"), wanted.encode("utf-8")):
        return None
    address = request.client.host if request.client else "unknown"
    if FAILURES.is_blocked(address):
        return JSONResponse({"detail": "Too many wrong passwords — try again in a minute."}, status_code=429,
                            headers={"Retry-After": "60", "Cache-Control": "no-store"})
    if given is not None:
        FAILURES.fail(address)
    return JSONResponse({"detail": "Household AI's settings page needs its password (ADMIN_PASSWORD)."},
                        status_code=401, headers={"WWW-Authenticate": REALM, "Cache-Control": "no-store"})


__all__ = ["config", "get_current_user", "require_admin", "no_admins", "password_refused"]
