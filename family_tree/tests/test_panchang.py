"""Panchang (panchang.py), tithi anniversaries (§13.12) and milestones (§13.15).
Checked against published calendars for Hyderabad."""
from base import ADMIN, ApiTestCase, set_app_settings, sql
import _env  # noqa: F401

import unittest
from datetime import date, datetime
from unittest import mock
from zoneinfo import ZoneInfo

from app import config, panchang, reminders

IST = ZoneInfo("Asia/Kolkata")
HYD = (17.385, 78.4867)


class Calendar(unittest.TestCase):
    def test_ugadi(self):
        for year, want in ((2023, date(2023, 3, 22)), (2024, date(2024, 4, 9)), (2025, date(2025, 3, 30)),
                           (2026, date(2026, 3, 19))):
            self.assertEqual(panchang.date_for(year, 1, "shukla", 1, *HYD, IST, "sunrise"), want, year)

    def test_mahalaya_amavasya_aparahna(self):
        self.assertEqual(panchang.date_for(2024, 6, "krishna", 15, *HYD, IST), date(2024, 10, 2))
        self.assertEqual(panchang.date_for(2025, 6, "krishna", 15, *HYD, IST), date(2025, 9, 21))

    def test_adhika_months(self):
        adh = {(m["masa"], datetime.fromtimestamp((m["start"] - 2440587.5) * 86400).year)
               for m in panchang.lunar_months(2026) if m["adhika"]}
        self.assertIn((3, 2026), adh)                                     # Adhika Jyeshtha 2026
        self.assertTrue(any(m["adhika"] and m["masa"] == 5 for m in panchang.lunar_months(2023)))   # Adhika Shravana
        self.assertFalse(any(m["adhika"] for m in panchang.lunar_months(2025)
                             if datetime.fromtimestamp((m["start"] - 2440587.5) * 86400).year == 2025))

    def test_tithi_changing_during_the_day(self):
        opts = panchang.tithis_on(date(2026, 9, 26), *HYD, IST)
        self.assertEqual([(o["masa"], o["paksha"], o["tithi"]) for o in opts], [(6, "shukla", 15), (6, "krishna", 1)])
        self.assertEqual(panchang.name(6, "krishna", 15), "Bhadrapada Amavasya")
        self.assertTrue(panchang.name(7, "krishna", 10, "te").startswith("ఆశ్వయుజ"))

    def test_thousand_full_moons(self):
        d = panchang.full_moon_count_date(datetime(1945, 1, 1, 6, tzinfo=IST), 1000)
        self.assertEqual((d.year, d.month), (2025, 11))                    # 1000 × 29.53 days ≈ 80 years 10 months


