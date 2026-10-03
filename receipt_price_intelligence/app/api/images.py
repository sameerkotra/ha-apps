"""Image upload API endpoints."""

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.services import drafts as drafts_service
from app.services import archive
from app.services import images as images_service
from app.services import pdf as pdf_service

router = APIRouter(prefix="/api/v1/receipt-drafts", tags=["images"])


def _image_json(image) -> dict:
    return {
        "id": image.id,
        "image_type": image.image_type,
        "file_path": image.file_path,
        "mime_type": image.mime_type,
        "file_size": image.file_size,
        "width": image.width,
        "height": image.height,
        "created_at": image.created_at.isoformat(),
    }


@router.post("/{draft_id}/images")
async def upload_draft_image(
    draft_id: str,
    file: Annotated[UploadFile, ...],
    db: Session = Depends(get_db),
):
    """
    Add a photo or a PDF to a receipt draft.

    Photos: JPEG, PNG, GIF or WebP, up to 10 MB. A PDF (up to 20 MB) is converted here: each
    page becomes a JPEG image on the draft, and the PDF itself is not kept. A receipt can have
    up to 8 pages in total; pages of a longer PDF beyond that are left out (``truncated``).

    The response describes the first stored image and lists all of them in ``images``.
    """
    draft = drafts_service.get_draft(db, draft_id)
    if not draft:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Draft not found")
    if draft.status not in ("PROCESSING", "NEEDS_REVIEW", "EXTRACTION_FAILED"):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This receipt is already saved")

    try:
        content, is_pdf = images_service.read_upload(file.file, file.content_type)
        room = images_service.remaining_slots(db, draft_id)
        if room < 1:
            raise ValueError(f"A receipt can have up to {images_service.MAX_IMAGES_PER_DRAFT} pages")

        total_pages = None
        if is_pdf:
            # Rendering can take a while: keep it off the event loop
            pages, total_pages = await asyncio.to_thread(pdf_service.render_pdf, content, room)
            stored = [
                images_service.store_image_bytes(db, draft_id, page, "image/jpeg", f"page_{i + 1}.jpg")
                for i, page in enumerate(pages)
            ]
        else:
            stored = [images_service.store_image_bytes(
                db, draft_id, content, file.content_type, file.filename)]
        # the file as uploaded (the photo, or the whole PDF) into the receipt archive folder, if one is set
        await asyncio.to_thread(archive.save_original, draft_id, content, file.filename, is_pdf)
    except ValueError as e:  # includes PdfError
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to store image: {str(e)}")

    return {
        **_image_json(stored[0]),
        "images": [_image_json(i) for i in stored],
        "converted_from_pdf": is_pdf,
        "pdf_pages": total_pages,
        "truncated": bool(total_pages and total_pages > len(stored)),
    }


@router.get("/{draft_id}/images/{image_id}")
async def get_draft_image(
    draft_id: str,
    image_id: str,
    db: Session = Depends(get_db),
):
    """
    Get a draft image by ID.

    Returns the image file or metadata.
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

    # Return the actual image file
    try:
        return FileResponse(
            image.file_path,
            media_type=image.mime_type or "image/jpeg",
        )
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Image file not found on disk",
        )


@router.delete("/{draft_id}/images/{image_id}")
async def delete_draft_image(
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

    success = images_service.delete_draft_image(image_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete image",
        )

    return {"status": "deleted", "image_id": image_id}
