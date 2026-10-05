"""One way to talk to the AI model, whichever provider is chosen on Admin → App settings
(SPEC.md section 22). Used by statement, utility-bill and toll extraction, categorization
and Write SQL.

Providers:
- "ollama": a local Ollama server, POST {address}/api/generate.
- "openai": anything that speaks the OpenAI chat-completions API (OpenAI, OpenRouter, Groq,
  LM Studio, vLLM, Ollama's /v1 …), POST {address}/chat/completions with a Bearer key.
- "anthropic": Anthropic's Messages API, POST {address}/v1/messages with x-api-key.

The requests, retries and error messages are the shared app/common/ai_client.py.
Every request reports its tokens and time to ai_usage. The access key is only ever put in a
request header: never in a URL, a log line, an error message or a page.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from . import ai_usage, settings
from .common import ai_client as core

logger = logging.getLogger(__name__)

PROVIDERS = core.PROVIDERS
DEFAULT_URLS = {"ollama": "", "openai": "https://api.openai.com/v1", "anthropic": "https://api.anthropic.com"}
ANTHROPIC_VERSION = core.ANTHROPIC_VERSION
RETRY_STATUSES = core.RETRY_STATUSES
MAX_RETRIES = core.MAX_RETRIES
MAX_RETRY_WAIT = core.MAX_RETRY_WAIT

AIError = core.AIError
Reply = core.Reply


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


# ---- HTTP (app/common/ai_client.py) -----------------------------------------------------------------

_post = core.post                          # tests replace this


def _sleep(seconds: float) -> None:        # tests replace this
    time.sleep(seconds)


CLIENT = core.Client(post=lambda *a: _post(*a), sleep=lambda s: _sleep(s), log=logger)


# ---- the three providers ---------------------------------------------------------------------------

def _ollama(cfg, prompt, images, want_json, timeout, max_tokens) -> Reply:
    return CLIENT.ollama(cfg, prompt, images=images, want_json=want_json, max_tokens=max_tokens, timeout=timeout)


def _openai(cfg, prompt, images, want_json, timeout, max_tokens) -> Reply:
    return CLIENT.openai(cfg, prompt, images=images, want_json=want_json, max_tokens=max_tokens, timeout=timeout)


def _anthropic(cfg, prompt, images, want_json, timeout, max_tokens) -> Reply:
    return CLIENT.anthropic(cfg, prompt, images=images, want_json=want_json, timeout=timeout,
                            limit=max_tokens or cfg.max_output_tokens or 16000)


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
    return CLIENT.list_models(cfg, timeout)


def privacy() -> dict | None:
    """For the privacy notice (Home, App settings, upload pages): None while the AI is on the home network."""
    if not settings.ai_is_external():
        return None
    return {"label": provider_label(), "host": settings.ai_host(), "sql": True}
