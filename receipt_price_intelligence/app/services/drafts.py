"""Receipt draft management service."""

import json
import uuid
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import text

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.services import archive, corrections, normalize
from app.services import images as images_service
from app.db.models import (
    CommonItem,
    ExtractionRun,
    StoreLocation,
    PriceObservation,
    StoreTaxObservation,
    Receipt,
    ReceiptDraft,
    ReceiptDraftItem,
    ReceiptDraftTax,
    ReceiptImage,
    ReceiptItem,
    TaxRecord,
    User,
)
from app.logging_config import get_logger
from app.services import receipt_llm as llm
from app.services import receipt_records

logger = get_logger("drafts")

# Header fields the user may correct during review. They live in the draft's
# raw_llm_output JSON (the draft table has no columns for them) and are copied onto
# the permanent Receipt at approval.
HEADER_TEXT_FIELDS = {
    "store_name": 255,
    "store_address": 500,
    "store_number": 50,
    "receipt_number": 100,
}
HEADER_AMOUNT_FIELDS = {"subtotal", "discount_total", "tax_total", "fee_total", "grand_total"}
HEADER_FIELDS = set(HEADER_TEXT_FIELDS) | HEADER_AMOUNT_FIELDS | {"purchase_date", "purchase_time", "currency", "tags"}

# Item columns the user may set when replacing a draft's items.
ITEM_FIELDS = {
    "receipt_description",
    "item_type",
    "quantity",
    "weight_value",
    "weight_unit",
    "unit_price",
    "unit_price_unit",
    "line_subtotal",
    "discount_amount",
    "line_total",
    "tax_amount",
    "extraction_confidence",
    "suggested_common_item_id",
    "suggested_common_item_name",
    "original_description",
}


def get_extraction_json(draft: ReceiptDraft) -> dict[str, Any]:
    """Parse the draft's stored extraction JSON. Returns {} when absent or unreadable."""
    if not draft.raw_llm_output:
        return {}
    try:
        data = json.loads(draft.raw_llm_output)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def start_manual_entry(db: Session, draft_id: str) -> ReceiptDraft | None:
    """Let a person type in a receipt the model could not read.

    The draft moves to NEEDS_REVIEW with an empty header and no lines; the photos stay attached
    so they can be read by eye. Only a failed extraction can switch to manual entry (a draft
    that already has a reading can simply be edited).
    """
    draft = get_draft(db, draft_id)
    if not draft:
        return None
    if draft.status != "EXTRACTION_FAILED":
        raise ValueError("Only a receipt that could not be read can be entered by hand")

    previous_error = get_extraction_json(draft).get("error")
    data: dict[str, Any] = {
        "items": [], "taxes": [], "validation_warnings": [],
        "meta": {"manual": True}, "manual_entry": True,
    }
    if previous_error:
        data["previous_error"] = previous_error

    db.query(ReceiptDraftItem).filter(ReceiptDraftItem.receipt_draft_id == draft_id).delete()
    db.query(ReceiptDraftTax).filter(ReceiptDraftTax.receipt_draft_id == draft_id).delete()
    draft.raw_llm_output = json.dumps(data, indent=2)
    draft.status = "NEEDS_REVIEW"
    db.commit()
    db.refresh(draft)
    return draft


class SavedReceiptError(ValueError):
    """A saved receipt could not be corrected. The message is safe to show."""


def _detach_extraction_runs(db: Session, *, receipt_id: str | None = None, draft_id: str | None = None) -> None:
    """Point extraction-run history rows away from a receipt or draft that is about to be deleted.

    ``extraction_runs`` rows are a record of past reads and are worth keeping, so they are never
    deleted along with the receipt or draft they reference, just detached from it. Without this,
    deleting a receipt or draft that still has a linked run (from an older version of the app, or a
    restored backup) fails with a foreign key error, because SQLite enforces this constraint and it
    has no ON DELETE action of its own.
    """
    if receipt_id:
        db.query(ExtractionRun).filter(ExtractionRun.receipt_id == receipt_id).update(
            {"receipt_id": None}, synchronize_session=False
        )
    if draft_id:
        db.query(ExtractionRun).filter(ExtractionRun.receipt_draft_id == draft_id).update(
            {"receipt_draft_id": None}, synchronize_session=False
        )


