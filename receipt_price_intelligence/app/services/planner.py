"""Shopping trip planning: where to buy a list of items, and in what order to drive there.

Pure functions over plain data (no database, no network), so every rule is checkable.

The planner chooses up to a few stores and an order to visit them that minimises

    what the items cost at the chosen stores  +  what the driving costs (fuel or electricity)

Item prices are the latest you actually paid at each store (from receipts). A store that has
not been priced for an item is filled in from another store of the same chain, and marked as an
estimate. Distances come from a routing service, or a straight-line estimate when it is
unreachable.
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta
from itertools import combinations, permutations
from typing import Any

from app.services import analytics

KM_PER_MILE = 1.609344
LITRES_PER_GALLON = 3.785411784
MPG_TO_L_PER_100KM = 235.214583  # l/100km = 235.214583 / mpg

FUEL_KINDS = ("GAS", "DIESEL", "HYBRID")
KINDS = FUEL_KINDS + ("EV",)

STALE_DAYS = 180          # a price older than this is flagged
ROAD_FACTOR = 1.3         # straight line -> road distance when routing is unavailable
FALLBACK_KMH = 40.0       # average speed used for that estimate
MAX_CANDIDATE_STORES = 14
K_AUTO = 5                # most stores tried exhaustively (every visiting order); beyond that a heuristic is used
MAX_PREFERRED_STOPS = 6
MAX_STORES_IN_PLAN = 40   # stores kept before distances are worked out
EPS = 1e-9


# --------------------------------------------------------------------------- #
# Units: stored metric, shown in the configured system
# --------------------------------------------------------------------------- #

def economy_to_metric(kind: str, value: float, system: str) -> float:
    """Consumption entered by a person -> litres/100 km (fuel) or kWh/100 km (electric)."""
    if system == "metric":
        return value
    if kind == "EV":                     # kWh per 100 miles
        return value / KM_PER_MILE
    return MPG_TO_L_PER_100KM / value    # miles per US gallon


def economy_from_metric(kind: str, value: float, system: str) -> float:
    if system == "metric":
        return value
    if kind == "EV":
        return value * KM_PER_MILE
    return MPG_TO_L_PER_100KM / value


def price_to_metric(kind: str, value: float, system: str) -> float:
    """Price per gallon -> per litre (electricity is per kWh either way)."""
    if kind == "EV" or system == "metric":
        return value
    return value / LITRES_PER_GALLON


def price_from_metric(kind: str, value: float, system: str) -> float:
    if kind == "EV" or system == "metric":
        return value
    return value * LITRES_PER_GALLON


def distance_from_km(km: float, system: str) -> float:
    return km / KM_PER_MILE if system == "imperial" else km


def distance_to_km(value: float, system: str) -> float:
    return value * KM_PER_MILE if system == "imperial" else value


def cost_per_km(consumption: float, energy_price: float, other_per_km: float = 0.0) -> float:
    """Running cost of one kilometre: energy used per km times its price, plus any other cost."""
    return consumption / 100.0 * energy_price + other_per_km


# --------------------------------------------------------------------------- #
# Distances
# --------------------------------------------------------------------------- #

def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    (lat1, lon1), (lat2, lon2) = a, b
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371.0088 * 2 * math.asin(math.sqrt(h))


def estimate_matrix(points: list[tuple[float, float]]) -> tuple[list[list[float]], list[list[float]]]:
    """Road distance (km) and drive time (minutes) between every pair, from straight lines."""
    n = len(points)
    km = [[0.0] * n for _ in range(n)]
    minutes = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i != j:
                d = haversine_km(points[i], points[j]) * ROAD_FACTOR
                km[i][j] = d
                minutes[i][j] = d / FALLBACK_KMH * 60
    return km, minutes


# --------------------------------------------------------------------------- #
# Prices per store
# --------------------------------------------------------------------------- #

def usual_amounts(observations: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Per item: the unit prices are compared in and how much of it one purchase usually is.

    For an item sold by weight the amount is the usual weight (about 2.4 lb) so a quantity of 1
    means "a usual purchase". For everything else it is 1 unit.
    """
    rows = analytics.prepare_price_rows(observations)
    by_item: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_item.setdefault(r["item_id"], []).append(r)

    out = {}
    for item_id, irows in by_item.items():
        priced = [r for r in irows if r["price"] is not None]
        unit = priced[0]["cunit"] if priced else None
        amount = 1.0
        if unit and unit != "each":
            samples = [r["line_total"] / r["price"] for r in priced if r.get("line_total") and r["price"] and r["price"] > 0]
            if samples:
                amount = sum(samples) / len(samples)
        out[item_id] = {"name": irows[0]["item_name"], "unit": unit, "amount": round(amount, 3), "purchases": len(irows)}
    return out


