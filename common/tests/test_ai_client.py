"""Tests for the shared AI provider layer (common/python/ai_client.py), with a fake HTTP post."""
import json
import unittest
from dataclasses import dataclass

from app.common import ai_client as core


@dataclass
class Cfg:
    provider: str
    url: str = "http://ai.local"
    model: str = "m1"
    api_key: str = "sk-1"

    @property
    def label(self):
        return core.PROVIDERS.get(self.provider, self.provider)


class FakePost:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, url, headers, body, timeout, method="POST"):
        self.calls.append({"url": url, "headers": headers, "body": json.loads(json.dumps(body)), "method": method})
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        status, payload, headers = (a + ({},))[:3]
        return status, payload if isinstance(payload, str) else json.dumps(payload), headers


def client(post, **kw):
    sleeps = []
    return core.Client(post=post, sleep=sleeps.append, **kw), sleeps


class ProviderTests(unittest.TestCase):
    def test_ollama_body_and_reply(self):
        post = FakePost((200, {"response": "", "thinking": "t", "prompt_eval_count": 3, "eval_count": 4,
                               "total_duration": 2_500_000_000}))
        c, _ = client(post)
        r = c.ollama(Cfg("ollama"), "hi", want_json=True, max_tokens=50, timeout=9)
        self.assertEqual(post.calls[0]["url"], "http://ai.local/api/generate")
        self.assertEqual(post.calls[0]["headers"], {})
        self.assertEqual(post.calls[0]["body"], {"model": "m1", "prompt": "hi", "stream": False, "format": "json",
                                                 "options": {"num_predict": 50}})
        self.assertEqual((r.text, r.input_tokens, r.output_tokens, r.seconds), ("t", 3, 4, 2.5))

    def test_openai_images_system_and_fallback(self):
        post = FakePost((400, {"error": {"message": "unknown response_format"}}),
                        (200, {"choices": [{"message": {"content": "ok"}}], "usage": {"prompt_tokens": 1}}))
        c, _ = client(post)
        r = c.openai(Cfg("openai"), "read", images=["QQ=="], system="sys", want_json=True, max_tokens=10, timeout=5)
        first, second = post.calls
        self.assertEqual(first["headers"], {"Authorization": "Bearer sk-1"})
        self.assertEqual(first["body"]["messages"][0], {"role": "system", "content": "sys"})
        self.assertEqual(first["body"]["messages"][1]["content"][1],
                         {"type": "image_url", "image_url": {"url": "data:image/png;base64,QQ=="}})
        self.assertIn("response_format", first["body"])
        self.assertNotIn("response_format", second["body"])
        self.assertEqual((r.text, r.input_tokens), ("ok", 1))

    def test_openai_max_completion_tokens(self):
        post = FakePost((400, "use max_completion_tokens instead of max_tokens"), (200, {"choices": [{"message": {"content": "x"}}]}))
        c, _ = client(post)
        c.openai(Cfg("openai"), "q", max_tokens=99)
        self.assertEqual(post.calls[1]["body"]["max_completion_tokens"], 99)
        self.assertNotIn("max_tokens", post.calls[1]["body"])

    def test_temperature_fallback(self):
        post = FakePost((400, "temperature not supported"), (200, {"choices": [{"message": {"content": "x"}}]}))
        c, _ = client(post)
        c.openai(Cfg("openai"), "q", temperature=0.2, fallback=core.fallback_json_and_temperature)
        self.assertEqual(post.calls[0]["body"]["temperature"], 0.2)
        self.assertNotIn("temperature", post.calls[1]["body"])

    def test_anthropic_shrinks_max_tokens(self):
        post = FakePost((400, {"error": {"message": "max_tokens: 20000 > 8192, the maximum"}}),
                        (200, {"content": [{"type": "text", "text": "hi"}], "stop_reason": "max_tokens",
                               "usage": {"input_tokens": 5, "cache_read_input_tokens": 2, "output_tokens": 1}}))
        c, _ = client(post)
        with self.assertLogs("ai_client", "WARNING"):
            r = c.anthropic(Cfg("anthropic"), "q", limit=20000, want_json=True)
        self.assertEqual(post.calls[0]["headers"], {"x-api-key": "sk-1", "anthropic-version": core.ANTHROPIC_VERSION})
        self.assertTrue(post.calls[0]["body"]["messages"][0]["content"][-1]["text"].endswith(core.JSON_ONLY))
        self.assertEqual(post.calls[1]["body"]["max_tokens"], 8192)
        self.assertEqual((r.text, r.input_tokens), ("hi", 7))
        self.assertEqual(core.shrink_to_largest_smaller(4096)("limit 1024 or 2048", 3000), 2048)
        self.assertEqual(core.shrink_to_largest_smaller(4096)("too many", 9000), 4096)

    TOOLS = [{"name": "todo__tasks", "description": "Open tasks.",
              "parameters": {"type": "object", "properties": {"when": {"type": "string", "enum": ["today", "week"]}},
                             "required": ["when"]}}]

    def test_tool_call_ollama(self):
        post = FakePost((200, {"message": {"content": "", "tool_calls": [
            {"function": {"name": "todo__tasks", "arguments": {"when": "today"}}},
            {"function": {"name": "", "arguments": {}}}, "junk"]}, "prompt_eval_count": 7, "eval_count": 2}))
        c, _ = client(post)
        r = c.tool_call(Cfg("ollama"), "What's on today?", self.TOOLS, system="sys", temperature=0.2, timeout=9)
        call = post.calls[0]
        self.assertEqual(call["url"], "http://ai.local/api/chat")
        self.assertEqual(call["body"]["messages"], [{"role": "system", "content": "sys"},
                                                    {"role": "user", "content": "What's on today?"}])
        self.assertEqual(call["body"]["tools"][0], {"type": "function", "function": self.TOOLS[0]})
        self.assertEqual((call["body"]["stream"], call["body"]["options"]), (False, {"temperature": 0.2}))
        self.assertEqual((r.calls, r.text, r.input_tokens), ([{"name": "todo__tasks", "args": {"when": "today"}}], "", 7))

    def test_tool_call_openai(self):
        post = FakePost((400, {"error": {"message": "Unsupported parameter: 'max_tokens'; use 'max_completion_tokens'"}}),
                        (200, {"choices": [{"message": {"content": None, "tool_calls": [
                            {"type": "function", "function": {"name": "todo__tasks", "arguments": "{\"when\": \"week\"}"}},
                            {"type": "function", "function": {"name": "todo__tasks", "arguments": "not json"}}]}}],
                               "usage": {"prompt_tokens": 5, "completion_tokens": 1}}))
        c, _ = client(post)
        r = c.tool_call(Cfg("openai"), "q", self.TOOLS, max_tokens=300)
        first, second = post.calls
        self.assertEqual(first["url"], "http://ai.local/chat/completions")
        self.assertEqual(first["body"]["tools"], [{"type": "function", "function": self.TOOLS[0]}])
        self.assertEqual((first["body"]["max_tokens"], second["body"]["max_completion_tokens"]), (300, 300))
        self.assertEqual(r.calls, [{"name": "todo__tasks", "args": {"when": "week"}}, {"name": "todo__tasks", "args": {}}])
        self.assertEqual(r.text, "")

    def test_tool_call_anthropic(self):
        post = FakePost((200, {"content": [{"type": "text", "text": "Let me look."},
                                           {"type": "tool_use", "id": "t1", "name": "todo__tasks", "input": {"when": "today"}}],
                               "usage": {"input_tokens": 9, "output_tokens": 3}, "stop_reason": "tool_use"}))
        c, _ = client(post)
        r = c.tool_call(Cfg("anthropic"), "q", self.TOOLS, system="sys", max_tokens=1000)
        body = post.calls[0]["body"]
        self.assertEqual(post.calls[0]["url"], "http://ai.local/v1/messages")
        self.assertEqual(body["tools"], [{"name": "todo__tasks", "description": "Open tasks.",
                                          "input_schema": self.TOOLS[0]["parameters"]}])
        self.assertEqual((body["system"], body["max_tokens"]), ("sys", 1000))
        self.assertEqual((r.text, r.calls), ("Let me look.", [{"name": "todo__tasks", "args": {"when": "today"}}]))

    def test_tool_call_without_tool_support(self):
        post = FakePost((400, {"error": "registry.ollama.ai/library/gemma:2b does not support tools"}))
        c, _ = client(post)
        with self.assertRaises(core.AIError) as e:
            c.tool_call(Cfg("ollama"), "q", self.TOOLS)
        self.assertEqual(e.exception.kind, "bad_request")

    def test_list_models(self):
        post = FakePost((200, {"models": [{"name": "a"}, {"model": "b"}, "x"]}))
        c, _ = client(post, model_name=lambda m: m.get("name") or m.get("model") or "")
        self.assertEqual(c.list_models(Cfg("ollama")), ["a", "b"])
        self.assertEqual(post.calls[0]["method"], "GET")
        with self.assertRaises(core.AIError) as e:
            c.list_models(Cfg("anthropic", api_key=""))
        self.assertEqual(e.exception.kind, "auth")


