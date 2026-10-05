"""Content-Security-Policy and the other headers (app/common/web_security.py, policy in app/main.py), and the page
keeps to the policy: no inline scripts or on* handler attributes."""
import _env  # noqa: F401  (must be first)

import glob
import os
import re
import unittest

from app import main
from common_tests.ingress import ingress_client

STATIC = os.path.join(os.path.dirname(__file__), "..", "app", "static")


class SecurityHeaders(unittest.TestCase):
    def test_page_and_api_headers(self):
        c = ingress_client(main.app)
        r = c.get("/")
        self.assertEqual(r.headers["content-security-policy"], main.CSP)
        self.assertIn("script-src 'self';", main.CSP)
        self.assertEqual(r.headers["x-content-type-options"], "nosniff")
        self.assertEqual(r.headers["cache-control"], "no-cache, no-store, must-revalidate")
        self.assertEqual(r.headers["pragma"], "no-cache")
        r = c.get("/api/health")
        self.assertEqual(r.headers["content-security-policy"], main.CSP)
        self.assertEqual(r.headers["x-content-type-options"], "nosniff")

    def test_pages_have_no_inline_scripts_or_handlers(self):
        for path in glob.glob(os.path.join(STATIC, "*.html")):
            with open(path, encoding="utf-8") as f:
                html = f.read()
            with self.subTest(os.path.basename(path)):
                self.assertNotRegex(html, r"<script(?![^>]*\bsrc=)[^>]*>")
                self.assertNotRegex(html, r"\son[a-z]+\s*=")
                self.assertNotIn("javascript:", html)
        for path in glob.glob(os.path.join(STATIC, "*.js")):
            with open(path, encoding="utf-8") as f:
                js = f.read()
            with self.subTest(os.path.basename(path)):
                self.assertNotRegex(js, r"\bnew Function\(|[^.\w]eval\(")
                self.assertIsNone(re.search(r"""\son[a-z]+=\\?["']""", js))


if __name__ == "__main__":
    unittest.main()
