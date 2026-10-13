"""Receipt extraction API endpoints."""

import asyncio
import json
import os
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db, get_db_session
from app.db.models import ReceiptDraft, ReceiptDraftItem, ReceiptImage
from app.logging_config import get_logger
from app.services import maintenance, receipt_llm
from app.services.drafts import get_draft as drafts_get_draft, update_draft as drafts_update_draft
from app.services.extraction import (
    FAILED_STATUS,
    ExtractionProgress,
    get_extraction_service,
)

logger = get_logger("extraction_api")

router = APIRouter(prefix="/api/v1/extraction", tags=["extraction"])

# Drafts that may be (re-)extracted. NEEDS_REVIEW is included so a bad extraction can be redone.
EXTRACTABLE_STATUSES = {"PROCESSING", "NEEDS_REVIEW", FAILED_STATUS}

# In-flight extractions: draft_id -> {"started": epoch seconds, "message": latest progress text}.
# In-process state; fine for a single-worker app.
_active: dict[str, dict] = {}
_tasks: set[asyncio.Task] = set()  # keep references so tasks are not garbage collected mid-run


_slots: asyncio.Semaphore | None = None
_slots_size = 0


def _model_slots() -> asyncio.Semaphore:
    """Limits how many receipts the model reads at once (bulk imports queue up behind it). Rebuilt when
    the setting changes; readings already waiting finish on the old one."""
    global _slots, _slots_size
    size = max(1, get_settings().extraction_concurrency)
    if _slots is None or size != _slots_size:
        _slots, _slots_size = asyncio.Semaphore(size), size
    return _slots


class ExtractionRequest(BaseModel):
    """Request to trigger extraction on a draft."""

    draft_id: str = Field(..., max_length=36, description="Draft ID to extract")
    image_id: str | None = Field(None, max_length=36, description="Extract from only this image (default: all images)")
    correction: str | None = Field(
        None,
        max_length=receipt_llm.MAX_CORRECTION_LENGTH,
        description="A note added to the standard prompt when reading the receipt again",
    )


class ExtractionStatusResponse(BaseModel):
    """Current extraction status for a draft."""

    draft_id: str
    status: str
    is_extracting: bool
    active: bool = Field(False, description="True when an extraction is running in this server process")
    extraction_started_at: datetime | None = None
    elapsed_seconds: float | None = None
    last_event: str | None = None
    error: str | None = None
    item_count: int = 0


def get_image_paths_for_draft(db: Session, draft_id: str, image_id: str | None = None) -> list[str]:
    """Paths of a draft's images in upload order (rowid preserves insertion order)."""
    query = db.query(ReceiptImage).filter(ReceiptImage.receipt_draft_id == draft_id)
    if image_id:
        query = query.filter(ReceiptImage.id == image_id)
    images = query.order_by(text("receipt_images.rowid")).all()
    return [image.file_path for image in images if image.file_path and os.path.exists(image.file_path)]


def _require_draft(db: Session, draft_id: str) -> ReceiptDraft:
    draft = drafts_get_draft(db, draft_id)
    if not draft:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Draft not found")
    return draft


def _load_draft(draft_id: str) -> ReceiptDraft | None:
    """The draft, read in a session of its own and closed again (nothing is held during the model's reading)."""
    with get_db_session()() as db:
        draft = db.query(ReceiptDraft).filter(ReceiptDraft.id == draft_id).first()
        if draft is not None:
            db.expunge(draft)
        return draft


def _save(draft_id: str, what) -> None:
    """``what(db, draft)`` in a fresh session, inside the maintenance gate (a backup restore never swaps the database
    under it). A draft deleted meanwhile (or not in the restored database) is left alone."""
    with maintenance.GATE.work(), get_db_session()() as db:
        draft = db.query(ReceiptDraft).filter(ReceiptDraft.id == draft_id).first()
        if draft is None:
            logger.info("Draft %s is gone; its reading is not saved", draft_id)
            return
        try:
            what(db, draft)
        except Exception as e:  # noqa: BLE001 - never leave a draft stuck in PROCESSING
            logger.error("Could not save the reading of draft %s: %s", draft_id, e, exc_info=True)
            db.rollback()
            draft = db.query(ReceiptDraft).filter(ReceiptDraft.id == draft_id).first()
            if draft is not None:
                get_extraction_service().mark_failed(db, draft, "Could not save what was read. Read it again.")


