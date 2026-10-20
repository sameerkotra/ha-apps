"""Income categories (SPEC.md section 9; FINANCE_INCOME_CATEGORIES_SPEC.md §1–§9, tests §8): every category is
Spending or Income, "Income" became "Other income" (migration 0032), grouped menus ordered by direction, the income
built-in rules, the AI offered only the right list, card refunds, Sort my income, Home's Income breakdown and
Compare, the Household Assistant's income line and finance.income, and one person's data at schema 32.

All people, merchants and amounts here are invented."""
import io
import json
import os
import re
import sqlite3
import zipfile
from datetime import date
from pathlib import Path

import pytest

from test_tools import call, nack, turn_on

HTML = {"accept": "text/html"}
JSON = {"accept": "application/json"}
WIFE = {"x-remote-user-id": "wife", "x-remote-user-name": "Wife"}


def figures(page: str) -> list[float]:
    """Home's Income and Spend figures, in that order."""
    return [float(sign + x.replace(",", "")) for sign, x in
            re.findall(r'<p class="dashboard-figure"><span class="money[^"]*">(-?)\$([\d.,]+)</span></p>', page)]


def section(page: str, start: str, end: str) -> str:
    i = page.index(start)
    return page[i:page.index(end, i + len(start))]


def table_rows(html: str) -> list[tuple[str, float]]:
    """(category, amount) of each row of a Home breakdown table."""
    out = []
    for row in re.findall(r"<tr>\s*<td data-label=\"Category\">(.*?)</tr>", html, re.S):
        name = re.sub(r"<[^>]+>", "", row.split("</td>")[0]).strip()
        sign, amount = re.search(r'<td data-label="Amount"><span class="money[^"]*">(-?)\$([\d.,]+)</span></td>', row).groups()
        out.append((name, float(sign + amount.replace(",", ""))))
    return out


def groups_in(select_html: str) -> list[str]:
    return re.findall(r'<optgroup label="(\w+)">', select_html)


# ------------------------------------------------------------------ migration 0032

OLD_SPEND = ("((a.type = 'credit_card' AND t.amount > 0) OR (a.type != 'credit_card' AND t.amount < 0))")


def test_migration_0032_renames_income_keeps_home_totals_and_custom_categories_are_spending(make_env, tmp_path):
    tmp = str(tmp_path)
    path = os.path.join(tmp, "finance.db")
    migrations = Path(__file__).resolve().parent.parent / "migrations"
    c = sqlite3.connect(path)
    for p in sorted(migrations.glob("*.sql")):
        if int(p.name[:4]) <= 31:
            c.executescript(p.read_text())
    month = date.today().strftime("%Y-%m")
    c.execute("INSERT INTO accounts (id, user_id, name, type) VALUES (1, 'tester', 'Checking', 'checking')")
    c.execute("INSERT INTO accounts (id, user_id, name, type) VALUES (2, 'tester', 'Visa', 'credit_card')")
    rows = [(1, f"{month}-01", 3000.0, "ACME PAYROLL", "Income"), (1, f"{month}-02", 12.5, "INTEREST", "Income"),
            (1, f"{month}-03", -900.0, "RENT", "Housing"), (2, f"{month}-04", 45.0, "GROCER", "Groceries"),
            (2, f"{month}-05", -45.0, "PAYMENT", "Payments"), (1, f"{month}-06", 20.0, "PETS REFUND", "Pets")]
    c.executemany("INSERT INTO transactions (user_id, account_id, date, amount, description, category) "
                  "VALUES ('tester', ?, ?, ?, ?, ?)", rows)
    c.execute("INSERT INTO category_rules (user_id, pattern, match_type, category) VALUES ('tester', 'PAYROLL', 'substring', 'Income')")
    c.execute("INSERT INTO skipped_duplicates (user_id, account_id, date, amount, description, category, reason) "
              "VALUES ('tester', 1, ?, 3000.0, 'ACME PAYROLL', 'Income', 'repeated')", (f"{month}-01",))
    c.execute("INSERT INTO custom_categories (user_id, name) VALUES ('tester', 'Pets')")
    c.execute("INSERT INTO custom_categories (user_id, name) VALUES ('tester', 'salary')")     # a new built-in's name
    before_income = c.execute("SELECT SUM(t.amount) FROM transactions t JOIN accounts a ON a.id = t.account_id "
                              "WHERE t.amount > 0 AND a.type IN ('checking', 'savings')").fetchone()[0]
    before_spend = c.execute(f"SELECT SUM(CASE WHEN a.type = 'credit_card' THEN t.amount ELSE -t.amount END) "
                             f"FROM transactions t JOIN accounts a ON a.id = t.account_id WHERE {OLD_SPEND}").fetchone()[0]
    c.commit()
    c.close()

    env = make_env(tmp=tmp)                      # starts the app on it: 0032 runs
    with env.db() as c:
        assert c.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 32
        assert c.execute("SELECT COUNT(*) FROM transactions WHERE category = 'Income'").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM transactions WHERE category = 'Other income'").fetchone()[0] == 2
        assert c.execute("SELECT category FROM category_rules").fetchone()[0] == "Other income"
        assert c.execute("SELECT category FROM skipped_duplicates").fetchone()[0] == "Other income"
        assert [tuple(r) for r in c.execute("SELECT name, kind, colour FROM custom_categories")] == [("Pets", "spending", None)]
        cols = {r[1] for r in c.execute("PRAGMA table_info(category_rules)")}
        assert "last_matched_at" in cols
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("INSERT INTO custom_categories (user_id, name, kind) VALUES ('tester', 'X', 'savings')")
    page = env.get("dashboard?period=all").text
    assert figures(page) == [before_income, before_spend]
    from app import categorize
    with env.db() as c:
        assert categorize.categories_by_kind(c, "tester")["spending"].count("Pets") == 1
        assert "Salary" in categorize.categories_by_kind(c, "tester")["income"]


