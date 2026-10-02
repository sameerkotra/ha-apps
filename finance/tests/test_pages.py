"""Every page renders, empty and with data — catches template/route breakage from refactors."""
import os

import pytest

from toll_fixture import SAMPLE_TOTAL, make_pdf as make_toll_pdf

PAGES = [
    "accounts", "whoami", "transactions", "transactions?searched=1", "transfers", "recurring", "categories",
    "dashboard", "dashboard?period=all", "dashboard-compare", "uploads", "review", "recently-deleted",
    "utilities", "utilities?tab=upload", "utility-comparison", "tolls", "tolls?tab=upload",
    "toll-compare", "toll-analyze", "admin-storage",
]


@pytest.mark.parametrize("url", PAGES)
def test_page_renders_when_empty(env, url):
    r = env.get(url)
    assert r.status_code == 200, (url, r.text[:300])


def test_pages_render_with_data(env, fake_vision):
    from app.parser import pipeline as pl
    a = env.account("Checking")
    card = env.account("Card", "credit_card")
    for m in range(1, 7):
        env.txn(a, f"2026-0{m}-03", 2500.0, "PAYROLL ACME", category="Income")
        env.txn(a, f"2026-0{m}-05", -61.20, "NETFLIX.COM", category="Entertainment")
        env.txn(card, f"2026-0{m}-09", 45.10, "GROCERY STORE", category="Groceries")
    path = env.pdf(["Statement", "Previous Balance $1000.00", "New Balance $980.00", "2026-08-05 COFFEE -20.00"])
    sid = env.insert("statements", user_id="tester", account_id=a, original_filename="s.pdf", pdf_path=path,
                     file_hash="h", status="processing", needs_confirmation=1)
    fake_vision([("2026-08-05", -20.0, "COFFEE")])
    pl.process_statement(sid, "tester", a, "http://x", "m")
    for url in PAGES + [f"account?id={a}", f"statement-confirm?id={sid}", f"statement-debug?id={sid}",
                        "dashboard-compare?mode=year", "transactions?searched=1"]:
        r = env.get(url)
        assert r.status_code == 200, (url, r.text[:300])


def test_toll_statement_end_to_end(env, monkeypatch):
    """A toll PDF goes through the real deterministic parser; the AI reading is faked as agreeing."""
    from app.parser import toll_pipeline as tp
    from app.parser.toll_deterministic import extract_toll_deterministic
    from app.parser.toll_vision import TollVisionResult

    monkeypatch.setattr(tp, "extract_vision_toll", lambda pdf, *a, **k: TollVisionResult(
        extraction=extract_toll_deterministic(pdf)[0], raw_response="{}"))
    d = os.path.join(env.pending, "tester", "toll-statements")
    os.makedirs(d, exist_ok=True)
    path = make_toll_pdf(os.path.join(d, "t.pdf"))
    tid = env.insert("toll_statements", user_id="tester", original_filename="t.pdf", pdf_path=path,
                     file_hash="th", status="processing")
    tp.process_toll_statement(tid, "tester", "http://x", "m")
    with env.db() as c:
        st = c.execute("SELECT status, error_message FROM toll_statements WHERE id=?", (tid,)).fetchone()
        total = c.execute("SELECT ROUND(SUM(amount), 2) FROM toll_transactions WHERE statement_id=?", (tid,)).fetchone()[0]
    assert st["status"] in ("complete", "pending_review"), st["error_message"]
    assert abs(total - SAMPLE_TOTAL) < 0.005
    for url in ("tolls", "tolls?period=all", "toll-compare?mode=month&a=2026-07&b=2026-08",
                "toll-compare?mode=year", "toll-analyze?period=all", f"toll-debug?id={tid}",
                f"pdf-view?kind=toll&id={tid}") + ((f"toll-review?id={tid}",) if st["status"] == "pending_review" else ()):
        r = env.get(url)
        assert r.status_code == 200, (url, r.text[:300])


def test_restart_only_applies_to_failed_uploads(env):
    a = env.account()
    path = env.pdf(["x"])
    done = env.insert("statements", user_id="tester", account_id=a, original_filename="d.pdf",
                      pdf_path=path, file_hash="d", status="complete")
    ub = env.insert("utility_bills", user_id="tester", provider="Xcel Energy", status="complete", pdf_path=path)
    tb = env.insert("toll_statements", user_id="tester", status="complete", pdf_path=path)
    assert env.post(f"statement-restart?id={done}").status_code == 400
    assert env.post(f"utility-restart?id={ub}").status_code == 400
    assert env.post(f"toll-restart?id={tb}").status_code == 400


def test_toll_statement_needing_review(env, monkeypatch):
    """When the AI reading misses a pass, the statement waits for review and the review page renders."""
    import copy
    from app.parser import toll_pipeline as tp
    from app.parser.toll_deterministic import extract_toll_deterministic
    from app.parser.toll_vision import TollVisionResult

    def fake_ai(pdf, *a, **k):
        ex = copy.deepcopy(extract_toll_deterministic(pdf)[0])
        ex.cars[0].passes.pop()
        return TollVisionResult(extraction=ex, raw_response="{}")
    monkeypatch.setattr(tp, "extract_vision_toll", fake_ai)
    d = os.path.join(env.pending, "tester", "toll-statements")
    os.makedirs(d, exist_ok=True)
    path = make_toll_pdf(os.path.join(d, "r.pdf"))
    tid = env.insert("toll_statements", user_id="tester", original_filename="r.pdf", pdf_path=path,
                     file_hash="rh", status="processing")
    tp.process_toll_statement(tid, "tester", "http://x", "m")
    with env.db() as c:
        assert c.execute("SELECT status FROM toll_statements WHERE id=?", (tid,)).fetchone()[0] == "pending_review"
    r = env.get(f"toll-review?id={tid}")
    assert r.status_code == 200 and "toll-resolve" in r.text
    assert env.get("tolls?tab=upload").status_code == 200


def test_wipe_all_data_includes_utility_bills_and_tolls(env):
    a = env.account()
    env.txn(a, "2026-01-05", -1.0, "x")
    env.insert("utility_bills", user_id="tester", provider="Xcel Energy", status="complete")
    t = env.insert("toll_statements", user_id="tester", status="complete")
    r = env.get("user-wipe")
    assert "1 utility bill" in r.text and "1 toll statement" in r.text
    assert env.post("user-wipe", data={"confirm_text": "Tester"}).status_code == 303
    with env.db() as c:
        for table in ("accounts", "transactions", "utility_bills", "toll_statements"):
            assert c.execute(f"SELECT COUNT(*) FROM {table} WHERE deleted_at IS NULL").fetchone()[0] == 0, table
    assert env.post("toll-restore", data={"id": str(t)}).status_code in (200, 303)


def test_wipe_then_restore_toll_statement_brings_its_passes_back(env):
    t = env.insert("toll_statements", user_id="tester", status="complete")
    dev = env.insert("toll_devices", user_id="tester", device_id="123")
    env.insert("toll_transactions", user_id="tester", statement_id=t, device_ref=dev,
               occurred_at="2026-08-10 06:30:53", date="2026-08-10", amount=1.25, dedupe_key="k1")
    env.post("user-wipe", data={"confirm_text": "Tester"})
    env.post("toll-restore", data={"id": str(t)})
    with env.db() as c:
        assert c.execute("SELECT COUNT(*) FROM toll_transactions WHERE deleted_at IS NULL").fetchone()[0] == 1
