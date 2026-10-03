"""Keeping every receipt's original file (photo or PDF) in a folder of your choice, for example Home Assistant's shared
folder (``receipt_archive_path``: ``/share/receipts`` or ``/media/receipts``).

The app only keeps photos while a receipt is being reviewed. This archive is a separate, permanent copy:

* on scan (or import), the file as uploaded (the photo, or the whole PDF) is saved in ``<folder>/Being reviewed/<id>/``
* on approval, that folder moves to ``<folder>/<year>/<date> <store> <total>/`` (for example
  ``2026/2026-09-18 Kroger $54.23``), so the archive is easy to browse
* a scan that is rejected or deleted before approval has its folder removed, so only real receipts stay

Deleting a saved receipt in the app leaves its archived file alone: the archive is yours. Nothing here ever stops a
scan or an approval: a problem writing to the folder is logged and the receipt goes on as usual.
"""

from __future__ import annotations

import os
import re
import shutil
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.logging_config import get_logger

logger = get_logger("archive")

PENDING = "Being reviewed"
ALLOWED_ROOTS = ("/share", "/media")


def root() -> str | None:
    """The archive folder, when one is set and allowed (Home Assistant's /share or /media), else None."""
    path = (get_settings().receipt_archive_path or "").strip()
    if not path:
        return None
    path = os.path.normpath(path)
    if not any(path == r or path.startswith(r + "/") for r in ALLOWED_ROOTS):
        logger.warning("Receipt archive folder %s is not under /share or /media: not used", path)
        return None
    return path


def _safe(text: str, limit: int = 60) -> str:
    """A name that is fine as a folder or file name on any system."""
    text = re.sub(r'[\\/:*?"<>|\x00-\x1f]', " ", text or "")
    return re.sub(r"\s+", " ", text).strip(" .")[:limit] or "receipt"


def _pending_folder(draft_id: str) -> str | None:
    base = root()
    return os.path.join(base, PENDING, _safe(draft_id, 40)) if base else None


def save_original(draft_id: str, content: bytes, filename: str | None, is_pdf: bool = False) -> str | None:
    """Keep the uploaded file as it came (a photo, or the whole PDF). Returns where, or None when not archiving."""
    folder = _pending_folder(draft_id)
    if folder is None:
        return None
    try:
        os.makedirs(folder, exist_ok=True)
        name = _safe(os.path.basename(filename or ""), 80)
        if "." not in name:
            name += ".pdf" if is_pdf else ".jpg"
        stem, ext = os.path.splitext(name)
        path, n = os.path.join(folder, name), 1
        while os.path.exists(path):  # several pages uploaded with the same name
            n += 1
            path = os.path.join(folder, f"{stem} ({n}){ext}")
        with open(path, "wb") as f:
            f.write(content)
        return path
    except OSError as e:
        logger.warning("Could not keep the scan in the receipt archive (%s): %s", folder, e)
        return None


def _approved_name(db: Session, receipt: Any) -> tuple[str, str]:
    """(year folder, receipt folder name) for an approved receipt: "2026", "2026-09-18 Kroger $54.23"."""
    from app.db.models import StoreChain, StoreLocation
    store = "Receipt"
    location = db.get(StoreLocation, receipt.store_location_id) if receipt.store_location_id else None
    if location is not None:
        chain = db.get(StoreChain, location.store_chain_id)
        store = (chain.name if chain else None) or location.name or store
    day = (receipt.purchase_date or "")[:10] or "undated"
    total = getattr(receipt, "grand_total", None)
    money = ""
    if total is not None:
        symbol = {"USD": "$", "CAD": "$", "AUD": "$", "GBP": "£", "EUR": "€", "INR": "₹"}.get((receipt.currency_code or "").upper(), "")
        money = f" {symbol}{total:.2f}" if symbol else f" {total:.2f} {receipt.currency_code or ''}".rstrip()
    return (day[:4] if day[:4].isdigit() else "Undated"), _safe(f"{day} {store}{money}", 80)


def on_approved(db: Session, draft_id: str, receipt: Any) -> str | None:
    """Move the scan's archived file(s) to their place by date and store. Returns the folder, or None."""
    source = _pending_folder(draft_id)
    if source is None or not os.path.isdir(source):
        return None
    try:
        year, name = _approved_name(db, receipt)
        target_dir = os.path.join(root(), year)
        os.makedirs(target_dir, exist_ok=True)
        target, n = os.path.join(target_dir, name), 1
        while os.path.exists(target):  # two receipts from one store on one day with the same total
            n += 1
            target = os.path.join(target_dir, f"{name} ({n})")
        shutil.move(source, target)
        logger.info("Archived receipt %s in %s", receipt.id, target)
        return target
    except OSError as e:
        logger.warning("Could not file the archived scan of receipt %s: %s", getattr(receipt, "id", "?"), e)
        return None


def on_discarded(draft_id: str) -> None:
    """A scan rejected or deleted before approval: its archived file(s) go too."""
    folder = _pending_folder(draft_id)
    if folder and os.path.isdir(folder):
        try:
            shutil.rmtree(folder)
        except OSError as e:
            logger.warning("Could not remove the archived scan %s: %s", folder, e)


def status() -> dict[str, Any]:
    """For the app: whether scans are archived, where, and whether that folder can be written to."""
    path = (get_settings().receipt_archive_path or "").strip()
    base = root()
    writable = False
    if base:
        try:
            os.makedirs(base, exist_ok=True)
            writable = os.access(base, os.W_OK)
        except OSError:
            writable = False
    return {"path": path or None, "enabled": bool(base), "writable": writable,
            "problem": None if not path or (base and writable) else
            ("Must be a folder under /share or /media" if not base else "The folder cannot be written to")}
