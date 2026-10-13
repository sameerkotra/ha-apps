"""The 13 Oct 2026 review's performance fixes give the same answers as before (SPP2–SPP6, SPP9): each rewritten
query is compared with the old way of working it out, on a seeded household; plus query counts, the stored sort key's
migration and the database settings. Every person and amount here is invented."""
import _env  # noqa: F401  (must be first)

import contextlib
import os
import random
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

from app import main
from test_app import ALICE, Base

PEOPLE = [("p1", "Asha"), ("p2", "Ravi"), ("p3", "Mia"), ("p4", "Zed"), ("p5", "Noor")]


@contextlib.contextmanager
def counting():
    """Every SQL statement run on connections opened meanwhile."""
    stmts = []
    orig = main._connect

    def connect():
        c = orig()
        c.set_trace_callback(stmts.append)
        return c
    main._connect = connect
    try:
        yield stmts
    finally:
        main._connect = orig


def seed(conn, n=400, seed_value=7):
    """Three groups, five people, `n` entries: picked days and timestamps (many on the same day), equal, custom and
    percent splits, and settle-up payments."""
    rnd = random.Random(seed_value)
    for uid, name in PEOPLE:
        conn.execute("INSERT INTO users (id, name, created_at) VALUES (?, ?, ?)", (uid, name, main.now_iso()))
    groups = []
    for gi, members in enumerate((["p1", "p2", "p3"], ["p2", "p3", "p4", "p5"], ["p1", "p5"])):
        gid = f"g{gi}"
        groups.append((gid, members))
        conn.execute("INSERT INTO groups (id, name, created_at) VALUES (?, ?, ?)", (gid, f"G{gi}", f"2026-01-0{gi + 1}"))
        for pos, uid in enumerate(members):
            conn.execute("INSERT INTO group_members (group_id, user_id, position) VALUES (?, ?, ?)", (gid, uid, pos))
    now = datetime.now(timezone.utc)
    for i in range(n):
        gid, members = rnd.choice(groups)
        if rnd.random() < 0.5:
            date = (now - timedelta(days=rnd.randint(0, 80))).date().isoformat()          # a picked day
        else:
            date = (now - timedelta(minutes=rnd.randint(0, 80 * 24 * 60))).isoformat()    # added "today"
        amount = rnd.randint(1, 50000) / 100
        payer = rnd.choice(members)
        eid = f"e{i:04d}"
        kind = rnd.choice(["equal", "custom", "percent", "payment"])
        if kind == "payment":
            to = rnd.choice([m for m in members if m != payer])
            main.insert_entry(conn, eid, gid, "pay", amount, payer, "payment", date)
            main.write_splits(conn, eid, [{"userId": to, "amount": amount}])
            continue
        people = rnd.sample(members, rnd.randint(1, len(members)))
        if kind == "equal":
            splits = main.equal_shares(amount, people)
        elif kind == "percent":
            pct = [10000 // len(people)] * len(people)
            pct[0] += 10000 - sum(pct)
            splits = main.percent_shares(amount, [(u, p / 100) for u, p in zip(people, pct)])
        else:
            cents = round(amount * 100)
            cut = sorted(rnd.randint(0, cents) for _ in people[1:])
            parts = [b - a for a, b in zip([0] + cut, cut + [cents])]
            splits = [{"userId": u, "amount": c / 100} for u, c in zip(people, parts) if c > 0] or \
                [{"userId": people[0], "amount": amount}]
        main.insert_entry(conn, eid, gid, f"Item {i}", amount, payer, kind, date)
        main.write_splits(conn, eid, splits)
    conn.commit()
    return groups


# ---- the old ways (2.6.1), for comparison ----
def old_ledger_ids(conn, gid):
    rows = conn.execute("SELECT id, date FROM expenses WHERE group_id = ?", (gid,)).fetchall()
    return [r["id"] for r in sorted(rows, key=lambda r: (main.date_sort_key(r["date"]), r["id"]), reverse=True)]


def old_running_balances(conn, gid):
    members = main.group_member_ids(conn, gid)
    cents = {m: 0 for m in members}
    out = {}
    for eid in reversed(old_ledger_ids(conn, gid)):
        e = conn.execute("SELECT paid_by, amount FROM expenses WHERE id = ?", (eid,)).fetchone()
        cents[e["paid_by"]] = cents.get(e["paid_by"], 0) + round(e["amount"] * 100)
        for s in main.fetch_expense_splits(conn, eid):
            cents[s["userId"]] = cents.get(s["userId"], 0) - round(s["amount"] * 100)
        out[eid] = [(u, round(v / 100, 2)) for u, v in cents.items()]
    return out


def old_overall_net(conn):
    overall, names = {}, {}
    for g in conn.execute("SELECT id FROM groups").fetchall():
        member_ids = main.group_member_ids(conn, g["id"])
        expenses = [{"paidBy": e["paid_by"], "amount": e["amount"], "splits": main.fetch_expense_splits(conn, e["id"])}
                    for e in conn.execute("SELECT id, paid_by, amount FROM expenses WHERE group_id = ?", (g["id"],))]
        net, _ = main.compute_balances(member_ids, expenses)
        for uid, amt in net.items():
            overall[uid] = overall.get(uid, 0.0) + amt
            names.setdefault(uid, main.user_name(conn, uid))
    return {uid: (names[uid], round(amt, 2)) for uid, amt in overall.items()}


def old_period(conn, start, end):
    out = []
    for r in conn.execute("SELECT e.id, e.amount, e.date, e.paid_by, g.name AS group_name FROM expenses e "
                          "JOIN groups g ON g.id = e.group_id WHERE e.split_type != 'payment'"):
        if start <= main.parse_date(r["date"]) < end:
            out.append((r["id"], r["amount"], r["paid_by"], r["group_name"]))
    return out


class Seeded(unittest.TestCase):
    def setUp(self):
        for ext in ("", "-wal", "-shm"):
            p = str(main.DB_FILE) + ext
            if os.path.exists(p):
                os.remove(p)
        main.init_db()
        main.set_timezone("America/New_York")      # picked days then sit between other days' timestamps
        self.addCleanup(main.set_timezone, "UTC")
        main.WRITES.clear()
        with main.get_conn() as conn:
            self.groups = seed(conn)

    def test_ledger_pages_and_running_balances_match_the_old_order(self):
        with main.get_conn() as conn:
            for gid, _ in self.groups:
                want = old_ledger_ids(conn, gid)
                balances = old_running_balances(conn, gid)
                for limit in (1, 7, 20, 500):
                    ids, seen, cursor = [], {}, None
                    while True:
                        page, cursor = main.ledger_page(conn, gid, limit, cursor, balances=True)
                        ids += [e["id"] for e in page]
                        seen.update({e["id"]: [(b["userId"], b["amount"]) for b in e["balancesAfter"]] for e in page})
                        if not cursor:
                            break
                    self.assertEqual(ids, want, (gid, limit))
                    self.assertEqual(seen, balances, (gid, limit))
                whole, _ = main.ledger_page(conn, gid)
                self.assertEqual([e["id"] for e in whole], want)
                self.assertNotIn("balancesAfter", whole[0])     # only when asked for

    def test_overall_net_matches(self):
        with main.get_conn() as conn:
            new = main.compute_overall_net(conn)
            self.assertEqual({x["userId"]: (x["name"], x["amount"]) for x in new}, old_overall_net(conn))
        self.assertEqual([x["amount"] for x in new], sorted((x["amount"] for x in new), reverse=True))
        self.assertEqual(round(sum(x["amount"] for x in new) * 100), 0)

    def test_weeks_and_months_match(self):
        with main.get_conn() as conn:
            for period, offsets in (("week", range(-13, 1)), ("month", range(-3, 1))):
                for off in offsets:
                    start, end, _ = main.week_range(off) if period == "week" else main.month_range(off)
                    old = old_period(conn, start, end)
                    new = main.period_expenses(conn, start, end)
                    self.assertEqual(sorted((e["id"], e["amount"], e["paidBy"], e["groupName"]) for e in new),
                                     sorted(old), (period, off))
                    keys = [(main.date_sort_key(e["date"]), e["id"]) for e in new]
                    self.assertEqual(keys, sorted(keys, reverse=True))
                    self.assertEqual(main.cents_total(e["amount"] for e in new), round(sum(x[1] for x in old), 2))
            d = main.build_dashboard_data(conn)
            for key, (start, end, _) in (("week", main.week_range(0)), ("month", main.month_range(0))):
                old = old_period(conn, start, end)
                self.assertEqual(d[key], {"total": round(sum(x[1] for x in old), 2), "count": len(old)})

    def test_a_changed_time_zone_reorders_picked_days(self):
        with main.get_conn() as conn:
            before = old_ledger_ids(conn, "g0")
        main.set_timezone("Asia/Tokyo")                # keys of picked days are worked out again
        with main.get_conn() as conn:
            now = old_ledger_ids(conn, "g0")
            self.assertEqual([e["id"] for e in main.ledger_page(conn, "g0")[0]], now)
            stale = conn.execute("SELECT COUNT(*) FROM expenses WHERE sort_key IS NULL").fetchone()[0]
        self.assertEqual(stale, 0)
        self.assertNotEqual(before, now)               # the seeded data does have picked days next to timestamps

    def test_query_counts_dont_grow_with_the_page(self):
        def run(limit):
            with counting() as stmts:
                with main.get_conn() as conn:
                    main.ledger_page(conn, "g1", limit)
            return len([s for s in stmts if s.lstrip().upper().startswith("SELECT")])
        self.assertEqual(run(5), run(200))
        self.assertLessEqual(run(200), 3)

    def test_group_list_and_group_page_query_counts(self):
        def run(path):
            with counting() as stmts:
                r = self.client.get(path, headers=ALICE)
                self.assertEqual(r.status_code, 200)
            return len([s for s in stmts if s.lstrip().upper().startswith("SELECT")])
        from common_tests.ingress import ingress_client
        with ingress_client(main.app) as self.client:
            groups = run("/api/groups")
            self.assertLessEqual(groups, 6)            # not three per group
            with main.get_conn() as conn:
                for i in range(10):
                    conn.execute("INSERT INTO groups (id, name, created_at) VALUES (?, ?, ?)", (f"x{i}", "X", "2026"))
                    conn.execute("INSERT INTO group_members (group_id, user_id, position) VALUES (?, 'p1', 0)",
                                 (f"x{i}",))
                conn.commit()
            self.assertEqual(run("/api/groups"), groups)
            self.assertLessEqual(run("/api/groups/g1?limit=50"), 12)


class Migration(unittest.TestCase):
    def test_older_database_gets_sort_keys_and_loses_duplicate_indexes(self):
        for ext in ("", "-wal", "-shm"):
            p = str(main.DB_FILE) + ext
            if os.path.exists(p):
                os.remove(p)
        main.init_db()
        with main.get_conn() as conn:                  # make it look like 2.6.1's
            conn.execute("DROP INDEX idx_expenses_group_sort")
            conn.execute("DROP INDEX idx_expenses_sort")
            conn.execute("ALTER TABLE expenses DROP COLUMN sort_key")
            conn.execute("CREATE INDEX idx_expense_splits_expense ON expense_splits(expense_id)")
            conn.execute("CREATE INDEX idx_group_members_group ON group_members(group_id)")
            conn.execute("CREATE INDEX idx_expenses_group_date ON expenses(group_id, date, id)")
            conn.execute("INSERT INTO users (id, name, created_at) VALUES ('a', 'Ann', 'x')")
            conn.execute("INSERT INTO groups (id, name, created_at) VALUES ('g', 'G', 'x')")
            for eid, date in (("e1", "2026-03-11"), ("e2", "2026-03-10T22:30:00+00:00")):
                conn.execute("INSERT INTO expenses (id, group_id, description, amount, paid_by, split_type, date) "
                             "VALUES (?, 'g', 'x', 1, 'a', 'equal', ?)", (eid, date))
            conn.commit()
        main.init_db()
        with main.get_conn() as conn:
            keys = dict(conn.execute("SELECT id, sort_key FROM expenses").fetchall())
            names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
            sync = conn.execute("PRAGMA synchronous").fetchone()[0]
            journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
        self.assertEqual(keys, {"e1": main.date_sort_key("2026-03-11"), "e2": main.date_sort_key("2026-03-10T22:30:00+00:00")})
        self.assertFalse(names & set(main.OLD_INDEXES))
        self.assertTrue({"idx_expenses_group_sort", "idx_expenses_sort"} <= names)
        self.assertEqual((sync, journal), (1, "wal"))  # synchronous = NORMAL under WAL


class UsersList(Base):
    def test_people_are_written_only_when_home_assistant_was_read_again(self):
        orig = main.fetch_ha_persons
        main.forget_persons()
        main.fetch_ha_persons = lambda: [{"entity_id": "person.meera", "name": "Meera", "user_id": "u_meera"}]
        self.addCleanup(lambda: (setattr(main, "fetch_ha_persons", orig), main.forget_persons()))

        def writes():
            with counting() as stmts:
                self.assertEqual(self.c.get("/api/users", headers=ALICE).status_code, 200)
            return [s for s in stmts if s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))]
        self.assertTrue(writes())                      # first read: Meera is added
        self.assertEqual(writes(), [])                 # same cached list: no write (and no write lock)
        self.assertIn("Meera", [u["name"] for u in self.c.get("/api/users", headers=ALICE).json()])
        main.forget_persons()                          # e.g. after a restore
        self.assertEqual(writes(), [])                 # read again, nothing changed: UPDATEs only when different


class ActivityLog(Base):
    def test_old_entries_are_pruned_by_the_setting(self):
        old = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
        with main.get_conn() as conn:
            conn.execute("INSERT INTO events (id, type, message, created_at) VALUES ('old', 'x', 'old one', ?)", (old,))
            conn.commit()
        main.update_settings({"keep_activity_days": 0}, "test")
        self.assertEqual(main.prune_events(), 0)       # 0 = keep everything
        main.update_settings({"keep_activity_days": 365}, "test")
        self.assertEqual(main.prune_events(), 1)
        self.assertTrue(self.c.get("/api/events", headers=ALICE).json())     # newer ones stay


if __name__ == "__main__":
    unittest.main()
