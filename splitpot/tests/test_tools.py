"""What Splitpot answers the Household Assistant (app/tools.py; HOUSEHOLD_ASSISTANT_SPEC §4.2, §7.2).

- off until an admin turns it on; the person's own switch on My settings;
- only the groups the asking person is in, matched by their Home Assistant login;
- splitpot.balances: the settle-up transfers and the person's overall standing;
- splitpot.recent: the newest entries (at most 20, `more` beyond), a group by name, the last N days.

All people and amounts here are invented."""
import _env  # noqa: F401  (must be first)

import os
import unittest
from datetime import datetime, timedelta, timezone

from app import config, main, tools
from app.common import app_bus
from common_tests.ingress import identity_headers, ingress_client

ADMIN = identity_headers("u_admin", "adminy", "Adminy")
ASHA = identity_headers("ha_asha", "asha", "Asha")
RAVI = identity_headers("ha_ravi", "ravi", "Ravi")


def call(tool, uid, **args):
    now = datetime.now(timezone.utc)
    env = {"id": app_bus.new_ulid(now), "v": 1, "from": "household_assistant", "to": "splitpot",
           "kind": "assist.tool.call", "kv": 1, "reply_to": None, "ref": None, "sent": now.isoformat(),
           "expires": (now + timedelta(seconds=20)).isoformat(),
           "data": {"tool": tool, "args": args, "requested_by": uid, "question": "q1"}}
    with main.get_conn() as conn:
        try:
            return tools.tools.on_call(app_bus.Message(env), conn)
        except app_bus.Nack as n:
            return n


class ToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main.ADMIN_NAMES.clear()
        main.ADMIN_NAMES.add("adminy")
        cls._ctx = ingress_client(main.app)
        cls.c = cls._ctx.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls._ctx.__exit__(None, None, None)

    def setUp(self):
        for ext in ("", "-wal", "-shm"):
            p = str(main.DB_FILE) + ext
            if os.path.exists(p):
                os.remove(p)
        main.init_db()
        with main.get_conn() as conn:
            for uid, name, ha in (("asha", "Asha", "ha_asha"), ("ravi", "Ravi", "ha_ravi"), ("mia", "Mia", "ha_mia")):
                conn.execute("INSERT INTO users (id, name, created_at, ha_user_id) VALUES (?, ?, ?, ?)",
                             (uid, name, main.now_iso(), ha))
            conn.commit()
        self.flat = self.group("Flat", ["asha", "ravi", "mia"])
        self.trip = self.group("Trip", ["ravi", "mia"])                      # Asha isn't in it
        self.add(self.flat, "Groceries", 90, "asha", ["asha", "ravi", "mia"])
        self.add(self.flat, "Internet", 30, "ravi", ["asha", "ravi", "mia"])
        self.add(self.trip, "Secret hotel", 200, "mia", ["ravi", "mia"])
        self.settings(assistant_answers=True)
        self.old_page, config.SIDEBAR_PAGE = config.SIDEBAR_PAGE, "/a1b2c3d4_splitpot"

    def tearDown(self):
        config.SIDEBAR_PAGE = self.old_page

    def group(self, name, members):
        r = self.c.post("/api/groups", json={"name": name, "memberIds": members}, headers=ASHA)
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["id"]

    def add(self, gid, what, amount, paid_by, participants, date=None):
        body = {"description": what, "amount": amount, "paidBy": paid_by, "participants": participants}
        if date:
            body["date"] = date
        r = self.c.post(f"/api/groups/{gid}/expenses", json=body, headers=ASHA)
        self.assertEqual(r.status_code, 201, r.text)

    def settings(self, **values):
        r = self.c.put("/api/admin/settings", json=values, headers=ADMIN)
        self.assertEqual(r.status_code, 200, r.text)

    def assertNack(self, res, reason, detail):
        self.assertIsInstance(res, app_bus.Nack, res)
        self.assertEqual((res.reason, res.detail), (reason, detail))

    def test_off_by_default(self):
        self.settings(assistant_answers=False)
        self.assertNack(call("splitpot.balances", "ha_asha"), "not_allowed", "off")
        main.invalidate_settings_cache()
        with main.get_conn() as conn:
            conn.execute("DELETE FROM app_settings WHERE key = 'assistant_answers'")
            conn.commit()
        main.invalidate_settings_cache()
        self.assertFalse(main.get_setting("assistant_answers"))

    def test_balances_in_my_groups(self):
        res = call("splitpot.balances", "ha_asha")
        # Flat: Asha paid 90, Ravi 30; each owes 40 → Asha +50, Ravi -10, Mia -40
        self.assertIn("Asha is owed $50.00 overall", res["text"])
        self.assertIn("Mia owes you $40.00", res["text"])
        self.assertIn("Ravi owes you $10.00", res["text"])
        self.assertEqual({(i["from"], i["to"], i["amount"]) for i in res["items"]},
                         {("Mia", "Asha", 40.0), ("Ravi", "Asha", 10.0)})
        self.assertTrue(all(i["group"] == "Flat" for i in res["items"]))       # not the Trip
        self.assertEqual(res["links"], [{"label": "Flat in Splitpot", "panel": "/a1b2c3d4_splitpot", "target": f"/group/{self.flat}"}])
        res = call("splitpot.balances", "ha_ravi", group="trip")
        self.assertIn("you owe Mia $100.00", res["text"])
        self.assertNack(call("splitpot.balances", "ha_asha", group="Trip"), "not_found", "group")

    def test_recent(self):
        res = call("splitpot.recent", "ha_asha")
        self.assertEqual([i["what"] for i in res["items"]], ["Internet", "Groceries"])
        self.assertEqual(res["items"][0]["your_share"], 10.0)
        self.assertNotIn("Secret hotel", str(res))
        self.assertFalse(res["more"])
        old = (datetime.now(timezone.utc) - timedelta(days=40)).date().isoformat()
        self.add(self.flat, "Old sofa", 300, "asha", ["asha", "ravi"], date=old)
        self.assertEqual(len(call("splitpot.recent", "ha_asha")["items"]), 3)
        self.assertEqual(len(call("splitpot.recent", "ha_asha", days=7)["items"]), 2)
        for i in range(20):
            self.add(self.flat, f"Milk {i}", 2, "ravi", ["asha", "ravi"])
        res = call("splitpot.recent", "ha_asha")
        self.assertEqual(len(res["items"]), 20)
        self.assertTrue(res["more"])

    def test_who_may_ask(self):
        self.assertNack(call("splitpot.balances", "ha_nobody"), "not_allowed", "no_access")
        with main.get_conn() as conn:
            conn.execute("UPDATE users SET disabled = 1 WHERE id = 'mia'")
            conn.commit()
        self.assertNack(call("splitpot.balances", "ha_mia"), "not_allowed", "no_access")
        p = self.c.get("/api/me/prefs", headers=RAVI).json()
        self.assertTrue(p["assistant"] and p["assistantOk"])
        r = self.c.put("/api/me/prefs", json={"assistantOk": False}, headers=RAVI)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(r.json()["assistantOk"])
        self.assertTrue(r.json()["receiveNotifications"])                   # untouched
        self.assertNack(call("splitpot.recent", "ha_ravi"), "not_allowed", "person_off")
        self.assertIn("text", call("splitpot.recent", "ha_asha"))
        self.assertEqual(self.c.put("/api/me/prefs", json={"assistantOk": "no"}, headers=RAVI).status_code, 422)

    def test_catalogue(self):
        tools.tools.check()
        self.assertEqual([t["name"] for t in tools.tools.spec()], ["splitpot.balances", "splitpot.recent"])
        self.assertIn("assist.tool.call", app_bus.default._handlers)
