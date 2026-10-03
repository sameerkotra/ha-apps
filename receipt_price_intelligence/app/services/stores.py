"""Managing stores: chains, their locations, addresses, and clean-up of duplicates.

Stores are shared by every home. Anything that re-points or removes rows (merges, deletes)
is done here in one transaction so price history is never left pointing at a store that
no longer exists.

When a chain is renamed or merged, the name it used to have is kept as an *alias*, so the
next receipt that prints the old name still lands on the right chain.
"""

from __future__ import annotations

import json
import re
from urllib.parse import urlparse
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import (
    Address,
    PriceObservation,
    Receipt,
    Recommendation,
    StoreChain,
    StoreChainAlias,
    StoreLocation,
    StoreTaxObservation,
)
from app.logging_config import get_logger
from app.services import normalize, planner
from app.services.receipt_records import find_chain_by_key

logger = get_logger("stores")


class StoreError(ValueError):
    """A store change that cannot be made. ``conflict_id`` names the row in the way, if any."""

    def __init__(self, message: str, conflict_id: str | None = None):
        super().__init__(message)
        self.conflict_id = conflict_id


def _clean(text: str | None, limit: int) -> str | None:
    cleaned = " ".join((text or "").split())[:limit]
    return cleaned or None


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #

def _address_dict(address: Address | None) -> dict[str, Any]:
    return {
        "raw": address.raw_address if address else None,
        "street": address.street_line_1 if address else None,
        "city": address.city if address else None,
        "state": address.state_province if address else None,
        "postal_code": address.postal_code if address else None,
    }


def _compose_address(street: str | None, city: str | None, state: str | None, postal: str | None) -> str | None:
    tail = " ".join(x for x in (state, postal) if x)
    parts = [x for x in (street, city, tail) if x]
    return ", ".join(parts) or None


def list_chains(db: Session, home_id: str) -> dict[str, Any]:
    """Chains (with locations and stats for this home) plus likely duplicates.

    A chain is listed if this home has receipts from it, or if nothing has receipts from it
    yet (for example one just created by hand).
    """
    stats = {
        loc_id: (count, spend or 0.0, last)
        for loc_id, count, spend, last in db.query(
            Receipt.store_location_id, func.count(Receipt.id), func.sum(Receipt.grand_total), func.max(Receipt.purchase_date)
        ).filter(Receipt.home_id == home_id).group_by(Receipt.store_location_id).all()
    }
    everywhere = dict(
        db.query(Receipt.store_location_id, func.count(Receipt.id)).group_by(Receipt.store_location_id).all()
    )

    aliases: dict[str, list[str]] = {}
    for alias in db.query(StoreChainAlias).all():
        aliases.setdefault(alias.store_chain_id, []).append(alias.normalized_alias)

    rows = (
        db.query(StoreLocation, Address)
        .outerjoin(Address, StoreLocation.address_id == Address.id)
        .all()
    )
    by_chain: dict[str, list[tuple[StoreLocation, Address | None]]] = {}
    for loc, addr in rows:
        by_chain.setdefault(loc.store_chain_id, []).append((loc, addr))

    chains_out = []
    for chain in db.query(StoreChain).order_by(func.lower(StoreChain.name)).all():
        locs = by_chain.get(chain.id, [])
        global_receipts = sum(everywhere.get(l.id, 0) for l, _ in locs)
        home_receipts = sum(stats.get(l.id, (0, 0, None))[0] for l, _ in locs)
        if home_receipts == 0 and global_receipts > 0:
            continue  # only other homes use this chain

        loc_out = []
        for loc, addr in sorted(locs, key=lambda x: (-(stats.get(x[0].id, (0,))[0]), x[0].store_number or "")):
            count, spend, last = stats.get(loc.id, (0, 0.0, None))
            loc_out.append({
                "id": loc.id,
                "store_number": loc.store_number,
                "name": loc.name,
                "address": _address_dict(addr),
                "label": _label(chain.name, loc.store_number, addr),
                "opening_hours": json.loads(loc.opening_hours) if loc.opening_hours else None,
                "page_url": loc.page_url,
                "receipt_count": count,
                "spend": round(spend, 2),
                "last_visit": last,
                "deletable": everywhere.get(loc.id, 0) == 0,
            })
        chains_out.append({
            "id": chain.id,
            "name": chain.name,
            "key": chain.normalized_name,
            "aliases": sorted(aliases.get(chain.id, [])),
            "website": chain.website, "search_url": chain.search_url, "deals_url": chain.deals_url,
            "receipt_count": home_receipts,
            "spend": round(sum(l["spend"] for l in loc_out), 2),
            "last_visit": max((l["last_visit"] for l in loc_out if l["last_visit"]), default=None),
            "deletable": global_receipts == 0,
            "locations": loc_out,
        })

    similar = normalize.similar_chains([(c["id"], c["key"]) for c in chains_out])

    duplicate_locations = []
    for chain in chains_out:
        locs = chain["locations"]
        for i, a in enumerate(locs):
            for b in locs[i + 1:]:
                pa, pb = _parsed(a["address"]), _parsed(b["address"])
                if (a.get("store_number") and b.get("store_number") and a["store_number"] != b["store_number"]):
                    continue  # two store numbers: two stores
                akey, bkey = normalize.address_key(pa), normalize.address_key(pb)
                if (akey and akey == bkey) or normalize.same_address(pa, pb):
                    duplicate_locations.append({"chain_id": chain["id"], "a": a["id"], "b": b["id"], "reason": "Same street address"})

    return {"chains": chains_out, "similar_chains": similar, "duplicate_locations": duplicate_locations}


