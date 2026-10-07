"""Phone notifications for new charges and settle-up payments (SPEC §7.1), with the shared fake Home
Assistant (common_tests/fake_ha.py) behind the shared ha_notify / ha_people / people_admin. Every person here
is invented."""
import _env  # noqa: F401  (must be first)

import logging
import os
import sqlite3
import unittest
from unittest import mock

from app import config, main
from app.common import ha_client as ha_core, ha_notify, ha_people
from common_tests.fake_ha import FakeHA
from common_tests.ingress import identity_headers, ingress_client

for _name in ("httpx", "httpx2"):
    logging.getLogger(_name).setLevel(logging.WARNING)

# Splitpot people (users.id) and their Home Assistant logins (users.ha_user_id)
ASHA = identity_headers("ha_asha", "asha", "Asha")
RAVI = identity_headers("ha_ravi", "ravi", "Ravi")
ADMIN = identity_headers("ha_admin", "adminy", "Adminy")       # an admin who isn't a Splitpot person
NOBODY = identity_headers("ha_ghost", "ghost", "Ghost")        # a login with no person in Splitpot
PEOPLE = [   # what POST /api/template renders (ha_people)
    {"entity_id": "person.asha", "name": "Asha", "user_id": "ha_asha", "state": "home", "picture": None,
     "phones": [{"name": "Asha Pixel", "label": "Asha's Pixel", "tracker": "device_tracker.asha_pixel"}]},
    {"entity_id": "person.ravi", "name": "Ravi", "user_id": "ha_ravi", "state": "home", "picture": None,
     "phones": [{"name": "Ravi Phone", "label": "Ravi's phone", "tracker": "device_tracker.ravi_phone"}]},
    {"entity_id": "person.mia", "name": "Mia", "user_id": "ha_mia", "state": "away", "picture": None, "phones": []},
]


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main.ADMIN_NAMES.clear()
        main.ADMIN_NAMES.add("adminy")
        cls._ctx = ingress_client(main.app)
        cls.c = cls._ctx.__enter__()
        cls.ha = FakeHA()                  # one server per class (stopping one takes half a second)
        cls.ha_url = cls.ha.start()

    @classmethod
    def tearDownClass(cls):
        cls.ha.stop()
        cls._ctx.__exit__(None, None, None)

    def setUp(self):
        for ext in ("", "-wal", "-shm"):
            p = str(main.DB_FILE) + ext
            if os.path.exists(p):
                os.remove(p)
        main.init_db()
        with main.get_conn() as conn:
            for uid, name, ha in (("asha", "Asha", "ha_asha"), ("ravi", "Ravi", "ha_ravi"), ("mia", "Mia", "ha_mia"),
                                  ("zed", "Zed", None)):          # Zed: no Home Assistant login linked
                conn.execute("INSERT INTO users (id, name, created_at, ha_user_id) VALUES (?, ?, ?, ?)",
                             (uid, name, main.now_iso(), ha))
            conn.commit()
        self.ha.requests.clear()
        self.ha.states.clear()
        self.ha.fail = False
        self.ha.people = PEOPLE
        self.ha.notify_services = ["mobile_app_asha_pixel", "mobile_app_ravi_phone", "kitchen_speaker"]
        self.ha.notify_entities = []
        url = self.ha_url
        patches = [mock.patch.object(config, "SUPERVISOR_TOKEN", "test-token"),
                   mock.patch.object(config, "SUPERVISOR_CORE_API", url),
                   mock.patch.object(config, "SIDEBAR_PAGE", "/local_splitpot")]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        main._TEST_LIMITER.last.clear()
        ha_notify._targets_cache = None
        ha_notify._entity_mode.clear()
        ha_people.reset()
        self.addCleanup(ha_people.reset)
        ha_people.refresh_blocking(True)
        self.gid = self.c.post("/api/groups", json={"name": "Flat", "memberIds": ["asha", "ravi", "mia", "zed"]},
                               headers=ASHA).json()["id"]
        self.ha.requests.clear()

    def settings(self, **values):
        r = self.c.put("/api/admin/settings", json=values, headers=ADMIN)
        self.assertEqual(r.status_code, 200, r.text)

    def add(self, who=ASHA, **kw):
        body = {"description": "Groceries", "amount": 90, "paidBy": "asha",
                "participants": ["asha", "ravi", "mia"], **kw}
        r = self.c.post(f"/api/groups/{self.gid}/expenses", json=body, headers=who)
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()

    def sent(self):
        """{notify service: [message, ...]} of what reached the fake Home Assistant."""
        out = {}
        for name, body in self.ha.notifications():
            out.setdefault(name, []).append(body)
        return out


