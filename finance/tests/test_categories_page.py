"""The redesigned Categories page (SPEC.md section 9; FINANCE_INCOME_CATEGORIES_SPEC.md §10 A–G, tests §11): the
Spending · Income · Rules tabs, the category lists and their figures, the ⋯ menu (rename, merge, move, hide, colour,
delete), the rules table (search, chips, edit in place, Test, Use my own instead, last matched), suggestions from
the person's own corrections, colours on Home and Compare and their contrast in every theme, the phone layout's
CSS, who may change what, and the data (migration 0032, one person's data).

All people, merchants and amounts here are invented."""
import io
import json
import os
import re
import zipfile
from datetime import date, timedelta

import pytest

HTML = {"accept": "text/html"}
JSON = {"accept": "application/json"}
WIFE = {"x-remote-user-id": "wife", "x-remote-user-name": "Wife"}
INTRUDER = {"x-remote-user-id": "intruder", "x-remote-user-name": "Intruder"}
APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MONTH = date.today().strftime("%Y-%m")


def panel(page: str, tab: str) -> str:
    start = page.index(f'id="panel-{tab}"')
    nxt = [page.find(f'id="panel-{t}"', start + 1) for t in ("spending", "income", "rules")]
    nxt = [n for n in nxt if n > start]
    return page[start:min(nxt)] if nxt else page[start:]


def row(page_or_panel: str, name: str) -> str:
    i = page_or_panel.index(f'<tr class="cat-row" data-category="{name}"')
    return page_or_panel[i:page_or_panel.index("</tr>", i)]


def cell(tr: str, label: str) -> str:
    return re.sub(r"<[^>]+>", "", re.search(rf'<td data-label="{label}"[^>]*>(.*?)</td>', tr, re.S).group(1)).strip()


def order(panel_html: str) -> list[str]:
    return re.findall(r'<tr class="cat-row" data-category="([^"]+)"', panel_html)


def rules_shown(page: str) -> list[str]:
    return re.findall(r'<tr class="rule-row[^"]*"[^>]*data-pattern="([^"]+)"', panel(page, "rules"))


def dump(env, *tables):
    with env.db() as c:
        return {t: sorted(tuple(r) for r in c.execute(f"SELECT * FROM {t}")) for t in tables}


def cats(env, tab="spending", **params):
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    r = env.get(f"categories?tab={tab}" + ("&" + qs if qs else ""))
    assert r.status_code == 200, r.text[:300]
    return r.text


# ------------------------------------------------------------------ A. Tabs

def test_each_tab_lists_the_right_categories_and_rules(env):
    env.insert("custom_categories", user_id="tester", name="Rental income", kind="income")
    env.insert("custom_categories", user_id="tester", name="Pet Care")
    env.insert("category_rules", user_id="tester", pattern="ACME PAYROLL", match_type="prefix", category="Salary")
    page = cats(env, "income")
    spending, income, rules = panel(page, "spending"), panel(page, "income"), panel(page, "rules")
    assert order(spending) == ["Dining", "Entertainment", "Gas", "Groceries", "Health", "Housing", "Other", "Pet Care",
                               "Shopping", "Subscriptions", "Transportation", "Travel", "Utilities"]
    assert order(income) == ["Dividends", "Gifts received", "Interest", "Other income", "Refunds", "Reimbursements",
                             "Rental income", "Salary"]
    assert "Payments" not in order(spending) + order(income)
    assert 'id="panel-income" data-panel="income"' in page and 'id="panel-spending"' in page
    assert re.search(r'id="panel-spending"[^>]*hidden', page) and not re.search(r'id="panel-income"[^>]*hidden', page)
    shown = rules_shown(page)
    assert shown[0] == "ACME PAYROLL" and {"PAYROLL", "REFUND", "NETFLIX", "UBER EATS"} <= set(shown)
    assert len(shown) == 1 + 38
    assert 'class="subtab cat-tab active" data-tab="income"' in page
    assert ">Built in<" in rules and ">Built in<" in spending


def test_the_tabs_and_every_form_work_without_the_script(env):
    page = cats(env)
    for tab in ("spending", "income", "rules"):
        assert f'href="categories?tab={tab}"' in page                      # the tabs are plain links
        assert f'id="panel-{tab}"' in cats(env, tab)
    assert env.get("categories?tab=nonsense").status_code == 200
    for form in re.findall(r"<form[^>]*>", page):
        assert re.search(r'method="(post|get)"', form) and 'action="' in form, form
    assert "onclick" not in page and "<script>" not in page                  # CSP: no inline script
    assert 'src="static/pages/categories.js' in page
    # every action comes back to the tab it was sent from
    r = env.post("custom-categories", data={"name": "Freelance", "kind": "income", "tab": "income"})
    assert r.headers["location"] == "categories?tab=income&notice=cat_added"
    r = env.post("category-rules", data={"pattern": "ACME", "category": "Salary", "tab": "rules"})
    assert r.headers["location"] == "categories?tab=rules&notice=rule_saved"
    r = env.post("category-hide", data={"name": "Gas", "tab": "spending"})
    assert r.headers["location"] == "categories?tab=spending&notice=cat_hidden"
    assert "Hidden from the menus" in env.get(r.headers["location"]).text


def test_how_this_works_opens(env):
    page = cats(env)
    how = page[page.index('<details class="how-it-works" id="how-categories">'):]
    how = how[:how.index("</details>")]
    assert "<summary>How this works</summary>" in how and "most specific" in how and "Spending" in how
    assert page.count('class="hint one-line"') == 1


