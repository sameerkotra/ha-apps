"""Card payment reminders and subscription change notifications (SPEC.md section 31), their data (migration 0031,
one person's data at schema 31, delete and wipe, the Query tab).

"Now" is frozen by replacing app.statement_due.now (the one clock the reminders read); nothing here talks to Home
Assistant: the phones come from ha_people's cache and ha_notify.send_notify is replaced by a recorder."""
import io
import json
import re
import sqlite3
import time
from datetime import date, datetime, timedelta

import pytest

BOB = {"x-remote-user-id": "bob", "x-remote-user-name": "Bob"}
MONTHLY = ["2026-06-12", "2026-07-12", "2026-08-12", "2026-09-12"]


class Clock:
    def __init__(self, monkeypatch, sd):
        self.at = datetime(2026, 10, 1, 8, 0)
        monkeypatch.setattr(sd, "now", lambda: self.at)

    def set(self, *args):
        self.at = datetime(*args)
        return self


@pytest.fixture
def al(env):
    from app import alerts
    return alerts


@pytest.fixture
def clock(monkeypatch, env):
    from app import statement_due
    return Clock(monkeypatch, statement_due)


class Calls(list):
    ok = True


@pytest.fixture
def sent(monkeypatch, env):
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


def card(env, name="Visa", day=12, set_at="2026-09-20 00:00:00", user="tester", last4=None, **extra):
    return env.insert("accounts", user_id=user, name=name, type="credit_card", payment_due_day=day,
                      payment_due_set_at=set_at, account_number_last4=last4, **extra)


def prefs(env, kind, offsets=(), at="09:00", user="tester", mode="on"):
    env.insert("notify_prefs", user_id=user, kind=kind, mode=mode, offsets=json.dumps(list(offsets)), at_time=at)


def check(al, clock, *at):
    if at:
        clock.set(*at)
    return al.run_check()


def log(env, table="payment_reminders"):
    with env.db() as c:
        return [dict(r) for r in c.execute(f"SELECT * FROM {table} ORDER BY id")]


def sends_over(al, clock, start, days, hours=(8, 9, 12, 18)):
    out = []
    for n in range(days):
        d = start + timedelta(days=n)
        for h in hours:
            clock.set(d.year, d.month, d.day, h, 0)
            if al.run_check():
                out.append((d, h))
    return out


def netflix(env, account_id, new=17.99, on="2026-09-12"):
    for d in MONTHLY[:3]:
        env.txn(account_id, d, -15.49, "NETFLIX")
    env.txn(account_id, on, -new, "NETFLIX")


# ------------------------------------------------------------------ the cards on Setup → Settings

def test_no_default_and_the_cards_say_so(env):
    page = env.get("my-settings").text
    for section in ('id="notify-cards"', 'id="notify-subs"'):
        assert section in page
    assert "No card payment reminders until you choose when." in page
    assert "No subscription change notifications until you choose them." in page
    assert re.search(r'id="cards-form"[^>]*data-max="6"', page)
    for v, label in (("-7", "7 days before"), ("0", "On the day"), ("3", "3 days after")):
        assert f'value="{v}" > {label}' in page
    with env.db() as c:
        assert c.execute("SELECT COUNT(*) FROM notify_prefs").fetchone()[0] == 0


