"""Talking to an AI model: Ollama, any OpenAI-compatible server, or Anthropic Claude (shared by the
household apps: common/python/ai_client.py, copied into each app's app/common/ by
tools/sync_common.py). Standard library only; nothing is imported from the app.

Providers:
- "ollama": an Ollama server, POST {address}/api/generate (GET /api/tags lists the models).
- "openai": anything that speaks the OpenAI chat-completions API (OpenAI, OpenRouter, Groq,
  LM Studio, vLLM, Ollama's /v1 …), POST {address}/chat/completions with a Bearer key
  (GET /models lists the models).
- "anthropic": Anthropic's Messages API, POST {address}/v1/messages with x-api-key
  (GET /v1/models?limit=100).

The access key is only ever put in a request header: never in a URL, a log line or an error message.

Each app keeps its own `app/ai_client.py`: its Config (provider, url, model, api_key, its output
limit), reading App settings, its prompts, its usage notes and its wording. It builds one Client:

    CLIENT = ai_client.Client(post=lambda *a: _post(*a), sleep=lambda s: _sleep(s), log=logger,
                              wording=ai_client.Wording(...))     # hooks looked up at call time (tests)
    reply = CLIENT.ollama(cfg, prompt, want_json=True, max_tokens=..., timeout=...)
    reply = CLIENT.openai(cfg, prompt, images=[b64png, …], system=..., temperature=..., timeout=...)
    reply = CLIENT.anthropic(cfg, prompt, limit=4000, timeout=...)
    reply = CLIENT.tool_call(cfg, prompt, tools, system=..., max_tokens=..., timeout=...)   # reply.calls
    names = CLIENT.list_models(cfg, timeout=10)

`cfg` is any object with provider, url, model, api_key and label. A Reply has text, raw (the
response body), payload (the parsed answer), input_tokens, output_tokens, seconds (Ollama's own
timing, else None) and calls (the tools a model called through `tool_call`: [{"name", "args"}]).
Every failure is an AIError(message, kind, raw) with kind one of unreachable |
timeout | auth | rate_limit | not_found | bad_request | bad_response.

Requests that the provider answers 429/500/502/503/504/529 are retried up to MAX_RETRIES times,
waiting Retry-After (1–60 s) or 5, 15, 45 s. A 400 from an OpenAI-compatible server about an
option it doesn't know is retried once without it (`openai_fallback`); a 400 from Claude about
max_tokens is retried once with a smaller limit (`anthropic_shrink`).

Native tool calling (`tool_call`): `tools` is [{"name", "description", "parameters": a JSON Schema
object}], names matching ^[A-Za-z0-9_-]{1,64}$ (what every provider accepts). Ollama gets them on
POST /api/chat, an OpenAI-compatible server as `tools` of type function, Claude as `tools` with an
input_schema; the model may call several, or answer in words. A model or server without tool
support answers 400, an AIError of kind bad_request: the caller falls back to asking for JSON.
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

PROVIDERS = {"ollama": "Ollama", "openai": "OpenAI-compatible", "anthropic": "Anthropic Claude"}
ANTHROPIC_VERSION = "2023-06-01"
RETRY_STATUSES = {429, 500, 502, 503, 504, 529}
MAX_RETRIES = 3
MAX_RETRY_WAIT = 60
JSON_ONLY = "\n\nAnswer with the JSON object only."

logger = logging.getLogger("ai_client")


class AIError(Exception):
    """kind: not_configured | unreachable | timeout | auth | rate_limit | not_found | bad_request | bad_response."""

    def __init__(self, message: str, kind: str = "error", raw: str = ""):
        super().__init__(message)
        self.kind = kind
        self.raw = raw


@dataclass
class Reply:
    text: str                  # the model's answer
    raw: str = ""              # the whole response body (debug pages)
    payload: dict = field(default_factory=dict)
    input_tokens: int | None = None
    output_tokens: int | None = None
    seconds: float | None = None   # as reported by the provider, else None
    calls: list = field(default_factory=list)   # tool_call only: [{"name": str, "args": dict}]


# ---- HTTP -----------------------------------------------------------------------------------------

def post(url: str, headers: dict, body: dict | None, timeout: float | None, method: str = "POST") -> tuple[int, str, dict]:
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


def error_text(text: str) -> str:
    """The provider's own error message out of a JSON error body, else the start of the body."""
    try:
        data = json.loads(text)
    except ValueError:
        return text.strip()[:300]
    err = data.get("error", data) if isinstance(data, dict) else data
    if isinstance(err, dict):
        return str(err.get("message") or err.get("type") or err)[:300]
    return str(err)[:300]


