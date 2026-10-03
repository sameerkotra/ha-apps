"""Grocery stores near a home, including ones it has never bought from (OpenStreetMap, see ``osm.py``).

A search keeps what it finds per home in ``nearby_stores``:

* a store that is one the home already buys from (same chain, within a few hundred metres or at the
  same street address) is marked **KNOWN**; its OpenStreetMap opening hours are offered as a
  suggestion for that store, the same way hours found on the web are
* any other store is **NEW** until the person **adds** it (it becomes one of the home's stores, with
  its location and opening hours, and from then on online price checks, deals and the trip planner
  include it) or **hides** it

Adding a store never makes up prices: until a price is found online (or a receipt is saved) the
trip planner has nothing to buy there, and leaves it out.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Address, Home, NearbyStore, Receipt, StoreChain, StoreLocation
from app.logging_config import get_logger
from app.services import normalize, osm, planner, trips
from app.services.receipt_records import find_chain_by_key, get_or_create_chain

logger = get_logger("nearby")

DEFAULT_RADIUS_KM = 8.0
MAX_RADIUS_KM = 30.0
KNOWN_MATCH_KM = 0.25    # an OpenStreetMap shop this close to one of your stores of the same chain is that store
SAME_PLACE_KM = 0.1      # adding a store reuses an existing location of the chain this close
REPEAT_SEARCH_SECONDS = 600  # the same (or a smaller) search within this time reuses the last answer
OSM_HOURS_CONFIDENCE = 0.9

_last_search: dict[str, tuple[float, float]] = {}  # home_id -> (time, radius_km)


class NearbyError(ValueError):
    """Something a person can fix, or a search that could not be done. The message is safe to show."""


def _osm_url(osm_id: str) -> str:
    return f"https://www.openstreetmap.org/{osm_id}"


def _chain_for(name: str | None, brand: str | None, db: Session) -> StoreChain | None:
    """An existing chain this store belongs to, by its brand or its name."""
    for text in (brand, name):
        normalized = normalize.normalize_store_name(text)
        if normalized:
            chain = find_chain_by_key(db, normalized[1])
            if chain is not None:
                return chain
    return None


def _known_location(row: NearbyStore, locations: list[dict[str, Any]]) -> str | None:
    """The id of the home's own store that this OpenStreetMap shop is, if any."""
    if not row.store_chain_id:
        return None
    key = normalize.address_key({"street": row.street, "postal_code": row.postal_code}) if row.street else None
    for loc in locations:
        if loc["chain_id"] != row.store_chain_id or loc.get("nearby_id") == row.id:
            continue
        if loc["lat"] is not None and loc["lng"] is not None:
            if osm.haversine_km((row.latitude, row.longitude), (loc["lat"], loc["lng"])) <= KNOWN_MATCH_KM:
                return loc["id"]
        addr = loc.get("_address_row")
        if key and addr is not None and key == normalize.address_key({"street": addr.street_line_1, "postal_code": addr.postal_code}):
            return loc["id"]
    return None


def _suggest_hours(db: Session, row: NearbyStore, location_id: str) -> bool:
    """Offer OpenStreetMap's hours for one of the home's stores, unless it already has exactly those."""
    if not row.opening_hours:
        return False
    location = db.get(StoreLocation, location_id)
    if location is None:
        return False
    hours = json.loads(row.opening_hours)
    current = json.loads(location.opening_hours) if location.opening_hours else None
    if current == hours:
        return False
    from app.services import webinfo  # imported here: webinfo -> trips; this keeps the import order simple
    webinfo._save_hours(db, location_id, {"hours": hours, "source_url": _osm_url(row.osm_id), "confidence": OSM_HOURS_CONFIDENCE,
                                          "note": "From OpenStreetMap"}, bool(location.opening_hours))
    return True


def hours_for_location(db: Session, location_id: str) -> dict[str, Any] | None:
    """Opening hours OpenStreetMap gives for one of the home's stores, in the shape ``webinfo`` saves, or None."""
    row = (db.query(NearbyStore)
           .filter((NearbyStore.matched_location_id == location_id) | (NearbyStore.store_location_id == location_id),
                   NearbyStore.opening_hours.isnot(None))
           .order_by(NearbyStore.fetched_at.desc()).first())
    if row is None:
        return None
    return {"hours": json.loads(row.opening_hours), "source_url": _osm_url(row.osm_id), "confidence": OSM_HOURS_CONFIDENCE,
            "note": "From OpenStreetMap"}


# --------------------------------------------------------------------------- #
# Search
# --------------------------------------------------------------------------- #

def search(db: Session, home_id: str, radius_km: float | None = None, force: bool = False) -> dict[str, Any]:
    """Look for grocery stores around the home and keep them. Returns the same as ``listing``."""
    home = db.get(Home, home_id)
    if home is None:
        raise NearbyError("Home not found")
    if home.latitude is None or home.longitude is None:
        raise NearbyError("Add your home address on the Trip page first, so there is a place to search around.")
    settings = get_settings()
    if not settings.overpass_url:
        raise NearbyError("No OpenStreetMap store search server is set in Admin → App settings.")
    radius = max(1.0, min(float(radius_km or DEFAULT_RADIUS_KM), MAX_RADIUS_KM))

    last = _last_search.get(home_id)
    if not force and last and time.time() - last[0] < REPEAT_SEARCH_SECONDS and radius <= last[1]:
        return listing(db, home_id)

    try:
        found = osm.fetch_stores(settings.overpass_url, (home.latitude, home.longitude), radius, settings.map_contact_email.strip())
    except osm.OsmError as e:
        raise NearbyError(str(e)) from e
    _last_search[home_id] = (time.time(), radius)

    now = datetime.utcnow()
    rows = {r.osm_id: r for r in db.query(NearbyStore).filter(NearbyStore.home_id == home_id).all()}
    seen: set[str] = set()
    for s in found:
        row = rows.get(s["osm_id"])
        if row is None:
            row = NearbyStore(home_id=home_id, osm_id=s["osm_id"], status="NEW")
            db.add(row)
            rows[s["osm_id"]] = row
        a = s["address"]
        row.name, row.brand, row.shop = s["name"], s["brand"], s["shop"]
        row.latitude, row.longitude, row.distance_km = s["lat"], s["lng"], s["distance_km"]
        row.street, row.city, row.state, row.postal_code, row.raw_address = a["street"], a["city"], a["state"], a["postal_code"], a["raw"]
        row.opening_hours_raw = s["opening_hours_raw"]
        row.opening_hours = json.dumps(s["opening_hours"]) if s["opening_hours"] else None
        row.website, row.fetched_at = s["website"], now
        if row.status != "ADDED":
            chain = _chain_for(s["name"], s["brand"], db)
            row.store_chain_id = chain.id if chain else None
        seen.add(s["osm_id"])
    db.flush()

    locations = trips.home_locations(db, home_id)
    suggested = 0
    for osm_id, row in rows.items():
        if osm_id not in seen:
            if row.status in ("NEW", "KNOWN"):
                db.delete(row)  # no longer found (closed, or outside a smaller search): hidden and added ones are kept
            continue
        if row.status in ("ADDED", "HIDDEN"):
            continue
        match = _known_location(row, locations)
        row.status, row.matched_location_id = ("KNOWN", match) if match else ("NEW", None)
        if match and row.website:
            known = db.get(StoreLocation, match)
            if known is not None and not known.page_url:
                known.page_url = row.website  # OpenStreetMap's link to this store's own page
        if match and _suggest_hours(db, row, match):
            suggested += 1
    db.commit()
    result = listing(db, home_id)
    result["hours_suggested"] = suggested
    return result


def listing(db: Session, home_id: str) -> dict[str, Any]:
    """What the last search found for this home, nearest first, with distances in the app's unit."""
    labels = trips.unit_labels()
    system = labels["unit_system"]
    rows = db.query(NearbyStore).filter(NearbyStore.home_id == home_id).all()
    chains = {c.id: c.name for c in db.query(StoreChain).filter(StoreChain.id.in_({r.store_chain_id for r in rows if r.store_chain_id})).all()} if rows else {}
    by_id = {l["id"]: l for l in trips.home_locations(db, home_id)}
    out = []
    for r in sorted(rows, key=lambda r: (r.distance_km or 0, r.name.lower())):
        matched = by_id.get(r.matched_location_id) if r.matched_location_id else None
        out.append({
            "id": r.id, "name": r.name, "brand": r.brand, "kind": osm.SHOP_KINDS.get(r.shop or "", "Store"),
            "distance": round(planner.distance_from_km(r.distance_km or 0, system), 1), "address": r.raw_address,
            "lat": r.latitude, "lng": r.longitude, "status": r.status, "chain": chains.get(r.store_chain_id),
            "opening_hours": json.loads(r.opening_hours) if r.opening_hours else None, "opening_hours_raw": r.opening_hours_raw,
            "website": r.website, "osm_url": _osm_url(r.osm_id), "store_location_id": r.store_location_id,
            "matched_label": matched["label"] if matched else None,
        })
    searched = max((r.fetched_at for r in rows), default=None)
    return {"distance_unit": labels["distance_unit"], "searched_at": searched.isoformat() + "Z" if searched else None,
            "default_radius": round(planner.distance_from_km(DEFAULT_RADIUS_KM, system)),
            "max_radius": round(planner.distance_from_km(MAX_RADIUS_KM, system)), "stores": out}


# --------------------------------------------------------------------------- #
# Adding, hiding, removing
# --------------------------------------------------------------------------- #

def _row(db: Session, home_id: str, nearby_id: str) -> NearbyStore:
    row = db.get(NearbyStore, nearby_id)
    if row is None or row.home_id != home_id:
        raise NearbyError("That store was not found. Search again.")
    return row


def add(db: Session, home_id: str, nearby_id: str) -> dict[str, Any]:
    """Make a nearby store one of the home's stores. Returns {store_location_id, chain}."""
    row = _row(db, home_id, nearby_id)
    if row.status == "KNOWN":
        raise NearbyError("You already shop at this store.")
    if row.status == "ADDED" and row.store_location_id and db.get(StoreLocation, row.store_location_id) is not None:
        chain = db.get(StoreChain, db.get(StoreLocation, row.store_location_id).store_chain_id)
        return {"store_location_id": row.store_location_id, "chain": chain.name if chain else None}

    chain = db.get(StoreChain, row.store_chain_id) if row.store_chain_id else None
    if chain is None:
        normalized = normalize.normalize_store_name(row.brand or row.name)
        if normalized is None:
            raise NearbyError("This store has no usable name.")
        chain = get_or_create_chain(db, normalized[0], normalized[1])

    # The same shop may already be a location of the chain (added by another home, or from a receipt)
    location = None
    for loc, addr in (db.query(StoreLocation, Address).outerjoin(Address, StoreLocation.address_id == Address.id)
                      .filter(StoreLocation.store_chain_id == chain.id).all()):
        lat = loc.latitude if loc.latitude is not None else (addr.latitude if addr else None)
        lng = loc.longitude if loc.longitude is not None else (addr.longitude if addr else None)
        if lat is not None and lng is not None and osm.haversine_km((row.latitude, row.longitude), (lat, lng)) <= SAME_PLACE_KM:
            location = loc
            break
    if location is None:
        address = Address(
            raw_address=row.raw_address or f"{row.name} ({row.latitude:.5f}, {row.longitude:.5f})",
            street_line_1=row.street, city=row.city, state_province=row.state, postal_code=row.postal_code,
            latitude=row.latitude, longitude=row.longitude, geocoding_confidence=1.0,
        )
        db.add(address)
        db.flush()
        location = StoreLocation(store_chain_id=chain.id, address_id=address.id, name=row.name,
                                 latitude=row.latitude, longitude=row.longitude, opening_hours=row.opening_hours,
                                 page_url=row.website)
        db.add(location)
        db.flush()
    else:
        if row.opening_hours and not location.opening_hours:
            location.opening_hours = row.opening_hours
        if row.website and not location.page_url:
            location.page_url = row.website

    row.status, row.store_location_id, row.store_chain_id, row.matched_location_id = "ADDED", location.id, chain.id, None
    db.commit()
    return {"store_location_id": location.id, "chain": chain.name}


def remove(db: Session, home_id: str, nearby_id: str) -> dict[str, Any]:
    """Undo ``add``: the store goes back to the list of stores found. Its location is deleted if nothing uses it."""
    row = _row(db, home_id, nearby_id)
    location_id = row.store_location_id
    row.status, row.store_location_id = "NEW", None
    db.commit()
    if location_id:
        in_use = (db.query(func.count(Receipt.id)).filter(Receipt.store_location_id == location_id).scalar()
                  or db.query(func.count(NearbyStore.id)).filter(NearbyStore.store_location_id == location_id).scalar()
                  or db.query(func.count(NearbyStore.id)).filter(NearbyStore.matched_location_id == location_id).scalar())
        if not in_use:
            from app.services import stores as stores_service
            location = db.get(StoreLocation, location_id)
            chain_id = location.store_chain_id if location else None
            try:
                stores_service.delete_location(db, location_id)
            except stores_service.StoreError:
                db.rollback()
            else:
                # a chain that was only ever this one store goes too, so the Stores page stays tidy
                chain = db.get(StoreChain, chain_id) if chain_id else None
                if chain is not None and not db.query(StoreLocation).filter(StoreLocation.store_chain_id == chain_id).count():
                    try:
                        stores_service.delete_chain(db, chain_id)
                    except Exception:  # noqa: BLE001 - leaving an empty chain behind is harmless
                        db.rollback()
    return {"status": "NEW"}


def set_hidden(db: Session, home_id: str, nearby_id: str, hidden: bool) -> dict[str, Any]:
    row = _row(db, home_id, nearby_id)
    if row.status in ("ADDED", "KNOWN") and hidden:
        raise NearbyError("Remove this store first." if row.status == "ADDED" else "You already shop at this store.")
    row.status = "HIDDEN" if hidden else "NEW"
    db.commit()
    return {"status": row.status}


