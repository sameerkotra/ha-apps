"""Skipped possible duplicates and "Insert anyway" on the Review page (SPEC.md section 7)."""
import io


def _run_statement(env, fake_vision, rows, lines, acct=None):
    from app.parser import pipeline as pl
    acct = acct or env.account()
    path = env.pdf(["Monthly statement for your account", "Account activity for the period shown below"] + lines)
    sid = env.insert("statements", user_id="tester", account_id=acct, original_filename="s.pdf",
                     pdf_path=path, file_hash=path, status="processing", needs_confirmation=1)
    fake_vision(rows)
    pl.process_statement(sid, "tester", acct, "http://x", "m")
    return acct, sid


def _txns(env, desc):
    with env.db() as c:
        return c.execute("SELECT COUNT(*) FROM transactions WHERE description = ? AND deleted_at IS NULL",
                         (desc,)).fetchone()[0]


def test_repeated_line_is_kept_for_review_and_can_be_inserted(env, fake_vision):
    acct, sid = _run_statement(env, fake_vision, [("2026-08-05", -4.5, "COFFEE"), ("2026-08-05", -4.5, "COFFEE")],
                               ["2026-08-05 COFFEE -4.50", "2026-08-05 COFFEE -4.50"])
    assert _txns(env, "COFFEE") == 1
    uploads = env.get("uploads").text
    assert "1 skipped as duplicate" in uploads
    page = env.get(f"review?statement_id={sid}").text
    assert "Appeared twice in the same statement" in page and "Insert anyway" in page
    with env.db() as c:
        did = c.execute("SELECT id FROM skipped_duplicates WHERE statement_id = ?", (sid,)).fetchone()[0]
    r = env.post("skipped-duplicate-insert", data={"id": str(did), "statement_filter": str(sid)})
    assert r.status_code == 303 and f"statement_id={sid}" in r.headers["location"]
    assert _txns(env, "COFFEE") == 2
    with env.db() as c:
        row = c.execute("SELECT statement_id, review_status FROM transactions WHERE description='COFFEE' "
                        "ORDER BY id DESC").fetchone()
        assert tuple(row) == (sid, "clean")
    assert 'id="skipped"' not in env.get(f"review?statement_id={sid}").text      # an empty section isn't shown
    # a second click does nothing
    env.post("skipped-duplicate-insert", data={"id": str(did)})
    assert _txns(env, "COFFEE") == 2


def test_on_file_match_and_dismiss(env, fake_vision):
    acct = env.account()
    env.txn(acct, "2026-08-05", -20.0, "GROCER")
    _, sid = _run_statement(env, fake_vision, [("2026-08-05", -20.0, "GROCER"), ("2026-08-06", -5.0, "NEW")],
                            ["2026-08-05 GROCER -20.00", "2026-08-06 NEW -5.00"], acct=acct)
    page = env.get("review").text
    assert "a transaction already stored" in page
    with env.db() as c:
        did = c.execute("SELECT id FROM skipped_duplicates WHERE statement_id = ?", (sid,)).fetchone()[0]
    env.post("skipped-duplicate-dismiss", data={"id": str(did)})
    assert "GROCER" not in (env.get("review").text.split('id="skipped"') + [""])[1]
    assert _txns(env, "GROCER") == 1


def test_reextract_replaces_skipped_rows(env, fake_vision):
    acct, sid = _run_statement(env, fake_vision, [("2026-08-05", -4.5, "COFFEE"), ("2026-08-05", -4.5, "COFFEE")],
                               ["2026-08-05 COFFEE -4.50", "2026-08-05 COFFEE -4.50"])
    fake_vision([("2026-08-05", -4.5, "COFFEE")])
    env.post(f"statement-reextract?id={sid}", data={"notes": ""})
    env.wait_status("statements", sid)
    with env.db() as c:
        assert c.execute("SELECT COUNT(*) FROM skipped_duplicates WHERE statement_id = ?", (sid,)).fetchone()[0] == 0


def test_csv_duplicates_are_kept_for_review(env):
    acct = env.account("Checking")
    env.txn(acct, "2026-08-01", -9.0, "ALREADY")
    csv = "Date,Description,Amount\n2026-08-01,ALREADY,-9.00\n2026-08-02,TWICE,-3.00\n2026-08-02,TWICE,-3.00\n"
    r = env.post("transactions-import-csv", data={"default_account_id": str(acct)}, headers={"accept": "application/json"},
                 files={"files": ("t.csv", io.BytesIO(csv.encode()), "text/csv")})
    assert r.status_code in (200, 303)
    page = env.get("review").text.split('id="skipped"')[1]
    assert "ALREADY" in page and "TWICE" in page and "Appeared twice in the same CSV" in page
    with env.db() as c:
        did = c.execute("SELECT id FROM skipped_duplicates WHERE description = 'TWICE'").fetchone()[0]
    env.post("skipped-duplicate-insert", data={"id": str(did)})
    assert _txns(env, "TWICE") == 2


def test_other_users_cannot_insert(env, fake_vision):
    acct, sid = _run_statement(env, fake_vision, [("2026-08-05", -4.5, "COFFEE"), ("2026-08-05", -4.5, "COFFEE")],
                               ["2026-08-05 COFFEE -4.50", "2026-08-05 COFFEE -4.50"])
    with env.db() as c:
        did = c.execute("SELECT id FROM skipped_duplicates").fetchone()[0]
    env.post("skipped-duplicate-insert", data={"id": str(did)},
             headers={"x-remote-user-id": "other", "x-remote-user-name": "Other"})
    assert _txns(env, "COFFEE") == 1


