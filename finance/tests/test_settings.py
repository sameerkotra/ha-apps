"""Admin → App settings and AI usage on the debug pages (SPEC.md sections 21–22)."""
import io
import json

import pytest

WIFE = {"x-remote-user-id": "wife", "x-remote-user-name": "Wife"}
PAYLOAD = {"response": "{}", "prompt_eval_count": 1234, "eval_count": 56, "total_duration": 2_500_000_000}


def _save(env, **values):
    data = {"ai_url": "http://ollama.invalid:11434", "ai_model": "test-model", "show_ai_usage": "1",
            "price_input_per_million": "0", "price_output_per_million": "0",
            "feature_utilities": "1", "feature_tolls": "1"}
    data.update(values)
    return env.post("settings", data={k: v for k, v in data.items() if v is not None})


@pytest.fixture
def fake_model(monkeypatch):
    """Every AI request answers PAYLOAD (an Ollama reply), without the network."""
    from app import ai_client
    sent = []

    def post(url, headers, body, timeout, method="POST"):
        sent.append((url, (body or {}).get("model")))
        return 200, json.dumps(PAYLOAD), {}
    monkeypatch.setattr(ai_client, "_post", post)
    return sent


def _statement(env):
    acct = env.account()
    path = env.pdf(["Previous Balance $100.00", "New Balance $80.00", "2026-08-05 SHOP -20.00"])
    sid = env.insert("statements", user_id="tester", account_id=acct, original_filename="s.pdf",
                     pdf_path=path, file_hash=path, status="processing")
    return acct, sid


def test_admin_only(env):
    assert env.get("settings", headers=WIFE).status_code == 403
    assert _save(env, ai_model="x").status_code == 303
    assert env.post("settings", data={"ai_model": "evil"}, headers=WIFE).status_code == 403
    assert env.post("settings-test", data={"ai_url": "http://x"}, headers=WIFE).status_code == 403
    assert 'href="settings' in env.get("accounts").text          # More panel, under Admin
    storage = env.get("admin-storage").text
    assert ">App settings</a>" in storage and ">Settings</a>" not in env.get("accounts").text.split("section-tabs", 1)[-1][:600]
    page = env.get("settings").text
    assert "<h1>App settings</h1>" in page and 'aria-current="page">App settings' in page
    env.get("accounts", headers=WIFE)
    assert 'href="settings' not in env.get("accounts", headers=WIFE).text
    from app import settings
    assert settings.ai_model() == "x"


def test_seeded_from_addon_options_once(make_env):
    env = make_env()
    page = env.get("settings").text
    assert 'value="http://ollama.invalid:11434"' in page and "Copied from the old app options" in page
    assert _save(env, ai_url="http://gpu-box:11434/", ai_model="llava:13b").status_code == 303
    page = env.get("settings?saved=1").text
    assert 'value="http://gpu-box:11434"' in page and "Saved." in page and "by tester" in page
    # a restart with different app options keeps what was set on the page
    env2 = make_env(tmp=env.dir, OLLAMA_URL="http://other:1", OLLAMA_MODEL="other")
    from app import settings
    assert settings.ai_url() == "http://gpu-box:11434" and settings.ai_model() == "llava:13b"
    assert 'value="llava:13b"' in env2.get("settings").text


def test_validation_saves_nothing(env):
    r = _save(env, ai_url="javascript:alert(1)", price_input_per_million="-1", ai_model="new")
    assert r.status_code == 200 and "Nothing was saved" in r.text
    assert "must start with http://" in r.text and "can&#39;t be negative" in r.text
    assert 'value="new"' in r.text          # what was typed stays in the form
    from app import settings
    assert settings.ai_model() == "test-model"
    assert "must be a number" in _save(env, price_output_per_million="abc").text


def test_change_applies_to_next_job_and_usage_is_stored(env, fake_model, monkeypatch):
    from app import jobs
    from app.parser import pipeline as pl, vision
    from app.parser.vision import ParsedTransaction, VisionResult
    seen = {}

    def fake(pdf, url, model, on_step=None, account_type=None, notes=""):
        seen.update(url=url, model=model)
        vision.warmup_model(url, model)
        vision._ask(url, model, "x", timeout=5, purpose="Extraction (attempt 1)")
        return VisionResult(transactions=[ParsedTransaction("2026-08-05", -20.0, "SHOP")], raw_response="{}")
    monkeypatch.setattr(pl, "extract_vision", fake)
    assert _save(env, ai_url="http://new-host:11434", ai_model="llava:13b",
                 price_input_per_million="3", price_output_per_million="15").status_code == 303
    acct, sid = _statement(env)
    jobs._run("statement", sid, "tester", acct)            # no restart in between
    assert seen == {"url": "http://new-host:11434", "model": "llava:13b"}
    assert fake_model[-1] == ("http://new-host:11434/api/generate", "llava:13b")
    with env.db() as c:
        usage = json.loads(c.execute("SELECT ai_usage FROM statements WHERE id=?", (sid,)).fetchone()[0])
    # the pipeline's categorization asks the model too
    assert [u["purpose"] for u in usage] == ["Warm-up", "Extraction (attempt 1)", "Categorization"]
    assert not usage[2]["error"] and usage[2]["input_tokens"] == 1234
    assert usage[1]["input_tokens"] == 1234 and usage[1]["output_tokens"] == 56 and usage[1]["seconds"] == 2.5
    page = env.get(f"statement-debug?id={sid}").text
    assert "AI usage — last processing run" in page
    assert "1,234" in page and "3,702" in page and "llava:13b" in page
    assert "$0.0045" in page and "$0.0136" in page        # (1234*3 + 56*15)/1e6 each, three in total
    # switched off: not shown
    assert _save(env, show_ai_usage=None).status_code == 303
    assert "AI usage — last processing run" not in env.get(f"statement-debug?id={sid}").text


