"""Importing receipts from a watched folder and a mailbox."""

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from app.auth import CurrentUser, get_current_user
from app.db import get_db_session
from app.services import homes as homes_service
from app.services import images as images_service
from app.services import importer
from app.services import pdf as pdf_service

MAX_FILES_PER_UPLOAD = 30
MAX_FILE_BYTES = max(pdf_service.MAX_PDF_SIZE, images_service.MAX_FILE_SIZE)

router = APIRouter(prefix="/api/v1/import", tags=["import"])


@router.get("/status")
def import_status(user: CurrentUser = Depends(get_current_user)):
    """Whether the watched folder and the mailbox are set up (they are set in Admin → App settings). Which folder and
    which mail server, only to an administrator."""
    found = importer.status()
    if not user.is_admin:
        found["folder"] = found["mailbox"] = None
    return found


@router.post("/scan")
async def scan_now(_: CurrentUser = Depends(get_current_user)):
    """Check the folder and the mailbox right now instead of waiting for the next check (a check already running is
    not started twice)."""
    ids = await asyncio.to_thread(importer.scan_folder)
    ids += await asyncio.to_thread(importer.poll_mailbox)
    await importer.start_reading(ids)
    return {"imported": len(ids), "draft_ids": ids}


def _import_one(home_id: str, filename: str, content: bytes, user_id: str) -> str:
    with get_db_session()() as db:
        return importer.import_bytes(db, home_id, filename, content, "UPLOAD", user_id)


@router.post("/upload")
async def upload_many(
    home_id: Annotated[str, Form()],
    files: Annotated[list[UploadFile], File()],
    user: CurrentUser = Depends(get_current_user),
):
    """Add several receipts at once: each photo becomes its own draft, and each PDF becomes one draft
    with a page per image. The model then reads them in the background and they wait under To review.

    Returns one entry per file: ``draft_id`` when it was added, or ``error`` saying why not. One bad
    file never stops the others. Up to 30 files per request.
    """
    with get_db_session()() as db:
        if homes_service.get_home(db, home_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Home not found")
        room = importer.room_for_drafts(db, home_id)
    if not files:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Choose at least one file")
    if len(files) > MAX_FILES_PER_UPLOAD:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Up to {MAX_FILES_PER_UPLOAD} files at a time")

    results, ids = [], []
    for file in files:
        name = file.filename or "receipt"
        if room <= 0:
            results.append({"filename": name, "draft_id": None,
                            "error": "Too many receipts are waiting for review in this home; review some first"})
            continue
        try:
            file.file.seek(0, 2)
            size = file.file.tell()
            file.file.seek(0)
            if size > MAX_FILE_BYTES:   # checked before the file is read into memory
                raise importer.ImportFailed(f"That file is too large (at most {MAX_FILE_BYTES // (1024 * 1024)} MB)")
            content = await file.read()
            draft_id = await asyncio.to_thread(_import_one, home_id, name, content, user.id)
            ids.append(draft_id)
            room -= 1
            results.append({"filename": name, "draft_id": draft_id, "error": None})
        except importer.ImportFailed as e:
            results.append({"filename": name, "draft_id": None, "error": str(e)})
        except Exception:  # noqa: BLE001 - keep going with the other files
            results.append({"filename": name, "draft_id": None, "error": "This file could not be added"})
    await importer.start_reading(ids)
    return {"added": len(ids), "results": results}
