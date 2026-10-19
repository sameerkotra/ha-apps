"""Uploading QFX / OFX files as statements (SPEC.md section 4a): saving, duplicates, accounts, balances, history,
the debug page, person data and migration 0028."""
import io
import json
import os
import sqlite3

import qfx_samples as qs


def _upload(env, raw, account="match", name="statement.qfx", **data):
    r = env.post("upload", data={"account_id": str(account), **{k: (json.dumps(v) if isinstance(v, dict) else v)
                                                                  for k, v in data.items()}},
                 files={"files": (name, io.BytesIO(raw), "application/octet-stream")})
    assert r.status_code == 200, r.text
    return r.json()["results"][0]


def _rows(env, sql, *args):
    with env.db() as c:
        return [dict(r) for r in c.execute(sql, args)]


def _account(env, name, type_="checking", last4=None, **extra):
    return env.insert("accounts", user_id="tester", name=name, type=type_, account_number_last4=last4, **extra)


def _leftover_files(env):
    out = []
    for root, _, files in os.walk(env.pending):
        out += files
    return out


# ---- saving ----------------------------------------------------------------------------------------

def test_checking_file_becomes_a_complete_statement(env):
    acct = _account(env, "Chase Checking", last4="6789")
    r = _upload(env, qs.checking(), account=acct)
    [s] = r["statements"]
    assert (s["account_name"], s["imported"], s["duplicates"], s["status"]) == ("Chase Checking", 3, 0, "complete")
    assert s["period"] == "Mar 3 – Jun 1, 2026"
    [st] = _rows(env, "SELECT * FROM statements")
    assert (st["source"], st["status"], st["needs_confirmation"], st["pdf_path"]) == ("qfx", "complete", 0, None)
    assert (st["period_start"], st["period_end"], st["statement_period"]) == ("2026-03-03", "2026-06-01", "2026-06")
    assert st["original_filename"] == "statement.qfx" and len(st["file_hash"]) == 64
    # OFX has no opening balance: closing balance less the period's rows
    assert st["new_balance"] == 1000.0 and st["previous_balance"] == round(1000 - (-4.5 + 2500 - 120), 2)
    txns = _rows(env, "SELECT date, post_date, amount, description, fitid, category FROM transactions ORDER BY date")
    assert [(t["date"], t["amount"], t["fitid"]) for t in txns] == [
        ("2026-03-04", -4.5, "C1"), ("2026-03-15", 2500.0, "C2"), ("2026-03-20", -120.0, "C3")]
    assert txns[0]["post_date"] == "2026-03-05" and txns[2]["description"] == "Check 1234"
    assert all(t["category"] for t in txns)                  # categorized after the commit
    assert _leftover_files(env) == []                        # the file is deleted after the import


def test_credit_card_signs_are_flipped_and_payments_categorized(env):
    acct = _account(env, "Visa", "credit_card", last4="9876")
    [s] = _upload(env, qs.card(), account=acct)["statements"]
    assert s["imported"] == 3
    txns = _rows(env, "SELECT description, amount, category FROM transactions ORDER BY date")
    assert [(t["description"], t["amount"]) for t in txns] == [
        ("GROCER", 25.0), ("HARDWARE STORE", 100.5), ("PAYMENT THANK YOU", -200.0)]
    assert txns[2]["category"] == "Payments"
    [st] = _rows(env, "SELECT new_balance, previous_balance, import_details FROM statements")
    assert st["new_balance"] == 125.5 and st["previous_balance"] == round(125.5 - (25 + 100.5 - 200), 2)
    assert "every sign flipped" in json.loads(st["import_details"])["sign_rule"]


def test_card_file_already_in_finances_convention_is_read_as_is(env):
    acct = _account(env, "Visa", "credit_card", last4="9876")
    rows = [qs.sgml_trn("DEBIT", "20260305", "25.00", "W1", "GROCER"),
            qs.sgml_trn("DEBIT", "20260306", "10.00", "W2", "CAFE"),
            qs.sgml_trn("CREDIT", "20260320", "-35.00", "W3", "PAYMENT")]
    _upload(env, qs.sgml_file(qs.card_block(rows, balance="0.00")), account=acct)
    assert [t["amount"] for t in _rows(env, "SELECT amount FROM transactions ORDER BY date")] == [25.0, 10.0, -35.0]
    [st] = _rows(env, "SELECT import_details, duplicate_transactions FROM statements")
    assert "read as written" in json.loads(st["import_details"])["sign_rule"]
    assert "read as written" in st["duplicate_transactions"]


