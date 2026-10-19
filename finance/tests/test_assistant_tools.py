"""The Household Assistant's questions about statements, due dates, balances and subscriptions (app/tools.py;
FINANCE_ASSISTANT_TOOLS_SPEC.md §7): finance.statements, finance.payments_due, finance.balance_history,
finance.subscription_changes, finance.dates, finance.overview, and what finance.recurring and finance.bills add.

"Today" is frozen by replacing app.statement_due.now (the clock every one of them reads), Monday 19 October 2026.
All people, merchants, numbers and amounts here are invented."""
import json
import os
import re
from datetime import date, datetime

import pytest

from test_tools import call, nack, turn_on

NEW = ("finance.statements", "finance.payments_due", "finance.balance_history", "finance.subscription_changes",
       "finance.dates", "finance.overview")
FULL_NUMBER = "5500005555555559"


@pytest.fixture
def clock(env, monkeypatch):
    from app import statement_due
    state = {"at": datetime(2026, 10, 19, 10, 0)}
    monkeypatch.setattr(statement_due, "now", lambda: state["at"])
    turn_on(env)

    class C:
        def set(self, *a):
            state["at"] = datetime(*a)
    return C()


def account(env, name, type_="checking", user="tester", **extra):
    return env.insert("accounts", user_id=user, name=name, type=type_, **extra)


def monthly(day):
    return {"expected_every_months": 1, "expected_day": day, "expected_set_at": "2026-01-01 00:00:00"}


def card(env, name, due_day=None, user="tester", **extra):
    if due_day is not None:
        extra.update(payment_due_day=due_day, payment_due_set_at="2026-10-01 00:00:00")
    return account(env, name, "credit_card", user=user, **extra)


def statement(env, account_id, uploaded_at, period=None, period_end=None, balance=None, source="pdf", user="tester",
              status="complete"):
    return env.insert("statements", user_id=user, account_id=account_id, uploaded_at=uploaded_at, status=status,
                      statement_period=period, period_end=period_end, new_balance=balance, source=source)


def charges(env, account_id, description, rows, user="tester"):
    for d, amount in rows:
        env.txn(account_id, d, -amount, description, user=user)


def row(conn, account_id):
    return conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()


# ------------------------------------------------------------------ the catalogue

def test_the_new_tools_are_listed_read_only_with_their_args_and_quick_words(env):
    from app import tools
    tools.tools.check()                                                  # still fits on the bus
    spec = {t["name"]: t for t in tools.tools.spec()}
    want = {"finance.statements": {"account", "person"}, "finance.payments_due": {"account", "days", "person"},
            "finance.balance_history": {"account", "month", "months", "person"},
            "finance.subscription_changes": {"merchant", "days", "person"}, "finance.dates": {"days", "person"},
            "finance.overview": {"person"}}
    for name, args in want.items():
        assert set(spec[name]["args"]) == args, name
        assert spec[name]["quick"]["words"], name
        assert all(not a.get("required") for a in spec[name]["args"].values())
    assert spec["finance.payments_due"]["args"]["days"] == {"type": "number", "what": "due within N days", "min": 1,
                                                            "max": 60}
    assert spec["finance.balance_history"]["args"]["month"]["type"] == "month"
    assert spec["finance.subscription_changes"]["args"]["days"]["max"] == 365
    assert set(spec["finance.recurring"]["args"]) == {"merchant", "account", "person"}
    assert all(t["acts"] is False for n, t in spec.items() if n != "finance.expense.add")
    assert spec["finance.expense.add"]["acts"] is True
    # quick words are the Assistant's lexicon's concepts (none here: see the module notes) and plain words
    for t in spec.values():
        for c in (t.get("quick") or {}).get("concepts", []):
            assert c in ("BILL", "BALANCE", "RECURRING", "PRICE")
    assert "finance summary" in spec["finance.overview"]["quick"]["words"]
    assert "net worth" in spec["finance.balance_history"]["quick"]["words"]


def test_link_targets_accept_accounts_and_uploads_and_those_paths_redirect(env):
    from app import tools
    for target in ("/accounts/12", "/uploads/7", "/uploads", "/dashboard", "/recurring", "/month/2026-10"):
        assert tools.tools.link("x", target)["target"] == target
    for bad in ("/accounts/x", "/uploads/7/8", "/accounts/", "/account?id=1"):
        with pytest.raises(ValueError):
            tools.tools.link("x", bad)
    r = env.get("accounts/12", follow_redirects=False)
    assert (r.status_code, r.headers["location"]) == (307, "../account?id=12")
    r = env.get("uploads/7", follow_redirects=False)
    assert (r.status_code, r.headers["location"]) == (307, "../uploads?account=7")
    assert env.get("uploads").status_code == 200