class TithiApi(ApiTestCase):
    def setUp(self):
        super().setUp()
        config.set_timezone("Asia/Kolkata")
        self.addCleanup(config.set_timezone, "UTC")
        p = mock.patch.object(config, "today", return_value=date(2026, 9, 1))
        p.start()
        self.addCleanup(p.stop)
        self.pid = self.person("Seetha", "Sharma", gender="female", born={"d": 5, "m": 3, "y": 1930},
                               died={"d": 2, "m": 10, "y": 2024})
        self.death = sql("SELECT id FROM events WHERE person_id = ? AND type = 'death'", (self.pid,))[0]["id"]

    def test_computed_entered_override_undo(self):
        opts = self.ok(self.get(f"/api/events/{self.death}/tithi/options"))["options"]
        self.assertEqual((opts[0]["masa"], opts[0]["paksha"], opts[0]["tithi"]), (6, "krishna", 15))
        # a tithi not on that day can't be sent as "computed"
        self.assertEqual(self.put(f"/api/events/{self.death}/tithi",
                                  {"masa": 1, "paksha": "shukla", "tithi": 1, "source": "computed"}).status_code, 422)
        r = self.ok(self.put(f"/api/events/{self.death}/tithi",
                             {"masa": 6, "paksha": "krishna", "tithi": 15, "source": "computed"}))
        self.assertEqual(r["tithi"]["labelEn"], "Bhadrapada Amavasya")
        self.assertEqual(r["tithi"]["next"], "2026-10-10")
        detail = self.ok(self.get(f"/api/people/{self.pid}"))
        self.assertEqual(detail["tithi"]["death"]["next"], "2026-10-10")
        self.assertIsNone(detail["tithi"]["birth"])
        # Upcoming lists it (🪔)
        up = self.ok(self.get("/api/upcoming?days=60&scope=all"))["items"]
        t = [i for i in up if i["kind"] == "tithi"]
        self.assertEqual([(i["date"], i["tithiName"]) for i in t], [("2026-10-10", "Bhadrapada Amavasya")])
        self.assertEqual(self.ok(self.get("/api/upcoming?days=60&scope=all&tithi=false"))["items"], [])
        # the priest says the 11th
        r = self.ok(self.put(f"/api/tithi/{self.death}/2026", {"date": "2026-10-11"}))
        self.assertEqual((r["tithi"]["next"], r["tithi"]["nextOverridden"]), ("2026-10-11", True))
        yr = self.ok(self.get("/api/tithi/dates?year=2026"))["items"]
        self.assertEqual((yr[0]["date"], yr[0]["overridden"]), ("2026-10-11", True))
        # a change of rule doesn't touch the correction
        set_app_settings(tithi_rule="sunrise")
        self.assertEqual(self.ok(self.get(f"/api/people/{self.pid}"))["tithi"]["death"]["next"], "2026-10-11")
        self.ok(self.post(f"/api/history/{r['batchId']}/undo"))
        self.assertEqual(self.ok(self.get(f"/api/people/{self.pid}"))["tithi"]["death"]["nextOverridden"], False)
        # typed in instead; changing the tithi drops old corrections
        self.ok(self.put(f"/api/tithi/{self.death}/2027", {"date": "2027-09-29"}))
        r = self.ok(self.put(f"/api/events/{self.death}/tithi", {"masa": 7, "paksha": "krishna", "tithi": 10}))
        self.assertEqual(r["tithi"]["overrides"], [])
        self.assertEqual(r["tithi"]["source"], "entered")
        self.ok(self.delete(f"/api/events/{self.death}/tithi"))
        self.assertIsNone(self.ok(self.get(f"/api/people/{self.pid}"))["tithi"]["death"])

    def test_validation(self):
        self.assertEqual(self.put(f"/api/events/{self.death}/tithi", {"masa": 13, "paksha": "shukla", "tithi": 1}).status_code, 422)
        self.assertEqual(self.put(f"/api/events/{self.death}/tithi", {"masa": 1, "paksha": "x", "tithi": 1}).status_code, 422)
        self.assertEqual(self.put(f"/api/tithi/{self.death}/2026", {"date": "2026-10-11"}).status_code, 404)
        other = self.person("Nodate")
        ev = self.ok(self.post("/api/events", {"personId": other, "type": "death", "date": {"y": 2000}}), 201)
        eid = ev["event"]["id"] if "event" in ev else ev["id"]
        self.assertEqual(self.get(f"/api/events/{eid}/tithi/options").status_code, 422)

    def test_reminder_lines(self):
        self.ok(self.put(f"/api/events/{self.death}/tithi", {"masa": 6, "paksha": "krishna", "tithi": 15}))
        sql("UPDATE people SET remind = 1")
        sql("UPDATE settings SET value = 'x' || value WHERE key = 'tree_version'")
        from app import db, graph
        prefs = dict(reminders.DEFAULTS, enabled=True)
        with db.get_conn() as conn:
            g = graph.get(conn)
            on_lead = reminders.collect(g, prefs, None, date(2026, 10, 3), conn=conn)
            on_day = reminders.collect(g, prefs, None, date(2026, 10, 10), conn=conn)
            between = reminders.collect(g, prefs, None, date(2026, 10, 5), conn=conn)
            off = reminders.collect(g, dict(prefs, tithi=False), None, date(2026, 10, 10), conn=conn)
        self.assertEqual(reminders.format_digest(on_lead, date(2026, 10, 3)),
                         "🪔 Tithi for Seetha Sharma — Bhadrapada Amavasya on Sat 10 Oct")
        self.assertEqual(reminders.format_digest(on_day, date(2026, 10, 10)),
                         "🪔 Tithi for Seetha Sharma — Bhadrapada Amavasya today")
        self.assertEqual(between, [])
        self.assertEqual(off, [])


