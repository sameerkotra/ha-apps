"""Four ways the app looks out for you, from what it already knows (receipts, online prices, your list):

* ``restock``: what you are due to buy again, from how often you buy it
* ``price_drops``: items on your shopping list that are now noticeably cheaper than you usually pay (notified once)
* ``overcharges``: receipt lines where you paid more than the store's own posted price that week (online or a deal)
* ``savings``: cheaper ways to buy what you buy: another store, or another pack size

``run_daily`` and ``run_hourly`` (the scheduler) send the notifications; the pages show the same lists.
"""

from __future__ import annotations

import statistics
import threading
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.db import get_db_session
from app.db.models import Home, ShoppingListItem, WebDeal
from app.logging_config import get_logger
from app.services import analytics, ha, itemquery, webcache, webinfo, webparse, reqcache, notifier
from app.services.analysis_data import load_observations
from app.services.shoplist import price_text

logger = get_logger("lookout")

RESTOCK_SENSOR = "sensor.receipt_restock_due"
MIN_PURCHASES = 3           # to say how often you buy something
HISTORY_DAYS = 365
DROP_MEMORY_DAYS = 7        # the same drop is not notified again for this long
OVERCHARGE_WINDOW_DAYS = 7  # a posted price this close to the purchase counts
OVERCHARGE_TOLERANCE = 0.03
SAVING_MIN_PERCENT = 10.0


