"""A home's shopping list: items you pick from what you have bought before or type in, each with the store where it is
cheapest right now (the same prices the trip planner uses: the store's online price when found, else your receipt).

It is also published to Home Assistant, for dashboards:

* ``sensor.receipt_shopping_list`` (state: items left to buy; attributes: the items with their cheapest store, and
  the list grouped by store), republished after every change and every few minutes
* optionally synced both ways with a Home Assistant to-do list (``shopping_list_todo_entity``, e.g.
  ``todo.shopping_list``): items added or ticked off on either side show on the other, and each to-do item's
  description says where it is cheapest

``nearby`` (see ``shoplist_nearby.py``) uses ``cheapest_by_store`` to notify you when you are at a store where items
on your list are cheapest.
"""

from __future__ import annotations

import threading
import time
from datetime import date, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.common import sensor_publisher
from app.config import get_settings
from app.db import get_db_session
from app.db.models import CommonItem, Home, ShoppingListItem
from app.logging_config import get_logger
from app.services import discounts, ha, planner, shopping, trips, webinfo, reqcache

logger = get_logger("shoplist")

SENSOR = "sensor.receipt_shopping_list"
MATCH_THRESHOLD = 0.6  # a typed item is linked to a known item this similar
MAX_ITEMS = 200
_sync_lock = threading.Lock()


class ListError(ValueError):
    """Something a person can fix. The message is safe to show."""


# --------------------------------------------------------------------------- #
# Where each item is cheapest
# --------------------------------------------------------------------------- #

_cheapest_kept: dict[tuple, tuple[Any, float, dict]] = {}
_cheapest_lock = threading.Lock()
CHEAPEST_KEEP_SECONDS = 600


def cheapest(db: Session, home_id: str, item_ids: list[str], online: bool = True) -> dict[str, list[dict[str, Any]]]:
    """Where each item is cheapest (see ``_cheapest``). The answer is kept and given again while nothing it depends on
    has changed (receipts, online prices, stores, discounts, ignored prices: the same fingerprint as kept trip plans)
    and for at most 10 minutes, so the minute-by-minute checks ("cheapest here", the to-do sync) cost almost nothing."""
    import copy
    import time

    from app.services import tripcache
    ids = [i for i in dict.fromkeys(item_ids) if i]
    if not ids:
        return {}
    key = (home_id, tuple(sorted(ids)), online)
    stamp = tripcache._data_stamp(db, home_id)
    with _cheapest_lock:
        kept = _cheapest_kept.get(key)
        if kept and kept[0] == stamp and time.time() - kept[1] < CHEAPEST_KEEP_SECONDS:
            return copy.deepcopy(kept[2])
    result = _cheapest(db, home_id, ids, online)
    with _cheapest_lock:
        if len(_cheapest_kept) > 50:
            _cheapest_kept.clear()
        _cheapest_kept[key] = (stamp, time.time(), result)
    return copy.deepcopy(result)


