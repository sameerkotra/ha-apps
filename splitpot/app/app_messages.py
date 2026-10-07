"""Splitpot on the household apps bus (APP_MESSAGES_SPEC.md; the shared app/common/app_bus.py).

For now Splitpot only answers the Household Assistant (tools.py, APP_MESSAGES_SPEC §6.6). The bus opens its own
WebSocket to Home Assistant and runs its outbox on its own thread; without a Supervisor token (the tests) it stays
off.
"""
from . import config, db, tools
from .common import app_bus as bus
from .common import assist_tools

SLUG = "splitpot"

tools.tools.install(bus)


def start() -> None:
    """In the lifespan: learn the sidebar page (for links), then join the bus."""
    if bus.default.started:
        return
    config.SIDEBAR_PAGE = assist_tools.sidebar_page(SLUG, token=config.SUPERVISOR_TOKEN,
                                                    supervisor_api=config.SUPERVISOR_API)
    bus.start(SLUG, config.APP_TITLE, config.APP_VERSION, db=db.get_conn, outbox_thread=bool(config.SUPERVISOR_TOKEN),
              api_base=config.SUPERVISOR_CORE_API, ws_url=config.SUPERVISOR_CORE_WS, token=config.SUPERVISOR_TOKEN)


def stop() -> None:
    bus.stop()