def _delete_receipt_records(db: Session, receipt_id: str) -> None:
    """Remove a saved receipt and everything recorded from it (no commit)."""
    item_ids = select(ReceiptItem.id).where(ReceiptItem.receipt_id == receipt_id)
    db.query(PriceObservation).filter(PriceObservation.receipt_item_id.in_(item_ids)).delete(synchronize_session=False)
    db.query(StoreTaxObservation).filter(StoreTaxObservation.receipt_id == receipt_id).delete(synchronize_session=False)
    db.query(TaxRecord).filter(TaxRecord.receipt_id == receipt_id).delete(synchronize_session=False)
    _detach_extraction_runs(db, receipt_id=receipt_id)
    db.query(ReceiptImage).filter(ReceiptImage.receipt_id == receipt_id).delete(synchronize_session=False)
    db.query(ReceiptItem).filter(ReceiptItem.receipt_id == receipt_id).delete(synchronize_session=False)
    db.query(Receipt).filter(Receipt.id == receipt_id).delete(synchronize_session=False)


def reopen_saved_receipt(db: Session, draft_id: str) -> ReceiptDraft | None:
    """Open a saved receipt for full editing (store, date, lines, totals).

    The receipt stays in the history untouched until the edited version is saved, at which point
    it is replaced. Until then it can be abandoned with ``discard_saved_edit``.
    """
    draft = get_draft(db, draft_id)
    if not draft:
        return None
    if draft.status != "APPROVED":
        raise SavedReceiptError("Only a saved receipt can be edited this way")
    receipt = _receipt_for_draft(db, draft)
    if receipt is None:
        raise SavedReceiptError("Could not find the saved receipt for this draft")
    draft.approved_receipt_id = receipt.id
    draft.status = "NEEDS_REVIEW"
    db.commit()
    db.refresh(draft)
    return draft


def discard_saved_edit(db: Session, draft_id: str) -> ReceiptDraft | None:
    """Abandon an edit of a saved receipt: the draft goes back to exactly what was saved."""
    draft = get_draft(db, draft_id)
    if not draft:
        return None
    if draft.status != "NEEDS_REVIEW" or not draft.approved_receipt_id:
        raise SavedReceiptError("This receipt is not an edit of a saved receipt")
    receipt = db.get(Receipt, draft.approved_receipt_id)
    if receipt is None:
        raise SavedReceiptError("The saved receipt no longer exists")

    try:
        draft.raw_llm_output = receipt.raw_llm_output
        db.query(ReceiptDraftItem).filter(ReceiptDraftItem.receipt_draft_id == draft_id).delete(synchronize_session=False)
        for row in db.query(ReceiptItem).filter(ReceiptItem.receipt_id == receipt.id).all():
            common = db.get(CommonItem, row.common_item_id) if row.common_item_id else None
            named = common is not None and bool(common.name_confirmed)
            db.add(ReceiptDraftItem(
                receipt_draft_id=draft_id, receipt_description=row.receipt_description,
                suggested_common_item_id=common.id if named else None,
                suggested_common_item_name=common.name if named else None,
                item_type=row.item_type, quantity=row.quantity, weight_value=row.weight_value,
                weight_unit=row.weight_unit, unit_price=row.unit_price, unit_price_unit=row.unit_price_unit,
                line_subtotal=row.line_subtotal, discount_amount=row.discount_amount, line_total=row.line_total,
                tax_amount=row.tax_amount, extraction_confidence=row.extraction_confidence,
                normalization_confidence=row.normalization_confidence, raw_text=row.raw_text,
            ))
        draft.status = "APPROVED"
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(draft)
    return draft


def delete_saved_receipt(db: Session, draft_id: str) -> bool:
    """Delete a saved receipt completely: the receipt, its prices and tax records, and its draft."""
    draft = get_draft(db, draft_id)
    if not draft:
        return False
    if draft.status != "APPROVED" and not draft.approved_receipt_id:
        raise SavedReceiptError("Only a saved receipt can be deleted this way")
    receipt = _receipt_for_draft(db, draft)
    try:
        if receipt is not None:
            _delete_receipt_records(db, receipt.id)
        _detach_extraction_runs(db, draft_id=draft_id)
        images_service.purge_draft_images(db, draft_id)
        db.delete(draft)
        db.commit()
    except Exception:
        db.rollback()
        raise
    logger.info("Deleted saved receipt for draft %s", draft_id)
    return True


