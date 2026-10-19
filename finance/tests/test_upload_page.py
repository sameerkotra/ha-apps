"""The redesigned Upload → Statements page (SPEC.md sections 4, 10, 13; FINANCE_QFX_SPEC.md §10): one drop zone, the
accounts strip, one upload history with filter chips and "Waiting for you", tabs, what each account remembers
(a saved CSV layout, Skip confirmation: migration 0030), the summary's progress endpoint and the page without its
script. The browser-side helpers (static/pages/upload-logic.js) run in Node."""
import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
from datetime import datetime

import pytest

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "static", "pages")
HTML = {"accept": "text/html,application/xhtml+xml"}
WIFE = {"x-remote-user-id": "wife", "x-remote-user-name": "Wife"}


@pytest.fixture
def clock(env, monkeypatch):
    from app import statement_due
    monkeypatch.setattr(statement_due, "now", lambda: datetime(2026, 10, 18, 10, 0))
    return statement_due


def _csv(env, acct, text, name="bank.csv", **fields):
    data = {"default_account_id": str(acct), **{k: str(v) for k, v in fields.items()}}
    r = env.post("transactions-import-csv", data=data, headers={"accept": "application/json"},
                 files={"files": (name, io.BytesIO(text.encode()), "text/csv")})
    assert r.status_code == 200, r.text
    return r.json()["results"][0]


def _dates(env, acct):
    with env.db() as c:
        return [(r[0], r[1]) for r in c.execute(
            "SELECT date, description FROM transactions WHERE account_id = ? AND deleted_at IS NULL ORDER BY id", (acct,))]


def _account(env, acct):
    with env.db() as c:
        return dict(c.execute("SELECT * FROM accounts WHERE id = ?", (acct,)).fetchone())


def _pdf_bytes(env, text):
    return open(env.pdf([text]), "rb").read()


def _needs_confirmation(env):
    with env.db() as c:
        return c.execute("SELECT needs_confirmation FROM statements ORDER BY id DESC LIMIT 1").fetchone()[0]


# ------------------------------------------------------------------ migration 0030

def test_migration_0030_runs_on_an_old_database(tmp_path, monkeypatch, env):
    from app import db
    old_dir = tmp_path / "old_migrations"
    old_dir.mkdir()
    for p in sorted(db.MIGRATIONS_DIR.glob("*.sql")):
        if int(p.name[:4]) <= 29:
            (old_dir / p.name).write_text(p.read_text())
    path = str(tmp_path / "old.db")
    monkeypatch.setattr(db, "DB_PATH", path)
    monkeypatch.setattr(db, "MIGRATIONS_DIR", old_dir)
    db.run_migrations()
    c = sqlite3.connect(path)
    with c:
        c.execute("INSERT INTO accounts (user_id, name, type, preferred_upload_method, expected_day) "
                  "VALUES ('u', 'A', 'checking', 'csv', 12)")
    c.close()
    monkeypatch.setattr(db, "MIGRATIONS_DIR", db.Path(db.__file__).parent.parent / "migrations")
    db.run_migrations()
    c = sqlite3.connect(path)
    assert c.execute("SELECT name, preferred_upload_method, expected_day, csv_layout, skip_confirmation FROM accounts"
                     ).fetchone() == ("A", "csv", 12, None, None)
    assert c.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 30
    with pytest.raises(sqlite3.IntegrityError):
        with c:
            c.execute("UPDATE accounts SET skip_confirmation = 2")
    c.close()


# ------------------------------------------------------------------ the saved CSV layout (E)

TWO_DATES = ("Report for card ending 1234,,,\n"
             "Date Posted,Date Made,Description,Amount\n"
             "2026-01-06,2026-01-05,{a},-4.50\n"
             "2026-01-09,2026-01-08,{b},-12.00\n")


