"""Regression tests for the 13 Oct 2026 review of Finance (1.4.2): security, delete-while-processing,
transactions held during the AI, money in cents, migrations, and the smaller page fixes."""
import json
import os
import re
import sqlite3
import threading

import pytest

INTRUDER = {"x-remote-user-id": "intruder", "x-remote-user-name": "Intruder"}
HTML = {"accept": "text/html,application/xhtml+xml"}
# A deletion a few hours ago: well inside the 30 days, so the app's own purge sweep never takes it.
from datetime import datetime, timedelta, timezone  # noqa: E402
RECENT = (datetime.now(timezone.utc) - timedelta(hours=5)).strftime("%Y-%m-%d %H:%M:%S")

# Tables that hold someone's data (an id-taking route must never change another person's rows).
DATA_TABLES = ("accounts", "transactions", "statements", "csv_imports", "skipped_duplicates", "utility_bills",
               "electric_meter_details", "toll_statements", "toll_transactions", "toll_cars", "toll_devices",
               "toll_pattern_groups", "toll_patterns", "category_rules", "custom_categories", "recurring_overrides",
               "user_access")


def _dump(env):
    with env.db() as c:
        return {t: sorted(tuple(r) for r in c.execute(f"SELECT * FROM {t}")) for t in DATA_TABLES}


# ---- IDOR: another person's ids posted to every id-taking route -----------------------------------

def test_ids_of_someone_elses_data_change_nothing(env):
    chk = env.account("Checking")
    card = env.account("Card", "credit_card")
    t1 = env.txn(chk, "2026-08-01", -100.0, "XFER OUT")
    t2 = env.txn(card, "2026-08-02", -100.0, "PAYMENT", review_status="pending_review")
    env.txn(chk, "2026-08-03", -5.0, "LINKED A")
    pdf = env.pdf(["x"])
    sid = env.insert("statements", user_id="tester", account_id=chk, original_filename="s.pdf", pdf_path=pdf,
                     file_hash="h", status="pending_review", needs_confirmation=1)
    t3 = env.txn(chk, "2026-08-04", -6.0, "ON STATEMENT", statement_id=sid)
    csv_id = env.insert("csv_imports", user_id="tester", original_filename="a.csv", row_count=0)
    skip = env.insert("skipped_duplicates", user_id="tester", account_id=chk, statement_id=sid, date="2026-08-01",
                      amount=-1.0, description="DUP", reason="on_file")
    bill = env.insert("utility_bills", user_id="tester", provider="Xcel Energy", status="pending_review",
                      review_status="pending_review", pdf_path=pdf, period_start_date="2026-01-01",
                      period_end_date="2026-01-31", cost=10.0, utility_type="gas")
    toll = env.insert("toll_statements", user_id="tester", status="error", pdf_path=pdf)
    car = env.insert("toll_cars", user_id="tester", name="Blue")
    device = env.insert("toll_devices", user_id="tester", device_id="1", car_id=car)
    group = env.insert("toll_pattern_groups", user_id="tester", name="Work")
    pattern = env.insert("toll_patterns", user_id="tester", name="Commute", signature="A>B", window_start_min=1,
                         window_end_min=2)
    rule = env.insert("category_rules", user_id="tester", pattern="SHOP", match_type="substring", category="Fun")
    custom = env.insert("custom_categories", user_id="tester", name="Pets")
    env.get("accounts", headers=INTRUDER)          # the intruder is a known, non-admin user
    before = _dump(env)

    posts = [
        ("transaction-note", {"id": t1, "note": "x"}), ("transaction-category", {"id": t1, "category": "Hacked"}),
        ("transaction-exclude", {"id": t1, "is_excluded": "1"}),
        ("transaction-resolve", {"id": t2, "date": "2026-08-02", "amount": "1", "description": "x"}),
        ("statement-delete", {"id": sid, "confirm_text": "Checking (no period on file)"}),
        ("statement-confirm", {"id": sid}), ("statement-remove-transaction", {"transaction_id": t3}),
        ("statement-acknowledge-mismatch", {"id": sid}),
        ("csv-import-delete", {"id": csv_id, "confirm_text": "a.csv"}),
        ("account-update", {"id": chk, "name": "Mine"}), ("account-archive", {"id": chk, "archived": "1"}),
        ("account-delete", {"id": chk, "confirm_text": "Checking"}),
        ("transfer-link", {"outflow_id": t1, "inflow_id": t2}), ("transfer-unlink", {"id": t1}),
        ("transfer-match-one", {"id": t1}),
        ("skipped-duplicate-insert", {"id": skip}), ("skipped-duplicate-dismiss", {"id": skip}),
        ("utility-delete", {"id": bill}), ("utility-reextract", {"id": bill}),
        ("utility-resolve", {"id": bill, "period_start_date": "2026-01-01", "period_end_date": "2026-01-31",
                             "cost": "1"}),
        ("toll-delete", {"id": toll}), ("toll-car-update", {"id": car, "name": "Red"}),
        ("toll-car-archive", {"id": car}), ("toll-car-delete", {"id": car}),
        ("toll-device-assign", {"id": device, "car_id": ""}), ("toll-device-delete", {"id": device}),
        ("toll-group-update", {"id": group, "name": "Gym"}), ("toll-group-delete", {"id": group}),
        ("toll-pattern-group-assign", {"id": pattern, "group_id": ""}), ("toll-pattern-delete", {"id": pattern}),
        ("toll-pattern-save", {"id": pattern, "name": "X", "start": "07:00", "end": "08:00"}),
        ("category-rule-delete", {"id": rule}), ("custom-category-delete", {"id": custom}),
        ("recurring-override", {"merchant_pattern": "SHOP", "is_recurring": "1"}),
    ]
    for url in ("statement-restart", "statement-reextract", "utility-restart", "toll-restart"):
        posts.append((f"{url}?id={sid if url.startswith('statement') else bill if url.startswith('utility') else toll}", {}))
    posts.append(("transactions-bulk", {"ids": f"{t1},{t2},{t3}", "action": "delete"}))
    posts.append(("transactions-bulk", {"ids": f"{t1},{t2}", "action": "categorize", "category": "X"}))
    posts.append(("transactions-bulk", {"ids": f"{t1},{t2}", "action": "approve"}))
    for url, data in posts:
        env.post(url, data={k: str(v) for k, v in data.items()}, headers=INTRUDER)
    after = _dump(env)
    # the intruder's own recurring override is theirs; nothing of tester's changed
    after["recurring_overrides"] = [r for r in after["recurring_overrides"] if "intruder" not in r]
    assert after == before


