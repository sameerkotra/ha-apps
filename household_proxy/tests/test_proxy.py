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
        self.assertEqual(r.status_code, 403)
        self.assertNotIn("127.0.0.1", r.text)

    def test_everyone_and_chosen(self):
        self.add_site(access="everyone")
        self.assertEqual(self.get("/s/fake/echo", BOB).status_code, 200)
        self.add_site(name="Two", slug="two", access="chosen", people=["u-bob"])
        self.assertEqual(self.get("/s/two/echo", BOB).status_code, 200)
        self.assertEqual(self.get("/s/two/echo", CAROL).status_code, 403)

    def test_launcher_lists_only_allowed_sites(self):
        self.add_site(access="everyone")
        self.add_site(name="Secret", slug="secret")
        names = [s["slug"] for s in self.get("/api/sites", BOB).json()["sites"]]
        self.assertEqual(names, ["fake"])
        self.assertEqual(len(self.get("/api/sites").json()["sites"]), 2)

    def test_turned_off_person_opens_nothing(self):
        self.add_site(access="everyone")
        self.c.put("/api/admin/people/u-bob", json={"enabled": False}, headers=ADMIN)
        self.assertEqual(self.get("/s/fake/echo", BOB).status_code, 403)
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
        e = self.echo(**{"Cookie": "ingress_session=HA-SECRET; sid=abc", "X-Forwarded-For": "1.2.3.4",
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


class Shim(ProxyCase):
    def test_shim_first_in_every_page(self):
        import html as h
        import json as j
        self.add_site()
        t = self.get("/s/fake/").text
        self.assertIn('<head><script src="/api/hassio_ingress/TOKEN123/_hp/shim.js?v=', t)
        cfg = j.loads(h.unescape(t.split('data-hp="')[1].split('"')[0]))
        self.assertEqual(cfg, {"p": P, "i": INGRESS, "o": f"http://127.0.0.1:{self.port}", "spa": False, "v": None})
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
