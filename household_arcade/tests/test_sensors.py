"""Home Assistant sensors: nothing is posted while the switch is off (the
default); with it on, the record, played-today and playing sensors are kept
up to date; turning it off marks them unavailable once. Invented people only."""
import _env  # noqa: F401  (must be first)

import unittest

from app import config, ha_sensors
from base import ASHA, KABIR, MEERA, ApiBase


def posted(ha):
    return [p[len("/api/states/"):] for m, p, _b, _a in ha.requests if m == "POST" and p.startswith("/api/states/")]


class TestSensors(ApiBase):
    def test_off_by_default_nothing_posted(self):
        self.play(100)
        self.assertEqual(ha_sensors.push_blocking(force=True), {"pushed": 0, "failed": 0})
        self.assertEqual(posted(self.ha), [])

    def test_on_publishes_everything(self):
        self.make_child(KABIR, minutesSchool=30, quietSchoolFrom="21:00", quietSchoolTo="07:00")
        self.play(1240, seconds=300)
        self.play(800, seconds=60, h=MEERA, game="brick", mode="classic")
        self.settings({"ha_sensors": True})            # background task pushes at once
        st = self.ha.states
        rec = st["sensor.household_arcade_snake_record"]
        self.assertEqual(rec["state"], "1240")
        self.assertEqual(rec["attributes"]["person"], "Kabir Rao")
        self.assertEqual(rec["attributes"]["mode"], "Walls · Normal")
        self.assertEqual(st["sensor.household_arcade_brick_record"]["state"], "800")
        kabir = st["sensor.household_arcade_kabir_played_today"]
        self.assertEqual(kabir["state"], "5")
        self.assertEqual(kabir["attributes"]["minutes_left"], 25)
        self.assertFalse(kabir["attributes"]["quiet_hours"])
        self.assertEqual(st["sensor.household_arcade_meera_played_today"]["state"], "1")
        self.assertNotIn("minutes_left", st["sensor.household_arcade_meera_played_today"]["attributes"])
        self.assertEqual(st["binary_sensor.household_arcade_kabir_playing"]["state"], "off")
        self.assertIn("sensor.household_arcade_asha_played_today", st)

    def test_playing_follows_sessions(self):
        self.settings({"ha_sensors": True})
        r = self.start()
        self.assertEqual(self.ha.states["binary_sensor.household_arcade_kabir_playing"]["state"], "on")
        self.clock.advance(seconds=40)
        self.post(f"/api/sessions/{r.json()['id']}/end", {"activeSeconds": 40})
        self.assertEqual(self.ha.states["binary_sensor.household_arcade_kabir_playing"]["state"], "off")
        self.clock.advance(seconds=10)
        r = self.start()
        self.clock.advance(minutes=5)                   # no heartbeat any more
        ha_sensors.push_blocking()
        self.assertEqual(self.ha.states["binary_sensor.household_arcade_kabir_playing"]["state"], "off")

    def test_record_updates_on_new_score_only_when_changed(self):
        self.settings({"ha_sensors": True})
        self.play(100)
        self.assertEqual(self.ha.states["sensor.household_arcade_snake_record"]["state"], "100")
        self.ha.requests.clear()
        ha_sensors.push_blocking()
        self.assertEqual(posted(self.ha), [])          # nothing changed, nothing posted

    def test_switch_off_marks_unavailable_then_silent(self):
        self.settings({"ha_sensors": True})
        self.play(100)
        self.settings({"ha_sensors": False})
        self.assertEqual(self.ha.states["sensor.household_arcade_snake_record"]["state"], "unavailable")
        self.assertEqual(self.ha.states["binary_sensor.household_arcade_kabir_playing"]["state"], "unavailable")
        self.ha.requests.clear()
        self.play(500)
        ha_sensors.push_blocking(force=True)
        self.assertEqual(posted(self.ha), [])

    def test_unique_person_slugs(self):
        self.get("/api/me", {"X-Remote-User-Id": "u_k2", "X-Remote-User-Name": "Kabir",
                             "X-Remote-User-Display-Name": "Another Kabir"})
        self.settings({"ha_sensors": True})
        self.assertIn("sensor.household_arcade_kabir_played_today", self.ha.states)
        self.assertIn("sensor.household_arcade_kabir_2_played_today", self.ha.states)

    def test_disabled_people_not_published(self):
        self.patch("/api/admin/users/u_meera", {"disabled": True})
        self.settings({"ha_sensors": True})
        self.assertNotIn("sensor.household_arcade_meera_played_today", self.ha.states)

    def test_no_token_no_calls(self):
        self.settings({"ha_sensors": True})
        self.ha.requests.clear()
        config.SUPERVISOR_TOKEN = ""
        self.play(100)
        self.assertEqual(ha_sensors.push_blocking(force=True), {"pushed": 0, "failed": 0})
        self.assertEqual(posted(self.ha), [])

    def test_admin_page_has_the_switch(self):
        self.assertFalse(self.get("/api/admin/settings", ASHA).json()["values"]["ha_sensors"])


if __name__ == "__main__":
    unittest.main()
