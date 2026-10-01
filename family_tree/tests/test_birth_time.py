"""Time of birth and death."""
from base import ApiTestCase, sql
import _env  # noqa: F401

import unittest

from app import config
from app.export_view import ExportOptions, build
from app import db


class BirthTime(ApiTestCase):
    def test_set_show_validate_undo(self):
        pid = self.ok(self.post("/api/people", {"given_names": "Asha", "birth": {
                "date": {"d": 26, "m": 9, "y": 2026}, "time": "05:42", "place": "Guntur"}}), 201)["person"]["id"]
        d = self.detail(pid)
        self.assertEqual(d["birth"]["time"], "05:42")
        birth = next(e for e in d["events"] if e["type"] == "birth")
        self.assertEqual(birth["time"], "05:42")
        # validation
        for bad in ({"date": {"d": 26, "m": 9, "y": 2026}, "time": "5:42"}, {"date": {"d": 26, "m": 9, "y": 2026}, "time": "24:00"},
                    {"date": {"qual": "about", "d": 26, "m": 9, "y": 2026}, "time": "05:42"},
                    {"date": {"m": 9, "y": 2026}, "time": "05:42"}, {"date": {"d": 26, "m": 9}, "time": "05:42"},
                    {"place": "Guntur", "time": "05:42"}):
            self.assertEqual(self.patch(f"/api/people/{pid}", {"birth": bad}).status_code, 422, bad)
        # change it, then undo
        r = self.ok(self.patch(f"/api/people/{pid}", {"birth": {"date": {"d": 26, "m": 9, "y": 2026}, "time": "23:10", "place": "Guntur"}}))
        self.assertEqual(self.detail(pid)["birth"]["time"], "23:10")
        self.ok(self.post(f"/api/history/{r['batchId']}/undo"))
        self.assertEqual(self.detail(pid)["birth"]["time"], "05:42")
        # the events route: keeping the date keeps the time; an approximate date drops it
        self.ok(self.patch(f"/api/events/{birth['id']}", {"place": "Tenali"}))
        self.assertEqual(self.detail(pid)["birth"]["time"], "05:42")
        self.ok(self.patch(f"/api/events/{birth['id']}", {"time": None}))
        self.assertIsNone(self.detail(pid)["birth"]["time"])
        self.ok(self.patch(f"/api/events/{birth['id']}", {"time": "06:00"}))
        self.ok(self.patch(f"/api/events/{birth['id']}", {"date": {"qual": "about", "y": 2026}}))
        self.assertIsNone(self.detail(pid)["birth"]["time"])
        # only births and deaths have a time
        self.assertEqual(self.post("/api/events", {"personId": pid, "type": "residence", "date": {"d": 1, "m": 1, "y": 2020},
                                                   "time": "10:00"}).status_code, 422)
        self.ok(self.post("/api/events", {"personId": pid, "type": "death", "date": {"d": 1, "m": 1, "y": 2090},
                                          "time": "10:00"}), 201)
        self.assertEqual(self.detail(pid)["death"]["time"], "10:00")

    def test_tithi_at_the_time(self):
        config.set_timezone("Asia/Kolkata")
        self.addCleanup(config.set_timezone, "UTC")
        pid = self.ok(self.post("/api/people", {"given_names": "Asha", "birth": {
            "date": {"d": 26, "m": 9, "y": 2026}}}), 201)["person"]["id"]
        eid = sql("SELECT id FROM events WHERE person_id = ?", (pid,))[0]["id"]
        r = self.ok(self.get(f"/api/events/{eid}/tithi/options"))
        self.assertEqual(len(r["options"]), 2)                      # Pournami ends at 22:18 that night
        self.ok(self.patch(f"/api/events/{eid}", {"time": "23:00"}))
        r = self.ok(self.get(f"/api/events/{eid}/tithi/options"))
        self.assertEqual(r["atTime"], "23:00")
        self.assertEqual([(o["masa"], o["paksha"], o["tithi"]) for o in r["options"]], [(6, "krishna", 1)])
        self.ok(self.patch(f"/api/events/{eid}", {"time": "10:00"}))
        r = self.ok(self.get(f"/api/events/{eid}/tithi/options"))
        self.assertEqual([(o["masa"], o["paksha"], o["tithi"]) for o in r["options"]], [(6, "shukla", 15)])
        # before sunrise belongs to the moment, not the civil day's sunrise tithi
        self.ok(self.patch(f"/api/events/{eid}", {"time": "03:00"}))
        r = self.ok(self.get(f"/api/events/{eid}/tithi/options"))
        self.assertEqual(len(r["options"]), 1)
        self.ok(self.put(f"/api/events/{eid}/tithi", {**{k: r["options"][0][k] for k in ("masa", "paksha", "tithi")},
                                                    "source": "computed"}))

    def test_export(self):
        pid = self.ok(self.post("/api/people", {"given_names": "Venkat", "deceased": True, "birth": {
            "date": {"d": 10, "m": 3, "y": 1930}, "time": "05:42"}, "death": {"date": {"d": 2, "m": 10, "y": 2024}, "time": "14:05"}}), 201)["person"]["id"]
        with db.get_conn() as conn:
            full = build(conn, ExportOptions(), None).people[pid]
            years = build(conn, ExportOptions(dates="year"), None).people[pid]
        self.assertEqual(full["birth"]["time"], "05:42")
        self.assertEqual(full["events"][0]["date"], "10 March 1930, 05:42")
        self.assertIsNone(years["birth"]["time"])
        self.assertEqual(years["events"][0]["date"], "1930")


if __name__ == "__main__":
    unittest.main()
