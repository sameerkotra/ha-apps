"""Admin → Users → Data: one person's data exported, imported and deleted (SPEC.md section 20a,
PERSON_DATA_SPEC.md §5)."""
import io
import json
import os
import time
import zipfile

WIFE = {"x-remote-user-id": "wife", "x-remote-user-name": "Wife"}
BASE = "api/admin/people"

# Every table that holds a person's rows (electric_meter_details hangs on utility_bills).
TABLES = ("accounts", "statements", "transactions", "csv_imports", "skipped_duplicates", "category_rules",
          "custom_categories", "recurring_overrides", "utility_bills", "toll_cars", "toll_devices", "toll_statements",
          "toll_transactions", "toll_pattern_groups", "toll_patterns", "toll_trip_overrides", "toll_trips")


def _setup(env):
    """Tester with some of everything (a PDF, a transfer pair, a toll statement with a device, a utility bill with
    meter details, rules) and shared access both ways; Wife and Third with their own little bit."""
    for uid, name in (("tester", "Tester"), ("wife", "Wife"), ("third", "Third")):
        env.insert("known_users", id=uid, name=name)
    chk = env.insert("accounts", user_id="tester", name="Checking", type="checking", account_number_last4="1234")
    sav = env.insert("accounts", user_id="tester", name="Savings", type="savings")
    pdf = env.pdf(["Statement"], name="stmt-1.pdf")
    st = env.insert("statements", user_id="tester", account_id=chk, status="complete", file_hash="h-stmt-1",
                    pdf_path=pdf, original_filename="march.pdf", llm_raw_response="{}")
    csv = env.insert("csv_imports", user_id="tester", original_filename="a.csv", row_count=2)
    t1 = env.txn(chk, "2026-03-02", -50.0, "TRANSFER TO SAVINGS", statement_id=st, txn_type="transfer")
    t2 = env.txn(sav, "2026-03-02", 50.0, "TRANSFER FROM CHECKING", csv_import_id=csv, txn_type="transfer")
    with env.db() as c:
        c.execute("UPDATE transactions SET transfer_pair_id = ? WHERE id = ?", (t2, t1))
        c.execute("UPDATE transactions SET transfer_pair_id = ? WHERE id = ?", (t1, t2))
    env.txn(chk, "2026-03-05", -12.34, "COFFEE SHOP", statement_id=st, category="Dining", note="with Bob")
    gone = env.txn(chk, "2026-03-06", -9.99, "DELETED ONE", statement_id=st)
    with env.db() as c:
        c.execute("UPDATE transactions SET deleted_at = datetime('now') WHERE id = ?", (gone,))
    env.insert("skipped_duplicates", user_id="tester", account_id=chk, statement_id=st, date="2026-03-05",
               amount=-12.34, description="COFFEE SHOP", reason="on_file", matched_transaction_id=t1)
    env.insert("category_rules", user_id="tester", pattern="COFFEE", match_type="substring", category="Dining")
    env.insert("custom_categories", user_id="tester", name="Pets")
    env.insert("recurring_overrides", user_id="tester", merchant_pattern="NETFLIX", is_recurring=1)
    bill = env.insert("utility_bills", user_id="tester", utility_type="electric", provider="Power Co", status="complete",
                      file_hash="h-bill", cost=80.5)
    env.insert("electric_meter_details", utility_bill_id=bill, grid_import_total_kwh=400.0)
    car = env.insert("toll_cars", user_id="tester", name="Blue car")
    dev = env.insert("toll_devices", user_id="tester", device_id="1234567", car_id=car)
    ts = env.insert("toll_statements", user_id="tester", status="complete", file_hash="h-toll")
    for i, (when, plaza) in enumerate((("2026-08-10 06:30:53", "SMOKY HILL RD"), ("2026-08-10 06:37:12", "PLAZA A"))):
        env.insert("toll_transactions", user_id="tester", statement_id=ts, device_ref=dev, occurred_at=when,
                   date=when[:10], plaza=plaza, direction="South", amount=1.25 + i, dedupe_key=f"k{i}", raw_line="x")
    grp = env.insert("toll_pattern_groups", user_id="tester", name="Work")
    env.insert("toll_patterns", user_id="tester", name="Morning", signature="SMOKY HILL RD|South > PLAZA A|South",
               window_start_min=360, window_end_min=420, group_id=grp)
    with env.db() as c:
        from app.toll_trips import rebuild_trips
        rebuild_trips(c, "tester")
        key = c.execute("SELECT trip_key FROM toll_trips WHERE user_id = 'tester'").fetchone()[0]
        c.execute("INSERT INTO toll_trip_overrides (user_id, trip_key, first_dedupe_key, pattern_id) "
                  "VALUES ('tester', ?, 'k0', NULL)", (key,))
        c.execute("INSERT INTO user_access (viewer_id, owner_id, granted_by) VALUES ('wife', 'tester', 'tester')")
        c.execute("INSERT INTO user_access (viewer_id, owner_id, granted_by) VALUES ('tester', 'third', 'tester')")
        c.execute("INSERT INTO user_access (viewer_id, owner_id, granted_by) VALUES ('wife', 'third', 'tester')")
    hers = env.account("Wife own", user="wife")
    env.txn(hers, "2026-09-02", -3.0, "HER OWN COFFEE", user="wife")
    env.insert("category_rules", user_id="wife", pattern="TEA", match_type="substring", category="Dining")


