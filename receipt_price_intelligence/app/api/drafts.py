"""Receipt drafts API endpoints.

The routes are plain functions (FastAPI runs them on its thread pool): they use the database, which would otherwise
hold up the event loop and every other request with it.
"""

from datetime import datetime
from typing import Literal

from fastapi.responses import JSONResponse
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth import get_current_user_id
from app.db import get_db
from app.db.models import ReceiptDraft, ReceiptImage
from app.services import background
from app.services import deals as deals_service
from app.services import drafts as drafts_service
from app.services import homes as homes_service
from app.services import importer

router = APIRouter(prefix="/api/v1/receipt-drafts", tags=["receipt drafts"])


# Request/Response models

# A draft's own contents can be changed only while it is being read or reviewed
EDITABLE_STATUSES = ("NEEDS_REVIEW", "PROCESSING", "EXTRACTION_FAILED")
MONEY = 1e9                       # no receipt amount is anywhere near this

class DraftCreateRequest(BaseModel):
    """Request to create a new draft."""

    source: Literal["camera", "upload"] = Field(default="camera", description="Where the receipt came from")
    home_id: str | None = Field(
        None, max_length=36, description="Home the receipt belongs to (optional when exactly one home exists)"
    )


class DraftUpdateRequest(BaseModel):
    """Request to update a draft. Only the fields that are sent are changed. (The model's own output is never
    sent back: it is the app's record of what was read.)"""

    # Receipt header corrections made during review. An empty string clears the field.
    store_name: str | None = Field(None, max_length=255, description="Store name")
    store_address: str | None = Field(None, max_length=500, description="Physical store address")
    store_number: str | None = Field(None, max_length=50, description="Store / branch number")
    receipt_number: str | None = Field(None, max_length=100, description="Receipt or transaction number")
    purchase_date: str | None = Field(None, max_length=20, description="Purchase date, YYYY-MM-DD")
    purchase_time: str | None = Field(None, max_length=20, description="Purchase time, HH:MM")
    currency: str | None = Field(None, max_length=3, description="3-letter currency code")
    subtotal: float | None = Field(None, ge=-MONEY, le=MONEY, description="Subtotal amount")
    discount_total: float | None = Field(None, ge=-MONEY, le=MONEY, description="Receipt-level discount total")
    tax_total: float | None = Field(None, ge=-MONEY, le=MONEY, description="Tax total")
    fee_total: float | None = Field(None, ge=-MONEY, le=MONEY, description="Fees, deposits, and surcharges")
    grand_total: float | None = Field(None, ge=-MONEY, le=MONEY, description="Grand total")
    tags: list[str] | None = Field(None, max_length=30, description="Tags such as business or warranty (replaces the list)")


class DraftItemInput(BaseModel):
    """One item in a full replacement of a draft's items."""

    receipt_description: str = Field(..., min_length=1, max_length=500)
    item_type: str | None = Field(None, max_length=50)
    quantity: float | None = Field(None, ge=-MONEY, le=MONEY)
    weight_value: float | None = Field(None, ge=-MONEY, le=MONEY)
    weight_unit: str | None = Field(None, max_length=50)
    unit_price: float | None = Field(None, ge=-MONEY, le=MONEY)
    unit_price_unit: str | None = Field(None, max_length=50)
    line_subtotal: float | None = Field(None, ge=-MONEY, le=MONEY)
    discount_amount: float | None = Field(None, ge=-MONEY, le=MONEY)
    line_total: float | None = Field(None, ge=-MONEY, le=MONEY)
    tax_amount: float | None = Field(None, ge=-MONEY, le=MONEY)
    extraction_confidence: float | None = Field(None, ge=0, le=1)
    suggested_common_item_id: str | None = Field(None, max_length=36)
    suggested_common_item_name: str | None = Field(None, max_length=255)
    original_description: str | None = Field(None, max_length=500)


class DraftItemsReplaceRequest(BaseModel):
    """Replace all items on a draft (the review screen saves its edited list this way)."""

    items: list[DraftItemInput] = Field(..., max_length=500)


