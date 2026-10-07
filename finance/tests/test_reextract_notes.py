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


def test_card_signs_flipped_when_the_payments_prove_them_backwards(env, capture_vision):
    """No printed balances to go by: every "PAYMENT - THANK YOU" line came out positive and the purchases
    negative, so the model signed the card like a bank account (a real statement: money in and out swapped)."""
    calls, setter = capture_vision
    from app.parser import pipeline as pl
    acct, sid = _statement(env, ["2026-09-05 MOBILE PAYMENT - THANK YOU 17.58", "2026-09-05 AMAZON MARKETPLACE 17.58",
                                 "2026-09-13 SUBWAY AURORA CO 10.25", "2026-09-14 MOBILE PAYMENT - THANK YOU 10.25",
                                 "2026-09-25 AMAZON MARKETPLACE 14.01"], type_="credit_card")
    setter([("2026-09-05", 17.58, "MOBILE PAYMENT - THANK YOU"), ("2026-09-05", -17.58, "AMAZON MARKETPLACE"),
            ("2026-09-13", -10.25, "SUBWAY AURORA CO"), ("2026-09-14", 10.25, "MOBILE PAYMENT - THANK YOU"),
            ("2026-09-25", 14.01, "AMAZON MARKETPLACE REFUND")])
    pl.process_statement(sid, "tester", acct, "http://x", "m")
    with env.db() as c:
        rows = c.execute("SELECT description, amount FROM transactions WHERE statement_id=? ORDER BY id", (sid,)).fetchall()
        st = c.execute("SELECT duplicate_transactions FROM statements WHERE id=?", (sid,)).fetchone()
    assert [r["amount"] for r in rows] == [-17.58, 17.58, 10.25, -10.25, -14.01]
    assert "Signs flipped" in (st["duplicate_transactions"] or "")
    from app.routes.upload import statement_flows
    with env.db() as c:
        flow = statement_flows(c, "tester", sid)[sid]
    assert (round(flow["money_in"], 2), round(flow["money_out"], 2)) == (41.84, 27.83)   # payments + refund in, charges out


def test_card_signs_from_a_credit_balance(env, capture_vision):
    """A card that ends in credit prints its new balance as "-$14.01" or "$14.01 CR" — negative, so a
    backwards reading is caught by the balances (it used to read as +14.01 and look reconciled)."""
    calls, setter = capture_vision
    from app.parser import pipeline as pl
    for new_balance in ("New Balance -$14.01", "New Balance $14.01 CR", "New Balance ($14.01)"):
        acct, sid = _statement(env, ["Previous Balance $0.00", new_balance, "2026-09-05 SHOP 20.00",
                                     "2026-09-06 PAYMENT 20.00", "2026-09-25 SHOP REFUND 14.01"], type_="credit_card")
        setter([("2026-09-05", -20.0, "SHOP"), ("2026-09-06", 20.0, "PAYMENT"), ("2026-09-25", 14.01, "SHOP REFUND")])
        pl.process_statement(sid, "tester", acct, "http://x", "m")
        with env.db() as c:
            amounts = [r[0] for r in c.execute("SELECT amount FROM transactions WHERE statement_id=? ORDER BY id", (sid,))]
            st = c.execute("SELECT balance_mismatch FROM statements WHERE id=?", (sid,)).fetchone()
        assert amounts == [20.0, -20.0, -14.01], new_balance
        assert st["balance_mismatch"] is None, new_balance


def test_card_payments_already_negative_are_left_alone(env, capture_vision):
    calls, setter = capture_vision
    from app.parser import pipeline as pl
    acct, sid = _statement(env, ["2026-09-05 AUTOPAY PAYMENT 30.00", "2026-09-06 SHOP 30.00", "2026-09-07 CAFE 4.50"],
                           type_="credit_card")
    setter([("2026-09-05", -30.0, "AUTOPAY PAYMENT"), ("2026-09-06", 30.0, "SHOP"), ("2026-09-07", 4.5, "CAFE")])
    pl.process_statement(sid, "tester", acct, "http://x", "m")
    with env.db() as c:
        amounts = [r[0] for r in c.execute("SELECT amount FROM transactions WHERE statement_id=? ORDER BY id", (sid,))]
    assert amounts == [-30.0, 30.0, 4.5]