class Milestones(ApiTestCase):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(config, "today", return_value=date(2026, 9, 1))
        p.start()
        self.addCleanup(p.stop)

    def test_upcoming_and_reminders(self):
        ravi = self.person("Ravi", "K", gender="male", born={"d": 15, "m": 10, "y": 1966})     # 60 on 15 Oct
        self.person("Baby", "K", born={"d": 20, "m": 9, "y": 2025})                             # 1 on 20 Sep
        up = self.ok(self.get("/api/upcoming?days=60&scope=all"))["items"]
        by = {i["title"]: i for i in up}
        self.assertEqual(by["Ravi K turns 60"]["milestone"], "60th birthday")
        self.assertEqual(by["Baby K turns 1"]["milestone"], "First birthday")
        self.assertFalse(any(i["kind"] == "milestone" for i in up))      # folded into the birthday entries
        sql("UPDATE people SET remind = 1")
        sql("UPDATE settings SET value = 'x' || value WHERE key = 'tree_version'")
        from app import db, graph
        prefs = dict(reminders.DEFAULTS, enabled=True)
        with db.get_conn() as conn:
            g = graph.get(conn)
            e30 = reminders.collect(g, prefs, None, date(2026, 9, 15), conn=conn)
            e29 = reminders.collect(g, prefs, None, date(2026, 9, 16), conn=conn)
            g90 = reminders.collect(g, prefs, None, date(2026, 7, 17), conn=conn)
        self.assertEqual(reminders.format_digest(e30, date(2026, 9, 15)),
                         "🎉 Ravi K — 60th birthday, turns 60 on Thu 15 Oct (in 4 weeks)")
        self.assertEqual(e29, [])
        self.assertIn("in 3 months", reminders.format_digest(g90, date(2026, 7, 17)))
        self.assertIsNotNone(ravi)

    def test_admin_edits(self):
        items = self.ok(self.get("/api/milestones"))["items"]
        self.assertEqual(len(items), 8)
        self.assertEqual(self.post("/api/milestones", {"kind": "age", "value": 75, "labelEn": "75"}).status_code, 403)
        items = self.ok(self.post("/api/milestones", {"kind": "age", "value": 75, "labelEn": "Amrut Mahotsav",
                                                       "leadDays": [30, 0]}, user=ADMIN), 201)["items"]
        m = next(i for i in items if i["value"] == 75)
        self.assertEqual(m["leadDays"], [30, 0])
        self.assertEqual(self.post("/api/milestones", {"kind": "age", "value": 75, "labelEn": "x"}, user=ADMIN).status_code, 409)
        sixty = next(i for i in items if i["value"] == 60 and i["kind"] == "age")
        self.ok(self.patch(f"/api/milestones/{sixty['id']}", {"enabled": False}, user=ADMIN))
        self.person("Ravi", "K", born={"d": 15, "m": 10, "y": 1966})
        up = self.ok(self.get("/api/upcoming?days=60&scope=all"))["items"]
        self.assertNotIn("milestone", up[0])
        self.ok(self.delete(f"/api/milestones/{m['id']}", user=ADMIN))
        self.assertEqual(len(self.ok(self.get("/api/milestones"))["items"]), 8)


if __name__ == "__main__":
    unittest.main()
