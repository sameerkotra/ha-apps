"""An account's website (SPEC.md section 11)."""


def _url(env, aid):
    with env.db() as c:
        return c.execute("SELECT website_url FROM accounts WHERE id = ?", (aid,)).fetchone()[0]


def test_add_edit_and_show_account_website(env):
    r = env.post("accounts", data={"name": "Card", "type": "credit_card", "website_url": "www.example-bank.com/login"})
    assert r.status_code == 303
    with env.db() as c:
        aid = c.execute("SELECT id FROM accounts WHERE name = 'Card'").fetchone()[0]
    assert _url(env, aid) == "https://www.example-bank.com/login"
    for page in ("accounts", f"account?id={aid}", "transactions-accounts"):
        html = env.get(page).text
        assert 'href="https://www.example-bank.com/login" target="_blank" rel="noopener noreferrer"' in html, page
    env.post("account-update", data={"id": str(aid), "name": "Card", "website_url": "http://bank.test/x"})
    assert _url(env, aid) == "http://bank.test/x"
    env.post("account-update", data={"id": str(aid), "name": "Card", "website_url": ""})
    assert _url(env, aid) is None


def test_only_http_links_are_kept(env):
    for bad in ("javascript:alert(1)", "data:text/html,x", "ftp://x.test", "https://", "http://a b.test"):
        env.post("accounts", data={"name": bad[:10], "type": "checking", "website_url": bad})
    with env.db() as c:
        assert c.execute("SELECT COUNT(*) FROM accounts WHERE website_url IS NOT NULL").fetchone()[0] == 0
