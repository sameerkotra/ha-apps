"""Taking turns (SPEC §5.4b): a household schedule item whose days go to people in order — counted from the anchor,
so a skipped week doesn't shift the turns; a moved day stays its original turn; one turn handed to someone else;
the calendar, the "me" filters, the sensor, the dashboard and the digest follow whose turn it is."""
import _env  # noqa: F401

from datetime import date

from test_api import ADMIN, ALICE, BOB, NOW, ApiBase
from app import config, db, reminders, schedule_logic


class Turns(ApiBase):
    def setUp(self):
        super().setUp()
        self.alice, self.bob, self.adm = self.uid("Alice"), self.uid("Bob"), self.uid("Adminy")
        # bins every Monday from 21 Sep 2026 (NOW is Monday 21 Sep, 12:00 UTC): Alice, Bob, Adminy, Alice …
        r = self.post("/api/schedule", {"name": "Bins", "rule": "weeks:1:1", "anchor_date": "2026-09-21",
                                        "rotation": [self.alice, self.bob, self.adm]})
        self.assertEqual(r.status_code, 201, r.text)
        self.item = r.json()

    def turns(self, h=ALICE):
        it = next(i for i in self.get("/api/schedule", h).json() if i["id"] == self.item["id"])
        return [(u["date"], u.get("turnName"), u["status"]) for u in it["upcoming"]]

    def test_in_order_from_the_anchor(self):
        self.assertEqual(self.item["rotationNames"], ["Alice", "Bob", "Adminy"])
        self.assertEqual((self.item["nextDate"], self.item["nextTurnName"]), ("2026-09-21", "Alice"))
        self.assertEqual([t[1] for t in self.turns()][:5], ["Alice", "Bob", "Adminy", "Alice", "Bob"])
        # a skipped week keeps everyone else's week
        self.assertEqual(self.post(f"/api/schedule/{self.item['id']}/exceptions", {"kind": "skip", "date": "2026-09-28"}).status_code, 201)
        self.assertEqual(self.turns()[:3], [("2026-09-21", "Alice", "normal"), ("2026-09-28", "Bob", "skipped"),
                                            ("2026-10-05", "Adminy", "normal")])
        # a moved day is still its original day's turn
        self.assertEqual(self.post(f"/api/schedule/{self.item['id']}/exceptions",
                                   {"kind": "move", "date": "2026-10-05", "to_date": "2026-10-06"}).status_code, 201)
        self.assertIn(("2026-10-06", "Adminy", "moved_here"), self.turns())

    def test_hand_a_turn_over(self):
        url = f"/api/schedule/{self.item['id']}/turns/2026-09-28"
        r = self.put(url, {"user_id": self.alice}, BOB)                        # Bob gives his week to Alice
        self.assertEqual(r.status_code, 200, r.text)
        up = {u["date"]: u for u in r.json()["upcoming"]}
        self.assertEqual((up["2026-09-28"]["turnName"], up["2026-09-28"]["handedOver"]), ("Alice", True))
        self.assertEqual(up["2026-10-05"]["turnName"], "Adminy")                # the order after it is unchanged
        self.assertEqual(self.put(url, {"user_id": None}).json()["upcoming"][1]["turnName"], "Bob")
        self.assertEqual(self.put(f"/api/schedule/{self.item['id']}/turns/2026-09-29", {"user_id": self.bob}).status_code, 422)
        self.assertEqual(self.put(f"/api/schedule/{self.item['id']}/turns/2026-09-14", {"user_id": self.bob}).status_code, 422)
        self.assertEqual(self.put(url, {"user_id": "nobody"}).status_code, 422)

    def test_rules(self):
        base = {"name": "X", "rule": "daily", "anchor_date": "2026-09-21"}
        for extra in ({"rotation": [self.alice]}, {"rotation": [self.alice, self.alice]},
                      {"rotation": [self.alice, "ghost"]}, {"rotation": "alice"},
                      {"rotation": [self.alice, self.bob], "assigned_to": self.alice}):
            self.assertEqual(self.post("/api/schedule", {**base, **extra}).status_code, 422, extra)
        # turned off again: no turns, and handed-over turns go
        self.put(f"/api/schedule/{self.item['id']}/turns/2026-09-28", {"user_id": self.alice})
        r = self.patch(f"/api/schedule/{self.item['id']}", {"rotation": None})
        self.assertEqual((r.json()["rotation"], r.json()["nextTurn"]), ([], None))
        with db.get_conn() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM schedule_turns").fetchone()[0], 0)
        self.assertNotIn("turnName", r.json()["upcoming"][0])

    def test_calendar_filters_dashboard_sensor_and_digest(self):
        q = {"from": "2026-09-21", "to": "2026-10-11"}
        cal = self.get("/api/calendar", params=q).json()["schedule"]
        self.assertEqual([(e["date"], e["assigneeName"]) for e in cal],
                         [("2026-09-21", "Alice"), ("2026-09-28", "Bob"), ("2026-10-05", "Adminy")])
        mine = self.get("/api/calendar", BOB, params={**q, "assignee": "me"}).json()["schedule"]
        self.assertEqual([e["date"] for e in mine], ["2026-09-28"])
        self.assertEqual([i["name"] for i in self.get("/api/schedule", BOB, params={"assignee": "me"}).json()], ["Bins"])
        dash = self.get("/api/dashboard").json()["schedule"]
        self.assertEqual((dash[0]["name"], dash[0]["turnName"]), ("Bins", "Alice"))
        with db.get_conn() as c:
            item = schedule_logic.attach_turns(c, [dict(c.execute("SELECT * FROM schedule_items").fetchone())])[0]
            excs = schedule_logic.load_exceptions(c).get(item["id"], [])
            users, _ = schedule_logic.lookups(c)
            _e, _s, attrs = schedule_logic.sensor_payload(item, excs, NOW.date(), NOW, users)
            self.assertEqual(attrs["turn"], "Alice")
            got = reminders.collect_schedule_occurrences(c, self.bob, date(2026, 9, 21), date(2026, 10, 11))
            self.assertEqual([(o["dueDate"], o["title"]) for o in got], [("2026-09-28", "Bins (your turn)")])
