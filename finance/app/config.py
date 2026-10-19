"""What the shared Home Assistant code (app/common/ha_client.py, ha_people.py, ha_notify.py) reads from the app.

The shared `ha_client` reads `config.SUPERVISOR_CORE_API` and `config.SUPERVISOR_TOKEN` at call time, so tests can
point them elsewhere. Finance's other settings live in App settings (app/settings.py), never here.
"""
import os

SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
SUPERVISOR_CORE_API = os.environ.get("SUPERVISOR_CORE_API", "http://supervisor/core/api").rstrip("/")
