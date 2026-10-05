"""Tiny Home Assistant helper: the Supervisor's Core API proxy
(http://supervisor/core/api/) with the auto-injected SUPERVISOR_TOKEN.

The client is the shared app/common/ha_client.py (re-exported here, so
`ha_client.request(...)`, `post_state`, … keep working); the time zone comes
through app/common/ha_time.py. The blocking functions are meant to run off the
event loop — `run_in_threadpool(...)` from async code, or inside a sync FastAPI
route / BackgroundTask (which FastAPI runs in a thread).

Every function here is best effort: it returns a result instead of raising,
and logs at most one "no token" warning for the life of the process.
"""
import logging

from . import config
from .common import ha_time
from .common.ha_client import delete_state, has_token, post_state, request, warn_no_token_once  # noqa: F401

logger = logging.getLogger("ha_client")


def _home_details(data: dict) -> None:
    # the home's latitude picks the hemisphere for maintenance seasons; the currency labels costs
    config.LATITUDE = data.get("latitude") if isinstance(data.get("latitude"), (int, float)) else config.LATITUDE
    if isinstance(data.get("currency"), str) and data["currency"].strip():
        config.CURRENCY = data["currency"].strip()[:8]


async def load_timezone() -> None:
    """At startup: fetch HA's time zone (and the home's latitude and currency)
    once and apply it; fall back to the UTC default already set in config.py if
    it can't be read."""
    await ha_time.load(config.ZONE, on_config=_home_details, log=logger)
