"""Splitpot tests. Run from the app folder:

    python3 -m unittest discover -s tests

Needs requirements-dev.txt (the pinned requirements plus httpx2 for
Starlette's TestClient). Every person and household in these tests is
invented data."""
import _env  # noqa: F401  (must be first)

import io
import logging
import os
import sqlite3
import unittest
from datetime import timedelta

from starlette.testclient import TestClient

import main

for _name in ("httpx", "httpx2"):
    logging.getLogger(_name).setLevel(logging.WARNING)


def hdr(uid, name, username=None):
    h = {"X-Remote-User-Id": uid, "X-Remote-User-Display-Name": name}
    if username:
        h["X-Remote-User-Name"] = username
    return h


ADMIN = hdr("u_admin", "Adminy", "adminy")
ALICE = hdr("u_alice", "Alice", "alice")


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main.ADMIN_NAMES.clear()
        main.ADMIN_NAMES.add("adminy")
        cls._ctx = TestClient(main.app, client=("127.0.0.1", 12345))   # stands in for HA's ingress proxy
        cls.c = cls._ctx.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls._ctx.__exit__(None, None, None)

    def setUp(self):
        for ext in ("", "-wal", "-shm"):
            p = str(main.DB_FILE) + ext
            if os.path.exists(p):
                os.remove(p)
        main.init_db()
        with main.get_conn() as conn:
            for uid, name in (("a", "Ann"), ("b", "Ben"), ("c", "Cat"), ("x", "Xavier")):
                conn.execute("INSERT INTO users (id, name, created_at) VALUES (?, ?, ?)", (uid, name, main.now_iso()))
            conn.commit()
        r = self.c.post("/api/groups", json={"name": "House", "memberIds": ["a", "b", "c"]}, headers=ALICE)
        self.assertEqual(r.status_code, 201, r.text)
        self.gid = r.json()["id"]

    def expense(self, amount=30, paid_by="a", status=201, **kw):
        body = {"description": "Groceries", "amount": amount, "paidBy": paid_by, **kw}
        r = self.c.post(f"/api/groups/{self.gid}/expenses", json=body, headers=ALICE)
        self.assertEqual(r.status_code, status, r.text)
        return r.json()

    def net(self):
        g = self.c.get(f"/api/groups/{self.gid}", headers=ALICE).json()
        return {n["userId"]: n["amount"] for n in g["balances"]["net"]}


class IngressGate(unittest.TestCase):
    def test_non_ingress_source_rejected_and_header_required(self):
        with TestClient(main.app, client=("172.30.33.9", 1)) as c:
            self.assertEqual(c.get("/api/groups", headers=ALICE).status_code, 403)
        with TestClient(main.app, client=("172.30.32.2", 1)) as c:
            self.assertEqual(c.get("/api/groups").status_code, 401)


class AdminMatching(Base):
    def test_login_name_matches_but_display_name_alone_does_not(self):
        self.assertTrue(self.c.get("/api/whoami", headers=ADMIN).json()["isAdmin"])
        impostor = hdr("u_eve", "adminy", "eve")
        w = self.c.get("/api/whoami", headers=impostor).json()
        self.assertFalse(w["isAdmin"])
        self.assertTrue(w["displayNameOnly"])
        self.assertEqual(self.c.get("/api/admin/backup-db", headers=impostor).status_code, 403)


class ExpenseValidation(Base):
    def test_split_people_must_be_group_members(self):
        self.expense(participants=["a", "x"], status=400)
        self.expense(splitType="custom", splits=[{"userId": "x", "amount": 30}], status=400)
        self.expense(paid_by="x", status=400)

    def test_duplicates_are_rejected_or_merged(self):
        self.expense(splitType="custom", splits=[{"userId": "a", "amount": 15}, {"userId": "a", "amount": 15}], status=400)
        e = self.expense(amount=30, participants=["a", "b", "b"])   # equal split just dedupes
        self.assertEqual(sorted(s["userId"] for s in e["splits"]), ["a", "b"])

    def test_negative_infinite_and_nan_amounts_rejected(self):
        self.expense(splitType="custom", splits=[{"userId": "a", "amount": 40}, {"userId": "b", "amount": -10}], status=422)
        for raw in ("Infinity", "NaN", "-5", "0"):
            r = self.c.post(f"/api/groups/{self.gid}/expenses", headers={**ALICE, "Content-Type": "application/json"},
                            content=f'{{"description": "x", "amount": {raw}, "paidBy": "a"}}')
            self.assertEqual(r.status_code, 422, raw)
        # dashboards still work afterwards
        self.assertEqual(self.c.get("/api/dashboard", headers=ALICE).status_code, 200)

    def test_dates_validated(self):
        self.expense(date="yesterday", status=400)
        future = (main.local_now().date() + timedelta(days=5)).isoformat()
        self.expense(date=future, status=400)
        past = (main.local_now().date() - timedelta(days=3)).isoformat()
        e = self.expense(date=past)
        self.assertEqual(e["date"], past)
        self.assertEqual(self.c.get("/api/transactions?period=month", headers=ALICE).status_code, 200)

    def test_group_members_validated(self):
        r = self.c.post("/api/groups", json={"name": "G", "memberIds": ["a", "nobody"]}, headers=ALICE)
        self.assertEqual(r.status_code, 400)
        r = self.c.post("/api/groups", json={"name": "G", "memberIds": ["a", "a", "b"]}, headers=ALICE)
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()["memberIds"], ["a", "b"])