def test_no_insert_into_a_deleted_statement_or_csv_import(env, fake_vision):
    """The list hides skipped rows whose statement or CSV import was deleted; inserting one by id must refuse too
    (it used to check only the account, attaching a transaction to a deleted parent)."""
    acct, sid = _run_statement(env, fake_vision, [("2026-08-05", -4.5, "COFFEE"), ("2026-08-05", -4.5, "COFFEE")],
                               ["2026-08-05 COFFEE -4.50", "2026-08-05 COFFEE -4.50"])
    csv = "Date,Description,Amount\n2026-08-02,TWICE,-3.00\n2026-08-02,TWICE,-3.00\n"
    env.post("transactions-import-csv", data={"default_account_id": str(acct)}, headers={"accept": "application/json"},
             files={"files": ("t.csv", io.BytesIO(csv.encode()), "text/csv")})
    with env.db() as c:
        coffee = c.execute("SELECT id FROM skipped_duplicates WHERE statement_id = ?", (sid,)).fetchone()[0]
        twice, ci = c.execute("SELECT id, csv_import_id FROM skipped_duplicates WHERE description = 'TWICE'").fetchone()
        c.execute("UPDATE statements SET deleted_at = datetime('now') WHERE id = ?", (sid,))
        c.execute("UPDATE csv_imports SET deleted_at = datetime('now') WHERE id = ?", (ci,))
    env.post("skipped-duplicate-insert", data={"id": str(coffee)})
    env.post("skipped-duplicate-insert", data={"id": str(twice)})
    assert (_txns(env, "COFFEE"), _txns(env, "TWICE")) == (1, 1)
    with env.db() as c:
        assert {r[0] for r in c.execute("SELECT status FROM skipped_duplicates")} == {"skipped"}


def _three_skipped(env):
    acct = env.account("Checking")
    for d in ("2026-08-01", "2026-08-02", "2026-08-03"):
        env.txn(acct, d, -5.0, "COFFEE " + d)
    csv = "".join(f"{d},COFFEE {d},-5.00\n" for d in ("2026-08-01", "2026-08-02", "2026-08-03"))
    env.post("transactions-import-csv", data={"default_account_id": str(acct)}, headers={"accept": "application/json"},
             files={"files": ("t.csv", io.BytesIO(("Date,Description,Amount\n" + csv).encode()), "text/csv")})
    with env.db() as c:
        ids = [r[0] for r in c.execute("SELECT id FROM skipped_duplicates WHERE status = 'skipped' ORDER BY date")]
    assert len(ids) == 3
    return acct, ids


def _status(env):
    with env.db() as c:
        return {r["id"]: r["status"] for r in c.execute("SELECT id, status FROM skipped_duplicates")}


def test_bulk_insert_the_ticked_rows(env):
    acct, ids = _three_skipped(env)
    r = env.post("skipped-duplicates-bulk", data={"action": "insert", "ids": [str(ids[0]), str(ids[2])]})
    assert r.status_code in (200, 303)
    assert _status(env) == {ids[0]: "inserted", ids[1]: "skipped", ids[2]: "inserted"}
    assert _txns(env, "COFFEE 2026-08-01") == 2 and _txns(env, "COFFEE 2026-08-02") == 1
    # the page still lists only the one left
    page = env.get("review").text.split('id="skipped"')[1]
    assert "COFFEE 2026-08-02" in page and "COFFEE 2026-08-01" not in page


def test_bulk_keep_skipped(env):
    acct, ids = _three_skipped(env)
    env.post("skipped-duplicates-bulk", data={"action": "dismiss", "ids": [str(i) for i in ids]})
    assert set(_status(env).values()) == {"dismissed"}
    assert 'id="skipped"' not in env.get("review").text and "All clear" in env.get("review").text
    assert _txns(env, "COFFEE 2026-08-01") == 1


def test_bulk_passes_over_rows_not_on_the_list(env):
    acct, ids = _three_skipped(env)
    env.post("skipped-duplicates-bulk", data={"action": "dismiss", "ids": [str(ids[0])]})
    # someone else can't touch these; an already-resolved one and a bad action do nothing
    env.post("skipped-duplicates-bulk", data={"action": "insert", "ids": [str(i) for i in ids]},
             headers={"x-remote-user-id": "other", "x-remote-user-name": "Other"})
    assert _status(env) == {ids[0]: "dismissed", ids[1]: "skipped", ids[2]: "skipped"}
    env.post("skipped-duplicates-bulk", data={"action": "explode", "ids": [str(ids[1])]})
    env.post("skipped-duplicates-bulk", data={"action": "insert"})              # nothing ticked
    assert _status(env)[ids[1]] == "skipped"
    env.post("skipped-duplicates-bulk", data={"action": "insert", "ids": [str(i) for i in ids]})
    assert _status(env) == {ids[0]: "dismissed", ids[1]: "inserted", ids[2]: "inserted"}
    assert _txns(env, "COFFEE 2026-08-01") == 1                                  # the dismissed one stayed out


def test_review_page_has_the_bulk_controls(env):
    _three_skipped(env)
    page = env.get("review").text.split('id="skipped"')[1]
    for needle in ('action="skipped-duplicates-bulk', 'class="check-all"', 'name="ids"', 'value="insert"',
                   'value="dismiss"', 'formaction="skipped-duplicate-insert', 'formaction="skipped-duplicate-dismiss'):
        assert needle in page, needle
