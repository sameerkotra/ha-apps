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
        ("dashboard", "Home", None), ("transactions", "Transactions", "All"), ("review", "Upload", "Review"),
        ("transfers", "Transactions", "Transfers"), ("recurring", "Transactions", "Recurring"), ("transactions-accounts", "Transactions", "Accounts"),
        ("uploads", "Upload", "Statements"), ("utilities?tab=upload", "Upload", "Utility bill"),
        ("tolls?tab=upload", "Upload", "E-470 statement"), ("utilities?tab=dashboard", "Bills", "Utilities"),
        ("utility-comparison", "Bills", "Utilities"), ("tolls", "Bills", "E-470"), ("toll-compare", "Bills", "E-470"),
        ("toll-analyze", "Bills", "E-470"), ("reports", "Reports", None), ("accounts", "Setup", "Accounts"),
        ("categories", "Setup", "Categories"), ("admin-storage", "Admin", "Storage"),
        ("admin-storage?tab=query", "Admin", "Query"), ("admin-storage?tab=users", "Admin", "Users"),
        ("recently-deleted", "Admin", "Recently deleted"),
    ]
    for url, section, tab in cases:
        html = env.get(url).text
        assert _active_nav(html)[0].split(":")[0] == section or _active_nav(html)[0].startswith(section), (url, _active_nav(html))
        if tab:
            assert tab in _active_tab(html), (url, _active_tab(html))


def test_utilities_and_tolls_no_longer_have_their_own_upload_subtab(env):
    for url in ("utilities?tab=dashboard", "tolls?tab=dashboard", "toll-compare", "utility-comparison"):
        html = env.get(url).text
        assert not re.search(r'class="subtab[^"]*"[^>]*>Upload<', html), url


def test_more_panel_and_admin_only_items(env):
    html = env.get("dashboard").text
    assert 'class="nav-more"' in html and "admin-storage?tab=users" in html and "Admin" in html
    user = env.get("dashboard", headers=WIFE).text
    assert "nav-more-btn" in user and "admin-storage" not in user and "recently-deleted" not in user
    assert 'href="accounts' in user and 'href="categories' in user


def test_transactions_accounts_tab(env):
    old = env.account("Old card")
    env.txn(old, "2026-01-02", -1.0, "OLD")
    new = env.account("New checking")
    env.txn(new, "2026-09-01", -1.0, "NEW")
    env.account("Empty savings")
    env.insert("accounts", user_id="tester", name="Archived one", type="checking", is_archived=1)
    html = env.get("transactions-accounts").text
    assert html.index("Empty savings") < html.index("Old card") < html.index("New checking")
    assert "Archived one" not in html and "2026-01-02" in html and "Either" in html
    assert "HIDDEN" not in env.get("transactions-accounts", headers=WIFE).text


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


def test_upload_nav_remembers_the_last_upload_page(env):
    assert '"utilities?tab=upload"' in env.get("utilities?tab=upload").text.split("lastUploadPage")[0][-400:]
    html = env.get("dashboard").text
    assert "lastUploadPage" in html and "data-upload-nav" in html
