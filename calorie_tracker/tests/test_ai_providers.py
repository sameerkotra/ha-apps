"""AI providers through app/ai_client.py: Ollama, OpenAI-compatible and
Anthropic Claude — request shapes, auth headers, JSON mode, retries, error
messages, Test connection, the access key as a secret (page/API, backup
download and restore) and the privacy notice. No real network: ai_client._post
is replaced by FakeAI."""
import _env  # noqa: F401  (must be first)

import glob
import io
import json
import logging
import os
import sqlite3
import unittest
import urllib.error
from unittest import mock

from starlette.testclient import TestClient

from app import ai_client, config, db, settings
from app.main import app

logging.getLogger("ai").setLevel(logging.ERROR)
logging.getLogger("settings").setLevel(logging.ERROR)

KEY = "sk-test-SECRET-abcdefgh1234"
ADMIN = {"X-Remote-User-Id": "u_admin", "X-Remote-User-Name": "adminy", "X-Remote-User-Display-Name": "Adminy"}
ALICE = {"X-Remote-User-Id": "u_alice", "X-Remote-User-Name": "alice", "X-Remote-User-Display-Name": "Alice"}
ESTIMATE = {"food_name": "Toast", "serving_size": 1, "serving_unit": "slice",
            "calories": 80, "protein": 3, "carbs": 15, "fat": 1, "note": "one slice"}


class FakeAI:
    """Replaces ai_client._post: records each request and answers from a queue (or a default)."""

    def __init__(self):
        self.calls, self.queue = [], []

    def __call__(self, url, headers, body, timeout, method="POST"):
        self.calls.append({"url": url, "headers": dict(headers), "body": json.loads(json.dumps(body)),
                           "method": method, "timeout": timeout})
        if self.queue:
            item = self.queue.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        if url.endswith("/chat/completions"):
            return 200, json.dumps({"choices": [{"message": {"content": json.dumps(ESTIMATE)}}],
                                    "usage": {"prompt_tokens": 900, "completion_tokens": 40}}), {}
        if url.endswith("/v1/messages"):
            return 200, json.dumps({"content": [{"type": "text", "text": "Sure: " + json.dumps(ESTIMATE)}],
                                    "stop_reason": "end_turn",
                                    "usage": {"input_tokens": 700, "output_tokens": 30,
                                              "cache_read_input_tokens": 100}}), {}
        if url.endswith("/api/tags"):
            return 200, json.dumps({"models": [{"name": "llama3:latest"}, {"name": "mistral:7b"}]}), {}
        if url.endswith("/models") or "/v1/models" in url:
            return 200, json.dumps({"data": [{"id": "gpt-4o"}, {"id": "claude-sonnet-x"}]}), {}
        return 200, json.dumps({"response": json.dumps(ESTIMATE), "prompt_eval_count": 5, "eval_count": 2}), {}


class ProviderBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._ctx = TestClient(app, client=("127.0.0.1", 12345))
        cls.c = cls._ctx.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls._ctx.__exit__(None, None, None)

    def setUp(self):
        for f in glob.glob(config.DB_PATH + "*"):
            os.remove(f)
        db.init_db()
        ai_client.forget_state()
        self.fake = FakeAI()
        for p in (mock.patch.object(ai_client, "_post", self.fake),
                  mock.patch.object(ai_client, "_sleep", lambda s: self.fake.calls.append({"slept": s}))):
            p.start()
            self.addCleanup(p.stop)
        for h in (ADMIN, ALICE):
            self.c.get("/api/me", headers=h)

    def put(self, **values):
        r = self.c.put("/api/admin/settings", json=values, headers=ADMIN)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def requests(self):
        return [c for c in self.fake.calls if "url" in c]


