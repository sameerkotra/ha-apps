"""Home Assistant Core API through the Supervisor proxy (SUPERVISOR_TOKEN):
the time zone, persons (the user list) and, through ha_notify.py, notify calls
for reminders. Stdlib only; blocking calls are meant for a thread. Everything
is best effort."""
import json
import logging
import urllib.error
import urllib.request

from starlette.concurrency import run_in_threadpool

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
    """One Core API call → (status, body). Status is None when there was no HTTP
    answer at all. On an HTTP error the body is Home Assistant's own explanation
    (e.g. {"message": "Service notify.x not found."})."""
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


def fetch_timezone_blocking() -> str | None:
    if not config.SUPERVISOR_TOKEN:
        return None
    status, body = request("GET", "/config")
    if status != 200:
        return None
    try:
        cfg = json.loads(body)
    except ValueError:
        return None
    if not isinstance(cfg, dict):
        return None
    try:                                              # home location, for sunrise and tithi days (§13.12)
        config.set_location(float(cfg["latitude"]), float(cfg["longitude"]))
    except (KeyError, TypeError, ValueError):
        pass
    return cfg.get("time_zone")


async def load_timezone() -> None:
    name = await run_in_threadpool(fetch_timezone_blocking)
    if name and config.set_timezone(name):
        logger.info("Using Home Assistant's time zone: %s", name)
    else:
        logger.warning("Could not read Home Assistant's time zone; using %s.", config.timezone_name())


def fetch_persons_blocking() -> list[dict] | None:
    """[{user_id, name, entity_id}] for every person.* linked to an HA user."""
    if not config.SUPERVISOR_TOKEN:
        return None
    status, body = request("GET", "/states")
    if status != 200:
        logger.warning("Couldn't read Home Assistant persons (HTTP %s).", status)
        return None
    try:
        states = json.loads(body)
    except ValueError:
        return None
    out = []
    for s in states:
        eid = s.get("entity_id", "")
        attrs = s.get("attributes") or {}
        if eid.startswith("person.") and attrs.get("user_id"):
            out.append({"user_id": attrs["user_id"], "name": attrs.get("friendly_name") or eid[7:], "entity_id": eid})
    return out


def sync_users_blocking() -> int:
    """Add every HA person with a login to `users` (enabled by default) and
    keep names current. Never disables anyone — only admins do that."""
    persons = fetch_persons_blocking()
    if persons is None:
        return 0
    now = config.now_iso()
    with db.get_conn() as conn:
        for p in persons:
            row = conn.execute("SELECT id FROM users WHERE id = ?", (p["user_id"],)).fetchone()
            if row:
                conn.execute("UPDATE users SET ha_person = ?, name = COALESCE(NULLIF(name, ''), ?) WHERE id = ?",
                             (p["entity_id"], p["name"], p["user_id"]))
            else:
                conn.execute("INSERT INTO users (id, name, ha_person, created_at) VALUES (?, ?, ?, ?)",
                             (p["user_id"], p["name"], p["entity_id"], now))
    return len(persons)