def find_duplicates(db: Session, draft: ReceiptDraft) -> list[dict[str, Any]]:
    """Saved receipts that look like this draft: same home, store and day, and the same total
    or receipt number. A receipt being edited never counts as a duplicate of itself."""
    header = get_extraction_json(draft)
    store = receipt_records.normalize.normalize_store_name(header.get("store_name"))
    date_text, total, number = header.get("purchase_date"), header.get("grand_total"), (header.get("receipt_number") or "").strip()
    if not (draft.home_id and store and date_text) or (total is None and not number):
        return []
    chain = receipt_records.find_chain_by_key(db, store[1])
    if chain is None:
        return []

    rows = (
        db.query(Receipt, StoreLocation)
        .join(StoreLocation, Receipt.store_location_id == StoreLocation.id)
        .filter(
            Receipt.home_id == draft.home_id,
            Receipt.purchase_date == date_text,
            StoreLocation.store_chain_id == chain.id,
        )
        .all()
    )
    out = []
    for receipt, location in rows:
        if receipt.id == draft.approved_receipt_id:
            continue
        same = []
        if total is not None and receipt.grand_total is not None and abs(receipt.grand_total - total) < 0.011:
            same.append("total")
        if number and receipt.receipt_number and receipt.receipt_number.strip() == number:
            same.append("receipt number")
        if not same:
            continue
        saved = db.query(ReceiptDraft.id).filter(ReceiptDraft.approved_receipt_id == receipt.id).first()
        out.append({
            "receipt_id": receipt.id, "draft_id": saved[0] if saved else None, "store": chain.name,
            "date": receipt.purchase_date, "total": receipt.grand_total, "matches": same,
        })
    return out


def _receipt_for_draft(db: Session, draft: ReceiptDraft) -> Receipt | None:
    """The permanent receipt an approved draft became.

    Receipts saved before drafts recorded the link are matched by their copy of the original
    reading (identical to the draft's until now) and the link is stored for next time.
    """
    if draft.approved_receipt_id:
        return db.get(Receipt, draft.approved_receipt_id)
    if not draft.raw_llm_output:
        return None
    matches = db.query(Receipt).filter(
        Receipt.home_id == draft.home_id,
        Receipt.raw_llm_output == draft.raw_llm_output,
    ).limit(2).all()
    if len(matches) != 1:
        return None
    draft.approved_receipt_id = matches[0].id
    return matches[0]


def update_saved_receipt_date(db: Session, draft_id: str, purchase_date: str) -> ReceiptDraft | None:
    """Correct the purchase date of a receipt that has already been saved.

    The date is changed everywhere it is recorded: the receipt, the price history built from it
    (which the analysis and best-price pages read), its tax observations, and the review copy.
    Raises SavedReceiptError for a date that cannot be right.
    """
    draft = get_draft(db, draft_id)
    if not draft:
        return None
    if draft.status != "APPROVED":
        raise SavedReceiptError("Only a saved receipt can be changed this way")

    parsed = llm.normalize_date(purchase_date)
    if parsed is None:
        raise SavedReceiptError("That is not a valid date")
    value = date.fromisoformat(parsed)
    if value.year < 2000:
        raise SavedReceiptError("That date looks too old")
    if value > date.today() + timedelta(days=1):
        raise SavedReceiptError("A receipt can't be dated in the future")

    receipt = _receipt_for_draft(db, draft)
    if receipt is None:
        raise SavedReceiptError("Could not find the saved receipt for this draft")

    try:
        receipt.purchase_date = parsed
        receipt.updated_at = datetime.utcnow()
        item_ids = select(ReceiptItem.id).where(ReceiptItem.receipt_id == receipt.id)
        db.query(PriceObservation).filter(PriceObservation.receipt_item_id.in_(item_ids)).update(
            {"purchase_date": parsed}, synchronize_session=False
        )
        db.query(StoreTaxObservation).filter(StoreTaxObservation.receipt_id == receipt.id).update(
            {"observation_date": parsed}, synchronize_session=False
        )

        data = get_extraction_json(draft)
        data["purchase_date"] = parsed
        data["purchase_date_ambiguous"] = False
        data["purchase_date_alternatives"] = []
        data["user_edited_fields"] = sorted(set(data.get("user_edited_fields") or []) | {"purchase_date"})
        text = json.dumps(data, indent=2)
        draft.raw_llm_output = text
        receipt.raw_llm_output = text  # keep the two copies identical

        draft.updated_at = datetime.utcnow()
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(draft)
    logger.info("Changed the date of saved receipt %s to %s", receipt.id, parsed)
    return draft


