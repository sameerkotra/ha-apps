"""One way to talk to the AI model, whichever provider is chosen on
Admin → App settings. Used by the AI estimate ("✨ Estimate with AI"), the
AI Assistant chat, the Ollama warm-up, /api/ai/status and Test connection.
Nothing else in the app builds HTTP requests to a model.

Providers:
- "ollama": an Ollama server, POST {address}/api/generate.
- "openai": anything that speaks the OpenAI chat-completions API (OpenAI,
  OpenRouter, Groq, LM Studio, vLLM, Ollama's /v1 …), POST
  {address}/chat/completions, with a Bearer key only when one is set.
- "anthropic": Anthropic's Messages API, POST {address}/v1/messages with
  x-api-key.

Provider, address, model and key are read from App settings at each request,
so a change applies to the next request without a restart. The access key is
only ever put in a request header: never in a URL, a log line, an error
message or a page. Only standard-library urllib is used; callers run the
blocking functions in the threadpool.
"""
from __future__ import annotations

import http.client
import json
import logging
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from starlette.concurrency import run_in_threadpool

from . import settings

logger = logging.getLogger("ai")

PROVIDERS = settings.PROVIDERS
ANTHROPIC_VERSION = "2023-06-01"
RETRY_STATUSES = {429, 500, 502, 503, 504, 529}
MAX_RETRIES = 3
MAX_RETRY_WAIT = 60

WARMUP_TIMEOUT = 120
QUERY_TIMEOUT = 300
TEST_TIMEOUT = 10
# Ollama keeps a model loaded for 5 minutes after its last use by default
# (keep_alive), so a model that answered anything within the last few minutes
# is still loaded and doesn't need a separate "hi" warm-up first.
WARM_FOR_SECONDS = 4 * 60


class AIError(Exception):
    """kind: not_configured | unreachable | timeout | auth | rate_limit | not_found |
    bad_request | bad_response."""

    def __init__(self, message: str, kind: str = "error"):
        super().__init__(message)
        self.kind = kind


@dataclass
class Reply:
    text: str                  # the model's answer
    payload: dict = field(default_factory=dict)
    input_tokens: int | None = None
    output_tokens: int | None = None
    seconds: float | None = None


@dataclass
class Config:
    provider: str
    url: str
    model: str
    api_key: str = field(default="", repr=False)     # never in a repr / log line
    max_tokens: int = 4096

    @property
    def label(self) -> str:
        return PROVIDERS.get(self.provider, "AI")


def current() -> Config:
    """The configuration on App settings, read now."""
    return Config(provider=settings.ai_provider(), url=settings.ai_url(), model=settings.get("ai_model"),
                  api_key=settings.get("ai_api_key"), max_tokens=settings.get("ai_max_tokens"))


# ---- state shown on the AI Assistant page ----------------------------------------------------------

_last_ok: float | None = None
_last_error: str | None = None
_last_request: dict | None = None


def forget_state() -> None:
    """Called when the AI settings change (or a restore swaps them): the old
    "warm" timestamp, last error and last request were about another setup."""
    global _last_ok, _last_error, _last_request
    _last_ok = None
    _last_error = None
    _last_request = None


def is_warm() -> bool:
    return _last_ok is not None and (time.time() - _last_ok) < WARM_FOR_SECONDS


def _record(purpose: str, cfg: Config, reply: Reply | None, seconds: float, error: str = "") -> None:
    """Remember the last request (tokens and time) and log it — never the key."""
    global _last_ok, _last_error, _last_request
    _last_request = {
        "purpose": purpose, "model": cfg.model,
        "input_tokens": reply.input_tokens if reply else None,
        "output_tokens": reply.output_tokens if reply else None,
        "seconds": round(seconds, 2), "error": error[:300] if error else None, "at": time.time(),
    }
    if reply is not None:
        _last_ok, _last_error = time.time(), None
        logger.info("%s: %s %s, %s tokens in / %s out, %.1f s", purpose, cfg.label, cfg.model,
                    reply.input_tokens, reply.output_tokens, seconds)
    else:
        _last_error = error
        logger.warning("%s failed (%s %s): %s", purpose, cfg.label, cfg.model, error)


