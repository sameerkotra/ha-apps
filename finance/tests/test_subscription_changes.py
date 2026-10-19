"""Subscription price changes and new subscriptions (SPEC.md section 29), on Home and Recurring, with Dismiss.

"Today" is frozen by replacing app.statement_due.now (the clock app/subscriptions.py reads)."""
from datetime import date, datetime

import pytest

BOB = {"x-remote-user-id": "bob", "x-remote-user-name": "Bob"}


@pytest.fixture
def subs(env):
    from app import subscriptions
    return subscriptions


@pytest.fixture
def clock(env, monkeypatch):
    from app import statement_due
    state = {"at": datetime(2026, 10, 19, 10, 0)}
    monkeypatch.setattr(statement_due, "now", lambda: state["at"])

    class C:
        def set(self, *a):
            state["at"] = datetime(*a)
    return C()


def charges(env, account_id, description, rows, user="tester"):
    """rows: [("2026-06-12", 15.49), …] as money out on a checking account."""
    for d, amount in rows:
        env.txn(account_id, d, -amount, description, user=user)


def found(env, subs, user="tester", **kw):
    with env.db() as c:
        return [(ch["kind"], ch["text"]) for ch in subs.changes(c, user, **kw)]


MONTHLY = ["2026-06-12", "2026-07-12", "2026-08-12", "2026-09-12", "2026-10-12"]


# ------------------------------------------------------------------ price changes

def test_a_price_change_after_two_charges_at_the_old_amount(env, subs, clock):
    a = env.account()
    charges(env, a, "NETFLIX", [(d, 15.49) for d in MONTHLY[:3]] + [(MONTHLY[3], 17.99)])
    assert found(env, subs) == [("price", "NETFLIX 15.49 → 17.99 (+16%) since Sep 12")]


def test_the_change_goes_once_the_new_price_repeats(env, subs, clock):
    a = env.account()
    charges(env, a, "NETFLIX", [(d, 15.49) for d in MONTHLY[:3]] + [(d, 17.99) for d in MONTHLY[3:]])
    assert found(env, subs) == []


def test_a_big_jump_that_lands_in_a_series_of_its_own_is_still_the_same_subscription(env, subs, clock):
    a = env.account()
    charges(env, a, "GYM CLUB", [(d, 9.99) for d in MONTHLY[:4]] + [(MONTHLY[4], 15.49)])
    assert found(env, subs) == [("price", "GYM CLUB 9.99 → 15.49 (+55%) since Oct 12")]


def test_one_charge_at_the_old_amount_isnt_enough_and_a_cent_isnt_a_change(env, subs, clock):
    a = env.account()
    charges(env, a, "NEWS SITE", [("2026-06-03", 9.00), ("2026-07-03", 9.00), ("2026-08-03", 10.00),
                                  ("2026-09-03", 11.00)])
    charges(env, a, "CLOUD STORAGE", [(d, 2.99) for d in MONTHLY[:3]] + [(MONTHLY[3], 3.00)])
    assert found(env, subs) == []


def test_a_variable_bill_only_counts_outside_its_usual_range(env, subs, clock):
    a = env.account()
    bill = [("2026-04-20", 80.12), ("2026-05-20", 95.40), ("2026-06-20", 88.10), ("2026-07-20", 91.00),
            ("2026-08-20", 86.30)]
    charges(env, a, "CITY ELECTRIC", bill + [("2026-09-20", 92.50)])
    assert found(env, subs) == []
    charges(env, a, "CITY ELECTRIC", [("2026-10-18", 140.00)])
    assert found(env, subs) == [("range", "CITY ELECTRIC usually 80.12–95.40 → 140.00 (+47%) on Oct 18")]


def test_an_old_change_is_no_longer_shown(env, subs, clock):
    a = env.account()
    charges(env, a, "NETFLIX", [("2026-02-12", 15.49), ("2026-03-12", 15.49), ("2026-04-12", 15.49),
                                ("2026-05-12", 17.99)])
    clock.set(2026, 8, 1, 9, 0)
    assert len(found(env, subs)) == 1
    clock.set(2026, 10, 19, 9, 0)                                 # more than 90 days on (it was cancelled)
    assert found(env, subs) == []


def test_income_and_transfers_are_never_subscriptions(env, subs, clock):
    a = env.account()
    for d in MONTHLY[:3]:
        env.txn(a, d, 2000.0, "PAYROLL")
    env.txn(a, MONTHLY[3], 2100.0, "PAYROLL")
    for d in MONTHLY[:3]:
        env.txn(a, d, -15.49, "NETFLIX", txn_type="transfer")
    env.txn(a, MONTHLY[3], -17.99, "NETFLIX", txn_type="transfer")
    assert found(env, subs) == []


# ------------------------------------------------------------------ new subscriptions

def test_a_series_that_became_recurring_in_the_last_45_days(env, subs, clock):
    a = env.account()
    charges(env, a, "SPOTIFY", [("2026-08-20", 9.99), ("2026-09-20", 9.99), ("2026-10-18", 9.99)])
    charges(env, a, "OLD SUB", [("2026-01-05", 4.99), ("2026-02-05", 4.99), ("2026-03-05", 4.99),
                                ("2026-04-05", 4.99), ("2026-05-05", 4.99)])
    assert found(env, subs) == [("new", "New: SPOTIFY 9.99 monthly since Aug 20")]
    clock.set(2026, 12, 3, 9, 0)                                  # 46 days after its third charge
    assert found(env, subs) == []


