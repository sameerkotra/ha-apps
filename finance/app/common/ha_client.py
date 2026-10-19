# Shared file: edit common/python/ha_client.py and run tools/sync_common.py; don't edit this copy. sha256=3483fab4155ebdfd9615667665dde0845d42a1bf61abdb44f72cf57e4f6519f6
"""Home Assistant's Core API through the Supervisor proxy (shared by the household apps:
common/python/ha_client.py, copied into each app's app/common/ by tools/sync_common.py).

`http://supervisor/core/api/` with the auto-injected SUPERVISOR_TOKEN (the app's config.yaml grants
`homeassistant_api: true`). Standard library only. Every call is blocking — run it off the event
loop (`run_in_threadpool(...)` from async code, or inside a sync route / BackgroundTask) and never
while holding a DB connection. Everything here is best effort: it returns a result instead of
raising, and logs at most one "no token" warning for the life of the process.

Uses the app's own `config` module, read at call time (so tests can point it at a fake Home
Assistant): `config.SUPERVISOR_CORE_API` (base URL, no trailing slash) and `config.SUPERVISOR_TOKEN`.
An app without those (Splitpot, Receipt) passes `base_url=` and `token=` to every call instead.

    status, body = ha_client.request("GET", "/config")     # (200, b'{...}'); (None, b"") = no answer
    ha_client.post_state("sensor.x", "3", {...})           # True / False
    ha_client.fetch_states_blocking()                      # every entity (cached 30 s), or None
    ha_client.fetch_config_blocking()                      # GET /config as a dict, or None

The apps keep their own `app/ha_client.py` for what only they do (syncing people into their own
`users` table, …); it re-exports these functions, so `from . import ha_client` keeps working there.
"""
import json
import logging
import time
import urllib.error
import urllib.request

logger = logging.getLogger("ha_client")

_warned_no_token = False


def _config():
    from .. import config          # the app's own config (imported on first use: not every app has one)
    return config


def _token(token: str | None) -> str:
    return _config().SUPERVISOR_TOKEN if token is None else (token or "")


def has_token() -> bool:
    """True when the Supervisor gave the app a token for the Core API proxy."""
    return bool(_config().SUPERVISOR_TOKEN)


def warn_no_token_once(what: str) -> None:
    global _warned_no_token
    if not _warned_no_token:
        logger.warning("SUPERVISOR_TOKEN not set — skipping %s. Expected outside Home Assistant; inside HA it "
                       "means homeassistant_api isn't granted in config.yaml.", what)
        _warned_no_token = True


def request(method: str, path: str, body=None, timeout: float = 10, *, base_url: str | None = None,
            token: str | None = None, on_error=None) -> tuple[int | None, bytes]:
    """One Core API call → (status, body). Status is None when there was no HTTP answer at all
    (network error, timeout). On an HTTP error the body is Home Assistant's own explanation
    (e.g. {"message": "Service notify.x not found."}).

    `on_error(exc)` is called with the exception when there was no answer (instead of the debug
    log line); it may log, or raise to pass the failure on."""
    url = f"{_config().SUPERVISOR_CORE_API if base_url is None else base_url}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {_token(token)}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        try:
            detail = e.read() or b""
        except Exception:
            detail = b""
        return e.code, detail
    except Exception as e:  # URLError, timeout, connection reset, ...
        if on_error is not None:
            on_error(e)
        else:
            logger.debug("HA request %s %s failed: %s", method, path, e)
        return None, b""


# ---------- states ----------

def post_state(entity_id: str, state: str, attributes: dict) -> bool:
    """Create/update an entity through POST /states/<entity_id>."""
    if not has_token():
        warn_no_token_once("Home Assistant sensor sync")
        return False
    status, _ = request("POST", f"/states/{entity_id}", {"state": state, "attributes": attributes})
    return status in (200, 201)


def delete_state(entity_id: str) -> bool:
    """Remove an entity. A 404 (already gone) counts as success."""
    if not has_token():
        warn_no_token_once("Home Assistant sensor sync")
        return False
    status, _ = request("DELETE", f"/states/{entity_id}")
    return status in (200, 204, 404)