# ---- F7 / FSI3: deleting while a PDF is being read ------------------------------------------------

def test_a_statement_being_read_cant_be_deleted(env):
    a = env.account("Checking")
    sid = env.insert("statements", user_id="tester", account_id=a, original_filename="s.pdf", status="processing")
    r = env.post("statement-delete", data={"id": sid, "confirm_text": "Checking (no period on file)"})
    assert r.status_code == 303 and "error=processing" in r.headers["location"]
    assert "still being read" in env.get(r.headers["location"]).text
    with env.db() as c:
        assert c.execute("SELECT deleted_at FROM statements WHERE id=?", (sid,)).fetchone()[0] is None
    bill = env.insert("utility_bills", user_id="tester", provider="Xcel Energy", status="processing")
    toll = env.insert("toll_statements", user_id="tester", status="processing")
    assert env.post("utility-delete", data={"id": bill}, headers=HTML).status_code == 400
    assert env.post("toll-delete", data={"id": toll}, headers=HTML).status_code == 400
    r = env.post("user-wipe", data={"confirm_text": "Tester"})
    assert "error=user_processing" in r.headers["location"]
    with env.db() as c:
        for table, rid in (("utility_bills", bill), ("toll_statements", toll)):
            assert c.execute(f"SELECT deleted_at FROM {table} WHERE id=?", (rid,)).fetchone()[0] is None
        assert c.execute("SELECT COUNT(*) FROM accounts WHERE deleted_at IS NULL").fetchone()[0] == 1


def test_a_statement_deleted_while_read_saves_nothing(env, fake_vision, monkeypatch):
    from app.parser import pipeline as pl
    a = env.account()
    path = env.pdf(["Previous Balance $0.00", "New Balance -$20.00", "2026-08-05 SHOP -20.00"])
    sid = env.insert("statements", user_id="tester", account_id=a, original_filename="s.pdf", pdf_path=path,
                     file_hash="h", status="processing")
    fake_vision([("2026-08-05", -20.0, "SHOP")])
    real = pl.plan_categories

    def delete_meanwhile(*args, **kw):          # the person deletes it while the AI is being asked
        with env.db() as c:
            c.execute("UPDATE statements SET deleted_at = datetime('now') WHERE id = ?", (sid,))
        return real(*args, **kw)
    monkeypatch.setattr(pl, "plan_categories", delete_meanwhile)
    pl.process_statement(sid, "tester", a, "http://x", "m")
    with env.db() as c:
        assert c.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0
        row = c.execute("SELECT status, error_message FROM statements WHERE id=?", (sid,)).fetchone()
    assert row["status"] == "error" and "Deleted while it was being read" in row["error_message"]


def test_purging_a_statement_takes_all_its_deleted_rows_and_then_its_file(env):
    from app.db import get_db
    from app.purge import purge_soft_deleted
    a = env.account()
    pdf = env.pdf(["x"])
    old = "2000-01-01 00:00:00"
    sid = env.insert("statements", user_id="tester", account_id=a, pdf_path=pdf, status="complete", deleted_at=old)
    later = env.txn(a, "2026-08-01", -1.0, "DELETED LATER", statement_id=sid, deleted_at="2999-01-01 00:00:00")
    live = env.txn(a, "2026-08-01", -2.0, "RESTORED ON ITS OWN", statement_id=sid)
    with get_db() as conn:
        purge_soft_deleted(conn)          # (the app's own startup sweep may have got there first)
    assert not os.path.exists(pdf)
    with env.db() as c:
        ids = {r[0]: r[1] for r in c.execute("SELECT id, statement_id FROM transactions")}
    assert later not in ids and ids == {live: None}


def test_an_account_purge_removes_the_files_of_all_its_statements(env):
    from app.db import get_db
    from app.purge import purge_soft_deleted
    a = env.account()
    pdf = env.pdf(["x"])
    env.insert("statements", user_id="tester", account_id=a, pdf_path=pdf, status="complete",
               deleted_at="2999-01-01 00:00:00")        # deleted after the account: not on its own clock yet
    with env.db() as c:
        c.execute("UPDATE accounts SET deleted_at = '2000-01-01 00:00:00' WHERE id = ?", (a,))
    with get_db() as conn:
        purge_soft_deleted(conn)
    with env.db() as c:
        assert c.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0
    assert not os.path.exists(pdf)


