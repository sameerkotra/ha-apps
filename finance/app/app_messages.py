"""Finance Dashboard on the household apps bus (APP_MESSAGES_SPEC.md; the shared app/common/app_bus.py).

For now Finance only answers the Household Assistant (tools.py, APP_MESSAGES_SPEC §6.6). The bus's tables
(`bus_outbox`, `bus_seen`, `bus_apps`) are made in the app's own database by `app_bus` when it starts; the tools only
read, inside the bus's transaction. The bus opens its own WebSocket to Home Assistant and runs its outbox on its own
thread; without a Supervisor token (the tests) it stays off.
"""
import os

from . import tools
from .common import app_bus as bus
from .common import assist_tools
from .db import get_db
from .version import APP_VERSION

SLUG = "finance"
TITLE = "Finance Dashboard"

tools.tools.install(bus)


def start() -> None:
    """In the lifespan: learn the sidebar page (for links), then join the bus."""
    if bus.default.started:
        return
    token = os.environ.get("SUPERVISOR_TOKEN", "")
    tools.PANEL["value"] = assist_tools.sidebar_page(SLUG, token=token,
                                                     supervisor_api=os.environ.get("SUPERVISOR_API", "http://supervisor"))
    bus.start(SLUG, TITLE, APP_VERSION, db=get_db, outbox_thread=bool(token), token=token,
              api_base=os.environ.get("SUPERVISOR_CORE_API", "http://supervisor/core/api"),
              ws_url=os.environ.get("SUPERVISOR_CORE_WS", "ws://supervisor/core/websocket"))


def stop() -> None:
    bus.stop()