def set_saved_tags(db: Session, draft_id: str, tags) -> ReceiptDraft | None:
    """Change the tags of a receipt that is already saved (on the receipt and its review copy)."""
    draft = get_draft(db, draft_id)
    if not draft:
        return None
    if draft.status != "APPROVED":
        raise SavedReceiptError("Only a saved receipt can be tagged this way")
    cleaned = normalize.clean_tags(tags)
    receipt = _receipt_for_draft(db, draft)
    try:
        data = get_extraction_json(draft)
        data["tags"] = cleaned
        text = json.dumps(data, indent=2)
        draft.raw_llm_output = text
        if receipt is not None:
            receipt.tags = json.dumps(cleaned) if cleaned else None
            receipt.raw_llm_output = text  # keeps the two copies identical
        draft.updated_at = datetime.utcnow()
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(draft)
    return draft


def update_draft_header(
    db: Session,
    draft_id: str,
    fields: dict[str, Any],
) -> ReceiptDraft | None:
    """Apply user corrections to the receipt header (store, date, totals, ...).

    Empty strings clear a field. Raises ValueError for an unparseable date, time,
    currency, or amount. Edited field names are recorded in ``user_edited_fields`` so
    the original model output can still be told apart from human corrections.
    """
    draft = get_draft(db, draft_id)
    if not draft:
        return None

    data = get_extraction_json(draft)
    edited = set(data.get("user_edited_fields") or [])

    for name, value in fields.items():
        if name not in HEADER_FIELDS:
            continue

        blank = value is None or (isinstance(value, str) and not value.strip())

        if name in HEADER_TEXT_FIELDS:
            data[name] = None if blank else str(value).strip()[: HEADER_TEXT_FIELDS[name]]
        elif name in HEADER_AMOUNT_FIELDS:
            amount = None if blank else llm.to_number(value)
            if not blank and amount is None:
                raise ValueError(f"Invalid amount for {name}")
            data[name] = amount
        elif name == "purchase_date":
            parsed = None if blank else llm.normalize_date(value)
            if not blank and parsed is None:
                raise ValueError("Invalid purchase_date (expected YYYY-MM-DD)")
            if parsed != data.get(name):
                data["purchase_date_ambiguous"] = False
                data["purchase_date_alternatives"] = []
            data[name] = parsed
        elif name == "purchase_time":
            parsed = None if blank else llm.normalize_time(value)
            if not blank and parsed is None:
                raise ValueError("Invalid purchase_time (expected HH:MM)")
            data[name] = parsed
        elif name == "tags":
            data["tags"] = normalize.clean_tags(value)
        elif name == "currency":
            code = None if blank else str(value).strip().upper()
            if code is not None and (len(code) != 3 or not code.isalpha()):
                raise ValueError("Invalid currency (expected a 3-letter code)")
            data[name] = code
        edited.add(name)

    data["user_edited_fields"] = sorted(edited)
    draft.raw_llm_output = json.dumps(data, indent=2)
    db.commit()
    db.refresh(draft)
    return draft


def ensure_user(db: Session, user_id: str, display_name: str | None = None) -> None:
    """Make sure a user row exists (anonymous and system users are not created by the login step)."""
    if db.get(User, user_id) is None:
        db.add(User(id=user_id, display_name=display_name or user_id, role="USER"))
        db.flush()


def create_draft(
    db: Session,
    user_id: str,
    source: str = "camera",
    extras: dict[str, Any] | None = None,
    home_id: str | None = None,
) -> ReceiptDraft:
    """
    Create a new receipt draft.

    Args:
        db: Database session
        user_id: User identifier
        source: Source of the draft (camera, upload, etc.)
        extras: Optional extra metadata

    Returns:
        Created ReceiptDraft instance
    """
    settings = get_settings()
    ensure_user(db, user_id)

    draft = ReceiptDraft(
        id=str(uuid.uuid4()),
        user_id=user_id,
        home_id=home_id,
        status="PROCESSING",
        source=source,
        expires_at=datetime.utcnow() + timedelta(hours=settings.draft_retention_hours),
    )

    if extras:
        if "extraction_model" in extras:
            draft.extraction_model = extras["extraction_model"]
        if "extraction_prompt_version" in extras:
            draft.extraction_prompt_version = extras["extraction_prompt_version"]
        if "extraction_schema_version" in extras:
            draft.extraction_schema_version = extras["extraction_schema_version"]

    db.add(draft)
    db.commit()
    db.refresh(draft)

    logger.info("Created draft %s for user %s", draft.id, user_id)

    return draft


