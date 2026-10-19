"""Credit card payment due dates (SPEC.md section 30): the day, paid or not, Mark as paid, the notices and column.

"Today" is frozen by replacing app.statement_due.now (the clock app/card_payments.py reads)."""
import re
from datetime import date, datetime

import pytest

BOB = {"x-remote-user-id": "bob", "x-remote-user-name": "Bob"}


@pytest.fixture
def cp(env):
    from app import card_payments
    return card_payments


@pytest.fixture
def clock(env, monkeypatch):
    from app import statement_due
    state = {"at": datetime(2026, 10, 19, 10, 0)}
    monkeypatch.setattr(statement_due, "now", lambda: state["at"])

    class C:
        def set(self, *a):
            state["at"] = datetime(*a)
    return C()


def card(env, name="Visa", day=22, set_at="2026-10-01 00:00:00", user="tester", **extra):
    return env.insert("accounts", user_id=user, name=name, type="credit_card", payment_due_day=day,
                      payment_due_set_at=set_at, **extra)


def status(env, cp, account_id, on=None):
    with env.db() as c:
        a = c.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        return cp.status(c, a, on)


# ------------------------------------------------------------------ the day

def test_due_dates_clamp_like_the_expected_dates(cp):
    a31, last = {"payment_due_day": 31}, {"payment_due_day": 0}
    assert cp.due_dates(a31, date(2026, 1, 1), date(2026, 4, 30)) == [
        date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30)]
    assert cp.due_dates(a31, date(2028, 2, 1), date(2028, 2, 29)) == [date(2028, 2, 29)]
    assert cp.due_dates(last, date(2026, 11, 1), date(2027, 2, 28)) == [
        date(2026, 11, 30), date(2026, 12, 31), date(2027, 1, 31), date(2027, 2, 28)]
    assert cp.due_dates({"payment_due_day": 5}, date(2026, 10, 6), date(2026, 12, 5)) == [
        date(2026, 11, 5), date(2026, 12, 5)]
    assert cp.due_dates({"payment_due_day": None}, date(2026, 1, 1), date(2026, 12, 31)) == []


def test_the_form_sets_it_on_cards_only_with_a_set_at_stamp(env, clock):
    r = env.post("accounts", data={"name": "Visa", "type": "credit_card", "payment_form": "1", "payment_due_day": "5"})
    assert r.status_code == 303
    r = env.post("accounts", data={"name": "Chk", "type": "checking", "payment_form": "1", "payment_due_day": "5"})
    assert r.status_code == 303
    r = env.post("accounts", data={"name": "Bad", "type": "credit_card", "payment_form": "1", "payment_due_day": "32"})
    assert r.status_code == 400 and "Pick the day the card&#39;s payment is due" in r.text or "payment is due" in r.text
    with env.db() as c:
        rows = {r["name"]: (r["payment_due_day"], r["payment_due_set_at"]) for r in c.execute("SELECT * FROM accounts")}
    assert rows == {"Visa": (5, "2026-10-19 10:00:00"), "Chk": (None, None)}
    with env.db() as c:
        visa = c.execute("SELECT id FROM accounts WHERE name = 'Visa'").fetchone()[0]
    page = env.get(f"account?id={visa}").text
    assert 'id="payment-due-edit"' in page and '<option value="5" selected>Monthly on day 5</option>' in page
    assert 'id="payment-due-add"' in env.get("accounts").text and 'data-for-type="credit_card"' in env.get("accounts").text
    # changing it re-stamps; saving it unchanged doesn't; last day; Off
    clock.set(2026, 10, 20, 9, 0)
    base = {"id": str(visa), "name": "Visa", "payment_form": "1"}
    env.post("account-update", data={**base, "payment_due_day": "5"})
    with env.db() as c:
        assert c.execute("SELECT payment_due_set_at FROM accounts WHERE id = ?", (visa,)).fetchone()[0] == "2026-10-19 10:00:00"
    env.post("account-update", data={**base, "payment_due_day": "last"})
    with env.db() as c:
        assert tuple(c.execute("SELECT payment_due_day, payment_due_set_at FROM accounts WHERE id = ?", (visa,)).fetchone()) \
            == (0, "2026-10-20 09:00:00")
    env.post("account-update", data={**base, "payment_due_day": ""})
    env.post("account-update", data={"id": str(visa), "name": "Visa renamed"})          # no group: left as it is
    with env.db() as c:
        assert c.execute("SELECT payment_due_day FROM accounts WHERE id = ?", (visa,)).fetchone()[0] is None
    chk_page = env.get("account?id=2").text
    assert 'id="payment-due-edit"' not in chk_page


