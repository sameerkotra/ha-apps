"""Expected statement dates and overdue reminders (SPEC.md section 27; FINANCE_STATEMENT_REMINDERS_SPEC.md §9).

"Now" is frozen by replacing app.statement_due.now; nothing here talks to Home Assistant: the phones come from
ha_people's cache and ha_notify.send_notify is replaced by a recorder."""
import io
import json
import re
import sqlite3
import time
from datetime import date, datetime, timedelta

import pytest

import qfx_samples as qs

BOB = {"x-remote-user-id": "bob", "x-remote-user-name": "Bob"}


# ------------------------------------------------------------------ helpers

class Clock:
    def __init__(self, monkeypatch, sd):
        self.at = datetime(2026, 10, 1, 8, 0)
        monkeypatch.setattr(sd, "now", lambda: self.at)

    def set(self, *args):
        self.at = datetime(*args)
        return self


@pytest.fixture
def sd(env):
    from app import statement_due
    return statement_due


@pytest.fixture
def clock(monkeypatch, sd):
    return Clock(monkeypatch, sd)


class Calls(list):
    ok = True


@pytest.fixture
def sent(monkeypatch, env):
    """Phones for tester and bob in Home Assistant's people (ha_people's cache) and a recorder for every send."""
    from app.common import ha_notify, ha_people
    calls = Calls()
    people = [{"entityId": f"person.{u}", "name": u.title(), "userId": u, "state": "home", "picture": None,
               "phones": [{"label": "Phone", "tracker": None, "service": f"mobile_app_{u}"}]} for u in ("tester", "bob")]
    monkeypatch.setitem(ha_people._cache, "people", people)
    monkeypatch.setitem(ha_people._cache, "at", time.monotonic())

    def fake(service, title, message, data=None):
        calls.append({"service": service, "title": title, "message": message, "data": data})
        return calls.ok
    monkeypatch.setattr(ha_notify, "send_notify", fake)
    return calls


def _stamp(d):
    return f"{d} 00:00:00" if len(d) == 10 else d


def acct(env, name="Visa", every=1, day=12, frm=None, set_at="2026-01-01 00:00:00", user="tester", last4=None,
         type_="credit_card", **extra):
    return env.insert("accounts", user_id=user, name=name, type=type_, expected_every_months=every, expected_day=day,
                      expected_from=frm, expected_set_at=set_at, account_number_last4=last4, **extra)


def stmt(env, account_id, when, status="complete", source="pdf", user="tester"):
    return env.insert("statements", user_id=user, account_id=account_id, status=status, source=source,
                      uploaded_at=_stamp(when))


def csv_import(env, account_id, when, rows=1, user="tester"):
    cid = env.insert("csv_imports", user_id=user, original_filename="x.csv", row_count=rows, imported_at=_stamp(when))
    for i in range(rows):
        env.txn(account_id, "2026-09-01", -1.0 - i, f"ROW {i}", user=user, csv_import_id=cid)
    return cid


def prefs(env, offsets, repeat=0, at="09:00", user="tester", mode="remind"):
    env.insert("reminder_prefs", user_id=user, mode=mode, offsets=json.dumps(offsets), repeat_days=repeat, at_time=at)


def status(env, sd, account_id, on):
    with env.db() as c:
        a = c.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        return sd.cycle_status(c, a, on)


def check(sd, clock, *at):
    if at:
        clock.set(*at)
    return sd.run_check()


def log(env):
    with env.db() as c:
        return [dict(r) for r in c.execute("SELECT * FROM statement_reminders ORDER BY id")]


def sends_over(sd, clock, start, days, hours=(8, 9, 12, 18)):
    """Run the check at several times a day for `days` days; returns the days a notification went out."""
    out = []
    for n in range(days):
        d = start + timedelta(days=n)
        for h in hours:
            clock.set(d.year, d.month, d.day, h, 0)
            if sd.run_check():
                out.append((d, h))
    return out


# ------------------------------------------------------------------ expected dates

def test_monthly_on_the_12th(sd):
    a = {"expected_every_months": 1, "expected_day": 12, "expected_from": None}
    assert sd.expected_dates(a, date(2026, 9, 1), date(2026, 11, 30)) == [date(2026, 9, 12), date(2026, 10, 12),
                                                                          date(2026, 11, 12)]
    assert sd.expected_dates(a, date(2026, 9, 13), date(2026, 10, 12)) == [date(2026, 10, 12)]


def test_the_31st_in_short_months_and_a_leap_year(sd):
    a = {"expected_every_months": 1, "expected_day": 31, "expected_from": None}
    got = sd.expected_dates(a, date(2026, 1, 1), date(2026, 4, 30))
    assert got == [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30)]
    assert sd.expected_dates(a, date(2028, 2, 1), date(2028, 2, 29)) == [date(2028, 2, 29)]
    a30 = {"expected_every_months": 1, "expected_day": 30, "expected_from": None}
    assert sd.expected_dates(a30, date(2026, 2, 1), date(2026, 2, 28)) == [date(2026, 2, 28)]