def _parsed(address: dict[str, Any] | None) -> dict[str, Any]:
    """A location's address read the way receipts are (from its printed text when there is one), for comparing."""
    address = address or {}
    if address.get("raw"):
        parsed = normalize.parse_address(address["raw"])
        if parsed.get("street"):
            return parsed
    return {"street": address.get("street"), "city": address.get("city"), "postal_code": address.get("postal_code"), "unit": None}


def _label(chain_name: str, store_number: str | None, address: Address | None) -> str:
    label = chain_name
    if store_number:
        label += f" #{store_number}"
    if address is not None and address.street_line_1:
        label += f" · {address.street_line_1}"
    elif address is not None and address.city:
        label += f" · {address.city}"
    return label


# --------------------------------------------------------------------------- #
# Chains
# --------------------------------------------------------------------------- #

def _set_alias(db: Session, chain: StoreChain, key: str) -> None:
    """Make ``key`` resolve to ``chain`` (no-op for the chain's own current key)."""
    if not key or key == chain.normalized_name:
        return
    alias = db.query(StoreChainAlias).filter(StoreChainAlias.normalized_alias == key).first()
    if alias is None:
        db.add(StoreChainAlias(store_chain_id=chain.id, normalized_alias=key))
    else:
        alias.store_chain_id = chain.id


def rename_chain(db: Session, chain_id: str, name: str) -> StoreChain | None:
    """Rename a chain. Raises StoreError if that name already belongs to another chain."""
    chain = db.get(StoreChain, chain_id)
    if chain is None:
        return None

    display = _clean(name, 255)
    normalized = normalize.normalize_store_name(display)
    if display is None or normalized is None:
        raise StoreError("A store needs a name")
    key = normalized[1]

    other = find_chain_by_key(db, key)
    if other is not None and other.id != chain.id:
        raise StoreError(f'"{other.name}" already exists. Merge the two stores instead.', conflict_id=other.id)

    old_key, old_name = chain.normalized_name, chain.name
    chain.name = display
    chain.normalized_name = key
    if old_key != key:
        _set_alias(db, chain, old_key)
    # a stale alias equal to the new primary key would be redundant
    db.query(StoreChainAlias).filter(
        StoreChainAlias.normalized_alias == key, StoreChainAlias.store_chain_id == chain.id
    ).delete(synchronize_session=False)
    # locations that carried the old chain name as their label follow the rename
    db.query(StoreLocation).filter(
        StoreLocation.store_chain_id == chain.id, StoreLocation.name == old_name
    ).update({"name": display}, synchronize_session=False)
    db.commit()
    db.refresh(chain)
    return chain


