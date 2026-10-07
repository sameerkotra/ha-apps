"""What Receipt Price Intelligence answers the Household Assistant (HOUSEHOLD_ASSISTANT_SPEC.md §4.2; the shared
app/common/assist_tools.py).

- `receipt.shopping_list`: the home's shopping list, each item with where it was cheapest and its last price;
- `receipt.price`: an item's latest price at each store, the cheapest, and each store's trend;
- `receipt.spending`: spending in a month (or the last 30 days) by store, or one store's;
- `receipt.shopping_list.add` (acts): adds an item to the list — only after the person taps the proposed change.

Everyone who has opened the app sees every home here, so the asking person needs only to be known (a `users` row);
with more than one home, `home` names it. The bus's own transaction is on a separate small database (app_messages.py),
so each tool reads and writes the app's data in its own SQLAlchemy session. Prices and spending are private: the
admin's *Answer the Household Assistant* is **off** until turned on. There are no per-person settings, so there is no
per-person switch. Links open the shopping list or Insights on the app's sidebar page.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from sqlalchemy import func

from app import app_settings
from app.common import app_bus as bus
from app.common import assist_tools
from app.common.assist_tools import Arg

_PANEL_RE = re.compile(r"^/[a-z0-9]{1,16}_receipt_price_intelligence$")
PANEL: dict = {"value": None}                         # set at start-up (app_messages.start)


def _session():
    from app.db import get_db_session
    return get_db_session()()


def _actor(conn, uid: str):
    from app.db.models import User
    with _session() as db:
        u = db.get(User, uid)
        return {"id": u.id, "name": u.display_name or uid} if u else None


def _panel():
    return PANEL["value"] if _PANEL_RE.match(PANEL["value"] or "") else None


tools = assist_tools.Catalogue(
    "receipt", targets=[r"/list", r"/insights"], actor=_actor, enabled=lambda conn: bool(app_settings.values().get("assistant_answers")), panel=_panel)

_HOME = Arg("string", "which home, when there is more than one", max_length=255)


def _home(db, ctx) -> tuple[str, str]:
    from app.services import homes
    rows = homes.list_homes(db)
    want = ctx.args.get("home")
    if want:
        rows = [h for h in rows if h.name.lower() == want.lower()]
        if not rows:
            raise bus.Nack("not_found", "home")
    if not rows:
        raise bus.Nack("not_found", "home")
    if len(rows) > 1:
        raise bus.Nack("invalid", "home")            # more than one home: the assistant asks which
    return rows[0].id, rows[0].name


def _money(x) -> str:
    from app.config import get_settings
    return f"{x:,.2f} {get_settings().default_currency}" if x is not None else "?"


def _link(ctx, target: str):
    label = {"/list": "Shopping list", "/insights": "Insights"}[target]
    return ctx.link(f"{label} in Receipt Price Intelligence", target)


def _find_item(db, home_id: str, text: str):
    """A known item by what the person called it: the app's own match ("milk" is "2% Milk"), else a name containing it."""
    from app.db.models import CommonItem
    from app.services import shoplist
    item_id = shoplist._match(db, home_id, text)
    if item_id:
        return db.get(CommonItem, item_id)
    like = f"%{text.lower()}%"
    return (db.query(CommonItem).filter(CommonItem.home_id == home_id, func.lower(CommonItem.name).like(like))
            .order_by(func.length(CommonItem.name)).first())


@tools.tool("receipt.shopping_list",
            "The household's shopping list, each item with the store where it was cheapest and its last price there.",
            args={"home": _HOME}, returns="items with quantity, cheapest store and price; ticked-off items left out",
            scope="household", examples=("What's on the shopping list?",))
def shopping_list(ctx):
    from app.services import reqcache, shoplist
    with _session() as db, reqcache.scope():
        home_id, home_name = _home(db, ctx)
        data = shoplist.get_list(db, home_id, online=False)
    rows = [i for i in data["items"] if not i["checked"]]
    if not rows:
        return ctx.result(f"The shopping list for {home_name} is empty.", links=[_link(ctx, "/list")])
    items = [{"item": i["text"], "qty": i["qty"], "cheapest_at": (i["cheapest"] or {}).get("store"),
              "price": (i["cheapest"] or {}).get("price")} for i in rows]
    parts = []
    for g in data["by_store"]:
        names = [i["text"] for i in rows if i["id"] in g["items"]]
        where = g["store"] or "no price known"
        parts.append(f"{where}: " + ", ".join(names[:8]) + ("…" if len(names) > 8 else "")
                     + (f" (about {_money(g['total'])})" if g["store"] else ""))
    text = f"{len(rows)} item{'s' if len(rows) != 1 else ''} on the list. " + "; ".join(parts) + "."
    return ctx.result(text, items=items, links=[_link(ctx, "/list")])


@tools.tool("receipt.price",
            "An item's latest price at each store it was bought at, which store is cheapest, and how its price is "
            "trending.",
            args={"item": Arg("string", "the item, e.g. milk", required=True, max_length=100), "home": _HOME},
            returns="per store: latest price and date, lowest, trend; the cheapest store", scope="household",
            examples=("Where is milk cheapest?",))