def test_a_saved_csv_layout_is_used_by_itself_and_can_be_overridden(env):
    visa = env.account("Visa", "credit_card")
    # first time: the date column has to be asked, and the result says which layout worked
    r = _csv(env, visa, TWO_DATES.format(a="COFFEE", b="LUNCH"))
    assert r["needs_date_column"] and r["layout_source"] == "auto"
    r = _csv(env, visa, TWO_DATES.format(a="COFFEE", b="LUNCH"), date_column="Date Made")
    assert (r["imported"], r["layout_source"]) == (2, "given")
    assert r["layout"] == {"header_row": 2, "data_row": 3, "date_column": "Date Made", "debit_credit": False}

    # "Remember this layout for Visa"
    saved = env.post("account-upload-prefs", data={"id": str(visa), "csv_layout": json.dumps(r["layout"])},
                     headers={"accept": "application/json"})
    assert saved.status_code == 200 and saved.json()["csv_layout"] == r["layout"]
    assert json.loads(_account(env, visa)["csv_layout"]) == r["layout"]

    # next time it's applied when the request gives none
    r = _csv(env, visa, TWO_DATES.format(a="BOOKS", b="TRAIN"))
    assert (r["imported"], r["layout_source"], r["fatal_error"]) == (2, "saved", None)
    assert _dates(env, visa)[-2:] == [("2026-01-05", "BOOKS"), ("2026-01-08", "TRAIN")]   # Date Made, as saved

    # the request's own choice wins (the header / data rows as a pair, the date column on its own)
    r = _csv(env, visa, TWO_DATES.format(a="SHOES", b="HATS"), header_row=2, date_column="Date Posted")
    assert (r["imported"], r["layout_source"]) == (2, "given")
    assert _dates(env, visa)[-2:] == [("2026-01-06", "SHOES"), ("2026-01-09", "HATS")]
    r = _csv(env, visa, TWO_DATES.format(a="SOCKS", b="TIES"), date_column="Date Posted")
    assert (r["imported"], r["layout_source"]) == (2, "saved")             # rows from the saved layout
    assert _dates(env, visa)[-1] == ("2026-01-09", "TIES")

    # saved_layout=0 imports without it (back to asking for the date column)
    r = _csv(env, visa, TWO_DATES.format(a="MILK", b="EGGS"), saved_layout="0")
    assert r["needs_date_column"] and r["layout_source"] == "auto"

    # another account, or the file's own Account column, has nothing saved
    other = env.account("Checking")
    assert _csv(env, other, TWO_DATES.format(a="X1", b="X2"))["needs_date_column"]


def test_a_saved_layout_that_no_longer_fits_says_so(env):
    visa = env.account("Visa", "credit_card")
    env.post("account-upload-prefs", data={"id": str(visa), "csv_layout": json.dumps(
        {"header_row": 2, "data_row": 3, "date_column": "Date Made", "debit_credit": False})})
    r = _csv(env, visa, "Date,Description,Amount\n2026-01-05,COFFEE,-4.50\n")
    assert r["fatal_error"] and r["layout_source"] == "saved"
    r = _csv(env, visa, "Date,Description,Amount\n2026-01-05,COFFEE,-4.50\n", saved_layout="0")
    assert (r["fatal_error"], r["imported"]) == (None, 1)


def test_debit_and_credit_layout_is_remembered(env):
    card = env.account("Amex", "credit_card")
    text = ("Card:,x,,\n,,,\nTrans. Date,Post Date,Description,Debit,Credit\n"
            "01/05/2026,01/06/2026,COFFEE,4.50,\n01/07/2026,01/08/2026,REFUND,,10.00\n")
    r = _csv(env, card, text)
    assert r["imported"] == 2 and r["layout_source"] == "auto"
    assert r["layout"] == {"header_row": 3, "data_row": 4, "date_column": "Trans. Date", "debit_credit": True}


def test_account_upload_prefs_checks_what_it_keeps(env):
    visa = env.account("Visa")
    theirs = env.account("Theirs", user="someone")
    post = lambda **d: env.post("account-upload-prefs", data={k: str(v) for k, v in d.items()},
                                headers={"accept": "application/json"})
    assert post(id=visa, csv_layout="not json").status_code == 400
    assert post(id=visa, csv_layout=json.dumps({"header_row": 3, "data_row": 2})).status_code == 400
    assert post(id=visa, csv_layout=json.dumps({"header_row": 0})).status_code == 400
    assert post(id=visa, skip_confirmation="maybe").status_code == 400
    assert post(id=theirs, skip_confirmation="1").status_code == 404
    r = post(id=visa, csv_layout=json.dumps({"header_row": "4", "date_column": " Trans Date ", "extra": "<x>"}))
    assert r.json()["csv_layout"] == {"header_row": 4, "data_row": None, "date_column": "Trans Date", "debit_credit": False}
    assert post(id=visa, skip_confirmation="1").json() == {"csv_layout": r.json()["csv_layout"], "skip_confirmation": 1}
    assert post(id=visa, clear_csv_layout="1").json()["csv_layout"] is None   # cleared; the other one kept
    assert _account(env, visa)["skip_confirmation"] == 1
    # a browser form goes back to the account's page
    r = env.post("account-upload-prefs", data={"id": str(visa), "skip_confirmation": "0"}, headers=HTML)
    assert r.status_code == 303 and r.headers["location"] == f"account?id={visa}"
    assert _account(env, visa)["skip_confirmation"] == 0


# ------------------------------------------------------------------ Skip confirmation per account (E)

def test_the_statement_upload_uses_the_accounts_saved_skip_confirmation(env):
    a = env.account("Checking")
    up = lambda text, **d: env.post("upload", data={"account_id": str(a), **d},
                                    files={"files": (f"{text}.pdf", _pdf_bytes(env, text), "application/pdf")})
    assert up("one").json()["results"][0]["statement_id"] and _needs_confirmation(env) == 1     # never chosen: confirm
    with env.db() as c:
        c.execute("UPDATE accounts SET skip_confirmation = 1 WHERE id = ?", (a,))
    up("two")
    assert _needs_confirmation(env) == 0                                  # not given: the saved choice
    up("three", skip_confirmation="")
    assert _needs_confirmation(env) == 0                                  # blank is "not given" too
    up("four", skip_confirmation="0")
    assert _needs_confirmation(env) == 1                                  # given: the upload's own choice
    with env.db() as c:
        c.execute("UPDATE accounts SET skip_confirmation = 0 WHERE id = ?", (a,))
    up("five", skip_confirmation="1")
    assert _needs_confirmation(env) == 0
    up("six")
    assert _needs_confirmation(env) == 1


