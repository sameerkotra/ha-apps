"""Split an expense by percentage (splitType "percent"). Invented data.

Run with the rest: python3 -m unittest discover -s tests"""
import _env  # noqa: F401  (must be first)

import random
import unittest

from fastapi import HTTPException

from app import main
from test_app import ALICE, Base


def pct(*rows):
    return [{"userId": uid, "percent": p} for uid, p in rows]


class PercentShares(unittest.TestCase):
    """The pure rounding function."""

    def shares(self, amount, *percents):
        ids = "abcdefghij"
        return [s["amount"] for s in main.percent_shares(amount, list(zip(ids, percents)))]

    def test_rounding_cases(self):
        self.assertEqual(self.shares(100, 33.33, 33.33, 33.34), [33.33, 33.33, 33.34])
        self.assertEqual(self.shares(10, 33.33, 33.33, 33.34), [3.33, 3.33, 3.34])
        self.assertEqual(self.shares(0.01, 33.33, 33.33, 33.34), [0.0, 0.0, 0.01])   # largest remainder
        self.assertEqual(self.shares(0.01, 50, 50), [0.01, 0.0])                     # tie -> list order
        self.assertEqual(self.shares(1000.01, *[12.5] * 8), [125.01] + [125.0] * 7)
        self.assertEqual(self.shares(99.99, 60, 40), [59.99, 40.0])
        self.assertEqual(self.shares(1_000_000, 0.01, 99.99), [100.0, 999900.0])

    def test_percentages_rounded_to_two_decimals(self):
        out = main.percent_shares(20, [("a", 50.004), ("b", 49.996)])
        self.assertEqual([(s["percent"], s["amount"]) for s in out], [(50.0, 10.0), (50.0, 10.0)])
        with self.assertRaises(HTTPException) as cm:
            main.percent_shares(20, [("a", 33.333), ("b", 33.333), ("c", 33.3349)])   # 99.99 once rounded
        self.assertEqual(cm.exception.detail, "Percentages must add up to 100%")

    def test_zero_rows_dropped_and_all_zero_rejected(self):
        out = main.percent_shares(30, [("a", 0), ("b", 100), ("c", 0)])
        self.assertEqual(out, [{"userId": "b", "amount": 30.0, "percent": 100.0}])
        with self.assertRaises(HTTPException):
            main.percent_shares(30, [("a", 0), ("b", 0)])

    def test_shares_always_sum_exactly_and_are_never_negative(self):
        rnd = random.Random(1234)
        for _ in range(3000):
            n = rnd.randint(1, 8)
            cuts = sorted(rnd.randint(0, 10000) for _ in range(n - 1))
            hundredths = [b - a for a, b in zip([0] + cuts, cuts + [10000])]
            amount = rnd.randint(1, 100_000_000) / 100
            rows = [(f"u{i}", h / 100) for i, h in enumerate(hundredths)]
            if not any(hundredths):
                continue
            out = main.percent_shares(amount, rows)
            cents = [round(s["amount"] * 100) for s in out]
            self.assertEqual(sum(cents), round(amount * 100), (amount, rows))
            for s, c in zip(out, cents):
                self.assertGreaterEqual(c, 0)
                self.assertLessEqual(abs(c - amount * s["percent"]), 1.0 + 1e-6, (amount, rows))


