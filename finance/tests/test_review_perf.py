"""Performance fixes from the 13 Oct 2026 review must not change results: each rewritten loop or query is
compared with the version it replaced (copied here) on seeded data, and timed at size where that was the
point (FP1 transfer matching, FP3 recurring detection, FP4 rule re-application, FSP8 toll devices)."""
import random
import sqlite3
import time
from datetime import date, timedelta

import pytest


# ---- FP1: transfer auto-link --------------------------------------------------------------------

def _old_candidates_for(outflow, inflows):
    """matching._candidates_for as it was (1.4.1)."""
    from app.matching import DATE_WINDOW_DAYS, _parse_date
    outflow_date = _parse_date(outflow["date"])
    if outflow_date is None:
        return []
    target_amount = abs(outflow["amount"])
    matches = []
    for i in inflows:
        if i["account_id"] == outflow["account_id"]:
            continue
        if abs(abs(i["amount"]) - target_amount) > 0.01:
            continue
        inflow_date = _parse_date(i["date"])
        if inflow_date is None or abs((inflow_date - outflow_date).days) > DATE_WINDOW_DAYS:
            continue
        matches.append(i)
    return matches


def _old_auto_link(conn, user_id):
    """matching.auto_link_unambiguous as it was: refetch everything and restart after every link."""
    from app.matching import find_transfer_candidates, link_transfer
    linked = 0
    while True:
        outflows, inflows = find_transfer_candidates(conn, user_id)
        if not outflows or not inflows:
            break
        made_a_link = False
        for outflow in outflows:
            matches = _old_candidates_for(outflow, inflows)
            if len(matches) != 1:
                continue
            inflow = matches[0]
            if any(o["id"] != outflow["id"] and any(m["id"] == inflow["id"] for m in _old_candidates_for(o, inflows))
                   for o in outflows):
                continue
            link_transfer(conn, outflow["id"], inflow["id"])
            linked += 1
            made_a_link = True
            break
        if not made_a_link:
            break
    return linked


