"""DNS rebinding on a Docker host: the settings page answers only to this machine's names (config.ALLOWED_HOSTS,
app/hosts.py), and a write's Origin must name the host it was sent to. (The gateway's own Host check is in
test_review_fixes.HostCheck.)"""
import _env  # noqa: F401  (must be first)

import os
import unittest

import harness
from app import config, hosts

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class SettingsPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.h = harness.get()

    def setUp(self):
        self.h.reset()
        self.saved = config.ALLOWED_HOSTS
        config.ALLOWED_HOSTS = frozenset({"nas.example.com"})

    def tearDown(self):
        config.ALLOWED_HOSTS = self.saved

    def client(self, host):
        """The running app's client (its event loop), as if the browser had opened http://<host>/."""
        api = self.h.api

        class AtHost:
            def request(self, method, path, headers=None, **kw):
                return api.request(method, path, headers={"Host": host, **(headers or {})}, **kw)

            def get(self, path, **kw):
                return self.request("GET", path, **kw)

            def post(self, path, **kw):
                return self.request("POST", path, **kw)
        return AtHost()

    def test_a_rebound_name_can_neither_read_nor_write(self):
        evil = self.client("evil.example.com:8080")
        for path in ("/", "api/status", "api/keys", "api/backups", "api/health"):
            r = evil.get(path)
            self.assertEqual(r.status_code, 403, path)
        self.assertIn("ALLOWED_HOSTS", evil.get("api/status").json()["detail"])
        # Same-origin as far as the browser knows (it thinks it is still on evil.example.com).
        for method, path, body in (("POST", "api/keys", {"label": "stolen"}),
                                   ("PUT", "api/admin/settings", {"require_key": False}),
                                   ("POST", "api/models/pull", {"name": "qwen2.5:3b"}),
                                   ("DELETE", "api/downloaded/qwen2.5:3b", None)):
            r = evil.request(method, path, json=body, headers={"Origin": "http://evil.example.com:8080",
                                                               "Sec-Fetch-Site": "same-origin"})
            self.assertEqual(r.status_code, 403, path)
        from app import keys
        self.assertEqual(keys.rows(), [])
        self.assertEqual([e for e in self.h.log() if e.get("path") in ("/api/pull", "/api/delete")], [])

    def test_this_machines_names_work(self):
        for host in ("192.168.1.10:8080", "localhost:8080", "nas:8080", "nas.local:8080", "nas.example.com:8080",
                     "testserver"):
            c = self.client(host)
            self.assertEqual(c.get("api/status").status_code, 200, host)
            r = c.post("api/keys", json={"label": "k"}, headers={"Origin": f"http://{host}"})
            self.assertEqual(r.status_code, 200, (host, r.text))
            r = c.post("api/keys", json={"label": "k"})                  # no Origin (curl): allowed
            self.assertEqual(r.status_code, 200, (host, r.text))

    def test_a_write_whose_origin_names_another_host_is_refused(self):
        c = self.client("192.168.1.10:8080")
        for origin in ("http://evil.example.com", "http://192.168.1.10:9999", "null"):
            r = c.post("api/keys", json={"label": "k"}, headers={"Origin": origin, "Sec-Fetch-Site": "same-origin"})
            self.assertEqual(r.status_code, 403, origin)
        self.assertEqual(c.get("api/status", headers={"Origin": "http://evil.example.com"}).status_code, 200)

    def test_allowed_hosts_comes_from_the_environment_and_is_documented(self):
        self.assertIn(hosts.host_only(config.own_hostname()), self.saved)
        self.assertEqual(hosts.parse_list("nas.example.com, ai.example.com:8080"),
                         {"nas.example.com", "ai.example.com"})
        with open(os.path.join(HERE, "app", "config.py"), encoding="utf-8") as f:
            self.assertIn('os.environ.get("ALLOWED_HOSTS")', f.read())
        for name in ("README.md", "docker-compose.yml"):
            with open(os.path.join(HERE, name), encoding="utf-8") as f:
                self.assertIn("ALLOWED_HOSTS", f.read(), name)


if __name__ == "__main__":
    unittest.main()