def test_permanent_delete_removes_the_file_only_after_the_rows(env, monkeypatch):
    from app import purge
    a = env.account("Checking")
    pdf = env.pdf(["x"])
    sid = env.insert("statements", user_id="tester", account_id=a, pdf_path=pdf, status="complete",
                     deleted_at=RECENT)
    seen = []
    monkeypatch.setattr(purge, "_remove_file", lambda p: seen.append(
        (p, sqlite3.connect(env.db_path).execute("SELECT COUNT(*) FROM statements WHERE id=?", (sid,)).fetchone()[0])))
    r = env.post("permanent-delete", data={"kind": "statement", "id": sid,
                                           "confirm_text": "Checking (no period on file)"})
    assert r.status_code == 303
    assert seen == [(pdf, 0)]                    # the row was already gone (committed) when the file went


# ---- FSI4: restore brings back only what went with it ----------------------------------------------

def test_statement_restore_leaves_a_row_deleted_on_its_own(env):
    a = env.account("Checking")
    sid = env.insert("statements", user_id="tester", account_id=a, status="complete")
    kept = env.txn(a, "2026-08-01", -1.0, "WITH IT", statement_id=sid)
    alone = env.txn(a, "2026-08-02", -2.0, "DELETED BEFORE", statement_id=sid, deleted_at=RECENT)
    env.post("statement-delete", data={"id": sid, "confirm_text": "Checking (no period on file)"})
    with env.db() as c:
        stamps = {r[0] for r in c.execute("SELECT deleted_at FROM transactions WHERE id = ? UNION "
                                          "SELECT deleted_at FROM statements WHERE id = ?", (kept, sid))}
    assert len(stamps) == 1                       # one stamp for the statement and its rows
    env.post("statement-restore", data={"id": sid})
    with env.db() as c:
        rows = dict(c.execute("SELECT id, deleted_at FROM transactions").fetchall())
    assert rows[kept] is None and rows[alone] == RECENT


def test_nothing_comes_back_onto_a_deleted_account(env):
    a = env.account("Checking")
    t = env.txn(a, "2026-08-01", -1.0, "X", deleted_at=RECENT)
    sid = env.insert("statements", user_id="tester", account_id=a, status="complete", deleted_at=RECENT)
    with env.db() as c:
        c.execute("UPDATE accounts SET deleted_at = ? WHERE id = ?", (RECENT, a))
    for url, rid in (("transaction-restore", t), ("statement-restore", sid)):
        r = env.post(url, data={"id": rid})
        assert "error=account_deleted" in r.headers["location"]
        assert "restore the account first" in env.get(r.headers["location"]).text
    with env.db() as c:
        assert c.execute("SELECT COUNT(*) FROM transactions WHERE deleted_at IS NULL").fetchone()[0] == 0


def test_dashboard_leaves_out_rows_on_a_deleted_account(env):
    a = env.account("Gone")
    env.txn(a, "2026-08-01", -50.0, "LIVE ROW")
    with env.db() as c:
        c.execute("UPDATE accounts SET deleted_at = '2026-02-01 00:00:00' WHERE id = ?", (a,))
    page = env.get("dashboard?period=all").text
    assert "50.00" not in page


# ---- FP2: no write transaction while the AI is asked ------------------------------------------------

def _can_write_meanwhile(db_path) -> bool:
    other = sqlite3.connect(db_path, timeout=0.3)
    try:
        other.execute("BEGIN IMMEDIATE")
        other.execute("UPDATE app_settings SET value = value WHERE key = 'none'")
        other.rollback()
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        other.close()


def test_statement_categorization_doesnt_hold_the_database(env, fake_vision, monkeypatch):
    from app import categorize
    from app.parser import pipeline as pl
    a = env.account()
    path = env.pdf(["Previous Balance $0.00", "New Balance -$20.00", "2026-08-05 ODD SHOP -20.00"])
    sid = env.insert("statements", user_id="tester", account_id=a, original_filename="s.pdf", pdf_path=path,
                     file_hash="h", status="processing")
    fake_vision([("2026-08-05", -20.0, "ODD SHOP")])
    seen = []

    def llm(url, model, descriptions):
        seen.append(_can_write_meanwhile(env.db_path))
        return {"ODD SHOP": "Fun"}, "prompt", "{}", None
    monkeypatch.setattr(categorize, "categorize_via_llm", llm)
    pl.process_statement(sid, "tester", a, "http://x", "m")
    assert seen == [True]
    with env.db() as c:
        assert c.execute("SELECT category, category_source FROM transactions").fetchone()[:] == ("Fun", "llm")


def test_csv_categorization_doesnt_hold_the_database(env, monkeypatch):
    from app import categorize
    acct = env.account()
    seen = []

    def llm(url, model, descriptions):
        seen.append(_can_write_meanwhile(env.db_path))
        return {d: "Fun" for d in descriptions}, "prompt", "{}", None
    monkeypatch.setattr(categorize, "categorize_via_llm", llm)
    r = env.post("transactions-import-csv", data={"default_account_id": str(acct)}, headers={"accept": "application/json"},
                 files={"files": ("a.csv", b"Date,Description,Amount\n2026-08-01,ODD SHOP,-3\n", "text/csv")})
    assert r.json()["results"][0]["imported"] == 1 and seen == [True]
    with env.db() as c:
        assert c.execute("SELECT category FROM transactions").fetchone()[0] == "Fun"
        assert c.execute("SELECT categorization_llm_prompt FROM csv_imports").fetchone()[0] == "prompt"


def test_a_step_note_never_fails_a_job():
    from app.parser import documents

    class Busy:
        def execute(self, *a):
            raise sqlite3.OperationalError("database is locked")

        def rollback(self):
            pass
    documents.set_step("statements", Busy(), 1, "Reading")      # logged, not raised


# ---- money in cents, card signs before duplicates (FI1, FI2) ----------------------------------------

