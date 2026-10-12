"""The group ledger a page at a time (SPEC §6 "Ledger paging"): GET /groups/{id}?limit=N and
GET /groups/{id}/ledger?limit=&cursor=. Newest first by (date, id); balances, counts and the CSV
export always cover every entry. Every person here is invented."""
import _env  # noqa: F401  (must be first)

import csv
import io
import os
import unittest

from app import main
from common_tests.ingress import identity_headers, ingress_client

ALICE = identity_headers("u_alice", "alice", "Alice")


class Ledger(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._ctx = ingress_client(main.app)
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
            for uid, name in (("a", "Ann"), ("b", "Ben"), ("c", "Cat")):
                conn.execute("INSERT INTO users (id, name, created_at) VALUES (?, ?, ?)", (uid, name, main.now_iso()))
            conn.commit()
        r = self.c.post("/api/groups", json={"name": "House", "memberIds": ["a", "b", "c"]}, headers=ALICE)
        self.gid = r.json()["id"]

    def add(self, n, date=None, paid_by="a", amount=None):
        for i in range(n):
            body = {"description": f"Item {i}", "amount": amount or (10 + i % 7), "paidBy": paid_by}
            if date:
                body["date"] = date
            r = self.c.post(f"/api/groups/{self.gid}/expenses", json=body, headers=ALICE)
            self.assertEqual(r.status_code, 201, r.text)

    def group(self, **q):
        r = self.c.get(f"/api/groups/{self.gid}", params=q, headers=ALICE)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def page(self, status=200, **q):
        r = self.c.get(f"/api/groups/{self.gid}/ledger", params=q, headers=ALICE)
        self.assertEqual(r.status_code, status, r.text)
        return r.json()

    def walk(self, limit):
        """Every entry through the pages (first from GET /groups/{id}?limit, then /ledger)."""
        g = self.group(limit=limit)
        ids, cursor, pages = [e["id"] for e in g["expenses"]], g["nextCursor"], 1
        while cursor:
            p = self.page(limit=limit, cursor=cursor)
            self.assertLessEqual(len(p["expenses"]), limit)
            ids += [e["id"] for e in p["expenses"]]
            cursor, pages = p["nextCursor"], pages + 1
        return ids, pages

    def test_empty_group(self):
        g = self.group(limit=20)
        self.assertEqual((g["expenses"], g["nextCursor"], g["ledgerTotal"]), ([], None, 0))
        self.assertEqual(self.page(), {"expenses": [], "nextCursor": None, "total": 0})

    def test_without_limit_everything_as_before(self):
        self.add(25)
        g = self.group()
        self.assertEqual(len(g["expenses"]), 25)
        self.assertEqual((g["ledgerTotal"], g["nextCursor"]), (25, None))

    def test_first_page_order_and_ties_on_the_same_date(self):
        # 30 on one bare date (all ties: ordered by id), 10 "today" (timestamps), 5 on an older date
        self.add(30, date="2026-01-15")
        self.add(10)
        self.add(5, date="2025-12-01")
        full = self.group()["expenses"]
        keys = [(e["date"], e["id"]) for e in full]
        self.assertEqual(keys, sorted(keys, reverse=True))                 # newest first, id breaks ties
        g = self.group(limit=20)
        self.assertEqual([e["id"] for e in g["expenses"]], [e["id"] for e in full[:20]])
        self.assertEqual(g["ledgerTotal"], 45)
        self.assertIsNotNone(g["nextCursor"])
        for limit in (1, 7, 20, 44, 45, 46):
            with self.subTest(limit=limit):
                ids, pages = self.walk(limit)
                self.assertEqual(ids, [e["id"] for e in full])            # each once, in order
                self.assertEqual(pages, -(-45 // limit))                   # no empty last page

    def test_last_page_exactly_full_has_no_cursor(self):
        self.add(40)
        g = self.group(limit=20)
        p = self.page(limit=20, cursor=g["nextCursor"])
        self.assertEqual(len(p["expenses"]), 20)
        self.assertIsNone(p["nextCursor"])
        self.assertEqual(p["total"], 40)

    def test_cursor_is_stable_when_entries_are_added_meanwhile(self):
        self.add(25, date="2026-02-01")
        g = self.group(limit=20)
        first = [e["id"] for e in g["expenses"]]
        self.add(3)                                                       # new entries on top
        self.add(2, date="2026-02-01")                                    # and ties on the same day
        p = self.page(limit=20, cursor=g["nextCursor"])
        rest = [e["id"] for e in p["expenses"]]
        self.assertFalse(set(first) & set(rest))                          # nothing repeats
        after = [e for e in self.group()["expenses"] if (e["date"], e["id"]) < (g["expenses"][-1]["date"], first[-1])]
        self.assertEqual(rest, [e["id"] for e in after])                  # exactly what lies below the cursor
        self.assertEqual(p["total"], 30)

    def test_balances_and_counts_cover_every_entry(self):
        self.add(30, paid_by="a")
        self.add(25, paid_by="b", date="2026-03-03")
        r = self.c.post(f"/api/groups/{self.gid}/payments", json={"fromUserId": "c", "toUserId": "a", "amount": 12.34},
                        headers=ALICE)
        self.assertEqual(r.status_code, 201)
        full, paged = self.group(), self.group(limit=5)
        self.assertEqual(paged["balances"], full["balances"])
        self.assertEqual(len(paged["expenses"]), 5)
        self.assertEqual(paged["ledgerTotal"], 56)
        # the same numbers the old way (every expense through compute_balances)
        net, transfers = main.compute_balances(full["memberIds"], full["expenses"])
        self.assertEqual({n["userId"]: n["amount"] for n in full["balances"]["net"]}, net)
        self.assertEqual([{k: t[k] for k in ("from", "to", "amount")} for t in full["balances"]["transfers"]], transfers)
        # the group list's count (no payments) and the CSV export (every row) aren't paged
        listed = next(x for x in self.c.get("/api/groups", headers=ALICE).json() if x["id"] == self.gid)
        self.assertEqual(listed["expenseCount"], 55)
        rows = list(csv.reader(io.StringIO(self.c.get(f"/api/groups/{self.gid}/export.csv", headers=ALICE).text)))
        self.assertEqual(len(rows) - 1, 56)

    def test_routes_answering_with_the_group_take_a_limit(self):
        self.add(30)
        r = self.c.post(f"/api/groups/{self.gid}/payments?limit=5",
                        json={"fromUserId": "b", "toUserId": "a", "amount": 5}, headers=ALICE)
        self.assertEqual((len(r.json()["expenses"]), r.json()["ledgerTotal"]), (5, 31))
        eid = r.json()["expenses"][1]["id"]
        r = self.c.put(f"/api/expenses/{eid}?limit=3", json={"description": "Edited", "amount": 9, "paidBy": "a"},
                       headers=ALICE)
        self.assertEqual(len(r.json()["expenses"]), 3)
        r = self.c.put(f"/api/groups/{self.gid}/default?limit=2", json={"isDefault": True}, headers=ALICE)
        self.assertEqual(len(r.json()["expenses"]), 2)
        r = self.c.post(f"/api/groups/{self.gid}/members?limit=4", json={"userId": "c"}, headers=ALICE)
        self.assertEqual(len(r.json()["expenses"]), 4)
        r = self.c.post(f"/api/groups/{self.gid}/payments", json={"fromUserId": "b", "toUserId": "a", "amount": 5},
                        headers=ALICE)
        self.assertEqual(len(r.json()["expenses"]), 32)                   # no limit: the whole ledger, as before

    def test_bad_input(self):
        self.add(3)
        self.page(400, cursor="not-a-cursor")
        self.page(400, cursor=main.encode_cursor("2026-01-01", "x")[:-3] + "!!!")
        self.page(422, limit=0)
        self.page(422, limit=main.LEDGER_MAX_LIMIT + 1)
        self.c.get(f"/api/groups/{self.gid}", params={"limit": 0}, headers=ALICE)
        self.assertEqual(self.c.get(f"/api/groups/{self.gid}", params={"limit": 0}, headers=ALICE).status_code, 422)
        self.assertEqual(self.c.get("/api/groups/nope/ledger", headers=ALICE).status_code, 404)
        # a well-formed cursor from nowhere is just a position: entries older than it
        self.assertEqual(self.page(cursor=main.encode_cursor("1999-01-01", "z"))["expenses"], [])

    def test_cursor_round_trip(self):
        for date, eid in (("2026-01-01", "abc"), ("2026-10-06T12:00:00.123456+00:00", "6f1c-…")):
            self.assertEqual(main.decode_cursor(main.encode_cursor(date, eid)), (date, eid))

    def test_ledger_index_exists(self):
        with main.get_conn() as conn:
            names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
        self.assertIn("idx_expenses_group_date", names)


    def test_each_entry_carries_everyones_balance_just_after_it(self):
        """The ledger's "Show each person's balance": oldest first, every entry added up to that point, in cents."""
        self.add(1, date="2026-01-01", paid_by="a", amount=30)         # Ann paid 30: Ann +20, Ben -10, Cat -10
        self.add(1, date="2026-01-02", paid_by="b", amount=0.07)       # Ben paid 0.07 (0.03/0.02/0.02)
        r = self.c.post(f"/api/groups/{self.gid}/payments", headers=ALICE,
                        json={"fromUserId": "c", "toUserId": "a", "amount": 10, "date": "2026-01-03"})
        self.assertEqual(r.status_code, 201, r.text)
        g = self.group(limit=20)
        after = {e["date"][:10]: {b["userId"]: b["amount"] for b in e["balancesAfter"]} for e in g["expenses"]}
        self.assertEqual(after["2026-01-01"], {"a": 20.0, "b": -10.0, "c": -10.0})
        self.assertEqual(after["2026-01-02"], {"a": 19.97, "b": -9.95, "c": -10.02})
        self.assertEqual(after["2026-01-03"], {"a": 9.97, "b": -9.95, "c": -0.02})
        for e in g["expenses"]:
            self.assertEqual([b["name"] for b in e["balancesAfter"]], ["Ann", "Ben", "Cat"])
            self.assertEqual(round(sum(b["amount"] for b in e["balancesAfter"]) * 100), 0)
        newest = {b["userId"]: b["amount"] for b in g["expenses"][0]["balancesAfter"]}
        self.assertEqual(newest, {n["userId"]: n["amount"] for n in g["balances"]["net"]})   # = the group's balances

    def test_balances_after_are_the_same_on_every_page(self):
        self.add(25, paid_by="b")
        whole = {e["id"]: e["balancesAfter"] for e in self.group()["expenses"]}
        g = self.group(limit=7)
        seen = {e["id"]: e["balancesAfter"] for e in g["expenses"]}
        cursor = g["nextCursor"]
        while cursor:
            p = self.page(limit=7, cursor=cursor)
            seen.update({e["id"]: e["balancesAfter"] for e in p["expenses"]})
            cursor = p["nextCursor"]
        self.assertEqual(seen, whole)

if __name__ == "__main__":
    unittest.main()