class DraftItemResponse(BaseModel):
    """Response model for draft items."""

    id: str
    receipt_description: str
    suggested_common_item_id: str | None
    suggested_common_item_name: str | None
    original_description: str | None = None
    item_type: str | None
    quantity: float | None
    weight_value: float | None
    weight_unit: str | None
    unit_price: float | None
    unit_price_unit: str | None
    line_subtotal: float | None
    discount_amount: float | None
    line_total: float | None
    tax_amount: float | None
    extraction_confidence: float | None
    normalization_confidence: float | None
    filled_note: str | None = None  # weight / pack size / quantity filled in from the last purchase at this store

    class Config:
        from_attributes = True


class DraftTaxResponse(BaseModel):
    """Response model for draft tax records."""

    id: str
    tax_type: str | None
    tax_name: str | None
    tax_rate: float | None
    taxable_amount: float | None
    tax_amount: float | None

    class Config:
        from_attributes = True


class ImageResponse(BaseModel):
    """Response model for image metadata."""

    id: str
    image_type: str
    mime_type: str | None
    file_size: int | None
    width: int | None
    height: int | None
    created_at: datetime

    class Config:
        from_attributes = True


class DraftResponse(BaseModel):
    """Full draft response with items, taxes, and images."""

    id: str
    user_id: str
    home_id: str | None = None
    status: str
    source: str
    raw_ocr_text: str | None
    raw_llm_output: str | None
    extraction_model: str | None
    extraction_prompt_version: str | None
    extraction_schema_version: str | None
    created_at: datetime
    expires_at: datetime | None
    items: list[DraftItemResponse] = []
    taxes: list[DraftTaxResponse] = []
    images: list[ImageResponse] = []
    editing_saved: bool = False  # a saved receipt reopened for editing; saving replaces it

    class Config:
        from_attributes = True


class ApprovalSummary(BaseModel):
    """What approving a receipt recorded."""

    receipt_id: str
    store_name: str
    store_location_id: str
    price_observations: int
    items_matched: int
    items_created: int
    tax_observations: int


class ApproveResponse(DraftResponse):
    """The approved draft plus a summary of the records created."""

    approval: ApprovalSummary


class DraftApproveRequest(BaseModel):
    """Request to approve a draft."""

    allow_duplicate: bool = Field(False, description="Save even if a matching saved receipt exists")
    purchase_date: str | None = Field(None, max_length=20, description="Override purchase date")


class DraftRejectRequest(BaseModel):
    """Request to reject a draft."""

    reason: str | None = Field(None, max_length=500, description="Optional rejection reason")


class DraftSummary(BaseModel):
    """One line of the Receipts page: what the card shows, without the items, taxes and photos."""

    id: str
    status: str
    editing_saved: bool = False
    created_at: datetime | None
    store_name: str | None = None
    purchase_date: str | None = None
    currency: str | None = None
    grand_total: float | None = None
    items_total: float | None = None
    item_count: int = 0
    tags: list[str] = []


class DraftSummaryPage(BaseModel):
    drafts: list[DraftSummary]
    more: bool = Field(False, description="There are older ones: ask again with before=<the last created_at>")


# Helper functions

def _build_draft_response(db: Session, draft: ReceiptDraft) -> DraftResponse:
    """Assemble the full draft response (items, taxes, images)."""
    items = drafts_service.get_draft_items(db, draft.id)
    taxes = drafts_service.get_draft_taxes(db, draft.id)
    images = (
        db.query(ReceiptImage)
        .filter(ReceiptImage.receipt_draft_id == draft.id)
        .order_by(text("receipt_images.rowid"))  # upload order == page order
        .all()
    )

    return DraftResponse(
        id=draft.id,
        user_id=draft.user_id,
        home_id=draft.home_id,
        status=draft.status,
        source=draft.source,
        raw_ocr_text=draft.raw_ocr_text,
        raw_llm_output=draft.raw_llm_output,
        extraction_model=draft.extraction_model,
        extraction_prompt_version=draft.extraction_prompt_version,
        extraction_schema_version=draft.extraction_schema_version,
        created_at=draft.created_at,
        expires_at=draft.expires_at,
        items=[DraftItemResponse.model_validate(i) for i in items],
        taxes=[DraftTaxResponse.model_validate(t) for t in taxes],
        images=[ImageResponse.model_validate(img) for img in images],
        editing_saved=bool(draft.approved_receipt_id) and draft.status == "NEEDS_REVIEW",
    )