def build_offers(
    observations: list[dict[str, Any]],
    location_chain: dict[str, str | None],
    today: date | None = None,
    include_sales: bool = True,
) -> dict[str, dict[str, dict[str, Any]]]:
    """The latest price of each item at each candidate store location.

    ``location_chain`` maps every candidate location id to its chain id. A location with no
    price of its own for an item borrows the latest price at another location of its chain
    (``estimated``). A price seen with a sale, coupon or member discount is marked ``on_sale``;
    with ``include_sales`` the discounted price is what the plan uses, otherwise the regular
    price is. Returns {item_id: {location_id: offer}}.
    """
    today = today or date.today()
    rows = [r for r in analytics.prepare_price_rows(observations) if r["price"] is not None]

    latest_at: dict[tuple[str, str], dict[str, Any]] = {}      # (item, location)
    latest_chain: dict[tuple[str, str], dict[str, Any]] = {}   # (item, chain), any location of the chain
    for r in sorted(rows, key=lambda r: r["date"]):
        latest_at[(r["item_id"], r["location_id"])] = r
        if r.get("chain_id"):
            latest_chain[(r["item_id"], r["chain_id"])] = r

    def offer(row: dict[str, Any], estimated: bool) -> dict[str, Any]:
        age = (today - date.fromisoformat(row["date"])).days
        ratio = row.get("sale_ratio") or 0.0
        on_sale = ratio >= 0.02
        regular = row["price"]
        price = regular * (1 - ratio) if (on_sale and include_sales) else regular
        return {"price": price, "regular_price": regular, "on_sale": on_sale, "unit": row["cunit"],
                "date": row["date"], "estimated": estimated, "stale": age > STALE_DAYS}

    items = {r["item_id"] for r in rows}
    offers: dict[str, dict[str, dict[str, Any]]] = {i: {} for i in items}
    for item_id in items:
        for location_id, chain_id in location_chain.items():
            own = latest_at.get((item_id, location_id))
            if own:
                offers[item_id][location_id] = offer(own, False)
            elif chain_id and (item_id, chain_id) in latest_chain:
                offers[item_id][location_id] = offer(latest_chain[(item_id, chain_id)], True)
    return offers


# --------------------------------------------------------------------------- #
# Opening hours
# --------------------------------------------------------------------------- #

DAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
Schedule = dict[int, list[tuple[int, int]]]


def _minutes(text: str) -> int | None:
    try:
        h, m = text.strip().split(":")[:2]
        h, m = int(h), int(m)
    except (ValueError, AttributeError):
        return None
    return h * 60 + m if 0 <= h <= 24 and 0 <= m < 60 else None


def parse_hours(value: Any) -> Schedule | None:
    """Opening hours as stored (JSON text or dict) -> {weekday: [(open, close) in minutes]}.

    Format: {"mon": [["08:00", "22:00"]], ..., "sun": []} where an empty list means closed;
    "all" applies to every day not listed; ``true`` for a day means open 24 hours. A close time
    at or before the open time runs past midnight. None means unknown (treated as always open).
    """
    if not value:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    if not isinstance(value, dict):
        return None

    schedule: Schedule = {}
    for index, key in enumerate(DAY_KEYS):
        spec = value.get(key, value.get("all"))
        if spec is None:
            continue  # this day's hours are not known
        if spec is True:
            schedule[index] = [(0, 1440)]
            continue
        spans = []
        for span in spec or []:
            try:
                start, end = _minutes(span[0]), _minutes(span[1])
            except (TypeError, IndexError):
                continue
            if start is None or end is None:
                continue
            spans.append((start, end + 1440 if end <= start else end))
        schedule[index] = spans
    return schedule or None


def is_open(schedule: Schedule | None, when: datetime, needed: float = 0.0) -> bool:
    """Whether a store is open at ``when`` and stays open ``needed`` more minutes.

    Unknown hours (no schedule, or a day without hours) count as open.
    """
    if not schedule:
        return True
    minute = when.hour * 60 + when.minute
    today = when.weekday()
    if today not in schedule:
        return True
    for start, end in schedule.get(today, []):
        if start <= minute and minute + needed <= end:
            return True
    yesterday = (today - 1) % 7
    for start, end in schedule.get(yesterday, []):
        if end > 1440 and minute + 1440 >= start and minute + 1440 + needed <= end:
            return True  # still open from last night
    return False