def test_ofx_2x_xml_file(env):
    acct = _account(env, "Savings", "savings", last4="4444")
    [s] = _upload(env, qs.xml_file(), account=acct, name="april.ofx")["statements"]
    assert s["imported"] == 2
    assert [t["description"] for t in _rows(env, "SELECT description FROM transactions ORDER BY date")] == [
        "Café Zoë", "Interest"]


def test_no_usable_row_ends_as_error_without_restart(env):
    acct = _account(env, "Chase Checking", last4="6789")
    rows = [qs.sgml_trn("DEBIT", "", "-1.00", "L1", "NO DATE")]
    [s] = _upload(env, qs.sgml_file(qs.bank_block(rows)), account=acct)["statements"]
    assert s["status"] == "error" and "no date" in s["error"]
    page = env.get("uploads").text
    assert "No transaction in this file could be used" in page
    row_html = page.split('id="statement-row-')[1].split("</tr>")[0]
    assert "Restart" not in row_html and "Re-extract" not in row_html


# ---- refusals --------------------------------------------------------------------------------------

def test_html_saved_as_qfx_and_other_files_are_refused(env):
    acct = _account(env, "Checking")
    r = _upload(env, b"<html><body>Your session has expired</body></html>", account=acct)
    assert "looks like a web page" in r["error"]
    r = _upload(env, qs.sgml_file(qs.bank_block([])), account=acct)
    assert "no transactions" in r["error"]
    r = _upload(env, b"just some text", account=acct, name="x.qfx")
    assert "isn't a PDF or a QFX/OFX file" in r["error"]
    assert _rows(env, "SELECT id FROM statements") == [] and _leftover_files(env) == []


def test_a_qfx_file_over_the_size_limit_is_refused(make_env):
    env = make_env(MAX_CSV_MB="1")
    acct = _account(env, "Checking")
    big = qs.checking().replace(b"<OFX>", b"<OFX>" + b" " * (1024 * 1024 + 10))
    assert "larger than 1 MB" in _upload(env, big, account=acct)["error"]
    assert _leftover_files(env) == []


def test_investment_blocks_are_skipped_and_said(env):
    acct = _account(env, "Chase Checking", last4="6789")
    inv = ("<INVSTMTMSGSRSV1><INVSTMTTRNRS><INVSTMTRS><DTASOF>20260601<CURDEF>USD<INVACCTFROM><BROKERID>x"
           "<ACCTID>777</INVACCTFROM></INVSTMTRS></INVSTMTTRNRS></INVSTMTMSGSRSV1>")
    r = _upload(env, qs.sgml_file(qs.bank_block(qs.CHECKING_ROWS), inv), account=acct)
    assert r["statements"][0]["imported"] == 3
    assert any("Only bank and credit card QFX files are supported." in n for n in r["notes"])
    r = _upload(env, qs.sgml_file(inv), account=acct, name="inv.qfx")
    assert r["error"] == "Only bank and credit card QFX files are supported."


def test_pdf_with_match_by_account_number_asks_for_an_account(env):
    _account(env, "Checking")
    r = _upload(env, b"%PDF-1.4 x", account="match", name="s.pdf")
    assert r["error"] == "Pick an account for PDF files."


def test_pdf_and_qfx_in_one_upload(env, monkeypatch):
    import app.routes.upload as up
    monkeypatch.setattr(up, "start_job", lambda *a, **k: None)
    acct = _account(env, "Chase Checking", last4="6789")
    r = env.post("upload", data={"account_id": str(acct)},
                 files=[("files", ("s.pdf", io.BytesIO(b"%PDF-1.4 x"), "application/pdf")),
                        ("files", ("s.qfx", io.BytesIO(qs.checking()), "application/octet-stream"))])
    pdf, qfx = r.json()["results"]
    assert pdf["status"] == "processing" and qfx["statements"][0]["imported"] == 3
    assert sorted(s["source"] for s in _rows(env, "SELECT source FROM statements")) == ["pdf", "qfx"]
    [p] = _rows(env, "SELECT pdf_path FROM statements WHERE source = 'pdf'")
    assert p["pdf_path"].endswith(".pdf") and os.path.isfile(p["pdf_path"])