def test_the_household_assistants_fixture_is_this_catalogue(env):
    """household_assistant/tests/data/finance_tools.json routes the quick-answer phrases in the Assistant's own
    tests (test_finance_quick.py): it must be this catalogue."""
    from app import tools
    path = os.path.join(os.path.dirname(__file__), "..", "..", "household_assistant", "tests", "data",
                        "finance_tools.json")
    if not os.path.isfile(path):
        pytest.skip("the Household Assistant isn't next to this app")
    with open(path, encoding="utf-8") as f:
        fixture = json.load(f)
    assert fixture == json.loads(json.dumps(tools.tools.spec()))


def test_quick_words_are_the_assistants_kind_and_dont_take_its_other_questions(env):
    """Lower-case phrases (assist_tools checks), no two tools sharing one, and none of the Assistant's everyday
    questions for Todo or the older Finance tools inside them (those would be taken from them)."""
    from app import tools
    seen = {}
    for t in tools.tools.spec():
        for w in (t.get("quick") or {}).get("words", []):
            assert w == w.lower() and w not in seen, (w, seen.get(w))
            seen[w] = t["name"]
    for taken in ("what is due", "overdue", "coming up", "payment due", "due date", "how much do i owe", "balance in",
                  "did i pay"):
        assert taken not in seen


# ------------------------------------------------------------------ finance.statements

def statements_household(env):
    chk = account(env, "Checking", account_number_last4="1234", **monthly(10))
    visa = card(env, "Visa", account_number_last4="4242", **monthly(12))
    sav = account(env, "Savings", "savings", **monthly(19))
    amex = card(env, "Amex", **monthly(5))
    cash = account(env, "Cash")
    old = account(env, "Old Checking", is_archived=1, **monthly(1))
    s = statement(env, chk, "2026-10-11 08:00:00", "2026-09")                     # received for Oct 10
    for d in ("2026-06-03", "2026-07-03", "2026-09-03", "2026-10-03"):            # nothing for the Aug cycle
        env.txn(chk, d, -10.0, "COFFEE", statement_id=s)
    statement(env, visa, "2026-09-13 09:00:00", period_end="2026-09-11", source="qfx")
    statement(env, amex, "2026-09-06 09:00:00", "2026-08")
    env.insert("statement_dismissals", account_id=amex, cycle_date="2026-10-05", dismissed_by="tester")
    imp = env.insert("csv_imports", user_id="tester", imported_at="2026-10-02 12:00:00", row_count=1)
    env.txn(sav, "2026-09-30", 5.0, "INTEREST", csv_import_id=imp)
    return {"chk": chk, "visa": visa, "sav": sav, "amex": amex, "cash": cash, "old": old}


def test_statements_status_matches_cycle_status(env, clock):
    from app import statement_due
    ids = statements_household(env)
    res = call("finance.statements")
    by = {i["account"]: i for i in res["items"]}
    assert list(by) == ["Visa ••4242", "Savings", "Amex", "Checking ••1234", "Cash"]    # overdue first
    with env.db() as c:
        for name, key in (("Visa ••4242", "visa"), ("Savings", "sav"), ("Amex", "amex"), ("Checking ••1234", "chk")):
            st = statement_due.cycle_status(c, row(c, ids[key]), date(2026, 10, 19))
            assert by[name]["status"] == st["status"]
            assert by[name]["next"] == st["next_date"].isoformat()
    assert [by[n]["status"] for n in by] == ["overdue", "due", "dismissed", "received", "not set"]
    assert "Old Checking" not in str(res)                                       # archived
    text = res["text"]
    assert text.startswith("Statements, 1 overdue: Visa ••4242: monthly on the 12th; overdue 7 days (expected Oct 12); "
                           "last uploaded Sep 13 (QFX, to Sep 11); next expected Nov 12.")
    assert "Savings: monthly on the 19th; due today; last uploaded Oct 2 (CSV, to Sep 30); next expected Nov 19." in text
    assert "Amex: monthly on the 5th; dismissed (expected Oct 5); last uploaded Sep 6 (PDF, Aug 2026)" in text
    assert "Checking ••1234: monthly on the 10th; received (expected Oct 10)" in text
    assert "Cash: not set; nothing uploaded yet." in text
    assert res["links"][0]["target"] == "/uploads"


def test_statements_gap_list_is_the_upload_strips(env, clock):
    from types import SimpleNamespace

    from app import db, upload_page
    ids = statements_household(env)
    me = SimpleNamespace(id="tester", is_admin=False)
    with db.get_db() as c:
        strip = {s["id"]: s["missing"] for s in upload_page.strip_cards(c, me, me, date(2026, 10, 19))}
    assert strip[ids["chk"]] == ["Aug"]
    by = {i["account"]: i for i in call("finance.statements")["items"]}
    assert by["Checking ••1234"]["missing"] == "Aug"
    assert "nothing uploaded for Aug" in call("finance.statements", account="checking")["text"]
    assert all(by[n]["missing"] == "" for n in by if n != "Checking ••1234")