def next_open(schedule: Schedule | None, when: datetime) -> datetime | None:
    """The next time the store opens after ``when`` (within a week), or None."""
    if not schedule:
        return None
    for offset in range(0, 8):
        day = (when + timedelta(days=offset)).replace(hour=0, minute=0, second=0, microsecond=0)
        for start, _end in sorted(schedule.get(day.weekday(), [])):
            candidate = day + timedelta(minutes=start)
            if candidate > when:
                return candidate
    return None


PRICE_SANITY_LOW, PRICE_SANITY_HIGH = 0.4, 2.5   # a shelf price this far from what you last paid is probably misread


def apply_deals(
    offers: dict[str, dict[str, dict[str, Any]]],
    deals: list[dict[str, Any]],
    stores: list[dict[str, Any]],
    item_units: dict[str, str | None],
    today: date | None = None,
    report: list[dict[str, Any]] | None = None,
) -> int:
    """Use what was found online, in place. Returns how many offers changed.

    ``deals``: {kind, item_id, chain_id, price (in the item's own comparison unit), unit, valid_to, source_url, date}.
    Each applies to every location of its chain.

    * ``PRICE`` (the current shelf price on the store's website) replaces the price from your last receipt,
      up or down, when it is newer, unless it is wildly different from what you paid (then it was probably
      read wrongly and is ignored). Where the store had no price, it creates an estimate.
    * ``DEAL`` (a promotion) only ever lowers a price, and is ignored once it has ended.

    Prices are applied first, so a deal is compared with the up-to-date price.

    A shelf price at a chain you have never bought the item from (a store added from the nearby search)
    has no price of its own to check against, so it is checked against the middle of what you have paid
    for the item anywhere; with no price for the item at all it cannot be checked and is not used.
    """
    today_iso = (today or date.today()).isoformat()
    changed = 0
    # What each chain last charged per item (from any of its stores), to check a listing against
    # even for a store that has no price of its own.
    reference: dict[tuple[str, str], dict[str, Any]] = {}
    for item_id, per_store in offers.items():
        for store in stores:
            offer = per_store.get(store["id"])
            if offer and not offer.get("web_price"):
                key = (item_id, store.get("chain_id"))
                if key not in reference or (offer.get("date") or "") > (reference[key].get("date") or ""):
                    reference[key] = offer
    # What you pay for each item anywhere (the median of the stores' own prices), for a chain with no price of its own.
    anywhere: dict[str, float] = {}
    for item_id, per_store in offers.items():
        paid = sorted(o["price"] for o in per_store.values() if o and not o.get("web_price") and o.get("price"))
        if paid:
            mid = len(paid) // 2
            anywhere[item_id] = paid[mid] if len(paid) % 2 else (paid[mid - 1] + paid[mid]) / 2
    # Why each online price was or was not used, one entry per price (the best outcome over its stores).
    outcome: dict[int, str] = {}
    rank = {"used": 0, "used (online price, instead of your receipt)": 0, "used (no receipt price at this store)": 0,
            "receipt price is cheaper or the same": 1, "not cheaper than what is on record": 1, "older than your last receipt": 2,
            "same as what you last paid": 3, "too different from what you paid (probably misread)": 4,
            "too different from what you pay elsewhere (probably misread)": 4,
            "nothing to check it against (you have no price for this item)": 5, "no store of this chain near you": 6}

    def note(deal: dict[str, Any], reason: str) -> None:
        key = id(deal)
        if key not in outcome or rank.get(reason, 9) < rank.get(outcome[key], 9):
            outcome[key] = reason

    for deal in sorted(deals, key=lambda d: d.get("kind") != "PRICE"):
        kind = deal.get("kind") or "DEAL"
        if kind == "DEAL" and deal.get("valid_to") and deal["valid_to"] < today_iso:
            outcome[id(deal)] = "deal has ended"
            continue
        if not deal.get("price") or deal["price"] <= 0:
            outcome[id(deal)] = "no usable price"
            continue
        note(deal, "no store of this chain near you")
        for store in stores:
            if store.get("chain_id") != deal["chain_id"]:
                continue
            per_item = offers.setdefault(deal["item_id"], {})
            existing = per_item.get(store["id"])
            if kind == "PRICE":
                # The store's current online price is used whenever there is one (it is today's price; a receipt is
                # what it cost then). What you paid is kept on the offer so the plan can show both and say which it used.
                receipt = existing if existing is not None and not existing.get("web_price") else None
                ref = receipt or reference.get((deal["item_id"], deal["chain_id"]))
                if ref is not None:
                    ratio = deal["price"] / ref["price"] if ref["price"] > 0 else 1.0
                    if not PRICE_SANITY_LOW <= ratio <= PRICE_SANITY_HIGH:
                        note(deal, "too different from what you paid (probably misread)")
                        if receipt is not None:
                            receipt["online_rejected"] = {"price": round(deal["price"], 4), "url": deal.get("source_url"), "product": deal.get("product"),
                                                          "reason": "looked misread: far from what you paid"}
                        continue  # nothing like what this chain charged: probably misread
                else:
                    usual = anywhere.get(deal["item_id"])
                    if not usual:
                        note(deal, "nothing to check it against (you have no price for this item)")
                        continue
                    if not PRICE_SANITY_LOW <= deal["price"] / usual <= PRICE_SANITY_HIGH:
                        note(deal, "too different from what you pay elsewhere (probably misread)")
                        continue
                online = {"price": deal["price"], "url": deal.get("source_url"), "product": deal.get("product"),
                          "fetched_at": deal.get("fetched_at"), "method": deal.get("method"), "note": deal.get("conditions"),
                          "listed_price": deal.get("listed_price"), "listed_unit": deal.get("listed_unit")}
                per_item[store["id"]] = {
                    "price": deal["price"], "regular_price": ref["price"] if ref else deal["price"],
                    "unit": (ref or {}).get("unit") or item_units.get(deal["item_id"]) or deal.get("unit"),
                    "date": deal.get("date") or today_iso, "estimated": existing is None, "stale": False, "on_sale": False,
                    "web_price": True, "source_url": deal.get("source_url"), "was_price": ref["price"] if ref else None,
                    "fetched_at": deal.get("fetched_at"), "method": deal.get("method"), "online": online,
                    "receipt_price": receipt["price"] if receipt else None, "receipt_date": receipt.get("date") if receipt else None,
                    "receipt_estimated": bool(receipt and receipt.get("estimated")),
                }
                note(deal, "used (online price, instead of your receipt)" if receipt else "used (no receipt price at this store)")
                changed += 1
                continue
            if existing is not None and deal["price"] >= existing["price"] - 1e-9:
                note(deal, "not cheaper than what is on record")
                continue
            per_item[store["id"]] = {
                "price": deal["price"], "regular_price": existing["price"] if existing else deal["price"],
                "unit": (existing or {}).get("unit") or deal.get("unit") or item_units.get(deal["item_id"]),
                "date": deal.get("date") or today_iso, "estimated": existing is None, "stale": False, "on_sale": True,
                "web_deal": True, "valid_to": deal.get("valid_to"), "source_url": deal.get("source_url"),
                "web_price": bool(existing and existing.get("web_price")), "was_price": (existing or {}).get("was_price"),
                "fetched_at": deal.get("fetched_at"), "method": deal.get("method"),
                # what it replaces, for showing both: the online price and/or what you paid
                "online": (existing or {}).get("online"),
                "receipt_price": (existing or {}).get("receipt_price") if (existing or {}).get("web_price") else (existing or {}).get("price"),
                "receipt_date": (existing or {}).get("receipt_date") if (existing or {}).get("web_price") else (existing or {}).get("date"),
                "deal_product": deal.get("product"),
            }
            note(deal, "used")
            changed += 1
    if report is not None:
        for deal in deals:
            report.append({**deal, "status": outcome.get(id(deal), "no usable price")})
    return changed