class RequestShapes(ProviderBase):
    def test_ollama_generate_json_mode_and_warmup(self):
        self.put(ai_provider="ollama", ai_url="http://ollama.lan:11434", ai_model="llama3")
        r = self.c.post("/api/ai/estimate", json={"description": "toast"}, headers=ALICE)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["food_name"], "Toast")
        warm, est = self.requests()
        self.assertEqual(warm["body"]["prompt"], "hi")                   # Ollama only: loads the model
        self.assertEqual(est["url"], "http://ollama.lan:11434/api/generate")
        self.assertEqual(est["headers"], {})                             # no auth for Ollama
        self.assertEqual(est["body"]["format"], "json")
        self.assertEqual(est["body"]["options"], {"temperature": 0.2})
        self.assertIs(est["body"]["stream"], False)
        self.assertIn('"toast"', est["body"]["prompt"])
        # chat: system prompt + the message; warm now, so no second "hi"
        self.fake.calls.clear()
        self.assertEqual(self.c.post("/api/ai/chat", json={"message": "Is 150g protein a lot?"}, headers=ALICE).status_code, 200)
        (chat,) = self.requests()
        self.assertEqual(chat["body"]["prompt"], "Is 150g protein a lot?")
        self.assertIn("nutrition", chat["body"]["system"])
        self.assertNotIn("format", chat["body"])

    def test_openai_request_shape_and_bearer_key(self):
        self.put(ai_provider="openai", ai_model="gpt-4o", ai_api_key=KEY)
        r = self.c.post("/api/ai/estimate", json={"description": "toast"}, headers=ALICE)
        self.assertEqual(r.status_code, 200, r.text)
        (sent,) = self.requests()                                        # no warm-up for a cloud provider
        self.assertEqual(sent["url"], "https://api.openai.com/v1/chat/completions")
        self.assertEqual(sent["headers"]["Authorization"], f"Bearer {KEY}")
        self.assertEqual(sent["body"]["response_format"], {"type": "json_object"})
        self.assertEqual(sent["body"]["messages"][-1]["role"], "user")
        self.assertNotIn(KEY, sent["url"])
        st = self.c.get("/api/ai/status", headers=ALICE).json()
        self.assertEqual((st["last_request"]["input_tokens"], st["last_request"]["output_tokens"]), (900, 40))
        self.assertNotIn(KEY, json.dumps(st))
        self.fake.calls.clear()
        self.c.post("/api/ai/chat", json={"message": "hi?"}, headers=ALICE)
        msgs = self.requests()[0]["body"]["messages"]
        self.assertEqual([m["role"] for m in msgs], ["system", "user"])

    def test_openai_compatible_without_key_or_json_mode(self):
        self.put(ai_provider="openai", ai_url="http://lmstudio.lan:1234/v1/", ai_model="local")
        self.fake.queue = [(400, json.dumps({"error": {"message": "response_format is not supported"}}), {})]
        r = self.c.post("/api/ai/estimate", json={"description": "toast"}, headers=ALICE)
        self.assertEqual(r.status_code, 200, r.text)
        first, second = self.requests()
        self.assertIn("response_format", first["body"])
        self.assertNotIn("response_format", second["body"])
        self.assertNotIn("Authorization", second["headers"])             # no key: no header
        self.assertEqual(second["url"], "http://lmstudio.lan:1234/v1/chat/completions")

    def test_anthropic_request_shape(self):
        self.put(ai_provider="anthropic", ai_model="claude-sonnet-x", ai_api_key=KEY, ai_max_tokens=2000)
        r = self.c.post("/api/ai/estimate", json={"description": "toast"}, headers=ALICE)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["calories"], 80)                       # first { … last } of the answer
        (sent,) = self.requests()
        self.assertEqual(sent["url"], "https://api.anthropic.com/v1/messages")
        self.assertEqual(sent["headers"]["x-api-key"], KEY)
        self.assertEqual(sent["headers"]["anthropic-version"], "2023-06-01")
        self.assertNotIn("Authorization", sent["headers"])
        self.assertEqual((sent["body"]["max_tokens"], sent["body"]["model"]), (2000, "claude-sonnet-x"))
        content = sent["body"]["messages"][0]["content"]
        self.assertTrue(content[-1]["text"].endswith("Answer with the JSON object only."))
        st = self.c.get("/api/ai/status", headers=ALICE).json()
        self.assertEqual(st["last_request"]["input_tokens"], 800)         # cache reads count as input
        self.fake.calls.clear()
        self.c.post("/api/ai/chat", json={"message": "hi?"}, headers=ALICE)
        self.assertIn("nutrition", self.requests()[0]["body"]["system"])

    def test_anthropic_lowers_max_tokens_when_the_model_allows_less(self):
        self.put(ai_provider="anthropic", ai_model="small", ai_api_key=KEY, ai_max_tokens=16000)
        self.fake.queue = [(400, json.dumps({"type": "error", "error": {
            "type": "invalid_request_error", "message": "max_tokens: 16000 > 8192, which is the maximum allowed"}}), {})]
        self.assertEqual(self.c.post("/api/ai/chat", json={"message": "x"}, headers=ALICE).status_code, 200)
        a, b = self.requests()
        self.assertEqual((a["body"]["max_tokens"], b["body"]["max_tokens"]), (16000, 8192))

    def test_anthropic_needs_a_key(self):
        self.put(ai_provider="anthropic", ai_model="claude-sonnet-x")
        self.assertFalse(settings.ai_configured())
        r = self.c.post("/api/ai/estimate", json={"description": "toast"}, headers=ALICE)
        self.assertEqual(r.status_code, 503)
        self.assertIn("needs an access key", r.json()["detail"])
        self.assertEqual(self.requests(), [])
        self.assertIn("needs an access key", self.c.get("/api/ai/status", headers=ALICE).json()["message"])

    def test_warmup_only_for_ollama(self):
        self.put(ai_provider="openai", ai_model="gpt-4o", ai_api_key=KEY)
        r = self.c.post("/api/ai/warmup", headers=ALICE)
        self.assertEqual(r.json(), {"ok": True, "model": "gpt-4o", "skipped": True})
        self.assertFalse(self.c.get("/api/ai/status", headers=ALICE).json()["warmup"])
        self.assertEqual(self.requests(), [])
        self.put(ai_provider="ollama", ai_url="http://ollama.lan:11434", ai_model="llama3")
        self.assertEqual(self.c.post("/api/ai/warmup", headers=ALICE).json(), {"ok": True, "model": "llama3"})
        self.assertEqual(self.requests()[0]["body"]["prompt"], "hi")

    def test_settings_change_applies_to_the_next_request(self):
        self.put(ai_provider="ollama", ai_url="http://ollama.lan:11434", ai_model="llama3")
        self.c.post("/api/ai/chat", json={"message": "x"}, headers=ALICE)
        self.put(ai_provider="openai", ai_url="", ai_model="gpt-4o", ai_api_key=KEY)
        self.fake.calls.clear()
        self.c.post("/api/ai/chat", json={"message": "x"}, headers=ALICE)
        self.assertEqual([(c["url"], c["body"]["model"]) for c in self.requests()],
                         [("https://api.openai.com/v1/chat/completions", "gpt-4o")])


