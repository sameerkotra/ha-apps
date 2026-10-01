"""Storage → Users tab and shared access between regular users (SPEC.md section 20)."""
WIFE = {"x-remote-user-id": "wife", "x-remote-user-name": "Wife"}
THIRD = {"x-remote-user-id": "third", "x-remote-user-name": "Third"}


def _setup(env):
    for uid, name in (("tester", "Tester"), ("wife", "Wife"), ("third", "Third")):
        env.insert("known_users", id=uid, name=name)
    mine = env.account("Joint checking")
    env.txn(mine, "2026-09-01", -12.5, "GROCER ONE")
    hers = env.account("Wife own", user="wife")
    env.txn(hers, "2026-09-02", -3.0, "HER OWN COFFEE", user="wife")
    theirs = env.account("Third's", user="third")
    env.txn(theirs, "2026-09-03", -9.0, "THIRD SECRET", user="third")


def _grant(env, viewer="wife", owner="tester"):
    return env.post("user-access-grant", data={"viewer": viewer, "owner": owner})


def test_users_tab_is_admin_only(env):
    _setup(env)
    page = env.get("admin-storage?tab=users").text
    assert "Wife" in page and "Third" in page and "Give access" in page
    assert env.get("admin-storage?tab=users", headers=WIFE).status_code == 403
    assert env.post("user-access-grant", data={"viewer": "wife", "owner": "tester"}, headers=WIFE).status_code == 403
    assert env.post("user-access-remove", data={"viewer": "wife", "owner": "tester"}, headers=WIFE).status_code == 403


def test_shared_user_opens_into_the_owners_data_and_can_switch(env):
    _setup(env)
    assert "HER OWN COFFEE" in env.get("transactions?searched=1", headers=WIFE).text
    r = _grant(env)
    assert r.status_code == 303
    page = env.get("transactions?searched=1", headers=WIFE).text
    assert "GROCER ONE" in page and "HER OWN COFFEE" not in page
    assert "shared with you" in page and "My own data" in page
    own = env.get("transactions?searched=1&as_user=wife", headers=WIFE).text
    assert "HER OWN COFFEE" in own and "GROCER ONE" not in own
    # anyone not granted is ignored: back to the data she opens into
    sneaky = env.get("transactions?searched=1&as_user=third", headers=WIFE).text
    assert "THIRD SECRET" not in sneaky and "GROCER ONE" in sneaky


def test_shared_user_has_full_rights_but_no_admin_and_no_wipe(env):
    _setup(env)
    _grant(env)
    assert env.get("admin-storage", headers=WIFE).status_code == 403
    assert env.get("user-wipe", headers=WIFE).status_code == 403
    assert env.post("user-wipe", data={"confirm_text": "Tester"}, headers=WIFE).status_code == 403
    r = env.get("report?id=1", headers=WIFE).text          # reports run on the shared data
    assert "12.50" in r or "No rows" in r


def test_remove_access_and_admin_viewer_refused(env):
    _setup(env)
    _grant(env)
    env.post("user-access-remove", data={"viewer": "wife", "owner": "tester"})
    page = env.get("transactions?searched=1", headers=WIFE).text
    assert "HER OWN COFFEE" in page and "GROCER ONE" not in page and "shared with you" not in page
    r = _grant(env, viewer="tester", owner="wife")
    assert "admin" in r.headers["location"]
    with env.db() as c:
        assert c.execute("SELECT COUNT(*) FROM user_access").fetchone()[0] == 0


def test_first_grant_is_where_they_open(env):
    _setup(env)
    _grant(env, owner="tester")
    _grant(env, owner="third")
    page = env.get("transactions?searched=1", headers=WIFE).text
    assert "GROCER ONE" in page
    assert "THIRD SECRET" in env.get("transactions?searched=1&as_user=third", headers=WIFE).text


def test_admin_acting_as_still_works(env):
    _setup(env)
    assert "HER OWN COFFEE" in env.get("transactions?searched=1&as_user=wife").text


def test_shared_user_changes_land_in_the_owners_data(env):
    _setup(env)
    _grant(env)
    r = env.post("accounts", data={"name": "Added by wife", "type": "checking"}, headers=WIFE)
    assert r.status_code in (200, 303)
    with env.db() as c:
        assert c.execute("SELECT user_id FROM accounts WHERE name = 'Added by wife'").fetchone()[0] == "tester"


def test_user_access_is_hidden_from_queries(env):
    _setup(env)
    _grant(env)
    r = env.post("query-run", data={"sql": "SELECT * FROM user_access", "per_page": "100"}).text
    assert 'class="q-error"' in r


def test_recently_deleted_is_admin_only(env):
    _setup(env)
    for url in ("recently-deleted", "user-wipe", "permanent-delete-confirm?kind=account&id=1"):
        assert env.get(url, headers=WIFE).status_code == 403, url
    for url in ("statement-restore", "csv-import-restore", "transaction-restore", "account-restore",
                "utility-restore", "toll-restore", "permanent-delete", "user-wipe"):
        assert env.post(url, data={"id": "1"}, headers=WIFE).status_code == 403, url
    assert "recently-deleted" not in env.get("accounts", headers=WIFE).text
    assert env.get("recently-deleted").status_code == 200
    assert 'href="recently-deleted' in env.get("accounts").text


def test_users_can_still_delete(env):
    _setup(env)
    with env.db() as c:
        aid = c.execute("SELECT id FROM accounts WHERE user_id = 'wife'").fetchone()[0]
    page = env.get(f"account-delete-confirm?id={aid}", headers=WIFE)
    assert page.status_code == 200 and "the admin can restore it" in page.text
