"""Turn an approved receipt into store, item, price, and tax records.

Runs inside the approval transaction (see ``drafts.approve_draft``): either everything is
written or nothing is. Matching is deliberately conservative and exact:

* stores match on normalized chain name, then store number, then street address;
* items match on the normalized printed description within the same home. An unmatched
  description creates a new item named exactly as printed. It is never renamed or merged
  automatically; that stays a human decision.
"""

from __future__ import annotations

import re

from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import (
    Address,
    CommonItem,
    CommonItemAlias,
    PriceObservation,
    Receipt,
    ReceiptItem,
    StoreChain,
    StoreChainAlias,
    StoreLocation,
    StoreTaxObservation,
)
from app.logging_config import get_logger
from app.services import normalize
from app.services.categories import suggest_category

logger = get_logger("receipt_records")


class MissingReceiptData(ValueError):
    """Required receipt details are absent; the message says what to add."""


def require_store_and_date(header: dict[str, Any]) -> tuple[tuple[str, str], str]:
    """Return ((store display, store key), purchase_date) or raise MissingReceiptData."""
    store = normalize.normalize_store_name(header.get("store_name"))
    date = header.get("purchase_date")
    missing = []
    if store is None:
        missing.append("store name")
    if not date:
        missing.append("purchase date")
    if missing:
        raise MissingReceiptData("Add the " + " and ".join(missing) + " before saving")
    return store, date


def find_chain_by_key(db: Session, key: str) -> StoreChain | None:
    """Chain whose normalized name is ``key``, or that once went by that name (rename/merge)."""
    chain = db.query(StoreChain).filter(StoreChain.normalized_name == key).first()
    if chain is not None:
        return chain
    alias = db.query(StoreChainAlias).filter(StoreChainAlias.normalized_alias == key).first()
    return db.get(StoreChain, alias.store_chain_id) if alias else None


def get_or_create_chain(db: Session, display: str, key: str) -> StoreChain:
    chain = find_chain_by_key(db, key)
    if chain is None:
        chain = StoreChain(name=display, normalized_name=key)
        db.add(chain)
        db.flush()
    return chain


def _parsed_of(address: Address | None) -> dict[str, Any] | None:
    """A saved address read the same way as a new receipt's (from its printed text when there is one, so addresses
    saved by older versions compare the same)."""
    if address is None:
        return None
    if address.raw_address:
        parsed = normalize.parse_address(address.raw_address)
        if parsed.get("street"):
            return parsed
    return {"raw": address.raw_address, "street": address.street_line_1, "city": address.city, "state": address.state_province,
            "postal_code": address.postal_code, "unit": None}


def chain_at_address(db: Session, key: str, address_text: str | None) -> StoreChain | None:
    """A store already saved at this receipt's address, whose name is close to the printed one ("KINGSOOPERS",
    "KING SOOPERS FUEL" -> King Soopers). A different business at the same address (a coffee shop inside a
    supermarket) is not matched."""
    parsed = normalize.parse_address(address_text)
    if not parsed.get("street"):
        return None
    for location, address, chain in (db.query(StoreLocation, Address, StoreChain)
                                     .join(Address, StoreLocation.address_id == Address.id)
                                     .join(StoreChain, StoreLocation.store_chain_id == StoreChain.id).all()):
        saved = _parsed_of(address)
        if saved and normalize.same_address(parsed, saved) and normalize.similar_store_names(key, chain.normalized_name):
            return chain
    return None


def get_or_create_location(
    db: Session,
    chain: StoreChain,
    store_number: str | None,
    address_text: str | None,
) -> StoreLocation:
    """Find this branch of the chain, or create it."""
    parsed = normalize.parse_address(address_text)
    wanted_key = normalize.address_key(parsed)

    by_number = None
    if store_number:
        by_number = (
            db.query(StoreLocation)
            .filter(StoreLocation.store_chain_id == chain.id, StoreLocation.store_number == store_number)
            .first()
        )
    if by_number is not None:
        if by_number.address_id is None and parsed["raw"]:
            by_number.address_id = _new_address(db, parsed).id
        return by_number

    locations = (
        db.query(StoreLocation, Address)
        .outerjoin(Address, StoreLocation.address_id == Address.id)
        .filter(StoreLocation.store_chain_id == chain.id)
        .all()
    )

    if wanted_key:
        # the same place, however the address was printed ("18901 E MAINSTREET" / "18901 East Main Street", a misread
        # letter); two different store numbers are two stores even at one address
        for location, address in locations:
            saved = _parsed_of(address)
            if saved is None or (store_number and location.store_number and location.store_number != store_number):
                continue
            if normalize.address_key(saved) == wanted_key or normalize.same_address(parsed, saved):
                if store_number and not location.store_number:
                    location.store_number = store_number  # first receipt had no number
                if address is not None and not address.street_line_1 and parsed.get("street"):
                    # saved by an older reader that put the street in the city: store it the way it reads now
                    address.street_line_1 = parsed["street"]
                    if not address.city or re.match(r"\s*\d", address.city):
                        address.city = parsed["city"]
                return location
    elif not store_number:
        # Nothing identifies the branch: share one "unknown location" per chain...
        for location, address in locations:
            if location.store_number is None and location.address_id is None:
                return location
        # ...or, if the chain has only one location (for example after a merge into a single
        # store), it can only be that one.
        if len(locations) == 1:
            return locations[0][0]

    address_id = _new_address(db, parsed).id if parsed["raw"] else None
    location = StoreLocation(
        store_chain_id=chain.id,
        address_id=address_id,
        name=chain.name,
        store_number=store_number,
    )
    db.add(location)
    db.flush()
    return location


