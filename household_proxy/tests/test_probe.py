"""The Test, trusted certificates, the site's own icon and per-site rules (spec/SPEC.md §3.5, §4.5, §5.3); and the
bounded reading of compressed answers."""
import _env  # noqa: F401  (must be first)

import tempfile
import unittest

import fakesite
from app import sites
from helpers import ADMIN, BOB, INGRESS, ProxyCase

P = f"{INGRESS}/s/fake"


class TheTest(ProxyCase):
    def check_form(self, **kw):
        body = {"name": "Fake", "target": f"http://127.0.0.1:{self.port}"}
        body.update(kw)
        r = self.c.post("/api/admin/sites/test", json=body, headers=ADMIN)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_plain_page_should_work(self):
        r = self.check_form()
        self.assertTrue(r["reachable"])
        self.assertEqual((r["status"], r["verdict"], r["verdictText"]), (200, "works", "Should work"))
        self.assertEqual(r["frameHeaders"], ["x-frame-options", "content-security-policy"])
        self.assertGreater(r["rewritten"], 5)
        self.assertEqual(r["scripts"]["checked"], 1)
        self.assertTrue(r["favicon"].endswith("/favicon.ico"))

    def test_router_suggests_single_page_app_mode(self):
        self.assertEqual(self.check_form(start_path="/stacks/1")["verdict"], "try_spa")
        self.assertEqual(self.check_form(start_path="/stacks/1", spa_mode=True)["verdict"], "works")

    def test_fixed_base_path_suggests_a_rule(self):
        r = self.check_form(start_path="/basename")
        self.assertEqual(r["verdict"], "needs_rule")
        rule = r["suggestedRule"]
        self.add_site(rules=[rule])
        js = self.get("/s/fake/static/base.js").text
        self.assertEqual(js, f'createBrowserRouter(routes, {{basename:"{P}/"}}); fetch("/api/x")')
        page = self.get("/s/fake/basename").text
        self.assertNotIn("integrity", page)                      # scripts are rewritten now: hashes dropped

    def test_redirect_to_another_address_suggests_a_tunnel(self):
        r = self.check_form(start_path="/r-away")
        self.assertEqual((r["verdict"], r["redirects"]), ("tunnel", ["https://example.com/away"]))

    def test_redirects_on_the_site_are_followed(self):
        r = self.check_form(start_path="/r")
        self.assertEqual(r["redirects"], [f"http://127.0.0.1:{self.port}/login"])

    def test_unreachable(self):
        r = self.check_form(target="http://127.0.0.1:9")
        self.assertFalse(r["reachable"])
        self.assertEqual((r["verdict"], r["problemKind"]), ("unreachable", "refused"))

    def test_bad_address_and_admins_only(self):
        self.assertEqual(self.c.post("/api/admin/sites/test", json={"name": "x", "target": "ftp://x"},
                                     headers=ADMIN).status_code, 422)
        self.assertEqual(self.c.post("/api/admin/sites/test", json={"name": "x", "target": "http://x"},
                                     headers=BOB).status_code, 403)

    def test_saved_site_uses_its_saved_header_values(self):
        s = self.add_site(start_path="/echo", headers=[{"name": "X-Key", "value": "k1"}])
        self.assertTrue(self.c.post(f"/api/admin/sites/{s['id']}/test", headers=ADMIN).json()["reachable"])
        r = self.c.post(f"/api/admin/sites/test?id={s['id']}", headers=ADMIN, json={
            "name": "Fake", "target": f"http://127.0.0.1:{self.port}", "start_path": "/echo",
            "headers": [{"name": "X-Key", "value": None}]})
        self.assertTrue(r.json()["reachable"])
        self.assertNotIn("k1", r.text)


