"""Home Assistant's Core API through the Supervisor proxy. The client is the shared app/common/ha_client.py,
re-exported here; this module adds what only Household Docs does: adding Home Assistant's people (persons with
a login) to `users`, so they can be shared with before they first open the app. Blocking — call from a thread."""
import logging

from . import config, db, settings
from .common import ha_people, ha_time
from .common.ha_client import fetch_states_blocking, has_token, request, warn_no_token_once  # noqa: F401

logger = logging.getLogger("ha_client")


def sync_people(people: list | None = None) -> int:
    """Add every Home Assistant person with a login to `users` (access per `new_people_access`) and keep the
    person link current. Never removes anyone or changes anyone's access."""
    people = ha_people.people() if people is None else people
    now = config.now_iso()
    n = 0
    with db.get_conn() as conn:
        access = settings.get("new_people_access", conn)
        for p in people:
            uid = p.get("userId")
            if not uid:
                continue
            n += 1
            row = conn.execute("SELECT id FROM users WHERE id = ?", (uid,)).fetchone()
            if row:
                conn.execute("UPDATE users SET ha_person = ? WHERE id = ?", (p.get("entityId"), uid))
            else:
                conn.execute("INSERT INTO users (id, name, ha_person, disabled, created_at) VALUES (?, ?, ?, ?, ?)",
                             (uid, str(p.get("name") or "Someone")[:100], p.get("entityId"), 0 if access else 1, now))
    return n


def refresh_people_blocking() -> int:
    ha_people.refresh_blocking(True)
    return sync_people()


def load_time_zone_blocking() -> str | None:
    """HA's time zone, and its currency (for sheets) from the same GET /config; kept current
    (ha_time.start_blocking, which logs the zone in use)."""
    return ha_time.start_blocking(config.ZONE, on_config=lambda cfg: config.set_currency(cfg.get("currency")),
                                  log=logger)
