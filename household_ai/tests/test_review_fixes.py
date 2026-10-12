"""Regressions for the October 2026 review: browser "simple" POSTs refused (415), at most MAX_READING bodies read at
once, only the sampling options passed on, the /v1 keep_alive reset made while the request still holds the queue,
a key's last use written at most once a minute, other registries' models not looked up in Ollama's library, and
the Host check (app/hosts.py) — on only when config.ALLOWED_HOSTS is set (the Docker host)."""
import _env  # noqa: F401  (must be first)

import asyncio
import unittest

import harness
from app import config, db, gateway, hosts, keys, models, ollama

GEN = "/api/generate"
V1 = "/v1/chat/completions"


def gen(**kw):
    return {"model": "qwen2.5:3b", "prompt": "hi", "stream": False, **kw}


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.h = harness.get()

    def setUp(self):
        self.h.reset()

    def tearDown(self):
        self.h.wait_idle()


class SimplePosts(Base):
    def test_a_browser_post_that_isnt_json_is_415(self):
        c = self.h.gw()
        body = b'{"model": "qwen2.5:3b", "prompt": "hi", "stream": false}'
        for kind in ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data; boundary=x", None):
            headers = {"Origin": "https://evil.example.com"}
            if kind:
                headers["Content-Type"] = kind
            r = c.post(GEN, content=body, headers=headers)
            self.assertEqual(r.status_code, 415, kind)
            self.assertIn("JSON", r.json()["error"])
        r = c.post(V1, content=b'{"model": "qwen2.5:3b", "messages": []}',
                   headers={"Origin": "https://evil.example.com", "Content-Type": "text/plain"})
        self.assertEqual(r.status_code, 415)
        self.assertEqual(self.h.requests(GEN), [])
        self.assertEqual(self.h.requests(V1), [])

    def test_json_and_curl_without_a_type_still_work(self):
        c = self.h.gw()
        body = b'{"model": "qwen2.5:3b", "prompt": "hi", "stream": false}'
        r = c.post(GEN, content=body, headers={"Origin": "http://localhost:3000",
                                               "Content-Type": "application/json; charset=utf-8"})
        self.assertEqual(r.status_code, 200, r.text)
        # `curl -d` sends a form type and no Origin: not a browser, still served.
        r = c.post(GEN, content=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(r.status_code, 200, r.text)


class ReadingLimit(Base):
    def test_too_many_bodies_at_once_is_503_and_the_count_comes_back(self):
        gateway.READING["n"] = gateway.MAX_READING
        try:
            r = self.h.gw().post(GEN, json=gen())
            self.assertEqual(r.status_code, 503)
            self.assertEqual(r.headers["retry-after"], "5")
            self.assertIn("too many requests", r.json()["error"])
            self.assertEqual(self.h.requests(GEN), [])
        finally:
            gateway.READING["n"] = 0
        c = self.h.gw()
        self.assertEqual(c.post(GEN, json=gen()).status_code, 200)
        self.assertEqual(c.post(GEN, content=b"{nope", headers={"content-type": "application/json"}).status_code,
                         400)
        self.assertEqual(c.post(GEN, json=gen(model="nope:1b")).status_code, 404)
        self.assertEqual(c.post("/api/show", json={"model": "qwen2.5:3b"}).status_code, 200)
        self.assertEqual(c.post(GEN, json=gen(stream=True)).status_code, 200)
        self.h.wait_idle()
        self.assertEqual(gateway.READING["n"], 0)


class Options(Base):
    def test_only_sampling_options_pass(self):
        r = self.h.gw().post(GEN, json=gen(options={
            "temperature": 0.3, "top_p": 0.9, "top_k": 20, "min_p": 0.05, "seed": 7, "stop": ["\n"],
            "repeat_penalty": 1.1, "presence_penalty": 0.1, "frequency_penalty": 0.2, "mirostat": 1,
            "mirostat_tau": 5.0, "mirostat_eta": 0.1, "typical_p": 1.0, "tfs_z": 1.0,
            "num_gpu": 99, "num_batch": 4096, "use_mlock": True, "num_keep": 100000, "main_gpu": 1, "numa": True,
            "use_mmap": False, "low_vram": True}))
        self.assertEqual(r.status_code, 200, r.text)
        opts = self.h.requests(GEN)[-1]["options"]
        for k in ("num_gpu", "num_batch", "use_mlock", "num_keep", "main_gpu", "numa", "use_mmap", "low_vram"):
            self.assertNotIn(k, opts)
        for k in ("temperature", "top_p", "top_k", "min_p", "seed", "stop", "repeat_penalty", "presence_penalty",
                  "frequency_penalty", "mirostat", "mirostat_tau", "mirostat_eta", "typical_p", "tfs_z",
                  "num_ctx", "num_predict", "num_thread"):
            self.assertIn(k, opts)


class KeepAliveInsideTheQueue(Base):
    def test_v1_timer_reset_happens_before_the_queue_is_freed(self):
        seen = []
        real = ollama.set_keep_alive

        async def spy(name, keep_alive, timeout=300):
            seen.append(gateway.QUEUE.running is not None)
            return await real(name, keep_alive, timeout=timeout)
        ollama.set_keep_alive = spy
        try:
            msg = {"model": "qwen2.5:3b", "messages": [{"role": "user", "content": "hi"}]}
            self.assertEqual(self.h.gw().post(V1, json=msg).status_code, 200)
            self.h.wait_idle()
            r = self.h.gw().post(V1, json={**msg, "stream": True})
            self.assertEqual(r.status_code, 200)
            self.h.wait_idle()
        finally:
            ollama.set_keep_alive = real
        self.assertEqual(seen, [True, True])


class KeyUse(Base):
    def last_used(self, kid):
        with db.get_conn() as conn:
            return conn.execute("SELECT last_used_at FROM model_keys WHERE id = ?", (kid,)).fetchone()[0]

    def test_last_used_is_written_at_most_once_a_minute(self):
        now = [1000.0]
        clock, keys.CLOCK = keys.CLOCK, lambda: now[0]
        try:
            kid, key = keys.create("x", None, "u")
            self.assertEqual(keys.check(key)["id"], kid)
            self.assertIsNotNone(self.last_used(kid))
            with db.get_conn() as conn:
                conn.execute("UPDATE model_keys SET last_used_at = 'marker' WHERE id = ?", (kid,))
            now[0] += 30
            self.assertEqual(keys.check(key)["id"], kid)
            self.assertEqual(self.last_used(kid), "marker")             # not written again within the minute
            now[0] += 31
            self.assertEqual(keys.check(key)["id"], kid)
            self.assertNotEqual(self.last_used(kid), "marker")
        finally:
            keys.CLOCK = clock

    def test_the_gateway_checks_the_key_off_the_event_loop(self):
        from app import settings
        settings.update({"require_key": True}, None)
        _, key = keys.create("x", None, "u")
        on_loop = []
        real = keys.check

        def spy(token):
            try:
                asyncio.get_running_loop()
                on_loop.append(True)
            except RuntimeError:
                on_loop.append(False)
            return real(token)
        keys.check = spy
        try:
            r = self.h.gw().post(GEN, json=gen(), headers={"Authorization": f"Bearer {key}"})
            self.assertEqual(r.status_code, 200, r.text)
        finally:
            keys.check = real
        self.assertEqual(on_loop, [False])


class OtherRegistries(unittest.TestCase):
    def test_only_ollamas_own_names_are_looked_up(self):
        self.assertIsNone(models.manifest_url("hf.co/bartowski/Llama-3.2-1B-Instruct-GGUF:Q4_K_M"))
        self.assertIsNone(models.manifest_url("ghcr.io/someone/model:1"))
        self.assertIsNone(models.manifest_url("localhost:5000/model:1"))
        self.assertEqual(models.manifest_url("registry.ollama.ai/library/qwen2.5:3b"),
                         f"{config.REGISTRY_URL}/v2/library/qwen2.5/manifests/3b")
        self.assertEqual(models.manifest_url("registry.ollama.ai/someone/model"),
                         f"{config.REGISTRY_URL}/v2/someone/model/manifests/latest")
        self.assertEqual(models.manifest_url("someone/model:1"), f"{config.REGISTRY_URL}/v2/someone/model/manifests/1")

    def test_a_404_from_ollamas_library_doesnt_block_another_registrys_model(self):
        import httpx
        calls = []

        class NotFound:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, url, **kw):
                calls.append(url)
                return httpx.Response(404)
        real, models.httpx.AsyncClient = models.httpx.AsyncClient, NotFound
        try:
            self.assertIsNone(asyncio.run(models.download_size("hf.co/org/repo:Q4_K_M")))
            self.assertEqual(calls, [])
            with self.assertRaises(ValueError):
                asyncio.run(models.download_size("nosuchmodel:1b"))
            self.assertEqual(len(calls), 1)
        finally:
            models.httpx.AsyncClient = real


