"""The page and its headers (SPEC §3.3): the strict CSP, no inline scripts or handlers, no innerHTML with user
data, only the ingress proxy gets in, oversized requests refused."""
import _env  # noqa: F401

import os
import re
import unittest

from base import KABIR, ApiBase
from common_tests.ingress import identity_headers
from starlette.testclient import TestClient

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "static")
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; connect-src 'self'; "
       "object-src 'none'; base-uri 'none'; frame-ancestors 'self'")


class Headers(ApiBase):
    def test_csp_on_the_page_and_api(self):
        r = self.c.get("/", headers=KABIR)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["content-security-policy"], CSP)
        self.assertEqual(r.headers["x-content-type-options"], "nosniff")
        self.assertIn("no-store", r.headers["cache-control"])
        r = self.get("/api/me")
        self.assertEqual(r.headers["content-security-policy"], CSP)
        self.assertEqual(r.headers["cache-control"], "no-store")

    def test_only_the_ingress_proxy_gets_in(self):
        from app.main import app
        outsider = TestClient(app, client=("10.0.0.5", 5000))
        self.assertEqual(outsider.get("/api/me", headers=KABIR).status_code, 403)
        self.assertEqual(self.c.get("/api/me").status_code, 401)                    # no user

    def test_big_requests_refused(self):
        r = self.c.post("/api/nodes/x/rename", content=b"{" + b" " * 300_000 + b"}",
                        headers={**KABIR, "Content-Type": "application/json"})
        self.assertEqual(r.status_code, 413)

    def test_validation_errors_dont_echo_input(self):
        r = self.post("/api/docs", {"kind": "note", "name": "x", "secretfield": "my secret text"})
        self.assertEqual(r.status_code, 422)
        self.assertNotIn("my secret text", r.text)


class PageRules(unittest.TestCase):
    def files(self):
        for name in sorted(os.listdir(STATIC)):
            if name.endswith((".js", ".html", ".css")):
                with open(os.path.join(STATIC, name), encoding="utf-8") as f:
                    yield name, f.read()

    def test_no_inline_script_handlers_or_html_parsing(self):
        for name, text in self.files():
            with self.subTest(name):
                if name.endswith(".html"):
                    self.assertNotRegex(text, r"<script(?![^>]*\bsrc=)[^>]*>")
                    self.assertNotRegex(text, r"\son[a-z]+\s*=")
                    self.assertNotRegex(text, r"\sstyle\s*=")
                if name.endswith(".js"):
                    for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
                        self.assertFalse(bad in text, f"{bad} in {name}")

    def test_every_script_is_on_the_page_in_order(self):
        with open(os.path.join(STATIC, "index.html"), encoding="utf-8") as f:
            index = f.read()
        own = re.findall(r'<script src="([a-z]+\.js)\?v=', index)
        self.assertEqual(own, ["textutil.js", "md.js", "app.js", "docs.js", "sheetcalc.js", "sheet.js", "files.js", "search.js", "organise.js",
                               "admin.js", "scanpdf.js", "activity.js", "bring.js", "ai.js", "connect.js", "tidy.js",
                               "start.js"])          # start.js last


if __name__ == "__main__":
    unittest.main()