def test_the_last_day(sd):
    a = {"expected_every_months": 1, "expected_day": 0, "expected_from": None}
    assert sd.expected_dates(a, date(2028, 1, 1), date(2028, 4, 30)) == [
        date(2028, 1, 31), date(2028, 2, 29), date(2028, 3, 31), date(2028, 4, 30)]


def test_every_3_months_from_a_starting_month(sd):
    a = {"expected_every_months": 3, "expected_day": 5, "expected_from": "2026-02"}
    assert sd.expected_dates(a, date(2025, 1, 1), date(2027, 3, 1)) == [
        date(2026, 2, 5), date(2026, 5, 5), date(2026, 8, 5), date(2026, 11, 5), date(2027, 2, 5)]


def test_yearly(sd):
    a = {"expected_every_months": 12, "expected_day": 15, "expected_from": "2026-03"}
    assert sd.expected_dates(a, date(2026, 1, 1), date(2028, 12, 31)) == [
        date(2026, 3, 15), date(2027, 3, 15), date(2028, 3, 15)]
    assert sd.expected_dates({"expected_every_months": None, "expected_day": None, "expected_from": None},
                             date(2026, 1, 1), date(2026, 12, 31)) == []


# ------------------------------------------------------------------ received or overdue

def test_an_upload_on_the_expected_date_counts_and_the_day_before_is_the_previous_cycles(env, sd, clock):
    on_day, day_before = acct(env, "On the day"), acct(env, "Day before")
    stmt(env, on_day, "2026-10-12 08:00:00")
    stmt(env, day_before, "2026-10-11 23:00:00")
    st = status(env, sd, on_day, date(2026, 10, 15))
    assert st["status"] == "received" and st["cycle_date"] == date(2026, 10, 12)
    st = status(env, sd, day_before, date(2026, 10, 15))
    assert (st["status"], st["days_overdue"]) == ("overdue", 3)
    assert status(env, sd, day_before, date(2026, 10, 12))["status"] == "due"     # on the day itself: not overdue yet


def test_qfx_pdf_and_csv_uploads_count_hand_entered_rows_dont(env, sd, clock):
    clock.set(2026, 10, 15, 10, 0)
    qfx, pdf, csv, hand = (acct(env, n, type_="checking", last4=l)
                           for n, l in (("QFX", "6789"), ("PDF", None), ("CSV", None), ("Hand", None)))
    r = env.post("upload", data={"account_id": str(qfx)},
                 files={"files": ("s.qfx", io.BytesIO(qs.checking()), "application/octet-stream")})
    assert r.status_code == 200, r.text
    r = env.post("transactions-import-csv", data={"default_account_id": str(csv)}, headers={"accept": "application/json"},
                 files={"files": ("b.csv", io.BytesIO(b"Date,Description,Amount\n10/13/2026,COFFEE,-4.50\n"), "text/csv")})
    assert r.status_code == 200 and r.json()["results"][0]["imported"] == 1
    with env.db() as c:      # the real uploads are stamped with today's real time: move them to Oct 13
        c.execute("UPDATE statements SET uploaded_at = '2026-10-13 10:00:00'")
        c.execute("UPDATE csv_imports SET imported_at = '2026-10-13 10:00:00'")
    stmt(env, pdf, "2026-10-13 10:00:00")
    env.txn(hand, "2026-10-13", -5.0, "BY HAND")
    got = {n: status(env, sd, a, date(2026, 10, 15))["status"]
           for n, a in (("qfx", qfx), ("pdf", pdf), ("csv", csv), ("hand", hand))}
    assert got == {"qfx": "received", "pdf": "received", "csv": "received", "hand": "overdue"}


def test_processing_counts_error_doesnt_and_an_empty_csv_doesnt(env, sd):
    busy, failed, empty = acct(env, "Busy"), acct(env, "Failed"), acct(env, "Empty")
    stmt(env, busy, "2026-10-13", status="processing")
    stmt(env, failed, "2026-10-13", status="error")
    csv_import(env, empty, "2026-10-13", rows=0)
    on = date(2026, 10, 15)
    assert [status(env, sd, a, on)["status"] for a in (busy, failed, empty)] == ["received", "overdue", "overdue"]


def test_dismiss_stops_the_cycle_and_its_notice_and_badge_the_next_cycle_is_as_normal(env, sd, clock, sent):
    visa = acct(env)
    prefs(env, [2, 5])
    clock.set(2026, 10, 14, 9, 30)
    assert check(sd, clock) == 1
    page = env.get("dashboard").text
    assert "Statement overdue" in page and 'class="nav-badge"' in page and "expected Oct 12" in page
    r = env.post("statement-dismiss", data={"account_id": str(visa), "cycle_date": "2026-10-12", "next": "dashboard"})
    assert r.status_code == 303 and r.headers["location"] == "dashboard"
    for url in ("dashboard", "uploads"):
        page = env.get(url).text
        assert "Statement overdue" not in page and 'class="nav-badge"' not in page
    assert check(sd, clock, 2026, 10, 17, 9, 30) == 0                     # day 5 isn't sent
    assert status(env, sd, visa, date(2026, 10, 17))["status"] == "dismissed"
    assert check(sd, clock, 2026, 11, 14, 9, 30) == 1                     # Nov 12's cycle: day 2 as normal
    assert "Statement overdue" in env.get("dashboard").text


