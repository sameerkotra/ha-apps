"""Loading receipts and price observations as the plain rows ``analytics`` works on."""

from datetime import date
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import (
    Address,
    CommonItem,
    PriceObservation,
    Receipt,
    ReceiptItem,
    StoreChain,
    StoreLocation,
)


def iso(d: date) -> str:
    return d.isoformat()


def _line_total(obs: PriceObservation) -> float | None:
    """The amount paid, worked out from the unit price for rows saved without a total."""
    if obs.line_total is not None:
        return obs.line_total
    if obs.unit_price is None:
        return None
    return round(obs.unit_price * (obs.weight_value or obs.quantity or 1), 2)


def _sale_ratio(line_total: float | None, discount: float | None) -> float:
    """The fraction taken off the line by a sale, coupon or member price (0 when none)."""
    if not discount or discount <= 0 or line_total is None or line_total < 0:
        return 0.0
    return round(min(discount / (line_total + discount), 0.9), 4)


def load_receipts(db: Session, home_id: str, start: str | None = None, end: str | None = None) -> list[dict[str, Any]]:
    """Receipt rows for analytics.summarize_spend."""
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

    line_sums = dict(
        db.query(ReceiptItem.receipt_id, func.sum(ReceiptItem.line_total))
        .join(Receipt, ReceiptItem.receipt_id == Receipt.id)
        .filter(Receipt.home_id == home_id)
        .group_by(ReceiptItem.receipt_id)
        .all()
    )

    rows = []
    for receipt, location, chain, address in query.all():
        total = receipt.grand_total
        if total is None and receipt.subtotal is not None:
            total = receipt.subtotal + (receipt.tax_total or 0.0)
        if total is None:
            total = line_sums.get(receipt.id)
        rows.append({
            "id": receipt.id,
            "date": receipt.purchase_date,
            "chain_id": chain.id if chain else None,
            "chain_name": chain.name if chain else None,
            "location_id": location.id if location else None,
            "store_number": location.store_number if location else None,
            "city": address.city if address else None,
            "total": total,
        })
    return rows


def load_observations(
    db: Session,
    home_id: str,
    start: str | None = None,
    end: str | None = None,
    item_id: str | None = None,
    item_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Price observation rows, named by the item's common name (read once per request or job for the same question)."""
    from app.services import reqcache
    key = ("observations", home_id, start, end, item_id, tuple(sorted(item_ids)) if item_ids is not None else None)
    return reqcache.remember(key, lambda: _load_observations(db, home_id, start, end, item_id, item_ids))


def _load_observations(
    db: Session,
    home_id: str,
    start: str | None = None,
    end: str | None = None,
    item_id: str | None = None,
    item_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    query = (
        db.query(
            PriceObservation, CommonItem, StoreChain, StoreLocation, Address,
            ReceiptItem.receipt_id, ReceiptItem.receipt_description, ReceiptItem.discount_amount,
            ReceiptItem.weight_value, ReceiptItem.weight_unit,
        )
        .join(CommonItem, PriceObservation.common_item_id == CommonItem.id)
        .join(StoreChain, PriceObservation.store_chain_id == StoreChain.id)
        .join(StoreLocation, PriceObservation.store_location_id == StoreLocation.id)
        .outerjoin(Address, StoreLocation.address_id == Address.id)
        .outerjoin(ReceiptItem, PriceObservation.receipt_item_id == ReceiptItem.id)
        .filter(PriceObservation.home_id == home_id)
    )
    if start:
        query = query.filter(PriceObservation.purchase_date >= start)
    if end:
        query = query.filter(PriceObservation.purchase_date < end)
    if item_id:
        query = query.filter(PriceObservation.common_item_id == item_id)
    if item_ids is not None:
        query = query.filter(PriceObservation.common_item_id.in_(item_ids))

    return [{
        "item_id": item.id,
        "item_name": item.name,
        "receipt_id": receipt_id,
        "chain_id": chain.id,
        "chain_name": chain.name,
        "location_id": location.id,
        "store_number": location.store_number,
        "city": address.city if address else None,
        "date": obs.purchase_date,
        "unit_price": obs.unit_price,
        "unit": obs.unit_price_unit,
        "line_total": _line_total(obs),
        "description": description,
        "category": item.category,
        "sale_ratio": _sale_ratio(_line_total(obs), discount),
        # the weight printed on (or filled in for) the line: a pack sold each ("3 LB BAG") gives its size
        "weight_value": weight_value, "weight_unit": weight_unit,
    } for obs, item, chain, location, address, receipt_id, description, discount, weight_value, weight_unit in query.all()]


def load_discount_lines(db: Session, home_id: str, start: str | None = None, end: str | None = None) -> list[dict[str, Any]]:
    """Lines where a sale, coupon or member price took something off, for insights.discount_summary.

    A discount is either recorded on the line it applies to (``discount_amount``), or is a line of its
    own with a negative total (a coupon read as a separate line).
    """
    query = (
        db.query(ReceiptItem, Receipt, StoreChain, CommonItem)
        .join(Receipt, ReceiptItem.receipt_id == Receipt.id)
        .outerjoin(StoreLocation, Receipt.store_location_id == StoreLocation.id)
        .outerjoin(StoreChain, StoreLocation.store_chain_id == StoreChain.id)
        .outerjoin(CommonItem, ReceiptItem.common_item_id == CommonItem.id)
        .filter(Receipt.home_id == home_id)
        .filter((ReceiptItem.discount_amount > 0) | (ReceiptItem.line_total < 0))
    )
    if start:
        query = query.filter(Receipt.purchase_date >= start)
    if end:
        query = query.filter(Receipt.purchase_date < end)

    rows = []
    for item, receipt, chain, common in query.all():
        if item.line_total is not None and item.line_total < 0:
            saved, paid = -item.line_total, 0.0
        else:
            saved, paid = item.discount_amount or 0.0, item.line_total or 0.0
        rows.append({
            "receipt_id": receipt.id, "date": receipt.purchase_date,
            "chain_id": chain.id if chain else None, "chain_name": chain.name if chain else None,
            "item_name": common.name if common else item.receipt_description, "saved": saved, "paid": paid,
        })
    return rows
