"""What Calorie Tracker answers the Household Assistant (app/tools.py; HOUSEHOLD_ASSISTANT_SPEC §4.2, §7.2).

calorie.today: the asking person's own day against their goals (never anyone else's, never weight); the admin's
switch; the catalogue fits. "Today" is patched through config.today. All people and foods here are invented."""
import _env  # noqa: F401  (must be first)

import json
from datetime import date, datetime, timedelta, timezone
from unittest import mock

from test_app import ADMIN, ALICE, BOB, ApiBase

from app import config, db, tools
from app.common import app_bus


def call(uid, **args):
    now = datetime.now(timezone.utc)
    env = {"id": app_bus.new_ulid(now), "v": 1, "from": "household_assistant", "to": "calorie_tracker",
           "kind": "assist.tool.call", "kv": 1, "reply_to": None, "ref": None, "sent": now.isoformat(),
           "expires": (now + timedelta(seconds=20)).isoformat(),
           "data": {"tool": "calorie.today", "args": args, "requested_by": uid, "question": "q1"}}
    with db.get_conn() as conn:
        try:
            return tools.tools.on_call(app_bus.Message(env), conn)
        except app_bus.Nack as n:
            return n


class TodayTests(ApiBase):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(config, "today", return_value=date(2026, 9, 21))
        p.start()
        self.addCleanup(p.stop)
        self.log(ALICE, "Porridge", calories=350, protein=12, carbs=60, fat=6, meal_type="breakfast")
        self.log(ALICE, "Dal and rice", calories=650, protein=25, carbs=100, fat=12, meal_type="lunch")
        self.log(BOB, "Secret cake", calories=900)
        self.c.post("/api/weight", json={"date": "2026-09-21", "weight_kg": 70.5}, headers=ALICE)
        self.old_panel, config.INGRESS_PANEL = config.INGRESS_PANEL, "/a1b2c3d4_calorie_tracker"

    def tearDown(self):
        config.INGRESS_PANEL = self.old_panel
        super().tearDown()

    def assertNack(self, res, reason, detail):
        self.assertIsInstance(res, app_bus.Nack, res)
        self.assertEqual((res.reason, res.detail), (reason, detail))

    def test_own_day_against_goals(self):
        res = call("u_alice")
        self.assertIn("Today: 1000 of 2000 kcal (1000 left)", res["text"])
        self.assertIn("protein 37/150 g", res["text"])
        self.assertIn("breakfast: Porridge (350 kcal); lunch: Dal and rice (650 kcal)", res["text"])
        self.assertEqual([i["food"] for i in res["items"]], ["Porridge", "Dal and rice"])
        self.assertEqual(res["links"], [{"label": "Calorie Tracker", "panel": "/a1b2c3d4_calorie_tracker"}])
        blob = json.dumps(res)
        self.assertNotIn("Secret cake", blob)                      # Bob's day
        self.assertNotIn("70.5", blob)                             # weight

    def test_goals_and_other_days(self):
        self.assertEqual(self.c.put("/api/goals", json={"calorie_goal": 800}, headers=ALICE).status_code, 200)
        self.assertIn("1000 of 800 kcal (200 over)", call("u_alice")["text"])
        res = call("u_alice", date="2026-09-20")
        self.assertEqual(res["text"], "Nothing logged 2026-09-20; the goal is 800 kcal.")
        self.assertNack(call("u_alice", date="2026-09-22"), "invalid", "date")
        self.assertNack(call("u_alice", date="yesterday"), "invalid", "date")

    def test_switch_and_unknown_people(self):
        r = self.c.put("/api/admin/settings", json={"assistant_answers": False}, headers=ADMIN)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertNack(call("u_alice"), "not_allowed", "off")
        self.c.put("/api/admin/settings", json={"assistant_answers": True}, headers=ADMIN)
        self.assertNack(call("u_nobody"), "not_allowed", "no_access")

    def test_catalogue(self):
        tools.tools.check()
        self.assertEqual([t["name"] for t in tools.tools.spec()], ["calorie.today"])
        self.assertIn("assist.tool.call", app_bus.default._handlers)
