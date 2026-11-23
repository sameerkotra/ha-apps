"""Receipt Price Intelligence on the household apps bus (APP_MESSAGES_SPEC.md; the shared app/common/app_bus.py).

The app answers the Household Assistant (tools.py, APP_MESSAGES_SPEC §6.6) and Household Docs' `receipt.sheet.get`
(§6.9 there: the shopping list's totals and items' lowest prices for a sheet's formulas). The app's data is in
SQLAlchemy; the bus wants plain sqlite3 and holds a write lock for the length of each answer, which would block the
tools' own writes to the same file. So the bus keeps its three small tables (`bus_outbox`, `bus_seen`, `bus_apps`)
in a database of its own next to the app's (`app_bus.db`), and each tool uses its own SQLAlchemy session. The bus
opens its own WebSocket to Home Assistant and runs its outbox on its own thread; without a Supervisor token (the
tests) it stays off.
"""
from __future__ import annotations

import os
import sqlite3

from app import tools
from app.common import app_bus as bus
from app.common import assist_tools
from app.config import APP_VERSION, get_settings

SLUG = "receipt_price_intelligence"
TITLE = "Receipt Price Intelligence"
DOCS = "household_docs"
MAX_ASKED = 30                       # items' prices in one question (the answer stays under 8 KB)
MAX_STORES = 20

tools.tools.install(bus)


def _clean_names(raw) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_ASKED:
        raise bus.Nack("invalid", "items")
    out = []
    for x in raw:
        if not isinstance(x, str) or not 0 < len(x.strip()) <= 100:
            raise bus.Nack("invalid", "items")
        if x.strip().lower() not in (o.lower() for o in out):
            out.append(" ".join(x.split()))
    return out


@bus.handler("receipt.sheet.get")
def on_sheet(msg, conn):
    """Household Docs, for =SHOPPING.TOTAL(), =SHOPPING.COUNT() and =SHOPPING.PRICE() in a sheet: {requested_by,
    items?: [names]} → {currency, home, list: {count, total, unpriced, stores: [{name, count, total}]}, prices:
    [{item, name, price, store}]} — the home's shopping list (ticked-off items left out; the total at each item's
    cheapest store × its quantity, as the list page shows it) and, for each asked name, the item it is here (the
    assistant's own match) with its lowest price and store now (price null when none is known). Only for a person
    who has opened the app (Docs' requested_by is their Home Assistant user id, as here); one home, or nack invalid
    home with several. Admin → App settings → Show the shopping list and prices in Household Docs (on) off → nack
    not_allowed off."""
    from app import app_settings
    from app.db.models import User
    from app.services import homes, reqcache, shoplist
    if msg.from_app != DOCS:
        raise bus.Nack("not_allowed", "sender")
    data = msg.data if isinstance(msg.data, dict) else {}
    uid = data.get("requested_by")
    if not isinstance(uid, str) or not 0 < len(uid) <= 128:
        raise bus.Nack("invalid", "requested_by")
    names = _clean_names(data.get("items"))
    if not app_settings.values().get("docs_answers"):
        raise bus.Nack("not_allowed", "off")
    with tools._session() as db, reqcache.scope():
        if db.get(User, uid) is None:
            raise bus.Nack("not_allowed", "no_access")
        rows = homes.list_homes(db)
        if not rows:
            raise bus.Nack("not_found", "home")
        if len(rows) > 1:
            raise bus.Nack("invalid", "home")
        home = rows[0]
        lst = shoplist.get_list(db, home.id, online=False)
        stores = [{"name": g["store"][:60], "count": len(g["items"]), "total": g["total"]}
                  for g in lst["by_store"] if g["store"]]
        unpriced = sum(len(g["items"]) for g in lst["by_store"] if not g["store"])
        found = [(n, tools._find_item(db, home.id, n)) for n in names]
        places = shoplist.cheapest(db, home.id, [it.id for _n, it in found if it is not None], online=False)
        prices = []
        for n, it in found:
            best = (places.get(it.id) or [None])[0] if it is not None else None
            prices.append({"item": n, "name": it.name[:80] if it is not None else None,
                           "price": round(best["price"], 2) if best and best.get("price") is not None else None,
                           "store": (best.get("store") or "")[:60] or None if best else None})
    return {"currency": lst["currency"], "home": home.name,
            "list": {"count": lst["left"], "total": round(sum(s["total"] for s in stores), 2), "unpriced": unpriced,
                     "stores": stores[:MAX_STORES]},
            "prices": prices}


def bus_db_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(get_settings().database_path)), "app_bus.db")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(bus_db_path(), timeout=10)
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def start() -> None:
    """In the lifespan: learn the sidebar page (for links), then join the bus."""
    if bus.default.started:
        return
    token = os.environ.get("SUPERVISOR_TOKEN", "")
    tools.PANEL["value"] = assist_tools.sidebar_page(SLUG, token=token,
                                                     supervisor_api=os.environ.get("SUPERVISOR_API", "http://supervisor"))
    bus.start(SLUG, TITLE, APP_VERSION, db=_connect, outbox_thread=bool(token), token=token,
              api_base=os.environ.get("SUPERVISOR_CORE_API", "http://supervisor/core/api"),
              ws_url=os.environ.get("SUPERVISOR_CORE_WS", "ws://supervisor/core/websocket"),
              signing_key=_signing_key)


def _signing_key() -> str:
    """Admin → App settings → App-messages signing key ("" : unsigned, as before)."""
    from app import app_settings
    return app_settings.REGISTRY.get("bus_signing_key") or ""


def stop() -> None:
    bus.stop()
