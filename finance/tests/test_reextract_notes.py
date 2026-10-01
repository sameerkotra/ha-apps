"""Re-extract with notes, and credit-card signs (SPEC.md sections 4 and 5)."""
import pytest


@pytest.fixture
def capture_vision(monkeypatch):
    """Fake vision that records account_type and notes, returning canned rows."""
    calls = []

    def setter(rows):
        from app.parser import pipeline as pl
        from app.parser.vision import ParsedTransaction, VisionResult

        def fake(pdf, url, model, on_step=None, account_type=None, notes=""):
            calls.append({"account_type": account_type, "notes": notes})
            return VisionResult(transactions=[ParsedTransaction(d, a, t) for d, a, t in rows], raw_response="{}")
        monkeypatch.setattr(pl, "extract_vision", fake)
    return calls, setter


def _statement(env, lines, type_="checking", needs_confirmation=1):
    acct = env.account("Card" if type_ == "credit_card" else "Checking", type_=type_)
    path = env.pdf(lines)
    sid = env.insert("statements", user_id="tester", account_id=acct, original_filename="s.pdf",
                     pdf_path=path, file_hash=path, status="processing", needs_confirmation=needs_confirmation)
    return acct, sid


def test_prompts_follow_account_type_and_notes():
    from app.parser.vision import statement_prompt
    assert "POSITIVE for anything that\nincreases the balance owed" in statement_prompt("credit_card")
    assert "negative for money leaving" in statement_prompt("checking")
    p = statement_prompt("credit_card", "  The total was read as money in.  ")
    assert p.endswith("The total was read as money in.") and "override the rules above" in p
    assert "A person checked" not in statement_prompt("checking", "   ")


def test_reextract_sends_notes_and_saves_them(env, capture_vision):
    calls, setter = capture_vision
    from app.parser import pipeline as pl
    acct, sid = _statement(env, ["Previous Balance $100.00", "New Balance $120.00", "2026-08-05 SHOP 20.00"],
                           type_="credit_card")
    setter([("2026-08-05", 20.0, "SHOP")])
    pl.process_statement(sid, "tester", acct, "http://x", "m")
    assert calls[-1] == {"account_type": "credit_card", "notes": ""}
    page = env.get("uploads").text
    assert "reextract-open" in page and "reextract-modal" in page
    r = env.post(f"statement-reextract?id={sid}", data={"notes": "Purchases are money out on this card."})
    assert r.status_code == 200
    env.wait_status("statements", sid)
    assert calls[-1]["notes"] == "Purchases are money out on this card."
    with env.db() as c:
        assert c.execute("SELECT extraction_notes FROM statements WHERE id=?", (sid,)).fetchone()[0] \
            == "Purchases are money out on this card."
    assert "Purchases are money out on this card." in env.get(f"statement-debug?id={sid}").text
    assert 'data-notes="Purchases are money out on this card."' in env.get("uploads").text
    # an empty re-extract clears them
    env.post(f"statement-reextract?id={sid}", data={"notes": ""})
    env.wait_status("statements", sid)
    assert calls[-1]["notes"] == ""


def test_card_signs_flipped_when_the_balances_prove_them_backwards(env, capture_vision):
    calls, setter = capture_vision
    from app.parser import pipeline as pl
    # Owed went 100 -> 150: purchases 70, payment 20. The model signed them like a bank account.
    acct, sid = _statement(env, ["Previous Balance $100.00", "New Balance $150.00",
                                 "2026-08-05 SHOP 70.00", "2026-08-10 PAYMENT THANK YOU -20.00"], type_="credit_card")
    setter([("2026-08-05", -70.0, "SHOP"), ("2026-08-10", 20.0, "PAYMENT THANK YOU")])
    pl.process_statement(sid, "tester", acct, "http://x", "m")
    with env.db() as c:
        amounts = dict(c.execute("SELECT description, amount FROM transactions WHERE statement_id=?", (sid,)).fetchall())
        st = c.execute("SELECT balance_mismatch, duplicate_transactions FROM statements WHERE id=?", (sid,)).fetchone()
    assert amounts == {"SHOP": 70.0, "PAYMENT THANK YOU": -20.0}
    assert st["balance_mismatch"] is None and "Signs flipped" in (st["duplicate_transactions"] or "")


def test_card_signs_left_alone_when_they_reconcile_or_checking(env, capture_vision):
    calls, setter = capture_vision
    from app.parser import pipeline as pl
    acct, sid = _statement(env, ["Previous Balance $100.00", "New Balance $150.00",
                                 "2026-08-05 SHOP 70.00", "2026-08-10 PAYMENT -20.00"], type_="credit_card")
    setter([("2026-08-05", 70.0, "SHOP"), ("2026-08-10", -20.0, "PAYMENT")])
    pl.process_statement(sid, "tester", acct, "http://x", "m")
    with env.db() as c:
        amounts = dict(c.execute("SELECT description, amount FROM transactions WHERE statement_id=?", (sid,)).fetchall())
    assert amounts == {"SHOP": 70.0, "PAYMENT": -20.0}
    acct2, sid2 = _statement(env, ["Previous Balance $100.00", "New Balance $50.00", "2026-08-05 SHOP -50.00"])
    setter([("2026-08-05", 50.0, "SHOP")])
    pl.process_statement(sid2, "tester", acct2, "http://x", "m")
    with env.db() as c:
        assert c.execute("SELECT amount FROM transactions WHERE statement_id=?", (sid2,)).fetchone()[0] == 50.0
