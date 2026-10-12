"""The app's sidebar page in Home Assistant ("/<full slug>"), for deep links (/<page>/site/<slug>) and the viewer's
"Copy link". From the Supervisor's GET /addons/self/info (every app may ask about itself with its own token — no
hassio_api needed), else from the container's host name, which the Supervisor sets to the full slug with "-" for "_".
Accepted only as /<8 hex or local>_household_proxy."""
import json
import logging
import os
import re
import urllib.request

from . import config

logger = logging.getLogger("sidebar")
_OK = re.compile(rf"^([0-9a-f]{{8}}|local)_{re.escape(config.SLUG)}$")
PAGE: str | None = None


def load_blocking() -> str | None:
    global PAGE
    info = None
    if config.SUPERVISOR_TOKEN:
        req = urllib.request.Request("http://supervisor/addons/self/info",
                                     headers={"Authorization": f"Bearer {config.SUPERVISOR_TOKEN}"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:          # noqa: S310 — fixed internal URL
                body = json.loads(resp.read(256 * 1024).decode("utf-8"))
            info = body.get("data") if isinstance(body, dict) else None
        except Exception as e:                                               # noqa: BLE001 — host name below
            logger.info("Couldn't read this app's info from the Supervisor (%s).", type(e).__name__)
    if isinstance(info, dict) and isinstance(info.get("slug"), str):
        PAGE = "/" + info["slug"] if _OK.match(info["slug"]) and info.get("ingress_panel") is not False else None
        return PAGE
    slug = os.environ.get("HOSTNAME", "").strip().lower().replace("-", "_")
    PAGE = "/" + slug if _OK.match(slug) else None
    return PAGE
