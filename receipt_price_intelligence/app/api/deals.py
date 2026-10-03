"""Best prices: the result of the daily check for a home."""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth import CurrentUser, get_current_user
from app.config import get_settings
from app.db import get_db
from app.services import deals
from app.services import homes as homes_service
from app.services import scheduler

router = APIRouter(prefix="/api/v1/homes/{home_id}/best-prices", tags=["best-prices"])


def _require_home(db: Session, home_id: str) -> None:
    if homes_service.get_home(db, home_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Home not found")


def _with_schedule(snapshot: dict) -> dict:
    snapshot["next_run"] = scheduler.next_run(get_settings().recommendation_run_hour).isoformat()
    return snapshot


@router.get("")
async def get_best_prices(
    home_id: str,
    _: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """The latest daily check for this home.

    ``status`` is ``PENDING`` before the first run, ``OK`` after, or ``FAILED`` if the last run
    failed (in which case the previous results are still returned along with ``error``).
    ``best_prices`` lists every checked item with the store where it was cheapest most recently;
    ``switch_store`` and ``price_alerts`` are the items worth acting on.
    """
    _require_home(db, home_id)
    return _with_schedule(deals.get_snapshot(db, home_id))


@router.post("/refresh")
async def refresh_best_prices(
    home_id: str,
    _: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Run the check for this home now instead of waiting for tonight."""
    _require_home(db, home_id)
    await asyncio.to_thread(deals.refresh_home_id, home_id)
    db.expire_all()
    return _with_schedule(deals.get_snapshot(db, home_id))