# ---- HTTP -----------------------------------------------------------------------------------------

def _sleep(seconds: float) -> None:        # tests replace this
    time.sleep(seconds)


def _post(url: str, headers: dict, body: dict | None, timeout: float | None, method: str = "POST") -> tuple[int, str, dict]:
    """(status, body text, response headers). HTTP errors come back as a status, not an exception."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **headers}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace"), dict(resp.headers)
    except urllib.error.HTTPError as e:
        try:
            text = e.read().decode("utf-8", "replace")
        except Exception:
            text = ""
        return e.code, text, dict(e.headers or {})


def _scrub(cfg: Config, text: str) -> str:
    """Belt and braces: a provider echoing the key back never gets it onto a page."""
    return text.replace(cfg.api_key, "…") if cfg.api_key else text


def _error_text(text: str) -> str:
    """The provider's own error message out of a JSON error body, else the start of the body."""
    try:
        data = json.loads(text)
    except ValueError:
        return text.strip()[:300]
    err = data.get("error", data) if isinstance(data, dict) else data
    if isinstance(err, dict):
        return str(err.get("message") or err.get("type") or err)[:300]
    return str(err)[:300]


def _retry_after(headers: dict, attempt: int) -> float:
    lowered = {k.lower(): v for k, v in (headers or {}).items()}
    try:
        return min(MAX_RETRY_WAIT, max(1.0, float(lowered.get("retry-after", ""))))
    except ValueError:
        return min(MAX_RETRY_WAIT, 5.0 * (3 ** attempt))   # 5, 15, 45 s


def _request(cfg: Config, path: str, headers: dict, body: dict | None, timeout: float | None,
             method: str = "POST") -> tuple[int, str]:
    """One call with retries on rate limits / overload; network errors become AIError."""
    url = f"{cfg.url}{path}"
    for attempt in range(MAX_RETRIES + 1):
        try:
            status, text, resp_headers = _post(url, headers, body, timeout, method)
        except TimeoutError as e:
            raise AIError(f"{cfg.label} at {cfg.url} didn't answer within {int(timeout or 0)} s.", "timeout") from e
        except (urllib.error.URLError, ConnectionError, http.client.HTTPException, OSError) as e:
            reason = getattr(e, "reason", e)
            if isinstance(reason, TimeoutError) or "timed out" in str(reason):
                raise AIError(f"{cfg.label} at {cfg.url} didn't answer within {int(timeout or 0)} s.", "timeout") from e
            raise AIError(_scrub(cfg, f"Couldn't reach {cfg.label} at {cfg.url}: {reason}. "
                                      "Check the address on Admin → App settings and that the server is running."),
                          "unreachable") from e
        if status in RETRY_STATUSES and attempt < MAX_RETRIES:
            wait = _retry_after(resp_headers, attempt)
            logger.warning("%s answered %s; retrying in %.0f s", cfg.label, status, wait)
            _sleep(wait)
            continue
        return status, text
    return status, text  # pragma: no cover


def _check(cfg: Config, status: int, text: str) -> None:
    if status < 400:
        return
    message = _scrub(cfg, _error_text(text))
    if status in (401, 403):
        raise AIError(f"{cfg.label} refused the access key ({status}: {message}). "
                      "Check it on Admin → App settings.", "auth")
    if status == 429:
        raise AIError(f"{cfg.label} is rate-limiting requests (429: {message}). Try again later.", "rate_limit")
    if status == 404:
        raise AIError(f"{cfg.label} answered 404 ({message}): check the address and the model name "
                      "on Admin → App settings.", "not_found")
    if status in RETRY_STATUSES:
        raise AIError(f"{cfg.label} is overloaded or failing ({status}: {message}). Try again later.", "rate_limit")
    raise AIError(f"{cfg.label} rejected the request ({status}: {message}).", "bad_request")


