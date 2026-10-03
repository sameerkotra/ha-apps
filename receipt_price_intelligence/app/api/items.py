"""Common item names: autocomplete, rename, merge."""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import CurrentUser, get_current_user
from app.db import get_db
from app.services import homes as homes_service
from app.services import items as items_service

router = APIRouter(prefix="/api/v1", tags=["items"])


class ItemRename(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    category: str | None = Field(None, max_length=60, description="Send an empty string to clear it")


class ItemMerge(BaseModel):
    into_item_id: str


def _conflict(error: items_service.ItemError) -> JSONResponse:
    body = {"detail": str(error)}
    if error.conflict_id:
        body["conflict_id"] = error.conflict_id
    return JSONResponse(status_code=status.HTTP_409_CONFLICT, content=body)


@router.get("/homes/{home_id}/common-items")
async def list_common_items(
    home_id: str,
    q: str | None = None,
    limit: int = 500,
    _: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Common items in a home, most used first. Feeds the common-name autocomplete."""
    if homes_service.get_home(db, home_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Home not found")
    return items_service.list_items(db, home_id, q, limit)


@router.get("/common-items/{item_id}")
async def get_common_item(item_id: str, _: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """A common item and the printed descriptions that map to it."""
    item = items_service.get_item(db, item_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")
    return item


@router.patch("/common-items/{item_id}")
async def update_common_item(
    item_id: str,
    request: ItemRename,
    _: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Give an item its common name and/or a category (only the fields sent change).

    409 (with ``conflict_id``) if another item already has that name.
    """
    sent = request.model_fields_set
    item = None
    try:
        if request.name is not None:
            item = items_service.rename_item(db, item_id, request.name)
        if "category" in sent:
            item = items_service.set_category(db, item_id, request.category)
    except items_service.ItemError as e:
        return _conflict(e)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")
    return {"id": item.id, "name": item.name, "name_confirmed": bool(item.name_confirmed), "category": item.category}


@router.get("/homes/{home_id}/categories")
async def item_categories(home_id: str, _: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Categories to choose from: the standard ones plus any you added."""
    if homes_service.get_home(db, home_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Home not found")
    return items_service.list_categories(db, home_id)


@router.post("/homes/{home_id}/common-items/categorize")
async def categorize_items(home_id: str, _: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Fill in the category of every uncategorized item from its name, where it can be recognised."""
    if homes_service.get_home(db, home_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Home not found")
    return items_service.categorize_uncategorized(db, home_id)


@router.post("/common-items/{item_id}/merge")
async def merge_common_item(
    item_id: str,
    request: ItemMerge,
    _: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Merge this item into another in the same home."""
    try:
        return items_service.merge_items(db, item_id, request.into_item_id)
    except items_service.ItemError as e:
        return _conflict(e)