def price_sources(offer: dict[str, Any]) -> dict[str, Any]:
    """Where an offer's price came from and the other price it was compared with, for showing both:
    ``price_source`` (online, deal or receipt), ``receipt_price`` / ``receipt_date`` (what you paid, if known) and
    ``online`` ({price, url, product, fetched_at, method, note}, if a current price was found online)."""
    if offer.get("web_deal"):
        source = "deal"
    elif offer.get("web_price"):
        source = "online"
    else:
        source = "receipt"
    if source == "receipt":
        receipt_price, receipt_date = offer["price"], offer.get("date")
    else:
        receipt_price, receipt_date = offer.get("receipt_price"), offer.get("receipt_date")
    online = offer.get("online")
    return {"price_source": source, "online_rejected": offer.get("online_rejected"),
            "discount": offer.get("discount"), "before_discount": offer.get("before_discount"),
            "receipt_price": round(receipt_price, 4) if receipt_price else None, "receipt_date": receipt_date,
            "receipt_from_other_store": bool(offer.get("estimated") if source == "receipt" else offer.get("receipt_estimated")),
            "online": {**online, "price": round(online["price"], 4)} if online else None}


# --------------------------------------------------------------------------- #
# Optimisation
# --------------------------------------------------------------------------- #

class Trip:
    """What a trip costs to make, apart from the items."""

    def __init__(
        self,
        dist: list[list[float]],
        dur: list[list[float]],
        per_km: float,
        round_trip: bool = True,
        per_min: float = 0.0,
        stop_minutes: float = 0.0,
        hours: dict[str, Schedule | None] | None = None,
        depart: datetime | None = None,
        stores: list[dict[str, Any]] | None = None,
    ):
        self.dist, self.dur = dist, dur
        self.per_km, self.per_min, self.stop_minutes = per_km, per_min, stop_minutes
        self.round_trip = round_trip
        self.hours = hours or {}
        self.depart = depart
        self.stores = stores or []

    def nodes(self, order: tuple[int, ...]) -> list[int]:
        return [0] + [s + 1 for s in order] + ([0] if self.round_trip else [])

    def km(self, order: tuple[int, ...]) -> float:
        n = self.nodes(order)
        return sum(self.dist[a][b] for a, b in zip(n, n[1:]))

    def drive_minutes(self, order: tuple[int, ...]) -> float:
        n = self.nodes(order)
        return sum(self.dur[a][b] for a, b in zip(n, n[1:]))

    def total_minutes(self, order: tuple[int, ...]) -> float:
        return self.drive_minutes(order) + self.stop_minutes * len(order)

    def cost(self, order: tuple[int, ...]) -> float:
        return self.km(order) * self.per_km + self.total_minutes(order) * self.per_min

    def arrivals(self, order: tuple[int, ...]) -> list[float]:
        """Minutes after leaving home that each stop is reached."""
        t, here, out = 0.0, 0, []
        for s in order:
            t += self.dur[here][s + 1]
            out.append(t)
            t += self.stop_minutes
            here = s + 1
        return out

    def feasible(self, order: tuple[int, ...]) -> bool:
        """Every store with known hours is open when we get there (and while we shop)."""
        if self.depart is None or not self.hours:
            return True
        for s, minutes in zip(order, self.arrivals(order)):
            schedule = self.hours.get(self.stores[s]["id"])
            if schedule and not is_open(schedule, self.depart + timedelta(minutes=minutes), self.stop_minutes):
                return False
        return True

    def best_order(self, combo: tuple[int, ...]) -> tuple[int, ...] | None:
        best, best_cost = None, float("inf")
        for order in permutations(combo):
            if not self.feasible(order):
                continue
            c = self.cost(order)
            if c < best_cost - EPS:
                best, best_cost = order, c
        return best