def test_dismissing_an_older_cycle_does_nothing(env, sd, clock):
    visa = acct(env)
    clock.set(2026, 11, 14, 9, 0)
    env.post("statement-dismiss", data={"account_id": str(visa), "cycle_date": "2026-10-12"})
    with env.db() as c:
        assert c.execute("SELECT COUNT(*) FROM statement_dismissals").fetchone()[0] == 0


def test_when_the_next_expected_date_arrives_the_previous_cycle_is_dropped(env, sd, clock, sent):
    acct(env)
    prefs(env, [1], repeat=7)
    clock.set(2026, 11, 11, 12, 0)
    assert "Statement overdue" in env.get("dashboard").text                # Oct 12, 30 days overdue
    clock.set(2026, 11, 12, 12, 0)
    assert "Statement overdue" not in env.get("dashboard").text           # Nov 12 arrived: Oct is gone
    assert check(sd, clock) == 0                                           # and isn't reminded about either
    clock.set(2026, 11, 13, 9, 15)
    assert check(sd, clock) == 1 and log(env)[-1]["cycle_date"] == "2026-11-12"


def test_a_new_or_changed_setting_never_makes_past_cycles_overdue(env, sd, clock, sent):
    clock.set(2026, 10, 20, 10, 0)
    r = env.post("accounts", data={"name": "Visa", "type": "credit_card", "expected_form": "1",
                                   "expected_every_months": "1", "expected_day": "12", "reminders_on": "1"})
    assert r.status_code == 303
    with env.db() as c:
        visa = c.execute("SELECT id FROM accounts WHERE name = 'Visa'").fetchone()[0]
    prefs(env, [0, 1, 2])
    assert status(env, sd, visa, date(2026, 10, 20))["status"] == "upcoming"
    assert check(sd, clock) == 0 and "Statement overdue" not in env.get("dashboard").text
    clock.set(2026, 11, 13, 10, 0)
    assert status(env, sd, visa, date(2026, 11, 13))["status"] == "overdue"
    # changing the day on Nov 13: the 10th has passed, so nothing is overdue until Dec 10
    r = env.post("account-update", data={"id": str(visa), "name": "Visa", "expected_form": "1",
                                         "expected_every_months": "1", "expected_day": "10", "reminders_on": "1"})
    assert r.status_code == 303
    st = status(env, sd, visa, date(2026, 11, 13))
    assert st["status"] == "upcoming" and st["next_date"] == date(2026, 12, 10)
    # saving the form unchanged keeps it as it was (still upcoming, never moved later)
    with env.db() as c:
        before = c.execute("SELECT expected_set_at FROM accounts WHERE id = ?", (visa,)).fetchone()[0]
    clock.set(2026, 12, 11, 10, 0)
    env.post("account-update", data={"id": str(visa), "name": "Visa 2", "expected_form": "1",
                                     "expected_every_months": "1", "expected_day": "10", "reminders_on": "1"})
    with env.db() as c:
        assert c.execute("SELECT expected_set_at FROM accounts WHERE id = ?", (visa,)).fetchone()[0] == before
    assert status(env, sd, visa, date(2026, 12, 11))["status"] == "overdue"


def test_archived_and_deleted_accounts_are_never_overdue_and_restoring_doesnt_remind(env, sd, clock, sent):
    arch, gone = acct(env, "Archived", is_archived=1), acct(env, "Deleted", deleted_at="2026-10-01 00:00:00")
    prefs(env, [2, 5])
    clock.set(2026, 10, 17, 10, 0)
    assert status(env, sd, arch, date(2026, 10, 17)) is None and status(env, sd, gone, date(2026, 10, 17)) is None
    assert check(sd, clock) == 0 and "overdue" not in env.get("dashboard").text.split("<main")[1].split("Dashboard")[0]
    env.post("account-archive", data={"id": str(arch), "archived": "0"})
    env.post("account-restore", data={"id": str(gone)})
    for a in (arch, gone):
        assert status(env, sd, a, date(2026, 10, 17))["status"] == "upcoming"
    assert check(sd, clock) == 0
    assert check(sd, clock, 2026, 11, 14, 9, 30) == 1                     # Nov 12 on: reminded again
    assert len(sent) == 1 and "2 statements overdue" == sent[0]["title"]


# ------------------------------------------------------------------ reminder timing

def test_no_prefs_row_and_no_notifications_get_nothing(env, sd, clock, sent):
    acct(env)
    acct(env, "Bob's", user="bob")
    prefs(env, [], mode="off", user="bob")
    assert sends_over(sd, clock, date(2026, 10, 12), 20) == [] and sent == []
    with env.db() as c:
        assert c.execute("SELECT COUNT(*) FROM reminder_prefs WHERE user_id = 'tester'").fetchone()[0] == 0