def _seed_transfers(path, n, seed):
    """n outflows and about n inflows over a few accounts, with many near and exact collisions. Amounts
    are quarters, so the old float comparison and the new cents one agree on every pair."""
    rng = random.Random(seed)
    conn = sqlite3.connect(path)
    accounts = []
    for name, kind in (("Chk A", "checking"), ("Chk B", "checking"), ("Sav", "savings"), ("Card 1", "credit_card"),
                       ("Card 2", "credit_card")):
        accounts.append((conn.execute("INSERT INTO accounts (user_id, name, type) VALUES ('tester', ?, ?)",
                                      (name, kind)).lastrowid, kind))
    bank = [a for a in accounts if a[1] != "credit_card"]
    start = date(2025, 1, 1)
    rows = []
    for _ in range(n):
        amount = rng.choice([25, 50, 75, 100, 125]) + rng.randrange(0, 40) * 0.25
        day = start + timedelta(days=rng.randrange(0, 365))
        src = rng.choice(bank)
        rows.append((src[0], day.isoformat(), -amount))
        if rng.random() < 0.8:                                  # its other side, often a day or two later
            dst = rng.choice([a for a in accounts if a[0] != src[0]])
            other_day = day + timedelta(days=rng.choice([0, 0, 1, 2, 3, 6]))
            rows.append((dst[0], other_day.isoformat(), -amount if dst[1] == "credit_card" else amount))
    for _ in range(n // 5):                                     # plain spending and stray deposits
        acct = rng.choice(accounts)
        rows.append((acct[0], (start + timedelta(days=rng.randrange(0, 365))).isoformat(),
                     rng.choice([-1, 1]) * (rng.randrange(1, 400) * 0.25)))
    conn.executemany("INSERT INTO transactions (user_id, account_id, date, amount, description) "
                     "VALUES ('tester', ?, ?, ?, 'X')", rows)
    conn.execute("UPDATE transactions SET review_status = 'pending_review' WHERE id % 17 = 0")
    conn.execute("UPDATE transactions SET is_excluded = 1 WHERE id % 23 = 0")
    conn.commit()
    conn.close()


def _pairs(path):
    conn = sqlite3.connect(path)
    try:
        return sorted(conn.execute("SELECT id, transfer_pair_id, txn_type FROM transactions").fetchall())
    finally:
        conn.close()


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_auto_link_links_exactly_the_same_pairs_as_before(env, tmp_path, seed):
    import shutil
    from app.matching import auto_link_unambiguous
    _seed_transfers(env.db_path, 300, seed)
    copy = str(tmp_path / "copy.db")
    with env.db() as c:
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    shutil.copyfile(env.db_path, copy)

    with env.db() as c:
        new_count = auto_link_unambiguous(c, "tester")
    c2 = sqlite3.connect(copy)
    c2.row_factory = sqlite3.Row
    with c2:
        old_count = _old_auto_link(c2, "tester")
    c2.close()
    assert new_count == old_count and new_count > 20
    assert _pairs(env.db_path) == _pairs(copy)


def test_candidates_come_back_in_the_same_order(env):
    from app.matching import InflowIndex, _candidates_for, find_transfer_candidates
    _seed_transfers(env.db_path, 200, 9)
    with env.db() as c:
        outflows, inflows = find_transfer_candidates(c, "tester")
    index = InflowIndex(inflows)
    for o in outflows:
        old = [r["id"] for r in _old_candidates_for(o, inflows)]
        assert [r["id"] for r in _candidates_for(o, inflows)] == old
        assert [r["id"] for r in index.candidates(o)] == old


def test_a_cent_apart_always_matches_now(env):
    """0.07 - 0.06 is 0.010000000000000009 in floating point: the old check missed some one-cent pairs."""
    from app.matching import auto_link_unambiguous
    chk = env.account("Checking")
    sav = env.account("Savings", "savings")
    out = env.txn(chk, "2026-08-01", -0.07, "XFER")
    inn = env.txn(sav, "2026-08-01", 0.06, "XFER")
    with env.db() as c:
        assert auto_link_unambiguous(c, "tester") == 1
        assert c.execute("SELECT transfer_pair_id FROM transactions WHERE id = ?", (out,)).fetchone()[0] == inn


def test_auto_link_at_size_is_quick(env):
    from app.matching import auto_link_unambiguous
    _seed_transfers(env.db_path, 4000, 5)
    started = time.perf_counter()
    with env.db() as c:
        linked = auto_link_unambiguous(c, "tester")
    assert linked > 500
    assert time.perf_counter() - started < 5.0          # the old sweep took minutes at this size


def test_opening_the_dashboard_or_transfers_writes_nothing(env):
    """The sweep runs when the data changes, not on a page load (a GET never writes)."""
    chk = env.account("Checking")
    sav = env.account("Savings", "savings")
    env.txn(chk, "2026-08-01", -100.0, "XFER")
    env.txn(sav, "2026-08-02", 100.0, "XFER")
    for url in ("dashboard?period=all", "transfers?start_date=&end_date="):
        env.get(url)
        with env.db() as c:
            assert c.execute("SELECT COUNT(*) FROM transactions WHERE transfer_pair_id IS NOT NULL").fetchone()[0] == 0
    assert "Couldn" not in env.get("transfers?start_date=&end_date=").text
    r = env.post("transfer-run-sweep", data={"next": "dashboard?period=all"})
    assert r.headers["location"].startswith("dashboard?period=all&") and "matched=1" in r.headers["location"]


def test_changes_link_what_became_unambiguous(env):
    """A manual transaction, a CSV import, a review and an include each link a pair they complete."""
    chk = env.account("Checking")
    sav = env.account("Savings", "savings")
    out = env.txn(chk, "2026-08-01", -100.0, "XFER OUT")
    r = env.post("transaction-manual", data={"account_id": str(sav), "date": "2026-08-02", "amount": "100",
                                             "description": "XFER IN"})
    assert r.status_code == 303

    def partner(tid):
        with env.db() as c:
            return c.execute("SELECT transfer_pair_id FROM transactions WHERE id = ?", (tid,)).fetchone()[0]

    assert partner(out) is not None
    out2 = env.txn(chk, "2026-08-10", -42.0, "XFER OUT 2")
    csv = "Date,Description,Amount\n2026-08-11,XFER IN 2,42.00\n"
    r = env.post("transactions-import-csv", data={"default_account_id": str(sav)},
                 files={"files": ("a.csv", csv.encode(), "text/csv")}, headers={"accept": "application/json"})
    assert r.json()["results"][0]["imported"] == 1
    assert partner(out2) is not None
    out3 = env.txn(chk, "2026-08-20", -7.0, "XFER OUT 3")
    flagged = env.txn(sav, "2026-08-20", 7.0, "XFER IN 3", review_status="pending_review")
    env.post("transaction-resolve", data={"id": str(flagged), "date": "2026-08-20", "amount": "7",
                                          "description": "XFER IN 3"})
    assert partner(out3) == flagged
    out4 = env.txn(chk, "2026-08-25", -9.0, "XFER OUT 4")
    excluded = env.txn(sav, "2026-08-25", 9.0, "XFER IN 4", is_excluded=1)
    env.post("transactions-bulk", data={"ids": str(excluded), "action": "unexclude"})
    assert partner(out4) == excluded


# ---- FP3: recurring detection ---------------------------------------------------------------------

def _old_family_roots(keys):
    from app.recurring import _FAMILY_MIN_CHARS, _FAMILY_MIN_WORDS
    roots, result = [], {}
    for key in sorted(keys, key=len):
        root = next((r for r in roots if key.startswith(r + " ")
                     and (len(r.split()) >= _FAMILY_MIN_WORDS or len(r) >= _FAMILY_MIN_CHARS)), None)
        if root is None:
            roots.append(key)
            root = key
        result[key] = root
    return result


def _old_cluster_by_amount(txns):
    from collections import defaultdict
    from app.recurring import _tolerance
    exact = defaultdict(int)
    for t in txns:
        exact[round(abs(t["amount"]), 2)] += 1
    anchors = sorted((a for a, n in exact.items() if n >= 2), key=lambda a: (-exact[a], a))
    clusters = {a: [] for a in anchors}
    leftovers = []
    for t in txns:
        amount = abs(t["amount"])
        near = [a for a in anchors if abs(amount - a) <= _tolerance(a)]
        if near:
            clusters[min(near, key=lambda a: abs(amount - a))].append(t)
        else:
            leftovers.append(t)
    variable = []
    for t in sorted(leftovers, key=lambda t: abs(t["amount"])):
        if variable and abs(t["amount"]) - variable[-1][0] <= _tolerance(variable[-1][0]):
            variable[-1][1].append(t)
        else:
            variable.append((abs(t["amount"]), [t]))
    return [(a, ts) for a, ts in clusters.items() if ts] + variable


def _old_parse_date(s):
    from datetime import datetime
    from app.datenorm import normalize_date
    normalized = normalize_date(s)
    return datetime.strptime(normalized, "%Y-%m-%d").date() if normalized else None


def _seed_recurring(env, n, seed):
    rng = random.Random(seed)
    accounts = [env.account("Chk"), env.account("Card", "credit_card")]
    merchants = ["NETFLIX COM", "DIRECT PAYMENT ACME PREMIUM", "DIRECT PAYMENT ACME PREMIUM INSURANCE",
                 "XCEL ENERGY ONLINE PAYMENT", "SPOTIFY USA", "CORNER DELI", "AMAZON MKTP", "GYM CLUB MONTHLY FEE",
                 "CITY WATER DEPT AUTOPAY BILL", "PARKING", "DIRECT PAYMENT", "XFINITY MOBILE 1234"]
    rows = []
    start = date(2024, 1, 3)
    for m in merchants:
        acct, kind = rng.choice([(accounts[0], "checking"), (accounts[1], "credit_card")])
        base = rng.choice([9.99, 15.49, 54.0, 120.0, 87.13, 33.33])
        step = rng.choice([7, 30, 31, 91, 365, 13])
        day = start + timedelta(days=rng.randrange(0, 20))
        for _ in range(n // len(merchants)):
            amount = round(base * rng.choice([1, 1, 1, 1.03, 0.97, 2.1, 0.5]), 2)
            rows.append((acct, day.isoformat(), -amount if kind == "checking" else amount,
                         f"{m} {rng.randrange(100, 999)}"))
            day += timedelta(days=step + rng.choice([-2, -1, 0, 0, 1, 3]))
    with env.db() as c:
        c.executemany("INSERT INTO transactions (user_id, account_id, date, amount, description, category) "
                      "VALUES ('tester', ?, ?, ?, ?, 'Bills')", rows)
        c.execute("INSERT INTO recurring_overrides (user_id, merchant_pattern, is_recurring) "
                  "VALUES ('tester', 'CORNER DELI', 0)")


def _strip(series):
    return [{k: (v if k != "txns" else [t["id"] for t in v]) for k, v in s.items()} for s in series]


@pytest.mark.parametrize("seed", [4, 8])
def test_recurring_scan_is_the_same_as_before(env, monkeypatch, seed):
    from app import recurring
    _seed_recurring(env, 600, seed)
    with env.db() as c:
        new, new_scanned = recurring._scan_series(c, "tester")
    monkeypatch.setattr(recurring, "_family_roots", _old_family_roots)
    monkeypatch.setattr(recurring, "_cluster_by_amount", _old_cluster_by_amount)
    monkeypatch.setattr(recurring, "_parse_date", _old_parse_date)
    with env.db() as c:
        old, old_scanned = recurring._scan_series(c, "tester")
    assert new_scanned == old_scanned and len(new) > 10
    assert _strip(new) == _strip(old)


def test_family_roots_and_clusters_match_on_many_shapes():
    from app import recurring
    rng = random.Random(3)
    words = ["DIRECT", "PAYMENT", "ACME", "PREMIUM", "INSURANCE", "CO", "NETFLIX", "COM", "A", "LONGERWORDHERE"]
    for _ in range(200):
        keys = {" ".join(rng.choice(words) for _ in range(rng.randint(1, 5))) for _ in range(rng.randint(1, 30))}
        assert recurring._family_roots(keys) == _old_family_roots(keys)
        txns = [{"id": i, "amount": rng.choice([-1, 1]) * rng.choice([1, 2.5, 3, 9.99, 10, 14, 15, 16.5, 20, 100, 120,
                                                                      121, 400]) * rng.choice([1, 1, 1.1, 0.9])}
                for i in range(rng.randint(1, 40))]
        assert recurring._cluster_by_amount(txns) == _old_cluster_by_amount(txns)
    for s in ("2026-08-05", "2026-8-5", "08/05/2026", "2026-02-30", "", "nonsense", "2026/08/05"):
        assert recurring._parse_date(s) == _old_parse_date(s), s


def test_recurring_scan_is_kept_until_the_data_changes(env):
    from app import recurring
    _seed_recurring(env, 120, 2)
    with env.db() as c:
        first = recurring.scan_recurring(c, "tester")
        again = recurring.scan_recurring(c, "tester")
    assert again["recurring"] is first["recurring"] or again["recurring"] == first["recurring"]
    with env.db() as c:
        c.execute("UPDATE transactions SET is_excluded = 1")
    with env.db() as c:
        assert recurring.scan_recurring(c, "tester")["scanned"] == 0


# ---- FP4: "Also apply to all existing transactions" -------------------------------------------------

def test_saving_a_rule_recategorizes_exactly_what_it_matches(env):
    from app import categorize
    acct = env.account()
    rng = random.Random(11)
    names = ["NETFLIX.COM", "netflix monthly", "SHELL OIL 123", "Shell Gas", "WHOLE FOODS", "AMAZON PRIME VIDEO",
             "AMAZON MKTP", "STRASSE CAFE", "straße cafe", "100% PURE", "A_B SHOP"]
    for i in range(400):
        env.txn(acct, "2026-08-01", -float(i + 1), rng.choice(names), category=rng.choice(["Dining", "Other", None]))
    with env.db() as c:
        for pattern, kind, cat in (("AMAZON", "substring", "Shopping"), ("AMAZON PRIME", "prefix", "Subscriptions"),
                                   ("SHELL GAS", "exact", "Gas"), ("%", "substring", "Odd")):
            categorize.save_rule(c, "tester", pattern, kind, cat)
        before = dict(c.execute("SELECT id, category FROM transactions").fetchall())
        # what the old full pass gives the rows the new rule matches
        categorize.save_rule(c, "tester", "AMAZON", "substring", "Online")
        rules = categorize._user_rules(c, "tester")
        expected = {}
        for tid, desc in c.execute("SELECT id, description FROM transactions").fetchall():
            if categorize._matches("AMAZON", "substring", desc):
                expected[tid] = categorize._best_match(rules, desc)[0]
        changed = categorize.recategorize_for_rule(c, "tester", "AMAZON", "substring")
        after = dict(c.execute("SELECT id, category FROM transactions").fetchall())
    assert changed == sum(1 for tid, cat in expected.items() if before[tid] != cat)
    for tid, cat in after.items():
        assert cat == expected.get(tid, before[tid])
    assert "Subscriptions" in after.values()     # the more specific older rule still wins where it applies


def test_an_empty_rule_pattern_matches_nothing():
    from app import categorize
    assert not categorize._matches("", "substring", "ANYTHING")
    assert categorize._best_match([("", "prefix", "All")], "ANYTHING") is None
    with pytest.raises(ValueError):
        categorize.save_rule(None, "tester", "  ", "substring", "All")


# ---- FSP8: toll devices, one query ------------------------------------------------------------------

def _old_devices(conn, user_id):
    from app.routes.tolls import _CLEAN_PASS
    from app.tolls import device_label
    rows = conn.execute(
        "SELECT d.*, c.name AS car_name, "
        f"(SELECT COUNT(*) FROM toll_transactions x JOIN toll_statements s ON s.id = x.statement_id WHERE x.device_ref = d.id AND {_CLEAN_PASS}) AS pass_count, "
        f"(SELECT COALESCE(SUM(x.amount), 0) FROM toll_transactions x JOIN toll_statements s ON s.id = x.statement_id WHERE x.device_ref = d.id AND {_CLEAN_PASS}) AS total, "
        f"(SELECT MIN(x.date) FROM toll_transactions x JOIN toll_statements s ON s.id = x.statement_id WHERE x.device_ref = d.id AND {_CLEAN_PASS}) AS first_date, "
        f"(SELECT MAX(x.date) FROM toll_transactions x JOIN toll_statements s ON s.id = x.statement_id WHERE x.device_ref = d.id AND {_CLEAN_PASS}) AS last_date, "
        "(SELECT COUNT(*) FROM toll_transactions x WHERE x.device_ref = d.id) AS any_rows "
        "FROM toll_devices d LEFT JOIN toll_cars c ON c.id = d.car_id WHERE d.user_id = ? ORDER BY d.id", (user_id,)).fetchall()
    return [{**dict(r), "label": device_label(r)} for r in rows]


def test_toll_devices_add_up_the_same(env):
    from app.routes.tolls import _devices_for_user
    car = env.insert("toll_cars", user_id="tester", name="Blue car")
    devices = [env.insert("toll_devices", user_id="tester", device_id=f"10{i}", plate="ABC", plate_state="CO",
                          car_id=car if i % 2 else None) for i in range(4)]
    env.insert("toll_devices", user_id="other", device_id="999", plate="ZZZ", plate_state="CO")
    statements = [env.insert("toll_statements", user_id="tester", status="complete"),
                  env.insert("toll_statements", user_id="tester", status="complete", deleted_at="2026-01-01 00:00:00")]
    rng = random.Random(5)
    for i in range(60):
        env.insert("toll_transactions", user_id="tester", statement_id=rng.choice(statements),
                   device_ref=rng.choice(devices[:3]), occurred_at=f"2026-0{rng.randint(1, 9)}-1{rng.randint(0, 9)} 07:00:00",
                   date=f"2026-0{rng.randint(1, 9)}-1{rng.randint(0, 9)}", plaza="P", direction="North",
                   amount=rng.choice([1.25, 2.6]), dedupe_key=f"k{i}",
                   review_status=rng.choice(["clean", "clean", "pending_review"]),
                   deleted_at=rng.choice([None, None, None, "2026-02-01 00:00:00"]))
    with env.db() as c:
        assert _devices_for_user(c, "tester") == _old_devices(c, "tester")