def _build_draft_responses(db: Session, drafts: list[ReceiptDraft]) -> list[DraftResponse]:
    """Full responses for many drafts with three queries in all (not three per draft)."""
    ids = [d.id for d in drafts]
    items = drafts_service.items_by_draft(db, ids)
    taxes = drafts_service.taxes_by_draft(db, ids)
    images = drafts_service.images_by_draft(db, ids)
    return [DraftResponse(
        id=d.id, user_id=d.user_id, home_id=d.home_id, status=d.status, source=d.source,
        raw_ocr_text=d.raw_ocr_text, raw_llm_output=d.raw_llm_output, extraction_model=d.extraction_model,
        extraction_prompt_version=d.extraction_prompt_version, extraction_schema_version=d.extraction_schema_version,
        created_at=d.created_at, expires_at=d.expires_at,
        items=[DraftItemResponse.model_validate(i) for i in items.get(d.id, [])],
        taxes=[DraftTaxResponse.model_validate(t) for t in taxes.get(d.id, [])],
        images=[ImageResponse.model_validate(img) for img in images.get(d.id, [])],
        editing_saved=bool(d.approved_receipt_id) and d.status == "NEEDS_REVIEW",
    ) for d in drafts]


def _require_editable(draft: ReceiptDraft) -> None:
    """409 unless the draft is still being read or reviewed (a saved receipt is reopened first)."""
    if draft.status not in EDITABLE_STATUSES:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="This receipt is already saved or discarded; reopen it to change it")


def _require_draft(db: Session, draft_id: str) -> ReceiptDraft:
    """Load a draft or raise 404. Drafts are shared: any signed-in user may open them."""
    draft = drafts_service.get_draft(db, draft_id)
    if not draft:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Draft not found")
    return draft


@router.post("", response_model=DraftResponse, status_code=status.HTTP_201_CREATED)
def create_draft(
    request: DraftCreateRequest,
    db: Session = Depends(get_db),
):
    """
    Create a new receipt draft.

    The draft starts in PROCESSING status and moves to NEEDS_REVIEW
    once extraction is complete.
    """
    try:
        home_id = homes_service.resolve_home_id(db, request.home_id)
    except homes_service.HomeError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    if home_id and importer.room_for_drafts(db, home_id) <= 0:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Too many receipts are waiting for review in this home; review some first")

    draft = drafts_service.create_draft(
        db=db,
        user_id=get_current_user_id(),
        source=request.source,
        home_id=home_id,
    )
    return _build_draft_response(db, draft)


@router.get("", response_model=list[DraftResponse])
def list_drafts(
    status_filter: str | None = Query(None, max_length=50),
    home_id: str | None = Query(None, max_length=36),
    limit: int = Query(200, ge=1, le=500),
    before: datetime | None = Query(None, description="Only drafts created before this (the next page)"),
    db: Session = Depends(get_db),
):
    """
    List drafts (shared by all users), newest first, in full (items, taxes and photos), at most ``limit``.

    Optionally filter by home and/or status (PROCESSING, NEEDS_REVIEW, EXTRACTION_FAILED,
    APPROVED, REJECTED). The Receipts page uses the lighter ``/summaries``.
    """
    drafts = drafts_service.get_drafts(db=db, home_id=home_id, status=status_filter, limit=limit, before=before)
    return _build_draft_responses(db, drafts)


@router.get("/summaries", response_model=DraftSummaryPage)
def list_draft_summaries(
    home_id: str = Query(..., max_length=36),
    group: Literal["review", "saved"] = Query("review", description="review: being read or waiting for review; "
                                                                   "saved: approved receipts"),
    limit: int = Query(100, ge=1, le=500),
    before: datetime | None = Query(None, description="The next page: the last line's created_at …"),
    before_id: str | None = Query(None, max_length=36, description="… and its id"),
    db: Session = Depends(get_db),
):
    """The Receipts page's list: one line per receipt (store, date, total, tags, item count), newest first."""
    statuses = EDITABLE_STATUSES if group == "review" else ("APPROVED",)
    rows, more = drafts_service.draft_summaries(db, home_id, statuses, limit, before, before_id)
    return DraftSummaryPage(drafts=[DraftSummary(**r) for r in rows], more=more)


@router.get("/{draft_id}", response_model=DraftResponse)
def get_draft(
    draft_id: str,
    db: Session = Depends(get_db),
):
    """Get a specific draft by ID."""
    draft = _require_draft(db, draft_id)
    return _build_draft_response(db, draft)


