"""What Receipt Price Intelligence answers the Household Assistant (app/tools.py; HOUSEHOLD_ASSISTANT_SPEC §4.2).

- off until an admin turns it on; the asking person must have opened the app and not turned off their own switch;
- receipt.shopping_list, receipt.price, receipt.spending from receipts made here; one home, or `home` with several;
- receipt.shopping_list.add only with the tap;
- the bus keeps its tables in its own database file, never in the app's.

Needs the full requirements. All stores, items and amounts here are invented."""
import importlib.util
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HAVE_DEPS = all(importlib.util.find_spec(m) for m in ("sqlalchemy", "fastapi"))


@unittest.skipUnless(HAVE_DEPS, "needs the app's requirements")
class ToolTests(unittest.TestCase):
    def setUp(self):
        from app import app_settings
        from app.common import app_bus
        from app.db import dispose_engine, init_models
        self.app_bus = app_bus
        self.dir = tempfile.mkdtemp()
        self.old_env = {k: os.environ.get(k) for k in ("DATABASE_PATH", "IMAGE_PATH")}
        os.environ["DATABASE_PATH"] = os.path.join(self.dir, "t.db")
        os.environ["IMAGE_PATH"] = os.path.join(self.dir, "img")
        dispose_engine()
        app_settings.configure(os.environ["DATABASE_PATH"])
        init_models()
        self.app_settings = app_settings
        app_settings.update({"assistant_answers": True}, "test")
        self.seed()

    def tearDown(self):
        from app.db import dispose_engine
        dispose_engine()
        for k, v in self.old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        if self.old_env["DATABASE_PATH"]:
            self.app_settings.configure(self.old_env["DATABASE_PATH"])

    def seed(self):
        from app.db import get_db_session
        from app.db.models import (CommonItem, Home, PriceObservation, Receipt, ReceiptItem, StoreChain,
                                   StoreLocation, User)
        today = date.today()
        with get_db_session()() as db:
            db.add(User(id="u_pat", display_name="Pat"))
            db.flush()
            db.add(Home(id="h1", name="Maple Street", created_by_user_id="u_pat"))
            db.flush()
            milk = CommonItem(id="i_milk", user_id="u_pat", home_id="h1", name="2% Milk", name_confirmed=1)
            db.add(milk)
            db.flush()
            for cid, name, total, price, days in (("c1", "Freshmart", 42.5, 3.49, 3), ("c2", "Valuco", 20.0, 2.99, 10)):
                db.add(StoreChain(id=cid, name=name, normalized_name=name.lower()))
                db.flush()
                db.add(StoreLocation(id=f"l{cid}", store_chain_id=cid, store_number="1"))
                db.flush()
                when = (today - timedelta(days=days)).isoformat()
                db.add(Receipt(id=f"r{cid}", user_id="u_pat", home_id="h1", store_location_id=f"l{cid}",
                               purchase_date=when, grand_total=total, currency_code="USD"))
                db.flush()
                db.add(ReceiptItem(id=f"ri{cid}", receipt_id=f"r{cid}", common_item_id="i_milk",
                                   receipt_description="MILK 2%", quantity=1, unit_price=price, line_total=price))
                db.flush()
                db.add(PriceObservation(user_id="u_pat", home_id="h1", common_item_id="i_milk", receipt_item_id=f"ri{cid}",
                                        store_chain_id=cid, store_location_id=f"l{cid}", purchase_date=when, quantity=1,
                                        unit_price=price, unit_price_unit="each", line_total=price, currency_code="USD"))
            db.commit()

    def call(self, tool, uid="u_pat", confirm=None, **args):
        from app import tools
        now = datetime.now(timezone.utc)
        data = {"tool": tool, "args": args, "requested_by": uid, "question": "q1"}
        if confirm is not None:
            data["confirm"] = confirm
        env = {"id": self.app_bus.new_ulid(now), "v": 1, "from": "household_assistant",
               "to": "receipt_price_intelligence", "kind": "assist.tool.call", "kv": 1, "reply_to": None, "ref": None,
               "sent": now.isoformat(), "expires": (now + timedelta(seconds=20)).isoformat(), "data": data}
        conn = sqlite3.connect(":memory:")
        try:
            return tools.tools.on_call(self.app_bus.Message(env), conn)
        except self.app_bus.Nack as n:
            return n
        finally:
            conn.close()

    def assertNack(self, res, reason, detail):
        self.assertIsInstance(res, self.app_bus.Nack, res)
        self.assertEqual((res.reason, res.detail), (reason, detail))

    def test_off_by_default_and_who_may_ask(self):
        self.assertFalse(self.app_settings.DEFAULTS["assistant_answers"])
        self.app_settings.update({"assistant_answers": False}, "test")
        self.assertNack(self.call("receipt.spending"), "not_allowed", "off")
        self.app_settings.update({"assistant_answers": True}, "test")
        self.assertNack(self.call("receipt.spending", uid="u_stranger"), "not_allowed", "no_access")

    def test_persons_own_switch(self):
        from fastapi.testclient import TestClient
        from app import main
        c = TestClient(main.app, client=("172.30.32.2", 50000))
        pat = {"X-Remote-User-Id": "u_pat"}
        me = c.get("/api/v1/me", headers=pat).json()
        self.assertEqual((me["assistant"], me["assistantOk"]), (True, True))
        r = c.put("/api/v1/me/assistant", json={"assistantOk": False}, headers=pat)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(r.json()["assistantOk"])
        self.assertNack(self.call("receipt.spending"), "not_allowed", "person_off")
        self.assertEqual(c.put("/api/v1/me/assistant", json={"assistantOk": "no"}, headers=pat).status_code, 422)
        c.put("/api/v1/me/assistant", json={"assistantOk": True}, headers=pat)
        self.assertIn("text", self.call("receipt.spending"))

    def test_price(self):
        res = self.call("receipt.price", item="milk")
        self.assertIn("2% Milk: cheapest at Valuco", res["text"])
        self.assertEqual([i["latest"] for i in res["items"]], [2.99, 3.49])
        self.assertIn("No purchases", self.call("receipt.price", item="saffron")["text"])

    def test_links_open_the_right_page(self):
        from app import tools
        tools.PANEL["value"] = "/local_receipt_price_intelligence"
        try:
            self.assertEqual(self.call("receipt.price", item="milk")["links"],
                             [{"label": "Insights in Receipt Price Intelligence",
                               "panel": "/local_receipt_price_intelligence", "target": "/insights"}])
            self.assertEqual(self.call("receipt.shopping_list")["links"][0]["target"], "/list")
        finally:
            tools.PANEL["value"] = None

    def test_sub_paths_redirect_to_the_page(self):
        from fastapi.testclient import TestClient
        from app import main
        c = TestClient(main.app, client=("172.30.32.2", 50000))
        for path, page in (("/insights", "analysis.html"), ("/list", "list.html"), ("/receipts", "index.html")):
            r = c.get(path, headers={"X-Remote-User-Id": "u_pat"}, follow_redirects=False)
            self.assertEqual((r.status_code, r.headers["location"]), (307, page), path)

    def test_spending(self):
        res = self.call("receipt.spending")
        self.assertIn("62.50 USD in 2 trips", res["text"])
        self.assertEqual([(i["store"], i["spend"]) for i in res["items"]], [("Freshmart", 42.5), ("Valuco", 20.0)])
        res = self.call("receipt.spending", store="valu")
        self.assertIn("20.00 USD at Valuco in 1 trip.", res["text"])
        month = (date.today() - timedelta(days=400)).strftime("%Y-%m")
        self.assertIn("No receipts", self.call("receipt.spending", month=month)["text"])

    def test_shopping_list_and_adding_with_the_tap(self):
        self.assertIn("is empty", self.call("receipt.shopping_list")["text"])
        self.assertNack(self.call("receipt.shopping_list.add", item="milk"), "not_allowed", "confirm")
        res = self.call("receipt.shopping_list.add", confirm=True, item="milk", qty=2)
        self.assertIn("Added 2% Milk to the shopping list for Maple Street — cheapest at Valuco", res["text"])
        res = self.call("receipt.shopping_list")
        self.assertEqual(res["items"], [{"item": "2% Milk", "qty": 2.0, "cheapest_at": "Valuco #1", "price": 2.99}])

    def test_deals(self):
        from app.db import get_db_session
        from app.db.models import PriceObservation, Receipt, ReceiptItem
        when = (date.today() - timedelta(days=20)).isoformat()              # Freshmart is where milk is usually bought
        with get_db_session()() as db:
            db.add(Receipt(id="rc1b", user_id="u_pat", home_id="h1", store_location_id="lc1", purchase_date=when,
                           grand_total=3.49, currency_code="USD"))
            db.flush()
            db.add(ReceiptItem(id="ric1b", receipt_id="rc1b", common_item_id="i_milk", receipt_description="MILK 2%",
                               quantity=1, unit_price=3.49, line_total=3.49))
            db.flush()
            db.add(PriceObservation(user_id="u_pat", home_id="h1", common_item_id="i_milk", receipt_item_id="ric1b",
                                    store_chain_id="c1", store_location_id="lc1", purchase_date=when, quantity=1,
                                    unit_price=3.49, unit_price_unit="each", line_total=3.49, currency_code="USD"))
            db.commit()
        res = self.call("receipt.deals")
        self.assertIn("2% Milk: 14% cheaper at Valuco (2.99 USD vs 3.49 USD at Freshmart)", res["text"])
        self.assertEqual(res["items"][0]["kind"], "another store")
        self.assertEqual(res["links"][0]["target"], "/deals")
        with get_db_session()() as db:
            db.query(PriceObservation).filter(PriceObservation.store_chain_id == "c2").delete()
            db.commit()
        self.assertIn("No cheaper way", self.call("receipt.deals")["text"])

    def test_budgets(self):
        self.assertIn("no monthly budgets yet", self.call("receipt.budgets")["text"])
        from app.db import get_db_session
        from app.services import budgets
        with get_db_session()() as db:
            budgets.save_budget(db, "h1", None, 1000)
            budgets.save_budget(db, "h1", "Dairy", 1)
        res = self.call("receipt.budgets")
        self.assertTrue(res["text"].startswith("This month ("), res["text"])
        self.assertEqual([i["budget"] for i in res["items"]], ["Everything", "Dairy"])
        self.assertIn("Everything:", res["text"])
        self.assertEqual(res["items"][0]["amount"], 1000.0)

    def test_several_homes(self):
        from app.db import get_db_session
        from app.db.models import Home
        with get_db_session()() as db:
            db.add(Home(id="h2", name="Lake House", created_by_user_id="u_pat"))
            db.commit()
        self.assertNack(self.call("receipt.spending"), "invalid", "home")
        self.assertIn("62.50", self.call("receipt.spending", home="maple street")["text"])
        self.assertNack(self.call("receipt.spending", home="Nowhere"), "not_found", "home")

    def test_bus_tables_are_in_their_own_file(self):
        from app import app_messages, tools
        tools.tools.check()
        self.assertEqual(app_messages.bus_db_path(), os.path.join(self.dir, "app_bus.db"))
        conn = app_messages._connect()
        try:
            self.app_bus.migrate(conn)
        finally:
            conn.close()
        with sqlite3.connect(os.environ["DATABASE_PATH"]) as main_db:
            names = {r[0] for r in main_db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertNotIn("bus_outbox", names)
        self.assertIn("assist.tool.call", self.app_bus.default._handlers)


if __name__ == "__main__":
    unittest.main()
