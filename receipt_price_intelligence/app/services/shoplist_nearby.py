""""Items on your list are cheapest here": a notification when one of your phones is at a store where items on your
shopping list cost least.

Every minute, each phone chosen on the Notifications page is followed by its own location tracker (the Home Assistant
companion app keeps ``device_tracker.<phone>`` up to date; see ``watchers``). A phone within the chosen distance of a
store where items on the list (not yet ticked off) are cheapest gets one notification, on that phone only, listing
just those items; a ``receipt_price_intelligence_nearby_cheapest`` event is fired for automations. Each phone is told
once per visit: not again until it has been well away (twice the distance), and at most once every few hours per store.
With no phone chosen, the person or tracker chosen on the page is followed instead.
"""

from __future__ import annotations

import time
from typing import Any

from app.db import get_db_session
from app.db.models import ShoppingListItem
from app.logging_config import get_logger
from app.services import ha, planner, shoplist, reqcache, notifier

logger = get_logger("shoplist_nearby")

EVENT = "receipt_price_intelligence_nearby_cheapest"
COOLDOWN_SECONDS = 4 * 3600
MAX_ACCURACY_METERS = 500  # a position less sure than this is not used

_visits: dict[tuple[str, str], dict[str, Any]] = {}  # (home, store) -> {"inside": bool, "notified": time}


def _distance_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    return planner.haversine_km(a, b) * 1000


def position_of(state: dict[str, Any] | None) -> tuple[float, float] | None:
    """Where a tracker is, if Home Assistant knows it precisely enough."""
    attrs = (state or {}).get("attributes") or {}
    lat, lng = attrs.get("latitude"), attrs.get("longitude")
    if lat is None or lng is None or (attrs.get("gps_accuracy") or 0) > MAX_ACCURACY_METERS:
        return None
    return float(lat), float(lng)


def watchers(states: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Who "cheapest here" follows: each phone chosen on the Notifications page, by its own location tracker (and
    notified alone when it is at a store). Only when no phone is chosen, the person or tracker chosen there instead,
    notified wherever notifications go."""
    choice = notifier.settings()
    out = []
    for service in choice["devices"]:
        tracker = ha.tracker_for(service, states)
        if tracker:
            out.append({"service": service, "tracker": tracker})
    if not out and choice.get("tracker"):
        out.append({"service": None, "tracker": choice["tracker"]})
    return out


def position() -> tuple[float, float] | None:
    """Where the first watched phone (or person) is: for the List page's test notification."""
    if not ha.available():
        return None
    states = ha.all_states()
    by_id = {s.get("entity_id"): s for s in states}
    for w in watchers(states):
        where = position_of(by_id.get(w["tracker"]))
        if where:
            return where
    return None


def cheapest_here(home_id: str) -> dict[str, dict[str, Any]]:
    """{store id: {store, lat, lng, items: [{name, qty, price, unit, price_source}]}} for the stores where items on the list
    (not ticked off) are cheapest."""
    with get_db_session()() as db:
        rows = db.query(ShoppingListItem).filter(ShoppingListItem.home_id == home_id, ShoppingListItem.checked == 0,
                                                 ShoppingListItem.common_item_id.isnot(None)).all()
        places = shoplist.cheapest(db, home_id, [r.common_item_id for r in rows])
    stores: dict[str, dict[str, Any]] = {}
    for r in rows:
        best = (places.get(r.common_item_id) or [None])[0]
        if not best or best.get("lat") is None or best.get("lng") is None:
            continue
        s = stores.setdefault(best["store_id"], {"store": best["store"], "lat": best["lat"], "lng": best["lng"], "items": []})
        s["items"].append({"name": r.text, "qty": r.qty, "price": best["price"], "unit": best["unit"], "price_source": best["price_source"]})
    return stores


def message(store: dict[str, Any]) -> tuple[str, str]:
    items = store["items"]
    title = f"{store['store']}: {len(items)} item{'s' if len(items) != 1 else ''} on your list {'are' if len(items) != 1 else 'is'} cheapest here"
    parts = []
    for i in items[:6]:
        unit = f"/{str(i['unit']).lower()}" if i.get("unit") and i["unit"] != "each" else ""
        parts.append(f"{i['name']} {shoplist.price_text(i['price'])}{unit}{' (online)' if i['price_source'] in ('online', 'deal') else ''}")
    more = f" and {len(items) - 6} more" if len(items) > 6 else ""
    return title, " · ".join(parts) + more


def check_home(home_id: str, where: tuple[float, float], watcher: dict[str, Any] | None = None) -> list[str]:
    """Notify ``watcher`` (a phone, or everyone) for the stores of this home it is at now. Only items on the shopping
    list, not yet ticked off, whose cheapest store this is, are told about. Returns the stores notified."""
    watcher = watcher or {"service": None, "tracker": "-"}
    meters = notifier.settings()["nearby_meters"]
    notified = []
    for store_id, store in cheapest_here(home_id).items():
        key = (home_id, store_id, watcher["tracker"])  # each phone has its own visits
        visit = _visits.setdefault(key, {"inside": False, "notified": 0.0})
        distance = _distance_m(where, (store["lat"], store["lng"]))
        if distance <= meters:
            if not visit["inside"] and time.time() - visit["notified"] > COOLDOWN_SECONDS:
                title, text = message(store)
                try:
                    if not notifier.send("nearby", title, text, devices=[watcher["service"]] if watcher["service"] else None):
                        continue  # switched off or not sent: try again next minute
                    ha.fire_event(EVENT, {"store": store["store"], "items": store["items"], "distance_m": round(distance),
                                          "device": watcher["service"], "tracker": watcher["tracker"]})
                    visit["notified"] = time.time()
                    notified.append(store["store"])
                    logger.info("Sent 'cheapest here' for %s (%d m away, %d items)", store["store"], distance, len(store["items"]))
                except ha.HAError as e:
                    logger.warning("Could not send the 'cheapest here' notification: %s", e)
            visit["inside"] = True
        elif distance > 2 * meters:
            visit["inside"] = False  # well away: the next visit notifies again
    return notified


@reqcache.scoped
def check_all() -> None:
    """The minute loop: every home's list against where each watched phone is (one read of Home Assistant)."""
    choice = notifier.settings()
    if not choice["kinds"].get("nearby") or not ha.available():
        return
    try:
        states = ha.all_states()
    except ha.HAError as e:
        logger.debug("Could not read positions: %s", e)
        return
    by_id = {s.get("entity_id"): s for s in states}
    found = [(w, position_of(by_id.get(w["tracker"]))) for w in watchers(states)]
    found = [(w, where) for w, where in found if where]
    if not found:
        return
    with get_db_session()() as db:
        homes = [h for (h,) in db.query(ShoppingListItem.home_id).filter(ShoppingListItem.checked == 0).distinct().all()]
    for home_id in homes:
        for w, where in found:
            try:
                check_home(home_id, where, w)
            except Exception:  # noqa: BLE001 - keep checking the others
                logger.exception("Nearby check failed")