def _counts(env, uid):
    with env.db() as c:
        out = {t: c.execute(f"SELECT COUNT(*) FROM {t} WHERE user_id = ?", (uid,)).fetchone()[0] for t in TABLES}
        out["electric_meter_details"] = c.execute(
            "SELECT COUNT(*) FROM electric_meter_details m JOIN utility_bills b ON b.id = m.utility_bill_id "
            "WHERE b.user_id = ?", (uid,)).fetchone()[0]
    return out


def _export(env, uid="tester", **options):
    r = env.post(f"{BASE}/{uid}/data/export", json={"options": options})
    assert r.status_code == 200, r.text
    if not r.headers["content-type"].startswith("application/json"):
        assert r.headers["content-disposition"].startswith('attachment; filename="finance-')
        return r.content
    job = r.json()["job"]
    for _ in range(200):
        st = env.get(f"{BASE}/data/jobs/{job}").json()
        if st["state"] != "running":
            break
        time.sleep(0.05)
    assert st["state"] == "done", st
    f = env.get(f"{BASE}/data/jobs/{job}/file")
    assert f.status_code == 200
    return f.content


def _upload(env, data, headers=None):
    return env.post(f"{BASE}/data/import", files={"file": ("x.zip", data, "application/zip")}, headers=headers or {})


def _import(env, data, uid="tester", mode="add", confirm=""):
    r = _upload(env, data)
    assert r.status_code == 200, r.text
    return env.post(f"{BASE}/{uid}/data/import/{r.json()['token']}", json={"mode": mode, "confirm": confirm})


def _delete(env, uid="tester", confirm="Tester", forget=False):
    return env.post(f"{BASE}/{uid}/data/delete", json={"confirm": confirm, "forget": forget, "choices": {}})


def _craft(tables=None, files=(), schema=None, app="finance"):
    from app import people_data as pdata
    from app.common import person_data
    return person_data.bytes_io_package(app=app, app_version="9.9.9", schema=pdata.SCHEMA if schema is None else schema,
                                        person={"id": "x", "name": "X"}, exported_by="admin",
                                        tables=tables or {"accounts": []}, files=files)


def test_schema_is_the_latest_migration():
    # A migration that changes a person's table needs an UPGRADES step in app/people_data.py; then move this on.
    from app import people_data as pdata
    assert pdata.SCHEMA == 29 and set(pdata.UPGRADES) == {27, 28}