@router.patch("/{draft_id}", response_model=DraftResponse)
def update_draft(
    draft_id: str,
    request: DraftUpdateRequest,
    db: Session = Depends(get_db),
):
    """
    Update draft data.

    Allows correcting the receipt header (store, date, totals) before approval.
    Only fields present in the request body are changed.
    """
    _require_editable(_require_draft(db, draft_id))
    sent = request.model_dump(exclude_unset=True)
    for tag in sent.get("tags") or []:
        if len(tag) > 50:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="A tag can have up to 50 characters")

    header_updates = {k: v for k, v in sent.items() if k in drafts_service.HEADER_FIELDS}
    if header_updates:
        try:
            drafts_service.update_draft_header(db, draft_id, header_updates)
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))

    return _build_draft_response(db, drafts_service.get_draft(db, draft_id))


class PurchaseDateRequest(BaseModel):
    purchase_date: str = Field(..., min_length=8, max_length=20, description="YYYY-MM-DD")


@router.patch("/{draft_id}/purchase-date", response_model=DraftResponse)
def change_saved_receipt_date(
    draft_id: str,
    request: PurchaseDateRequest,
    db: Session = Depends(get_db),
):
    """Correct the purchase date of a receipt that is already saved.

    Updates the receipt and the price history recorded from it, so spending, price trends and the
    best-price check all follow the new date. Any user can do this (data is shared within a home).
    ``400`` for an invalid, pre-2000 or future date; ``409`` if the receipt is not saved yet.
    """
    _require_draft(db, draft_id)
    try:
        draft = drafts_service.update_saved_receipt_date(db, draft_id, request.purchase_date)
    except drafts_service.SavedReceiptError as e:
        code = status.HTTP_409_CONFLICT if "Only a saved receipt" in str(e) else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=code, detail=str(e))

    # Keep the stored best-price results and sensors in step with the new date (best effort)
    _refresh_home_later(draft.home_id)
    return _build_draft_response(db, draft)


@router.get("/{draft_id}/duplicates")
def draft_duplicates(draft_id: str, db: Session = Depends(get_db)):
    """Saved receipts that look like this draft (same home, store and day, and the same total or
    receipt number). Empty when nothing matches. Saving anyway needs ``allow_duplicate``."""
    draft = _require_draft(db, draft_id)
    return drafts_service.find_duplicates(db, draft)


@router.post("/{draft_id}/reopen", response_model=DraftResponse)
def reopen_saved_receipt(draft_id: str, db: Session = Depends(get_db)):
    """Open a saved receipt for full editing. It stays in the history until the edited version is
    saved (which replaces it); ``POST /{id}/discard-edit`` abandons the edit."""
    _require_draft(db, draft_id)
    try:
        draft = drafts_service.reopen_saved_receipt(db, draft_id)
    except drafts_service.SavedReceiptError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    return _build_draft_response(db, draft)


@router.post("/{draft_id}/discard-edit", response_model=DraftResponse)
def discard_saved_edit(draft_id: str, db: Session = Depends(get_db)):
    """Abandon an edit of a saved receipt: it goes back to exactly what was saved."""
    _require_draft(db, draft_id)
    try:
        draft = drafts_service.discard_saved_edit(db, draft_id)
    except drafts_service.SavedReceiptError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    return _build_draft_response(db, draft)


@router.delete("/{draft_id}/saved")
def delete_saved_receipt(draft_id: str, db: Session = Depends(get_db)):
    """Delete a saved receipt completely, including the prices and tax records recorded from it."""
    draft = _require_draft(db, draft_id)
    home_id = draft.home_id
    try:
        drafts_service.delete_saved_receipt(db, draft_id)
    except drafts_service.SavedReceiptError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    _refresh_home_later(home_id)
    return {"status": "deleted", "draft_id": draft_id}


def _refresh_home_later(home_id: str | None) -> None:
    """Bring the stored best-price results and Home Assistant sensors up to date, after the answer (best effort; many
    changes in a row make one refresh, and one more after it)."""
    if home_id:
        background.coalesce(("refresh-home", home_id), deals_service.refresh_home_id, home_id)


class TagsRequest(BaseModel):
    tags: list[str] = Field(default_factory=list, max_length=30)