# ------------------------------------------------------------------ the Categories page: add, kinds, duplicates

def test_add_a_category_of_each_kind_and_change_a_kind(env):
    r = env.post("custom-categories", data={"name": "Rental income", "kind": "income", "tab": "income"})
    assert r.status_code == 303 and r.headers["location"] == "categories?tab=income&notice=cat_added"
    assert env.post("custom-categories", data={"name": "Pet Care"}).headers["location"] == "categories"   # as before
    with env.db() as c:
        kinds = {r["name"]: r["kind"] for r in c.execute("SELECT name, kind FROM custom_categories")}
        pet = c.execute("SELECT id FROM custom_categories WHERE name = 'Pet Care'").fetchone()[0]
    assert kinds == {"Rental income": "income", "Pet Care": "spending"}
    r = env.post("category-kind", data={"id": pet, "kind": "income", "tab": "spending"})
    assert r.headers["location"] == "categories?tab=spending&notice=cat_moved"
    from app import categorize
    with env.db() as c:
        groups = categorize.categories_by_kind(c, "tester")
    assert "Pet Care" in groups["income"] and "Pet Care" not in groups["spending"]
    assert "Rental income" in groups["income"]


def test_the_duplicate_check_covers_both_kinds_in_any_case(env):
    for name, kind in (("salary", "spending"), ("GROCERIES", "income"), ("Other Income", "spending")):
        r = env.post("custom-categories", data={"name": name, "kind": kind}, headers=HTML)
        assert r.status_code == 400 and "already exists" in r.text, name
    assert env.post("custom-categories", data={"name": "Freelance", "kind": "income"}).status_code == 303
    r = env.post("custom-categories", data={"name": "freelance", "kind": "spending"})
    assert r.status_code == 400 and "already exists" in r.json()["error"]
    assert env.post("custom-categories", data={"name": "X", "kind": "loan"}).status_code == 400
    with env.db() as c:
        assert [r[0] for r in c.execute("SELECT name FROM custom_categories")] == ["Freelance"]


# ------------------------------------------------------------------ grouped menus, ordered by direction

def _forms_by_id(page: str) -> dict[int, str]:
    out = {}
    for form in re.findall(r'<form class="category-form".*?</form>', page, re.S):
        out[int(re.search(r'name="id" value="(\d+)"', form).group(1))] = form
    return out