def test_card_payments_need_1_to_6_choices(env):
    base = {"kind": "card_payments", "mode": "on", "at_time": "08:30"}
    r = env.post("my-settings-notify", data=base)
    assert r.status_code == 400 and "Pick at least one, or choose No notifications." in r.text
    r = env.post("my-settings-notify", data={**base, "offsets": ["-7", "-5", "-3", "-2", "-1", "0", "1"]})
    assert r.status_code == 400 and "Pick at most 6." in r.text
    r = env.post("my-settings-notify", data={**base, "offsets": ["-4"]})
    assert r.status_code == 400 and "isn&#39;t one of the choices" in r.text or "isn't one of the choices" in r.text
    r = env.post("my-settings-notify", data={**base, "at_time": "25:00", "offsets": ["0"]})
    assert r.status_code == 400 and "HH:MM" in r.text
    with env.db() as c:
        assert c.execute("SELECT COUNT(*) FROM notify_prefs").fetchone()[0] == 0
    r = env.post("my-settings-notify", data={**base, "offsets": ["-7", "-5", "-3", "-1", "0", "3"]})
    assert r.status_code == 303 and r.headers["location"] == "my-settings?saved=cards#notify-cards"
    page = env.get("my-settings?saved=cards").text
    cards = page.split('id="notify-cards"')[1].split("</section>")[0]
    assert "Saved." in cards and 'value="on" checked' in cards and 'value="08:30"' in cards
    for v in ("-7", "-5", "-3", "-1", "0", "3"):
        assert re.search(rf'name="offsets" value="{v}" checked', cards)
    assert not re.search(r'name="offsets" value="-2" checked', cards)
    with env.db() as c:
        p = c.execute("SELECT * FROM notify_prefs WHERE user_id = 'tester'").fetchone()
        assert (p["kind"], p["mode"], json.loads(p["offsets"]), p["at_time"]) == (
            "card_payments", "on", [-7, -5, -3, -1, 0, 3], "08:30")
    env.post("my-settings-notify", data={"kind": "card_payments", "mode": "off", "offsets": ["0"]})
    with env.db() as c:
        p = c.execute("SELECT mode, offsets FROM notify_prefs WHERE user_id = 'tester'").fetchone()
        assert tuple(p) == ("off", "[]")


def test_subscription_changes_card_saves(env):
    r = env.post("my-settings-notify", data={"kind": "subscriptions", "mode": "on", "at_time": "18:15"})
    assert r.status_code == 303 and r.headers["location"] == "my-settings?saved=subs#notify-subs"
    subs = env.get("my-settings?saved=subs").text.split('id="notify-subs"')[1].split("</section>")[0]
    assert "Saved." in subs and 'value="on" checked' in subs and 'value="18:15"' in subs
    assert env.post("my-settings-notify", data={"kind": "nonsense", "mode": "on"}).status_code == 400
    # the statements' My reminders card is unchanged by it
    with env.db() as c:
        assert c.execute("SELECT COUNT(*) FROM reminder_prefs").fetchone()[0] == 0


def test_an_admin_acting_as_someone_edits_theirs(env):
    env.insert("known_users", id="bob", name="Bob")
    r = env.post("my-settings-notify?as_user=bob", data={"kind": "subscriptions", "mode": "on", "at_time": "09:00"})
    assert r.headers["location"] == "my-settings?saved=subs&as_user=bob#notify-subs"
    with env.db() as c:
        assert [r[0] for r in c.execute("SELECT user_id FROM notify_prefs")] == ["bob"]


# ------------------------------------------------------------------ card payment reminders

def test_no_row_and_no_notifications_get_nothing(env, al, clock, sent):
    card(env)
    card(env, "Bob's", user="bob")
    prefs(env, "card_payments", user="bob", mode="off")
    assert sends_over(al, clock, date(2026, 10, 1), 20) == [] and sent == []


def test_each_chosen_day_sends_once_at_the_chosen_time(env, al, clock, sent):
    card(env, day=12)
    prefs(env, "card_payments", [-3, 0, 1], at="09:00")
    got = sends_over(al, clock, date(2026, 10, 1), 20)
    assert got == [(date(2026, 10, 9), 9), (date(2026, 10, 12), 9), (date(2026, 10, 13), 9)]
    assert [(r["due_date"], r["offset_days"]) for r in log(env)] == [("2026-10-12", -3), ("2026-10-12", 0),
                                                                    ("2026-10-12", 1)]
    assert [s["message"] for s in sent] == ["Visa — payment due Oct 12, in 3 days.",
                                            "Visa — payment due today (Oct 12).",
                                            "Visa — payment was due Oct 12, 1 day ago."]
    assert [s["title"] for s in sent] == ["Card payment due", "Card payment due", "Card payment overdue"]


