"""The ingress gate, who is an administrator, and the App settings routes. Needs the full requirements."""
import importlib.util
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HAVE_DEPS = all(importlib.util.find_spec(m) for m in ("sqlalchemy", "httpx", "fastapi"))
INGRESS = ("172.30.32.2", 50000)
PAT = {"X-Remote-User-Id": "abc123", "X-Remote-User-Name": "Pat", "X-Remote-User-Display-Name": "Pat Example"}
SAM = {"X-Remote-User-Id": "def456", "X-Remote-User-Name": "sam"}


@unittest.skipUnless(HAVE_DEPS, "needs the app's requirements")
class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        os.environ["DATABASE_PATH"] = os.path.join(cls.dir, "t.db")
        os.environ["IMAGE_PATH"] = os.path.join(cls.dir, "img")
        os.environ["ADMIN_USERS"] = " pat ,'other'"
        from fastapi.testclient import TestClient
        from app import auth, main
        cls.auth = auth
        auth.ADMIN_USERS[:] = auth.load_admin_users()
        auth._ADMIN_FOLDED.clear()
        auth._ADMIN_FOLDED.update(n.casefold() for n in auth.ADMIN_USERS)
        cls.app = main.app
        cls.TestClient = TestClient

    def client(self, host=INGRESS):
        return self.TestClient(self.app, client=host)

    def setUp(self):
        self.auth.invalidate_user_cache()
        auth = self.auth

        def sync(user_id, username, display_name):   # the users row is the database's business, not tested here
            return auth.CurrentUser(id=user_id, username=username, display_name=display_name or username or user_id,
                                    is_admin=auth.is_admin_identity(user_id, username))
        self.patch = mock.patch.object(self.auth, "_sync_user", side_effect=sync)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    def test_admin_users_parsing(self):
        self.assertEqual(self.auth.ADMIN_USERS, ["pat", "other"])
        self.assertTrue(self.auth.is_admin_identity("x", "PAT"))
        self.assertTrue(self.auth.is_admin_identity("Other", None))
        self.assertFalse(self.auth.is_admin_identity("abc", "Pat Example"))   # display names never match

    def test_only_ingress_gets_in(self):
        r = self.client(("10.0.0.5", 1)).get("/api/v1/me", headers=PAT)
        self.assertEqual(r.status_code, 403)
        r = self.client(("10.0.0.5", 1)).get("/list.html")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.client().get("/health").json(), {"status": "ok"})

    def test_api_needs_a_user(self):
        self.assertEqual(self.client().get("/api/v1/me").status_code, 401)

    def test_me_and_whoami(self):
        me = self.client().get("/api/v1/me", headers=PAT).json()
        self.assertTrue(me["is_admin"])
        self.assertFalse(me["no_admin_yet"])
        w = self.client().get("/api/v1/whoami", headers=SAM).json()
        self.assertEqual((w["userId"], w["username"], w["isAdmin"], w["adminEntries"]), ("def456", "sam", False, 2))
        self.assertNotIn("pat", str(w).lower())

    def test_settings_are_admin_only(self):
        self.assertEqual(self.client().get("/api/admin/settings", headers=SAM).status_code, 403)
        self.assertEqual(self.client().put("/api/admin/settings", headers=SAM, json={"unit_system": "metric"}).status_code, 403)

    def test_settings_round_trip(self):
        c = self.client()
        r = c.put("/api/admin/settings", headers=PAT, json={"model_url": "http://192.0.2.1:11434", "model_api_key": "k"})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["values"]["model_url"], "http://192.0.2.1:11434")
        self.assertEqual(body["values"]["model_api_key"], "")
        self.assertTrue(body["secretsSet"]["model_api_key"])
        r = c.put("/api/admin/settings", headers=PAT, json={"model_timeout_seconds": 1})
        self.assertEqual(r.status_code, 422)
        self.assertIn("Model timeout", r.json()["detail"])

    def test_pages_carry_the_version(self):
        html = self.client().get("/admin.html").text
        from app.config import APP_VERSION
        self.assertIn(f"static/app.js?v={APP_VERSION}", html)
        self.assertIn(f"static/backnav.js?v={APP_VERSION}", html)
        self.assertIn(f'window.__APP_VERSION__="{APP_VERSION}"', html)


if __name__ == "__main__":
    unittest.main()