def _combine_locations(db: Session, chain_id: str) -> int:
    """Merge every location of a chain into one, so all its receipts belong to a single store.

    The location with the most receipts is kept (then one with an address, then the oldest);
    it inherits a store number or address the others had if it lacked them. Returns how many
    locations were folded in.
    """
    locations = db.query(StoreLocation).filter(StoreLocation.store_chain_id == chain_id).all()
    if len(locations) < 2:
        return 0
    counts = {l.id: _receipt_count(db, l.id) for l in locations}
    keeper = max(locations, key=lambda l: (counts[l.id], bool(l.address_id), -(l.created_at.timestamp() if getattr(l, "created_at", None) else 0)))
    combined = 0
    for loc in locations:
        if loc.id != keeper.id:
            _merge_location_rows(db, loc, keeper)
            combined += 1
    db.flush()
    return combined


def merge_chains(db: Session, source_id: str, target_id: str, combine_locations: bool = True) -> dict[str, int]:
    """Fold ``source`` into ``target``: locations, price history and names all move over.

    With ``combine_locations`` (the default) the merged store ends up as a single location that
    holds every receipt from both. Without it, each location stays separate under the merged chain
    (locations with the same store number or address are still combined).
    """
    if source_id == target_id:
        raise StoreError("Choose a different store to merge into")
    source, target = db.get(StoreChain, source_id), db.get(StoreChain, target_id)
    if source is None or target is None:
        raise StoreError("Store not found")
    for field in ("website", "search_url", "deals_url"):  # keep web pages the target was missing
        if not getattr(target, field) and getattr(source, field):
            setattr(target, field, getattr(source, field))

    moved = merged = 0
    try:
        target_locs = (
            db.query(StoreLocation, Address)
            .outerjoin(Address, StoreLocation.address_id == Address.id)
            .filter(StoreLocation.store_chain_id == target.id)
            .all()
        )
        for loc in db.query(StoreLocation).filter(StoreLocation.store_chain_id == source.id).all():
            src_addr = db.get(Address, loc.address_id) if loc.address_id else None
            as_dict = lambda a: {"raw": a.raw_address, "street": a.street_line_1, "city": a.city, "postal_code": a.postal_code} if a else None  # noqa: E731
            src_parsed = _parsed(as_dict(src_addr)) if src_addr else None
            src_key = normalize.address_key(src_parsed) if src_parsed else None
            twin = None
            for t_loc, t_addr in target_locs:
                same_number = loc.store_number and loc.store_number == t_loc.store_number
                t_parsed = _parsed(as_dict(t_addr)) if t_addr else None
                t_key = normalize.address_key(t_parsed) if t_parsed else None
                if same_number or (src_key and src_key == t_key) or (src_parsed and t_parsed and normalize.same_address(src_parsed, t_parsed)):
                    twin = t_loc
                    break
            if twin is not None:
                _merge_location_rows(db, loc, twin)
                merged += 1
            else:
                if loc.name == source.name:
                    loc.name = target.name
                loc.store_chain_id = target.id
                moved += 1
        db.flush()

        db.query(PriceObservation).filter(PriceObservation.store_chain_id == source.id).update(
            {"store_chain_id": target.id}, synchronize_session=False
        )
        # names: the source's own name and every alias it had now resolve to the target
        old_keys = [source.normalized_name] + [
            a.normalized_alias for a in db.query(StoreChainAlias).filter(StoreChainAlias.store_chain_id == source.id).all()
        ]
        db.query(StoreChainAlias).filter(StoreChainAlias.store_chain_id == source.id).delete(synchronize_session=False)
        db.delete(source)
        db.flush()
        for key in old_keys:
            _set_alias(db, target, key)
        combined = _combine_locations(db, target.id) if combine_locations else 0
        db.commit()
    except Exception:
        db.rollback()
        raise
    logger.info("Merged chain %s into %s (%d moved, %d merged locations)", source_id, target_id, moved, merged)
    return {"locations_moved": moved, "locations_merged": merged, "locations_combined": combined}


def delete_chain(db: Session, chain_id: str) -> bool:
    """Delete a chain that no receipt refers to."""
    chain = db.get(StoreChain, chain_id)
    if chain is None:
        return False
    for loc in db.query(StoreLocation).filter(StoreLocation.store_chain_id == chain_id).all():
        if _receipt_count(db, loc.id):
            raise StoreError("This store has receipts. Merge it into another store instead of deleting it.")
    for loc in db.query(StoreLocation).filter(StoreLocation.store_chain_id == chain_id).all():
        _delete_location_rows(db, loc)
    db.query(StoreChainAlias).filter(StoreChainAlias.store_chain_id == chain_id).delete(synchronize_session=False)
    db.delete(chain)
    db.commit()
    return True


