"""Managing stores: chains, locations, addresses, merges.

Stores are shared by every home. Any signed-in user can correct names and addresses and
move locations between chains; merging and deleting (which re-point or remove rows across
homes) are administrator-only.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import CurrentUser, get_current_user, require_admin
from app.db import get_db
from app.db.models import StoreChain, StoreLocation
from app.services import homes as homes_service
from app.services import stores as stores_service

router = APIRouter(prefix="/api/v1", tags=["stores"])


class ChainRename(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)


class ChainMerge(BaseModel):
    into_chain_id: str
    combine_locations: bool = Field(True, description="Also combine all locations into one, so every receipt belongs to a single store")


class LocationMerge(BaseModel):
    into_location_id: str


class LocationData(BaseModel):
    """Fields of a location. On update only the fields that are sent change."""

    store_number: str | None = Field(None, max_length=50)
    name: str | None = Field(None, max_length=255)
    raw: str | None = Field(None, max_length=500, description="Full address as one line")
    street: str | None = Field(None, max_length=255)
    city: str | None = Field(None, max_length=100)
    state: str | None = Field(None, max_length=100)
    postal_code: str | None = Field(None, max_length=20)
    store_chain_id: str | None = Field(None, description="Move the location to this chain")
    page_url: str | None = Field(None, max_length=500, description="This store's own web page (its hours are read from it before searching); empty clears it")
    opening_hours: dict | None = Field(None, description='Weekly hours, e.g. {"all": [["08:00","22:00"]], "sun": [["10:00","18:00"]]}; an empty list closes a day; null clears them')


class DiscountIn(BaseModel):
    name: str | None = Field(None, max_length=120, description="e.g. Sam's Club Mastercard")
    percent: float = Field(0, ge=0, le=50, description="Percent off")
    cents_per_unit: float = Field(0, ge=0, le=100, description="For fuel: cents off per gallon (or litre)")
    applies_to: str = Field("ALL", pattern="^(ALL|FUEL|NON_FUEL)$", description="ALL, FUEL or NON_FUEL")
    active: bool = True


class DiscountsIn(BaseModel):
    discounts: list[DiscountIn] = Field(default_factory=list, max_length=10)


class ChainWeb(BaseModel):
    """A chain's web pages. Only the fields sent change; an empty string clears one."""

    website: str | None = Field(None, max_length=500, description="The chain's site, e.g. kroger.com: searches are limited to it first")
    search_url: str | None = Field(None, max_length=500, description="Product search page with {query}, e.g. https://www.kroger.com/search?query={query}")
    deals_url: str | None = Field(None, max_length=500, description="Weekly ad or deals page")


def _conflict(error: stores_service.StoreError) -> JSONResponse:
    body: dict[str, Any] = {"detail": str(error)}
    if error.conflict_id:
        body["conflict_id"] = error.conflict_id
    return JSONResponse(status_code=status.HTTP_409_CONFLICT, content=body)


def _chain_json(chain: StoreChain) -> dict[str, Any]:
    return {"id": chain.id, "name": chain.name, "key": chain.normalized_name,
            "website": chain.website, "search_url": chain.search_url, "deals_url": chain.deals_url}


def _location_json(location: StoreLocation) -> dict[str, Any]:
    return {
        "id": location.id, "store_chain_id": location.store_chain_id,
        "store_number": location.store_number, "name": location.name, "page_url": location.page_url,
    }