def test_remind_me_with_no_choice_cant_be_saved(env):
    r = env.post("my-settings-reminders", data={"mode": "remind", "repeat_days": "0", "at_time": "09:00"})
    assert r.status_code == 400 and "Pick at least one, or choose No notifications." in r.text
    with env.db() as c:
        assert c.execute("SELECT COUNT(*) FROM reminder_prefs").fetchone()[0] == 0


def test_2_5_and_7_days_each_send_once_at_the_chosen_time(env, sd, clock, sent):
    acct(env)
    prefs(env, [2, 5, 7], at="09:00")
    got = sends_over(sd, clock, date(2026, 10, 12), 20)
    assert got == [(date(2026, 10, 14), 9), (date(2026, 10, 17), 9), (date(2026, 10, 19), 9)]
    assert [r["offset_days"] for r in log(env)] == [2, 5, 7]
    assert all(r["result"] == "sent" for r in log(env)) and len(sent) == 3


def test_on_the_day(env, sd, clock, sent):
    acct(env)
    prefs(env, [0], at="18:30")
    assert check(sd, clock, 2026, 10, 12, 18, 15) == 0
    assert check(sd, clock, 2026, 10, 12, 18, 30) == 1
    assert sent[0]["message"] == "Visa — expected Oct 12, today. Upload it in Finance."
    assert check(sd, clock, 2026, 10, 12, 18, 45) == 0


def test_keep_reminding_every_week_continues_and_stops_after_3_months(env, sd, clock, sent):
    acct(env, "Quarterly", every=3, day=1, frm="2026-01")              # Oct 1, then Jan 1
    prefs(env, [2], repeat=7)
    got = [d for d, _ in sends_over(sd, clock, date(2026, 10, 1), 100, hours=(10,))]
    weekly = [date(2026, 10, 3) + timedelta(days=7 * k) for k in range(13)]   # Oct 3 … Dec 26
    assert got == weekly + [date(2027, 1, 3)]           # then Jan 1's cycle, from its own day 2


def test_an_upload_between_two_reminders_stops_the_rest(env, sd, clock, sent):
    visa = acct(env)
    prefs(env, [2, 5, 7])
    assert check(sd, clock, 2026, 10, 14, 9, 0) == 1
    stmt(env, visa, "2026-10-15 12:00:00")
    assert sends_over(sd, clock, date(2026, 10, 15), 25) == []


def test_keep_reminding_stops_at_the_next_expected_date(env, sd, clock, sent):
    acct(env)
    prefs(env, [7], repeat=14)
    got = [d for d, _ in sends_over(sd, clock, date(2026, 10, 12), 31, hours=(10,))]
    # Oct 19, Nov 2; Nov 16 would be past Nov 12 (the next expected date); the Nov cycle's own day 7 is Nov 19
    assert got == [date(2026, 10, 19), date(2026, 11, 2)]
    with env.db() as c:
        assert {r[0] for r in c.execute("SELECT cycle_date FROM statement_reminders")} == {"2026-10-12"}


# ------------------------------------------------------------------ delivery

def test_two_accounts_due_together_come_as_one_notification(env, sd, clock, sent):
    acct(env, "Visa", day=12)
    acct(env, "Checking", day=10, type_="checking")
    prefs(env, [2, 4])
    assert check(sd, clock, 2026, 10, 14, 9, 0) == 1
    [n] = sent
    assert n["title"] == "2 statements overdue"
    assert n["message"] == "Checking (Oct 10), Visa (Oct 12). Upload them in Finance."
    assert len(log(env)) == 2


def test_a_restart_doesnt_resend(make_env, monkeypatch):
    first = make_env()
    acct(first, "Visa")
    prefs(first, [2])
    calls = []

    def setup(e):
        from app import statement_due
        from app.common import ha_notify, ha_people
        monkeypatch.setattr(statement_due, "now", lambda: datetime(2026, 10, 14, 9, 30))
        monkeypatch.setitem(ha_people._cache, "people", [{"entityId": "person.t", "name": "T", "userId": "tester",
                                                           "phones": [{"service": "mobile_app_t"}]}])
        monkeypatch.setitem(ha_people._cache, "at", time.monotonic())
        monkeypatch.setattr(ha_notify, "send_notify", lambda *a, **k: calls.append(a) or True)
        return statement_due

    assert setup(first).run_check() == 1
    again = make_env(tmp=first.dir)                     # the app restarted on the same database
    assert setup(again).run_check() == 0 and len(calls) == 1


def test_after_downtime_only_the_newest_missed_reminder_is_sent(env, sd, clock, sent):
    acct(env)
    prefs(env, [2, 5, 7])
    assert check(sd, clock, 2026, 10, 20, 10, 0) == 1                   # off from Oct 13 to Oct 20
    assert [r["offset_days"] for r in log(env)] == [7]
    assert sent[0]["message"] == "Visa — expected Oct 12, 8 days ago. Upload it in Finance."
    assert check(sd, clock, 2026, 10, 20, 10, 15) == 0


