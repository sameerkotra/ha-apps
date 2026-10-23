"""The offline alert's delay (spec/SPEC.md §5 "Alerts", §7): a household default and a per-phone choice; the phone is
Offline as before and only its Offline / App not reporting alert waits; do-not-monitor hours and pauses don't count;
the count survives a restart; lost contact waits for each phone's delay too (test_lost_contact.py). T0 is Friday 2027-01-15 08:00 (UTC here)."""
import os
import re
import shutil
import sqlite3
import tempfile
from unittest import mock

from base import ADMIN, ASHA, T0, PhonesCase

from app import config, db, ha_source, monitor


def offline(case):
    return [s for s in case.sent if "Offline" in s[1]]


def back(case):
    return [s for s in case.sent if "Back online" in s[1]]


class Delay(PhonesCase):
    def setUp(self):
        super().setUp()
        self.people([{"name": "Ann", "userId": "u-ann", "phones": [{"label": "Ann", "tracker": "x",
                                                                    "service": "mobile_app_ann"}]}])
        self.pid = self.add()
        self.report(self.pid, value="4")
        self.tick()
        self.setting(alert_back=True)

    def walk(self, until_min, step=1.0):
        """Tick every `step` minutes until T0 + `until_min`."""
        end = T0 + until_min * 60
        while self.now + step * 60 <= end + 1e-6:
            self.advance(step)
            self.tick()

    def own(self, minutes):
        r = self.put(f"/api/admin/phones/{self.pid}", {"offlineAlertAfterMin": minutes})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    # ------------------------------------------------------------------ choices
    def test_default_is_at_once_as_before(self):
        self.walk(27)
        self.assertEqual(self.row(self.pid)["status"], "offline")
        self.assertEqual(len(offline(self)), 1)
        self.assertIn("is offline: no check-in since 08:00", offline(self)[0][2])
        self.report(self.pid)
        self.tick()
        self.assertEqual(len(back(self)), 1)                   # back online after an Offline, as before

    def test_setting_choices_and_validation(self):
        m = self.get("/api/admin/settings").json()["meta"]["offline_alert_after_min"]
        self.assertEqual([c["value"] for c in m["choices"]], [0, 30, 60, 120, 240, 360, 600, 720, 1440])
        self.assertEqual((m["group"], m["showIf"], m["choices"][0]["label"]), ("alerts", "alert_offline",
                                                                               "As soon as it's Offline"))
        self.assertIn("App not reporting", m["help"])
        r = self.put("/api/admin/settings", {"offline_alert_after_min": 45})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(self.put("/api/admin/settings", {"offline_alert_after_min": 600}).status_code, 200)
        self.assertEqual(self.put(f"/api/admin/phones/{self.pid}", {"offlineAlertAfterMin": 45}).status_code, 422)
        self.assertEqual(self.put(f"/api/admin/phones/{self.pid}", {"offlineAlertAfterMin": 60}, ASHA).status_code, 403)
        page = self.own(60)["admin"]["offlineAlert"]
        self.assertEqual((page["own"], page["delayMin"], page["defaultLabel"]), (60, 60, "10 hours"))
        page = self.own(None)["admin"]["offlineAlert"]
        self.assertEqual((page["own"], page["delayMin"]), (None, 600))

    def test_phone_overrides_household_default(self):
        self.setting(offline_alert_after_min=600)
        self.own(0)                                              # this phone: at once
        self.walk(27)
        self.assertEqual(len(offline(self)), 1)
        self.report(self.pid)
        self.tick()
        self.sent.clear()
        self.setting(offline_alert_after_min=0)
        self.own(60)                                             # household at once, this phone after an hour
        self.walk(27 + 30)
        self.assertEqual(self.row(self.pid)["status"], "offline")
        self.assertEqual(offline(self), [])

    def test_add_to_monitoring_with_a_delay(self):
        pid = self.add("Bob Tab", monitored=False)
        avail = [a for a in self.get("/api/admin/phones").json()["available"] if a["id"] == pid][0]
        self.assertIsNone(avail["offlineAlert"]["own"])
        r = self.post(f"/api/admin/phones/{pid}/monitor", {"kind": "phone", "name": "", "intervalMin": None,
                                                          "ownerId": None, "offlineAlertAfterMin": 240})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.row(pid)["offline_alert_after_min"], 240)

    # ------------------------------------------------------------------ the countdown
    def test_alert_only_after_the_delay_with_how_long(self):
        self.own(60)
        self.walk(27)
        row = self.row(self.pid)
        self.assertEqual((row["status"], row["offline_since"]), ("offline", self.now))
        self.walk(27 + 59)
        self.assertEqual(offline(self), [])
        self.walk(27 + 60)
        self.assertEqual(len(offline(self)), 1)
        self.assertRegex(offline(self)[0][2], r"has been offline for 1 hour \(since 08:27, battery was 4%\)\.$")
        self.walk(27 + 180, step=5)
        self.assertEqual(len(offline(self)), 1)                  # once per spell
        self.report(self.pid)
        self.tick()
        self.assertEqual(len(back(self)), 1)                     # back online: its offline alert had gone out
        self.assertIsNone(self.row(self.pid)["offline_since"])

    def test_back_before_the_delay_sends_nothing(self):
        self.own(120)
        self.walk(27 + 100, step=5)
        self.assertEqual(self.row(self.pid)["status"], "offline")
        self.report(self.pid)
        self.tick()
        self.assertEqual(self.row(self.pid)["status"], "online")
        self.assertEqual((offline(self), back(self)), ([], []))
        self.assertIsNone(self.row(self.pid)["offline_since"])
        statuses = [h["text"] for h in self.get(f"/api/phones/{self.pid}").json()["admin"]["history"]
                    if h["kind"] == "status"]
        self.assertTrue(any(t.startswith("Offline") for t in statuses))   # the status and history are as before

    def test_do_not_monitor_hours_in_the_middle_dont_count(self):
        r = self.put(f"/api/phones/{self.pid}/dnm", {"windows": [{"days": [4], "start": "09:00", "end": "11:00",
                                                                  "pauseForwarding": False}]}, ASHA)
        self.assertEqual(r.status_code, 200, r.text)
        self.own(60)
        self.walk(60)                                            # 08:27 → 09:00 counted: 33 minutes
        self.walk(180, step=5)                                   # 09:00 → 11:00 not counted
        self.assertEqual(offline(self), [])
        self.assertAlmostEqual(self.row(self.pid)["offline_counted_s"], 33 * 60, delta=60)
        self.walk(180 + 26)                                      # 11:26: 59 minutes counted
        self.assertEqual(offline(self), [])
        self.walk(180 + 30)
        self.assertEqual(len(offline(self)), 1)
        self.assertIn("has been offline for 3 hours", offline(self)[0][2])

    def test_back_during_the_window_sends_nothing(self):
        self.put(f"/api/phones/{self.pid}/dnm", {"windows": [{"days": [4], "start": "09:00", "end": "11:00",
                                                              "pauseForwarding": False}]}, ASHA)
        self.own(60)
        self.walk(170, step=5)
        self.report(self.pid)                                    # during the window: not written
        self.walk(185)
        self.report(self.pid)                                    # after it: back
        self.tick()
        self.assertEqual(self.row(self.pid)["status"], "online")
        self.walk(210)
        self.assertEqual((offline(self), back(self)), ([], []))
        self.assertIsNone(self.row(self.pid)["offline_since"])

    def test_pauses_dont_count(self):
        self.own(60)
        self.walk(40)                                            # 13 minutes counted
        r = self.post(f"/api/phones/{self.pid}/pause", {"kind": "alerts", "hours": 1}, ADMIN)
        self.assertEqual(r.status_code, 200, r.text)
        self.walk(95, step=5)                                    # paused until 09:40
        page = self.get(f"/api/phones/{self.pid}").json()["admin"]["offlineAlert"]["counting"]
        self.assertIn("not counting now", page["text"])
        self.walk(100, step=5)
        self.walk(146, step=2)                                   # 09:40 + 46 = 59 minutes counted
        self.assertEqual(offline(self), [])
        self.walk(150, step=2)
        self.assertEqual(len(offline(self)), 1)

    def test_survives_a_restart(self):
        self.own(60)
        self.walk(57)                                            # 30 minutes counted
        before = self.row(self.pid)
        self.assertAlmostEqual(before["offline_counted_s"], 30 * 60, delta=1)
        # the app (or Home Assistant) restarts: nothing in memory, the start-up grace begins
        monitor.reset()
        self.advance(5)                                          # down for 5 minutes
        monitor.STARTED["at"] = self.now
        self.tick()
        self.tick()                                              # the same moment twice: counted once
        row = self.row(self.pid)
        self.assertEqual(row["status"], "late")                  # the start-up grace
        self.assertEqual(row["offline_since"], before["offline_since"])
        self.assertAlmostEqual(row["offline_counted_s"], 35 * 60, delta=1)
        self.walk(57 + 5 + 24)                                   # 59 minutes counted, still in the grace
        self.assertEqual(offline(self), [])
        self.walk(57 + 5 + 28)                                   # grace over (27 min): Offline, delay reached
        self.assertEqual(len(offline(self)), 1)
        self.assertIn("has been offline for 1 hour 2 minutes (since 08:27", offline(self)[0][2])
        self.walk(57 + 5 + 120, step=5)
        self.assertEqual(len(offline(self)), 1)

    def test_page_hint_while_counting(self):
        self.own(600)
        self.walk(27)
        self.walk(27 + 130, step=10)
        self.advance(4)                                          # between rounds: the page adds the time since
        page = self.get(f"/api/phones/{self.pid}").json()["admin"]["offlineAlert"]
        self.assertEqual(page["counting"]["text"], "Offline 2 h 14 min — alert after 10 h")
        self.assertIsNone(self.get(f"/api/phones/{self.pid}", ASHA).json().get("offlineAlert"))
        self.own(0)                                              # no delay: no hint
        self.assertIsNone(self.get(f"/api/phones/{self.pid}").json()["admin"]["offlineAlert"]["counting"])


