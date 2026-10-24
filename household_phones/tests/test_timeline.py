"""The online line (spec/SPEC.md §5.1): status changes logged for monitored phones, the strip and Online %, who may
see it, the gap while the app wasn't running, and the clean-up. T0 is Friday 2027-01-15 08:00 (UTC here)."""
from base import ADMIN, ASHA, BOB, PhonesCase

from app import db, housekeeping, timeline


def log(pid):
    with db.get_conn() as conn:
        return [(r["at"], r["status"]) for r in conn.execute(
            "SELECT at, status FROM status_log WHERE phone_id = ? ORDER BY at, id", (pid,))]


class Recording(PhonesCase):
    def test_each_change_once(self):
        pid = self.add()
        self.report(pid)
        self.tick()
        self.tick()
        self.assertEqual(log(pid), [(self.now, "online")])
        self.advance(18)
        self.tick()
        self.advance(10)
        self.tick()
        self.tick()
        self.assertEqual([s for _, s in log(pid)], ["online", "late", "offline"])
        self.advance(1)
        self.report(pid)
        self.tick()
        self.assertEqual([s for _, s in log(pid)], ["online", "late", "offline", "online"])

    def test_devices_no_admin_added_are_never_logged(self):
        pid = self.add("Bob Phone", "u-bob", monitored=False)
        self.tick()
        self.assertEqual(log(pid), [])

    def test_stop_monitoring_keeps_or_deletes(self):
        pid = self.add()
        self.report(pid)
        self.tick()
        self.post(f"/api/admin/phones/{pid}/unmonitor", {"keepHistory": True})
        self.advance(1)
        self.tick()
        self.assertEqual([s for _, s in log(pid)], ["online", "unknown"])
        self.tick()
        self.assertEqual(len(log(pid)), 2)
        self.post(f"/api/admin/phones/{pid}/unmonitor", {"keepHistory": False})
        self.tick()
        self.assertEqual(log(pid), [])

    def test_watching_paused_shows_only_paused(self):
        pid = self.add()
        self.report(pid)
        self.tick()
        self.assertEqual(self.post(f"/api/phones/{pid}/pause", {"kind": "watching", "hours": 2}, ASHA).status_code, 200)
        self.advance(1)
        self.tick()
        self.advance(30)                          # silent while paused: still only "paused"
        self.tick()
        self.assertEqual([s for _, s in log(pid)], ["online", "paused"])

    def test_a_stopped_app_leaves_a_gap(self):
        pid = self.add()
        self.report(pid)
        self.tick()
        stopped = self.now
        timeline.reset()                          # the app restarts an hour later
        self.advance(60)
        self.report(pid)
        self.tick()
        self.assertEqual(log(pid), [(stopped, "online"), (stopped, "none"), (self.now, "online")])
        self.advance(10)
        with db.get_conn() as conn:
            self.assertEqual(timeline.uptime(conn, pid, self.now, 86400), 100.0)    # the gap isn't counted
            strip = timeline.buckets(conn, pid, self.now)
        self.assertEqual([(x["c"], x["t"]) for x in strip[-3:]], [
            ("none", "Fri 08:00–08:30 · No data"), ("none", "Fri 08:30–09:00 · No data"), ("up", "Fri 09:00–now · Online")])

    def test_a_quick_restart_leaves_no_gap(self):
        pid = self.add()
        self.report(pid)
        self.tick()
        timeline.reset()
        self.advance(2)
        self.report(pid)
        self.tick()
        self.assertEqual([s for _, s in log(pid)], ["online"])

    def test_older_databases_get_the_table(self):
        with db.get_conn() as conn:
            conn.execute("DROP TABLE status_log")
        db.init_db()
        pid = self.add()
        self.report(pid)
        self.tick()
        self.assertEqual([s for _, s in log(pid)], ["online"])

    def test_replaces_moves_the_line(self):
        old = self.add()
        self.report(old)
        self.tick()
        self.advance(5)
        new = self.add("Asha Galaxy", monitored=False)
        self.assertEqual(self.post(f"/api/phones/{new}/replaces", {"old": old}, ASHA).status_code, 200)
        self.tick()
        self.assertEqual(log(old), [])
        self.assertEqual([s for _, s in log(new)][0], "online")

    def test_pruned_after_the_setting_keeping_the_status_at_the_cut(self):
        pid = self.add()
        self.report(pid)
        self.tick()                               # online at T0
        self.advance(20)
        self.tick()                               # late
        self.setting(online_history_days=1)
        self.advance(3 * 24 * 60)
        housekeeping.prune(self.now)
        self.assertEqual([s for _, s in log(pid)], ["late"])
        self.assertEqual(self.c.get("/api/admin/settings", headers=ADMIN).status_code, 200)


