"""The settings page on a Docker host (13 Oct 2026 review, DK-S1/DK-S3): published on this machine only by default,
and with ADMIN_PASSWORD set it needs the password (HTTP Basic) for the page and its API; the compose file's
hardening; downloads only from Ollama's library and Hugging Face unless ALLOW_ANY_REGISTRY=1."""
import _env  # noqa: F401  (must be first)

import base64
import importlib
import os
import unittest
from unittest import mock

import harness
from app import auth, config, db

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def basic(password, user="admin"):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()}


class AdminPassword(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.h = harness.get()

    def setUp(self):
        self.h.reset()
        self.saved = config.ADMIN_PASSWORD
        auth.FAILURES.blocked.clear()
        auth.FAILURES.seen.clear()

    def tearDown(self):
        config.ADMIN_PASSWORD = self.saved
        auth.FAILURES.blocked.clear()
        auth.FAILURES.seen.clear()

    def test_without_a_password_the_page_is_open(self):
        config.ADMIN_PASSWORD = ""
        self.assertEqual(self.h.api.get("api/status").status_code, 200)

    def test_with_a_password_every_route_needs_it(self):
        config.ADMIN_PASSWORD = "correct horse"
        c = self.h.api
        for path in ("/", "app.js?v=1.0.5", "api/status", "api/keys", "api/admin/settings",
                     "api/admin-storage-download-db"):
            r = c.get(path)
            self.assertEqual(r.status_code, 401, path)
            self.assertTrue(r.headers["www-authenticate"].startswith("Basic "), path)
            self.assertIn("no-store", r.headers["cache-control"])
        r = c.post("api/keys", json={"label": "stolen"})
        self.assertEqual(r.status_code, 401)
        self.assertEqual(c.get("api/health").status_code, 200)          # the health check needs none
        self.assertEqual(c.get("api/status", headers=basic("wrong")).status_code, 401)
        self.assertEqual(c.get("api/status", headers=basic("correct horse")).status_code, 200)
        self.assertEqual(c.get("api/status", headers=basic("correct horse", user="anyone")).status_code, 200)
        self.assertEqual(c.post("api/keys", json={"label": "k"}, headers=basic("correct horse")).status_code, 200)
        self.assertEqual(c.get("api/status", headers={"Authorization": "Bearer correct horse"}).status_code, 401)
        self.assertEqual(c.get("api/status", headers={"Authorization": "Basic !!!"}).status_code, 401)

    def test_ten_wrong_passwords_block_the_address_for_a_minute(self):
        config.ADMIN_PASSWORD = "pw"
        c = self.h.api
        for _ in range(9):
            self.assertEqual(c.get("api/status", headers=basic("nope")).status_code, 401)
        self.assertEqual(c.get("api/status", headers=basic("nope")).status_code, 401)     # the tenth blocks
        r = c.get("api/status", headers=basic("pw"))
        self.assertEqual(r.status_code, 200)                    # the right password is still accepted
        r = c.get("api/status", headers=basic("nope"))
        self.assertEqual(r.status_code, 429)
        self.assertEqual(r.headers["retry-after"], "60")
        # Asking without a password (the browser's first try) isn't counted as a wrong one.
        auth.FAILURES.blocked.clear()
        for _ in range(20):
            self.assertEqual(c.get("api/status").status_code, 401)
        self.assertEqual(auth.FAILURES.blocked, {})

    def test_the_gateway_doesnt_need_it(self):
        config.ADMIN_PASSWORD = "pw"
        r = self.h.gw().post("/api/generate", json={"model": "qwen2.5:3b", "prompt": "hi", "stream": False})
        self.assertEqual(r.status_code, 200)
        self.h.wait_idle()
        with db.get_conn() as conn:               # leave no usage behind for the other modules' listings
            conn.execute("DELETE FROM model_usage")
            conn.execute("DELETE FROM model_callers")

    def test_parsing(self):
        self.assertEqual(auth.basic_password(basic("a:b")["Authorization"]), "a:b")
        self.assertEqual(auth.basic_password("basic " + base64.b64encode(b"u:").decode()), "")
        for bad in (None, "", "Basic", "Bearer x", "Basic " + base64.b64encode(b"nocolon").decode(),
                    "Basic " + base64.b64encode(b"\xff:\xfe").decode()):
            self.assertIsNone(auth.basic_password(bad), bad)


class FromTheEnvironment(unittest.TestCase):
    def reload(self, **env):
        with mock.patch.dict(os.environ, env):
            return importlib.reload(config)

    def tearDown(self):
        importlib.reload(config)

    def test_password_and_registries(self):
        c = self.reload(ADMIN_PASSWORD="s3cret", ALLOW_ANY_REGISTRY="0")
        self.assertEqual(c.ADMIN_PASSWORD, "s3cret")
        self.assertEqual(c.ALLOWED_REGISTRIES, frozenset({"registry.ollama.ai", "hf.co", "huggingface.co"}))
        self.assertIsNone(self.reload(ALLOW_ANY_REGISTRY="1").ALLOWED_REGISTRIES)
        self.assertIsNone(c.INSIDE_NETWORK)
        self.assertEqual(c.ADDR_PER_CALLER, 6)


class ComposeFile(unittest.TestCase):
    def test_page_on_this_machine_only_and_hardening(self):
        with open(os.path.join(HERE, "docker-compose.yml"), encoding="utf-8") as f:
            compose = f.read()
        self.assertIn('- "127.0.0.1:8080:8080"', compose)
        self.assertNotRegex(compose, r'(?m)^\s*- "8080:8080"')
        for line in ("ADMIN_PASSWORD:", "ALLOW_ANY_REGISTRY:", "- no-new-privileges:true", "stop_grace_period: 30s",
                     "# cap_drop: [ALL]", "# user:"):
            self.assertIn(line, compose)
        with open(os.path.join(HERE, "README.md"), encoding="utf-8") as f:
            readme = f.read()
        for phrase in ("ADMIN_PASSWORD", "127.0.0.1:8080", "ALLOW_ANY_REGISTRY", "cap_drop", "Hardening"):
            self.assertIn(phrase, readme)


if __name__ == "__main__":
    unittest.main()