def _cheapest(db: Session, home_id: str, item_ids: list[str], online: bool = True) -> dict[str, list[dict[str, Any]]]:
    """{item id: [stores with a price for it, cheapest first]}: each {store_id, store, chain_id, lat, lng, price, unit,
    price_source (online / deal / receipt), date, source_url}. Online prices come first, as in the trip planner."""
    item_ids = [i for i in dict.fromkeys(item_ids) if i]
    if not item_ids:
        return {}
    locations, observations = trips._relevant_locations(db, home_id, item_ids)
    stores = [{"id": l["id"], "label": l["label"], "chain_id": l["chain_id"], "chain_name": l["chain_name"],
               "address": l["address"], "lat": l["lat"], "lng": l["lng"]} for l in locations]
    today = date.today()
    offers = planner.build_offers(observations, {s["id"]: s["chain_id"] for s in stores}, today, True)
    units = {i: u["unit"] for i, u in planner.usual_amounts(observations).items()}
    try:
        if online:  # else receipt prices only
            planner.apply_deals(offers, webinfo.deal_offers(db, home_id, item_ids), stores, units, today, [])
    except Exception:  # noqa: BLE001 - receipt prices alone still say where it is cheapest
        logger.exception("Could not apply online prices to the shopping list")
    store_discounts = discounts.list_for_home(db, home_id)
    if store_discounts:
        discounts.apply(offers, stores, store_discounts, discounts.fuel_item_ids(observations))
    by_id = {s["id"]: s for s in stores}
    out: dict[str, list[dict[str, Any]]] = {}
    for item_id in item_ids:
        rows = []
        for store_id, offer in (offers.get(item_id) or {}).items():
            store = by_id.get(store_id)
            if not store or not offer:
                continue
            src = planner.price_sources(offer)
            rows.append({"store_id": store_id, "store": store["label"], "chain_id": store["chain_id"], "lat": store["lat"],
                         "lng": store["lng"], "price": round(offer["price"], 4), "unit": offer.get("unit") or units.get(item_id),
                         "price_source": src["price_source"], "date": offer.get("date"),
                         "discount": src["discount"], "before_discount": src["before_discount"],
                         "source_url": (src["online"] or {}).get("url") or offer.get("source_url")})
        rows.sort(key=lambda r: r["price"])
        out[item_id] = rows
    return out


# --------------------------------------------------------------------------- #
# The list
# --------------------------------------------------------------------------- #

def _known_items(db: Session, home_id: str) -> list[dict[str, Any]]:
    """Every item of the home, bought or not (items typed on the list are items too), most bought first."""
    from app.services import items as items_service
    return [{"id": i["id"], "name": i["name"], "purchases": i["purchase_count"]} for i in items_service.list_items(db, home_id, None, 2000)]


def _user_for(db: Session, home_id: str, user_id: str | None) -> str | None:
    """Who a new item belongs to: the person adding it, else the home's creator, else the first user."""
    if user_id:
        return user_id
    from app.db.models import User
    home = db.get(Home, home_id)
    if home is not None and getattr(home, "created_by_user_id", None):
        return home.created_by_user_id
    first = db.query(User).first()
    return first.id if first is not None else None


def ensure_item(db: Session, home_id: str, text: str, user_id: str | None = None, guess: bool = True) -> str | None:
    """The item typed text is: one you already have (same name, or clearly the same), or a new one made for it.

    A new item has no price yet; when you buy it, give its receipt line this name (or pick it) on the Review page and
    from then on it has prices, and the list shows where it is cheapest."""
    from app.services.categories import suggest_category
    from app.services.receipt_records import clean_common_name, find_item_by_name
    name = clean_common_name(text)
    if not name:
        return None
    if name == name.lower():
        name = name[:1].upper() + name[1:]  # "birthday candles" -> "Birthday candles"
    existing = find_item_by_name(db, home_id, name)
    if existing is not None:
        return existing.id  # exactly that name (any case): that item
    if guess:  # (the Home Assistant to-do list, where nobody picks from suggestions)
        matched = _match(db, home_id, name)
        if matched:
            return matched
    owner = _user_for(db, home_id, user_id)
    if owner is None:
        return None
    item = CommonItem(home_id=home_id, user_id=owner, name=name[:255], name_confirmed=1, status="ACTIVE",
                      category=suggest_category(name))
    db.add(item)
    db.flush()
    logger.info("Shopping list: new item %r", name)
    return item.id


def _row_json(row: ShoppingListItem, places: list[dict[str, Any]] | None) -> dict[str, Any]:
    best = places[0] if places else None
    return {
        "id": row.id, "text": row.text, "item_id": row.common_item_id, "qty": row.qty, "checked": bool(row.checked),
        "added_at": row.added_at.isoformat() + "Z" if row.added_at else None,
        "cheapest": best, "also": (places or [])[1:3], "stores_with_price": len(places or []),
    }