def test_export_delete_import_gives_back_the_same(env):
    _setup(env)
    before = _counts(env, "tester")
    summary = env.get(f"{BASE}/tester/data").json()
    got = {c["key"]: c["count"] for c in summary["counts"]}
    assert got["accounts"] == 2 and got["transactions"] == 4 and got["pdfs"] == 1 and got["access"] == 2
    assert [o["key"] for o in summary["exportOptions"]] == ["pdfs", "deleted", "text"]

    data = _export(env, deleted=True)            # with PDFs: a background job
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = z.namelist()
        manifest = json.loads(z.read("manifest.json"))
        accounts = json.loads(z.read("data/accounts.json"))
    assert manifest["app"] == "finance" and manifest["schema"] == 29 and manifest["person"]["name"] == "Tester"
    assert "files/stmt-1.pdf" in names and "data/toll_trips.json" not in names
    assert all("user_id" not in a for a in accounts)
    assert "user_access" not in str(names) and "saved_reports" not in str(names)

    r = _delete(env)
    assert r.status_code == 200, r.text
    assert all(v == 0 for v in _counts(env, "tester").values())
    assert not os.path.exists(os.path.join(env.pending, "tester"))
    with env.db() as c:
        assert c.execute("SELECT name FROM known_users WHERE id = 'tester'").fetchone()[0] == "Tester"   # kept

    r = _import(env, data)
    assert r.status_code == 200, r.text
    assert "Imported" in r.json()["message"]
    assert _counts(env, "tester") == before
    with env.db() as c:
        st = c.execute("SELECT pdf_path FROM statements WHERE user_id = 'tester'").fetchone()
        assert st["pdf_path"].startswith(os.path.join(env.pending, "tester")) and os.path.isfile(st["pdf_path"])
        pairs = c.execute("SELECT id, transfer_pair_id FROM transactions WHERE transfer_pair_id IS NOT NULL").fetchall()
        assert len(pairs) == 2 and {p[0] for p in pairs} == {p[1] for p in pairs}
        dev = c.execute("SELECT d.car_id, c.name FROM toll_devices d JOIN toll_cars c ON c.id = d.car_id").fetchone()
        assert dev["name"] == "Blue car"
        assert c.execute("SELECT COUNT(*) FROM toll_transactions WHERE trip_id IS NOT NULL").fetchone()[0] == 2
        deleted = c.execute("SELECT deleted_at FROM transactions WHERE description = 'DELETED ONE'").fetchone()
        assert deleted["deleted_at"] is not None
    assert "COFFEE SHOP" in env.get("transactions?searched=1").text


def test_export_options_leave_things_out(env):
    _setup(env)
    data = _export(env, pdfs=False, text=False)   # no PDFs going in: the .zip at once
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert not [n for n in z.namelist() if n.startswith("files/")]
        txns = json.loads(z.read("data/transactions.json"))
        stmts = json.loads(z.read("data/statements.json"))
        passes = json.loads(z.read("data/toll_transactions.json"))
    assert "DELETED ONE" not in str(txns) and len(txns) == 3
    assert stmts[0]["pdf_path"] is None and stmts[0]["pdf_deleted_at"] and "llm_raw_response" not in stmts[0]
    assert "raw_line" not in passes[0]
    preview = _upload(env, data).json()["preview"]
    assert any("PDFs were left out" in w for w in preview["warnings"])
    assert preview["target"] == "tester"


def test_import_into_another_person(env):
    _setup(env)
    before = _counts(env, "tester")
    wife_before = _counts(env, "wife")
    data = _export(env, deleted=True)
    r = _import(env, data, uid="wife")
    assert r.status_code == 200, r.text
    after = _counts(env, "wife")
    for t in before:
        assert after[t] == before[t] + wife_before[t], t
    assert _counts(env, "tester") == before
    with env.db() as c:
        mine = {r[0] for r in c.execute("SELECT id FROM transactions WHERE user_id = 'tester'")}
        hers = {r[0] for r in c.execute("SELECT id FROM transactions WHERE user_id = 'wife'")}
        assert not mine & hers
        acc = {r[0] for r in c.execute("SELECT id FROM accounts WHERE user_id = 'wife'")}
        assert {r[0] for r in c.execute("SELECT account_id FROM transactions WHERE user_id = 'wife'")} <= acc
        path = c.execute("SELECT pdf_path FROM statements WHERE user_id = 'wife'").fetchone()[0]
        assert path.startswith(os.path.join(env.pending, "wife")) and os.path.isfile(path)
        # the grants aren't part of it
        assert c.execute("SELECT COUNT(*) FROM user_access").fetchone()[0] == 3
    assert "COFFEE SHOP" in env.get("transactions?searched=1&as_user=wife").text