def _nearest_neighbour(trip: Trip, stops: set[int]) -> tuple[int, ...]:
    """Quick route through any number of stores (used only for the 'ignoring driving' comparison)."""
    remaining, order, here = set(stops), [], 0
    while remaining:
        nxt = min(remaining, key=lambda s: trip.dist[here][s + 1])
        order.append(nxt)
        here = nxt + 1
        remaining.remove(nxt)
    return tuple(order)


def _describe(
    trip: Trip,
    order: tuple[int, ...],
    assignment: dict[int, int],
    items: list[dict[str, Any]],
    item_cost: list[list[float | None]],
    offers_by_item: list[list[dict[str, Any] | None]],
) -> dict[str, Any]:
    nodes = trip.nodes(order)
    arrivals = trip.arrivals(order)
    stops = []
    for pos, s in enumerate(order):
        lines = []
        for i, chosen in assignment.items():
            if chosen != s:
                continue
            offer = offers_by_item[i][s]
            lines.append({
                "item_id": items[i]["item_id"], "name": items[i]["name"], "qty": items[i]["qty"],
                "amount": items[i]["amount"], "unit": offer["unit"], "unit_price": round(offer["price"], 4),
                "regular_price": round(offer.get("regular_price", offer["price"]), 4),
                "on_sale": bool(offer.get("on_sale")),
                "web_deal": bool(offer.get("web_deal")), "web_price": bool(offer.get("web_price")), "was_price": offer.get("was_price"),
                "fetched_at": offer.get("fetched_at"), "method": offer.get("method"),
                "deal_until": offer.get("valid_to"), "source_url": offer.get("source_url"),
                "cost": round(item_cost[i][s], 2), "price_date": offer["date"],
                "estimated": offer["estimated"], "stale": offer["stale"],
                **price_sources(offer),
            })
        stop = {
            "store": trip.stores[s],
            "leg_km": round(trip.dist[nodes[pos]][nodes[pos + 1]], 2),
            "leg_minutes": round(trip.dur[nodes[pos]][nodes[pos + 1]], 1),
            "items": lines,
            "subtotal": round(sum(l["cost"] for l in lines), 2),
        }
        if trip.depart is not None:
            arrive = trip.depart + timedelta(minutes=arrivals[pos])
            schedule = trip.hours.get(trip.stores[s]["id"])
            stop["arrive_at"] = arrive.isoformat(timespec="minutes")
            stop["hours_known"] = bool(schedule)
            stop["closed_on_arrival"] = bool(schedule) and not is_open(schedule, arrive, trip.stop_minutes)
        stops.append(stop)

    back_km = trip.dist[nodes[-2]][0] if trip.round_trip else 0.0
    back_min = trip.dur[nodes[-2]][0] if trip.round_trip else 0.0
    km = trip.km(order)
    drive = trip.drive_minutes(order)
    shopping = trip.stop_minutes * len(order)
    items_total = sum(s["subtotal"] for s in stops)
    travel = km * trip.per_km
    time_cost = (drive + shopping) * trip.per_min
    return {
        "stop_count": len(stops), "stops": stops,
        "return_km": round(back_km, 2), "return_minutes": round(back_min, 1),
        "distance_km": round(km, 2), "drive_minutes": round(drive, 1), "shopping_minutes": round(shopping, 1),
        "duration_minutes": round(drive + shopping, 1),
        "items_total": round(items_total, 2), "travel_cost": round(travel, 2), "time_cost": round(time_cost, 2),
        "total": round(items_total + travel + time_cost, 2),
    }