class Certificates(ProxyCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tmp = tempfile.mkdtemp()
        cls.tls_port = fakesite.start_tls(*fakesite.self_signed(cls.tmp, "nas.lan"))
        cls.other_port = fakesite.start_tls(*fakesite.self_signed(cls.tmp, "other.lan"))

    def test_trust_this_certificate(self):
        s = self.add_site(target=f"https://127.0.0.1:{self.tls_port}")
        self.assertEqual(self.get("/s/fake/echo").status_code, 502)               # not trusted yet
        r = self.c.post(f"/api/admin/sites/{s['id']}/test", headers=ADMIN).json()
        self.assertEqual(r["problemKind"], "certificate")
        cert = r["certificate"]
        self.assertIn("nas.lan", cert["subject"])
        self.assertNotIn("pem", cert)
        bad = self.c.post(f"/api/admin/sites/{s['id']}/certificate", json={"fingerprint": "AA:" * 31 + "AA"},
                          headers=ADMIN)
        self.assertEqual(bad.status_code, 409)
        ok = self.c.post(f"/api/admin/sites/{s['id']}/certificate", json={"fingerprint": cert["fingerprint"]},
                         headers=ADMIN)
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(self.get("/s/fake/echo").json()["path"], "/echo")
        self.assertEqual(self.c.post(f"/api/admin/sites/{s['id']}/test", headers=ADMIN).json()["verdict"], "works")
        # the trusted certificate is the only one accepted: another server's is refused
        body = {"name": "Fake", "slug": "fake", "target": f"https://127.0.0.1:{self.other_port}", "trusted_ok": True}
        self.c.put(f"/api/admin/sites/{s['id']}", json=body, headers=ADMIN)       # new address: certificate forgotten
        from app import sites
        sites.set_certificate(s["id"], open(f"{self.tmp}/nas.lan.crt").read(), cert["fingerprint"])
        self.assertEqual(self.get("/s/fake/echo").status_code, 502)

    def test_forget(self):
        s = self.add_site(target=f"https://127.0.0.1:{self.tls_port}")
        fp = self.c.post(f"/api/admin/sites/{s['id']}/test", headers=ADMIN).json()["certificate"]["fingerprint"]
        self.c.post(f"/api/admin/sites/{s['id']}/certificate", json={"fingerprint": fp}, headers=ADMIN)
        self.assertEqual(self.c.delete(f"/api/admin/sites/{s['id']}/certificate", headers=ADMIN).status_code, 200)
        self.assertEqual(self.get("/s/fake/echo").status_code, 502)


class Icons(ProxyCase):
    def test_site_icon(self):
        s = self.add_site(start_path="/basename", use_favicon=True, access="everyone")
        self.assertEqual(self.c.post(f"/api/admin/sites/{s['id']}/favicon", headers=ADMIN).status_code, 200)
        tile = self.get("/api/sites", BOB).json()["sites"][0]
        self.assertTrue(tile["favicon"].startswith("api/sites/fake/favicon?v="))
        r = self.get("/api/sites/fake/favicon", BOB)
        self.assertEqual((r.status_code, r.headers["content-type"]), (200, "image/png"))
        self.assertIn("sandbox", r.headers["content-security-policy"])
        self.assertEqual(r.headers["x-content-type-options"], "nosniff")

    def test_svg_never_used(self):
        s = self.add_site(start_path="/svgicon", use_favicon=True)
        self.assertEqual(self.c.post(f"/api/admin/sites/{s['id']}/favicon", headers=ADMIN).status_code, 422)

    def test_icon_only_for_people_who_may_open_the_site(self):
        s = self.add_site(use_favicon=True)
        self.c.post(f"/api/admin/sites/{s['id']}/favicon", headers=ADMIN)
        self.assertEqual(self.get("/api/sites/fake/favicon").status_code, 200)
        self.assertEqual(self.get("/api/sites/fake/favicon", BOB).status_code, 404)


class Rules(ProxyCase):
    def test_header_rules_and_types(self):
        self.add_site(rules=[{"where": "header", "types": "application/javascript", "match": "/old-base",
                              "replace": "$prefix/new"},
                             {"where": "body", "types": "text/css", "match": "body", "replace": "main"}])
        r = self.get("/s/fake/static/base.js")
        self.assertEqual(r.headers["x-base"], f"{P}/new")
        self.assertIn("main{background", self.get("/s/fake/static/app.css").text)
        self.assertNotIn("x-base", self.get("/s/fake/echo").headers)

    def test_rule_validation(self):
        for bad in ({"match": "(", "where": "body"}, {"match": "x", "where": "nowhere"},
                    {"match": "x", "types": "not a type"}):
            r = self.c.post("/api/admin/sites", headers=ADMIN, json={
                "name": "Fake", "target": f"http://127.0.0.1:{self.port}", "trusted_ok": True, "rules": [bad]})
            self.assertEqual(r.status_code, 422, bad)

    def test_rules_that_could_run_for_minutes_refused(self):
        for bad in (r"(a+)+$", r"(x*y?)*z", r"(?:\w+\s?)+:", r"(a|b+){2,}"):
            r = self.c.post("/api/admin/sites", headers=ADMIN, json={
                "name": "Fake", "target": f"http://127.0.0.1:{self.port}", "trusted_ok": True,
                "rules": [{"match": bad}]})
            self.assertEqual(r.status_code, 422, bad)
            self.assertIn("repeats something that repeats", r.text)
        for fine in (r"basename:(\s*)([\"'])/\2", r"/api/", r"(ab){2}", r"(a{2})+", r"(?>a+)+b", r"a++b"):
            self.assertFalse(sites.nested_repeat(fine), fine)

    def test_a_rule_cant_add_a_header_line(self):
        self.add_site(rules=[{"where": "header", "match": "/old-base", "replace": "x\r\nSet-Cookie: evil=1"}])
        r = self.get("/s/fake/static/base.js")
        self.assertNotIn("evil", r.headers.get("set-cookie", ""))


class Compressed(ProxyCase):
    def test_bomb_is_read_in_bounded_pieces(self):
        self.add_site()
        r = self.get("/s/fake/bomb")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.content), 20 * 1024 * 1024 + len("<html><body></body></html>"))
        self.assertNotIn("content-encoding", r.headers)


if __name__ == "__main__":
    unittest.main()
