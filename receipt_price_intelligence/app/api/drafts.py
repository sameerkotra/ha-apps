"""Receipt drafts API endpoints."""

from datetime import datetime
import asyncio

from fastapi.responses import JSONResponse
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth import get_current_user_id
from app.db import get_db
from app.db.models import ReceiptDraft, ReceiptImage
from app.services import deals as deals_service
from app.services import drafts as drafts_service
from app.services import homes as homes_service

router = APIRouter(prefix="/api/v1/receipt-drafts", tags=["receipt drafts"])


# Request/Response models

class DraftCreateRequest(BaseModel):
    """Request to create a new draft."""

    source: str = Field(default="camera", description="Source of the draft")
    home_id: str | None = Field(
        None, description="Home the receipt belongs to (optional when exactly one home exists)"
    )


class DraftUpdateRequest(BaseModel):
    """Request to update a draft. Only the fields that are sent are changed."""

    raw_ocr_text: str | None = Field(None, description="Raw OCR text output")
    raw_llm_output: str | None = Field(None, description="Raw LLM extraction output")
    # Receipt header corrections made during review. An empty string clears the field.
    store_name: str | None = Field(None, description="Store name")
    store_address: str | None = Field(None, description="Physical store address")
    store_number: str | None = Field(None, description="Store / branch number")
    receipt_number: str | None = Field(None, description="Receipt or transaction number")
    purchase_date: str | None = Field(None, description="Purchase date, YYYY-MM-DD")
    purchase_time: str | None = Field(None, description="Purchase time, HH:MM")
    currency: str | None = Field(None, description="3-letter currency code")
    subtotal: float | None = Field(None, description="Subtotal amount")
    discount_total: float | None = Field(None, description="Receipt-level discount total")
    tax_total: float | None = Field(None, description="Tax total")
    fee_total: float | None = Field(None, description="Fees, deposits, and surcharges")
    grand_total: float | None = Field(None, description="Grand total")
    tags: list[str] | None = Field(None, description="Tags such as business or warranty (replaces the list)")


class DraftItemInput(BaseModel):
    """One item in a full replacement of a draft's items."""

    receipt_description: str = Field(..., min_length=1, max_length=500)
    item_type: str | None = Field(None, max_length=50)
    quantity: float | None = None
    weight_value: float | None = None
    weight_unit: str | None = Field(None, max_length=50)
    unit_price: float | None = None
    unit_price_unit: str | None = Field(None, max_length=50)
    line_subtotal: float | None = None
    discount_amount: float | None = None
    line_total: float | None = None
    tax_amount: float | None = None
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
    file_path: str
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

    # Optional overrides before approval
    store_chain_id: str | None = Field(None, description="Override store chain")
    store_location_id: str | None = Field(None, description="Override store location")
    purchase_date: str | None = Field(None, description="Override purchase date")


class DraftRejectRequest(BaseModel):
    """Request to reject a draft."""

    reason: str | None = Field(None, description="Optional rejection reason")


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


def _require_draft(db: Session, draft_id: str) -> ReceiptDraft:
    """Load a draft or raise 404. Drafts are shared: any signed-in user may open them."""
    draft = drafts_service.get_draft(db, draft_id)
    if not draft:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Draft not found")
    return draft