def test_amounts_are_stored_in_cents(env, fake_vision):
    from app.parser import pipeline as pl
    a = env.account()
    path = env.pdf(["Previous Balance $0.00", "New Balance -$12.34", "2026-08-05 SHOP -12.34"])
    sid = env.insert("statements", user_id="tester", account_id=a, original_filename="s.pdf", pdf_path=path,
                     file_hash="h", status="processing")
    fake_vision([("2026-08-05", -12.340000000001, "SHOP")])
    pl.process_statement(sid, "tester", a, "http://x", "m")
    with env.db() as c:
        row = c.execute("SELECT amount FROM transactions").fetchone()
        st = c.execute("SELECT balance_mismatch FROM statements").fetchone()
    assert row[0] == -12.34 and st[0] is None


def test_a_backwards_card_is_put_right_before_looking_for_duplicates(env, fake_vision):
    """A purchase already on file (from a CSV, stored the right way round) is recognised even when the model
    read the card backwards: the signs are decided first."""
    from app.parser import pipeline as pl
    card = env.account("Card", "credit_card")
    env.txn(card, "2026-09-06", 30.0, "SHOP")
    path = env.pdf(["Previous Balance $0.00", "New Balance $34.50", "2026-09-05 AUTOPAY PAYMENT 30.00",
                    "2026-09-06 SHOP 30.00", "2026-09-07 CAFE 4.50"])
    sid = env.insert("statements", user_id="tester", account_id=card, original_filename="s.pdf", pdf_path=path,
                     file_hash="h", status="processing")
    fake_vision([("2026-09-05", 30.0, "AUTOPAY PAYMENT"), ("2026-09-06", -30.0, "SHOP"), ("2026-09-07", -4.5, "CAFE")])
    pl.process_statement(sid, "tester", card, "http://x", "m")
    with env.db() as c:
        on_statement = sorted((r[0], r[1]) for r in c.execute(
            "SELECT description, amount FROM transactions WHERE statement_id = ?", (sid,)))
        skipped = c.execute("SELECT description, amount, reason FROM skipped_duplicates").fetchall()
    assert on_statement == [("AUTOPAY PAYMENT", -30.0), ("CAFE", 4.5)]
    assert [tuple(r) for r in skipped] == [("SHOP", 30.0, "on_file")]


@pytest.mark.parametrize("raw, value", [("12,50", None), ("1,23", None), ("1.234,56", None), ("12,3456", None),
                                         ("1,234", 1234.0), ("$1,234.56", 1234.56), ("(1,234.50)", -1234.5),
                                         ("-$1,000", -1000.0), ("7", 7.0)])
def test_a_decimal_comma_is_refused_not_multiplied(raw, value):
    from app.amounts import parse_number
    assert parse_number(raw) == value


# ---- what the AI sends back (F6) ---------------------------------------------------------------------

def test_ai_rows_get_iso_dates_finite_amounts_and_short_text(monkeypatch):
    from app.parser import vision
    data = {"transactions": [
        {"date": "08/05/2026", "amount": "-$1,234.50", "description": "A" * 2000},
        {"date": "2026-08-06T00:00:00", "amount": 5, "description": "OK"},
        {"date": "soon", "amount": 1, "description": "BAD DATE"},
        {"date": "2026-08-07", "amount": float("nan"), "description": "NAN"},
        {"date": "2026-08-07", "amount": "Infinity", "description": "INF"},
        "not a row",
    ]}
    monkeypatch.setattr(vision, "ask_vision_json", lambda *a, **k: (data, "{}"))
    result = vision.extract_vision("x.pdf", "http://x", "m")
    assert [(t.date, t.amount, len(t.description)) for t in result.transactions] == \
        [("2026-08-05", -1234.5, vision.MAX_DESCRIPTION_CHARS), ("2026-08-06", 5.0, 2)]
    assert len(result.rejected) == 3 and all("not imported" in r for r in result.rejected)


def test_ai_utility_and_toll_values_are_cleaned(monkeypatch):
    from app.parser import toll_vision, utility_vision
    bill = {"utility_type": "gas", "provider": "P" * 300, "period_start_date": "09/02/2026",
            "period_end_date": "2026-10-04", "usage_amount": 25, "usage_unit": "therms", "cost": "31.2",
            "account_number_last4": "1234567890123"}
    bad = dict(bill, cost=float("inf"))
    monkeypatch.setattr(utility_vision, "ask_vision_json", lambda *a, **k: ({"bills": [bill, bad]}, "{}"))
    bills = utility_vision.extract_vision_utility("x.pdf", "http://x", "m").bills
    assert len(bills) == 1 and bills[0].period_start_date == "2026-09-02" and bills[0].cost == 31.2
    assert len(bills[0].provider) == 60 and len(bills[0].account_number_last4) == 8
    ex, rejected = toll_vision.parse_toll_response({"grand_total": float("inf"), "cars": [{"device_id": "1", "transactions": [
        {"date": "2026-08-10", "time": "06:30", "amount": float("inf"), "plaza": "X"},
        {"date": "2026-08-10", "time": "06:31", "amount": 1.25, "plaza": "Y" * 900}]}]})
    assert ex.grand_total is None and len(rejected) == 1 and len(ex.cars[0].passes[0].plaza) == 200


# ---- the AI's limits (F3, F8, FI6) ------------------------------------------------------------------

def _pdf_pages(path, pages):
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(path)
    for i in range(pages):
        c.drawString(50, 750, f"page {i + 1}")
        c.showPage()
    c.save()
    return path