class Line(PhonesCase):
    def setUp(self):
        super().setUp()
        self.pid = self.add()
        self.report(self.pid)
        self.tick()
        self.advance(60)                          # online (08:00-09:20), then silent: late, offline at 09:30
        self.report(self.pid)
        self.tick()
        self.advance(20)
        self.tick()
        self.advance(10)
        self.tick()
        self.advance(10)                          # now 09:40
        self.tick()

    def test_strip_and_online_share(self):
        r = self.get(f"/api/phones/{self.pid}/online-history", ASHA)
        self.assertEqual(r.status_code, 200)
        b = r.json()
        self.assertFalse(b["hidden"])
        self.assertEqual(b["keepDays"], 30)
        self.assertEqual(len(b["buckets"]), 48)
        classes = [x["c"] for x in b["buckets"]]
        self.assertEqual(classes[0], "none")      # before it was monitored
        self.assertIn("up", classes)
        self.assertIn("down", classes)            # offline in the half hour now
        self.assertEqual(classes[-3:], ["up", "late", "down"])
        self.assertEqual(b["buckets"][-2]["t"], "Fri 09:00–09:30 · Late 10 min, Online 20 min")
        self.assertEqual(b["buckets"][-1]["t"], "Fri 09:30–now · Offline")
        self.assertEqual(b["uptime"]["day"], 90.0)            # Late counts as online: 90 of 100 minutes
        self.assertEqual(len(self.get(f"/api/phones/{self.pid}/online-history?range=7d").json()["buckets"]), 84)
        self.assertEqual(len(self.get(f"/api/phones/{self.pid}/online-history?range=30d").json()["buckets"]), 60)
        self.assertEqual(self.get(f"/api/phones/{self.pid}/online-history?range=1y").status_code, 422)

    def test_tooltips_in_home_assistant_time(self):
        b = self.get(f"/api/phones/{self.pid}/online-history").json()
        self.assertEqual(b["buckets"][0]["t"], "Thu 10:00–10:30 · No data")
        self.assertEqual(b["buckets"][-4]["t"], "Fri 08:00–08:30 · Online")
        self.assertEqual(b["startText"], "Thu 10:00")
        week = self.get(f"/api/phones/{self.pid}/online-history?range=7d").json()["buckets"]
        self.assertEqual(week[-1]["t"], "Fri 08:00–now · Offline 10 min, Late 10 min, Online 1 h 20 min")
        self.assertEqual(week[-2]["t"], "Fri 06:00–08:00 · No data")
        month = self.get(f"/api/phones/{self.pid}/online-history?range=30d").json()["buckets"]
        self.assertEqual(month[-2]["t"], "Thu 14 Jan 12:00–00:00 · No data")
        # in a 12-hour part, 10 minutes offline is a blip: green, and said in the tooltip
        self.assertEqual((month[-1]["c"], month[-1]["t"]),
                         ("up", "Fri 15 Jan 00:00–now · Offline 10 min, Late 10 min, Online 1 h 20 min, No data 8 h"))

    def test_parts_follow_home_assistant_s_time_zone(self):
        from unittest import mock
        from zoneinfo import ZoneInfo
        from app import config
        with mock.patch.object(config.ZONE, "tz", ZoneInfo("Asia/Kolkata")):     # 09:40 UTC is 15:10 there
            day = self.get(f"/api/phones/{self.pid}/online-history").json()["buckets"]
            month = self.get(f"/api/phones/{self.pid}/online-history?range=30d").json()["buckets"]
        self.assertEqual(day[-1]["t"], "Fri 15:00–now · Offline")
        self.assertTrue(month[-1]["t"].startswith("Fri 15 Jan 12:00–now"), month[-1]["t"])

    def test_who_may_see_it(self):
        self.assertEqual(self.get(f"/api/phones/{self.pid}/online-history", ADMIN).status_code, 200)
        self.assertEqual(self.get(f"/api/phones/{self.pid}/online-history", BOB).status_code, 404)
        # watching paused: the owner still sees the line, admins don't
        self.post(f"/api/phones/{self.pid}/pause", {"kind": "watching", "hours": 2}, ASHA)
        self.assertTrue(self.get(f"/api/phones/{self.pid}/online-history", ADMIN).json()["hidden"])
        self.assertEqual(self.get(f"/api/phones/{self.pid}/online-history", ADMIN).json()["buckets"], [])
        self.assertFalse(self.get(f"/api/phones/{self.pid}/online-history", ASHA).json()["hidden"])
        lines = self.get("/api/admin/phones?history=1").json()["lines"]
        self.assertNotIn(self.pid, lines)

    def test_all_phones_strips_only_when_asked(self):
        self.assertIsNone(self.get("/api/admin/phones").json()["lines"])
        lines = self.get("/api/admin/phones?history=true").json()["lines"]
        self.assertEqual(len(lines[self.pid]), 48)
        self.assertEqual(self.get("/api/admin/phones?history=1", ASHA).status_code, 403)

    def test_do_not_monitor_hours_hide_it_from_admins(self):
        w = [{"days": [4], "start": "09:00", "end": "11:00", "pauseForwarding": False}]
        self.assertEqual(self.put(f"/api/phones/{self.pid}/dnm", {"windows": w}, ASHA).status_code, 200)
        self.tick()
        self.assertEqual(log(self.pid)[-1][1], "dnm")
        self.assertTrue(self.get(f"/api/phones/{self.pid}/online-history", ADMIN).json()["hidden"])
        self.advance(5)
        b = self.get(f"/api/phones/{self.pid}/online-history", ASHA).json()
        self.assertEqual((b["buckets"][-1]["c"], b["buckets"][-1]["t"]),
                         ("down", "Fri 09:30–now · Offline 10 min, Do-not-monitor hours 5 min"))
