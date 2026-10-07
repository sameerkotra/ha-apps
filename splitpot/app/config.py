"""What the shared Home Assistant modules (common/ha_client.py, ha_notify.py, ha_people.py) read from the app,
at call time — so tests can point them at a fake Home Assistant. The rest of Splitpot's set-up is in main.py."""
import os

SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
SUPERVISOR_CORE_API = os.environ.get("SUPERVISOR_CORE_API", "http://supervisor/core/api").rstrip("/")

# The app's panel in Home Assistant, which a notification opens when tapped (the container's host name is the
# app's slug with - for _, e.g. local-splitpot → /hassio/ingress/local_splitpot). None outside Home Assistant.
_host = os.environ.get("HOSTNAME", "")
INGRESS_PANEL = ("/hassio/ingress/" + _host.replace("-", "_")) if _host and _host.replace("-", "").isalnum() \
    and _host.endswith("splitpot") else None
