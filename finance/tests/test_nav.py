import os
"""The six-section navigation (SPEC.md section 13)."""
import re

WIFE = {"x-remote-user-id": "wife", "x-remote-user-name": "Wife"}


def _active_nav(html):
    return re.findall(r'class="nav-item[^"]*\bactive\b[^"]*"[^>]*title="([^"]+)"', html)


def _active_tab(html):
    return re.findall(r'class="subtab active"[^>]*>([^<]+)<', html)


def test_each_page_lights_its_section_and_tab(env):
    env.account()
    cases = [
        ("dashboard", "Home", "Overview"), ("dashboard-compare", "Home", "Compare"), ("transactions", "Money", "Transactions"),
        ("review", "Upload", "Review"),
        ("transfers", "Money", "Transfers"), ("recurring", "Money", "Recurring"),
        ("uploads", "Upload", "Statements"), ("utilities?tab=upload", "Upload", "Utility bill"),
        ("tolls?tab=upload", "Upload", "Toll statement"), ("utilities?tab=dashboard", "Bills", "Utilities"),
        ("utility-comparison", "Bills", "Compare utilities"), ("tolls", "Bills", "Tolls"), ("toll-compare", "Bills", "Compare tolls"),
        ("toll-analyze", "Bills", "Analyze tolls"), ("reports", "Reports", "Saved"), ("report-new", "Reports", "New"),
        ("reports-check", "Reports", "Check"), ("accounts", "Accounts", None),
        ("categories", "Settings", "Categories & rules"), ("my-settings", "Settings", "My settings"),
        ("whoami", "Settings", "How the app sees you"), ("admin-storage", "Admin", "Storage"),
        ("admin-storage?tab=query", "Admin", "Query"), ("admin-storage?tab=users", "Admin", "Users"),
        ("recently-deleted", "Admin", "Recently deleted"), ("settings", "Admin", "App settings"), ("ai-usage", "Admin", "AI usage"),
    ]
    for url, section, tab in cases:
        html = env.get(url).text
        assert _active_nav(html) and _active_nav(html)[0].startswith(section), (url, _active_nav(html))
        if tab:
            assert tab in [t.strip().replace("&amp;", "&") for t in _active_tab(html)], (url, _active_tab(html))
    # the account page lights Accounts and leads back to the list
    a = env.account("Second")
    html = env.get(f"account?id={a}").text
    assert _active_nav(html)[0] == "Accounts" and 'class="back-link"' in html and 'href="accounts"' in html


def test_admin_tabs_order_and_admin_opens_app_settings(env):
    html = env.get("settings").text
    tabs = [t.strip() for t in re.findall(r'class="subtab ?(?:active)? *"[^>]*>([^<]+)<', html)]
    assert tabs == ["App settings", "Users", "Storage", "Query", "Recently deleted", "AI usage"], tabs
    assert re.search(r'<a href="settings" class="nav-item[^"]*"[^>]*title="Admin', html)


def test_settings_tabs_are_named_my_settings_and_app_settings(env):
    html = env.get("my-settings").text
    assert ">My settings<" in html and "<h1>My settings</h1>" in html
    assert "<h1>App settings</h1>" in env.get("settings").text


def test_upload_pages_have_their_own_titles(env):
    assert "<h1>Upload statements</h1>" in env.get("uploads").text
    assert "<h1>Upload utility bill</h1>" in env.get("utilities?tab=upload").text
    assert "<h1>Upload toll statement</h1>" in env.get("tolls?tab=upload").text


def test_bills_pages_have_one_tab_row(env):
    for url in ("utilities?tab=dashboard", "tolls?tab=dashboard", "toll-compare", "toll-analyze", "utility-comparison"):
        html = env.get(url).text
        assert html.count('class="subtabs') == 1, url


def test_new_report_lives_under_reports(env):
    html = env.get("reports").text
    assert 'href="report-new"' in html
    page = html.split('<div class="page">')[1]
    assert "admin-storage?tab=query" not in page
    html = env.get("report-new").text
    assert "<h1>New report</h1>" in html and 'id="query-form"' in html
    assert env.get("report-new", headers=WIFE).status_code == 403


def test_utilities_and_tolls_no_longer_have_their_own_upload_subtab(env):
    for url in ("utilities?tab=dashboard", "tolls?tab=dashboard", "toll-compare", "utility-comparison"):
        html = env.get(url).text
        assert not re.search(r'class="subtab[^"]*"[^>]*>Upload<', html), url


def test_more_panel_and_admin_only_items(env):
    html = env.get("dashboard").text
    assert 'class="nav-more"' in html and "admin-storage?tab=users" in html and "Admin" in html
    more = html.split('id="nav-more"')[1]
    for href in ('href="accounts', 'href="my-settings', 'href="categories', 'href="whoami', 'href="settings',
                 'href="utilities?tab=dashboard', 'href="tolls?tab=dashboard', "theme-select"):
        assert href in more, href
    user = env.get("dashboard", headers=WIFE).text
    assert "nav-more-btn" in user and "admin-storage" not in user and "recently-deleted" not in user
    assert 'href="accounts' in user and 'href="categories' in user
    # the phone bar: Home, Money, Upload, Reports and More (the rest are desktop-only)
    bar = html.split('<div class="nav-items">')[1].split('<div class="nav-user">')[0]
    phone = [t for t in re.findall(r'class="nav-item ([^"]*)"[^>]*title="([^":]+)', bar) if "nav-desktop-only" not in t[0]]
    assert [t[1] for t in phone] == ["Home", "Money", "Upload", "Reports", "More"], phone