def test_a_payment_or_mark_as_paid_stops_the_rest(env, al, clock, sent):
    a = card(env, day=12)
    prefs(env, "card_payments", [-7, -3, 0, 3])
    assert check(al, clock, 2026, 10, 5, 9, 0) == 1
    env.txn(a, "2026-10-06", -300.0, "PAYMENT THANK YOU")
    assert sends_over(al, clock, date(2026, 10, 6), 12) == []
    b = card(env, "Amex", day=20, set_at="2026-10-01 00:00:00")             # set after Sep 20
    assert check(al, clock, 2026, 10, 17, 9, 0) == 1                       # Amex: 3 days before Oct 20
    r = env.post("card-payment-paid", data={"account_id": str(b), "due_date": "2026-10-20"})
    assert r.status_code == 303
    assert sends_over(al, clock, date(2026, 10, 17), 8) == []


def test_after_downtime_only_the_newest_is_sent_and_a_restart_never_repeats(env, al, clock, sent, make_env, monkeypatch):
    card(env, day=12)
    prefs(env, "card_payments", [-7, -3, 0, 1])
    assert check(al, clock, 2026, 10, 12, 18, 0) == 1                      # off from Oct 1 to the evening of Oct 12
    assert [r["offset_days"] for r in log(env)] == [0]
    again = make_env(tmp=env.dir)                                         # the app restarted on the same database
    from app import alerts, statement_due
    monkeypatch.setattr(statement_due, "now", lambda: datetime(2026, 10, 12, 18, 15))
    assert alerts.run_check() == 0 and len(sent) == 1
    assert again


def test_several_cards_together_are_one_notification_with_no_number_and_a_link(env, al, clock, sent):
    from app import tools
    tools.PANEL["value"] = "/a1b2c3d4_finance"
    try:
        card(env, "Visa 4111222233334444", day=12, last4="4444")
        card(env, "Amex", day=13)
        prefs(env, "card_payments", [-2])
        check(al, clock, 2026, 10, 11, 9, 0)
    finally:
        tools.PANEL["value"] = None
    [n] = sent
    assert n["title"] == "2 card payments due" and n["message"] == "Visa ···· (Oct 12), Amex (Oct 13)."
    assert "4444" not in n["message"] and not re.search(r"\d{5,}", n["message"])
    assert n["data"]["url"] == "/a1b2c3d4_finance/dashboard"
    assert len(log(env)) == 2


def test_the_tap_opens_the_cards_page(env, al, clock, sent):
    from app import tools
    tools.PANEL["value"] = "/a1b2c3d4_finance"
    try:
        a = card(env, day=12)
        prefs(env, "card_payments", [0])
        check(al, clock, 2026, 10, 12, 9, 0)
    finally:
        tools.PANEL["value"] = None
    assert sent[0]["data"] == {"url": f"/a1b2c3d4_finance/accounts/{a}", "clickAction": f"/a1b2c3d4_finance/accounts/{a}"}
    r = env.get(f"accounts/{a}")
    assert r.status_code == 307 and r.headers["location"] == f"../account?id={a}"
    assert env.get("accounts/abc").status_code == 404


def test_a_failed_send_is_retried_for_6_hours(env, al, clock, sent):
    card(env, day=12)
    prefs(env, "card_payments", [0])
    sent.ok = False
    assert check(al, clock, 2026, 10, 12, 9, 0) == 0 and log(env)[0]["result"] == "failed"
    check(al, clock, 2026, 10, 12, 9, 15)
    check(al, clock, 2026, 10, 12, 14, 45)
    assert len(sent) == 3
    check(al, clock, 2026, 10, 12, 15, 0)
    assert len(sent) == 3 and log(env)[0]["result"] == "failed"
    # a retry that works is recorded as sent, with its first try kept
    card(env, "Amex", day=13)
    prefs(env, "card_payments", [0], user="bob")
    check(al, clock, 2026, 10, 13, 9, 0)
    sent.ok = True
    assert check(al, clock, 2026, 10, 13, 9, 15) == 1
    amex = [r for r in log(env) if r["due_date"] == "2026-10-13"][0]
    assert (amex["result"], amex["sent_at"]) == ("sent", "2026-10-13 09:00:00")


