"""Receipt Price Intelligence on the household apps bus (APP_MESSAGES_SPEC.md; the shared app/common/app_bus.py).

For now the app only answers the Household Assistant (tools.py, APP_MESSAGES_SPEC §6.6). The app's data is in
SQLAlchemy; the bus wants plain sqlite3 and holds a write lock for the length of each answer, which would block the
tools' own writes to the same file. So the bus keeps its three small tables (`bus_outbox`, `bus_seen`, `bus_apps`)
in a database of its own next to the app's (`app_bus.db`), and each tool uses its own SQLAlchemy session. The bus
opens its own WebSocket to Home Assistant and runs its outbox on its own thread; without a Supervisor token (the
tests) it stays off.
"""
from __future__ import annotations

import os
import sqlite3

from app import tools
from app.common import app_bus as bus
from app.common import assist_tools
from app.config import APP_VERSION, get_settings

SLUG = "receipt_price_intelligence"
TITLE = "Receipt Price Intelligence"

tools.tools.install(bus)


def bus_db_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(get_settings().database_path)), "app_bus.db")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(bus_db_path(), timeout=10)
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def start() -> None:
    """In the lifespan: learn the sidebar page (for links), then join the bus."""
    if bus.default.started:
        return
    token = os.environ.get("SUPERVISOR_TOKEN", "")
    tools.PANEL["value"] = assist_tools.sidebar_page(SLUG, token=token,
                                                     supervisor_api=os.environ.get("SUPERVISOR_API", "http://supervisor"))
    bus.start(SLUG, TITLE, APP_VERSION, db=_connect, outbox_thread=bool(token), token=token,
              api_base=os.environ.get("SUPERVISOR_CORE_API", "http://supervisor/core/api"),
              ws_url=os.environ.get("SUPERVISOR_CORE_WS", "ws://supervisor/core/websocket"))


def stop() -> None:
    bus.stop()