def test_a_pdf_with_too_many_pages_isnt_sent(env, tmp_path):
    from app import settings
    from app.parser import vision
    path = _pdf_pages(str(tmp_path / "long.pdf"), 3)
    assert settings.save({"max_pdf_pages": "2"}, "tester") == []
    with pytest.raises(vision.VisionExtractionError, match="3 pages; the most the app sends to the AI is 2"):
        vision.rasterize_pdf(path)
    assert settings.save({"max_pdf_pages": "3"}, "tester") == []
    out_dir, pages = vision.rasterize_pdf(path)
    assert len(pages) == 3
    import shutil
    shutil.rmtree(out_dir)
    assert settings.save({"max_pdf_pages": "0"}, "tester")      # 1..500
    assert "Most pages per PDF" in env.get("settings").text


def test_every_ai_request_has_an_end(monkeypatch):
    from app import ai_client
    from app.parser import vision
    assert ai_client.deadline_for(None) == 30 * 60
    assert ai_client.deadline_for(vision.GENERATE_TIMEOUT_SECONDS) >= vision.GENERATE_TIMEOUT_SECONDS
    seen = []
    monkeypatch.setattr(vision, "_ask", lambda *a, **k: seen.append(k["timeout"]) or ai_client.Reply(text="{}", raw="{}"))
    monkeypatch.setattr(vision, "warmup_model", lambda *a, **k: seen.append(k.get("timeout")))
    monkeypatch.setattr(vision, "rasterize_pdf", lambda p: ("/nonexistent-dir", []))
    vision.debug_call("x.pdf", "http://x", "m")
    assert seen == [vision.DEBUG_TIMEOUT_SECONDS, vision.DEBUG_TIMEOUT_SECONDS]


def test_a_job_keeps_the_settings_it_started_with(env):
    from app import settings
    with settings.pinned():
        before = settings.ai_provider()
        settings.save({"ai_provider": "anthropic", "ai_url": "", "ai_model": "c"}, "tester")
        assert settings.ai_provider() == before          # mid-job: unchanged
    assert settings.ai_provider() == "anthropic"         # the next job sees the change


def test_no_time_limit_still_ends(env, monkeypatch):
    from app import query_engine as qe
    monkeypatch.setattr(qe, "LONG_LIMIT_SECONDS", 0)
    with pytest.raises(qe.QueryError, match="minutes and was stopped"):
        qe.execute("tester", "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n) SELECT max(x) FROM n",
                   time_limit=False)


def test_a_query_result_is_measured_row_by_row(env, monkeypatch):
    from app import query_engine as qe
    a = env.account()
    for i in range(30):
        env.txn(a, "2026-08-01", -1.0 - i, "X" * 2000)
    monkeypatch.setattr(qe, "MAX_RESULT_BYTES", 20_000)
    with pytest.raises(qe.QueryError, match="larger than"):
        qe.execute("tester", "SELECT description FROM transactions")
    assert qe.MAX_VALUE_BYTES == 1_000_000


# ---- uploads (FS1, FSI2) ------------------------------------------------------------------------------

def test_upload_bodies_are_capped_before_they_are_read(env):
    from app import storage
    a = env.account()
    r = env.post("upload", data={"account_id": str(a)}, files={"files": ("s.pdf", b"%PDF-1.4", "application/pdf")},
                 headers={"content-length": str(storage.MAX_UPLOAD_REQUEST_BYTES + 1)})
    assert r.status_code == 413
    r = env.post("settings", data={"x": "y" * (3 * 1024 * 1024)})
    assert r.status_code == 413
    many = [("files", (f"{i}.pdf", b"%PDF-1.4 x", "application/pdf")) for i in range(storage.MAX_FILES_PER_REQUEST + 1)]
    r = env.post("upload", data={"account_id": str(a)}, files=many)
    assert r.status_code == 400 and "at most" in r.json()["error"]


def test_an_upload_is_streamed_to_disk_and_hashed(tmp_path):
    import hashlib
    import io
    from app import storage
    data = b"%PDF-1.4\n" + os.urandom(3 * 1024 * 1024)
    path, digest = storage.save_upload(io.BytesIO(data), str(tmp_path), "upload", ".pdf", 10 * 1024 * 1024, pdf=True)
    assert digest == hashlib.sha256(data).hexdigest() and open(path, "rb").read() == data
    with pytest.raises(storage.UploadRejected):
        storage.save_upload(io.BytesIO(data), str(tmp_path / "x"), "upload", ".pdf", 1024 * 1024, pdf=True)
    with pytest.raises(storage.UploadRejected, match="isn't a PDF"):
        storage.save_upload(io.BytesIO(b"hello"), str(tmp_path / "y"), "upload", ".pdf", 1024, pdf=True)
    assert os.listdir(tmp_path / "x") == [] and os.listdir(tmp_path / "y") == []


def test_an_admin_uploading_while_viewing_as_someone_uploads_for_them(env):
    env.insert("known_users", id="other", name="Robin")
    theirs = env.account("Robin's", user="other")
    r = env.post("upload?as_user=other", data={"account_id": str(theirs)},
                 files={"files": ("s.pdf", open(env.pdf(["x"]), "rb").read(), "application/pdf")})
    assert r.json()["results"][0]["statement_id"]
    with env.db() as c:
        assert c.execute("SELECT user_id FROM statements").fetchone()[0] == "other"
    js = open(os.path.join(os.path.dirname(__file__), "..", "app", "static", "pages", "uploads.js")).read()
    calls = re.findall(r'fetch\(([^,)]*)', js)
    assert len(calls) >= 8
    for call in calls:
        assert call.startswith("api("), call          # every request keeps the page's as_user …
    api = re.search(r'function api\(path\) \{(.*?)\}\n', js).group(1)
    assert "qs" in api                                # … which api() appends


# ---- links and messages (FS2, FS4, FSI7, FSI8, FSI12) ---------------------------------------------------

