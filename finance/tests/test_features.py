import os
"""Regression tests for user-facing behavior added in recent rounds (99-108)."""
import json


def _process(env, fake_vision, rows, lines, needs_confirmation=0):
    """Create a statement row for a real PDF and run the pipeline synchronously."""
    from app.parser import pipeline as pl
    acct = env.account() if not hasattr(env, "_acct") else env._acct
    env._acct = acct
    path = env.pdf(lines)
    sid = env.insert("statements", user_id="tester", account_id=acct, original_filename="s.pdf",
                     pdf_path=path, file_hash=path, status="processing", needs_confirmation=needs_confirmation)
    fake_vision(rows)
    pl.process_statement(sid, "tester", acct, "http://x", "m")
    return sid


BAL = ["Statement", "Previous Balance $1000.00", "New Balance $980.00"]


# ---- Transactions page (rounds 99, 108) -------------------------------------------------------

def test_transactions_page_does_not_query_until_search(env):
    a = env.account()
    env.txn(a, "2026-01-05", -12.34, "Coffee Shop")
    r = env.get("transactions")
    assert r.status_code == 200
    assert "Coffee Shop" not in r.text


def test_real_filter_shows_results(env):
    a = env.account()
    env.txn(a, "2026-01-05", -12.34, "Coffee Shop")
    r = env.get(f"transactions?account_id={a}")
    assert "Coffee Shop" in r.text


def test_all_accounts_search_shows_everything(env):
    a, b = env.account("Checking"), env.account("Savings", "savings")
    env.txn(a, "2026-01-05", -12.34, "Coffee Shop")
    env.txn(b, "2026-01-06", 200.0, "Payroll")
    r = env.get("transactions?searched=1&account_id=&q=&desc=")
    assert "Coffee Shop" in r.text and "Payroll" in r.text
    assert 'data-next="transactions?searched=1"' in r.text


def test_bulk_redirect_keeps_filter(env):
    a = env.account()
    t = env.txn(a, "2026-01-05", -12.34, "Coffee Shop")
    r = env.post("transactions-bulk", data={"ids": str(t), "action": "exclude",
                                            "next": f"transactions?account_id={a}"})
    assert r.status_code == 303
    loc = r.headers["location"]
    assert "??" not in loc and f"account_id={a}" in loc


# ---- CSV import status filter (rounds 99, 107) ------------------------------------------------

def test_csv_import_keeps_posted_and_cleared_only(env):
    a = env.account()
    csv_text = (
        "Date,Status,Amount,Merchant,Description\n"
        "2026-02-01,Posted,-20.00,Coffee Co,Coffee Co Purchase\n"
        "2026-02-02,Cleared,-40.00,Gas Co,Gas Co Purchase\n"
        "2026-02-03,CLEARED,-15.00,Book,Book Store Purchase\n"
        "2026-02-04,Pending,-8.00,Snack,Snack Co Purchase\n"
    )
    r = env.post("transactions-import-csv", data={"default_account_id": str(a)},
                 files={"files": ("card.csv", csv_text, "text/csv")},
                 headers={"accept": "application/json"})
    res = r.json()["results"][0]
    assert res["imported"] == 3 and res["skipped_status_count"] == 1
    with env.db() as c:
        descs = sorted(x[0] for x in c.execute("SELECT description FROM transactions"))
        ci = c.execute("SELECT skipped_status FROM csv_imports").fetchone()
    assert descs == ["Book Store Purchase", "Coffee Co Purchase", "Gas Co Purchase"]
    assert "Pending" in json.loads(ci[0])[0]


def test_csv_without_status_column_imports_everything(env):
    a = env.account()
    r = env.post("transactions-import-csv", data={"default_account_id": str(a)},
                 files={"files": ("p.csv", "Date,Description,Amount\n2026-02-10,Row,-5.00\n", "text/csv")},
                 headers={"accept": "application/json"})
    res = r.json()["results"][0]
    assert res["imported"] == 1 and res["skipped_status_count"] == 0


# ---- Statement pipeline (rounds 100, 102) -----------------------------------------------------

