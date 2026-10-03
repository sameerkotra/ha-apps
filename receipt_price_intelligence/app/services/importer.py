"""Bringing receipts in without the camera: a watched folder and a mailbox.

* **Folder**: photos and PDFs dropped into ``import_folder`` (for example a network share that a
  scanner or phone saves into) become drafts. Handled files move to ``processed/`` (or ``failed/``)
  inside the folder and are deleted after two weeks.
* **Mailbox**: unread emails in an IMAP folder have their PDF and image attachments imported, one
  draft per attachment. Messages are marked as read. Only attachments are used; a receipt that is
  only in the body of an email is skipped.

Each import creates a normal draft (a PDF is turned into page images first). Reading by the model
then happens in the background, queued behind other receipts, and a person reviews the result as
usual. Nothing is saved to the price history without approval.
"""

import email
import email.policy
import imaplib
import mimetypes
import os
import shutil
import time
from datetime import datetime
from io import BytesIO
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db_session
from app.db.models import Home
from app.logging_config import get_logger
from app.services import drafts as drafts_service
from app.services import archive
from app.services import images as images_service
from app.services import pdf as pdf_service

logger = get_logger("importer")

IMPORT_USER_ID = "system-import"
EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".pdf"}
SETTLE_SECONDS = 5            # a file must have been left alone this long (it may still be copying)
KEEP_DAYS = 14
MAX_MAILS_PER_POLL = 20
MIN_EMAIL_IMAGE_BYTES = 20 * 1024   # smaller pictures are logos and signatures


class ImportFailed(ValueError):
    """A file could not be imported. The message is safe to show."""


# --------------------------------------------------------------------------- #
# Making a draft
# --------------------------------------------------------------------------- #

def resolve_home(db: Session) -> str | None:
    """The home imports go to: the one named in ``import_home``, or the only home."""
    wanted = get_settings().import_home.strip().lower()
    homes = db.query(Home).all()
    if wanted:
        for home in homes:
            if home.name.strip().lower() == wanted:
                return home.id
        logger.warning("Import home %r does not exist", get_settings().import_home)
        return None
    if len(homes) == 1:
        return homes[0].id
    if homes:
        logger.warning("There is more than one home: set the 'Import into home' option to choose one")
    return None


def import_bytes(db: Session, home_id: str, filename: str, content: bytes, source: str, user_id: str | None = None) -> str:
    """Create a draft from one file. Returns the draft id. Raises ImportFailed if it cannot be used."""
    guessed = mimetypes.guess_type(filename)[0] or ""
    file = BytesIO(content)
    try:
        data, is_pdf = images_service.read_upload(file, guessed)
    except ValueError as e:
        raise ImportFailed(str(e)) from e

    if is_pdf:
        try:
            pages, _total = pdf_service.render_pdf(data, images_service.MAX_IMAGES_PER_DRAFT)
        except pdf_service.PdfError as e:
            raise ImportFailed(str(e)) from e
        stored = [(page, "image/jpeg", f"page_{i + 1}.jpg") for i, page in enumerate(pages)]
    else:
        stored = [(data, guessed, filename)]

    owner = user_id or IMPORT_USER_ID
    draft = drafts_service.create_draft(db, owner, source=source, home_id=home_id)
    try:
        for content_bytes, content_type, name in stored:
            images_service.store_image_bytes(db, draft.id, content_bytes, content_type, name)
    except Exception:
        db.rollback()
        drafts_service.delete_draft(db, draft.id, owner)
        raise
    archive.save_original(draft.id, data, filename, is_pdf)  # into the receipt archive folder, if one is set
    logger.info("Imported %s as draft %s (%s)", filename, draft.id, source)
    return draft.id


async def start_reading(draft_ids: list[str]) -> None:
    """Queue the model on the new drafts (runs in the event loop)."""
    from app.api.extraction import get_image_paths_for_draft, start_extraction

    with get_db_session()() as db:
        for draft_id in draft_ids:
            paths = get_image_paths_for_draft(db, draft_id)
            if paths:
                start_extraction(draft_id, paths)


# --------------------------------------------------------------------------- #
# Folder
# --------------------------------------------------------------------------- #

