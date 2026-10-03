"""Receipt extraction API endpoints."""

import asyncio
import json
import os
import time
from datetime import datetime
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db, get_db_session
from app.db.models import ReceiptDraft, ReceiptImage
from app.logging_config import get_logger
from app.services import receipt_llm
from app.services.drafts import get_draft as drafts_get_draft, update_draft as drafts_update_draft
from app.services.extraction import (
    FAILED_STATUS,
    ExtractionEvent,
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

    draft_id: str = Field(..., description="Draft ID to extract")
    image_id: str | None = Field(None, description="Extract from only this image (default: all images)")
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


class SSEEvent:
    """Server-Sent Events event formatter."""

    @staticmethod
    def format(event: ExtractionEvent) -> str:
        """Format an extraction event as SSE."""
        data = {"stage": event.stage.value, "message": event.message}
        if event.data:
            data["data"] = event.data
        if event.error:
            data["error"] = event.error
        return f"event: extraction\ndata: {json.dumps(data)}\n\n"


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


async def _run_extraction(
    draft_id: str, image_paths: list[str], correction: str | None = None, keep_previous: bool = False
) -> None:
    """Background task: run the model and persist either the result or the failure.

    With ``keep_previous`` (a re-read of a draft that already had a result) a failure leaves the
    earlier result in place instead of replacing it with an error.
    """
    service = get_extraction_service()
    session_factory = get_db_session()

    try:
        if draft_id in _active:
            _active[draft_id]["message"] = "Waiting for the model"
        async with _model_slots():
            with session_factory() as db:
                draft = db.query(ReceiptDraft).filter(ReceiptDraft.id == draft_id).first()
                if not draft:
                    logger.error("Draft vanished before extraction: %s", draft_id)
                    return

                try:
                    async for event in service.extract_receipt(draft, image_paths, correction):
                        logger.info("Extraction [%s] %s: %s", draft_id[:8], event.stage.value, event.message)
                        if draft_id in _active:
                            _active[draft_id]["message"] = event.message

                        if event.stage == ExtractionProgress.COMPLETE and event.result is not None:
                            await service.save_extraction_to_draft(db, draft, event.result)
                        elif event.stage == ExtractionProgress.ERROR:
                            if keep_previous:
                                service.keep_previous_after_failed_reread(db, draft, event.error or event.message)
                            else:
                                service.mark_failed(
                                    db, draft, event.error or event.message, (event.result or {}).get("raw")
                                )
                except Exception as e:  # noqa: BLE001 - never leave a draft stuck in PROCESSING
                    logger.error("Extraction task failed for %s: %s", draft_id, e, exc_info=True)
                    db.rollback()
                    if keep_previous:
                        service.keep_previous_after_failed_reread(db, draft, f"Could not save extraction: {e}")
                    else:
                        service.mark_failed(db, draft, f"Could not save extraction: {e}")
    finally:
        _active.pop(draft_id, None)


def start_extraction(draft_id: str, image_paths: list[str], correction: str | None = None, keep_previous: bool = False) -> None:
    """Begin reading a draft in the background (must be called from the running event loop)."""
    _active[draft_id] = {"started": time.time(), "message": "Queued"}
    task = asyncio.create_task(_run_extraction(draft_id, image_paths, correction, keep_previous))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


@router.post("/extract")
async def trigger_extraction(
    request: ExtractionRequest,
    db: Session = Depends(get_db),
):
    """
    Start AI extraction on a draft.

    Returns immediately; extraction runs in the background. Poll
    /status/{draft_id} (or tail /stream/{draft_id}) for progress.
    """
    draft = _require_draft(db, request.draft_id)

    if request.draft_id in _active:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Extraction is already running")

    if draft.status not in EXTRACTABLE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot extract draft in status: {draft.status}",
        )

    image_paths = get_image_paths_for_draft(db, request.draft_id, request.image_id)
    if not image_paths:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No images found for this draft")

    correction = receipt_llm.clean_correction(request.correction)
    # A draft that already holds a reading keeps it if the re-read fails.
    keep_previous = draft.status == "NEEDS_REVIEW" and bool(draft.raw_llm_output)

    if draft.status != "PROCESSING":
        drafts_update_draft(db, request.draft_id, {"status": "PROCESSING"})

    start_extraction(request.draft_id, image_paths, correction, keep_previous)

    return {
        "status": "started",
        "draft_id": request.draft_id,
        "image_count": len(image_paths),
        "message": "Extraction started in background",
    }


@router.get("/status/{draft_id}", response_model=ExtractionStatusResponse)
async def get_extraction_status(
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

    return ExtractionStatusResponse(
        draft_id=draft_id,
        status=draft.status,
        is_extracting=draft.status == "PROCESSING",
        active=active is not None,
        extraction_started_at=datetime.utcfromtimestamp(active["started"]) if active else None,
        elapsed_seconds=round(time.time() - active["started"], 1) if active else None,
        last_event=active["message"] if active else None,
        error=error,
        item_count=len(draft.items) if draft.items else 0,
    )


@router.get("/stream/{draft_id}")
async def stream_extraction(
    draft_id: str,
    db: Session = Depends(get_db),
):
    """
    Stream extraction progress as Server-Sent Events.

    Observes the extraction started by POST /extract; it never starts a second
    model run. The stream ends with a COMPLETE or ERROR event.
    """
    _require_draft(db, draft_id)

    async def event_generator() -> AsyncGenerator[str, None]:
        last_message = None
        deadline = time.time() + 900

        while time.time() < deadline:
            active = _active.get(draft_id)
            if active is None:
                break
            if active["message"] != last_message:
                last_message = active["message"]
                yield SSEEvent.format(ExtractionEvent(stage=ExtractionProgress.CALLING_MODEL, message=last_message))
            await asyncio.sleep(0.5)

        db.expire_all()
        final = drafts_get_draft(db, draft_id)
        if final is not None and final.status == FAILED_STATUS:
            yield SSEEvent.format(ExtractionEvent(
                stage=ExtractionProgress.ERROR, message="Extraction failed", error="See draft status for details",
            ))
        else:
            yield SSEEvent.format(ExtractionEvent(
                stage=ExtractionProgress.COMPLETE, message="Extraction finished",
                data={"status": final.status if final else None},
            ))

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )
