"""The trip planner's data and orchestration.

Ties the pure planner (``planner.py``) to the database and the map services (``geo.py``): the home
address and cars, finding coordinates for the stores you shop at, and building a plan.
"""

import math
import urllib.parse
from datetime import date, datetime
from typing import Any

from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Address, CommonItem, Home, NearbyStore, Receipt, StoreChain, StoreLocation, Vehicle
from app.logging_config import get_logger
from app.services import analytics, discounts, geo, planner, reqcache, webinfo
from app.services.analysis_data import load_observations

logger = get_logger("trips")


class TripError(ValueError):
    """Something a person can fix (a missing address, an invalid car). The message is safe to show."""


# --------------------------------------------------------------------------- #
# Units and labels
# --------------------------------------------------------------------------- #

def unit_labels(system: str | None = None) -> dict[str, str]:
    system = system or get_settings().unit_system
    imperial = system == "imperial"
    return {
        "unit_system": system,
        "distance_unit": "mi" if imperial else "km",
        "volume_unit": "gallon" if imperial else "litre",
        "fuel_economy_unit": "mpg" if imperial else "L/100 km",
        "ev_consumption_unit": "kWh/100 mi" if imperial else "kWh/100 km",
        "currency": get_settings().default_currency,
    }


# --------------------------------------------------------------------------- #
# Cars
# --------------------------------------------------------------------------- #

def _vehicle_json(v: Vehicle, system: str) -> dict[str, Any]:
    per_km = planner.cost_per_km(v.consumption, v.energy_price, v.other_cost_per_km)
    return {
        "id": v.id, "name": v.name, "kind": v.kind, "is_default": bool(v.is_default),
        "economy": round(planner.economy_from_metric(v.kind, v.consumption, system), 2),
        "energy_price": round(planner.price_from_metric(v.kind, v.energy_price, system), 3),
        "other_cost_per_distance": round(v.other_cost_per_km * planner.distance_to_km(1.0, system), 4),
        "range": round(planner.distance_from_km(v.range_km, system)) if v.range_km else None,
        "cost_per_distance": round(per_km * planner.distance_to_km(1.0, system), 4),
    }


def _number(data: dict[str, Any], key: str, label: str, minimum: float | None = None, required: bool = True) -> float | None:
    raw = data.get(key)
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        if required:
            raise TripError(f"Enter {label}")
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise TripError(f"{label.capitalize()} must be a number")
    if minimum is not None and value < minimum:
        raise TripError(f"{label.capitalize()} must be at least {minimum:g}")
    if value > 100000:
        raise TripError(f"{label.capitalize()} looks too large")
    return value


def _apply_vehicle(v: Vehicle, data: dict[str, Any], system: str) -> None:
    name = " ".join(str(data.get("name") or "").split())[:255]
    if not name:
        raise TripError("Give the car a name")
    kind = str(data.get("kind") or "GAS").upper()
    if kind not in planner.KINDS:
        raise TripError("Choose gas, diesel, hybrid or electric")

    if kind == "EV":
        economy = _number(data, "economy", "the electricity used per distance", minimum=0.1)
        price = _number(data, "energy_price", "the electricity price per kWh", minimum=0)
    else:
        economy = _number(data, "economy", "the fuel economy", minimum=0.1)
        price = _number(data, "energy_price", "the fuel price", minimum=0)
    other = _number(data, "other_cost_per_distance", "the other cost per distance", minimum=0, required=False) or 0.0
    rng = _number(data, "range", "the range", minimum=1, required=False)

    v.name, v.kind = name, kind
    v.consumption = planner.economy_to_metric(kind, economy, system)
    v.energy_price = planner.price_to_metric(kind, price, system)
    v.other_cost_per_km = other / planner.distance_to_km(1.0, system)
    v.range_km = planner.distance_to_km(rng, system) if rng else None


def list_vehicles(db: Session, home_id: str) -> list[dict[str, Any]]:
    system = get_settings().unit_system
    rows = db.query(Vehicle).filter(Vehicle.home_id == home_id).order_by(Vehicle.created_at).all()
    return [_vehicle_json(v, system) for v in rows]


