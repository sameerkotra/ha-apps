"""Store-level discounts: what a home saves at a store chain on top of its prices, from a store card or membership
("Sam's Club Mastercard: 5% on everything at Sam's Club, fuel included", "Costco: 2%").

Each discount takes ``percent`` off the prices it applies to (``applies_to``: ALL, FUEL or NON_FUEL), and for fuel
optionally ``cents_per_unit`` off per gallon (or litre). Several discounts at one chain add up.

They are applied wherever the app decides where something is cheapest (the trip planner and the shopping list), to
online and receipt prices alike: a card's cashback is not on the receipt, so what you paid there still costs that much
less. The "looks misread" check on online prices compares prices before discounts.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.db.models import StoreChain, StoreDiscount
from app.services import itemquery

SCOPES = ("ALL", "FUEL", "NON_FUEL")
SCOPE_TEXT = {"ALL": "everything", "FUEL": "fuel only", "NON_FUEL": "everything except fuel"}
MAX_PERCENT = 50.0
MAX_CENTS = 100.0


class DiscountError(ValueError):
    """Something a person can fix. The message is safe to show."""


def _json(d: StoreDiscount) -> dict[str, Any]:
    return {"id": d.id, "chain_id": d.store_chain_id, "name": d.name, "percent": d.percent, "cents_per_unit": d.cents_per_unit,
            "applies_to": d.applies_to, "applies_to_text": SCOPE_TEXT.get(d.applies_to, "everything"), "active": bool(d.active)}


def list_for_home(db: Session, home_id: str) -> dict[str, list[dict[str, Any]]]:
    """{chain id: [discounts]} for this home."""
    out: dict[str, list[dict[str, Any]]] = {}
    for d in db.query(StoreDiscount).filter(StoreDiscount.home_id == home_id).order_by(StoreDiscount.created_at).all():
        out.setdefault(d.store_chain_id, []).append(_json(d))
    return out


def set_for_chain(db: Session, home_id: str, chain_id: str, discounts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Replace this home's discounts at a chain with ``discounts`` ([{name, percent, cents_per_unit, applies_to, active}])."""
    if db.get(StoreChain, chain_id) is None:
        raise DiscountError("Store not found")
    cleaned = []
    for raw in discounts[:10]:
        name = " ".join(str(raw.get("name") or "").split())[:120]
        try:
            percent = float(raw.get("percent") or 0)
            cents = float(raw.get("cents_per_unit") or 0)
        except (TypeError, ValueError):
            raise DiscountError("A discount must be a number")
        scope = str(raw.get("applies_to") or "ALL").upper()
        if scope not in SCOPES:
            raise DiscountError("A discount applies to everything, fuel only, or everything except fuel")
        if not 0 <= percent <= MAX_PERCENT:
            raise DiscountError(f"A discount must be between 0 and {MAX_PERCENT:g}%")
        if not 0 <= cents <= MAX_CENTS:
            raise DiscountError(f"Cents off per gallon must be between 0 and {MAX_CENTS:g}")
        if cents and scope == "NON_FUEL":
            raise DiscountError("Cents off per gallon only applies to fuel")
        if not percent and not cents:
            continue  # nothing off: not worth keeping
        if not name:
            name = f"{percent:g}% off" if percent else f"{cents:g}¢/gal off"
        cleaned.append(StoreDiscount(home_id=home_id, store_chain_id=chain_id, name=name, percent=percent, cents_per_unit=cents,
                                     applies_to=scope, active=0 if raw.get("active") is False else 1))
    db.query(StoreDiscount).filter(StoreDiscount.home_id == home_id, StoreDiscount.store_chain_id == chain_id).delete()
    for d in cleaned:
        db.add(d)
    db.commit()
    return [_json(d) for d in cleaned]


def fuel_item_ids(observations: list[dict[str, Any]]) -> set[str]:
    """The items among these receipt lines that are fuel (by name, printed text and category)."""
    return {r["item_id"] for r in observations
            if itemquery.fuel_grade({"name": r.get("item_name") or "", "category": r.get("category"), "aliases": [r.get("description") or ""]})}


def apply(offers: dict[str, dict[str, dict[str, Any]]], stores: list[dict[str, Any]],
          discounts: dict[str, list[dict[str, Any]]], fuel_items: set[str]) -> int:
    """Take each store's discounts off its prices, in place. The offer keeps ``before_discount`` and ``discount``
    ({names, percent, cents, saved}) for showing. Returns how many prices changed."""
    chain_of = {s["id"]: s["chain_id"] for s in stores}
    changed = 0
    for item_id, per_store in offers.items():
        fuel = item_id in fuel_items
        for store_id, offer in per_store.items():
            if not offer:
                continue
            ds = [d for d in discounts.get(chain_of.get(store_id), []) if d["active"]
                  and (d["applies_to"] == "ALL" or (d["applies_to"] == "FUEL") == fuel)]
            if not ds:
                continue
            before = offer["price"]
            percent = min(sum(d["percent"] for d in ds), MAX_PERCENT)
            cents = sum(d["cents_per_unit"] for d in ds) if fuel else 0.0
            price = before * (1 - percent / 100) - cents / 100
            price = max(round(price, 4), 0.0)
            offer["before_discount"] = round(before, 4)
            offer["discount"] = {"names": [d["name"] for d in ds], "percent": percent, "cents": cents,
                                 "saved": round(before - price, 4)}
            offer["price"] = price
            changed += 1
    return changed
