"""Fixes from the 2026-10 review: equal splits in whole cents, custom splits that add up exactly, a unique
balance sensor per person, a bounded /transactions offset and a ledger ordered by real time. Invented data.

Run with the rest: python3 -m unittest discover -s tests"""
import _env  # noqa: F401  (must be first)

import random
import unittest

from fastapi import HTTPException

from app import main
from test_app import ALICE, Base


class EqualShares(unittest.TestCase):
    def shares(self, amount, n):
        payload = main.ExpenseCreate(description="x", amount=amount, paidBy="u0",
                                     participants=[f"u{i}" for i in range(n)])
        splits, kind = main.build_splits([f"u{i}" for i in range(n)], payload)
        self.assertEqual(kind, "equal")
        return [s["amount"] for s in splits]

    def test_tiny_amounts_never_go_negative(self):
        self.assertEqual(self.shares(0.07, 9), [0.01] * 7 + [0.0] * 2)        # was one at -0.01
        self.assertEqual(self.shares(0.01, 3), [0.01, 0.0, 0.0])

    def test_remainder_is_spread_not_dumped_on_the_last_person(self):
        self.assertEqual(self.shares(100, 6), [16.67] * 4 + [16.66] * 2)       # was 16.67×5 + 16.65
        self.assertEqual(self.shares(10, 3), [3.34, 3.33, 3.33])
        self.assertEqual(self.shares(30, 3), [10.0, 10.0, 10.0])

    def test_always_exact_and_within_a_cent(self):
        rnd = random.Random(99)
        for _ in range(3000):
            n = rnd.randint(1, 12)
            amount = rnd.randint(1, 100_000_000) / 100
            cents = [round(s * 100) for s in self.shares(amount, n)]
            self.assertEqual(sum(cents), round(amount * 100), (amount, n))
            self.assertLessEqual(max(cents) - min(cents), 1)
            self.assertGreaterEqual(min(cents), 0)


class SplitsApi(Base):
    def test_equal_split_nets_add_up(self):
        self.expense(amount=0.07)
        net = self.net()
        self.assertAlmostEqual(sum(net.values()), 0, places=9)
        self.assertEqual(round(net["a"] * 100), 4)       # paid 0.07, own share 0.03 (0.03/0.02/0.02)

    def test_custom_split_must_add_up_exactly(self):
        r = self.expense(amount=30, splitType="custom", status=400,
                         splits=[{"userId": "a", "amount": 10}, {"userId": "b", "amount": 10},
                                 {"userId": "c", "amount": 9.99}])
        self.assertIn("exactly 30.00", r["detail"])
        self.assertIn("0.01 short", r["detail"])
        r = self.expense(amount=30, splitType="custom", status=400,
                         splits=[{"userId": "a", "amount": 10.01}, {"userId": "b", "amount": 20.01}])
        self.assertIn("0.02 over", r["detail"])
        self.expense(amount=30, splitType="custom",
                     splits=[{"userId": "a", "amount": 10.1}, {"userId": "b", "amount": 19.9}])
        self.assertAlmostEqual(sum(self.net().values()), 0, places=9)

    def test_transactions_offset_is_bounded(self):
        for bad in (10**9, -(10**9), 1201, -1201):
            r = self.c.get(f"/api/transactions?period=week&offset={bad}", headers=ALICE)
            self.assertEqual(r.status_code, 422, (bad, r.text))
            r = self.c.get(f"/api/transactions?period=month&offset={bad}", headers=ALICE)
            self.assertEqual(r.status_code, 422, (bad, r.text))
        for ok in (-1200, 0, 1200):
            for period in ("week", "month"):
                self.assertEqual(self.c.get(f"/api/transactions?period={period}&offset={ok}",
                                            headers=ALICE).status_code, 200)


