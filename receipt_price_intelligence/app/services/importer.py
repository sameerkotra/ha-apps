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
import email.utils
import hashlib
import imaplib
import mimetypes
import os
import re
import shutil
import ssl
import stat
import threading
import time
from datetime import datetime
from io import BytesIO
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db_session
from app.db.models import Home, ReceiptDraft
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
MAX_MAIL_BYTES = 30 * 1024 * 1024   # bigger emails are skipped (never downloaded)
MAX_ATTACHMENTS_PER_MAIL = 10
WAITING_STATUSES = ("PROCESSING", "NEEDS_REVIEW", "EXTRACTION_FAILED")

# One folder scan and one mailbox check at a time (the background check and "Check now" could otherwise import the
# same file or email twice)
_folder_lock = threading.Lock()
_mail_lock = threading.Lock()


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


def waiting_drafts(db: Session, home_id: str) -> int:
    """Receipts of a home waiting to be read or reviewed."""
    return db.query(ReceiptDraft).filter(ReceiptDraft.home_id == home_id,
                                         ReceiptDraft.status.in_(WAITING_STATUSES)).count()


def room_for_drafts(db: Session, home_id: str) -> int:
    """How many more receipts may wait in this home (Admin → App settings → Receipts waiting at most)."""
    return max(0, get_settings().max_waiting_receipts - waiting_drafts(db, home_id))


def content_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def seen_before(db: Session, home_id: str, digest: str) -> bool:
    """A file with exactly these bytes was already imported into this home (and not discarded)."""
    return db.query(ReceiptDraft.id).filter(ReceiptDraft.home_id == home_id, ReceiptDraft.source_hash == digest,
                                            ReceiptDraft.status != "REJECTED").first() is not None


