"""Maintenance (SPEC §14): the tab's API, Mark done / Undo / Snooze, suggestions, notifications,
the files folder, the calendar, sensors, history and admin settings."""
import _env  # noqa: F401  (must be first)

import io
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timezone

from app import config, db, maint_catalog as cat, maint_files, maint_notify, maintenance as mt, reminders, settings
from test_api import ADMIN, ALICE, BOB, NOW, ApiBase, link


def at(hh, mm=30, day=21):
    return datetime(2026, 9, day, hh, mm, tzinfo=timezone.utc)


class MaintBase(ApiBase):
    def setUp(self):
        super().setUp()
        maint_files.reset()
        config.LATITUDE = 40.0
        self.share = tempfile.mkdtemp(prefix="share_")
        config.SHARE_ROOT = self.share

    def tearDown(self):
        shutil.rmtree(self.share, ignore_errors=True)
        super().tearDown()

    def enable(self, **extra):
        r = self.put("/api/admin/maintenance", {"enabled": True, **extra}, ADMIN)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def item(self, h=ALICE, **kw):
        body = {"name": "Replace furnace filter", "category": "hvac", "every_n": 3, "every_unit": "month", **kw}
        r = self.post("/api/maintenance/items", body, h)
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()

    def overview(self, h=ALICE):
        r = self.get("/api/maintenance", h)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()


class TestTurningOn(MaintBase):
    def test_off_by_default(self):
        self.assertFalse(self.get("/api/whoami").json()["maintenance"]["enabled"])
        self.assertEqual(self.get("/api/maintenance").status_code, 409)
        self.assertEqual(self.post("/api/maintenance/items", {"name": "x"}).status_code, 409)

    def test_admin_only(self):
        self.assertEqual(self.put("/api/admin/maintenance", {"enabled": True}, ALICE).status_code, 403)
        self.assertEqual(self.get("/api/admin/maintenance", ALICE).status_code, 403)

    def test_turning_on_creates_the_list_and_makes_admins_recipients(self):
        out = self.enable()
        self.assertTrue(out["enabled"])
        self.assertEqual(out["recipients"], ["adm"])
        lists = self.lists()
        m = [l for l in lists if l["role"] == "maintenance"]
        self.assertEqual([(l["name"], l["kind"]) for l in m], [("Maintenance", "shared")])
        self.assertTrue(self.get("/api/whoami").json()["maintenance"]["enabled"])
        # can't delete it while on; can rename it
        self.assertEqual(self.delete(f"/api/lists/{m[0]['id']}").status_code, 409)
        self.assertEqual(self.patch(f"/api/lists/{m[0]['id']}", {"name": "House jobs"}).status_code, 200)
        # turning it off and on again doesn't make a second list or change recipients
        self.put("/api/admin/maintenance", {"enabled": False}, ADMIN)
        self.put("/api/admin/maintenance", {"recipients": [self.uid("Bob")]}, ADMIN)
        out = self.enable()
        self.assertEqual(out["recipients"], [self.uid("Bob")])
        self.assertEqual(len([l for l in self.lists() if l["role"] == "maintenance"]), 1)

    def test_validation(self):
        self.enable()
        bad = [({"recipients": ["nobody"]}, 422), ({"profile": ["spaceship"]}, 422), ({"colour": 1}, 422),
               ({"enabled": "yes"}, 422)]
        for body, code in bad:
            self.assertEqual(self.put("/api/admin/maintenance", body, ADMIN).status_code, code, body)
        ok = self.put("/api/admin/maintenance", {"profile": ["gutters", "furnace", "gutters"]}, ADMIN).json()
        self.assertEqual(ok["profile"], ["furnace", "gutters"])