class AppNotReportingAndLostContact(PhonesCase):
    def setUp(self):
        super().setUp()
        self.people([{"name": "Ann", "userId": "u-ann", "phones": [{"label": "Ann", "tracker": "x",
                                                                    "service": "mobile_app_ann"}]}])

    def test_app_not_reporting_waits_the_same_delay(self):
        self.setting(offline_alert_after_min=60)
        pid = self.add()
        self.report(pid)
        self.ha.result = ha_source.CommandResult(False, "signed_out", "gone")
        self.advance(18)
        self.tick()
        self.assertEqual(self.row(pid)["status"], "app_not_reporting")
        since = self.now
        while self.now < since + 55 * 60:
            self.advance(5)
            self.tick()
        self.advance(4)
        self.tick()                                              # 59 minutes
        self.assertFalse([s for s in self.sent if "App not reporting" in s[1]])
        self.advance(1)
        self.tick()
        sent = [s for s in self.sent if "App not reporting" in s[1]]
        self.assertEqual(len(sent), 1)
        self.assertIn("Unreachable for 1 hour", sent[0][2])
        for _ in range(12):
            self.advance(10)
            self.tick()
        self.assertEqual(len([s for s in self.sent if "App not reporting" in s[1]]), 1)

    def test_lost_contact_waits_for_the_household_delay(self):
        self.setting(offline_alert_after_min=600)
        ids = [self.add(f"Phone {i}", user_id="u-asha") for i in range(4)]
        for i, pid in enumerate(ids):
            self.report(pid, name=f"Phone {i}")
        self.tick()
        self.advance(18)
        self.report(ids[3], name="Phone 3")
        self.ha.result = ha_source.CommandResult(False, "cant_check", "Timed out")
        self.tick()
        self.assertEqual(self.ha.notes, [])                     # not before each has been offline for 10 hours