# ---- duplicates ------------------------------------------------------------------------------------

def test_overlapping_download_imports_only_new_rows_and_lists_the_rest(env):
    acct = _account(env, "Chase Checking", last4="6789")
    _upload(env, qs.checking(), account=acct)
    [s] = _upload(env, qs.overlapping(), account=acct, name="next.qfx")["statements"]
    assert (s["imported"], s["duplicates"]) == (2, 2)
    assert len(_rows(env, "SELECT id FROM transactions")) == 5
    skipped = _rows(env, "SELECT description, reason, matched_transaction_id FROM skipped_duplicates ORDER BY id")
    assert [(d["description"], d["reason"]) for d in skipped] == [("PAYROLL ACME", "on_file"), ("Check 1234", "on_file")]
    assert all(d["matched_transaction_id"] for d in skipped)
    # the balance uses every row of the period, the skipped ones included
    st = _rows(env, "SELECT previous_balance, new_balance FROM statements ORDER BY id")[1]
    assert st["previous_balance"] == round(925 - (2500 - 120 - 60 - 15), 2)
    # listed for Insert anyway
    sid = _rows(env, "SELECT id FROM statements ORDER BY id")[1]["id"]
    assert "2 skipped as duplicates" in env.get("uploads").text
    page = env.get(f"review?statement_id={sid}").text.split('id="skipped"')[1]
    assert "PAYROLL ACME" in page and "Insert anyway" in page
    did = _rows(env, "SELECT id FROM skipped_duplicates WHERE description = 'PAYROLL ACME'")[0]["id"]
    env.post("skipped-duplicate-insert", data={"id": str(did)})
    assert len(_rows(env, "SELECT id FROM transactions WHERE description = 'PAYROLL ACME'")) == 2


def test_a_row_matching_a_pdf_row_is_skipped(env):
    acct = _account(env, "Chase Checking", last4="6789")
    env.txn(acct, "2026-03-15", 2500.0, "PAYROLL ACME")          # from a PDF or CSV: no FITID
    [s] = _upload(env, qs.checking(), account=acct)["statements"]
    assert (s["imported"], s["duplicates"]) == (2, 1)
    [d] = _rows(env, "SELECT reason, description FROM skipped_duplicates")
    assert d == {"reason": "on_file", "description": "PAYROLL ACME"}


def test_same_fitid_twice_in_one_file_counts_once_and_same_looking_rows_with_new_fitids_are_kept(env):
    acct = _account(env, "Chase Checking", last4="6789")
    rows = [qs.sgml_trn("DEBIT", "20260305", "-4.50", "D1", "COFFEE"),
            qs.sgml_trn("DEBIT", "20260305", "-4.50", "D1", "COFFEE"),          # the same row twice
            qs.sgml_trn("DEBIT", "20260305", "-4.50", "D2", "COFFEE")]          # a second real coffee
    [s] = _upload(env, qs.sgml_file(qs.bank_block(rows, balance="91.00")), account=acct)["statements"]
    assert (s["imported"], s["duplicates"]) == (2, 1)
    [d] = _rows(env, "SELECT reason FROM skipped_duplicates")
    assert d["reason"] == "repeated"
    [st] = _rows(env, "SELECT previous_balance FROM statements")
    assert st["previous_balance"] == 100.0


def test_the_same_file_twice_is_refused(env):
    acct = _account(env, "Chase Checking", last4="6789")
    _upload(env, qs.checking(), account=acct)
    r = _upload(env, qs.checking(), account=acct)
    assert r["duplicate_of"] and r["message"].startswith("Already uploaded on ")
    assert len(_rows(env, "SELECT id FROM statements")) == 1


# ---- accounts --------------------------------------------------------------------------------------

