"""Security and robustness fixes from the 2026-09 review."""
import os
import sqlite3

import pytest


# ---- only Home Assistant's ingress proxy may connect -----------------------------------------

def test_refuses_clients_other_than_the_ingress_proxy(make_env):
    env = make_env(client_host="172.30.33.9", TRUSTED_CLIENT_IPS="")
    r = env.get("accounts")
    assert r.status_code == 403 and "172.30.33.9" in r.text
    # the forged identity headers never reach the app, so no user was recorded
    with env.db() as c:
        assert c.execute("SELECT COUNT(*) FROM known_users").fetchone()[0] == 0


def test_accepts_supervisor_ingress_proxy(make_env):
    env = make_env(client_host="172.30.32.2", TRUSTED_CLIENT_IPS="")
    assert env.get("accounts").status_code == 200


def test_trusted_client_ips_option_and_dev_override(make_env):
    assert make_env(client_host="10.1.1.1", TRUSTED_CLIENT_IPS="10.1.1.1").get("accounts").status_code == 200
    assert make_env(client_host="10.9.9.9", TRUSTED_CLIENT_IPS="", ALLOW_ANY_CLIENT="1").get("accounts").status_code == 200


def test_cross_site_posts_are_refused(env):
    a = env.account()
    data = {"id": str(a), "archived": "1", "next": "accounts"}
    assert env.post("account-archive", data=data, headers={"sec-fetch-site": "cross-site"}).status_code == 403
    assert env.post("account-archive", data=data, headers={"sec-fetch-site": "same-site"}).status_code == 403
    with env.db() as c:
        assert c.execute("SELECT is_archived FROM accounts WHERE id=?", (a,)).fetchone()[0] == 0
    assert env.post("account-archive", data=data, headers={"sec-fetch-site": "same-origin"}).status_code in (200, 303)
    assert env.post("account-archive", data={**data, "archived": "0"}).status_code in (200, 303)  # no header: allowed
    # reads are never blocked
    assert env.get("accounts", headers={"sec-fetch-site": "cross-site"}).status_code == 200


# ---- jobs interrupted by a restart -----------------------------------------------------------

def test_restart_turns_stuck_processing_rows_into_restartable_errors(make_env):
    env = make_env()
    a = env.account()
    path = env.pdf(["Statement", "Previous Balance $10.00", "New Balance $10.00"])
    sid = env.insert("statements", user_id="tester", account_id=a, original_filename="s.pdf",
                     pdf_path=path, file_hash="h1", status="processing", current_step="Analyzing with AI")
    uid = env.insert("utility_bills", user_id="tester", provider="Xcel Energy", status="processing")
    tid = env.insert("toll_statements", user_id="tester", status="processing")

    env2 = make_env(tmp=env.dir)  # the app restarts on the same database
    with env2.db() as c:
        for table, rid in (("statements", sid), ("utility_bills", uid), ("toll_statements", tid)):
            row = c.execute(f"SELECT status, error_message FROM {table} WHERE id=?", (rid,)).fetchone()
            assert row["status"] == "error" and "interrupted" in row["error_message"]
    up = env2.get("uploads").text
    assert f"statement-restart?id={sid}" in up
    assert env2.post(f"statement-restart?id={sid}").status_code == 200


# ---- uploads ---------------------------------------------------------------------------------

def test_non_pdf_upload_is_refused(env):
    a = env.account()
    r = env.post("upload", data={"account_id": str(a)},
                 files={"files": ("notes.pdf", b"hello, not a pdf", "application/pdf")})
    res = r.json()["results"][0]
    assert "isn't a PDF" in res["error"]
    with env.db() as c:
        assert c.execute("SELECT COUNT(*) FROM statements").fetchone()[0] == 0


def test_oversized_uploads_are_refused(make_env):
    env = make_env(MAX_PDF_MB="1", MAX_CSV_MB="1")
    a = env.account()
    big_pdf = b"%PDF-1.4\n" + b"0" * (1024 * 1024 + 10)
    res = env.post("upload", data={"account_id": str(a)},
                   files={"files": ("big.pdf", big_pdf, "application/pdf")}).json()["results"][0]
    assert "larger than 1 MB" in res["error"]
    for url in ("utility-upload", "toll-upload"):
        data = {"provider": "Xcel Energy"} if url == "utility-upload" else {}
        r = env.post(url, data=data, files={"files": ("big.pdf", big_pdf, "application/pdf")})
        assert "larger than 1 MB" in r.json()["results"][0]["error"], url
    big_csv = "Date,Description,Amount\n" + "2026-01-01,x,-1.00\n" * 70000
    res = env.post("transactions-import-csv", data={"default_account_id": str(a)},
                   files={"files": ("big.csv", big_csv, "text/csv")},
                   headers={"accept": "application/json"}).json()["results"][0]
    assert res["imported"] == 0 and "larger than 1 MB" in res["fatal_error"]


def test_upload_is_stored_inside_the_users_folder(env, monkeypatch):
    from app import jobs
    monkeypatch.setattr(jobs, "start_job", lambda *a, **k: None)
    import app.routes.upload as up
    monkeypatch.setattr(up, "start_job", lambda *a, **k: None)
    a = env.account()
    env.post("upload", data={"account_id": str(a)}, files={"files": ("s.pdf", b"%PDF-1.4 x", "application/pdf")})
    with env.db() as c:
        path = c.execute("SELECT pdf_path FROM statements").fetchone()[0]
    assert os.path.dirname(path) == os.path.join(env.pending, "tester")


