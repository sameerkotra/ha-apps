"""Talking to the AI model chosen on Admin → App settings (SPEC §11): the same three providers, the same
settings and the same behaviour as Finance Dashboard's ai_client.py, without images.

Providers:
- "ollama": a local Ollama server, POST {address}/api/generate.
- "openai": anything that speaks the OpenAI chat-completions API (OpenAI, OpenRouter, Groq,
  LM Studio, vLLM, Ollama's /v1 …), POST {address}/chat/completions with a Bearer key.
- "anthropic": Anthropic's Messages API, POST {address}/v1/messages with x-api-key.

The requests, retries and error messages are the shared app/common/ai_client.py.
Only the server talks to the model. The access key is only ever put in a request header: never in a
URL, a log line, an error message or a page.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from . import settings
from .common import ai_client as core

logger = logging.getLogger(__name__)

PROVIDERS = core.PROVIDERS
ANTHROPIC_VERSION = core.ANTHROPIC_VERSION
RETRY_STATUSES = core.RETRY_STATUSES
MAX_RETRIES = core.MAX_RETRIES
MAX_RETRY_WAIT = core.MAX_RETRY_WAIT
TIMEOUT = 300

AIError = core.AIError
Reply = core.Reply


@dataclass
class Config:
    provider: str
    url: str
    model: str
    api_key: str = ""
    max_output_tokens: int = 4000

    @property
    def label(self) -> str:
        return PROVIDERS.get(self.provider, self.provider)


def current() -> Config:
    """The configuration on App settings."""
    v = settings.all()
    return Config(provider=v["ai_provider"], url=settings.ai_url(), model=v["ai_model"],
                  api_key=v["ai_api_key"], max_output_tokens=v["ai_max_output_tokens"])


# ---- HTTP (app/common/ai_client.py) -----------------------------------------------------------------

_post = core.post                          # tests replace this


def _sleep(seconds: float) -> None:        # tests replace this
    time.sleep(seconds)


CLIENT = core.Client(post=lambda *a: _post(*a), sleep=lambda s: _sleep(s), log=logger)


# ---- the three providers ---------------------------------------------------------------------------

def _ollama(cfg, prompt, want_json, timeout, max_tokens) -> Reply:
    return CLIENT.ollama(cfg, prompt, want_json=want_json, max_tokens=max_tokens, timeout=timeout)


def _openai(cfg, prompt, want_json, timeout, max_tokens) -> Reply:
    return CLIENT.openai(cfg, prompt, want_json=want_json, max_tokens=max_tokens, timeout=timeout)


def _anthropic(cfg, prompt, want_json, timeout, max_tokens) -> Reply:
    return CLIENT.anthropic(cfg, prompt, want_json=want_json, timeout=timeout,
                            limit=max_tokens or cfg.max_output_tokens or 4000)


_CALLS = {"ollama": _ollama, "openai": _openai, "anthropic": _anthropic}


def generate(prompt: str, *, want_json: bool = False, timeout: float | None = TIMEOUT,
             max_tokens: int | None = None, cfg: Config | None = None) -> Reply:
    """Ask the model once. Raises AIError."""
    cfg = cfg or current()
    if not cfg.url or not cfg.model:
        raise AIError("AI isn't set up: an admin adds the provider, address and model on Admin → App settings.",
                      "not_configured")
    if cfg.provider == "anthropic" and not cfg.api_key:
        raise AIError("Anthropic Claude needs an access key: add it on Admin → App settings.", "auth")
    return _CALLS.get(cfg.provider, _ollama)(cfg, prompt, want_json, timeout, max_tokens or cfg.max_output_tokens)


def list_models(cfg: Config, timeout: float = 10) -> list[str]:
    """The models the provider offers (Test connection). Raises AIError."""
    return CLIENT.list_models(cfg, timeout)
