"""Expiry dates, reminders and item templates."""
import datetime

from base import ADMIN, NEHA, PW, ApiTestCase, sql

from app import alerts, db, kdbx, reminders, storage


class Expiry(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.sent = []
        self._orig = alerts.deliver
        alerts.deliver = lambda services, title, message: self.sent.append((tuple(services), message))
        self.set_up(ADMIN)
        self.pv = self.personal()["id"]

    def tearDown(self):
        alerts.deliver = self._orig
        super().tearDown()

    def test_card_expiry_becomes_a_date_and_kdbx_keeps_it(self):
        it = self.add_item(self.pv, type="card", title="Visa", expiry="03/27", card={"number": "4111111111111111"})
        self.assertEqual(it["expires"], "2027-03-31")
        self.assertEqual(it["remindDays"], 30)
        with db.get_conn() as conn:
            raw = storage.read_current(conn, self.pv)
        d = kdbx.open_bytes(raw, PW[ADMIN["id"]])
        e = next(x for x in d.entries.values() if d.get_field(x, "Title") == "Visa")
        self.assertEqual(e.findtext("Times/Expires"), "True")
        self.assertEqual(kdbx.parse_time(e.findtext("Times/ExpiryTime")).date(), datetime.date(2027, 3, 31))
        # a date typed by hand wins over the card's MM/YY afterwards
        it = self.ok(self.patch(f"/api/vaults/{self.pv}/items/{it['id']}", {"expires": "2027-02-01"}))
        self.assertEqual(it["expires"], "2027-02-01")
        it = self.ok(self.patch(f"/api/vaults/{self.pv}/items/{it['id']}", {"expiry": "04/28"}))
        self.assertEqual(it["expires"], "2027-02-01")

    def test_templates_and_validation(self):
        it = self.add_item(self.pv, type="note", title="Passport — Kiran", template="identity", expires="2030-06-01",
                           remindDays=90, fields=[{"name": "Document number", "value": "Z1234567", "protected": True}])
        self.assertEqual((it["template"], it["expires"], it["remindDays"]), ("identity", "2030-06-01", 90))
        self.assertEqual(it["fields"][0], {"name": "Document number", "protected": True, "value": None})
        for bad in ({"template": "spaceship"}, {"expires": "31/12/2030"}, {"expires": "1800-01-01"}, {"remindDays": 5}):
            self.assertEqual(self.post(f"/api/vaults/{self.pv}/items", dict(title="x", **bad)).status_code, 422, bad)
        it = self.ok(self.patch(f"/api/vaults/{self.pv}/items/{it['id']}", {"expires": ""}))
        self.assertIsNone(it["expires"])
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM reminders")[0]["n"], 0)

    def test_expiring_list(self):
        today = datetime.date.today()
        self.add_item(self.pv, title="Soon", expires=(today + datetime.timedelta(days=10)).isoformat())
        self.add_item(self.pv, title="Gone", expires=(today - datetime.timedelta(days=3)).isoformat())
        self.add_item(self.pv, title="Far", expires=(today + datetime.timedelta(days=400)).isoformat())
        self.add_item(self.pv, title="Never")
        r = self.ok(self.get("/api/expiring?within=60"))["items"]
        self.assertEqual([(x["title"], x["daysLeft"]) for x in r], [("Gone", -3), ("Soon", 10)])

    def test_reminders_go_to_members_once_per_stage(self):
        self.set_up(NEHA)
        self.unlock(ADMIN)
        for u in (ADMIN, NEHA):
            self.ok(self.post(f"/api/admin/users/{u['id']}/notify", {"service": f"notify.mobile_app_{u['name']}"}, ADMIN, token=False), 201)
        hh = self.vaults()["Household"]["id"]
        self.add_item(hh, title="Car insurance", template="vehicle", type="note", expires="2027-01-31", remindDays=14)
        self.add_item(self.pv, title="My passport", expires="2027-01-31")               # default 30 days
        self.add_item(self.pv, title="Quiet one", expires="2027-01-31", remindDays=0)
        self.ok(self.put("/api/me/settings", {"expiryAlerts": False}, NEHA))
        with db.get_conn() as conn:
            self.assertEqual(reminders.run(conn, datetime.date(2026, 12, 1)), 0)          # 61 days: too early
            self.assertEqual(reminders.run(conn, datetime.date(2027, 1, 1)), 1)           # 30 days: the passport
        msgs = [m for _s, m in self.sent]
        self.assertEqual(len(msgs), 1)
        self.assertIn("My passport (", msgs[0])
        self.assertIn("Personal vault) expires in 30 days, on 31 Jan 2027", msgs[0])
        with db.get_conn() as conn:
            reminders.run(conn, datetime.date(2026, 12, 5))                               # nothing new
            reminders.run(conn, datetime.date(2027, 1, 20))                               # car: 11 days
            reminders.run(conn, datetime.date(2027, 1, 31))                               # both: today
            reminders.run(conn, datetime.date(2027, 2, 2))                                # nothing more
        msgs = [m for _s, m in self.sent]
        self.assertEqual(len(msgs), 4, msgs)
        self.assertTrue(any("Car insurance (Household) expires in 11 days" in m for m in msgs))
        self.assertEqual(sum("expires today" in m for m in msgs), 2)
        self.assertFalse(any("Quiet one" in m for m in msgs))
        self.assertFalse(any("mobile_app_neha" in s for s, _m in self.sent))            # she turned them off

    def test_changing_the_date_reminds_again_and_long_expired_is_quiet(self):
        self.ok(self.post(f"/api/admin/users/{ADMIN['id']}/notify", {"service": "notify.mobile_app_admin"}, token=False), 201)
        it = self.add_item(self.pv, title="Licence", expires="2027-01-31")
        self.add_item(self.pv, title="Ancient", expires="2001-01-01")
        with db.get_conn() as conn:
            reminders.run(conn, datetime.date(2027, 1, 31))
        self.ok(self.patch(f"/api/vaults/{self.pv}/items/{it['id']}", {"expires": "2037-01-31"}))
        with db.get_conn() as conn:
            reminders.run(conn, datetime.date(2037, 1, 30))
        msgs = [m for _s, m in self.sent]
        self.assertEqual(len(msgs), 2, msgs)
        self.assertFalse(any("Ancient" in m for m in msgs))