class SensorSlugs(Base):
    def add_user(self, uid, name, created):
        with main.get_conn() as conn:
            conn.execute("INSERT INTO users (id, name, created_at) VALUES (?, ?, ?)", (uid, name, created))
            conn.commit()

    def test_unique_names_keep_their_sensor_ids(self):
        with main.get_conn() as conn:
            slugs = main.sensor_slugs(conn)
        self.assertEqual((slugs["a"], slugs["b"], slugs["c"], slugs["x"]), ("ann", "ben", "cat", "xavier"))

    def test_colliding_and_non_latin_names_get_a_suffix(self):
        self.add_user("U-Asha-1", "Asha K", "2026-01-01T00:00:00+00:00")
        self.add_user("u_asha2", "Asha-K", "2026-02-01T00:00:00+00:00")
        self.add_user("p1", "আশা", "2026-01-01T00:00:00+00:00")
        self.add_user("p2", "मीरा", "2026-02-01T00:00:00+00:00")
        self.add_user("p3", "李", "2026-03-01T00:00:00+00:00")
        with main.get_conn() as conn:
            slugs = main.sensor_slugs(conn)
        self.assertEqual(slugs["U-Asha-1"], "asha_k")         # the first one keeps it
        self.assertEqual(slugs["u_asha2"], "asha_k_uasha2")
        self.assertEqual(slugs["p1"], "user")
        self.assertEqual((slugs["p2"], slugs["p3"]), ("user_p2", "user_p3"))
        self.assertEqual(len(set(slugs.values())), len(slugs))

    def test_sensor_states_are_one_per_person(self):
        self.add_user("d", "Asha K", "2026-01-01T00:00:00+00:00")
        self.add_user("e", "Asha-K", "2026-02-01T00:00:00+00:00")
        r = self.c.post("/api/groups", json={"name": "Trip", "memberIds": ["a", "d", "e"]}, headers=ALICE)
        gid = r.json()["id"]
        self.c.post(f"/api/groups/{gid}/expenses", headers=ALICE,
                    json={"description": "Taxi", "amount": 30, "paidBy": "d"})
        with main.get_conn() as conn:
            states = main.balance_sensor_states(conn)
        ids = [s[0] for s in states]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertIn("sensor.splitpot_balance_asha_k", ids)
        self.assertIn("sensor.splitpot_balance_asha_k_e", ids)


class LedgerOrder(Base):
    def tearDown(self):
        main.set_timezone("UTC")

    def test_picked_days_and_timestamps_sort_by_real_time(self):
        main.set_timezone("Asia/Kolkata")              # UTC+5:30
        with main.get_conn() as conn:
            rows = [("e1", "2026-03-10T22:30:00+00:00"),   # 11 March 04:00 local
                    ("e2", "2026-03-11"),                  # picked: 11 March (local midnight = 10 March 18:30 UTC)
                    ("e3", "2026-03-10T12:00:00+00:00")]   # 10 March 17:30 local
            for eid, date in rows:
                conn.execute("INSERT INTO expenses (id, group_id, description, amount, paid_by, split_type, date) "
                             "VALUES (?, ?, 'x', 1, 'a', 'equal', ?)", (eid, self.gid, date))
                conn.execute("INSERT INTO expense_splits (expense_id, user_id, amount) VALUES (?, 'a', 1)", (eid,))
            conn.commit()
            main.fill_sort_keys(conn)           # rows written without a key (as before the upgrade) get theirs
            entries, _ = main.ledger_page(conn, self.gid)
            self.assertEqual([e["id"] for e in entries], ["e1", "e2", "e3"])   # strings alone: e2, e1, e3
            first, cursor = main.ledger_page(conn, self.gid, 1)
            second, cursor = main.ledger_page(conn, self.gid, 1, cursor)
            third, cursor = main.ledger_page(conn, self.gid, 1, cursor)
            self.assertEqual([first[0]["id"], second[0]["id"], third[0]["id"]], ["e1", "e2", "e3"])
            self.assertIsNone(cursor)

    def test_sort_key_survives_odd_values(self):
        self.assertEqual(main.date_sort_key("not a date"), "not a date")
        self.assertLess(main.date_sort_key("2026-03-10"), main.date_sort_key("2026-03-10T00:00:01+00:00"))


if __name__ == "__main__":
    unittest.main()