@router.get("/homes/{home_id}/store-directory")
async def store_directory(
    home_id: str,
    _: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Chains with their locations, addresses, receipt counts and spend for this home,
    plus suggested duplicates (similar chain names, locations at the same address)."""
    if homes_service.get_home(db, home_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Home not found")
    return stores_service.list_chains(db, home_id)


@router.patch("/store-chains/{chain_id}")
async def rename_chain(
    chain_id: str,
    request: ChainRename,
    _: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Rename a chain. The old name keeps resolving to it. 409 (with ``conflict_id``) if
    another chain already has that name."""
    try:
        chain = stores_service.rename_chain(db, chain_id, request.name)
    except stores_service.StoreError as e:
        return _conflict(e)
    if chain is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Store not found")
    return _chain_json(chain)


@router.get("/homes/{home_id}/store-discounts")
async def list_discounts(home_id: str, _: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """This home's store discounts (cards, memberships): ``{chain id: [{id, name, percent, cents_per_unit, applies_to, active}]}``."""
    from app.services import discounts
    return discounts.list_for_home(db, home_id)


@router.put("/homes/{home_id}/store-chains/{chain_id}/discounts")
async def set_discounts(home_id: str, chain_id: str, request: DiscountsIn, _: CurrentUser = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    """Replace this home's discounts at a store. They are taken off prices wherever the app works out where something
    is cheapest (trip planner, shopping list): ``percent`` off prices it applies to (everything, fuel only, or everything
    but fuel), and for fuel ``cents_per_unit`` off per gallon. An empty list removes them."""
    from app.services import discounts
    try:
        return discounts.set_for_chain(db, home_id, chain_id, [d.model_dump() for d in request.discounts])
    except discounts.DiscountError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.put("/store-chains/{chain_id}/web")
async def set_chain_web(
    chain_id: str,
    request: ChainWeb,
    _: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Set a chain's website, product search page and weekly ad page. Online price, deal and hours lookups
    open these directly before searching. ``400`` if one is not a web address (or the search page has no {query})."""
    try:
        chain = stores_service.set_chain_web(db, chain_id, request.model_dump(exclude_unset=True))
    except stores_service.StoreError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    if chain is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Store not found")
    return _chain_json(chain)


@router.post("/store-chains/{chain_id}/merge")
async def merge_chain(
    chain_id: str,
    request: ChainMerge,
    _: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Merge this chain into another (administrators only).

    All locations, receipts and prices move to the target. By default the locations are also
    combined, so the merged store is a single store holding every receipt; send
    ``combine_locations: false`` to keep each location separate."""
    try:
        return stores_service.merge_chains(db, chain_id, request.into_chain_id, request.combine_locations)
    except stores_service.StoreError as e:
        return _conflict(e)


@router.delete("/store-chains/{chain_id}")
async def delete_chain(
    chain_id: str,
    _: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Delete a chain that has no receipts (administrators only)."""
    try:
        deleted = stores_service.delete_chain(db, chain_id)
    except stores_service.StoreError as e:
        return _conflict(e)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Store not found")
    return {"status": "deleted", "chain_id": chain_id}


@router.post("/store-chains/{chain_id}/locations", status_code=status.HTTP_201_CREATED)
async def add_location(
    chain_id: str,
    request: LocationData,
    _: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Add a location to a chain by hand."""
    try:
        location = stores_service.create_location(db, chain_id, request.model_dump(exclude_unset=True))
    except stores_service.StoreError as e:
        return _conflict(e)
    if location is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Store not found")
    return _location_json(location)


@router.patch("/store-locations/{location_id}")
async def update_location(
    location_id: str,
    request: LocationData,
    _: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Edit a location's store number, label and address, or move it to another chain."""
    try:
        location = stores_service.update_location(db, location_id, request.model_dump(exclude_unset=True))
    except stores_service.StoreError as e:
        return _conflict(e)
    if location is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Location not found")
    return _location_json(location)


@router.post("/store-locations/{location_id}/merge")
async def merge_location(
    location_id: str,
    request: LocationMerge,
    _: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Merge this location into another; receipts and prices move with it (administrators only)."""
    try:
        stores_service.merge_locations(db, location_id, request.into_location_id)
    except stores_service.StoreError as e:
        return _conflict(e)
    return {"status": "merged", "location_id": location_id, "into_location_id": request.into_location_id}


@router.delete("/store-locations/{location_id}")
async def delete_location(
    location_id: str,
    _: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Delete a location with no receipts (administrators only)."""
    try:
        deleted = stores_service.delete_location(db, location_id)
    except stores_service.StoreError as e:
        return _conflict(e)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Location not found")
    return {"status": "deleted", "location_id": location_id}
