"""Is the gateway published on the LAN? (SPEC.md §2 "On my network", §3 "Home Assistant access")

Home Assistant publishes an app's ports only from the app's Network tab, which an app can't change. The app reads
what is set from the Supervisor's info about itself — `GET /addons/self/info`, the one Supervisor call it makes
(hassio_api with the default role) — so the page's warning is true rather than guessed.
"""
import logging
import time

import httpx

from . import config

logger = logging.getLogger("network")

PORT_KEY = "11434/tcp"
_cache: dict = {"at": 0.0, "value": None}
CACHE_SECONDS = 60


async def published_port(force: bool = False) -> dict:
    """{"known": bool, "port": int | None}: the host port 11434 is published on, None when it isn't."""
    if not force and time.monotonic() - _cache["at"] < CACHE_SECONDS and _cache["value"] is not None:
        return _cache["value"]
    value = {"known": False, "port": None}
    if config.SUPERVISOR_TOKEN:
        try:
            async with httpx.AsyncClient(timeout=5, trust_env=False) as c:
                r = await c.get(f"{config.SUPERVISOR_API}/addons/self/info",
                                headers={"Authorization": f"Bearer {config.SUPERVISOR_TOKEN}"})
            if r.status_code == 200:
                net = (r.json().get("data") or {}).get("network") or {}
                port = net.get(PORT_KEY)
                value = {"known": True, "port": int(port) if port else None}
            else:
                logger.warning("Reading the app's network settings: HTTP %s", r.status_code)
        except (httpx.HTTPError, ValueError, TypeError) as e:
            logger.warning("Reading the app's network settings failed: %s", e)
    _cache.update(at=time.monotonic(), value=value)
    return value