class Charges(Base):
    def test_off_by_default(self):
        self.assertFalse(main.get_setting("notify_charges"))
        self.add()
        self.assertEqual(self.ha.notifications(), [])

    def test_everyone_involved_but_whoever_added_it(self):
        self.settings(notify_charges=True)
        with main.get_conn() as conn:                              # Mia: an extra service from Admin → Users
            conn.execute("INSERT INTO user_notify (user_id, service, created_at) VALUES ('mia', 'notify.kitchen_speaker', 'x')")
            conn.commit()
        self.add()                                                 # Asha adds, Asha paid, split three ways
        sent = self.sent()
        self.assertEqual(set(sent), {"mobile_app_ravi_phone", "kitchen_speaker"})   # not Asha; Zed isn't in it
        ravi = sent["mobile_app_ravi_phone"][0]
        self.assertEqual(ravi["title"], "Splitpot")
        self.assertEqual(ravi["message"], "Asha added 'Groceries' ($90.00) to Flat, paid by Asha. Your share: $30.00.")
        link = "/local_splitpot/group/" + self.gid
        self.assertEqual(ravi["data"], {"url": link, "clickAction": link})
        self.assertIn("Your share: $30.00", sent["kitchen_speaker"][0]["message"])

    def test_payer_hears_when_someone_else_adds_it_and_currency_is_used(self):
        self.settings(notify_charges=True, currency="EUR")
        self.add(who=RAVI, amount=100, participants=["ravi", "mia"])     # Ravi adds what Asha paid
        sent = self.sent()
        self.assertEqual(set(sent), {"mobile_app_asha_pixel"})     # Mia has no phone and no service
        self.assertEqual(sent["mobile_app_asha_pixel"][0]["message"],
                         "Ravi added 'Groceries' (€100.00) to Flat, paid by you. You're not in the split.")

    def test_custom_split_share_and_unlinked_actor(self):
        self.settings(notify_charges=True)
        self.add(who=ADMIN, splitType="custom", participants=None,
                 splits=[{"userId": "asha", "amount": 60}, {"userId": "ravi", "amount": 30}])
        sent = self.sent()
        self.assertEqual(sent["mobile_app_asha_pixel"][0]["message"],
                         "Adminy added 'Groceries' ($90.00) to Flat, paid by you. Your share: $60.00.")
        self.assertIn("Your share: $30.00.", sent["mobile_app_ravi_phone"][0]["message"])

    def test_opt_out_and_disabled(self):
        self.settings(notify_charges=True)
        r = self.c.put("/api/me/prefs", json={"receiveNotifications": False}, headers=RAVI)
        self.assertEqual(r.json()["receiveNotifications"], False)
        self.add(who=NOBODY)
        self.assertEqual(set(self.sent()), {"mobile_app_asha_pixel"})
        self.ha.requests.clear()
        with main.get_conn() as conn:
            conn.execute("UPDATE users SET disabled = 1 WHERE id = 'asha'")
            conn.execute("UPDATE users SET receive_notifications = 1 WHERE id = 'ravi'")
            conn.commit()
        self.add(who=NOBODY)
        self.assertEqual(set(self.sent()), {"mobile_app_ravi_phone"})

    def test_edits_and_deletes_are_not_notified(self):
        self.settings(notify_charges=True)
        e = self.add(who=NOBODY)
        self.ha.requests.clear()
        r = self.c.put(f"/api/expenses/{e['id']}", json={"description": "Fixed", "amount": 50, "paidBy": "ravi"},
                       headers=NOBODY)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.c.delete(f"/api/expenses/{e['id']}", headers=ADMIN).status_code, 204)
        self.assertEqual(self.ha.notifications(), [])

    def test_home_assistant_down_or_missing_never_breaks_adding(self):
        self.settings(notify_charges=True)
        self.ha.fail = True
        with self.assertLogs("ha_notify", "WARNING"):
            self.add(who=NOBODY)                                   # still a 201
        self.ha.fail = False
        self.ha.requests.clear()
        with mock.patch.object(config, "SUPERVISOR_TOKEN", ""):    # outside Home Assistant: nothing is tried
            self.add(who=NOBODY)
        self.assertEqual(self.ha.requests, [])
        with mock.patch.object(main, "send_notices", side_effect=RuntimeError("boom")), \
                self.assertLogs("splitpot", "ERROR"):
            self.add(who=NOBODY)                                   # a bug in sending is logged, not raised

    def test_sent_after_the_response_with_no_connection_open(self):
        self.settings(notify_charges=True)
        calls = []
        real = ha_core.request
        open_conns = []
        orig_get_conn = main.get_conn

        def tracking_get_conn():
            ctx = orig_get_conn()

            class Wrap:
                def __enter__(self):
                    open_conns.append(1)
                    return ctx.__enter__()

                def __exit__(self, *a):
                    open_conns.pop()
                    return ctx.__exit__(*a)
            return Wrap()

        def spy(method, path, *a, **k):
            calls.append((path, len(open_conns)))
            return real(method, path, *a, **k)
        with mock.patch.object(main, "get_conn", tracking_get_conn), mock.patch.object(ha_core, "request", spy):
            self.add(who=NOBODY)
        notify_calls = [c for c in calls if c[0].startswith("/services/notify/")]
        self.assertEqual(len(notify_calls), 2)
        self.assertTrue(all(n == 0 for _, n in notify_calls))      # never while a DB connection is open

    def test_quiet_without_any_phone(self):
        self.settings(notify_charges=True)
        self.ha.people = []
        ha_people.refresh_blocking(True)
        self.add(who=NOBODY, participants=["mia", "zed"], paidBy="zed")
        self.assertEqual(self.ha.notifications(), [])


