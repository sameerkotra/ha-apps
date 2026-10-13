"""A record of every request the web lookups make, for the Web debug page.

``websearch`` (Tavily, SearXNG, your custom server, pages the app opens itself) and ``textllm`` (the model)
call ``record`` for each request: what was sent and what came back. Entries are kept in memory only (the
last ``KEEP`` of them, gone on restart) and shown to administrators.

Secrets never go in: authorization headers, API keys, and keys or tokens in an address's query are replaced by
"(hidden)". Bodies are cut to
``MAX_CHARS`` so a large page cannot fill memory.

``capture()`` additionally collects the entries of one debug run (the *Try it* tools), even when the
request goes on in a worker thread started with ``asyncio.to_thread`` (it copies context variables).
"""

from __future__ import annotations

import contextvars
import itertools
import threading
import time
from collections import deque
from contextlib import contextmanager
from typing import Any

KEEP = 150
MAX_CHARS = 20_000
_SECRET_KEYS = {"authorization", "api_key", "apikey", "x-api-key", "x-subscription-token", "token", "key", "password"}

_log: deque[dict[str, Any]] = deque(maxlen=KEEP)
_lock = threading.Lock()
_ids = itertools.count(1)
_capture: contextvars.ContextVar[list | None] = contextvars.ContextVar("webdebug_capture", default=None)
_current_step: contextvars.ContextVar[str | None] = contextvars.ContextVar("webdebug_step", default=None)


def _cut(value: Any) -> Any:
    if isinstance(value, str):
        return value if len(value) <= MAX_CHARS else value[:MAX_CHARS] + f"\n… ({len(value) - MAX_CHARS} more characters not kept)"
    return value


def redact(value: Any) -> Any:
    """A copy with secrets replaced, for dicts of headers or request bodies."""
    if isinstance(value, dict):
        return {k: "(hidden)" if str(k).lower() in _SECRET_KEYS and v else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return _cut(value)


_SECRET_PARAMS = _SECRET_KEYS | {"access_token", "auth", "sig", "signature", "secret", "client_secret",
                                 "session", "sessionid", "code", "pass", "pwd", "x-amz-signature", "x-amz-credential"}


def redact_url(url: str) -> str:
    """The address with a user name or password, and query values that look like keys or tokens, replaced."""
    if not url or ("?" not in url and "@" not in url):
        return url
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
    try:
        parts = urlsplit(url)
    except ValueError:
        return "(an address that could not be read)"
    netloc = parts.netloc
    if "@" in netloc:
        netloc = "(hidden)@" + netloc.rsplit("@", 1)[1]
    query = parts.query
    if query:
        pairs = parse_qsl(query, keep_blank_values=True)
        query = urlencode([(k, "(hidden)" if k.lower() in _SECRET_PARAMS or "token" in k.lower() or "key" in k.lower()
                            else v) for k, v in pairs], safe="()")
    return urlunsplit((parts.scheme, netloc, parts.path, query, parts.fragment))


def record(service: str, action: str, *, method: str = "GET", url: str = "", sent: Any = None, status: int | None = None,
           received: Any = None, content_type: str | None = None, error: str | None = None, started: float | None = None,
           note: str | None = None) -> dict[str, Any]:
    """Keep one request. ``service``: tavily, searxng, custom, page or model. ``action``: search, fetch, ask..."""
    size = len(received) if isinstance(received, (str, bytes)) else None
    entry = {
        "id": next(_ids), "time": time.time(), "service": service, "action": action, "method": method, "url": redact_url(url),
        "step": _current_step.get(), "sent": redact(sent), "status": status, "content_type": content_type,
        "received": _cut(received.decode("utf-8", "replace") if isinstance(received, bytes) else received),
        "received_chars": size, "error": error, "ms": round((time.time() - started) * 1000) if started else None, "note": note,
    }
    with _lock:
        _log.append(entry)
    captured = _capture.get()
    if captured is not None:
        captured.append(entry)
    return entry


def entries(limit: int = KEEP, service: str | None = None) -> list[dict[str, Any]]:
    """Newest first."""
    with _lock:
        items = list(_log)
    items.reverse()
    if service:
        items = [e for e in items if e["service"] == service]
    return items[: max(1, limit)]


def clear() -> None:
    with _lock:
        _log.clear()


@contextmanager
def capture():
    """Collect the entries recorded inside this block (and in threads it starts with asyncio.to_thread)."""
    collected: list[dict[str, Any]] = []
    token = _capture.set(collected)
    try:
        yield collected
    finally:
        _capture.reset(token)


@contextmanager
def step(name: str):
    """Label the requests made inside this block (e.g. "store page", "site search"), for reading the trace."""
    token = _current_step.set(name)
    try:
        yield
    finally:
        _current_step.reset(token)