def retry_after(headers: dict, attempt: int) -> float:
    lowered = {k.lower(): v for k, v in (headers or {}).items()}
    try:
        return min(MAX_RETRY_WAIT, max(1.0, float(lowered.get("retry-after", ""))))
    except ValueError:
        return min(MAX_RETRY_WAIT, 5.0 * (3 ** attempt))   # 5, 15, 45 s


def auth_headers(cfg) -> dict:
    """Claude: x-api-key + anthropic-version; anything else: a Bearer key when one is set."""
    if cfg.provider == "anthropic":
        return {"x-api-key": cfg.api_key, "anthropic-version": ANTHROPIC_VERSION}
    return {"Authorization": f"Bearer {cfg.api_key}"} if cfg.api_key else {}


def _function_calls(tool_calls) -> list[dict]:
    """OpenAI's and Ollama's tool_calls → [{"name", "args"}]; arguments come as a JSON string (OpenAI) or an
    object (Ollama). Anything unreadable becomes no arguments: the caller checks them anyway."""
    out = []
    for c in tool_calls if isinstance(tool_calls, list) else []:
        fn = c.get("function") if isinstance(c, dict) else None
        if not isinstance(fn, dict) or not isinstance(fn.get("name"), str) or not fn["name"]:
            continue
        args = fn.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args) if args.strip() else {}
            except ValueError:
                args = {}
        out.append({"name": fn["name"], "args": args if isinstance(args, dict) else {}})
    return out


# ---- retrying a 400 without what the server didn't accept --------------------------------------------

def fallback_json_and_max_tokens(body: dict, text: str) -> bool:
    """An OpenAI-compatible server that doesn't know JSON mode, or a newer OpenAI model that wants
    max_completion_tokens: retry without response_format (and with max_completion_tokens)."""
    if "response_format" not in text and "max_tokens" not in text:
        return False
    body.pop("response_format", None)
    if "max_tokens" in body and "max_completion_tokens" in text:
        body["max_completion_tokens"] = body.pop("max_tokens")
    return True


def fallback_json_and_temperature(body: dict, text: str) -> bool:
    """A server that doesn't know JSON mode, or a model that only allows its default temperature:
    retry without both."""
    if "response_format" not in text and "temperature" not in text:
        return False
    body.pop("response_format", None)
    body.pop("temperature", None)
    return True


def fallback_temperature_and_max_tokens(body: dict, text: str) -> bool:
    """A model that only allows its default temperature, or wants max_completion_tokens (or no limit):
    retry without what it named."""
    changed = False
    if "temperature" in text and body.pop("temperature", None) is not None:
        changed = True
    if "max_tokens" in text and "max_tokens" in body:
        limit = body.pop("max_tokens")
        if "max_completion_tokens" in text:
            body["max_completion_tokens"] = limit
        changed = True
    return changed


def shrink_to_named_maximum(fallback: int = 8192):
    """Claude refused max_tokens: the limit its message names (when smaller), else min(limit, fallback)."""
    def shrink(message: str, limit: int) -> int:
        m = re.search(r"(?:maximum|max)[^0-9]{0,60}(\d{3,6})", message)
        return int(m.group(1)) if m and int(m.group(1)) < limit else min(limit, fallback)
    return shrink


def shrink_to_largest_smaller(fallback: int = 4096):
    """Claude refused max_tokens: the largest number in its message below the limit, else min(limit, fallback)."""
    def shrink(message: str, limit: int) -> int:
        smaller = [int(n) for n in re.findall(r"\d{3,6}", message) if 0 < int(n) < limit]
        return max(smaller) if smaller else min(limit, fallback)
    return shrink


# ---- wording ----------------------------------------------------------------------------------------

@dataclass
class Wording:
    """The app's error messages ({label}, {url}, {seconds}, {reason}, {status}, {message})."""
    timeout: str = "{label} didn't answer within {seconds} s."
    unreachable: str = "Couldn't reach {label} at {url}: {reason}"
    auth: str = "{label} refused the access key ({status}: {message}). Check it on Admin → App settings."
    rate_limit: str = "{label} is rate-limiting requests (429: {message}). Try again later."
    not_found: str = "{label} answered 404 ({message}): check the address and the model name."
    overloaded: str = "{label} is overloaded or failing ({status}: {message}). Try again later."
    rejected: str = "{label} rejected the request ({status}: {message})."
    not_json: str = "{label} returned something that isn't JSON."
    unexpected: str = "{label} returned an unexpected answer."
    no_answer: str = "{label} returned no answer."
    keep_raw: bool = True             # AIError.raw and Reply.raw = the provider's answer (else "")
    scrub_key: bool = False           # replace the access key, should a provider echo it, with "…"
    timeout_in_reason: bool = False   # a connection error caused by a timeout counts as a timeout