def test_every_menu_is_grouped_and_income_comes_first_for_money_in_on_checking(env):
    chk = env.account("Checking")
    sav = env.account("Savings", "savings")
    card = env.account("Visa", "credit_card")
    t_in = env.txn(chk, "2026-09-01", 3000.0, "ACME PAYROLL", category="Salary")
    t_sav = env.txn(sav, "2026-09-02", 1.5, "INTEREST", category="Interest")
    t_out = env.txn(chk, "2026-09-03", -40.0, "GROCER", category="Groceries")
    t_card = env.txn(card, "2026-09-04", 25.0, "DINER", category="Dining")
    t_card_back = env.txn(card, "2026-09-05", -25.0, "DINER REFUND", category="Refunds")
    env.txn(chk, "2026-09-06", -5.0, "FLAGGED", review_status="pending_review", category="Other")
    forms = _forms_by_id(env.get("transactions?searched=1").text)
    assert groups_in(forms[t_in]) == ["Income", "Spending"]
    assert groups_in(forms[t_sav]) == ["Income", "Spending"]
    for t in (t_out, t_card, t_card_back):
        assert groups_in(forms[t]) == ["Spending", "Income"], t
    assert 'value="Refunds" selected' in forms[t_card_back]           # a card refund's menu still lists Refunds
    page = env.get("transactions?searched=1").text
    bulk = section(page, '<select class="bulk-category-select"', "</select>")
    assert groups_in(bulk) == ["Spending", "Income"]                  # a mixed selection: Spending first
    assert groups_in(section(env.get("transactions?flow=income").text, '<select class="bulk-category-select"',
                             "</select>")) == ["Income", "Spending"]  # only money in on the page
    filt = section(page, '<select name="category"', "</select>")
    assert groups_in(filt) == ["Spending", "Income"] and "All categories" in filt
    review = env.get("review").text
    assert groups_in(section(review, '<select class="bulk-category-select"', "</select>")) == ["Spending", "Income"]
    assert groups_in(section(review, '<form class="category-form"', "</form>")) == ["Spending", "Income"]
    uploads = env.get("uploads").text
    manual = section(uploads, 'action="transaction-manual', "</form>")
    assert groups_in(manual) == ["Spending", "Income"] and 'value="Uncategorized" selected' in manual
    rules = section(env.get("categories?tab=rules").text, 'id="add-rule"', "</details>")
    assert groups_in(rules) == ["Spending", "Income"]
    for html in (forms[t_in], bulk, filt, manual, rules):
        assert 'value="Payments"' in html and 'value="Uncategorized"' in html
        assert 'value="Income"' not in html                            # the old name is gone


def test_all_income_quick_filter_shows_money_in_on_checking_and_savings(env):
    chk, card = env.account("Checking"), env.account("Visa", "credit_card")
    env.txn(chk, "2026-09-01", 3000.0, "ACME PAYROLL", category="Salary")
    env.txn(chk, "2026-09-02", -40.0, "GROCER", category="Groceries")
    env.txn(card, "2026-09-03", -40.0, "CARD PAYMENT", category="Payments")
    page = env.get("transactions").text
    assert 'id="quick-all-income"' in page and 'href="transactions?flow=income"' in page
    page = env.get("transactions?flow=income").text
    assert "ACME PAYROLL" in page and "GROCER" not in page and "CARD PAYMENT" not in page


# ------------------------------------------------------------------ categorizing

def test_the_built_in_income_rules_by_direction(env):
    from app import categorize
    rows = [(1, "ACME PAYROLL 1234", "in"), (2, "EMPLOYER DIRECT DEP", "in"), (3, "DIR DEP STATE", "in"),
            (4, "MONTHLY SALARY", "in"), (5, "INTEREST PAID", "in"), (6, "INTEREST EARNED JUN", "in"),
            (7, "INT PAID SAVINGS", "in"), (8, "VANGUARD DIVIDEND", "in"), (9, "AMAZON REFUND", "in"),
            (10, "ZELLE FROM ALEX REFUND", "in"), (11, "VENMO FROM SAM", "in"), (12, "TARGET", "in"),
            (13, "PAYROLL SERVICE FEE", "out"), (14, "TARGET 0042", "out"), (15, "AMAZON REFUND", "card_in"),
            (16, "PAYMENT THANK YOU", "card_in"), (17, "TARGET", "card")]
    with env.db() as c:
        plan = categorize.plan_categories(c, "tester", "", "", rows)
    got = {k: v[0] for k, v in plan.assignments.items()}
    assert got == {1: "Salary", 2: "Salary", 3: "Salary", 4: "Salary", 5: "Interest", 6: "Interest", 7: "Interest",
                   8: "Dividends", 9: "Refunds", 10: "Uncategorized", 11: "Uncategorized", 12: "Uncategorized",
                   13: "Uncategorized", 14: "Shopping", 15: "Refunds", 16: "Payments", 17: "Shopping"}
    assert plan.assignments[16][1] == "card_payment" and plan.assignments[1][1] == "builtin_rule"
    # a person's own rule wins over the built-in ones, for income as for spending
    env.insert("category_rules", user_id="tester", pattern="ACME PAYROLL", match_type="prefix", category="Freelance")
    with env.db() as c:
        plan = categorize.plan_categories(c, "tester", "", "", rows[:1])
    assert plan.assignments[1][:2] == ("Freelance", "user_rule")


