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


def call(uid, tool="calorie.today", confirm=False, **args):
    now = datetime.now(timezone.utc)
    env = {"id": app_bus.new_ulid(now), "v": 1, "from": "household_assistant", "to": "calorie_tracker",
           "kind": "assist.tool.call", "kv": 1, "reply_to": None, "ref": None, "sent": now.isoformat(),
           "expires": (now + timedelta(seconds=20)).isoformat(),
           "data": {"tool": tool, "args": args, "requested_by": uid, "question": "q1"}}
    if confirm:
        env["data"].update(confirm=True, confirmed_at=now.isoformat())
    with db.get_conn() as conn:
        try:
            return tools.tools.on_call(app_bus.Message(env), conn)
        except app_bus.Nack as n:
            conn.rollback()
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
        self.assertEqual(res["links"], [{"label": "Food Log in Calorie Tracker", "panel": "/a1b2c3d4_calorie_tracker",
                                         "target": "/foodlog/2026-09-21"}])
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

    def test_persons_own_switch(self):
        me = self.c.get("/api/me", headers=ALICE).json()
        self.assertEqual((me["assistant"], me["assistantOk"]), (True, True))
        r = self.c.put("/api/me/assistant", json={"assistantOk": False}, headers=ALICE)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(r.json()["assistantOk"])
        self.assertNack(call("u_alice"), "not_allowed", "person_off")
        self.assertIn("text", call("u_bob"))
        self.assertEqual(self.c.put("/api/me/assistant", json={"assistantOk": "no"}, headers=ALICE).status_code, 422)
        # an admin "acting as" someone changes only their own switch
        self.c.put("/api/me/assistant?as_user=u_bob", json={"assistantOk": False}, headers=ADMIN)
        self.assertIn("text", call("u_bob"))

    def test_week(self):
        self.log(ALICE, "Toast", calories=2400, date="2026-09-19")
        res = call("u_alice", tool="calorie.week")
        self.assertEqual([i["date"] for i in res["items"]][0], "2026-09-15")
        self.assertEqual([i["kcal"] for i in res["items"]], [0, 0, 0, 0, 2400, 0, 1000])
        self.assertIn("2 of 7 days logged, 1700 kcal a day on average (goal 2000); 1 day over the goal.", res["text"])
        self.assertIn("Sat 2400", res["text"])
        self.assertNotIn("900", json.dumps(res))                 # Bob's cake
        res = call("u_alice", tool="calorie.week", date="2026-09-14")
        self.assertTrue(res["text"].startswith("Nothing logged from 2026-09-08 to 2026-09-14"))
        self.assertNack(call("u_alice", tool="calorie.week", date="2026-09-22"), "invalid", "date")

    def test_log_food_needs_the_tap(self):
        self.assertNack(call("u_alice", tool="calorie.food.log", food="Apple", calories=95), "not_allowed", "confirm")
        res = call("u_alice", tool="calorie.food.log", confirm=True, food="Apple", calories=95, carbs=25, meal="snack")
        self.assertEqual(res["text"], "Logged Apple (95 kcal) for snack today. Today: 1095 of 2000 kcal.")
        logs = self.c.get("/api/logs?date=2026-09-21", headers=ALICE).json()
        self.assertEqual([(l["food_name"], l["meal_type"], l["calories"], l["carbs"]) for l in logs][-1],
                         ("Apple", "snack", 95.0, 25.0))
        # a food logged before: its calories per serving, times the servings
        res = call("u_alice", tool="calorie.food.log", confirm=True, food="porridge", servings=2, meal="breakfast")
        self.assertIn("Logged porridge (700 kcal) for breakfast", res["text"])
        # a saved food
        r = self.c.post("/api/saved-foods", json={"name": "Protein shake", "calories": 200, "protein": 30},
                        headers=ALICE)
        self.assertIn(r.status_code, (200, 201), r.text)
        res = call("u_alice", tool="calorie.food.log", confirm=True, food="Protein Shake", meal="snack")
        self.assertIn("(200 kcal)", res["text"])
        before = len(self.c.get("/api/logs?date=2026-09-21", headers=ALICE).json())
        res = call("u_alice", tool="calorie.food.log", confirm=True, food="Mystery stew")
        self.assertIn("I don't know the calories in “Mystery stew”", res["text"])
        self.assertEqual(len(self.c.get("/api/logs?date=2026-09-21", headers=ALICE).json()), before)
        self.assertNack(call("u_alice", tool="calorie.food.log", confirm=True, food="Pie", calories=300,
                             date="2026-09-22"), "invalid", "date")
        self.assertNack(call("u_alice", tool="calorie.food.log", confirm=True, food="Pie", calories=300, meal="brunch"),
                        "invalid", "meal")

    def test_sub_paths_redirect_to_the_page(self):
        for path, where in (("/foodlog", "../#/foodlog"), ("/foodlog/2026-09-20", "../../#/foodlog/2026-09-20"),
                            ("/dashboard", "../#/dashboard")):
            r = self.c.get(path, headers=ALICE, follow_redirects=False)
            self.assertEqual((r.status_code, r.headers["location"]), (307, where), path)
        self.assertEqual(self.c.get("/api/me", headers=ALICE).json()["page"], "/a1b2c3d4_calorie_tracker")

    def test_catalogue(self):
        tools.tools.check()
        self.assertEqual([t["name"] for t in tools.tools.spec()], ["calorie.today", "calorie.week", "calorie.food.log"])
        self.assertIn("assist.tool.call", app_bus.default._handlers)
