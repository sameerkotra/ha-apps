"""The shopping list: items to buy, where each is cheapest, and the Home Assistant side of it."""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import CurrentUser, get_current_user
from app.db import get_db, get_db_session
from app.services import ha, shoplist, shoplist_nearby
from app.api.common import require_home as _home

router = APIRouter(prefix="/api/v1", tags=["shopping list"])


class AddIn(BaseModel):
    item_id: str | None = Field(None, description="A known item (bought before)")
    text: str | None = Field(None, max_length=200, description="Or what to buy, typed ('2 milk' adds two); linked to a known item when it clearly is one")
    qty: float = Field(1, gt=0, le=999)
    new: bool = Field(False, description="Typed text is a new item (only an item with exactly this name is reused); "
                                         "else it may be matched to an item it clearly is")


class AddMany(BaseModel):
    item_ids: list[str] = Field(..., max_length=100)


class UpdateIn(BaseModel):
    checked: bool | None = None
    qty: float | None = Field(None, gt=0, le=999)
    text: str | None = Field(None, max_length=200)


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except shoplist.ListError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


async def _in_thread(fn, *args):
    def work():
        with get_db_session()() as db:
            return _call(fn, db, *args)
    return await asyncio.to_thread(work)


@router.get("/homes/{home_id}/list")
async def get_list(home_id: str, online: bool = True, _: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """The list, each item with where it is cheapest (``cheapest``: store, price, unit, price_source online/deal/receipt)
    and the next cheapest stores (``also``), plus the same grouped by cheapest store (``by_store``)."""
    _home(db, home_id)
    return await _in_thread(shoplist.get_list, home_id, online)


@router.get("/homes/{home_id}/list/browse")
async def browse(home_id: str, q: str | None = None, _: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Items bought before, to pick from (``on_list`` marks those already on the list)."""
    _home(db, home_id)
    return await _in_thread(shoplist.browse, home_id, q)


@router.post("/homes/{home_id}/list", status_code=status.HTTP_201_CREATED)
async def add(home_id: str, request: AddIn, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Typed text that is not an item you have becomes a new item (with no price yet): when you buy it, give its
    receipt line that name on the Review page, and the list shows where it is cheapest from then on."""
    _home(db, home_id)
    return _call(shoplist.add, db, home_id, request.text, request.item_id, request.qty, user_id=None if user.anonymous else user.id,
                 new=request.new)


@router.post("/homes/{home_id}/list/many", status_code=status.HTTP_201_CREATED)
async def add_many(home_id: str, request: AddMany, _: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Add several known items at once (the page's "Add selected")."""
    _home(db, home_id)
    added = [_call(shoplist.add, db, home_id, None, i, 1, None, False) for i in request.item_ids]
    shoplist.sync_soon(home_id)
    return {"added": len(added)}


@router.patch("/homes/{home_id}/list/{row_id}")
async def update(home_id: str, row_id: str, request: UpdateIn, _: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    _home(db, home_id)
    return _call(shoplist.update, db, home_id, row_id, request.model_dump(exclude_unset=True))


@router.delete("/homes/{home_id}/list/{row_id}")
async def remove(home_id: str, row_id: str, _: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    _home(db, home_id)
    _call(shoplist.remove, db, home_id, row_id)
    return {"removed": True}


@router.post("/homes/{home_id}/list/clear-checked")
async def clear_checked(home_id: str, _: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    _home(db, home_id)
    return {"removed": shoplist.clear_checked(db, home_id)}


@router.post("/homes/{home_id}/list/sync")
async def sync_now(home_id: str, _: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Sync with the Home Assistant to-do list and republish the sensor now."""
    _home(db, home_id)
    await asyncio.to_thread(shoplist.sync, home_id)
    return {"synced": True}


@router.post("/homes/{home_id}/list/test-nearby")
async def test_nearby(home_id: str, _: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Send the 'cheapest here' notification for the store where most list items are cheapest, now, wherever you are
    (to check the notify service), and say how far the tracked person or phone is from it."""
    _home(db, home_id)
    if not ha.available():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Home Assistant is not available to the app")

    def work():
        stores = shoplist_nearby.cheapest_here(home_id)
        if not stores:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail="No item on the list has a cheapest store with a known location yet")
        store = max(stores.values(), key=lambda s: len(s["items"]))
        title, text = shoplist_nearby.message(store)
        from app.services import notifier
        if not notifier.send("nearby", title, text, test=True):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="The notification could not be sent (see the app's Log tab)")
        try:
            where = shoplist_nearby.position()
        except ha.HAError:
            where = None
        away = round(shoplist_nearby._distance_m(where, (store["lat"], store["lng"]))) if where else None
        return {"sent": True, "store": store["store"], "title": title, "message": text, "distance_m": away}

    return await asyncio.to_thread(work)
