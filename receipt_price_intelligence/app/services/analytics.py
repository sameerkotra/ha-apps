"""Spend analysis, price trends, and recommendations.

Pure functions over plain dicts (no database, no third-party imports), so every rule is
unit-testable. ``app/api/analysis.py`` loads rows from the database and calls these.

Two row shapes are used.

Receipt rows (one per approved receipt):
    id, date (YYYY-MM-DD), chain_id, chain_name, location_id, store_number, city, total

Observation rows (one per priced receipt line):
    item_id, item_name (the common name), receipt_id, chain_id, chain_name, location_id,
    store_number, city, date, unit_price, unit, line_total

Prices are only ever compared like-for-like: mass units (LB, OZ, KG, G) are converted to
one unit per item, and anything else (for example "each") only compares with itself.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import date, timedelta
from statistics import mean
from typing import Any, Iterable

from app.services import units

# How many pounds one of each unit is.
MASS_IN_LB = {"LB": 1.0, "OZ": 1 / 16, "KG": 2.2046226218, "G": 0.0022046226218}

TREND_THRESHOLD_PCT = 3.0     # a change smaller than this is "flat"
ALERT_THRESHOLD_PCT = 10.0    # latest price this far from the item's usual is worth a note
ALERT_RECENT_DAYS = 30        # ...but only if it was seen this recently
RESTOCK_MAX_RATIO = 3.0       # beyond this many usual intervals an item has probably lapsed


# --------------------------------------------------------------------------- #
# Units
# --------------------------------------------------------------------------- #

VOLUME_IN_FLOZ = {
    "FLOZ": 1.0, "ML": 1 / 29.5735295625, "L": 1000 / 29.5735295625,
    "GAL": 128.0, "QT": 32.0, "PT": 16.0,
}
FAMILIES = {"MASS": MASS_IN_LB, "VOLUME": VOLUME_IN_FLOZ}


def _unit_key(unit: str | None) -> str:
    """A unit as its canonical code (see app.services.units), or "" for an empty value."""
    return units.unit_code(unit) or ""


def _family(key: str) -> str:
    """The family a unit belongs to (units in a family convert to each other)."""
    for name, table in FAMILIES.items():
        if key in table:
            return name
    return key  # every other unit only compares with itself


def display_unit(unit: str | None) -> str | None:
    return units.display_unit(unit)


def convert_price(price: float | None, from_unit: str | None, to_unit: str | None) -> float | None:
    """Convert a per-unit price between units of one family (pounds and ounces, litres and gallons).

    None if the units are not comparable.
    """
    if price is None:
        return None
    src, dst = _unit_key(from_unit), _unit_key(to_unit)
    if src == dst:
        return price
    family = _family(src)
    if family in FAMILIES and family == _family(dst):
        table = FAMILIES[family]
        return price / table[src] * table[dst]
    return None


def canonical_unit(rows: Iterable[dict[str, Any]]) -> str | None:
    """The unit an item's prices are compared in: the most common one, treating each family as one unit."""
    counts: Counter[str] = Counter()
    for row in rows:
        if row.get("unit_price") is not None and row.get("unit"):
            counts[_unit_key(row["unit"])] += 1
    if not counts:
        return None

    families: dict[str, Counter[str]] = defaultdict(Counter)
    for unit, n in counts.items():
        families[_family(unit)][unit] += n
    family = max(families.values(), key=lambda c: sum(c.values()))
    return display_unit(family.most_common(1)[0][0])


# --------------------------------------------------------------------------- #
# Pack sizes: "MILK 1GAL" and "MILK 64OZ" compared per fluid ounce
# --------------------------------------------------------------------------- #

_NUM = r"(\d+(?:\.\d+)?|\.\d+)"
_SIZE_UNITS = r"(FL\.?\s?OZ|OZ|LBS?|KG|GM|G|ML|LTR|LT|L|GAL|GA|QT|PT)"
_SIZE_RE = re.compile(rf"(?<![\d.]){_NUM}\s*{_SIZE_UNITS}(?![A-Z])")
_COUNT_RE = re.compile(r"(?<![\d.])(\d+)\s*(?:PK|PACK|CT|COUNT)(?![A-Z])")
_MULT_RE = re.compile(rf"(?<![\d.])(\d+)\s*X\s*{_NUM}\s*{_SIZE_UNITS}(?![A-Z])")
_PACKS_OF_RE = re.compile(rf"{_NUM}\s*{_SIZE_UNITS}\s*X\s*(\d+)(?![\d.])")
_VOLUME_KEYS = {"FLOZ", "ML", "L", "GAL", "QT", "PT"}
_MASS_KEYS = {"LB", "KG", "G"}