def limit_stores(
    items: list[dict[str, Any]],
    offers: dict[str, dict[str, dict[str, Any]]],
    stores: list[dict[str, Any]],
    home: tuple[float, float],
    cap: int = MAX_STORES_IN_PLAN,
) -> list[dict[str, Any]]:
    """Keep at most ``cap`` stores before distances are worked out (routing services take a limited
    number of points). Stores that sell an item cheaply are kept first, then the nearest and the
    ones that sell the most of the list, so a trip that needs every item stays possible."""
    if len(stores) <= cap:
        return stores
    ids = [st["id"] for st in stores]
    keep: dict[str, None] = {}
    for it in items:
        priced = sorted((sid for sid in ids if offers.get(it["item_id"], {}).get(sid)),
                        key=lambda sid: offers[it["item_id"]][sid]["price"])
        for sid in priced[:2]:
            keep[sid] = None
    by_distance = sorted(stores, key=lambda st: haversine_km(home, (st["lat"], st["lng"])))
    coverage = sorted(stores, key=lambda st: -sum(1 for it in items if offers.get(it["item_id"], {}).get(st["id"])))
    for st in by_distance[:10] + coverage[:10]:
        keep[st["id"]] = None
    essential = list(keep)
    for st in by_distance:  # use up the rest of the room on the nearest stores
        if len(keep) >= cap:
            break
        keep[st["id"]] = None
    chosen = essential[:cap] + [sid for sid in keep if sid not in essential[:cap]][:max(0, cap - len(essential))]
    return [st for st in stores if st["id"] in set(chosen)]


def _heuristic_order(trip: Trip, stops: set[int]) -> tuple[int, ...] | None:
    """A good visiting order for any number of stores: exact for a handful, otherwise nearest
    neighbour improved by 2-opt. None if no order fits the opening hours."""
    if not stops:
        return ()
    if len(stops) <= 6:
        return trip.best_order(tuple(sorted(stops)))
    order = list(_nearest_neighbour(trip, stops))
    if not trip.feasible(tuple(order)):
        return None
    best = trip.cost(tuple(order))
    improved, passes = True, 0
    while improved and passes < 60:
        improved, passes = False, passes + 1
        for i in range(len(order) - 1):
            for j in range(i + 1, len(order)):
                candidate = order[:i] + order[i:j + 1][::-1] + order[j + 1:]
                if not trip.feasible(tuple(candidate)):
                    continue
                c = trip.cost(tuple(candidate))
                if c < best - EPS:
                    order, best, improved = candidate, c, True
    return tuple(order)