class HostCheck(Base):
    def test_rebinding_names_refused_only_when_allowed_hosts_is_set(self):
        c = self.h.gw()
        missing = object()
        saved = getattr(config, "ALLOWED_HOSTS", missing)
        try:
            config.ALLOWED_HOSTS = frozenset({"nas.example.com"})
            for host in ("evil.example.com", "evil.example.com:11434", "127.0.0.1.nip.io"):
                r = c.post(GEN, json=gen(), headers={"Host": host})
                self.assertEqual(r.status_code, 403, host)
                self.assertIn("ALLOWED_HOSTS", r.json()["error"])
                self.assertEqual(c.get("/api/version", headers={"Host": host}).status_code, 403)
                self.assertEqual(c.get("/api/ps", headers={"Host": host}).status_code, 403)
            r = c.post(V1, json={"model": "qwen2.5:3b", "messages": []}, headers={"Host": "evil.example.com"})
            self.assertEqual(r.json()["error"]["type"], "permission_error")
            self.assertEqual(self.h.requests(GEN), [])
            for host in ("192.168.1.10:11434", "[::1]:11434", "localhost:11434", "nas", "nas.local:11434",
                         "nas.example.com:11434", "NAS.Example.COM."):
                self.assertEqual(c.post(GEN, json=gen(), headers={"Host": host}).status_code, 200, host)
            config.ALLOWED_HOSTS = None                       # the Home Assistant app: not checked
            self.assertEqual(c.post(GEN, json=gen(), headers={"Host": "evil.example.com"}).status_code, 200)
        finally:
            if saved is missing:
                config.__dict__.pop("ALLOWED_HOSTS", None)
            else:
                config.ALLOWED_HOSTS = saved


