"""What Finance Dashboard answers the Household Assistant (app/tools.py; HOUSEHOLD_ASSISTANT_SPEC §4.2, §7.2).

- off until an admin turns it on; the asking person must be known to the app;
- the person's own data, or a shared owner's by name — never anyone else's, and an admin can't ask "as" someone;
- finance.summary / finance.spending count as the Overview does (clean rows; no transfers or excluded rows);
- never a transaction's note; finance.recurring and finance.bills from the recurring-charge scan;
- the bus's tables are hidden from reports.

All people, merchants and amounts here are invented."""
from datetime import date, datetime, timedelta, timezone


def call(tool, uid="tester", **args):
    from app import db, tools
    from app.common import app_bus
    now = datetime.now(timezone.utc)
    env = {"id": app_bus.new_ulid(now), "v": 1, "from": "household_assistant", "to": "finance",
           "kind": "assist.tool.call", "kv": 1, "reply_to": None, "ref": None, "sent": now.isoformat(),
           "expires": (now + timedelta(seconds=20)).isoformat(),
           "data": {"tool": tool, "args": args, "requested_by": uid, "question": "q1"}}
    with db.get_db() as conn:
        try:
            return tools.tools.on_call(app_bus.Message(env), conn)
        except app_bus.Nack as n:
            return n


def nack(res, reason, detail):
    from app.common import app_bus
    assert isinstance(res, app_bus.Nack), res
    assert (res.reason, res.detail) == (reason, detail)


def turn_on(env, on=True):
    with env.db() as c:
        c.execute("INSERT OR IGNORE INTO known_users (id, name) VALUES ('tester', 'Tester')")   # has opened the app
        c.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES ('assistant_answers', ?)", ("1" if on else "0",))


def seed(env):
    month = date.today().strftime("%Y-%m")
    chk = env.account("Checking")
    card = env.account("Visa", "credit_card")
    env.txn(chk, f"{month}-01", 3000.0, "PAYROLL ACME", category="Income", txn_type="income")
    env.txn(card, f"{month}-02", 120.5, "FRESHMART 0042", category="Groceries", note="birthday cake secret")
    env.txn(card, f"{month}-03", 45.25, "CORNER DELI", category="Groceries")
    env.txn(chk, f"{month}-04", -900.0, "RENT JULY", category="Housing")
    env.txn(chk, f"{month}-05", -500.0, "TO SAVINGS", category="Transfer", txn_type="transfer")
    env.txn(chk, f"{month}-06", -70.0, "EXCLUDED THING", category="Shopping", is_excluded=1)
    env.insert("known_users", id="other", name="Robin")
    env.txn(env.account("Robin's", user="other"), f"{month}-02", -60.0, "ROBIN SHOP", user="other", category="Fun")
    return month


def test_off_until_turned_on_and_who_may_ask(env):
    seed(env)
    from app import settings
    assert settings.SETTINGS["assistant_answers"].default == "0"
    turn_on(env, False)
    nack(call("finance.summary"), "not_allowed", "off")
    turn_on(env)
    nack(call("finance.summary", uid="stranger"), "not_allowed", "no_access")


def test_summary_and_spending_count_as_the_overview(env):
    month = seed(env)
    turn_on(env)
    res = call("finance.summary")
    assert "income 3,000.00, spending 1,065.75, net 1,934.25" in res["text"]
    assert res["items"][0] == {"category": "Housing", "spend": 900.0}
    res = call("finance.spending", month=month)
    assert [(i["category"], i["spend"]) for i in res["items"]] == [("Housing", 900.0), ("Groceries", 165.75)]
    res = call("finance.spending", category="groc")
    assert [(i["merchant"], i["amount"]) for i in res["items"]] == [("FRESHMART 0042", 120.5), ("CORNER DELI", 45.25)]
    assert "birthday cake" not in str(res)                                 # never a note
    assert "No spending on “Travel”" in call("finance.spending", category="Travel")["text"]


def test_only_own_or_shared_data(env):
    seed(env)
    turn_on(env)
    assert "ROBIN" not in str(call("finance.spending", category="Fun"))
    nack(call("finance.summary", person="Robin"), "not_found", "person")      # not shared with tester
    with env.db() as c:
        c.execute("INSERT INTO user_access (viewer_id, owner_id, granted_by) VALUES ('tester', 'other', 'tester')")
    res = call("finance.spending", person="robin", category="Fun")
    assert res["items"][0]["merchant"] == "ROBIN SHOP"
    assert "(Robin's)" in res["text"]


def test_recurring_and_bills(env):
    turn_on(env)
    chk = env.account("Checking")
    today = date.today()
    for months_back in (3, 2, 1):
        y, m = today.year, today.month - months_back
        while m <= 0:
            y, m = y - 1, m + 12
        env.txn(chk, date(y, m, 10).isoformat(), -15.99, "STREAMFLIX MONTHLY", category="Subscriptions")
    res = call("finance.recurring")
    assert res["items"][0]["merchant"] == "STREAMFLIX MONTHLY"
    assert res["items"][0]["frequency"] == "Monthly"
    nxt = res["items"][0]["next"]
    days = (date.fromisoformat(nxt) - today).days
    if 0 <= days <= 60:
        assert call("finance.bills", days=60)["items"][0]["merchant"] == "STREAMFLIX MONTHLY"
    nack(call("finance.bills", days=61), "invalid", "days")


def test_catalogue_and_reports_never_see_the_bus(env):
    from app import query_engine, tools
    tools.tools.check()
    assert [t["name"] for t in tools.tools.spec()] == ["finance.summary", "finance.spending", "finance.recurring",
                                                       "finance.bills"]
    assert {"bus_outbox", "bus_seen", "bus_apps"} <= query_engine.HIDDEN_TABLES


def test_links_open_the_right_page(env):
    from app import tools
    month = seed(env)
    turn_on(env)
    tools.PANEL["value"] = "/local_finance"
    try:
        res = call("finance.summary", month=month)
        assert res["links"][0]["target"] == f"/month/{month}"
        assert res["links"][0]["panel"] == "/local_finance"
        assert call("finance.recurring")["links"][0]["target"] == "/recurring"
        page = env.get("dashboard")
        assert 'data-page="/local_finance"' in page.text
        assert "static/common/deeplink.js" in page.text
    finally:
        tools.PANEL["value"] = None
    r = env.get(f"month/{month}", follow_redirects=False)
    assert (r.status_code, r.headers["location"]) == (307, f"../dashboard?period={month}")
    assert env.get("month/2026-13", follow_redirects=False).status_code == 404
