# Shared file: edit common/python/ha_people.py and run tools/sync_common.py; don't edit this copy. sha256=8559fe254423b19b5f0d7da20b4937fdf9ddda805ae5028a890adb7c57f4279a
"""People and their phones from Home Assistant (shared by the household apps: common/python/ha_people.py).

Home Assistant is the one place a household member and their phone are set up: **Settings → People →
(a person)** — "Allow person to login" links the person to their Home Assistant user, and "Track device"
picks the phone(s) running the Companion app. From that, every app works out on its own:

- which phones to notify — each tracked Companion-app device's own notify action,
  `notify.mobile_app_<device name>` (checked against the actions Home Assistant really has);
- the person's name, picture and home/away state, for the apps that show them.

So a new phone (or a lost one removed) is set up once, in Home Assistant, and every app follows within
a few minutes. What stays per app: whether the person may use it at all (an app with an "Enable"
step still needs it), its admins, and any *extra* notify targets an admin adds there (a speaker, a second
service), which are sent to as well.

One call: POST /api/template renders the people and their Companion-app devices as JSON (HA's
device registry isn't on the REST API; the template functions `device_id` / `device_attr` reach it).
Refreshed at start-up and every few minutes by the app's background loop (`refresh_blocking`, blocking —
from a thread, never while holding a DB connection); if Home Assistant can't be asked, the last good answer
is kept. Everything else here only reads that cached answer, so it's safe anywhere.
"""
import json
import logging
import re
import threading
import time
import unicodedata

from . import ha_client

logger = logging.getLogger("ha_people")

MAX_AGE_S = 300
_TEMPLATE = """[
{%- for p in states.person -%}
{%- set ns = namespace(phones=[]) -%}
{%- for t in (p.attributes.get('device_trackers') or []) -%}
{%- set d = device_id(t) -%}
{%- if d -%}
{%- for i in (device_attr(d, 'identifiers') or []) -%}
{%- if i[0] == 'mobile_app' -%}
{%- set ns.phones = ns.phones + [{'name': device_attr(d, 'name'), 'label': device_attr(d, 'name_by_user') or device_attr(d, 'name'), 'tracker': t}] -%}
{%- endif -%}
{%- endfor -%}
{%- endif -%}
{%- endfor -%}
{{ {'entity_id': p.entity_id, 'name': p.name, 'user_id': p.attributes.get('user_id'), 'state': p.state,
    'picture': p.attributes.get('entity_picture'), 'phones': ns.phones} | to_json }}{{ ',' if not loop.last }}
{%- endfor -%}
]"""

_lock = threading.Lock()
_cache = {"at": 0.0, "people": None}          # people: list of dicts, or None if never read


def slugify(text: str) -> str:
    """Home Assistant's slugify, for the names it gives mobile_app notify actions."""
    t = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "_", t).strip("_")


def _notify_name(device_name: str, actions: set | None) -> str | None:
    """The phone's notify action: mobile_app_<slug>, or mobile_app_<slug>_2… when HA had to number it."""
    base = "mobile_app_" + slugify(device_name)
    if actions is None:
        return base
    if base in actions:
        return base
    numbered = sorted(a for a in actions if re.fullmatch(re.escape(base) + r"_\d+", a))
    return numbered[0] if numbered else None


def refresh_blocking(force: bool = False) -> list | None:
    """Read the people from Home Assistant (at most every MAX_AGE_S unless `force`)."""
    with _lock:
        if not force and _cache["people"] is not None and time.monotonic() - _cache["at"] < MAX_AGE_S:
            return _cache["people"]
    if not ha_client.has_token():
        return _cache["people"]
    status, body = ha_client.request("POST", "/template", {"template": _TEMPLATE})
    if status != 200:
        logger.warning("Couldn't read the people from Home Assistant (HTTP %s).", status)
        return _cache["people"]
    try:
        raw = json.loads(body)
    except ValueError:
        logger.warning("Home Assistant's people list couldn't be read.")
        return _cache["people"]
    from . import ha_notify
    known = ha_notify.available_notify_targets()
    actions = known[0] if known else None
    people = []
    for p in raw if isinstance(raw, list) else []:
        if not isinstance(p, dict) or not isinstance(p.get("entity_id"), str):
            continue
        phones = []
        for ph in p.get("phones") or []:
            svc = _notify_name(ph.get("name") or "", actions)
            phones.append({"label": ph.get("label") or ph.get("name") or "Phone", "tracker": ph.get("tracker"),
                           "service": svc})
        people.append({"entityId": p["entity_id"], "name": p.get("name") or p["entity_id"][7:],
                       "userId": p.get("user_id"), "state": p.get("state"), "picture": p.get("picture"),
                       "phones": phones})
    with _lock:
        _cache.update(at=time.monotonic(), people=people)
    return people


def people() -> list:
    """The last list read (never calls Home Assistant)."""
    with _lock:
        return list(_cache["people"] or [])


def known() -> bool:
    """Has Home Assistant's people list been read at least once?"""
    with _lock:
        return _cache["people"] is not None


def person_for(user_id: str) -> dict | None:
    """The Home Assistant person linked to this login, if any."""
    return next((p for p in people() if user_id and p.get("userId") == user_id), None)


def phones_for(user_id: str) -> list[str]:
    """Bare notify names (what ha_notify.send_notify takes) of the person's Companion-app phones."""
    p = person_for(user_id)
    return [ph["service"] for ph in (p or {}).get("phones", []) if ph.get("service")]


def reset() -> None:
    """Tests."""
    with _lock:
        _cache.update(at=0.0, people=None)


async def loop() -> None:
    """Keep the people fresh (started by the app's lifespan; cancelled on shutdown)."""
    import asyncio
    from starlette.concurrency import run_in_threadpool
    while True:
        try:
            await run_in_threadpool(refresh_blocking, True)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Reading the people from Home Assistant failed")
        await asyncio.sleep(MAX_AGE_S)
