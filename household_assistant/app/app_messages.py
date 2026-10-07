"""The assistant on the household apps bus (APP_MESSAGES_SPEC.md §6.6; the shared app/common/app_bus.py).

It answers no kinds of its own: it sends `assist.tools.list` (catalogue.py) and `assist.tool.call` (engine.py)
and reads their answers through `bus.on_reply`. The bus opens its own WebSocket to Home Assistant and runs its
outbox on its own thread; without a Supervisor token (the tests) it stays off.
"""
from . import catalogue, config, db, engine  # noqa: F401  (catalogue and engine register their reply callbacks)
from .common import app_bus as bus


def start() -> None:
    if bus.default.started:
        return
    bus.start(config.SLUG, config.APP_TITLE, config.APP_VERSION, can=[], db=db.get_conn,
              outbox_thread=bool(config.SUPERVISOR_TOKEN), api_base=config.SUPERVISOR_CORE_API,
              ws_url=config.SUPERVISOR_CORE_WS, token=config.SUPERVISOR_TOKEN)


def stop() -> None:
    bus.stop()


def connected_apps() -> dict:
    """Admin → Connected apps (APP_MESSAGES_SPEC §5)."""
    on = bool(bus.default.started and bus.default.token)
    return {"on": on, "connected": bus.default.connected, "apps": bus.apps() if bus.default.started else []}