def get_draft(db: Session, draft_id: str) -> ReceiptDraft | None:
    """Get a draft by ID."""
    return db.query(ReceiptDraft).filter(
        ReceiptDraft.id == draft_id
    ).first()


def get_drafts(
    db: Session,
    home_id: str | None = None,
    status: str | None = None,
) -> list[ReceiptDraft]:
    """Drafts visible to every user, newest first. Optionally limited to one home and/or status."""
    query = db.query(ReceiptDraft)
    if home_id:
        query = query.filter(ReceiptDraft.home_id == home_id)
    if status:
        query = query.filter(ReceiptDraft.status == status)
    return query.order_by(ReceiptDraft.created_at.desc()).all()


def update_draft(
    db: Session,
    draft_id: str,
    updates: dict[str, Any],
) -> ReceiptDraft | None:
    """
    Update a draft with new data.

    Args:
        db: Database session
        draft_id: Draft identifier
        updates: Fields to update

    Returns:
        Updated ReceiptDraft or None if not found
    """
    draft = get_draft(db, draft_id)
    if not draft:
        return None

    # Only allow certain fields to be updated
    allowed_fields = {
        "raw_ocr_text",
        "raw_llm_output",
        "extraction_model",
        "extraction_prompt_version",
        "extraction_schema_version",
        "status",
    }

    for field, value in updates.items():
        if field in allowed_fields:
            setattr(draft, field, value)

    draft.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(draft)

    logger.info("Updated draft %s", draft_id)

    return draft


def add_draft_item(
    db: Session,
    draft_id: str,
    item_data: dict[str, Any],
) -> ReceiptDraftItem:
    """Add an item to a draft."""
    item = ReceiptDraftItem(
        id=str(uuid.uuid4()),
        receipt_draft_id=draft_id,
        **item_data,
    )

    db.add(item)
    db.commit()
    db.refresh(item)

    return item


def add_draft_tax(
    db: Session,
    draft_id: str,
    tax_data: dict[str, Any],
) -> ReceiptDraftTax:
    """Add a tax record to a draft."""
    tax = ReceiptDraftTax(
        id=str(uuid.uuid4()),
        receipt_draft_id=draft_id,
        **tax_data,
    )

    db.add(tax)
    db.commit()
    db.refresh(tax)

    return tax


def update_draft_items(
    db: Session,
    draft_id: str,
    items: list[dict[str, Any]],
) -> list[ReceiptDraftItem]:
    """
    Replace all items in a draft.

    Args:
        db: Database session
        draft_id: Draft identifier
        items: List of item data dicts

    Returns:
        List of created ReceiptDraftItem instances
    """
    # Delete existing items
    db.query(ReceiptDraftItem).filter(
        ReceiptDraftItem.receipt_draft_id == draft_id
    ).delete()

    # Add new items
    new_items = []
    for item_data in items:
        item = ReceiptDraftItem(
            id=str(uuid.uuid4()),
            receipt_draft_id=draft_id,
            **{k: v for k, v in item_data.items() if v is not None and k in ITEM_FIELDS}
        )
        db.add(item)
        new_items.append(item)

    db.commit()

    # Refresh all items
    return get_draft_items(db, draft_id)


def get_draft_items(db: Session, draft_id: str) -> list[ReceiptDraftItem]:
    """Get all items for a draft."""
    # rowid order == insertion order == the order lines are printed on the receipt
    return db.query(ReceiptDraftItem).filter(
        ReceiptDraftItem.receipt_draft_id == draft_id
    ).order_by(text("receipt_draft_items.rowid")).all()


def get_draft_taxes(db: Session, draft_id: str) -> list[ReceiptDraftTax]:
    """Get all tax records for a draft."""
    return db.query(ReceiptDraftTax).filter(
        ReceiptDraftTax.receipt_draft_id == draft_id
    ).all()


class ApprovalError(ValueError):
    """The draft cannot be approved yet; the message says why."""