def _new_address(db: Session, parsed: dict[str, Any]) -> Address:
    address = Address(
        raw_address=parsed["raw"],
        street_line_1=parsed["street"],
        city=parsed["city"],
        state_province=parsed["state"],
        postal_code=parsed["postal_code"],
    )
    db.add(address)
    db.flush()
    return address


def clean_common_name(name: str | None) -> str | None:
    cleaned = " ".join((name or "").split())[:255]
    return cleaned or None


def find_item_by_name(db: Session, home_id: str, name: str) -> CommonItem | None:
    """Active item in this home with this common name (case-insensitive)."""
    return (
        db.query(CommonItem)
        .filter(
            CommonItem.home_id == home_id,
            CommonItem.status == "ACTIVE",
            func.lower(CommonItem.name) == name.lower(),
        )
        .first()
    )


def find_item_by_alias(db: Session, home_id: str, key: str) -> CommonItem | None:
    """Active item in this home already linked to this printed description."""
    return (
        db.query(CommonItem)
        .join(CommonItemAlias, CommonItemAlias.common_item_id == CommonItem.id)
        .filter(
            CommonItem.home_id == home_id,
            CommonItem.status == "ACTIVE",
            CommonItemAlias.normalized_alias == key,
        )
        .first()
    )


def find_confirmed_common_item(db: Session, home_id: str, description: str) -> CommonItem | None:
    """The common name to pre-fill for a printed description, if a person has already named it.

    Items that only carry their printed text as a name (never confirmed) are not suggested,
    since that would just repeat the description.
    """
    key = normalize.item_key(description)
    if key is None or not home_id:
        return None
    item = find_item_by_alias(db, home_id, key)
    return item if item is not None and item.name_confirmed else None


def _link_alias(db: Session, home_id: str, user_id: str, item: CommonItem, description: str, key: str, source: str) -> None:
    """Make ``description`` resolve to ``item`` from now on (latest choice wins)."""
    alias = (
        db.query(CommonItemAlias)
        .join(CommonItem, CommonItemAlias.common_item_id == CommonItem.id)
        .filter(CommonItem.home_id == home_id, CommonItemAlias.normalized_alias == key)
        .first()
    )
    if alias is None:
        db.add(CommonItemAlias(
            user_id=user_id, common_item_id=item.id, alias=description.strip()[:255],
            normalized_alias=key, source=source,
        ))
    elif alias.common_item_id != item.id:
        alias.common_item_id = item.id
        alias.source = source
    db.flush()


def resolve_common_item(
    db: Session,
    home_id: str,
    user_id: str,
    description: str,
    common_name: str | None = None,
    common_id: str | None = None,
) -> tuple[CommonItem | None, bool]:
    """The common item for one receipt line. Returns (item, matched_existing).

    * A name typed or chosen by the person wins. It joins the existing item with that name
      or creates one, and the printed description is linked to it for future receipts.
    * Otherwise a previously linked item is reused.
    * Otherwise an item named exactly as printed is created (unconfirmed, so the UI can ask
      for a proper name later). Nothing is ever renamed or merged automatically.
    """
    key = normalize.item_key(description)
    name = clean_common_name(common_name)
    if key is None and name is None:
        return None, False

    if name:
        item = None
        if common_id:
            candidate = db.get(CommonItem, common_id)
            if (candidate is not None and candidate.home_id == home_id and candidate.status == "ACTIVE"
                    and candidate.name.lower() == name.lower()):
                item = candidate
        if item is None:
            item = find_item_by_name(db, home_id, name)
        matched = item is not None
        if item is None:
            item = CommonItem(home_id=home_id, user_id=user_id, name=name, name_confirmed=1, status="ACTIVE", category=suggest_category(name))
            db.add(item)
            db.flush()
        elif not item.name_confirmed:
            item.name_confirmed = 1
        if key:
            _link_alias(db, home_id, user_id, item, description, key, "USER")
        return item, matched

    existing = find_item_by_alias(db, home_id, key)
    if existing is not None:
        return existing, True

    item = CommonItem(home_id=home_id, user_id=user_id, name=description.strip()[:255], name_confirmed=0, status="ACTIVE", category=suggest_category(description))
    db.add(item)
    db.flush()
    _link_alias(db, home_id, user_id, item, description, key, "EXTRACTION")
    return item, False


