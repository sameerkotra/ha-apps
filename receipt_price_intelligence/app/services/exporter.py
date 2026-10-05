"""CSV export of receipts and their lines, for spreadsheets, taxes or expense claims."""

import json
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.common import csv_export
from app.db.models import Address, CommonItem, Receipt, ReceiptItem, StoreChain, StoreLocation

RECEIPT_COLUMNS = [
    "date", "time", "store", "store_number", "address", "currency", "subtotal", "discounts", "tax", "fees",
    "total", "receipt_number", "tags", "receipt_id",
]
ITEM_COLUMNS = [
    "date", "store", "store_number", "item", "category", "printed_description", "quantity", "weight", "weight_unit",
    "unit_price", "unit", "discount", "line_total", "receipt_number", "tags", "receipt_id",
]


class ExportError(ValueError):
    """A bad export request. The message is safe to show."""


def parse_range(start: str | None, end: str | None) -> tuple[str | None, str | None]:
    """Validate optional ISO dates. ``end`` is inclusive, so it is returned as the day after."""
    try:
        s = date.fromisoformat(start).isoformat() if start else None
        e = None
        if end:
            from datetime import timedelta
            e = (date.fromisoformat(end) + timedelta(days=1)).isoformat()
    except ValueError:
        raise ExportError("Dates must look like 2025-03-14")
    if s and e and s >= e:
        raise ExportError("The end date is before the start date")
    return s, e


def _tags(receipt: Receipt) -> list[str]:
    try:
        value = json.loads(receipt.tags) if receipt.tags else []
        return [str(t) for t in value] if isinstance(value, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _wanted(receipt: Receipt, tag: str | None) -> bool:
    return not tag or tag.strip().lower() in {t.lower() for t in _tags(receipt)}


# Stop spreadsheets treating text as a formula (a leading = + - @ or control character).
_safe = csv_export.guard


def to_csv(rows: list[dict[str, Any]], columns: list[str]) -> str:
    """UTF-8 CSV with a byte-order mark so Excel opens accents correctly."""
    return csv_export.to_text(columns, ([_safe(row.get(c)) for c in columns] for row in rows), bom=True)


def _base_query(db: Session, home_id: str, start: str | None, end: str | None):
    query = (
        db.query(Receipt, StoreLocation, StoreChain, Address)
        .outerjoin(StoreLocation, Receipt.store_location_id == StoreLocation.id)
        .outerjoin(StoreChain, StoreLocation.store_chain_id == StoreChain.id)
        .outerjoin(Address, StoreLocation.address_id == Address.id)
        .filter(Receipt.home_id == home_id)
    )
    if start:
        query = query.filter(Receipt.purchase_date >= start)
    if end:
        query = query.filter(Receipt.purchase_date < end)
    return query.order_by(Receipt.purchase_date, Receipt.purchase_time)


def receipt_rows(db: Session, home_id: str, start: str | None, end: str | None, tag: str | None) -> list[dict[str, Any]]:
    rows = []
    for receipt, location, chain, address in _base_query(db, home_id, start, end).all():
        if not _wanted(receipt, tag):
            continue
        rows.append({
            "date": receipt.purchase_date, "time": receipt.purchase_time, "store": chain.name if chain else None,
            "store_number": location.store_number if location else None,
            "address": (address.raw_address or address.street_line_1) if address else None,
            "currency": receipt.currency_code, "subtotal": receipt.subtotal, "discounts": receipt.discount_total,
            "tax": receipt.tax_total, "fees": receipt.fee_total, "total": receipt.grand_total,
            "receipt_number": receipt.receipt_number, "tags": "; ".join(_tags(receipt)), "receipt_id": receipt.id,
        })
    return rows


def item_rows(db: Session, home_id: str, start: str | None, end: str | None, tag: str | None) -> list[dict[str, Any]]:
    receipts = {r.id: (r, loc, chain) for r, loc, chain, _ in _base_query(db, home_id, start, end).all() if _wanted(r, tag)}
    if not receipts:
        return []
    lines = (
        db.query(ReceiptItem, CommonItem).outerjoin(CommonItem, ReceiptItem.common_item_id == CommonItem.id)
        .filter(ReceiptItem.receipt_id.in_(list(receipts))).all()
    )
    order = {rid: n for n, rid in enumerate(receipts)}
    rows = []
    for item, common in sorted(lines, key=lambda x: (order[x[0].receipt_id], x[0].created_at.timestamp() if x[0].created_at else 0.0)):
        receipt, location, chain = receipts[item.receipt_id]
        rows.append({
            "date": receipt.purchase_date, "store": chain.name if chain else None,
            "store_number": location.store_number if location else None,
            "item": common.name if common else item.receipt_description, "category": common.category if common else None,
            "printed_description": item.receipt_description, "quantity": item.quantity, "weight": item.weight_value,
            "weight_unit": item.weight_unit, "unit_price": item.unit_price, "unit": item.unit_price_unit,
            "discount": item.discount_amount, "line_total": item.line_total, "receipt_number": receipt.receipt_number,
            "tags": "; ".join(_tags(receipt)), "receipt_id": receipt.id,
        })
    return rows