class ErrorsAndRetries(ProviderBase):
    def setUp(self):
        super().setUp()
        self.put(ai_provider="openai", ai_model="gpt-4o", ai_api_key=KEY)

    def chat(self):
        return self.c.post("/api/ai/chat", json={"message": "x"}, headers=ALICE)

    def test_refused_key_is_explained_without_the_key(self):
        self.fake.queue = [(401, json.dumps({"error": {"message": f"Incorrect API key provided: {KEY}"}}), {})]
        r = self.chat()
        self.assertEqual(r.status_code, 502)
        detail = r.json()["detail"]
        self.assertIn("refused the access key", detail)
        self.assertIn("Incorrect API key provided", detail)             # the provider's own message
        self.assertIn("App settings", detail)
        self.assertNotIn(KEY, detail)
        self.assertNotIn(KEY, json.dumps(self.c.get("/api/ai/status", headers=ALICE).json()))

    def test_404_names_address_and_model(self):
        self.fake.queue = [(404, json.dumps({"error": {"message": "model gpt-4o not found"}}), {})]
        detail = self.chat().json()["detail"]
        self.assertIn("check the address and the model name", detail)
        self.assertIn("model gpt-4o not found", detail)

    def test_unreachable_and_timeout_name_provider_and_address(self):
        self.fake.queue = [urllib.error.URLError("connection refused")]
        detail = self.chat().json()["detail"]
        self.assertIn("Couldn't reach OpenAI-compatible at https://api.openai.com/v1", detail)
        self.assertIn("connection refused", detail)
        self.fake.queue = [TimeoutError("timed out")]
        detail = self.chat().json()["detail"]
        self.assertIn("didn't answer within", detail)
        self.assertIn("https://api.openai.com/v1", detail)
        self.assertNotIn(KEY, detail)

    def test_retries_with_retry_after_then_gives_up(self):
        self.fake.queue = [(429, "{}", {"Retry-After": "7"}), (529, "{}", {}), (503, "{}", {})]
        self.assertEqual(self.chat().status_code, 200)
        self.assertEqual([c["slept"] for c in self.fake.calls if "slept" in c], [7.0, 15.0, 45.0])
        self.fake.calls.clear()
        self.fake.queue = [(429, json.dumps({"error": {"message": "slow down"}}), {"retry-after": "999"})] * 4
        r = self.chat()
        self.assertEqual(r.status_code, 502)
        self.assertIn("rate-limiting", r.json()["detail"])
        self.assertEqual(len(self.requests()), 4)                        # 1 + 3 retries
        self.assertEqual({c["slept"] for c in self.fake.calls if "slept" in c}, {60.0})   # capped
        for status in (500, 502, 504):
            self.fake.queue = [(status, "{}", {})] * 4
            self.assertIn("overloaded or failing", self.chat().json()["detail"])

    def test_estimate_that_is_not_json(self):
        self.fake.queue = [(200, json.dumps({"choices": [{"message": {"content": "no idea"}}]}), {})]
        r = self.c.post("/api/ai/estimate", json={"description": "toast"}, headers=ALICE)
        self.assertEqual(r.status_code, 502)
        self.assertIn("couldn't be read as nutrition facts", r.json()["detail"])


