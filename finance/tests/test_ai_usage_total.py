"""Admin → AI usage: the running total of every AI request (SPEC.md section 21.3)."""
import glob
import json
import os
import sqlite3

from test_ai_providers import FakeAI, _set  # noqa: F401  (fixture helpers)

import pytest

WIFE = {"x-remote-user-id": "wife", "x-remote-user-name": "Wife"}
MIGRATIONS = sorted(glob.glob(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                           "migrations", "*.sql")))


@pytest.fixture
def fake_ai(monkeypatch):
    from app import ai_client
    fake = FakeAI()
    monkeypatch.setattr(ai_client, "_post", fake)
    monkeypatch.setattr(ai_client, "_sleep", lambda s: None)
    return fake


def _log(env):
    from app import ai_usage
    ai_usage.flush()                                      # the background writer may not have run yet
    with env.db() as c:
        return [dict(r) for r in c.execute("SELECT * FROM ai_usage_log ORDER BY id")]


def _statement(env, acct, ai_usage=None):
    path = env.pdf(["Previous Balance $100.00", "New Balance $80.00", "2026-08-05 ODD SHOP -20.00"])
    return env.insert("statements", user_id="tester", account_id=acct, original_filename="s.pdf",
                      pdf_path=path, file_hash=path, status="processing", ai_usage=ai_usage)


def _openai_answer():
    return (200, json.dumps({"choices": [{"message": {"content": json.dumps(
        {"transactions": [{"date": "2026-08-05", "amount": -20.0, "description": "ODD SHOP"}]})}}],
        "usage": {"prompt_tokens": 1000, "completion_tokens": 100}}), {})


def test_total_keeps_every_run_not_just_the_last(env, fake_ai, monkeypatch):
    from app import jobs
    from app.parser import vision
    monkeypatch.setattr(vision, "rasterize_pdf", lambda pdf: (env.dir + "/nothing", []))
    _set(env, ai_provider="openai", ai_model="gpt-vision", ai_api_key="sk-test-x",
         price_input_per_million="3", price_output_per_million="15")
    acct = env.account()
    sid = _statement(env, acct)
    for _ in range(2):                                    # processed, then re-run
        fake_ai.queue = [_openai_answer()]
        jobs._run("statement", sid, "tester", acct)
        with env.db() as c:                               # as if the PDF had been kept for a re-run
            c.execute("UPDATE statements SET pdf_path = ?, status = 'processing' WHERE id = ?",
                      (env.pdf(["Previous Balance $100.00", "New Balance $80.00", "2026-08-05 ODD SHOP -20.00"]), sid))
    with env.db() as c:
        on_row = json.loads(c.execute("SELECT ai_usage FROM statements WHERE id=?", (sid,)).fetchone()[0])
    assert len(on_row) == 2                               # the debug page: the last run only
    log = _log(env)
    assert len(log) == 4 and all(r["earlier"] == 0 for r in log)   # the total: both runs
    assert {r["provider"] for r in log} == {"openai"}
    assert sum(r["input_tokens"] for r in log) == 2 * (1000 + 900)

    from app import ai_usage
    t = ai_usage.totals()
    assert t["overall"]["requests"] == 4 and t["overall"]["total_tokens"] == 2 * (1100 + 940)
    assert t["overall"]["cost"] == pytest.approx((3800 * 3 + 280 * 15) / 1e6)
    assert {p["name"] for p in t["purposes"]} == {"Extraction", "Categorization"}

    # deleting the statement doesn't lower the total
    with env.db() as c:
        c.execute("DELETE FROM transactions WHERE statement_id = ?", (sid,))
        c.execute("DELETE FROM statements WHERE id = ?", (sid,))
    assert ai_usage.totals()["overall"]["requests"] == 4


def test_failed_requests_count_with_no_tokens(env):
    from app import ai_usage
    from app.categorize import categorize_via_llm
    categorize_via_llm("http://x", "m", ["SOMETHING ODD"])      # the network is refused in tests
    log = _log(env)
    assert len(log) == 1 and log[0]["error"] and log[0]["input_tokens"] is None
    t = ai_usage.totals()
    assert t["overall"]["requests"] == 1 and t["overall"]["failed"] == 1 and t["overall"]["total_tokens"] == 0


def test_admin_page(env):
    from app import ai_usage
    page = env.get("ai-usage").text
    assert "<h1>AI usage</h1>" in page and "No AI requests yet" in page
    assert 'aria-current="page">AI usage' in page
    ai_usage.add("Extraction (attempt 1)", "llava:13b", 1234, 56, 12.5, provider="ollama")
    ai_usage.add("Extraction (attempt 2)", "llava:13b", 1000, 44, 10, provider="ollama")
    ai_usage.add("Write SQL", "qwen", 300, 20, 2, provider="ollama")
    page = env.get("ai-usage").text
    assert "All time" in page and "2,534" in page and "2,654" in page      # input, total
    assert "By month" in page and "By kind of request" in page and "By model" in page
    assert ">Extraction<" in page and "(attempt" not in page and "llava:13b" in page
    assert "a local model costs nothing" in page
    # linked from the admin tabs and the More panel; admins only
    assert 'href="ai-usage' in env.get("dashboard").text
    assert env.get("ai-usage", headers=WIFE).status_code == 403
    assert "ai-usage" not in env.get("dashboard", headers=WIFE).text
    # not visible to the Query tab
    from app import query_engine as qe
    assert "ai_usage_log" in qe.HIDDEN_TABLES