def test_the_ai_is_offered_only_the_list_for_the_rows_direction(env, monkeypatch):
    from app import ai_client, categorize
    prompts = []

    class Reply:
        def __init__(self, text):
            self.text, self.raw = text, text

    def generate(prompt, **kw):
        prompts.append(prompt)
        n = prompt.count("\n", prompt.index("Descriptions:"))
        return Reply(json.dumps({"categories": {str(i): ("Gifts received" if "money coming in" in prompt else "Dining")
                                                for i in range(1, n + 1)}}))
    monkeypatch.setattr(ai_client, "generate", generate)
    env.insert("custom_categories", user_id="tester", name="Rental income", kind="income")
    env.insert("custom_categories", user_id="tester", name="Pet Care", kind="spending")
    with env.db() as c:
        plan = categorize.plan_categories(c, "tester", "http://ai", "m", [
            (1, "MYSTERY DEPOSIT", "in"), (2, "ODD SHOP", "out"), (3, "ODD CAFE", "card")])
    assert len(prompts) == 3
    by = {("MYSTERY DEPOSIT" in p, "ODD SHOP" in p, "ODD CAFE" in p): p for p in prompts}
    income, out, card = by[(True, False, False)], by[(False, True, False)], by[(False, False, True)]
    offered = lambda p: p.split("into one of these categories: ", 1)[1].split(".\n", 1)[0].split(", ")
    assert set(offered(income)) == set(categorize.INCOME_CATEGORIES) | {"Rental income"}
    assert set(offered(out)) == set(categorize.SPENDING_CATEGORIES) | {"Pet Care"}
    assert set(offered(card)) == set(categorize.SPENDING_CATEGORIES) | {"Pet Care", "Refunds"}
    assert "Groceries" not in income and "Salary" not in out and "Salary" not in card
    assert {k: v[0] for k, v in plan.assignments.items()} == {1: "Gifts received", 2: "Dining", 3: "Dining"}
    # a hidden built-in isn't offered
    env.insert("category_prefs", user_id="tester", name="Dividends", hidden=1)
    prompts.clear()
    with env.db() as c:
        categorize.plan_categories(c, "tester", "http://ai", "m", [(1, "MYSTERY DEPOSIT", "in")])
    assert "Dividends" not in offered(prompts[0])


def test_card_refunds_get_refunds_and_count_as_negative_spend(env):
    card = env.account("Visa", "credit_card")
    env.post("category-rules", data={"pattern": "STORE RETURN", "match_type": "prefix", "category": "Refunds"})
    csv = ("Date,Description,Amount\n2026-09-01,BIG STORE,80.00\n2026-09-02,SHOE SHOP REFUND,-30.00\n"
           "2026-09-03,STORE RETURN 77,-10.00\n2026-09-04,PAYMENT THANK YOU,-500.00\n")
    r = env.post("transactions-import-csv", data={"default_account_id": str(card)}, headers=JSON,
                 files={"files": ("a.csv", csv.encode(), "text/csv")})
    assert r.json()["results"][0]["imported"] == 4
    with env.db() as c:
        got = {r["description"]: (r["category"], r["category_source"])
               for r in c.execute("SELECT description, category, category_source FROM transactions")}
    assert got["SHOE SHOP REFUND"] == ("Refunds", "builtin_rule")
    assert got["STORE RETURN 77"] == ("Refunds", "user_rule")
    assert got["PAYMENT THANK YOU"] == ("Payments", "card_payment")
    page = env.get("dashboard?period=all").text
    income, spend = figures(page)
    assert (income, spend) == (0.0, 40.0)                               # 80 spent, 40 back; the payment is neither
    spending = table_rows(section(page, "<h2>By category</h2>", "Income by category"))
    assert ("Refunds", -40.0) in spending
    r = env.get("transactions?flow=expense&category=Refunds")
    assert "SHOE SHOP REFUND" in r.text and "-$40.00" in r.text          # the drill-down agrees