def test_owner_only_and_no_phone(env, al, clock, sent, monkeypatch):
    for uid, name in (("tester", "Tester"), ("bob", "Bob")):
        env.insert("known_users", id=uid, name=name)
    env.insert("user_access", viewer_id="bob", owner_id="tester", granted_by="tester")
    card(env, day=12)
    prefs(env, "card_payments", [0])
    prefs(env, "card_payments", [0], user="bob")
    assert check(al, clock, 2026, 10, 12, 9, 0) == 1 and [c["service"] for c in sent] == ["mobile_app_tester"]
    from app.common import ha_people
    monkeypatch.setitem(ha_people._cache, "people", [])
    card(env, "Amex", day=13)
    assert check(al, clock, 2026, 10, 13, 9, 0) == 0
    assert [r["result"] for r in log(env)][-1] == "no_phone"
    assert "No phone found for you in Home Assistant" in env.get("my-settings").text.split('id="notify-cards"')[1]


# ------------------------------------------------------------------ subscription changes

def test_each_change_is_sent_once_at_the_chosen_time_after_it_was_found(env, al, clock, sent):
    from app import tools
    a = env.account()
    netflix(env, a)
    prefs(env, "subscriptions", at="09:00")
    tools.PANEL["value"] = "/a1b2c3d4_finance"
    try:
        assert check(al, clock, 2026, 10, 1, 11, 0) == 0                     # found after 09:00: tomorrow
        assert log(env, "subscription_alerts")[0]["result"] == "pending"
        assert sends_over(al, clock, date(2026, 10, 2), 5) == [(date(2026, 10, 2), 9)]
    finally:
        tools.PANEL["value"] = None
    [n] = sent
    assert n["title"] == "Subscription price change"
    assert n["message"] == "NETFLIX 15.49 → 17.99 (+16%) since Sep 12."
    assert n["data"]["url"] == "/a1b2c3d4_finance/recurring"
    [row] = log(env, "subscription_alerts")
    assert (row["result"], row["found_at"], row["sent_at"]) == ("sent", "2026-10-01 11:00:00", "2026-10-02 09:00:00")


def test_several_changes_are_one_notification_and_found_before_the_time_goes_that_day(env, al, clock, sent):
    a = env.account()
    netflix(env, a)
    for d in ("2026-08-01", "2026-09-01", "2026-09-30"):
        env.txn(a, d, -9.99, "SPOTIFY")
    prefs(env, "subscriptions", at="09:00")
    assert check(al, clock, 2026, 10, 1, 8, 45) == 0
    assert check(al, clock, 2026, 10, 1, 9, 0) == 1
    [n] = sent
    assert n["title"] == "2 subscription changes"
    assert n["message"] == "NETFLIX 15.49 → 17.99 (+16%) since Sep 12; New: SPOTIFY 9.99 monthly since Aug 1."
    assert check(al, clock, 2026, 10, 2, 9, 0) == 0


def test_a_dismissed_change_isnt_sent_and_a_later_different_one_is(env, al, clock, sent):
    from app import subscriptions
    a = env.account()
    netflix(env, a)
    prefs(env, "subscriptions", at="09:00")
    check(al, clock, 2026, 10, 1, 8, 0)
    with env.db() as c:
        ch = subscriptions.changes(c, "tester")[0]
    env.post("subscription-dismiss", data={"account_id": str(a), "series": ch["series"], "change_key": ch["change_key"]})
    assert sends_over(al, clock, date(2026, 10, 1), 3) == []
    env.txn(a, "2026-10-12", -17.99, "NETFLIX")
    env.txn(a, "2026-11-12", -19.99, "NETFLIX")
    assert sends_over(al, clock, date(2026, 11, 13), 2, hours=(9,)) == [(date(2026, 11, 13), 9)]
    assert "17.99 → 19.99" in sent[-1]["message"]