class TestItems(MaintBase):
    def setUp(self):
        super().setUp()
        self.enable()

    def test_interval_item_due_done_undo(self):
        it = self.item(last_done="2026-06-01")                  # + 3 months = 1 Sep: overdue on 21 Sep
        self.assertEqual((it["dueDate"], it["status"], it["daysUntil"]), ("2026-09-01", "overdue", -20))
        self.assertEqual(it["repeatLabel"], "Every 3 months after last done")
        r = self.post(f"/api/maintenance/items/{it['id']}/done", {"note": "MERV 11", "cost": 24.5})
        self.assertEqual(r.status_code, 201, r.text)
        after = r.json()["item"]
        self.assertEqual((after["lastDone"], after["dueDate"], after["status"]), ("2026-09-21", "2026-12-21", "upcoming"))
        self.assertEqual(after["lastRecord"], {"date": "2026-09-21", "by": "Alice"})
        hist = self.get("/api/maintenance/history").json()
        self.assertEqual([(x["itemName"], x["note"], x["cost"], x["dueWas"]) for x in hist["records"]],
                         [("Replace furnace filter", "MERV 11", 24.5, "2026-09-01")])
        self.assertEqual(hist["yearTotals"], {"2026": 24.5})
        back = self.post(f"/api/maintenance/items/{it['id']}/undo").json()
        self.assertEqual((back["lastDone"], back["dueDate"], back["doneCount"]), ("2026-06-01", "2026-09-01", 0))
        self.assertEqual(self.post(f"/api/maintenance/items/{it['id']}/undo").status_code, 409)

    def test_never_done_is_due_now_and_earlier_dates(self):
        it = self.item()
        self.assertEqual((it["dueDate"], it["status"]), ("2026-09-21", "due"))
        # done a while ago; an older back-filled record doesn't move last_done backwards
        self.post(f"/api/maintenance/items/{it['id']}/done", {"date": "2026-09-10"})
        after = self.post(f"/api/maintenance/items/{it['id']}/done", {"date": "2026-08-01"}).json()["item"]
        self.assertEqual((after["lastDone"], after["dueDate"]), ("2026-09-10", "2026-12-10"))
        for body in ({"date": "2026-09-22"}, {"cost": -1}, {"note": "x" * 501}, {"date": "nope"}):
            self.assertEqual(self.post(f"/api/maintenance/items/{it['id']}/done", body).status_code, 422, body)

    def test_months_clamp_to_month_end(self):
        it = self.item(every_n=1, every_unit="month", last_done="2026-08-31")
        self.assertEqual(it["dueDate"], "2026-09-30")

    def test_calendar_item(self):
        it = self.item(name="Clean the gutters", mode="calendar", rule="months:6:1", anchor_date="2025-04-01",
                       every_n=None, every_unit=None)
        self.assertEqual((it["dueDate"], it["status"]), ("2025-04-01", "overdue"))
        self.assertEqual(it["repeatLabel"], "Every 6 months on the 1st")
        # three occurrences missed: marking done today clears through the latest one (1 Apr 2026)
        after = self.post(f"/api/maintenance/items/{it['id']}/done").json()["item"]
        self.assertEqual((after["clearedThrough"], after["dueDate"]), ("2026-04-01", "2026-10-01"))
        # done early: clears the next one without moving the dates after it
        after = self.post(f"/api/maintenance/items/{it['id']}/done").json()["item"]
        self.assertEqual(after["dueDate"], "2027-04-01")

    def test_snooze(self):
        it = self.item(last_done="2026-06-01")
        s = self.post(f"/api/maintenance/items/{it['id']}/snooze", {"days": 7}).json()
        self.assertEqual((s["dueDate"], s["snoozedTo"], s["status"]), ("2026-09-28", "2026-09-28", "due"))
        s = self.post(f"/api/maintenance/items/{it['id']}/snooze", {"until": "2026-10-15"}).json()
        self.assertEqual((s["dueDate"], s["status"]), ("2026-10-15", "upcoming"))
        self.assertEqual(self.post(f"/api/maintenance/items/{it['id']}/snooze", {"until": "2026-09-01"}).status_code, 422)
        s = self.post(f"/api/maintenance/items/{it['id']}/snooze", {"until": None}).json()
        self.assertEqual((s["dueDate"], s["snoozedTo"]), ("2026-09-01", None))
        # marking done clears a snooze
        self.post(f"/api/maintenance/items/{it['id']}/snooze", {"days": 3})
        after = self.post(f"/api/maintenance/items/{it['id']}/done").json()["item"]
        self.assertIsNone(after["snoozedTo"])

    def test_pause_and_edit(self):
        it = self.item(last_done="2026-06-01")
        p = self.patch(f"/api/maintenance/items/{it['id']}", {"paused": True}).json()
        self.assertEqual((p["status"], p["dueDate"]), ("paused", None))
        e = self.patch(f"/api/maintenance/items/{it['id']}", {"paused": False, "every_n": 6, "name": "Filter",
                                                                "icon": "🌬️", "lead_days": 14}).json()
        self.assertEqual((e["name"], e["dueDate"], e["icon"], e["leadDays"]), ("Filter", "2026-12-01", "🌬️", 14))
        # switch to calendar and back
        c = self.patch(f"/api/maintenance/items/{it['id']}", {"mode": "calendar", "rule": "years:1",
                                                              "anchor_date": "2026-10-01"}).json()
        self.assertEqual((c["mode"], c["dueDate"], c["everyN"]), ("calendar", "2026-10-01", None))
        i = self.patch(f"/api/maintenance/items/{it['id']}", {"mode": "interval", "every_n": 1, "every_unit": "year"}).json()
        self.assertEqual((i["mode"], i["dueDate"]), ("interval", "2026-09-21"))

    def test_validation(self):
        bad = [{"name": ""}, {"name": "x" * 61}, {"category": "space"}, {"every_n": 0}, {"every_unit": "fortnight"},
               {"every_n": 40, "every_unit": "month"}, {"last_done": "2026-12-01"}, {"lead_days": 61},
               {"overdue_every": 5}, {"icon": "abc"}, {"url": "javascript:alert(1)"}, {"mode": "calendar", "rule": "bad"},
               {"recipients": ["nobody"]}, {"paused": 1}, {"colour": "red"}]
        for extra in bad:
            body = {"name": "Thing", "every_n": 1, "every_unit": "month", **extra}
            self.assertIn(self.post("/api/maintenance/items", body).status_code, (422, 404), extra)

    def test_overview_and_delete(self):
        a = self.item(name="A", last_done="2026-06-01")         # overdue
        b = self.item(name="B", last_done="2026-09-01", every_n=4, every_unit="week", lead_days=10)   # 29 Sep: due
        c = self.item(name="C", last_done="2026-09-20")         # upcoming
        o = self.overview()
        self.assertEqual([(x["name"], x["status"]) for x in o["items"]], [("A", "overdue"), ("B", "due"), ("C", "upcoming")])
        self.assertEqual(o["overdueCount"], 1)
        self.assertFalse(o["files"]["configured"])
        self.assertEqual(self.delete(f"/api/maintenance/items/{b['id']}").status_code, 204)
        self.assertEqual([x["name"] for x in self.overview()["items"]], ["A", "C"])
        del a, c

    def test_jobs_are_tasks_in_the_maintenance_list(self):
        lid = self.overview()["listId"]
        self.add("Fix the dripping tap", list_id=lid, due_date="2026-09-25")
        o = self.overview()
        self.assertEqual([j["title"] for j in o["jobs"]], ["Fix the dripping tap"])
        self.assertEqual(o["jobs"][0]["listRole"], "maintenance")

    def test_calendar_shows_due_dates_and_projections(self):
        self.item(name="Filter", last_done="2026-06-01", assigned_to=self.uid("Bob"))
        cal = self.get("/api/calendar", params={"from": "2026-09-01", "to": "2026-12-01"}).json()
        got = [(e["date"], e["status"]) for e in cal["maintenance"]]
        self.assertEqual(got, [("2026-09-21", "overdue"), ("2026-12-01", "projected")])
        self.assertEqual(self.get("/api/calendar", params={"from": "2026-09-01", "to": "2026-09-30", "maintenance": 0}).json()["maintenance"], [])
        self.assertEqual(self.get("/api/calendar", params={"from": "2026-09-01", "to": "2026-09-30", "assignee": "me"}).json()["maintenance"], [])

    def test_history_csv(self):
        it = self.item()
        self.post(f"/api/maintenance/items/{it['id']}/done", {"cost": 10, "note": 'Said "fine"'})
        r = self.get("/api/maintenance/history.csv?year=2026")
        self.assertEqual(r.status_code, 200)
        text = r.content.decode("utf-8-sig")
        self.assertIn("Date,Item,Category,Done by,Note", text)
        self.assertIn('2026-09-21,Replace furnace filter,Heating & cooling,Alice,"Said ""fine""",10.00', text)
        self.assertEqual(self.get("/api/maintenance/history?year=abc").status_code, 422)


