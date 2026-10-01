"""Plain English → SQL on the Query tab (SPEC.md section 19.7). Ollama is faked."""
import json
import re
import time

import pytest

OTHER = {"x-remote-user-id": "other", "x-remote-user-name": "Other"}


@pytest.fixture
def fake_ollama(monkeypatch):
    from app import sql_assist
    sql_assist.reset()
    calls = []
    answers = []

    def generate(prompt, *, timeout, as_json=False, num_predict=None, purpose=""):
        calls.append(prompt)
        if prompt == "hi":
            if answers and answers[0] == "hi-fails":
                answers.pop(0)
                raise sql_assist.AssistError("URLError: connection refused")
            return "Hello!"
        return json.dumps(answers.pop(0))
    monkeypatch.setattr(sql_assist, "_generate", generate)
    return calls, answers


def _ask(env, question="spending by category"):
    return env.post("sql-assist-start", data={"ai_question": question, "ai_vars": "1"}).text


def _finish(env, text, timeout=10):
    deadline = time.time() + timeout
    while "hx-trigger=\"every 2s\"" in text and time.time() < deadline:
        job = re.search(r'sql-assist-status\?job=([\w-]+)', text).group(1)
        time.sleep(0.05)
        text = env.get(f"sql-assist-status?job={job}").text
    return text


def test_writes_sql_with_table_info_after_saying_hi(env, fake_ollama):
    calls, answers = fake_ollama
    env.account("Everyday")
    answers.append({"sql": "SELECT category, ROUND(-SUM(amount), 2) AS spent FROM transactions GROUP BY category",
                    "explanation": "Money out per category."})
    done = _finish(env, _ask(env))
    assert calls[0] == "hi"
    prompt = calls[1]
    for needle in ("transactions_with_account", "utility_bills", "negative = money out", "YYYY-MM-DD",
                   ":period_start", "spending by category", "Everyday"):
        assert needle in prompt, needle
    assert "known_users" not in prompt and "saved_reports" not in prompt
    assert 'data-sql="SELECT category, ROUND(-SUM(amount), 2) AS spent FROM transactions GROUP BY category"' in done
    assert "Money out per category." in done and "Undo" in done


def test_waits_for_pdf_parsing_and_offers_retry(env, fake_ollama):
    calls, answers = fake_ollama
    from app import jobs
    assert jobs.OLLAMA_LOCK.acquire(blocking=False)
    try:
        done = _finish(env, _ask(env))
    finally:
        jobs.OLLAMA_LOCK.release()
    assert "The AI is busy" in done and "Try again" in done and calls == []
    answers.append({"sql": "SELECT 1 AS one", "explanation": ""})
    assert 'data-sql="SELECT 1 AS one"' in _finish(env, _ask(env))


def test_hi_failure_is_reported_with_retry(env, fake_ollama):
    calls, answers = fake_ollama
    answers.append("hi-fails")
    done = _finish(env, _ask(env))
    assert "didn&#39;t answer" in done and "Try again" in done and calls == ["hi"]


def test_rejected_sql_goes_back_once_for_a_fix(env, fake_ollama):
    calls, answers = fake_ollama
    answers.append({"sql": "SELECT no_such_col FROM transactions", "explanation": "first"})
    answers.append({"sql": "SELECT COUNT(*) AS n FROM transactions", "explanation": "fixed"})
    done = _finish(env, _ask(env))
    assert "no such column: no_such_col" in calls[2]
    assert 'data-sql="SELECT COUNT(*) AS n FROM transactions"' in done and "fixed" in done


def test_refuses_writes_and_is_admin_only(env, fake_ollama):
    calls, answers = fake_ollama
    answers.append({"sql": "DELETE FROM transactions", "explanation": ""})
    answers.append({"sql": "DELETE FROM transactions", "explanation": ""})
    done = _finish(env, _ask(env))
    assert "still reports a problem" in done
    assert env.post("sql-assist-start", data={"ai_question": "x"}, headers=OTHER).status_code == 403
    assert env.get("sql-assist-status?job=x", headers=OTHER).status_code == 403


def test_empty_question(env, fake_ollama):
    assert "Describe what you want" in env.post("sql-assist-start", data={"ai_question": "  "}).text