def test_subscription_notifications_owner_only_and_retry(env, al, clock, sent):
    for uid, name in (("tester", "Tester"), ("bob", "Bob")):
        env.insert("known_users", id=uid, name=name)
    env.insert("user_access", viewer_id="bob", owner_id="tester", granted_by="tester")
    netflix(env, env.account())
    prefs(env, "subscriptions", at="09:00", user="bob")
    assert check(al, clock, 2026, 10, 1, 9, 0) == 0 and sent == []          # bob only uses tester's data
    prefs(env, "subscriptions", at="09:00")
    sent.ok = False
    check(al, clock, 2026, 10, 1, 9, 15)                                    # found 09:15: tomorrow 09:00
    check(al, clock, 2026, 10, 2, 9, 0)
    assert len(sent) == 1 and log(env, "subscription_alerts")[0]["result"] == "failed"
    check(al, clock, 2026, 10, 2, 14, 45)
    check(al, clock, 2026, 10, 2, 15, 0)
    assert len(sent) == 2 and [c["service"] for c in sent] == ["mobile_app_tester"] * 2


# ------------------------------------------------------------------ App settings

def test_the_switches_default_on_and_off_stop_sending_and_keep_the_settings(env, al, clock, sent):
    from app import settings
    assert settings.SETTINGS["card_payment_reminders"].default == "1"
    assert settings.SETTINGS["subscription_alerts"].default == "1"
    page = env.get("settings").text
    assert "Card payment reminders" in page and "Subscription change alerts" in page
    card(env, day=12)
    netflix(env, env.account())
    prefs(env, "card_payments", [0])
    prefs(env, "subscriptions", at="09:00")
    r = env.post("settings", data={"ai_provider": "ollama", "ai_url": "http://ollama.invalid:11434",
                                   "ai_model": "test-model", "statement_reminders": "1"})
    assert r.status_code == 303
    assert settings.get("card_payment_reminders") == "0" and settings.get("subscription_alerts") == "0"
    assert check(al, clock, 2026, 10, 12, 9, 0) == 0 and sent == []
    page = env.get("my-settings").text
    assert "Card payment reminders are turned off for the whole app" in page
    assert "Subscription change alerts are turned off for the whole app" in page
    with env.db() as c:
        assert c.execute("SELECT COUNT(*) FROM notify_prefs").fetchone()[0] == 2
        assert c.execute("SELECT payment_due_day FROM accounts WHERE name = 'Visa'").fetchone()[0] == 12
    env.post("settings", data={"ai_provider": "ollama", "ai_url": "http://ollama.invalid:11434",
                               "ai_model": "test-model", "card_payment_reminders": "1", "subscription_alerts": "1"})
    assert check(al, clock, 2026, 10, 12, 9, 15) == 1                      # the card's; the change was only
    assert len(sent) == 1 and sent[0]["title"] == "Card payment due"         # found now (after 09:00): tomorrow
    assert check(al, clock, 2026, 10, 13, 9, 0) == 1 and sent[-1]["title"] == "Subscription price change"


def test_one_loop_runs_both_and_one_failing_doesnt_stop_the_other(env, al, clock, sent, monkeypatch):
    from app import statement_due
    card(env, day=12)
    prefs(env, "card_payments", [0])
    monkeypatch.setattr(statement_due, "run_check", lambda at=None: 1 / 0)
    clock.set(2026, 10, 12, 9, 0)
    assert statement_due.run_all() == 1


# ------------------------------------------------------------------ data