def test_balance_labels_keep_a_credit_balance_negative():
    from app.parser.deterministic import extract_balances
    assert extract_balances("Previous Balance $0.00\nNew Balance -$14.01") == (0.0, -14.01)
    assert extract_balances("Previous Balance: $0.00  New Balance $14.01 CR") == (0.0, -14.01)
    assert extract_balances("Previous Balance +$10.00\nNew Balance ($14.01)") == (10.0, -14.01)
    assert extract_balances("Previous Balance $-3.00\nNew Balance $1,234.56") == (-3.0, 1234.56)
    assert extract_balances("Previous Balance $100.00\nNew Balance $150.00") == (100.0, 150.0)
    assert extract_balances("no balances here") == (None, None)


# The statement from the report (2026-09): 13 Amazon/Subway/Carter's charges and 9 mobile payments, a 14.01 refund.
_REPORTED = [
    ("2026-09-05", 17.58, "MOBILE PAYMENT - THANK YOU"), ("2026-09-05", -17.58, "AMAZON MARKETPLACE NA PA AMZN.COM/BILL WA MERCHANDISE"),
    ("2026-09-09", 21.60, "MOBILE PAYMENT - THANK YOU"), ("2026-09-10", -21.60, "carters, Inc. 000000998 Atlanta GA 8773330117"),
    ("2026-09-13", -10.25, "SUBWAY AURORA CO 303-627-9468"), ("2026-09-13", -30.80, "AMAZON MARKETPLACE NA PA AMZN.COM/BILL WA MERCHANDISE"),
    ("2026-09-14", 41.05, "MOBILE PAYMENT - THANK YOU"), ("2026-09-15", -8.81, "AMAZON.COM AMZN.COM/BILL WA BOOK STORES"),
    ("2026-09-16", 8.81, "MOBILE PAYMENT - THANK YOU"), ("2026-09-16", -32.70, "AMAZON MARKETPLACE NA PA AMZN.COM/BILL WA MERCHANDISE"),
    ("2026-09-17", -35.14, "AMAZON MARKETPLACE NA PA AMZN.COM/BILL WA MERCHANDISE"), ("2026-09-18", 67.11, "MOBILE PAYMENT - THANK YOU"),
    ("2026-09-18", 0.03, "MOBILE PAYMENT - THANK YOU"), ("2026-09-19", 41.26, "MOBILE PAYMENT - THANK YOU"),
    ("2026-09-19", -18.66, "AMAZON MARKETPLACE NA PA AMZN.COM/BILL WA MERCHANDISE"), ("2026-09-19", -21.90, "AMAZON MARKETPLACE NA PA AMZN.COM/BILL WA MERCHANDISE"),
    ("2026-09-22", -25.14, "AMAZON MARKETPLACE NA PA AMZN.COM/BILL WA MERCHANDISE"), ("2026-09-23", 25.14, "MOBILE PAYMENT - THANK YOU"),
    ("2026-09-23", -11.10, "AMAZON MARKETPLACE NA PA AMZN.COM/BILL WA MERCHANDISE"), ("2026-09-24", 46.49, "MOBILE PAYMENT - THANK YOU"),
    ("2026-09-24", -35.39, "AMAZON MARKETPLACE NA PA AMZN.COM/BILL WA MERCHANDISE"), ("2026-09-25", 14.01, "AMAZON MARKETPLACE NA PA AMZN.COM/BILL WA MERCHANDISE"),
]


@pytest.mark.parametrize("balances", [
    [],                                                        # no balances found
    ["Previous Balance $0.00", "New Balance $14.01"],          # a credit balance printed without a sign the search sees
    ["Previous Balance $14.01", "New Balance $0.00"],          # owed 14.01 before, nothing now
    ["Previous Balance $0.00", "New Balance $14.01-"],         # trailing minus
])
def test_the_reported_statement_comes_out_the_right_way_round(env, capture_vision, balances):
    calls, setter = capture_vision
    from app.parser import pipeline as pl
    from app.routes.upload import statement_flows
    acct, sid = _statement(env, balances + [f"{d} {desc} {abs(a):.2f}" for d, a, desc in _REPORTED], type_="credit_card")
    setter(_REPORTED)
    pl.process_statement(sid, "tester", acct, "http://x", "m")
    with env.db() as c:
        flow = statement_flows(c, "tester", sid)[sid]
        st = c.execute("SELECT balance_mismatch, duplicate_transactions FROM statements WHERE id=?", (sid,)).fetchone()
        refund = c.execute("SELECT amount FROM transactions WHERE statement_id=? AND date='2026-09-25'", (sid,)).fetchone()[0]
    assert (round(flow["money_in"], 2), round(flow["money_out"], 2)) == (283.08, 269.07)
    assert refund == -14.01
    assert st["balance_mismatch"] is None
    assert "Signs flipped" in (st["duplicate_transactions"] or "")
