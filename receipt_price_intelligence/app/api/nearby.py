"""Grocery stores near a home from OpenStreetMap, including ones it has never bought from."""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import CurrentUser, get_current_user
from app.config import get_settings
from app.db import get_db, get_db_session
from app.api.common import require_home as _home
from app.services import nearby, planner

router = APIRouter(prefix="/api/v1", tags=["nearby stores"])


class SearchRequest(BaseModel):
    radius: float | None = Field(None, gt=0, description="How far to look, in the app's distance unit (miles or km). Default 5 mi / 8 km, at most 30 km")
    force: bool = Field(False, description="Search again even if the same search ran in the last 10 minutes")


def _call(fn, *args):
    try:
        return fn(*args)
    except nearby.NearbyError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/homes/{home_id}/nearby-stores")
async def list_nearby(home_id: str, _: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """What the last search found around this home, nearest first. ``status``: NEW, ADDED, KNOWN (you already
    shop there) or HIDDEN. ``enabled`` is false when no search server is set."""
    _home(db, home_id)
    return {**nearby.listing(db, home_id), "enabled": bool(get_settings().overpass_url)}


@router.post("/homes/{home_id}/nearby-stores/search")
async def search_nearby(home_id: str, request: SearchRequest, _: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Search OpenStreetMap for grocery stores around the home (takes a few seconds) and return them.

    Stores you already shop at are recognised; their opening hours, when OpenStreetMap has them, are offered
    as suggestions on the Stores page (``hours_suggested`` says how many)."""
    _home(db, home_id)
    km = planner.distance_to_km(request.radius, get_settings().unit_system) if request.radius else None

    def work():
        with get_db_session()() as session:  # its own session: this runs in a worker thread
            return _call(nearby.search, session, home_id, km, request.force)

    return await asyncio.to_thread(work)


def _action(fn, home_id: str, nearby_id: str, db: Session, *extra):
    _home(db, home_id)
    return _call(fn, db, home_id, nearby_id, *extra)


@router.post("/homes/{home_id}/nearby-stores/{nearby_id}/add")
async def add_nearby(home_id: str, nearby_id: str, _: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Make this store one of the home's stores: online price checks, deals and the trip planner include it."""
    return _action(nearby.add, home_id, nearby_id, db)


@router.post("/homes/{home_id}/nearby-stores/{nearby_id}/remove")
async def remove_nearby(home_id: str, nearby_id: str, _: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Undo *add*. The store's location is deleted too, unless a receipt or another home uses it."""
    return _action(nearby.remove, home_id, nearby_id, db)


@router.post("/homes/{home_id}/nearby-stores/{nearby_id}/hide")
async def hide_nearby(home_id: str, nearby_id: str, _: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Hide a store you are not interested in (it stays hidden when you search again)."""
    return _action(nearby.set_hidden, home_id, nearby_id, db, True)


@router.post("/homes/{home_id}/nearby-stores/{nearby_id}/unhide")
async def unhide_nearby(home_id: str, nearby_id: str, _: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Show a hidden store again."""
    return _action(nearby.set_hidden, home_id, nearby_id, db, False)
