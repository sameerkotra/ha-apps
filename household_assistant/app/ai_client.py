"""One way to talk to the AI model, whichever provider is chosen on
Admin → App settings: the plan and the answer of every question (engine.py)
and Test connection. Nothing else in the app builds HTTP requests to a model.

Providers:
- "ollama": an Ollama server, POST {address}/api/generate.
- "openai": anything that speaks the OpenAI chat-completions API (OpenAI,
  OpenRouter, Groq, LM Studio, vLLM, Ollama's /v1 …), POST
  {address}/chat/completions, with a Bearer key only when one is set.
- "anthropic": Anthropic's Messages API, POST {address}/v1/messages with
  x-api-key.

The requests and retries are the shared app/common/ai_client.py, with this
app's wording (WORDING). Provider, address, model and key are read from App settings at each request,
so a change applies to the next request without a restart. The access key is
only ever put in a request header: never in a URL, a log line, an error
message or a page. Only standard-library urllib is used; callers run the
blocking functions in the threadpool.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field

from starlette.concurrency import run_in_threadpool

from . import settings
from .common import ai_client as core

logger = logging.getLogger("ai")

PROVIDERS = settings.PROVIDERS
ANTHROPIC_VERSION = core.ANTHROPIC_VERSION
RETRY_STATUSES = core.RETRY_STATUSES
MAX_RETRIES = core.MAX_RETRIES
MAX_RETRY_WAIT = core.MAX_RETRY_WAIT

WARMUP_TIMEOUT = 120
QUERY_TIMEOUT = 300
TEST_TIMEOUT = 10
# Ollama keeps a model loaded for 5 minutes after its last use by default
# (keep_alive), so a model that answered anything within the last few minutes
# is still loaded and doesn't need a separate "hi" warm-up first.
WARM_FOR_SECONDS = 4 * 60


AIError = core.AIError       # kind: not_configured | unreachable | timeout | auth | rate_limit | not_found |
Reply = core.Reply           # bad_request | bad_response


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


# ---- HTTP (app/common/ai_client.py) -----------------------------------------------------------------

_post = core.post                          # tests replace this


def _sleep(seconds: float) -> None:        # tests replace this
    time.sleep(seconds)


def _auth_headers(cfg: Config) -> dict:
    if cfg.provider == "anthropic":
        return {"x-api-key": cfg.api_key, "anthropic-version": ANTHROPIC_VERSION}
    if cfg.provider == "openai" and cfg.api_key:
        return {"Authorization": f"Bearer {cfg.api_key}"}
    return {}


WORDING = core.Wording(
    timeout="{label} at {url} didn't answer within {seconds} s.",
    unreachable="Couldn't reach {label} at {url}: {reason}. "
                "Check the address on Admin → App settings and that the server is running.",
    not_found="{label} answered 404 ({message}): check the address and the model name on Admin → App settings.",
    not_json="{label} returned something that isn't JSON — is the address right?",
    keep_raw=False, scrub_key=True, timeout_in_reason=True)

CLIENT = core.Client(post=lambda *a: _post(*a), sleep=lambda s: _sleep(s), log=logger, wording=WORDING,
                     auth_headers=_auth_headers, model_name=lambda m: m.get("name") or m.get("model") or "")


# ---- the three providers ---------------------------------------------------------------------------

def _ollama(cfg, prompt, system, want_json, temperature, timeout) -> Reply:
    return CLIENT.ollama(cfg, prompt, system=system, want_json=want_json, temperature=temperature, timeout=timeout)


def _openai(cfg, prompt, system, want_json, temperature, timeout) -> Reply:
    return CLIENT.openai(cfg, prompt, system=system, want_json=want_json, temperature=temperature, timeout=timeout,
                         fallback=core.fallback_json_and_temperature)


def _anthropic(cfg, prompt, system, want_json, temperature, timeout) -> Reply:
    return CLIENT.anthropic(cfg, prompt, system=system, want_json=want_json, temperature=temperature, timeout=timeout,
                            limit=cfg.max_tokens or 4096, shrink=core.shrink_to_largest_smaller(4096))


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
    return CLIENT.list_models(cfg, timeout)


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


def extract_json(text: str) -> dict:
    """The first JSON object in the model's answer (some models wrap it in prose or a code fence)."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("no JSON object in the model's answer")
    data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("the model's answer isn't a JSON object")
    return data


def privacy() -> dict | None:
    """For the privacy notice: None while the AI is on the home network (or not set up)."""
    if not settings.ai_is_external():
        return None
    return {"label": PROVIDERS.get(settings.ai_provider(), "AI"), "host": settings.ai_host()}


def status() -> dict:
    """For the page and Admin (no key, ever)."""
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
