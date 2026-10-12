"""Typed and imported numbers, edited dates and the `next` redirect (SPEC.md sections 7, 9, 10, 15)."""
import io

import pytest

from app.amounts import parse_number


def test_parse_number():
    for raw, want in (("12.5", 12.5), ("-4", -4.0), ("$1,234.56", 1234.56), ("-$1,234.56", -1234.56),
                      ("$ 1 234.56", 1234.56), ("(12.50)", -12.5), ("($1,000.00)", -1000.0), (".5", 0.5), (3, 3.0)):
        assert parse_number(raw) == want, raw
    for raw in ("", None, "nan", "NaN", "inf", "-Infinity", "1e999", "1e3", "abc", "1.2.3", "(-5)", "()", "$",
                float("nan"), float("inf"), True):
        assert parse_number(raw) is None, raw


def _csv(env, acct, text):
    r = env.post("transactions-import-csv", data={"default_account_id": str(acct)},
                 headers={"accept": "application/json"},
                 files={"files": ("t.csv", io.BytesIO(text.encode()), "text/csv")})
    assert r.status_code in (200, 303), r.text
    with env.db() as c:
        return c.execute("SELECT * FROM csv_imports ORDER BY id DESC").fetchone()


def _amounts(env):
    with env.db() as c:
        return {r["description"]: r["amount"] for r in c.execute("SELECT description, amount FROM transactions")}


def test_csv_rejects_nan_and_inf_per_row_and_reads_formatted_amounts(env):
    acct = env.account()
    imp = _csv(env, acct, "Date,Description,Amount\n2026-08-01,NAN ROW,nan\n2026-08-02,INF ROW,inf\n"
                          '2026-08-03,BIG,"-$1,234.56"\n2026-08-04,PAREN,(12.50)\n2026-08-05,PLAIN,7\n')
    assert _amounts(env) == {"BIG": -1234.56, "PAREN": -12.5, "PLAIN": 7.0}
    assert imp["error_count"] == 2 and "Row 2: Amount must be a number" in imp["row_errors"]
    # the Debit / Credit pair too
    imp = _csv(env, acct, 'Date,Description,Debit,Credit\n2026-08-06,DEBIT,"$1,000.00",\n2026-08-07,BAD,,inf\n')
    assert _amounts(env)["DEBIT"] == -1000.0 and "BAD" not in _amounts(env)
    assert imp["error_count"] == 1 and "neither Debit nor Credit" in imp["row_errors"]


def test_manual_entry_and_resolve_reject_non_finite(env):
    acct = env.account()
    form = {"account_id": str(acct), "date": "2026-08-01", "description": "X"}
    for bad in ("nan", "inf", "-Infinity"):
        assert env.post("transaction-manual", data={**form, "amount": bad}).status_code == 400
    assert env.post("transaction-manual", data={**form, "amount": "$1,250.00"}).status_code == 303
    assert _amounts(env) == {"X": 1250.0}
    tid = env.txn(acct, "2026-08-02", -5.0, "FLAGGED", review_status="pending_review")
    base = {"id": str(tid), "date": "2026-08-02", "description": "FLAGGED"}
    assert env.post("transaction-resolve", data={**base, "amount": "inf"}).status_code == 400
    assert env.post("transaction-resolve", data={**base, "amount": "nan"}).status_code == 400


def test_transaction_resolve_normalizes_the_date(env):
    acct = env.account()
    tid = env.txn(acct, "2026-08-02", -5.0, "FLAGGED", review_status="pending_review")
    base = {"id": str(tid), "amount": "-5", "description": "FLAGGED"}
    r = env.post("transaction-resolve", data={**base, "date": "Aug 2"})
    assert r.status_code == 400 and "recognized format" in r.json()["error"]
    assert env.post("transaction-resolve", data={**base, "date": "08/03/2026"}).status_code == 303
    with env.db() as c:
        assert tuple(c.execute("SELECT date, review_status FROM transactions WHERE id=?", (tid,)).fetchone()) \
            == ("2026-08-03", "clean")


def _pending_bill(env, utility_type="gas"):
    return env.insert("utility_bills", user_id="tester", utility_type=utility_type, provider="Xcel Energy",
                      period_start_date="2026-09-02", period_end_date="2026-10-04", cost=30.0,
                      status="pending_review", review_status="clean", original_filename="b.pdf")


def test_utility_resolve_normalizes_dates_and_rejects_non_finite(env):
    bid = _pending_bill(env)
    form = {"id": str(bid), "period_start_date": "09/02/2026", "period_end_date": "2026-10-04", "cost": "$31.20",
            "due_date": "10/27/2026"}
    for field, bad in (("cost", "inf"), ("cost", "nan"), ("usage_amount", "nan"), ("period_start_date", "Sep 2"),
                       ("period_end_date", "2026-13-01"), ("due_date", "soon")):
        r = env.post("utility-resolve", data={**form, field: bad})
        assert r.status_code == 400, (field, bad)
    eid = _pending_bill(env, "electric")
    r = env.post("utility-resolve", data={**form, "id": str(eid), "meter_net_total_kwh": "inf"})
    assert r.status_code == 400
    assert env.post("utility-resolve", data=form).status_code == 303
    with env.db() as c:
        row = c.execute("SELECT * FROM utility_bills WHERE id=?", (bid,)).fetchone()
    assert (row["period_start_date"], row["period_end_date"], row["due_date"], row["cost"], row["status"]) \
        == ("2026-09-02", "2026-10-04", "2026-10-27", 31.2, "complete")


@pytest.mark.parametrize("bad", ["https://evil.example/x", "//evil.example", "/\\evil.example", "\\\\evil.example",
                                 "javascript:alert(1)", "/absolute", "transactions\r\nX: y"])
def test_next_is_only_a_path_inside_the_app(env, bad):
    acct = env.account()
    tid = env.txn(acct, "2026-08-02", -5.0, "CAFE")
    r = env.post("transaction-category", data={"id": str(tid), "category": "Food", "next": bad})
    assert r.status_code == 303 and r.headers["location"] == "transactions"
    r = env.post("transactions-bulk", data={"ids": str(tid), "action": "exclude", "next": bad})
    assert r.headers["location"].startswith("transactions")
    r = env.post("transaction-manual", data={"account_id": str(acct), "date": "2026-08-01", "amount": "1",
                                             "description": "M", "next": bad})
    assert r.status_code == 303 and r.headers["location"] == "uploads"
    r = env.post("transfer-run-sweep", data={"next": bad})
    assert r.headers["location"].startswith("dashboard?")


def test_next_keeps_an_in_app_path(env):
    acct = env.account()
    tid = env.txn(acct, "2026-08-02", -5.0, "CAFE")
    r = env.post("transaction-category", data={"id": str(tid), "category": "Food",
                                                "next": "transactions?account_id=3"})
    assert r.headers["location"] == "transactions?account_id=3"
