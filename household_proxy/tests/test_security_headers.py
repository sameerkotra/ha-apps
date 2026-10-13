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
    @classmethod
    def setUpClass(cls):
        from app import db
        db.init_db()                         # these clients don't run the lifespan

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

    def test_proxied_responses_keep_the_sites_own_headers(self):
        """/s/… answers carry the site's headers (cleaned), not the app's page policy (spec §7)."""
        c = ingress_client(main.app)
        r = c.get("/s/nope/", headers={"X-Remote-User-Id": "u1"})
        self.assertNotEqual(r.headers.get("content-security-policy"), main.CSP)
        self.assertIn("frame-ancestors 'self'", r.headers["content-security-policy"])

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


class CachingAndLimits(unittest.TestCase):
    setUpClass = SecurityHeaders.setUpClass

    def test_versioned_files_kept_for_good(self):
        from app import config
        from common_tests.ingress import identity_headers
        c = ingress_client(main.app)
        for path in (f"/app.js?v={config.APP_VERSION}", f"/common/ui.js?v={config.APP_VERSION}",
                     f"/_hp/shim.js?v={config.APP_VERSION}"):
            self.assertEqual(c.get(path).headers["cache-control"], "public, max-age=31536000, immutable", path)
        self.assertNotIn("immutable", c.get("/app.js").headers.get("cache-control", ""))
        self.assertEqual(c.get("/?v=1").headers["cache-control"], "no-cache, no-store, must-revalidate")
        self.assertEqual(c.get("/api/me", headers=identity_headers("u1", "u", "U")).headers["cache-control"],
                         "no-store")

    def test_every_asset_in_the_page_is_versioned(self):
        import re
        with open(os.path.join(STATIC, "index.html"), encoding="utf-8") as f:
            page = f.read()
        refs = re.findall(r'(?:src|href)="([^"#]+\.(?:js|css))[^"]*"', page)
        self.assertTrue(refs)
        for ref in re.findall(r'(?:src|href)="([^"]+\.(?:js|css)[^"]*)"', page):
            self.assertIn("?v=", ref)

    def test_api_bodies_limited_chunked_too(self):
        from common_tests.ingress import identity_headers
        c = ingress_client(main.app)
        admin = identity_headers("u-admin", "adminy", "Ann")

        def gen():
            for _ in range(20):
                yield b" " * 65536
        r = c.put("/api/admin/settings", content=gen(), headers={**admin, "Content-Type": "application/json"})
        self.assertEqual(r.status_code, 413)
        r = c.put("/api/admin/settings", content=b" " * (2 << 20), headers={**admin, "Content-Type": "application/json"})
        self.assertEqual(r.status_code, 413)
        r = c.post("/api/admin-storage-import-db", content=gen(), headers=admin)
        self.assertEqual(r.status_code, 411)                 # a restore says its size


if __name__ == "__main__":
    unittest.main()
