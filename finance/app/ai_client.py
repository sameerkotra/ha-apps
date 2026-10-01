"""One way to talk to the AI model, whichever provider is chosen on Admin → App settings
(SPEC.md section 22). Used by statement, utility-bill and toll extraction, categorization
and Write SQL.

Providers:
- "ollama": a local Ollama server, POST {address}/api/generate.
- "openai": anything that speaks the OpenAI chat-completions API (OpenAI, OpenRouter, Groq,
  LM Studio, vLLM, Ollama's /v1 …), POST {address}/chat/completions with a Bearer key.
- "anthropic": Anthropic's Messages API, POST {address}/v1/messages with x-api-key.

Every request reports its tokens and time to ai_usage. The access key is only ever put in a
request header: never in a URL, a log line, an error message or a page.
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

from . import ai_usage, settings

logger = logging.getLogger(__name__)

PROVIDERS = {"ollama": "Ollama", "openai": "OpenAI-compatible", "anthropic": "Anthropic Claude"}
DEFAULT_URLS = {"ollama": "", "openai": "https://api.openai.com/v1", "anthropic": "https://api.anthropic.com"}
ANTHROPIC_VERSION = "2023-06-01"
RETRY_STATUSES = {429, 500, 502, 503, 504, 529}
MAX_RETRIES = 3
MAX_RETRY_WAIT = 60


class AIError(Exception):
    """kind: unreachable | timeout | auth | rate_limit | not_found | bad_request | bad_response."""

    def __init__(self, message: str, kind: str = "error", raw: str = ""):
        super().__init__(message)
        self.kind = kind
        self.raw = raw


@dataclass
class Reply:
    text: str                  # the model's answer
    raw: str                   # the whole response body (debug pages)
    payload: dict = field(default_factory=dict)
    input_tokens: int | None = None
    output_tokens: int | None = None
    seconds: float | None = None   # as reported by the provider, else None


@dataclass
class Config:
    provider: str
    url: str
    model: str
    api_key: str = ""
    max_output_tokens: int = 16000

    @property
    def label(self) -> str:
        return PROVIDERS.get(self.provider, self.provider)


def current(url: str | None = None, model: str | None = None) -> Config:
    """The configuration on App settings; url/model override (a job passes what it started with)."""
    provider = settings.ai_provider()
    return Config(provider=provider, url=(url or settings.ai_url()).rstrip("/"), model=model or settings.ai_model(),
                  api_key=settings.get("ai_api_key"), max_output_tokens=settings.get_int("ai_max_output_tokens"))


def provider_label() -> str:
    return PROVIDERS.get(settings.ai_provider(), "AI")


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


def _request(cfg: Config, path: str, headers: dict, body: dict | None, timeout: float | None,
             method: str = "POST") -> tuple[int, str]:
    """One call with retries on rate limits / overload; network errors become AIError."""
    url = f"{cfg.url}{path}"
    for attempt in range(MAX_RETRIES + 1):
        try:
            status, text, resp_headers = _post(url, headers, body, timeout, method)
        except TimeoutError as e:
            raise AIError(f"{cfg.label} didn't answer within {int(timeout or 0)} s.", "timeout") from e
        except (urllib.error.URLError, ConnectionError, http.client.HTTPException, OSError) as e:
            reason = getattr(e, "reason", e)
            raise AIError(f"Couldn't reach {cfg.label} at {cfg.url}: {reason}", "unreachable") from e
        if status in RETRY_STATUSES and attempt < MAX_RETRIES:
            wait = _retry_after(resp_headers, attempt)
            logger.warning("%s answered %s; retrying in %.0f s", cfg.label, status, wait)
            _sleep(wait)
            continue
        return status, text
    return status, text  # pragma: no cover


def _retry_after(headers: dict, attempt: int) -> float:
    lowered = {k.lower(): v for k, v in (headers or {}).items()}
    try:
        return min(MAX_RETRY_WAIT, max(1.0, float(lowered.get("retry-after", ""))))
    except ValueError:
        return min(MAX_RETRY_WAIT, 5.0 * (3 ** attempt))   # 5, 15, 45 s


def _check(cfg: Config, status: int, text: str) -> None:
    if status < 400:
        return
    message = _error_text(text)
    if status in (401, 403):
        raise AIError(f"{cfg.label} refused the access key ({status}: {message}). "
                      "Check it on Admin → App settings.", "auth", text)
    if status == 429:
        raise AIError(f"{cfg.label} is rate-limiting requests (429: {message}). Try again later.", "rate_limit", text)
    if status == 404:
        raise AIError(f"{cfg.label} answered 404 ({message}): check the address and the model name.", "not_found", text)
    if status in RETRY_STATUSES:
        raise AIError(f"{cfg.label} is overloaded or failing ({status}: {message}). Try again later.", "rate_limit", text)
    raise AIError(f"{cfg.label} rejected the request ({status}: {message}).", "bad_request", text)


def _parse(cfg: Config, text: str) -> dict:
    try:
        data = json.loads(text)
    except ValueError:
        raise AIError(f"{cfg.label} returned something that isn't JSON.", "bad_response", text) from None
    if not isinstance(data, dict):
        raise AIError(f"{cfg.label} returned an unexpected answer.", "bad_response", text)
    return data


# ---- the three providers ---------------------------------------------------------------------------

def _ollama(cfg, prompt, images, want_json, timeout, max_tokens) -> Reply:
    body = {"model": cfg.model, "prompt": prompt, "stream": False}
    if images:
        body["images"] = images
    if want_json:
        body["format"] = "json"
    if max_tokens:
        body["options"] = {"num_predict": max_tokens}
    status, text = _request(cfg, "/api/generate", {}, body, timeout)
    _check(cfg, status, text)
    data = _parse(cfg, text)
    total = data.get("total_duration")
    return Reply(
        # A reasoning model can leave "response" empty and answer in "thinking".
        text=data.get("response") or data.get("thinking", "") or "", raw=text, payload=data,
        input_tokens=data.get("prompt_eval_count"), output_tokens=data.get("eval_count"),
        seconds=round(total / 1e9, 2) if isinstance(total, (int, float)) and total else None)


def _openai(cfg, prompt, images, want_json, timeout, max_tokens) -> Reply:
    content = [{"type": "text", "text": prompt}]
    content += [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}} for b64 in images or []]
    body = {"model": cfg.model, "messages": [{"role": "user", "content": content if images else prompt}]}
    if want_json:
        body["response_format"] = {"type": "json_object"}
    if max_tokens:
        body["max_tokens"] = max_tokens
    headers = {"Authorization": f"Bearer {cfg.api_key}"} if cfg.api_key else {}
    status, text = _request(cfg, "/chat/completions", headers, body, timeout)
    if status == 400 and ("response_format" in text or "max_tokens" in text):
        # Some compatible servers don't know JSON mode; newer OpenAI models want max_completion_tokens.
        body.pop("response_format", None)
        if "max_tokens" in body and "max_completion_tokens" in text:
            body["max_completion_tokens"] = body.pop("max_tokens")
        status, text = _request(cfg, "/chat/completions", headers, body, timeout)
    _check(cfg, status, text)
    data = _parse(cfg, text)
    try:
        message = data["choices"][0]["message"]
        answer = message.get("content") or message.get("reasoning_content") or ""
    except (KeyError, IndexError, TypeError):
        raise AIError(f"{cfg.label} returned no answer.", "bad_response", text) from None
    if isinstance(answer, list):   # content parts
        answer = "".join(p.get("text", "") for p in answer if isinstance(p, dict))
    usage = data.get("usage") or {}
    return Reply(text=answer, raw=text, payload=data, input_tokens=usage.get("prompt_tokens"),
                 output_tokens=usage.get("completion_tokens"))


def _anthropic(cfg, prompt, images, want_json, timeout, max_tokens) -> Reply:
    content = [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}}
               for b64 in images or []]
    content.append({"type": "text", "text": prompt + ("\n\nAnswer with the JSON object only." if want_json else "")})
    limit = max_tokens or cfg.max_output_tokens or 16000
    body = {"model": cfg.model, "max_tokens": limit, "messages": [{"role": "user", "content": content}]}
    headers = {"x-api-key": cfg.api_key, "anthropic-version": ANTHROPIC_VERSION}
    status, text = _request(cfg, "/v1/messages", headers, body, timeout)
    if status == 400 and "max_tokens" in text:
        # The model allows fewer output tokens than asked: use the limit it names, else 8192.
        m = re.search(r"(?:maximum|max)[^0-9]{0,60}(\d{3,6})", _error_text(text))
        body["max_tokens"] = int(m.group(1)) if m and int(m.group(1)) < limit else min(limit, 8192)
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
    return Reply(text=answer, raw=text, payload=data, input_tokens=input_tokens, output_tokens=usage.get("output_tokens"))


_CALLS = {"ollama": _ollama, "openai": _openai, "anthropic": _anthropic}


def generate(prompt: str, *, url: str | None = None, model: str | None = None, images: list[str] | None = None,
             want_json: bool = False, timeout: float | None = None, max_tokens: int | None = None,
             purpose: str = "AI request", cfg: Config | None = None) -> Reply:
    """Ask the model once. images: base64 PNGs. Raises AIError; records usage either way."""
    cfg = cfg or current(url, model)
    started = time.monotonic()
    if not cfg.url or not cfg.model:
        raise AIError("AI isn't set up: the admin adds the provider, address and model on Admin → App settings.",
                      "not_configured")
    if cfg.provider == "anthropic" and not cfg.api_key:
        raise AIError("Anthropic Claude needs an access key: add it on Admin → App settings.", "auth")
    try:
        reply = _CALLS.get(cfg.provider, _ollama)(cfg, prompt, images, want_json, timeout, max_tokens)
    except AIError as e:
        ai_usage.add(purpose, cfg.model, None, None, time.monotonic() - started, error=str(e), provider=cfg.provider)
        raise
    ai_usage.add(purpose, cfg.model, reply.input_tokens, reply.output_tokens,
                 reply.seconds if reply.seconds is not None else time.monotonic() - started, provider=cfg.provider)
    return reply


def list_models(cfg: Config, timeout: float = 10) -> list[str]:
    """The models the provider offers (Test connection). Raises AIError."""
    if cfg.provider == "ollama":
        status, text = _request(cfg, "/api/tags", {}, None, timeout, method="GET")
        _check(cfg, status, text)
        return [m.get("name", "") for m in _parse(cfg, text).get("models", []) if isinstance(m, dict)]
    if cfg.provider == "anthropic":
        if not cfg.api_key:
            raise AIError("Enter the access key first.", "auth")
        headers = {"x-api-key": cfg.api_key, "anthropic-version": ANTHROPIC_VERSION}
        status, text = _request(cfg, "/v1/models?limit=100", headers, None, timeout, method="GET")
    else:
        headers = {"Authorization": f"Bearer {cfg.api_key}"} if cfg.api_key else {}
        status, text = _request(cfg, "/models", headers, None, timeout, method="GET")
    _check(cfg, status, text)
    return [m.get("id", "") for m in _parse(cfg, text).get("data", []) if isinstance(m, dict)]


def privacy() -> dict | None:
    """For the privacy notice (Home, App settings, upload pages): None while the AI is on the home network."""
    if not settings.ai_is_external():
        return None
    return {"label": provider_label(), "host": settings.ai_host(), "sql": True}
