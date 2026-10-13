"""The proxy end to end against a fake site (spec/SPEC.md §3, §4, §10): access, request and response cleaning,
rewriting, streaming, limits, errors, the access log."""
import _env  # noqa: F401  (must be first)

import json
import time
import unittest

from app import db, settings
from helpers import ADMIN, BOB, CAROL, INGRESS, ProxyCase

P = f"{INGRESS}/s/fake"


class Access(ProxyCase):
    def test_admins_only_by_default(self):
        self.add_site()
        self.assertEqual(self.get("/s/fake/echo").status_code, 200)
        r = self.get("/s/fake/echo", BOB)
        self.assertEqual(r.status_code, 404)              # the same as a site that doesn't exist
        self.assertNotIn("127.0.0.1", r.text)
        self.assertEqual(r.text, self.get("/s/nope/echo", BOB).text)

    def test_everyone_and_chosen(self):
        self.add_site(access="everyone")
        self.assertEqual(self.get("/s/fake/echo", BOB).status_code, 200)
        self.add_site(name="Two", slug="two", access="chosen", people=["u-bob"])
        self.assertEqual(self.get("/s/two/echo", BOB).status_code, 200)
        self.assertEqual(self.get("/s/two/echo", CAROL).status_code, 404)

    def test_launcher_lists_only_allowed_sites(self):
        self.add_site(access="everyone")
        self.add_site(name="Secret", slug="secret")
        names = [s["slug"] for s in self.get("/api/sites", BOB).json()["sites"]]
        self.assertEqual(names, ["fake"])
        self.assertEqual(len(self.get("/api/sites").json()["sites"]), 2)

    def test_turned_off_person_opens_nothing(self):
        self.add_site(access="everyone")
        self.c.put("/api/admin/people/u-bob", json={"enabled": False}, headers=ADMIN)
        r = self.get("/s/fake/echo", BOB)
        self.assertEqual(r.status_code, 403)
        self.assertIn("turned you off", r.text)
        self.assertEqual(self.get("/s/nope/echo", BOB).text, r.text)       # the same for every short name
        self.assertEqual(self.get("/api/sites", BOB).json()["sites"], [])

    def test_no_user_and_unknown_site(self):
        self.add_site()
        r = self.c.get("/s/fake/echo")
        self.assertEqual(r.status_code, 401)
        self.assertEqual(self.get("/s/nope/x").status_code, 404)

    def test_outsiders_refused(self):
        from common_tests.ingress import ingress_client
        from app import main
        self.add_site(access="everyone")
        outsider = ingress_client(main.app, peer=("172.30.33.9", 5555))
        self.assertEqual(outsider.get("/s/fake/echo", headers=ADMIN).status_code, 403)
        self.assertEqual(outsider.get("/api/sites", headers=ADMIN).status_code, 403)

    def test_cross_site_writes_refused_on_sites_too(self):
        self.add_site()
        for site in ("cross-site", "same-site"):
            r = self.c.post("/s/fake/echo", content=b"x", headers={**ADMIN, "Sec-Fetch-Site": site})
            self.assertEqual(r.status_code, 403)
        r = self.c.post("/s/fake/echo", content=b"x", headers={**ADMIN, "Sec-Fetch-Site": "same-origin"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.get("/s/fake/echo", headers={"Sec-Fetch-Site": "cross-site"}).status_code, 200)

    def test_no_way_to_another_host_or_up_the_path(self):
        self.add_site()
        for path in ("/s/fake/%2e%2e/x", "/s/fake/%252e%252e/x", "/s/fake/a/%2E%2E%2Fb", "/s/fake/%2e/x"):
            self.assertEqual(self.get(path).status_code, 400, path)
        r = self.get("/s/fake//example.com/echo")
        self.assertIn(r.status_code, (404, 200))           # the site's own path "//example.com/echo"
        if r.status_code == 200:
            self.assertEqual(r.json()["headers"]["host"], f"127.0.0.1:{self.port}")

    def test_bare_site_path_redirects_inside_ingress(self):
        self.add_site()
        r = self.get("/s/fake", follow_redirects=False)
        self.assertEqual(r.status_code, 307)
        self.assertEqual(r.headers["location"], "fake/")


class RequestCleaning(ProxyCase):
    def echo(self, **headers):
        r = self.c.post("/s/fake/echo?a=1&b=%2F", content=b"hello", headers={**ADMIN, **headers})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_home_assistant_headers_and_cookies_never_reach_the_site(self):
        self.add_site(headers=[{"name": "Authorization", "value": "Basic c2VjcmV0"}])
        e = self.echo(**{"Cookie": "ingress_session=HA-SECRET; hp_fake_sid=abc; other=1; hp_other_sid=x",
                         "X-Forwarded-For": "1.2.3.4",
                         "X-Forwarded-Proto": "https", "X-Hass-Source": "core", "X-Real-IP": "1.2.3.4",
                         "Forwarded": "for=1.2.3.4"})
        h = e["headers"]
        for k in h:
            self.assertFalse(k.startswith(("x-remote-user", "x-hass", "x-forwarded", "x-ingress")), k)
        self.assertNotIn("forwarded", h)
        self.assertNotIn("x-real-ip", h)
        self.assertEqual(h["cookie"], "sid=abc")
        self.assertEqual(h["authorization"], "Basic c2VjcmV0")
        self.assertEqual(h["host"], f"127.0.0.1:{self.port}")
        self.assertEqual(e["query"], "a=1&b=%2F")
        self.assertEqual(e["body"], "hello")

    def test_query_brackets_as_the_browser_sent_them(self):
        # Home Assistant's ingress escapes [ ] { } | ^ ` in the query; the site gets them as the browser sent them,
        # and every other escape is left alone.
        self.add_site()
        e = self.get("/s/fake/echo?f%5Bx%5D%5B%5D=1&g=%7B%7D&s=a+b&t=%2F%26%3D%25").json()
        self.assertEqual(e["query"], "f[x][]=1&g={}&s=a+b&t=%2F%26%3D%25")

    def test_origin_and_referer_become_the_sites_own(self):
        self.add_site()
        e = self.echo(Origin="https://ha.example.com",
                      Referer=f"https://ha.example.com{P}/stacks/1?x=2")
        self.assertEqual(e["headers"]["origin"], f"http://127.0.0.1:{self.port}")
        self.assertEqual(e["headers"]["referer"], f"http://127.0.0.1:{self.port}/stacks/1?x=2")

    def test_pass_user_and_keep_host(self):
        self.add_site(pass_user=True, keep_host=True)
        h = self.echo()["headers"]
        self.assertEqual(h["remote-user"], "adminy")
        self.assertEqual(h["x-forwarded-user"], "adminy")
        self.assertEqual(h["host"], "testserver")

    def test_browser_cant_forge_header_sign_in(self):
        forged = {"Remote-User": "adminy", "Remote-Name": "Ann", "Remote-Email": "ann (at) example",
                  "X-Forwarded-User": "adminy", "Remote-Groups": "admins"}
        self.add_site(access="everyone")
        raw = self.echo_as(BOB, forged)
        self.assertFalse([k for k, _ in raw if k.lower().startswith(("remote-", "x-forwarded-user"))], raw)
        self.add_site(name="Signed", slug="signed", access="everyone", pass_user=True)
        r = self.c.post("/s/signed/echo", content=b"x", headers={**BOB, **forged})
        users = [v for k, v in r.json()["raw"] if k.lower() == "remote-user"]
        self.assertEqual(users, ["bob"])                     # exactly one, the proxy's own

    def echo_as(self, who, extra):
        return self.c.post("/s/fake/echo", content=b"x", headers={**who, **extra}).json()["raw"]

    def test_trace_not_passed_on(self):
        self.add_site(headers=[{"name": "Authorization", "value": "Basic SECRET"}])
        r = self.c.request("TRACE", "/s/fake/echo", headers=ADMIN)
        self.assertEqual(r.status_code, 405)
        self.assertNotIn("SECRET", r.text)

    def test_extra_headers_never_reach_the_browser(self):
        self.add_site(headers=[{"name": "X-Api-Key", "value": "k-123456"}])
        r = self.get("/api/admin/sites")
        self.assertNotIn("k-123456", r.text)
        page = self.get("/s/fake/")
        self.assertNotIn("k-123456", page.text)
        self.assertNotIn("k-123456", json.dumps(dict(page.headers)))

    def test_saved_header_value_kept_when_left_blank(self):
        s = self.add_site(headers=[{"name": "X-Api-Key", "value": "k-1"}])
        body = {"name": "Fake", "target": f"http://127.0.0.1:{self.port}", "trusted_ok": True,
                "headers": [{"name": "X-Api-Key", "value": None}]}
        self.assertEqual(self.c.put(f"/api/admin/sites/{s['id']}", json=body, headers=ADMIN).status_code, 200)
        self.assertEqual(self.echo()["headers"]["x-api-key"], "k-1")

    def test_reserved_extra_headers_refused(self):
        for name in ("Host", "X-Remote-User-Id", "Cookie", "X-Forwarded-For", "Sec-Fetch-Site"):
            r = self.c.post("/api/admin/sites", headers=ADMIN, json={
                "name": "Fake", "target": f"http://127.0.0.1:{self.port}", "trusted_ok": True,
                "headers": [{"name": name, "value": "x"}]})
            self.assertEqual(r.status_code, 422, name)

    def test_upload_limit(self):
        self.add_site()
        settings.update({"max_upload_mb": 1}, None)
        r = self.c.post("/s/fake/echo", content=b"x" * (1024 * 1024 + 1), headers=ADMIN)
        self.assertEqual(r.status_code, 413)
        r = self.c.post("/s/fake/echo", content=b"x" * 1000, headers=ADMIN)
        self.assertEqual(r.json()["size"], 1000)

    def test_chunked_upload_over_the_limit(self):
        self.add_site()
        settings.update({"max_upload_mb": 1}, None)

        def gen():
            for _ in range(20):
                yield b"y" * 65536
        r = self.c.post("/s/fake/echo", content=gen(), headers=ADMIN)
        self.assertEqual(r.status_code, 413)


class Responses(ProxyCase):
    def test_page_rewritten(self):
        self.add_site()
        r = self.get("/s/fake/")
        self.assertEqual(r.status_code, 200)
        t = r.text
        for want in (f'<base href="{P}/">', f'href="{P}/static/app.css"', f'src="{P}/static/app.js"',
                     f'href="{P}/stacks"', 'href="stacks/1"', 'href="https://example.com/x"',
                     f'srcset="{P}/img/a.png 1x, {P}/img/b.png 2x"', f'action="{P}/login"',
                     f'formaction="{P}/login2"', f"url(&#x27;{P}/img/bg.png&#x27;)", f"url({P}/img/c.png)",
                     f'href="{P}/icons.svg#a"', f"url={P}/again", "Caf&eacute; &amp; &#169; — ünïcode"):
            self.assertIn(want, t)
        self.assertNotIn("integrity", t)                          # the stylesheet is rewritten
        self.assertEqual(int(r.headers["content-length"]), len(r.content))

    def test_headers_cleaned(self):
        self.add_site()
        r = self.get("/s/fake/", headers={"Referer": f"http://ha.local:8123{P}/"})
        h = r.headers
        for gone in ("x-frame-options", "strict-transport-security", "clear-site-data"):
            self.assertNotIn(gone, h)
        csps = r.headers.get_list("content-security-policy")
        self.assertIn("frame-ancestors 'self'", csps)
        site_csp = [c for c in csps if c != "frame-ancestors 'self'"][0]
        self.assertNotIn("frame-ancestors", site_csp)
        self.assertNotIn("upgrade-insecure-requests", site_csp)     # Home Assistant is plain http here
        cookie = h["set-cookie"]
        self.assertTrue(cookie.startswith("hp_fake_sid=abc;"), cookie)      # the site's own name in the browser
        self.assertIn(f"Path={P}/", cookie)
        self.assertNotIn("Domain", cookie)
        self.assertNotIn("Secure", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertEqual(h["link"], f"<{P}/static/app.css>; rel=preload; as=style")

    def test_secure_cookies_kept_over_https(self):
        self.add_site()
        cookie = self.get("/s/fake/", headers={"Referer": f"https://ha.example.com{P}/"}).headers["set-cookie"]
        self.assertIn("Secure", cookie)
        self.assertIn("SameSite=None", cookie)

    def test_css_rewritten_js_untouched(self):
        self.add_site()
        self.assertEqual(self.get("/s/fake/static/app.css").text,
                         f'@import "{P}/static/more.css"; body{{background:url("{P}/img/z.png")}}')
        self.assertEqual(self.get("/s/fake/static/app.js").text, 'fetch("/api/data")')

    def test_redirects(self):
        self.add_site()
        self.assertEqual(self.get("/s/fake/r", follow_redirects=False).headers["location"], f"{P}/login")
        self.assertEqual(self.get("/s/fake/r-abs", follow_redirects=False).headers["location"], f"{P}/home")
        self.assertEqual(self.get("/s/fake/r-away", follow_redirects=False).headers["location"],
                         "https://example.com/away")

    def test_gzip_page_decoded_and_rewritten(self):
        self.add_site()
        r = self.get("/s/fake/gz", headers={"Accept-Encoding": "identity"})
        self.assertIn(f"href='{P}/z'".replace("'", '"'), r.text)
        self.assertNotIn("content-encoding", r.headers)

    def test_big_download_streams_untouched(self):
        self.add_site()
        r = self.get("/s/fake/big")
        self.assertEqual(len(r.content), 48 * 65536)
        self.assertEqual(r.headers["content-length"], str(48 * 65536))

    def test_html_over_the_cap_passes_untouched(self):
        self.add_site()
        r = self.get("/s/fake/huge")
        self.assertEqual(r.status_code, 200)
        self.assertIn("<a href='/x'>x</a>", r.text[:200])
        self.assertTrue(r.text.endswith("</body></html>"))

    def test_slow_stream_not_cut_by_the_timeout(self):
        self.add_site(timeout_s=1)
        t = time.monotonic()
        r = self.get("/s/fake/slow")
        self.assertEqual(r.text, "data: 0\n\ndata: 1\n\ndata: 2\n\n")
        self.assertGreater(time.monotonic() - t, 0.9)

    def test_site_errors_pass_through(self):
        self.add_site()
        r = self.get("/s/fake/status/418")
        self.assertEqual((r.status_code, r.text), (418, "teapot"))

    def test_unreachable_site(self):
        self.add_site(target="http://127.0.0.1:9")                  # nothing listens on the discard port
        r = self.get("/s/fake/")
        self.assertEqual(r.status_code, 502)
        self.assertIn("refused", r.text)
        self.assertIn("frame-ancestors 'self'", r.headers["content-security-policy"])


class AccessLog(ProxyCase):
    def test_off_by_default_and_on_per_site(self):
        self.add_site()
        self.get("/s/fake/echo?secret=1")
        with db.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM access_log").fetchone()[0], 0)
        self.add_site(name="Logged", slug="logged", access_log=True)
        self.get("/s/logged/echo?secret=1")
        rows = self.get("/api/admin/sites/2/log").json()["log"]
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["path"], rows[0]["status"], rows[0]["name"]), ("/echo", 200, "Ann Admin"))
        self.assertEqual(self.get("/api/admin/sites/2/log", BOB).status_code, 403)