# ------------------------------------------------------------------ B. The category list

def test_this_months_total_the_12_month_count_and_the_rules_count(env):
    chk, card = env.account("Checking"), env.account("Visa", "credit_card")
    today = date.today()
    env.txn(chk, f"{MONTH}-01", -12.5, "CAFE", category="Dining")
    env.txn(card, f"{MONTH}-01", 7.5, "DINER", category="Dining")
    env.txn(chk, f"{MONTH}-01", -99.0, "EXCLUDED CAFE", category="Dining", is_excluded=1)
    env.txn(chk, (today.replace(day=1) - timedelta(days=3)).isoformat(), -40.0, "LAST MONTH", category="Dining")
    env.txn(chk, (today - timedelta(days=400)).isoformat(), -5.0, "LONG AGO", category="Dining")
    env.txn(chk, f"{MONTH}-01", 2500.0, "PAY", category="Salary")
    env.txn(chk, f"{MONTH}-01", 20.0, "DINER CASHBACK", category="Dining")     # money in: not Dining's spend
    env.insert("category_rules", user_id="tester", pattern="CAFE", match_type="substring", category="Dining")
    page = cats(env)
    dining = row(panel(page, "spending"), "Dining")
    assert cell(dining, "This month") == "$20.00"
    assert cell(dining, "Last 12 months") == "5"
    assert cell(dining, "Rules") == "7"                    # 6 built-in (UBER EATS, STARBUCKS, …) and theirs
    salary = row(panel(page, "income"), "Salary")
    assert cell(salary, "This month") == "$2,500.00" and cell(salary, "Last 12 months") == "1"
    assert cell(salary, "Rules") == "4"


def test_the_three_sorts(env):
    chk = env.account("Checking")
    env.txn(chk, f"{MONTH}-01", -300.0, "RENT", category="Housing")
    env.txn(chk, f"{MONTH}-02", -10.0, "A", category="Health")
    for i in range(4):
        env.txn(chk, f"{MONTH}-0{i + 3}", -1.0, f"COFFEE {i}", category="Dining")
    by_name = order(panel(cats(env, sort="name"), "spending"))
    assert by_name == sorted(by_name, key=str.lower)
    assert order(panel(cats(env, sort="total"), "spending"))[:3] == ["Housing", "Health", "Dining"]
    assert order(panel(cats(env, sort="use"), "spending"))[:3] == ["Dining", "Health", "Housing"]
    assert 'href="categories?tab=spending&sort=use"' in cats(env)


def test_a_row_opens_transactions_filtered_to_that_category(env):
    chk = env.account("Checking")
    env.txn(chk, f"{MONTH}-01", -12.5, "CAFE", category="Food & Drink")
    env.txn(chk, f"{MONTH}-02", -40.0, "SHOE SHOP", category="Shopping")
    env.insert("custom_categories", user_id="tester", name="Food & Drink")
    tr = row(panel(cats(env), "spending"), "Food &amp; Drink")
    href = re.search(r'class="cat-link" href="([^"]+)"', tr).group(1)
    assert href == "transactions?category=Food%20%26%20Drink"
    assert 'data-href="transactions?category=Food%20%26%20Drink"' in tr
    page = env.get(href.replace("&amp;", "&")).text
    assert "CAFE" in page and "SHOE SHOP" not in page


def test_add_on_each_tab_gets_the_kind_right(env):
    page = cats(env)
    spend_form = panel(page, "spending")[panel(page, "spending").index('id="add-spending"'):]
    income_form = panel(page, "income")[panel(page, "income").index('id="add-income"'):]
    assert 'name="kind" value="spending" checked' in spend_form and 'name="kind" value="income" >' in spend_form
    assert 'name="kind" value="income" checked' in income_form
    assert 'name="tab" value="income"' in income_form
    env.post("custom-categories", data={"name": "Child benefit", "kind": "income", "tab": "income"})
    env.post("custom-categories", data={"name": "Kids", "kind": "spending", "tab": "spending"})
    env.post("custom-categories", data={"name": "Side gig", "kind": "income", "tab": "spending"})     # switched
    page = cats(env)
    assert {"Child benefit", "Side gig"} <= set(order(panel(page, "income")))
    assert "Kids" in order(panel(page, "spending"))


# ------------------------------------------------------------------ C. The ⋯ menu

def _pets(env):
    chk = env.account("Checking")
    cid = env.insert("custom_categories", user_id="tester", name="Pets")
    ts = [env.txn(chk, d, -20.0, "VET CLINIC", category="Pets") for d in ("2026-06-03", "2026-07-03", "2026-08-03")]
    rule = env.insert("category_rules", user_id="tester", pattern="VET", match_type="substring", category="Pets")
    env.insert("skipped_duplicates", user_id="tester", account_id=chk, date="2026-08-03", amount=-20.0,
               description="VET CLINIC", category="Pets", reason="repeated")
    env.insert("category_suggestion_dismissals", user_id="tester", merchant="VET CLINIC", category="Pets")
    env.insert("recurring_overrides", user_id="tester", merchant_pattern="VET CLINIC", is_recurring=1)
    other = env.txn(env.account("Robin's", user="other"), "2026-08-01", -1.0, "VET", user="other", category="Pets")
    return chk, cid, ts, rule, other