def test_the_account_page_shows_and_changes_what_uploads_remember(env):
    a = env.account("Visa", "credit_card")
    page = env.get(f"account?id={a}").text
    assert 'name="skip_confirmation"' in page and "No saved CSV layout" in page and 'name="clear_csv_layout"' not in page
    with env.db() as c:
        c.execute("UPDATE accounts SET csv_layout = ? WHERE id = ?",
                  (json.dumps({"header_row": 3, "data_row": 4, "date_column": "Trans. Date", "debit_credit": True}), a))
    page = env.get(f"account?id={a}").text
    assert "header on row 3" in page and "Trans. Date" in page and 'name="clear_csv_layout"' in page
    base = {"id": str(a), "name": "Visa", "preferred_upload_method": "both"}
    env.post("account-update", data={**base, "upload_prefs_form": "1", "skip_confirmation": "1"})
    acct = _account(env, a)
    assert acct["skip_confirmation"] == 1 and acct["csv_layout"]                      # layout kept
    env.post("account-update", data=base)                                              # no Uploads group: unchanged
    assert _account(env, a)["skip_confirmation"] == 1
    assert 'id="acct-skip-confirmation" checked' in env.get(f"account?id={a}").text
    env.post("account-update", data={**base, "upload_prefs_form": "1", "clear_csv_layout": "1"})
    acct = _account(env, a)
    assert acct["skip_confirmation"] == 0 and acct["csv_layout"] is None


# ------------------------------------------------------------------ one person's data: schema 30

def test_person_data_carries_what_uploads_remember(env):
    from test_people_data import _export, _import
    from app import people_data as pdata
    assert pdata.SCHEMA == 30
    env.insert("known_users", id="tester", name="Tester")
    env.insert("known_users", id="wife", name="Wife")
    layout = {"header_row": 2, "data_row": 3, "date_column": "Date Made", "debit_credit": False}
    env.insert("accounts", user_id="tester", name="Visa", type="credit_card", csv_layout=json.dumps(layout),
               skip_confirmation=1)
    data = _export(env)
    import zipfile
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert json.loads(z.read("manifest.json"))["schema"] == 30
        [row] = json.loads(z.read("data/accounts.json"))
    assert json.loads(row["csv_layout"]) == layout and row["skip_confirmation"] == 1
    r = _import(env, data, uid="wife")
    assert r.status_code == 200, r.text
    with env.db() as c:
        a = c.execute("SELECT csv_layout, skip_confirmation FROM accounts WHERE user_id = 'wife'").fetchone()
    assert json.loads(a["csv_layout"]) == layout and a["skip_confirmation"] == 1


def test_a_schema_29_file_still_imports_and_bad_values_are_refused(env):
    from test_people_data import _craft, _import, _upload
    env.insert("known_users", id="tester", name="Tester")
    r = _import(env, _craft({"accounts": [{"id": 1, "name": "Old", "type": "checking", "reminders_on": 1}]}, schema=29))
    assert r.status_code == 200, r.text
    with env.db() as c:
        a = c.execute("SELECT csv_layout, skip_confirmation FROM accounts WHERE name = 'Old'").fetchone()
    assert (a["csv_layout"], a["skip_confirmation"]) == (None, None)
    for bad in ({"csv_layout": "junk"}, {"csv_layout": json.dumps({"header_row": -1})}, {"skip_confirmation": 5}):
        r = _upload(env, _craft({"accounts": [{"id": 1, "name": "New", "type": "checking", **bad}]}))
        assert r.status_code == 400 and "accounts, row 1" in r.json()["detail"], bad


# ------------------------------------------------------------------ one upload history and Waiting for you (C)