@pytest.mark.parametrize("bad", ["..", "../x", "a/../b", ".//evil.example", "./x", "x//y", "/x", "\\\\x"])
def test_next_never_climbs_out(bad):
    from app.security import safe_next
    assert safe_next(bad, "D") == "D"
    assert safe_next("dashboard?period=m:2026-01", "D") == "dashboard?period=m:2026-01"


def test_messages_in_the_address_are_codes_not_text(env):
    a = env.account("Checking")
    sid = env.insert("statements", user_id="tester", account_id=a, status="complete")
    fake = "Your account is locked, call 555-0100"
    for url in (f"statement-delete-confirm?id={sid}&error={fake}", f"account-delete-confirm?id={a}&error={fake}",
                f"user-wipe?error={fake}", f"recently-deleted?error={fake}", f"admin-storage?import_error={fake}",
                f"tolls?tab=upload&car_error={fake}", f"toll-analyze?error={fake}",
                f"admin-storage?tab=users&message={fake}&error={fake}"):
        page = env.get(url).text
        assert "555-0100" not in page, url
    r = env.post("statement-delete", data={"id": sid, "confirm_text": "nope"})
    assert "error=no_match" in r.headers["location"]
    assert "Typed text didn&#39;t match" in env.get(r.headers["location"]).text
    r = env.post("toll-car-add", data={"name": "  "})
    assert "car_error=car_name_missing" in r.headers["location"]


def test_a_browser_form_gets_a_page_not_raw_json(env):
    a = env.account()
    r = env.post("transaction-manual", data={"account_id": str(a), "date": "soon", "amount": "1", "description": "X"},
                 headers=HTML)
    assert r.status_code == 400 and "text/html" in r.headers["content-type"]
    assert "Not saved" in r.text and "recognized format" in r.text and 'href="uploads"' in r.text
    r = env.post("transaction-manual", data={"account_id": str(a), "date": "soon", "amount": "1", "description": "X"})
    assert r.status_code == 400 and "recognized format" in r.json()["error"]         # a script still gets JSON
    assert env.post("custom-categories", data={"name": " "}, headers=HTML).status_code == 400
    assert env.post("category-rules", data={"pattern": "x" * 201, "category": "A"}, headers=HTML).status_code == 400
    assert env.post("accounts", data={"name": "  ", "type": "checking"}, headers=HTML).status_code == 400
    r = env.post("accounts", data={"name": "Ok", "type": "piggy_bank"}, headers=HTML)
    assert r.status_code == 400 and "type" in r.text                                 # used to be a 500
    assert env.post("transaction-manual", data={"account_id": str(a), "date": "2026-08-01", "amount": "1",
                                                "description": "D" * 501}).status_code == 400


def test_saving_a_category_returns_to_the_same_filtered_view(env):
    a = env.account()
    t = env.txn(a, "2026-08-01", -5.0, "CAFE")
    page = env.get("transactions?desc=CAFE").text
    assert 'name="next" value="transactions?desc=CAFE"' in page
    r = env.post("transaction-category?as_user=tester", data={"id": str(t), "category": "Food", "next": "transactions?desc=CAFE"})
    assert r.headers["location"] == "transactions?desc=CAFE"


def test_recurring_redirect_query_is_encoded(env):
    r = env.post("recurring-scan-all", data={"account_id": "1&x=2"})
    assert r.headers["location"] == "recurring?scanned=1&account_id=1%26x%3D2"


# ---- pages (FSP4, FSP11, FSI5, FSI11, FSI13, FP7) -------------------------------------------------------

def test_transactions_come_a_page_at_a_time_with_the_full_total(env):
    a = env.account()
    with env.db() as c:
        c.executemany("INSERT INTO transactions (user_id, account_id, date, amount, description) VALUES "
                      "('tester', ?, ?, ?, ?)", [(a, f"2026-08-{1 + i % 28:02d}", -1.0, f"ROW {i}") for i in range(250)])
    page = env.get("transactions?searched=1").text
    assert page.count('class="bulk-select"') == 100
    assert "250 transactions" in page and "-$250.00" in page and "Page 1 of 3" in page
    page3 = env.get("transactions?searched=1&page=3").text
    assert page3.count('class="bulk-select"') == 50
    assert env.get("transactions?searched=1&size=1000").text.count('class="bulk-select"') == 250
    csv = env.get("transactions-export?q=").text
    assert csv.count("\n") == 251


def test_the_utility_dashboard_polls_with_a_tiny_answer(env):
    bid = env.insert("utility_bills", user_id="tester", provider="Xcel Energy", status="processing")
    page = env.get("utilities").text
    assert 'id="utility-dashboard-poll"' in page and 'hx-trigger="every 3s"' in page
    r = env.get("utility-dashboard-poll", headers={"hx-request": "true"})
    assert len(r.text) < 400 and "utility-dashboard-poll" in r.text
    with env.db() as c:
        c.execute("UPDATE utility_bills SET status = 'error' WHERE id = ?", (bid,))
    r = env.get("utility-dashboard-poll", headers={"hx-request": "true"})
    assert r.headers["hx-retarget"] == "#utility-bills-table-wrap" and 'id="utility-bills-table-wrap"' in r.text
    assert "utility-dashboard-poll" not in r.text


