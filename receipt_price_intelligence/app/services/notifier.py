"""Every notification the app sends goes through here, following the Notifications page.

Settings (``settings()``): where to send (push to the phones picked, via the Home Assistant companion app's
``notify.mobile_app_*`` services, other notify services, and/or the bell in Home Assistant), which kinds to send, each
kind's own settings, and quiet hours. What the page saved (``app_settings``, key ``notifications``) takes precedence;
the App settings are the defaults, so nothing changes for anyone who never opens the page.

Kinds: ``alerts`` (price alerts and targets), ``price_drop`` (list items cheaper than usual), ``restock`` (due to buy
again), ``overcharge`` (paid more than the store posted), ``nearby`` ("cheapest here", at a store).
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Any

from app.config import get_settings
from app.db import get_db_session
from app.db.models import AppSetting
from app.logging_config import get_logger
from app.services import ha

logger = get_logger("notifier")

KEY = "notifications"
KINDS = ("alerts", "price_drop", "restock", "overcharge", "nearby")
KIND_NAMES = {"alerts": "Price alerts and targets", "price_drop": "Cheaper than usual", "restock": "Due to restock",
              "overcharge": "Paid more than posted", "nearby": "Cheapest here"}
_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class SettingsError(ValueError):
    """Something a person can fix. The message is safe to show."""


def defaults() -> dict[str, Any]:
    """The App settings, in the page's shape."""
    s = get_settings()
    service = s.shopping_notify_service or s.alert_notify_service
    return {
        "devices": [service] if service else [],
        "bell": not service,
        "kinds": {"alerts": True, "price_drop": s.price_drop_percent > 0, "restock": bool(s.restock_notify),
                  "overcharge": bool(s.overcharge_notify), "nearby": bool(s.shopping_tracker_entity and s.shopping_nearby_meters > 0)},
        "price_drop_percent": s.price_drop_percent or 10,
        "restock_auto_add": bool(s.restock_auto_add),
        "tracker": s.shopping_tracker_entity or None,
        "nearby_meters": s.shopping_nearby_meters or 250,
        "quiet": None,
    }


def settings() -> dict[str, Any]:
    """The settings in use: what the page saved, over the App settings."""
    merged = defaults()
    try:
        with get_db_session()() as db:
            row = db.get(AppSetting, KEY)
            saved = json.loads(row.value) if row is not None else {}
    except Exception:  # noqa: BLE001 - notifications must never break because settings could not be read
        logger.warning("Could not read the notification settings; using the App settings", exc_info=True)
        saved = {}
    for k, v in saved.items():
        if k == "kinds" and isinstance(v, dict):
            merged["kinds"] = {**merged["kinds"], **{x: bool(y) for x, y in v.items() if x in KINDS}}
        elif k in merged:
            merged[k] = v
    merged["saved"] = bool(saved)
    return merged


def save(new: dict[str, Any]) -> dict[str, Any]:
    """Check and store the page's settings. Returns the settings in use."""
    devices = [d for d in (new.get("devices") or []) if isinstance(d, str) and re.fullmatch(r"notify\.[a-z0-9_]+", d)][:10]
    kinds = {k: bool((new.get("kinds") or {}).get(k)) for k in KINDS}
    try:
        percent = int(new.get("price_drop_percent") or 10)
        meters = int(new.get("nearby_meters") or 250)
    except (TypeError, ValueError):
        raise SettingsError("Percent and distance must be whole numbers")
    if not 1 <= percent <= 90:
        raise SettingsError("The price drop must be between 1 and 90 percent")
    if not 50 <= meters <= 3000:
        raise SettingsError("The distance must be between 50 and 3000 meters")
    tracker = new.get("tracker") or None
    if tracker and not re.fullmatch(r"(person|device_tracker)\.[a-z0-9_]+", tracker):
        raise SettingsError("Pick a person or a device tracker")
    if kinds["nearby"] and not tracker and not any(d.startswith("notify.mobile_app_") for d in devices):
        raise SettingsError('"Cheapest here" follows the phones you send to: choose a phone above (or, without one, a person to track)')
    quiet = new.get("quiet") or None
    if quiet:
        if not (isinstance(quiet, dict) and _TIME.match(str(quiet.get("start"))) and _TIME.match(str(quiet.get("end")))):
            raise SettingsError("Quiet hours need a start and an end, like 22:00 and 07:00")
        quiet = {"start": quiet["start"], "end": quiet["end"]}
    value = {"devices": devices, "bell": bool(new.get("bell")), "kinds": kinds, "price_drop_percent": percent,
             "restock_auto_add": bool(new.get("restock_auto_add")), "tracker": tracker, "nearby_meters": meters, "quiet": quiet}
    with get_db_session()() as db:
        row = db.get(AppSetting, KEY)
        if row is None:
            db.add(AppSetting(key=KEY, value=json.dumps(value), updated_at=datetime.utcnow()))
        else:
            row.value, row.updated_at = json.dumps(value), datetime.utcnow()
        db.commit()
    return settings()


def enabled(kind: str) -> bool:
    return bool(settings()["kinds"].get(kind))


def in_quiet_hours(quiet: dict[str, str] | None, now: datetime | None = None) -> bool:
    """Whether ``now`` (local time) is inside quiet hours; they may run past midnight (22:00 to 07:00)."""
    if not quiet:
        return False
    t = (now or datetime.now()).strftime("%H:%M")
    start, end = quiet["start"], quiet["end"]
    return start <= t < end if start <= end else (t >= start or t < end)


def _panel_path() -> str | None:
    """Where the app's panel is in Home Assistant (a tap on a push notification opens it). The container's name is
    the app's slug with dashes ("local-receipt-price-intelligence")."""
    host = os.environ.get("HOSTNAME") or ""
    return f"/hassio/ingress/{host.replace('-', '_')}" if "receipt" in host else None


def send(kind: str, title: str, message: str, *, test: bool = False, devices: list[str] | None = None) -> int:
    """Send a notification of ``kind`` wherever the settings say (``devices`` overrides, for a test). Skipped when that
    kind is off, or in quiet hours (except "cheapest here", which only comes when you are at a store). Returns how many
    places it was sent to."""
    if not ha.available():
        return 0
    s = settings()
    if not test and (not s["kinds"].get(kind) or (kind != "nearby" and in_quiet_hours(s["quiet"]))):
        return 0
    targets = devices if devices is not None else list(s["devices"])
    data: dict[str, Any] = {"tag": f"receipt-prices-{kind}", "group": "Receipt prices"}
    panel = _panel_path()
    if panel:
        data.update({"url": panel, "clickAction": panel})  # iOS / Android: open the app when tapped
    sent = 0
    for service in targets:
        try:
            ha.notify(title, message, service, data)
            sent += 1
        except ha.HAError as e:
            logger.warning("Could not send to %s: %s", service, e)
    if (s["bell"] and devices is None) or not targets:
        try:
            ha.notify(title, message)  # the bell in Home Assistant
            sent += 1
        except ha.HAError as e:
            logger.warning("Could not create the Home Assistant notification: %s", e)
    return sent