def get_list(db: Session, home_id: str, online: bool = True) -> dict[str, Any]:
    """The list with where each item is cheapest, unchecked first, and the same grouped by cheapest store."""
    rows = (db.query(ShoppingListItem).filter(ShoppingListItem.home_id == home_id)
            .order_by(ShoppingListItem.checked, ShoppingListItem.added_at).all())
    unlinked = [r for r in rows if not r.common_item_id]
    for r in unlinked:  # typed before items were made for them: make (or find) them now
        r.common_item_id = ensure_item(db, home_id, r.text)
    if unlinked:
        db.commit()
    places = cheapest(db, home_id, [r.common_item_id for r in rows if r.common_item_id and not r.checked], online)
    items = [_row_json(r, places.get(r.common_item_id) if r.common_item_id and not r.checked else None) for r in rows]
    by_store: dict[str, dict[str, Any]] = {}
    for it in items:
        if it["checked"]:
            continue
        key = it["cheapest"]["store_id"] if it["cheapest"] else ""
        group = by_store.setdefault(key, {"store_id": key or None, "store": it["cheapest"]["store"] if it["cheapest"] else None,
                                          "items": [], "total": 0.0})
        group["items"].append(it["id"])
        if it["cheapest"]:
            group["total"] += it["cheapest"]["price"] * (it["qty"] or 1)
    settings = get_settings()
    groups = sorted(by_store.values(), key=lambda g: (g["store"] is None, -len(g["items"])))
    for g in groups:
        g["total"] = round(g["total"], 2)
    return {"items": items, "by_store": groups, "left": sum(1 for i in items if not i["checked"]), "currency": settings.default_currency,
            "online": online,
            "ha": {"available": ha.available(), "todo_entity": settings.shopping_list_todo_entity or None, "sensor": SENSOR,
                   "nearby": nearby_settings()}}


def nearby_settings() -> dict[str, Any]:
    from app.services import notifier
    n = notifier.settings()
    phones = [d.replace("notify.mobile_app_", "").replace("_", " ").title() for d in n["devices"] if d.startswith("notify.mobile_app_")]
    follows = ", ".join(phones) or n["tracker"]
    return {"tracker": follows, "notify_service": "each phone at the store" if phones else (", ".join(n["devices"]) or "the Home Assistant bell"),
            "meters": n["nearby_meters"], "enabled": bool(n["kinds"].get("nearby") and follows)}


def same_thing(typed: str, item_name: str) -> bool:
    """Whether typed text names the same kind of thing as an item: their main word (the noun the rest describes,
    usually the last) is the same. "milk" is "2% Milk" and "eggs" is "Large Eggs", but "almond" is not "Almond milk"
    (almond only describes the milk) and "chicken" is not "Chicken breast"."""
    from app.services import webparse
    a, b = webparse._head_noun(typed), webparse._head_noun(item_name)
    return bool(a and b and a == b)


def _match(db: Session, home_id: str, text: str) -> str | None:
    """The known item typed text is, if it clearly is one: the closest match whose main word is the same."""
    known = _known_items(db, home_id)
    matched = shopping.match_lines([text], known, MATCH_THRESHOLD)
    if not matched:
        return None
    name = matched[0].get("name") or text
    for candidate in matched[0].get("candidates") or []:
        if same_thing(name, candidate["name"]):
            return candidate["id"]
    return None