def test_a_failed_send_is_retried_then_given_up_after_6_hours(env, sd, clock, sent):
    acct(env)
    prefs(env, [2])
    sent.ok = False
    assert check(sd, clock, 2026, 10, 14, 9, 0) == 0
    assert log(env)[0]["result"] == "failed"
    check(sd, clock, 2026, 10, 14, 9, 15)
    check(sd, clock, 2026, 10, 14, 14, 45)
    assert len(sent) == 3                                               # retried at each check
    check(sd, clock, 2026, 10, 14, 15, 0)                               # six hours: given up
    check(sd, clock, 2026, 10, 14, 18, 0)
    assert len(sent) == 3 and log(env)[0]["result"] == "failed" and len(log(env)) == 1


def test_a_retry_that_works_is_recorded_as_sent(env, sd, clock, sent):
    acct(env)
    prefs(env, [2])
    sent.ok = False
    check(sd, clock, 2026, 10, 14, 9, 0)
    sent.ok = True
    assert check(sd, clock, 2026, 10, 14, 9, 15) == 1
    assert log(env)[0]["result"] == "sent" and log(env)[0]["sent_at"] == "2026-10-14 09:00:00"
    assert check(sd, clock, 2026, 10, 14, 9, 30) == 0


def test_no_phone_sends_nothing_and_the_card_says_so(env, sd, clock, monkeypatch):
    from app.common import ha_notify
    calls = []
    monkeypatch.setattr(ha_notify, "send_notify", lambda *a, **k: calls.append(a) or True)
    acct(env)
    prefs(env, [2])
    assert check(sd, clock, 2026, 10, 14, 9, 0) == 0 and calls == []
    assert log(env)[0]["result"] == "no_phone"
    assert "No phone found for you in Home Assistant" in env.get("my-settings").text


# ------------------------------------------------------------------ shared accounts

def test_only_the_owner_is_reminded_and_sees_the_notice(env, sd, clock, sent):
    for uid, name in (("tester", "Tester"), ("bob", "Bob")):
        env.insert("known_users", id=uid, name=name)
    env.insert("user_access", viewer_id="bob", owner_id="tester", granted_by="tester")
    acct(env)
    prefs(env, [2])
    prefs(env, [2], user="bob")
    clock.set(2026, 10, 14, 9, 0)
    page = env.get("dashboard", headers=BOB).text                       # bob opens into tester's data
    assert "Using <strong>Tester</strong>" in page
    assert "Statement overdue" not in page and 'class="nav-badge"' not in page
    assert "Statement overdue" not in env.get("uploads", headers=BOB).text
    assert "Next statement" not in env.get("accounts", headers=BOB).text
    assert check(sd, clock) == 1 and [c["service"] for c in sent] == ["mobile_app_tester"]
    assert "Statement overdue" in env.get("dashboard").text            # the owner sees it
    # an admin acting as the owner sees the owner's
    owner = acct(env, "Bob's card", user="bob")
    assert owner
    page = env.get("dashboard?as_user=bob").text
    assert "Bob&#39;s card" in page or "Bob's card" in page


# ------------------------------------------------------------------ the notification itself

def test_the_notification_has_no_account_number(env, sd, clock, sent):
    acct(env, "Visa 4111222233334444", last4="4444")
    prefs(env, [2])
    check(sd, clock, 2026, 10, 14, 9, 0)
    [n] = sent
    assert "4444" not in n["message"] + n["title"] and not re.search(r"\d{5,}", n["message"])
    assert n["message"].startswith("Visa ···· — expected Oct 12, 2 days ago.")


def test_the_tap_link_opens_upload_with_the_account_picked(env, sd, clock, sent):
    from app import tools
    tools.PANEL["value"] = "/a1b2c3d4_finance"
    try:
        visa = acct(env)
        acct(env, "Other", day=20, set_at="2026-10-01 00:00:00")     # set after Sep 20: not due until Oct 20
        prefs(env, [2])
        check(sd, clock, 2026, 10, 14, 9, 0)
    finally:
        tools.PANEL["value"] = None
    [n] = sent
    assert n["data"] == {"url": f"/a1b2c3d4_finance/uploads/{visa}", "clickAction": f"/a1b2c3d4_finance/uploads/{visa}"}
    r = env.get(f"uploads/{visa}")
    assert r.status_code == 307 and r.headers["location"] == f"../uploads?account={visa}"
    page = env.get(f"uploads?account={visa}").text
    assert re.search(rf'<option value="{visa}"[^>]*selected', page)
    assert env.get("uploads/abc").status_code == 404


def test_several_accounts_link_to_upload_with_none_picked(env, sd, clock, sent):
    from app import tools
    tools.PANEL["value"] = "/a1b2c3d4_finance"
    try:
        acct(env, "A")
        acct(env, "B")
        prefs(env, [2])
        check(sd, clock, 2026, 10, 14, 9, 0)
    finally:
        tools.PANEL["value"] = None
    assert sent[0]["data"]["url"] == "/a1b2c3d4_finance/uploads"