def _day(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _skip(row: dict[str, Any]) -> bool:
    """Things that are not bought on a schedule or compared: fees, restaurant dishes, clothing."""
    return bool(itemquery.skip_reason({"name": row.get("item_name") or "", "category": row.get("category"),
                                       "aliases": [row.get("description") or ""], "store_names": [row.get("chain_name") or ""]}))


# --------------------------------------------------------------------------- #
# 1. Restock
# --------------------------------------------------------------------------- #

def restock(db: Session | None, home_id: str, today: date | None = None, observations: list[dict[str, Any]] | None = None,
            on_list: set[str] | None = None) -> list[dict[str, Any]]:
    """Items you buy regularly, with when you are next due to buy them, soonest first.

    An item needs at least three purchases (on different days) with a fairly steady gap: the typical gap is the median,
    and the gaps must not spread more than half of it (median absolute deviation). ``status``: due (today or overdue),
    soon (within 3 days) or later.
    """
    today = today or date.today()
    rows = observations if observations is not None else load_observations(db, home_id, start=(today - timedelta(days=HISTORY_DAYS)).isoformat())
    by_item: dict[str, dict[str, Any]] = {}
    for r in rows:
        d = _day(r.get("date"))
        if d is None or _skip(r):
            continue
        it = by_item.setdefault(r["item_id"], {"name": r.get("item_name"), "days": set(), "stores": Counter()})
        it["days"].add(d)
        it["stores"][r.get("chain_name") or ""] += 1
    if on_list is None:
        on_list = {r.common_item_id for r in db.query(ShoppingListItem).filter(ShoppingListItem.home_id == home_id,
                                                                                ShoppingListItem.checked == 0).all()}
    out = []
    for item_id, it in by_item.items():
        days = sorted(it["days"])
        if len(days) < MIN_PURCHASES:
            continue
        gaps = [(b - a).days for a, b in zip(days, days[1:]) if (b - a).days >= 1]
        if len(gaps) < MIN_PURCHASES - 1:
            continue
        typical = statistics.median(gaps)
        spread = statistics.median(abs(g - typical) for g in gaps)
        if not 2 <= typical <= 120 or spread > typical * 0.5:
            continue  # not bought on any steady schedule
        due = days[-1] + timedelta(days=round(typical))
        days_left = (due - today).days
        if days_left > 30 or -days_left > typical * 3:
            continue  # far off, or you seem to have stopped buying it
        out.append({
            "item_id": item_id, "name": it["name"], "every_days": round(typical), "last_bought": days[-1].isoformat(),
            "due": due.isoformat(), "days_left": days_left, "status": "due" if days_left <= 0 else ("soon" if days_left <= 3 else "later"),
            "regular": spread <= typical * 0.25, "purchases": len(days), "usual_store": it["stores"].most_common(1)[0][0] or None,
            "on_list": item_id in on_list,
        })
    out.sort(key=lambda x: (x["days_left"], x["name"] or ""))
    return out


def restock_daily(home_id: str) -> None:
    """Once a day: publish what is due, notify it, and add it to the shopping list if that option is on."""
    with get_db_session()() as db:
        items = restock(db, home_id)
        due = [i for i in items if i["status"] == "due"]
        home = db.get(Home, home_id)
        home_name = home.name if home else None
        if notifier.settings()["restock_auto_add"] and due:
            from app.services import shoplist
            for i in due:
                if not i["on_list"]:
                    shoplist.add(db, home_id, item_id=i["item_id"], sync=False)
            shoplist.sync_soon(home_id)
    if not ha.available():
        return
    try:
        ha.set_state(RESTOCK_SENSOR, len(due), {
            "friendly_name": "Due to restock", "icon": "mdi:cart-arrow-down", "unit_of_measurement": "items", "home": home_name,
            "items": [{"name": i["name"], "due": i["due"], "every_days": i["every_days"], "status": i["status"], "on_list": i["on_list"]} for i in items[:30]],
        })
    except ha.HAError as e:
        logger.debug("Could not publish %s: %s", RESTOCK_SENSOR, e)
    fresh = [i for i in due if not i["on_list"]]
    if fresh and notifier.enabled("restock"):
        key = webcache._key("restock-notified", home_id, date.today().isoformat())
        if webcache.get(key) is None:
            names = ", ".join(i["name"] for i in fresh[:8]) + (f" and {len(fresh) - 8} more" if len(fresh) > 8 else "")
            added = " (added to your shopping list)" if notifier.settings()["restock_auto_add"] else ""
            if notifier.send("restock", f"Due to restock: {len(fresh)} item{'s' if len(fresh) != 1 else ''}", f"{names}{added}"):
                webcache.put(key, "notified", True, 2 * 86400)


# --------------------------------------------------------------------------- #
# 2. Price drops on your list
# --------------------------------------------------------------------------- #

def usual_prices(rows: list[dict[str, Any]]) -> dict[str, tuple[float, str | None]]:
    """{item id: (median price you paid in the item's unit, that unit)}, for items bought at least twice."""
    by_item: dict[str, list[float]] = defaultdict(list)
    units: dict[str, str | None] = {}
    for r in analytics.prepare_price_rows(rows):
        if r.get("price"):
            by_item[r["item_id"]].append(r["price"])
            units[r["item_id"]] = r.get("cunit")
    return {i: (statistics.median(p), units.get(i)) for i, p in by_item.items() if len(p) >= 2}


def find_drops(list_items: list[dict[str, Any]], places: dict[str, list[dict[str, Any]]],
               usual: dict[str, tuple[float, str | None]], percent: float) -> list[dict[str, Any]]:
    """The list items whose cheapest price now is at least ``percent`` below what you usually pay."""
    out = []
    for r in list_items:
        best = (places.get(r["item_id"]) or [None])[0]
        typical = usual.get(r["item_id"])
        if not best or not typical or not typical[0]:
            continue
        cut = (1 - best["price"] / typical[0]) * 100
        if cut >= percent:
            out.append({"item_id": r["item_id"], "name": r["name"], "store": best["store"], "price": best["price"], "unit": best["unit"],
                        "usual": round(typical[0], 4), "percent": round(cut), "price_source": best["price_source"],
                        "source_url": best.get("source_url")})
    out.sort(key=lambda x: -x["percent"])
    return out


def price_drops(db: Session, home_id: str) -> list[dict[str, Any]]:
    """Items on the shopping list whose cheapest price now (online, deals and store discounts included) is at least
    ``price_drop_percent`` below the median you paid for them over the last six months."""
    from app.services import shoplist
    choice = notifier.settings()
    percent = choice["price_drop_percent"] if choice["kinds"].get("price_drop") else 0
    if percent <= 0:
        return []
    rows = db.query(ShoppingListItem).filter(ShoppingListItem.home_id == home_id, ShoppingListItem.checked == 0,
                                             ShoppingListItem.common_item_id.isnot(None)).all()
    if not rows:
        return []
    ids = [r.common_item_id for r in rows]
    usual = usual_prices(load_observations(db, home_id, start=(date.today() - timedelta(days=180)).isoformat(), item_ids=ids))
    return find_drops([{"item_id": r.common_item_id, "name": r.text} for r in rows], shoplist.cheapest(db, home_id, ids), usual, percent)


@reqcache.scoped
def notify_price_drops(home_id: str) -> int:
    """Notify the new drops (each item, store and price once a week). Returns how many were sent."""
    with get_db_session()() as db:
        drops = price_drops(db, home_id)
    fresh = []
    for d in drops:
        key = webcache._key("price-drop", home_id, d["item_id"], d["store"], round(d["price"], 2))
        if webcache.get(key) is None:
            fresh.append((d, key))
    if not fresh or not ha.available():
        return 0
    lines = []
    for d, _ in fresh[:6]:
        unit = f"/{str(d['unit']).lower()}" if d.get("unit") and d["unit"] != "each" else ""
        lines.append(f"{d['name']}: {price_text(d['price'])}{unit} at {d['store']}{' (online)' if d['price_source'] in ('online', 'deal') else ''}, "
                     f"usually {price_text(d['usual'])} ({d['percent']}% less)")
    if not notifier.send("price_drop", f"Cheaper now: {len(fresh)} item{'s' if len(fresh) != 1 else ''} on your list", "\n".join(lines)):
        return 0
    for _, key in fresh:
        webcache.put(key, "notified", True, DROP_MEMORY_DAYS * 86400)
    return len(fresh)


# --------------------------------------------------------------------------- #
# 3. Paid more than the store posted
# --------------------------------------------------------------------------- #

def compare_with_posted(rows: list[dict[str, Any]], web: dict[tuple[str, str], list[Any]]) -> list[dict[str, Any]]:
    """Receipt lines (prepared rows) against the same store's posted prices (``web``: {(item, chain): [WebDeal-like]})."""
    out = []
    for r in rows:
        bought = _day(r.get("date"))
        if bought is None or not r.get("price") or not r.get("cunit"):
            continue
        best = None
        for w in web.get((r["item_id"], r["chain_id"]), []):
            seen = w.fetched_at.date() if w.fetched_at else None
            if w.kind == "DEAL":
                start_ok = not w.valid_from or str(w.valid_from)[:10] <= bought.isoformat()
                end_ok = not w.valid_to or str(w.valid_to)[:10] >= bought.isoformat()
                if not (start_ok and end_ok and seen and abs((seen - bought).days) <= 21):
                    continue  # the deal did not run that day
            elif not seen or abs((seen - bought).days) > OVERCHARGE_WINDOW_DAYS:
                continue  # posted too long before or after to say what it cost that day
            posted = webparse.normalize_web_price(w.product, w.price, w.unit, r["cunit"])
            if posted and (best is None or posted < best[0]):
                best = (posted, w)
        if best is None:
            continue
        posted, w = best
        if r["price"] <= posted * (1 + OVERCHARGE_TOLERANCE) + 0.05:
            continue  # the same, give or take rounding
        qty = (r.get("line_total") or 0) / r["price"] if r.get("line_total") else 1.0
        extra = (r["price"] - posted) * qty
        if extra < 0.1:
            continue
        out.append({"receipt_id": r.get("receipt_id"), "date": bought.isoformat(), "store": r.get("chain_name"), "item_id": r["item_id"],
                    "item": r.get("item_name"), "description": r.get("description"), "paid": round(r["price"], 4), "posted": round(posted, 4),
                    "unit": r["cunit"], "extra": round(extra, 2), "posted_on": w.fetched_at.date().isoformat() if w.fetched_at else None,
                    "kind": w.kind, "product": w.product, "source_url": w.source_url})
    out.sort(key=lambda x: (x["date"], x["extra"]), reverse=True)
    return out


def overcharges(db: Session, home_id: str, receipt_id: str | None = None, days: int = 45,
                prepared: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Receipt lines where you paid noticeably more than the same store's price posted online (or a deal it ran) around
    the purchase: {receipt_id, date, store, item, paid, posted, unit, extra, posted_on, kind, product, source_url}.
    ``prepared``: receipt rows already read and prepared (``summary`` reads them once for both lists)."""
    start = None if receipt_id else (date.today() - timedelta(days=days)).isoformat()
    if prepared is None:
        prepared = analytics.prepare_price_rows(load_observations(db, home_id, start=start))
    rows = [r for r in prepared if (not receipt_id or r.get("receipt_id") == receipt_id)
            and (receipt_id or str(r.get("date") or "")[:10] >= start)]
    if not rows:
        return []
    pairs = {(r["item_id"], r["chain_id"]) for r in rows}
    ignored = webinfo.ignored_pairs(db, home_id)
    web: dict[tuple[str, str], list[Any]] = defaultdict(list)
    for w in db.query(WebDeal).filter(WebDeal.home_id == home_id).all():
        pair = (w.common_item_id, w.store_chain_id)
        if pair in pairs and pair not in ignored:
            web[pair].append(w)
    return compare_with_posted(rows, web)


def check_receipt_soon(home_id: str, receipt_id: str) -> None:
    """After a receipt is approved: notify lines that cost more than the store posted (in the background)."""
    def work() -> None:
        try:
            with get_db_session()() as db:
                found = overcharges(db, home_id, receipt_id)
            if not found:
                return
            total = sum(f["extra"] for f in found)
            lines = [f"{f['item']}: paid {price_text(f['paid'])}, posted {price_text(f['posted'])} ({f['posted_on']})" for f in found[:6]]
            notifier.send("overcharge", f"{found[0]['store']}: you may have paid ${total:.2f} more than posted",
                          "\n".join(lines) + "\nMany stores refund the difference if you ask.")
        except Exception:  # noqa: BLE001 - never affects approving the receipt
            logger.exception("Receipt price check failed")

    threading.Thread(target=work, daemon=True).start()


# --------------------------------------------------------------------------- #
# 4. Cheaper ways to buy what you buy
# --------------------------------------------------------------------------- #

def find_savings(rows: list[dict[str, Any]], online_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """From prepared receipt rows and online prices (deal offers): cheaper stores and pack sizes, biggest saving first."""
    rows = [r for r in rows if r.get("price") and not _skip(r)]
    by_item: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_item[r["item_id"]].append(r)
    online_by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for o in online_rows:
        online_by[o["item_id"]].append(o)
    out = []
    for item_id, irows in by_item.items():
        if len(irows) < 2:
            continue
        name, unit = irows[0].get("item_name"), irows[0].get("cunit")
        # another store: the store you buy it at most, against the others (your receipts and their online prices)
        per_chain: dict[str, list[float]] = defaultdict(list)
        chain_names: dict[str, str] = {}
        for r in irows:
            per_chain[r["chain_id"]].append(r["price"])
            chain_names[r["chain_id"]] = r.get("chain_name") or ""
        usual_chain = Counter(r["chain_id"] for r in irows).most_common(1)[0][0]
        usual = statistics.median(per_chain[usual_chain])
        options = [(statistics.median(p), chain_names[c], "receipt") for c, p in per_chain.items() if c != usual_chain]
        # online prices only when they are in the same unit as what you paid (never per egg against per dozen)
        options += [(o["price"], o.get("chain") or "", "online") for o in online_by.get(item_id, [])
                    if o["chain_id"] != usual_chain and o.get("price") and str(o.get("unit") or unit or "").upper() == str(unit or "").upper()]
        if options and usual > 0:
            price, store, source = min(options)
            cut = (1 - price / usual) * 100
            if cut >= SAVING_MIN_PERCENT:
                out.append({"kind": "store", "item_id": item_id, "item": name, "unit": unit, "percent": round(cut),
                            "now": {"store": chain_names[usual_chain], "price": round(usual, 4)},
                            "better": {"store": store, "price": round(price, 4), "source": source}})
        # another pack size: the per-size prices the receipts give when you bought it in different sizes
        per_size: dict[str, list[float]] = defaultdict(list)
        for r in irows:
            if r.get("pack_normalized"):
                pack = analytics.parse_pack_size(r.get("description"))
                if pack:
                    per_size[f"{pack['amount']:g} {pack['unit'].lower()}"].append(r["price"])
        if len(per_size) >= 2:
            usual_size = max(per_size, key=lambda k: len(per_size[k]))
            usual_p = statistics.median(per_size[usual_size])
            best_size, best_p = min(((k, statistics.median(v)) for k, v in per_size.items() if k != usual_size), key=lambda x: x[1])
            cut = (1 - best_p / usual_p) * 100 if usual_p else 0
            same = next((x for x in out if x["item_id"] == item_id and x["kind"] == "store"), None)
            if same and abs(same["percent"] - round(cut)) <= 3:
                # the cheaper store is cheaper because of the bigger pack: one suggestion that says both
                same["now"]["size"], same["better"]["size"] = usual_size, best_size
            elif cut >= SAVING_MIN_PERCENT:
                out.append({"kind": "pack", "item_id": item_id, "item": name, "unit": unit, "percent": round(cut),
                            "now": {"size": usual_size, "price": round(usual_p, 4)}, "better": {"size": best_size, "price": round(best_p, 4)}})
    out.sort(key=lambda x: -x["percent"])
    return out


def summary(db: Session, home_id: str) -> dict[str, Any]:
    """Both lists of the Best prices page from one read of six months of receipts: {savings, overcharges}."""
    prepared = analytics.prepare_price_rows(load_observations(db, home_id, start=(date.today() - timedelta(days=180)).isoformat()))
    return {"savings": find_savings(prepared, webinfo.deal_offers(db, home_id, list({r["item_id"] for r in prepared}))),
            "overcharges": overcharges(db, home_id, prepared=prepared)}


def savings(db: Session, home_id: str) -> list[dict[str, Any]]:
    """Cheaper ways to buy the things you buy regularly (last six months): another store at least 10% cheaper per unit
    than where you usually buy it, or another pack size at least 10% cheaper per unit than the one you usually buy."""
    rows = analytics.prepare_price_rows(load_observations(db, home_id, start=(date.today() - timedelta(days=180)).isoformat()))
    return find_savings(rows, webinfo.deal_offers(db, home_id, list({r["item_id"] for r in rows})))


# --------------------------------------------------------------------------- #
# The scheduler
# --------------------------------------------------------------------------- #

def _homes() -> list[str]:
    with get_db_session()() as db:
        return [h.id for h in db.query(Home).all()]


@reqcache.scoped
def run_daily() -> None:
    for home_id in _homes():
        try:
            restock_daily(home_id)
        except Exception:  # noqa: BLE001 - keep going for the other homes
            logger.exception("Restock check failed")


@reqcache.scoped
def run_hourly() -> None:
    if not notifier.enabled("price_drop"):
        return
    with get_db_session()() as db:
        homes = [h for (h,) in db.query(ShoppingListItem.home_id).filter(ShoppingListItem.checked == 0).distinct().all()]
    for home_id in homes:
        try:
            notify_price_drops(home_id)
        except Exception:  # noqa: BLE001
            logger.exception("Price drop check failed")