@router.patch("/{draft_id}/tags", response_model=DraftResponse)
def set_saved_tags(draft_id: str, request: TagsRequest, db: Session = Depends(get_db)):
    """Change the tags of a saved receipt (for example ``business``, ``reimbursable``, ``warranty``).
    Tags on a receipt still in review are sent with the header update instead."""
    _require_draft(db, draft_id)
    try:
        draft = drafts_service.set_saved_tags(db, draft_id, request.tags)
    except drafts_service.SavedReceiptError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    return _build_draft_response(db, draft)


@router.post("/{draft_id}/manual", response_model=DraftResponse)
def start_manual_entry(
    draft_id: str,
    db: Session = Depends(get_db),
):
    """Enter a receipt by hand when the model could not read it.

    Only for a draft in ``EXTRACTION_FAILED``: it moves to ``NEEDS_REVIEW`` with an empty header
    and no lines, then the usual PATCH / PUT items endpoints fill it in.
    """
    _require_draft(db, draft_id)
    try:
        draft = drafts_service.start_manual_entry(db, draft_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    return _build_draft_response(db, draft)


@router.put("/{draft_id}/items", response_model=DraftResponse)
def replace_draft_items(
    draft_id: str,
    request: DraftItemsReplaceRequest,
    db: Session = Depends(get_db),
):
    """Replace every item on a draft with the supplied list (used by the review screen)."""
    _require_editable(_require_draft(db, draft_id))

    drafts_service.update_draft_items(
        db, draft_id, [item.model_dump() for item in request.items]
    )
    return _build_draft_response(db, drafts_service.get_draft(db, draft_id))


@router.post("/{draft_id}/approve", response_model=ApproveResponse)
def approve_draft(
    draft_id: str,
    request: DraftApproveRequest,
    db: Session = Depends(get_db),
):
    """
    Approve a draft and create permanent receipt records.

    This is the ONLY path to create permanent receipt records. Approving also records the
    store, matches or creates items, and adds price and tax observations to the home's
    history. The store name and purchase date are required.
    """
    user_id = get_current_user_id()
    draft = _require_draft(db, draft_id)

    if not drafts_service.get_draft_items(db, draft_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot approve a receipt with no items",
        )

    if request.purchase_date:
        try:
            drafts_service.update_draft_header(db, draft_id, {"purchase_date": request.purchase_date})
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))

    if not request.allow_duplicate:
        duplicates = drafts_service.find_duplicates(db, draft)
        if duplicates:
            return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={
                "detail": "This looks like a receipt you already saved",
                "duplicates": duplicates,
            })

    # Mark draft as NEEDS_REVIEW first if it is still marked as processing
    if draft.status == "PROCESSING":
        drafts_service.update_draft(db, draft_id, {"status": "NEEDS_REVIEW"})

    try:
        result = drafts_service.approve_draft(db, draft_id, user_id)
    except drafts_service.ApprovalError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))

    if not result:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to approve draft. It may not be ready for approval (status must be NEEDS_REVIEW).",
        )
    receipt, summary = result
    # Paid more than the store posted that week? (in the background; notified if that option is on)
    from app.services import lookout
    lookout.check_receipt_soon(draft.home_id, receipt.id)

    # Keep the best-price results, sensors and alerts in step with the new receipt (best effort)
    _refresh_home_later(draft.home_id)

    base = _build_draft_response(db, drafts_service.get_draft(db, draft_id))
    return ApproveResponse(**base.model_dump(), approval=ApprovalSummary(**summary))


@router.post("/{draft_id}/reject", response_model=dict)
def reject_draft(
    draft_id: str,
    request: DraftRejectRequest,
    db: Session = Depends(get_db),
):
    """
    Reject a draft without creating permanent records.

    The draft data is discarded and no receipt records are created.
    """
    user_id = get_current_user_id()

    success = drafts_service.reject_draft(db, draft_id, user_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Draft not found or not authorized",
        )

    return {"status": "rejected", "draft_id": draft_id}


@router.delete("/{draft_id}", response_model=dict)
def delete_draft(
    draft_id: str,
    db: Session = Depends(get_db),
):
    """
    Delete a draft (only if not yet reviewed).

    This removes the draft and all associated images.
    """
    user_id = get_current_user_id()

    success = drafts_service.delete_draft(db, draft_id, user_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Draft not found, may have already been approved/rejected, or not authorized",
        )

    return {"status": "deleted", "draft_id": draft_id}