if __name__ == "__main__":
    unittest.main()


class SiteCookies(ProxyCase):
    """Each site's cookies are its own: kept in the browser as hp_<slug>_<name> on the site's path, and only those are
    passed back to it (spec §3.1)."""

    def test_named_for_the_site_and_back(self):
        self.add_site()
        r = self.get("/s/fake/cookie-host", headers={"Referer": f"https://ha.example.com{P}/"})
        cookies = r.headers.get_list("set-cookie")
        self.assertIn(f"hp_fake___Host-tok=t1; Path={P}/; Secure; HttpOnly", cookies)
        self.assertIn(f"hp_fake___Secure-s=s1; Path={P}/app; Secure", cookies)
        for c in cookies:
            self.assertFalse(c.startswith("__"), c)     # a __Host- cookie on P/ would be refused by the browser
        e = self.c.post("/s/fake/echo", content=b"x", headers={
            **ADMIN, "Cookie": "hp_fake___Host-tok=t1; hp_fake___Secure-s=s1; ingress_session=HA; __Host-tok=forged; "
                               "hp_two_sid=other-site; hp_fakeish_x=1; hp_fake=nameless"}).json()
        self.assertEqual(e["headers"]["cookie"], "__Host-tok=t1; __Secure-s=s1")

    def test_no_cookie_header_when_none_is_the_sites(self):
        self.add_site()
        e = self.c.post("/s/fake/echo", content=b"x", headers={**ADMIN, "Cookie": "ingress_session=HA; a=1"}).json()
        self.assertNotIn("cookie", e["headers"])