def test_due_dates_before_the_setting_never_count(env, cp, clock):
    a = card(env, day=5, set_at="2026-10-19 08:00:00")          # set after Oct 5
    st = status(env, cp, a)
    assert st["previous"]["state"] == "before_setting" and st["notice"] is None
    assert st["next"]["due_date"] == date(2026, 11, 5) and st["next"]["state"] == "unpaid"
    assert status(env, cp, a, date(2026, 11, 7))["kind"] == "overdue"            # Nov 5 does


# ------------------------------------------------------------------ paid

def test_a_payment_on_or_after_the_previous_due_date_pays(env, cp, clock):
    a = card(env, day=22)
    assert status(env, cp, a)["kind"] == "soon"                                  # Oct 22 in 3 days
    env.txn(a, "2026-09-21", -100.0, "PAYMENT THANK YOU")                        # before Sep 22: pays Sep 22's
    assert status(env, cp, a)["kind"] == "soon"
    env.txn(a, "2026-09-25", 40.0, "COFFEE")                                     # a purchase isn't a payment
    assert status(env, cp, a)["kind"] == "soon"
    env.txn(a, "2026-09-22", -80.0, "PAYMENT THANK YOU")                         # on Sep 22 itself: Sep 22's
    assert status(env, cp, a)["next"]["state"] == "unpaid"
    pay = env.txn(a, "2026-10-02", -250.0, "AUTOPAY PAYMENT")
    st = status(env, cp, a)
    assert st["next"]["state"] == "paid" and st["next"]["paid_on"] == date(2026, 10, 2) and st["notice"] is None
    with env.db() as c:
        c.execute("UPDATE transactions SET deleted_at = datetime('now') WHERE id = ?", (pay,))
    assert status(env, cp, a)["kind"] == "soon"                                  # a deleted payment doesn't count


def test_a_linked_transfer_into_the_card_pays(env, cp, clock):
    a = card(env, day=22)
    chk = env.account("Checking")
    out = env.txn(chk, "2026-10-10", -300.0, "PAYMENT TO VISA", txn_type="transfer")
    into = env.txn(a, "2026-10-11", -300.0, "PAYMENT RECEIVED", txn_type="transfer", transfer_pair_id=out)
    with env.db() as c:
        c.execute("UPDATE transactions SET transfer_pair_id = ? WHERE id = ?", (into, out))
    assert status(env, cp, a)["next"]["state"] == "paid"


def test_one_payment_pays_one_due_date_and_a_late_one_pays_the_one_it_was_late_for(env, cp, clock):
    a = card(env, day=5)
    env.txn(a, "2026-09-05", -50.0, "PAYMENT")                                    # Sep 5's, on the day
    env.txn(a, "2026-10-07", -60.0, "PAYMENT")                                    # Oct 5's, two days late
    st = status(env, cp, a)                                                      # Oct 19
    assert st["previous"]["due_date"] == date(2026, 10, 5) and st["previous"]["state"] == "paid"
    assert st["previous"]["paid_on"] == date(2026, 10, 7)
    assert st["next"]["due_date"] == date(2026, 11, 5) and st["next"]["state"] == "unpaid"
    assert status(env, cp, a, date(2026, 10, 30))["kind"] == "soon"              # Nov 5 in 6 days, not paid by Oct 7


# ------------------------------------------------------------------ the notices

def test_notice_kinds_and_texts(env, cp, clock):
    soon, today, over = card(env, "Soon", day=22), card(env, "Today", day=19), card(env, "Over", day=17)
    far = card(env, "Far", day=30)
    with env.db() as c:
        due = {s["name"]: (s["kind"], s["text"]) for s in cp.due_for(c, "tester")}
    assert due == {"Soon": ("soon", "Due Oct 22 (in 3 days)"), "Today": ("today", "Due today"),
                   "Over": ("overdue", "Was due 2 days ago")}
    assert far
    page = env.get("dashboard").text
    box = page.split('id="payments-due"')[1].split("</section>")[0]
    assert "3 card payments due" in box and "Mark as paid" in box and box.index("Over") < box.index("Today") < box.index("Soon")
    assert 'id="payments-due"' in env.get("uploads").text
    labels = {}
    for url in ("accounts", "transactions-accounts"):
        text = env.get(url).text
        assert "<th>Payment due</th>" in text
        for name, aid in (("Soon", soon), ("Today", today), ("Over", over), ("Far", far)):
            labels[name] = re.search(r'class="payment-due ([^"]*)">([^<]*)<', text.split(f">{name}<")[1]).groups()
    assert labels == {"Soon": ("due-soon", "Oct 22 (in 3 days)"), "Today": ("due-today", "Due today"),
                      "Over": ("due-overdue", "Was due 2 days ago"), "Far": ("", "Oct 30 (in 11 days)")}
    assert "Was due 2 days ago" in env.get(f"account?id={over}").text