def test_busy_database_keeps_the_request_for_later(env, monkeypatch):
    from app import ai_usage
    real_connect = sqlite3.connect

    def busy(*a, **k):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(ai_usage.sqlite3, "connect", busy)
    ai_usage.add("Categorization", "m", 10, 2, 1)          # never raises
    assert len(ai_usage._pending) == 1
    monkeypatch.setattr(ai_usage.sqlite3, "connect", real_connect)
    assert ai_usage.totals()["overall"]["requests"] == 1 and ai_usage._pending == []


def _old_database(path, upto=23):
    """A database as an older version left it (migrations up to `upto`), with stored per-document usage."""
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT (datetime('now')))")
    for p in MIGRATIONS:
        if int(os.path.basename(p).split("_", 1)[0]) <= upto:
            c.executescript(open(p).read())
    c.execute("INSERT INTO accounts (user_id, name, type) VALUES ('tester', 'Old checking', 'checking')")
    good = json.dumps([{"purpose": "Extraction (attempt 1)", "model": "llava", "input_tokens": 5000,
                        "output_tokens": 600, "seconds": 40.2, "error": ""},
                       {"purpose": "Categorization", "model": "llava", "input_tokens": 300,
                        "output_tokens": 50, "seconds": 3, "error": ""}])
    if upto >= 22:
        for usage in (good, "not json", '{"an": "object"}', '[1, "x", null]', None):
            c.execute("INSERT INTO statements (user_id, account_id, original_filename, status, uploaded_at, ai_usage) "
                      "VALUES ('tester', 1, 's.pdf', 'complete', '2026-03-04 10:00:00', ?)", (usage,))
        c.execute("INSERT INTO csv_imports (user_id, original_filename, imported_at, row_count, ai_usage) VALUES "
                  "('tester', 'c.csv', '2026-04-01 09:00:00', 1, ?)",
                  (json.dumps([{"purpose": "Categorization", "model": "qwen", "input_tokens": 100,
                                "output_tokens": "lots", "seconds": 1}]),))
    else:
        c.execute("INSERT INTO statements (user_id, account_id, original_filename, status) "
                  "VALUES ('tester', 1, 's.pdf', 'complete')")
    c.commit()
    c.close()


def test_upgrading_an_older_database_starts_from_its_stored_usage(make_env):
    import tempfile
    tmp = tempfile.mkdtemp()
    _old_database(os.path.join(tmp, "finance.db"))
    env = make_env(tmp=tmp)                                # start-up runs migration 0024
    log = _log(env)
    assert [(r["purpose"], r["input_tokens"], r["output_tokens"], r["earlier"]) for r in log] == [
        ("Extraction (attempt 1)", 5000, 600, 1), ("Categorization", 300, 50, 1), ("Categorization", 100, None, 1)]
    assert log[0]["at"] == "2026-03-04 10:00:00"
    page = env.get("ai-usage").text
    assert "6,050" in page and "already in the app when the running total" in page
    assert "2026-03" in page and "2026-04" in page
    # a restart doesn't copy them again
    env2 = make_env(tmp=tmp)
    assert len(_log(env2)) == 3


def test_importing_an_older_backup_works_straight_away(env, fake_ai):
    from app import ai_usage
    ai_usage.add("Write SQL", "m", 10, 1, 1)
    for upto in (23, 21):                                  # with and without stored per-document usage
        path = os.path.join(env.dir, f"old-{upto}.db")
        _old_database(path, upto)
        with open(path, "rb") as f:
            r = env.post("admin-storage-import-db", data={"confirm": "yes"},
                         files={"db_file": ("old.db", f.read(), "application/octet-stream")})
        assert r.status_code == 303 and "imported=1" in r.headers["location"], r.headers["location"]
        page = env.get("ai-usage")
        assert page.status_code == 200 and "isn't available" not in page.text
        # the next AI request goes into the imported database's total
        ai_usage.add("Categorization", "m", 7, 3, 1)
        t = ai_usage.totals()
        expected_earlier = 3 if upto == 23 else 0
        assert t["overall"]["requests"] == expected_earlier + 1
        with env.db() as c:
            assert c.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == len(MIGRATIONS)


def test_page_explains_a_database_without_the_total(env, monkeypatch):
    from app import ai_usage
    monkeypatch.setattr(ai_usage, "totals", lambda: None)
    assert "Restart the app" in env.get("ai-usage").text


def test_migration_is_safe_to_run_again():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT (datetime('now')))")
    for p in MIGRATIONS:
        c.executescript(open(p).read())
    c.execute("INSERT INTO ai_usage_log (purpose, earlier) VALUES ('x', 1)")
    c.execute("DELETE FROM schema_version WHERE version = 24")
    c.executescript(open(next(p for p in MIGRATIONS if os.path.basename(p).startswith("0024_"))).read())
    assert c.execute("SELECT COUNT(*) FROM ai_usage_log").fetchone()[0] == 1


def test_a_request_inside_an_open_transaction_never_waits(env):
    """A CSV import asks for categories before it commits: recording that request mustn't wait for
    (or deadlock on) the import's own transaction; it lands in the total once the import commits."""
    import time
    from app import ai_usage
    from app.db import get_db
    conn = get_db()
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("INSERT INTO accounts (user_id, name, type) VALUES ('tester', 'Mid-import', 'checking')")
    started = time.monotonic()
    ai_usage.add("Categorization", "m", 40, 4, 1)
    assert time.monotonic() - started < 0.5
    conn.commit()
    conn.close()
    assert ai_usage.totals()["overall"]["requests"] == 1
