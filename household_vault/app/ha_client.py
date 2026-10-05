"""Home Assistant Core API through the Supervisor proxy: the person list and,
through ha_notify.py, notify calls (alerts, emergency access, expiry
reminders). Also the optional guest Wi-Fi sensor (guest_wifi.py). The client
is the shared app/common/ha_client.py, re-exported here; this module adds the
user sync. Blocking calls are meant for a thread. Best effort."""
from . import config, db
from .common.ha_client import fetch_persons_blocking, has_token, request, warn_no_token_once  # noqa: F401


def sync_users(persons: list) -> int:
    """Add every HA person with a login to `users` — **disabled** (SPEC §4) — and keep names current.
    Never enables, disables or deletes anyone."""
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


def sync_users_blocking() -> int:
    persons = fetch_persons_blocking()
    return 0 if persons is None else sync_users(persons)
