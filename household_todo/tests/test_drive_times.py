"""The "Drive times (uses OpenStreetMap services)" App setting: off for a new
install, on for an older database that already uses drive times, and while
it is off nothing is looked up, routed or shown. Addresses are invented data."""
import _env  # noqa: F401  (must be first)

import unittest
from unittest import mock

from app import config, db, drive_time, geocode, reminders, settings
from test_admin_settings import SettingsBase
from test_api import ADMIN, ALICE


def _no_network(*a, **k):
    raise AssertionError("nothing may be sent while Drive times is off")


class DriveTimesSwitchBase(SettingsBase):
    def add_place(self, pid="p1", address="1 Example Road", minutes=None):
        with db.get_conn() as c:
            c.execute("INSERT INTO places (id, name, address, drive_minutes, drive_checked_at, drive_mode, created_at) "
                      "VALUES (?, 'Clinic', ?, ?, ?, 'no_tolls', ?)",
                      (pid, address, minutes, config.now_iso() if minutes else None, config.now_iso()))

    def stored_switch(self):
        with db.get_conn() as c:
            row = c.execute("SELECT value FROM app_settings WHERE key = 'drive_times_enabled'").fetchone()
        return None if row is None else row["value"]

    def as_older_database(self, home_address=None, place_minutes=None):
        """Make the current database look like one from before the switch existed, then start up again."""
        with db.get_conn() as c:
            c.execute("DELETE FROM app_settings WHERE key = 'drive_times_enabled'")
            if home_address is not None:
                c.execute("INSERT INTO app_settings (key, value, updated_at) VALUES ('home_address', ?, 'x')",
                          ('"%s"' % home_address,))
        if place_minutes is not None:
            self.add_place(minutes=place_minutes)
        db.init_db()


class TestDefaults(DriveTimesSwitchBase):
    def test_new_install_is_off(self):
        self.assertFalse(settings.drive_times_enabled())
        self.assertEqual(self.stored_switch(), "false")            # decided once, at the first start
        body = self.get("/api/admin/settings", ADMIN).json()
        self.assertIs(body["values"]["drive_times_enabled"], False)
        self.assertIs(body["defaults"]["drive_times_enabled"], False)
        self.assertIs(self.get("/api/whoami").json()["driveTimes"]["enabled"], False)

    def test_switch_is_a_strict_bool_and_admin_only(self):
        self.assertEqual(self.put_settings({"drive_times_enabled": 1}).status_code, 422)
        self.assertEqual(self.put("/api/admin/settings", {"drive_times_enabled": True}, ALICE).status_code, 403)
        r = self.put_settings({"drive_times_enabled": True})
        self.assertEqual(r.status_code, 200)
        self.assertIs(r.json()["values"]["drive_times_enabled"], True)
        self.assertIs(self.get("/api/whoami").json()["driveTimes"]["enabled"], True)


class TestExistingDataRule(DriveTimesSwitchBase):
    def test_older_database_with_a_home_address_turns_it_on(self):
        self.as_older_database(home_address="1 Example Road, Springfield")
        self.assertTrue(settings.drive_times_enabled())
        self.assertEqual(self.stored_switch(), "true")

    def test_older_database_with_computed_drive_times_turns_it_on(self):
        self.as_older_database(place_minutes=14)
        self.assertTrue(settings.drive_times_enabled())

    def test_older_database_without_either_stays_off(self):
        self.add_place()                                            # a place, but never estimated
        self.as_older_database(home_address="")                     # a blank home address doesn't count
        self.assertFalse(settings.drive_times_enabled())
        self.assertEqual(self.stored_switch(), "false")

    def test_decision_is_made_once(self):
        # a new install (off) later gets a home address without the switch: a restart doesn't flip it on
        with mock.patch.object(geocode, "geocode_blocking", _no_network):
            self.assertEqual(self.put_settings({"home_address": "1 Example Road"}).status_code, 200)
        self.add_place(pid="p2", address="2 Example Road", minutes=9)
        db.init_db()
        self.assertFalse(settings.drive_times_enabled())
        # and an admin who turned it off keeps it off
        self.put_settings({"drive_times_enabled": True})
        self.put_settings({"drive_times_enabled": False})
        db.init_db()
        self.assertFalse(settings.drive_times_enabled())

    def test_restoring_an_older_backup_applies_the_rule(self):
        self.as_older_database(place_minutes=12)                    # this file now has the row 'true'…
        with db.get_conn() as c:                                    # …so make it an older file again and back it up
            c.execute("DELETE FROM app_settings WHERE key = 'drive_times_enabled'")
        data = open(db.backup_to_tempfile(), "rb").read()
        self.put_settings({"drive_times_enabled": False})
        r = self.c.post("/api/admin-storage-import-db", headers=ADMIN, files={"file": ("b.db", data)})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(settings.drive_times_enabled())