class ForgedHeaders(ProxyCase):
    def test_underscores_cant_stand_in_for_dropped_headers(self):
        # many servers (and frameworks that turn headers into variables) read Remote_User as Remote-User
        self.add_site(access="everyone", headers=[{"name": "X-Api-Key", "value": "real"}])
        raw = self.c.post("/s/fake/echo", content=b"x", headers={
            **BOB, "Remote_User": "adminy", "X_Forwarded_User": "adminy", "X_Remote_User_Id": "u-admin",
            "X_Ingress_Path": "/x", "X_Hass_Source": "core", "Remote_Groups": "admins", "X_Api_Key": "forged",
            "Cookie_": "a"}).json()["raw"]
        names = [k.lower() for k, _ in raw]
        for k in names:
            self.assertFalse(k.replace("_", "-").startswith(("remote-", "x-forwarded-user", "x-remote-user",
                                                             "x-ingress", "x-hass")), k)
        self.assertEqual([v for k, v in raw if k.lower().replace("_", "-") == "x-api-key"], ["real"])


class FromProxiedPages(ProxyCase):
    """A page opened through the proxy can't use the app's admin API or change anything through the app's API (spec
    §4.3) — a speed bump, not a wall: without a Referer or Sec-Fetch-Dest nothing tells such a page apart."""

    def put_settings(self, **headers):
        return self.c.put("/api/admin/settings", json={"access_log_days": 8}, headers={**ADMIN, **headers})

    def test_admin_api_refused_from_a_proxied_page(self):
        self.add_site()
        for page in (f"http://testserver{P}/stacks", f"http://testserver{INGRESS}/s/other/",
                     "http://testserver/stacks/1"):                     # single-page app mode: the site's own path
            r = self.put_settings(Referer=page)
            self.assertEqual(r.status_code, 403, page)
            self.assertIn("site opened through the proxy", r.json()["detail"])
            self.assertEqual(self.get("/api/admin/sites", headers={"Referer": page}).status_code, 403)
            self.assertEqual(self.c.post("/api/monitor/fake/mute", json={"muted": True},
                                         headers={**ADMIN, "Referer": page}).status_code, 403)
        # reading what everyone may read is still fine (the launcher's list)
        self.assertEqual(self.get("/api/sites", headers={"Referer": f"http://testserver{P}/"}).status_code, 200)

    def test_the_apps_own_page_still_works(self):
        for page in (f"http://testserver{INGRESS}/", f"http://testserver{INGRESS}/?x=1", "http://testserver/", None):
            r = self.put_settings(**({"Referer": page} if page else {}), **{"Sec-Fetch-Dest": "empty"})
            self.assertEqual(r.status_code, 200, page)
        self.assertEqual(self.get("/api/admin-storage-download-db",
                                  headers={"Referer": f"http://testserver{INGRESS}/",
                                           "Sec-Fetch-Dest": "document"}).status_code, 200)

    def test_forms_and_frames_cant_change_anything(self):
        for dest in ("document", "iframe", "image"):
            self.assertEqual(self.put_settings(**{"Sec-Fetch-Dest": dest}).status_code, 403, dest)


