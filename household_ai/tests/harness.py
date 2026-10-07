"""One running app for the tests that need the model server and the gateway: main.app's lifespan (in a
TestClient), the fake model server as the child process, and the gateway on a free port. Shared by every test
module in the run (the app's state is module-global), started on first use and stopped at exit.

    h = harness.get()
    h.api.get("api/status", headers=harness.ADMIN)          # the ingress page
    h.gw("127.0.0.2").post("/api/generate", json={...})     # the gateway, as a caller from 127.0.0.2
"""
import _env  # noqa: F401  (must be first)

import atexit
import json
import os
import time

import httpx

from app import callers, db, gateway, main, ollama, server, settings
from common_tests.ingress import ingress_client

ADMIN = {"X-Remote-User-Id": "u-admin", "X-Remote-User-Name": "adminy", "X-Remote-User-Display-Name": "Admin"}
USER = {"X-Remote-User-Id": "u-user", "X-Remote-User-Name": "someone", "X-Remote-User-Display-Name": "Someone"}
PREFIX = "a1b2c3d4"
# Host names → loopback addresses the tests send from (the whole 127.0.0.0/8 works on Linux).
HOSTS = {callers.host_for(PREFIX, "household_assistant"): ["127.0.0.2"],
         callers.host_for(PREFIX, "household_docs"): ["127.0.0.3"],
         callers.host_for(PREFIX, "receipt_price_intelligence"): ["127.0.0.4"]}
LOG = os.environ["FAKE_OLLAMA_LOG"]
DEFAULT_MODELS = ["qwen2.5:3b", "qwen2.5vl:3b"]

_h = None


async def _resolve(host):
    return list(HOSTS.get(host, []))


async def _reverse(address):
    return None


class Harness:
    def __init__(self):
        self.port = _env.free_port()
        os.environ["GATEWAY_PORT"] = str(self.port)
        main.config.GATEWAY_PORT = self.port
        callers.RESOLVER._resolve = _resolve
        callers.RESOLVER._reverse = _reverse
        db.init_db()
        settings.update({"server_on": True}, None)
        self.seed(DEFAULT_MODELS)
        self.api = ingress_client(main.app)
        self.api.__enter__()
        self.wait_ready()
        self._clients = {}

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def seed(names):
        os.makedirs(main.config.MODELS_DIR, exist_ok=True)
        rows = [{"name": n if ":" in n else n + ":latest", "model": n, "size": 100, "digest": "d-" + n,
                 "modified_at": f"2026-01-0{i + 1}T00:00:00Z", "details": {}} for i, n in enumerate(names)]
        with open(os.path.join(main.config.MODELS_DIR, "fake_models.json"), "w", encoding="utf-8") as f:
            json.dump(rows, f)

    def wait_ready(self, timeout=20):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if server.SERVER.state == "ready" and ollama.MODELS.names and gateway.QUEUE.idle():
                return
            time.sleep(0.05)
        raise AssertionError(f"model server not ready: {server.SERVER.status()} {list(server.SERVER.log)[-5:]}")

    def run(self, coro):
        """Run a coroutine on the app's own event loop (the TestClient's portal)."""
        return self.api.portal.call(lambda: coro)

    def gw(self, address="127.0.0.9", timeout=30.0) -> httpx.Client:
        key = (address, timeout)
        if key not in self._clients:
            self._clients[key] = httpx.Client(base_url=f"http://127.0.0.1:{self.port}", timeout=timeout,
                                              transport=httpx.HTTPTransport(local_address=address), trust_env=False)
        return self._clients[key]

    def refresh_callers(self):
        self.run(callers.RESOLVER.refresh())

    def reset(self):
        """Back to the defaults between tests: settings, the queue, the fake's log, the models."""
        values = {k: v for k, v in settings.DEFAULTS.items()}
        values["server_on"] = True
        settings.update(values, None)
        gateway.MINUTE = 60
        self.wait_idle()
        open(LOG, "w").close()
        with db.get_conn() as conn:
            conn.execute("DELETE FROM model_keys")
        self.seed(DEFAULT_MODELS)
        self.run(ollama.MODELS.refresh())
        self.refresh_callers()

    @staticmethod
    def log():
        with open(LOG, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def requests(self, path):
        return [e["body"] for e in self.log() if e.get("path") == path and "body" in e]

    def wait_idle(self, timeout=10):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if gateway.QUEUE.idle():
                return
            time.sleep(0.02)
        raise AssertionError(f"queue not idle: {gateway.QUEUE.snapshot()}")

    def close(self):
        for c in self._clients.values():
            c.close()
        self.api.__exit__(None, None, None)


def get() -> Harness:
    global _h
    if _h is None:
        _h = Harness()
        atexit.register(_h.close)
    return _h