def _history(env, a, b):
    """A few of every kind: ids by name."""
    ids = {}
    ids["done"] = env.insert("statements", user_id="tester", account_id=a, original_filename="aa-done.pdf",
                             status="complete", uploaded_at="2026-10-01 09:00:00", file_hash="h1")
    env.txn(a, "2026-09-03", -5.0, "SHOP", statement_id=ids["done"])
    ids["awaiting"] = env.insert("statements", user_id="tester", account_id=a, original_filename="bb-awaiting.pdf",
                                 status="pending_review", needs_confirmation=1, uploaded_at="2026-10-02 09:00:00",
                                 file_hash="h2")
    ids["review"] = env.insert("statements", user_id="tester", account_id=b, original_filename="cc-review.pdf",
                               status="pending_review", needs_confirmation=0, uploaded_at="2026-10-03 09:00:00",
                               file_hash="h3")
    env.txn(b, "2026-09-05", -7.0, "FLAGGED", statement_id=ids["review"], review_status="pending_review")
    ids["error"] = env.insert("statements", user_id="tester", account_id=b, original_filename="dd-error.pdf",
                              status="error", error_message="It broke", uploaded_at="2026-10-04 09:00:00", file_hash="h4")
    ids["qfx_error"] = env.insert("statements", user_id="tester", account_id=b, original_filename="ee-bad.qfx",
                                  source="qfx", status="error", error_message="No usable row",
                                  uploaded_at="2026-10-05 09:00:00", file_hash="h5")
    ids["csv"] = env.insert("csv_imports", user_id="tester", original_filename="ff-good.csv", row_count=1,
                            imported_at="2026-10-06 09:00:00")
    env.txn(a, "2026-09-20", -9.0, "CSV ROW", csv_import_id=ids["csv"])
    ids["csv_bad"] = env.insert("csv_imports", user_id="tester", original_filename="gg-bad.csv", row_count=0,
                                fatal_error="CSV is missing required column(s): amount",
                                imported_at="2026-10-07 09:00:00")
    return ids


def _row_ids(html):
    table = html.split('id="history-table"')[1] if 'id="history-table"' in html else ""
    return re.findall(r'<tr id="((?:statement|csv)-row-\d+)"', table)


def _waiting_ids(html):
    part = html.split('id="waiting-list"')[1].split("</ul>")[0] if 'id="waiting-list"' in html else ""
    return [int(x) for x in re.findall(r'id="waiting-(\d+)"', part)]


def test_one_history_newest_first_with_waiting_for_you_pinned(env):
    a, b = env.account("Alpha"), env.account("Beta")
    ids = _history(env, a, b)
    r = env.get("uploads-history")
    assert r.status_code == 200 and r.text.lstrip().startswith("<") and "<html" not in r.text
    assert _row_ids(r.text) == [f"csv-row-{ids['csv_bad']}", f"csv-row-{ids['csv']}", f"statement-row-{ids['qfx_error']}",
                                f"statement-row-{ids['error']}", f"statement-row-{ids['review']}",
                                f"statement-row-{ids['awaiting']}", f"statement-row-{ids['done']}"]
    # Waiting for you: confirm, review, restart — newest first; never a complete one or a CSV import
    assert _waiting_ids(r.text) == [ids["qfx_error"], ids["error"], ids["review"], ids["awaiting"]]
    w = r.text.split('id="waiting-list"')[1].split("</ul>")[0]
    assert f'href="statement-confirm?id={ids["awaiting"]}"' in w and f'href="review?statement_id={ids["review"]}"' in w
    assert f'data-url="statement-restart?id={ids["error"]}"' in w
    assert f'statement-restart?id={ids["qfx_error"]}' not in w            # a QFX file isn't kept: nothing to restart
    assert "Waiting for you" in r.text and '<span class="count-pill">4</span>' in r.text
    # each row: type badge, account, period, count, status chip
    row = r.text.split(f'id="statement-row-{ids["done"]}"')[1].split("</tr>")[0]
    assert ">PDF</span>" in row and "Alpha" in row and "Sep 3, 2026" in row and "1 transaction<" in row
    assert "status-complete" in row
    csv_row = r.text.split(f'id="csv-row-{ids["csv"]}"')[1].split("</tr>")[0]
    assert ">CSV</span>" in csv_row and "Alpha" in csv_row and "1 imported" in csv_row
    bad_row = r.text.split(f'id="csv-row-{ids["csv_bad"]}"')[1].split("</tr>")[0]
    assert "status-error" in bad_row and "Failed" in bad_row
    # the page shows the same list
    page = env.get("uploads").text
    assert _row_ids(page) == _row_ids(r.text) and _waiting_ids(page) == _waiting_ids(r.text)