def parse_pack_size(description: str | None) -> dict[str, Any] | None:
    """The total size in a printed description, or None.

    "GV 2% MILK 1GAL" -> 1 GAL; "COKE 12PK 12OZ" -> 144 OZ; "EGGS 18CT" -> 18 CT. A plain OZ
    is ambiguous (weight or fluid); the caller settles it from the item's other purchases.
    """
    if not description:
        return None
    text = description.upper()

    m = _MULT_RE.search(text)
    if m:
        return {"amount": int(m[1]) * float(m[2]), "unit": _unit_key(m[3])}
    m = _PACKS_OF_RE.search(text)
    if m:
        return {"amount": float(m[1]) * int(m[3]), "unit": _unit_key(m[2])}

    size = _SIZE_RE.search(text)
    count = _COUNT_RE.search(text)
    if size:
        amount = float(size[1])
        if count and int(count[1]) > 1:
            amount *= int(count[1])
        return {"amount": amount, "unit": _unit_key(size[2])}
    if count and int(count[1]) > 1:
        return {"amount": float(count[1]), "unit": "CT"}
    return None


def _apply_pack_sizes(rows: list[dict[str, Any]]) -> None:
    """Re-express an item's per-package prices per unit of size when its purchases came in
    different pack sizes. Rows are changed in place (originals kept as ``orig_*``).

    Only applies when at least two purchases are "each" priced, most of those have a readable
    size, and the sizes differ; otherwise a per-package price is already fair.
    """
    each = [r for r in rows if _unit_key(r.get("unit")) in ("EACH", "")]
    if len(each) < 2:
        return
    parsed = [(r, parse_pack_size(r.get("description"))) for r in each]
    ok = [(r, p) for r, p in parsed if p and p["amount"] > 0 and r.get("unit_price") is not None]
    if len(ok) < 2 or len(ok) / len(each) < 0.6:
        return

    has_volume = any(p["unit"] in _VOLUME_KEYS for _, p in ok)
    for _, p in ok:
        if p["unit"] == "OZ" and has_volume:
            p["unit"] = "FLOZ"  # this item is a liquid

    families = Counter(_family(p["unit"]) for _, p in ok)
    family, n = families.most_common(1)[0]
    if n < 2 or n / len(each) < 0.6:
        return
    use = [(r, p) for r, p in ok if _family(p["unit"]) == family]
    base = FAMILIES.get(family)
    sizes = {round(p["amount"] * (base[p["unit"]] if base else 1), 3) for _, p in use}
    if len(sizes) < 2:
        return  # always the same size: the per-package price is comparable as it is

    for r, p in use:
        r["orig_unit"], r["orig_unit_price"] = r.get("unit"), r.get("unit_price")
        r["unit_price"] = r["unit_price"] / p["amount"]
        r["unit"] = p["unit"]
        r["pack_normalized"] = True