def import_bytes(db: Session, home_id: str, filename: str, content: bytes, source: str, user_id: str | None = None,
                 content_type: str | None = None) -> str:
    """Create a draft from one file. Returns the draft id. Raises ImportFailed if it cannot be used.
    A photo's type is taken from the file itself; ``content_type`` (an email attachment's type) only helps
    recognise a PDF whose name says nothing (no or an odd extension)."""
    guessed = mimetypes.guess_type(filename)[0] or ""
    if content_type and not guessed.endswith("/pdf"):
        guessed = content_type
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
    draft = drafts_service.create_draft(db, owner, source=source, home_id=home_id, source_hash=content_hash(content))
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
    if os.path.islink(target_dir):
        raise OSError(f"{target_dir} is a link")
    os.makedirs(target_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    shutil.move(path, os.path.join(target_dir, f"{stamp}_{os.path.basename(path)}"))


def _prune(folder: str) -> None:
    cutoff = time.time() - KEEP_DAYS * 86400
    for sub in ("processed", "failed"):
        directory = os.path.join(folder, sub)
        if not os.path.isdir(directory) or os.path.islink(directory):
            continue
        for name in os.listdir(directory):
            path = os.path.join(directory, name)
            try:
                info = os.lstat(path)
                if stat.S_ISREG(info.st_mode) and info.st_mtime < cutoff:
                    os.remove(path)
            except OSError:
                pass


def _read_regular_file(path: str, limit: int) -> bytes | None:
    """The file's bytes, if it is a regular file (never a link, a pipe or a device) no bigger than ``limit``."""
    try:
        if not stat.S_ISREG(os.lstat(path).st_mode):
            return None
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        return None
    with os.fdopen(fd, "rb") as f:
        info = os.fstat(f.fileno())
        if not stat.S_ISREG(info.st_mode):
            return None
        if info.st_size > limit:
            raise ImportFailed(f"That file is too large (at most {limit // (1024 * 1024)} MB)")
        return f.read(limit + 1)[:limit + 1]


def scan_folder() -> list[str]:
    """Import every settled file in the watched folder. Returns the new draft ids."""
    if not _folder_lock.acquire(blocking=False):
        logger.info("The import folder is being checked already")
        return []
    try:
        return _scan_folder()
    finally:
        _folder_lock.release()


def _scan_folder() -> list[str]:
    folder = get_settings().import_folder.strip()
    if not folder or not os.path.isdir(folder) or os.path.islink(folder):
        return []

    created: list[str] = []
    limit = max(pdf_service.MAX_PDF_SIZE, images_service.MAX_FILE_SIZE)
    with get_db_session()() as db:
        home_id = resolve_home(db)
        if home_id is None:
            return []
        room = room_for_drafts(db, home_id)
        for name in sorted(os.listdir(folder)):
            path = os.path.join(folder, name)
            if name.startswith(".") or os.path.splitext(name)[1].lower() not in EXTENSIONS:
                continue
            try:
                info = os.lstat(path)
            except OSError:
                continue
            if not stat.S_ISREG(info.st_mode):
                continue  # links, folders, pipes: never followed or read
            if time.time() - info.st_mtime < SETTLE_SECONDS:
                continue  # still being written
            if room <= 0:
                logger.warning("%d receipts are already waiting in this home; the rest of the import folder waits too",
                               get_settings().max_waiting_receipts)
                break
            try:
                content = _read_regular_file(path, limit)
                if content is None:
                    continue
                created.append(import_bytes(db, home_id, name, content, "IMPORT_FOLDER"))
                room -= 1
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
        if len(out) >= MAX_ATTACHMENTS_PER_MAIL:
            logger.info("Email has more than %d attachments; the rest are skipped", MAX_ATTACHMENTS_PER_MAIL)
            break
        payload = part.get_payload(decode=True)
        if not payload or (is_image and len(payload) < MIN_EMAIL_IMAGE_BYTES):
            continue
        key = hashlib.sha256(payload).digest()
        if key in seen:
            continue
        seen.add(key)
        ext = ".pdf" if is_pdf else (mimetypes.guess_extension(content_type) or ".jpg")
        out.append((filename or f"attachment_{index}{ext}", "application/pdf" if is_pdf else content_type, payload))
    return out


def mailbox_configured() -> bool:
    s = get_settings()
    return bool(s.imap_host.strip() and s.imap_user.strip() and s.imap_password)


def _message_size(client: Any, number: bytes) -> int | None:
    """The email's size (RFC822.SIZE) as the server reports it, without downloading it; None if unknown."""
    status, data = client.fetch(number, "(RFC822.SIZE)")
    if status != "OK" or not data:
        return None
    for item in data:
        raw = item[0] if isinstance(item, tuple) else item
        if isinstance(raw, bytes):
            match = re.search(rb"RFC822\.SIZE\s+(\d+)", raw, re.I)
            if match:
                return int(match.group(1))
    return None


# what a failed mail connection raises (read once: tests replace imaplib's classes with stand-ins)
_MAIL_ERRORS = (OSError, imaplib.IMAP4.error, ssl.SSLError)


class MailSecurityError(OSError):
    """The mail server can't be reached safely (the message is safe to show)."""


def allowed_senders() -> list[str]:
    """The App setting, as lowercase addresses and "@domain" entries ([] allows everyone)."""
    raw = get_settings().imap_allowed_senders or ""
    return [x.strip().lower() for x in re.split(r"[,;\s]+", raw) if x.strip()]


def sender_allowed(message: email.message.Message, allowed: list[str]) -> bool:
    """Whether the email's From address is on the list (an exact address, or "@domain" for a whole domain). An
    empty list allows everyone. (From can be forged by a sender; this keeps out everyone who doesn't bother.)"""
    if not allowed:
        return True
    address = email.utils.parseaddr(str(message.get("From", "")))[1].strip().lower()
    if "@" not in address:
        return False
    domain = "@" + address.rsplit("@", 1)[1]
    return address in allowed or domain in allowed


def quote_mailbox(name: str) -> str:
    """An IMAP folder name as a quoted string ("My Receipts" has a space; quotes and backslashes are escaped)."""
    name = name or "INBOX"
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


def connect_mailbox() -> Any:
    """A logged-in IMAP connection: SSL with the certificate checked, or, with SSL off, STARTTLS (also checked).
    A server that offers no STARTTLS gets the password in plain text only when an administrator allows it
    (Admin → App settings → Allow mail login without encryption)."""
    s = get_settings()
    host = s.imap_host.strip()
    context = ssl.create_default_context()
    if s.imap_ssl:
        client = imaplib.IMAP4_SSL(host, s.imap_port, timeout=30, ssl_context=context)
    else:
        client = imaplib.IMAP4(host, s.imap_port, timeout=30)
        if "STARTTLS" in getattr(client, "capabilities", ()):
            try:
                client.starttls(ssl_context=context)
            except Exception:
                _logout(client)
                raise
        elif not s.imap_allow_plaintext:
            _logout(client)
            raise MailSecurityError("The mail server offers no encryption (no STARTTLS), so the password was not sent. "
                                    "Turn on SSL, or allow mail login without encryption in App settings.")
        else:
            logger.warning("Logging in to the mail server without encryption (allowed in App settings)")
    try:
        client.login(s.imap_user, s.imap_password)
    except Exception:
        _logout(client)
        raise
    return client


def _logout(client: Any) -> None:
    try:
        client.logout()
    except Exception:  # noqa: BLE001
        pass


def poll_mailbox() -> list[str]:
    """Import attachments of unread emails. Returns the new draft ids."""
    if not mailbox_configured():
        return []
    if not _mail_lock.acquire(blocking=False):
        logger.info("The mailbox is being checked already")
        return []
    try:
        return _poll_mailbox()
    finally:
        _mail_lock.release()


def _poll_mailbox() -> list[str]:
    s = get_settings()
    created: list[str] = []
    try:
        client = connect_mailbox()
    except _MAIL_ERRORS as e:
        logger.warning("Could not connect to the mail server: %s", e)
        return []

    allowed = allowed_senders()
    try:
        client.select(quote_mailbox(s.imap_folder or "INBOX"))
        status, data = client.search(None, "UNSEEN")
        if status != "OK":
            return []
        with get_db_session()() as db:
            home_id = resolve_home(db)
            if home_id is None:
                return []
            room = room_for_drafts(db, home_id)
            for number in data[0].split()[:MAX_MAILS_PER_POLL]:
                if room <= 0:
                    # the rest stay unread, for when the receipts waiting have been reviewed
                    logger.warning("%d receipts are already waiting in this home; the other emails stay unread",
                                   s.max_waiting_receipts)
                    break
                size = _message_size(client, number)
                if size is None or size > MAX_MAIL_BYTES:
                    logger.warning("Email %s is too big to import (%s bytes, at most %d); skipped and marked as read",
                                   number.decode(errors="replace"), size if size is not None else "unknown",
                                   MAX_MAIL_BYTES)
                    client.store(number, "+FLAGS", "\\Seen")
                    continue
                status, parts = client.fetch(number, "(BODY.PEEK[])")
                if status != "OK" or not parts or not isinstance(parts[0], tuple):
                    continue
                message = email.message_from_bytes(parts[0][1], policy=email.policy.default)
                if not sender_allowed(message, allowed):
                    logger.info("Email from %r is not from an allowed sender; skipped and marked as read",
                                email.utils.parseaddr(str(message.get("From", "")))[1])
                    client.store(number, "+FLAGS", "\\Seen")
                    continue
                attachments = extract_attachments(message)
                if not attachments:
                    logger.info("Email %r has no PDF or image attachment; skipped", message.get("Subject", ""))
                fresh = []
                for filename, ctype, content in attachments:
                    if seen_before(db, home_id, content_hash(content)):
                        logger.info("Attachment %s was imported before; skipped", filename)
                    else:
                        fresh.append((filename, ctype, content))
                if len(fresh) > room:
                    logger.warning("Not enough room for the %d receipts of this email (%d receipts may wait); it stays "
                                   "unread", len(fresh), s.max_waiting_receipts)
                    break
                for filename, ctype, content in fresh:
                    try:
                        created.append(import_bytes(db, home_id, filename, content, "EMAIL", content_type=ctype))
                        room -= 1
                    except Exception as e:  # noqa: BLE001
                        logger.warning("Could not import attachment %s: %s", filename, e)
                client.store(number, "+FLAGS", "\\Seen")
    except _MAIL_ERRORS as e:
        logger.warning("Mailbox check failed: %s", e)
    finally:
        _logout(client)
    return created


def status() -> dict[str, Any]:
    s = get_settings()
    folder = s.import_folder.strip()
    return {
        "folder": folder or None, "folder_ready": bool(folder and os.path.isdir(folder)),
        "mailbox": s.imap_host.strip() or None if mailbox_configured() else None,
        "mailbox_ready": mailbox_configured(),
    }