def test_statements_one_account_by_name_or_last_4_digits(env, clock):
    ids = statements_household(env)
    for asked in ("VISA", "visa", "4242"):
        res = call("finance.statements", account=asked)
        assert [i["account"] for i in res["items"]] == ["Visa ••4242"]
        assert res["links"][0]["target"] == f"/uploads/{ids['visa']}"
    res = call("finance.statements", account="Brokerage")
    assert res["text"].startswith("No account called “Brokerage”. Accounts: Amex, Cash, Checking ••1234,")
    assert call("finance.statements", account="old checking")["text"].startswith("No account called")   # archived


def test_statements_none_set_and_no_accounts(env, clock):
    assert call("finance.statements")["text"] == "You have no accounts in Finance yet."
    a = account(env, "Checking")
    statement(env, a, "2026-10-11 08:00:00", "2026-09")
    assert call("finance.statements")["text"] == (
        "None of your accounts has an expected statement date. You can set one on the account's page. "
        "Last uploads: Checking Oct 11.")


# ------------------------------------------------------------------ finance.payments_due

def cards_household(env):
    chk = account(env, "Checking")
    visa = card(env, "Visa", 22, account_number_last4="4242")
    env.txn(visa, "2026-10-10", -300.0, f"AUTOPAY THANK YOU {FULL_NUMBER}")      # pays Oct 22
    env.txn(visa, "2026-10-12", 25.0, "COFFEE")
    statement(env, visa, "2026-10-03 09:00:00", "2026-09", period_end="2026-10-01", balance=440.0)
    amex = card(env, "Amex", 15)                                                    # Oct 15 unpaid: overdue
    env.txn(amex, "2026-09-10", -80.0, "PAYMENT RECEIVED")                         # paid Sep 15
    master = card(env, "Master", 25)                                                # paid by a linked transfer
    out = env.txn(chk, "2026-10-10", -120.0, "PAYMENT TO MASTER", txn_type="transfer")
    into = env.txn(master, "2026-10-11", -120.0, "TRANSFER IN SECRET MEMO", txn_type="transfer",
                   transfer_pair_id=out)
    with env.db() as c:
        c.execute("UPDATE transactions SET transfer_pair_id = ? WHERE id = ?", (into, out))
    disc = card(env, "Disc", 28)                                                    # marked as paid
    env.insert("payment_dismissals", account_id=disc, due_date="2026-10-28", dismissed_by="tester")
    store = card(env, "Store card", 24)                                             # Oct 24, nothing paid
    plain = card(env, "Plain")                                                      # no due day
    card(env, "Old card", 20, is_archived=1)
    return {"visa": visa, "amex": amex, "master": master, "disc": disc, "store": store, "plain": plain, "chk": chk}


def test_payments_paid_by_a_payment_a_transfer_or_marked_and_unpaid_or_overdue(env, clock):
    from app import card_payments, db
    ids = cards_household(env)
    res = call("finance.payments_due")
    by = {i["account"]: i for i in res["items"]}
    assert list(by) == ["Amex", "Visa ••4242", "Store card", "Master", "Disc", "Plain"]    # soonest first
    assert (by["Visa ••4242"]["status"], by["Visa ••4242"]["paid_on"], by["Visa ••4242"]["paid_amount"]) == \
        ("paid", "2026-10-10", 300.0)
    assert (by["Master"]["status"], by["Master"]["paid_on"]) == ("paid", "2026-10-11")
    assert by["Disc"]["status"] == "marked as paid"
    assert (by["Store card"]["status"], by["Store card"]["days"]) == ("unpaid", 5)
    assert (by["Amex"]["status"], by["Amex"]["due_date"], by["Amex"]["days"]) == ("unpaid", "2026-10-15", -4)
    assert by["Plain"]["status"] == "not set"
    with db.get_db() as c:                                       # the states are card_payments.status's
        st = card_payments.status(c, row(c, ids["visa"]), date(2026, 10, 19))
    assert st["next"]["state"] == "paid" and st["next"]["paid_on"] == date(2026, 10, 10)
    text = res["text"]
    assert ("Visa ••4242 (due the 22nd): Oct 22 (in 3 days), paid Oct 10 (300.00); latest statement 440.00 owed "
            "(Oct 1)") in text
    assert "Amex (due the 15th): was due Oct 15, 4 days ago, unpaid; last payment 80.00 on Sep 10" in text
    assert "Disc (due the 28th): Oct 28 (in 9 days), marked as paid" in text
    assert "Plain: payment due day not set" in text
    assert "Old card" not in text
    assert {link["target"] for link in res["links"]} == {f"/accounts/{ids[k]}" for k in ("amex", "visa", "store",
                                                                                          "master", "disc")}
    for secret in ("AUTOPAY", "THANK YOU", "SECRET MEMO", "PAYMENT RECEIVED", FULL_NUMBER[:8]):
        assert secret not in str(res)                                           # never a payment's description


def test_payments_days_filter_and_one_card(env, clock):
    ids = cards_household(env)
    res = call("finance.payments_due", days=5)
    assert [i["account"] for i in res["items"]] == ["Amex", "Visa ••4242", "Store card"]
    assert res["text"].endswith(" No payment due day set: Plain.")
    res = call("finance.payments_due", account="4242")
    assert [i["account"] for i in res["items"]] == ["Visa ••4242"]
    assert res["links"] == [{"label": "Visa in Finance Dashboard", "target": f"/accounts/{ids['visa']}"}]
    assert (res["items"][0]["statement_balance"], res["items"][0]["statement_date"]) == (440.0, "2026-10-01")
    nack(call("finance.payments_due", days=61), "invalid", "days")


