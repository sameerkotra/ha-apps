"""Talking to Home Assistant through the Supervisor's Core API proxy.

Available to the app when ``homeassistant_api`` is enabled in its manifest; the Supervisor
provides the token in ``SUPERVISOR_TOKEN``. Used to publish sensors and events, send
notifications, and read shopping / to-do lists. Everything here is best effort: when Home
Assistant is not reachable the app carries on without it.

``HA_API_URL`` and ``HA_API_TOKEN`` override the defaults (used to point at another instance). The
HTTP call itself is the shared client (app/common/ha_client.py); this module turns its answers into
results or ``HAError``.
"""

import json
import os
import re
import urllib.error
from typing import Any

from app.common import ha_client
from app.logging_config import get_logger

logger = get_logger("ha")

DEFAULT_URL = "http://supervisor/core/api"
TIMEOUT = 10


class HAError(RuntimeError):
    """Home Assistant could not be reached or refused the request."""


def _token() -> str:
    return os.environ.get("HA_API_TOKEN") or os.environ.get("SUPERVISOR_TOKEN") or ""


def base_url() -> str:
    return (os.environ.get("HA_API_URL") or DEFAULT_URL).rstrip("/")


def available() -> bool:
    """True when this app can talk to Home Assistant."""
    return bool(_token())


def slug(text: str) -> str:
    """A safe entity-id fragment: lowercase letters, digits and underscores."""
    return re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_") or "home"


def _request(method: str, path: str, body: Any = None) -> Any:
    if not available():
        raise HAError("Home Assistant is not available to this app")

    def unreachable(e: Exception) -> None:   # no answer at all: say so; anything unexpected goes up as it is
        if isinstance(e, (urllib.error.URLError, TimeoutError, OSError)):
            raise HAError("Home Assistant could not be reached") from e
        raise e

    status, raw = ha_client.request(method, path, body, timeout=TIMEOUT, on_error=unreachable,
                                    base_url=base_url(), token=_token())
    if status is None or not 200 <= status < 300:
        raise HAError(f"Home Assistant answered with an error ({status})")
    try:
        text = raw.decode("utf-8")
        return json.loads(text) if text else None
    except json.JSONDecodeError as e:
        raise HAError("Home Assistant sent an unreadable answer") from e


def set_state(entity_id: str, state: Any, attributes: dict[str, Any] | None = None) -> None:
    """Create or update an entity's state (it lasts until Home Assistant restarts, so it is republished)."""
    _request("POST", f"/states/{entity_id}", {"state": str(state), "attributes": attributes or {}})


def post_sensor(entity_id: str, state: Any, attributes: dict[str, Any] | None = None) -> bool:
    """set_state() for app/common/sensor_publisher.py: True once posted; raises HAError like set_state."""
    set_state(entity_id, state, attributes)
    return True


def call_service(domain: str, service: str, data: dict[str, Any] | None = None, response: bool = False) -> Any:
    suffix = "?return_response" if response else ""
    return _request("POST", f"/services/{domain}/{service}{suffix}", data or {})


def fire_event(event_type: str, data: dict[str, Any] | None = None) -> None:
    _request("POST", f"/events/{event_type}", data or {})


def notify(title: str, message: str, service: str = "", data: dict[str, Any] | None = None) -> None:
    """Send a notification: through ``service`` (like ``notify.mobile_app_pixel``, a push to that phone) or, without
    one, as a persistent notification in Home Assistant. ``data`` is passed to push notifications (tap action, tag)."""
    if service and "." in service:
        domain, name = service.split(".", 1)
        call_service(domain, name, {"title": title, "message": message, **({"data": data} if data else {})})
    else:
        call_service("persistent_notification", "create", {"title": title, "message": message})


def list_notify_services() -> list[dict[str, str]]:
    """Home Assistant's notify services: [{service, name, kind}]. ``kind`` "phone" for companion-app devices
    (``notify.mobile_app_*``, push notifications), "other" for the rest (Telegram, e-mail...)."""
    domains = _request("GET", "/services") or []
    out = []
    for d in domains:
        if d.get("domain") != "notify":
            continue
        for name in sorted((d.get("services") or {}).keys()):
            if name in ("notify", "persistent_notification", "send_message"):
                continue
            phone = name.startswith("mobile_app_")
            label = name[len("mobile_app_"):] if phone else name
            out.append({"service": f"notify.{name}", "name": label.replace("_", " ").strip().title(), "kind": "phone" if phone else "other"})
    return sorted(out, key=lambda x: (x["kind"] != "phone", x["name"]))


def tracker_for(service: str, states: list[dict[str, Any]]) -> str | None:
    """The location tracker of the phone behind a companion-app notify service: ``notify.mobile_app_pixel_8`` ->
    ``device_tracker.pixel_8`` (the app names both after the device), else the tracker with the same name."""
    if not service.startswith("notify.mobile_app_"):
        return None
    suffix = service[len("notify.mobile_app_"):]
    trackers = {str(s.get("entity_id")): s for s in states if str(s.get("entity_id", "")).startswith("device_tracker.")}
    if f"device_tracker.{suffix}" in trackers:
        return f"device_tracker.{suffix}"
    for entity, s in trackers.items():
        if slug((s.get("attributes") or {}).get("friendly_name") or "") == suffix:
            return entity
    return None


def all_states() -> list[dict[str, Any]]:
    return _request("GET", "/states") or []


def list_trackers() -> list[dict[str, str]]:
    """People and device trackers that report a position: [{entity_id, name}]."""
    out = []
    for s in _request("GET", "/states") or []:
        entity = str(s.get("entity_id", ""))
        attrs = s.get("attributes") or {}
        if entity.split(".")[0] in ("person", "device_tracker") and attrs.get("latitude") is not None:
            out.append({"entity_id": entity, "name": attrs.get("friendly_name") or entity})
    return sorted(out, key=lambda x: (not x["entity_id"].startswith("person."), x["name"]))


def list_todo_lists() -> list[dict[str, str]]:
    """The to-do / shopping lists in Home Assistant: [{entity_id, name}]."""
    states = _request("GET", "/states") or []
    return [
        {"entity_id": s["entity_id"], "name": (s.get("attributes") or {}).get("friendly_name") or s["entity_id"]}
        for s in states if str(s.get("entity_id", "")).startswith("todo.")
    ]


def get_todo_items(entity_id: str) -> list[str]:
    """The open items of a to-do list, as text."""
    result = call_service("todo", "get_items", {"entity_id": entity_id, "status": ["needs_action"]}, response=True)
    items = ((result or {}).get("service_response") or {}).get(entity_id, {}).get("items") or []
    return [str(i.get("summary", "")).strip() for i in items if str(i.get("summary", "")).strip()]
