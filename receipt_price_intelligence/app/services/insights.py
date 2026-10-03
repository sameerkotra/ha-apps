"""Spending insights that go beyond totals (pure functions over plain rows, no database).

* ``spend_by_category``: where the money goes by kind of item
* ``budget_status``: how a monthly budget is going and where the month is heading
* ``discount_summary``: what sales, coupons and member prices saved
* ``basket_index``: how the prices of the things you keep buying have moved (a personal inflation index)
"""

from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date
from statistics import mean
from typing import Any

from app.services import analytics

UNCATEGORIZED = "Uncategorized"
BUDGET_WARN_PCT = 80.0


# --------------------------------------------------------------------------- #
# Categories and budgets
# --------------------------------------------------------------------------- #

def spend_by_category(rows: list[dict[str, Any]], previous: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Spend per category from price observation rows (``category``, ``line_total``, ``item_name``).

    ``previous`` (rows of the period before) adds ``previous_spend`` and ``change_pct`` per category.
    """
    def totals(source: list[dict[str, Any]]) -> dict[str, float]:
        out: dict[str, float] = defaultdict(float)
        for r in source:
            out[r.get("category") or UNCATEGORIZED] += r.get("line_total") or 0.0
        return out

    now, before = totals(rows), totals(previous or [])
    grand = sum(now.values())
    items: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for r in rows:
        items[r.get("category") or UNCATEGORIZED][r["item_name"]] += r.get("line_total") or 0.0

    categories = []
    for name, spend in sorted(now.items(), key=lambda kv: kv[1], reverse=True):
        prev = before.get(name)
        top = sorted(items[name].items(), key=lambda kv: kv[1], reverse=True)[:3]
        categories.append({
            "category": name, "spend": round(spend, 2),
            "share": round(spend / grand * 100, 1) if grand else 0.0,
            "top_items": [{"name": n, "spend": round(v, 2)} for n, v in top],
            "previous_spend": round(prev, 2) if prev is not None else None,
            "change_pct": round((spend - prev) / prev * 100, 1) if prev else None,
        })
    return {"total": round(grand, 2), "categories": categories,
            "uncategorized_share": round(now.get(UNCATEGORIZED, 0.0) / grand * 100, 1) if grand else 0.0}


def budget_status(amount: float, spent: float, today: date) -> dict[str, Any]:
    """Where a monthly budget stands on ``today``.

    ``projected`` is the month's spend if the pace so far continues. Status: ``over`` once spent
    exceeds the budget, ``warn`` from 80% used or when the pace would overshoot by a tenth,
    otherwise ``ok``.
    """
    days = calendar.monthrange(today.year, today.month)[1]
    projected = spent / today.day * days if today.day else spent
    pct = spent / amount * 100 if amount > 0 else 0.0
    if amount > 0 and spent > amount:
        status = "over"
    elif amount > 0 and (pct >= BUDGET_WARN_PCT or (today.day >= 7 and projected > amount * 1.1)):
        status = "warn"
    else:
        status = "ok"
    return {
        "amount": round(amount, 2), "spent": round(spent, 2), "remaining": round(amount - spent, 2),
        "percent": round(pct, 1), "projected": round(projected, 2), "status": status,
        "days_left": days - today.day,
    }


# --------------------------------------------------------------------------- #
# Sales, coupons and member prices
# --------------------------------------------------------------------------- #

def discount_summary(lines: list[dict[str, Any]], spend_total: float | None = None) -> dict[str, Any]:
    """What discounts saved. ``lines`` each have ``saved`` (amount taken off), ``paid`` (what the
    line cost after it), ``date``, ``chain_id``, ``chain_name``, ``item_name``, ``receipt_id``.

    ``rate_pct`` is the saving as a share of what those lines would have cost without it. With
    ``spend_total`` (everything spent in the period) ``share_of_spend_pct`` is the saving against
    total spend plus savings.
    """
    lines = [l for l in lines if (l.get("saved") or 0) > 0]
    saved = sum(l["saved"] for l in lines)
    paid = sum(max(l.get("paid") or 0.0, 0.0) for l in lines)

    by_store: dict[str, dict[str, Any]] = {}
    by_month: dict[str, float] = defaultdict(float)
    by_item: dict[str, dict[str, Any]] = {}
    for l in lines:
        s = by_store.setdefault(l.get("chain_id") or "unknown", {"chain_id": l.get("chain_id"), "name": l.get("chain_name") or "Unknown store", "saved": 0.0, "paid": 0.0, "lines": 0})
        s["saved"] += l["saved"]; s["paid"] += max(l.get("paid") or 0.0, 0.0); s["lines"] += 1
        if l.get("date"):
            by_month[l["date"][:7]] += l["saved"]
        i = by_item.setdefault(l.get("item_name") or "Item", {"name": l.get("item_name") or "Item", "saved": 0.0, "times": 0})
        i["saved"] += l["saved"]; i["times"] += 1

    def rate(sv: float, pd: float) -> float:
        return round(sv / (sv + pd) * 100, 1) if sv + pd > 0 else 0.0

    months = []
    if by_month:
        for key in analytics._month_range(min(by_month), max(by_month)):
            months.append({"month": key, "saved": round(by_month.get(key, 0.0), 2)})
    return {
        "total_saved": round(saved, 2),
        "lines": len(lines),
        "receipts": len({l.get("receipt_id") for l in lines if l.get("receipt_id")}),
        "rate_pct": rate(saved, paid),
        "share_of_spend_pct": round(saved / (spend_total + saved) * 100, 1) if spend_total and spend_total + saved > 0 else None,
        "by_store": sorted(({**s, "saved": round(s["saved"], 2), "paid": round(s["paid"], 2), "rate_pct": rate(s["saved"], s["paid"])}
                            for s in by_store.values()), key=lambda s: s["saved"], reverse=True),
        "by_month": months,
        "top_items": sorted(({"name": i["name"], "saved": round(i["saved"], 2), "times": i["times"]} for i in by_item.values()),
                            key=lambda i: i["saved"], reverse=True)[:8],
    }


# --------------------------------------------------------------------------- #
# Personal price index
# --------------------------------------------------------------------------- #

MIN_MONTHS_FOR_ITEM = 3   # an item must be bought in this many different months to be in the basket
MIN_BASKET_ITEMS = 3


def basket_index(observations: list[dict[str, Any]], today: date, months: int = 12) -> dict[str, Any]:
    """How much the things you keep buying cost now compared with a year ago.

    Only items bought in at least three different months count. Each month's average price per item
    (in a consistent unit) is compared with the month before over the items present in both, weighted
    by how much you spend on each, and the monthly changes are chained into an index that starts at
    100. Because it follows your own basket, it reflects your prices, not the national average.
    """
    start_month = _shift_month(today, -(months - 1))
    rows = [r for r in analytics.prepare_price_rows(observations) if r["price"] is not None and r["date"][:7] >= start_month]

    per_item: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    spend: dict[str, float] = defaultdict(float)
    meta: dict[str, dict[str, Any]] = {}
    for r in rows:
        per_item[r["item_id"]][r["date"][:7]].append(r["price"])
        spend[r["item_id"]] += r.get("line_total") or 0.0
        meta[r["item_id"]] = {"name": r["item_name"], "unit": r["cunit"]}

    basket = {i for i, m in per_item.items() if len(m) >= MIN_MONTHS_FOR_ITEM}
    if len(basket) < MIN_BASKET_ITEMS:
        return {"available": False, "reason": f"Needs at least {MIN_BASKET_ITEMS} items you have bought in {MIN_MONTHS_FOR_ITEM} different months.",
                "items_used": len(basket)}

    prices = {i: {m: mean(v) for m, v in per_item[i].items()} for i in basket}
    all_months = sorted({m for i in basket for m in prices[i]})
    timeline = analytics._month_range(all_months[0], all_months[-1])
    weight = {i: max(spend[i], 0.01) for i in basket}

    series = [{"month": timeline[0], "index": 100.0, "mom_pct": None}]
    index = 100.0
    for prev, cur in zip(timeline, timeline[1:]):
        common = [i for i in basket if prev in prices[i] and cur in prices[i]]
        ratio = 1.0
        if common:
            before = sum(weight[i] * prices[i][prev] for i in common)
            after = sum(weight[i] * prices[i][cur] for i in common)
            ratio = after / before if before else 1.0
        index *= ratio
        series.append({"month": cur, "index": round(index, 2), "mom_pct": round((ratio - 1) * 100, 2)})

    movers = []
    for i in basket:
        ms = sorted(prices[i])
        first, last = prices[i][ms[0]], prices[i][ms[-1]]
        if first > 0 and ms[0] != ms[-1]:
            movers.append({"item_id": i, "name": meta[i]["name"], "unit": meta[i]["unit"], "from_price": round(first, 4), "to_price": round(last, 4),
                           "change_pct": round((last - first) / first * 100, 1), "from_month": ms[0], "to_month": ms[-1]})
    return {
        "available": True,
        "series": series,
        "change_pct": round(series[-1]["index"] - 100.0, 1),
        "months": len(timeline),
        "items_used": len(basket),
        "risers": sorted((m for m in movers if m["change_pct"] > 0), key=lambda m: m["change_pct"], reverse=True)[:5],
        "fallers": sorted((m for m in movers if m["change_pct"] < 0), key=lambda m: m["change_pct"])[:5],
    }


def _shift_month(today: date, delta: int) -> str:
    year, month = today.year, today.month + delta
    while month < 1:
        year, month = year - 1, month + 12
    while month > 12:
        year, month = year + 1, month - 12
    return f"{year:04d}-{month:02d}"