def add(db: Session, home_id: str, text: str | None = None, item_id: str | None = None, qty: float = 1.0,
        ha_uid: str | None = None, sync: bool = True, user_id: str | None = None, new: bool = False) -> dict[str, Any]:
    """Add an item: a known one (``item_id``) or typed text, which becomes the item it clearly is, or a new item (so
    that when you buy it, its receipt line can be given that name). Adding something already on the list (not ticked
    off) raises its quantity instead."""
    if db.query(ShoppingListItem).filter(ShoppingListItem.home_id == home_id).count() >= MAX_ITEMS:
        raise ListError(f"The list is full ({MAX_ITEMS} items). Clear bought items first.")
    qty = max(0.01, min(float(qty or 1), 999))
    if item_id:
        item = db.get(CommonItem, item_id)
        if item is None or item.home_id != home_id:
            raise ListError("That item was not found")
        text = item.name
    text = " ".join((text or "").split())[:200]
    if not text:
        raise ListError("Type what to add")
    if not item_id:
        name, typed_qty = shopping.parse_line(text)  # "2 milk", "milk x2"
        if name:
            text, qty = name[:200], round(qty * (typed_qty or 1), 2)
    if not item_id:
        # ``new``: the person chose "add as a new item" instead of a suggestion: only an item with exactly this name
        # is reused, nothing is guessed
        item_id = ensure_item(db, home_id, text, user_id, guess=not new)
        if item_id:
            text = db.get(CommonItem, item_id).name
    same = (db.query(ShoppingListItem).filter(ShoppingListItem.home_id == home_id, ShoppingListItem.checked == 0)
            .filter((ShoppingListItem.common_item_id == item_id) if item_id else (ShoppingListItem.text == text)).first())
    if same is not None:
        same.qty = round(same.qty + qty, 2)
        row = same
    else:
        row = ShoppingListItem(home_id=home_id, common_item_id=item_id, text=text, qty=qty, checked=0, ha_uid=ha_uid,
                               added_at=datetime.utcnow())
        db.add(row)
    db.commit()
    db.refresh(row)
    if sync:
        sync_soon(home_id)
    return _row_json(row, None)


def update(db: Session, home_id: str, row_id: str, data: dict[str, Any], sync: bool = True) -> dict[str, Any]:
    row = db.get(ShoppingListItem, row_id)
    if row is None or row.home_id != home_id:
        raise ListError("That list item was not found")
    if "checked" in data:
        row.checked = 1 if data["checked"] else 0
        row.checked_at = datetime.utcnow() if row.checked else None
    if "qty" in data and data["qty"] is not None:
        row.qty = max(0.01, min(float(data["qty"]), 999))
    if "text" in data and data["text"]:
        row.text = " ".join(str(data["text"]).split())[:200]
        row.common_item_id = data.get("item_id") or ensure_item(db, home_id, row.text)
    db.commit()
    if sync:
        sync_soon(home_id)
    return _row_json(row, None)


def remove(db: Session, home_id: str, row_id: str) -> None:
    row = db.get(ShoppingListItem, row_id)
    if row is None or row.home_id != home_id:
        raise ListError("That list item was not found")
    uid = row.ha_uid
    db.delete(row)
    db.commit()
    if uid:
        _ha_remove(uid)
    sync_soon(home_id)


def clear_checked(db: Session, home_id: str) -> int:
    rows = db.query(ShoppingListItem).filter(ShoppingListItem.home_id == home_id, ShoppingListItem.checked == 1).all()
    uids = [r.ha_uid for r in rows if r.ha_uid]
    for r in rows:
        db.delete(r)
    db.commit()
    for uid in uids:
        _ha_remove(uid)
    sync_soon(home_id)
    return len(rows)


def browse(db: Session, home_id: str, query: str | None = None, limit: int | None = None) -> list[dict[str, Any]]:
    """Items you have bought before, most bought first, to pick from (filtered by ``query``)."""
    known = _known_items(db, home_id)
    units = {k["id"]: k.get("unit") for k in trips.available_items(db, home_id, None, 1000)}
    on_list = {r.common_item_id for r in db.query(ShoppingListItem).filter(ShoppingListItem.home_id == home_id,
                                                                        ShoppingListItem.checked == 0).all()}
    q = (query or "").strip().lower()
    out = [{"id": k["id"], "name": k["name"], "unit": units.get(k["id"]), "on_list": k["id"] in on_list,
            "bought": bool(k.get("purchases"))} for k in known if not q or q in k["name"].lower()]
    return out[:limit] if limit else out


# --------------------------------------------------------------------------- #
# Home Assistant: a sensor, and a to-do list kept in step
# --------------------------------------------------------------------------- #