def test_payments_the_owed_amount_is_the_latest_statements(env, clock):
    visa = card(env, "Visa", 22)
    statement(env, visa, "2026-09-03 09:00:00", "2026-08", period_end="2026-09-01", balance=200.0)
    statement(env, visa, "2026-10-03 09:00:00", "2026-09", period_end="2026-10-01", balance=-14.01)
    assert "latest statement 14.01 in credit (Oct 1)" in call("finance.payments_due")["text"]
    assert call("finance.payments_due")["items"][0]["statement_balance"] == -14.01


def test_payments_no_cards(env, clock):
    account(env, "Checking")
    assert call("finance.payments_due")["text"] == "You have no credit card accounts in Finance."


# ------------------------------------------------------------------ finance.balance_history

def balances_household(env):
    chk = account(env, "Checking", account_number_last4="1234")
    for end, bal in (("2025-12-31", 900.0), ("2026-03-31", 1000.0), ("2026-06-30", 1200.0), ("2026-09-30", 1500.0)):
        statement(env, chk, end + " 12:00:00", end[:7], period_end=end, balance=bal, source="qfx")
    visa = card(env, "Visa", account_number_last4="4242")
    statement(env, visa, "2026-10-02 09:00:00", "2026-09", period_end="2026-10-01", balance=440.0)
    sav = account(env, "Savings", "savings")
    old = account(env, "Old", is_archived=1)
    statement(env, old, "2026-10-02 09:00:00", "2026-09", period_end="2026-09-30", balance=99999.0)
    return {"chk": chk, "visa": visa, "sav": sav}


def test_one_accounts_history_and_change(env, clock):
    ids = balances_household(env)
    res = call("finance.balance_history", account="checking")
    assert res["text"] == ("Checking ••1234: 1,500.00 on Sep 30, 2026, up 600.00 since Dec 31, 2025 (900.00). "
                           "Statements in the last 12 months: Sep 30, 2026 1,500.00; Jun 30, 2026 1,200.00; "
                           "Mar 31, 2026 1,000.00; Dec 31, 2025 900.00.")
    assert [i["balance"] for i in res["items"]] == [1500.0, 1200.0, 1000.0, 900.0]
    assert res["links"][0]["target"] == f"/accounts/{ids['chk']}"
    res = call("finance.balance_history", account="checking", months=6)      # since Apr 30: carried from Mar 31
    assert "up 500.00 since Mar 31, 2026 (1,000.00)" in res["text"]
    assert [i["balance"] for i in res["items"]] == [1500.0, 1200.0]
    assert "440.00 owed on Oct 1, 2026" in call("finance.balance_history", account="visa")["text"]
    assert call("finance.balance_history", account="savings")["text"] == (
        "Savings: No statement balances yet — balances come from uploaded statements.")


def test_month_picks_the_statement_ending_on_or_before_its_end(env, clock):
    balances_household(env)
    res = call("finance.balance_history", account="1234", month="2026-07")
    assert res["text"] == ("Checking ••1234 at the end of July 2026: 1,200.00, from the statement ending Jun 30, 2026 "
                           "(QFX).")
    assert res["items"][0]["date"] == "2026-06-30"
    assert "1,000.00, from the statement ending Mar 31" in call("finance.balance_history", account="checking",
                                                                month="2026-03")["text"]
    assert "had no statement balance by the end of November 2025" in call(
        "finance.balance_history", account="checking", month="2025-11")["text"]


def test_net_worth_subtracts_cards_leaves_out_accounts_without_a_balance_and_matches_balances_py(env, clock):
    from app import balances, db
    balances_household(env)
    res = call("finance.balance_history")
    with db.get_db() as c:
        nw = balances.net_worth(c, "tester", date(2026, 10, 19))
    assert nw["total"] == 1060.0
    assert res["text"].startswith(f"Net worth {nw['total']:,.2f} (cash 1,500.00, cards 440.00 owed), up 160.00 since "
                                  "Dec 31, 2025.")
    first = next(m for m in nw["months"] if m["value"] is not None)
    assert first["value"] == 900.0 and 1060.0 - first["value"] == 160.0
    assert "Checking ••1234 1,500.00 (Sep 30, 2026); Savings no balance yet (left out); Visa ••4242 440.00 owed " \
           "(Oct 1, 2026)." in res["text"]
    assert [(i["account"], i["balance"], i["counts"]) for i in res["items"]] == [
        (f"{r['name']}" + {"Checking": " ••1234", "Visa": " ••4242"}.get(r["name"], ""), r["balance"], r["signed"])
        for r in nw["accounts"]]
    assert "Old" not in res["text"] and "99,999" not in res["text"]
    assert res["links"][0]["target"] == "/dashboard"
    # another window: the same arithmetic, carried forward from the month-end 24 months back
    assert "since Dec 31, 2025" in call("finance.balance_history", months=24)["text"]
    assert "Net worth 900.00" in call("finance.balance_history", month="2026-02")["text"]