def test_sort_my_income_lists_first_and_changes_only_what_is_confirmed(env, monkeypatch):
    from app import categorize
    chk, card = env.account("Checking"), env.account("Visa", "credit_card")
    pay = env.txn(chk, "2026-09-01", 3000.0, "ACME PAYROLL", category="Other income")
    interest = env.txn(chk, "2026-09-02", 1.25, "INTEREST PAID", category="Uncategorized")
    gift = env.txn(chk, "2026-09-03", 50.0, "ZELLE FROM GRAN", category="Uncategorized")
    salary = env.txn(chk, "2026-09-04", 3000.0, "ACME PAYROLL", category="Salary")          # already sorted
    out = env.txn(chk, "2026-09-05", -20.0, "INTEREST PAID ON LOAN", category="Uncategorized")
    back = env.txn(card, "2026-09-06", -20.0, "REFUND", category="Uncategorized")             # a card: not income
    seen = []

    def llm(url, model, descriptions, categories=None, kind="spending"):
        seen.append((list(descriptions), categories, kind))
        return {d: "Gifts received" for d in descriptions}, "prompt", "{}", None
    monkeypatch.setattr(categorize, "categorize_via_llm", llm)
    with env.db() as c:
        before = [tuple(r) for r in c.execute("SELECT id, category, category_source FROM transactions ORDER BY id")]
    r = env.post("category-sort-income", headers=HTML)
    assert r.status_code == 200
    plan = section(r.text, 'id="sort-income-plan"', "</form>")
    assert "3 changes to confirm" in plan
    assert re.findall(r'name="pick" value="(\d+)" checked', plan) == [str(gift), str(interest), str(pay)]
    assert 'name="to_%d" value="Salary"' % pay in plan and 'name="to_%d" value="Interest"' % interest in plan
    assert 'name="to_%d" value="Gifts received"' % gift in plan
    assert seen == [(["ZELLE FROM GRAN"], categorize.categories_by_kind(env.db(), "tester")["income"], "income")]
    with env.db() as c:                                                  # listing changed nothing
        assert [tuple(r) for r in c.execute("SELECT id, category, category_source FROM transactions ORDER BY id")] == before
    form = dict(re.findall(r'name="((?:to|source|pattern|type)_\d+)" value="([^"]*)"', plan))
    form["pick"] = [str(pay), str(gift)]                                 # the interest one isn't ticked
    r = env.post("category-sort-income-apply", data=form)
    assert r.headers["location"] == "categories?tab=income&notice=income_sorted&sorted=2"
    with env.db() as c:
        got = {r["id"]: (r["category"], r["category_source"]) for r in c.execute("SELECT * FROM transactions")}
    assert got[pay] == ("Salary", "builtin_rule") and got[gift] == ("Gifts received", "llm")
    assert got[interest] == ("Uncategorized", None) and got[salary][0] == "Salary"
    assert got[out][0] == "Uncategorized" and got[back][0] == "Uncategorized"
    # a tampered id (another person's row, or one that isn't money in) changes nothing
    theirs = env.txn(env.account("Robin's", user="other"), "2026-09-01", 5.0, "X", user="other", category="Uncategorized")
    env.post("category-sort-income-apply", data={"pick": [str(theirs), str(out)], f"to_{theirs}": "Salary",
                                                 f"to_{out}": "Salary"})
    with env.db() as c:
        assert c.execute("SELECT category FROM transactions WHERE id = ?", (theirs,)).fetchone()[0] == "Uncategorized"
        assert c.execute("SELECT category FROM transactions WHERE id = ?", (out,)).fetchone()[0] == "Uncategorized"