class Hosts(unittest.TestCase):
    def test_allowed(self):
        for h in ("127.0.0.1", "127.0.0.1:8080", "[::1]:8080", "::1", "192.168.1.10:8080", "8.8.8.8", "localhost",
                  "LOCALHOST:8080", "a.localhost", "nas", "nas:8080", "nas.local", "nas.lan", "nas.home.arpa",
                  "host.docker.internal:11434", None, ""):
            self.assertTrue(hosts.allowed(h), h)
        for h in ("evil.example.com", "evil.example.com:8080", "localhost.evil.com", "192.168.1.10.nip.io",
                  "nas.local.evil.com"):
            self.assertFalse(hosts.allowed(h), h)
        extra = hosts.parse_list("nas.example.com, .home.example.org  AI.Example.net:8080")
        self.assertEqual(extra, {"nas.example.com", ".home.example.org", "ai.example.net"})
        for h in ("nas.example.com:8080", "x.home.example.org", "home.example.org", "ai.example.net"):
            self.assertTrue(hosts.allowed(h, extra), h)
        self.assertFalse(hosts.allowed("evil.example.com", extra))
        self.assertTrue(hosts.allowed("evil.example.com", {"*"}))

    def test_origin_matches(self):
        self.assertTrue(hosts.origin_matches(None, "nas:8080"))
        self.assertTrue(hosts.origin_matches("http://nas:8080", "nas:8080"))
        self.assertTrue(hosts.origin_matches("http://NAS:8080", "nas:8080"))
        self.assertTrue(hosts.origin_matches("http://nas", "nas"))
        self.assertTrue(hosts.origin_matches("http://[::1]:8080", "[::1]:8080"))
        self.assertTrue(hosts.origin_matches("https://ai.example.com", "ai.example.com"))
        for origin, host in (("http://evil.example.com", "nas:8080"), ("http://nas:9090", "nas:8080"),
                             ("null", "nas:8080"), ("", "nas:8080"), ("file://", "nas:8080"),
                             ("http://nas:8080", "evil:8080"), ("http://nas:bad", "nas:8080")):
            self.assertFalse(hosts.origin_matches(origin, host), (origin, host))


if __name__ == "__main__":
    unittest.main()
