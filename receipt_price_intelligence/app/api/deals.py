"""Best prices: the result of the daily check for a home."""


from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.common import require_home as _require_home
from app.auth import CurrentUser, get_current_user
from app.config import get_settings
from app.db import get_db
from app.services import deals
from app.services import scheduler

router = APIRouter(prefix="/api/v1/homes/{home_id}/best-prices", tags=["best-prices"])




def _with_schedule(snapshot: dict) -> dict:
    snapshot["next_run"] = scheduler.next_run(get_settings().recommendation_run_hour).isoformat()
    return snapshot


@router.get("")
def get_best_prices(
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
def refresh_best_prices(
    home_id: str,
    _: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Run the check for this home now instead of waiting for tonight."""
    _require_home(db, home_id)
    deals.refresh_home_id(home_id)
    db.expire_all()
    return _with_schedule(deals.get_snapshot(db, home_id))
