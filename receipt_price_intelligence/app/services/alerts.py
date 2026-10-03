"""Alerts, price targets, and the sensors published to Home Assistant.

After each check of a home's prices (daily, and whenever a receipt is saved) this module

* checks the household's price targets ("tell me when eggs are below $3", "tell me about a new low"),
* looks for events worth mentioning in the check's results (a much cheaper store, a big price drop,
  something due for restock),
* records each as an alert (every event only once), and passes new ones to Home Assistant as an
  event and a notification, and
* refreshes the sensors that summarise the home.

Everything Home Assistant related is best effort and skipped when it is not available.
"""

import json
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Alert, CommonItem, Home, PriceTarget, Receipt, StoreChain, StoreLocation
from app.logging_config import get_logger
from app.services import analytics, budgets as budget_service, ha, insights, webinfo, notifier
from app.services.analysis_data import load_observations, load_receipts

logger = get_logger("alerts")

EVENT_TYPE = "receipt_price_intelligence_alert"
RECENT_DAYS = 30        # a price must have been seen this recently to trigger a target
NEW_LOW_HISTORY = 180   # a "new low" is measured against this many days
NEW_LOW_MIN_HISTORY = 3


class AlertError(ValueError):
    """Something a person can fix (a bad target). The message is safe to show."""


# --------------------------------------------------------------------------- #
# Alerts
# --------------------------------------------------------------------------- #

def _money(value: float, currency: str | None = None) -> str:
    return f"{value:,.2f}" if not currency else f"{value:,.2f} {currency}"


def create_alert(db: Session, home_id: str, kind: str, key: str, title: str, message: str,
                 data: dict[str, Any] | None = None) -> Alert | None:
    """Record an alert. Returns None if an alert with this key already exists for the home."""
    if db.query(Alert.id).filter(Alert.home_id == home_id, Alert.dedupe_key == key).first():
        return None
    alert = Alert(home_id=home_id, kind=kind, dedupe_key=key[:255], title=title[:255], message=message,
                  data=json.dumps(data or {}))
    db.add(alert)
    try:
        db.commit()
    except IntegrityError:  # raised by a parallel run
        db.rollback()
        return None
    db.refresh(alert)
    return alert


def alert_json(alert: Alert) -> dict[str, Any]:
    return {
        "id": alert.id, "kind": alert.kind, "title": alert.title, "message": alert.message,
        "data": json.loads(alert.data) if alert.data else {}, "dismissed": bool(alert.dismissed),
        "created_at": alert.created_at.isoformat() + "Z" if alert.created_at else None,
    }


def list_alerts(db: Session, home_id: str, include_dismissed: bool = False, limit: int = 30) -> list[dict[str, Any]]:
    query = db.query(Alert).filter(Alert.home_id == home_id)
    if not include_dismissed:
        query = query.filter(Alert.dismissed == 0)
    return [alert_json(a) for a in query.order_by(Alert.created_at.desc()).limit(max(1, min(limit, 200))).all()]


def dismiss_alert(db: Session, alert_id: str) -> bool:
    alert = db.get(Alert, alert_id)
    if alert is None:
        return False
    alert.dismissed = 1
    db.commit()
    return True


def dispatch(alerts: list[Alert], home_name: str) -> None:
    """Tell Home Assistant about new alerts: an event (for automations) and a notification."""
    settings = get_settings()
    if not alerts or not settings.alerts_enabled or not ha.available():
        return
    for alert in alerts:
        try:
            ha.fire_event(EVENT_TYPE, {"home": home_name, "kind": alert.kind, "title": alert.title,
                                       "message": alert.message, "data": json.loads(alert.data or "{}")})
            notifier.send("alerts", alert.title, alert.message)
        except ha.HAError as e:
            logger.warning("Could not send alert to Home Assistant: %s", e)
            return  # the rest would fail the same way


# --------------------------------------------------------------------------- #
# Price targets
# --------------------------------------------------------------------------- #

