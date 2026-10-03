"""Saved trip plans: a plan is kept for ``trip_plan_keep_hours`` (default 12) and given back instead of being
worked out again, while nothing it depends on has changed.

A plan depends on the request (items and quantities, car, options, departure time) and on data: receipts, online
prices and deals, store locations and hours, the home's address and the cars. All of it goes into the key, so any
change (a new receipt, a new online price, edited hours) means the next plan is worked out afresh. Only a plan that
leaves "now" can go stale by the clock alone (a store may close later in the day); the page shows when it was made.

The newest plan of each home is also kept on its own, so the Trip page can show it again when reopened.
Stored in the web cache file (``webcache``), not in backups.
"""

from __future__ import annotations

import json
import time
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Home, Receipt, StoreLocation, Vehicle, WebDeal
from app.services import webcache


def keep_seconds() -> float:
    return max(0, get_settings().trip_plan_keep_hours) * 3600


def _data_stamp(db: Session, home_id: str) -> list[Any]:
    """Cheap fingerprint of everything a plan reads, so a change to any of it makes a different key."""
    receipts = db.query(func.count(Receipt.id), func.max(Receipt.updated_at)).filter(Receipt.home_id == home_id).one()
    deals = db.query(func.count(WebDeal.id), func.max(WebDeal.fetched_at)).filter(WebDeal.home_id == home_id).one()
    stores = db.query(func.count(StoreLocation.id), func.max(StoreLocation.updated_at)).one()
    home = db.get(Home, home_id)
    cars = sorted(
        json.dumps({c.name: str(getattr(v, c.name)) for c in Vehicle.__table__.columns}, sort_keys=True)
        for v in db.query(Vehicle).filter(Vehicle.home_id == home_id).all()
    )
    from app.services import discounts, webinfo
    ignored = sorted(f"{i}|{c}" for i, c in webinfo.ignored_pairs(db, home_id))  # online prices you chose not to use
    store_discounts = json.dumps(discounts.list_for_home(db, home_id), sort_keys=True)  # cards and memberships
    return [list(map(str, receipts)), list(map(str, deals)), list(map(str, stores)),
            str(home.updated_at) if home else "", str(getattr(home, "latitude", "")), str(getattr(home, "longitude", "")), cars,
            ignored, store_discounts]


def _request_key(request: dict[str, Any]) -> dict[str, Any]:
    items = sorted((str(i.get("item_id")), float(i.get("qty") or 1)) for i in request.get("items") or [])
    rest = {k: request.get(k) for k in sorted(request) if k != "items"}
    return {"items": items, **rest}


def key(db: Session, home_id: str, request: dict[str, Any]) -> str:
    return webcache._key("plan", home_id, _request_key(request), _data_stamp(db, home_id))


def saved(db: Session, home_id: str, request: dict[str, Any]) -> dict[str, Any] | None:
    """The saved plan for exactly this request and data, with ``saved`` {at, hours_left}, or None."""
    if keep_seconds() <= 0:
        return None
    found = webcache.get(key(db, home_id, request))
    if found is None:
        return None
    result, at = found
    return {**result, "saved": {"at": at, "hours_left": round(max(0, at + keep_seconds() - time.time()) / 3600, 1)}}


def save(db: Session, home_id: str, request: dict[str, Any], result: dict[str, Any]) -> None:
    seconds = keep_seconds()
    if seconds <= 0:
        return
    webcache.put(key(db, home_id, request), "plan", result, seconds, label=f"trip plan for home {home_id}")
    webcache.put(webcache._key("last-plan", home_id), "plan", {"request": request, "result": result}, seconds,
                 label=f"last trip plan for home {home_id}")


def last(db: Session, home_id: str) -> dict[str, Any] | None:
    """The home's newest plan with its request, and whether it still matches the data (``current``)."""
    if keep_seconds() <= 0:
        return None
    found = webcache.get(webcache._key("last-plan", home_id))
    if found is None:
        return None
    value, at = found
    current = webcache.get(key(db, home_id, value["request"])) is not None
    return {"request": value["request"], "result": value["result"], "saved_at": at, "current": current,
            "hours_left": round(max(0, at + keep_seconds() - time.time()) / 3600, 1)}


def forget(home_id: str) -> None:
    """Drop the home's newest plan (the page's *Clear*)."""
    webcache.expire(webcache._key("last-plan", home_id))