class DateOnlyValues(unittest.TestCase):
    def test_bare_date_is_local_midnight(self):
        dt = main.parse_date("2026-09-20")
        self.assertEqual((dt.year, dt.month, dt.day, dt.hour), (2026, 9, 20, 0))
        self.assertEqual(dt.tzinfo, main._tz)


class EditAndPayments(Base):
    def test_edit_expense(self):
        e = self.expense(amount=30)
        r = self.c.put(f"/api/expenses/{e['id']}", headers=ALICE, json={
            "description": "Big shop", "amount": 60, "paidBy": "b", "splitType": "custom",
            "splits": [{"userId": "a", "amount": 30}, {"userId": "c", "amount": 30}],
            "date": main.local_now().date().isoformat()})
        self.assertEqual(r.status_code, 200, r.text)
        exp = r.json()["expenses"][0]
        self.assertEqual((exp["description"], exp["amount"], exp["paidBy"]), ("Big shop", 60, "b"))
        self.assertEqual(exp["date"], e["date"])   # same day -> timestamp kept
        self.assertEqual(self.net(), {"a": -30.0, "b": 60.0, "c": -30.0})
        self.assertEqual(self.c.put("/api/expenses/nope", headers=ALICE, json={
            "description": "x", "amount": 1, "paidBy": "a"}).status_code, 404)

    def test_payment_settles_debt_and_is_not_spending(self):
        self.expense(amount=30, participants=["a", "b", "c"])   # b and c each owe a 10
        r = self.c.post(f"/api/groups/{self.gid}/payments", headers=ALICE,
                        json={"fromUserId": "b", "toUserId": "a", "amount": 10})
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(self.net(), {"a": 10.0, "b": 0.0, "c": -10.0})
        t = self.c.get("/api/transactions?period=month", headers=ALICE).json()
        self.assertEqual((t["total"], t["count"]), (30, 1))
        d = self.c.get("/api/dashboard", headers=ALICE).json()
        self.assertEqual((d["expensesCount"], d["month"]["total"]), (1, 30))
        pay = [e for e in r.json()["expenses"] if e["splitType"] == "payment"][0]
        self.assertEqual(self.c.put(f"/api/expenses/{pay['id']}", headers=ALICE, json={
            "description": "x", "amount": 5, "paidBy": "b"}).status_code, 400)
        self.assertEqual(self.c.delete(f"/api/expenses/{pay['id']}", headers=ALICE).status_code, 403)
        self.assertEqual(self.c.delete(f"/api/expenses/{pay['id']}", headers=ADMIN).status_code, 204)
        self.assertEqual(self.net()["b"], -10.0)

    def test_export_group_csv(self):
        self.expense(amount=30, participants=["a", "b", "c"], description="=Groceries")
        self.c.post(f"/api/groups/{self.gid}/payments", headers=ALICE, json={"fromUserId": "b", "toUserId": "a", "amount": 10})
        r = self.c.get(f"/api/groups/{self.gid}/export.csv", headers=ALICE)    # anyone, not only admins
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.headers["content-type"].startswith("text/csv"))
        self.assertIn('attachment; filename="House - ', r.headers["content-disposition"])
        import csv as _csv
        rows = list(_csv.reader(io.StringIO(r.content.decode("utf-8-sig"))))
        self.assertEqual(rows[0], ["Date", "Type", "Description", "Amount", "Currency", "Paid by", "Split",
                                   "Ann share", "Ben share", "Cat share"])
        kinds = sorted((row[1], row[2], row[3], row[5], row[7:]) for row in rows[1:])
        self.assertEqual(kinds, [("Expense", "'=Groceries", "30.00", "Ann", ["10.00", "10.00", "10.00"]),
                                 ("Payment", "Ben paid Ann", "10.00", "Ben", ["10.00", "", ""])])
        self.assertEqual(self.c.get("/api/groups/nope/export.csv", headers=ALICE).status_code, 404)

    def test_payment_validation(self):
        for body in ({"fromUserId": "a", "toUserId": "a", "amount": 5},
                     {"fromUserId": "a", "toUserId": "x", "amount": 5}):
            self.assertEqual(self.c.post(f"/api/groups/{self.gid}/payments", headers=ALICE, json=body).status_code, 400)