def test_extra_notify_services_from_admin_users_are_used(env, sd, clock, sent):
    env.insert("known_users", id="tester", name="Tester")
    r = env.post("user-notify-add", data={"user_id": "tester", "service": "notify.living_room_speaker"})
    assert r.status_code == 303
    assert env.post("user-notify-add", data={"user_id": "tester", "service": "bad name!"}).headers["location"].endswith(
        "error=notify_bad")
    page = env.get("admin-storage?tab=users").text
    assert "notify.living_room_speaker" in page
    acct(env)
    prefs(env, [2])
    check(sd, clock, 2026, 10, 14, 9, 0)
    assert sorted(c["service"] for c in sent) == ["living_room_speaker", "mobile_app_tester"]
    env.post("user-notify-remove", data={"user_id": "tester", "service": "notify.living_room_speaker"})
    with env.db() as c:
        assert c.execute("SELECT COUNT(*) FROM user_notify").fetchone()[0] == 0


# ------------------------------------------------------------------ App settings

def test_the_app_setting_off_stops_reminders_notices_and_the_badge_and_keeps_the_settings(env, sd, clock, sent):
    visa = acct(env)
    prefs(env, [2, 5])
    clock.set(2026, 10, 14, 9, 0)
    r = env.post("settings", data={"ai_provider": "ollama", "ai_url": "http://ollama.invalid:11434",
                                   "ai_model": "test-model", "feature_utilities": "1", "feature_tolls": "1"})
    assert r.status_code == 303
    from app import settings
    assert settings.get("statement_reminders") == "0"
    assert check(sd, clock) == 0 and sent == []
    for url in ("dashboard", "uploads"):
        page = env.get(url).text
        assert "Statement overdue" not in page and 'class="nav-badge"' not in page
    assert "Next statement" not in env.get("accounts").text
    assert "turned off for the whole app" in env.get("my-settings").text
    with env.db() as c:
        assert tuple(c.execute("SELECT expected_every_months, expected_day FROM accounts WHERE id = ?",
                                (visa,)).fetchone()) == (1, 12)
        assert c.execute("SELECT COUNT(*) FROM reminder_prefs").fetchone()[0] == 1
    env.post("settings", data={"ai_provider": "ollama", "ai_url": "http://ollama.invalid:11434", "ai_model": "test-model",
                               "statement_reminders": "1"})
    assert "Statement overdue" in env.get("dashboard").text and check(sd, clock) == 1


def test_the_switch_defaults_on(env):
    from app import settings
    assert settings.SETTINGS["statement_reminders"].default == "1" and settings.get_bool("statement_reminders")
    assert "Statement reminders" in env.get("settings").text


# ------------------------------------------------------------------ data

def test_one_persons_data_keeps_the_account_columns_prefs_and_dismissals(env, sd, clock):
    from test_people_data import _export, _import
    env.insert("known_users", id="tester", name="Tester")
    env.insert("known_users", id="wife", name="Wife")
    visa = acct(env, "Visa", every=3, day=0, frm="2026-02", reminders_on=0)
    prefs(env, [2, 7], repeat=14, at="07:45")
    env.insert("statement_dismissals", account_id=visa, cycle_date="2026-08-31", dismissed_by="tester")
    env.insert("statement_reminders", user_id="tester", account_id=visa, cycle_date="2026-08-31", offset_days=2,
               result="sent")
    data = _export(env)
    names = __import__("zipfile").ZipFile(io.BytesIO(data)).namelist()
    assert names
    r = _import(env, data, uid="wife")
    assert r.status_code == 200, r.text
    with env.db() as c:
        a = c.execute("SELECT * FROM accounts WHERE user_id = 'wife'").fetchone()
        assert (a["expected_every_months"], a["expected_day"], a["expected_from"], a["reminders_on"]) == (3, 0, "2026-02", 0)
        p = c.execute("SELECT * FROM reminder_prefs WHERE user_id = 'wife'").fetchone()
        assert (p["mode"], json.loads(p["offsets"]), p["repeat_days"], p["at_time"]) == ("remind", [2, 7], 14, "07:45")
        d = c.execute("SELECT cycle_date FROM statement_dismissals WHERE account_id = ?", (a["id"],)).fetchone()
        assert d["cycle_date"] == "2026-08-31"
        # the send log isn't carried
        assert c.execute("SELECT COUNT(*) FROM statement_reminders WHERE account_id = ?", (a["id"],)).fetchone()[0] == 0
    # importing again into someone who already has a My reminders keeps theirs (one per person)
    assert _import(env, data, uid="wife").status_code == 200
    with env.db() as c:
        assert c.execute("SELECT COUNT(*) FROM reminder_prefs WHERE user_id = 'wife'").fetchone()[0] == 1


def test_a_schema_28_file_still_imports(env):
    from test_people_data import _craft, _import
    env.insert("known_users", id="tester", name="Tester")
    old = {"accounts": [{"id": 1, "name": "Old", "type": "checking"}]}
    r = _import(env, _craft(old, schema=28))
    assert r.status_code == 200, r.text
    with env.db() as c:
        a = c.execute("SELECT * FROM accounts WHERE name = 'Old'").fetchone()
        assert (a["expected_every_months"], a["reminders_on"]) == (None, 1)


