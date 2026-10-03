"""Filling in what a receipt line leaves out, from the last time the same thing was bought at the same store.

Receipts often print only a price: "BANANAS 1.86", "GAS UNLEADED 35.60", "KS CHICKEN BREAST 11.97". Things are usually
bought in much the same amount at the same store, so right after a receipt is read (before review):

* **weight, for things sold by weight** (last time priced per lb, kg, gallon...): worked out from last time's price per
  unit (1.86 / 0.59 per lb = about 3.15 lb), which stays right when you bought more or less than last time; last time's
  weight when that result is far off (the price changed a lot)
* **pack size, for things priced per pack** (last time a "3 lb" bag at a price each): last time's size
* **quantity**: last time's, only when the total says so (6.98 when you last bought 2 @ 3.49)

What the receipt prints is never changed. Each line filled in says so on the Review page ("about 3.15 lb, from your
last purchase here on Sep 3"), where it can be changed like anything else.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import Receipt, ReceiptItem, StoreLocation

WEIGHT_UNITS = {"LB", "LBS", "KG", "G", "OZ", "GAL", "L", "ML", "FL OZ", "FLOZ", "QT", "PT"}


def _last_time(db: Session, home_id: str, chain_id: str | None, description: str, item_id: str | None) -> ReceiptItem | None:
    """The most recent line for the same thing at the same store: the same printed description, else the same item."""
    if not chain_id:
        return None
    base = (db.query(ReceiptItem, Receipt.purchase_date)
            .join(Receipt, ReceiptItem.receipt_id == Receipt.id)
            .join(StoreLocation, Receipt.store_location_id == StoreLocation.id)
            .filter(Receipt.home_id == home_id, StoreLocation.store_chain_id == chain_id))
    for condition in ([func.lower(ReceiptItem.receipt_description) == description.strip().lower()]
                      + ([ReceiptItem.common_item_id == item_id] if item_id else [])):
        row = base.filter(condition).order_by(Receipt.purchase_date.desc()).first()
        if row is not None:
            prev, when = row
            prev._bought_on = when
            return prev
    return None


def _date_text(value: Any) -> str:
    from datetime import date
    try:
        d = date.fromisoformat(str(value)[:10])
        return d.strftime("%b %-d") if hasattr(d, "strftime") else str(value)
    except ValueError:
        return str(value or "")[:10]


def plan(line: dict[str, Any], prev: Any) -> dict[str, Any]:
    """What to fill in on ``line`` (the receipt line as read) from ``prev`` (last time's line): {field: value}, plus
    ``_note`` saying what was done. Empty when nothing is missing or last time does not help."""
    total = line.get("line_total")
    changes: dict[str, Any] = {}
    note = None
    prev_unit = (prev.unit_price_unit or "").upper().strip()
    prev_weight_unit = (prev.weight_unit or "").strip()
    if line.get("weight_value") is None and prev.weight_value and prev_weight_unit:
        if prev_unit in WEIGHT_UNITS and prev.unit_price:
            # sold by weight: from last time's price per unit; last time's weight when that is far off
            weight = prev.weight_value
            how = "your last purchase here"
            if total:
                estimate = total / prev.unit_price
                if 0.4 * prev.weight_value <= estimate <= 2.5 * prev.weight_value:
                    weight, how = estimate, "last time's price here"
            weight = round(weight, 2)
            changes = {"weight_value": weight, "weight_unit": prev_weight_unit, "unit_price_unit": prev.unit_price_unit,
                       "item_type": "WEIGHT"}
            if total:
                changes["unit_price"] = round(total / weight, 4)
            if line.get("quantity") == 1:
                changes["quantity"] = None
            note = f"about {weight:g} {prev_weight_unit.lower()}, from {how}"
        elif prev_unit in ("", "EACH", "EA"):
            # a pack with its size (a 3 lb bag): the same size as last time
            changes = {"weight_value": prev.weight_value, "weight_unit": prev_weight_unit}
            note = f"{prev.weight_value:g} {prev_weight_unit.lower()} pack, like your last purchase here"
    if (line.get("quantity") in (None, 1) and "quantity" not in changes and prev.quantity and prev.quantity > 1
            and prev.unit_price and total and abs(total - prev.quantity * prev.unit_price) <= 0.1 * total):
        changes.update({"quantity": prev.quantity, "unit_price": round(total / prev.quantity, 4), "item_type": "QUANTITY"})
        note = (note + "; " if note else "") + f"{prev.quantity:g} of them, like your last purchase here"
    if changes:
        changes["_note"] = f"{note} on {_date_text(getattr(prev, '_bought_on', ''))}"
    return changes


def fill(db: Session, home_id: str, chain_id: str | None, item: Any, item_id: str | None = None) -> str | None:
    """Fill in the draft line ``item`` from last time at this store (see ``plan``). Returns the note, or None."""
    line = {"weight_value": item.weight_value, "quantity": item.quantity, "line_total": item.line_total}
    if line["weight_value"] is not None and line["quantity"] not in (None, 1):
        return None  # everything is printed
    prev = _last_time(db, home_id, chain_id, item.receipt_description, item_id)
    if prev is None:
        return None
    changes = plan(line, prev)
    if not changes:
        return None
    note = changes.pop("_note")
    for field, value in changes.items():
        setattr(item, field, value)
    try:
        raw = json.loads(item.raw_text or "{}")
    except json.JSONDecodeError:
        raw = {}
    raw["filled_from_last_time"] = {"note": note, "fields": sorted(changes)}
    item.raw_text = json.dumps(raw)
    return note