# ---- stored-file paths -----------------------------------------------------------------------

def test_paths_outside_storage_are_never_served_or_deleted(env):
    from app import storage
    outside = os.path.join(env.dir, "secret.txt")
    open(outside, "w").write("do not serve")
    a = env.account()
    sid = env.insert("statements", user_id="tester", account_id=a, original_filename="x.pdf",
                     pdf_path=outside, file_hash="h", status="pending_review")
    view = env.get(f"pdf-view?kind=statement&id={sid}")
    assert view.status_code == 200 and "pdf-page?kind=statement" not in view.text
    assert env.get(f"pdf-page?kind=statement&id={sid}&page=1").status_code == 404
    with env.db() as c:
        storage.forget_pdf("statements", c, [sid], outside)
        assert c.execute("SELECT pdf_path FROM statements WHERE id=?", (sid,)).fetchone()[0] is None
    assert os.path.exists(outside)


def test_user_dir_cannot_escape_storage():
    from app import storage
    assert storage.user_dir("abc123") == os.path.join(storage.PENDING_DIR, "abc123")
    assert storage.user_dir("../../etc") == os.path.join(storage.PENDING_DIR, "______etc")


# ---- database connections and known_users ----------------------------------------------------

def test_with_get_db_closes_the_connection(env):
    from app.db import get_db
    with get_db() as conn:
        conn.execute("SELECT 1")
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")


def test_known_users_is_not_written_on_every_request(env):
    env.get("accounts")
    with env.db() as c:
        c.execute("UPDATE known_users SET last_seen_at = 'sentinel' WHERE id = 'tester'")
    env.get("accounts")
    with env.db() as c:
        assert c.execute("SELECT last_seen_at FROM known_users WHERE id='tester'").fetchone()[0] == "sentinel"
    env.get("accounts", headers={"x-remote-user-name": "Renamed"})
    with env.db() as c:
        row = c.execute("SELECT name, last_seen_at FROM known_users WHERE id='tester'").fetchone()
    assert row["name"] == "Renamed" and row["last_seen_at"] != "sentinel"


def test_vision_work_holds_the_shared_lock(env):
    from app import jobs
    assert jobs.exclusive(jobs.OLLAMA_LOCK.locked) is True
    assert not jobs.OLLAMA_LOCK.locked()


def test_database_backup_download_and_restore(env):
    a = env.account("Kept")
    env.txn(a, "2026-01-05", -1.0, "Backed up")
    r = env.get("admin-storage-download-db")
    assert r.status_code == 200 and r.content[:16] == b"SQLite format 3\x00"
    backup = r.content
    env.txn(a, "2026-01-06", -2.0, "After backup")
    r = env.post("admin-storage-import-db", data={"confirm": "yes"},
                 files={"db_file": ("finance.db", backup, "application/octet-stream")})
    assert r.status_code == 303 and "import_error" not in r.headers["location"]
    with env.db() as c:
        descs = [x[0] for x in c.execute("SELECT description FROM transactions")]
    assert descs == ["Backed up"]
    bad = env.post("admin-storage-import-db", data={"confirm": "yes"},
                   files={"db_file": ("x.db", b"not a database", "application/octet-stream")})
    assert "import_error" in bad.headers["location"]


def test_thirty_day_purge_covers_utility_bills_and_keeps_shared_pdfs(env):
    from app.db import get_db
    from app.purge import purge_soft_deleted
    pdf = env.pdf(["bill"])
    old = "2000-01-01 00:00:00"
    env.insert("utility_bills", user_id="tester", provider="Xcel Energy", status="complete",
               pdf_path=pdf, deleted_at=old)
    sibling = env.insert("utility_bills", user_id="tester", provider="Xcel Energy", status="complete", pdf_path=pdf)
    recent = env.insert("utility_bills", user_id="tester", provider="Xcel Energy", status="complete",
                        deleted_at="2999-01-01 00:00:00")
    with get_db() as conn:
        counts = purge_soft_deleted(conn)
    assert counts["utility_bills"] == 1
    with env.db() as c:
        ids = {r[0] for r in c.execute("SELECT id FROM utility_bills")}
    assert ids == {sibling, recent} and os.path.exists(pdf)  # the live sibling still needs the PDF


def test_csp_and_no_store_on_every_response(env):
    from app import main
    for url in ("dashboard", "static/style.css", "static/base.js"):
        r = env.get(url)
        assert r.headers["content-security-policy"] == main.CSP, url
        assert r.headers["x-content-type-options"] == "nosniff", url
        assert r.headers["cache-control"] == "no-store", url
    assert "script-src 'self';" in main.CSP and "unsafe-eval" not in main.CSP


def test_templates_keep_to_the_csp():
    """No inline scripts, on* handler attributes, javascript: URLs, hx-on or htmx trigger filters (they need eval)."""
    import glob
    import os
    import re
    root = os.path.join(os.path.dirname(__file__), "..", "app", "templates")
    for path in glob.glob(os.path.join(root, "*.html")):
        with open(path, encoding="utf-8") as f:
            html = f.read()
        name = os.path.basename(path)
        assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html), name
        assert not re.search(r"\son[a-z]+\s*=", html), name
        assert "javascript:" not in html and "hx-on" not in html, name
        for trigger in re.findall(r'hx-trigger="([^"]*)"', html):
            assert "[" not in trigger, (name, trigger)
