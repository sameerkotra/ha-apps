"""The "How the app sees you" page (WHOAMI_PAGE_SPEC.md): the shared contract drawn by whoami.html."""


def H(uid, name, **extra):
    return {"x-ingress-path": "/hassio_ingress/test", "x-remote-user-id": uid, "x-remote-user-name": name, **extra}


def test_not_an_admin_sees_what_to_add(env):
    r = env.client.get("whoami", headers=H("abc123", "jane.doe", **{"x-remote-user-display-name": "Jane"}))
    assert r.status_code == 200
    page = " ".join(r.text.split())
    assert "<strong>No</strong>" in page and "jane.doe" in page and "abc123" in page
    assert "Display name (not used for matching)</th><td>Jane</td>" in page
    assert "Add <strong>jane.doe</strong> (or <strong>abc123</strong>) exactly as shown" in page
    assert "restart" in page.lower()
    assert 'href="whoami' in page                                   # the user chip links here


def test_admin_matches_by_id_or_name_in_any_case(env):
    assert "<strong>Yes</strong>" in env.client.get("whoami", headers=H("tester", "x")).text       # id
    assert "<strong>Yes</strong>" in env.client.get("whoami", headers=H("zzz", "TESTER")).text      # name, upper case
    assert "<strong>No</strong>" in env.client.get("whoami", headers=H("zzz", "testers")).text


def test_count_only_never_the_list(make_env):
    env = make_env(ADMIN_USERS="tester,hidden.person")
    page = " ".join(env.client.get("whoami", headers=H("abc123", "jane.doe")).text.split())
    assert "hidden.person" not in page and "<td>2</td>" in page and "2 names" in page


def test_empty_list_says_restart(make_env):
    env = make_env(ADMIN_USERS="")
    page = " ".join(env.client.get("whoami", headers=H("abc123", "jane.doe")).text.split())
    assert "is <strong>empty</strong> in the running app" in page and "Finance Dashboard" in page
    assert "No admin yet" in page                                   # the banner, on this page too


def test_name_not_sent(env):
    h = {"x-ingress-path": "/hassio_ingress/test", "x-remote-user-id": "solo"}
    page = env.client.get("whoami", headers=h).text
    assert "<td>not sent</td>" in page


def test_needs_ingress(env):
    assert env.client.get("whoami", headers={"x-remote-user-id": "tester"}).status_code == 401