def test_pages_show_real_characters_and_survive_a_missing_cost(env):
    env.insert("statements", user_id="tester", account_id=env.account(), status="processing")
    assert "&amp;hellip;" not in env.get("uploads").text
    env.insert("utility_bills", user_id="tester", provider="Xcel Energy", status="complete", utility_type="electric",
               period_start_date="2026-01-01", period_end_date="2026-01-31", cost=None)
    env.insert("utility_bills", user_id="tester", provider="Xcel Energy", status="complete", utility_type="electric",
               period_start_date="2026-02-01", period_end_date="2026-02-28", cost=50.0)
    assert env.get("utility-comparison").status_code == 200
    root = os.path.join(os.path.dirname(__file__), "..", "app", "templates")
    for name in os.listdir(root):
        text = open(os.path.join(root, name), encoding="utf-8").read()
        for expr in re.findall(r"{{(.*?)}}", text, re.S):
            assert not re.search(r"""["'][^"']*&[a-z]+;[^"']*["'](?!\s*\|\s*safe)""", expr), (name, expr)
    assert "isn't yours to view" in env.get("account?id=999").text and "account doesn" not in env.get("account?id=999").text


def test_ids_are_unique_on_the_utility_review_page(env):
    bid = env.insert("utility_bills", user_id="tester", provider="Xcel Energy", status="pending_review",
                     utility_type="gas", period_start_date="2026-01-01", period_end_date="2026-01-31", cost=1.0)
    page = env.get(f"utility-review?id={bid}").text
    ids = re.findall(r'\sid="([^"]+)"', page)
    assert len(ids) == len(set(ids)), [i for i in ids if ids.count(i) > 1]


def test_pdf_page_images_are_cached_by_file_stamp(env):
    a = env.account()
    pdf = env.pdf(["Money 1,234.56"])
    sid = env.insert("statements", user_id="tester", account_id=a, pdf_path=pdf, status="pending_review")
    page = env.get(f"pdf-view?kind=statement&id={sid}").text
    src = re.search(r'<img src="(pdf-page[^"]+)"', page).group(1).replace("&amp;", "&")
    assert "v=" in src
    r = env.get(src)
    assert r.status_code == 200 and r.headers["cache-control"] == "private, max-age=86400"
    assert env.get(f"pdf-page?kind=statement&id={sid}&page=1").headers["cache-control"] == "no-store"
    long = env.get(f"pdf-view?kind=statement&id={sid}&q={'x' * 5000}" + "&amount=1" * 50)
    assert long.status_code == 200


# ---- settings, migrations, backups (FP5, FI4, FI5, FSI9, FSI10) --------------------------------------------

def test_settings_follow_every_change_without_a_connection_per_value(env, monkeypatch):
    from app import db, settings
    assert settings.get("feature_tolls") == "1"
    with env.db() as c:
        c.execute("UPDATE app_settings SET value = '0' WHERE key = 'feature_tolls'")
    assert settings.get("feature_tolls") == "0"                 # a change made elsewhere is seen at once
    opened = []
    real = db.get_db
    monkeypatch.setattr(db, "get_db", lambda: opened.append(1) or real())
    for _ in range(50):
        settings.get("ai_model"), settings.feature("utilities"), settings.ai_configured()
    assert opened == []


def test_a_failing_migration_changes_nothing_and_a_copy_is_kept(env, monkeypatch, tmp_path):
    from app import db
    with env.db() as c:
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    bad = tmp_path / "migrations"
    bad.mkdir()
    for f in os.listdir(db.MIGRATIONS_DIR):
        (bad / f).write_text((db.MIGRATIONS_DIR / f).read_text())
    (bad / "0999_broken.sql").write_text(
        "PRAGMA foreign_keys = OFF;\nCREATE TABLE half_done (x);\nINSERT INTO half_done VALUES (1);\n"
        "ALTER TABLE accounts ADD COLUMN broken TEXT;\nSELECT * FROM no_such_table;\n"
        "INSERT INTO schema_version (version) VALUES (999);\nPRAGMA foreign_keys = ON;\n")
    monkeypatch.setattr(db, "MIGRATIONS_DIR", bad)
    with pytest.raises(sqlite3.OperationalError):
        db.run_migrations()
    with env.db() as c:
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        cols = {r[1] for r in c.execute("PRAGMA table_info(accounts)")}
        assert "half_done" not in tables and "broken" not in cols
        assert c.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 32     # the newest migration
    assert os.path.exists(env.db_path + ".pre-0999")


def test_the_new_migration_rounds_old_amounts(make_env):
    env = make_env()
    a = env.account()
    t = env.txn(a, "2026-08-01", -12.340000001, "OLD")
    with env.db() as c:
        c.execute("DELETE FROM schema_version WHERE version = 27")
    make_env(tmp=env.dir)
    with env.db() as c:
        assert c.execute("SELECT amount FROM transactions WHERE id = ?", (t,)).fetchone()[0] == -12.34
        names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert {"idx_transactions_user_date", "idx_skipped_duplicates_statement", "idx_toll_transactions_trip"} <= names


def test_the_backup_hook_empties_the_write_ahead_log(env):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "backup_hook", os.path.join(os.path.dirname(__file__), "..", "backup_hook.py"))
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)
    env.txn(env.account(), "2026-08-01", -1.0, "X")
    assert hook.checkpoint(env.db_path) is True
    assert os.path.getsize(env.db_path + "-wal") == 0
    assert hook.checkpoint(env.db_path + ".missing") is False
    import yaml
    cfg = yaml.safe_load(open(os.path.join(os.path.dirname(__file__), "..", "config.yaml")))
    assert cfg["backup_pre"] == "python3 /app/backup_hook.py"
    assert cfg["watchdog"] == "http://[HOST]:[PORT:8321]/api/health"
    assert env.get("api/health").json() == {"status": "ok"}