class AccessKeySecret(ProviderBase):
    def test_key_is_never_returned_and_blank_keeps_it(self):
        j = self.put(ai_provider="openai", ai_model="gpt-4o", ai_api_key=KEY)
        self.assertNotIn(KEY, json.dumps(j))
        self.assertEqual(j["secrets"]["ai_api_key"], {"saved": True, "hint": "…1234"})
        self.assertNotIn(KEY, self.c.get("/api/admin/settings", headers=ADMIN).text)
        self.put(ai_model="gpt-4.1", ai_api_key="")                     # blank box = keep
        self.put(ai_model="gpt-4.1", ai_api_key="   ")
        self.assertEqual(settings.get("ai_api_key"), KEY)
        self.put(ai_api_key="sk-other-9876543210zz")                    # replace
        self.assertEqual(settings.get("ai_api_key"), "sk-other-9876543210zz")
        j = self.put(clear_ai_api_key=True)                              # remove
        self.assertEqual(j["secrets"]["ai_api_key"], {"saved": False, "hint": ""})
        self.assertEqual(settings.get("ai_api_key"), "")

    def test_key_never_logged(self):
        with self.assertLogs("settings", "INFO") as logs:
            logging.getLogger("settings").setLevel(logging.INFO)
            try:
                self.put(ai_provider="openai", ai_model="gpt-4o", ai_api_key=KEY)
            finally:
                logging.getLogger("settings").setLevel(logging.ERROR)
        self.assertIn("ai_api_key", "\n".join(logs.output))
        self.assertNotIn(KEY, "\n".join(logs.output))

    def test_backup_download_blanks_the_key_and_restore_keeps_ours(self):
        self.put(ai_provider="openai", ai_model="gpt-4o", ai_api_key=KEY)
        r = self.c.get("/api/admin-storage-download-db", headers=ADMIN)
        self.assertEqual(r.status_code, 200)
        self.assertNotIn(KEY.encode(), r.content)
        path = os.path.join(config.DATA_DIR, "download-test.db")
        with open(path, "wb") as f:
            f.write(r.content)
        try:
            c = sqlite3.connect(path)
            self.assertEqual(c.execute("SELECT value FROM app_settings WHERE key='ai_api_key'").fetchone()[0], '""')
            self.assertEqual(c.execute("SELECT value FROM app_settings WHERE key='ai_model'").fetchone()[0], '"gpt-4o"')
            c.close()
            self.assertEqual(settings.get("ai_api_key"), KEY)            # the live database still has it
            self.put(ai_model="gpt-4.1")
            with open(path, "rb") as f:
                r = self.c.post("/api/admin-storage-import-db", headers=ADMIN, files={"file": ("b.db", f)})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(settings.get("ai_model"), "gpt-4o")        # the backup's settings…
            self.assertEqual(settings.get("ai_api_key"), KEY)            # …but this install's key
        finally:
            os.remove(path)

    def test_restore_without_a_key_on_either_side(self):
        snap = self.c.get("/api/admin-storage-download-db", headers=ADMIN).content
        r = self.c.post("/api/admin-storage-import-db", headers=ADMIN, files={"file": ("b.db", io.BytesIO(snap))})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(settings.get("ai_api_key"), "")


