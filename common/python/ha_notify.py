"""Push notifications through Home Assistant.

Shared by the household apps (common/python/ha_notify.py, copied into each
app's app/common/ by tools/sync_common.py). Uses the shared `ha_client`
(common/python/ha_client.py) and the app's own `db` module (`db.get_conn()`).

Who gets what: the phones Home Assistant links to the person (Settings →
People → Track device; read by ha_people.py, in the apps that have it),
plus any extra notify services an admin assigns under Admin → Users, stored
in `user_notify` as "notify.<name>", keyed by the HA user id.

Home Assistant has two ways to reach a phone, and an assigned service can
name either:

- a **notify action** (the older style), e.g. `mobile_app_annas_iphone`,
  called as POST /api/services/notify/mobile_app_annas_iphone; or
- a **notify entity** (the newer Companion-app style, one entity per device,
  e.g. `notify.annas_iphone`), which has no action of its own and is sent to
  with the generic `notify.send_message` action plus `entity_id`.

`send_notify` tries the action first and, if Home Assistant says there's no
such action (HTTP 400/404), retries via `notify.send_message` with the entity
of the same name. What worked is remembered per name.

`send_notify` never raises: it returns True on success, False otherwise.
Blocking — call it from a thread, and never while holding a DB connection.
"""
import difflib
import json
import logging
import re
import threading
import time

from . import ha_client

logger = logging.getLogger("ha_notify")

_SERVICE_RE = re.compile(r"^[a-z0-9_]+$")
FULL_SERVICE_RE = re.compile(r"^notify\.[a-z0-9_]+$")

# name -> True once it's known to be a notify ENTITY (send via notify.send_message)
_entity_mode: dict[str, bool] = {}


def _send_action(name: str, title: str, message: str, data: dict | None = None):
    body = {"title": title, "message": message}
    if data:
        body["data"] = data
    return ha_client.request("POST", f"/services/notify/{name}", body, timeout=10)


def _send_entity(name: str, title: str, message: str):
    # notify.send_message takes only entity_id/title/message — no `data`, so a
    # link travels in the message text only (SPEC §7.1)
    return ha_client.request(
        "POST", "/services/notify/send_message",
        {"entity_id": f"notify.{name}", "title": title, "message": message}, timeout=10,
    )


def send_notify(service: str, title: str, message: str, data: dict | None = None) -> bool:
    """`data` is the notify action's platform data — e.g. {"url": …,
    "clickAction": …} so tapping the notification opens a link (the iOS
    Companion app reads `url`, Android `clickAction`). Sent only on the
    notify-action path; notify entities can't take it."""
    if not service or not _SERVICE_RE.match(service):
        logger.warning("Refusing to call an invalid notify service name: %r", service)
        return False
    if not ha_client.has_token():
        ha_client.warn_no_token_once("Home Assistant notifications")
        return False

    if _entity_mode.get(service):
        status, body = _send_entity(service, title, message)
    else:
        status, body = _send_action(service, title, message, data)
        if status in (400, 404):
            # No action by that name — it may be a notify entity instead.
            known = available_notify_targets()
            if known is None or service in known[1]:
                e_status, e_body = _send_entity(service, title, message)
                if e_status in (200, 201):
                    if not _entity_mode.get(service):
                        logger.info("notify.%s is a notify entity — sending through notify.send_message", service)
                    _entity_mode[service] = True
                    return True
                if known is not None:          # it IS an entity, so its error is the relevant one
                    status, body = e_status, e_body
    if status in (200, 201):
        return True
    reason = _ha_message(body)
    hint = explain_failure(service) if status in (400, 404) else ""
    logger.warning("notify.%s failed (HTTP %s)%s%s", service, status,
                   f": {reason}" if reason else "", f" — {hint}" if hint else "")
    return False


def _ha_message(body: bytes) -> str:
    """Home Assistant's own error text from a failed call, if any."""
    if not body:
        return ""
    try:
        data = json.loads(body)
        if isinstance(data, dict) and data.get("message"):
            return str(data["message"])[:200]
    except ValueError:
        pass
    return body[:200].decode("utf-8", "replace").strip()