def plan_trip(
    items: list[dict[str, Any]],
    offers: dict[str, dict[str, dict[str, Any]]],
    stores: list[dict[str, Any]],
    dist: list[list[float]],
    dur: list[list[float]],
    per_km: float,
    max_stops: int | None = None,
    round_trip: bool = True,
    per_min: float = 0.0,
    stop_minutes: float = 0.0,
    hours: dict[str, Schedule | None] | None = None,
    depart: datetime | None = None,
) -> dict[str, Any]:
    """Pick the stores to visit and the driving order with the lowest total cost.

    Total = items + driving (distance x cost per km) + time (drive plus ``stop_minutes`` in each
    store, x ``per_min``).

    The number of stores is decided by the search: every count from one up to five is tried and the
    cheapest trip wins (more if buying everything needs more, using a heuristic). ``max_stops`` is
    only a preference: the best trip with at most that many stores is chosen when there is one;
    otherwise the best trip is still returned, with ``exceeds_preference`` set.

    With ``hours`` and a ``depart`` time, stores that would be closed when you arrive are avoided.
    If no order works at all, the best trip ignoring opening hours is returned with
    ``hours_ignored`` set and the stops that would be closed marked ``closed_on_arrival``.

    ``items``: {item_id, name, qty, amount}; ``stores``: dicts with an ``id``; ``dist``/``dur``
    are matrices over [home, *stores] (kilometres, minutes). Returns the best plan overall, the
    best plan for each number of stops, the best single-store plan, and what you would do if
    driving were free.
    """
    preferred = None
    if max_stops:
        preferred = max(1, min(int(max_stops), MAX_PREFERRED_STOPS))
    n = len(stores)
    trip = Trip(dist, dur, per_km, round_trip, per_min, stop_minutes, hours, depart, stores)
    trip_free = Trip(dist, dur, per_km, round_trip, per_min, stop_minutes, None, None, stores)  # ignores opening hours

    # cost[i][s]: what item i costs at store s, or None if that store has no price for it
    offers_by_item: list[list[dict[str, Any] | None]] = []
    item_cost: list[list[float | None]] = []
    for it in items:
        row_offers = [offers.get(it["item_id"], {}).get(st["id"]) for st in stores]
        offers_by_item.append(row_offers)
        item_cost.append([o["price"] * it["amount"] * it["qty"] if o else None for o in row_offers])

    buyable = [i for i in range(len(items)) if any(c is not None for c in item_cost[i])]
    unavailable = [items[i] for i in range(len(items)) if i not in buyable]
    empty = {"best": None, "by_stops": {}, "single_store": None, "ignoring_driving": None,
             "unavailable": unavailable, "considered_stores": n, "closed_stores": [],
             "preferred_stops": preferred, "stops_needed": None, "exceeds_preference": False, "hours_ignored": False}
    if not buyable or not n:
        return empty

    def cheapest_of(i: int, pool: list[int] | None = None) -> int | None:
        options = [s for s in (pool if pool is not None else range(n)) if item_cost[i][s] is not None]
        return min(options, key=lambda s: item_cost[i][s]) if options else None

    def assign(combo) -> dict[int, int] | None:
        out = {}
        for i in buyable:
            options = [s for s in combo if item_cost[i][s] is not None]
            if not options:
                return None
            out[i] = min(options, key=lambda s: item_cost[i][s])
        return out

    # --- which stores are worth combining (keeps the exhaustive search small)
    def top_two(i: int) -> list[int]:
        return sorted((s for s in range(n) if item_cost[i][s] is not None), key=lambda s: item_cost[i][s])[:2]

    essential = {s for i in buyable for s in top_two(i)}
    if len(essential) > MAX_CANDIDATE_STORES:
        essential = {c for c in (cheapest_of(i) for i in buyable) if c is not None}
    candidates: list[int] | None
    if len(essential) > MAX_CANDIDATE_STORES:
        candidates = None  # too many stores are needed to try every combination: use the heuristic
    elif n <= MAX_CANDIDATE_STORES:
        candidates = list(range(n))
    else:
        nearest = sorted(range(n), key=lambda s: dist[0][s + 1])[:5]
        covering = sorted(range(n), key=lambda s: -sum(1 for i in buyable if item_cost[i][s] is not None))[:6]
        candidates = list(dict.fromkeys(sorted(essential) + nearest + covering))[:max(MAX_CANDIDATE_STORES, len(essential))]

    def exact(t: Trip) -> dict[int, tuple[float, tuple[int, ...], dict[int, int]]]:
        found: dict[int, tuple[float, tuple[int, ...], dict[int, int]]] = {}
        if not candidates:
            return found
        for k in range(1, min(K_AUTO, len(candidates)) + 1):
            for combo in combinations(candidates, k):
                assignment = assign(combo)
                if assignment is None or len(set(assignment.values())) < k:
                    continue  # a shorter trip covers this one
                items_sum = sum(item_cost[i][s] for i, s in assignment.items())
                current = found.get(k)
                if current is not None and items_sum >= current[0] - EPS:
                    continue  # driving and time only add cost, so this cannot win
                order = t.best_order(combo)
                if order is None:
                    continue  # no visiting order fits the opening hours
                total = items_sum + t.cost(order)
                if current is None or total < current[0] - EPS:
                    found[k] = (total, order, assignment)
        return found

    def heuristic(t: Trip) -> dict[int, tuple[float, tuple[int, ...], dict[int, int]]]:
        """Start from the cheapest store for every item, then drop stores while that lowers the total."""
        def evaluate(stops: set[int]):
            assignment = {}
            for i in buyable:
                s = cheapest_of(i, sorted(stops))
                if s is None:
                    return None
                assignment[i] = s
            used = set(assignment.values())
            order = _heuristic_order(t, used)
            if order is None:
                return None
            return sum(item_cost[i][s] for i, s in assignment.items()) + t.cost(order), order, assignment

        current = evaluate({c for c in (cheapest_of(i) for i in buyable) if c is not None})
        while current is not None and len(current[1]) > 1:
            trials = [evaluate(set(current[1]) - {s}) for s in current[1]]
            better = [c for c in trials if c is not None and c[0] < current[0] - EPS]
            if not better:
                break
            current = min(better, key=lambda c: c[0])
        return {len(current[1]): current} if current is not None else {}

    # --- search: exact first, then the heuristic, and only then ignore opening hours
    hours_ignored = False
    found = exact(trip) or heuristic(trip)
    if not found and trip.hours and trip.depart is not None:
        found = exact(trip_free) or heuristic(trip_free)
        hours_ignored = bool(found)
    closed = [s for s in range(n) if not trip.feasible((s,))]
    if not found:
        empty["closed_stores"] = [stores[s] for s in closed]
        return empty

    def describe(order: tuple[int, ...], assignment: dict[int, int]) -> dict[str, Any]:
        return _describe(trip, order, assignment, items, item_cost, offers_by_item)

    by_stops = {k: describe(order, a) for k, (_, order, a) in sorted(found.items())}
    pool = [p for k, p in by_stops.items() if preferred is None or k <= preferred] or list(by_stops.values())
    best = min(pool, key=lambda p: (round(p["total"], 2), p["stop_count"]))  # fewest stops wins a tie

    # --- cheapest prices wherever they are, ignoring the drive (to show what driving costs)
    free = {i: cheapest_of(i) for i in buyable}
    free = {i: s for i, s in free.items() if s is not None}
    free_stops = set(free.values())
    free_order = _heuristic_order(trip_free, free_stops) or _nearest_neighbour(trip, free_stops)
    ignoring = describe(free_order, free)

    # stores left out only because they would be closed: those that were the cheapest for something
    cheapest = {cheapest_of(i) for i in buyable}
    in_plan = {stop["store"]["id"] for stop in best["stops"]}
    left_out = [stores[s] for s in closed if s in cheapest and stores[s]["id"] not in in_plan] if not hours_ignored else []

    return {
        "best": best,
        "by_stops": by_stops,
        "single_store": by_stops.get(1),
        "ignoring_driving": ignoring,
        "unavailable": unavailable,
        "considered_stores": len(candidates) if candidates else n,
        "closed_stores": left_out,
        "preferred_stops": preferred,
        "stops_needed": min(by_stops),
        "exceeds_preference": preferred is not None and best["stop_count"] > preferred,
        "hours_ignored": hours_ignored,
    }