class TestLookupsOct13(MaintBase):
    def old_lookups(self, conn):
        """maintenance.lookups as it was before the 13 October review (every item, done history in Python)."""
        users = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM users")}
        places = {r["id"]: dict(r) for r in conn.execute("SELECT id, name, address, drive_minutes FROM places")}
        custom = {r["item_id"]: [] for r in conn.execute("SELECT DISTINCT item_id FROM maint_recipients")}
        for r in conn.execute("SELECT item_id, user_id FROM maint_recipients"):
            custom[r["item_id"]].append(r["user_id"])
        files = {}
        for r in conn.execute("SELECT * FROM maint_files WHERE item_id IS NOT NULL ORDER BY created_at"):
            files.setdefault(r["item_id"], []).append(mt.file_json(r))
        last, counts = {}, {}
        for r in conn.execute("SELECT item_id, done_on, done_by, created_at FROM maint_done ORDER BY done_on, created_at"):
            last[r["item_id"]] = dict(r)
            counts[r["item_id"]] = counts.get(r["item_id"], 0) + 1
        return {"users": users, "places": places, "custom": custom, "files": files, "last": last, "counts": counts}

    def test_same_answers_as_before(self):
        import random
        rng = random.Random(3)
        self.enable(recipients=[self.uid("Alice")])
        items = [self.item(name=f"Item {i}") for i in range(8)]
        with db.get_conn() as conn:
            for n in range(80):
                it = rng.choice(items)
                done_on = f"2026-0{rng.randint(1, 9)}-{rng.randint(10, 28)}"
                created = f"2026-09-{rng.randint(10, 20)}T10:00:{rng.randint(10, 59)}+00:00"
                conn.execute("INSERT INTO maint_done (id, item_id, done_on, done_by, prev_state, created_at) "
                             "VALUES (?, ?, ?, ?, '{}', ?)",
                             (f"d{n}", it["id"], done_on, self.uid("Bob"), created))
            for n in range(20):
                conn.execute("INSERT INTO maint_files (id, item_id, rel_path, name, size, created_at) VALUES (?, ?, ?, ?, 1, ?)",
                             (f"f{n}", rng.choice(items)["id"], f"x/{n}", f"{n}.pdf", f"2026-09-{10 + n}T00:00:00+00:00"))
            for it in items[:3]:
                conn.execute("INSERT INTO maint_recipients (item_id, user_id) VALUES (?, ?)", (it["id"], self.uid("Bob")))
            old = self.old_lookups(conn)
            self.assertEqual(mt.lookups(conn), old)
            for it in items:
                one = mt.lookups(conn, [it["id"]])
                for key in ("custom", "files", "last", "counts"):
                    self.assertEqual(one[key], {k: v for k, v in old[key].items() if k == it["id"]}, key)
                self.assertEqual(mt.serialize(mt.load_item(conn, it["id"]), config.today(), one),
                                 mt.serialize(mt.load_item(conn, it["id"]), config.today(), old))