# --- what Home Assistant can actually notify ----------------------------------
# A name that matches neither a notify action nor a notify entity is by far
# the most common reason a reminder fails (HTTP 400). The same lookup feeds
# the Admin → Users service picker, the failure hints and the startup check.
# Cached briefly (and only on success) so the picker is fresh but HA isn't
# asked on every page view.
_TARGETS_TTL_SECONDS = 60
_targets_cache: tuple[float, tuple[set[str], set[str]]] | None = None
_targets_lock = threading.Lock()


class NotifyListError(RuntimeError):
    """Home Assistant's notify services couldn't be read — the message says why."""


def fetch_notify_targets_blocking(force: bool = False) -> tuple[set[str], set[str]]:
    """(notify action names, notify entity names) — both without the
    "notify." prefix. Actions come from GET /services (domain == "notify",
    minus the generic send_message, which needs an entity); entities are the
    notify.* entries in GET /states (best effort — an error there just means
    no entities). Raises NotifyListError if Home Assistant can't be asked.
    Cached for 60 s. Blocking; never call it while holding a DB connection."""
    global _targets_cache
    with _targets_lock:
        if not force and _targets_cache and time.monotonic() - _targets_cache[0] < _TARGETS_TTL_SECONDS:
            return _targets_cache[1]
    if not ha_client.has_token():
        raise NotifyListError("This app has no Supervisor token, so it can't ask Home Assistant "
                              "(expected when running outside Home Assistant).")
    status, body = ha_client.request("GET", "/services", timeout=10)
    if status is None:
        raise NotifyListError("Home Assistant didn't answer — it may be restarting. Try again in a moment.")
    if status != 200:
        raise NotifyListError(f"Home Assistant refused the request for its services (HTTP {status}).")
    try:
        actions = {str(n) for d in json.loads(body) if isinstance(d, dict) and d.get("domain") == "notify"
                   for n in (d.get("services") or {})}
    except (ValueError, TypeError, AttributeError):
        raise NotifyListError("Home Assistant sent a list of services this app couldn't read.") from None
    actions = {n for n in actions if _SERVICE_RE.match(n)} - {"send_message"}
    entities: set[str] = set()
    status, body = ha_client.request("GET", "/states", timeout=10)
    if status == 200:
        try:
            entities = {s["entity_id"][len("notify."):] for s in json.loads(body)
                        if str(s.get("entity_id", "")).startswith("notify.")}
            entities = {n for n in entities if _SERVICE_RE.match(n)}
        except (ValueError, TypeError, KeyError, AttributeError):
            entities = set()
    result = (actions, entities)
    with _targets_lock:
        _targets_cache = (time.monotonic(), result)
    return result


def available_notify_targets() -> tuple[set[str], set[str]] | None:
    """Like fetch_notify_targets_blocking, but None instead of an error."""
    try:
        return fetch_notify_targets_blocking()
    except NotifyListError:
        return None


def list_notify_services_blocking(force: bool = False) -> dict:
    """For the Admin → Users picker: {"services": ["notify.x", ...],
    "entities": ["notify.y", ...]}, each sorted. Raises NotifyListError."""
    actions, entities = fetch_notify_targets_blocking(force)
    return {
        "services": sorted(f"notify.{n}" for n in actions),
        "entities": sorted(f"notify.{n}" for n in entities - actions),
    }


def available_notify_services() -> set[str] | None:
    """Every name send_notify can reach (actions and entities)."""
    known = available_notify_targets()
    return None if known is None else known[0] | known[1]


