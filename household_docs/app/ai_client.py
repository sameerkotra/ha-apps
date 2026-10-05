"""Talking to the AI model chosen on Admin → App settings → AI (SPEC §17.6): Ollama, an OpenAI-compatible service or
Anthropic Claude — the same three providers, settings and behaviour as the other household apps with AI. The
requests, retries and error messages are the shared app/common/ai_client.py.

Only the server talks to the model, and only while AI is on and set up (`settings.ai_problem()` is None). The access
key is only ever put in a request header: never in a URL, a log line, an error message or a page.

Pictures: the shared client labels every picture `image/png`; Household Docs also sends JPEG scans (and GIF /
WebP photos), so `_post` gives each picture its real type (from its first bytes) before the request leaves.
"""
from __future__ import annotations

import base64
import logging
import time
from dataclasses import dataclass

from . import settings
from .common import ai_client as core

logger = logging.getLogger(__name__)

PROVIDERS = core.PROVIDERS
TIMEOUT = 180
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


def current(vision: bool = False, conn=None) -> Config:
    v = settings.all_values(conn)
    model = (v["ai_vision_model"] or v["ai_model"]) if vision else v["ai_model"]
    return Config(provider=v["ai_provider"], url=settings.ai_url(conn), model=model, api_key=v["ai_api_key"])


def image_type(b64: str) -> str:
    try:
        head = base64.b64decode(b64[:24] + "=" * (-len(b64[:24]) % 4))
    except ValueError:
        return "image/png"
    from .store import kinds
    return kinds.raster_type(head) or "image/png"


def _fix_images(body):
    """Each picture with its real type (the shared client says image/png for all)."""
    if not isinstance(body, dict):
        return body
    for m in body.get("messages") or []:
        content = m.get("content") if isinstance(m, dict) else None
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "image" and isinstance(part.get("source"), dict):
                part["source"]["media_type"] = image_type(part["source"].get("data", ""))
            elif part.get("type") == "image_url" and isinstance(part.get("image_url"), dict):
                url = part["image_url"].get("url", "")
                if url.startswith("data:image/png;base64,"):
                    b64 = url[len("data:image/png;base64,"):]
                    part["image_url"]["url"] = f"data:{image_type(b64)};base64,{b64}"
    return body


def _http(url, headers, body, timeout, method="POST"):     # tests replace this
    return core.post(url, headers, body, timeout, method)


def _post(url, headers, body, timeout, method="POST"):
    return _http(url, headers, _fix_images(body), timeout, method)


def _sleep(seconds: float) -> None:        # tests replace this
    time.sleep(seconds)


CLIENT = core.Client(post=lambda *a: _post(*a), sleep=lambda s: _sleep(s), log=logger,
                     wording=core.Wording(scrub_key=True, timeout_in_reason=True))


def generate(prompt: str, *, images=None, system: str | None = None, want_json: bool = False,
             max_tokens: int = 4000, timeout: float | None = TIMEOUT, cfg: Config | None = None) -> Reply:
    """Ask the model once. Raises AIError."""
    cfg = cfg or current(vision=bool(images))
    if not cfg.url or not cfg.model:
        raise AIError("AI isn't set up: an admin adds the provider, address and model on Admin → App settings.",
                      "not_configured")
    if cfg.provider == "anthropic" and not cfg.api_key:
        raise AIError("Anthropic Claude needs an access key: add it on Admin → App settings.", "auth")
    if cfg.provider == "anthropic":
        return CLIENT.anthropic(cfg, prompt, images=images, system=system, want_json=want_json, timeout=timeout,
                                limit=max_tokens)
    if cfg.provider == "openai":
        return CLIENT.openai(cfg, prompt, images=images, system=system, want_json=want_json, timeout=timeout,
                             max_tokens=max_tokens)
    return CLIENT.ollama(cfg, prompt, images=images, system=system, want_json=want_json, timeout=timeout,
                         max_tokens=max_tokens)


def list_models(cfg: Config, timeout: float = 10) -> list[str]:
    return CLIENT.list_models(cfg, timeout)