def _move(path: str, folder: str, sub: str) -> None:
    target_dir = os.path.join(folder, sub)
    os.makedirs(target_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    shutil.move(path, os.path.join(target_dir, f"{stamp}_{os.path.basename(path)}"))


def _prune(folder: str) -> None:
    cutoff = time.time() - KEEP_DAYS * 86400
    for sub in ("processed", "failed"):
        directory = os.path.join(folder, sub)
        if not os.path.isdir(directory):
            continue
        for name in os.listdir(directory):
            path = os.path.join(directory, name)
            try:
                if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                    os.remove(path)
            except OSError:
                pass


def scan_folder() -> list[str]:
    """Import every settled file in the watched folder. Returns the new draft ids."""
    folder = get_settings().import_folder.strip()
    if not folder or not os.path.isdir(folder):
        return []

    created: list[str] = []
    with get_db_session()() as db:
        home_id = resolve_home(db)
        if home_id is None:
            return []
        for name in sorted(os.listdir(folder)):
            path = os.path.join(folder, name)
            if name.startswith(".") or not os.path.isfile(path) or os.path.splitext(name)[1].lower() not in EXTENSIONS:
                continue
            if time.time() - os.path.getmtime(path) < SETTLE_SECONDS:
                continue  # still being written
            try:
                with open(path, "rb") as f:
                    content = f.read()
                created.append(import_bytes(db, home_id, name, content, "IMPORT_FOLDER"))
                _move(path, folder, "processed")
            except Exception as e:  # noqa: BLE001 - one bad file must not stop the rest
                logger.warning("Could not import %s: %s", name, e)
                try:
                    _move(path, folder, "failed")
                except OSError:
                    pass
    _prune(folder)
    return created


# --------------------------------------------------------------------------- #
# Mailbox
# --------------------------------------------------------------------------- #

def extract_attachments(message: email.message.EmailMessage) -> list[tuple[str, str, bytes]]:
    """PDF and image attachments of an email as (filename, content type, bytes). Tiny images
    (logos, signatures) are ignored."""
    out, seen = [], set()
    for index, part in enumerate(message.walk()):
        if part.is_multipart():
            continue
        content_type = part.get_content_type()
        is_pdf = content_type in ("application/pdf", "application/x-pdf")
        is_image = content_type.startswith("image/") and content_type in (
            "image/jpeg", "image/jpg", "image/png", "image/webp", "image/gif")
        filename = part.get_filename() or ""
        if not is_pdf and not is_image and filename.lower().endswith(".pdf"):
            is_pdf = True  # some senders label PDFs as octet-stream
        if not is_pdf and not is_image:
            continue
        payload = part.get_payload(decode=True)
        if not payload or (is_image and len(payload) < MIN_EMAIL_IMAGE_BYTES):
            continue
        key = hash(payload)
        if key in seen:
            continue
        seen.add(key)
        ext = ".pdf" if is_pdf else (mimetypes.guess_extension(content_type) or ".jpg")
        out.append((filename or f"attachment_{index}{ext}", "application/pdf" if is_pdf else content_type, payload))
    return out


def mailbox_configured() -> bool:
    s = get_settings()
    return bool(s.imap_host.strip() and s.imap_user.strip() and s.imap_password)


def poll_mailbox() -> list[str]:
    """Import attachments of unread emails. Returns the new draft ids."""
    if not mailbox_configured():
        return []
    s = get_settings()
    created: list[str] = []
    try:
        client = (imaplib.IMAP4_SSL(s.imap_host.strip(), s.imap_port, timeout=30) if s.imap_ssl
                  else imaplib.IMAP4(s.imap_host.strip(), s.imap_port, timeout=30))
    except (OSError, imaplib.IMAP4.error) as e:
        logger.warning("Could not connect to the mail server: %s", e)
        return []

    try:
        client.login(s.imap_user, s.imap_password)
        client.select(s.imap_folder or "INBOX")
        status, data = client.search(None, "UNSEEN")
        if status != "OK":
            return []
        with get_db_session()() as db:
            home_id = resolve_home(db)
            if home_id is None:
                return []
            for number in data[0].split()[:MAX_MAILS_PER_POLL]:
                status, parts = client.fetch(number, "(BODY.PEEK[])")
                if status != "OK" or not parts or not isinstance(parts[0], tuple):
                    continue
                message = email.message_from_bytes(parts[0][1], policy=email.policy.default)
                attachments = extract_attachments(message)
                if not attachments:
                    logger.info("Email %r has no PDF or image attachment; skipped", message.get("Subject", ""))
                for filename, _ctype, content in attachments:
                    try:
                        created.append(import_bytes(db, home_id, filename, content, "EMAIL"))
                    except Exception as e:  # noqa: BLE001
                        logger.warning("Could not import attachment %s: %s", filename, e)
                client.store(number, "+FLAGS", "\\Seen")
    except (OSError, imaplib.IMAP4.error) as e:
        logger.warning("Mailbox check failed: %s", e)
    finally:
        try:
            client.logout()
        except Exception:  # noqa: BLE001
            pass
    return created


def status() -> dict[str, Any]:
    s = get_settings()
    folder = s.import_folder.strip()
    return {
        "folder": folder or None, "folder_ready": bool(folder and os.path.isdir(folder)),
        "mailbox": s.imap_host.strip() or None if mailbox_configured() else None,
        "mailbox_ready": mailbox_configured(),
    }