def explain_failure(service: str) -> str:
    """A plain-language hint when `service` is neither a notify action nor a
    notify entity (with the closest real names), or "" if it exists / can't
    tell."""
    names = available_notify_services()
    if names is None or service in names:
        return ""
    ordered = sorted(names - {"persistent_notification"})   # generic, not a device
    suggestions = [n for n in ordered if n.endswith(service) or service in n or n in service]
    suggestions += [n for n in difflib.get_close_matches(service, ordered, n=3, cutoff=0.5) if n not in suggestions]
    phones = [n for n in ordered if n.startswith("mobile_app_") or "phone" in n]
    msg = f"Home Assistant has no notify action or notify entity called notify.{service}"
    if suggestions:
        msg += "; did you mean " + " or ".join(f"notify.{n}" for n in suggestions[:3]) + "?"
    elif phones:
        msg += "; phone targets it does have: " + ", ".join(f"notify.{n}" for n in phones[:8])
    return msg + (" Fix the person's service under Admin → Users in the app (Developer Tools → Actions lists "
                  "notify actions; Settings → Entities, filtered to notify, lists notify entities).")


def check_targets_blocking() -> int:
    """Startup check: warn once about every assigned service Home Assistant
    can't deliver to, before anyone's reminder silently fails, and note which
    ones are notify entities. Returns how many were flagged. Best effort —
    says nothing if HA can't be asked. Reads the DB first and closes it
    before asking Home Assistant."""
    from .. import db
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT u.name AS who, n.service FROM user_notify n JOIN users u ON u.id = n.user_id"
        ).fetchall()
    if not rows:
        return 0
    known = available_notify_targets()
    if known is None:
        return 0
    actions, entities = known
    bad = 0
    for r in sorted(rows, key=lambda r: (r["who"], r["service"])):
        who, service = r["who"], bare(r["service"])
        if service in actions:
            continue
        if service in entities:
            _entity_mode[service] = True
            logger.info("Notify service for %r is the notify entity notify.%s — "
                        "reminders go through notify.send_message", who, service)
            continue
        bad += 1
        logger.warning("Notify service for %r won't work: %s", who, explain_failure(service))
    return bad


# --- per-user assignments (user_notify) ----------------------------------------

def normalize_service(value) -> str:
    """"notify.mobile_app_x" (or just "mobile_app_x") -> "notify.mobile_app_x".
    Raises ValueError unless it is notify. + [a-z0-9_]+ — the name goes into a
    URL path, so nothing else is ever accepted."""
    if not isinstance(value, str):
        raise ValueError("A notify service must be text like notify.mobile_app_phone.")
    v = value.strip()
    if "." not in v:
        v = "notify." + v
    if not FULL_SERVICE_RE.match(v):
        raise ValueError(f"{value!r} isn't a notify service name — it must look like notify.mobile_app_phone "
                         "(lower-case letters, digits and _ after “notify.”).")
    return v


def bare(service: str) -> str:
    """"notify.x" -> "x" (what send_notify takes)."""
    return service[len("notify."):] if service.startswith("notify.") else service


def assigned_services(conn, user: dict) -> list[str]:
    """The person's services as "notify.<name>", sorted — matched by HA user
    id only (never a name a person could edit)."""
    if not user.get("id"):
        return []
    return sorted(r["service"] for r in conn.execute(
        "SELECT service FROM user_notify WHERE user_id = ?", (user["id"],)))


def ha_phones(user: dict) -> list[str]:
    """Bare notify names of the person's phones in Home Assistant (empty in apps without ha_people)."""
    try:
        from . import ha_people
    except ImportError:
        return []
    try:
        return ha_people.phones_for(user.get("id"))
    except Exception:
        logger.exception("Reading the person's phones from Home Assistant failed")
        return []


def services_for(user: dict, conn=None) -> list[str]:
    """Bare names (what send_notify takes) of everything that reaches `user`
    (a row/dict with id and username): their phones from Home Assistant, then
    the extra services assigned in the app, without duplicates."""
    if conn is not None:
        extra = [bare(s) for s in assigned_services(conn, user)]
    else:
        from .. import db
        with db.get_conn() as c:
            extra = [bare(s) for s in assigned_services(c, user)]
    return list(dict.fromkeys(ha_phones(user) + extra))


def send_to_services(services: list[str], title: str, message: str, data: dict | None = None) -> dict[str, bool]:
    """Send to each service (bare names); {service: ok}. Blocking."""
    return {svc: send_notify(svc, title, message, data) for svc in services}
