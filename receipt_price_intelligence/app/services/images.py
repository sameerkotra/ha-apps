"""Image handling service for receipt uploads."""

import os
import uuid
from datetime import datetime
from io import BytesIO
from typing import BinaryIO

from PIL import Image
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import ReceiptImage
from app.logging_config import get_logger
from app.services import pdf as pdf_service

logger = get_logger("images")

# Allowed MIME types for receipt images
ALLOWED_MIME_TYPES = {
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/gif",
    "image/webp",
}

# Maximum file size (10 MB)
MAX_FILE_SIZE = 10 * 1024 * 1024


def validate_mime_type(content_type: str) -> bool:
    """Validate that the content type is allowed."""
    return content_type.lower() in ALLOWED_MIME_TYPES


def validate_file_size(file: BinaryIO, limit: int = MAX_FILE_SIZE) -> int:
    """
    Validate file size and return the size in bytes.
    Raises ValueError if file is too large.
    """
    file.seek(0, 2)  # Seek to end
    size = file.tell()
    file.seek(0)  # Reset to beginning

    if size > limit:
        raise ValueError(f"That file is too large ({size // (1024 * 1024)} MB). The limit is {limit // (1024 * 1024)} MB")

    return size


def generate_safe_filename(original_filename: str) -> str:
    """Generate a safe filename for storage."""
    # Get extension from original filename
    ext = os.path.splitext(original_filename)[1].lower()
    if ext not in [".jpg", ".jpeg", ".png", ".gif", ".webp"]:
        ext = ".jpg"

    # Generate unique filename
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    unique_id = uuid.uuid4().hex[:8]
    return f"{timestamp}_{unique_id}{ext}"


def get_draft_image_path(draft_id: str, filename: str) -> str:
    """Get storage path for a draft image."""
    settings = get_settings()
    base_path = os.path.join(settings.image_path, "drafts", draft_id)
    os.makedirs(base_path, exist_ok=True)
    return os.path.join(base_path, filename)


def save_image_file(
    file: BinaryIO,
    storage_path: str,
    content_type: str,
    size: int,
) -> dict:
    """
    Save an uploaded image file to storage.

    Returns:
        dict with file metadata (path, mime_type, size, dimensions)
    """
    # Read the file
    file.seek(0)
    content = file.read()

    # Open with PIL to get dimensions
    try:
        img = Image.open(BytesIO(content))
        width, height = img.size
    except Exception as e:
        logger.warning("Could not open image: %s", e)
        width = height = 0

    # Write to disk
    with open(storage_path, "wb") as f:
        f.write(content)

    return {
        "file_path": storage_path,
        "mime_type": content_type,
        "size": size,
        "width": width,
        "height": height,
    }


MAX_IMAGES_PER_DRAFT = pdf_service.MAX_PDF_PAGES  # the model reads at most this many pages


def remaining_slots(db: Session, draft_id: str) -> int:
    """How many more pages this draft can take."""
    used = db.query(ReceiptImage).filter(ReceiptImage.receipt_draft_id == draft_id).count()
    return MAX_IMAGES_PER_DRAFT - used


def read_upload(file: BinaryIO, content_type: str | None) -> tuple[bytes, bool]:
    """Validate an uploaded file and return (bytes, is_pdf).

    PDFs are recognised by their signature as well as their content type. Anything else must
    be an allowed image type.
    """
    file.seek(0)
    head = file.read(1024)
    file.seek(0)
    is_pdf = pdf_service.looks_like_pdf(content_type, head)

    if is_pdf:
        validate_file_size(file, pdf_service.MAX_PDF_SIZE)
    else:
        if not content_type or not validate_mime_type(content_type):
            raise ValueError(f"Unsupported file type: {content_type or 'unknown'}. Use a photo or a PDF")
        validate_file_size(file)
    return file.read(), is_pdf


def store_image_bytes(
    db: Session,
    draft_id: str,
    content: bytes,
    content_type: str,
    original_filename: str | None = None,
) -> ReceiptImage:
    """Save one image's bytes on a draft and record it."""
    safe_filename = generate_safe_filename(original_filename or f"image_{uuid.uuid4().hex}.jpg")
    storage_path = get_draft_image_path(draft_id, safe_filename)
    metadata = save_image_file(BytesIO(content), storage_path, content_type, len(content))

    image = ReceiptImage(
        receipt_draft_id=draft_id,
        image_type="original",
        file_path=metadata["file_path"],
        mime_type=metadata["mime_type"],
        file_size=metadata["size"],
        width=metadata["width"],
        height=metadata["height"],
    )
    db.add(image)
    db.commit()
    db.refresh(image)
    logger.info("Stored draft image: %s (%d bytes)", storage_path, len(content))
    return image


def purge_draft_images(db: Session, draft_id: str) -> int:
    """Delete a draft's photos from disk and stage their removal from the database.

    Receipt photos are only kept while a receipt is being reviewed. This does not commit or
    catch database errors: it is a step inside a larger delete, and the caller controls the
    transaction. A commit here would end the caller's transaction early and, if anything failed,
    silently discard whatever the caller had already staged before calling this. Failures
    deleting one file from disk are logged and skipped so the rest still get removed.
    """
    images = db.query(ReceiptImage).filter(ReceiptImage.receipt_draft_id == draft_id).all()
    for image in images:
        try:
            if image.file_path and os.path.exists(image.file_path):
                os.remove(image.file_path)
        except OSError as e:
            logger.warning("Could not delete %s: %s", image.file_path, e)
        db.delete(image)

    folder = os.path.join(get_settings().image_path, "drafts", draft_id)
    if os.path.isdir(folder):
        try:
            os.rmdir(folder)  # only removes it if now empty
        except OSError:
            pass

    if images:
        logger.info("Deleted %d photo(s) for draft %s", len(images), draft_id)
    return len(images)


def delete_draft_image(image_id: str) -> bool:
    """
    Delete a draft image from storage.

    Returns:
        True if deleted, False if not found
    """
    db_session = None
    try:
        from app.db import get_db_session
        db_session = get_db_session()()

        image = db_session.query(ReceiptImage).filter(
            ReceiptImage.id == image_id
        ).first()

        if not image:
            return False

        # Delete file from disk
        if os.path.exists(image.file_path):
            os.remove(image.file_path)
            logger.info("Deleted image file: %s", image.file_path)

        # Delete database record
        db_session.delete(image)
        db_session.commit()

        return True
    except Exception as e:
        logger.error("Error deleting image %s: %s", image_id, e)
        if db_session:
            db_session.rollback()
        return False