def test_balance_history_nothing_to_show(env, clock):
    account(env, "Checking")
    assert call("finance.balance_history")["text"] == (
        "No statement balances yet — balances come from uploaded statements.")


# ------------------------------------------------------------------ finance.subscription_changes

MONTHLY = ["2026-06-12", "2026-07-12", "2026-08-12", "2026-09-12"]


def subs_household(env):
    a = account(env, "Checking")
    charges(env, a, "NETFLIX", [(d, 15.49) for d in MONTHLY[:3]] + [(MONTHLY[3], 17.99)])
    bill = [("2026-04-20", 80.12), ("2026-05-20", 95.40), ("2026-06-20", 88.10), ("2026-07-20", 91.00),
            ("2026-08-20", 86.30), ("2026-09-20", 92.50), ("2026-10-18", 140.00)]
    charges(env, a, "CITY ELECTRIC", bill)
    charges(env, a, "SPOTIFY", [("2026-08-20", 9.99), ("2026-09-20", 9.99), ("2026-10-18", 9.99)])
    old = account(env, "Old", is_archived=1)
    charges(env, old, "GYM CLUB", [(d, 9.99) for d in MONTHLY[:3]] + [(MONTHLY[3], 19.99)])
    return a


def test_price_change_range_and_new_subscription(env, clock):
    subs_household(env)
    res = call("finance.subscription_changes")
    assert [i["kind"] for i in res["items"]] == ["range", "price", "new"]          # newest first
    assert res["text"] == (
        "3 subscription changes in the last 90 days: CITY ELECTRIC usually 80.12–95.40 → 140.00 (+47%) on Oct 18, "
        "Checking; NETFLIX 15.49 → 17.99 (+16%) since Sep 12, Checking; New: SPOTIFY 9.99 monthly since Aug 20, "
        "Checking.")
    assert res["links"][0]["target"] == "/recurring"
    assert "GYM" not in str(res)                                          # archived


def test_a_dismissed_change_is_marked_not_hidden(env, clock):
    from app import db, subscriptions
    a = subs_household(env)
    with db.get_db() as c:
        ch = next(x for x in subscriptions.changes(c, "tester") if x["kind"] == "price")
        assert subscriptions.dismiss(c, "tester", a, ch["series"], ch["change_key"], "tester")
        assert not any(x["kind"] == "price" for x in subscriptions.changes(c, "tester"))     # gone from Home
    res = call("finance.subscription_changes", merchant="net flix")
    assert res["text"] == ("1 subscription change in the last 90 days: NETFLIX 15.49 → 17.99 (+16%) since Sep 12, "
                           "Checking (dismissed).")
    assert res["items"][0]["dismissed"] is True


def test_subscription_changes_merchant_and_days(env, clock):
    subs_household(env)
    assert [i["merchant"] for i in call("finance.subscription_changes", merchant="Spotify")["items"]] == ["SPOTIFY"]
    # a new subscription counts from the day it became one (its third charge, Oct 18), a price change from its charge
    assert [i["kind"] for i in call("finance.subscription_changes", days=30)["items"]] == ["range", "new"]
    assert [i["kind"] for i in call("finance.subscription_changes", days=40)["items"]] == ["range", "price", "new"]
    assert call("finance.subscription_changes", merchant="Hulu")["text"] == (
        "No subscription price changes or new subscriptions for “Hulu” in the last 90 days.")
    nack(call("finance.subscription_changes", days=366), "invalid", "days")


def test_no_subscription_changes(env, clock):
    account(env, "Checking")
    assert call("finance.subscription_changes")["text"] == (
        "No subscription price changes or new subscriptions in the last 90 days.")


# ------------------------------------------------------------------ finance.recurring and finance.bills

def recurring_household(env):
    chk = account(env, "Checking", account_number_last4="1234")
    visa = card(env, "Visa", 22, account_number_last4="4242")
    charges(env, chk, "NETFLIX", [(d, 15.49) for d in ("2026-06-25", "2026-07-25", "2026-08-25")]
            + [("2026-09-25", 17.99)])
    for d in ("2026-07-02", "2026-08-02", "2026-09-02", "2026-10-02"):
        env.txn(visa, d, 9.99, "SPOTIFY")                                # a card purchase is positive
    old = account(env, "Old", is_archived=1)
    charges(env, old, "GYM CLUB", [(d, 30.0) for d in MONTHLY])
    return chk, visa


