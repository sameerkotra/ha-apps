"""Homes, users, stores and price history.

Every signed-in Home Assistant user can read and add data in any home. Only
administrators (the app's admin_users option) create, rename and delete homes.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth import ADMIN_USERS, CurrentUser, get_current_user, no_admin_yet, require_admin
from app.db import get_db
from app.db.models import (
    Address,
    CommonItem,
    PriceObservation,
    Receipt,
    StoreChain,
    StoreLocation,
    User,
)
from app.services import homes as homes_service

router = APIRouter(prefix="/api/v1", tags=["homes"])


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #

class MeResponse(BaseModel):
    id: str
    display_name: str
    username: str | None = None
    is_admin: bool
    anonymous: bool = False
    no_admin_yet: bool = False


class HomeResponse(BaseModel):
    id: str
    name: str
    created_at: datetime | None = None
    receipt_count: int = 0
    pending_count: int = 0


class HomeRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)


class UserResponse(BaseModel):
    id: str
    display_name: str | None
    is_admin: bool


class StoreResponse(BaseModel):
    location_id: str
    chain_id: str
    name: str
    store_number: str | None = None
    address: str | None = None
    city: str | None = None
    receipt_count: int
    last_purchase_date: str | None = None


class PriceStore(BaseModel):
    store_location_id: str
    store_name: str
    store_number: str | None = None
    city: str | None = None
    unit_price: float | None
    unit: str | None
    line_total: float | None
    purchase_date: str
    currency: str
    cheapest: bool = False


class PriceItem(BaseModel):
    item_id: str
    name: str
    last_purchase_date: str
    observation_count: int
    stores: list[PriceStore]


# --------------------------------------------------------------------------- #
# Current user
# --------------------------------------------------------------------------- #

@router.get("/me", response_model=MeResponse)
async def me(user: CurrentUser = Depends(get_current_user)):
    """The signed-in user, whether they are an administrator, and whether anyone is yet."""
    return MeResponse(
        id=user.id,
        display_name=user.display_name,
        username=user.username,
        is_admin=user.is_admin,
        no_admin_yet=no_admin_yet(),
    )


@router.get("/whoami")
async def whoami(request: Request, user: CurrentUser = Depends(get_current_user)):
    """"How the app sees you": exactly what Home Assistant sent and whether it matched admin_users.

    Only the person's own identity and a COUNT of admin_users entries are returned, never the list."""
    return {
        "userId": user.id,
        "username": request.headers.get("x-remote-user-name"),
        "displayName": request.headers.get("x-remote-user-display-name"),
        "nameSent": bool(request.headers.get("x-remote-user-name")),
        "isAdmin": user.is_admin,
        "adminEntries": len(ADMIN_USERS),
        "noAdminYet": no_admin_yet(),
    }