# --------------------------------------------------------------------------- #
# Locations
# --------------------------------------------------------------------------- #

def _receipt_count(db: Session, location_id: str) -> int:
    return db.query(func.count(Receipt.id)).filter(Receipt.store_location_id == location_id).scalar() or 0


def _number_conflict(db: Session, chain_id: str, store_number: str | None, exclude_id: str | None) -> StoreLocation | None:
    if not store_number:
        return None
    query = db.query(StoreLocation).filter(
        StoreLocation.store_chain_id == chain_id, StoreLocation.store_number == store_number
    )
    if exclude_id:
        query = query.filter(StoreLocation.id != exclude_id)
    return query.first()


def _hours_json(value: Any) -> str | None:
    """Validate opening hours and return them as JSON text (None clears them)."""
    if not value:
        return None
    if not isinstance(value, dict) or planner.parse_hours(value) is None:
        raise StoreError("The opening hours are not valid")
    return json.dumps(value)


def _apply_address(db: Session, location: StoreLocation, data: dict[str, Any]) -> None:
    """Update (or create) the location's address from any of raw/street/city/state/postal_code."""
    fields = ("raw", "street", "city", "state", "postal_code")
    if not any(f in data for f in fields):
        return

    address = db.get(Address, location.address_id) if location.address_id else None
    if address is None:
        address = Address()
        db.add(address)
    before = (address.street_line_1, address.city, address.state_province, address.postal_code)

    if "street" in data:
        address.street_line_1 = _clean(data["street"], 255)
    if "city" in data:
        address.city = _clean(data["city"], 100)
    if "state" in data:
        state = _clean(data["state"], 100)
        address.state_province = state.upper() if state and len(state) == 2 else state
    if "postal_code" in data:
        address.postal_code = _clean(data["postal_code"], 20)

    if data.get("raw"):
        raw = _clean(data["raw"], 500)
        if not any(data.get(f) for f in ("street", "city", "state", "postal_code")):
            # only the full text was given: read the parts out of it
            parsed = normalize.parse_address(raw)
            address.street_line_1, address.city = parsed["street"], parsed["city"]
            address.state_province, address.postal_code = parsed["state"], parsed["postal_code"]
        address.raw_address = raw
    else:
        address.raw_address = _compose_address(
            address.street_line_1, address.city, address.state_province, address.postal_code
        )

    if (address.street_line_1, address.city, address.state_province, address.postal_code) != before:
        address.latitude = address.longitude = address.geocoding_confidence = None  # moved: locate it again
    db.flush()
    if not any((address.raw_address, address.street_line_1, address.city, address.postal_code)):
        location.address_id = None  # everything was cleared
        db.delete(address)
    else:
        location.address_id = address.id


def create_location(db: Session, chain_id: str, data: dict[str, Any]) -> StoreLocation | None:
    chain = db.get(StoreChain, chain_id)
    if chain is None:
        return None
    number = _clean(data.get("store_number"), 50)
    twin = _number_conflict(db, chain_id, number, None)
    if twin is not None:
        raise StoreError(f"Store #{number} already exists for {chain.name}.", conflict_id=twin.id)

    location = StoreLocation(store_chain_id=chain_id, store_number=number, name=_clean(data.get("name"), 255) or chain.name)
    db.add(location)
    db.flush()
    _apply_address(db, location, data)
    db.commit()
    db.refresh(location)
    return location


def clean_web_url(value: Any, *, needs_query: bool = False, label: str = "address") -> str | None:
    """A web address typed by a person, tidied: "kroger.com" becomes "https://kroger.com". None if empty.

    Raises StoreError if it is not a web address, or (``needs_query``) has no ``{query}`` for the search words.
    """
    text = _clean(value, 500)
    if not text:
        return None
    if not re.match(r"^[a-z][a-z0-9+.-]*://", text, re.I):
        text = "https://" + text
    parsed = urlparse(text)
    if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname or "." not in parsed.hostname:
        raise StoreError(f"The {label} is not a web address")
    if needs_query and "{query}" not in text:
        raise StoreError(f"The {label} needs {{query}} where the product name goes, e.g. https://www.example.com/search?q={{query}}")
    return text


