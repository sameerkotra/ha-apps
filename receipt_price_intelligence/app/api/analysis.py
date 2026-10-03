"""Spend analysis, price comparison, and recommendations for a home.

Everything is computed on request from approved receipts and price observations, so it
is always current and nothing stale is stored. Items are grouped by their common name.
The calculations live in ``app/services/analytics.py``; this module only loads rows.
"""

from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth import CurrentUser, get_current_user
from app.config import get_settings
from app.db import get_db
from app.db.models import CommonItem, CommonItemAlias
from app.services import analytics, insights
from app.services import homes as homes_service
from app.services.analysis_data import iso as _iso
from app.services.analysis_data import load_discount_lines, load_observations, load_receipts

router = APIRouter(prefix="/api/v1/homes/{home_id}/analysis", tags=["analysis"])

RANGES = {"30d": 30, "90d": 90, "12m": 365, "all": None}


def _parse_range(range_key: str) -> int | None:
    if range_key not in RANGES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"range must be one of {', '.join(RANGES)}",
        )
    return RANGES[range_key]


def _require_home(db: Session, home_id: str) -> None:
    if homes_service.get_home(db, home_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Home not found")


@router.get("/overview")
async def overview(
    home_id: str,
    range: str = "90d",
    _: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Headline numbers, spend by month, top stores and top items for a period.

    ``previous_spend`` / ``change_pct`` compare with the period of equal length just before.
    """
    _require_home(db, home_id)
    days = _parse_range(range)
    today = date.today()
    start = _iso(today - timedelta(days=days)) if days else None

    receipts = load_receipts(db, home_id, start)
    spend = analytics.summarize_spend(receipts)
    items = analytics.item_stats(load_observations(db, home_id, start), today)

    previous = change = None
    if days:
        prev = analytics.summarize_spend(load_receipts(db, home_id, _iso(today - timedelta(days=2 * days)), start))
        previous = prev["totals"]["spend"]
        if previous:
            change = round((spend["totals"]["spend"] - previous) / previous * 100, 1)

    return {
        "range": range,
        "start": start,
        "totals": {**spend["totals"], "distinct_items": len(items), "previous_spend": previous, "change_pct": change},
        "by_month": spend["by_month"],
        "top_stores": spend["by_store"][:5],
        "top_items": items[:5],
    }


@router.get("/stores")
async def spend_by_store(
    home_id: str,
    range: str = "90d",
    _: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Spend, trips and average basket per store chain, with a breakdown by location."""
    _require_home(db, home_id)
    days = _parse_range(range)
    start = _iso(date.today() - timedelta(days=days)) if days else None
    result = analytics.summarize_spend(load_receipts(db, home_id, start))
    return {"range": range, "start": start, "totals": result["totals"], "stores": result["by_store"]}


@router.get("/items")
async def spend_by_item(
    home_id: str,
    range: str = "90d",
    q: str | None = None,
    sort: str = "spend",
    _: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Spend and buying frequency per common item.

    ``sort``: spend (default), frequency (most purchases), recent, or name.
    """
    _require_home(db, home_id)
    days = _parse_range(range)
    today = date.today()
    start = _iso(today - timedelta(days=days)) if days else None

    stats = analytics.item_stats(load_observations(db, home_id, start), today)
    if q and q.strip():
        needle = q.strip().lower()
        stats = [s for s in stats if needle in s["name"].lower()]

    keys = {
        "spend": lambda s: -(s["spend"] or 0),
        "frequency": lambda s: (-s["purchases"], -(s["spend"] or 0)),
        "recent": lambda s: s["days_since_last"],
        "name": lambda s: s["name"].lower(),
    }
    if sort not in keys:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"sort must be one of {', '.join(keys)}")
    stats.sort(key=keys[sort])
    total = sum(s["spend"] or 0 for s in stats)
    return {"range": range, "start": start, "total_spend": round(total, 2), "items": stats[:300]}


@router.get("/prices")
async def item_prices(
    home_id: str,
    range: str = "12m",
    q: str | None = None,
    limit: int = 100,
    _: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Each item's latest price at each store, the cheapest, and the price trend."""
    _require_home(db, home_id)
    days = _parse_range(range)
    start = _iso(date.today() - timedelta(days=days)) if days else None

    rows = analytics.prepare_price_rows(load_observations(db, home_id, start))
    by_item: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_item.setdefault(row["item_id"], []).append(row)

    needle = q.strip().lower() if q and q.strip() else None
    out = []
    for item_id, irows in by_item.items():
        name = irows[0]["item_name"]
        if needle and needle not in name.lower():
            continue
        priced = [r for r in irows if r["price"] is not None]
        if not priced:
            continue
        stores = analytics.compare_stores(irows)
        trend = analytics.price_trend((r["date"], r["price"]) for r in priced)
        out.append({
            "item_id": item_id,
            "name": name,
            "unit": priced[0]["cunit"],
            "observations": len(priced),
            "last_date": max(r["date"] for r in priced),
            "stores": stores,
            "trend": trend,
        })
    out.sort(key=lambda x: x["last_date"], reverse=True)
    return {"range": range, "start": start, "items": out[: max(1, min(limit, 300))]}


@router.get("/items/{item_id}")
async def item_detail(
    home_id: str,
    item_id: str,
    range: str = "all",
    _: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """One item: its names, spend and rhythm, price at each store, and price history."""
    _require_home(db, home_id)
    days = _parse_range(range)
    today = date.today()
    start = _iso(today - timedelta(days=days)) if days else None

    item = db.get(CommonItem, item_id)
    if item is None or item.home_id != home_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")

    aliases = [
        a.alias for a in
        db.query(CommonItemAlias).filter(CommonItemAlias.common_item_id == item_id).order_by(CommonItemAlias.alias).all()
    ]
    observations = load_observations(db, home_id, start, item_id=item_id)
    prepared = analytics.prepare_price_rows(observations)
    stats = analytics.item_stats(observations, today)

    series: dict[str, list[dict[str, Any]]] = {}
    for row in sorted(prepared, key=lambda r: r["date"]):
        if row["price"] is not None:
            series.setdefault(row["location_id"], []).append({"date": row["date"], "price": round(row["price"], 4)})

    # Every purchase is listed with what was paid. A price in a unit the item is not compared in
    # (for example "each" when most purchases were per lb) is still shown, in its own unit.
    history = []
    for r in sorted(prepared, key=lambda r: r["date"], reverse=True)[:50]:
        comparable = r["price"] is not None
        price = r["price"] if comparable else r.get("unit_price")
        history.append({
            "date": r["date"],
            "store": analytics.location_label(r["chain_name"], r["store_number"], r["city"]),
            "price": round(price, 4) if price is not None else None,
            "unit": r["cunit"] if comparable else analytics.display_unit(r.get("unit")),
            "comparable": comparable,
            "total": r["line_total"],
        })
    other_unit = sum(1 for r in prepared if r["price"] is None and r.get("unit_price") is not None)

    return {
        "item": {"id": item.id, "name": item.name, "name_confirmed": bool(item.name_confirmed), "category": item.category, "aliases": aliases},
        "stats": stats[0] if stats else None,
        "stores": analytics.compare_stores(prepared),
        "series": series,
        "history": history,
        "other_unit_purchases": other_unit,
    }


@router.get("/recommendations")
async def recommendations(
    home_id: str,
    days: int | None = None,
    _: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Suggestions from recent prices: where an item is cheaper, unusual prices, and what is due.

    Uses the last ``days`` days (default: the ``recommendation_window_days`` setting) and the
    ``minimum_savings_percent`` setting as the smallest saving worth mentioning.
    """
    _require_home(db, home_id)
    settings = get_settings()
    window = max(7, min(days or settings.recommendation_window_days, 730))
    today = date.today()
    start = _iso(today - timedelta(days=window))

    result = analytics.recommendations(
        load_observations(db, home_id, start), today, window_days=window,
        min_savings_pct=settings.minimum_savings_percent,
    )
    result["window_days"] = window
    result["minimum_savings_percent"] = settings.minimum_savings_percent
    return result


@router.get("/categories")
async def spend_by_category(
    home_id: str,
    range: str = "90d",
    _: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Spend per item category for a period, with the change from the period before.

    ``uncategorized_share`` says how much of the spending is on items with no category yet
    (POST ``/common-items/categorize`` fills most of them in).
    """
    _require_home(db, home_id)
    days = _parse_range(range)
    today = date.today()
    start = _iso(today - timedelta(days=days)) if days else None
    previous = None
    if days:
        previous = load_observations(db, home_id, _iso(today - timedelta(days=2 * days)), start)
    result = insights.spend_by_category(load_observations(db, home_id, start), previous)
    return {"range": range, "start": start, **result}


@router.get("/discounts")
async def discounts(
    home_id: str,
    range: str = "90d",
    _: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """What sales, coupons and member prices saved in a period: total, rate, and by store, month and item."""
    _require_home(db, home_id)
    days = _parse_range(range)
    start = _iso(date.today() - timedelta(days=days)) if days else None
    spend = analytics.summarize_spend(load_receipts(db, home_id, start))["totals"]["spend"]
    return {"range": range, "start": start, **insights.discount_summary(load_discount_lines(db, home_id, start), spend)}


@router.get("/inflation")
async def inflation(
    home_id: str,
    months: int = 12,
    _: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """A personal price index: how the prices of the things you keep buying have moved (starts at 100).

    ``available`` is false until there are at least three items bought in three different months.
    """
    _require_home(db, home_id)
    months = max(3, min(months, 60))
    return {"months": months, **insights.basket_index(load_observations(db, home_id), date.today(), months)}