def test_history_filter_chips(env):
    a, b = env.account("Alpha"), env.account("Beta")
    ids = _history(env, a, b)
    get = lambda q: env.get("uploads-history?" + q).text
    assert _row_ids(get("h_type=csv")) == [f"csv-row-{ids['csv_bad']}", f"csv-row-{ids['csv']}"]
    assert _row_ids(get("h_type=qfx")) == [f"statement-row-{ids['qfx_error']}"]
    assert len(_row_ids(get("h_type=pdf"))) == 4
    assert _row_ids(get(f"h_account={b}")) == [f"statement-row-{ids['qfx_error']}", f"statement-row-{ids['error']}",
                                               f"statement-row-{ids['review']}"]
    assert _row_ids(get(f"h_account={a}&h_type=csv")) == [f"csv-row-{ids['csv']}"]
    assert len(_row_ids(get("h_status=waiting"))) == 4
    assert _row_ids(get("h_status=error")) == [f"csv-row-{ids['csv_bad']}", f"statement-row-{ids['qfx_error']}",
                                               f"statement-row-{ids['error']}"]
    assert _row_ids(get("h_status=done")) == [f"csv-row-{ids['csv']}", f"statement-row-{ids['done']}"]
    assert len(_row_ids(get("h_type=nonsense&h_status=<x>&h_account=abc"))) == 7      # unknown: all
    # the chips are buttons with aria-pressed; the waiting list is pinned whatever the filter
    html = get("h_type=csv")
    assert re.search(r'<button type="submit" class="chip" name="h_type" value="csv" aria-pressed="true">CSV</button>', html)
    assert re.search(r'name="h_type" value="" aria-pressed="false">All types<', html)
    assert re.search(r'name="h_status" value="" aria-pressed="true">Any status<', html)
    assert re.search(rf'name="h_account" value="{a}" aria-pressed="false">Alpha<', html)
    assert len(_waiting_ids(html)) == 4
    # each chip group keeps the other groups' choice (and works without the script: a GET form to the page)
    form = html.split('aria-label="Status"')[0].rsplit("<form", 1)[1]
    assert 'action="uploads"' in form
    status_form = html.split('aria-label="Status"')[1].split("</form>")[0]
    assert '<input type="hidden" name="h_type" value="csv">' in status_form
    assert _row_ids(env.get("uploads?h_type=csv").text) == _row_ids(html)
    assert "Nothing matches these filters" in get(f"h_account={b}&h_type=csv")


def test_history_sort_and_paging(env):
    a = env.account("Alpha")
    for i in range(12):
        cid = env.insert("csv_imports", user_id="tester", original_filename=f"f{i:02d}.csv", row_count=1,
                         imported_at=f"2026-09-{i + 1:02d} 09:00:00")
        env.txn(a, f"2026-08-{i + 1:02d}", -1.0 - i, f"ROW {i}", csv_import_id=cid)
    html = env.get("uploads-history").text
    assert len(_row_ids(html)) == 10 and "Showing 1&ndash;10 of 12" in html
    assert 'href="uploads?h_page=2"' in html                                # page links go to the page itself
    names = lambda h: re.findall(r'<span class="file-name-text">(f\d+)\.csv</span>', h)
    assert names(env.get("uploads-history?h_page=2").text) == ["f01", "f00"]
    assert names(env.get("uploads-history?h_sort=file&h_dir=asc&h_size=all").text) == [f"f{i:02d}" for i in range(12)]
    assert names(env.get("uploads-history?h_sort=count&h_dir=asc").text)[0] == "f00"
    assert 'aria-sort="ascending"' in env.get("uploads-history?h_sort=file&h_dir=asc").text


def test_history_actions_and_admin_debug_fit_in_seven_columns(env):
    a = env.account("Alpha")
    sid = env.insert("statements", user_id="tester", account_id=a, original_filename="s.pdf", status="complete")
    cid = env.insert("csv_imports", user_id="tester", original_filename="c.csv", row_count=0)
    html = env.get("uploads-history").text
    head = html.split('id="history-table"')[1].split("</thead>")[0]
    assert len(re.findall(r"<th[ >]", head)) == 7
    row = html.split(f'id="statement-row-{sid}"')[1].split("</tr>")[0]
    assert row.count("<td") == 7 and f'statement-delete-confirm?id={sid}' in row and f'statement-debug?id={sid}' in row
    crow = html.split(f'id="csv-row-{cid}"')[1].split("</tr>")[0]
    assert crow.count("<td") == 7 and f'csv-import-debug?id={cid}' in crow
    # the polled row has the same seven cells
    polled = env.get(f"statement-status?id={sid}").text
    assert polled.count("<td") == 7 and f'id="statement-row-{sid}"' in polled
    # someone who isn't an admin has no debug link
    env.insert("known_users", id="wife", name="Wife")
    mine = env.insert("statements", user_id="wife", account_id=env.account("Hers", user="wife"),
                      original_filename="w.pdf", status="complete")
    theirs = env.get("uploads-history", headers=WIFE).text
    assert f'statement-row-{mine}' in theirs and "statement-debug" not in theirs and f"statement-row-{sid}" not in theirs


def test_statement_progress(env):
    a, b = env.account("Alpha"), env.account("Beta")
    ids = _history(env, a, b)
    other = env.insert("statements", user_id="someone", account_id=env.account("X", user="someone"), status="complete")
    busy = env.insert("statements", user_id="tester", account_id=a, status="processing", current_step="Reading page 2")
    r = env.get(f"statement-progress?ids={ids['done']},{ids['awaiting']},{ids['review']},{ids['error']},{busy},{other},x")
    got = {s["id"]: s for s in r.json()["statements"]}
    assert set(got) == {ids["done"], ids["awaiting"], ids["review"], ids["error"], busy}
    assert got[ids["done"]]["state"] == "complete" and got[ids["done"]]["count"] == 1
    assert got[ids["awaiting"]]["state"] == "awaiting"
    assert got[ids["review"]]["state"] == "review" and got[ids["review"]]["flagged"] == 1
    assert got[ids["error"]]["state"] == "error" and got[ids["error"]]["error"] == "It broke"
    assert got[busy]["state"] == "processing" and got[busy]["step"] == "Reading page 2"
    assert env.get("statement-progress").json() == {"statements": []}