def test_recurring_merchant_account_yearly_cost_and_totals(env, clock):
    recurring_household(env)
    res = call("finance.recurring")
    assert res["text"] == (
        "2 recurring charges, 26.10 a month in monthly ones: NETFLIX 16.11 monthly (≈ 193.32 a year, was 15.49 until "
        "Sep 25); SPOTIFY 9.99 monthly (≈ 119.88 a year). In all about 26.10 a month, 313.20 a year.")
    by = {i["merchant"]: i for i in res["items"]}
    assert by["NETFLIX"]["yearly"] == 193.32 and (by["NETFLIX"]["was"], by["NETFLIX"]["until"]) == (15.49, "2026-09-25")
    assert by["SPOTIFY"]["was"] is None
    assert "GYM" not in str(res)                                          # archived
    res = call("finance.recurring", merchant="netflix")
    assert [i["merchant"] for i in res["items"]] == ["NETFLIX"]
    assert res["text"].startswith("1 recurring charges for “netflix”, 16.11 a month in monthly ones: NETFLIX")
    res = call("finance.recurring", account="4242")
    assert [i["merchant"] for i in res["items"]] == ["SPOTIFY"]
    assert res["text"].startswith("1 recurring charges on Visa ••4242, 9.99 a month")
    assert call("finance.recurring", merchant="Hulu")["text"] == "No recurring charges found for “Hulu”."
    assert call("finance.recurring", account="Brokerage")["text"].startswith("No account called “Brokerage”.")


def test_recurring_old_wording_is_kept(env, clock):
    """What the 1.10.0 answer said is still said, word for word; only additions."""
    recurring_household(env)
    text = call("finance.recurring")["text"]
    old = "2 recurring charges, 26.10 a month in monthly ones: NETFLIX 16.11 monthly; SPOTIFY 9.99 monthly."
    stripped = re.sub(r" \((≈|was)[^)]*\)", "", text).split(" In all about")[0]
    assert stripped == old
    assert call("finance.recurring", person="me")["text"] == text
    with env.db() as c:
        c.execute("DELETE FROM transactions")
    assert call("finance.recurring")["text"] == "No recurring charges found."


def test_bills_include_an_unpaid_card_payment_and_leave_out_a_paid_one(env, clock):
    chk, visa = recurring_household(env)
    store = card(env, "Store card", 24)
    statement(env, store, "2026-10-03 09:00:00", "2026-09", period_end="2026-10-01", balance=75.5)
    env.txn(visa, "2026-10-10", -300.0, "AUTOPAY")                         # Visa's Oct 22 is paid
    res = call("finance.bills", days=30)
    assert res["text"] == ("2 expected in the next 30 days, about 26.10 in recurring charges, plus 1 card payment: "
                           "2026-10-24 Store card payment (card payment, last statement 75.50); "
                           "2026-10-25 NETFLIX 16.11; 2026-11-02 SPOTIFY 9.99.")
    assert [i.get("kind") for i in res["items"]] == ["card_payment", "recurring", "recurring"]
    assert "Visa payment" not in res["text"]
    with env.db() as c:
        c.execute("UPDATE transactions SET deleted_at = datetime('now') WHERE description = 'AUTOPAY'")
    assert "2026-10-22 Visa ••4242 payment (card payment)" in call("finance.bills", days=30)["text"]
    assert call("finance.bills", days=3)["text"] == (
        "1 card payment due in the next 3 days: 2026-10-22 Visa ••4242 payment (card payment).")
    # the old wording without card payments
    with env.db() as c:
        c.execute("UPDATE accounts SET payment_due_day = NULL")
    assert call("finance.bills", days=30)["text"] == (
        "2 expected in the next 30 days, about 26.10: 2026-10-25 NETFLIX 16.11; 2026-11-02 SPOTIFY 9.99.")


# ------------------------------------------------------------------ finance.dates

def dates_household(env):
    chk = account(env, "Checking", **monthly(10))
    statement(env, chk, "2026-10-11 08:00:00", "2026-09")                  # received: Oct 10 left out
    visa = card(env, "Visa", 22, **monthly(12))                             # statement overdue 7 days
    statement(env, visa, "2026-10-03 09:00:00", "2026-09", period_end="2026-10-01", balance=440.0)
    amex = card(env, "Amex", 15, **monthly(5))                              # payment overdue 4 days
    env.insert("statement_dismissals", account_id=amex, cycle_date="2026-10-05", dismissed_by="tester")
    statement(env, amex, "2026-09-06 09:00:00", "2026-08")
    master = card(env, "Master", 25)                                        # paid: left out
    env.txn(master, "2026-10-11", -120.0, "PAYMENT")
    disc = card(env, "Disc", 28)                                            # marked as paid: left out
    env.insert("payment_dismissals", account_id=disc, due_date="2026-10-28", dismissed_by="tester")
    charges(env, chk, "NETFLIX", [(d, 17.99) for d in ("2026-07-24", "2026-08-24", "2026-09-24")])
    return chk


