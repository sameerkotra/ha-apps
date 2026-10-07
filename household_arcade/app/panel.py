"""The app's sidebar page in Home Assistant, for links in phone notifications (SPEC §7.3; the pattern is Household
Docs' "Open in Docs", APP_MESSAGES_SPEC §6.5).

Home Assistant registers a sidebar page for every app with *Show in sidebar* on, at `/<full slug>`
(`local_household_arcade` for a local copy, `<8 hex>_household_arcade` from a repository). A notification's link
is that page plus the app's own route as a sub-path (`/local_household_arcade/leaderboard`): Home Assistant opens
the page and hands the rest of the path to the app (the frontend's `home-assistant/properties` message, and the
top page's own address, which the app's page can read as it is the same origin), and app.js turns it into
`#/leaderboard`. A `#…` fragment on the link would be lost on the way in, and the admin's Settings → Apps page
(`/hassio/ingress/<slug>`) isn't open to everyone — that is what 1.7.x's links did wrong.

`learn()` asks the Supervisor (`GET /addons/self/info`, allowed for every app with its token) for the slug and
whether the sidebar page exists; it falls back to the HOSTNAME the Supervisor sets (config.INGRESS_PANEL).
"""
import json
import logging
import os
import re

from . import config

logger = logging.getLogger("panel")

SLUG_RE = re.compile(r"^([0-9a-f]{8}|local)_household_arcade$")
PANEL_RE = re.compile(r"^/[a-z0-9_]{1,80}$")


def fetch_self_info() -> dict | None:
    """GET {SUPERVISOR_API}/addons/self/info → its `data`, or None (no token, no Supervisor, an error)."""
    if not config.SUPERVISOR_TOKEN:
        return None
    import urllib.request
    req = urllib.request.Request(f"{config.SUPERVISOR_API}/addons/self/info",
                                 headers={"Authorization": f"Bearer {config.SUPERVISOR_TOKEN}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read(256 * 1024).decode("utf-8"))
    except Exception as e:                                       # noqa: BLE001 — the fallback below
        logger.info("Couldn't read this app's info from the Supervisor (%s); using HOSTNAME.", type(e).__name__)
        return None
    data = body.get("data") if isinstance(body, dict) else None
    return data if isinstance(data, dict) else None


def learn(info: dict | None = None, hostname: str | None = None) -> str | None:
    """Work out the sidebar page and keep it in config.INGRESS_PANEL: "/<full slug>", or None when the app has no
    sidebar page (notifications then carry no link)."""
    if info is None:
        info = fetch_self_info()
    panel = None
    if isinstance(info, dict) and isinstance(info.get("slug"), str):
        slug = info["slug"]
        if info.get("ingress_panel") is not False and PANEL_RE.match("/" + slug):
            panel = "/" + slug
    elif info is None:
        host = (hostname if hostname is not None else os.environ.get("HOSTNAME", "")).strip()
        slug = host.replace("-", "_")
        if SLUG_RE.match(slug):
            panel = "/" + slug
    config.INGRESS_PANEL = panel
    logger.info("Notifications open %s in Home Assistant.", panel or "nothing (no sidebar page)")
    return panel
