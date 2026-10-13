"""Image handling service for receipt uploads."""

import os
import uuid
from datetime import datetime
from io import BytesIO
from typing import BinaryIO

from sqlalchemy.orm import Session

from app.common import image_guard
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

# What a stored photo may be, by what Pillow finds in the file itself (never the type the browser or e-mail claims):
# Pillow's format name -> (MIME type, file extension)
IMAGE_FORMATS = {"JPEG": ("image/jpeg", ".jpg"), "PNG": ("image/png", ".png"), "GIF": ("image/gif", ".gif"),
                 "WEBP": ("image/webp", ".webp")}
# A photo is refused before it is decoded when it has more pixels than this (a few kilobytes can claim gigapixels)
MAX_IMAGE_PIXELS = image_guard.MAX_IMAGE_PIXELS


def validate_mime_type(content_type: str) -> bool:
    """Validate that the content type is allowed."""
    return content_type.lower() in ALLOWED_MIME_TYPES


def check_image_bytes(content: bytes) -> tuple[str, int, int]:
    """(MIME type, width, height) of a photo, read from its header only (nothing is decoded). Raises ValueError (a
    message that can be shown) for anything Pillow can't read as JPEG, PNG, GIF or WebP, or with too many pixels."""
    try:
        fmt, width, height = image_guard.check_image(content, MAX_IMAGE_PIXELS, formats=list(IMAGE_FORMATS))
    except image_guard.ImageTooBig as e:
        raise ValueError(f"{e} Photos can have up to {MAX_IMAGE_PIXELS // 1_000_000} million pixels.") from e
    except image_guard.ImageError as e:
        raise ValueError("That file isn't a photo the app can read. Use a JPEG, PNG, GIF or WebP photo, or a PDF") from e
    return IMAGE_FORMATS[fmt][0], width, height


def generate_safe_filename(original_filename: str, mime_type: str | None = None) -> str:
    """Generate a safe filename for storage (the extension follows the checked type when it is known)."""
    by_type = {m: e for m, e in IMAGE_FORMATS.values()}
    ext = by_type.get(mime_type or "") or os.path.splitext(original_filename)[1].lower()
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
    content_type: str | None = None,
    size: int | None = None,
) -> dict:
    """
    Check an uploaded photo and save it to storage.

    The photo must be one Pillow recognises as JPEG, PNG, GIF or WebP, within the pixel limit; its type comes
    from the file itself (``content_type`` is ignored). Raises ValueError otherwise, and nothing is written.

    Returns:
        dict with file metadata (path, mime_type, size, dimensions)
    """
    file.seek(0)
    content = file.read()
    mime_type, width, height = check_image_bytes(content)

    # Written with O_EXCL | O_NOFOLLOW: never through a link someone placed at that name
    fd = os.open(storage_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(content)

    return {
        "file_path": storage_path,
        "mime_type": mime_type,
        "size": len(content),
        "width": width,
        "height": height,
    }


THUMB_SIDE = 320


def thumb_path(path: str) -> str:
    return path + ".thumb.jpg"


def thumbnail(path: str) -> str:
    """A small JPEG copy of a stored photo (made once, kept beside it, deleted with it). Returns its path. Decoded
    within the shared limits (image_guard): checked first, a JPEG decoded at a smaller scale, two at a time."""
    target = thumb_path(path)
    if os.path.isfile(target) and not os.path.islink(target):
        return target
    from PIL import ImageOps
    with open(path, "rb") as f, image_guard.decode_slot():
        try:
            img = image_guard.open_for_decode(f, draft=("RGB", (THUMB_SIDE, THUMB_SIDE)))
        except image_guard.ImageError as e:
            raise ValueError(str(e)) from e
        with img:
            small = ImageOps.exif_transpose(img)
            small.thumbnail((THUMB_SIDE, THUMB_SIDE))
            small = small.convert("RGB")
            out = BytesIO()
            small.save(out, "JPEG", quality=80)
    temp = f"{target}.{uuid.uuid4().hex[:8]}.part"
    with open(temp, "wb") as f:
        f.write(out.getvalue())
    os.replace(temp, target)
    return target


MAX_IMAGES_PER_DRAFT = pdf_service.MAX_PDF_PAGES  # the model reads at most this many pages


def remaining_slots(db: Session, draft_id: str) -> int:
    """How many more pages this draft can take."""
    used = db.query(ReceiptImage).filter(ReceiptImage.receipt_draft_id == draft_id).count()
    return MAX_IMAGES_PER_DRAFT - used


def read_upload(file: BinaryIO, content_type: str | None) -> tuple[bytes, bool]:
    """Validate an uploaded file and return (bytes, is_pdf).

    PDFs are recognised by their signature as well as their content type. Anything else must be a photo Pillow
    can read (JPEG, PNG, GIF or WebP), whatever type it was sent as. The size is checked before the file is read.
    """
    file.seek(0)
    head = file.read(1024)
    file.seek(0, 2)
    size = file.tell()
    file.seek(0)
    is_pdf = pdf_service.looks_like_pdf(content_type, head)

    limit = pdf_service.MAX_PDF_SIZE if is_pdf else MAX_FILE_SIZE
    if size > limit:
        raise ValueError(f"That file is too large ({size // (1024 * 1024)} MB). The limit is {limit // (1024 * 1024)} MB")
    content = file.read(limit + 1)
    if len(content) > limit:
        raise ValueError(f"That file is too large. The limit is {limit // (1024 * 1024)} MB")
    if not is_pdf:
        check_image_bytes(content)
    return content, is_pdf


def store_image_bytes(
    db: Session,
    draft_id: str,
    content: bytes,
    content_type: str,
    original_filename: str | None = None,
) -> ReceiptImage:
    """Save one image's bytes on a draft and record it (ValueError, nothing stored, unless it is a readable photo)."""
    mime_type = check_image_bytes(content)[0]
    safe_filename = generate_safe_filename(original_filename or f"image_{uuid.uuid4().hex}.jpg", mime_type)
    storage_path = get_draft_image_path(draft_id, safe_filename)
    metadata = save_image_file(BytesIO(content), storage_path)

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
        for path in (image.file_path, thumb_path(image.file_path) if image.file_path else None):
            try:
                if path and os.path.lexists(path):
                    os.remove(path)
            except OSError as e:
                logger.warning("Could not delete %s: %s", path, e)
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

        # Delete file from disk (and its thumbnail)
        if os.path.exists(image.file_path):
            os.remove(image.file_path)
            logger.info("Deleted image file: %s", image.file_path)
        if os.path.lexists(thumb_path(image.file_path)):
            os.remove(thumb_path(image.file_path))

        # Delete database record
        db_session.delete(image)
        db_session.commit()

        return True
    except Exception as e:
        logger.error("Error deleting image %s: %s", image_id, e)
        if db_session:
            db_session.rollback()
        return False