class TestSuggestions(MaintBase):
    def setUp(self):
        super().setUp()
        self.enable()

    def keys(self):
        return {s["key"] for s in self.get("/api/maintenance/suggestions").json()["items"]}

    def test_profile_filters_and_hiding(self):
        k = self.keys()
        self.assertIn("b:test_alarms", set()) if False else None
        self.assertNotIn("b:furnace_filter", k)            # needs a furnace
        self.assertIn("b:roof_check", k)                    # needs nothing
        self.put("/api/admin/maintenance", {"profile": ["furnace", "gutters"]}, ADMIN)
        k = self.keys()
        self.assertIn("b:furnace_filter", k)
        self.assertEqual(self.put("/api/maintenance/suggestions/b:roof_check/hidden", None).status_code, 204)
        s = self.get("/api/maintenance/suggestions").json()
        self.assertNotIn("b:roof_check", {x["key"] for x in s["items"]})
        self.assertIn("b:roof_check", {x["key"] for x in s["hidden"]})
        self.delete("/api/maintenance/suggestions/b:roof_check/hidden")
        self.assertIn("b:roof_check", self.keys())
        self.assertEqual(self.put("/api/maintenance/suggestions/b:nope/hidden", None).status_code, 404)
        # adding one takes it out of the suggestions
        self.item(name="Filter", suggestion_key="b:furnace_filter")
        self.assertNotIn("b:furnace_filter", self.keys())

    def test_seasonal_defaults_and_order(self):
        self.put("/api/admin/maintenance", {"profile": ["gutters"]}, ADMIN)
        s = self.get("/api/maintenance/suggestions").json()
        self.assertEqual((s["season"], s["south"]), ("autumn", False))      # 21 Sep, northern hemisphere
        g = next(x for x in s["items"] if x["key"] == "b:gutters")
        self.assertEqual(g["defaults"], {"mode": "calendar", "rule": "months:6:1", "anchor_date": "2027-03-01"})
        self.assertTrue(g["inSeason"])
        self.assertTrue(s["items"][0]["inSeason"])                          # in-season ones come first
        config.LATITUDE = -33.9
        s = self.get("/api/maintenance/suggestions").json()
        self.assertEqual(s["season"], "spring")

    def test_admin_custom_suggestions(self):
        body = {"name": "Descale kettle", "icon": "🫖", "category": "appliances", "every_n": 2, "every_unit": "month",
                "why": "Scale", "howto": "Boil vinegar\nRinse"}
        out = self.post("/api/admin/maintenance/suggestions", body, ADMIN).json()
        sid = out["custom"][0]["id"]
        s = next(x for x in self.get("/api/maintenance/suggestions").json()["items"] if x["key"] == "c:" + sid)
        self.assertEqual((s["steps"], s["custom"], s["repeatLabel"]), (["Boil vinegar", "Rinse"], True, "Every 2 months after last done"))
        self.assertEqual(self.post("/api/admin/maintenance/suggestions", body, ALICE).status_code, 403)
        self.assertEqual(self.post("/api/admin/maintenance/suggestions", dict(body, seasons=["monsoon"]), ADMIN).status_code, 422)
        self.patch(f"/api/admin/maintenance/suggestions/{sid}", dict(body, name="Descale the kettle", seasons=["winter"]), ADMIN)
        s = next(x for x in self.get("/api/maintenance/suggestions").json()["items"] if x["key"] == "c:" + sid)
        self.assertEqual((s["name"], s["defaults"]["mode"]), ("Descale the kettle", "calendar"))
        self.delete(f"/api/admin/maintenance/suggestions/{sid}", ADMIN)
        self.assertNotIn("c:" + sid, self.keys())

    def test_season_maths(self):
        from datetime import date
        self.assertEqual(cat.season_of(date(2026, 1, 5), False), "winter")
        self.assertEqual(cat.season_start("winter", date(2027, 1, 5), False), date(2026, 12, 1))
        self.assertEqual(cat.season_of(date(2026, 1, 5), True), "summer")
        self.assertEqual(cat.season_key(date(2027, 2, 1), False), "2026-winter")
        self.assertEqual(cat.calendar_rule_for(["autumn"], date(2026, 9, 21), False), ("years:1", "2027-09-01"))


