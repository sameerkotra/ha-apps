"""The Notifications page: every notification setting in one place, and push notifications to your phones."""

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.auth import CurrentUser, get_current_user
from app.services import ha, notifier

router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])


class Quiet(BaseModel):
    start: str = Field(..., pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    end: str = Field(..., pattern=r"^([01]\d|2[0-3]):[0-5]\d$")


class SettingsIn(BaseModel):
    devices: list[str] = Field(default_factory=list, max_length=10, description="notify services to push to, e.g. notify.mobile_app_pixel_8")
    bell: bool = Field(False, description="Also as a notification in Home Assistant (the bell)")
    kinds: dict[str, bool] = Field(default_factory=dict, description=f"Which to send: {', '.join(notifier.KINDS)}")
    price_drop_percent: int = Field(10, ge=1, le=90)
    restock_auto_add: bool = False
    tracker: str | None = Field(None, description="person. or device_tracker. entity for 'cheapest here'")
    nearby_meters: int = Field(250, ge=50, le=3000)
    quiet: Quiet | None = Field(None, description="No notifications between these times (local), except 'cheapest here'")


class TestIn(BaseModel):
    service: str | None = Field(None, description="One notify service to test; empty tests every device chosen")


@router.get("")
async def get_notifications(_: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    """The settings in use, the kinds with their names, and what Home Assistant offers to send to and to track."""
    def work():
        found: dict[str, Any] = {"available": ha.available(), "devices": [], "trackers": [], "error": None}
        if ha.available():
            try:
                found["devices"], found["trackers"] = ha.list_notify_services(), ha.list_trackers()
                states = ha.all_states()
                # where each phone's location comes from, for "cheapest here"
                found["locations"] = {d["service"]: ha.tracker_for(d["service"], states) for d in found["devices"] if d["kind"] == "phone"}
            except ha.HAError as e:
                found["error"] = str(e)
        return {"settings": notifier.settings(), "kinds": [{"key": k, "name": notifier.KIND_NAMES[k]} for k in notifier.KINDS], "ha": found}
    return await asyncio.to_thread(work)


@router.put("")
async def save_notifications(request: SettingsIn, _: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(notifier.save, request.model_dump())
    except notifier.SettingsError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/test")
async def test_notification(request: TestIn, _: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    """Send a test notification now (quiet hours and switches do not apply)."""
    if not ha.available():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Home Assistant is not available to the app")
    devices = [request.service] if request.service else None
    sent = await asyncio.to_thread(notifier.send, "alerts", "Receipt prices: test", "Notifications from the app arrive here.",
                                   test=True, devices=devices)
    if not sent:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nothing could be sent (see the app's Log tab)")
    return {"sent": sent}
