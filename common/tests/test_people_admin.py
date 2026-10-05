"""The shared Admin → People helpers (common/python/people_admin.py), tested in every app that uses them."""
import sqlite3
import time
import unittest
from unittest import mock

from fastapi import HTTPException

from app.common import ha_notify, ha_people, people_admin


class PeopleAdmin(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("CREATE TABLE user_notify (user_id TEXT, service TEXT, created_at TEXT, created_by TEXT, "
                          "added_at TEXT, added_by TEXT, PRIMARY KEY (user_id, service))")
        self.addCleanup(self.conn.close)
        self.addCleanup(ha_people.reset)

    def test_ha_person_json(self):
        ha_people.reset()
        self.assertEqual(people_admin.ha_person_json("u1"), {"known": False, "person": None, "personName": None, "phones": []})
        with ha_people._lock:
            ha_people._cache.update(at=time.monotonic(), people=[
                {"entityId": "person.asha", "name": "Asha", "userId": "u1", "phones": [
                    {"label": "Pixel", "tracker": "device_tracker.pixel", "service": "mobile_app_pixel"},
                    {"label": "Old", "tracker": "device_tracker.old", "service": None}]}])
        self.assertEqual(people_admin.ha_person_json("u1"), {
            "known": True, "person": "person.asha", "personName": "Asha",
            "phones": [{"label": "Pixel", "service": "notify.mobile_app_pixel", "tracker": "device_tracker.pixel"},
                       {"label": "Old", "service": None, "tracker": "device_tracker.old"}]})
        self.assertEqual(people_admin.ha_person_json("u2")["person"], None)

    def test_refresh_only_when_asked(self):
        with mock.patch.object(ha_people, "refresh_blocking") as r:
            people_admin.refresh_people(False)
            r.assert_not_called()
            people_admin.refresh_people(True)
            r.assert_called_once_with(True)

    def test_notify_services(self):
        with mock.patch.object(ha_notify, "list_notify_services_blocking", return_value={"services": ["notify.a"], "entities": []}):
            self.assertEqual(people_admin.notify_services(), {"available": True, "error": None, "services": ["notify.a"], "entities": []})
        with mock.patch.object(ha_notify, "list_notify_services_blocking", side_effect=ha_notify.NotifyListError("no token")):
            out = people_admin.notify_services(True)
        self.assertEqual((out["available"], out["services"], out["entities"]), (False, [], []))
        self.assertEqual(out["error"], "Couldn't read the notify services from Home Assistant: no token")

    def test_clean_service(self):
        self.assertEqual(people_admin.clean_service("mobile_app_x"), "notify.mobile_app_x")
        for bad in ("notify.Bad Name", 7, None, "notify.x/y"):
            with self.subTest(bad=bad), self.assertRaises(HTTPException) as cm:
                people_admin.clean_service(bad)
            self.assertEqual(cm.exception.status_code, 422)

    def test_add_and_remove(self):
        out = people_admin.add_service(self.conn, "u1", "notify.b", "alex", "NOW", limit=2)
        self.assertEqual(out, ["notify.b"])
        self.assertEqual(people_admin.add_service(self.conn, "u1", "notify.b", "alex", "NOW", limit=2), ["notify.b"])
        people_admin.add_service(self.conn, "u1", "notify.a", "alex", "NOW", limit=2)
        with self.assertRaises(HTTPException) as cm:
            people_admin.add_service(self.conn, "u1", "notify.c", "alex", "NOW", limit=2)
        self.assertEqual((cm.exception.status_code, cm.exception.detail), (422, "At most 2 notify services per person."))
        self.assertEqual(people_admin.add_service(self.conn, "u1", "notify.a", "alex", "NOW", limit=2), ["notify.a", "notify.b"])
        row = self.conn.execute("SELECT * FROM user_notify WHERE service = 'notify.a'").fetchone()
        self.assertEqual((row["created_at"], row["created_by"]), ("NOW", "alex"))
        people_admin.add_service(self.conn, "u2", "notify.z", "sam", "THEN", limit=5, columns=("added_at", "added_by"))
        row = self.conn.execute("SELECT * FROM user_notify WHERE user_id = 'u2'").fetchone()
        self.assertEqual((row["added_at"], row["added_by"], row["created_by"]), ("THEN", "sam", None))
        self.assertEqual(people_admin.remove_service(self.conn, "u1", "notify.a", "gone"), ["notify.b"])
        with self.assertRaises(HTTPException) as cm:
            people_admin.remove_service(self.conn, "u1", "notify.a", "notify.a isn't theirs")
        self.assertEqual((cm.exception.status_code, cm.exception.detail), (404, "notify.a isn't theirs"))

    def test_limiter(self):
        lim = people_admin.TestLimiter(10)
        lim.check("u1")
        lim.check("u2")
        with self.assertRaises(HTTPException) as cm:
            lim.check("u1")
        self.assertEqual(cm.exception.status_code, 429)
        lim.last.clear()
        lim.check("u1")

    def test_results(self):
        self.assertEqual(people_admin.test_results({"a": True, "b": False}), {"notify.a": True, "notify.b": False})
        with mock.patch.object(ha_notify, "explain_failure", return_value="hint"):
            self.assertEqual(people_admin.test_results({"a": True, "b": False}, as_list=True),
                             [{"service": "notify.a", "ok": True, "hint": ""}, {"service": "notify.b", "ok": False, "hint": "hint"}])
        people_admin.require_one_sent({"notify.a": True, "notify.b": False}, "a", lambda: True)
        with self.assertRaises(HTTPException) as cm:
            people_admin.require_one_sent({"notify.a": False}, "a", lambda: False)
        self.assertEqual((cm.exception.status_code, cm.exception.detail), (502, "This app can't reach Home Assistant (no Supervisor token)."))
        with mock.patch.object(ha_notify, "explain_failure", return_value=""), self.assertRaises(HTTPException) as cm:
            people_admin.require_one_sent({"notify.a": False}, "a", lambda: True)
        self.assertEqual(cm.exception.detail, "Home Assistant didn't accept the test notification — check the service name.")


if __name__ == "__main__":
    unittest.main()