def _make_default(db: Session, vehicle: Vehicle) -> None:
    db.query(Vehicle).filter(Vehicle.home_id == vehicle.home_id, Vehicle.id != vehicle.id).update({"is_default": 0})
    vehicle.is_default = 1


def create_vehicle(db: Session, home_id: str, data: dict[str, Any]) -> dict[str, Any]:
    system = get_settings().unit_system
    vehicle = Vehicle(home_id=home_id)
    _apply_vehicle(vehicle, data, system)
    first = db.query(Vehicle).filter(Vehicle.home_id == home_id).count() == 0
    db.add(vehicle)
    db.flush()
    if first or data.get("is_default"):
        _make_default(db, vehicle)
    db.commit()
    db.refresh(vehicle)
    return _vehicle_json(vehicle, system)


def update_vehicle(db: Session, vehicle_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
    vehicle = db.get(Vehicle, vehicle_id)
    if vehicle is None:
        return None
    system = get_settings().unit_system
    _apply_vehicle(vehicle, data, system)
    if data.get("is_default"):
        _make_default(db, vehicle)
    db.commit()
    db.refresh(vehicle)
    return _vehicle_json(vehicle, system)


def delete_vehicle(db: Session, vehicle_id: str) -> bool:
    vehicle = db.get(Vehicle, vehicle_id)
    if vehicle is None:
        return False
    home_id, was_default = vehicle.home_id, vehicle.is_default
    db.delete(vehicle)
    db.flush()
    if was_default:
        nxt = db.query(Vehicle).filter(Vehicle.home_id == home_id).order_by(Vehicle.created_at).first()
        if nxt:
            nxt.is_default = 1
    db.commit()
    return True


# --------------------------------------------------------------------------- #
# Home address
# --------------------------------------------------------------------------- #

def _home_json(home: Home) -> dict[str, Any]:
    return {"address": home.address, "latitude": home.latitude, "longitude": home.longitude,
            "located": home.latitude is not None and home.longitude is not None}


def set_home_address(db: Session, home_id: str, address: str | None) -> dict[str, Any]:
    """Save the home address and find it on the map. The address is kept even if it can't be found yet."""
    home = db.get(Home, home_id)
    if home is None:
        raise TripError("Home not found")
    text = " ".join((address or "").split())[:500]
    home.address, home.latitude, home.longitude = (text or None), None, None

    message = None
    if text:
        try:
            found = geo.geocode(text)
            if found:
                home.latitude, home.longitude = found["lat"], found["lng"]
            else:
                message = "That address could not be found on the map. Check the spelling, or add the city and postal code."
        except geo.GeoError as e:
            message = f"{e}. The address is saved; try locating it again in a moment."
    db.commit()
    return {**_home_json(home), "message": message}


def setup(db: Session, home_id: str) -> dict[str, Any]:
    home = db.get(Home, home_id)
    if home is None:
        raise TripError("Home not found")
    return {"home": _home_json(home), "vehicles": list_vehicles(db, home_id), "config": unit_labels()}


# --------------------------------------------------------------------------- #
# Items you can plan with
# --------------------------------------------------------------------------- #

def available_items(db: Session, home_id: str, q: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
    """Items with a price on record, most bought first, with the unit and usual amount the planner uses."""
    observations = load_observations(db, home_id)
    usual = planner.usual_amounts(observations)

    latest: dict[str, dict[str, Any]] = {}
    chains: dict[str, set] = {}
    store_names: dict[str, set] = {}
    category: dict[str, str] = {}
    for r in sorted(observations, key=lambda r: r["date"]):
        latest[r["item_id"]] = r
        chains.setdefault(r["item_id"], set()).add(r["chain_id"])
        store_names.setdefault(r["item_id"], set()).add(r.get("chain_name") or "")
        if r.get("category"):
            category[r["item_id"]] = r["category"]

    needle = (q or "").strip().lower()
    out = []
    for item_id, info in usual.items():
        if not info["unit"]:
            continue  # nothing comparable to plan with
        if needle and needle not in info["name"].lower():
            continue
        last = latest[item_id]
        out.append({
            "id": item_id, "name": info["name"], "unit": info["unit"], "amount": info["amount"],
            "purchases": info["purchases"], "stores": len(chains[item_id]), "last_date": last["date"],
            "category": category.get(item_id), "store_names": sorted(s for s in store_names[item_id] if s),
            "chain_ids": sorted(chains[item_id]),
        })
    out.sort(key=lambda x: (-x["purchases"], x["name"].lower()))
    return out[: max(1, min(limit, 2000))]


# --------------------------------------------------------------------------- #
# Stores on the map
# --------------------------------------------------------------------------- #

def _address_text(address: Address | None) -> str | None:
    if address is None:
        return None
    if address.raw_address and address.raw_address.strip():
        return address.raw_address.strip()
    tail = " ".join(x for x in (address.state_province, address.postal_code) if x)
    text = ", ".join(x for x in (address.street_line_1, address.city, tail) if x)
    return text or None


def home_locations(db: Session, home_id: str) -> list[dict[str, Any]]:
    """The home's store locations (see ``_home_locations``), read once per request or job."""
    return reqcache.remember(("home_locations", home_id), lambda: _home_locations(db, home_id))


def _home_locations(db: Session, home_id: str) -> list[dict[str, Any]]:
    """Every store location this home has bought from, plus the ones it added from the nearby-store
    search (``nearby``: True, with ``nearby_id``), with where each stands on the map."""
    rows = (
        db.query(StoreLocation, StoreChain, Address)
        .join(Receipt, Receipt.store_location_id == StoreLocation.id)
        .join(StoreChain, StoreLocation.store_chain_id == StoreChain.id)
        .outerjoin(Address, StoreLocation.address_id == Address.id)
        .filter(Receipt.home_id == home_id)
        .distinct()
        .all()
    )
    bought = {loc.id for loc, _, _ in rows}
    added = {
        loc_id: nearby_id for loc_id, nearby_id in db.query(NearbyStore.store_location_id, NearbyStore.id)
        .filter(NearbyStore.home_id == home_id, NearbyStore.status == "ADDED", NearbyStore.store_location_id.isnot(None)).all()
    }
    # Locations added by hand on the Stores page (no receipt from anyone yet), of a store you shop at or one you made
    # yourself: they have their address and hours, and the planner can price them from the chain's other branches.
    home_chains = {chain.id for _, chain, _ in rows}
    with_receipts = select(Receipt.store_location_id).where(Receipt.store_location_id.isnot(None))
    chains_with_receipts = select(StoreLocation.store_chain_id).where(StoreLocation.id.in_(with_receipts))
    by_hand = (
        db.query(StoreLocation, StoreChain, Address)
        .join(StoreChain, StoreLocation.store_chain_id == StoreChain.id)
        .outerjoin(Address, StoreLocation.address_id == Address.id)
        .filter(~StoreLocation.id.in_(with_receipts))
        .filter(StoreLocation.store_chain_id.in_(home_chains) | ~StoreLocation.store_chain_id.in_(chains_with_receipts))
        .all()
    )
    manual = {loc.id for loc, _, _ in by_hand if loc.id not in added}
    rows = list(rows) + [r for r in by_hand if r[0].id in manual]
    bought = bought | manual  # counted like a store you shop at (not a nearby-search addition)
    extra_ids = [i for i in added if i not in bought]
    if extra_ids:
        rows = list(rows) + (
            db.query(StoreLocation, StoreChain, Address)
            .join(StoreChain, StoreLocation.store_chain_id == StoreChain.id)
            .outerjoin(Address, StoreLocation.address_id == Address.id)
            .filter(StoreLocation.id.in_(extra_ids))
            .all()
        )
    out = []
    for loc, chain, addr in rows:
        text = _address_text(addr)
        if text is None:
            state = "no_address"
        elif addr.latitude is not None and addr.longitude is not None:
            state = "ok"
        elif addr.geocoding_confidence == 0:
            state = "failed"
        else:
            state = "pending"
        out.append({
            "id": loc.id, "chain_id": chain.id, "chain_name": chain.name,
            "label": analytics.location_label(chain.name, loc.store_number, addr.city if addr else None),
            "address": text, "lat": addr.latitude if addr else None, "lng": addr.longitude if addr else None,
            # the address as plain values (safe to keep and share); its record is loaded by id to change it
            "state": state, "_address_row": _address_values(addr), "address_id": addr.id if addr is not None else None,
            "hours": planner.parse_hours(loc.opening_hours),
            "nearby": loc.id not in bought, "nearby_id": added.get(loc.id), "added_by_hand": loc.id in manual,
            "page_url": loc.page_url, "website": chain.website, "search_url": chain.search_url, "deals_url": chain.deals_url,
        })
    return out


def _relevant_locations(db: Session, home_id: str, item_ids: list[str] | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(candidate locations, observations): stores whose chain has a price for one of the items, and the
    stores added from the nearby search (they can only be priced from online prices)."""
    observations = load_observations(db, home_id, item_ids=item_ids)
    chain_ids = {r["chain_id"] for r in observations}
    locations = [l for l in home_locations(db, home_id) if l["chain_id"] in chain_ids or l["nearby"]]
    return locations, observations


def _address_values(addr: Address | None) -> SimpleNamespace | None:
    """An address's columns as plain values, detached from the database session."""
    if addr is None:
        return None
    return SimpleNamespace(**{c.name: getattr(addr, c.name) for c in Address.__table__.columns})


def geocode_pending(db: Session, home_id: str, item_ids: list[str] | None, limit: int = 6) -> dict[str, Any]:
    """Find coordinates for up to ``limit`` stores that have none yet. Call again until ``pending`` is 0."""
    locations, _ = _relevant_locations(db, home_id, item_ids)
    todo = [l for l in locations if l["state"] == "pending"]
    done, failed, unreachable = 0, [], False

    for loc in todo[:limit]:
        addr = db.get(Address, loc["address_id"]) if loc.get("address_id") else None
        if addr is None:
            continue
        try:
            found = geo.geocode(loc["address"])
        except geo.GeoError:
            unreachable = True
            break
        if found:
            addr.latitude, addr.longitude, addr.geocoding_confidence = found["lat"], found["lng"], found["confidence"]
            done += 1
        else:
            addr.geocoding_confidence = 0.0
            failed.append(loc["label"])
        db.commit()

    remaining = 0 if unreachable else max(0, len(todo) - limit)
    return {"located": done, "not_found": failed, "pending": remaining, "unreachable": unreachable}


# --------------------------------------------------------------------------- #
# Planning
# --------------------------------------------------------------------------- #

def _directions_url(a: tuple[float, float], b: tuple[float, float]) -> str:
    route = f"{a[0]:.6f},{a[1]:.6f};{b[0]:.6f},{b[1]:.6f}"
    return "https://www.openstreetmap.org/directions?" + urllib.parse.urlencode({"engine": "fossgis_osrm_car", "route": route})


def _clean_items(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not raw:
        raise TripError("Choose at least one item")
    if len(raw) > 60:
        raise TripError("Choose up to 60 items")
    out, seen = [], set()
    for entry in raw:
        item_id = str((entry or {}).get("item_id") or "")
        if not item_id or item_id in seen:
            continue
        seen.add(item_id)
        try:
            qty = float((entry or {}).get("qty", 1))
        except (TypeError, ValueError):
            raise TripError("A quantity is not a number")
        if not 0 < qty <= 999:
            raise TripError("A quantity must be between 0 and 999")
        out.append({"item_id": item_id, "qty": qty})
    if not out:
        raise TripError("Choose at least one item")
    return out


def _bounded_number(value: Any, default: float, low: float, high: float, label: str) -> float:
    """A number from a request: empty means ``default``; anything else must be a finite number
    and is limited to [low, high]."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise TripError(f"{label} must be a number")
    if not math.isfinite(number):
        raise TripError(f"{label} must be a number")
    return max(low, min(number, high))


USED_STATUSES = {"used", "used (online price, instead of your receipt)", "used (no receipt price at this store)"}


def _price_table(items: list[dict[str, Any]], offers: dict[str, dict[str, dict[str, Any]]], stores: list[dict[str, Any]],
                 best: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Every item on the list at every store that has a price for it: what you paid there (receipt), the store's
    current online price, which is cheaper, and where the plan buys it. Plus a summary of what online prices did."""
    labels = {s["id"]: s for s in stores}
    bought_at = {l["item_id"]: stop["store"]["id"] for stop in best["stops"] for l in stop["items"]}
    table = []
    for item in items:
        rows = []
        for store_id, offer in (offers.get(item["item_id"]) or {}).items():
            if store_id not in labels or not offer:
                continue
            src = planner.price_sources(offer)
            rows.append({
                "store_id": store_id, "chain_id": labels[store_id].get("chain_id"), "store": labels[store_id]["label"], "unit": offer.get("unit"), "price": round(offer["price"], 4),
                "price_source": src["price_source"], "receipt_price": src["receipt_price"], "receipt_date": src["receipt_date"],
                "receipt_from_other_store": src["receipt_from_other_store"], "online": src["online"], "online_rejected": src["online_rejected"],
                "discount": src["discount"], "before_discount": src["before_discount"],
                "deal_until": offer.get("valid_to") if offer.get("web_deal") else None,
                "chosen": bought_at.get(item["item_id"]) == store_id,
            })
        rows.sort(key=lambda r: r["price"])
        table.append({"item_id": item["item_id"], "name": item["name"], "qty": item["qty"], "amount": item["amount"],
                      "unit": rows[0]["unit"] if rows else None, "stores": rows,
                      "online_found": sum(1 for r in rows if r["online"]), "cheapest": rows[0]["store"] if rows else None})
    lines = [l for stop in best["stops"] for l in stop["items"]]
    online_lines = [l for l in lines if l.get("price_source") in ("online", "deal")]
    # how the online prices compare with what you paid, for the items bought at an online price (+ = online costs more)
    # both before store discounts, which apply to either price alike
    diff = sum(((l.get("before_discount") or l["unit_price"]) - l["receipt_price"]) * l["amount"] * l["qty"]
               for l in online_lines if l.get("receipt_price"))
    summary = {
        "items": len(lines), "priced_online": len(online_lines),
        "priced_receipt": sum(1 for l in lines if l.get("price_source") == "receipt"),
        "online_rejected": sum(1 for l in lines if l.get("online_rejected")),
        "compared_with_receipt": sum(1 for l in online_lines if l.get("receipt_price")),
        "discounted": sum(1 for l in lines if l.get("discount")),
        "discount_saved": round(sum((l["discount"]["saved"]) * l["amount"] * l["qty"] for l in lines if l.get("discount")), 2),
        "online_vs_receipts": round(diff, 2),
    }
    return table, summary


def make_plan(db: Session, home_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    system = settings.unit_system
    today = date.today()

    home = db.get(Home, home_id)
    if home is None:
        raise TripError("Home not found")
    if home.latitude is None or home.longitude is None:
        raise TripError("Add your home address first, so the trip has a start and end")

    chosen = _clean_items(payload.get("items"))
    # The number of stores is worked out by the planner; this is only an optional preference.
    preferred_raw = _bounded_number(payload.get("max_stops"), 0, 0, planner.MAX_PREFERRED_STOPS, "The number of stores")
    preferred = int(preferred_raw) or None
    round_trip = bool(payload.get("round_trip", True))
    include_sales = bool(payload.get("include_sales", True))
    warnings: list[dict[str, str]] = []

    per_hour = _bounded_number(payload.get("time_cost_per_hour"), 0.0, 0.0, 10000.0, "The value of your time")
    stop_minutes = _bounded_number(payload.get("stop_minutes"), 10.0, 0.0, 120.0, "The minutes per store")
    depart_text = payload.get("depart_at")
    try:
        depart = datetime.fromisoformat(str(depart_text)).replace(tzinfo=None) if depart_text else datetime.now()
    except ValueError:
        raise TripError("The departure time is not valid")

    # --- driving cost
    vehicle = None
    if payload.get("vehicle_id"):
        vehicle = db.get(Vehicle, str(payload["vehicle_id"]))
        if vehicle is None or vehicle.home_id != home_id:
            raise TripError("That car was not found")
        per_km = planner.cost_per_km(vehicle.consumption, vehicle.energy_price, vehicle.other_cost_per_km)
    elif payload.get("cost_per_distance") not in (None, ""):
        try:
            per_dist = float(payload["cost_per_distance"])
        except (TypeError, ValueError):
            raise TripError("The cost per distance must be a number")
        if per_dist < 0:
            raise TripError("The cost per distance can't be negative")
        per_km = per_dist / planner.distance_to_km(1.0, system)
    else:
        per_km = 0.0
        warnings.append({"code": "no_car", "message": "No car was chosen, so driving is treated as free. Add a car to include fuel or electricity."})

    # --- prices and stores
    item_ids = [c["item_id"] for c in chosen]
    locations, observations = _relevant_locations(db, home_id, item_ids)
    usual = planner.usual_amounts(observations)

    located = [l for l in locations if l["state"] == "ok"]
    for l in locations:
        if l["state"] != "ok":
            reason = {"pending": "not located on the map yet", "failed": "its address could not be found",
                      "no_address": "it has no address"}[l["state"]]
            warnings.append({"code": "store_not_located", "message": f"{l['label']} is left out: {reason}. You can fix its address on the Stores page."})

    items = []
    for c in chosen:
        info = usual.get(c["item_id"])
        name = info["name"] if info else None
        if name is None:  # no price on record: still say which item it was, if it is one of this home's
            row = db.get(CommonItem, c["item_id"])
            name = row.name if row is not None and row.home_id == home_id else "Unknown item"
        items.append({"item_id": c["item_id"], "name": name, "qty": c["qty"], "amount": info["amount"] if info else 1.0})

    stores = [{"id": l["id"], "label": l["label"], "chain_id": l["chain_id"], "chain_name": l["chain_name"],
               "address": l["address"], "lat": l["lat"], "lng": l["lng"]} for l in located]
    offers = planner.build_offers(observations, {s["id"]: s["chain_id"] for s in stores}, today, include_sales)
    include_web_deals = bool(payload.get("include_web_deals", True))
    web_deals_used = 0
    web_report: list[dict[str, Any]] = []
    if include_web_deals:
        try:
            web_deals_used = planner.apply_deals(offers, webinfo.deal_offers(db, home_id, item_ids), stores,
                                                 {i: u["unit"] for i, u in usual.items()}, today, web_report)
        except Exception:  # noqa: BLE001 - deals are a bonus: never let them break planning
            logger.exception("Could not apply web deals")
    # store cards and memberships: what each store takes off on top (after the online-price checks, which compare
    # prices before discounts)
    store_discounts = discounts.list_for_home(db, home_id)
    if store_discounts:
        discounts.apply(offers, stores, store_discounts, discounts.fuel_item_ids(observations))
    # A store added from the nearby search has no receipts: with no online price for any item on the
    # list it has nothing to offer, so it is left out rather than taking up a place on the route.
    nearby_ids = {l["id"] for l in located if l["nearby"]}
    if nearby_ids:
        priced = {sid for per_store in offers.values() for sid in per_store}
        unpriced = [s for s in stores if s["id"] in nearby_ids and s["id"] not in priced]
        stores = [s for s in stores if s not in unpriced]
        if unpriced and include_web_deals:
            names = ", ".join(sorted({s["label"] for s in unpriced}))
            warnings.append({"code": "nearby_unpriced", "message": f"{names}: no online price found for these items yet, so "
                             f"{'it is' if len(unpriced) == 1 else 'they are'} left out. Tick \"Check current prices online\" to look them up."})
    stores = planner.limit_stores(items, offers, stores, (home.latitude, home.longitude))  # routing services take a limited number of points
    kept = {st["id"] for st in stores}
    hours = {l["id"]: l["hours"] for l in located if l.get("hours") and l["id"] in kept}

    # --- distances
    points = [(home.latitude, home.longitude)] + [(s["lat"], s["lng"]) for s in stores]
    estimated = False
    try:
        dist, dur = geo.route_matrix(points) if stores else ([[0.0]], [[0.0]])
    except geo.GeoError as e:
        dist, dur = planner.estimate_matrix(points)
        estimated = True
        warnings.append({"code": "distances_estimated", "message": f"{e}. Distances are straight-line estimates (+30%), so treat the driving cost as approximate."})

    result = planner.plan_trip(items, offers, stores, dist, dur, per_km, preferred, round_trip,
                               per_hour / 60.0, stop_minutes, hours, depart if hours else None)

    for store in result.get("closed_stores") or []:
        opens = planner.next_open(hours.get(store["id"]), depart)
        when = f" It opens {opens.strftime('%a %H:%M')}." if opens else ""
        warnings.append({"code": "store_closed", "message": f"{store['label']} is left out: it would be closed when you get there.{when}"})

    for missing in result["unavailable"]:
        reason = "no price on record at a store with a known location" if located else "no store with a known location"
        warnings.append({"code": "item_unavailable", "message": f"{missing['name']} is left out: {reason}."})

    best = result["best"]
    out: dict[str, Any] = {
        **unit_labels(system),
        "distances_estimated": estimated, "round_trip": round_trip, "max_stops": preferred,
        "stops_needed": result.get("stops_needed"), "stops_message": None, "hours_ignored": bool(result.get("hours_ignored")),
        "cost_per_distance": round(per_km * planner.distance_to_km(1.0, system), 4),
        "vehicle": _vehicle_json(vehicle, system) if vehicle else None,
        "include_sales": include_sales, "include_web_deals": include_web_deals, "web_deals_used": web_deals_used, "time_cost_per_hour": per_hour, "stop_minutes": stop_minutes,
        "depart_at": depart.isoformat(timespec="minutes"),
        "considered_stores": result["considered_stores"], "warnings": warnings, "unavailable": result["unavailable"],
        "best": None, "alternatives": [], "single_store": None, "ignoring_driving": None,
        "savings_vs_single_store": None, "map": None, "online_prices": [],
    }
    if best is None:
        return out

    def convert(plan: dict[str, Any]) -> dict[str, Any]:
        plan["distance"] = round(planner.distance_from_km(plan["distance_km"], system), 1)
        plan["return_distance"] = round(planner.distance_from_km(plan["return_km"], system), 1)
        for stop in plan["stops"]:
            stop["leg_distance"] = round(planner.distance_from_km(stop["leg_km"], system), 1)
        return plan

    out["best"] = convert(best)
    out["price_table"], out["online_summary"] = _price_table(items, offers, stores, best)
    # online prices you chose not to use, for these items (they use their receipt price; the page offers to undo)
    out["online_ignored"] = [[i, c] for i, c in webinfo.ignored_pairs(db, home_id) if i in set(item_ids)]

    # The online prices this plan was worked out with: every price and deal found for these items, whether
    # the planner used it (and whether the chosen route buys at that price), or why it did not.
    in_route = {(l["item_id"], stop["store"].get("chain_id")) for stop in best["stops"] for l in stop["items"] if l.get("web_price") or l.get("web_deal")}
    out["online_prices"] = sorted(({
        "item_id": r["item_id"], "item_name": r.get("item_name"), "chain": r.get("chain"), "product": r.get("product"),
        "kind": r.get("kind"), "price": r.get("listed_price"), "unit": r.get("listed_unit"),
        "compare_price": r.get("price"), "compare_unit": r.get("unit"),
        "fetched_at": r.get("fetched_at"), "method": r.get("method") or "model", "source": r.get("source"), "source_url": r.get("source_url"),
        "status": r["status"], "in_route": r["status"] in USED_STATUSES and (r["item_id"], r["chain_id"]) in in_route,
    } for r in web_report), key=lambda x: (not x["in_route"], x["status"] != "used", x["fetched_at"] or "", x["item_name"] or ""), reverse=False)
    if result.get("exceeds_preference"):
        needed = best["stop_count"]
        out["stops_message"] = (f"Buying everything on your list needs {needed} stores, so this trip has {needed} "
                                f"(you asked for at most {preferred}).")
    for stop in best["stops"]:
        if stop.get("closed_on_arrival"):
            arrive = datetime.fromisoformat(stop["arrive_at"])
            opens = planner.next_open(hours.get(stop["store"]["id"]), arrive)
            when = f" It opens {opens.strftime('%a %H:%M')}." if opens else ""
            warnings.append({"code": "store_closed_on_arrival", "message":
                             f"{stop['store']['label']} will probably be closed when you get there ({arrive.strftime('%a %H:%M')}).{when} "
                             "No order of these stores fits their opening hours, so this is the cheapest trip ignoring them. Leaving at a different time may help."})
    out["alternatives"] = [convert(p) for k, p in sorted(result["by_stops"].items()) if p is not best and k != best["stop_count"]]
    if result["single_store"] is not None:
        out["single_store"] = convert(result["single_store"])
        if best["stop_count"] > 1:
            out["savings_vs_single_store"] = round(result["single_store"]["total"] - best["total"], 2)
    out["ignoring_driving"] = convert(result["ignoring_driving"])

    # stale or estimated prices in the recommended plan
    lines = [l for s in best["stops"] for l in s["items"]]
    if any(l["estimated"] for l in lines):
        warnings.append({"code": "prices_estimated", "message": "Some prices are taken from another store of the same chain, because that store has not been priced for the item."})
    if any(l.get("web_deal") or l.get("web_price") for l in lines):
        warnings.append({"code": "prices_web_deals", "message": "Some prices were found on the web (deals or current shelf prices). Check the store's own site or ad before you go: online prices can differ by store, change, and the search can misread them."})
    if any(l["on_sale"] and not l.get("web_deal") for l in lines):
        if include_sales:
            warnings.append({"code": "prices_on_sale", "message": "Some prices are sale, coupon or member prices seen on your receipts. They may have ended; turn off sale prices to plan with regular prices."})
    if any(l["stale"] for l in lines):
        warnings.append({"code": "prices_stale", "message": f"Some prices were last seen more than {planner.STALE_DAYS} days ago and may have changed."})
    if vehicle and vehicle.range_km and best["distance_km"] > vehicle.range_km * 0.8:
        warnings.append({"code": "range", "message": "This trip is more than 80% of the car's range. Check you have enough fuel or charge."})

    # the route to draw: the road line from the routing service if it is reachable
    stops = [{"lat": s["store"]["lat"], "lng": s["store"]["lng"], "label": s["store"]["label"]} for s in best["stops"]]
    route_points = [(home.latitude, home.longitude)] + [(s["lat"], s["lng"]) for s in stops] + ([(home.latitude, home.longitude)] if round_trip else [])
    line = None
    if not estimated:
        try:
            line = geo.route_geometry(route_points)["line"]
        except geo.GeoError:
            pass
    prev = (home.latitude, home.longitude)
    for stop, coords in zip(best["stops"], stops):
        here = (coords["lat"], coords["lng"])
        stop["directions_url"] = _directions_url(prev, here)
        prev = here
    out["map"] = {"home": {"lat": home.latitude, "lng": home.longitude, "label": "Home"}, "stops": stops, "line": line}
    return out
