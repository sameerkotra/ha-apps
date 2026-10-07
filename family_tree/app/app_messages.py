"""Family Tree on the household apps bus (APP_MESSAGES_SPEC.md; the shared app/common/app_bus.py).

For now Family Tree only answers the Household Assistant (tools.py, APP_MESSAGES_SPEC §6.6). The bus opens its own
WebSocket to Home Assistant and runs its outbox on its own thread; without a Supervisor token (the tests) it stays
off. `learn_panel` works out the app's sidebar page for the links in the answers (APP_MESSAGES_SPEC §6.5).
"""
import json
import logging
import re
import urllib.request

from . import config, db, tools
from .common import app_bus as bus

logger = logging.getLogger("app_messages")

SLUG = "family_tree"
_SLUG_RE = re.compile(r"^([0-9a-f]{8}|local)_family_tree$")

tools.tools.install(bus)


def learn_panel(info: dict | None = None) -> str | None:
    """The sidebar page from the Supervisor (`GET /addons/self/info`: slug, ingress_panel), into
    config.INGRESS_PANEL; the host-name guess stays when the Supervisor can't be asked."""
    if info is None and config.SUPERVISOR_TOKEN:
        req = urllib.request.Request(f"{config.SUPERVISOR_API}/addons/self/info",
                                     headers={"Authorization": f"Bearer {config.SUPERVISOR_TOKEN}"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = json.loads(resp.read(256 * 1024).decode("utf-8"))
            info = body.get("data") if isinstance(body, dict) else None
        except Exception as e:                                   # noqa: BLE001 — keep the guess
            logger.info("Couldn't read this app's info from the Supervisor (%s).", type(e).__name__)
    if isinstance(info, dict) and isinstance(info.get("slug"), str):
        ok = _SLUG_RE.match(info["slug"]) and info.get("ingress_panel") is not False
        config.INGRESS_PANEL = "/" + info["slug"] if ok else None
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
