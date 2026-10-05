"""Every app refuses a state-changing request a browser labels as coming from another site
(common/python/web_security.refuse_cross_site in the app's guard middleware), and lets the same request
through from its own pages. Each app is imported in its own process with its test environment."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Run inside an app folder: import its test environment and app, then send probes from the ingress proxy.
PROBE = r"""
import json, os, sys, tempfile
sys.path.insert(0, os.getcwd())
sys.path.insert(0, os.path.join(os.getcwd(), "tests"))
if os.path.exists(os.path.join("tests", "_env.py")):
    import _env  # noqa: F401
else:
    d = tempfile.mkdtemp()
    os.environ.setdefault("DATABASE_PATH", os.path.join(d, "t.db"))
    os.environ.setdefault("IMAGE_PATH", os.path.join(d, "img"))
import logging
logging.disable(logging.CRITICAL)
from starlette.testclient import TestClient
from app.main import app
user = {"X-Remote-User-Id": "probe-user", "X-Remote-User-Name": "probe"}
out = {}
with TestClient(app, client=("172.30.32.2", 50000)) as c:          # runs the app's start-up (its database)
    for site in ("cross-site", "same-site", "same-origin", "none", None):
        h = dict(user)
        if site:
            h["Sec-Fetch-Site"] = site
        r = c.post("/api/__cross_site_probe__", headers=h, json={})
        out[str(site)] = [r.status_code, r.text[:200]]
print("RESULT " + json.dumps(out))
"""

APPS = ["calorie_tracker", "family_tree", "household_arcade", "household_chat", "household_docs",
        "household_todo", "household_vault", "receipt_price_intelligence", "splitpot"]


class CrossSiteTests(unittest.TestCase):
    def test_every_app_refuses_cross_site_posts(self):
        for app in APPS:
            with self.subTest(app=app):
                with tempfile.TemporaryDirectory() as tmp:
                    env = {**os.environ, "HOME": tmp}
                    p = subprocess.run([sys.executable, "-c", PROBE], cwd=os.path.join(ROOT, app), env=env,
                                       capture_output=True, text=True, timeout=180)
                line = [l for l in p.stdout.splitlines() if l.startswith("RESULT ")]
                self.assertTrue(line, p.stderr[-2000:])
                out = json.loads(line[-1][len("RESULT "):])
                for site in ("cross-site", "same-site"):
                    self.assertEqual(out[site][0], 403, out)
                    self.assertIn("cross-site request", out[site][1])
                for site in ("same-origin", "none", "None"):
                    self.assertNotEqual(out[site][0], 403, out)

    def test_every_guard_uses_the_shared_check(self):
        files = {app: os.path.join(ROOT, app, "app", "main.py") for app in APPS}
        files["receipt_price_intelligence"] = os.path.join(ROOT, "receipt_price_intelligence", "app", "auth.py")
        files["finance"] = os.path.join(ROOT, "finance", "app", "security.py")
        for app, path in files.items():
            with self.subTest(app=app):
                with open(path, encoding="utf-8") as f:
                    self.assertIn("web_security.refuse_cross_site(request", f.read())


if __name__ == "__main__":
    unittest.main()