def price(ctx):
    from app.services import analytics, reqcache
    from app.services.analysis_data import load_observations
    with _session() as db, reqcache.scope():
        home_id, _name = _home(db, ctx)
        item = _find_item(db, home_id, ctx.args["item"])
        if item is None:
            return ctx.result(f"No purchases of “{ctx.args['item']}” on any receipt yet.", links=[_link(ctx, "/insights")])
        start = (date.today() - timedelta(days=365)).isoformat()
        stores = analytics.compare_stores(analytics.prepare_price_rows(load_observations(db, home_id, start,
                                                                                         item_id=item.id)))
        name = item.name
    if not stores:
        return ctx.result(f"No comparable prices for {name} in the last year.", links=[_link(ctx, "/insights")])
    stores = sorted(stores, key=lambda s: s["latest_price"])
    best = stores[0]
    items = [{"store": s["label"], "latest": s["latest_price"], "date": s["latest_date"], "lowest": s["min_price"],
              "unit": s["unit"], "trend": s.get("trend") if not isinstance(s.get("trend"), dict) else
              s["trend"].get("direction")} for s in stores]
    text = (f"{name}: cheapest at {best['label']} ({_money(best['latest_price'])}"
            + (f" per {best['unit']}" if best.get("unit") else "") + f", {best['latest_date']})"
            + "".join(f"; {s['label']} {_money(s['latest_price'])}" for s in stores[1:5]) + ".")
    return ctx.result(text, items=items, links=[_link(ctx, "/insights")])


@tools.tool("receipt.spending",
            "Grocery and shopping spending from the household's receipts: a month (default the last 30 days) by store, "
            "or one store's.",
            args={"month": Arg("month", "a month"), "store": Arg("string", "one store's name", max_length=100),
                  "home": _HOME},
            returns="total, trips, spend per store", scope="household",
            examples=("How much did we spend at Costco this month?",))
def spending(ctx):
    from app.services import analytics, reqcache
    from app.services.analysis_data import load_receipts
    if "month" in ctx.args:
        y, m = map(int, ctx.args["month"].split("-"))
        start = date(y, m, 1)
        end = date(y + (m == 12), m % 12 + 1, 1)
        label = start.strftime("%B %Y")
    else:
        end = date.today() + timedelta(days=1)
        start = end - timedelta(days=31)
        label = "the last 30 days"
    with _session() as db, reqcache.scope():
        home_id, _name = _home(db, ctx)
        s = analytics.summarize_spend(load_receipts(db, home_id, start.isoformat(), end.isoformat()))
    stores = s["by_store"]
    if "store" in ctx.args:
        want = ctx.args["store"].lower()
        stores = [x for x in stores if want in (x["name"] or "").lower()]
        if not stores:
            return ctx.result(f"No receipts from “{ctx.args['store']}” in {label}.", links=[_link(ctx, "/insights")])
        total = sum(x["spend"] for x in stores)
        trips = sum(x["trips"] for x in stores)
        text = f"{label}: {_money(total)} at {', '.join(x['name'] for x in stores)} in {trips} trip{'s' * (trips != 1)}."
    else:
        t = s["totals"]
        if not t["trips"]:
            return ctx.result(f"No receipts in {label}.", links=[_link(ctx, "/insights")])
        text = (f"{label}: {_money(t['spend'])} in {t['trips']} trips. "
                + "; ".join(f"{x['name']} {_money(x['spend'])}" for x in stores[:6]) + ".")
    items = [{"store": x["name"], "spend": x["spend"], "trips": x["trips"], "share": x["share"]} for x in stores]
    return ctx.result(text, items=items, links=[_link(ctx, "/insights")])


@tools.tool("receipt.shopping_list.add", "Adds an item to the household's shopping list (after the person confirms it).",
            args={"item": Arg("string", "what to add", required=True, max_length=200),
                  "qty": Arg("number", "how many (default 1)", min=0.01, max=999), "home": _HOME},
            acts=True, returns="the item as added, with its cheapest store", scope="household")
def add(ctx):
    from app.services import reqcache, shoplist
    with _session() as db, reqcache.scope():
        home_id, home_name = _home(db, ctx)
        try:
            row = shoplist.add(db, home_id, text=ctx.args["item"], qty=float(ctx.args.get("qty", 1)),
                               user_id=ctx.user["id"])
        except shoplist.ListError as e:
            raise bus.Nack("not_allowed" if "full" in str(e) else "invalid", "item") from None
        places = shoplist.cheapest(db, home_id, [row["item_id"]], online=False).get(row["item_id"]) if row["item_id"] else None
    best = (places or [None])[0]
    where = f" — cheapest at {best['store']} ({_money(best['price'])})" if best else ""
    return ctx.result(f"Added {row['text']} to the shopping list for {home_name}{where}.",
                      items=[{"item": row["text"], "qty": row["qty"], "cheapest_at": best["store"] if best else None}],
                      links=[_link(ctx, "/list")])
