"""Home Assistant Core API through the Supervisor proxy: persons (the people
list and home/away), the time zone, and — through ha_notify.py — notify
calls. Stdlib only; blocking calls are meant for a thread. Best effort."""
import json
import logging
import time
import urllib.error
import urllib.request

from . import config, db

logger = logging.getLogger("ha_client")
_warned_no_token = False


def has_token() -> bool:
    return bool(config.SUPERVISOR_TOKEN)


def warn_no_token_once(what: str) -> None:
    global _warned_no_token
    if not _warned_no_token:
        logger.warning("SUPERVISOR_TOKEN not set — skipping %s. Expected outside Home Assistant; inside HA it "
                       "means homeassistant_api isn't granted in config.yaml.", what)
        _warned_no_token = True


def request(method: str, path: str, body: dict | None = None, timeout: float = 10) -> tuple[int | None, bytes]:
    """One Core API call → (status, body). Status is None when there was no HTTP answer at all."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{config.SUPERVISOR_CORE_API}{path}", data=data, method=method, headers={
        "Authorization": f"Bearer {config.SUPERVISOR_TOKEN}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        try:
            detail = e.read() or b""
        except Exception:
            detail = b""
        return e.code, detail
    except Exception as e:
        logger.debug("HA request %s %s failed: %s", method, path, e)
        return None, b""


# ---------- states (persons), cached briefly: GET /states returns every entity ----------
_states_cache: tuple[float, list] | None = None


def fetch_states_blocking(max_age: float = 30) -> list | None:
    global _states_cache
    if _states_cache and time.monotonic() - _states_cache[0] < max_age:
        return _states_cache[1]
    if not has_token():
        return None
    status, body = request("GET", "/states")
    if status != 200:
        logger.warning("Couldn't read Home Assistant states (HTTP %s).", status)
        return None
    try:
        states = json.loads(body)
    except ValueError:
        return None
    if not isinstance(states, list):
        return None
    _states_cache = (time.monotonic(), states)
    return states


def person_entities(states: list) -> list:
    """[{entityId, name, state, lastChanged, userId}] for every person.* entity."""
    out = []
    for s in states or []:
        eid = str(s.get("entity_id", ""))
        if not eid.startswith("person."):
            continue
        attrs = s.get("attributes") or {}
        out.append({"entityId": eid, "name": attrs.get("friendly_name") or eid[7:], "state": s.get("state"),
                    "lastChanged": s.get("last_changed"), "userId": attrs.get("user_id")})
    return out


def sync_users(persons: list) -> int:
    """Add every HA person with a login to `users` — **disabled** — and keep the link current.
    Never enables, disables or deletes anyone."""
    now = config.now_iso()
    n = 0
    with db.get_conn() as conn:
        for p in persons:
            if not p.get("userId"):
                continue
            n += 1
            row = conn.execute("SELECT id FROM users WHERE id = ?", (p["userId"],)).fetchone()
            if row:
                conn.execute("UPDATE users SET ha_person = ? WHERE id = ?", (p["entityId"], p["userId"]))
            else:
                conn.execute("INSERT INTO users (id, name, ha_person, created_at) VALUES (?, ?, ?, ?)",
                             (p["userId"], str(p["name"])[:100], p["entityId"], now))
    return n


def sync_users_blocking() -> int:
    states = fetch_states_blocking(max_age=0)
    return 0 if states is None else sync_users(person_entities(states))


def load_time_zone_blocking() -> str | None:
    if not has_token():
        return None
    status, body = request("GET", "/config")
    if status != 200:
        return None
    try:
        name = json.loads(body).get("time_zone")
    except (ValueError, AttributeError):
        return None
    if name and config.set_time_zone(name):
        return name
    return None