def _scrub(cfg, text: str) -> str:
    return text.replace(cfg.api_key, "…") if cfg.api_key else text


class Client:
    def __init__(self, *, post=None, sleep=None, log: logging.Logger | None = None, wording: Wording | None = None,
                 auth_headers=auth_headers, model_name=lambda m: m.get("name", "")):
        self._post = post or globals()["post"]
        self._sleep = sleep or time.sleep
        self.log = log or logger
        self.wording = wording or Wording()
        self.auth_headers = auth_headers
        self.model_name = model_name          # an Ollama /api/tags entry -> its name

    # ---- one request ----
    def _error(self, cfg, template: str, kind: str, raw: str = "", scrub_all: bool = False, **values) -> AIError:
        w = self.wording
        if w.scrub_key and "message" in values:
            values["message"] = _scrub(cfg, values["message"])
        message = template.format(label=cfg.label, url=cfg.url, **values)
        if w.scrub_key and scrub_all:
            message = _scrub(cfg, message)
        return AIError(message, kind, raw if w.keep_raw else "")

    def _raw(self, text: str) -> str:
        return text if self.wording.keep_raw else ""

    def _timeout(self, cfg, timeout) -> AIError:
        return self._error(cfg, self.wording.timeout, "timeout", seconds=int(timeout or 0))

    def request(self, cfg, path: str, headers: dict, body: dict | None, timeout: float | None,
                method: str = "POST") -> tuple[int, str]:
        """One call with retries on rate limits / overload; network errors become AIError."""
        url = f"{cfg.url}{path}"
        for attempt in range(MAX_RETRIES + 1):
            try:
                status, text, resp_headers = self._post(url, headers, body, timeout, method)
            except TimeoutError as e:
                raise self._timeout(cfg, timeout) from e
            except (urllib.error.URLError, ConnectionError, http.client.HTTPException, OSError) as e:
                reason = getattr(e, "reason", e)
                if self.wording.timeout_in_reason and (isinstance(reason, TimeoutError) or "timed out" in str(reason)):
                    raise self._timeout(cfg, timeout) from e
                raise self._error(cfg, self.wording.unreachable, "unreachable", reason=reason, scrub_all=True) from e
            if status in RETRY_STATUSES and attempt < MAX_RETRIES:
                wait = retry_after(resp_headers, attempt)
                self.log.warning("%s answered %s; retrying in %.0f s", cfg.label, status, wait)
                self._sleep(wait)
                continue
            return status, text
        return status, text  # pragma: no cover

    def check(self, cfg, status: int, text: str) -> None:
        if status < 400:
            return
        w = self.wording
        message = error_text(text)
        if status in (401, 403):
            raise self._error(cfg, w.auth, "auth", text, status=status, message=message)
        if status == 429:
            raise self._error(cfg, w.rate_limit, "rate_limit", text, status=status, message=message)
        if status == 404:
            raise self._error(cfg, w.not_found, "not_found", text, status=status, message=message)
        if status in RETRY_STATUSES:
            raise self._error(cfg, w.overloaded, "rate_limit", text, status=status, message=message)
        raise self._error(cfg, w.rejected, "bad_request", text, status=status, message=message)

    def parse(self, cfg, text: str) -> dict:
        try:
            data = json.loads(text)
        except ValueError:
            raise self._error(cfg, self.wording.not_json, "bad_response", text) from None
        if not isinstance(data, dict):
            raise self._error(cfg, self.wording.unexpected, "bad_response", text)
        return data

    # ---- the three providers ----
    def ollama(self, cfg, prompt: str, *, images=None, system: str | None = None, want_json: bool = False,
               max_tokens: int | None = None, temperature: float | None = None, timeout: float | None = None) -> Reply:
        body = {"model": cfg.model, "prompt": prompt, "stream": False}
        if system:
            body["system"] = system
        if images:
            body["images"] = images
        if want_json:
            body["format"] = "json"
        options = {}
        if max_tokens:
            options["num_predict"] = max_tokens
        if temperature is not None:
            options["temperature"] = temperature
        if options:
            body["options"] = options
        status, text = self.request(cfg, "/api/generate", {}, body, timeout)
        self.check(cfg, status, text)
        data = self.parse(cfg, text)
        total = data.get("total_duration")
        return Reply(
            # A reasoning model can leave "response" empty and answer in "thinking".
            text=data.get("response") or data.get("thinking", "") or "", raw=self._raw(text), payload=data,
            input_tokens=data.get("prompt_eval_count"), output_tokens=data.get("eval_count"),
            seconds=round(total / 1e9, 2) if isinstance(total, (int, float)) and total else None)

    def openai(self, cfg, prompt: str, *, images=None, system: str | None = None, want_json: bool = False,
               max_tokens: int | None = None, temperature: float | None = None, timeout: float | None = None,
               fallback=fallback_json_and_max_tokens) -> Reply:
        """images: base64 PNGs, sent as data: URLs after the text."""
        if images:
            content = [{"type": "text", "text": prompt}]
            content += [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}} for b64 in images]
        else:
            content = prompt
        messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": content}]
        body = {"model": cfg.model, "messages": messages}
        if want_json:
            body["response_format"] = {"type": "json_object"}
        if max_tokens:
            body["max_tokens"] = max_tokens
        if temperature is not None:
            body["temperature"] = temperature
        headers = self.auth_headers(cfg)
        status, text = self.request(cfg, "/chat/completions", headers, body, timeout)
        if status == 400 and fallback(body, text):
            status, text = self.request(cfg, "/chat/completions", headers, body, timeout)
        self.check(cfg, status, text)
        data = self.parse(cfg, text)
        try:
            message = data["choices"][0]["message"]
            answer = message.get("content") or message.get("reasoning_content") or ""
        except (KeyError, IndexError, TypeError):
            raise self._error(cfg, self.wording.no_answer, "bad_response", text) from None
        if isinstance(answer, list):   # content parts
            answer = "".join(p.get("text", "") for p in answer if isinstance(p, dict))
        usage = data.get("usage") or {}
        return Reply(text=answer, raw=self._raw(text), payload=data, input_tokens=usage.get("prompt_tokens"),
                     output_tokens=usage.get("completion_tokens"))

    def anthropic(self, cfg, prompt: str, *, limit: int, images=None, system: str | None = None,
                  want_json: bool = False, temperature: float | None = None, timeout: float | None = None,
                  shrink=shrink_to_named_maximum(8192)) -> Reply:
        """limit: max_tokens to ask for. images: base64 PNGs, sent before the text."""
        content = [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}}
                   for b64 in images or []]
        content.append({"type": "text", "text": prompt + (JSON_ONLY if want_json else "")})
        body = {"model": cfg.model, "max_tokens": limit, "messages": [{"role": "user", "content": content}]}
        if system:
            body["system"] = system
        if temperature is not None:
            body["temperature"] = temperature
        headers = self.auth_headers(cfg)
        status, text = self.request(cfg, "/v1/messages", headers, body, timeout)
        if status == 400 and "max_tokens" in text:
            # The model allows fewer output tokens than asked.
            body["max_tokens"] = shrink(error_text(text), limit)
            if body["max_tokens"] != limit:
                status, text = self.request(cfg, "/v1/messages", headers, body, timeout)
        self.check(cfg, status, text)
        data = self.parse(cfg, text)
        answer = "".join(p.get("text", "") for p in data.get("content", []) if isinstance(p, dict) and p.get("type") == "text")
        usage = data.get("usage") or {}
        input_tokens = usage.get("input_tokens")
        if input_tokens is not None:
            input_tokens += (usage.get("cache_read_input_tokens") or 0) + (usage.get("cache_creation_input_tokens") or 0)
        if data.get("stop_reason") == "max_tokens":
            self.log.warning("Claude stopped at the output limit (%s tokens)", body["max_tokens"])
        return Reply(text=answer, raw=self._raw(text), payload=data, input_tokens=input_tokens, output_tokens=usage.get("output_tokens"))

    # ---- native tool calling ----
    def tool_call(self, cfg, prompt: str, tools: list[dict], *, system: str | None = None,
                  max_tokens: int | None = None, temperature: float | None = None,
                  timeout: float | None = None) -> Reply:
        """Offer `tools` to the model: Reply.calls holds the ones it called, Reply.text any words."""
        if cfg.provider == "anthropic":
            return self._anthropic_tools(cfg, prompt, tools, system, max_tokens or 4096, temperature, timeout)
        functions = [{"type": "function", "function": {"name": t["name"], "description": t.get("description", ""),
                                                       "parameters": t.get("parameters") or {"type": "object", "properties": {}}}}
                     for t in tools]
        messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
        if cfg.provider == "ollama":
            body = {"model": cfg.model, "messages": messages, "tools": functions, "stream": False}
            options = {}
            if max_tokens:
                options["num_predict"] = max_tokens
            if temperature is not None:
                options["temperature"] = temperature
            if options:
                body["options"] = options
            status, text = self.request(cfg, "/api/chat", {}, body, timeout)
            self.check(cfg, status, text)
            data = self.parse(cfg, text)
            message = data.get("message") if isinstance(data.get("message"), dict) else {}
            total = data.get("total_duration")
            return Reply(text=message.get("content") or "", raw=self._raw(text), payload=data,
                         input_tokens=data.get("prompt_eval_count"), output_tokens=data.get("eval_count"),
                         seconds=round(total / 1e9, 2) if isinstance(total, (int, float)) and total else None,
                         calls=_function_calls(message.get("tool_calls")))
        body = {"model": cfg.model, "messages": messages, "tools": functions}
        if max_tokens:
            body["max_tokens"] = max_tokens
        if temperature is not None:
            body["temperature"] = temperature
        headers = self.auth_headers(cfg)
        status, text = self.request(cfg, "/chat/completions", headers, body, timeout)
        if status == 400 and fallback_temperature_and_max_tokens(body, text):
            status, text = self.request(cfg, "/chat/completions", headers, body, timeout)
        self.check(cfg, status, text)
        data = self.parse(cfg, text)
        try:
            message = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            raise self._error(cfg, self.wording.no_answer, "bad_response", text) from None
        answer = message.get("content") or ""
        if isinstance(answer, list):
            answer = "".join(p.get("text", "") for p in answer if isinstance(p, dict))
        usage = data.get("usage") or {}
        return Reply(text=answer, raw=self._raw(text), payload=data, input_tokens=usage.get("prompt_tokens"),
                     output_tokens=usage.get("completion_tokens"), calls=_function_calls(message.get("tool_calls")))

    def _anthropic_tools(self, cfg, prompt, tools, system, limit, temperature, timeout) -> Reply:
        body = {"model": cfg.model, "max_tokens": limit, "messages": [{"role": "user", "content": prompt}],
                "tools": [{"name": t["name"], "description": t.get("description", ""),
                           "input_schema": t.get("parameters") or {"type": "object", "properties": {}}} for t in tools]}
        if system:
            body["system"] = system
        if temperature is not None:
            body["temperature"] = temperature
        status, text = self.request(cfg, "/v1/messages", self.auth_headers(cfg), body, timeout)
        self.check(cfg, status, text)
        data = self.parse(cfg, text)
        blocks = [b for b in data.get("content", []) if isinstance(b, dict)]
        usage = data.get("usage") or {}
        return Reply(text="".join(b.get("text", "") for b in blocks if b.get("type") == "text"), raw=self._raw(text),
                     payload=data, input_tokens=usage.get("input_tokens"), output_tokens=usage.get("output_tokens"),
                     calls=[{"name": b.get("name", ""), "args": b.get("input") if isinstance(b.get("input"), dict) else {}}
                            for b in blocks if b.get("type") == "tool_use" and b.get("name")])

    def list_models(self, cfg, timeout: float = 10) -> list[str]:
        """The models the provider offers (Test connection). Generates nothing. Raises AIError."""
        if cfg.provider == "ollama":
            status, text = self.request(cfg, "/api/tags", {}, None, timeout, method="GET")
            self.check(cfg, status, text)
            return [self.model_name(m) for m in self.parse(cfg, text).get("models", []) if isinstance(m, dict)]
        if cfg.provider == "anthropic":
            if not cfg.api_key:
                raise AIError("Enter the access key first.", "auth")
            status, text = self.request(cfg, "/v1/models?limit=100", self.auth_headers(cfg), None, timeout, method="GET")
        else:
            status, text = self.request(cfg, "/models", self.auth_headers(cfg), None, timeout, method="GET")
        self.check(cfg, status, text)
        return [m.get("id", "") for m in self.parse(cfg, text).get("data", []) if isinstance(m, dict)]
