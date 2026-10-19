"""Balance over time on the account page and Home's Net worth card (SPEC.md section 28).

"Today" is frozen by replacing app.statement_due.now (the clock app/balances.py reads)."""
import re
from datetime import date, datetime

import pytest

BOB = {"x-remote-user-id": "bob", "x-remote-user-name": "Bob"}


@pytest.fixture
def clock(env, monkeypatch):
    from app import statement_due
    state = {"at": datetime(2026, 10, 19, 10, 0)}
    monkeypatch.setattr(statement_due, "now", lambda: state["at"])
    return state


def stmt(env, account_id, balance, *, period=None, end=None, status="complete", source="pdf", user="tester",
         txn_dates=(), **extra):
    sid = env.insert("statements", user_id=user, account_id=account_id, status=status, source=source,
                     statement_period=period, period_end=end, new_balance=balance, **extra)
    for i, d in enumerate(txn_dates):
        env.txn(account_id, d, -1.0 - i, f"ROW {sid} {i}", user=user, statement_id=sid)
    return sid


def points(env, account_id):
    from app import balances
    with env.db() as c:
        return [(p["date"], p["balance"]) for p in balances.statement_points(c, account_id)]


def net(env, user="tester"):
    from app import balances
    with env.db() as c:
        return balances.net_worth(c, user)


# ------------------------------------------------------------------ the series

def test_each_statements_end_date_qfx_period_end_then_last_transaction_then_month_end(env):
    a = env.account()
    stmt(env, a, 1200.0, source="qfx", period="2026-06", end="2026-06-14", txn_dates=("2026-06-01",))
    stmt(env, a, 1000.0, period="2026-04", txn_dates=("2026-04-03", "2026-04-28"))
    stmt(env, a, 1100.0, period="2026-05")                                   # no rows: the month's last day
    assert points(env, a) == [(date(2026, 4, 28), 1000.0), (date(2026, 5, 31), 1100.0), (date(2026, 6, 14), 1200.0)]


def test_only_live_statements_that_didnt_fail_and_have_a_balance(env):
    a = env.account()
    stmt(env, a, 10.0, period="2026-01")
    stmt(env, a, 20.0, period="2026-02", status="error")
    stmt(env, a, 30.0, period="2026-03", deleted_at="2026-04-01 00:00:00")
    stmt(env, a, None, period="2026-04")
    stmt(env, a, 50.0, period="2026-05", status="pending_review")            # waiting in Review still counts
    stmt(env, a, 60.0, period=None)                                          # no date at all: left out
    assert points(env, a) == [(date(2026, 1, 31), 10.0), (date(2026, 5, 31), 50.0)]


def test_two_statements_ending_the_same_day_keep_the_newer(env):
    a = env.account()
    stmt(env, a, 10.0, period="2026-01")
    stmt(env, a, 12.5, period="2026-01")
    assert points(env, a) == [(date(2026, 1, 31), 12.5)]


def test_the_account_page_chart_summary_and_table(env, clock):
    a = env.account("Checking")
    stmt(env, a, 900.0, period="2025-09")
    stmt(env, a, 1000.0, period="2025-10")
    stmt(env, a, 1250.5, period="2026-10", end="2026-10-14", source="qfx")
    page = env.get(f"account?id={a}").text
    sec = page.split('id="balance-over-time"')[1]
    assert "Balance over time" in page and 'class="chart chart-multi"' in sec and "chart-line" in sec
    # a year before Oct 14, 2026 the balance was Sep 30, 2025's (carried forward)
    assert ("Balance 1,250.50 on Oct 14, 2026; up 350.50 since a year before (900.00 on Sep 30, 2025)."
            in sec)
    table = sec.split('id="balance-table"')[1]
    assert table.index("Oct 14, 2026") < table.index("Oct 31, 2025") < table.index("Sep 30, 2025")   # newest first
    # the chart is drawn oldest first (newest last)
    svg = sec.split("<svg")[1].split("</svg>")[0]
    assert svg.index("Sep 30, 2025") < svg.index("Oct 14, 2026")


def test_less_than_a_year_compares_with_the_first_statement_and_one_has_no_change(env, clock):
    a = env.account()
    stmt(env, a, 500.0, period="2026-08")
    only = env.account("Only")
    stmt(env, only, 75.0, period="2026-09")
    assert "Balance 500.00 on Aug 31, 2026. What the account held" in env.get(f"account?id={a}").text
    stmt(env, a, 420.0, period="2026-09")
    text = env.get(f"account?id={a}").text
    assert "Balance 420.00 on Sep 30, 2026; down 80.00 since the first statement (500.00 on Aug 31, 2026)." in text
    assert "Balance 75.00 on Sep 30, 2026. What the account held" in env.get(f"account?id={only}").text


def test_a_card_shows_what_is_owed(env, clock):
    card = env.account("Visa", "credit_card")
    stmt(env, card, 300.0, period="2026-08")
    stmt(env, card, -14.01, period="2026-09")                                 # a credit balance
    page = env.get(f"account?id={card}").text
    assert "Owed -14.01 on Sep 30, 2026; down 314.01 since the first statement" in page
    assert "Owed per statement" in page and "below zero: a credit" in page