async def _run_extraction(
    draft_id: str, image_paths: list[str], correction: str | None = None, keep_previous: bool = False
) -> None:
    """Background task: run the model and persist either the result or the failure.

    With ``keep_previous`` (a re-read of a draft that already had a result) a failure leaves the
    earlier result in place instead of replacing it with an error. The database work runs in threads (it would
    otherwise hold up every request while it reads past purchases).
    """
    service = get_extraction_service()

    try:
        if draft_id in _active:
            _active[draft_id]["message"] = "Waiting for the model"
        async with _model_slots():
            draft = await asyncio.to_thread(_load_draft, draft_id)
            if not draft:
                logger.error("Draft vanished before extraction: %s", draft_id)
                return
            try:
                async for event in service.extract_receipt(draft, image_paths, correction):
                    logger.info("Extraction [%s] %s: %s", draft_id[:8], event.stage.value, event.message)
                    if draft_id in _active:
                        _active[draft_id]["message"] = event.message

                    if event.stage == ExtractionProgress.COMPLETE and event.result is not None:
                        result = event.result
                        await asyncio.to_thread(_save, draft_id, lambda db, d: service.save_extraction_to_draft(db, d, result))
                    elif event.stage == ExtractionProgress.ERROR:
                        message, raw = event.error or event.message, (event.result or {}).get("raw")
                        if keep_previous:
                            await asyncio.to_thread(_save, draft_id, lambda db, d: service.keep_previous_after_failed_reread(db, d, message))
                        else:
                            await asyncio.to_thread(_save, draft_id, lambda db, d: service.mark_failed(db, d, message, raw))
            except Exception as e:  # noqa: BLE001 - never leave a draft stuck in PROCESSING
                logger.error("Extraction task failed for %s: %s", draft_id, e, exc_info=True)
                failure = "Could not save what was read. Read it again."
                if keep_previous:
                    await asyncio.to_thread(_save, draft_id, lambda db, d: service.keep_previous_after_failed_reread(db, d, failure))
                else:
                    await asyncio.to_thread(_save, draft_id, lambda db, d: service.mark_failed(db, d, failure))
    finally:
        _active.pop(draft_id, None)


def start_extraction(draft_id: str, image_paths: list[str], correction: str | None = None, keep_previous: bool = False) -> None:
    """Begin reading a draft in the background (must be called from the running event loop)."""
    _active[draft_id] = {"started": time.time(), "message": "Queued"}
    task = asyncio.create_task(_run_extraction(draft_id, image_paths, correction, keep_previous))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def _prepare_extraction(request: ExtractionRequest) -> tuple[list[str], bool]:
    """Check the draft and mark it PROCESSING (in a thread: it uses the database). Returns (photos, keep_previous)."""
    with get_db_session()() as db:
        draft = _require_draft(db, request.draft_id)
        if draft.status not in EXTRACTABLE_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot extract draft in status: {draft.status}",
            )
        image_paths = get_image_paths_for_draft(db, request.draft_id, request.image_id)
        if not image_paths:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No images found for this draft")
        # A draft that already holds a reading keeps it if the re-read fails.
        keep_previous = draft.status == "NEEDS_REVIEW" and bool(draft.raw_llm_output)
        if draft.status != "PROCESSING":
            drafts_update_draft(db, request.draft_id, {"status": "PROCESSING"})
        return image_paths, keep_previous


@router.post("/extract")
async def trigger_extraction(request: ExtractionRequest):
    """
    Start AI extraction on a draft.

    Returns immediately; extraction runs in the background. Poll /status/{draft_id} (or /status?ids=…) for progress.
    """
    if request.draft_id in _active:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Extraction is already running")
    correction = receipt_llm.clean_correction(request.correction)
    image_paths, keep_previous = await run_in_threadpool(_prepare_extraction, request)
    if request.draft_id in _active:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Extraction is already running")

    start_extraction(request.draft_id, image_paths, correction, keep_previous)

    return {
        "status": "started",
        "draft_id": request.draft_id,
        "image_count": len(image_paths),
        "message": "Extraction started in background",
    }


class DraftState(BaseModel):
    """Where one receipt is (the Receipts page polls the ones being read)."""

    draft_id: str
    status: str
    active: bool = False
    item_count: int = 0


@router.get("/status", response_model=list[DraftState])
def get_extraction_states(ids: str = Query(..., max_length=4000, description="Draft ids, separated by commas (100 at most)"),
                          db: Session = Depends(get_db)):
    """The status of several drafts in one go (unknown ids are left out)."""
    from sqlalchemy import func
    wanted = [i for i in dict.fromkeys(x.strip() for x in ids.split(",")) if i][:100]
    if not wanted:
        return []
    rows = db.query(ReceiptDraft.id, ReceiptDraft.status).filter(ReceiptDraft.id.in_(wanted)).all()
    counts = dict(db.query(ReceiptDraftItem.receipt_draft_id, func.count(ReceiptDraftItem.id)).filter(
        ReceiptDraftItem.receipt_draft_id.in_(wanted)).group_by(ReceiptDraftItem.receipt_draft_id).all())
    return [DraftState(draft_id=r.id, status=r.status, active=r.id in _active, item_count=counts.get(r.id, 0)) for r in rows]


@router.get("/status/{draft_id}", response_model=ExtractionStatusResponse)
def get_extraction_status(
    draft_id: str,
    db: Session = Depends(get_db),
):
    """Get current extraction status for a draft."""
    draft = _require_draft(db, draft_id)

    active = _active.get(draft_id)
    error = None
    if draft.raw_llm_output and draft.status in (FAILED_STATUS, "NEEDS_REVIEW"):
        try:
            saved = json.loads(draft.raw_llm_output)
            error = saved.get("error") if draft.status == FAILED_STATUS else saved.get("reextract_error")
        except (json.JSONDecodeError, AttributeError):
            error = "Extraction failed" if draft.status == FAILED_STATUS else None

    started = datetime.fromtimestamp(active["started"], tz=timezone.utc).replace(tzinfo=None) if active else None
    return ExtractionStatusResponse(
        draft_id=draft_id,
        status=draft.status,
        is_extracting=draft.status == "PROCESSING",
        active=active is not None,
        extraction_started_at=started,
        elapsed_seconds=round(time.time() - active["started"], 1) if active else None,
        last_event=active["message"] if active else None,
        error=error,
        item_count=len(draft.items) if draft.items else 0,
    )