def record_receipt_data(
    db: Session,
    receipt: Receipt,
    receipt_items: list[ReceiptItem],
    draft_items: list[Any],
    tax_rows: list[Any],
    header: dict[str, Any],
    user_id: str,
) -> dict[str, Any]:
    """Create store, item, price and tax records for an approved receipt.

    ``receipt`` must already have ``home_id`` and ``purchase_date`` set and be added to
    the session. Returns a summary for the API/UI.
    """
    (display, key), purchase_date = require_store_and_date(header)
    currency = receipt.currency_code or get_settings().default_currency
    receipt.currency_code = currency

    chain = find_chain_by_key(db, key)
    if chain is None:
        # a name not seen before, at the address of a store you have (printed differently): that store, and the
        # printed name is remembered for it so the next receipt resolves straight away
        chain = chain_at_address(db, key, header.get("store_address"))
        if chain is not None and not db.query(StoreChainAlias).filter(StoreChainAlias.normalized_alias == key).first():
            db.add(StoreChainAlias(store_chain_id=chain.id, normalized_alias=key))
            logger.info("Receipt store %r matched to %s by its address", display, chain.name)
    if chain is None:
        chain = get_or_create_chain(db, display, key)
    location = get_or_create_location(
        db, chain, (header.get("store_number") or None), header.get("store_address")
    )
    receipt.store_location_id = location.id

    matched = created = observations = 0
    for row, draft_item in zip(receipt_items, draft_items):
        item_dict = {
            "receipt_description": row.receipt_description,
            "line_total": row.line_total,
            "unit_price": row.unit_price,
            "unit_price_unit": row.unit_price_unit,
            "weight_value": row.weight_value,
            "weight_unit": row.weight_unit,
            "quantity": row.quantity,
        }
        if not normalize.is_priceable(item_dict):
            continue  # coupons, unreadable lines, lines with no price

        common, was_matched = resolve_common_item(
            db, receipt.home_id, user_id, row.receipt_description,
            common_name=draft_item.suggested_common_item_name,
            common_id=draft_item.suggested_common_item_id,
        )
        if common is None:
            continue

        row.common_item_id = common.id
        row.normalized_description = normalize.item_key(row.receipt_description)
        row.normalization_status = (
            "USER_CONFIRMED" if (draft_item.suggested_common_item_name or "").strip()
            else "EXACT_MATCH" if was_matched else "NEW_ITEM"
        )
        row.normalization_confidence = 1.0 if row.normalization_status != "NEW_ITEM" else None
        matched += was_matched
        created += not was_matched

        unit_price, unit = normalize.derive_price(item_dict)
        db.add(PriceObservation(
            user_id=user_id,
            home_id=receipt.home_id,
            common_item_id=common.id,
            receipt_item_id=row.id,
            store_chain_id=chain.id,
            store_location_id=location.id,
            purchase_date=purchase_date,
            quantity=row.quantity,
            weight_value=row.weight_value,
            weight_unit=row.weight_unit,
            unit_price=unit_price,
            unit_price_unit=unit,
            line_total=normalize.derive_line_total(item_dict, unit_price),
            currency_code=currency,
        ))
        observations += 1

    tax_observations = 0
    for tax in tax_rows:
        if tax.tax_rate is None and tax.tax_amount is None:
            continue
        db.add(StoreTaxObservation(
            store_location_id=location.id,
            receipt_id=receipt.id,
            tax_name=tax.tax_name,
            tax_rate=tax.tax_rate,
            taxable_amount=tax.taxable_amount,
            tax_amount=tax.tax_amount,
            observation_date=purchase_date,
        ))
        tax_observations += 1

    db.flush()
    logger.info(
        "Recorded receipt %s at %s (location %s): %d prices, %d new items, %d tax rows",
        receipt.id, chain.name, location.id, observations, created, tax_observations,
    )
    return {
        "store_chain_id": chain.id,
        "store_location_id": location.id,
        "store_name": chain.name,
        "price_observations": observations,
        "items_matched": matched,
        "items_created": created,
        "tax_observations": tax_observations,
    }