def price_text(price: float) -> str:
    """$3.29, or $3.199 for a price posted to a tenth of a cent (fuel)."""
    return f"${price:.3f}" if round(price, 2) != round(price, 3) else f"${price:.2f}"


def _describe(place: dict[str, Any] | None) -> str:
    if not place:
        return "No price on record yet"
    unit = f"/{str(place['unit']).lower()}" if place.get("unit") and place["unit"] != "each" else ""
    source = {"online": "online price", "deal": "deal online", "receipt": "what you paid"}[place["price_source"]]
    if place.get("discount"):
        source += ", after " + " + ".join(place["discount"]["names"])
    return f"Cheapest at {place['store']}: {price_text(place['price'])}{unit} ({source})"


# home id -> (fingerprint of the sensor last published, time.time() then)
_published = sensor_publisher.Publisher(clock=lambda: time.time())


def publish(db: Session, home_id: str) -> None:
    """Publish the list to Home Assistant as a sensor (best effort)."""
    if not ha.available():
        return
    data = get_list(db, home_id)
    home = db.get(Home, home_id)
    items = [{"name": i["text"], "qty": i["qty"], "cheapest_store": i["cheapest"]["store"] if i["cheapest"] else None,
              "price": i["cheapest"]["price"] if i["cheapest"] else None, "unit": i["cheapest"]["unit"] if i["cheapest"] else None,
              "source": i["cheapest"]["price_source"] if i["cheapest"] else None, "description": _describe(i["cheapest"])}
             for i in data["items"] if not i["checked"]]
    names = {i["id"]: i["text"] for i in data["items"]}
    by_store = [{"store": g["store"] or "No price yet", "items": [names[x] for x in g["items"]], "total": g["total"]} for g in data["by_store"]]
    attributes = {"friendly_name": "Shopping list", "icon": "mdi:cart", "unit_of_measurement": "items", "home": home.name if home else None,
                  "items": items, "by_store": by_store}
    import json
    fingerprint = json.dumps([data["left"], attributes], sort_keys=True, default=str)
    # only when it changed, and at least every 15 minutes (Home Assistant forgets a state set this way when it restarts)
    if _published.unchanged(home_id, fingerprint, max_age=900):
        return
    try:
        ha.set_state(SENSOR, data["left"], {**attributes, "updated": datetime.now().isoformat(timespec="seconds")})
        _published.remember(home_id, fingerprint)
    except ha.HAError as e:
        logger.debug("Could not publish the shopping list sensor: %s", e)


def _todo_items(entity: str) -> list[dict[str, Any]]:
    result = ha.call_service("todo", "get_items", {"entity_id": entity, "status": ["needs_action", "completed"]}, response=True)
    return ((result or {}).get("service_response") or {}).get(entity, {}).get("items") or []


def _ha_remove(uid: str) -> None:
    entity = get_settings().shopping_list_todo_entity
    if entity and ha.available():
        try:
            ha.call_service("todo", "remove_item", {"entity_id": entity, "item": uid})
        except ha.HAError as e:
            logger.debug("Could not remove a to-do item: %s", e)


@reqcache.scoped
def sync(home_id: str) -> None:
    """Keep the home's list and the Home Assistant to-do list in step, then republish the sensor.

    New to-do items are added here (linked to a known item when they clearly are one); ticking off or deleting on
    either side is copied to the other; items added here are added there, with where they are cheapest in the
    description. Items are paired by the to-do item's id, or by the same text the first time."""
    settings = get_settings()
    entity = settings.shopping_list_todo_entity
    with _sync_lock, get_db_session()() as db:
        if entity and ha.available():
            try:
                _sync_todo(db, home_id, entity)
            except ha.HAError as e:
                logger.info("Shopping list: could not sync with %s: %s", entity, e)
        publish(db, home_id)