def approve_draft(db: Session, draft_id: str, user_id: str) -> tuple[Receipt, dict[str, Any]] | None:
    """
    Approve a draft and create permanent receipt records.

    This is the ONLY path to create permanent receipt records. Besides the receipt and
    its items it records the store, item matches, price observations and tax
    observations, all in one transaction.

    Any user may approve any draft (data is shared within a home).

    Args:
        db: Database session
        draft_id: Draft identifier
        user_id: The approving user (recorded on the receipt and price rows)

    Returns:
        (Receipt, summary), or None if the draft does not exist or is not NEEDS_REVIEW.

    Raises:
        ApprovalError: required details (home, store name, purchase date) are missing.
    """
    draft = get_draft(db, draft_id)
    if not draft:
        logger.warning("Draft not found: %s", draft_id)
        return None

    if draft.status != "NEEDS_REVIEW":
        logger.warning("Draft %s is not in NEEDS_REVIEW status", draft_id)
        return None

    if not draft.home_id:
        raise ApprovalError("This receipt is not assigned to a home")
    ensure_user(db, user_id)

    # Header fields (store, date, totals) live in the draft's extraction JSON,
    # including any corrections the user made during review.
    header = get_extraction_json(draft)
    try:
        receipt_records.require_store_and_date(header)
    except receipt_records.MissingReceiptData as e:
        raise ApprovalError(str(e)) from e

    try:
        replaced = draft.approved_receipt_id  # set when a saved receipt was reopened for editing
        if replaced:
            _delete_receipt_records(db, replaced)
        receipt = Receipt(
            id=str(uuid.uuid4()),
            user_id=user_id,
            home_id=draft.home_id,
            store_location_id=None,  # set by record_receipt_data
            purchase_date=header.get("purchase_date"),
            purchase_time=header.get("purchase_time"),
            currency_code=header.get("currency"),
            receipt_number=header.get("receipt_number"),
            subtotal=header.get("subtotal"),
            discount_total=header.get("discount_total"),
            tax_total=header.get("tax_total"),
            fee_total=header.get("fee_total"),
            grand_total=header.get("grand_total"),
            tags=json.dumps(normalize.clean_tags(header.get("tags"))) if normalize.clean_tags(header.get("tags")) else None,
            extraction_status="EXTRACTED",
            validation_status="VALIDATED",
            extraction_model=draft.extraction_model,
            extraction_prompt_version=draft.extraction_prompt_version,
            extraction_schema_version=draft.extraction_schema_version,
            raw_ocr_text=draft.raw_ocr_text,
            raw_llm_output=draft.raw_llm_output,
        )
        db.add(receipt)
        db.flush()  # receipt row must exist before rows that reference it

        # Copy items to permanent receipt
        receipt_items = []
        draft_items = get_draft_items(db, draft_id)
        for draft_item in draft_items:
            receipt_item = ReceiptItem(
                id=str(uuid.uuid4()),
                receipt_id=receipt.id,
                receipt_description=draft_item.receipt_description,
                item_type=draft_item.item_type or "UNKNOWN",
                quantity=draft_item.quantity,
                weight_value=draft_item.weight_value,
                weight_unit=draft_item.weight_unit,
                unit_price=draft_item.unit_price,
                unit_price_unit=draft_item.unit_price_unit,
                line_subtotal=draft_item.line_subtotal,
                discount_amount=draft_item.discount_amount,
                line_total=draft_item.line_total,
                tax_amount=draft_item.tax_amount,
                extraction_confidence=draft_item.extraction_confidence,
                normalization_confidence=draft_item.normalization_confidence,
                raw_text=draft_item.raw_text,
                normalization_status="UNRESOLVED",
            )
            db.add(receipt_item)
            receipt_items.append(receipt_item)

        # Copy tax records to permanent receipt
        draft_taxes = get_draft_taxes(db, draft_id)
        for draft_tax in draft_taxes:
            db.add(TaxRecord(
                id=str(uuid.uuid4()),
                receipt_id=receipt.id,
                tax_type=draft_tax.tax_type or llm.tax_type_for(draft_tax.tax_name),
                tax_name=draft_tax.tax_name,
                tax_rate=draft_tax.tax_rate,
                taxable_amount=draft_tax.taxable_amount,
                tax_amount=draft_tax.tax_amount,
            ))
        db.flush()

        summary = receipt_records.record_receipt_data(
            db, receipt, receipt_items, draft_items, draft_taxes, header, user_id
        )

        # Remember fixes made to how this store's lines were read, for next time
        corrections.record(db, corrections.chain_for_location(db, receipt.store_location_id), draft_items)

        # Mark draft as approved
        draft.status = "APPROVED"
        draft.approved_receipt_id = receipt.id
        draft.updated_at = datetime.utcnow()

        db.commit()

        logger.info("Approved draft %s, created receipt %s", draft_id, receipt.id)

        summary["receipt_id"] = receipt.id
        # The archive copy (if a folder is set) moves to its place by date and store; never fails the approval
        archive.on_approved(db, draft_id, receipt)
        # The photos were only needed for reviewing: the receipt's data is what kept them
        # available and is already committed above, so a problem purging them now must never
        # be reported as the approval itself having failed.
        try:
            images_service.purge_draft_images(db, draft_id)
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
            logger.exception("Approved draft %s but could not remove its photos", draft_id)
        return receipt, summary

    except Exception as e:
        db.rollback()
        logger.error("Failed to approve draft %s: %s", draft_id, e)
        raise


