"""Home / away next to names (SPEC §15.3) and online status (§8).

Home/away comes from each person's `person.*` entity: the one linked to their
Home Assistant login (users.ha_person, kept by ha_client.sync_users), unless an
admin chose a different one in People → 🏠 (users.presence_entity, an override).
The same entity's picture is their photo (avatars.py). One GET /states a
minute, only while someone has the app open; changes go out as `presence` events.
"""
import logging
import threading

from . import db, ha_client, settings
from .live import hub

logger = logging.getLogger("presence")
_lock = threading.Lock()
_cache: dict = {}          # user id → {"state", "label", "since"}
_missing: set = set()      # user ids whose person entity no longer exists

# The person.* entity used for someone's home/away and photo: an admin's choice, else their login's person.
ENTITY_SQL = "COALESCE(presence_entity, ha_person)"


def entity_of(row) -> str | None:
    """The same as ENTITY_SQL, for a users row."""
    return row["presence_entity"] or row["ha_person"]


def label(state: str | None) -> str | None:
    if not state or state in ("unknown", "unavailable"):
        return None
    if state == "home":
        return "🏠 Home"
    if state == "not_home":
        return "Away"
    return f"📍 {state}"


def refresh_blocking(force: bool = False) -> bool:
    """→ True if anything changed (and was published)."""
    if not settings.get("show_presence"):
        with _lock:
            had = bool(_cache)
            _cache.clear()
        if had:
            hub.publish_all("presence", {"homeAway": {}})
        return had
    if not force and not hub.connected_users():
        return False
    with db.get_conn() as conn:
        assigned = {r["id"]: r["entity"] for r in conn.execute(
            f"SELECT id, {ENTITY_SQL} AS entity FROM users WHERE {ENTITY_SQL} IS NOT NULL AND disabled = 0")}
    if not assigned:
        with _lock:
            changed = bool(_cache)
            _cache.clear()
        return changed
    states = ha_client.fetch_states_blocking(max_age=45)
    if states is None:
        return False
    by_entity = {p["entityId"]: p for p in ha_client.person_entities(states)}
    new, missing = {}, set()
    for uid, eid in assigned.items():
        p = by_entity.get(eid)
        if p is None:
            missing.add(uid)
            continue
        lab = label(p["state"])
        if lab:
            new[uid] = {"state": p["state"], "label": lab, "since": p["lastChanged"]}
    with _lock:
        changed = new != _cache
        _cache.clear()
        _cache.update(new)
        _missing.clear()
        _missing.update(missing)
    if changed:
        hub.publish_all("presence", {"homeAway": new})
    return changed


def home_away() -> dict:
    if not settings.get("show_presence"):
        return {}
    with _lock:
        return dict(_cache)


def missing() -> set:
    with _lock:
        return set(_missing)


def online_map(conn) -> dict:
    """user id → True (online) / last seen, for people who don't hide it."""
    out = {}
    for r in conn.execute("SELECT id, last_seen, hide_online FROM users WHERE disabled = 0"):
        if r["hide_online"]:
            continue
        out[r["id"]] = {"online": hub.is_online(r["id"]), "lastSeen": r["last_seen"]}
    return out


def reset() -> None:
    with _lock:
        _cache.clear()
        _missing.clear()