@router.post("", response_model=DraftResponse, status_code=status.HTTP_201_CREATED)
async def create_draft(
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

    draft = drafts_service.create_draft(
        db=db,
        user_id=get_current_user_id(),
        source=request.source,
        home_id=home_id,
    )
    return _build_draft_response(db, draft)


@router.get("", response_model=list[DraftResponse])
async def list_drafts(
    status_filter: str | None = None,
    home_id: str | None = None,
    db: Session = Depends(get_db),
):
    """
    List drafts (shared by all users), newest first.

    Optionally filter by home and/or status (PROCESSING, NEEDS_REVIEW, EXTRACTION_FAILED,
    APPROVED, REJECTED).
    """
    drafts = drafts_service.get_drafts(db=db, home_id=home_id, status=status_filter)
    return [_build_draft_response(db, draft) for draft in drafts]


@router.get("/{draft_id}", response_model=DraftResponse)
async def get_draft(
    draft_id: str,
    db: Session = Depends(get_db),
):
    """Get a specific draft by ID."""
    draft = _require_draft(db, draft_id)
    return _build_draft_response(db, draft)


@router.patch("/{draft_id}", response_model=DraftResponse)
async def update_draft(
    draft_id: str,
    request: DraftUpdateRequest,
    db: Session = Depends(get_db),
):
    """
    Update draft data.

    Allows correcting the receipt header (store, date, totals) before approval.
    Only fields present in the request body are changed.
    """
    _require_draft(db, draft_id)
    sent = request.model_dump(exclude_unset=True)

    raw_updates = {k: sent[k] for k in ("raw_ocr_text", "raw_llm_output") if sent.get(k) is not None}
    if raw_updates:
        drafts_service.update_draft(db, draft_id, raw_updates)

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
async def change_saved_receipt_date(
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
    _spawn(_refresh_home_later(draft.home_id))
    return _build_draft_response(db, draft)


@router.get("/{draft_id}/duplicates")
async def draft_duplicates(draft_id: str, db: Session = Depends(get_db)):
    """Saved receipts that look like this draft (same home, store and day, and the same total or
    receipt number). Empty when nothing matches. Saving anyway needs ``allow_duplicate``."""
    draft = _require_draft(db, draft_id)
    return drafts_service.find_duplicates(db, draft)


@router.post("/{draft_id}/reopen", response_model=DraftResponse)
async def reopen_saved_receipt(draft_id: str, db: Session = Depends(get_db)):
    """Open a saved receipt for full editing. It stays in the history until the edited version is
    saved (which replaces it); ``POST /{id}/discard-edit`` abandons the edit."""
    _require_draft(db, draft_id)
    try:
        draft = drafts_service.reopen_saved_receipt(db, draft_id)
    except drafts_service.SavedReceiptError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    return _build_draft_response(db, draft)


@router.post("/{draft_id}/discard-edit", response_model=DraftResponse)
async def discard_saved_edit(draft_id: str, db: Session = Depends(get_db)):
    """Abandon an edit of a saved receipt: it goes back to exactly what was saved."""
    _require_draft(db, draft_id)
    try:
        draft = drafts_service.discard_saved_edit(db, draft_id)
    except drafts_service.SavedReceiptError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    return _build_draft_response(db, draft)


@router.delete("/{draft_id}/saved")
async def delete_saved_receipt(draft_id: str, db: Session = Depends(get_db)):
    """Delete a saved receipt completely, including the prices and tax records recorded from it."""
    draft = _require_draft(db, draft_id)
    home_id = draft.home_id
    try:
        drafts_service.delete_saved_receipt(db, draft_id)
    except drafts_service.SavedReceiptError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    _spawn(_refresh_home_later(home_id))
    return {"status": "deleted", "draft_id": draft_id}


_background: set = set()


def _spawn(coro) -> None:
    """Run a coroutine after the response without holding it up (and keep a reference so it finishes)."""
    task = asyncio.create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)


async def _refresh_home_later(home_id: str | None) -> None:
    """Bring the stored best-price results and Home Assistant sensors up to date (best effort)."""
    if not home_id:
        return
    try:
        await asyncio.to_thread(deals_service.refresh_home_id, home_id)
    except Exception:  # noqa: BLE001
        pass


class TagsRequest(BaseModel):
    tags: list[str] = Field(default_factory=list, max_length=30)


@router.patch("/{draft_id}/tags", response_model=DraftResponse)
async def set_saved_tags(draft_id: str, request: TagsRequest, db: Session = Depends(get_db)):
    """Change the tags of a saved receipt (for example ``business``, ``reimbursable``, ``warranty``).
    Tags on a receipt still in review are sent with the header update instead."""
    _require_draft(db, draft_id)
    try:
        draft = drafts_service.set_saved_tags(db, draft_id, request.tags)
    except drafts_service.SavedReceiptError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    return _build_draft_response(db, draft)


@router.post("/{draft_id}/manual", response_model=DraftResponse)
async def start_manual_entry(
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
async def replace_draft_items(
    draft_id: str,
    request: DraftItemsReplaceRequest,
    db: Session = Depends(get_db),
):
    """Replace every item on a draft with the supplied list (used by the review screen)."""
    draft = _require_draft(db, draft_id)
    if draft.status not in ("NEEDS_REVIEW", "PROCESSING", "EXTRACTION_FAILED"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot edit items on a draft in status: {draft.status}",
        )

    drafts_service.update_draft_items(
        db, draft_id, [item.model_dump() for item in request.items]
    )
    return _build_draft_response(db, drafts_service.get_draft(db, draft_id))


@router.post("/{draft_id}/items", response_model=DraftItemResponse)
async def add_draft_item(
    draft_id: str,
    request: dict,
    db: Session = Depends(get_db),
):
    """Add an item to a draft."""
    _require_draft(db, draft_id)
    item = drafts_service.add_draft_item(db, draft_id, request)
    return DraftItemResponse.model_validate(item)


@router.post("/{draft_id}/taxes", response_model=DraftTaxResponse)
async def add_draft_tax(
    draft_id: str,
    request: dict,
    db: Session = Depends(get_db),
):
    """Add a tax record to a draft."""
    _require_draft(db, draft_id)
    tax = drafts_service.add_draft_tax(db, draft_id, request)
    return DraftTaxResponse.model_validate(tax)


@router.post("/{draft_id}/approve", response_model=ApproveResponse)
async def approve_draft(
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
    _spawn(_refresh_home_later(draft.home_id))

    base = _build_draft_response(db, drafts_service.get_draft(db, draft_id))
    return ApproveResponse(**base.model_dump(), approval=ApprovalSummary(**summary))


@router.post("/{draft_id}/reject", response_model=dict)
async def reject_draft(
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
async def delete_draft(
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