def test_rename_moves_rows_rules_skipped_rows_and_recurring_charges(env):
    chk, cid, ts, rule, other = _pets(env)
    r = env.post("category-rename", data={"id": cid, "name": "Pet care", "tab": "spending"})
    assert r.headers["location"] == "categories?tab=spending&notice=cat_renamed"
    with env.db() as c:
        assert {x[0] for x in c.execute("SELECT category FROM transactions WHERE user_id = 'tester'")} == {"Pet care"}
        assert c.execute("SELECT category FROM category_rules WHERE id = ?", (rule,)).fetchone()[0] == "Pet care"
        assert c.execute("SELECT category FROM skipped_duplicates").fetchone()[0] == "Pet care"
        assert c.execute("SELECT category FROM category_suggestion_dismissals").fetchone()[0] == "Pet care"
        assert c.execute("SELECT name FROM custom_categories WHERE id = ?", (cid,)).fetchone()[0] == "Pet care"
        assert c.execute("SELECT category FROM transactions WHERE id = ?", (other,)).fetchone()[0] == "Pets"  # not theirs
        from app import recurring
        series = recurring.scan_recurring(c, "tester")["recurring"]
    assert [s["category"] for s in series] == ["Pet care"]         # recurring charges follow (no name stored there)
    assert env.post("category-rename", data={"id": cid, "name": "groceries"}).status_code == 400   # a duplicate
    assert env.post("category-rename", data={"id": cid, "name": "PET CARE"}).status_code == 303    # its own case


def test_merge_only_into_the_same_kind_and_a_built_in_can_be_merged_away(env):
    chk, cid, ts, rule, other = _pets(env)
    r = env.post("category-merge", data={"name": "Pets", "into": "Salary"}, headers=HTML)
    assert r.status_code == 400 and "another spending category" in r.text
    gas = env.txn(chk, "2026-08-04", -30.0, "SHELL", category="Gas")
    env.insert("category_rules", user_id="tester", pattern="SHELL", match_type="substring", category="Gas")
    r = env.post("category-merge", data={"name": "Gas", "into": "Transportation", "tab": "spending"})
    assert r.headers["location"] == "categories?tab=spending&notice=cat_merged"
    with env.db() as c:
        assert c.execute("SELECT category FROM transactions WHERE id = ?", (gas,)).fetchone()[0] == "Transportation"
        assert c.execute("SELECT category FROM category_rules WHERE pattern = 'SHELL'").fetchone()[0] == "Transportation"
    page = cats(env)
    assert "Gas" not in order(panel(page, "spending"))
    assert "Gas" in panel(page, "spending")[panel(page, "spending").index('id="hidden-spending"'):]
    env.post("category-merge", data={"name": "Pets", "into": "Shopping"})
    with env.db() as c:
        assert c.execute("SELECT COUNT(*) FROM custom_categories WHERE id = ?", (cid,)).fetchone()[0] == 0
        assert {x[0] for x in c.execute("SELECT category FROM transactions WHERE user_id = 'tester' "
                                        "AND description = 'VET CLINIC'")} == {"Shopping"}


def test_move_changes_the_list_without_touching_rows(env):
    chk, cid, ts, rule, other = _pets(env)
    before = dump(env, "transactions", "category_rules", "skipped_duplicates")
    env.post("category-kind", data={"id": cid, "kind": "income", "tab": "spending"})
    page = cats(env)
    assert "Pets" in order(panel(page, "income")) and "Pets" not in order(panel(page, "spending"))
    assert dump(env, "transactions", "category_rules", "skipped_duplicates") == before
    assert env.post("category-kind", data={"id": cid, "kind": "savings"}).status_code == 400


def _menus(env) -> list[str]:
    """Every category menu in the app (a pending row makes Review show its own)."""
    pages = [env.get("transactions?searched=1").text, env.get("review").text, env.get("uploads").text,
             env.get("categories?tab=rules").text]
    out = []
    for p in pages:
        out += re.findall(r'<select[^>]*(?:name="category"|bulk-category-select)[^>]*>.*?</select>', p, re.S)
    return out


def test_hide_show_and_a_rule_to_a_hidden_category_still_works(env):
    from app import categorize
    chk = env.account("Checking")
    old = env.txn(chk, "2026-08-01", -200.0, "AIRLINE", category="Travel")
    env.txn(chk, "2026-08-02", -1.0, "FLAGGED", review_status="pending_review", category="Other")
    env.insert("category_rules", user_id="tester", pattern="AIRLINE", match_type="substring", category="Travel")
    assert all('value="Travel"' in m for m in _menus(env))
    env.post("category-hide", data={"name": "Travel", "tab": "spending"})
    menus = _menus(env)
    assert len(menus) >= 6
    for m in menus:
        if 'value="Travel" selected' in m:
            continue                                                       # the old row still shows its own
        assert 'value="Travel"' not in m
    forms = env.get("transactions?searched=1").text
    own = re.search(rf'name="id" value="{old}".*?</select>', forms, re.S).group(0)
    assert 'value="Travel" selected' in own                                # stays on old rows
    with env.db() as c:
        plan = categorize.plan_categories(c, "tester", "", "", [(1, "AIRLINE TICKET", "out")])
        assert plan.assignments[1][0] == "Travel"                          # a rule to it still works
        assert "Travel" not in categorize.list_all_categories(c, "tester")
    hidden = panel(cats(env), "spending")
    assert "Show hidden (1)" in hidden and 'action="category-show"' in hidden
    assert "Travel" not in order(hidden)
    env.post("category-show", data={"name": "Travel", "tab": "spending"})
    assert all('value="Travel"' in m for m in _menus(env))
    assert "Travel" in order(panel(cats(env), "spending"))
    assert env.post("category-hide", data={"name": "Pets"}).status_code == 400          # only built-ins
    assert env.post("category-hide", data={"name": "Payments"}).status_code == 400