def set_chain_web(db: Session, chain_id: str, data: dict[str, Any]) -> StoreChain | None:
    """Set a chain's website, product search page and weekly ad page (only the fields sent change)."""
    chain = db.get(StoreChain, chain_id)
    if chain is None:
        return None
    if "website" in data:
        chain.website = clean_web_url(data["website"], label="website")
    if "search_url" in data:
        chain.search_url = clean_web_url(data["search_url"], needs_query=True, label="product search page")
    if "deals_url" in data:
        chain.deals_url = clean_web_url(data["deals_url"], label="weekly ad page")
    db.commit()
    db.refresh(chain)
    return chain


def update_location(db: Session, location_id: str, data: dict[str, Any]) -> StoreLocation | None:
    """Edit a location: store number, label, address, or move it to another chain."""
    location = db.get(StoreLocation, location_id)
    if location is None:
        return None

    chain_id = data.get("store_chain_id") or location.store_chain_id
    chain = db.get(StoreChain, chain_id)
    if chain is None:
        raise StoreError("Store not found")

    number = _clean(data["store_number"], 50) if "store_number" in data else location.store_number
    twin = _number_conflict(db, chain_id, number, location.id)
    if twin is not None:
        raise StoreError(f"Store #{number} already exists for {chain.name}. Merge the two locations instead.", conflict_id=twin.id)

    try:
        if "opening_hours" in data:
            location.opening_hours = _hours_json(data["opening_hours"])
        if "page_url" in data:
            location.page_url = clean_web_url(data["page_url"], label="store page")
        if "name" in data:
            location.name = _clean(data["name"], 255) or chain.name
        location.store_number = number
        _apply_address(db, location, data)
        if chain_id != location.store_chain_id:
            location.store_chain_id = chain_id
            db.query(PriceObservation).filter(PriceObservation.store_location_id == location.id).update(
                {"store_chain_id": chain_id}, synchronize_session=False
            )
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(location)
    return location


def _merge_location_rows(db: Session, source: StoreLocation, target: StoreLocation) -> None:
    """Re-point everything at ``source`` to ``target`` and delete ``source`` (no commit)."""
    # keep whatever the target was missing
    if not target.store_number and source.store_number:
        target.store_number = source.store_number
    if not target.address_id and source.address_id:
        target.address_id = source.address_id
        source.address_id = None

    db.query(Receipt).filter(Receipt.store_location_id == source.id).update(
        {"store_location_id": target.id}, synchronize_session=False
    )
    db.query(PriceObservation).filter(PriceObservation.store_location_id == source.id).update(
        {"store_location_id": target.id, "store_chain_id": target.store_chain_id}, synchronize_session=False
    )
    db.query(StoreTaxObservation).filter(StoreTaxObservation.store_location_id == source.id).update(
        {"store_location_id": target.id}, synchronize_session=False
    )
    db.query(Recommendation).filter(Recommendation.store_location_id == source.id).update(
        {"store_location_id": target.id}, synchronize_session=False
    )
    db.query(Recommendation).filter(Recommendation.comparison_store_location_id == source.id).update(
        {"comparison_store_location_id": target.id}, synchronize_session=False
    )
    _delete_location_rows(db, source)


def _delete_location_rows(db: Session, location: StoreLocation) -> None:
    address_id = location.address_id
    db.delete(location)
    db.flush()
    if address_id:
        still_used = db.query(func.count(StoreLocation.id)).filter(StoreLocation.address_id == address_id).scalar()
        if not still_used:
            address = db.get(Address, address_id)
            if address is not None:
                db.delete(address)
                db.flush()


def merge_locations(db: Session, source_id: str, target_id: str) -> None:
    if source_id == target_id:
        raise StoreError("Choose a different location to merge into")
    source, target = db.get(StoreLocation, source_id), db.get(StoreLocation, target_id)
    if source is None or target is None:
        raise StoreError("Location not found")
    try:
        _merge_location_rows(db, source, target)
        db.commit()
    except Exception:
        db.rollback()
        raise


def delete_location(db: Session, location_id: str) -> bool:
    location = db.get(StoreLocation, location_id)
    if location is None:
        return False
    if _receipt_count(db, location_id):
        raise StoreError("This location has receipts. Merge it into another location instead of deleting it.")
    _delete_location_rows(db, location)
    db.commit()
    return True