def test_migration_0031_runs_on_an_old_database(tmp_path, monkeypatch, env):
    from app import db
    old_dir = tmp_path / "old_migrations"
    old_dir.mkdir()
    for p in sorted(db.MIGRATIONS_DIR.glob("*.sql")):
        if int(p.name[:4]) <= 30:
            (old_dir / p.name).write_text(p.read_text())
    path = str(tmp_path / "old.db")
    monkeypatch.setattr(db, "DB_PATH", path)
    monkeypatch.setattr(db, "MIGRATIONS_DIR", old_dir)
    db.run_migrations()
    c = sqlite3.connect(path)
    with c:
        c.execute("INSERT INTO accounts (user_id, name, type, expected_day, skip_confirmation) "
                  "VALUES ('u', 'Visa', 'credit_card', 12, 1)")
    c.close()
    monkeypatch.setattr(db, "MIGRATIONS_DIR", db.Path(db.__file__).parent.parent / "migrations")
    db.run_migrations()
    c = sqlite3.connect(path)
    assert c.execute("SELECT name, expected_day, skip_confirmation, payment_due_day, payment_due_set_at FROM accounts"
                     ).fetchone() == ("Visa", 12, 1, None, None)
    tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"notify_prefs", "payment_dismissals", "payment_reminders", "subscription_dismissals",
            "subscription_alerts"} <= tables
    assert c.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 32     # the newest migration
    with pytest.raises(sqlite3.IntegrityError):
        with c:
            c.execute("UPDATE accounts SET payment_due_day = 32")
    with pytest.raises(sqlite3.IntegrityError):
        with c:
            c.execute("INSERT INTO notify_prefs (user_id, kind) VALUES ('u', 'statements')")
    c.close()


def test_one_persons_data_schema_31_carries_the_new_rows_but_not_the_logs(env):
    from test_people_data import _export, _import
    from app import people_data as pdata
    assert pdata.SCHEMA >= 31 and 30 in pdata.UPGRADES
    env.insert("known_users", id="tester", name="Tester")
    env.insert("known_users", id="wife", name="Wife")
    visa = card(env, day=0)
    prefs(env, "card_payments", [-3, 0], at="07:45")
    prefs(env, "subscriptions", at="20:00")
    env.insert("payment_dismissals", account_id=visa, due_date="2026-09-30", dismissed_by="tester")
    env.insert("subscription_dismissals", account_id=visa, series="NETFLIX",
               change_key="price:15.49>17.99:2026-09-12", dismissed_by="tester")
    env.insert("payment_reminders", user_id="tester", account_id=visa, due_date="2026-09-30", offset_days=0,
               result="sent")
    env.insert("subscription_alerts", user_id="tester", account_id=visa, series="NETFLIX", change_key="k",
               result="sent")
    data = _export(env)
    import zipfile
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert json.loads(z.read("manifest.json"))["schema"] == pdata.SCHEMA
        names = z.namelist()
    assert "data/notify_prefs.json" in names and "data/payment_reminders.json" not in names
    assert "data/subscription_alerts.json" not in names
    r = _import(env, data, uid="wife")
    assert r.status_code == 200, r.text
    with env.db() as c:
        a = c.execute("SELECT * FROM accounts WHERE user_id = 'wife'").fetchone()
        assert a["payment_due_day"] == 0
        got = {r["kind"]: (r["mode"], json.loads(r["offsets"]), r["at_time"])
               for r in c.execute("SELECT * FROM notify_prefs WHERE user_id = 'wife'")}
        assert got == {"card_payments": ("on", [-3, 0], "07:45"), "subscriptions": ("on", [], "20:00")}
        assert c.execute("SELECT due_date FROM payment_dismissals WHERE account_id = ?", (a["id"],)).fetchone()[0] \
            == "2026-09-30"
        assert c.execute("SELECT change_key FROM subscription_dismissals WHERE account_id = ?",
                         (a["id"],)).fetchone()[0] == "price:15.49>17.99:2026-09-12"
        for t in ("payment_reminders", "subscription_alerts"):
            assert c.execute(f"SELECT COUNT(*) FROM {t} WHERE account_id = ?", (a["id"],)).fetchone()[0] == 0
    assert _import(env, data, uid="wife").status_code == 200                # again: theirs kept, one per kind
    with env.db() as c:
        assert c.execute("SELECT COUNT(*) FROM notify_prefs WHERE user_id = 'wife'").fetchone()[0] == 2


