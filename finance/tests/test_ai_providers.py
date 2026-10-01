"""AI providers: Ollama, OpenAI-compatible and Claude through ai_client (SPEC.md section 22)."""
import json
import os
import sqlite3

import pytest

WIFE = {"x-remote-user-id": "wife", "x-remote-user-name": "Wife"}
KEY = "sk-test-SECRET-abcdefgh1234"


class FakeAI:
    """Replaces ai_client._post: records each request and answers from a queue (or a default)."""

    def __init__(self):
        self.calls, self.queue = [], []

    def __call__(self, url, headers, body, timeout, method="POST"):
        self.calls.append({"url": url, "headers": dict(headers), "body": json.loads(json.dumps(body)), "method": method})
        if self.queue:
            return self.queue.pop(0)
        if url.endswith("/chat/completions"):
            return 200, json.dumps({"choices": [{"message": {"content": '{"categories": {}}'}}],
                                    "usage": {"prompt_tokens": 900, "completion_tokens": 40}}), {}
        if url.endswith("/v1/messages"):
            return 200, json.dumps({"content": [{"type": "text", "text": '{"ok": true}'}], "stop_reason": "end_turn",
                                    "usage": {"input_tokens": 700, "output_tokens": 30,
                                              "cache_read_input_tokens": 100}}), {}
        if url.endswith("/models") or "/v1/models" in url:
            return 200, json.dumps({"data": [{"id": "gpt-4o"}, {"id": "claude-sonnet-x"}]}), {}
        return 200, json.dumps({"response": "{}", "prompt_eval_count": 5, "eval_count": 2}), {}


@pytest.fixture
def fake_ai(monkeypatch):
    from app import ai_client
    fake = FakeAI()
    monkeypatch.setattr(ai_client, "_post", fake)
    monkeypatch.setattr(ai_client, "_sleep", lambda s: fake.calls.append({"slept": s}))
    return fake


def _set(env, **values):
    data = {"ai_provider": "ollama", "ai_url": "http://ollama.invalid:11434", "ai_model": "test-model",
            "show_ai_usage": "1", "price_input_per_million": "0", "price_output_per_million": "0",
            "feature_utilities": "1", "feature_tolls": "1"}
    data.update(values)
    return env.post("settings", data={k: v for k, v in data.items() if v is not None})