class TestConnection(ProviderBase):
    def probe(self, **body):
        r = self.c.post("/api/admin/settings/test-ai", json=body, headers=ADMIN)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_per_provider_lists_models_and_generates_nothing(self):
        j = self.probe(ai_provider="openai", ai_url="", ai_model="gpt-4o", ai_api_key=KEY)
        self.assertTrue(j["ok"])
        self.assertIn("offers gpt-4o", j["message"])
        last = self.requests()[-1]
        self.assertEqual((last["url"], last["method"], last["body"]), ("https://api.openai.com/v1/models", "GET", None))
        self.assertEqual(last["headers"]["Authorization"], f"Bearer {KEY}")
        self.assertLessEqual(last["timeout"], 10)
        j = self.probe(ai_provider="anthropic", ai_url="", ai_model="claude-sonnet-x", ai_api_key=KEY)
        self.assertIn("offers claude-sonnet-x", j["message"])
        self.assertTrue(self.requests()[-1]["url"].startswith("https://api.anthropic.com/v1/models"))
        self.assertEqual(self.requests()[-1]["headers"]["x-api-key"], KEY)
        j = self.probe(ai_provider="anthropic", ai_model="nope", ai_api_key=KEY)
        self.assertFalse(j["ok"])
        self.assertIn("doesn't list nope", j["message"])
        self.assertIn("Enter the access key", self.probe(ai_provider="anthropic", ai_model="x")["message"])
        j = self.probe(ai_provider="ollama", ai_url="http://10.0.0.9:11434/", ai_model="llama3")
        self.assertTrue(j["ok"] and j["modelFound"])                     # "llama3" is listed as "llama3:latest"
        self.assertEqual(self.requests()[-1]["url"], "http://10.0.0.9:11434/api/tags")
        self.assertEqual(self.requests()[-1]["headers"], {})
        self.assertEqual(self.probe(ai_provider="ollama", ai_url="")["message"], "Enter the address first.")
        self.assertEqual(self.probe(ai_provider="")["message"], "Choose a provider first.")
        self.assertTrue(all(c["method"] == "GET" for c in self.requests()))
        self.assertEqual(settings.all()["ai_provider"], "")              # testing saves nothing

    def test_blank_key_box_uses_the_saved_key_unless_removing(self):
        self.put(ai_provider="openai", ai_model="gpt-4o", ai_api_key=KEY)
        self.probe(ai_provider="openai", ai_model="gpt-4o", ai_api_key="")
        self.assertEqual(self.requests()[-1]["headers"]["Authorization"], f"Bearer {KEY}")
        self.probe(ai_provider="openai", ai_model="gpt-4o", ai_api_key="sk-typed-00000000")
        self.assertEqual(self.requests()[-1]["headers"]["Authorization"], "Bearer sk-typed-00000000")
        self.probe(ai_provider="openai", ai_model="gpt-4o", clear_ai_api_key=True)
        self.assertNotIn("Authorization", self.requests()[-1]["headers"])
        self.probe()                                                      # nothing typed: the saved values
        self.assertEqual(self.requests()[-1]["headers"]["Authorization"], f"Bearer {KEY}")

    def test_errors_and_bad_input(self):
        self.fake.queue = [(401, json.dumps({"error": {"message": "bad key"}}), {})]
        j = self.probe(ai_provider="openai", ai_model="gpt-4o", ai_api_key=KEY)
        self.assertFalse(j["ok"])
        self.assertIn("refused the access key", j["message"])
        self.assertNotIn(KEY, json.dumps(j))
        self.fake.queue = [urllib.error.URLError("connection refused")]
        j = self.probe(ai_provider="ollama", ai_url="http://10.0.0.9:11434", ai_model="llama3")
        self.assertIn("Couldn't reach Ollama at http://10.0.0.9:11434", j["message"])
        r = self.c.post("/api/admin/settings/test-ai", json={"ai_provider": "ollama", "ai_url": "file:///etc/passwd"},
                        headers=ADMIN)
        self.assertEqual(r.status_code, 422)