def _item_unit(db: Session, home_id: str, item_id: str) -> str:
    rows = analytics.prepare_price_rows(load_observations(db, home_id, item_ids=[item_id]))
    return next((r["cunit"] for r in rows if r.get("cunit")), None) or "each"


def create_target(db: Session, home_id: str, item_id: str, mode: str, price: float | None) -> PriceTarget:
    item = db.get(CommonItem, item_id)
    if item is None or item.home_id != home_id or item.status != "ACTIVE":
        raise AlertError("That item was not found")
    mode = (mode or "BELOW").upper()
    if mode not in ("BELOW", "NEW_LOW"):
        raise AlertError("Choose a price below a number, or a new low")
    if mode == "BELOW":
        if price is None or price <= 0:
            raise AlertError("Enter the price you want to be told about")
        if price > 100000:
            raise AlertError("That price looks too large")
    target = PriceTarget(
        home_id=home_id, common_item_id=item_id, mode=mode,
        target_price=float(price) if mode == "BELOW" else None,
        unit=_item_unit(db, home_id, item_id), active=1,
    )
    db.add(target)
    db.commit()
    db.refresh(target)
    return target


def delete_target(db: Session, target_id: str) -> bool:
    target = db.get(PriceTarget, target_id)
    if target is None:
        return False
    db.delete(target)
    db.commit()
    return True