def test_add_skips_duplicates_and_replace_needs_the_name(env):
    _setup(env)
    before = _counts(env, "tester")
    data = _export(env, deleted=True)
    r = _import(env, data)
    assert r.status_code == 200, r.text
    assert "already there were skipped" in r.json()["message"]
    assert _counts(env, "tester") == before
    assert not any(r.json()["added"].values())

    up = _upload(env, data).json()
    r = env.post(f"{BASE}/tester/data/import/{up['token']}", json={"mode": "replace", "confirm": "Someone"})
    assert r.status_code == 409
    r = env.post(f"{BASE}/tester/data/import/{up['token']}", json={"mode": "replace", "confirm": " tester "})
    assert r.status_code == 200, r.text
    assert _counts(env, "tester") == before
    assert env.post(f"{BASE}/tester/data/import/{up['token']}", json={"mode": "add"}).status_code == 404  # used up


def test_delete_keep_and_forget(env):
    _setup(env)
    wife_before = _counts(env, "wife")
    assert _delete(env, confirm="Not him").status_code == 409
    assert _counts(env, "tester")["transactions"] == 4
    r = _delete(env, confirm="tester")
    assert r.status_code == 200 and "Deleted" in r.json()["message"]
    with env.db() as c:
        assert c.execute("SELECT COUNT(*) FROM known_users WHERE id = 'tester'").fetchone()[0] == 1
        # both sides of their shared access went; the grant between the others stays
        assert [tuple(r) for r in c.execute("SELECT viewer_id, owner_id FROM user_access")] == [("wife", "third")]
    env.insert("accounts", user_id="third", name="Third's", type="checking")
    r = _delete(env, uid="third", confirm="Third", forget=True)
    assert r.status_code == 200
    with env.db() as c:
        assert c.execute("SELECT COUNT(*) FROM known_users WHERE id = 'third'").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM user_access").fetchone()[0] == 0
    assert _counts(env, "wife") == wife_before                      # others' data untouched
    assert env.get(f"{BASE}/third/data").status_code == 404


def test_delete_leaves_the_shared_things_working(env):
    _setup(env)
    with env.db() as c:
        c.execute("INSERT INTO app_settings (key, value, updated_by) VALUES ('pd_test_key', 'm', 'tester')")
        reports = c.execute("SELECT COUNT(*) FROM saved_reports").fetchone()[0]
    assert _delete(env, uid="tester", confirm="Tester").status_code == 200
    with env.db() as c:
        assert c.execute("SELECT updated_by FROM app_settings WHERE key = 'pd_test_key'").fetchone()[0] == "tester"
        assert c.execute("SELECT COUNT(*) FROM saved_reports").fetchone()[0] == reports
    # Wife's access to Tester went with Tester's data; her own and her access to Third's still work
    env.txn(env.account("Third's", user="third"), "2026-09-03", -9.0, "THIRD SHOP", user="third")
    page = env.get("transactions?searched=1", headers=WIFE)
    assert page.status_code == 200 and "THIRD SHOP" in page.text and "COFFEE SHOP" not in page.text
    assert "HER OWN COFFEE" in env.get("transactions?searched=1&as_user=wife", headers=WIFE).text
    assert "COFFEE SHOP" not in env.get("transactions?searched=1&as_user=tester", headers=WIFE).text
    assert "Tester" in env.get("admin-storage?tab=users").text      # kept: still listed, empty


