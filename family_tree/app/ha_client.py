"""Home Assistant Core API through the Supervisor proxy (SUPERVISOR_TOKEN):
the time zone, persons (the user list) and, through ha_notify.py, notify calls
for reminders. The client itself is the shared app/common/ha_client.py (re-exported
here); this module adds what only Family Tree does. Blocking calls are meant for a
thread. Everything is best effort."""
import logging

from . import config, db
from .common import ha_time
from .common.ha_client import fetch_persons_blocking, has_token, request, warn_no_token_once  # noqa: F401

logger = logging.getLogger("ha_client")


def _home_location(cfg: dict) -> None:
    try:                                              # home location, for sunrise and tithi days (§13.12)
        config.set_location(float(cfg["latitude"]), float(cfg["longitude"]))
    except (KeyError, TypeError, ValueError):
        pass


async def load_timezone() -> None:
    """At startup: Home Assistant's time zone and home location (GET /config); UTC if it can't be read."""
    await ha_time.load(config.ZONE, on_config=_home_location, log=logger)


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