class AdminOnlyDeletes(Base):
    def test_only_admins_can_delete_expenses(self):
        e = self.expense(amount=30)
        self.assertEqual(self.c.delete(f"/api/expenses/{e['id']}", headers=ALICE).status_code, 403)
        # a display name that matches an admin entry isn't enough either
        self.assertEqual(self.c.delete(f"/api/expenses/{e['id']}", headers=hdr("u_eve", "adminy", "eve")).status_code, 403)
        self.assertEqual(len(self.c.get(f"/api/groups/{self.gid}", headers=ALICE).json()["expenses"]), 1)
        self.assertEqual(self.c.delete(f"/api/expenses/{e['id']}", headers=ADMIN).status_code, 204)
        self.assertEqual(self.c.delete(f"/api/expenses/{e['id']}", headers=ADMIN).status_code, 404)
        events = self.c.get("/api/events", headers=ALICE).json()
        self.assertEqual(events[0]["type"], "expense_deleted")

    def test_only_admins_can_delete_groups(self):
        self.expense(amount=30)
        self.assertEqual(self.c.delete(f"/api/groups/{self.gid}", headers=ALICE).status_code, 403)
        self.assertEqual(self.c.get(f"/api/groups/{self.gid}", headers=ALICE).status_code, 200)
        self.assertEqual(self.c.delete(f"/api/groups/{self.gid}", headers=ADMIN).status_code, 204)
        self.assertEqual(self.c.get(f"/api/groups/{self.gid}", headers=ALICE).status_code, 404)
        self.assertEqual(self.c.delete(f"/api/groups/{self.gid}", headers=ADMIN).status_code, 404)

    def test_everyone_can_still_add_edit_and_record_payments(self):
        e = self.expense(amount=30)
        r = self.c.put(f"/api/expenses/{e['id']}", headers=ALICE,
                       json={"description": "Fixed typo", "amount": 30, "paidBy": "a"})
        self.assertEqual(r.status_code, 200, r.text)
        r = self.c.post(f"/api/groups/{self.gid}/payments", headers=ALICE,
                        json={"fromUserId": "b", "toUserId": "a", "amount": 5})
        self.assertEqual(r.status_code, 201, r.text)


class Currency(Base):
    # The currency is an App setting, so set it through the settings module.
    def test_money_uses_configured_currency(self):
        self.assertEqual(main.money(2), "$2.00")   # default USD
        main.update_settings({"currency": "EUR"}, "test")
        self.assertEqual(main.money(1234.5), "€1,234.50")
        main.update_settings({"currency": "THB"}, "test")   # valid code without a symbol in the map
        self.assertEqual(main.money(2), "THB 2.00")
        self.assertEqual(main.money(2, "XYZ"), "XYZ 2.00")


class PersonsCache(unittest.TestCase):
    def test_persons_fetched_once_per_window_and_outage_not_cached(self):
        calls = []
        orig = main.fetch_ha_persons
        main._persons_cache = None
        try:
            main.fetch_ha_persons = lambda: calls.append(1) or []
            main.cached_ha_persons(); main.cached_ha_persons()
            self.assertEqual(len(calls), 2)          # an empty answer (outage) isn't cached
            main.fetch_ha_persons = lambda: calls.append(1) or [{"entity_id": "person.ann", "name": "Ann"}]
            main.cached_ha_persons(); main.cached_ha_persons()
            self.assertEqual(len(calls), 3)
        finally:
            main.fetch_ha_persons = orig
            main._persons_cache = None