@router.get("/users", response_model=list[UserResponse])
async def list_users(_: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    """Everyone who has opened the app (administrators only), and whether each is an administrator
    (the app's admin_users option; a match by login name shows once that person has opened the app)."""
    users = db.query(User).order_by(func.lower(func.coalesce(User.display_name, User.id))).all()
    return [UserResponse(id=u.id, display_name=u.display_name, is_admin=u.role == "ADMIN") for u in users]


# --------------------------------------------------------------------------- #
# Homes
# --------------------------------------------------------------------------- #

def _home_response(db: Session, home) -> HomeResponse:
    from app.db.models import ReceiptDraft

    receipts = db.query(func.count(Receipt.id)).filter(Receipt.home_id == home.id).scalar() or 0
    pending = (
        db.query(func.count(ReceiptDraft.id))
        .filter(
            ReceiptDraft.home_id == home.id,
            ReceiptDraft.status.in_(["PROCESSING", "NEEDS_REVIEW", "EXTRACTION_FAILED"]),
        )
        .scalar()
        or 0
    )
    return HomeResponse(
        id=home.id, name=home.name, created_at=home.created_at,
        receipt_count=receipts, pending_count=pending,
    )


@router.get("/homes", response_model=list[HomeResponse])
async def list_homes(_: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """All homes. Visible to every signed-in user."""
    return [_home_response(db, h) for h in homes_service.list_homes(db)]


@router.post("/homes", response_model=HomeResponse, status_code=status.HTTP_201_CREATED)
async def create_home(
    request: HomeRequest,
    user: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Create a home (administrators only)."""
    try:
        home = homes_service.create_home(db, request.name, user.id)
    except homes_service.HomeError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    return _home_response(db, home)


@router.patch("/homes/{home_id}", response_model=HomeResponse)
async def rename_home(
    home_id: str,
    request: HomeRequest,
    _: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Rename a home (administrators only)."""
    try:
        home = homes_service.rename_home(db, home_id, request.name)
    except homes_service.HomeError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    if home is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Home not found")
    return _home_response(db, home)


@router.delete("/homes/{home_id}")
async def delete_home(
    home_id: str,
    _: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Delete an empty home (administrators only)."""
    try:
        deleted = homes_service.delete_home(db, home_id)
    except homes_service.HomeError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Home not found")
    return {"status": "deleted", "home_id": home_id}


def _require_home(db: Session, home_id: str):
    home = homes_service.get_home(db, home_id)
    if home is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Home not found")
    return home


# --------------------------------------------------------------------------- #
# Stores and prices
# --------------------------------------------------------------------------- #

@router.get("/stores/chains", response_model=list[str])
async def known_chains(_: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Names of every store chain seen so far (for autocomplete when naming a store)."""
    return [c.name for c in db.query(StoreChain).order_by(func.lower(StoreChain.name)).all()]


@router.get("/homes/{home_id}/stores", response_model=list[StoreResponse])
async def home_stores(
    home_id: str,
    _: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Stores this home has bought from, most recently visited first."""
    _require_home(db, home_id)
    rows = (
        db.query(
            StoreLocation, StoreChain, Address,
            func.count(Receipt.id), func.max(Receipt.purchase_date),
        )
        .join(StoreChain, StoreLocation.store_chain_id == StoreChain.id)
        .outerjoin(Address, StoreLocation.address_id == Address.id)
        .join(Receipt, Receipt.store_location_id == StoreLocation.id)
        .filter(Receipt.home_id == home_id)
        .group_by(StoreLocation.id, StoreChain.id, Address.id)
        .order_by(func.max(Receipt.purchase_date).desc())
        .all()
    )
    return [
        StoreResponse(
            location_id=loc.id,
            chain_id=chain.id,
            name=chain.name,
            store_number=loc.store_number,
            address=addr.raw_address if addr else None,
            city=addr.city if addr else None,
            receipt_count=count,
            last_purchase_date=last,
        )
        for loc, chain, addr, count, last in rows
    ]


@router.get("/homes/{home_id}/prices", response_model=list[PriceItem])
async def home_prices(
    home_id: str,
    q: str | None = None,
    limit: int = 100,
    _: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Latest price of each item at each store, most recently bought items first.

    ``cheapest`` marks the lowest per-unit price among stores that report the same unit
    as the most recent observation, so a per-pound price is never compared with a
    per-item one.
    """
    _require_home(db, home_id)
    limit = max(1, min(limit, 300))

    query = (
        db.query(PriceObservation, CommonItem, StoreChain, StoreLocation, Address)
        .join(CommonItem, PriceObservation.common_item_id == CommonItem.id)
        .join(StoreChain, PriceObservation.store_chain_id == StoreChain.id)
        .join(StoreLocation, PriceObservation.store_location_id == StoreLocation.id)
        .outerjoin(Address, StoreLocation.address_id == Address.id)
        .filter(PriceObservation.home_id == home_id)
    )
    if q and q.strip():
        query = query.filter(func.lower(CommonItem.name).contains(q.strip().lower()))
    rows = query.order_by(PriceObservation.purchase_date.desc(), PriceObservation.created_at.desc()).all()

    items: dict[str, dict] = {}
    for obs, item, chain, loc, addr in rows:  # newest first
        entry = items.setdefault(item.id, {
            "item_id": item.id, "name": item.name, "last_purchase_date": obs.purchase_date,
            "observation_count": 0, "stores": {},
        })
        entry["observation_count"] += 1
        if loc.id not in entry["stores"]:  # first seen == latest for this store
            entry["stores"][loc.id] = PriceStore(
                store_location_id=loc.id,
                store_name=chain.name,
                store_number=loc.store_number,
                city=addr.city if addr else None,
                unit_price=obs.unit_price,
                unit=obs.unit_price_unit,
                line_total=obs.line_total,
                purchase_date=obs.purchase_date,
                currency=obs.currency_code,
            )

    result = []
    for entry in list(items.values())[:limit]:
        stores = list(entry["stores"].values())
        reference_unit = stores[0].unit  # stores are in order of first (newest) observation
        comparable = [s for s in stores if s.unit_price is not None and s.unit == reference_unit]
        if len(comparable) > 1:
            best = min(s.unit_price for s in comparable)
            for s in comparable:
                s.cheapest = s.unit_price == best
        stores.sort(key=lambda s: (s.unit != reference_unit, s.unit_price if s.unit_price is not None else 1e12))
        result.append(PriceItem(
            item_id=entry["item_id"], name=entry["name"],
            last_purchase_date=entry["last_purchase_date"],
            observation_count=entry["observation_count"], stores=stores,
        ))
    return result
