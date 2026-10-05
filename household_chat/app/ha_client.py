"""Home Assistant Core API through the Supervisor proxy: persons (the people
list and home/away), the time zone, and — through ha_notify.py — notify
calls. The client (and its brief GET /states cache) is the shared
app/common/ha_client.py, re-exported here; this module adds what only Chat
does. Blocking calls are meant for a thread. Best effort."""
from . import config, db
from .common import ha_time
from .common.ha_client import fetch_states_blocking, has_token, request, warn_no_token_once  # noqa: F401


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
    """Home Assistant's time zone (GET /config), applied to config; its name, or None."""
    return ha_time.load_blocking(config.ZONE)
