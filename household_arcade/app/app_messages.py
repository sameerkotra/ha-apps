"""Arcade on the household apps bus (APP_MESSAGES_SPEC.md; the shared app/common/app_bus.py).

For now Arcade only answers the Household Assistant (tools.py, APP_MESSAGES_SPEC §6.6). The bus opens its own
WebSocket to Home Assistant; its outbox runs on its own thread while the background loops do (not in the tests,
where every test recreates the database).
"""
from . import config, db, tools
from .common import app_bus as bus

SLUG = "household_arcade"

tools.tools.install(bus)


def start() -> None:
    """In the lifespan. Off (nothing connects) without a Supervisor token."""
    if bus.default.started:
        return
    bus.start(SLUG, config.APP_TITLE, config.APP_VERSION, db=db.get_conn, outbox_thread=bool(config.BACKGROUND_LOOPS),
              api_base=config.SUPERVISOR_CORE_API, ws_url=config.SUPERVISOR_CORE_WS, token=config.SUPERVISOR_TOKEN)


def stop() -> None:
    bus.stop()