def test_delete_and_wipe_remove_that_persons_rows(env, sd, clock):
    from test_people_data import _delete
    for uid, name in (("tester", "Tester"), ("bob", "Bob")):
        env.insert("known_users", id=uid, name=name)

    def seed(user):
        a = acct(env, f"{user} card", user=user)
        prefs(env, [2], user=user)
        env.insert("statement_dismissals", account_id=a, cycle_date="2026-09-12", dismissed_by=user)
        env.insert("statement_reminders", user_id=user, account_id=a, cycle_date="2026-09-12", offset_days=2,
                   result="sent")
        env.insert("user_notify", user_id=user, service="notify.x_" + user)

    def left(user):
        with env.db() as c:
            return [c.execute(f"SELECT COUNT(*) FROM {t} WHERE {w}", (user,)).fetchone()[0] for t, w in (
                ("reminder_prefs", "user_id = ?"), ("statement_reminders", "user_id = ?"),
                ("user_notify", "user_id = ?"),
                ("statement_dismissals", "dismissed_by = ?"))]
    seed("tester")
    seed("bob")
    r = env.post("user-wipe?as_user=bob", data={"confirm_text": "Bob"})
    assert r.status_code == 303
    assert left("bob") == [0, 0, 0, 0] and left("tester") == [1, 1, 1, 1]
    r = _delete(env, "tester", "Tester")
    assert r.status_code == 200, r.text
    assert left("tester") == [0, 0, 0, 0]


def test_the_send_log_is_purged_after_a_year(env):
    from app import purge
    visa = acct(env)
    env.insert("statement_reminders", user_id="tester", account_id=visa, cycle_date="2024-09-12", offset_days=2,
               result="sent", sent_at="2024-09-14 09:00:00")
    env.insert("statement_reminders", user_id="tester", account_id=visa, cycle_date="2099-09-12", offset_days=2,
               result="sent", sent_at="2099-09-14 09:00:00")
    with env.db() as c:
        c.row_factory = sqlite3.Row
        counts = purge.purge_soft_deleted(c)
    assert counts["statement_reminders"] == 1
    assert [r["cycle_date"] for r in log(env)] == ["2099-09-12"]


def test_migration_0029_runs_on_an_old_database(tmp_path, monkeypatch, env):
    from app import db
    old_dir = tmp_path / "old_migrations"
    old_dir.mkdir()
    for p in sorted(db.MIGRATIONS_DIR.glob("*.sql")):
        if int(p.name[:4]) <= 28:
            (old_dir / p.name).write_text(p.read_text())
    path = str(tmp_path / "old.db")
    monkeypatch.setattr(db, "DB_PATH", path)
    monkeypatch.setattr(db, "MIGRATIONS_DIR", old_dir)
    db.run_migrations()
    c = sqlite3.connect(path)
    with c:
        c.execute("INSERT INTO accounts (user_id, name, type, preferred_upload_method) VALUES ('u', 'A', 'checking', 'qfx')")
    c.close()
    monkeypatch.setattr(db, "MIGRATIONS_DIR", db.Path(db.__file__).parent.parent / "migrations")
    db.run_migrations()
    c = sqlite3.connect(path)
    assert c.execute("SELECT preferred_upload_method, expected_every_months, expected_day, reminders_on, "
                     "expected_set_at FROM accounts").fetchone() == ("qfx", None, None, 1, None)
    tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"reminder_prefs", "statement_dismissals", "statement_reminders", "user_notify"} <= tables
    assert c.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 30     # the newest migration
    c.close()


def test_query_tab_describes_the_new_tables_and_hides_the_log(env):
    from app import query_engine as qe
    assert {"reminder_prefs", "statement_dismissals"} <= set(qe.REGISTRY)
    assert {"statement_reminders", "user_notify"} <= qe.HIDDEN_TABLES


# ------------------------------------------------------------------ pages

def test_my_reminders_card_saves_and_shows_each_field(env):
    page = env.get("my-settings").text
    assert "My reminders" in page and "You won&#39;t be reminded" in page or "You won't be reminded" in page
    r = env.post("my-settings-reminders", data={"mode": "remind", "offsets": ["2", "5", "7"], "repeat_days": "7",
                                                "at_time": "07:30"})
    assert r.status_code == 303 and r.headers["location"] == "my-settings?saved=1"
    page = env.get("my-settings?saved=1").text
    assert "Saved." in page and "You won" not in page
    assert re.search(r'name="mode" value="remind" checked', page)
    for v in ("2", "5", "7"):
        assert re.search(rf'name="offsets" value="{v}" checked', page)
    assert not re.search(r'name="offsets" value="3" checked', page)
    assert re.search(r'<option value="7" selected>Every week', page) and 'value="07:30"' in page
    with env.db() as c:
        p = c.execute("SELECT * FROM reminder_prefs WHERE user_id = 'tester'").fetchone()
        assert (p["mode"], p["offsets"], p["repeat_days"], p["at_time"]) == ("remind", "[2, 5, 7]", 7, "07:30")
    env.post("my-settings-reminders", data={"mode": "off", "offsets": ["2"]})
    with env.db() as c:
        p = c.execute("SELECT * FROM reminder_prefs WHERE user_id = 'tester'").fetchone()
        assert (p["mode"], p["offsets"]) == ("off", "[]")


