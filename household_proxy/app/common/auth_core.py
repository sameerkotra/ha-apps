# Shared file: edit common/python/auth_core.py and run tools/sync_common.py; don't edit this copy. sha256=d8323804963fb9349aa318dfc1ae46cd8aa9270af7aa3063d601acf17243d08b
"""Who is asking, and are they an admin? The Home Assistant ingress part every app shares
(shared: common/python/auth_core.py, copied into each app's app/common/ by tools/sync_common.py).

There is no login screen in these apps. Home Assistant's Supervisor signs the person in and proxies
their requests to the app (Ingress), adding:

    X-Remote-User-Id            stable Home Assistant user id
    X-Remote-User-Name          login name (some auth providers don't send it)
    X-Remote-User-Display-Name  display name (shown, never used to decide anything)
    X-Ingress-Path              the ingress prefix (only Supervisor sets it)

Those headers prove nothing on their own — anything else on Home Assistant's internal network could
send them — so every app first refuses any connection that doesn't come from the ingress proxy
(172.30.32.2) or, where the app allows it, loopback (`from_ingress` / `refuse_outsiders`).

Admins are the people on the app's `admin_users` option (Configuration tab), matched by user id or
login name without regard to case, never by display name (`is_admin`). An empty list means nobody is
an admin: every page says "No admin yet" (`no_admin`).

Each app keeps its own `auth.py` (its `get_current_user` / `require_admin`, its user record, its 401/403
wording) and its own extra rules on top — Vault sessions, Todo "acting as", Finance "view as", disabled
people. This module holds only the rules that are the same everywhere, with each app's differences as
parameters (which addresses count as the proxy, lower() or casefold(), quote-stripping of list entries).
It needs nothing from the app (no `from .. import`).

    from .common import auth_core
    blocked = auth_core.refuse_outsiders(request)          # in the app's http middleware
    if blocked is not None:
        return blocked
    ADMIN_NAMES = auth_core.admin_names(options.get("admin_users") or []) | auth_core.env_admins("DEV_ADMINS")
    auth_core.is_admin(user_id, username, config.ADMIN_NAMES)
"""
import json
import logging
import os
from dataclasses import dataclass

from fastapi import HTTPException
from starlette.responses import JSONResponse

logger = logging.getLogger("auth")

# ---------------------------------------------------------------------------------------------
# Where a request comes from
# ---------------------------------------------------------------------------------------------
INGRESS_PROXY = "172.30.32.2"                     # Supervisor's ingress proxy
LOOPBACK = frozenset({"127.0.0.1", "::1"})        # the container itself (health checks, local tests)
INGRESS_HOSTS = frozenset({INGRESS_PROXY}) | LOOPBACK

FORBIDDEN_DETAIL = "Forbidden — access only via Home Assistant"


def client_host(conn, default=None):
    """The TCP peer address of a request or WebSocket (`default` when the server doesn't know it)."""
    return conn.client.host if conn.client else default


def from_ingress(conn, allowed=INGRESS_HOSTS) -> bool:
    """Whether the connection comes from one of the `allowed` addresses (default: the ingress proxy or
    loopback). An unknown peer address never counts."""
    return client_host(conn) in allowed


def forbidden(detail: str = FORBIDDEN_DETAIL) -> JSONResponse:
    """The 403 an app answers a connection from anywhere else with: {"detail": ...}."""
    return JSONResponse(status_code=403, content={"detail": detail})


def refuse_outsiders(conn, allowed=INGRESS_HOSTS, detail: str = FORBIDDEN_DETAIL):
    """For an http middleware: the 403 response when the request doesn't come from `allowed`, else None."""
    if not from_ingress(conn, allowed):
        return forbidden(detail)
    return None


# ---------------------------------------------------------------------------------------------
# The identity headers
# ---------------------------------------------------------------------------------------------
USER_ID = "x-remote-user-id"
USER_NAME = "x-remote-user-name"
DISPLAY_NAME = "x-remote-user-display-name"
INGRESS_PATH = "x-ingress-path"


@dataclass(frozen=True)
class Identity:
    """What Home Assistant sent, exactly as sent (None for a header that is missing)."""
    user_id: str | None
    username: str | None
    display_name: str | None
    ingress_path: str | None


def identity(conn) -> Identity:
    """The identity headers of a request or WebSocket, unchanged."""
    h = conn.headers
    return Identity(h.get(USER_ID), h.get(USER_NAME), h.get(DISPLAY_NAME), h.get(INGRESS_PATH))


def no_user_message(app_title: str) -> str:
    """The usual 401 text when Home Assistant identified nobody."""
    return (f"No Home Assistant user identified. Open {app_title} from its panel "
            "in the Home Assistant sidebar.")


def require_user_id(user_id, app_title: str) -> None:
    """401 (with `no_user_message`) unless there is a user id."""
    if not user_id:
        raise HTTPException(401, no_user_message(app_title))


# ---------------------------------------------------------------------------------------------
# admin_users
# ---------------------------------------------------------------------------------------------
def read_options(path: str, *, log: logging.Logger | None = None,
                 errors: tuple = (json.JSONDecodeError, OSError, UnicodeDecodeError)) -> dict:
    """The app's options.json as a dict: {} when it's missing, unreadable (`errors`; logged as a warning
    on `log`) or not a JSON object."""
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except errors:
        if log is not None:
            log.warning("Could not read %s; using defaults", path)
        return {}
    return data if isinstance(data, dict) else {}


def clean_entry(entry, *, strip_quotes: bool = False) -> str:
    """One admin_users entry as text without surrounding spaces (and quotes, with `strip_quotes`)."""
    s = str(entry).strip()
    if strip_quotes:
        s = s.strip("\"'").strip()
    return s


def admin_entries(entries, *, strip_quotes: bool = False) -> list:
    """The non-empty cleaned entries, in order."""
    return [s for s in (clean_entry(e, strip_quotes=strip_quotes) for e in entries) if s]


def admin_names(entries, *, strip_quotes: bool = False, fold=str.lower) -> set:
    """The set `is_admin` matches against: each non-empty cleaned entry, case-folded with `fold`."""
    return {fold(s) for s in admin_entries(entries, strip_quotes=strip_quotes)}


def env_admins(var: str, *, fold=str.lower) -> set:
    """Extra admins from a comma-separated environment variable (tests and local development); empty
    when it isn't set."""
    raw = os.environ.get(var)
    return admin_names(raw.split(",") if raw else (), fold=fold)


def is_admin(user_id, username, admins, *, fold=str.lower) -> bool:
    """On the list by user id or login name, without regard to case. Display names never count: they
    aren't unique, and a person may be able to change their own."""
    return bool({fold(str(n).strip()) for n in (user_id, username) if n} & admins)


def display_name_listed(display_name, admins, *, fold=str.lower) -> bool:
    """The display name is on the list (which never makes anyone an admin)."""
    return bool(display_name) and fold(display_name.strip()) in admins


def display_name_only(user_id, username, display_name, admins, *, fold=str.lower) -> bool:
    """Not an admin, but listed by display name — the mistake "How the app sees you" explains."""
    return (not is_admin(user_id, username, admins, fold=fold)
            and display_name_listed(display_name, admins, fold=fold))


def no_admin(admins) -> bool:
    """True while admin_users is empty: nobody is an admin, and every page shows "No admin yet"."""
    return not admins


def require_admin_flag(admin: bool, detail: str) -> None:
    """403 with the app's own wording unless `admin`."""
    if not admin:
        raise HTTPException(403, detail)