def _parse(cfg: Config, text: str) -> dict:
    try:
        data = json.loads(text)
    except ValueError:
        raise AIError(f"{cfg.label} returned something that isn't JSON — is the address right?", "bad_response") from None
    if not isinstance(data, dict):
        raise AIError(f"{cfg.label} returned an unexpected answer.", "bad_response")
    return data


def _auth_headers(cfg: Config) -> dict:
    if cfg.provider == "anthropic":
        return {"x-api-key": cfg.api_key, "anthropic-version": ANTHROPIC_VERSION}
    if cfg.provider == "openai" and cfg.api_key:
        return {"Authorization": f"Bearer {cfg.api_key}"}
    return {}


# ---- the three providers ---------------------------------------------------------------------------

def _ollama(cfg, prompt, system, want_json, temperature, timeout) -> Reply:
    body = {"model": cfg.model, "prompt": prompt, "stream": False}
    if system:
        body["system"] = system
    if want_json:
        body["format"] = "json"
    if temperature is not None:
        body["options"] = {"temperature": temperature}
    status, text = _request(cfg, "/api/generate", {}, body, timeout)
    _check(cfg, status, text)
    data = _parse(cfg, text)
    total = data.get("total_duration")
    return Reply(
        # A reasoning model can leave "response" empty and answer in "thinking".
        text=data.get("response") or data.get("thinking", "") or "", payload=data,
        input_tokens=data.get("prompt_eval_count"), output_tokens=data.get("eval_count"),
        seconds=round(total / 1e9, 2) if isinstance(total, (int, float)) and total else None)


def _openai(cfg, prompt, system, want_json, temperature, timeout) -> Reply:
    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    body = {"model": cfg.model, "messages": messages}
    if want_json:
        body["response_format"] = {"type": "json_object"}
    if temperature is not None:
        body["temperature"] = temperature
    headers = _auth_headers(cfg)
    status, text = _request(cfg, "/chat/completions", headers, body, timeout)
    if status == 400 and ("response_format" in text or "temperature" in text):
        # Some compatible servers don't know JSON mode; some models only allow their default temperature.
        body.pop("response_format", None)
        body.pop("temperature", None)
        status, text = _request(cfg, "/chat/completions", headers, body, timeout)
    _check(cfg, status, text)
    data = _parse(cfg, text)
    try:
        message = data["choices"][0]["message"]
        answer = message.get("content") or message.get("reasoning_content") or ""
    except (KeyError, IndexError, TypeError):
        raise AIError(f"{cfg.label} returned no answer.", "bad_response") from None
    if isinstance(answer, list):   # content parts
        answer = "".join(p.get("text", "") for p in answer if isinstance(p, dict))
    usage = data.get("usage") or {}
    return Reply(text=answer, payload=data, input_tokens=usage.get("prompt_tokens"),
                 output_tokens=usage.get("completion_tokens"))


def _anthropic(cfg, prompt, system, want_json, temperature, timeout) -> Reply:
    text_in = prompt + ("\n\nAnswer with the JSON object only." if want_json else "")
    limit = cfg.max_tokens or 4096
    body = {"model": cfg.model, "max_tokens": limit,
            "messages": [{"role": "user", "content": [{"type": "text", "text": text_in}]}]}
    if system:
        body["system"] = system
    if temperature is not None:
        body["temperature"] = temperature
    headers = _auth_headers(cfg)
    status, text = _request(cfg, "/v1/messages", headers, body, timeout)
    if status == 400 and "max_tokens" in text:
        # The model allows fewer output tokens than asked: use the limit it names, else 4096.
        smaller = [int(n) for n in re.findall(r"\d{3,6}", _error_text(text)) if 0 < int(n) < limit]
        body["max_tokens"] = max(smaller) if smaller else min(limit, 4096)
        if body["max_tokens"] != limit:
            status, text = _request(cfg, "/v1/messages", headers, body, timeout)
    _check(cfg, status, text)
    data = _parse(cfg, text)
    answer = "".join(p.get("text", "") for p in data.get("content", []) if isinstance(p, dict) and p.get("type") == "text")
    usage = data.get("usage") or {}
    input_tokens = usage.get("input_tokens")
    if input_tokens is not None:
        input_tokens += (usage.get("cache_read_input_tokens") or 0) + (usage.get("cache_creation_input_tokens") or 0)
    if data.get("stop_reason") == "max_tokens":
        logger.warning("Claude stopped at the output limit (%s tokens)", body["max_tokens"])
    return Reply(text=answer, payload=data, input_tokens=input_tokens, output_tokens=usage.get("output_tokens"))


