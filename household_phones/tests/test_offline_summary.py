"""Offline alerts for you (spec/SPEC.md §5 "Alerts", app/digest.py): each admin's own choice — right away (the
default, as before), a summary once or twice a day, or off. T0 is Friday 2027-01-15 08:00 (UTC here)."""
import os
import re
import shutil
import sqlite3
import tempfile
from unittest import mock

from base import ADMIN, ASHA, BOB, T0, PhonesCase

from app import config, db, digest, monitor


class Summary(PhonesCase):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(config, "ADMIN_USERS", {"ann", "bob"})        # two admins
        p.start()
        self.addCleanup(p.stop)
        self.people([{"name": "Ann", "userId": "u-ann", "phones": [{"label": "Ann", "tracker": "t1", "service": "mobile_app_ann"}]},
                     {"name": "Bob", "userId": "u-bob", "phones": [{"label": "Bob", "tracker": "t2", "service": "mobile_app_bob"}]},
                     {"name": "Asha", "userId": "u-asha", "phones": [
                         {"label": "Pixel", "tracker": "device_tracker.asha_pixel", "service": "mobile_app_asha_pixel"},
                         {"label": "Tab", "tracker": "t3", "service": "mobile_app_asha_tab"}]}])
        self.pid = self.add()                                    # Asha's Pixel 8: goes offline
        self.kid = self.add("Kid Phone", "u-kid")                # keeps reporting
        self.report(self.pid, value="7")
        self.report(self.kid, name="Kid Phone")
        self.tick()

    def choose(self, who=ADMIN, **body):
        r = self.put("/api/me/offline-alerts", body, who)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def walk(self, minutes, keep=True):
        """Tick every minute; the kid's phone keeps reporting."""
        for _ in range(int(minutes)):
            self.advance(1)
            if keep:
                self.report(self.kid, name="Kid Phone")
            self.tick()

    def to(self, svc, word):
        return [s for s in self.sent if s[0] == svc and word in s[1]]

    # ------------------------------------------------------------------ choices
    def test_default_is_right_away_as_before(self):
        self.assertEqual(self.get("/api/my").json()["offlineAlerts"]["mode"], "now")
        self.assertIsNone(self.get("/api/my", ASHA).json()["offlineAlerts"])         # not an admin
        self.walk(30)
        self.assertEqual(self.row(self.pid)["status"], "offline")
        self.assertEqual(len(self.to("mobile_app_ann", "Offline")), 1)
        self.assertEqual(len(self.to("mobile_app_bob", "Offline")), 1)
        self.walk(120)
        self.assertFalse([s for s in self.sent if "summary" in s[1]])

    def test_validation(self):
        bad = [{"mode": "weekly"}, {"mode": "summary"}, {"mode": "summary", "times": []},
               {"mode": "summary", "times": ["8am"]}, {"mode": "summary", "times": ["24:00"]},
               {"mode": "summary", "times": ["08:00", "08:00"]}, {"mode": "summary", "times": ["08:00", "12:00", "20:00"]},
               {"mode": "summary", "times": "08:00"}]
        for body in bad:
            self.assertEqual(self.put("/api/me/offline-alerts", body).status_code, 422, body)
        self.assertEqual(self.put("/api/me/offline-alerts", {"mode": "off"}, ASHA).status_code, 403)
        o = self.choose(mode="summary", times=["20:00", "08:30"])
        self.assertEqual((o["mode"], o["times"]), ("summary", ["08:30", "20:00"]))
        self.assertEqual(self.get("/api/my").json()["offlineAlerts"]["times"], ["08:30", "20:00"])
        people = {p["id"]: p for p in self.get("/api/admin/people").json()["people"]}
        self.assertEqual(people["u-ann"]["offlineAlerts"], "a summary at 08:30 and 20:00")
        self.assertEqual(people["u-bob"]["offlineAlerts"], "right away")
        self.assertIsNone(people["u-asha"]["offlineAlerts"])

    # ------------------------------------------------------------------ the summary
    def test_summary_instead_of_alerts_listing_only_offline_phones(self):
        self.setting(alert_back=True)
        self.choose(mode="summary", times=["09:00"])
        self.walk(30)
        self.assertEqual(self.row(self.pid)["status"], "offline")
        self.assertFalse(self.to("mobile_app_ann", "Offline"))                # no individual alert
        self.assertEqual(len(self.to("mobile_app_bob", "Offline")), 1)         # Bob: right away (per admin)
        with db.get_conn() as conn:
            h = [r["text"] for r in conn.execute("SELECT text FROM history WHERE phone_id = ? AND kind = 'alert'", (self.pid,))]
        self.assertTrue(any("Ann Admin (in their summary)" in t for t in h), h)
        self.walk(30)                                                          # 09:00
        got = self.to("mobile_app_ann", "Offline summary")
        self.assertEqual(len(got), 1)
        self.assertFalse(self.to("mobile_app_bob", "Offline summary"))
        msg = got[0][2]
        self.assertIn("1 phone can't be reached (09:00)", msg)
        self.assertIn("Asha's Pixel 8 — Offline for", msg)
        self.assertIn("since 08:", msg)
        self.assertIn("battery 7%", msg)
        self.assertNotIn("Kid", msg)
        self.walk(60)                                                          # once per time
        self.assertEqual(len(self.to("mobile_app_ann", "Offline summary")), 1)
        self.report(self.pid)                                                  # back online: no message for Ann
        self.tick()
        self.assertFalse(self.to("mobile_app_ann", "Back online"))
        self.assertEqual(len(self.to("mobile_app_bob", "Back online")), 1)

    def test_twice_a_day_and_nothing_when_all_online(self):
        self.choose(mode="summary", times=["08:10", "08:50"])
        self.walk(12)                                                          # 08:10: all online (late at most)
        self.assertFalse([s for s in self.sent if "summary" in s[1]])
        with db.get_conn() as conn:
            at = conn.execute("SELECT offline_digest_at FROM users WHERE id = 'u-ann'").fetchone()[0]
        self.assertEqual(at, T0 + 10 * 60)                                     # handled all the same
        self.walk(40)                                                          # 08:50: offline now
        self.assertEqual(len(self.to("mobile_app_ann", "Offline summary")), 1)

    def test_paused_and_do_not_monitor_phones_are_not_listed(self):
        self.choose(mode="summary", times=["09:00"])
        self.walk(30)
        self.set_row(self.pid, pause_kind="alerts", pause_until=T0 + 86400)
        self.walk(30)
        self.assertFalse(self.to("mobile_app_ann", "Offline summary"))

    def test_off(self):
        self.setting(alert_back=True)
        self.choose(mode="off")
        self.walk(30)
        self.report(self.pid)
        self.tick()
        self.assertFalse([s for s in self.sent if s[0] == "mobile_app_ann"])
        self.assertTrue(self.to("mobile_app_bob", "Offline"))

    def test_owner_alerts_unaffected(self):
        self.setting(alert_offline_to="both")
        self.choose(mode="summary", times=["09:00"])
        self.choose(BOB, mode="off")
        ann = self.add("Ann Phone", "u-ann")                                   # an admin's own phone
        self.report(ann, name="Ann Phone")
        self.tick()
        self.walk(30, keep=False)
        self.assertEqual(len(self.to("mobile_app_asha_tab", "Offline")), 1)   # the owner, as before
        ann_got = [s[2] for s in self.to("mobile_app_ann", "Offline")]
        self.assertFalse([t for t in ann_got if "Asha" in t])                  # Asha's phone: in Ann's summary
        self.assertEqual(len([t for t in ann_got if "Ann's Pixel 8" in t]), 1)  # her own phone: right away, as owner
        self.assertFalse(self.to("mobile_app_bob", "Offline"))                # Bob: off

    def test_owner_only_means_no_summary(self):
        self.setting(alert_offline_to="owner", alert_app_to="owner")
        self.choose(mode="summary", times=["09:00"])
        self.assertFalse(self.get("/api/my").json()["offlineAlerts"]["applies"])
        self.walk(60)
        self.assertFalse([s for s in self.sent if "summary" in s[1]])

    def test_no_double_send_and_catch_up_across_a_restart(self):
        self.choose(mode="summary", times=["09:00"])
        self.walk(60)                                                          # 09:00 sent
        self.assertEqual(len(self.to("mobile_app_ann", "Offline summary")), 1)
        monitor.reset()                                                        # the app restarts at 09:05
        monitor.STARTED["at"] = self.now
        self.walk(60)
        self.assertEqual(len(self.to("mobile_app_ann", "Offline summary")), 1)  # not again
        # stopped over the 11:00 summary, started at 12:30: sent once the start-up grace is over (within 2 hours)
        self.choose(mode="summary", times=["11:00"])
        self.now = T0 + 4.5 * 3600
        monitor.reset()
        monitor.STARTED["at"] = self.now
        self.tick()
        self.walk(25)
        self.assertEqual(len(self.to("mobile_app_ann", "Offline summary")), 1)  # not during the grace (27 min)
        self.walk(3)                                                              # 12:58
        self.assertEqual(len(self.to("mobile_app_ann", "Offline summary")), 2)
        self.walk(10)
        self.assertEqual(len(self.to("mobile_app_ann", "Offline summary")), 2)

    def test_too_late_to_catch_up(self):
        self.assertIsNone(digest.due_slot(["09:00"], None, T0 + 3 * 3600 + 60))  # 11:01: more than 2 hours late
        self.assertEqual(digest.due_slot(["09:00"], None, T0 + 2 * 3600), T0 + 3600)
        self.assertIsNone(digest.due_slot(["09:00"], T0 + 3600, T0 + 2 * 3600))   # already handled
        self.assertEqual(digest.due_slot(["23:30"], None, T0 - 6.5 * 3600), T0 - 8.5 * 3600)  # 01:30: yesterday's

    def test_summary_at_its_time_even_in_quiet_hours(self):
        self.put("/api/me/quiet", {"quietStart": "08:30", "quietEnd": "10:00"}, ADMIN)
        self.choose(mode="summary", times=["09:00"])
        self.walk(60)
        self.assertEqual(len(self.to("mobile_app_ann", "Offline summary")), 1)

    def test_send_a_test_summary(self):
        r = self.post("/api/me/offline-alerts/test")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("All phones are online", self.sent[-1][2])
        self.assertIn("(test)", self.sent[-1][1])
        self.walk(30)
        self.post("/api/me/offline-alerts/test")
        self.assertIn("Asha's Pixel 8", self.sent[-1][2])
        self.assertEqual(self.post("/api/me/offline-alerts/test", who=ASHA).status_code, 403)


class Migration(PhonesCase):
    def test_columns_added_to_an_older_database(self):
        new = ("offline_mode", "offline_times", "offline_digest_at")
        schema = "\n".join(line for line in db.SCHEMA.splitlines() if not re.match(r"\s+(%s) " % "|".join(new), line))
        folder = tempfile.mkdtemp(prefix="phones_mig18_")
        self.addCleanup(shutil.rmtree, folder, True)
        path = os.path.join(folder, "phones.db")
        conn = sqlite3.connect(path)
        conn.executescript(schema)
        self.assertFalse(set(new) & {r[1] for r in conn.execute("PRAGMA table_info(users)")})
        conn.execute("INSERT INTO users (id, name) VALUES ('u1', 'Ann')")
        conn.commit()
        conn.close()
        with mock.patch.object(config, "DB_PATH", path):
            db.init_db()
            db.init_db()
            with db.get_conn() as c:
                r = dict(c.execute("SELECT * FROM users WHERE id = 'u1'").fetchone())
        db.init_db()
        self.assertEqual({k: r[k] for k in new}, {"offline_mode": "now", "offline_times": "", "offline_digest_at": None})


if __name__ == "__main__":
    import unittest
    unittest.main()