def reject_draft(db: Session, draft_id: str, user_id: str) -> bool:
    """
    Reject a draft without creating permanent records.

    Args:
        db: Database session
        draft_id: Draft identifier
        user_id: User identifier (for ownership verification)

    Returns:
        True if rejected, False if not found/authorized
    """
    draft = get_draft(db, draft_id)
    if not draft or draft.status == "APPROVED" or draft.approved_receipt_id:
        return False  # a saved receipt (even one reopened for editing) is part of the price history

    draft.status = "REJECTED"
    draft.updated_at = datetime.utcnow()
    db.commit()

    # Best-effort: the draft is already rejected either way, so a problem removing its photos
    # must never be reported as the rejection itself having failed.
    try:
        images_service.purge_draft_images(db, draft_id)
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("Rejected draft %s but could not remove its photos", draft_id)

    archive.on_discarded(draft_id)  # a rejected scan is not kept in the archive either
    logger.info("Rejected draft %s", draft_id)

    return True


def delete_draft(db: Session, draft_id: str, user_id: str) -> bool:
    """
    Delete a draft (only if not yet reviewed).

    Args:
        db: Database session
        draft_id: Draft identifier
        user_id: User identifier

    Returns:
        True if deleted, False otherwise
    """
    draft = get_draft(db, draft_id)
    if not draft:
        return False

    if draft.status not in ("PROCESSING", "NEEDS_REVIEW", "EXTRACTION_FAILED"):
        logger.warning("Cannot delete draft %s in status %s", draft_id, draft.status)
        return False

    images_service.purge_draft_images(db, draft_id)
    _detach_extraction_runs(db, draft_id=draft_id)

    db.delete(draft)
    db.commit()
    archive.on_discarded(draft_id)

    logger.info("Deleted draft %s", draft_id)

    return True


def purge_finished_draft_images(db: Session) -> int:
    """Delete photos still attached to approved or rejected receipts (kept by older versions)."""
    ids = [
        row[0] for row in db.query(ReceiptImage.receipt_draft_id)
        .join(ReceiptDraft, ReceiptDraft.id == ReceiptImage.receipt_draft_id)
        .filter(ReceiptDraft.status.in_(["APPROVED", "REJECTED"]))
        .distinct().all()
    ]
    removed = sum(images_service.purge_draft_images(db, draft_id) for draft_id in ids)
    if removed:
        db.commit()
    return removed


def get_expired_drafts(db: Session) -> list[ReceiptDraft]:
    """Get all expired drafts."""
    now = datetime.utcnow()
    return db.query(ReceiptDraft).filter(
        ReceiptDraft.expires_at < now,
        ReceiptDraft.status.notin_(["APPROVED", "REJECTED"]),
        ReceiptDraft.approved_receipt_id.is_(None),  # never expire a saved receipt being edited
    ).all()


def cleanup_expired_drafts(db: Session) -> int:
    """Clean up expired drafts. Returns count of deleted drafts."""
    expired = get_expired_drafts(db)

    for draft in expired:
        try:
            delete_draft(db, draft.id, draft.user_id)
        except Exception as e:
            db.rollback()  # keep the session usable for the next one
            logger.error("Failed to delete expired draft %s: %s", draft.id, e)

    return len(expired)