def _prices_by_item(db: Session, home_id: str, item_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    rows = analytics.prepare_price_rows(load_observations(db, home_id, item_ids=item_ids))
    out: dict[str, list[dict[str, Any]]] = {}
    for r in sorted(rows, key=lambda r: r["date"]):
        if r["price"] is not None:
            out.setdefault(r["item_id"], []).append(r)
    return out


def _best_recent(rows: list[dict[str, Any]], today: date) -> dict[str, Any] | None:
    """The lowest of the latest prices at each store seen in the last month."""
    latest: dict[str, dict[str, Any]] = {}
    cutoff = (today - timedelta(days=RECENT_DAYS)).isoformat()
    for r in rows:
        if r["date"] >= cutoff:
            latest[r["location_id"]] = r
    return min(latest.values(), key=lambda r: r["price"]) if latest else None


def _price_text(price: float, unit: str | None) -> str:
    text = f"{price:.3f}" if price < 1 else f"{price:.2f}"
    return text + (f"/{unit}" if unit and unit != "each" else "")


def list_targets(db: Session, home_id: str, today: date | None = None) -> list[dict[str, Any]]:
    """The home's price targets with the item's current best recent price for comparison."""
    today = today or date.today()
    targets = db.query(PriceTarget).filter(PriceTarget.home_id == home_id).order_by(PriceTarget.created_at).all()
    if not targets:
        return []
    prices = _prices_by_item(db, home_id, [t.common_item_id for t in targets])
    out = []
    for t in targets:
        item = db.get(CommonItem, t.common_item_id)
        best = _best_recent(prices.get(t.common_item_id, []), today)
        current = None
        if best:
            cunit = best["cunit"]
            current = {"price": round(best["price"], 4), "unit": cunit, "date": best["date"],
                       "store": analytics.location_label(best["chain_name"], best["store_number"], best["city"])}
        out.append({
            "id": t.id, "item_id": t.common_item_id, "item_name": item.name if item else "Unknown item",
            "mode": t.mode, "target_price": t.target_price, "unit": t.unit, "current": current,
            "last_triggered_at": t.last_triggered_at.isoformat() + "Z" if t.last_triggered_at else None,
        })
    return out


def evaluate_targets(db: Session, home_id: str, today: date | None = None) -> list[Alert]:
    """Raise an alert for each target reached by a price seen recently. Each store and day only once."""
    today = today or date.today()
    targets = db.query(PriceTarget).filter(PriceTarget.home_id == home_id, PriceTarget.active == 1).all()
    if not targets:
        return []
    prices = _prices_by_item(db, home_id, [t.common_item_id for t in targets])
    new: list[Alert] = []

    for t in targets:
        item = db.get(CommonItem, t.common_item_id)
        rows = prices.get(t.common_item_id, [])
        if item is None or not rows:
            continue

        alert = None
        if t.mode == "BELOW":
            best = _best_recent(rows, today)
            if best is None:
                continue
            limit = analytics.convert_price(t.target_price, t.unit, best["cunit"])
            if limit is not None and best["price"] <= limit + 1e-9:
                store = analytics.location_label(best["chain_name"], best["store_number"], best["city"])
                alert = create_alert(
                    db, home_id, "PRICE_TARGET", f"target:{t.id}:{best['location_id']}:{best['date']}",
                    f"{item.name} is at your target price",
                    f"{store} had {item.name} at {_price_text(best['price'], best['cunit'])} on {best['date']}, "
                    f"at or below your target of {_price_text(t.target_price, t.unit)}.",
                    {"item_id": item.id, "store": store, "price": best["price"], "unit": best["cunit"], "date": best["date"]},
                )
        else:  # NEW_LOW
            last = rows[-1]
            if (today - date.fromisoformat(last["date"])).days > RECENT_DAYS:
                continue
            since = (date.fromisoformat(last["date"]) - timedelta(days=NEW_LOW_HISTORY)).isoformat()
            earlier = [r["price"] for r in rows if since <= r["date"] < last["date"]]
            if len(earlier) >= NEW_LOW_MIN_HISTORY and last["price"] < min(earlier) - 1e-9:
                store = analytics.location_label(last["chain_name"], last["store_number"], last["city"])
                alert = create_alert(
                    db, home_id, "NEW_LOW", f"low:{t.id}:{last['location_id']}:{last['date']}",
                    f"New low for {item.name}",
                    f"{store} had {item.name} at {_price_text(last['price'], last['cunit'])} on {last['date']}, "
                    f"the lowest in {NEW_LOW_HISTORY} days (before: {_price_text(min(earlier), last['cunit'])}).",
                    {"item_id": item.id, "store": store, "price": last["price"], "unit": last["cunit"], "date": last["date"]},
                )
        if alert is not None:
            t.last_triggered_at = datetime.utcnow()
            db.commit()
            new.append(alert)
    return new


# --------------------------------------------------------------------------- #
# Events in the daily check
# --------------------------------------------------------------------------- #

def evaluate_snapshot(db: Session, home_id: str, payload: dict[str, Any]) -> list[Alert]:
    """Alerts for a much cheaper store, a big price drop, and items overdue for restock."""
    minimum = get_settings().alert_min_saving_percent
    new: list[Alert] = []

    def add(*args: Any, **kwargs: Any) -> None:
        alert = create_alert(db, home_id, *args, **kwargs)
        if alert is not None:
            new.append(alert)

    for s in payload.get("switch_store") or []:
        if s["saving_pct"] >= minimum and s.get("confidence", 0) >= 0.3:
            saving = f", about {s['est_monthly_savings']:.2f} a month" if s.get("est_monthly_savings") else ""
            add("SAVING", f"saving:{s['item_id']}:{s['to']['label']}:{s['to']['price']:.2f}",
                f"{s['item_name']} is {s['saving_pct']:.0f}% cheaper at {s['to']['label']}",
                f"{s['to']['label']} charged {_price_text(s['to']['price'], s['unit'])} against "
                f"{_price_text(s['from']['price'], s['unit'])} at {s['from']['label']}, where you usually buy it{saving}.",
                {"item_id": s["item_id"], "saving_pct": s["saving_pct"], "to": s["to"]["label"], "from": s["from"]["label"]})

    for a in payload.get("price_alerts") or []:
        if a["type"] == "PRICE_DOWN" and abs(a["change_pct"]) >= minimum:
            add("PRICE_DROP", f"drop:{a['item_id']}:{a['store']}:{a['date']}",
                f"{a['item_name']} dropped {abs(a['change_pct']):.0f}%",
                f"{a['store']} had it at {_price_text(a['price'], a['unit'])}, usually {_price_text(a['usual_price'], a['unit'])}.",
                {"item_id": a["item_id"], "store": a["store"], "price": a["price"]})

    for r in payload.get("restock") or []:
        if r["status"] == "overdue":
            add("RESTOCK", f"restock:{r['item_id']}:{r['last_date']}",
                f"Time to buy {r['item_name']}?",
                f"You usually buy it every {r['avg_interval_days']:.0f} days. It has been {r['days_since_last']}.",
                {"item_id": r["item_id"]})
    return new


# --------------------------------------------------------------------------- #
# Sensors
# --------------------------------------------------------------------------- #

def evaluate_web_deals(db: Session, home_id: str) -> list[Alert]:
    """Alerts for deals found online that are well below what you usually pay at that store (or under a target)."""
    settings = get_settings()
    currency = settings.default_currency
    cutoff = (date.today() - timedelta(days=3)).isoformat()
    new: list[Alert] = []
    for d in webinfo.deal_rows(db, home_id):
        if d["kind"] != "DEAL" or (d["fetched_at"] or "")[:10] < cutoff or d["saving_pct"] is None or d["saving_pct"] < settings.alert_min_saving_percent:
            continue
        until = f", until {d['valid_to']}" if d["valid_to"] else ""
        per = "" if d["unit"] == "each" else "/" + d["unit"]
        message = "{} is {}{} at {} (you usually pay {}){}.{}".format(
            d["item_name"], _money(d["price"], currency), per, d["chain"], _money(d["usual_price"], currency), until,
            " " + d["conditions"] + "." if d["conditions"] else "")
        alert = create_alert(db, home_id, "WEB_DEAL", "webdeal:{}".format(d["id"]),
                             "{} deal at {}: {:.0f}% less".format(d["item_name"], d["chain"], d["saving_pct"]), message,
                             {"item_id": d["item_id"], "chain": d["chain"], "price": d["price"], "source": d["source_url"]})
        if alert:
            new.append(alert)
    return new


def evaluate_budgets(db: Session, home_id: str, today: date | None = None) -> list[Alert]:
    """Alerts when a monthly budget is nearly used up (from 80%) or exceeded, once each per month."""
    today = today or date.today()
    currency = get_settings().default_currency
    new: list[Alert] = []
    for b in budget_service.list_budgets(db, home_id, today):
        if b["status"] == "ok" and b["percent"] < insights.BUDGET_WARN_PCT:
            continue
        over = b["spent"] > b["amount"]
        if not over and b["percent"] < insights.BUDGET_WARN_PCT:
            continue  # on course to overshoot, but nothing is used up yet: not worth an alert
        name = b["category"] or "Monthly"
        level = "over" if over else "warn"
        used = "exceeded" if over else "{:.0f}% used".format(b["percent"])
        title = "{} budget {}".format(name, used)
        left = ", {} days left".format(b["days_left"]) if b["days_left"] else ""
        message = "You have spent {} of {} this month{}.".format(_money(b["spent"], currency), _money(b["amount"], currency), left)
        if not over and b["projected"] > b["amount"]:
            message += " At this pace the month ends near {}.".format(_money(b["projected"], currency))
        alert = create_alert(db, home_id, "BUDGET_OVER" if over else "BUDGET_WARN", f"budget:{b['id']}:{today:%Y-%m}:{level}", title, message,
                             {"category": b["category"], "spent": b["spent"], "amount": b["amount"], "percent": b["percent"]})
        if alert:
            new.append(alert)
    return new


def _month_start(d: date) -> date:
    return d.replace(day=1)


def publish_sensors(db: Session, home_id: str, payload: dict[str, Any], today: date | None = None) -> bool:
    """Create or update the Home Assistant sensors for a home. Returns False if they were skipped."""
    settings = get_settings()
    if not settings.ha_sensors or not ha.available():
        return False
    home = db.get(Home, home_id)
    if home is None:
        return False
    today = today or date.today()
    base = f"sensor.receipts_{ha.slug(home.name)}"
    currency = settings.default_currency

    this_month = _month_start(today)
    last_month = _month_start(this_month - timedelta(days=1))
    now = analytics.summarize_spend(load_receipts(db, home_id, this_month.isoformat()))["totals"]
    before = analytics.summarize_spend(load_receipts(db, home_id, last_month.isoformat(), this_month.isoformat()))["totals"]
    change = round((now["spend"] - before["spend"]) / before["spend"] * 100, 1) if before["spend"] else None

    latest = (
        db.query(Receipt, StoreChain).join(StoreLocation, Receipt.store_location_id == StoreLocation.id)
        .join(StoreChain, StoreLocation.store_chain_id == StoreChain.id)
        .filter(Receipt.home_id == home_id, Receipt.purchase_date.isnot(None))
        .order_by(Receipt.purchase_date.desc()).first()
    )
    recent_alerts = list_alerts(db, home_id, limit=5)
    summary = payload.get("summary") or {}
    switches = payload.get("switch_store") or []
    due = payload.get("restock") or []

    sensors = {
        f"{base}_spend_this_month": (now["spend"], {
            "friendly_name": f"{home.name} spend this month", "unit_of_measurement": currency, "icon": "mdi:receipt-text",
            "trips": now["trips"], "average_basket": now["avg_basket"], "last_month": before["spend"], "change_percent": change}),
        f"{base}_potential_savings": (summary.get("est_monthly_savings") or 0, {
            "friendly_name": f"{home.name} possible savings", "unit_of_measurement": currency, "icon": "mdi:piggy-bank",
            "suggestions": [{"item": s["item_name"], "shop_at": s["to"]["label"], "saving_percent": s["saving_pct"]} for s in switches[:5]],
            "count": len(switches)}),
        f"{base}_items_due": (len(due), {
            "friendly_name": f"{home.name} items due", "icon": "mdi:cart-arrow-down",
            "items": [r["item_name"] for r in due[:15]]}),
        f"{base}_price_alerts": (len(recent_alerts), {
            "friendly_name": f"{home.name} price alerts", "icon": "mdi:tag-heart",
            "latest": [a["title"] for a in recent_alerts]}),
    }
    if latest:
        receipt, chain = latest
        sensors[f"{base}_last_receipt"] = (receipt.purchase_date, {
            "friendly_name": f"{home.name} last receipt", "icon": "mdi:receipt", "store": chain.name, "total": receipt.grand_total})

    over = [b for b in budget_service.list_budgets(db, home_id, today) if b["status"] != "ok"]
    sensors[f"{base}_budgets_over"] = (len(over), {
        "friendly_name": f"{home.name} budgets to watch", "icon": "mdi:wallet-outline",
        "budgets": [{"category": b["category"] or "Monthly", "spent": b["spent"], "amount": b["amount"], "status": b["status"]} for b in over]})
    index = insights.basket_index(load_observations(db, home_id), today, 12)
    if index.get("available"):
        sensors[f"{base}_basket_change"] = (index["change_pct"], {
            "friendly_name": f"{home.name} price change (12 months)", "unit_of_measurement": "%", "icon": "mdi:trending-up",
            "items_in_basket": index["items_used"], "biggest_rises": [f"{m['name']} {m['change_pct']:+.0f}%" for m in index["risers"][:3]]})

    try:
        for entity_id, (state, attributes) in sensors.items():
            ha.set_state(entity_id, state, attributes)
    except ha.HAError as e:
        logger.warning("Could not publish sensors: %s", e)
        return False
    return True


# --------------------------------------------------------------------------- #
# One call for the daily check
# --------------------------------------------------------------------------- #

def process_home(db: Session, home_id: str, payload: dict[str, Any], announce: bool = True) -> int:
    """Evaluate targets and results, announce new alerts, and refresh the sensors. Returns the alerts raised.

    ``announce`` is False the first time a home is checked, so a long-standing situation is recorded
    without flooding notifications.
    """
    home = db.get(Home, home_id)
    new = evaluate_targets(db, home_id) + evaluate_snapshot(db, home_id, payload) + evaluate_budgets(db, home_id) + evaluate_web_deals(db, home_id)
    if announce and home is not None:
        dispatch(new, home.name)
    publish_sensors(db, home_id, payload)
    return len(new)