# ------------------------------------------------------------------ the accounts strip (B)

def test_accounts_strip_latest_gaps_overdue_and_preferred_method(env, clock):
    # no expected date: a calendar month with nothing uploaded between the first and the newest is missing
    visa = env.insert("accounts", user_id="tester", name="Visa", type="credit_card", preferred_upload_method="qfx",
                      account_number_last4="1234")
    sv = env.insert("statements", user_id="tester", account_id=visa, status="complete", uploaded_at="2026-09-25 10:00:00")
    for d in ("2026-06-05", "2026-08-07", "2026-09-03"):
        env.txn(visa, d, 5.0, "SHOP " + d, statement_id=sv)
    env.txn(visa, "2026-07-10", 5.0, "BY HAND")                            # typed in: not an upload
    # an expected date (monthly, on the 12th): each statement's own span counts, and the latest one is overdue
    chk = env.insert("accounts", user_id="tester", name="Checking", type="checking", preferred_upload_method="pdf",
                     expected_every_months=1, expected_day=12, expected_set_at="2026-01-01 00:00:00")
    sc = env.insert("statements", user_id="tester", account_id=chk, status="complete", uploaded_at="2026-09-25 10:00:00")
    for d in ("2026-06-20", "2026-08-01", "2026-09-20"):
        env.txn(chk, d, -5.0, "SPEND " + d, statement_id=sc)
    env.insert("accounts", user_id="tester", name="Old one", type="checking", is_archived=1)
    fresh = env.insert("accounts", user_id="tester", name="Brand new", type="savings")

    from app import upload_page
    from app.auth import User
    me = User(id="tester", name="Tester", is_admin=True)
    with env.db() as c:
        cards = {x["name"]: x for x in upload_page.strip_cards(c, me, me)}
    assert list(cards) == ["Brand new", "Checking", "Visa"]                 # live, unarchived, by name
    v, k, n = cards["Visa"], cards["Checking"], cards["Brand new"]
    assert (v["latest_label"], v["missing"], v["gap_text"], v["due_text"]) == ("up to Sep 2026", ["Jul"], "Jul missing", "")
    assert (v["pref_label"], v["accept"]) == ("QFX only", ".qfx,.ofx,.qbo")
    # by calendar month it would be "Jul missing"; by its statement cycles the Aug 13 – Sep 12 one has nothing
    assert (k["missing"], k["gap_text"]) == (["Sep"], "Sep missing")
    assert (k["due_text"], k["due_class"]) == ("Overdue 6 days", "due-overdue")
    assert (k["pref_label"], k["accept"]) == ("PDF only", ".pdf,application/pdf")
    assert (n["latest_label"], n["gap_text"], n["accept"]) == ("", "", upload_page.ALL_ACCEPT)

    html = env.get("uploads-strip").text
    card = html.split(f'id="strip-card-{chk}"')[1].split("</li>")[0]
    assert "Sep missing" in card and "Overdue 6 days" in card and "PDF only" in card and "up to Sep 2026" in card
    assert f'href="uploads?account={chk}#drop-zone"' in card and 'data-accept=".pdf,application/pdf"' in card
    assert "Nothing uploaded yet" in html.split(f'id="strip-card-{fresh}"')[1].split("</li>")[0]
    assert 'id="accounts-strip"' in env.get("uploads").text


def test_strip_gaps_over_a_year_end_and_none_when_continuous(env, clock):
    from app import upload_page
    acct = {"expected_every_months": None, "expected_day": None}
    on = datetime(2026, 10, 18).date()
    d = lambda s: datetime.strptime(s, "%Y-%m-%d").date()
    assert upload_page.missing_months(acct, [d("2025-11-02"), d("2026-02-03")], on) == ["Dec 2025", "Jan"]
    assert upload_page.missing_months(acct, [d("2026-08-02"), d("2026-09-03"), d("2026-10-01")], on) == []
    assert upload_page.missing_months(acct, [], on) == []
    assert upload_page.gap_text(["Jun", "Jul", "Aug"]) == "3 months missing"


# ------------------------------------------------------------------ the page itself (A, D, F, G)

