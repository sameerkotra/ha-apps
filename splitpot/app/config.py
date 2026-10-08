"""What the shared Home Assistant modules (common/ha_client.py, ha_notify.py, ha_people.py) read from the app,
at call time — so tests can point them at a fake Home Assistant. The rest of Splitpot's set-up is in main.py."""
import os

SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
SUPERVISOR_CORE_API = os.environ.get("SUPERVISOR_CORE_API", "http://supervisor/core/api").rstrip("/")


# The household apps bus (app_messages.py) and the Household Assistant's links (tools.py).
SUPERVISOR_CORE_WS = os.environ.get("SUPERVISOR_CORE_WS", "ws://supervisor/core/websocket")
SUPERVISOR_API = os.environ.get("SUPERVISOR_API", "http://supervisor").rstrip("/")
APP_TITLE = "Splitpot"
APP_VERSION = "2.4.3"           # config.yaml's version: the app bus says it in its hello
# The app's sidebar page ("/<full slug>", open to everyone): what a phone notification and the Household Assistant's
# links open. Set at start-up from the Supervisor or the host name; None when the app isn't in the sidebar.
SIDEBAR_PAGE = None
