"""Notifications through Home Assistant: security alerts, emergency
access and expiry reminders. Never any secret — a title and a short message.

Who gets what: the person's phones from Home Assistant (Settings →
People → Track device, read by ha_people.py) — only while the person is
enabled and set up (active) in Household Vault — plus any extra notify
services an admin assigned (Admin → People → 🔔, `user_notify`). Security
alerts and expiry reminders can each be turned off by the person (Settings →
Notifications); emergency-access messages always go.

`send()` looks the services up with the caller's DB connection and delivers
from a background thread, so a slow Home Assistant never holds up a request.
Tests replace `deliver`.
"""
import logging
import threading
import time

from . import ha_notify

logger = logging.getLogger("alerts")
KINDS = ("security", "emergency", "expiry", "admin")
PREF_COLUMN = {"security": "security_alerts", "expiry": "expiry_alerts"}
TITLE = "Household Vault"

_throttle: dict = {}
_throttle_lock = threading.Lock()


def _deliver_thread(services: list, title: str, message: str) -> None:
    def run():
        try:
            ha_notify.send_to_services(services, title, message)
        except Exception:
            logger.exception("Sending a notification failed")
    threading.Thread(target=run, daemon=True, name="notify").start()


deliver = _deliver_thread


def reachable(row) -> bool:
    """Do Home Assistant's phones count for this person? Only once they're enabled and set up here."""
    return row is not None and not row["disabled"] and row["status"] == "active"


def services_for(conn, row) -> list[str]:
    """Bare notify names that reach this person (a `users` row): their phones from Home Assistant while
    they're active here, then the extra services an admin assigned."""
    if row is None:
        return []
    if reachable(row):
        return ha_notify.services_for({"id": row["id"]}, conn)
    return [ha_notify.bare(s) for s in ha_notify.assigned_services(conn, {"id": row["id"]})]


def send(conn, user_ids, message: str, kind: str = "security", title: str = TITLE,
         throttle_key: str | None = None, throttle_seconds: int = 0) -> int:
    """Notify each person (by HA user id) who has a service and hasn't turned this kind off.
    Returns how many people it went to."""
    if kind not in KINDS:
        raise ValueError(kind)
    if throttle_key:
        now = time.time()
        with _throttle_lock:
            if now - _throttle.get(throttle_key, 0) < throttle_seconds:
                return 0
            _throttle[throttle_key] = now
    n = 0
    for uid in dict.fromkeys(u for u in user_ids if u):
        row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        if row is None:
            continue
        col = PREF_COLUMN.get(kind)
        if col and not row[col]:
            continue
        services = services_for(conn, row)
        if not services:
            continue
        deliver(services, title, message)
        n += 1
    return n


def others_in(conn, vault_id: str, except_user: str) -> list:
    """The other members of a vault."""
    return [r["user_id"] for r in conn.execute("SELECT user_id FROM vault_members WHERE vault_id = ? AND user_id != ?",
                                               (vault_id, except_user))]


def name_of(conn, user_id: str | None) -> str:
    if not user_id:
        return "Someone"
    r = conn.execute("SELECT name FROM users WHERE id = ?", (user_id,)).fetchone()
    return r["name"] if r else "Someone"


def vault_label(conn, vault_id: str) -> str:
    v = conn.execute("SELECT kind, name, owner_user_id FROM vaults WHERE id = ?", (vault_id,)).fetchone()
    if v is None:
        return "a vault"
    if v["kind"] == "personal":
        return f"{name_of(conn, v['owner_user_id'])}'s Personal vault"
    if v["kind"] == "emergency":
        return f"{name_of(conn, v['owner_user_id'])}'s emergency items"
    return v["name"]