def test_delete_asks_where_the_rows_go_uncategorized_by_default(env):
    chk, cid, ts, rule, other = _pets(env)
    menu = row(panel(cats(env), "spending"), "Pets")
    form = menu[menu.index('action="custom-category-delete"'):]
    assert '<select name="move_to">' in form and '<option value="Uncategorized" selected>' in form
    assert 'value="Pets"' not in form[:form.index("</select>")].replace('name="name" value="Pets"', "")
    built_in = row(panel(cats(env), "spending"), "Groceries")
    assert "custom-category-delete" not in built_in and "it can't be deleted" in built_in
    env.post("custom-category-delete", data={"id": cid})
    with env.db() as c:
        assert {x[0] for x in c.execute("SELECT category FROM transactions WHERE user_id = 'tester'")} == {"Uncategorized"}
        assert c.execute("SELECT COUNT(*) FROM category_rules WHERE id = ?", (rule,)).fetchone()[0] == 0
        assert c.execute("SELECT category FROM skipped_duplicates").fetchone()[0] == "Uncategorized"
    cid2 = env.insert("custom_categories", user_id="tester", name="Vet")
    env.txn(chk, "2026-09-01", -5.0, "VET 2", category="Vet")
    r2 = env.insert("category_rules", user_id="tester", pattern="VET 2", match_type="exact", category="Vet")
    env.post("custom-category-delete", data={"id": cid2, "move_to": "Health", "tab": "spending"})
    with env.db() as c:
        assert c.execute("SELECT category FROM transactions WHERE description = 'VET 2'").fetchone()[0] == "Health"
        assert c.execute("SELECT category FROM category_rules WHERE id = ?", (r2,)).fetchone()[0] == "Health"


# ------------------------------------------------------------------ D. The rules table

def test_search_and_the_filter_chips(env):
    env.insert("category_rules", user_id="tester", pattern="ACME PAYROLL", match_type="prefix", category="Salary")
    env.insert("category_rules", user_id="tester", pattern="CAFE", match_type="substring", category="Dining")
    page = cats(env, "rules")
    assert rules_shown(cats(env, "rules", q="payroll")) == ["ACME PAYROLL", "PAYROLL"]
    dining = rules_shown(cats(env, "rules", q="dining"))
    assert dining[0] == "CAFE" and set(dining) == {"CAFE", "CHIPOTLE", "DOORDASH", "GRUBHUB", "MCDONALD", "STARBUCKS",
                                                   "UBER EATS"}           # theirs first, then in the cascade's order
    assert rules_shown(cats(env, "rules", who="mine")) == ["CAFE", "ACME PAYROLL"]
    builtin = rules_shown(cats(env, "rules", who="builtin"))
    assert "CAFE" not in builtin and len(builtin) == 38
    income = rules_shown(cats(env, "rules", kind="income"))
    assert income[0] == "ACME PAYROLL" and "NETFLIX" not in income and "REFUND" in income
    assert rules_shown(cats(env, "rules", rule_category="Salary")) == ["ACME PAYROLL", "DIRECT DEP", "PAYROLL",
                                                                      "DIR DEP", "SALARY"]
    assert 'href="categories?tab=rules&amp;who=mine"' in page and 'data-chip="income"' in page
    assert rules_shown(cats(env, "rules", who="mine", kind="income", q="acme")) == ["ACME PAYROLL"]


def test_editing_a_rule_in_place_changes_only_that_rule(env):
    a = env.insert("category_rules", user_id="tester", pattern="CAFE", match_type="substring", category="Dining")
    b = env.insert("category_rules", user_id="tester", pattern="ACME", match_type="prefix", category="Salary")
    before = dump(env, "category_rules")["category_rules"]
    r = env.post("category-rule-edit", data={"id": a, "pattern": "CAFE NERO", "match_type": "prefix",
                                             "category": "Entertainment", "tab": "rules"})
    assert r.headers["location"] == "categories?tab=rules&notice=rule_saved"
    with env.db() as c:
        rows = {r["id"]: (r["pattern"], r["match_type"], r["category"]) for r in c.execute("SELECT * FROM category_rules")}
    assert rows == {a: ("CAFE NERO", "prefix", "Entertainment"), b: ("ACME", "prefix", "Salary")}
    assert [x for x in dump(env, "category_rules")["category_rules"] if x[0] == b] == [x for x in before if x[0] == b]
    r = env.post("category-rule-edit", data={"id": a, "pattern": "ACME", "match_type": "prefix", "category": "Dining"},
                 headers=HTML)
    assert r.status_code == 400 and "already have a rule" in r.text
    page = cats(env, "rules", edit=str(a))
    assert re.search(rf'<details class="sheet rule-edit" data-sheet open>\s*<summary[^>]*>Edit</summary>\s*'
                     rf'<div class="sheet-body"[^>]*>\s*<p class="sheet-title">Edit rule</p>\s*<form method="post" '
                     rf'action="category-rule-edit" class="sheet-form rule-form" data-rule-form data-rule-id="{a}"', page)


def _shop(env, n=12):
    chk = env.account("Checking")
    ids = [env.txn(chk, f"2026-0{1 + i % 9}-{10 + i:02d}", -float(i + 1), f"CORNER SHOP {i}",
                   category="Groceries" if i % 3 else "Shopping") for i in range(n)]
    env.txn(chk, "2026-09-01", -3.0, "OTHER PLACE", category="Dining")
    return ids