class TestNotifications(MaintBase):
    def setUp(self):
        super().setUp()
        link("alice", "notify.mobile_app_alice")
        link("bob", "notify.mobile_app_bob")
        link("adminy", "notify.mobile_app_admin")
        self.enable(recipients=[self.uid("Alice")])

    def sent(self):
        return [(s, b["message"]) for s, b in self.ha.notifications()]

    def run_at(self, hh, day=21):
        return maint_notify.run_pass_blocking(now=at(hh, day=day))

    def test_nothing_is_worked_out_while_its_nobodys_time(self):
        from unittest import mock
        self.item(name="Filter", last_done="2026-06-01")
        with mock.patch.object(mt, "all_items", side_effect=AssertionError("worked out")):
            self.assertEqual(self.run_at(7), 0)
            self.assertEqual(self.run_at(12), 0)
        self.assertEqual(self.run_at(8), 1)

    def test_a_late_time_reaches_past_midnight(self):
        self.item(name="Filter", last_done="2026-06-01")
        with db.get_conn() as conn:
            conn.execute("UPDATE user_prefs SET digest_time = '23:45' WHERE user_id = ?", (self.uid("Alice"),))
            if not conn.execute("SELECT 1 FROM user_prefs WHERE user_id = ?", (self.uid("Alice"),)).fetchone():
                conn.execute("INSERT INTO user_prefs (user_id, digest_time) VALUES (?, '23:45')", (self.uid("Alice"),))
        self.assertEqual(maint_notify.run_pass_blocking(now=datetime(2026, 9, 22, 0, 20, tzinfo=timezone.utc)), 1)
        (svc, msg), = self.sent()
        self.assertEqual(msg, "• Filter — 20 days overdue")                    # Monday's notification

    def test_one_grouped_message_at_their_time(self):
        self.item(name="Filter", last_done="2026-06-01")                                   # overdue
        self.item(name="Alarms", every_n=1, every_unit="month", last_done="2026-08-21")    # due today
        self.item(name="Gutters", every_n=1, every_unit="month", last_done="2026-08-25")   # due in 4 days
        self.assertEqual(self.run_at(7), 0)                     # before 08:00
        self.assertEqual(self.run_at(8), 1)
        (svc, msg), = self.sent()
        self.assertEqual(svc, "mobile_app_alice")
        self.assertEqual(msg.split("\n"), ["• Filter — 20 days overdue", "• Alarms — due today", "• Gutters — due Fri 25 Sep"])
        self.ha.requests.clear()
        self.assertEqual(self.run_at(8), 0)                     # logged: never twice
        for x in self.overview()["items"]:
            if x["name"] != "Filter":
                self.delete(f"/api/maintenance/items/{x['id']}")
        # overdue repeats every 7 days; due-soon doesn't
        self.assertEqual(maint_notify.run_pass_blocking(now=at(8, day=27)), 0)
        self.assertEqual(maint_notify.run_pass_blocking(now=at(8, day=28)), 1)
        self.assertEqual([m for _, m in self.sent()], ["• Filter — 27 days overdue"])

    def test_recipients_mute_disabled_custom_assignee(self):
        it = self.item(name="Filter", last_done="2026-06-01")
        self.put("/api/prefs", {"maintenanceNotify": False}, ALICE)
        self.assertEqual(self.run_at(8), 0)
        self.put("/api/prefs", {"maintenanceNotify": True}, ALICE)
        self.patch(f"/api/maintenance/items/{it['id']}", {"recipients_mode": "custom", "recipients": [self.uid("Bob")],
                                                          "assigned_to": self.uid("Adminy")})
        self.assertEqual(self.run_at(8), 2)
        self.assertEqual(sorted(s for s, _ in self.sent()), ["mobile_app_admin", "mobile_app_bob"])
        # a disabled person gets nothing
        self.ha.requests.clear()
        self.patch(f"/api/users/{self.uid('Bob')}", {"disabled": True}, ADMIN)
        self.assertEqual(maint_notify.run_pass_blocking(now=at(8, day=28)), 1)
        self.assertEqual([s for s, _ in self.sent()], ["mobile_app_admin"])

    def test_own_time_and_never_overdue(self):
        self.put("/api/prefs", {"digestTime": "18:00"}, ALICE)
        it = self.item(name="Filter", last_done="2026-06-01", overdue_every=0)
        self.assertEqual(self.run_at(8), 0)
        self.assertEqual(self.run_at(18), 0)                    # overdue, and overdue reminders are off
        self.patch(f"/api/maintenance/items/{it['id']}", {"overdue_every": 3})
        self.assertEqual(self.run_at(18), 1)

    def test_unassigned_job_due_today(self):
        lid = self.get("/api/maintenance").json()["listId"]
        self.add("Fix the tap", list_id=lid, due_date="2026-09-21")
        self.add("Paint the fence", list_id=lid, due_date="2026-09-21", assigned_to=self.uid("Bob"))
        self.assertEqual(self.run_at(8), 1)
        self.assertEqual(self.sent(), [("mobile_app_alice", "• 🔧 Fix the tap — job due today")])

    def test_season_suggestions_once(self):
        self.put("/api/admin/maintenance", {"profile": ["gutters"]}, ADMIN)
        config.utcnow = lambda: datetime(2026, 9, 3, 12, tzinfo=timezone.utc)
        self.assertEqual(maint_notify.run_pass_blocking(now=datetime(2026, 9, 3, 8, 30, tzinfo=timezone.utc)), 1)
        (_, msg), = self.sent()
        self.assertTrue(msg.startswith("• Autumn: "), msg)
        self.assertEqual(maint_notify.run_pass_blocking(now=datetime(2026, 9, 4, 8, 30, tzinfo=timezone.utc)), 0)
        # outside the first two weeks nothing is sent
        self.ha.requests.clear()
        maint_notify.run_pass_blocking(now=at(8))
        self.assertEqual(self.sent(), [])

    def test_off_sends_nothing(self):
        self.item(name="Filter", last_done="2026-06-01")
        self.put("/api/admin/maintenance", {"enabled": False}, ADMIN)
        self.assertEqual(self.run_at(8), 0)

    def test_weekly_summary_has_a_maintenance_section(self):
        self.item(name="Filter", last_done="2026-06-01")
        self.put("/api/prefs", {"notificationsEnabled": True, "weeklySummary": {"enabled": True, "dayOfWeek": 1, "time": "18:00"}})
        n = reminders.run_weekly_pass_blocking(now=at(18))
        self.assertEqual(n, 1)
        msg = [m for s, m in self.sent() if s == "mobile_app_alice"][-1]
        self.assertIn("Maintenance:\n• 🔧 Filter — 20 days overdue", msg)

    def test_prefs_show_recipient(self):
        self.assertTrue(self.get("/api/prefs", ALICE).json()["maintenanceRecipient"])
        self.assertFalse(self.get("/api/prefs", BOB).json()["maintenanceRecipient"])