def test_zero_amount_lines_are_dropped_not_fatal(env, fake_vision):
    sid = _process(env, fake_vision,
                   [("2026-08-05", -20.0, "COFFEE"), ("2026-08-28", 0.0, "INTEREST CHARGED ON PURCHASES")],
                   BAL + ["2026-08-05 COFFEE -20.00"])
    with env.db() as c:
        st = c.execute("SELECT status FROM statements WHERE id=?", (sid,)).fetchone()
        n = c.execute("SELECT COUNT(*) FROM transactions WHERE statement_id=?", (sid,)).fetchone()[0]
    assert st["status"] != "error" and n == 1


def test_reextract_replaces_unconfirmed_transactions(env, fake_vision):
    sid = _process(env, fake_vision, [("2026-08-05", -20.0, "WRONG NAME")],
                   BAL + ["2026-08-05 SOME MERCHANT -20.00"], needs_confirmation=1)
    r = env.get("uploads")
    assert f"statement-reextract?id={sid}" in r.text
    fake_vision([("2026-08-05", -20.0, "RIGHT NAME")])
    r = env.post(f"statement-reextract?id={sid}")
    assert r.status_code == 200
    st = env.wait_status("statements", sid)
    with env.db() as c:
        descs = [x[0] for x in c.execute("SELECT description FROM transactions WHERE statement_id=?", (sid,))]
    assert st["status"] == "pending_review" and st["needs_confirmation"] == 1
    assert descs == ["RIGHT NAME"]


def test_reextract_refuses_complete_statement(env, fake_vision):
    sid = _process(env, fake_vision, [("2026-08-05", -20.0, "CLEAN")], BAL + ["2026-08-05 CLEAN -20.00"])
    with env.db() as c:
        assert c.execute("SELECT status FROM statements WHERE id=?", (sid,)).fetchone()[0] == "complete"
    assert env.post(f"statement-reextract?id={sid}").status_code == 400


# ---- Review link + Accounts column (rounds 104, 105) ------------------------------------------

def test_review_page_filters_to_one_statement(env):
    a = env.account()
    sa = env.insert("statements", user_id="tester", account_id=a, original_filename="a.pdf",
                    status="pending_review", needs_confirmation=0, balance_mismatch="off by $5")
    sb = env.insert("statements", user_id="tester", account_id=a, original_filename="b.pdf",
                    status="pending_review", needs_confirmation=0)
    env.txn(a, "2026-01-05", -12.34, "Flagged B", statement_id=sb, review_status="pending_review")
    up = env.get("uploads").text
    assert f"review?statement_id={sa}" in up and f"review?statement_id={sb}" in up
    ra = env.get(f"review?statement_id={sa}").text
    assert "a.pdf" in ra and "Flagged B" not in ra
    rb = env.get(f"review?statement_id={sb}").text
    assert "Flagged B" in rb and "a.pdf" not in rb


def test_accounts_show_latest_live_transaction_date(env):
    a = env.account()
    env.txn(a, "2026-01-05", -1.0, "Old")
    env.txn(a, "2026-03-20", -1.0, "New")
    env.txn(a, "2026-06-01", -1.0, "Gone", deleted_at="2026-06-02 00:00:00")
    r = env.get("transactions-accounts").text
    assert "Latest transaction" in r and "2026-03-20" in r and "2026-06-01" not in r
    assert "Latest transaction" not in env.get("accounts").text          # moved off Setup → Accounts


# ---- PDF viewer (rounds 103, 106) -------------------------------------------------------------

def test_pdf_viewer_has_sticky_toolbar_and_pinch_zoom(env):
    a = env.account()
    path = env.pdf(BAL)
    sid = env.insert("statements", user_id="tester", account_id=a, original_filename="s.pdf",
                     pdf_path=path, file_hash="h", status="complete")
    for url in (f"pdf-view?kind=statement&id={sid}", f"pdf-view?kind=statement&id={sid}&embed=1"):
        r = env.get(url)
        assert r.status_code == 200 and 'class="pdf-toolbar"' in r.text and "static/pages/pdf-view.js" in r.text
    with open(os.path.join(os.path.dirname(__file__), "..", "app", "static", "pages", "pdf-view.js")) as f:
        assert "setZoom" in f.read()          # the page's script (an external file: CSP)
    r = env.get(f"pdf-page?kind=statement&id={sid}&page=1")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"