def test_dates_overdue_first_then_soonest_with_every_kind(env, clock):
    dates_household(env)
    res = call("finance.dates")
    assert [(i["date"], i["kind"], i["overdue"]) for i in res["items"]] == [
        ("2026-10-12", "statement", True), ("2026-10-15", "card payment", True),
        ("2026-10-22", "card payment", False), ("2026-10-24", "bill", False)]
    assert res["text"] == (
        "Coming up: Visa statement — expected Oct 12, overdue 7 days; Amex payment — was due Oct 15, overdue 4 days; "
        "Visa payment — Oct 22 (in 3 days), last statement 440.00; NETFLIX — about Oct 24, 17.99, Checking. "
        "2 overdue, 2 more in the next 14 days.")
    assert res["links"][0]["target"] == "/dashboard"
    text = res["text"]
    for left_out in ("Checking statement", "Master", "Disc", "Amex statement"):
        assert left_out not in text


def test_dates_window_count_line_and_empty(env, clock):
    dates_household(env)
    res = call("finance.dates", days=3)
    assert [i["date"] for i in res["items"]] == ["2026-10-12", "2026-10-15", "2026-10-22"]
    assert res["text"].endswith("2 overdue, 1 more in the next 3 days.")
    res = call("finance.dates", days=30)                    # the next statements and the next month's payments too
    assert ("Checking statement — expected Nov 10 (in 22 days)" in res["text"]
            and "Amex statement — expected Nov 5 (in 17 days)" in res["text"]
            and "Master payment — Oct 25" not in res["text"])
    with env.db() as c:
        c.execute("DELETE FROM accounts")
    account(env, "Cash")
    assert call("finance.dates")["text"] == "Nothing due in the next 14 days, and nothing overdue."


def test_dates_only_overdue(env, clock):
    v = card(env, "Visa", **monthly(12))
    statement(env, v, "2026-09-13 09:00:00", "2026-08")
    assert call("finance.dates", days=1)["text"].endswith("1 overdue, nothing else in the next 1 days.")


# ------------------------------------------------------------------ finance.overview

def test_overview_parts_in_order_with_the_same_numbers(env, clock):
    from app import tools
    chk = dates_household(env)
    statement(env, chk, "2026-10-01 09:00:00", "2026-09", period_end="2026-09-30", balance=2000.0)
    env.txn(chk, "2026-10-01", 3000.0, "PAYROLL", category="Income", txn_type="income")
    env.txn(chk, "2026-10-03", -900.0, "RENT", category="Housing", review_status="clean")
    charges(env, chk, "SPOTIFY", [("2026-08-20", 9.99), ("2026-09-20", 9.99), ("2026-10-18", 9.99)])
    res = call("finance.overview")
    text = res["text"]
    parts = [text.index(p) for p in ("Net worth", "Due:", "October 2026 so far:", "Subscription changes:")]
    assert parts == sorted(parts)
    assert text.startswith("Net worth 1,560.00 (cash 2,000.00, cards 440.00 owed), down 440.00 since Sep 30, 2026. "
                           "Amex no balance yet (left out); Checking 2,000.00 (Sep 30, 2026);")
    hist = call("finance.balance_history")
    assert hist["text"].split(".")[0].split(",")[0] == text.split(".")[0].split(",")[0]
    dates = call("finance.dates")
    assert [i["what"] for i in res["items"] if i.get("part") == "due"] == [i["what"] for i in dates["items"]]
    assert dates["text"].split(". ")[-1] in text                                 # the same count line
    month = call("finance.summary", month="2026-10")["text"]
    assert month.startswith("October 2026: income 3,000.00, spending ")
    figures = month.split(": ", 1)[1].split(". Top")[0]
    assert f"October 2026 so far: {figures}." in text
    assert "Subscription changes: New: SPOTIFY 9.99 monthly since Aug 20, Checking; " in text
    assert res["links"][0]["target"] == "/dashboard"
    assert tools.OVERVIEW_CHANGE_DAYS == 30 and tools.DATES_DAYS == 14


def test_overview_leaves_out_empty_parts_and_says_when_there_are_no_accounts(env, clock):
    assert call("finance.overview")["text"] == "You have no accounts in Finance yet."
    a = account(env, "Checking")
    assert call("finance.overview")["text"] == (
        "Nothing to report yet: no statement balances, nothing due in the next 14 days and no transactions in "
        "October 2026.")
    statement(env, a, "2026-10-01 09:00:00", "2026-09", period_end="2026-09-30", balance=50.0)
    text = call("finance.overview")["text"]
    assert text == ("Net worth 50.00 (cash 50.00, cards nothing owed), no change since Sep 30, 2026. "
                    "Checking 50.00 (Sep 30, 2026).")
    assert "Due:" not in text and "so far" not in text and "Subscription" not in text