# ------------------------------------------------------------------ Home and Compare

def test_homes_income_breakdown_adds_up_and_a_spending_category_on_a_deposit_is_income(env):
    chk, sav, card = env.account("Checking"), env.account("Savings", "savings"), env.account("Visa", "credit_card")
    month = date.today().strftime("%Y-%m")
    env.txn(chk, f"{month}-01", 4800.0, "ACME PAYROLL", category="Salary")
    env.txn(sav, f"{month}-02", 12.4, "INTEREST PAID", category="Interest")
    env.txn(chk, f"{month}-03", 60.0, "GROCER CASHBACK", category="Groceries")       # a spending name, money in
    env.txn(chk, f"{month}-04", 15.0, "MYSTERY", category="Uncategorized")
    env.txn(chk, f"{month}-05", -100.0, "GROCER", category="Groceries")
    env.txn(card, f"{month}-06", -300.0, "CARD PAYMENT", category="Payments")         # never income
    env.txn(chk, f"{month}-07", 500.0, "FROM SAVINGS", category="Other income", txn_type="transfer")
    env.txn(chk, f"{month}-08", 99.0, "EXCLUDED", category="Gifts received", is_excluded=1)
    page = env.get("dashboard").text
    income, spend = figures(page)
    breakdown = table_rows(section(page, 'id="income-by-category"', "</table>"))
    assert breakdown == [("Salary", 4800.0), ("Groceries", 60.0), ("Uncategorized", 15.0), ("Interest", 12.4)]
    assert round(sum(v for _c, v in breakdown), 2) == income == 4887.4
    assert spend == 100.0
    assert table_rows(section(page, "<h2>By category</h2>", 'id="income-by-category"')) == [("Groceries", 100.0)]
    assert "% of income" in page and 'class="share-bar"' in page
    assert f"category=Salary&amp;flow=income&amp;start_date={month}-01" in page      # a row opens its transactions


def test_compare_shows_income_by_category(env):
    chk = env.account("Checking")
    env.txn(chk, "2026-08-01", 4000.0, "ACME PAYROLL", category="Salary")
    env.txn(chk, "2026-09-01", 4200.0, "ACME PAYROLL", category="Salary")
    env.txn(chk, "2026-09-10", 30.0, "REFUND", category="Refunds")
    page = env.get("dashboard-compare?mode=month&a=2026-08&b=2026-09").text
    income = section(page, "<h2>Income by category</h2>", "</table>")
    assert "Salary" in income and "$4,000.00" in income and "$4,200.00" in income and "Refunds" in income
    assert "+$200.00" in income


# ------------------------------------------------------------------ the Household Assistant

def _assistant_month(env):
    month = "2026-09"
    chk, card = env.account("Checking"), env.account("Visa", "credit_card")
    env.txn(chk, f"{month}-01", 4800.0, "ACME PAYROLL", category="Salary")
    env.txn(chk, f"{month}-02", 12.4, "INTEREST PAID", category="Interest")
    env.txn(chk, f"{month}-03", 387.6, "TAX REFUND", category="Refunds")
    env.txn(chk, f"{month}-04", -900.0, "RENT", category="Housing")
    env.txn(card, f"{month}-05", 50.0, "GROCER", category="Groceries")
    env.txn(chk, "2026-03-31", 2.0, "INTEREST PAID", category="Interest")
    return month


def test_finance_summary_names_the_top_income_categories(env):
    month = _assistant_month(env)
    turn_on(env)
    res = call("finance.summary", month=month)
    assert "income 5,200.00, spending 950.00, net 4,250.00" in res["text"]
    assert res["text"].endswith("Income 5,200.00: Salary 4,800.00, Refunds 387.60, Interest 12.40.")
    assert {"category": "Salary", "income": 4800.0} in res["items"]


