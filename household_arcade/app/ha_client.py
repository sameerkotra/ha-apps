"""Tiny Home Assistant helper: the Supervisor's Core API proxy
(http://supervisor/core/api/) with the auto-injected SUPERVISOR_TOKEN.

Stdlib `urllib` only (no new dependency). The blocking functions are meant to
run off the event loop — `run_in_threadpool(...)` from async code, or inside
a sync FastAPI route / BackgroundTask (which FastAPI runs in a thread).

Every function here is best effort: it returns a result instead of raising,
and logs at most one "no token" warning for the life of the process.
"""
import json
import logging
import urllib.error
import urllib.request

from starlette.concurrency import run_in_threadpool

from . import config

logger = logging.getLogger("ha_client")

_warned_no_token = False


def has_token() -> bool:
    return bool(config.SUPERVISOR_TOKEN)


def warn_no_token_once(what: str) -> None:
    global _warned_no_token
    if not _warned_no_token:
        logger.warning(
            "SUPERVISOR_TOKEN not set — skipping %s. Expected outside Home Assistant; "
            "inside HA it means homeassistant_api isn't granted in config.yaml.",
            what,
        )
        _warned_no_token = True


def request(method: str, path: str, body: dict | None = None, timeout: float = 10) -> tuple[int | None, bytes]:
    """One call to the Core API. Returns (http_status, response_body);
    status is None if the request never got an HTTP answer (network error).
    Blocking — see the module docstring."""
    url = f"{config.SUPERVISOR_CORE_API}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {config.SUPERVISOR_TOKEN}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        try:
            detail = e.read() or b""   # HA's own explanation, e.g. {"message": "Service ... not found."}
        except Exception:
            detail = b""
        return e.code, detail
    except Exception as e:  # URLError, timeout, connection reset, ...
        logger.debug("HA request %s %s failed: %s", method, path, e)
        return None, b""


def post_state(entity_id: str, state: str, attributes: dict) -> bool:
    """Create/update an entity through POST /states/<entity_id>. Blocking."""
    if not has_token():
        warn_no_token_once("Home Assistant sensor sync")
        return False
    status, _ = request("POST", f"/states/{entity_id}", {"state": state, "attributes": attributes})
    return status in (200, 201)


def delete_state(entity_id: str) -> bool:
    """Remove an entity. A 404 (already gone) counts as success. Blocking."""
    if not has_token():
        warn_no_token_once("Home Assistant sensor sync")
        return False
    status, _ = request("DELETE", f"/states/{entity_id}")
    return status in (200, 204, 404)


def fetch_timezone_blocking() -> str | None:
    """Home Assistant's configured time zone name (GET /config), or None."""
    if not has_token():
        return None
    status, body = request("GET", "/config")
    if status != 200:
        return None
    try:
        data = json.loads(body)
        return data.get("time_zone")
    except (ValueError, AttributeError):
        return None


async def load_timezone() -> None:
    """At startup: fetch HA's time zone once and apply it; fall back to the
    UTC default already set in config.py if it can't be read."""
    name = await run_in_threadpool(fetch_timezone_blocking)
    if name and config.set_timezone(name):
        logger.info("Using Home Assistant's time zone: %s", name)
    else:
        logger.warning(
            "Could not read Home Assistant's time zone — staying on the %s default.",
            config.timezone_name(),
        )
