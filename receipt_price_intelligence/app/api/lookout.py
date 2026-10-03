"""What the app looks out for: items due to restock, list items cheaper than usual, receipt lines that cost more
than the store posted, and cheaper ways to buy what you buy."""

import asyncio

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.auth import CurrentUser, get_current_user
from app.db import get_db, get_db_session
from app.api.common import require_home as _home
from app.services import lookout

router = APIRouter(prefix="/api/v1", tags=["lookout"])


async def _run(fn, home_id: str, *args):
    def work():
        with get_db_session()() as db:
            return fn(db, home_id, *args)
    return await asyncio.to_thread(work)


@router.get("/homes/{home_id}/restock")
async def restock(home_id: str, _: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Items you buy regularly and when you are next due to buy them (``status``: due, soon, later), soonest first."""
    _home(db, home_id)
    return await _run(lookout.restock, home_id)


@router.get("/homes/{home_id}/price-drops")
async def price_drops(home_id: str, _: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Shopping list items now at least ``price_drop_percent`` cheaper somewhere than you usually pay."""
    _home(db, home_id)
    return await _run(lookout.price_drops, home_id)


@router.get("/homes/{home_id}/overcharges")
async def overcharges(home_id: str, receipt_id: str | None = None, days: int = 45,
                      _: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Receipt lines (one receipt, or the last ``days`` days) where you paid noticeably more than the same store posted
    online or in a deal around the purchase: ``extra`` is roughly how much more."""
    _home(db, home_id)
    return await _run(lookout.overcharges, home_id, receipt_id, max(1, min(days, 365)))


@router.get("/homes/{home_id}/lookout-summary")
async def lookout_summary(home_id: str, _: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """``savings`` and ``overcharges`` (last 45 days) together, from one read of the receipts (the Best prices page)."""
    _home(db, home_id)
    return await _run(lookout.summary, home_id)


@router.get("/homes/{home_id}/savings")
async def savings(home_id: str, _: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Cheaper ways to buy what you buy: ``kind`` store (another store at least 10% cheaper per unit than where you
    usually buy it) or pack (another pack size at least 10% cheaper per unit)."""
    _home(db, home_id)
    return await _run(lookout.savings, home_id)