class PrivacyNotice(ProviderBase):
    CASES = [
        ("http://192.168.1.10:11434", False), ("http://10.0.0.5:1234/v1", False), ("http://localhost:11434", False),
        ("http://homeassistant.local:11434", False), ("http://gpu:11434", False), ("http://[::1]:11434", False),
        ("http://ollama.lan:11434", False), ("http://box.home.arpa:11434", False), ("http://169.254.1.2:1", False),
        ("https://api.openai.com/v1", True), ("https://openrouter.ai/api/v1", True), ("http://8.8.8.8:80", True),
    ]

    def test_what_counts_as_outside_the_home_network(self):
        for url, external in self.CASES:
            with self.subTest(url=url):
                self.put(ai_provider="openai", ai_url=url, ai_model="m")
                self.assertIs(settings.ai_is_external(), external)

    def test_notice_for_everyone_while_external(self):
        self.put(ai_provider="ollama", ai_url="http://192.168.1.10:11434", ai_model="llama3")
        self.assertIsNone(self.c.get("/api/ai/status", headers=ALICE).json()["privacy"])
        self.put(ai_provider="anthropic", ai_url="", ai_model="claude-sonnet-x", ai_api_key=KEY)
        for h in (ALICE, ADMIN):
            p = self.c.get("/api/ai/status", headers=h).json()["privacy"]
            self.assertEqual(p, {"label": "Anthropic Claude", "host": "api.anthropic.com"})
        self.put(ai_provider="")                                         # AI off: nothing is sent anywhere
        self.assertIsNone(self.c.get("/api/ai/status", headers=ALICE).json()["privacy"])
        with open(os.path.join(os.path.dirname(__file__), "..", "app", "static", "app.js"), encoding="utf-8") as f:
            js = f.read()
        self.assertIn("own privacy and data-retention rules apply", js)
        self.assertIn("Estimate with AI", js.split("function aiNoticeNodes", 1)[1].split("function renderAiState", 1)[0])


if __name__ == "__main__":
    unittest.main()