class Payments(Base):
    def pay(self, who, frm="ravi", to="asha", amount=25):
        r = self.c.post(f"/api/groups/{self.gid}/payments", json={"fromUserId": frm, "toUserId": to, "amount": amount},
                        headers=who)
        self.assertEqual(r.status_code, 201, r.text)

    def test_both_people_but_not_whoever_recorded_it(self):
        self.settings(notify_charges=True)
        self.pay(RAVI)                                             # Ravi records paying Asha
        self.assertEqual(self.sent(), {"mobile_app_asha_pixel": [mock.ANY]})
        self.assertEqual(self.sent()["mobile_app_asha_pixel"][0]["message"],
                         "Ravi paid you $25.00 in Flat (a settle-up payment).")
        self.ha.requests.clear()
        self.pay(ASHA)                                             # Asha records that Ravi paid her
        self.assertEqual(self.sent()["mobile_app_ravi_phone"][0]["message"],
                         "Asha recorded your payment of $25.00 to them in Flat.")
        self.assertNotIn("mobile_app_asha_pixel", self.sent())
        self.ha.requests.clear()
        self.pay(ADMIN)                                            # someone else: both hear
        sent = self.sent()
        self.assertEqual(sent["mobile_app_asha_pixel"][0]["message"], "Adminy recorded Ravi paying you $25.00 in Flat.")
        self.assertEqual(sent["mobile_app_ravi_phone"][0]["message"], "Adminy recorded you paying Asha $25.00 in Flat.")

    def test_switches(self):
        self.pay(ADMIN)                                            # everything off by default
        self.settings(notify_charges=True, notify_payments=False)
        self.pay(ADMIN)
        self.assertEqual(self.ha.notifications(), [])
        self.settings(notify_payments=True, notify_charges=False)  # payments need the main switch too
        self.pay(ADMIN)
        self.assertEqual(self.ha.notifications(), [])


