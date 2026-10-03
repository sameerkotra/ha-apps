"""Alerts and price targets for a home."""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import CurrentUser, get_current_user
from app.db import get_db
from app.services import alerts as alerts_service
from app.services import deals
from app.api.common import require_home as _home

router = APIRouter(prefix="/api/v1", tags=["alerts"])


class TargetIn(BaseModel):
    item_id: str
    mode: str = Field("BELOW", description="BELOW: tell me at or below a price. NEW_LOW: tell me about a new lowest price")
    target_price: float | None = Field(None, gt=0, description="In the item's price unit (per each, per lb, per fl oz...)")


@router.get("/homes/{home_id}/alerts")
async def list_alerts(home_id: str, include_dismissed: bool = False, _: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Recent alerts, newest first."""
    _home(db, home_id)
    return alerts_service.list_alerts(db, home_id, include_dismissed)


@router.post("/alerts/{alert_id}/dismiss")
async def dismiss_alert(alert_id: str, _: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    if not alerts_service.dismiss_alert(db, alert_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found")
    return {"status": "dismissed", "id": alert_id}


@router.get("/homes/{home_id}/price-targets")
async def list_targets(home_id: str, _: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """The home's price targets, each with the item's best recent price for comparison."""
    _home(db, home_id)
    return alerts_service.list_targets(db, home_id)


@router.post("/homes/{home_id}/price-targets", status_code=status.HTTP_201_CREATED)
async def create_target(home_id: str, request: TargetIn, _: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Add a price target. It is checked at once and after each daily check and each saved receipt."""
    _home(db, home_id)
    try:
        target = await asyncio.to_thread(alerts_service.create_target, db, home_id, request.item_id, request.mode, request.target_price)
    except alerts_service.AlertError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    try:
        await asyncio.to_thread(deals.refresh_home_id, home_id)  # so an already-met target alerts straight away
    except Exception:  # noqa: BLE001
        pass
    return next((t for t in alerts_service.list_targets(db, home_id) if t["id"] == target.id), {"id": target.id})


@router.delete("/price-targets/{target_id}")
async def delete_target(target_id: str, _: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    if not alerts_service.delete_target(db, target_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Target not found")
    return {"status": "deleted", "id": target_id}