class BackupRestore(Base):
    def test_round_trip_and_older_backup_migrated(self):
        self.expense(amount=30)
        snap = self.c.get("/api/admin/backup-db", headers=ADMIN).content
        path = os.path.join(os.environ["DATA_DIR"], "old.db")
        with open(path, "wb") as f:
            f.write(snap)
        old = sqlite3.connect(path)       # pretend it predates the default-group feature
        old.execute("DROP INDEX IF EXISTS idx_groups_one_default")
        old.execute("ALTER TABLE groups DROP COLUMN is_default")
        old.commit(); old.close()
        self.expense(amount=99)
        with open(path, "rb") as f:
            r = self.c.post("/api/admin/import-db", headers=ADMIN, files={"file": ("old.db", f)})
        os.remove(path)
        self.assertEqual(r.status_code, 200, r.text)
        g = self.c.get(f"/api/groups/{self.gid}", headers=ALICE).json()
        self.assertEqual([e["amount"] for e in g["expenses"]], [30])
        r = self.c.put(f"/api/groups/{self.gid}/default", json={"isDefault": True}, headers=ALICE)
        self.assertEqual(r.status_code, 200, r.text)   # works without a restart
        r = self.c.post("/api/admin/import-db", headers=ADMIN, files={"file": ("x.db", io.BytesIO(b"junk"))})
        self.assertEqual(r.status_code, 400)


class SignedInPerson(Base):
    """/whoami names the Splitpot person for the signed-in HA user, so
    "Acting as" and "Paid by" can start as them. Invented people (Meera and
    Rohan Sharma)."""
    def setUp(self):
        super().setUp()
        self._orig = main.fetch_ha_persons
        main._persons_cache = None
        main.fetch_ha_persons = lambda: [
            {"entity_id": "person.meera_sharma", "name": "Meera Sharma", "user_id": "U_MEERA"},
            {"entity_id": "person.rohan_sharma", "name": "Rohan Sharma", "user_id": "u_rohan"},
        ]

    def tearDown(self):
        main.fetch_ha_persons = self._orig
        main._persons_cache = None

    def who(self, h):
        return self.c.get("/api/whoami", headers=h).json()

    def test_matched_by_ha_user_id_not_name(self):
        users = {u["name"]: u["id"] for u in self.c.get("/api/users", headers=ALICE).json()}
        with main.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT ha_user_id FROM users WHERE id = ?", (users["Meera Sharma"],)).fetchone()[0], "u_meera")
        # the display name says "Rohan Sharma", but the login is Meera's — the id wins
        self.assertEqual(self.who(hdr("u_meera", "Rohan Sharma"))["userId"], users["Meera Sharma"])
        self.assertEqual(self.who(hdr("U_ROHAN", "Someone"))["userId"], users["Rohan Sharma"])

    def test_found_before_the_people_list_is_synced(self):
        # whoami can run before GET /users stores the link (e.g. an older database)
        with main.get_conn() as conn:
            main.sync_users_from_ha(conn, [{"entity_id": "person.meera_sharma", "name": "Meera Sharma"}])   # no user_id stored
            pid = conn.execute("SELECT id FROM users WHERE ha_entity_id = 'person.meera_sharma'").fetchone()[0]
        self.assertEqual(self.who(hdr("u_meera", "M"))["userId"], pid)

    def test_display_name_fallback_only_when_unique_and_enabled(self):
        main.fetch_ha_persons = lambda: []
        self.assertEqual(self.who(hdr("u_nobody", "ben"))["userId"], "b")
        with main.get_conn() as conn:
            conn.execute("INSERT INTO users (id, name, created_at) VALUES ('b2', 'Ben', ?)", (main.now_iso(),))
            conn.commit()
        self.assertIsNone(self.who(hdr("u_nobody", "Ben"))["userId"])      # two Bens — don't guess
        with main.get_conn() as conn:
            conn.execute("UPDATE users SET disabled = 1 WHERE id = 'c'")
            conn.commit()
        self.assertIsNone(self.who(hdr("u_nobody", "Cat"))["userId"])      # disabled — never picked

    def test_disabled_linked_person_not_returned(self):
        users = {u["name"]: u["id"] for u in self.c.get("/api/users", headers=ALICE).json()}
        with main.get_conn() as conn:
            conn.execute("UPDATE users SET disabled = 1 WHERE id = ?", (users["Meera Sharma"],))
            conn.commit()
        self.assertIsNone(self.who(hdr("u_meera", "Meera Sharma"))["userId"])

    def test_dashboard_transactions_carry_payer_id(self):
        self.expense(amount=12, paid_by="b")
        t = self.c.get("/api/transactions?period=week", headers=ALICE).json()
        self.assertEqual([(e["paidBy"], e["paidByName"]) for e in t["expenses"]], [("b", "Ben")])

if __name__ == "__main__":
    unittest.main()