_CALLS = {"ollama": _ollama, "openai": _openai, "anthropic": _anthropic}


def check_configured(cfg: Config) -> None:
    if cfg.provider not in PROVIDERS or not cfg.url or not cfg.model:
        raise AIError(settings.NOT_SET_UP, "not_configured")
    if cfg.provider == "anthropic" and not cfg.api_key:
        raise AIError("Anthropic Claude needs an access key — an admin can add it in Admin → App settings.",
                      "not_configured")


def generate(prompt: str, *, system: str | None = None, want_json: bool = False, temperature: float | None = None,
             timeout: float | None = QUERY_TIMEOUT, purpose: str = "AI request", cfg: Config | None = None) -> Reply:
    """Ask the model once. Raises AIError (a readable message, never the key)."""
    cfg = cfg or current()
    check_configured(cfg)
    started = time.monotonic()
    try:
        reply = _CALLS[cfg.provider](cfg, prompt, system, want_json, temperature, timeout)
    except AIError as e:
        _record(purpose, cfg, None, time.monotonic() - started, error=str(e))
        raise
    _record(purpose, cfg, reply, reply.seconds if reply.seconds is not None else time.monotonic() - started)
    return reply


def list_models(cfg: Config, timeout: float = TEST_TIMEOUT) -> list[str]:
    """The models the provider offers (Test connection). Generates nothing. Raises AIError."""
    if cfg.provider == "ollama":
        status, text = _request(cfg, "/api/tags", {}, None, timeout, method="GET")
        _check(cfg, status, text)
        return [m.get("name") or m.get("model") or "" for m in _parse(cfg, text).get("models", []) if isinstance(m, dict)]
    if cfg.provider == "anthropic":
        if not cfg.api_key:
            raise AIError("Enter the access key first.", "auth")
        status, text = _request(cfg, "/v1/models?limit=100", _auth_headers(cfg), None, timeout, method="GET")
    else:
        status, text = _request(cfg, "/models", _auth_headers(cfg), None, timeout, method="GET")
    _check(cfg, status, text)
    return [m.get("id", "") for m in _parse(cfg, text).get("data", []) if isinstance(m, dict)]


def test_connection(cfg: Config) -> dict:
    """The "Test connection" button: ask the provider which models it offers.
    Quick, loads nothing, costs nothing. Never raises."""
    base = {"provider": cfg.provider, "label": cfg.label, "url": cfg.url, "model": cfg.model}
    if cfg.provider not in PROVIDERS:
        return {**base, "ok": False, "message": "Choose a provider first."}
    if not cfg.url:
        return {**base, "ok": False, "message": "Enter the address first."}
    try:
        names = [n for n in list_models(cfg) if n]
    except AIError as err:
        return {**base, "ok": False, "message": str(err)}
    shown = ", ".join(names[:40]) + (f" … and {len(names) - 40} more" if len(names) > 40 else "")
    found = bool(cfg.model) and (cfg.model in names or f"{cfg.model}:latest" in names)
    result = {**base, "models": names[:100], "modelCount": len(names), "modelFound": found}
    if not cfg.model:
        return {**result, "ok": True, "message": f"{cfg.label} answered. Models: {shown or 'none listed'}."}
    if found:
        return {**result, "ok": True, "message": f"{cfg.label} answered and offers {cfg.model}."}
    if not names:
        return {**result, "ok": True,
                "message": f"{cfg.label} answered (it doesn't list its models, so {cfg.model} couldn't be checked)."}
    return {**result, "ok": False, "message": f"{cfg.label} answered but doesn't list {cfg.model}. Models: {shown}."}


