"""Publishes ONLY a per-user daily-calorie-total sensor to Home Assistant.
Nothing else (no macros, no weight, no food names) ever leaves this app
towards Home Assistant's entity system — that's a deliberate privacy
boundary, not just a default.

Uses the Supervisor's Core API proxy (http://supervisor/core/api/), which
requires `homeassistant_api: true` in config.yaml and the auto-injected
SUPERVISOR_TOKEN. No extra HTTP dependency — stdlib urllib, off the event
loop via the threadpool.
"""
import json
import logging
import urllib.error
import urllib.request

from starlette.concurrency import run_in_threadpool

from . import config, settings

logger = logging.getLogger("ha_sync")

_warned_no_token = False
# Entity ids pushed successfully by this process — so turning the sensor
# switch off also removes one whose user was renamed since (new slug).
_published: set[str] = set()
# Set when the switch was turned off but some DELETEs failed (HA down); the
# periodic job retries them.
_removal_pending = False


def exposed() -> bool:
    """Admin → App settings → "Publish daily calories to Home Assistant",
    read live on every push."""
    return bool(settings.get("expose_daily_calories_sensor"))


def entity_id_for(user_slug: str) -> str:
    return f"sensor.calorie_tracker_{user_slug}_daily_calories"


def _has_token() -> bool:
    global _warned_no_token
    if config.SUPERVISOR_TOKEN:
        return True
    if not _warned_no_token:
        logger.warning(
            "SUPERVISOR_TOKEN not set — skipping HA sensor sync. This is "
            "expected outside of Home Assistant; inside HA it means "
            "homeassistant_api isn't granted in config.yaml."
        )
        _warned_no_token = True
    return False


def _request(method: str, entity_id: str, payload: dict | None = None) -> int | None:
    """One Core API call for `entity_id`; returns the HTTP status, or None if
    HA couldn't be reached. Blocking."""
    url = f"{config.SUPERVISOR_CORE_API}/states/{entity_id}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {config.SUPERVISOR_TOKEN}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception as e:
        logger.warning("HA sensor sync (%s %s) failed: %s", method, entity_id, e)
        return None


def _sync_push_state(entity_id: str, state_value, attributes: dict) -> bool:
    if not _has_token():
        return False
    status = _request("POST", entity_id, {"state": state_value, "attributes": attributes})
    if status is not None and 200 <= status < 300:
        _published.add(entity_id)
        return True
    if status is not None:
        logger.warning("HA sensor sync failed for %s: HTTP %s", entity_id, status)
    return False


def _sync_delete_state(entity_id: str) -> bool:
    """DELETE /api/states/<entity_id>. 404 (never published / already gone)
    counts as removed."""
    status = _request("DELETE", entity_id)
    if status is not None and (200 <= status < 300 or status == 404):
        _published.discard(entity_id)
        return True
    if status is not None:
        logger.warning("Could not remove %s from Home Assistant: HTTP %s", entity_id, status)
    return False


def _payload(display_name: str, calories: float) -> tuple:
    attributes = {
        "unit_of_measurement": "kcal",
        "friendly_name": f"{display_name} Daily Calories",
        "icon": "mdi:food-apple",
    }
    return round(calories, 1), attributes


async def push_daily_calories(user_slug: str, display_name: str, calories: float):
    if not exposed():
        return
    state, attributes = _payload(display_name, calories)
    await run_in_threadpool(_sync_push_state, entity_id_for(user_slug), state, attributes)


def _today_totals(user_id: str | None = None) -> list[dict]:
    """[{id, name, total}] for every user (or one), today's calories. Opens
    and closes its own connection — callers do their network calls after."""
    from . import db  # local import to avoid a circular import at module load

    today = str(config.today())
    where, args = ("WHERE u.id = ?", (today, user_id)) if user_id else ("", (today,))
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT u.id, u.name, COALESCE(SUM(f.calories), 0) AS total FROM users u "
            "LEFT JOIN food_logs f ON f.user_id = u.id AND f.date = ? "
            f"{where} GROUP BY u.id ORDER BY u.name",
            args,
        ).fetchall()
    return [dict(r) for r in rows]