class MyPrefs(Base):
    def test_get_and_put(self):
        p = self.c.get("/api/me/prefs", headers=RAVI).json()
        self.assertEqual((p["linked"], p["name"], p["receiveNotifications"], p["reachable"], p["notifyCharges"]),
                         (True, "Ravi", True, True, False))
        self.assertFalse(self.c.get("/api/me/prefs", headers=identity_headers("ha_mia", "mia", "Mia")).json()["reachable"])
        p = self.c.put("/api/me/prefs", json={"receiveNotifications": False}, headers=RAVI).json()
        self.assertFalse(p["receiveNotifications"])
        with main.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT receive_notifications FROM users WHERE id='ravi'").fetchone()[0], 0)

    def test_matched_by_login_only(self):
        self.assertFalse(self.c.get("/api/me/prefs", headers=NOBODY).json()["linked"])
        r = self.c.put("/api/me/prefs", json={"receiveNotifications": False}, headers=NOBODY)
        self.assertEqual(r.status_code, 404)
        # a display name that matches a person is NOT enough
        r = self.c.put("/api/me/prefs", json={"receiveNotifications": False},
                       headers=identity_headers("ha_other", "other", "Ravi"))
        self.assertEqual(r.status_code, 404)

    def test_validation(self):
        for body in ({"receiveNotifications": "no"}, {"receiveNotifications": 0}, {}, {"receiveNotifications": True, "x": 1}):
            with self.subTest(body=body):
                self.assertEqual(self.c.put("/api/me/prefs", json=body, headers=RAVI).status_code, 422)

    def test_whoami_says_whether_a_phone_is_linked(self):
        self.assertTrue(self.c.get("/api/whoami", headers=RAVI).json()["notifyLinked"])
        w = self.c.get("/api/whoami", headers=identity_headers("ha_mia", "mia", "Mia")).json()
        self.assertFalse(w["notifyLinked"])
        self.assertEqual(w["extras"][0]["label"], "Phone linked for notifications")