def _sync_todo(db: Session, home_id: str, entity: str) -> None:
    remote = {str(t.get("uid")): t for t in _todo_items(entity) if t.get("uid")}
    rows = db.query(ShoppingListItem).filter(ShoppingListItem.home_id == home_id).all()
    linked = {r.ha_uid for r in rows if r.ha_uid}
    # 1. pair by text the first time (a list that was already on both sides)
    for r in rows:
        if not r.ha_uid:
            twin = next((t for uid, t in remote.items() if uid not in linked and str(t.get("summary", "")).strip().lower() == r.text.lower()), None)
            if twin:
                r.ha_uid = str(twin["uid"])
                linked.add(r.ha_uid)
    # 2. what changed in Home Assistant since the last sync (a status that differs from the one recorded then);
    #    a difference Home Assistant did not make is a change made here, sent there in step 3
    for r in list(rows):
        if not r.ha_uid:
            continue
        t = remote.get(r.ha_uid)
        if t is None:  # deleted in Home Assistant
            db.delete(r)
            rows.remove(r)
            continue
        status = t.get("status")
        done = status == "completed"
        changed_there = r.ha_status is None or status != r.ha_status
        if changed_there and done != bool(r.checked):
            if r.ha_status is None and bool(r.checked):
                continue  # first pairing: bought on either side counts as bought (sent there in step 3)
            r.checked, r.checked_at = (1, datetime.utcnow()) if done else (0, None)
        r.ha_status = status
    for uid, t in remote.items():
        if uid not in linked and t.get("status") != "completed" and str(t.get("summary", "")).strip():
            text = " ".join(str(t["summary"]).split())[:200]
            row = ShoppingListItem(home_id=home_id, common_item_id=ensure_item(db, home_id, text), text=text, qty=1.0, checked=0,
                                   ha_uid=uid, ha_status=t.get("status"), added_at=datetime.utcnow())
            db.add(row)
            rows.append(row)
    db.commit()
    # 3. what is new here, and where each item is cheapest (in the to-do item's description)
    places = cheapest(db, home_id, [r.common_item_id for r in rows if r.common_item_id and not r.checked])
    for r in rows:
        place = (places.get(r.common_item_id) or [None])[0] if r.common_item_id and not r.checked else None
        summary = r.text if (r.qty or 1) == 1 else f"{r.text} × {r.qty:g}"
        description = _describe(place) if r.common_item_id else ""
        status = "completed" if r.checked else "needs_action"
        if not r.ha_uid:
            if r.checked:
                continue
            ha.call_service("todo", "add_item", {"entity_id": entity, "item": summary, **({"description": description} if description else {})})
            fresh = {str(t.get("uid")): t for t in _todo_items(entity) if t.get("uid")}
            new = next((uid for uid, t in fresh.items() if uid not in remote and uid not in {x.ha_uid for x in rows if x.ha_uid}
                        and str(t.get("summary", "")).strip() == summary), None)
            r.ha_uid, r.ha_status = new, "needs_action"
            remote = fresh
            continue
        t = remote.get(r.ha_uid) or {}
        changes: dict[str, Any] = {}
        if t.get("status") != status:
            changes["status"] = status
        if description and not r.checked and (t.get("description") or "") != description:
            changes["description"] = description  # where it is cheapest; left alone once bought
        if changes:
            ha.call_service("todo", "update_item", {"entity_id": entity, "item": r.ha_uid, **changes})
        r.ha_status = status
    db.commit()


def sync_soon(home_id: str) -> None:
    """Sync in the background, so the page does not wait for Home Assistant."""
    threading.Thread(target=_safe_sync, args=(home_id,), daemon=True).start()


def _safe_sync(home_id: str) -> None:
    try:
        sync(home_id)
    except Exception:  # noqa: BLE001 - best effort
        logger.exception("Shopping list sync failed")


def sync_all() -> None:
    """Every home with a list (the minute loop)."""
    with get_db_session()() as db:
        homes = [h for (h,) in db.query(ShoppingListItem.home_id).distinct().all()]
        if get_settings().shopping_list_todo_entity and not homes:
            homes = [h.id for h in db.query(Home).all()][:1]  # an empty list here still picks up items added in HA
    for home_id in homes:
        _safe_sync(home_id)