def test_overview_limits_and_n_more(env, clock):
    for n in range(12):
        a = account(env, f"Acct {n:02d}", **monthly(1 + n))                  # 12 accounts, every statement overdue
        statement(env, a, "2026-09-01 09:00:00", "2026-08", period_end="2026-08-31", balance=10.0 + n)
    chk = account(env, "Zed")
    for name, start in (("ALPHA", 5.0), ("BRAVO", 6.0), ("CHARLIE", 7.0), ("DELTA", 8.0)):
        charges(env, chk, name, [("2026-07-01", start), ("2026-08-01", start), ("2026-09-01", start),
                                 ("2026-10-01", start + 1)])
    text = call("finance.overview")["text"]
    assert "Acct 09 19.00 (Aug 31, 2026); and 3 more." in text                 # 10 of 13 accounts
    assert "Acct 10 20.00" not in text
    assert text.count(" statement — expected") == 8
    assert "and 10 more — ask for the finance dates." in text         # 12 overdue, 2 next statements, 4 bills
    sub = text.split("Subscription changes: ")[1]
    assert sub.count(" → ") == 3 and sub.endswith("; and 1 more.")


# ------------------------------------------------------------------ the rules for every tool

def everything(env):
    ids = statements_household(env)
    cards_household(env)
    balances_household(env)
    subs_household(env)
    with env.db() as c:
        c.execute("UPDATE accounts SET name = ? WHERE name = 'Cash'", (f"Cash {FULL_NUMBER}",))
    env.txn(ids["chk"], "2026-10-05", -15.0, f"TRANSFER REF {FULL_NUMBER}", note="the safe code is 4417")
    return ids


ALL = [("finance.statements", {}), ("finance.statements", {"account": "visa"}), ("finance.payments_due", {}),
       ("finance.payments_due", {"days": 30}), ("finance.balance_history", {}),
       ("finance.balance_history", {"account": "checking"}), ("finance.subscription_changes", {}),
       ("finance.dates", {"days": 60}), ("finance.overview", {}), ("finance.recurring", {}), ("finance.bills", {})]


def test_no_account_number_beyond_the_last_4_digits_and_no_notes_anywhere(env, clock):
    everything(env)
    for tool, args in ALL:
        res = call(tool, **args)
        assert isinstance(res, dict), (tool, res)
        blob = json.dumps(res, ensure_ascii=False)
        assert not re.search(r"\d{5,}", res["text"]), (tool, res["text"])
        assert FULL_NUMBER[:5] not in blob and "safe code" not in blob, tool
    assert "Cash ···· " in call("finance.statements")["text"] or "Cash ····" in call("finance.statements")["text"]


def test_someone_elses_data_needs_a_share_and_person_works_with_one(env, clock):
    env.insert("known_users", id="other", name="Robin")
    rob = account(env, "Robin Checking", user="other", **monthly(12))
    statement(env, rob, "2026-09-13 09:00:00", "2026-08", period_end="2026-08-31", balance=321.0, user="other")
    rcard = card(env, "Robin Visa", 22, user="other")
    charges(env, rob, "ROBIN GYM", [(d, 20.0) for d in MONTHLY[:3]] + [(MONTHLY[3], 25.0)], user="other")
    for tool in NEW + ("finance.recurring", "finance.bills"):
        nack(call(tool, person="Robin"), "not_found", "person")
        assert "Robin" not in str(call(tool)), tool                            # never in the asker's own answers
    with env.db() as c:
        c.execute("INSERT INTO user_access (viewer_id, owner_id, granted_by) VALUES ('tester', 'other', 'tester')")
    assert call("finance.statements", person="robin")["text"].startswith(
        "Statements (Robin's), 1 overdue: Robin Checking: monthly on the 12th; overdue 7 days")
    assert call("finance.payments_due", person="Robin")["text"].startswith(
        "Card payments (Robin's): Robin Visa (due the 22nd): Oct 22 (in 3 days), unpaid")
    assert call("finance.balance_history", person="Robin")["text"].startswith("Net worth (Robin's) 321.00")
    assert "ROBIN GYM 20.00 → 25.00" in call("finance.subscription_changes", person="Robin")["text"]
    assert call("finance.dates", person="Robin")["text"].startswith("Coming up (Robin's): Robin Checking statement")
    assert call("finance.overview", person="Robin")["text"].startswith("Net worth (Robin's) 321.00")
    assert call("finance.payments_due", person="Robin")["links"][0]["target"] == f"/accounts/{rcard}"


def test_the_app_setting_and_the_persons_own_switch(env, clock):
    statements_household(env)
    for tool in NEW:
        assert "text" in call(tool)
    turn_on(env, False)
    for tool in NEW:
        nack(call(tool), "not_allowed", "off")
    turn_on(env)
    with env.db() as c:
        c.execute("UPDATE known_users SET assistant_ok = 0 WHERE id = 'tester'")
    for tool in NEW:
        nack(call(tool), "not_allowed", "person_off")


def test_archived_accounts_are_left_out_everywhere(env, clock):
    everything(env)
    with env.db() as c:
        c.execute("UPDATE accounts SET is_archived = 1 WHERE name IN ('Visa', 'Checking')")
    for tool, args in ALL:
        res = call(tool, **args)
        blob = json.dumps(res, ensure_ascii=False)
        if args.get("account") in ("visa", "checking"):
            assert res["text"].startswith("No account called"), tool
            continue
        for name in ("Visa", "Checking", "GYM", "Old card", "NETFLIX", "1234", "4242"):
            assert name not in blob, (tool, name)
