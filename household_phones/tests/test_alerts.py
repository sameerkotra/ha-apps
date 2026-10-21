"""Alerts: cooldowns, quiet hours, recipients, never to the phone itself, restarts, battery and the charging reminder
(spec/SPEC.md §5 "Alerts")."""
from base import ADMIN, ASHA, PhonesCase

from app import alerts, db


class Alerts(PhonesCase):
    def setUp(self):
        super().setUp()
        self.people([{"name": "Ann", "userId": "u-ann", "phones": [{"label": "Ann", "tracker": "t1", "service": "mobile_app_ann"}]},
                     {"name": "Asha", "userId": "u-asha", "phones": [
                         {"label": "Pixel", "tracker": "device_tracker.asha_pixel", "service": "mobile_app_asha_pixel"},
                         {"label": "Tab", "tracker": "t3", "service": "mobile_app_asha_tab"}]}])
        self.pid = self.add()

    def titles(self, word):
        return [s for s in self.sent if word in s[1]]

    def test_cooldown_per_phone_per_alert(self):
        r1 = alerts.send("offline", self.pid, "x", now=self.now)
        r2 = alerts.send("offline", self.pid, "x", now=self.now + 60)
        self.assertEqual((r1["skipped"], r2["skipped"]), (None, "cooldown"))
        self.assertIsNone(alerts.send("back", self.pid, "x", now=self.now + 60)["skipped"])    # another alert
        self.assertIsNone(alerts.send("offline", self.pid, "x", now=self.now + 31 * 60)["skipped"])
        self.setting(alert_cooldown_min=60)
        self.assertEqual(alerts.send("offline", self.pid, "x", now=self.now + 62 * 60)["skipped"], "cooldown")

    def test_never_to_the_phone_it_is_about(self):
        alerts.send("battery", self.pid, "low", to="owner", now=self.now)
        self.assertEqual([s[0] for s in self.sent], ["mobile_app_asha_tab"])
        self.sent.clear()
        alerts.send("charge", self.pid, "charge it", to="owner", now=self.now, also_phone=True)
        self.assertEqual(sorted(s[0] for s in self.sent), ["mobile_app_asha_pixel", "mobile_app_asha_tab"])

    def test_recipients(self):
        alerts.send("offline", self.pid, "x", to="both", now=self.now)
        self.assertEqual(sorted(s[0] for s in self.sent), ["mobile_app_ann", "mobile_app_asha_tab"])

    def test_recipient_quiet_hours_queue_until_the_end(self):
        self.put("/api/me/quiet", {"quietStart": "07:30", "quietEnd": "09:00"}, ADMIN)   # T0 is 08:00 UTC
        r = alerts.send("offline", self.pid, "x", to="admins", now=self.now)
        self.assertEqual((r["sent"], r["queued"]), ([], ["Ann Admin"]))
        self.assertEqual(alerts.release_queue(self.now + 30 * 60), 0)
        self.assertEqual(alerts.release_queue(self.now + 61 * 60), 1)
        self.assertEqual(self.sent[-1][0], "mobile_app_ann")
        # the household's quiet hours apply to whoever has none of their own
        self.put("/api/me/quiet", {"quietStart": "", "quietEnd": ""}, ADMIN)
        self.setting(quiet_start="07:00", quiet_end="08:30")
        r = alerts.send("back", self.pid, "x", to="admins", now=self.now)
        self.assertEqual(r["queued"], ["Ann Admin"])
        self.assertEqual(self.put("/api/me/quiet", {"quietStart": "7pm", "quietEnd": ""}, ADMIN).status_code, 422)

    def test_quiet_window_over_midnight(self):
        from datetime import datetime
        self.assertTrue(alerts.in_quiet("23:00", "06:00", datetime(2026, 1, 1, 2, 0)))
        self.assertFalse(alerts.in_quiet("23:00", "06:00", datetime(2026, 1, 1, 12, 0)))
        self.assertFalse(alerts.in_quiet("", "", datetime(2026, 1, 1, 2, 0)))

    def test_delivery_by_ha_notification_and_chat(self):
        self.setting(alert_delivery=["ha"])
        r = alerts.send("offline", self.pid, "x", now=self.now)
        self.assertEqual(r["sent"], ["Home Assistant"])
        self.assertEqual(len(self.ha.notes), 1)
        self.assertFalse(self.sent)

    def test_restart_with_a_guess_and_battery_once_per_discharge(self):
        eid = self.ha.eid
        self.report(self.pid, key="last_reboot", value="2027-01-14T10:00:00+00:00")
        self.report(self.pid, key="battery_level", value="3")
        self.report(self.pid, key="is_charging", value="off")
        self.tick()
        self.assertEqual(len(self.titles("Battery low")), 1)
        self.tick()
        self.assertEqual(len(self.titles("Battery low")), 1)                  # once per discharge
        self.advance(1)
        self.report(self.pid, key="last_reboot", value="2027-01-15T08:01:00+00:00")
        self.report(self.pid, key="battery_level", value="1")
        self.tick()
        restart = self.titles("Restarted")
        self.assertEqual(len(restart), 1)
        self.assertIn("battery was 3%, probably ran out", restart[0][2])
        # charging, then low again: a new discharge, a new alert (after the cooldown)
        self.report(self.pid, key="is_charging", value="on")
        self.report(self.pid, key="battery_level", value="30")
        self.tick()
        self.advance(40)
        self.report(self.pid, key="is_charging", value="off")
        self.report(self.pid, key="battery_level", value="10")
        self.tick()
        self.assertEqual(len(self.titles("Battery low")), 2)
        _ = eid

    def test_restart_after_an_update(self):
        from app import phones
        self.report(self.pid, key="last_reboot", value="a")
        self.report(self.pid, key="battery_level", value="70")
        self.tick()
        self.ha.devices[0]["sw_version"] = "16"
        phones.refresh(self.ha)
        self.report(self.pid, key="last_reboot", value="b")
        self.tick()
        self.assertIn("probably an update", self.titles("Restarted")[0][2])

    def test_back_online_off_by_default_then_on(self):
        self.report(self.pid)
        self.advance(30)
        self.tick()
        self.report(self.pid)
        self.tick()
        self.assertFalse(self.titles("Back online"))
        self.setting(alert_back=True)
        self.advance(30)
        self.tick()
        self.advance(31)
        self.report(self.pid)
        self.tick()
        self.assertEqual(len(self.titles("Back online")), 1)

    def test_charging_reminder_at_night(self):
        self.setting(alert_charge=True, charge_time="08:00", charge_pct=30)
        self.report(self.pid, key="battery_level", value="25")
        self.report(self.pid, key="is_charging", value="off")
        self.tick()
        sent = self.titles("Charging reminder")
        self.assertEqual(len(sent), 1)
        self.assertIn("is at 25% and not charging", sent[0][2])
        self.assertEqual(sent[0][0], "mobile_app_asha_tab")                   # the owner's other device
        self.advance(5)
        self.report(self.pid)
        self.tick()
        self.assertEqual(len(self.titles("Charging reminder")), 1)            # once a day

    def test_test_buttons(self):
        r = self.post("/api/admin/test-alert", {"alert": "offline"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("(test)", self.sent[-1][1])
        self.assertEqual(self.post("/api/admin/test-alert", {"alert": "nope"}).status_code, 422)
        self.assertEqual(self.post("/api/admin/test-alert", {"alert": "offline"}, ASHA).status_code, 403)
        with db.get_conn() as conn:
            self.assertIsNone(conn.execute("SELECT * FROM alert_state").fetchone())   # a test uses no cooldown

    def test_last_place_only_when_shared(self):
        self.setting(alert_last_place=True)
        self.report(self.pid)
        self.advance(30)
        self.tick()
        self.assertNotIn("Last seen at", self.titles("Offline")[0][2])
        self.put(f"/api/phones/{self.pid}/mine", {"sharePlace": True}, ASHA)
        self.report(self.pid)
        self.tick()
        self.advance(31 + 30)
        self.tick()
        self.assertIn("Last seen at Home", self.titles("Offline")[-1][2])