def test_the_6_choice_limit(env):
    r = env.post("my-settings-reminders", data={"mode": "remind", "offsets": ["0", "1", "2", "3", "5", "7", "10"],
                                                "repeat_days": "0", "at_time": "09:00"})
    assert r.status_code == 400 and "Pick at most 6." in r.text
    r = env.post("my-settings-reminders", data={"mode": "remind", "offsets": ["0", "1", "2", "3", "5", "7"],
                                                "repeat_days": "0", "at_time": "09:00"})
    assert r.status_code == 303
    assert 'data-max="6"' in env.get("my-settings").text


def test_an_admin_acting_as_someone_edits_their_reminders(env):
    env.insert("known_users", id="bob", name="Bob")
    r = env.post("my-settings-reminders?as_user=bob", data={"mode": "remind", "offsets": ["3"], "at_time": "10:00"})
    assert r.status_code == 303 and r.headers["location"] == "my-settings?saved=1&as_user=bob"
    with env.db() as c:
        assert [r[0] for r in c.execute("SELECT user_id FROM reminder_prefs")] == ["bob"]
    assert "Bob's own settings" in env.get("my-settings?as_user=bob").text


def test_the_account_form_needs_a_day_and_a_starting_month(env):
    base = {"name": "Visa", "type": "credit_card", "expected_form": "1", "reminders_on": "1"}
    r = env.post("accounts", data={**base, "expected_every_months": "1"})
    assert r.status_code == 400 and "Pick the day the statement is ready" in r.text
    r = env.post("accounts", data={**base, "expected_every_months": "3", "expected_day": "5"})
    assert r.status_code == 400 and "Pick the month the statements count from" in r.text
    r = env.post("accounts", data={**base, "expected_every_months": "3", "expected_day": "last",
                                   "expected_from": "2026-02"})
    assert r.status_code == 303
    r = env.post("accounts", data={**base, "name": "Off", "expected_every_months": ""})
    assert r.status_code == 303
    with env.db() as c:
        rows = c.execute("SELECT name, expected_every_months, expected_day, expected_from, reminders_on FROM accounts "
                         "ORDER BY id").fetchall()
    assert [tuple(r) for r in rows] == [("Visa", 3, 0, "2026-02", 1), ("Off", None, None, None, 1)]
    page = env.get(f"account?id=1").text
    assert "Expected statement" in page and '<option value="last" selected>' in page


def test_the_next_statement_column_and_account_page(env, sd, clock):
    clock.set(2026, 10, 15, 10, 0)
    over = acct(env, "Over", day=12)
    soon = acct(env, "Soon", day=20, set_at="2026-10-01 00:00:00")
    gone = acct(env, "Dismissed one", day=1)
    env.insert("statement_dismissals", account_id=gone, cycle_date="2026-10-01", dismissed_by="tester")
    acct(env, "Off", every=None, day=None)
    for url in ("accounts", "transactions-accounts"):
        page = env.get(url).text
        assert "Next statement" in page and "Overdue 3 days" in page and ">Oct 20<" in page and ">Dismissed<" in page
    stmt(env, soon, "2026-10-10 09:00:00")
    page = env.get(f"account?id={soon}").text
    assert "Last statement arrived" in page and "2026-10-10 09:00" in page and "Expected Oct 20" in page
    assert "Overdue 3 days" in env.get(f"account?id={over}").text


def test_dismiss_on_home_and_on_upload(env, sd, clock):
    a, b = acct(env, "Visa"), acct(env, "Checking", day=10, type_="checking")
    clock.set(2026, 10, 15, 10, 0)
    page = env.get("uploads").text
    assert "2 statements overdue" in page
    assert re.search(rf'<option value="{b}"[^>]*selected', page)       # the first overdue one (Oct 10) is picked
    r = env.post("statement-dismiss", data={"account_id": str(b), "cycle_date": "2026-10-10", "next": "uploads"})
    assert r.headers["location"] == "uploads"
    page = env.get("uploads").text
    assert "Statement overdue" in page and "Checking</strong>" not in page
    env.post("statement-dismiss", data={"account_id": str(a), "cycle_date": "2026-10-12", "next": "dashboard"})
    assert "overdue" not in env.get("dashboard").text.lower().split("<main")[1].split("dashboard-filters")[0]


def test_the_notice_shows_for_someone_set_to_no_notifications(env, sd, clock):
    acct(env)
    prefs(env, [], mode="off")
    clock.set(2026, 10, 15, 10, 0)
    page = env.get("dashboard").text
    assert "Statement overdue" in page and "Visa</strong> &mdash; expected Oct 12" in page
    assert '<span class="nav-badge"' in page