# ---- what the app asks ------------------------------------------------------------------------------

def _sync_warmup() -> bool:
    """Ollama only: a tiny "hi" loads the model into memory. For a cloud
    provider it would only cost money, so it is skipped (returns True)."""
    cfg = current()
    if cfg.provider != "ollama":
        return True
    try:
        generate("hi", timeout=WARMUP_TIMEOUT, purpose="Warm-up", cfg=cfg)
        return True
    except AIError:
        return False


async def warmup() -> bool:
    return await run_in_threadpool(_sync_warmup)


async def ensure_warm() -> None:
    """Warm the Ollama model only if it hasn't answered anything recently."""
    if settings.ai_provider() == "ollama" and settings.ai_configured() and not is_warm():
        await warmup()


def _extract_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("no JSON object in the model's answer")
    data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("the model's answer isn't a JSON object")
    return data


ESTIMATE_PROMPT = """You are a nutrition estimation assistant. A user will describe a food or meal,
and you must estimate its nutrition facts.

Respond with ONLY a single JSON object, no markdown, no commentary, no code fences.
Use this exact schema:
{{
  "food_name": string,
  "serving_size": number,
  "serving_unit": string (e.g. "g", "cup", "serving", "piece"),
  "calories": number,
  "protein": number (grams),
  "carbs": number (grams),
  "fat": number (grams),
  "note": string (short assumption you made, e.g. "assumed 100g cooked")
}}

Food description: "{description}"

JSON:"""

CHAT_SYSTEM = ("You are a helpful, concise nutrition and fitness assistant embedded in a "
               "personal calorie tracking app. Keep answers short and practical.")


def _sync_estimate(description: str) -> dict:
    reply = generate(ESTIMATE_PROMPT.format(description=description), want_json=True, temperature=0.2,
                     purpose="AI estimate")
    try:
        return _extract_json(reply.text)
    except ValueError as e:
        raise AIError(f"The model's answer couldn't be read as nutrition facts ({e}). Try again or "
                      "describe the food differently.", "bad_response") from None


async def estimate(description: str) -> dict:
    return await run_in_threadpool(_sync_estimate, description)


def _sync_chat(message: str) -> str:
    return generate(message, system=CHAT_SYSTEM, purpose="AI chat").text.strip()


async def chat(message: str) -> str:
    return await run_in_threadpool(_sync_chat, message)


def privacy() -> dict | None:
    """For the privacy notice: None while the AI is on the home network (or not set up)."""
    if not settings.ai_is_external():
        return None
    return {"label": PROVIDERS.get(settings.ai_provider(), "AI"), "host": settings.ai_host()}


def status() -> dict:
    """GET /api/ai/status — for everyone (no key, ever)."""
    cfg = current()
    configured = settings.ai_configured()
    return {
        "configured": configured,
        "message": None if configured else (
            "Anthropic Claude needs an access key — an admin can add it in Admin → App settings."
            if cfg.provider == "anthropic" and cfg.url and cfg.model and not cfg.api_key else settings.NOT_SET_UP),
        "provider": cfg.provider,
        "providerLabel": cfg.label if cfg.provider in PROVIDERS else None,
        "url": cfg.url,
        "model": cfg.model,
        "warmup": cfg.provider == "ollama",
        "last_ok": _last_ok,
        "last_error": _last_error,
        "last_request": _last_request,
        "privacy": privacy(),
    }