class Uploads(ProxyCase):
    def test_upload_without_a_length_reaches_the_site_with_one(self):
        self.add_site()

        def gen():
            for _ in range(3):
                yield b"z" * 1000
        e = self.c.post("/s/fake/echo", content=gen(), headers=ADMIN).json()
        self.assertEqual((e["size"], e["headers"].get("content-length")), (3000, "3000"))
        self.assertNotIn("transfer-encoding", e["headers"])

    def test_bigger_ones_stream_on(self):
        self.add_site()
        settings.update({"collect_upload_mb": 1, "max_upload_mb": 4}, None)

        def gen():
            for _ in range(20):
                yield b"y" * 65536
        e = self.c.post("/s/fake/echo", content=gen(), headers=ADMIN).json()
        self.assertEqual(e["size"], 20 * 65536)
        self.assertEqual(e["headers"].get("transfer-encoding"), "chunked")
        settings.update({"collect_upload_mb": 0}, None)                 # 0: always stream
        e = self.c.post("/s/fake/echo", content=iter([b"a", b"b"]), headers=ADMIN).json()
        self.assertEqual((e["body"], e["headers"].get("transfer-encoding")), ("ab", "chunked"))


class Answers(ProxyCase):
    def test_secure_from_home_assistants_forwarded_proto(self):
        self.add_site()
        for proto, secure in (("https", True), ("http", False)):
            cookie = self.get("/s/fake/", headers={"X-Forwarded-Proto": proto,
                                                   "X-Forwarded-Host": "ha.example.com"}).headers["set-cookie"]
            self.assertEqual("Secure" in cookie, secure, proto)

    def test_live_events_and_partial_pages_not_rewritten(self):
        self.add_site(rules=[{"where": "body", "types": "", "match": "/api/", "replace": "$prefix/api/"}])
        r = self.get("/s/fake/events")
        self.assertEqual(r.text, "data: /api/0\n\ndata: /api/1\n\n")         # streamed, never buffered
        r = self.get("/s/fake/partial")
        self.assertEqual((r.status_code, r.text), (206, "<html><body><a href='/x'>x</a></body></html>"))

    def test_rule_target_without_a_length_streams(self):
        self.add_site(rules=[{"where": "body", "types": "application/json", "match": "yes", "replace": "YES"}])
        from app import proxy
        self.assertFalse(proxy.should_rewrite(None, ["r"], "application/json", None, None, "GET", 200))
        self.assertTrue(proxy.should_rewrite(None, ["r"], "application/json", None, "20", "GET", 200))
        self.assertTrue(proxy.should_rewrite("html", [], "text/html", None, None, "GET", 200))
        self.assertEqual(self.get("/s/fake/api/data").json(), {"ok": "YES"})     # JSONResponse says its length

    def test_meta_policy_cleaned_or_removed(self):
        self.add_site()
        t = self.get("/s/fake/meta-csp", headers={"Referer": f"http://ha.local{P}/"}).text
        self.assertIn('content="default-src &#x27;self&#x27;"', t)
        self.add_site(name="Bare", slug="bare", strip_csp=True)
        t = self.get("/s/bare/meta-csp").text
        self.assertNotIn("Content-Security-Policy", t)

    def test_path_bytes_outside_ascii_reach_the_site_as_sent(self):
        from app import proxy
        self.assertEqual(proxy.ascii_url("/caf\xc3\xa9 x"), "/caf%C3%A9%20x")
        self.assertEqual(proxy.ascii_url("/plain?a=1"), "/plain?a=1")