def test_no_statement_balances_yet(env):
    a = env.account()
    stmt(env, a, None, period="2026-09")
    page = env.get(f"account?id={a}").text
    assert 'id="balance-empty"' in page and "No statement balances yet." in page and "chart-multi" not in page


# ------------------------------------------------------------------ net worth

def test_net_worth_adds_cash_and_takes_off_what_cards_owe(env, clock):
    chk, sav, visa = env.account("Checking"), env.account("Savings", "savings"), env.account("Visa", "credit_card")
    none_yet = env.account("New card", "credit_card")
    env.insert("accounts", user_id="tester", name="Archived", type="checking", is_archived=1)
    gone = env.insert("accounts", user_id="tester", name="Gone", type="checking", deleted_at="2026-09-01 00:00:00")
    stmt(env, gone, 99999.0, period="2026-09")
    other = env.account("Bob's", user="bob")
    stmt(env, other, 777.0, period="2026-09", user="bob")
    stmt(env, chk, 900.0, period="2026-08")
    stmt(env, chk, 1000.0, period="2026-09")
    stmt(env, sav, 500.0, source="qfx", period="2026-10", end="2026-10-01")
    stmt(env, visa, 200.0, period="2026-09")
    nw = net(env)
    assert nw["total"] == 1300.0 and (nw["counted"], nw["missing"]) == (3, 1)
    rows = {r["name"]: (r["balance"], r["as_of"], r["signed"]) for r in nw["accounts"]}
    assert rows == {"Checking": (1000.0, date(2026, 9, 30), 1000.0), "New card": (None, None, None),
                    "Savings": (500.0, date(2026, 10, 1), 500.0), "Visa": (200.0, date(2026, 9, 30), -200.0)}
    assert none_yet in {r["id"] for r in nw["accounts"]}


def test_twelve_month_ends_carry_each_accounts_last_balance_forward(env, clock):
    chk, visa = env.account("Checking"), env.account("Visa", "credit_card")
    stmt(env, chk, 100.0, period="2026-01")
    stmt(env, chk, 300.0, period="2026-06")
    stmt(env, visa, 50.0, period="2026-03")
    months = net(env)["months"]
    assert [m["date"] for m in months][:2] == [date(2025, 11, 30), date(2025, 12, 31)]
    assert months[-1]["date"] == date(2026, 10, 19) and len(months) == 12          # this month: up to today
    values = {m["date"].strftime("%Y-%m"): m["value"] for m in months}
    assert values["2025-12"] is None and values["2026-01"] == 100.0 and values["2026-02"] == 100.0
    assert values["2026-03"] == 50.0 and values["2026-05"] == 50.0 and values["2026-06"] == 250.0
    assert values["2026-10"] == 250.0


def test_a_statement_ending_after_today_isnt_counted_yet(env, clock):
    a = env.account()
    stmt(env, a, 10.0, period="2026-09")
    stmt(env, a, 20.0, source="qfx", period="2026-10", end="2026-10-25")
    assert net(env)["total"] == 10.0


def test_the_home_card(env, clock):
    chk, visa = env.account("Checking"), env.account("Visa", "credit_card")
    env.account("Empty")
    stmt(env, chk, 1000.0, period="2026-09")
    stmt(env, visa, 250.25, period="2026-09")
    page = env.get("dashboard").text
    card = page.split('id="net-worth"')[1].split("</section>")[0]
    assert 'id="net-worth-total">749.75<' in card
    assert "1 account with no balance yet left out" in card and "no balance yet" in card
    assert "Sep 30, 2026" in card and "(owed)" in card and "chart-multi" in card


def test_no_balances_at_all_and_no_accounts(env, clock):
    assert 'id="net-worth"' not in env.get("dashboard").text                  # no accounts: no card
    env.account()
    card = env.get("dashboard").text.split('id="net-worth"')[1].split("</section>")[0]
    assert "No statement balances yet." in card and "chart-multi" not in card and "no balance yet" in card


def test_shared_access_sees_the_owners_net_worth(env, clock):
    for uid, name in (("tester", "Tester"), ("bob", "Bob")):
        env.insert("known_users", id=uid, name=name)
    env.insert("user_access", viewer_id="bob", owner_id="tester", granted_by="tester")
    chk = env.account("Checking")
    stmt(env, chk, 640.0, period="2026-09")
    mine = env.account("Bob's own", user="bob")
    stmt(env, mine, 5.0, period="2026-09", user="bob")
    page = env.get("dashboard", headers=BOB).text                            # opens into tester's data
    assert re.search(r'id="net-worth-total">640\.00<', page) and "Bob&#39;s own" not in page
    own = env.get("dashboard?as_user=bob", headers=BOB).text                 # their own data
    assert re.search(r'id="net-worth-total">5\.00<', own)