def test_no_prices_means_no_cost(env, fake_model):
    from app import ai_usage
    with ai_usage.recording() as calls:
        from app.parser import vision
        vision._ask("http://x", "m", "hi", timeout=5, purpose="Warm-up")
    s = ai_usage.summary(ai_usage.to_json(calls))
    assert s["priced"] is False and s["cost"] is None and s["input_tokens"] == 1234


def test_failed_request_is_recorded(env):
    from app import ai_usage
    from app.categorize import categorize_via_llm
    from app.parser import vision
    with ai_usage.recording() as calls:
        categorize_via_llm("http://x", "m", ["SOMETHING ODD"])
        with pytest.raises(vision.VisionExtractionError):
            vision._ask("http://x", "m", "hi", timeout=5, purpose="Warm-up")
    assert [c["purpose"] for c in calls] == ["Categorization", "Warm-up"]
    assert all(c["error"] and c["input_tokens"] is None for c in calls)
    # nothing records outside recording()
    ai_usage.add("x", "m", 1, 2, 1)


def test_debug_resend_shows_its_usage(env, fake_model, monkeypatch):
    from app.parser import vision
    from app.routes import upload

    def fake_debug(pdf, url, model, prompt):
        vision._ask(url, model, "p", timeout=None, purpose="Debug re-send")
        return 1.5, "{}"
    monkeypatch.setattr(upload, "debug_call", fake_debug)
    _acct, sid = _statement(env)
    page = env.post(f"statement-debug-resend?id={sid}").text
    assert "AI usage — this re-send" in page and "Debug re-send" in page and "1,234" in page


def test_sql_assist_uses_settings(env, monkeypatch):
    from app import sql_assist
    urls = []

    class Resp(io.BytesIO):
        status, headers = 200, {}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def urlopen(req, timeout=None):
        urls.append((req.full_url, json.loads(req.data)["model"]))
        return Resp(json.dumps(dict(PAYLOAD, response="hi")).encode())
    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    _save(env, ai_url="http://sql-host:11434", ai_model="coder")
    with __import__("app.ai_usage", fromlist=["x"]).recording() as calls:
        assert sql_assist._generate("hi", timeout=5, purpose="Hi") == "hi"
    assert urls == [("http://sql-host:11434/api/generate", "coder")]
    assert calls[0]["purpose"] == "Hi" and calls[0]["output_tokens"] == 56
    _save(env, ai_model="")
    job, why = sql_assist.start("tester", "tester", "anything", True)
    assert job is None and "Admin → App settings" in why


def test_connection_test(env, monkeypatch):
    r = env.post("settings-test", data={"ai_url": "http://nowhere:1", "ai_model": "m"})
    assert "reach Ollama at http://nowhere:1" in r.text

    class Resp(io.BytesIO):
        status, headers = 200, {}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    tags = {"models": [{"name": "qwen3.5:122b"}, {"name": "llava:latest"}]}
    monkeypatch.setattr("urllib.request.urlopen", lambda url, timeout=None: Resp(json.dumps(tags).encode()))
    assert "offers qwen3.5:122b" in env.post("settings-test", data={"ai_url": "http://h:1", "ai_model": "qwen3.5:122b"}).text
    assert "offers llava" in env.post("settings-test", data={"ai_url": "http://h:1", "ai_model": "llava"}).text
    r = env.post("settings-test", data={"ai_url": "http://h:1", "ai_model": "missing"})
    assert "doesn" in r.text and "qwen3.5:122b, llava:latest" in r.text
    assert "must start with http" in env.post("settings-test", data={"ai_url": "ftp://h"}).text


def test_ai_is_not_an_addon_option():
    import os
    text = open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.yaml")).read()
    assert "ollama_url" not in text and "ollama_model" not in text and "ai_url" not in text


def test_upload_pages_say_when_ai_is_missing(env):
    page = env.get("uploads").text
    assert "set up yet" not in page
    _save(env, ai_model="")
    for url in ("uploads", "utilities?tab=upload", "tolls?tab=upload"):
        page = env.get(url).text
        assert "set up yet, so PDFs can" in page and 'href="settings"' in page, url
    assert "set up yet" in env.get("uploads", headers=WIFE).text
    assert "Ask the admin" in env.get("uploads", headers=WIFE).text
    assert "choose the provider and enter its address" in env.get("settings").text