def test_refused_files(env):
    _setup(env)
    from app import people_data as pdata
    r = _upload(env, _craft(schema=pdata.SCHEMA + 1))
    assert r.status_code == 400 and "newer version" in r.json()["detail"]
    r = _upload(env, _craft(app="calorie_tracker"))
    assert r.status_code == 400 and "not this app" in r.json()["detail"]
    assert _upload(env, b"not a zip").status_code == 400

    # tampered: a row changed after the export (its checksum no longer matches)
    good = _export(env, pdfs=False)
    src, out = zipfile.ZipFile(io.BytesIO(good)), io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for n in src.namelist():
            body = src.read(n)
            if n == "data/transactions.json":
                body = body.replace(b"COFFEE SHOP", b"COFFEE SHOX")
            z.writestr(n, body)
    r = _upload(env, out.getvalue())
    assert r.status_code == 400 and "changed" in r.json()["detail"]

    # rows are checked like the app's own forms (table and row number named), files like an upload
    long_name = [{"id": 1, "name": "A" * 101, "type": "checking"}]
    r = _upload(env, _craft({"accounts": long_name}))
    assert r.status_code == 400 and "accounts, row 1" in r.json()["detail"] and "too long" in r.json()["detail"]
    r = _upload(env, _craft({"accounts": [{"id": 1, "name": "A", "type": "loan"}]}))
    assert r.status_code == 400 and "type" in r.json()["detail"]
    bad_amount = {"accounts": [{"id": 1, "name": "A", "type": "checking"}],
                  "transactions": [{"id": 1, "account_id": 1, "date": "2026-01-01", "amount": "lots", "description": "x"}]}
    r = _upload(env, _craft(bad_amount))
    assert r.status_code == 400 and "transactions, row 1" in r.json()["detail"]
    fake = os.path.join(env.dir, "fake.pdf")
    with open(fake, "wb") as f:
        f.write(b"<html>not a pdf</html>")
    stmt = {"accounts": [{"id": 1, "name": "A", "type": "checking"}],
            "statements": [{"id": 1, "account_id": 1, "status": "complete", "pdf_path": "files/fake.pdf"}]}
    r = _upload(env, _craft(stmt, files=[("fake.pdf", fake)]))
    assert r.status_code == 400 and "isn't a PDF" in r.json()["detail"]
    # a reference to a row that isn't in the file: refused at the import, nothing written
    orphan = {"accounts": [], "transactions": [{"id": 1, "account_id": 7, "date": "2026-01-01", "amount": 1.0,
                                                 "description": "x"}]}
    before = _counts(env, "wife")
    r = _import(env, _craft(orphan), uid="wife")
    assert r.status_code == 400 and "transactions, row 1" in r.json()["detail"]
    assert _counts(env, "wife") == before


def test_admin_only_on_every_endpoint(env):
    _setup(env)
    data = _export(env, pdfs=False)
    token = _upload(env, data).json()["token"]
    calls = [("get", f"{BASE}/tester/data", {}), ("post", f"{BASE}/tester/data/export", {"json": {}}),
             ("get", f"{BASE}/data/jobs/x", {}), ("get", f"{BASE}/data/jobs/x/file", {}),
             ("post", f"{BASE}/data/import", {"files": {"file": ("x.zip", data, "application/zip")}}),
             ("post", f"{BASE}/tester/data/import/{token}", {"json": {"mode": "add"}}),
             ("post", f"{BASE}/tester/data/delete", {"json": {"confirm": "Tester"}})]
    for method, url, kw in calls:
        r = getattr(env, method)(url, headers=WIFE, **kw)
        assert r.status_code == 403, url
        # acting as someone changes nothing: it's the signed-in person who counts
        r = getattr(env, method)(url + "?as_user=tester", headers=WIFE, **kw)
        assert r.status_code == 403, url
    assert _counts(env, "tester")["transactions"] == 4


def test_one_at_a_time_and_not_while_reading(env):
    _setup(env)
    from app.common import person_data
    assert person_data._lock.acquire(blocking=False)
    try:
        assert _delete(env).status_code == 423
    finally:
        person_data._lock.release()
    with env.db() as c:
        c.execute("UPDATE toll_statements SET status = 'processing' WHERE user_id = 'tester'")
    r = _delete(env)
    assert r.status_code == 400 and "being read" in r.json()["detail"]
    assert _counts(env, "tester")["transactions"] == 4


def test_users_page_has_the_data_buttons(env):
    _setup(env)
    page = env.get("admin-storage?tab=users").text
    assert 'id="u-people"' in page and 'data-person-id="wife"' in page and 'id="u-data-import"' in page
    assert "static/common/person-data.js?v=" in page and "static/common/person-data.css?v=" in page
    assert page.index("static/common/ui.js") < page.index("static/common/person-data.js") \
        < page.index("static/pages/people-data.js")
    assert "Admin &rarr; Users" in env.get("user-wipe").text
