"""The Activity search and the Summary report (SPEC §6.9): filters by who, group, days, amounts and words; pages;
the report's totals by group and person (paid and share), expenses only, the household's days. Invented data."""
import _env  # noqa: F401  (must be first)

import unittest

from app import main
from test_app import ADMIN, ALICE, Base, hdr

BEN = hdr("u_ben", "Ben B", "ben")


def today():
    return main.local_now().date().isoformat()


class Activity(Base):
    def setUp(self):
        super().setUp()
        r = self.c.post("/api/groups", json={"name": "Trip", "memberIds": ["a", "b"]}, headers=ALICE)
        self.trip = r.json()["id"]
        self.expense(12.5, description="Milk")                                   # Alice, House
        self.c.post(f"/api/groups/{self.trip}/expenses", headers=BEN,
                    json={"description": "Fuel", "amount": 80, "paidBy": "b"})    # Ben, Trip
        self.c.post(f"/api/groups/{self.gid}/payments", headers=BEN,
                    json={"fromUserId": "b", "toUserId": "a", "amount": 4.25})    # Ben, House

    def search(self, status=200, **q):
        r = self.c.get("/api/events/search", params=q, headers=ALICE)
        self.assertEqual(r.status_code, status, r.text)
        return r.json()

    def test_filters(self):
        every = self.search()["events"]
        self.assertGreaterEqual(len(every), 5)                                     # groups made, expenses, payment
        self.assertEqual([e["amount"] for e in self.search(actor="ben b")["events"]], [4.25, 80])
        self.assertEqual([e["amount"] for e in self.search(groupId=self.trip, min=1)["events"]], [80])
        self.assertEqual([e["amount"] for e in self.search(min=5, max=50)["events"]], [12.5])
        self.assertEqual([e["amount"] for e in self.search(max=12.5)["events"]], [4.25, 12.5])
        self.assertEqual([e["message"].split("'")[1] for e in self.search(q="fuel TRIP")["events"]], ["Fuel"])
        self.assertEqual(self.search(q="100%")["events"], [])                       # % is a letter, not a wildcard
        self.assertEqual(len(self.search(**{"from": today(), "to": today()})["events"]), len(every))
        self.assertEqual(self.search(**{"to": "2000-01-01"})["events"], [])
        self.assertEqual(self.c.get("/api/events/actors", headers=ALICE).json(), ["Alice", "Ben B"])
        for bad in ({"from": "2026-13-01"}, {"from": "2026-10-10", "to": "2026-10-01"}, {"min": 5, "max": 1},
                    {"min": -1}, {"before": "nonsense"}, {"from": "2000-01-01", "to": "2026-01-01"}):
            with self.subTest(bad=bad):
                self.search(422, **bad)

    def test_pages(self):
        first = self.search(limit=2)
        self.assertTrue(first["more"])
        second = self.search(limit=2, before=first["next"])
        ids = [e["id"] for e in first["events"] + second["events"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(ids, [e["id"] for e in self.search(limit=4)["events"]])

    def test_older_entries_get_their_amount(self):
        with main.get_conn() as conn:
            conn.executescript("ALTER TABLE events DROP COLUMN amount;")
            for typ, msg in (("expense_added", "Ann added 'Pizza 2.50' ($1,234.50) to House"),
                             ("expense_edited", "Ann edited 'Tea' ($3.00) in House: amount $3.00 → $4.75"),
                             ("expense_edited", "Ann edited 'Tea' ($3.00) in House: description 'Tea' → 'Chai'"),
                             ("payment_recorded", "Ann recorded Ben paying Ann ₹12.00 in House"),
                             ("group_created", "Ann created group 'Pizza 1.00'")):
                conn.execute("INSERT INTO events (id, type, message, created_at) VALUES (?, ?, ?, ?)",
                             (main.new_id(), typ, msg, "2020-01-01T00:00:00+00:00"))
            conn.commit()
        main.init_db()
        with main.get_conn() as conn:
            got = {r["message"]: r["amount"] for r in conn.execute(
                "SELECT message, amount FROM events WHERE created_at = '2020-01-01T00:00:00+00:00'")}
        self.assertEqual(list(got.values()), [1234.5, 4.75, 3.0, 12.0, None])


class Report(Base):
    def setUp(self):
        super().setUp()
        r = self.c.post("/api/groups", json={"name": "Trip", "memberIds": ["a", "b"]}, headers=ALICE)
        self.trip = r.json()["id"]
        self.expense(30, paid_by="a", description="Groceries")                       # a, b, c: 10 each
        self.expense(9, paid_by="b", description="Bread", date="2026-08-15")         # another month
        self.c.post(f"/api/groups/{self.trip}/expenses", headers=ALICE,
                    json={"description": "Fuel", "amount": 50, "paidBy": "b"})       # a, b: 25 each
        self.c.post(f"/api/groups/{self.gid}/payments", headers=ALICE,
                    json={"fromUserId": "b", "toUserId": "a", "amount": 10})        # not spending

    def report(self, status=200, **q):
        q.setdefault("from", today()[:8] + "01")
        q.setdefault("to", today())
        r = self.c.get("/api/report", params=q, headers=ALICE)
        self.assertEqual(r.status_code, status, r.text)
        return r.json()

    def test_totals_by_group_and_person(self):
        r = self.report()
        self.assertEqual((r["total"], r["count"], r["truncated"]), (80, 2, False))
        self.assertEqual([(g["name"], g["total"], g["count"]) for g in r["byGroup"]], [("Trip", 50, 1), ("House", 30, 1)])
        self.assertEqual({p["name"]: (p["paid"], p["share"]) for p in r["byPerson"]},
                         {"Ann": (30, 35), "Ben": (50, 35), "Cat": (0, 10)})
        self.assertEqual([t["description"] for t in r["transactions"]], ["Fuel", "Groceries"])

    def test_filters(self):
        self.assertEqual(self.report(paidBy="b")["total"], 50)
        self.assertEqual(self.report(groupId=self.gid)["total"], 30)
        self.assertEqual(self.report(min=31)["total"], 50)
        self.assertEqual(self.report(max=30, min=30)["total"], 30)
        r = self.report(**{"from": "2026-08-01", "to": "2026-08-31"})
        self.assertEqual(([t["description"] for t in r["transactions"]], r["byPerson"][0]["name"]), (["Bread"], "Ben"))
        self.assertEqual(self.report(paidBy="nobody")["byPerson"], [])
        for bad in ({"from": "x"}, {"from": "2026-10-10", "to": "2026-10-01"}, {"min": 3, "max": 2}):
            with self.subTest(bad=bad):
                self.report(422, **bad)
        self.assertEqual(self.c.get("/api/report", headers=ALICE).status_code, 422)          # a range is needed

    def test_the_last_day_is_counted_whole(self):
        self.expense(7, paid_by="c", description="Late", date="2026-08-31")
        r = self.report(**{"from": "2026-08-31", "to": "2026-08-31"})
        self.assertEqual([t["description"] for t in r["transactions"]], ["Late"])


if __name__ == "__main__":
    unittest.main()