async def push_for_user(user_id: str, user_slug: str, display_name: str):
    """Compute today's calorie total for a user from the DB and push it."""
    if not exposed():
        return
    rows = _today_totals(user_id)
    total = rows[0]["total"] if rows else 0.0
    await push_daily_calories(user_slug, display_name, total)


def _sync_push_all() -> dict:
    from .auth import _slugify  # local: auth imports db/config, keep load order simple

    result = {"pushed": 0, "failed": 0}
    if not exposed() or not _has_token():
        return result
    for u in _today_totals():               # DB connection closed before any HTTP call
        state, attributes = _payload(u["name"], u["total"])
        if _sync_push_state(entity_id_for(_slugify(u["name"])), state, attributes):
            result["pushed"] += 1
        else:
            result["failed"] += 1
    return result


async def push_all() -> dict:
    """Push every known user's sensor (disabled users included). Used by the
    periodic job and right after the switch is turned on."""
    return await run_in_threadpool(_sync_push_all)


def _sync_remove_all() -> dict:
    """Switch turned off: best-effort DELETE of every sensor this app
    publishes (each known user's current slug, plus anything this process
    pushed under an older name). Failures are retried by the periodic job."""
    global _removal_pending
    from . import db
    from .auth import _slugify

    result = {"removed": 0, "failed": 0}
    if exposed() or not _has_token():
        return result
    with db.get_conn() as conn:
        names = [r["name"] for r in conn.execute("SELECT name FROM users")]
    entity_ids = sorted({entity_id_for(_slugify(n)) for n in names} | _published)
    for eid in entity_ids:
        if _sync_delete_state(eid):
            result["removed"] += 1
        else:
            result["failed"] += 1
    _removal_pending = result["failed"] > 0
    return result


async def remove_all() -> dict:
    return await run_in_threadpool(_sync_remove_all)


async def apply_exposure_change() -> dict:
    """Called right after expose_daily_calories_sensor changed in App
    settings: on → publish everyone now; off → remove the sensors now."""
    global _removal_pending
    if exposed():
        _removal_pending = False
        return await push_all()
    return await remove_all()


async def periodic_sync() -> dict:
    """One tick of main.py's periodic job: on → push everyone (so the value
    rolls over to 0 after midnight); off → retry a removal that failed."""
    if exposed():
        return await push_all()
    if _removal_pending:
        return await remove_all()
    return {}


# ---------------------------------------------------------------------------
# Home Assistant's time zone (config.py owns the resulting state; see there)
# ---------------------------------------------------------------------------

def fetch_timezone_blocking() -> str | None:
    """Home Assistant's configured time zone name (GET /config), or None if
    it can't be read. Blocking — call via run_in_threadpool."""
    if not config.SUPERVISOR_TOKEN:
        return None
    req = urllib.request.Request(
        f"{config.SUPERVISOR_CORE_API}/config",
        headers={"Authorization": f"Bearer {config.SUPERVISOR_TOKEN}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read()
    except urllib.error.HTTPError as e:
        logger.warning("Could not read Home Assistant's time zone: HTTP %s", e.code)
        return None
    except Exception as e:
        logger.warning("Could not read Home Assistant's time zone: %s", e)
        return None
    try:
        return json.loads(body).get("time_zone")
    except (ValueError, AttributeError):
        return None


async def load_timezone() -> None:
    """At startup: fetch HA's time zone once and apply it; fall back to the
    UTC default already set in config.py if it can't be read."""
    name = await run_in_threadpool(fetch_timezone_blocking)
    if name and config.set_timezone(name):
        logger.info("Using Home Assistant's time zone: %s", name)
    else:
        logger.warning(
            "Could not read Home Assistant's time zone — staying on the %s default.",
            config.timezone_name(),
        )