def test_finance_income_with_a_month_and_with_a_category(env, monkeypatch):
    from app import statement_due
    from datetime import datetime
    monkeypatch.setattr(statement_due, "now", lambda: datetime(2026, 10, 14, 9, 0))
    month = _assistant_month(env)
    turn_on(env)
    res = call("finance.income", month=month)
    assert res["text"].startswith("September 2026: income 5,200.00: Salary 4,800.00; Refunds 387.60; Interest 12.40")
    assert res["items"][0] == {"category": "Salary", "income": 4800.0, "share": 92.3}
    assert res["links"][0]["target"] == "/month/2026-09"
    res = call("finance.income", category="interest")                     # this year, month by month
    assert res["text"].startswith("Interest in 2026 so far: 14.40: March 2026 2.00; September 2026 12.40.")
    assert [i for i in res["items"] if "month" in i] == [{"month": "2026-03", "income": 2.0},
                                                         {"month": "2026-09", "income": 12.4}]
    res = call("finance.income", category="Interest", month="2026-09")
    assert res["text"].startswith("Interest in September 2026: 12.40. Largest: INTEREST PAID 12.40 (2026-09-02)")
    assert "isn't an income category" in call("finance.income", category="Groceries")["text"]
    assert "No income in October 2026" in call("finance.income")["text"]


def test_finance_income_follows_sharing_and_the_switches(env):
    _assistant_month(env)
    env.insert("known_users", id="other", name="Robin")
    env.txn(env.account("Robin's", user="other"), "2026-09-01", 777.0, "ROBIN PAY", user="other", category="Salary")
    turn_on(env, False)
    nack(call("finance.income", month="2026-09"), "not_allowed", "off")
    turn_on(env)
    assert "777" not in call("finance.income", month="2026-09")["text"]
    nack(call("finance.income", month="2026-09", person="Robin"), "not_found", "person")
    with env.db() as c:
        c.execute("INSERT INTO user_access (viewer_id, owner_id, granted_by) VALUES ('tester', 'other', 'tester')")
    res = call("finance.income", month="2026-09", person="robin")
    assert "(Robin's)" in res["text"] and "777.00" in res["text"]
    with env.db() as c:
        c.execute("UPDATE known_users SET assistant_ok = 0 WHERE id = 'other'")
    nack(call("finance.income", month="2026-09", person="Robin"), "not_found", "person")
    with env.db() as c:
        c.execute("UPDATE known_users SET assistant_ok = 0 WHERE id = 'tester'")
    nack(call("finance.income", month="2026-09"), "not_allowed", "person_off")


def test_finance_incomes_quick_words_are_plain_and_change_no_other_question(env):
    """The phrase corpus itself is read in household_assistant/tests/test_finance_quick.py with this catalogue."""
    from app import tools
    spec = {t["name"]: t for t in tools.tools.spec()}
    quick = spec["finance.income"]["quick"]
    assert "concepts" not in quick and quick["words"]
    assert set(spec["finance.income"]["args"]) == {"month", "category", "person"}
    assert spec["finance.income"]["acts"] is False
    for w in quick["words"]:
        assert w not in ("income", "earn", "earnings", "salary", "summary", "net")      # the Assistant's own INCOME words


# ------------------------------------------------------------------ one person's data

def test_export_and_import_carry_kind_and_an_older_file_imports(env):
    from test_people_data import _craft, _export, _import
    for uid, name in (("tester", "Tester"), ("wife", "Wife")):
        env.insert("known_users", id=uid, name=name)
    env.insert("custom_categories", user_id="tester", name="Rental income", kind="income", colour="teal")
    env.insert("custom_categories", user_id="tester", name="Pet Care")
    data = _export(env, pdfs=False)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert json.loads(z.read("manifest.json"))["schema"] == 32
        rows = json.loads(z.read("data/custom_categories.json"))
    assert {(r["name"], r["kind"]) for r in rows} == {("Rental income", "income"), ("Pet Care", "spending")}
    assert _import(env, data, uid="wife").status_code == 200
    with env.db() as c:
        got = {tuple(r) for r in c.execute("SELECT name, kind, colour FROM custom_categories WHERE user_id = 'wife'")}
    assert got == {("Rental income", "income", "teal"), ("Pet Care", "spending", None)}

    old = _craft({"accounts": [{"id": 1, "name": "Old checking", "type": "checking"}],
                  "transactions": [{"id": 1, "account_id": 1, "date": "2026-01-02", "amount": 100.0,
                                    "description": "PAY", "category": "Income"}],
                  "category_rules": [{"id": 1, "pattern": "PAY", "match_type": "substring", "category": "Income"}],
                  "custom_categories": [{"id": 1, "name": "Side gig"}, {"id": 2, "name": "Refunds"}]}, schema=31)
    assert _import(env, old, uid="wife").status_code == 200
    with env.db() as c:
        assert c.execute("SELECT category FROM transactions WHERE user_id = 'wife' AND description = 'PAY'").fetchone()[0] \
            == "Other income"
        assert c.execute("SELECT category FROM category_rules WHERE user_id = 'wife' AND pattern = 'PAY'").fetchone()[0] \
            == "Other income"
        assert c.execute("SELECT kind FROM custom_categories WHERE user_id = 'wife' AND name = 'Side gig'").fetchone()[0] \
            == "spending"
        assert c.execute("SELECT COUNT(*) FROM custom_categories WHERE user_id = 'wife' AND name = 'Refunds'"
                         ).fetchone()[0] == 0                            # a built-in now
    from test_people_data import _upload
    bad = _craft({"custom_categories": [{"id": 1, "name": "X", "kind": "loan"}]})
    r = _upload(env, bad)
    assert r.status_code == 400 and "kind" in r.json()["detail"]