def test_matched_by_last4(env):
    _account(env, "Other", last4="1111")
    acct = _account(env, "Chase Checking", last4="6789")
    [s] = _upload(env, qs.checking())["statements"]
    assert s["account_name"] == "Chase Checking"
    assert _rows(env, "SELECT account_id FROM statements")[0]["account_id"] == acct


def test_different_last4_asks_first(env):
    visa = _account(env, "Visa", "credit_card", last4="1234")
    other = _account(env, "Other card", "credit_card", last4="5555")
    r = _upload(env, qs.card(), account=visa)
    assert r["needs_account"] and _rows(env, "SELECT id FROM statements") == []
    [b] = r["blocks"]
    assert b["reason"] == "last4_mismatch" and b["suggested"] == visa
    assert b["question"] == ("This file is for an account ending 9876, but Visa ends 1234. "
                             "Use Visa anyway, or pick another account?")
    # "Use Visa anyway"
    [s] = _upload(env, qs.card(), account=visa, confirm_last4="1")["statements"]
    assert s["account_name"] == "Visa"
    env.post("statement-delete", data={"id": _rows(env, "SELECT id FROM statements")[0]["id"],
                                       "confirm_text": "Visa 2026-03"})
    # or another one picked
    [s] = _upload(env, qs.card(), account=visa, block_accounts={"0": other})["statements"]
    assert s["account_name"] == "Other card"


def test_two_blocks_go_to_two_accounts(env):
    checking = _account(env, "Checking", last4="6789")
    savings = _account(env, "Savings", "savings", last4="4321")
    r = _upload(env, qs.two_accounts())
    assert [(s["account_name"], s["imported"]) for s in r["statements"]] == [("Checking", 3), ("Savings", 1)]
    sts = _rows(env, "SELECT account_id, account_number_last4 FROM statements ORDER BY id")
    assert [s["account_id"] for s in sts] == [checking, savings]
    details = json.loads(_rows(env, "SELECT import_details FROM statements ORDER BY id")[0]["import_details"])
    assert [b["account"] for b in details["file"]["blocks"]] == ["Checking", "Savings"]


def test_no_match_offers_to_create_it(env):
    _account(env, "Checking", last4="1111")
    r = _upload(env, qs.card())
    [b] = r["blocks"]
    assert b["reason"] == "no_match" and b["suggested"] == "new" and b["new_name"] == "Visa Bank 9876"
    assert "Create it" in b["question"] and [a["name"] for a in r["accounts"]] == ["Checking"]
    [s] = _upload(env, qs.card(), block_accounts={"0": {"new": b["new_name"]}})["statements"]
    assert s["created_account"] and s["account_name"] == "Visa Bank 9876"
    [a] = _rows(env, "SELECT * FROM accounts WHERE name = 'Visa Bank 9876'")
    assert (a["type"], a["account_number_last4"], a["preferred_upload_method"], a["bank_name"]) == (
        "credit_card", "9876", "qfx", "Visa Bank")
    assert "QFX only" in env.get("accounts").text


def test_an_empty_last4_is_filled_in(env):
    acct = _account(env, "Chase Checking")
    _upload(env, qs.checking(), account=acct)
    assert _rows(env, "SELECT account_number_last4 FROM accounts")[0]["account_number_last4"] == "6789"


def test_the_full_account_number_is_never_stored_or_shown(env):
    acct = _account(env, "Chase Checking", last4="6789")
    _upload(env, qs.checking(), account=acct)
    sid = _rows(env, "SELECT id FROM statements")[0]["id"]
    with env.db() as c:
        dump = "\n".join(c.iterdump())
    page = env.get(f"statement-debug?id={sid}").text
    assert "000123456789" not in dump and "123456789" not in page and "6789" in page


# ---- balances and the statement --------------------------------------------------------------------

def _previous(env, acct, end, balance):
    return env.insert("statements", user_id="tester", account_id=acct, source="qfx", status="complete",
                      period_start="2026-01-01", period_end=end, new_balance=balance, file_hash="prev-" + end)