class TestFiles(MaintBase):
    def setUp(self):
        super().setUp()
        self.enable()
        self.folder = os.path.join(self.share, "household", "maintenance")
        os.makedirs(os.path.dirname(self.folder))

    def set_folder(self, path=None, **kw):
        return self.put("/api/admin/settings", {"maintenance_files_path": path or self.folder, **kw}, ADMIN)

    def upload(self, name="manual.pdf", data=b"%PDF-1.4 hello", **owner):
        q = "&".join(f"{k}={v}" for k, v in owner.items())
        return self.c.post(f"/api/maintenance/files?{q}", files={"file": (name, io.BytesIO(data))}, headers=ALICE)

    def test_folder_rules(self):
        for bad in ("relative/x", "/etc/x", self.share, self.share + "/../x", self.share + "/a/./b"):
            self.assertEqual(self.post("/api/admin/settings/check-maintenance-folder", {"path": bad}, ADMIN).status_code, 422, bad)
            self.assertEqual(self.set_folder(bad).status_code, 422, bad)
        r = self.post("/api/admin/settings/check-maintenance-folder", {"path": self.share + "/nope/x"}, ADMIN).json()
        self.assertEqual((r["verdict"], r["refused"]), ("missing_parent", True))
        r = self.post("/api/admin/settings/check-maintenance-folder", {"path": self.folder}, ADMIN).json()
        self.assertEqual(r["verdict"], "new")
        self.assertFalse(os.path.exists(self.folder))            # checking has no side effects

    def test_upload_download_delete(self):
        it = self.item()
        self.assertEqual(self.upload(item_id=it["id"]).status_code, 503)     # no folder yet
        r = self.set_folder()
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["maintenanceFiles"]["online"])
        for sub in ("_deleted", "_thumbs", "README.txt", maint_files.MARKER):
            self.assertTrue(os.path.exists(os.path.join(self.folder, sub)), sub)
        f = self.upload(item_id=it["id"]).json()
        self.assertEqual((f["name"], f["size"]), ("manual.pdf", 14))
        self.assertTrue(os.path.isfile(os.path.join(self.folder, "Replace furnace filter", "manual.pdf")))
        again = self.upload(item_id=it["id"]).json()
        self.assertEqual(again["name"], "manual (2).pdf")
        d = self.get(f"/api/maintenance/files/{f['id']}")
        self.assertEqual((d.status_code, d.content), (200, b"%PDF-1.4 hello"))
        self.assertNotIn("content-security-policy", d.headers)      # a real PDF: Chrome's viewer can show it
        self.assertEqual(d.headers["x-content-type-options"], "nosniff")
        self.assertEqual([x["name"] for x in self.overview()["items"][0]["files"]], ["manual.pdf", "manual (2).pdf"])
        # a Mark done record's files go in a dated folder
        rec = self.post(f"/api/maintenance/items/{it['id']}/done").json()["recordId"]
        self.upload(name="receipt.jpg", data=b"notreallyajpeg", done_id=rec)
        self.assertTrue(os.path.isfile(os.path.join(self.folder, "Replace furnace filter", "2026-09-21", "receipt.jpg")))
        self.assertEqual([x["name"] for x in self.get("/api/maintenance/history").json()["records"][0]["files"]], ["receipt.jpg"])
        # delete moves to _deleted
        self.assertEqual(self.delete(f"/api/maintenance/files/{f['id']}").status_code, 204)
        self.assertTrue(os.path.isfile(os.path.join(self.folder, "_deleted", "2026-09-21", "Replace furnace filter", "manual.pdf")))
        # undo takes the record's files too
        self.post(f"/api/maintenance/items/{it['id']}/undo")
        self.assertFalse(os.path.exists(os.path.join(self.folder, "Replace furnace filter", "2026-09-21", "receipt.jpg")))
        # renaming the item renames its folder; deleting the item moves the folder
        self.patch(f"/api/maintenance/items/{it['id']}", {"name": "Furnace filter"})
        self.assertTrue(os.path.isfile(os.path.join(self.folder, "Furnace filter", "manual (2).pdf")))
        self.assertEqual(self.get(f"/api/maintenance/files/{again['id']}").content, b"%PDF-1.4 hello")
        self.delete(f"/api/maintenance/items/{it['id']}")
        self.assertFalse(os.path.exists(os.path.join(self.folder, "Furnace filter")))
        self.assertTrue(os.path.isdir(os.path.join(self.folder, "_deleted", "2026-09-21", "Furnace filter")))

    def test_rules(self):
        it = self.item()
        self.set_folder()
        self.assertEqual(self.upload().status_code, 422)                              # no owner
        self.assertEqual(self.upload(name="x.txt", data=b"", item_id=it["id"]).status_code, 422)
        big = b"x" * (maint_files.MAX_BYTES + 1)
        self.assertEqual(self.upload(name="big.bin", data=big, item_id=it["id"]).status_code, 413)
        self.assertFalse([n for n in os.listdir(os.path.join(self.folder, "Replace furnace filter")) if n.endswith(".part")])
        f = self.upload(name="../../etc/passwd", data=b"x", item_id=it["id"]).json()
        self.assertNotIn("/", f["name"])
        self.assertFalse(f["name"].startswith("."))
        # job files: only for the Maintenance list
        other = self.add("Not a job")
        self.assertEqual(self.upload(task_id=other["id"]).status_code, 422)
        job = self.add("Tap", list_id=self.overview()["listId"])
        self.assertEqual(self.upload(task_id=job["id"]).status_code, 201)
        self.assertEqual(len(self.get(f"/api/maintenance/files?task_id={job['id']}").json()), 1)
        self.assertTrue(os.path.isdir(os.path.join(self.folder, "Jobs", f"Tap ({job['id'][:8]})")))
        self.assertEqual(self.overview()["jobs"][0]["fileCount"], 1)

    def test_job_files_follow_the_task_rules_and_owners_have_caps(self):
        from app import task_files
        from app.routers import maintenance as mroute
        it = self.item()
        self.set_folder()
        job = self.add("Tap", list_id=self.overview()["listId"])
        for i in range(task_files.MAX_FILES):
            self.assertEqual(self.upload(name=f"p{i}.txt", data=b"x", task_id=job["id"]).status_code, 201)
        r = self.upload(name="one-more.txt", data=b"x", task_id=job["id"])
        self.assertEqual(r.status_code, 422, r.text)
        self.assertIn(str(task_files.MAX_FILES), r.json()["detail"])
        done = self.add("Done job", list_id=self.overview()["listId"])
        self.patch(f"/api/tasks/{done['id']}", {"completed": True})
        self.assertEqual(self.upload(task_id=done["id"]).status_code, 409)       # a ticked-off job takes no files
        old = (mroute.MAX_ITEM_FILES, mroute.MAX_RECORD_FILES)
        mroute.MAX_ITEM_FILES = mroute.MAX_RECORD_FILES = 2
        try:
            self.assertEqual(self.upload(item_id=it["id"]).status_code, 201)
            self.assertEqual(self.upload(item_id=it["id"]).status_code, 201)
            self.assertEqual(self.upload(item_id=it["id"]).status_code, 422)
            rec = self.post(f"/api/maintenance/items/{it['id']}/done").json()["recordId"]
            self.assertEqual(self.upload(done_id=rec).status_code, 201)
            self.assertEqual(self.upload(done_id=rec).status_code, 201)
            self.assertEqual(self.upload(done_id=rec).status_code, 422)
        finally:
            mroute.MAX_ITEM_FILES, mroute.MAX_RECORD_FILES = old

    def test_free_space_floor(self):
        from collections import namedtuple
        from unittest import mock
        it = self.item()
        self.set_folder()
        usage = namedtuple("usage", "total used free")
        low = usage(10 << 30, 10 << 30, 500 << 20)                          # 500 MB free, 1 GB kept free
        with mock.patch.object(maint_files.shutil, "disk_usage", return_value=low):
            r = self.upload(item_id=it["id"])
            self.assertEqual(r.status_code, 507, r.text)
            job = self.add("A ticket")
            r = self.c.post(f"/api/tasks/{job['id']}/files", files={"file": ("t.pdf", io.BytesIO(b"%PDF-1"))}, headers=ALICE)
            self.assertEqual(r.status_code, 507, r.text)
            self.assertEqual(self.put("/api/admin/settings", {"files_min_free_mb": 0}, ADMIN).status_code, 200)
            self.assertEqual(self.upload(item_id=it["id"]).status_code, 201)      # 0 = don't check
        self.assertFalse([n for n in os.listdir(os.path.join(self.folder, "Replace furnace filter")) if n.endswith(".part")])

    def test_a_cost_must_be_a_real_number_and_ids_must_be_text(self):
        it = self.item()
        for bad in ("NaN", "Infinity", "-Infinity"):
            r = self.c.post(f"/api/maintenance/items/{it['id']}/done", content='{"cost": %s}' % bad,
                            headers={**ALICE, "Content-Type": "application/json"})
            self.assertEqual(r.status_code, 422, (bad, r.text))
        for key in ("place_id", "assigned_to"):
            for bad in ([1], {"a": 1}, 5, True):
                r = self.patch(f"/api/maintenance/items/{it['id']}", {key: bad})
                self.assertEqual(r.status_code, 422, (key, bad, r.text))

    def test_a_huge_photo_is_kept_without_decoding_it(self):
        import struct
        import zlib
        from unittest import mock

        def png(w, h):
            def chunk(t, b):
                return struct.pack(">I", len(b)) + t + b + struct.pack(">I", zlib.crc32(t + b) & 0xFFFFFFFF)
            return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                    + chunk(b"IDAT", zlib.compress(b"\0" * 10)) + chunk(b"IEND", b""))
        from app.common import image_guard
        it = self.item()
        self.set_folder()
        with mock.patch.object(image_guard, "decode_slot", side_effect=AssertionError("decoded")):
            f = self.upload(name="bomb.png", data=png(30_000, 30_000), item_id=it["id"])      # 900 megapixels
            self.assertEqual(f.status_code, 201, f.text)
            f = f.json()
            self.assertEqual(f["mime"], "image/png")
            self.assertFalse(f["thumb"])
            f2 = self.upload(name="big.png", data=png(7000, 7000), item_id=it["id"]).json()     # 49 MP: over 40
            self.assertFalse(f2["thumb"])
        self.assertEqual(os.listdir(os.path.join(self.folder, "_thumbs")), [])
        # a normal photo still gets its preview
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (1200, 900), "red").save(buf, "JPEG")
        ok = self.upload(name="photo.jpg", data=buf.getvalue(), item_id=it["id"]).json()
        self.assertTrue(ok["thumb"])
        self.assertEqual(self.get(f"/api/maintenance/files/{ok['id']}?thumb=1").headers["content-type"], "image/jpeg")

    def test_not_connected(self):
        it = self.item()
        self.set_folder()
        f = self.upload(item_id=it["id"]).json()
        os.remove(os.path.join(self.folder, maint_files.MARKER))        # e.g. the NAS isn't mounted
        st = maint_files.check()
        self.assertFalse(st["online"])
        self.assertIn("missing", st["reason"])
        self.assertEqual(self.upload(item_id=it["id"]).status_code, 503)
        self.assertEqual(self.get(f"/api/maintenance/files/{f['id']}").status_code, 503)
        self.assertEqual(self.delete(f"/api/maintenance/items/{it['id']}").status_code, 503)    # it has files
        self.assertFalse(self.overview()["files"]["online"])
        # "Use this folder" sets it up again
        self.assertTrue(self.post("/api/admin/maintenance/files/check", {"useThisFolder": True}, ADMIN).json()["online"])
        self.assertEqual(self.get(f"/api/maintenance/files/{f['id']}").status_code, 200)

    def test_change_needs_confirm_and_other_install_is_refused(self):
        it = self.item()
        self.set_folder()
        self.upload(item_id=it["id"])
        new = os.path.join(self.share, "household", "other")
        self.assertEqual(self.set_folder(new).status_code, 409)                     # files would stay behind
        self.assertEqual(self.set_folder(new, confirm=True).status_code, 200)
        self.assertEqual(settings.get("maintenance_files_path"), new)
        os.makedirs(os.path.join(self.share, "x"))
        with open(os.path.join(self.share, "x", maint_files.MARKER), "w") as fh:
            fh.write("someone-else")
        self.assertEqual(self.set_folder(os.path.join(self.share, "x"), confirm=True).status_code, 409)
        # the old folder is ours: switching back is safe
        r = self.post("/api/admin/settings/check-maintenance-folder", {"path": self.folder}, ADMIN).json()
        self.assertEqual(r["verdict"], "this")

    def test_restore_keeps_the_folder(self):
        self.set_folder()
        backup = self.get("/api/admin-storage-download-db", ADMIN).content
        new = os.path.join(self.share, "household", "moved")
        self.set_folder(new, confirm=True)
        store = maint_files.store_id()
        r = self.c.post("/api/admin-storage-import-db", files={"file": ("b.db", io.BytesIO(backup))}, headers=ADMIN)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((settings.get("maintenance_files_path"), maint_files.store_id()), (new, store))

    def test_purge_deleted(self):
        self.set_folder()
        old = os.path.join(self.folder, "_deleted", "2026-08-01")
        recent = os.path.join(self.folder, "_deleted", "2026-09-01")
        os.makedirs(old)
        os.makedirs(recent)
        from datetime import date
        self.assertEqual(maint_files.purge_deleted(date(2026, 9, 21)), 1)
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(recent))


class TestSensors(MaintBase):
    def test_overdue_sensor_and_item_sensor(self):
        from app import ha_sensors
        self.enable(sensor=True)
        it = self.item(name="Filter", last_done="2026-06-01", expose_sensor=True)
        ha_sensors.push_maintenance_blocking()
        st = self.ha.states
        self.assertEqual(st[mt.OVERDUE_SENSOR]["state"], "1")
        self.assertEqual(st[mt.OVERDUE_SENSOR]["attributes"]["overdue"], ["Filter"])
        self.assertEqual(st["binary_sensor.household_todo_maintenance_filter"]["state"], "on")
        self.post(f"/api/maintenance/items/{it['id']}/done")
        ha_sensors.push_maintenance_blocking()
        self.assertEqual(self.ha.states[mt.OVERDUE_SENSOR]["state"], "0")
        self.put("/api/admin/maintenance", {"sensor": False}, ADMIN)
        ha_sensors.push_maintenance_blocking()
        self.assertNotIn(mt.OVERDUE_SENSOR, self.ha.states)


if __name__ == "__main__":
    unittest.main()
