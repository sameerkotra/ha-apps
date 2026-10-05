"""Listen for the phone's notification buttons (SPEC §15.1).

One WebSocket to Home Assistant (`ws://supervisor/core/websocket`, the
Supervisor token; covered by `homeassistant_api`), subscribed to
`mobile_app_notification_action`. Actions without the HCHAT_ prefix are
ignored. The client is the shared app/common/ha_ws.py: a daemon thread that
reconnects with back-off (5 s doubling to 5 min) and pings every 50 s.

The same connection carries the messages between the household apps
(app_messages.py → app_bus.start(..., ws=connection())): one WebSocket for both.
"""
import logging

from . import config
from .common import ha_ws

logger = logging.getLogger("ha_events")

EVENT = "mobile_app_notification_action"
PREFIX = "HCHAT_"

_ws: ha_ws.HAWebSocket | None = None


def _on_action(event: dict) -> None:
    from . import notifier
    data = event.get("data") or {}
    action = data.get("action")
    if isinstance(action, str) and action.startswith(PREFIX):
        try:
            result = notifier.handle_action(action, data.get("reply_text"))
            logger.info("Notification action: %s", result)
        except Exception:
            logger.exception("Handling a notification action failed")


def _connected(first: bool) -> None:
    logger.info("Listening for notification replies from Home Assistant.")


def connection() -> ha_ws.HAWebSocket | None:
    """The app's one WebSocket to Home Assistant (made and subscribed, started by start()); None without a
    Supervisor token."""
    global _ws
    if not config.SUPERVISOR_TOKEN:
        return None
    if _ws is None:
        _ws = ha_ws.HAWebSocket(config.SUPERVISOR_CORE_WS, config.SUPERVISOR_TOKEN, name="ha-events", read_timeout=90)
        _ws.subscribe(EVENT, _on_action)
        _ws.on_connect(_connected)
    return _ws


def start() -> None:
    ws = connection()
    if ws is not None and not ws.running:
        ws.start()


def stop() -> None:
    global _ws
    if _ws:
        _ws.stop()
        _ws = None