class TestNothingSentWhileOff(DriveTimesSwitchBase):
    def setUp(self):
        super().setUp()
        patches = [mock.patch("app.geocode.urllib.request.urlopen", _no_network),
                   mock.patch.object(geocode, "geocode_blocking", _no_network),
                   mock.patch.object(geocode, "route_blocking", _no_network)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_home_address_saved_while_off_is_not_looked_up(self):
        r = self.put_settings({"home_address": "1 Example Road", "nominatim_url": "https://geo.example.org"})
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(geocode.ensure_home_blocking())
        self.assertIsNone(geocode.HOME_LATLON)
        self.assertFalse(geocode.feature_enabled())

    def test_background_warmer_does_nothing(self):
        self.put_settings({"home_address": "1 Example Road"})
        self.add_place()
        geocode.HOME_LATLON = (45.0, 7.0)                           # even with a stale home location
        self.assertEqual(drive_time.run_pass_blocking(), 0)
        self.assertEqual(drive_time.compute_for_address("1 Example Road"), (None, None, None, None))
        with db.get_conn() as c:
            self.assertIsNone(c.execute("SELECT drive_checked_at FROM places").fetchone()[0])

    def test_recalculate_is_refused(self):
        p = self.post("/api/places", {"name": "Gym", "address": "3 Example Road"}).json()
        r = self.post(f"/api/places/{p['id']}/recalculate-drive-time")
        self.assertEqual(r.status_code, 409)
        self.assertIn("turned off", r.json()["detail"])

    def test_cached_drive_times_are_hidden(self):
        self.add_place(minutes=18)
        place = self.get("/api/places").json()[0]
        self.assertEqual((place["driveMinutes"], place["driveAvoidTolls"], place["driveTollsAvoided"]), (None, None, None))
        task = self.add("Checkup", due_date="2026-09-22", due_time="10:00", place_id="p1")
        self.assertIsNone(task["place"]["driveMinutes"])
        self.assertEqual(reminders._drive_note("2026-09-22", "10:00", 18), "")
        # turned on, the cached estimate shows again (and turning it on looks nothing up without a home address)
        self.put_settings({"drive_times_enabled": True})
        self.assertEqual(self.get("/api/places").json()[0]["driveMinutes"], 18)

    def test_turning_off_forgets_home_at_once(self):
        geocode.HOME_LATLON, geocode._home_for = (45.0, 7.0), "1 Example Road"
        with db.get_conn() as c:
            c.execute("INSERT INTO app_settings (key, value, updated_at) VALUES ('home_address', '\"1 Example Road\"', 'x')")
            c.execute("UPDATE app_settings SET value = 'true' WHERE key = 'drive_times_enabled'")
        settings.invalidate()
        self.assertTrue(geocode.feature_enabled())
        self.put_settings({"drive_times_enabled": False})
        self.assertIsNone(geocode.HOME_LATLON)
        self.assertFalse(geocode.feature_enabled())


class TestTurningOn(DriveTimesSwitchBase):
    def test_turning_on_geocodes_home_in_the_background(self):
        asked = []
        with mock.patch.object(geocode, "geocode_blocking", lambda a: asked.append(a) or (45.0, 7.0)):
            self.put_settings({"home_address": "1 Example Road"})
            self.assertEqual(asked, [])                              # off: nothing sent
            self.put_settings({"drive_times_enabled": True})
        self.assertEqual(asked, ["1 Example Road"])
        self.assertTrue(geocode.feature_enabled())


if __name__ == "__main__":
    unittest.main()