def test_a_schema_30_file_still_imports_and_bad_rows_are_refused(env):
    from test_people_data import _craft, _import, _upload
    env.insert("known_users", id="tester", name="Tester")
    r = _import(env, _craft({"accounts": [{"id": 1, "name": "Old card", "type": "credit_card"}]}, schema=30))
    assert r.status_code == 200, r.text
    with env.db() as c:
        assert c.execute("SELECT payment_due_day FROM accounts WHERE name = 'Old card'").fetchone()[0] is None
    for tables in ({"notify_prefs": [{"id": 1, "kind": "card_payments", "mode": "on", "offsets": "[-4]",
                                      "at_time": "09:00"}]},
                   {"notify_prefs": [{"id": 1, "kind": "card_payments", "mode": "on",
                                      "offsets": json.dumps([-7, -5, -3, -2, -1, 0, 1]), "at_time": "09:00"}]},
                   {"notify_prefs": [{"id": 1, "kind": "email", "mode": "on", "offsets": "[]", "at_time": "09:00"}]},
                   {"accounts": [{"id": 1, "name": "Chk", "type": "checking", "payment_due_day": 5}]}):
        r = _upload(env, _craft(tables))
        assert r.status_code in (400, 422), (tables, r.text)


def test_delete_and_wipe_remove_the_new_rows(env):
    from test_people_data import _delete
    for uid, name in (("tester", "Tester"), ("bob", "Bob")):
        env.insert("known_users", id=uid, name=name)

    def seed(user):
        a = card(env, f"{user} card", user=user)
        prefs(env, "card_payments", [0], user=user)
        env.insert("payment_dismissals", account_id=a, due_date="2026-09-12", dismissed_by=user)
        env.insert("subscription_dismissals", account_id=a, series="X", change_key="new:X@1.00", dismissed_by=user)
        env.insert("payment_reminders", user_id=user, account_id=a, due_date="2026-09-12", offset_days=0, result="sent")
        env.insert("subscription_alerts", user_id=user, account_id=a, series="X", change_key="k", result="pending")

    def left(user):
        with env.db() as c:
            return [c.execute(f"SELECT COUNT(*) FROM {t} WHERE {w}", (user,)).fetchone()[0] for t, w in (
                ("notify_prefs", "user_id = ?"), ("payment_reminders", "user_id = ?"),
                ("subscription_alerts", "user_id = ?"), ("payment_dismissals", "dismissed_by = ?"),
                ("subscription_dismissals", "dismissed_by = ?"))]
    seed("tester")
    seed("bob")
    r = env.post("user-wipe?as_user=bob", data={"confirm_text": "Bob"})
    assert r.status_code == 303
    assert left("bob") == [0] * 5 and left("tester") == [1] * 5
    r = _delete(env, "tester", "Tester")
    assert r.status_code == 200, r.text
    assert left("tester") == [0] * 5


def test_the_send_logs_are_purged_after_a_year(env):
    from app import purge
    a = card(env)
    env.insert("payment_reminders", user_id="tester", account_id=a, due_date="2024-09-12", offset_days=0,
               result="sent", sent_at="2024-09-12 09:00:00")
    env.insert("subscription_alerts", user_id="tester", account_id=a, series="X", change_key="old",
               found_at="2024-09-12 09:00:00", result="sent")
    env.insert("subscription_alerts", user_id="tester", account_id=a, series="X", change_key="new",
               found_at="2099-09-12 09:00:00", result="pending")
    with env.db() as c:
        c.row_factory = sqlite3.Row
        counts = purge.purge_soft_deleted(c)
    assert counts["alert_logs"] == 2
    assert [r["change_key"] for r in log(env, "subscription_alerts")] == ["new"] and log(env) == []


def test_query_tab_describes_the_new_tables_and_hides_the_logs(env):
    from app import query_engine as qe
    assert {"notify_prefs", "payment_dismissals", "subscription_dismissals"} <= set(qe.REGISTRY)
    assert {"payment_reminders", "subscription_alerts"} <= qe.HIDDEN_TABLES