def test_continuity_with_the_previous_statement(env):
    acct = _account(env, "Chase Checking", last4="6789")
    opening = round(1000 - (-4.5 + 2500 - 120), 2)
    _previous(env, acct, "2026-03-02", opening)
    [s] = _upload(env, qs.checking(), account=acct)["statements"]
    assert s["status"] == "complete"
    details = json.loads(_rows(env, "SELECT import_details FROM statements WHERE file_hash NOT LIKE 'prev-%'")[0]
                         ["import_details"])
    assert "matches the previous statement" in details["continuity"]


def test_a_continuity_mismatch_flags_the_statement(env):
    acct = _account(env, "Chase Checking", last4="6789")
    _previous(env, acct, "2026-03-02", 50.0)
    [s] = _upload(env, qs.checking(), account=acct)["statements"]
    assert s["status"] == "pending_review"
    [st] = _rows(env, "SELECT id, status, balance_mismatch FROM statements WHERE source = 'qfx' AND file_hash NOT LIKE 'prev-%'")
    assert "closed at 50.00" in st["balance_mismatch"] and "missing" in st["balance_mismatch"]
    assert "Needs review" in env.get("uploads").text
    env.post("statement-acknowledge-mismatch", data={"id": str(st["id"])})
    assert _rows(env, "SELECT status FROM statements WHERE id = ?", st["id"])[0]["status"] == "complete"


def test_history_row_shows_the_badge_and_period_and_delete_removes_its_rows(env):
    acct = _account(env, "Chase Checking", last4="6789")
    _upload(env, qs.checking(), account=acct)
    page = env.get("uploads").text
    row = page.split('id="statement-row-')[1].split("</tr>")[0]
    assert ">QFX</span>" in row and "Mar 3 – Jun 1, 2026" in row and "3 transactions" in row
    assert "Re-extract" not in row and "Restart" not in row and "Open PDF" not in row
    sid = _rows(env, "SELECT id FROM statements")[0]["id"]
    assert "Chase Checking 2026-06" in env.get(f"statement-delete-confirm?id={sid}").text
    env.post("statement-delete", data={"id": str(sid), "confirm_text": "Chase Checking 2026-06"})
    assert _rows(env, "SELECT id FROM transactions WHERE deleted_at IS NULL") == []
    # deleted: the same file can come in again
    assert _upload(env, qs.checking(), account=acct)["statements"][0]["imported"] == 3


def test_debug_page_shows_blocks_rows_and_duplicates(env):
    acct = _account(env, "Chase Checking", last4="6789")
    _upload(env, qs.checking(), account=acct)
    rows = [qs.CHECKING_ROWS[1], qs.sgml_trn("DEBIT", "", "-1.00", "L1", "NO DATE")]
    _upload(env, qs.sgml_file(qs.bank_block(rows, start="20260601", end="20260630")), account=acct, name="b.qfx")
    sid = _rows(env, "SELECT id FROM statements ORDER BY id")[1]["id"]
    page = env.get(f"statement-debug?id={sid}").text
    assert "QFX / OFX file" in page and "····6789" in page and "Chase Checking" in page
    assert "DTPOSTED" in page and "<code>C2</code>" in page
    assert "duplicate: same FITID as transaction" in page and "left out: no date" in page
    assert "Signs as written" in page or "signs as written" in page
    assert "View PDF" not in page and "Resend to the AI" not in page
    first = _rows(env, "SELECT id FROM statements ORDER BY id")[0]["id"]
    assert "DTUSER" in env.get(f"statement-debug?id={first}").text


def test_account_page_shows_the_qfx_period(env):
    acct = _account(env, "Chase Checking", last4="6789")
    _upload(env, qs.checking(), account=acct)
    assert "Mar 3 – Jun 1, 2026" in env.get(f"account?id={acct}").text


# ---- elsewhere -------------------------------------------------------------------------------------