def test_overdue_lasts_until_the_next_due_date(env, cp, clock):
    a = card(env, day=5)
    assert status(env, cp, a, date(2026, 11, 4))["kind"] == "overdue"            # Oct 5, 30 days ago
    st = status(env, cp, a, date(2026, 11, 5))
    assert st["kind"] == "today" and st["notice"]["due_date"] == date(2026, 11, 5)
    # within 7 days of the next one, the overdue one is still what shows (it's the older, more urgent one)
    assert status(env, cp, a, date(2026, 11, 1))["notice"]["due_date"] == date(2026, 10, 5)


def test_mark_as_paid_clears_that_due_date_only(env, cp, clock):
    a = card(env, day=17)
    r = env.post("card-payment-paid", data={"account_id": str(a), "due_date": "2026-09-17", "next": "dashboard"})
    assert r.status_code == 303 and status(env, cp, a)["kind"] == "overdue"      # not the noticed one: ignored
    r = env.post("card-payment-paid", data={"account_id": str(a), "due_date": "2026-10-17", "next": "uploads"})
    assert r.headers["location"] == "uploads"
    st = status(env, cp, a)
    assert st["previous"]["state"] == "dismissed" and st["notice"] is None
    assert 'id="payments-due"' not in env.get("dashboard").text
    assert "Was due" not in env.get("accounts").text
    clock.set(2026, 11, 12, 9, 0)                                                # Nov 17 in 5 days: as normal
    assert status(env, cp, a)["kind"] == "soon" and 'id="payments-due"' in env.get("dashboard").text
    r = env.post("card-payment-paid", data={"account_id": str(a), "due_date": "2026-11-17", "next": "account"})
    assert r.headers["location"] == f"account?id={a}"
    assert status(env, cp, a)["next"]["state"] == "dismissed"
    assert "Marked paid · due Nov 17" in env.get("accounts").text


def test_archived_cards_have_none_and_unarchiving_restarts_from_the_next_due_date(env, cp, clock):
    a = card(env, day=17, is_archived=1)
    assert status(env, cp, a) is None and 'id="payments-due"' not in env.get("dashboard").text
    env.post("account-archive", data={"id": str(a), "archived": "0"})
    st = status(env, cp, a)
    assert st["previous"]["state"] == "before_setting" and st["notice"] is None


def test_owner_only(env, cp, clock):
    for uid, name in (("tester", "Tester"), ("bob", "Bob")):
        env.insert("known_users", id=uid, name=name)
    env.insert("user_access", viewer_id="bob", owner_id="tester", granted_by="tester")
    card(env, day=22)
    page = env.get("dashboard", headers=BOB).text
    assert "Using <strong>Tester</strong>" in page and 'id="payments-due"' not in page
    assert 'id="payments-due"' not in env.get("uploads", headers=BOB).text
    assert "Payment due</th>" not in env.get("accounts", headers=BOB).text
    assert 'id="payments-due"' in env.get("dashboard").text
    # bob can't mark tester's card as paid either
    env.post("card-payment-paid", headers=BOB, data={"account_id": "1", "due_date": "2026-10-22"})
    with env.db() as c:
        assert c.execute("SELECT COUNT(*) FROM payment_dismissals").fetchone()[0] == 0


def test_the_app_setting_off_hides_notices_and_column_and_keeps_the_day(env, cp, clock):
    from app import settings
    a = card(env, day=22)
    env.insert("app_settings", key="card_payment_reminders", value="0")
    settings.invalidate()
    assert not cp.enabled()
    assert 'id="payments-due"' not in env.get("dashboard").text and 'id="payments-due"' not in env.get("uploads").text
    assert "Payment due</th>" not in env.get("accounts").text
    assert "Card payment reminders are off for the whole app" in env.get(f"account?id={a}").text
    with env.db() as c:
        assert c.execute("SELECT payment_due_day FROM accounts WHERE id = ?", (a,)).fetchone()[0] == 22