class Migration(PhonesCase):
    def test_columns_added_to_a_1_1_database(self):
        new = ("offline_alert_after_min", "offline_since", "offline_counted_s", "offline_counted_at",
               "offline_alerted", "app_alerted")
        schema = "\n".join(line for line in db.SCHEMA.splitlines()
                           if not re.match(r"\s+(%s) " % "|".join(new), line))
        folder = tempfile.mkdtemp(prefix="phones_mig12_")
        self.addCleanup(shutil.rmtree, folder, True)
        path = os.path.join(folder, "phones.db")
        conn = sqlite3.connect(path)
        conn.executescript(schema)
        self.assertFalse(set(new) & {r[1] for r in conn.execute("PRAGMA table_info(phones)")})
        conn.execute("INSERT INTO phones (id, device_id, first_seen, monitored, status) VALUES ('p1', 'd1', 1, 1, 'offline')")
        conn.commit()
        conn.close()
        with mock.patch.object(config, "DB_PATH", path):
            db.init_db()
            db.init_db()                                         # a second start changes nothing
            with db.get_conn() as c:
                r = dict(c.execute("SELECT * FROM phones WHERE id = 'p1'").fetchone())
        db.init_db()
        self.assertEqual((r["monitored"], r["status"]), (1, "offline"))
        self.assertEqual({k: r[k] for k in new}, {"offline_alert_after_min": None, "offline_since": None,
                                                  "offline_counted_s": 0, "offline_counted_at": None,
                                                  "offline_alerted": 0, "app_alerted": 0})


if __name__ == "__main__":
    import unittest
    unittest.main()
