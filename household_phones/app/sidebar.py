"""The app's sidebar page in Home Assistant ("/<full slug>"), for links into the app: notifications, the Household
Assistant's answers and Household Chat's 📱 Check phone (/<page>/check/<phone>). From the Supervisor's
GET /addons/self/info (every app may ask about itself with its own token — no hassio_api needed), else from the
container's host name (assist_tools.sidebar_page). Accepted only as /<8 hex or local>_household_phones."""
import logging

from . import config
from .common import assist_tools

logger = logging.getLogger("sidebar")
PAGE: str | None = None


def load_blocking() -> str | None:
    global PAGE
    PAGE = assist_tools.sidebar_page(config.SLUG, token=config.SUPERVISOR_TOKEN)
    logger.info("Links open %s in Home Assistant.", PAGE or "nothing (no sidebar page)")
    return PAGE
