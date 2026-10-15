# Shared file: edit common/python/people_admin.py and run tools/sync_common.py; don't edit this copy. sha256=d6406f97249847461067e000b9d4f745319f37ba76df15ed971d4133b61a0a55
"""Admin → People: what every household app's people page does on the server (shared:
common/python/people_admin.py; the page is common/static/people.js).

The list of people, their access switch and each app's own columns stay in the app (its users table and
rules differ); these are the parts that are the same everywhere:

- `ha_person_json(user_id)` — what Home Assistant says about the person (Settings → People): whether HA's people
  could be read, the linked person and its phones, each with its notify action (or None when the Companion app
  hasn't registered one);
- `refresh_people(refresh)` — "Check Home Assistant again" (`?refresh=1`): read HA's people now (blocking; call it
  with no DB connection open);
- `notify_services()` — GET …/notify-services: the notify actions and entities HA has, or available=false and a
  readable error (still a 200, so the page falls back to typing a name);
- `clean_service(value)` — a notify service name as "notify.<name>", or a 422;
- `add_service(...)` / `remove_service(...)` — the person's extra notify services (the `user_notify` table), with
  the app's limit and wording;
- `TestLimiter` — at most one "Send a test" per person every few seconds (a 429), and `test_results(...)` /
  `require_one_sent(...)` for the answer.

Needs the app's common/ha_notify.py and common/ha_people.py (and FastAPI's HTTPException).
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional

from fastapi import HTTPException

from . import ha_notify, ha_people

DEFAULT_LIMIT_MESSAGE = "At most {limit} notify services per person."


def ha_person_json(user_id: str) -> dict:
    """What Home Assistant says about the person (Settings → People): their person and phones."""
    p = ha_people.person_for(user_id)
    return {"known": ha_people.known(), "person": p["entityId"] if p else None, "personName": p["name"] if p else None,
            "phones": [{"label": ph["label"], "service": f"notify.{ph['service']}" if ph["service"] else None,
                        "tracker": ph["tracker"]} for ph in (p or {}).get("phones", [])]}


def refresh_people(refresh: bool) -> None:
    """`?refresh=1` ("Check Home Assistant again"): read Home Assistant's people again first. Blocking."""
    if refresh:
        ha_people.refresh_blocking(True)


def notify_services(refresh: bool = False) -> dict:
    """What Home Assistant can notify (actions and entities, as "notify.<name>", sorted; cached 60 s, `refresh`
    skips the cache). If Home Assistant can't be asked: available=false and a readable error."""
    try:
        return {"available": True, "error": None, **ha_notify.list_notify_services_blocking(force=refresh)}
    except ha_notify.NotifyListError as e:
        return {"available": False, "error": f"Couldn't read the notify services from Home Assistant: {e}",
                "services": [], "entities": []}


def clean_service(value) -> str:
    """"notify.x" (or "x") → "notify.x"; anything else is a 422 with ha_notify's explanation."""
    try:
        return ha_notify.normalize_service(value)
    except ValueError as e:
        raise HTTPException(422, str(e))


def add_service(conn, user_id: str, service: str, who: Optional[str], now: str, *, limit: int,
                columns: tuple[str, str] = ("created_at", "created_by"),
                limit_message: str = DEFAULT_LIMIT_MESSAGE) -> list[str]:
    """Add one extra notify service (already cleaned) to the person; adding one they have is harmless.
    More than `limit` is a 422. Returns their services."""
    current = ha_notify.assigned_services(conn, {"id": user_id})
    if service not in current and len(current) >= limit:
        raise HTTPException(422, limit_message.format(limit=limit))
    at, by = columns
    conn.execute(f"INSERT OR IGNORE INTO user_notify (user_id, service, {at}, {by}) VALUES (?, ?, ?, ?)",
                 (user_id, service, now, who))
    return ha_notify.assigned_services(conn, {"id": user_id})


def remove_service(conn, user_id: str, service: str, not_found: str) -> list[str]:
    """Remove one extra notify service; one the person doesn't have is a 404 with `not_found`."""
    if not conn.execute("DELETE FROM user_notify WHERE user_id = ? AND service = ?", (user_id, service)).rowcount:
        raise HTTPException(404, not_found)
    return ha_notify.assigned_services(conn, {"id": user_id})


class TestLimiter:
    """One test notification per key every `interval` seconds (a 429 otherwise)."""

    def __init__(self, interval: float, message: str = "Please wait a few seconds before sending another test."):
        self.interval = interval
        self.message = message
        self.last: dict[str, float] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            last = self.last.get(key)
            if last is not None and now - last < self.interval:
                raise HTTPException(429, self.message)
            self.last[key] = now


def test_results(sent: dict[str, bool], *, as_list: bool = False):
    """ha_notify.send_to_services' {bare: ok} → {"notify.x": ok}, or a list of {service, ok, hint}."""
    if as_list:
        return [{"service": f"notify.{k}", "ok": v, "hint": "" if v else ha_notify.explain_failure(k)}
                for k, v in sent.items()]
    return {f"notify.{k}": v for k, v in sent.items()}


def require_one_sent(results: dict[str, bool], first_bare: str, has_token: Callable[[], bool]) -> None:
    """A 502 when Home Assistant accepted none of the tests (with its hint for the first service)."""
    if any(results.values()):
        return
    if not has_token():
        raise HTTPException(502, "This app can't reach Home Assistant (no Supervisor token).")
    hint = ha_notify.explain_failure(first_bare)
    raise HTTPException(502, "Home Assistant didn't accept the test notification"
                             + (f" — {hint}" if hint else " — check the service name."))