class AdminPeople(Base):
    def test_list_shows_phones_services_and_choice(self):
        users = {u["id"]: u for u in self.c.get("/api/admin/users", headers=ADMIN).json()}
        self.assertEqual(users["ravi"]["ha"]["phones"],
                         [{"label": "Ravi's phone", "service": "notify.mobile_app_ravi_phone",
                           "tracker": "device_tracker.ravi_phone"}])
        self.assertEqual(users["zed"]["ha"]["person"], None)
        self.assertEqual((users["ravi"]["notify"], users["ravi"]["receiveNotifications"]), ([], True))
        self.assertEqual(self.c.get("/api/admin/users", headers=RAVI).status_code, 403)

    def test_check_again_reads_home_assistant(self):
        self.ha.people = PEOPLE[:1]
        users = {u["id"]: u for u in self.c.get("/api/admin/users", headers=ADMIN).json()}
        self.assertIsNotNone(users["ravi"]["ha"]["person"])                 # cached
        users = {u["id"]: u for u in self.c.get("/api/admin/users?refresh=1", headers=ADMIN).json()}
        self.assertIsNone(users["ravi"]["ha"]["person"])

    def test_extra_services_and_test(self):
        r = self.c.post("/api/admin/users/mia/notify", json={"service": "kitchen_speaker"}, headers=ADMIN)
        self.assertEqual((r.status_code, r.json()["notify"]), (201, ["notify.kitchen_speaker"]))
        self.assertEqual(self.c.post("/api/admin/users/mia/notify", json={"service": "Bad Name"}, headers=ADMIN).status_code, 422)
        self.assertEqual(self.c.post("/api/admin/users/nope/notify", json={"service": "x"}, headers=ADMIN).status_code, 404)
        self.assertEqual(self.c.post("/api/admin/users/mia/notify", json={"service": "x"}, headers=RAVI).status_code, 403)
        r = self.c.post("/api/admin/users/mia/notify/test", headers=ADMIN)
        self.assertEqual(r.json(), {"results": {"notify.kitchen_speaker": True}})
        self.assertEqual(self.c.post("/api/admin/users/mia/notify/test", headers=ADMIN).status_code, 429)
        r = self.c.delete("/api/admin/users/mia/notify/notify.kitchen_speaker", headers=ADMIN)
        self.assertEqual(r.json()["notify"], [])
        self.assertEqual(self.c.delete("/api/admin/users/mia/notify/notify.kitchen_speaker", headers=ADMIN).status_code, 404)
        r = self.c.post("/api/admin/users/zed/notify/test", headers=ADMIN)
        self.assertEqual(r.status_code, 400)
        r = self.c.post("/api/admin/users/ravi/notify/test", headers=ADMIN)       # the phone from Home Assistant
        self.assertEqual(r.json(), {"results": {"notify.mobile_app_ravi_phone": True}})

    def test_notify_services_list(self):
        r = self.c.get("/api/admin/notify-services", headers=ADMIN).json()
        self.assertTrue(r["available"])
        self.assertIn("notify.kitchen_speaker", r["services"])
        with mock.patch.object(config, "SUPERVISOR_TOKEN", ""):
            r = self.c.get("/api/admin/notify-services?refresh=1", headers=ADMIN).json()
        self.assertFalse(r["available"])

    def test_startup_check_reads_the_assignments(self):
        with main.get_conn() as conn:
            conn.execute("INSERT INTO user_notify (user_id, service, created_at) VALUES ('mia', 'notify.gone_phone', 'x')")
            conn.commit()
        with self.assertLogs("ha_notify", "WARNING"):
            self.assertEqual(ha_notify.check_targets_blocking(), 1)


class SettingsAndSchema(Base):
    def test_settings_validation_and_log(self):
        r = self.c.put("/api/admin/settings", json={"notify_charges": "yes"}, headers=ADMIN)
        self.assertEqual(r.status_code, 422)
        meta = self.c.get("/api/admin/settings", headers=ADMIN).json()
        self.assertEqual(meta["meta"]["notify_payments"]["enabledIf"], "notify_charges")
        self.assertIn("notify", [g["id"] for g in meta["groups"]])
        self.settings(notify_charges=True)
        events = self.c.get("/api/events", headers=ADMIN).json()
        self.assertIn("charge notifications on", events[0]["message"])

    def test_old_database_gets_the_new_column_and_table(self):
        os.remove(main.DB_FILE)
        conn = sqlite3.connect(main.DB_FILE)
        conn.executescript("""
            CREATE TABLE users (id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL, ha_entity_id TEXT,
                                disabled INTEGER NOT NULL DEFAULT 0, ha_user_id TEXT);
            INSERT INTO users (id, name, created_at, ha_user_id) VALUES ('old', 'Old', 'x', 'ha_old');
        """)
        conn.commit()
        conn.close()
        main.init_db()
        with main.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT receive_notifications FROM users WHERE id='old'").fetchone()[0], 1)
            conn.execute("SELECT user_id, service, created_at, created_by FROM user_notify").fetchall()


if __name__ == "__main__":
    unittest.main()


class SidebarLinks(unittest.TestCase):
    """A notification's "/<page>/group/<id>" asked of the app itself: a redirect to the page's "#/group/<id>"."""

    def test_group_route_redirects_to_the_page(self):
        with ingress_client(main.app) as c:
            r = c.get("/group/abc-123", headers=ASHA, follow_redirects=False)
            self.assertEqual((r.status_code, r.headers["location"]), (307, "../../#/group/abc-123"))
            self.assertEqual(c.get("/group/a%20b", headers=ASHA, follow_redirects=False).status_code, 404)
            self.assertEqual(c.get("/api/config", headers=ASHA).json()["page"], config.SIDEBAR_PAGE)