def test_embedded_confirm_submits_to_the_app_page_not_the_top_window(env):
    # target="_top" would navigate Home Assistant's own window, which the companion
    # app hands to the phone's browser (no ingress session there -> "Not found").
    a = env.account()
    sid = env.insert("statements", user_id="tester", account_id=a, original_filename="s.pdf",
                     pdf_path=env.pdf(BAL), file_hash="h", status="pending_review", needs_confirmation=1)
    r = env.get(f"pdf-view?kind=statement&id={sid}&embed=1").text
    assert "Confirm &mdash; matches the PDF" in r and 'target="_parent"' in r and "_top" not in r

# ---- restart after a partial run (review findings) -------------------------------------------

def test_restart_cleans_rows_left_by_an_interrupted_run(env, fake_vision):
    a = env.account()
    path = env.pdf(BAL + ["2026-08-05 SOME MERCHANT -20.00"])
    sid = env.insert("statements", user_id="tester", account_id=a, original_filename="s.pdf", pdf_path=path,
                     file_hash="h", status="error", error_message="Processing was interrupted")
    env.txn(a, "2026-08-05", -20.0, "SOME MERCHANT", statement_id=sid)  # left by the cut-off run
    fake_vision([("2026-08-05", -20.0, "SOME MERCHANT")])
    assert env.post(f"statement-restart?id={sid}").status_code == 200
    st = env.wait_status("statements", sid)
    with env.db() as c:
        n = c.execute("SELECT COUNT(*) FROM transactions WHERE statement_id=?", (sid,)).fetchone()[0]
    assert st["status"] == "complete", st["error_message"]
    assert n == 1


def test_a_failure_while_saving_leaves_no_half_saved_rows(env, fake_vision, monkeypatch):
    from app.parser import pipeline as pl
    a = env.account()
    path = env.pdf(BAL + ["2026-08-05 SOME MERCHANT -20.00"])
    sid = env.insert("statements", user_id="tester", account_id=a, original_filename="s.pdf", pdf_path=path,
                     file_hash="h", status="processing")
    fake_vision([("2026-08-05", -20.0, "SOME MERCHANT")])

    def boom(*a, **k):
        raise RuntimeError("categorizer exploded")
    monkeypatch.setattr(pl, "categorize_transactions", boom)
    pl.process_statement(sid, "tester", a, "http://x", "m")
    with env.db() as c:
        st = c.execute("SELECT status FROM statements WHERE id=?", (sid,)).fetchone()[0]
        n = c.execute("SELECT COUNT(*) FROM transactions WHERE statement_id=?", (sid,)).fetchone()[0]
    assert st == "error" and n == 0


def test_resolving_one_dual_fuel_bill_keeps_the_pdf_for_its_sibling(env):
    pdf = env.pdf(["xcel"])
    electric = env.insert("utility_bills", user_id="tester", provider="Xcel Energy", utility_type="electric",
                          status="pending_review", review_status="pending_review", pdf_path=pdf,
                          period_start_date="2026-01-01", period_end_date="2026-01-31", cost=100.0)
    gas = env.insert("utility_bills", user_id="tester", provider="Xcel Energy", utility_type="gas",
                     status="pending_review", review_status="pending_review", pdf_path=pdf,
                     period_start_date="2026-01-01", period_end_date="2026-01-31", cost=30.0)
    form = {"id": str(gas), "period_start_date": "2026-01-01", "period_end_date": "2026-01-31",
            "usage_amount": "25", "usage_unit": "therms", "cost": "30.00", "due_date": "", "account_number_last4": ""}
    r = env.post("utility-resolve", data=form)
    assert r.status_code == 303, r.text[:300]
    import os
    assert os.path.exists(pdf)  # the electric bill is still waiting for review
    with env.db() as c:
        assert c.execute("SELECT pdf_path FROM utility_bills WHERE id=?", (electric,)).fetchone()[0] == pdf