def test_test_returns_the_right_10_and_count_and_saves_nothing(env):
    _shop(env)
    before = dump(env, "transactions", "category_rules")
    r = env.get("category-rule-test?pattern=corner%20shop&match_type=prefix&category=Shopping", headers=JSON)
    d = r.json()
    assert d["count"] == 12 and len(d["matches"]) == 10
    with env.db() as c:
        newest = [x[0] for x in c.execute("SELECT id FROM transactions WHERE description LIKE 'CORNER SHOP%' "
                                          "ORDER BY date DESC, id DESC LIMIT 10")]
    assert [m["id"] for m in d["matches"]] == newest
    assert d["will_change"] == 8                                     # the 8 Groceries ones; 4 are Shopping already
    assert all(m["to"] == "Shopping" for m in d["matches"])
    assert dump(env, "transactions", "category_rules") == before
    page = env.get("category-rule-test?pattern=CORNER+SHOP&match_type=prefix&category=Shopping").text   # no script
    assert "Matches <strong>12</strong> transactions" in page and "(will change 8)" in page
    assert 'id="add-rule" data-sheet open' in page and 'value="CORNER SHOP"' in page
    assert dump(env, "transactions", "category_rules") == before
    assert env.get("category-rule-test?pattern=&category=Shopping", headers=JSON).status_code == 400


def test_will_change_n_is_what_saving_does(env):
    _shop(env)
    env.insert("category_rules", user_id="tester", pattern="CORNER SHOP 1", match_type="prefix", category="Health")
    d = env.get("category-rule-test?pattern=CORNER&match_type=prefix&category=Shopping", headers=JSON).json()
    # the more specific older rule still wins for CORNER SHOP 1 and 10, 11 (the cascade's order)
    with env.db() as c:
        before = {r["id"]: r["category"] for r in c.execute("SELECT id, category FROM transactions")}
    r = env.post("category-rules", data={"pattern": "CORNER", "match_type": "prefix", "category": "Shopping",
                                         "recategorize": "1", "tab": "rules"})
    assert r.headers["location"] == f"categories?tab=rules&recategorized={d['will_change']}&notice=rule_saved"
    with env.db() as c:
        after = {r["id"]: r["category"] for r in c.execute("SELECT id, category FROM transactions")}
    assert sum(1 for k in after if after[k] != before[k]) == d["will_change"] == 8    # 3 Health, 5 Shopping
    # and for an edit
    rid = env.db().execute("SELECT id FROM category_rules WHERE pattern = 'CORNER'").fetchone()[0]
    d = env.get(f"category-rule-test?pattern=CORNER&match_type=prefix&category=Dining&rule_id={rid}", headers=JSON).json()
    r = env.post("category-rule-edit", data={"id": rid, "pattern": "CORNER", "match_type": "prefix",
                                             "category": "Dining", "recategorize": "1"})
    assert f"recategorized={d['will_change']}" in r.headers["location"] and d["will_change"] == 9


def test_use_my_own_instead_copies_a_built_in_rule_and_the_copy_wins(env):
    from app import categorize
    r = env.post("category-rule-copy", data={"pattern": "AMAZON", "match_type": "substring", "tab": "rules"})
    with env.db() as c:
        rid, cat = c.execute("SELECT id, category FROM category_rules WHERE pattern = 'AMAZON'").fetchone()
    assert cat == "Shopping"
    assert r.headers["location"] == f"categories?tab=rules&notice=rule_copied&edit={rid}"
    page = env.get(r.headers["location"]).text
    assert "yours is used" in page and "Copied into your rules" in page
    env.post("category-rule-edit", data={"id": rid, "pattern": "AMAZON", "match_type": "substring", "category": "Gifts"})
    with env.db() as c:
        plan = categorize.plan_categories(c, "tester", "", "", [(1, "AMAZON MKTPLACE", "out")])
    assert plan.assignments[1][:2] == ("Gifts", "user_rule")
    assert env.post("category-rule-copy", data={"pattern": "NOPE", "match_type": "exact"}).status_code == 400


def test_last_matched_at_updates_when_a_rule_categorizes_a_row(env):
    from app import categorize
    chk = env.account("Checking")
    rid = env.insert("category_rules", user_id="tester", pattern="CORNER", match_type="prefix", category="Groceries")
    other = env.insert("category_rules", user_id="tester", pattern="NEVER", match_type="exact", category="Dining")
    t = env.txn(chk, "2026-09-01", -5.0, "CORNER STORE")
    with env.db() as c:
        categorize.categorize_transactions(c, "tester", "", "", [(t, "CORNER STORE", "out")])
        c.commit()
        got = {r["id"]: r["last_matched_at"] for r in c.execute("SELECT id, last_matched_at FROM category_rules")}
    assert got[rid] is not None and got[other] is None
    rules = panel(cats(env, "rules"), "rules")
    tr = rules[rules.index('id="rule-%d"' % rid):]
    assert re.search(r'data-label="Last matched">\d{4}-\d{2}-\d{2}<', tr[:tr.index("</tr>")])
    assert re.search(r'data-label="Matches" class="num">1<', tr[:tr.index("</tr>")])
    with env.db() as c:
        c.execute("UPDATE category_rules SET last_matched_at = NULL")
    env.txn(chk, "2026-09-02", -6.0, "CORNER STORE 2", category="Other")
    env.post("category-rules", data={"pattern": "CORNER", "match_type": "prefix", "category": "Groceries",
                                     "recategorize": "1"})
    with env.db() as c:
        assert c.execute("SELECT last_matched_at FROM category_rules WHERE id = ?", (rid,)).fetchone()[0] is not None


