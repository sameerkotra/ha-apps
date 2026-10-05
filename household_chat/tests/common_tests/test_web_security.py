# Shared file: edit common/tests/test_web_security.py and run tools/sync_common.py; don't edit this copy. sha256=7fd654f6c16bc2b356dea142b836e698581af618796cfa6e57dae1db0c3e563c
"""The shared security/caching headers (common/python/web_security.py), tested in every app that uses them."""
import unittest

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse, Response
from fastapi.testclient import TestClient

from app.common import web_security

CSP = "default-src 'self'; script-src 'self'"


def client(headers: web_security.SecurityHeaders) -> TestClient:
    app = FastAPI()

    @app.get("/")
    def index():
        return PlainTextResponse("<html>", media_type="text/html")

    @app.get("/page.html")
    def page():
        return PlainTextResponse("<html>", media_type="text/html")

    @app.get("/api/x")
    def api_x():
        return {"ok": True}

    @app.get("/api/cached")
    def api_cached():
        return Response("x", headers={"Cache-Control": "private, max-age=60"})

    @app.get("/api/files/1")
    def api_file():
        return Response("x", headers={"Content-Security-Policy": "sandbox"})

    @app.get("/api/pdf")
    def api_pdf():
        return Response(b"%PDF", media_type="application/pdf")

    @app.get("/static/app.js")
    def asset():
        return PlainTextResponse("1")

    headers.install(app)
    return TestClient(app)


class SecurityHeaders(unittest.TestCase):
    def test_csp_nosniff_referrer(self):
        c = client(web_security.SecurityHeaders(CSP, referrer="no-referrer"))
        r = c.get("/api/x")
        self.assertEqual(r.headers["content-security-policy"], CSP)
        self.assertEqual(r.headers["x-content-type-options"], "nosniff")
        self.assertEqual(r.headers["referrer-policy"], "no-referrer")
        # a route's own policy is kept
        self.assertEqual(c.get("/api/files/1").headers["content-security-policy"], "sandbox")

    def test_csp_callable_and_skip(self):
        c = client(web_security.SecurityHeaders(lambda request: CSP + "; x-" + request.url.path.strip("/").replace("/", "-"),
                                                csp_skip=web_security.is_pdf))
        self.assertEqual(c.get("/api/x").headers["content-security-policy"], CSP + "; x-api-x")
        self.assertNotIn("content-security-policy", c.get("/api/pdf").headers)
        self.assertNotIn("referrer-policy", c.get("/api/x").headers)

    def test_no_csp(self):
        r = client(web_security.SecurityHeaders(None, nosniff=False)).get("/api/x")
        self.assertNotIn("content-security-policy", r.headers)
        self.assertNotIn("x-content-type-options", r.headers)

    def test_pages_never_cached(self):
        c = client(web_security.SecurityHeaders(CSP, pragma=True))
        for path in ("/", "/page.html"):
            r = c.get(path)
            self.assertEqual(r.headers["cache-control"], "no-cache, no-store, must-revalidate")
            self.assertEqual(r.headers["pragma"], "no-cache")
        self.assertNotIn("cache-control", c.get("/api/x").headers)
        self.assertNotIn("cache-control", c.get("/static/app.js").headers)
        self.assertNotIn("pragma", client(web_security.SecurityHeaders(CSP)).get("/").headers)

    def test_api_no_store(self):
        c = client(web_security.SecurityHeaders(CSP, api_no_store="default"))
        self.assertEqual(c.get("/api/x").headers["cache-control"], "no-store")
        self.assertEqual(c.get("/api/cached").headers["cache-control"], "private, max-age=60")
        c = client(web_security.SecurityHeaders(CSP, api_no_store="set", api_skip=lambda p: p.startswith("/api/files/")))
        self.assertEqual(c.get("/api/cached").headers["cache-control"], "no-store")
        self.assertNotIn("cache-control", c.get("/api/files/1").headers)
        self.assertEqual(c.get("/").headers["cache-control"], "no-cache, no-store, must-revalidate")
        with self.assertRaises(ValueError):
            web_security.SecurityHeaders(CSP, api_no_store="yes")

    def test_all_no_store(self):
        c = client(web_security.SecurityHeaders(CSP, all_no_store=True))
        for path in ("/", "/api/x", "/api/cached", "/static/app.js"):
            self.assertEqual(c.get(path).headers["cache-control"], "no-store", path)

    def test_other_no_store(self):
        c = client(web_security.SecurityHeaders(CSP, pages_no_cache=False, other_no_store=True))
        self.assertEqual(c.get("/").headers["cache-control"], "no-store")
        self.assertEqual(c.get("/static/app.js").headers["cache-control"], "no-store")
        self.assertNotIn("cache-control", c.get("/api/x").headers)

    def test_apply_returns_the_response(self):
        h = web_security.SecurityHeaders(CSP)

        class Req:
            class url:
                path = "/x"
        resp = Response("x")
        self.assertIs(h.apply(Req, resp), resp)
        self.assertEqual(resp.headers["content-security-policy"], CSP)


class CrossSite(unittest.TestCase):
    """refuse_cross_site: state-changing requests a browser labels as coming from another site are refused."""

    def app(self, **kw):
        from fastapi import Request
        app = FastAPI()

        @app.middleware("http")
        async def guard(request: Request, call_next):
            blocked = web_security.refuse_cross_site(request, **kw)
            if blocked is not None:
                return blocked
            return await call_next(request)

        for method in ("get", "post", "put", "patch", "delete", "options"):
            getattr(app, method)("/api/x")(lambda: {"ok": True})
        return TestClient(app)

    def test_unsafe_methods_from_another_site_are_refused(self):
        c = self.app()
        for method in ("post", "put", "patch", "delete"):
            for site in ("cross-site", "same-site", "Cross-Site"):
                with self.subTest(method=method, site=site):
                    r = c.request(method.upper(), "/api/x", headers={"Sec-Fetch-Site": site})
                    self.assertEqual(r.status_code, 403)
                    self.assertEqual(r.json(), {"detail": "Forbidden: cross-site request"})

    def test_same_origin_none_and_no_header_are_allowed(self):
        c = self.app()
        for method in ("post", "put", "patch", "delete"):
            for headers in ({"Sec-Fetch-Site": "same-origin"}, {"Sec-Fetch-Site": "none"}, {}):
                with self.subTest(method=method, headers=headers):
                    self.assertEqual(c.request(method.upper(), "/api/x", headers=headers).status_code, 200)

    def test_safe_methods_are_never_refused(self):
        c = self.app()
        for method in ("GET", "HEAD", "OPTIONS"):
            with self.subTest(method=method):
                self.assertNotEqual(c.request(method, "/api/x", headers={"Sec-Fetch-Site": "cross-site"}).status_code, 403)

    def test_the_apps_own_response(self):
        c = self.app(response=lambda: PlainTextResponse("Forbidden: cross-site request", status_code=403))
        r = c.post("/api/x", headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual((r.status_code, r.text), (403, "Forbidden: cross-site request"))

    def test_websocket_origin(self):
        class WS:
            def __init__(self, headers):
                self.headers = headers
        self.assertFalse(web_security.cross_origin_websocket(WS({"host": "ha.local:8123"})))
        self.assertFalse(web_security.cross_origin_websocket(
            WS({"host": "ha.local:8123", "origin": "http://ha.local:8123"})))
        self.assertTrue(web_security.cross_origin_websocket(
            WS({"host": "ha.local:8123", "origin": "https://evil.example"})))


if __name__ == "__main__":
    unittest.main()