def test_rules_giving_an_income_category_apply_only_to_money_in_on_checking_and_savings(env):
    from app import categorize, category_admin
    chk, sav, card = env.account("Checking"), env.account("Savings", "savings"), env.account("Visa", "credit_card")
    env.insert("custom_categories", user_id="tester", name="Rental income", kind="income")
    env.insert("category_rules", user_id="tester", pattern="ACME", match_type="prefix", category="Salary")
    env.insert("category_rules", user_id="tester", pattern="TENANT", match_type="prefix", category="Rental income")
    env.insert("category_rules", user_id="tester", pattern="GROCER", match_type="prefix", category="Groceries")
    # on import: only money in on checking/savings takes an income rule; a spending rule still applies everywhere
    rows = [(1, "ACME PAYROLL", "in"), (2, "ACME STORE", "out"), (3, "ACME STORE", "card"), (4, "TENANT RENT", "in"),
            (5, "TENANT RENT REVERSAL", "out"), (6, "GROCER", "out"), (7, "GROCER", "card"), (8, "ACME BACK", "card_in")]
    with env.db() as c:
        plan = categorize.plan_categories(c, "tester", "", "", rows)
    got = {k: v[:2] for k, v in plan.assignments.items()}
    assert got[1] == ("Salary", "user_rule") and got[4] == ("Rental income", "user_rule")
    assert got[2][0] != "Salary" and got[3][0] != "Salary" and got[5][0] != "Rental income"
    assert got[6] == ("Groceries", "user_rule") and got[7] == ("Groceries", "user_rule")
    assert got[8] == ("Payments", "card_payment")
    # re-categorizing existing transactions follows the same rule
    ids = {}
    for key, acct, amount, desc in [("chk_in", chk, 2000.0, "ACME PAYROLL"), ("sav_in", sav, 50.0, "ACME BONUS"),
                                    ("chk_out", chk, -20.0, "ACME STORE"), ("card", card, 30.0, "ACME STORE"),
                                    ("card_back", card, -30.0, "ACME STORE")]:
        ids[key] = env.insert("transactions", user_id="tester", account_id=acct, date="2026-10-01", amount=amount,
                              description=desc, category="Uncategorized")
    with env.db() as c:
        changes = {ch["id"]: ch["to"] for ch in categorize.rule_changes(c, "tester", "ACME", "prefix")}
        tried = category_admin.try_rule(c, "tester", "ACME", "prefix", "Salary")
        categorize.recategorize_for_rule(c, "tester", "ACME", "prefix")
        cats = {r["id"]: r["category"] for r in c.execute("SELECT id, category FROM transactions")}
    assert set(changes) == {ids["chk_in"], ids["sav_in"]}
    assert tried["count"] == 2 and tried["will_change"] == 2
    assert cats[ids["chk_in"]] == cats[ids["sav_in"]] == "Salary"
    assert {cats[ids[k]] for k in ("chk_out", "card", "card_back")} == {"Uncategorized"}
    with env.db() as c:
        assert categorize.recategorize_transactions(c, "tester") == 0       # nothing else for an income rule to take