def test_a_restore_waits_for_a_running_job(env, monkeypatch):
    from app.db import GATE
    from app.routes import admin_storage
    monkeypatch.setattr(admin_storage, "IMPORT_WAIT_SECONDS", 0.2)
    backup = env.get("admin-storage-download-db").content
    started, finish = threading.Event(), threading.Event()

    def job():
        with GATE.work():
            started.set()
            finish.wait(5)
    worker = threading.Thread(target=job)
    worker.start()
    started.wait(5)
    try:
        r = env.post("admin-storage-import-db", data={"confirm": "yes"}, files={"db_file": ("b.db", backup)})
        assert "import_error=import_busy" in r.headers["location"]
    finally:
        finish.set()
        worker.join()
    r = env.post("admin-storage-import-db", data={"confirm": "yes"}, files={"db_file": ("b.db", backup)})
    assert r.headers["location"] == "admin-storage?imported=1"
    leftovers = [f for f in os.listdir(os.path.dirname(env.db_path)) if f.startswith("finance-import-")]
    assert leftovers == []


def test_a_failed_backup_download_leaves_no_temporary_file(env, monkeypatch):
    import tempfile
    from app.common import db_core
    from app.purge import BACKUP_PREFIX
    monkeypatch.setattr(db_core, "snapshot", lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("disk full")))
    before = {f for f in os.listdir(tempfile.gettempdir()) if f.startswith(BACKUP_PREFIX)}
    with pytest.raises(sqlite3.OperationalError):
        env.get("admin-storage-download-db")
    assert {f for f in os.listdir(tempfile.gettempdir()) if f.startswith(BACKUP_PREFIX)} == before


# ---- shared code wiring ---------------------------------------------------------------------------------

def test_app_messages_signing_key_is_an_app_setting_kept_out_of_backups(env):
    from app import settings
    from app.routes.admin_storage import SECRET_SETTINGS
    assert settings.SETTINGS["bus_signing_key"].kind == "secret" and "bus_signing_key" in SECRET_SETTINGS
    assert settings.save({"bus_signing_key": "k" * 40}, "tester") == []
    page = env.get("settings").text
    assert 'name="bus_signing_key"' in page and "kkkk" in page and "k" * 40 not in page
    from app.common import app_bus
    assert callable(app_bus.default.signing_key) and app_bus.default.signing_key() == "k" * 40


# ---- the Household Assistant (FI9) ----------------------------------------------------------------------

def test_assistant_person_is_matched_by_id_before_name(env):
    from types import SimpleNamespace
    from app import tools
    from app.common import app_bus as bus
    with env.db() as c:
        c.executemany("INSERT INTO known_users (id, name) VALUES (?, ?)",
                      [("a1", "Sam"), ("a2", "Sam"), ("robin", "Robin"), ("sam", "Other")])
        c.executemany("INSERT INTO user_access (viewer_id, owner_id, granted_by) VALUES ('tester', ?, 'tester')",
                      [("a1",), ("a2",), ("robin",), ("sam",)])
    with env.db() as c:
        ctx = lambda person: SimpleNamespace(args={"person": person}, user={"id": "tester", "name": "Tester"}, conn=c)
        assert tools._owner(ctx("robin")) == ("robin", "Robin")
        assert tools._owner(ctx("Robin")) == ("robin", "Robin")
        assert tools._owner(ctx("sam")) == ("sam", "Other")           # the id wins over two people called Sam
        c.execute("DELETE FROM user_access WHERE owner_id = 'sam'")
        with pytest.raises(bus.Nack):
            tools._owner(ctx("Sam"))                                   # two people called Sam: ambiguous


# ---- smaller items (FI8, FS6, FSP10) ---------------------------------------------------------------------

def test_the_ai_answers_categories_by_number():
    from app import categorize
    descriptions = ["CORNER DELI", "CORNER DELI", "ODD SHOP"]
    got = categorize._by_description({"1": "Dining", "3.": " Hobbies ", "9": "Nope", "ODD SHOP": "Other"},
                                     descriptions)
    assert got == {"CORNER DELI": "Dining", "ODD SHOP": "Hobbies"}
    assert categorize._by_description({"1": "x" * 500}, ["A"])["A"] == "x" * categorize.MAX_CATEGORY_CHARS
    assert categorize._by_description({"A": True, "B": None}, ["A", "B"]) == {}


def test_a_utility_bill_keeps_only_the_last_four_digits(env):
    bid = env.insert("utility_bills", user_id="tester", provider="Xcel Energy", status="pending_review",
                     utility_type="gas", period_start_date="2026-01-01", period_end_date="2026-01-31", cost=1.0)
    r = env.post("utility-resolve", data={"id": bid, "period_start_date": "2026-01-01", "period_end_date": "2026-01-31",
                                          "cost": "1", "account_number_last4": "1234-5678-9012"})
    assert r.status_code == 303
    with env.db() as c:
        assert c.execute("SELECT account_number_last4 FROM utility_bills").fetchone()[0] == "9012"


def test_a_csv_with_many_bad_rows_keeps_a_bounded_list(env):
    from app.routes import csv_import
    acct = env.account()
    rows = "".join(f"2026-08-01,ROW {i},abc\n" for i in range(csv_import.MAX_KEPT_LINES + 50))
    r = env.post("transactions-import-csv", data={"default_account_id": str(acct)}, headers={"accept": "application/json"},
                 files={"files": ("a.csv", ("Date,Description,Amount\n" + rows).encode(), "text/csv")})
    assert r.json()["results"][0]["error_count"] == csv_import.MAX_KEPT_LINES + 50
    with env.db() as c:
        kept = json.loads(c.execute("SELECT row_errors FROM csv_imports").fetchone()[0])
    assert len(kept) == csv_import.MAX_KEPT_LINES + 1 and kept[-1] == "... and 50 more"