def prepare_price_rows(observations: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Copy observation rows adding ``price`` and ``cunit``: the price in the item's canonical unit (or None).

    Purchases of one item in different pack sizes are put on a per-size basis first (see
    ``_apply_pack_sizes``).
    """
    by_item: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for obs in observations:
        by_item[obs["item_id"]].append(dict(obs))

    prepared = []
    for rows in by_item.values():
        _apply_pack_sizes(rows)
        unit = canonical_unit(rows)
        for row in rows:
            row["cunit"] = unit
            row["price"] = convert_price(row.get("unit_price"), row.get("unit"), unit) if unit else None
            if row["price"] is None and unit and row.get("unit_price") is not None:
                # sold per piece at one store and by weight at another: convert by the weight of one piece
                converted = piece_price(row, unit)
                if converted:
                    row["price"], row["converted"] = converted
            prepared.append(row)
    return prepared


_COUNT = {"EACH", "CT"}
_GRAMS_PER = {"LB": 453.59237, "OZ": 28.349523, "KG": 1000.0, "G": 1.0}


def estimate_note(row: dict[str, Any]) -> str | None:
    """How a converted price was worked out, for showing next to it ("from $2.79/lb, a typical avocado is about
    0.44 lb"), or None for a price as paid."""
    c = row.get("converted")
    if not c:
        return None
    lb = c["grams"] / _GRAMS_PER["LB"]
    size = f"{lb:.2f} lb" if lb < 10 else f"{lb:.0f} lb"
    paid = f"${row['unit_price']:.2f}{'' if c['from'] == 'each' else '/' + c['from']}"
    if c["source"] == "receipt":
        return f"from {paid} for {size}"
    return f"from {paid}; a typical {c['as']} is about {size}"


def piece_price(row: dict[str, Any], to_unit: str) -> tuple[float, dict[str, Any]] | None:
    """A price per piece as a price by weight, or the other way round, by the weight of one piece: the weight on the
    receipt line when a pack is sold each ("3 LB BAG"), else the typical weight of one (see piece_weights). Returns
    (price in ``to_unit``, {grams, source, as}) or None when the weight of one is not known."""
    from app.services import piece_weights
    src, dst = _unit_key(row.get("unit")), _unit_key(to_unit)
    if src in _COUNT and _family(dst) == "MASS":
        grams, source, taken_as = None, None, None
        wkey = _unit_key(row.get("weight_unit"))
        if row.get("weight_value") and wkey in _GRAMS_PER:
            grams, source = row["weight_value"] * _GRAMS_PER[wkey], "receipt"
        else:
            found = piece_weights.typical(row.get("item_name"))
            if found:
                grams, taken_as = found
                source = "typical"
        if not grams:
            return None
        per_gram = row["unit_price"] / grams
        return per_gram * _GRAMS_PER[dst], {"grams": round(grams, 1), "source": source, "as": taken_as, "from": "each"}
    if _family(src) == "MASS" and dst in _COUNT:
        found = piece_weights.typical(row.get("item_name"))
        if not found or src not in _GRAMS_PER:
            return None
        grams, taken_as = found
        per_gram = row["unit_price"] / _GRAMS_PER[src]
        return per_gram * grams, {"grams": grams, "source": "typical", "as": taken_as, "from": src.lower()}
    return None


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #

def location_label(chain_name: str | None, store_number: str | None = None, city: str | None = None) -> str:
    label = chain_name or "Unknown store"
    if store_number:
        label += f" #{store_number}"
    if city:
        label += f" · {city.title() if city.isupper() else city}"
    return label


def _d(value: str) -> date:
    return date.fromisoformat(value)


def _round(value: float | None, places: int = 2) -> float | None:
    return None if value is None else round(value, places)


def _month_range(first: str, last: str) -> list[str]:
    year, month = int(first[:4]), int(first[5:7])
    end = (int(last[:4]), int(last[5:7]))
    months = []
    while (year, month) <= end:
        months.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return months


def avg_interval_days(dates: Iterable[str]) -> float | None:
    """Average days between distinct purchase dates, or None with fewer than two."""
    ordered = sorted({_d(d) for d in dates})
    if len(ordered) < 2:
        return None
    gaps = [(b - a).days for a, b in zip(ordered, ordered[1:])]
    return sum(gaps) / len(gaps)


# --------------------------------------------------------------------------- #
# Spend
# --------------------------------------------------------------------------- #

def summarize_spend(receipts: list[dict[str, Any]]) -> dict[str, Any]:
    """Totals, spend by month, and spend by store (chain, with its locations)."""
    spend = sum(r.get("total") or 0.0 for r in receipts)
    trips = len(receipts)

    months: dict[str, dict[str, Any]] = {}
    for r in receipts:
        if not r.get("date"):
            continue
        bucket = months.setdefault(r["date"][:7], {"spend": 0.0, "trips": 0})
        bucket["spend"] += r.get("total") or 0.0
        bucket["trips"] += 1
    by_month = []
    if months:
        for key in _month_range(min(months), max(months)):
            b = months.get(key, {"spend": 0.0, "trips": 0})
            by_month.append({"month": key, "spend": _round(b["spend"]), "trips": b["trips"]})

    chains: dict[str, dict[str, Any]] = {}
    for r in receipts:
        key = r.get("chain_id") or "unknown"
        chain = chains.setdefault(key, {
            "chain_id": r.get("chain_id"), "name": r.get("chain_name") or "Unknown store",
            "spend": 0.0, "trips": 0, "last_visit": None, "locations": {},
        })
        total = r.get("total") or 0.0
        chain["spend"] += total
        chain["trips"] += 1
        if r.get("date") and (chain["last_visit"] is None or r["date"] > chain["last_visit"]):
            chain["last_visit"] = r["date"]
        lkey = r.get("location_id") or "unknown"
        loc = chain["locations"].setdefault(lkey, {
            "location_id": r.get("location_id"),
            "label": location_label(r.get("chain_name"), r.get("store_number"), r.get("city")),
            "spend": 0.0, "trips": 0,
        })
        loc["spend"] += total
        loc["trips"] += 1

    by_store = []
    for chain in sorted(chains.values(), key=lambda c: c["spend"], reverse=True):
        locations = sorted(chain["locations"].values(), key=lambda l: l["spend"], reverse=True)
        by_store.append({
            "chain_id": chain["chain_id"], "name": chain["name"],
            "spend": _round(chain["spend"]), "trips": chain["trips"],
            "avg_basket": _round(chain["spend"] / chain["trips"]),
            "share": _round(chain["spend"] / spend * 100, 1) if spend else 0.0,
            "last_visit": chain["last_visit"],
            "locations": [{**l, "spend": _round(l["spend"])} for l in locations],
        })

    return {
        "totals": {
            "spend": _round(spend), "trips": trips,
            "avg_basket": _round(spend / trips) if trips else None,
            "stores": len([c for c in chains if c != "unknown"]),
        },
        "by_month": by_month,
        "by_store": by_store,
    }


# --------------------------------------------------------------------------- #
# Prices and trends
# --------------------------------------------------------------------------- #

def price_trend(points: Iterable[tuple[str, float | None]]) -> dict[str, Any]:
    """Change in price over time from (date, price) points.

    With two or more calendar months of data the first and last month's average prices
    are compared (so one odd shelf price does not decide it); otherwise first vs last.
    """
    pts = sorted((d, p) for d, p in points if p is not None)
    by_date: dict[str, list[float]] = defaultdict(list)
    for d, p in pts:
        by_date[d].append(p)
    series = [{"date": d, "price": _round(mean(v), 4)} for d, v in sorted(by_date.items())]

    if len(pts) < 2:
        return {"change_pct": None, "direction": "flat", "series": series}

    monthly: dict[str, list[float]] = defaultdict(list)
    for d, p in pts:
        monthly[d[:7]].append(p)
    months = sorted(monthly)
    if len(months) >= 2:
        first, last = mean(monthly[months[0]]), mean(monthly[months[-1]])
    else:
        first, last = pts[0][1], pts[-1][1]

    change = (last - first) / first * 100 if first else None
    direction = "flat"
    if change is not None:
        direction = "up" if change >= TREND_THRESHOLD_PCT else "down" if change <= -TREND_THRESHOLD_PCT else "flat"
    return {"change_pct": _round(change, 1), "direction": direction, "series": series}


def compare_stores(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One item's latest price at each store location, cheapest first.

    ``rows`` must come from ``prepare_price_rows`` (they carry ``price`` and ``cunit``).
    """
    by_loc: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if r.get("price") is not None:
            by_loc[r.get("location_id") or "unknown"].append(r)

    stores = []
    for loc_id, lrows in by_loc.items():
        lrows.sort(key=lambda r: r["date"])
        latest = lrows[-1]
        stores.append({
            "location_id": latest.get("location_id"),
            "chain_id": latest.get("chain_id"),
            "label": location_label(latest.get("chain_name"), latest.get("store_number"), latest.get("city")),
            "chain_name": latest.get("chain_name"),
            "latest_price": _round(latest["price"], 4),
            "latest_date": latest["date"],
            "avg_price": _round(mean(r["price"] for r in lrows), 4),
            "min_price": _round(min(r["price"] for r in lrows), 4),
            "observations": len(lrows),
            "unit": latest["cunit"],
            "trend": price_trend((r["date"], r["price"]) for r in lrows),
            "estimate": estimate_note(latest),  # converted from a price per piece or by weight
        })
    stores.sort(key=lambda s: s["latest_price"])
    if len(stores) > 1:
        best = stores[0]["latest_price"]
        for s in stores:
            s["cheapest"] = s["latest_price"] == best
            s["premium_pct"] = _round((s["latest_price"] - best) / best * 100, 1) if best else None
    else:
        for s in stores:
            s["cheapest"] = False
            s["premium_pct"] = None
    return stores


# --------------------------------------------------------------------------- #
# Item statistics
# --------------------------------------------------------------------------- #

def item_stats(observations: list[dict[str, Any]], today: date) -> list[dict[str, Any]]:
    """Per-item spend, purchase frequency, expected next purchase, and price trend."""
    rows = prepare_price_rows(observations)
    by_item: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_item[r["item_id"]].append(r)

    stats = []
    for item_id, irows in by_item.items():
        dates = [r["date"] for r in irows]
        last = max(dates)
        receipts = {r["receipt_id"] for r in irows if r.get("receipt_id")} or set(range(len(irows)))
        interval = avg_interval_days(dates)
        days_since = (today - _d(last)).days

        store_spend: Counter[str] = Counter()
        for r in irows:
            store_spend[r.get("chain_name") or "Unknown store"] += r.get("line_total") or 0.0

        priced = sorted((r for r in irows if r["price"] is not None), key=lambda r: r["date"])
        trend = price_trend((r["date"], r["price"]) for r in priced)

        next_expected = overdue = None
        if interval:
            next_expected = (_d(last) + timedelta(days=round(interval))).isoformat()
            overdue = round(days_since - interval)

        stats.append({
            "item_id": item_id,
            "name": irows[0]["item_name"],
            "spend": _round(sum(r.get("line_total") or 0.0 for r in irows)),
            "purchases": len(receipts),
            "first_date": min(dates),
            "last_date": last,
            "days_since_last": days_since,
            "avg_interval_days": _round(interval, 1),
            "next_expected_date": next_expected,
            "overdue_days": overdue,
            "stores": len({r.get("chain_id") for r in irows}),
            "top_store": store_spend.most_common(1)[0][0] if store_spend else None,
            "unit": priced[-1]["cunit"] if priced else None,
            "avg_price": _round(mean(r["price"] for r in priced), 4) if priced else None,
            "latest_price": _round(priced[-1]["price"], 4) if priced else None,
            "price_change_pct": trend["change_pct"],
            "trend": trend["direction"],
        })
    stats.sort(key=lambda s: s["spend"] or 0, reverse=True)
    return stats


def _location_info(priced: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Latest price, date and receipt count per store location, from priced rows of one item."""
    locs: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in priced:
        locs[r.get("location_id") or "unknown"].append(r)
    info = {}
    for lid, lrows in locs.items():
        lrows.sort(key=lambda r: r["date"])
        latest = lrows[-1]
        info[lid] = {
            "label": location_label(latest.get("chain_name"), latest.get("store_number"), latest.get("city")),
            "price": latest["price"], "date": latest["date"],
            "receipts": len({r["receipt_id"] for r in lrows if r.get("receipt_id")}) or len(lrows),
            "rows": lrows, "estimate": estimate_note(latest),
        }
    return info


def _usual_and_best(info: dict[str, dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    """The store you buy at most often, and the one with the lowest latest price (newest wins ties)."""
    usual = max(info.values(), key=lambda x: (x["receipts"], x["date"]))
    best = min(info.values(), key=lambda x: (x["price"], -_d(x["date"]).toordinal()))
    return usual, best


def cheapest_by_item(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """For every item with a comparable price: where it was cheapest most recently.

    Items bought at one store only are included (``compared`` is False) so the list covers
    everything. ``saving_pct`` is how much less the cheapest store charged than the store
    you usually buy the item at (0 when your usual store is already the cheapest).
    """
    rows = prepare_price_rows(observations)
    by_item: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if r["price"] is not None:
            by_item[r["item_id"]].append(r)

    out = []
    for item_id, priced in by_item.items():
        info = _location_info(priced)
        usual, best = _usual_and_best(info)
        compared = len(info) >= 2
        saving = 0.0
        if compared and best is not usual and usual["price"] > 0:
            saving = max(0.0, (usual["price"] - best["price"]) / usual["price"] * 100)
        ordered = sorted(info.values(), key=lambda x: x["price"])
        unit = priced[0]["cunit"]
        out.append({
            "item_id": item_id, "name": priced[0]["item_name"], "unit": unit,
            "compared": compared, "saving_pct": _round(saving, 1),
            "cheapest": {"label": best["label"], "price": _round(best["price"], 4), "date": best["date"], "estimate": best.get("estimate")},
            "usual": {"label": usual["label"], "price": _round(usual["price"], 4), "date": usual["date"], "receipts": usual["receipts"],
                      "estimate": usual.get("estimate")},
            "usual_is_cheapest": best is usual or not compared or saving == 0.0,
            "stores": [{"label": x["label"], "price": _round(x["price"], 4), "date": x["date"], "estimate": x.get("estimate")} for x in ordered[:6]],
        })
    out.sort(key=lambda x: (-(x["saving_pct"] or 0), not x["compared"], x["name"].lower()))
    return out


# --------------------------------------------------------------------------- #
# Recommendations
# --------------------------------------------------------------------------- #

def _money(value: float) -> str:
    return f"${value:,.2f}"


def recommendations(
    observations: list[dict[str, Any]],
    today: date,
    window_days: int = 90,
    min_savings_pct: float = 5.0,
) -> dict[str, Any]:
    """Suggestions from recent price history.

    * ``switch_store``: an item costs meaningfully less at another store than where you usually buy it
    * ``price_alerts``: the latest price is well above (or below) what that store usually charges
    * ``restock``: items bought on a regular rhythm that are now due
    * ``store_scorecard``: how often each chain is the cheapest for items sold at several chains

    Estimates are approximate and say how much data they rest on (``confidence``).
    """
    rows = prepare_price_rows(observations)
    by_item: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_item[r["item_id"]].append(r)

    switch: list[dict[str, Any]] = []
    alerts: list[dict[str, Any]] = []
    restock: list[dict[str, Any]] = []
    chain_scores: dict[str, dict[str, Any]] = {}

    for item_id, irows in by_item.items():
        name = irows[0]["item_name"]
        priced = [r for r in irows if r["price"] is not None]
        unit = priced[0]["cunit"] if priced else None
        unit_label = f"/{unit}" if unit and unit != "each" else ""

        # ---- per-location view
        info = _location_info(priced)
        locs = {lid: v["rows"] for lid, v in info.items()}

        # ---- switch store
        if len(info) >= 2:
            usual, best = _usual_and_best(info)
            if best is not usual and usual["price"] > 0:
                saving_pct = (usual["price"] - best["price"]) / usual["price"] * 100
                if saving_pct >= min_savings_pct:
                    dates = [r["date"] for r in irows]
                    receipts = {r["receipt_id"] for r in irows if r.get("receipt_id")} or set(range(len(irows)))
                    span = max(30, min(window_days, (today - _d(min(dates))).days))
                    per30 = len(receipts) / span * 30
                    spend = sum(r.get("line_total") or 0.0 for r in priced)
                    avg_price = mean(r["price"] for r in priced)
                    units_per_purchase = spend / avg_price / len(receipts) if avg_price and receipts else 1.0
                    per_unit = usual["price"] - best["price"]
                    age = (today - _d(best["date"])).days
                    freshness = max(0.2, 1 - age / max(window_days, 1))
                    confidence = round(min(1.0, (len(usual["rows"]) + len(best["rows"])) / 6) * freshness, 2)
                    switch.append({
                        "type": "SWITCH_STORE", "item_id": item_id, "item_name": name, "unit": unit,
                        "from": {"label": usual["label"], "price": _round(usual["price"], 4), "date": usual["date"], "receipts": usual["receipts"]},
                        "to": {"label": best["label"], "price": _round(best["price"], 4), "date": best["date"], "receipts": best["receipts"],
                               "estimate": best.get("estimate")},
                        "saving_pct": _round(saving_pct, 1),
                        "savings_per_unit": _round(per_unit, 4),
                        "est_monthly_savings": _round(per_unit * units_per_purchase * per30),
                        "confidence": confidence,
                        "message": (f"{name}: {best['label']} was {saving_pct:.0f}% cheaper "
                                    f"({_money(best['price'])}{unit_label} vs {_money(usual['price'])}{unit_label} at {usual['label']})"),
                    })

        # ---- price alerts: compare the newest price with what the same store charged before
        if priced:
            newest = max(priced, key=lambda r: r["date"])
            same_store = sorted(locs[newest.get("location_id") or "unknown"], key=lambda r: r["date"])
            prior = [r["price"] for r in same_store[:-1]]
            recent = (today - _d(newest["date"])).days <= ALERT_RECENT_DAYS
            if len(prior) >= 2 and recent:
                usual_price = mean(prior)
                change = (newest["price"] - usual_price) / usual_price * 100 if usual_price else 0.0
                if abs(change) >= ALERT_THRESHOLD_PCT:
                    label = location_label(newest.get("chain_name"), newest.get("store_number"), newest.get("city"))
                    up = change > 0
                    alerts.append({
                        "type": "PRICE_UP" if up else "PRICE_DOWN", "item_id": item_id, "item_name": name, "unit": unit,
                        "store": label, "price": _round(newest["price"], 4), "usual_price": _round(usual_price, 4),
                        "change_pct": _round(change, 1), "date": newest["date"], "observations": len(same_store),
                        "message": (f"{name} was {_money(newest['price'])}{unit_label} at {label}, "
                                    f"{abs(change):.0f}% {'above' if up else 'below'} its usual {_money(usual_price)}"),
                    })

        # ---- restock: a regular rhythm that is now due
        dates = sorted({r["date"] for r in irows})
        interval = avg_interval_days(dates)
        if interval and len(dates) >= 3 and interval >= 1:
            days_since = (today - _d(dates[-1])).days
            ratio = days_since / interval
            if 1.0 <= ratio <= RESTOCK_MAX_RATIO:
                restock.append({
                    "type": "RESTOCK", "item_id": item_id, "item_name": name,
                    "avg_interval_days": _round(interval, 1), "days_since_last": days_since,
                    "last_date": dates[-1], "purchases": len(dates),
                    "status": "overdue" if ratio >= 1.5 else "due",
                    "message": f"{name}: you usually buy it every {interval:.0f} days; it has been {days_since}.",
                })

        # ---- chain scorecard inputs (latest price per chain)
        chains: dict[str, dict[str, Any]] = {}
        for r in sorted(priced, key=lambda r: r["date"]):
            chains[r.get("chain_id") or "unknown"] = r
        if len(chains) >= 2:
            best_price = min(r["price"] for r in chains.values())
            for cid, r in chains.items():
                score = chain_scores.setdefault(cid, {
                    "chain_id": r.get("chain_id"), "name": r.get("chain_name") or "Unknown store",
                    "items_compared": 0, "cheapest_count": 0, "premiums": [],
                })
                score["items_compared"] += 1
                if r["price"] <= best_price + 1e-9:
                    score["cheapest_count"] += 1
                score["premiums"].append((r["price"] - best_price) / best_price * 100 if best_price else 0.0)

    switch.sort(key=lambda r: (r["est_monthly_savings"] or 0, r["saving_pct"]), reverse=True)
    alerts.sort(key=lambda r: abs(r["change_pct"]), reverse=True)
    restock.sort(key=lambda r: r["days_since_last"] / r["avg_interval_days"], reverse=True)

    scorecard = [{
        "chain_id": s["chain_id"], "name": s["name"], "items_compared": s["items_compared"],
        "cheapest_count": s["cheapest_count"], "avg_premium_pct": _round(mean(s["premiums"]), 1),
    } for s in chain_scores.values()]
    scorecard.sort(key=lambda s: (-s["cheapest_count"] / s["items_compared"], s["avg_premium_pct"]))

    return {
        "switch_store": switch,
        "price_alerts": alerts,
        "restock": restock[:15],
        "store_scorecard": scorecard,
        "est_monthly_savings": _round(sum(s["est_monthly_savings"] or 0 for s in switch)),
    }
