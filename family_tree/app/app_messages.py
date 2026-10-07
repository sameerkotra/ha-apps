"""Family Tree on the household apps bus (APP_MESSAGES_SPEC.md; the shared app/common/app_bus.py).

For now Family Tree only answers the Household Assistant (tools.py, APP_MESSAGES_SPEC §6.6). The bus opens its own
WebSocket to Home Assistant and runs its outbox on its own thread; without a Supervisor token (the tests) it stays
off. `learn_panel` works out the app's sidebar page for the links in the answers (APP_MESSAGES_SPEC §6.5).
"""
from . import config, db, tools
from .common import app_bus as bus
from .common import assist_tools

SLUG = "family_tree"

tools.tools.install(bus)


def learn_panel(info: dict | None = None) -> str | None:
    """The sidebar page into config.INGRESS_PANEL (the shared assist_tools.sidebar_page)."""
    config.INGRESS_PANEL = assist_tools.sidebar_page(SLUG, token=config.SUPERVISOR_TOKEN,
                                                     supervisor_api=config.SUPERVISOR_API, info=info)
    return config.INGRESS_PANEL


def start() -> None:
    """In the lifespan. Off (nothing connects) without a Supervisor token."""
    from .routers.admin import APP_VERSION
    if bus.default.started:
        return
    learn_panel()
    bus.start(SLUG, config.APP_TITLE, APP_VERSION, db=db.get_conn, api_base=config.SUPERVISOR_CORE_API,
              ws_url=config.SUPERVISOR_CORE_WS, token=config.SUPERVISOR_TOKEN)


def stop() -> None:
    bus.stop()