def test_two_charges_arent_recurring_until_confirmed_and_confirming_makes_it_new(env, subs, clock):
    a = env.account()
    charges(env, a, "HULU", [("2026-09-01", 7.99), ("2026-10-01", 7.99)])
    assert found(env, subs) == []
    r = env.post("recurring-override", data={"merchant_pattern": "HULU@7.99", "is_recurring": "1"})
    assert r.status_code == 303
    assert found(env, subs) == [("new", "New: HULU 7.99 monthly since Sep 1")]


def test_a_merchants_series_going_on_at_a_new_price_isnt_a_new_subscription(env, subs, clock):
    a = env.account()
    charges(env, a, "GYM CLUB", [("2026-03-12", 9.99), ("2026-04-12", 9.99), ("2026-05-12", 9.99),
                                 ("2026-06-12", 9.99), ("2026-07-12", 15.49), ("2026-08-12", 15.49),
                                 ("2026-09-12", 15.49)])
    assert found(env, subs) == []


# ------------------------------------------------------------------ the pages and Dismiss

def _card(page):
    return page.split('id="subscription-changes"')[1].split("</section>")[0] if 'id="subscription-changes"' in page else ""


def test_home_and_recurring_show_the_changes_and_dismiss_keeps_one_away(env, subs, clock):
    a = env.account("Checking")
    charges(env, a, "NETFLIX", [(d, 15.49) for d in MONTHLY[:3]] + [(MONTHLY[3], 17.99)])
    charges(env, a, "SPOTIFY", [("2026-08-20", 9.99), ("2026-09-20", 9.99), ("2026-10-18", 9.99)])
    home, rec = _card(env.get("dashboard").text), _card(env.get("recurring").text)
    for card in (home, rec):
        assert "NETFLIX 15.49 → 17.99 (+16%) since Sep 12" in card and "New: SPOTIFY 9.99 monthly" in card
        assert card.count("Dismiss") == 2 and "Checking" in card
    assert 'value="recurring"' in rec and 'value="dashboard"' in home
    with env.db() as c:
        netflix = next(ch for ch in subs.changes(c, "tester") if ch["kind"] == "price")
    r = env.post("subscription-dismiss", data={"account_id": str(a), "series": netflix["series"],
                                               "change_key": netflix["change_key"], "next": "recurring"})
    assert r.status_code == 303 and r.headers["location"] == "recurring"
    assert "NETFLIX" not in _card(env.get("dashboard").text) and "SPOTIFY" in _card(env.get("recurring").text)
    with env.db() as c:
        row = c.execute("SELECT * FROM subscription_dismissals").fetchone()
        assert (row["series"], row["change_key"]) == ("NETFLIX", "price:15.49>17.99:2026-09-12")
    # the next charge repeats the new price: nothing; a later, different change shows again
    charges(env, a, "NETFLIX", [(MONTHLY[4], 17.99)])
    assert "NETFLIX" not in _card(env.get("dashboard").text)
    clock.set(2026, 11, 14, 9, 0)
    charges(env, a, "NETFLIX", [("2026-11-12", 19.99)])
    assert "NETFLIX 17.99 → 19.99 (+11%) since Nov 12" in _card(env.get("dashboard").text)


def test_dismissing_something_that_isnt_there_does_nothing(env, subs, clock):
    a = env.account()
    env.post("subscription-dismiss", data={"account_id": str(a), "series": "X", "change_key": "new:X@1.00"})
    with env.db() as c:
        assert c.execute("SELECT COUNT(*) FROM subscription_dismissals").fetchone()[0] == 0


def test_recurring_filtered_to_one_account(env, subs, clock):
    a, b = env.account("Checking"), env.account("Visa", "credit_card")
    charges(env, a, "NETFLIX", [(d, 15.49) for d in MONTHLY[:3]] + [(MONTHLY[3], 17.99)])
    for d in ("2026-08-20", "2026-09-20", "2026-10-18"):
        env.txn(b, d, 9.99, "SPOTIFY")                            # a card purchase is positive
    card = _card(env.get(f"recurring?account_id={b}").text)
    assert "SPOTIFY" in card and "NETFLIX" not in card


def test_owner_only_and_the_app_setting(env, subs, clock):
    from app import settings
    for uid, name in (("tester", "Tester"), ("bob", "Bob")):
        env.insert("known_users", id=uid, name=name)
    env.insert("user_access", viewer_id="bob", owner_id="tester", granted_by="tester")
    a = env.account()
    charges(env, a, "NETFLIX", [(d, 15.49) for d in MONTHLY[:3]] + [(MONTHLY[3], 17.99)])
    assert _card(env.get("dashboard", headers=BOB).text) == "" and _card(env.get("recurring", headers=BOB).text) == ""
    assert "NETFLIX" in _card(env.get("dashboard").text)
    assert settings.SETTINGS["subscription_alerts"].default == "1"
    assert "Subscription change alerts" in env.get("settings").text
    env.insert("app_settings", key="subscription_alerts", value="0")
    settings.invalidate()
    assert _card(env.get("dashboard").text) == "" and _card(env.get("recurring").text) == ""