def test_openai_request_shape_and_usage(env, fake_ai):
    from app import ai_client, ai_usage
    assert _set(env, ai_provider="openai", ai_url="", ai_model="gpt-4o", ai_api_key=KEY).status_code == 303
    with ai_usage.recording() as calls:
        reply = ai_client.generate("Read this", images=["QUJD"], want_json=True, purpose="Extraction (attempt 1)")
    sent = fake_ai.calls[-1]
    assert sent["url"] == "https://api.openai.com/v1/chat/completions"
    assert sent["headers"]["Authorization"] == f"Bearer {KEY}"
    content = sent["body"]["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "Read this"}
    assert content[1]["image_url"]["url"] == "data:image/png;base64,QUJD"
    assert sent["body"]["response_format"] == {"type": "json_object"}
    assert reply.text == '{"categories": {}}'
    assert calls[0]["input_tokens"] == 900 and calls[0]["output_tokens"] == 40 and calls[0]["model"] == "gpt-4o"


def test_openai_without_json_mode_is_retried_plain(env, fake_ai):
    from app import ai_client
    _set(env, ai_provider="openai", ai_url="http://lmstudio.lan:1234/v1", ai_model="local", ai_api_key="")
    fake_ai.queue = [(400, json.dumps({"error": {"message": "response_format is not supported"}}), {})]
    ai_client.generate("x", want_json=True)
    assert "response_format" in fake_ai.calls[0]["body"] and "response_format" not in fake_ai.calls[1]["body"]
    assert "Authorization" not in fake_ai.calls[1]["headers"]          # no key: no header
    assert fake_ai.calls[1]["url"] == "http://lmstudio.lan:1234/v1/chat/completions"


def test_claude_request_shape_and_usage(env, fake_ai):
    from app import ai_client, ai_usage
    _set(env, ai_provider="anthropic", ai_url="", ai_model="claude-sonnet-x", ai_api_key=KEY, ai_max_output_tokens="12000")
    with ai_usage.recording() as calls:
        reply = ai_client.generate("Read this", images=["QUJD"], want_json=True)
    sent = fake_ai.calls[-1]
    assert sent["url"] == "https://api.anthropic.com/v1/messages"
    assert sent["headers"]["x-api-key"] == KEY and sent["headers"]["anthropic-version"] == "2023-06-01"
    assert sent["body"]["max_tokens"] == 12000 and sent["body"]["model"] == "claude-sonnet-x"
    content = sent["body"]["messages"][0]["content"]
    assert content[0]["source"] == {"type": "base64", "media_type": "image/png", "data": "QUJD"}
    assert content[-1]["type"] == "text" and content[-1]["text"].startswith("Read this")
    assert reply.text == '{"ok": true}'
    assert calls[0]["input_tokens"] == 800 and calls[0]["output_tokens"] == 30   # cache reads counted as input


def test_claude_lowers_max_tokens_when_the_model_allows_less(env, fake_ai):
    from app import ai_client
    _set(env, ai_provider="anthropic", ai_model="small", ai_api_key=KEY)
    fake_ai.queue = [(400, json.dumps({"type": "error", "error": {
        "type": "invalid_request_error", "message": "max_tokens: 16000 > 8192, which is the maximum allowed"}}), {})]
    ai_client.generate("x")
    assert fake_ai.calls[0]["body"]["max_tokens"] == 16000 and fake_ai.calls[1]["body"]["max_tokens"] == 8192


def test_claude_needs_a_key(env, fake_ai):
    from app import ai_client, settings
    _set(env, ai_provider="anthropic", ai_model="claude-sonnet-x")
    assert not settings.ai_configured()
    with pytest.raises(ai_client.AIError, match="needs an access key"):
        ai_client.generate("x")
    assert fake_ai.calls == []
    assert "set up yet" in env.get("uploads").text


def test_wrong_key_and_rate_limits_are_explained(env, fake_ai):
    from app import ai_client
    _set(env, ai_provider="openai", ai_model="gpt-4o", ai_api_key=KEY)
    fake_ai.queue = [(401, json.dumps({"error": {"message": "Incorrect API key provided"}}), {})]
    with pytest.raises(ai_client.AIError) as err:
        ai_client.generate("x")
    assert err.value.kind == "auth" and "refused the access key" in str(err.value) and KEY not in str(err.value)
    # 429 twice, then fine: retried after the Retry-After wait
    fake_ai.calls.clear()
    fake_ai.queue = [(429, "{}", {"Retry-After": "7"}), (529, "{}", {})]
    assert ai_client.generate("x").text
    assert [c.get("slept") for c in fake_ai.calls if "slept" in c] == [7.0, 15.0]
    # always 429: gives up with a clear message
    fake_ai.queue = [(429, json.dumps({"error": {"message": "slow down"}}), {})] * 4
    with pytest.raises(ai_client.AIError, match="rate-limiting"):
        ai_client.generate("x")


def test_key_is_never_shown_or_downloaded(env, fake_ai):
    _set(env, ai_provider="openai", ai_model="gpt-4o", ai_api_key=KEY)
    page = env.get("settings").text
    assert KEY not in page and "…1234" in page and "Remove the saved key" in page
    # saving again with the key box empty keeps it
    _set(env, ai_provider="openai", ai_model="gpt-4.1")
    from app import settings
    assert settings.get("ai_api_key") == KEY
    # the database download has it blanked; the live database still has it
    r = env.get("admin-storage-download-db")
    path = os.path.join(env.dir, "download.db")
    with open(path, "wb") as f:
        f.write(r.content)
    c = sqlite3.connect(path)
    assert c.execute("SELECT value FROM app_settings WHERE key='ai_api_key'").fetchone()[0] == ""
    assert c.execute("SELECT value FROM app_settings WHERE key='ai_model'").fetchone()[0] == "gpt-4.1"
    c.close()
    # importing that backup keeps this install's key
    with open(path, "rb") as f:
        r = env.post("admin-storage-import-db", data={"confirm": "yes"}, files={"db_file": ("b.db", f.read())})
    assert r.status_code == 303 and "imported=1" in r.headers["location"]
    c = sqlite3.connect(env.db_path)
    assert c.execute("SELECT value FROM app_settings WHERE key='ai_api_key'").fetchone()[0] == KEY
    c.close()
    # remove
    _set(env, ai_provider="openai", ai_model="gpt-4.1", clear_ai_api_key="1")
    assert settings.get("ai_api_key") == "" and "Remove the saved key" not in env.get("settings").text
    # Query tab can't see settings
    from app import query_engine as qe
    assert "app_settings" in qe.HIDDEN_TABLES


def test_connection_test_per_provider(env, fake_ai):
    r = env.post("settings-test", data={"ai_provider": "openai", "ai_url": "", "ai_model": "gpt-4o", "ai_api_key": KEY})
    assert "offers gpt-4o" in r.text
    assert fake_ai.calls[-1]["url"] == "https://api.openai.com/v1/models" and fake_ai.calls[-1]["method"] == "GET"
    r = env.post("settings-test", data={"ai_provider": "anthropic", "ai_model": "claude-sonnet-x", "ai_api_key": KEY})
    assert "offers claude-sonnet-x" in r.text and fake_ai.calls[-1]["headers"]["x-api-key"] == KEY
    r = env.post("settings-test", data={"ai_provider": "anthropic", "ai_model": "nope", "ai_api_key": KEY})
    assert "list nope" in r.text
    assert "Enter the access key" in env.post("settings-test", data={"ai_provider": "anthropic", "ai_model": "x"}).text


def test_cloud_skips_warm_up_and_uses_the_text_model(env, fake_ai, monkeypatch):
    from app import jobs
    from app.parser import pipeline as pl, vision
    monkeypatch.setattr(vision, "rasterize_pdf", lambda pdf: (env.dir + "/nothing", []))
    fake_ai.queue = [(200, json.dumps({"choices": [{"message": {"content": json.dumps(
        {"transactions": [{"date": "2026-08-05", "amount": -20.0, "description": "ODD SHOP"}]})}}],
        "usage": {"prompt_tokens": 1000, "completion_tokens": 100}}), {})]
    _set(env, ai_provider="openai", ai_model="gpt-vision", ai_text_model="gpt-mini", ai_api_key=KEY)
    acct = env.account()
    path = env.pdf(["Previous Balance $100.00", "New Balance $80.00", "2026-08-05 ODD SHOP -20.00"])
    sid = env.insert("statements", user_id="tester", account_id=acct, original_filename="s.pdf",
                     pdf_path=path, file_hash=path, status="processing")
    jobs._run("statement", sid, "tester", acct)
    models = [c["body"]["model"] for c in fake_ai.calls if c.get("body")]
    assert models == ["gpt-vision", "gpt-mini"]            # no warm-up; categorization on the text model
    with env.db() as c:
        usage = json.loads(c.execute("SELECT ai_usage FROM statements WHERE id=?", (sid,)).fetchone()[0])
    assert [u["purpose"] for u in usage] == ["Extraction (attempt 1)", "Categorization"]
    assert pl is not None


@pytest.mark.parametrize("url,external", [
    ("http://192.168.1.50:11434", False), ("http://10.0.0.5:1234/v1", False), ("http://localhost:11434", False),
    ("http://homeassistant.local:11434", False), ("http://gpu:11434", False), ("http://[::1]:11434", False),
    ("https://api.openai.com/v1", True), ("https://openrouter.ai/api/v1", True), ("http://8.8.8.8:80", True),
])
def test_what_counts_as_outside_the_home_network(env, url, external):
    from app import settings
    _set(env, ai_provider="openai", ai_url=url, ai_model="m")
    assert settings.ai_is_external() is external


def test_privacy_notice_on_home_settings_and_uploads(env, fake_ai):
    for page in ("dashboard", "settings", "uploads", "utilities?tab=upload", "tolls?tab=upload"):
        assert "Your documents go to" not in env.get(page).text, page
    _set(env, ai_provider="anthropic", ai_url="", ai_model="claude-sonnet-x", ai_api_key=KEY)
    for page in ("dashboard", "settings", "uploads", "utilities?tab=upload", "tolls?tab=upload"):
        text = env.get(page).text
        assert "Your documents go to Anthropic Claude" in text and "api.anthropic.com" in text, page
    assert "Your documents go to" in env.get("uploads", headers=WIFE).text   # everyone uploading sees it