# GET /states returns every entity in Home Assistant, so it is cached briefly.
_states_cache: tuple[float, list] | None = None


def fetch_states_blocking(max_age: float = 30, what: str = "states") -> list | None:
    """Every entity's state (GET /states), at most `max_age` seconds old; None if Home Assistant
    can't be asked (no token, an error, not a list). `what` names them in the warning."""
    global _states_cache
    if _states_cache and time.monotonic() - _states_cache[0] < max_age:
        return _states_cache[1]
    if not has_token():
        return None
    status, body = request("GET", "/states")
    if status != 200:
        logger.warning("Couldn't read Home Assistant %s (HTTP %s).", what, status)
        return None
    try:
        states = json.loads(body)
    except ValueError:
        return None
    if not isinstance(states, list):
        return None
    _states_cache = (time.monotonic(), states)
    return states


# Only the people, rendered by Home Assistant itself: a few hundred bytes instead of every entity's state.
# (An object with a named key, so an answer meant for another template can't be mistaken for it.)
PERSONS_TEMPLATE = """{"persons": [
{%- for p in states.person -%}
{{ {'entity_id': p.entity_id, 'name': p.name, 'user_id': p.attributes.get('user_id')} | to_json }}{{ ',' if not loop.last }}
{%- endfor -%}
]}"""


def _persons_from_template() -> list | None:
    """[{user_id, name, entity_id}] from POST /template, or None if that didn't work (an error or an odd answer)."""
    status, body = request("POST", "/template", {"template": PERSONS_TEMPLATE})
    if status != 200:
        return None
    try:
        rows = json.loads(body)
    except ValueError:
        return None
    rows = rows.get("persons") if isinstance(rows, dict) else None
    if not isinstance(rows, list):
        return None
    out = []
    for r in rows:
        if not isinstance(r, dict) or not isinstance(r.get("entity_id"), str) or not r["entity_id"].startswith("person."):
            return None
        if r.get("user_id"):
            out.append({"user_id": r["user_id"], "name": r.get("name") or r["entity_id"][7:], "entity_id": r["entity_id"]})
    return out


def fetch_persons_blocking(max_age: float = 0) -> list | None:
    """[{user_id, name, entity_id}] for every person.* linked to a Home Assistant login (always fresh).
    Asks Home Assistant to render just the people (POST /template) rather than reading GET /states —
    every entity it has, often megabytes — on every sync. If the template can't be used, it falls back
    to GET /states, at most `max_age` seconds old (the shared states cache; 0: fresh)."""
    if not has_token():
        return None
    persons = _persons_from_template()
    if persons is not None:
        return persons
    states = fetch_states_blocking(max_age=max_age, what="persons")
    if states is None:
        return None
    out = []
    for s in states:
        eid = s.get("entity_id", "")
        attrs = s.get("attributes") or {}
        if eid.startswith("person.") and attrs.get("user_id"):
            out.append({"user_id": attrs["user_id"], "name": attrs.get("friendly_name") or eid[7:], "entity_id": eid})
    return out


# ---------- configuration (time zone, currency, home location: see ha_time.py) ----------

def fetch_config_blocking(*, base_url: str | None = None, token: str | None = None, timeout: float = 10,
                          log=None) -> dict | None:
    """Home Assistant's configuration (GET /config: time_zone, currency, latitude, longitude, unit_system,
    …) as a dict, or None. Quiet on failure unless `log` (a logger) is given: then why it failed is
    logged there as a warning."""
    if not (has_token() if token is None else token):
        return None

    def failed(e):
        if log is not None:
            log.warning("Could not read Home Assistant's time zone: %s", e)

    status, body = request("GET", "/config", timeout=timeout, base_url=base_url, token=token, on_error=failed)
    if status is None:
        return None
    if status != 200:
        if log is not None:
            log.warning("Could not read Home Assistant's time zone: HTTP %s", status)
        return None
    try:
        data = json.loads(body)
    except ValueError as e:
        failed(e)
        return None
    return data if isinstance(data, dict) else None
