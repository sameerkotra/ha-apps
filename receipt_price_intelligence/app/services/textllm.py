"""Ask the configured model a text question and get JSON back.

Uses the same model server as receipt reading (Ollama or an OpenAI-compatible server), one request
at a time so a batch of web lookups never floods it.
"""

import json
import re
import threading
import time
import urllib.error
import urllib.request
from typing import Any

from app.config import get_settings
from app.logging_config import get_logger
from app.services import webdebug

logger = get_logger("textllm")

_lock = threading.Lock()
SEED = 7  # with temperature 0, a fixed seed makes the same question give the same answer


class TextLLMError(RuntimeError):
    """The model could not answer. The message is safe to show."""


def _post(path: str, payload: dict[str, Any], optional: tuple[str, ...]) -> dict[str, Any]:
    """POST to the model server, retrying once without optional fields if it rejects them."""
    settings = get_settings()
    if not settings.model_ready:
        raise TextLLMError("No model is set up yet. An administrator can set it in Admin → App settings.")
    headers = {"Content-Type": "application/json"}
    if settings.model_api_key:
        headers["Authorization"] = f"Bearer {settings.model_api_key}"
    url = settings.model_url.rstrip("/") + path
    body = dict(payload)
    for attempt in (1, 2):
        request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
        started = time.time()
        trace = {"method": "POST", "url": url, "sent": {"headers": headers, "body": body}, "started": started}
        try:
            with urllib.request.urlopen(request, timeout=settings.model_timeout_seconds) as response:
                raw = response.read().decode("utf-8", errors="replace")
                webdebug.record("model", "ask", status=response.status, received=raw, content_type=response.headers.get("Content-Type"), **trace)
                return json.loads(raw)
        except urllib.error.HTTPError as e:
            webdebug.record("model", "ask", status=e.code, error=f"HTTP {e.code}", **trace)
            if e.code == 400 and attempt == 1 and any(k in body for k in optional):
                body = {k: v for k, v in body.items() if k not in optional}
                continue
            raise TextLLMError(f"The model server answered with an error ({e.code})") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            webdebug.record("model", "ask", error=f"{type(e).__name__}: {e}", **trace)
            raise TextLLMError("The model server could not be reached") from e
        except ValueError as e:
            raise TextLLMError("The model server sent an unreadable answer") from e
    raise TextLLMError("The model server refused the request")


_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I)
_TRAILING_COMMA = re.compile(r",(\s*[}\]])")


def parse_json_object(text: str) -> dict[str, Any]:
    """The first JSON object in a model's text. Tolerates reasoning blocks, code fences,
    prose around the JSON and trailing commas. Raises ValueError if there is none."""
    cleaned = _THINK.sub("", text or "")
    if "<think>" in cleaned.lower():
        cleaned = cleaned[: cleaned.lower().index("<think>")]
    cleaned = _FENCE.sub("", cleaned.strip()).strip()
    decoder = json.JSONDecoder()
    for candidate in (_TRAILING_COMMA.sub(r"\1", cleaned), cleaned):  # the tidied text first, so an outer object wins over one inside it
        try:
            value = json.loads(candidate)
            if isinstance(value, dict):
                return value
        except ValueError:
            pass
        for match in re.finditer(r"\{", candidate):
            try:
                value, _ = decoder.raw_decode(candidate[match.start():])
            except ValueError:
                continue
            if isinstance(value, dict) and value:
                return value
    raise ValueError("no JSON object in the answer")


def complete_json(system: str, user: str, max_tokens: int = 2000) -> dict[str, Any]:
    """Send a system and user message and return the JSON object the model answers with."""
    settings = get_settings()
    with _lock:
        if settings.model_api_format == "ollama":
            options: dict[str, Any] = {"temperature": 0, "seed": SEED, "num_predict": max_tokens}
            if settings.model_num_ctx > 0:
                options["num_ctx"] = settings.model_num_ctx
            data = _post("/api/chat", {
                "model": settings.model_name, "stream": False, "format": "json", "think": False,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "options": options,
            }, optional=("think", "format"))
            text = ((data.get("message") or {}).get("content")) or ""
        else:
            data = _post("/chat/completions", {
                "model": settings.model_name, "temperature": 0, "seed": SEED, "max_tokens": max_tokens,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            }, optional=("response_format", "seed"))
            choices = data.get("choices") or [{}]
            text = ((choices[0].get("message") or {}).get("content")) or ""
    try:
        return parse_json_object(text)
    except ValueError as e:
        raise TextLLMError("The model's answer could not be read") from e