# ------------------------------------------------------------------ E. Suggestions

def test_a_suggestion_after_3_manual_changes_not_2_make_rule_and_dismiss(env):
    chk = env.account("Checking")
    ts = [env.txn(chk, f"2026-09-0{i + 1}", -9.99, f"AMAZON MKTPL*{code} SEATTLE WA", category="Uncategorized")
          for i, code in enumerate(("2K3J4", "9X8Y7", "ZZ11Q", "AB12C"))]
    for t in ts[:2]:
        env.post("transaction-category", data={"id": t, "category": "Shopping"})
    assert "make it a rule?" not in cats(env)
    env.post("transaction-category", data={"id": ts[2], "category": "Shopping"})
    page = cats(env)
    sugg = panel(page, "spending")
    assert "You set <strong>AMAZON MKTPL</strong> to <strong>Shopping</strong> 3 times &mdash; make it a rule?" in sugg
    assert "starts with &ldquo;AMAZON MKTPL&rdquo;" in sugg
    assert "make it a rule?" not in panel(page, "income")
    r = env.post("category-suggestion-make", data={"merchant": "AMAZON MKTPL", "category": "Shopping",
                                                   "tab": "spending"})
    assert r.headers["location"] == "categories?tab=spending&notice=suggestion_made"
    with env.db() as c:
        assert [tuple(x) for x in c.execute("SELECT pattern, match_type, category FROM category_rules")] == [
            ("AMAZON MKTPL", "prefix", "Shopping")]
    assert "make it a rule?" not in cats(env)                       # their rule does it now
    # another merchant, dismissed for good
    gs = [env.txn(chk, f"2026-08-0{i + 1}", -3.0, f"QUICKFUEL STATION #{i}", category="Uncategorized") for i in range(4)]
    for t in gs[:3]:
        env.post("transaction-category", data={"id": t, "category": "Transportation"})
    assert "You set <strong>QUICKFUEL STATION</strong>" in cats(env)
    env.post("category-suggestion-dismiss", data={"merchant": "QUICKFUEL STATION", "category": "Transportation"})
    env.post("transaction-category", data={"id": gs[3], "category": "Transportation"})
    assert "QUICKFUEL STATION" not in cats(env)
    for t in gs[:3]:                                                # the same merchant to another category is new
        env.post("transaction-category", data={"id": t, "category": "Gas"})
    assert "You set <strong>QUICKFUEL STATION</strong> to <strong>Gas</strong> 3 times" in cats(env)
    r = env.post("category-suggestion-make", data={"merchant": "NOPE", "category": "Gas"}, headers=HTML)
    assert r.status_code == 400


def test_the_uncategorized_count_and_its_link_to_sort_my_income(env):
    chk, card = env.account("Checking"), env.account("Visa", "credit_card")
    env.txn(chk, "2026-09-01", -5.0, "A", category="Uncategorized")
    env.txn(card, "2026-09-02", 7.0, "B", category="Uncategorized")
    env.txn(chk, "2026-09-03", 70.0, "C", category="Uncategorized")
    env.txn(chk, "2026-09-04", -1.0, "D", category="Uncategorized", is_excluded=1)
    page = cats(env)
    spending, income = panel(page, "spending"), panel(page, "income")
    assert '<a href="transactions?category=Uncategorized&flow=expense" class="uncat-link">2 uncategorized &rarr; sort them</a>' \
        in spending
    sort = income[income.index('id="uncategorized-income"'):]
    assert re.match(r'id="uncategorized-income">\s*<form method="post" action="category-sort-income" class="inline-form">\s*'
                    r'<button type="submit" class="link-button uncat-link">1 uncategorized &rarr; Sort my income</button>',
                    sort)
    listed = env.get("transactions?category=Uncategorized&flow=expense").text
    assert "2 transactions" in listed


# ------------------------------------------------------------------ F. Colours

def _theme_colours() -> dict[str, dict[str, str]]:
    css = open(os.path.join(APP, "app", "static", "common", "themes.css"), encoding="utf-8").read()
    style = open(os.path.join(APP, "app", "static", "style.css"), encoding="utf-8").read()
    out = {}
    for block in re.finditer(r'\[data-theme="(\w+)"\]\s*\{(.*?)\}', css, re.S):
        out[block.group(1)] = dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})", block.group(2)))
    assert set(out) == {"midnight", "slate", "daylight"}
    assert not re.search(r"--(card|panel):", style)        # the app's own style.css doesn't change them
    return out


