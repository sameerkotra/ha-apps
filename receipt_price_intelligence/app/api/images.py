"""Image upload API endpoints."""

import os
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse

from app.common import image_guard
from sqlalchemy.orm import Session

from app.db import get_db
from app.logging_config import get_logger
from app.services import drafts as drafts_service
from app.services import archive
from app.services import images as images_service
from app.services import pdf as pdf_service

logger = get_logger("images_api")

router = APIRouter(prefix="/api/v1/receipt-drafts", tags=["images"])


def _image_json(image) -> dict:
    return {
        "id": image.id,
        "image_type": image.image_type,
        "mime_type": image.mime_type,
        "file_size": image.file_size,
        "width": image.width,
        "height": image.height,
        "created_at": image.created_at.isoformat(),
    }


EDITABLE = ("PROCESSING", "NEEDS_REVIEW", "EXTRACTION_FAILED")


@router.post("/{draft_id}/images")
def upload_draft_image(
    draft_id: str,
    file: Annotated[UploadFile, ...],
    db: Session = Depends(get_db),
):
    """
    Add a photo or a PDF to a receipt draft.

    Photos: JPEG, PNG, GIF or WebP (by what the file is, not what it is called), up to 10 MB and 40 million pixels.
    A PDF (up to 20 MB) is converted here: each
    page becomes a JPEG image on the draft, and the PDF itself is not kept. A receipt can have
    up to 8 pages in total; pages of a longer PDF beyond that are left out (``truncated``).

    The response describes the first stored image and lists all of them in ``images``.
    """
    draft = drafts_service.get_draft(db, draft_id)
    if not draft:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Draft not found")
    if draft.status not in EDITABLE:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This receipt is already saved")

    try:
        content, is_pdf = images_service.read_upload(file.file, file.content_type)
        room = images_service.remaining_slots(db, draft_id)
        if room < 1:
            raise ValueError(f"A receipt can have up to {images_service.MAX_IMAGES_PER_DRAFT} pages")

        total_pages = None
        if is_pdf:
            # Rendering can take a while: keep it off the event loop
            pages, total_pages = pdf_service.render_pdf(content, room)
            stored = [
                images_service.store_image_bytes(db, draft_id, page, "image/jpeg", f"page_{i + 1}.jpg")
                for i, page in enumerate(pages)
            ]
        else:
            stored = [images_service.store_image_bytes(
                db, draft_id, content, file.content_type, file.filename)]
        # the file as uploaded (the photo, or the whole PDF) into the receipt archive folder, if one is set
        archive.save_original(draft_id, content, file.filename, is_pdf)
    except ValueError as e:  # includes PdfError
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception:  # noqa: BLE001 - the detail goes to the log, not to the browser
        logger.exception("Could not store an image on draft %s", draft_id)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to store image")

    return {
        **_image_json(stored[0]),
        "images": [_image_json(i) for i in stored],
        "converted_from_pdf": is_pdf,
        "pdf_pages": total_pages,
        "truncated": bool(total_pages and total_pages > len(stored)),
    }


@router.get("/{draft_id}/images/{image_id}")
def get_draft_image(
    draft_id: str,
    image_id: str,
    db: Session = Depends(get_db),
    size: Literal["full", "thumb"] = Query("full", description="thumb: a small copy for the page thumbnails"),
):
    """
    Get a draft image by ID: the photo as stored, or (``size=thumb``) a small JPEG copy made once and kept beside it.
    """
    # Verify the draft exists (drafts are shared by all users)
    draft = drafts_service.get_draft(db, draft_id)
    if not draft:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Draft not found",
        )

    # Get image record
    from app.db.models import ReceiptImage

    image = db.query(ReceiptImage).filter(
        ReceiptImage.id == image_id,
        ReceiptImage.receipt_draft_id == draft_id,
    ).first()

    if not image:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Image not found",
        )

    # Return the actual image file (FileResponse opens it only when sending, so check it is there first)
    if not image.file_path or not os.path.isfile(image.file_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Image file not found on disk",
        )
    if size == "thumb":
        try:
            return FileResponse(images_service.thumbnail(image.file_path), media_type="image/jpeg")
        except (ValueError, OSError, image_guard.DecodeBusy) as e:
            logger.info("No thumbnail for image %s (%s); sending the photo", image_id, e)
    return FileResponse(
        image.file_path,
        media_type=image.mime_type or "image/jpeg",
    )


@router.delete("/{draft_id}/images/{image_id}")
def delete_draft_image(
    draft_id: str,
    image_id: str,
    db: Session = Depends(get_db),
):
    """
    Delete an image from a draft.
    """
    # Verify the draft exists (drafts are shared by all users)
    draft = drafts_service.get_draft(db, draft_id)
    if not draft:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Draft not found",
        )

    # Verify image belongs to draft
    from app.db.models import ReceiptImage

    image = db.query(ReceiptImage).filter(
        ReceiptImage.id == image_id,
        ReceiptImage.receipt_draft_id == draft_id,
    ).first()

    if not image:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Image not found",
        )
    if draft.status not in EDITABLE:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This receipt is already saved or discarded")

    success = images_service.delete_draft_image(image_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete image",
        )

    return {"status": "deleted", "image_id": image_id}