class PercentExpensesApi(Base):
    def add(self, splits, amount=100, status=201, paid_by="a"):
        return self.expense(amount=amount, paid_by=paid_by, splitType="percent", splits=splits, status=status)

    def test_validation(self):
        cases = [
            (pct(("a", 50), ("b", 49.99)), 400),               # 99.99
            (pct(("a", 50), ("b", 50.01)), 400),               # 100.01
            (pct(("a", 0), ("b", 0), ("c", 0)), 400),          # all zero
            (pct(("a", 60), ("a", 40)), 400),                  # duplicate
            (pct(("a", 60), ("x", 40)), 400),                  # Xavier isn't in the group
            (pct(("a", 150), ("b", -50)), 422),                # > 100 / negative
            ([{"userId": "a", "amount": 100}], 400),           # amounts instead of percentages
            ([], 400),
        ]
        for splits, status in cases:
            r = self.c.post(f"/api/groups/{self.gid}/expenses", headers=ALICE, json={
                "description": "x", "amount": 100, "paidBy": "a", "splitType": "percent", "splits": splits})
            self.assertEqual(r.status_code, status, (splits, r.text))
        r = self.c.post(f"/api/groups/{self.gid}/expenses", headers=ALICE, json={
            "description": "x", "amount": 100, "paidBy": "a", "splitType": "percent", "splits": pct(("a", 50), ("b", 49))})
        self.assertEqual(r.json()["detail"], "Percentages must add up to 100%")
        for raw in ("NaN", "Infinity"):
            r = self.c.post(f"/api/groups/{self.gid}/expenses", headers={**ALICE, "Content-Type": "application/json"},
                            content='{"description": "x", "amount": 100, "paidBy": "a", "splitType": "percent", '
                                    f'"splits": [{{"userId": "a", "percent": {raw}}}]}}')
            self.assertEqual(r.status_code, 422, raw)
        self.assertEqual(self.c.get(f"/api/groups/{self.gid}", headers=ALICE).json()["expenses"], [])
        # custom (amounts) still needs amounts
        self.expense(splitType="custom", splits=pct(("a", 50), ("b", 50)), status=400)

    def test_stored_returned_and_balanced(self):
        e = self.add(pct(("a", 50), ("b", 30), ("c", 20)))
        self.assertEqual(e["splitType"], "percent")
        self.assertEqual([(s["userId"], s["amount"], s["percent"]) for s in e["splits"]],
                         [("a", 50.0, 50.0), ("b", 30.0, 30.0), ("c", 20.0, 20.0)])
        g = self.c.get(f"/api/groups/{self.gid}", headers=ALICE).json()
        exp = g["expenses"][0]
        self.assertEqual(exp["splitType"], "percent")
        self.assertEqual([(s["name"], s["amount"], s["percent"]) for s in exp["splits"]],
                         [("Ann", 50.0, 50.0), ("Ben", 30.0, 30.0), ("Cat", 20.0, 20.0)])
        self.assertEqual(self.net(), {"a": 50.0, "b": -30.0, "c": -20.0})
        d = self.c.get("/api/dashboard", headers=ALICE).json()
        self.assertEqual((d["expensesCount"], d["month"]["total"]), (1, 100))
        self.assertEqual({n["userId"]: n["amount"] for n in d["overallNet"]}, {"a": 50.0, "b": -30.0, "c": -20.0})
        t = self.c.get("/api/transactions?period=month", headers=ALICE).json()
        self.assertEqual((t["count"], t["total"]), (1, 100))
        self.assertIn("($100.00)", self.c.get("/api/events", headers=ALICE).json()[0]["message"])
        with main.get_conn() as conn:
            states = main.balance_sensor_states(conn)
        self.assertEqual({eid: st for eid, st, _ in states},
                         {"sensor.splitpot_balance_ann": 50.0, "sensor.splitpot_balance_ben": -30.0,
                          "sensor.splitpot_balance_cat": -20.0})
        # equal / custom rows carry no percent
        self.expense(amount=30)
        g = self.c.get(f"/api/groups/{self.gid}", headers=ALICE).json()
        self.assertNotIn("percent", g["expenses"][0]["splits"][0])

    def test_odd_amount_splits_to_the_cent(self):
        e = self.add(pct(("a", 33.33), ("b", 33.33), ("c", 33.34)), amount=10)
        self.assertEqual([s["amount"] for s in e["splits"]], [3.33, 3.33, 3.34])
        self.assertEqual(self.net(), {"a": 6.67, "b": -3.33, "c": -3.34})

    def test_edit_round_trip_between_modes(self):
        e = self.add(pct(("a", 60), ("b", 40)), amount=50)

        def edit(**body):
            r = self.c.put(f"/api/expenses/{e['id']}", headers=ALICE,
                           json={"description": "Dinner", "amount": 50, "paidBy": "a", **body})
            self.assertEqual(r.status_code, 200, r.text)
            return r.json()["expenses"][0]

        exp = edit(splitType="percent", splits=pct(("a", 25), ("b", 25), ("c", 50)), amount=80)
        self.assertEqual((exp["splitType"], exp["amount"]), ("percent", 80))
        self.assertEqual([(s["userId"], s["amount"], s["percent"]) for s in exp["splits"]],
                         [("a", 20.0, 25.0), ("b", 20.0, 25.0), ("c", 40.0, 50.0)])
        exp = edit(splitType="custom", splits=[{"userId": "a", "amount": 10}, {"userId": "b", "amount": 40}])
        self.assertEqual(exp["splitType"], "custom")
        self.assertTrue(all("percent" not in s for s in exp["splits"]))
        exp = edit(splitType="equal", participants=["a", "b"])
        self.assertEqual((exp["splitType"], [s["amount"] for s in exp["splits"]]), ("equal", [25.0, 25.0]))
        exp = edit(splitType="percent", splits=pct(("c", 100)))
        self.assertEqual([(s["userId"], s["amount"], s["percent"]) for s in exp["splits"]], [("c", 50.0, 100.0)])
        self.assertEqual(self.net(), {"a": 50.0, "b": 0.0, "c": -50.0})
        # a bad edit leaves the stored split alone
        r = self.c.put(f"/api/expenses/{e['id']}", headers=ALICE, json={
            "description": "Dinner", "amount": 50, "paidBy": "a", "splitType": "percent", "splits": pct(("a", 10))})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.net(), {"a": 50.0, "b": 0.0, "c": -50.0})

    def test_older_database_gets_the_percent_column(self):
        self.expense(amount=30)
        with main.get_conn() as conn:
            conn.execute("ALTER TABLE expense_splits DROP COLUMN percent")
            conn.commit()
        main.init_db()
        with main.get_conn() as conn:
            cols = [r["name"] for r in conn.execute("PRAGMA table_info(expense_splits)")]
        self.assertIn("percent", cols)
        self.assertEqual(len(self.c.get(f"/api/groups/{self.gid}", headers=ALICE).json()["expenses"]), 1)
        self.add(pct(("a", 100)))


if __name__ == "__main__":
    unittest.main()