class Limits(ProxyCase):
    def test_too_many_at_once_for_one_person(self):
        from app import proxy
        s = self.add_site(access="everyone")
        proxy.IN_FLIGHT.open[("u-bob", s["id"])] = proxy.MAX_IN_FLIGHT
        try:
            self.assertEqual(self.get("/s/fake/echo", BOB).status_code, 429)
            self.assertEqual(self.get("/s/fake/echo", ADMIN).status_code, 200)       # someone else isn't held up
        finally:
            proxy.IN_FLIGHT.open.pop(("u-bob", s["id"]), None)
        self.assertEqual(self.get("/s/fake/echo", BOB).status_code, 200)
        self.assertEqual(proxy.IN_FLIGHT.open, {})                       # streams included, every slot given back
        self.get("/s/fake/big", BOB)
        self.get("/s/fake/nothing-here", BOB)
        self.assertEqual(proxy.IN_FLIGHT.open, {})

    def test_the_proxys_own_client_refuses_loopback_and_internet_addresses(self):
        import asyncio
        import httpx
        from app import netguard, proxy, sites
        site = sites.Site(id=999, slug="x", name="X", icon="", scheme="http", host="127.0.0.1", port=self.port,
                          origin=f"http://127.0.0.1:{self.port}", start_path="/", access="admins", people=frozenset(),
                          spa_mode=False, show_bar=True, keep_host=False, strip_csp=False, pass_user=False,
                          allow_public=False, tls_pem=None, timeout_s=5, access_log=False, updated_at="")

        async def fetch(url, s):
            c = await proxy.client(s)
            try:
                return (await c.get(url)).status_code
            finally:
                await c.aclose()
                proxy._clients.clear()
        netguard.ALLOW_LOOPBACK = False
        try:
            with self.assertRaises(httpx.ConnectError) as e:
                asyncio.run(fetch(f"http://127.0.0.1:{self.port}/echo", site))
            self.assertIn("can't be reached through the proxy", str(e.exception))
            with self.assertRaises(httpx.ConnectError) as e:
                asyncio.run(fetch("http://93.184.216.34/", site))
            self.assertIn("internet address", str(e.exception))
        finally:
            netguard.ALLOW_LOOPBACK = True
        self.assertEqual(asyncio.run(fetch(f"http://127.0.0.1:{self.port}/echo", site)), 200)

    def test_transport_refused_if_the_check_cant_be_attached(self):
        from unittest import mock
        import httpx
        from app import netguard

        class Changed:
            def __init__(self, **kw):
                self._pool = object()          # a pool without the backend attribute
        with mock.patch.object(httpx, "AsyncHTTPTransport", Changed):
            with self.assertRaises(RuntimeError):
                netguard.guarded_transport(None, False)