def test_review_badge_counts_what_waits(env):
    a = env.account()
    assert "nav-badge-review" not in env.get("dashboard").text
    env.txn(a, "2026-01-02", -5.0, "FLAGGED", review_status="pending_review")
    env.txn(a, "2026-01-03", -6.0, "FLAGGED 2", review_status="pending_review")
    html = env.get("dashboard").text
    assert re.search(r'nav-badge nav-badge-review[^>]*>2<', html)
    assert 'class="tab-badge"' in env.get("uploads").text


def test_accounts_is_one_page(env):
    old = env.account("Old card")
    env.txn(old, "2026-01-02", -1.0, "OLD")
    new = env.account("New checking")
    env.txn(new, "2026-09-01", -1.0, "NEW")
    env.account("Empty savings")
    env.insert("accounts", user_id="tester", name="Archived one", type="checking", is_archived=1)
    html = env.get("accounts").text
    assert html.index("Empty savings") < html.index("Old card") < html.index("New checking")
    assert "Archived one" not in html and "2026-01-02" in html and "Any" in html
    assert f'href="account?id={old}"' in html and f'uploads?account={old}' in html and f"account_id={old}" in html
    assert "account-archive" not in html and "account-delete-confirm" not in html   # on the account's own page
    assert "Archived one" in env.get("accounts?show_archived=1").text
    assert "HIDDEN" not in env.get("accounts", headers=WIFE).text
    r = env.get("transactions-accounts", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "accounts"
    detail = env.get(f"account?id={old}").text
    assert "account-archive" in detail and "account-delete-confirm" in detail
    assert detail.index("<h2>Statements</h2>") < detail.index("<h2>Manage</h2>") < detail.index("account-delete-confirm")


def test_debug_and_review_pages_go_back_to_their_upload_page(env):
    ub = env.insert("utility_bills", user_id="tester", utility_type="electric", provider="Xcel Energy",
                    status="pending_review", original_filename="x.pdf")
    ts = env.insert("toll_statements", user_id="tester", status="pending_review", original_filename="t.pdf")
    a = env.account()
    st = env.insert("statements", user_id="tester", account_id=a, original_filename="s.pdf", status="complete")
    ci = env.insert("csv_imports", user_id="tester", original_filename="c.csv", row_count=0)
    cases = [(f"utility-debug?id={ub}", 'href="utilities?tab=upload"'), (f"utility-review?id={ub}", 'href="utilities?tab=upload"'),
             (f"toll-debug?id={ts}", 'href="tolls?tab=upload"'), (f"toll-review?id={ts}", 'href="tolls?tab=upload"'),
             (f"statement-debug?id={st}", 'href="uploads"'), (f"csv-import-debug?id={ci}", 'href="uploads"')]
    for url, back in cases:
        html = env.get(url).text
        assert back in html, url
        assert _active_nav(html)[0] == "Upload", (url, _active_nav(html))


def test_utility_actions_stay_on_the_utility_upload_page(env):
    ub = env.insert("utility_bills", user_id="tester", utility_type="electric", provider="Xcel Energy", status="complete")
    r = env.post("utility-delete", data={"id": str(ub)})
    assert r.status_code == 303 and r.headers["location"].startswith("utilities?tab=upload")


def test_upload_nav_always_opens_statements(env):
    html = env.get("utilities?tab=upload").text
    assert re.search(r'<a href="uploads" class="nav-item[^"]*active', html)
    with open(os.path.join(os.path.dirname(__file__), "..", "app", "static", "base.js")) as f:
        js = f.read()
    assert "lastUploadPage" not in js and "finance.txnQuery:" in js and "BackNav.init" in js
    assert "static/common/backnav.js" in html


def test_return_to_own_view_keeps_the_page(env):
    env.insert("known_users", id="wife", name="Wife")
    html = env.get("categories?as_user=wife&tab=rules").text
    assert '<a href="categories?tab=rules">return to your own view</a>' in html


def test_waiting_for_you_sits_above_the_drop_zone(env):
    a = env.account("Checking")
    env.insert("statements", user_id="tester", account_id=a, original_filename="s.pdf", status="pending_review",
               needs_confirmation=1)
    page = env.get("uploads").text
    assert page.count('id="upload-waiting"') == 1 and page.index('id="upload-waiting"') < page.index('id="drop-zone"')
    assert env.get("uploads-history").text.count('id="upload-waiting"') == 1     # the refresh brings it along


def test_transactions_open_on_this_month_with_totals_above(env):
    from datetime import date
    a = env.account()
    env.txn(a, date.today().replace(day=1).isoformat(), -12.5, "THIS MONTH")
    env.txn(a, "2001-01-05", -3.0, "LONG AGO")
    page = env.get("transactions").text
    assert "THIS MONTH" in page and "LONG AGO" not in page
    assert page.index('class="summary-strip txn-summary"') < page.index("THIS MONTH")
    assert "LONG AGO" in env.get("transactions?searched=1").text
