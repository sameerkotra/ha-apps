"""Homes, users, stores and price history.

Every signed-in Home Assistant user can read and add data in any home. Only
administrators (the app's admin_users option) create, rename and delete homes.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, StrictBool
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import tools
from app.api.common import require_home as _require_home
from app.auth import ADMIN_USERS, CurrentUser, get_current_user, no_admin_yet, require_admin
from app.common import whoami as whoami_core
from app.db import get_db
from app.db.models import (
    Address,
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
    noAdmin: bool = False          # admin_users is empty: every page shows "No admin yet"
    page: str | None = None        # the app's sidebar page: links open one of its pages there (deeplink.js)
    assistant: bool = False        # an admin lets the Household Assistant ask (App settings)
    assistantOk: bool = True       # this person's own "Let the Household Assistant answer for me"


class AssistantIn(BaseModel):
    assistantOk: StrictBool


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


# --------------------------------------------------------------------------- #
# Current user
# --------------------------------------------------------------------------- #

@router.get("/me", response_model=MeResponse)
def me(user: CurrentUser = Depends(get_current_user)):
    """The signed-in user, whether they are an administrator, and whether anyone is yet."""
    return MeResponse(
        id=user.id,
        display_name=user.display_name,
        username=user.username,
        is_admin=user.is_admin,
        noAdmin=no_admin_yet(),
        page=tools.PANEL["value"],
        **_assistant(user.id),
    )


def _assistant(user_id: str) -> dict:
    from app import app_settings
    from app.db import get_db_session
    from app.db.models import User
    with get_db_session()() as db:
        u = db.get(User, user_id)
        ok = bool(u.assistant_ok) if u is not None and u.assistant_ok is not None else True
    return {"assistant": bool(app_settings.values().get("assistant_answers")), "assistantOk": ok}


@router.put("/me/assistant")
def set_assistant(body: AssistantIn, user: CurrentUser = Depends(get_current_user)):
    """The person's own "Let the Household Assistant answer for me" (tools.py)."""
    from app.db import get_db_session
    from app.db.models import User
    with get_db_session()() as db:
        u = db.get(User, user.id)
        if u is None:
            raise HTTPException(status_code=404, detail="Open the app once first")
        u.assistant_ok = 1 if body.assistantOk else 0
        db.commit()
    return _assistant(user.id)


@router.get("/whoami")
def whoami(request: Request, user: CurrentUser = Depends(get_current_user)):
    """"How the app sees you": exactly what Home Assistant sent and whether it matched admin_users.

    Only the person's own identity and a COUNT of admin_users entries are returned, never the list."""
    return whoami_core.build(
        request, user_id=user.id, username=request.headers.get("x-remote-user-name"),
        display_name=request.headers.get("x-remote-user-display-name"), is_admin=user.is_admin,
        admin_entries=len(ADMIN_USERS))


@router.get("/users", response_model=list[UserResponse])
def list_users(_: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
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
def list_homes(_: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """All homes. Visible to every signed-in user."""
    return [_home_response(db, h) for h in homes_service.list_homes(db)]


@router.post("/homes", response_model=HomeResponse, status_code=status.HTTP_201_CREATED)
def create_home(
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
def rename_home(
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
def delete_home(
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




# --------------------------------------------------------------------------- #
# Stores and prices
# --------------------------------------------------------------------------- #

@router.get("/stores/chains", response_model=list[str])
def known_chains(_: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Names of every store chain seen so far (for autocomplete when naming a store)."""
    return [c.name for c in db.query(StoreChain).order_by(func.lower(StoreChain.name)).all()]


@router.get("/homes/{home_id}/stores", response_model=list[StoreResponse])
def home_stores(
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
