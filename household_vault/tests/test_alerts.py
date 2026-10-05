"""Notifications: notify services per person, security alerts, preferences."""
from base import ADMIN, NEHA, VIKRAM, ApiTestCase, sql
from common_tests.fake_ha import FakeHA

from app import alerts, config, db
from app.common import ha_people


class AlertCase(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.sent = []
        alerts._throttle.clear()
        self._orig = alerts.deliver
        alerts.deliver = lambda services, title, message: self.sent.append((tuple(services), message))

    def tearDown(self):
        alerts.deliver = self._orig
        super().tearDown()

    def link(self, user, service="notify.mobile_app_x"):
        return self.ok(self.post(f"/api/admin/users/{user['id']}/notify", {"service": service}, ADMIN, token=False), 201)

    def to(self, service="mobile_app_x"):
        return [m for s, m in self.sent if service in s]


class NotifyServices(AlertCase):
    def test_assign_validate_remove(self):
        self.set_up(ADMIN)
        self.seen(NEHA)
        r = self.link(NEHA, "mobile_app_neha")
        self.assertEqual(r["notify"], ["notify.mobile_app_neha"])
        users = {u["id"]: u for u in self.ok(self.get("/api/admin/users", token=False))["users"]}
        self.assertEqual(users[NEHA["id"]]["notify"], ["notify.mobile_app_neha"])
        self.assertEqual(self.post(f"/api/admin/users/{NEHA['id']}/notify", {"service": "notify.Bad Name"},
                                   token=False).status_code, 422)
        self.assertEqual(self.post(f"/api/admin/users/{NEHA['id']}/notify", {"service": "notify.x"}, NEHA,
                                   token=False).status_code, 403)
        self.ok(self.delete(f"/api/admin/users/{NEHA['id']}/notify/notify.mobile_app_neha", token=False))
        self.assertEqual(self.delete(f"/api/admin/users/{NEHA['id']}/notify/notify.mobile_app_neha",
                                     token=False).status_code, 404)
        self.assertEqual(self.post(f"/api/admin/users/{NEHA['id']}/notify/test", token=False).status_code, 409)

    def test_me_shows_link_and_preferences(self):
        self.set_up(ADMIN)
        me = self.ok(self.get("/api/me"))
        self.assertFalse(me["notifyLinked"])
        self.assertTrue(me["securityAlerts"] and me["expiryAlerts"])
        self.assertEqual(me["hideLockSeconds"], -1)
        self.link(ADMIN)
        me = self.ok(self.put("/api/me/settings", {"securityAlerts": False, "hideLockSeconds": 10}))
        self.assertTrue(me["notifyLinked"])
        self.assertFalse(me["securityAlerts"])
        self.assertEqual(me["hideLockSeconds"], 10)
        self.assertEqual(self.put("/api/me/settings", {"hideLockSeconds": 5}).status_code, 422)
        self.assertTrue(self.ok(self.get("/api/whoami"))["notifyLinked"])


class SecurityAlerts(AlertCase):
    def test_wrong_passwords_alert_once_then_throttled(self):
        self.set_up(ADMIN)
        self.link(ADMIN)
        for _ in range(4):
            self.assertEqual(self.post("/api/unlock", {"password": "nope"}, token=False).status_code, 403)
        msgs = self.to()
        self.assertEqual(len(msgs), 1)                 # 3rd failure alerts; the 4th is throttled
        self.assertIn("wrong password 3 times", msgs[0])

    def test_preference_off_means_silence(self):
        self.set_up(ADMIN)
        self.link(ADMIN)
        self.ok(self.put("/api/me/settings", {"securityAlerts": False}))
        for _ in range(3):
            self.post("/api/unlock", {"password": "nope"}, token=False)
        self.assertEqual(self.to(), [])

    def test_downloads_sheet_and_vault_password_tell_the_others(self):
        self.set_up(ADMIN)
        self.set_up(NEHA)
        self.unlock(ADMIN)
        self.link(NEHA, "mobile_app_neha")
        self.link(ADMIN, "mobile_app_kiran")
        hh = self.vaults()["Household"]
        self.assertEqual(self.get(f"/api/vaults/{hh['id']}/download").status_code, 200)
        self.ok(self.get(f"/api/vaults/{hh['id']}/password"))
        self.ok(self.get(f"/api/vaults/{hh['id']}/sheet"))
        neha = self.to("mobile_app_neha")
        self.assertEqual(len(neha), 3, neha)
        self.assertTrue(any("downloaded a copy of Household" in m for m in neha))
        self.assertTrue(any("looked at the password of Household" in m for m in neha))
        self.assertTrue(any("printed the household sheet" in m for m in neha))
        self.assertEqual(self.to("mobile_app_kiran"), [])       # not told about your own actions
        # a Personal vault nobody else is in: nobody to tell
        self.assertEqual(self.get(f"/api/vaults/{self.personal()['id']}/download").status_code, 200)
        self.assertEqual(len(self.to("mobile_app_neha")), 3)

    def test_download_all_and_master_change_tell_you(self):
        self.set_up(ADMIN)
        self.link(ADMIN)
        from base import PW
        self.assertEqual(self.post("/api/me/download-all", {"password": PW[ADMIN["id"]]}).status_code, 200)
        self.ok(self.post("/api/me/password", {"current": PW[ADMIN["id"]], "new": "lemon-garden-quartz-violet-anchor"}))
        msgs = self.to()
        self.assertTrue(any("copy of all your passwords" in m for m in msgs))
        self.assertTrue(any("master password was just changed" in m for m in msgs))

    def test_admin_actions_tell_the_person(self):
        self.set_up(ADMIN)
        self.set_up(NEHA)
        self.link(NEHA)
        self.ok(self.patch(f"/api/users/{NEHA['id']}", {"disabled": True}, token=False))
        self.ok(self.post(f"/api/users/{NEHA['id']}/reset", token=False))
        msgs = self.to()
        self.assertTrue(any("turned off your access" in m for m in msgs))
        self.assertTrue(any("reset your Household Vault" in m for m in msgs))
        # no secret or vault content in any message
        self.assertFalse(any("maple" in m or "harbor" in m for m in msgs))

    def test_no_service_no_message(self):
        self.set_up(ADMIN)
        self.seen(VIKRAM)
        for _ in range(3):
            self.post("/api/unlock", {"password": "nope"}, token=False)
        self.assertEqual(self.sent, [])
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM user_notify")[0]["n"], 0)


class PhonesFromHomeAssistant(AlertCase):
    """Phones come from Home Assistant (Settings → People → Track device) for people who are enabled and
    set up here; an admin can still add extra notify services."""

    @classmethod
    def setUpClass(cls):
        cls.ha = FakeHA()
        cls.ha_url = cls.ha.start()

    @classmethod
    def tearDownClass(cls):
        cls.ha.stop()

    def setUp(self):
        super().setUp()
        self._cfg = (config.SUPERVISOR_TOKEN, config.SUPERVISOR_CORE_API)
        config.SUPERVISOR_TOKEN, config.SUPERVISOR_CORE_API = "test-token", self.ha_url
        self.ha.requests.clear()
        self.ha.people = None
        self.ha.notify_services = None

    def tearDown(self):
        config.SUPERVISOR_TOKEN, config.SUPERVISOR_CORE_API = self._cfg
        super().tearDown()

    def set_people(self):
        self.ha.notify_services = ["mobile_app_pixel_7", "mobile_app_vikram_s_phone_2", "family_speaker"]
        self.ha.people = [
            {"entity_id": "person.neha", "name": "Neha", "user_id": NEHA["id"], "state": "home", "picture": None,
             "phones": [{"name": "Pixel 7", "label": "Neha's phone", "tracker": "device_tracker.pixel_7"},
                        {"name": "Old Tablet", "label": "Old Tablet", "tracker": "device_tracker.old_tablet"}]},
            {"entity_id": "person.vikram", "name": "Vikram", "user_id": VIKRAM["id"], "state": "not_home", "picture": None,
             "phones": [{"name": "Vikram's Phone", "label": "Vikram's Phone", "tracker": "device_tracker.vikrams_phone"}]},
            {"entity_id": "person.kid", "name": "Kid", "user_id": None, "state": "home", "picture": None, "phones": []},
        ]
        ha_people.refresh_blocking(True)

    def admin_users(self, q=""):
        return {u["id"]: u for u in self.ok(self.get("/api/admin/users" + q, token=False))["users"]}

    def test_phone_linked_in_home_assistant_gets_alerts(self):
        self.set_up(ADMIN)
        self.set_up(NEHA)
        self.assertFalse(self.ok(self.get("/api/me", NEHA))["notifyLinked"])
        self.set_people()
        self.assertEqual(ha_people.phones_for(NEHA["id"]), ["mobile_app_pixel_7"])        # the tablet has no action
        self.assertEqual(ha_people.phones_for(VIKRAM["id"]), ["mobile_app_vikram_s_phone_2"])  # HA numbered it
        self.assertTrue(self.ok(self.get("/api/me", NEHA))["notifyLinked"])
        self.assertTrue(self.ok(self.get("/api/whoami", NEHA))["notifyLinked"])
        self.assertFalse(self.ok(self.get("/api/me"))["notifyLinked"])                    # no person for the admin
        for _ in range(3):
            self.post("/api/unlock", {"password": "nope"}, NEHA, token=False)
        msgs = self.to("mobile_app_pixel_7")
        self.assertEqual(len(msgs), 1)
        self.assertIn("wrong password 3 times", msgs[0])

    def test_admin_sees_phones_test_send_and_extras(self):
        self.set_up(ADMIN)
        self.set_up(NEHA)
        self.set_up(VIKRAM)
        self.set_people()
        users = self.admin_users()
        a = users[NEHA["id"]]["ha"]
        self.assertTrue(a["known"])
        self.assertEqual((a["person"], a["personName"]), ("person.neha", "Neha"))
        self.assertEqual([(p["label"], p["service"]) for p in a["phones"]],
                         [("Neha's phone", "notify.mobile_app_pixel_7"), ("Old Tablet", None)])
        self.assertIsNone(users[ADMIN["id"]]["ha"]["person"])
        self.link(NEHA, "notify.family_speaker")                          # an extra, added here
        self.ha.requests.clear()
        r = self.ok(self.post(f"/api/admin/users/{NEHA['id']}/notify/test", token=False))
        self.assertEqual({x["service"] for x in r["results"]}, {"notify.mobile_app_pixel_7", "notify.family_speaker"})
        self.assertTrue(all(x["ok"] for x in r["results"]))
        self.assertEqual(sorted(n for n, _ in self.ha.notifications()), ["family_speaker", "mobile_app_pixel_7"])
        # Vikram's phone: Home Assistant had to number its action
        self.ha.requests.clear()
        r = self.ok(self.post(f"/api/admin/users/{VIKRAM['id']}/notify/test", token=False))
        self.assertEqual([x["service"] for x in r["results"]], ["notify.mobile_app_vikram_s_phone_2"])
        self.assertEqual([n for n, _ in self.ha.notifications()], ["mobile_app_vikram_s_phone_2"])
        # the phone is Home Assistant's: it can't be removed here, only extras
        self.assertEqual(self.delete(f"/api/admin/users/{NEHA['id']}/notify/notify.mobile_app_pixel_7",
                                     token=False).status_code, 404)

    def test_disabled_or_not_set_up_gets_nothing_on_their_phone(self):
        self.set_up(ADMIN)
        self.seen(VIKRAM)                                                    # known, not enabled and set up
        self.set_up(NEHA)
        self.set_people()
        self.assertEqual(self.admin_users()[VIKRAM["id"]]["ha"]["phones"][0]["service"], "notify.mobile_app_vikram_s_phone_2")
        self.assertFalse(self.ok(self.get("/api/whoami", VIKRAM, token=False))["notifyLinked"])
        r = self.post(f"/api/admin/users/{VIKRAM['id']}/notify/test", token=False)
        self.assertEqual(r.status_code, 409)
        self.assertIn("isn't enabled and set up", r.json()["detail"])
        with db.get_conn() as conn:
            self.assertEqual(alerts.send(conn, [VIKRAM["id"]], "hello", kind="admin"), 0)
        self.ok(self.patch(f"/api/users/{NEHA['id']}", {"disabled": True}, token=False))
        self.assertEqual(self.to("mobile_app_pixel_7"), [])               # not even "turned off your access"
        self.assertFalse(self.ok(self.get("/api/whoami", NEHA, token=False))["notifyLinked"])
        self.ha.requests.clear()
        self.assertEqual(self.post(f"/api/admin/users/{NEHA['id']}/notify/test", token=False).status_code, 409)
        self.assertEqual(self.ha.notifications(), [])

    def test_no_person_or_phone_says_so_and_follows_home_assistant(self):
        self.set_up(ADMIN)
        self.set_up(NEHA)
        r = self.post(f"/api/admin/users/{ADMIN['id']}/notify/test", token=False)
        self.assertEqual(r.status_code, 409)
        self.assertIn("no phone linked in Home Assistant", r.json()["detail"])
        self.assertFalse(self.admin_users()[ADMIN["id"]]["ha"]["known"])     # never read yet
        self.set_people()
        self.ha.fail = True
        ha_people.refresh_blocking(True)                                    # HA down: the last answer stays
        self.ha.fail = False
        self.assertEqual(ha_people.phones_for(NEHA["id"]), ["mobile_app_pixel_7"])
        self.ha.people[0]["phones"] = []                                    # phone removed in HA
        self.assertEqual(self.admin_users("?refresh=1")[NEHA["id"]]["ha"]["phones"], [])
        self.assertEqual(ha_people.phones_for(NEHA["id"]), [])
        self.assertFalse(self.ok(self.get("/api/me", NEHA))["notifyLinked"])


if __name__ == "__main__":
    import unittest
    unittest.main()
