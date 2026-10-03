"""Learning from corrections: when someone fixes how a store's line was read, remember it.

If the reader keeps printing "GV 2PCT MLK" for a store and it is corrected to "GV 2% MILK 1GAL",
the next receipt from that store with the same misread line is fixed automatically. Corrections
belong to a store chain, are matched on the normalized text, and are dropped if a person later
changes a line back to what was read (so a wrong fix cannot stick).
"""

from datetime import datetime

from sqlalchemy.orm import Session

from app.db.models import LineCorrection, ReceiptDraftItem, StoreChain, StoreLocation
from app.logging_config import get_logger
from app.services import normalize

logger = get_logger("corrections")

PLACEHOLDER = "unnamed item"


def chain_for_store_name(db: Session, store_name: str | None) -> str | None:
    """The chain a printed store name resolves to, if it is one we already know."""
    from app.services.receipt_records import find_chain_by_key
    parsed = normalize.normalize_store_name(store_name)
    if not parsed:
        return None
    chain = find_chain_by_key(db, parsed[1])
    return chain.id if chain else None


def chain_for_location(db: Session, location_id: str | None) -> str | None:
    location = db.get(StoreLocation, location_id) if location_id else None
    return location.store_chain_id if location else None


def lookup(db: Session, chain_id: str | None, text: str | None) -> str | None:
    """The corrected text remembered for this store's misread line, or None."""
    key = normalize.item_key(text)
    if not chain_id or key is None:
        return None
    found = db.query(LineCorrection).filter(
        LineCorrection.store_chain_id == chain_id, LineCorrection.original_key == key).first()
    return found.corrected_text if found else None


def record(db: Session, chain_id: str | None, draft_items: list[ReceiptDraftItem]) -> int:
    """Remember the fixes made to a saved receipt's lines (and forget ones that were undone).

    Returns how many corrections were added or changed.
    """
    if not chain_id or db.get(StoreChain, chain_id) is None:
        return 0
    changed = 0
    for item in draft_items:
        original, current = (item.original_description or "").strip(), (item.receipt_description or "").strip()
        okey, ckey = normalize.item_key(original), normalize.item_key(current)
        if okey is None or ckey is None or current.lower() == PLACEHOLDER:
            continue
        existing = db.query(LineCorrection).filter(
            LineCorrection.store_chain_id == chain_id, LineCorrection.original_key == okey).first()
        if okey != ckey:
            if existing is None:
                db.add(LineCorrection(store_chain_id=chain_id, original_key=okey, corrected_text=current[:500]))
            else:
                existing.times = (existing.times or 0) + 1 if existing.corrected_text == current[:500] else 1
                existing.corrected_text = current[:500]
                existing.updated_at = datetime.utcnow()
            changed += 1
        elif existing is not None:
            db.delete(existing)  # changed back to what was read: the earlier fix was wrong
    if changed:
        db.flush()
        logger.info("Remembered %d line correction(s)", changed)
    return changed