def test_the_page_has_the_new_parts_and_the_old_forms_are_gone(env):
    env.account("Visa", "credit_card")
    page = env.get("uploads").text
    for needle in ('id="drop-zone"', 'id="upload-files"', 'name="files"', "multiple", 'id="file-list"',
                   'id="upload-button"', 'id="upload-bar"', 'id="upload-summary"', 'id="upload-summary-review"',
                   'id="upload-announcer"', 'aria-live="polite"', 'id="upload-tabs"', 'role="tablist"',
                   'id="tab-upload-files"', 'id="tab-manual"', 'role="tabpanel"', 'id="manual-panel"',
                   'action="transaction-manual"', 'id="upload-history-wrap"', 'id="upload-history"',
                   'id="opt-pdf"', 'id="opt-qfx"', 'id="opt-csv"', 'id="skip-confirmation"', 'id="csv-layout-chip"',
                   'id="new-account-fields"', "+ Create new account", "How this works", 'id="accounts-strip"',
                   "Drop statements here, or choose files", "Choose files",
                   "static/pages/upload-logic.js", "static/pages/uploads.js", 'class="uploads-main"'):
        assert needle in page, needle
    for gone in ('id="csv-import-form"', 'id="statement-files"', 'id="csv-default-account"', "Import a CSV<",
                 "CSV import history", "Statement history", "statement-upload.js", "csv-import.js",
                 'id="upload-warning"', 'id="csv-import-warning"', "collapsible-section"):
        assert gone not in page, gone
    assert page.count("<form") >= 2 and page.count('id="upload-form"') == 1
    # the page's data for its script: accounts with their preference, saved layout and skip choice
    data = page.split('id="upload-data"')[1].split(">")[0]
    accounts = json.loads(re.search(r"data-accounts='([^']*)'", data).group(1).replace("&#34;", '"'))
    assert accounts[0]["name"] == "Visa" and accounts[0]["layout"] is None and accounts[0]["pref"] == "both"
    assert not os.path.exists(os.path.join(STATIC, "statement-upload.js"))
    assert not os.path.exists(os.path.join(STATIC, "csv-import.js"))


def test_without_the_script_the_form_still_uploads(env):
    a = env.account("Checking")
    page = env.get("uploads").text
    form = page.split('id="upload-form"')[1].split("</form>")[0]
    tag = page.split('<form id="upload-form"')[1].split(">")[0]
    assert 'method="post"' in tag and 'action="upload"' in tag and 'enctype="multipart/form-data"' in tag
    assert 'name="account_id"' in form and 'name="files"' in form and 'name="skip_confirmation"' in form
    r = env.post("upload", data={"account_id": str(a), "skip_confirmation": "1"}, headers=HTML,
                 files={"files": ("s.pdf", _pdf_bytes(env, "nojs"), "application/pdf")})
    assert r.status_code == 303 and r.headers["location"] == "uploads?notice=upload_sent"
    assert _needs_confirmation(env) == 0
    assert "Your files were sent" in env.get(r.headers["location"]).text
    r = env.post("upload", data={"account_id": "match"}, headers=HTML,
                 files={"files": ("t.pdf", _pdf_bytes(env, "nojs2"), "application/pdf")})
    assert r.headers["location"] == "uploads?notice=upload_some_failed"
    r = env.post("upload", data={"account_id": "__new__"}, headers=HTML,
                 files={"files": ("u.pdf", _pdf_bytes(env, "nojs3"), "application/pdf")})
    assert r.status_code == 400 and "Pick an account" in r.text and "text/html" in r.headers["content-type"]
    # a script still gets JSON
    assert env.post("upload", data={"account_id": "__new__"},
                    files={"files": ("u.pdf", b"%PDF-1.4", "application/pdf")}).status_code == 404
    assert "Your files" not in env.get("uploads?notice=<b>made up</b>").text


def test_preselected_account_and_the_strip_upload_link(env):
    a, b = env.account("Alpha"), env.account("Beta")
    page = env.get(f"uploads?account={b}").text
    top = page.split('id="upload-account"')[1].split("</select>")[0]
    assert re.search(rf'<option value="{b}"[^>]*selected', top) and not re.search(r'value="match" selected', top)
    top = env.get("uploads").text.split('id="upload-account"')[1].split("</select>")[0]
    assert 'value="match" selected' in top                                   # none picked: each file works it out


# ------------------------------------------------------------------ the browser-side helpers (Node)