class ErrorTests(unittest.TestCase):
    def test_retries_then_gives_up(self):
        post = FakePost(*[(503, "busy")] * 4)
        c, sleeps = client(post)
        with self.assertLogs("ai_client", "WARNING"), self.assertRaises(core.AIError) as e:
            c.ollama(Cfg("ollama"), "q")
        self.assertEqual(sleeps, [5.0, 15.0, 45.0])
        self.assertEqual((e.exception.kind, e.exception.raw), ("rate_limit", "busy"))

    def test_retry_after_header(self):
        post = FakePost((429, "", {"Retry-After": "3"}), (200, {"response": "ok"}))
        c, sleeps = client(post)
        with self.assertLogs("ai_client", "WARNING"):
            self.assertEqual(c.ollama(Cfg("ollama"), "q").text, "ok")
        self.assertEqual(sleeps, [3.0])

    def test_status_kinds(self):
        for status, kind in ((401, "auth"), (403, "auth"), (404, "not_found"), (400, "bad_request")):
            c, _ = client(FakePost((status, {"error": {"message": "why"}})))
            with self.assertRaises(core.AIError) as e:
                c.ollama(Cfg("ollama"), "q")
            self.assertEqual(e.exception.kind, kind)
            self.assertIn("why", str(e.exception))

    def test_bad_answers(self):
        for answer in ((200, "<html>"), (200, "[1]")):
            c, _ = client(FakePost(answer))
            with self.assertRaises(core.AIError) as e:
                c.ollama(Cfg("ollama"), "q")
            self.assertEqual(e.exception.kind, "bad_response")

    def test_network_errors(self):
        import urllib.error
        c, _ = client(FakePost(TimeoutError()))
        with self.assertRaises(core.AIError) as e:
            c.ollama(Cfg("ollama"), "q", timeout=7)
        self.assertEqual((e.exception.kind, str(e.exception)), ("timeout", "Ollama didn't answer within 7 s."))
        c, _ = client(FakePost(urllib.error.URLError("refused")))
        with self.assertRaises(core.AIError) as e:
            c.ollama(Cfg("ollama"), "q")
        self.assertEqual(e.exception.kind, "unreachable")

    def test_wording_scrubs_the_key_and_drops_raw(self):
        import urllib.error
        w = core.Wording(unreachable="No {label} at {url}: {reason}.", keep_raw=False, scrub_key=True,
                         timeout_in_reason=True)
        c, _ = client(FakePost((401, {"error": {"message": "bad key sk-1"}})), wording=w)
        with self.assertRaises(core.AIError) as e:
            c.openai(Cfg("openai"), "q")
        self.assertNotIn("sk-1", str(e.exception))
        self.assertEqual(e.exception.raw, "")
        c, _ = client(FakePost(urllib.error.URLError(TimeoutError("timed out"))), wording=w)
        with self.assertRaises(core.AIError) as e:
            c.openai(Cfg("openai"), "q", timeout=3)
        self.assertEqual(e.exception.kind, "timeout")
        c, _ = client(FakePost((200, {"choices": [{"message": {"content": "x"}}]})), wording=w)
        self.assertEqual(c.openai(Cfg("openai"), "q").raw, "")


if __name__ == "__main__":
    unittest.main()