def test_person_data_export_and_import_keep_source_and_fitid(env):
    from app import people_data as pdata
    from app.common import person_data
    acct = _account(env, "Chase Checking", last4="6789")
    _upload(env, qs.checking(), account=acct)
    with env.db() as c:
        tables, _ = pdata.collect(c, "tester", pdata.options_from({"pdfs": False}))
    assert tables["statements"][0]["source"] == "qfx" and tables["statements"][0]["period_end"] == "2026-06-01"
    assert sorted(t["fitid"] for t in tables["transactions"]) == ["C1", "C2", "C3"]
    env.insert("known_users", id="other", name="Robin")

    def load(tables, schema, uid):
        data = person_data.bytes_io_package(app="finance", app_version="9.9.9", schema=schema,
                                            person={"id": "x", "name": "X"}, exported_by="admin", tables=tables)
        r = env.post("api/admin/people/data/import", files={"file": ("p.zip", data, "application/zip")})
        assert r.status_code == 200, r.text
        r = env.post(f"api/admin/people/{uid}/data/import/{r.json()['token']}", json={"mode": "add", "confirm": ""})
        assert r.status_code == 200, r.text

    load(tables, pdata.SCHEMA, "other")
    got = _rows(env, "SELECT source, period_start FROM statements WHERE user_id = 'other'")
    assert got == [{"source": "qfx", "period_start": "2026-03-03"}]
    assert sorted(t["fitid"] for t in _rows(env, "SELECT fitid FROM transactions WHERE user_id = 'other'")) == [
        "C1", "C2", "C3"]

    # a file from schema 27 has no source: it's a PDF statement
    env.insert("known_users", id="third", name="Sam")
    old = {"accounts": [{"id": 1, "name": "Old", "type": "checking"}],
           "statements": [{"id": 5, "account_id": 1, "status": "complete", "file_hash": "old-hash",
                           "statement_period": "2025-01"}],
           "transactions": [{"id": 9, "account_id": 1, "statement_id": 5, "date": "2025-01-02", "amount": -3.0,
                             "description": "OLD ROW"}]}
    load(old, 27, "third")
    assert _rows(env, "SELECT source FROM statements WHERE user_id = 'third'") == [{"source": "pdf"}]
    assert _rows(env, "SELECT fitid FROM transactions WHERE user_id = 'third'") == [{"fitid": None}]


def test_migration_0028_runs_on_an_old_database(tmp_path, monkeypatch, env):
    from app import db
    old_dir = tmp_path / "old_migrations"
    old_dir.mkdir()
    for p in sorted(db.MIGRATIONS_DIR.glob("*.sql")):
        if int(p.name[:4]) <= 27:
            (old_dir / p.name).write_text(p.read_text())
    path = str(tmp_path / "old.db")
    monkeypatch.setattr(db, "DB_PATH", path)
    monkeypatch.setattr(db, "MIGRATIONS_DIR", old_dir)
    db.run_migrations()
    c = sqlite3.connect(path)
    with c:
        ids = [c.execute("INSERT INTO accounts (user_id, name, type, preferred_upload_method) VALUES ('u', ?, "
                         "'checking', ?)", (m, m)).lastrowid for m in ("both", "pdf", "csv")]
        sid = c.execute("INSERT INTO statements (user_id, account_id, status) VALUES ('u', ?, 'complete')",
                        (ids[1],)).lastrowid
        c.execute("INSERT INTO transactions (user_id, account_id, statement_id, date, amount, description) "
                  "VALUES ('u', ?, ?, '2026-01-01', -1, 'X')", (ids[1], sid))
    c.close()
    monkeypatch.setattr(db, "MIGRATIONS_DIR", db.Path(db.__file__).parent.parent / "migrations")
    db.run_migrations()
    c = sqlite3.connect(path)
    assert [r[0] for r in c.execute("SELECT preferred_upload_method FROM accounts ORDER BY id")] == ["both", "pdf", "csv"]
    assert c.execute("SELECT source, period_start FROM statements").fetchone() == ("pdf", None)
    assert c.execute("SELECT fitid, account_id FROM transactions").fetchone() == (None, ids[1])
    assert c.execute("PRAGMA foreign_key_check").fetchall() == []
    assert c.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 31     # the newest migration
    with c:
        c.execute("UPDATE accounts SET preferred_upload_method = 'qfx' WHERE id = ?", (ids[0],))
    try:
        with c:
            c.execute("UPDATE accounts SET preferred_upload_method = 'xls' WHERE id = ?", (ids[0],))
        raise AssertionError("the CHECK should refuse it")
    except sqlite3.IntegrityError:
        pass
    assert c.execute("SELECT 1 FROM sqlite_master WHERE name = 'idx_transactions_fitid'").fetchone()
    c.close()