class Clients(ProxyCase):
    def test_a_deleted_sites_client_is_closed(self):
        from app import proxy
        a = self.add_site()
        self.get("/s/fake/echo")
        self.add_site(name="Two", slug="two")
        self.c.delete(f"/api/admin/sites/{a['id']}", headers=ADMIN)
        self.get("/s/two/echo")                          # a new client: the deleted site's goes
        self.assertNotIn(a["id"], {k[1] for k in proxy._clients})


class AccessLogBatched(ProxyCase):
    def test_lines_written_together(self):
        from app import proxy
        self.add_site(access_log=True)
        self.get("/s/fake/echo")
        with db.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM access_log").fetchone()[0], 0)    # not one commit each
        self.assertEqual(proxy.flush_access_log(), 1)
        with db.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM access_log").fetchone()[0], 1)


class Shim(ProxyCase):
    def test_shim_first_in_every_page(self):
        import html as h
        import json as j
        self.add_site()
        t = self.get("/s/fake/").text
        self.assertIn('<head><script src="/api/hassio_ingress/TOKEN123/_hp/shim.js?v=', t)
        cfg = j.loads(h.unescape(t.split('data-hp="')[1].split('"')[0]))
        self.assertEqual(cfg, {"p": P, "i": INGRESS, "o": f"http://127.0.0.1:{self.port}", "s": "fake", "spa": False,
                               "v": None})
        self.assertNotIn("<base href=\"" + P + "/\"><base", t)

    def test_single_page_app_mode(self):
        self.add_site(spa_mode=True)
        t = self.get("/s/fake/gz?tab=2").text
        self.assertIn('&quot;spa&quot;:true,&quot;v&quot;:&quot;/gz?tab=2&quot;', t)
        self.assertIn(f'<base href="{P}/">', t)
        t = self.get("/s/fake/").text                    # the page's own <base> wins
        self.assertEqual(t.count("<base "), 1)

    def test_script_allowed_by_the_sites_policy(self):
        self.add_site()
        csps = self.get("/s/fake/").headers.get_list("content-security-policy")
        self.assertIn("default-src 'self'; upgrade-insecure-requests", csps)     # 'self' already allows it

    def test_shim_is_served(self):
        r = self.get("/_hp/shim.js")
        self.assertEqual(r.status_code, 200)
        self.assertIn("__householdProxy", r.text)