def _luminance(hexcode: str) -> float:
    def channel(v):
        v = int(v, 16) / 255
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    h = hexcode.lstrip("#")
    r, g, b = (channel(h[i:i + 2]) for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def test_every_palette_colour_reads_well_in_every_theme():
    import sys
    sys.path.insert(0, APP)
    from app import categorize
    themes = _theme_colours()
    for name, vars_ in themes.items():
        for surface in ("card", "panel"):
            for key, hexcode in {**categorize.PALETTE, "neutral": categorize.NEUTRAL_COLOUR}.items():
                assert contrast(hexcode, vars_[surface]) >= 3.0, (key, name, surface)   # WCAG 1.4.11: graphics
    assert not set(categorize.SPENDING_PALETTE) & set(categorize.INCOME_PALETTE)       # separate ranges
    assert len(set(categorize.PALETTE.values())) == len(categorize.PALETTE)


def test_built_ins_have_their_default_colours(env):
    from app import categorize
    with env.db() as c:
        cats_ = {x["name"]: x for x in categorize.all_categories(c, "tester")}
    for name, key in categorize.DEFAULT_COLOURS.items():
        assert cats_[name]["colour"] == key and cats_[name]["hex"] == categorize.PALETTE[key]
        assert key in categorize.palette_for(categorize.BUILTIN_KIND[name])
    assert set(categorize.DEFAULT_COLOURS) == set(categorize.BUILTIN_KIND)
    assert cats_["Payments"]["hex"] == categorize.NEUTRAL_COLOUR
    page = cats(env)
    assert 'style="background: #7a8c25"' in row(panel(page, "spending"), "Groceries")     # olive


def test_a_colour_is_saved_and_used_by_homes_and_compares_charts(env):
    chk = env.account("Checking")
    env.insert("custom_categories", user_id="tester", name="Side gig", kind="income")
    env.txn(chk, f"{MONTH}-01", -20.0, "CAFE", category="Dining")
    env.txn(chk, f"{MONTH}-02", 300.0, "GIG", category="Side gig")
    r = env.post("category-colour", data={"name": "Dining", "colour": "indigo", "tab": "spending"})
    assert r.headers["location"] == "categories?tab=spending&notice=cat_colour"
    env.post("category-colour", data={"name": "Side gig", "colour": "cyan", "tab": "income"})
    assert env.post("category-colour", data={"name": "Dining", "colour": "cyan"}).status_code == 400  # income's range
    assert env.post("category-colour", data={"name": "Dining", "colour": "#ff0000"}).status_code == 400
    with env.db() as c:
        assert c.execute("SELECT colour FROM category_prefs WHERE name = 'Dining'").fetchone()[0] == "indigo"
        assert c.execute("SELECT colour FROM custom_categories WHERE name = 'Side gig'").fetchone()[0] == "cyan"
    home = env.get("dashboard").text
    assert re.search(r'<span class="cat-dot" style="background: #6a7ae6"[^>]*></span><a href="transactions\?category=Dining',
                     home)
    assert 'class="share-seg" style="width: 100.00%; background: #6a7ae6"' in home
    assert re.search(r'style="background: #2d86c4"[^>]*></span><a href="transactions\?category=Side%20gig', home)
    prev = (date.today().replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    compare = env.get(f"dashboard-compare?mode=month&a={prev}&b={MONTH}").text
    assert '<span class="cat-dot" style="background: #6a7ae6" aria-hidden="true"></span>Dining' in compare
    assert '<span class="cat-dot" style="background: #2d86c4" aria-hidden="true"></span>Side gig' in compare
    menu = row(panel(cats(env), "spending"), "Dining")
    assert 'name="colour" value="indigo" checked' in menu and 'value="cyan"' not in menu
    env.post("category-colour", data={"name": "Dining", "colour": ""})                  # back to its default
    assert "#d4602a" in row(panel(cats(env), "spending"), "Dining")


# ------------------------------------------------------------------ G. Phone

def test_on_a_phone_rows_are_cards_and_add_and_edit_open_as_full_width_sheets():
    """The layout itself is checked in a browser at 390 px too; here: the rules the page relies on."""
    css = open(os.path.join(APP, "app", "static", "style.css"), encoding="utf-8").read()
    phone = css[css.index("/* G. Phone: rows are cards"):]
    phone = phone[:phone.index("\n}\n")]
    assert re.search(r"\.sheet\[open\] > \.sheet-body, \.row-menu\[open\] > \.menu-sheet \{\s*position: fixed; left: 0; "
                     r"right: 0; bottom: 0;", phone)
    assert "min-height: 44px" in phone                                  # big touch targets
    assert ".rule-chips .chip-group { flex-wrap: wrap; overflow-x: visible; }" in phone   # no sideways chips
    # the app's phone table pattern (every statements-table row becomes a card under 768px)
    assert re.search(r"@media \(max-width: 767px\) \{.*?table, thead, tbody, tr \{ display: block; width: 100%; \}", css, re.S)
    html = open(os.path.join(APP, "app", "templates", "categories.html"), encoding="utf-8").read()
    assert html.count('class="statements-table cat-table"') == 1 and 'class="statements-table rules-table"' in html
    assert "table-scroll" not in html.split('id="panel-rules"')[1]        # the rules table never scrolls sideways
    assert html.count("data-sheet") >= 3 and 'class="row-menu" data-menu' in html


# ------------------------------------------------------------------ who may change what

def test_another_persons_ids_and_names_change_nothing(env):
    chk, cid, ts, rule, other = _pets(env)
    env.get("accounts", headers=INTRUDER)
    tables = ("transactions", "category_rules", "custom_categories", "category_prefs", "skipped_duplicates",
              "category_suggestion_dismissals")
    before = dump(env, *tables)
    for url, data in [("category-rename", {"id": cid, "name": "Hacked"}), ("category-kind", {"id": cid, "kind": "income"}),
                      ("custom-category-delete", {"id": cid}), ("category-rule-edit", {"id": rule, "pattern": "X",
                                                                                       "category": "Y"}),
                      ("category-rule-delete", {"id": rule}), ("category-merge", {"name": "Pets", "into": "Shopping"}),
                      ("category-sort-income-apply", {"pick": str(ts[0]), f"to_{ts[0]}": "Salary"})]:
        env.post(url, data={k: str(v) for k, v in data.items()}, headers=INTRUDER)
    after = dump(env, *tables)
    for t in tables:      # the intruder's own (empty-handed) attempts touch only their own rows
        after[t] = [r for r in after[t] if "intruder" not in r]
    assert after == before


def test_an_admin_acting_as_someone_edits_theirs_and_shared_viewers_as_today(env):
    env.get("accounts", headers=WIFE)
    r = env.post("custom-categories?as_user=wife", data={"name": "Her income", "kind": "income", "tab": "income"})
    assert r.headers["location"] == "categories?tab=income&notice=cat_added&as_user=wife"
    env.post("category-hide?as_user=wife", data={"name": "Gas"})
    with env.db() as c:
        assert c.execute("SELECT user_id FROM custom_categories").fetchone()[0] == "wife"
        assert c.execute("SELECT user_id FROM category_prefs").fetchone()[0] == "wife"
    page = env.get("categories?tab=income&as_user=wife").text
    assert "Her income" in page and 'action="category-colour?as_user=wife"' in page
    assert 'name="as_user" value="wife"' in page                          # GET forms (search, Test) keep it too
    assert "Her income" not in env.get("categories?tab=income").text
    # a person an admin gave access to the tester's data works on it, as on every other page
    with env.db() as c:
        c.execute("INSERT INTO user_access (viewer_id, owner_id, granted_by) VALUES ('wife', 'tester', 'tester')")
    env.post("custom-categories?as_user=tester", data={"name": "Shared one", "tab": "spending"}, headers=WIFE)
    with env.db() as c:
        assert c.execute("SELECT user_id FROM custom_categories WHERE name = 'Shared one'").fetchone()[0] == "tester"


# ------------------------------------------------------------------ data

def test_the_migration_adds_the_columns_and_tables(env):
    with env.db() as c:
        assert {"kind", "colour"} <= {r[1] for r in c.execute("PRAGMA table_info(custom_categories)")}
        assert "last_matched_at" in {r[1] for r in c.execute("PRAGMA table_info(category_rules)")}
        assert {r[1] for r in c.execute("PRAGMA table_info(category_prefs)")} >= {"user_id", "name", "hidden", "colour"}
        assert {r[1] for r in c.execute("PRAGMA table_info(category_suggestion_dismissals)")} >= {
            "user_id", "merchant", "category"}
    from app import query_engine as qe
    assert {"category_prefs", "category_suggestion_dismissals"} <= set(qe.REGISTRY)


def test_the_query_tabs_categories_view_has_a_kind(env):
    from app import query_engine as qe
    env.insert("custom_categories", user_id="tester", name="Side gig", kind="income", colour="teal")
    env.insert("category_prefs", user_id="tester", name="Gas", hidden=1)
    res = qe.execute("tester", "SELECT name, kind, built_in, hidden, colour FROM categories ORDER BY name")
    rows = {r[0]: r[1:] for r in res.rows}
    assert rows["Salary"] == ("income", 1, 0, "emerald") and rows["Groceries"] == ("spending", 1, 0, "olive")
    assert rows["Side gig"] == ("income", 0, 0, "teal") and rows["Gas"][2] == 1 and rows["Payments"][0] is None
    assert "Income" not in rows
    other = qe.execute("other", "SELECT COUNT(*) FROM categories WHERE built_in = 0")
    assert other.rows[0][0] == 0


def test_one_persons_data_carries_colours_hidden_built_ins_and_dismissed_suggestions(env):
    from test_people_data import _craft, _export, _import, _upload
    for uid, name in (("tester", "Tester"), ("wife", "Wife")):
        env.insert("known_users", id=uid, name=name)
    env.insert("custom_categories", user_id="tester", name="Side gig", kind="income", colour="teal")
    env.post("category-hide", data={"name": "Gas"})
    env.post("category-colour", data={"name": "Dining", "colour": "plum"})
    env.post("category-suggestion-dismiss", data={"merchant": "AMAZON MKTPL", "category": "Shopping"})
    data = _export(env, pdfs=False)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        prefs = json.loads(z.read("data/category_prefs.json"))
        assert {(p["name"], p["hidden"], p["colour"]) for p in prefs} == {("Gas", 1, None), ("Dining", 0, "plum")}
        assert json.loads(z.read("data/category_suggestion_dismissals.json"))[0]["merchant"] == "AMAZON MKTPL"
    for _ in range(2):                                                      # twice: one of each, theirs kept
        assert _import(env, data, uid="wife").status_code == 200
    with env.db() as c:
        assert {tuple(r) for r in c.execute("SELECT name, hidden, colour FROM category_prefs WHERE user_id = 'wife'")} \
            == {("Gas", 1, None), ("Dining", 0, "plum")}
        assert [tuple(r) for r in c.execute("SELECT merchant, category FROM category_suggestion_dismissals "
                                            "WHERE user_id = 'wife'")] == [("AMAZON MKTPL", "Shopping")]
        assert c.execute("SELECT kind, colour FROM custom_categories WHERE user_id = 'wife'").fetchone()[:] == (
            "income", "teal")
    for bad in ({"category_prefs": [{"id": 1, "name": "Pets", "hidden": 1}]},
                {"category_prefs": [{"id": 1, "name": "Gas", "colour": "#ff0000"}]},
                {"category_suggestion_dismissals": [{"id": 1, "merchant": " ", "category": "X"}]}):
        assert _upload(env, _craft(bad)).status_code == 400, bad
    summary = env.get("api/admin/people/wife/data").json()
    assert {c["key"]: c["count"] for c in summary["counts"]}["rules"] >= 4
    with env.db() as c:
        from app import people_data
        counts = people_data.delete_rows(c, "wife", False)
    assert counts["category_prefs"] == 2 and counts["category_suggestion_dismissals"] == 1
