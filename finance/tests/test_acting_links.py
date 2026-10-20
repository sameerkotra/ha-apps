"""An admin "viewing as" someone stays on that person's data: every link to another page carries as_user."""
import re

import pytest

WIFE_DATA = "as_user=wife"


@pytest.fixture
def acting(env):
    env.insert("known_users", id="wife", name="Wife")
    a = env.insert("accounts", user_id="wife", name="Her checking", type="checking")
    env.insert("transactions", user_id="wife", account_id=a, date="2026-10-02", amount=-12.5, description="COFFEE")
    env.insert("transactions", user_id="wife", account_id=a, date="2026-10-03", amount=-7.5, description="FLAGGED",
               review_status="pending_review")
    st = env.insert("statements", user_id="wife", account_id=a, original_filename="s.pdf", status="complete")
    ub = env.insert("utility_bills", user_id="wife", utility_type="electric", provider="Xcel Energy",
                    status="pending_review", original_filename="x.pdf")
    ts = env.insert("toll_statements", user_id="wife", status="pending_review", original_filename="t.pdf")
    ci = env.insert("csv_imports", user_id="wife", original_filename="c.csv", row_count=0)
    return {"account": a, "statement": st, "bill": ub, "toll": ts, "csv": ci}


def _internal_links(html):
    page = html.split('<div class="page">', 1)[1] if '<div class="page">' in html else html
    page = re.sub(r'<div class="acting-banner">.*?</div>', "", page, flags=re.S)
    nav = html.split('<div class="page">', 1)[0]
    for href in re.findall(r'href="([^"]*)"', nav + page):
        if not href or href.startswith(("http", "#", "static/", "mailto:", "javascript:", "data:", "admin-storage-download")) or "{{" in href:
            continue
        yield href


def test_every_internal_link_keeps_the_person(env, acting):
    ids = acting
    pages = ["dashboard", "dashboard-compare", "transactions", "transactions?searched=1", "transfers", "recurring",
             "uploads", "review", "utilities?tab=upload", "utilities?tab=dashboard", "utility-comparison",
             "tolls?tab=upload", "tolls", "toll-compare", "toll-analyze", "reports", "report-new", "reports-check",
             "accounts", f"account?id={ids['account']}", "categories", "my-settings", "admin-storage",
             "admin-storage?tab=users", "admin-storage?tab=query", "recently-deleted", "ai-usage", "settings",
             f"statement-debug?id={ids['statement']}", f"csv-import-debug?id={ids['csv']}",
             f"toll-debug?id={ids['toll']}", f"utility-debug?id={ids['bill']}",
             f"utility-review?id={ids['bill']}", f"toll-review?id={ids['toll']}",
             f"account-delete-confirm?id={ids['account']}"]
    missing = []
    for url in pages:
        r = env.get(url + ("&" if "?" in url else "?") + WIFE_DATA)
        assert r.status_code == 200, (url, r.status_code)
        for href in _internal_links(r.text):
            if WIFE_DATA not in href:
                missing.append((url, href))
    assert not missing, "\n".join(f"{u} -> {h}" for u, h in missing)


def test_debug_pages_back_links_keep_the_person(env, acting):
    for url, back in ((f"statement-debug?id={acting['statement']}", "uploads?as_user=wife"),
                      (f"csv-import-debug?id={acting['csv']}", "uploads?as_user=wife"),
                      (f"toll-debug?id={acting['toll']}", "tolls?tab=upload&amp;as_user=wife"),
                      (f"utility-debug?id={acting['bill']}", "utilities?tab=upload&amp;as_user=wife")):
        html = env.get(url + "&" + WIFE_DATA).text
        assert f'<p class="back-link"><a href="{back}"' in html, url