LOGIC = r"""
const L = require(process.argv[2]);
const accounts = [{ id: 1, name: "Visa", last4: "1234", pref: "pdf" }, { id: 2, name: "Chase", last4: "6789", pref: "qfx" },
                  { id: 3, name: "Amex", last4: "2026", pref: "csv" }];
const g = (kind, name, opts) => L.guessAccount(kind, name, accounts, opts || {});
const out = {
    kinds: ["a.PDF", "b.qfx", "c.OFX", "d.qbo", "e.csv", "f.txt"].map(L.fileKind),
    qfx: g("qfx", "x.qfx", { picked: "match" }), qfxPicked: g("qfx", "x.qfx", { picked: "2" }),
    pdfBy4: g("pdf", "Visa-1234-Sep.pdf", { lastUsed: "2" }), pdfTwo: g("pdf", "1234-2026.pdf", { lastUsed: "2" }),
    pdfLast: g("pdf", "statement.pdf", { lastUsed: "2" }), pdfNone: g("pdf", "statement.pdf", {}),
    pdfPicked: g("pdf", "Visa-1234.pdf", { picked: "3" }), pdfGone: g("pdf", "s.pdf", { lastUsed: "99" }),
    csvCol: g("csv", "x.csv", { picked: "1", hasAccountColumn: true }), csvPicked: g("csv", "x.csv", { picked: "1", hasAccountColumn: false }),
    csvReading: g("csv", "x.csv", { hasAccountColumn: null }), csvNone: g("csv", "x.csv", { hasAccountColumn: false, lastUsed: "1" }),
    hasCol: L.hasAccountColumn(L.parseRows("Bank,x\nDate,Account,Description,Amount\n1,2,3,4\n", 10)),
    noCol: L.hasAccountColumn(L.parseRows("Date,Description,Amount\n", 10)),
    quoted: L.parseRows('a,"b,c","d\n""e"" x"\nf', 5),
    size: [L.formatSize(500), L.formatSize(2048), L.formatSize(3 * 1024 * 1024)],
    summary: L.summarize([{ kind: "pdf", state: "done", imported: 200, duplicates: 5 },
                          { kind: "qfx", state: "review", statements: 1, imported: 12, duplicates: 0 },
                          { kind: "csv", state: "done", imported: 200, duplicates: 4 }]),
    mixed: L.summarize([{ kind: "pdf", state: "processing" }, { kind: "pdf", state: "awaiting", imported: 3 },
                        { kind: "qfx", state: "duplicate" }, { kind: "csv", state: "error" }, { kind: "qfx", state: "cancelled" },
                        { kind: "qfx", state: "done", statements: 2, imported: 7, duplicates: 1 }]),
};
console.log(JSON.stringify(out));
"""


def _node(script, *args, tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js isn't installed")
    f = tmp_path / "harness.js"
    f.write_text(script)
    out = subprocess.run([node, str(f), *args], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_each_file_gets_its_kind_and_account(tmp_path):
    r = _node(LOGIC, os.path.join(STATIC, "upload-logic.js"), tmp_path=tmp_path)
    assert r["kinds"] == ["pdf", "qfx", "qfx", "qfx", "csv", "other"]
    assert r["qfx"] == {"value": "match", "note": "Match by account number"}
    assert r["qfxPicked"]["value"] == "2"
    assert r["pdfBy4"] == {"value": "1", "note": "Last 4 digits in the file name"}
    assert r["pdfTwo"] == {"value": "2", "note": "Used last time"}            # two accounts match: not a guess
    assert r["pdfLast"]["value"] == "2" and r["pdfNone"] == {"value": "", "note": "Pick an account"}
    assert r["pdfPicked"] == {"value": "3", "note": "Picked above"}
    assert r["pdfGone"]["value"] == ""                                      # last time's account is gone
    assert r["csvCol"] == {"value": "acctcol", "note": "From the file's Account column"}
    assert r["csvPicked"]["value"] == "1" and r["csvReading"]["note"].startswith("Reading")
    assert r["csvNone"]["value"] == ""                                      # a CSV never takes a PDF's last account
    assert r["hasCol"] is True and r["noCol"] is False
    assert [x["cells"] for x in r["quoted"]] == [["a", "b,c", 'd\n"e" x'], ["f"]]
    assert r["size"] == ["500 B", "2 KB", "3.0 MB"]


def test_the_summary_line(tmp_path):
    r = _node(LOGIC, os.path.join(STATIC, "upload-logic.js"), tmp_path=tmp_path)
    assert r["summary"]["text"] == ("3 files · 2 statements, 1 CSV · 412 transactions · "
                                    "9 duplicates skipped · 1 needs review")
    assert r["summary"]["review"] == 1
    assert r["mixed"]["text"] == ("6 files · 4 statements · 10 transactions · 1 duplicate skipped · "
                                  "1 needs review · 1 still being read · 1 already uploaded · "
                                  "1 not uploaded · 1 failed")


def test_the_page_scripts_parse():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js isn't installed")
    for name in ("uploads.js", "upload-logic.js"):
        out = subprocess.run([node, "--check", os.path.join(STATIC, name)], capture_output=True, text=True, timeout=60)
        assert out.returncode == 0, (name, out.stderr)


def test_the_page_script_keeps_its_promises():
    """Static checks on uploads.js: storage is guarded, status changes are announced, chips and the drop zone are
    reachable by keyboard, nothing reloads the page."""
    js = open(os.path.join(STATIC, "uploads.js")).read()
    assert "location.reload" not in js
    for call in re.findall(r"localStorage\.\w+\([^)]*\)", js):
        line = next(ln for ln in js.splitlines() if call in ln)
        assert "try" in line, line
    assert 'dropZone.setAttribute("role", "button")' in js and "dropZone.tabIndex = 0" in js
    assert 'e.key === "Enter" || e.key === " "' in js
    assert "announcer.textContent" in js
    assert "uploads-history" in js and "uploads-strip" in js and "statement-progress" in js
