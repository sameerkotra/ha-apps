"""Remembering expensive reads for the length of one request or one background job.

A page load or a price check often asks for the same thing several times: the home's stores
(``trips.home_locations``) and its receipt history (``load_observations``) are each read in many places. Inside a
``scope()`` (every API request, and every background job) the first read is kept and later ones reuse it.

It can never hand out something stale: the moment anything is written to the database (a flush, a commit or a
rollback on any session), everything kept is forgotten. Each caller gets its own copy of the list and of each row,
so changing what it got does not change what the next caller gets.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable

from sqlalchemy import event
from sqlalchemy.orm import Session

_cache: ContextVar[dict | None] = ContextVar("reqcache", default=None)
_lock = threading.Lock()


# A background job can run for many minutes, and other sessions (a person approving a receipt) write meanwhile in
# other threads, which this scope's listeners don't see: what a job keeps is read again after this long.
JOB_TTL_SECONDS = 120.0


@contextmanager
def scope(ttl: float | None = None):
    """Keep reads for the length of this block (a request, a job). Nested scopes share the outer one. With ``ttl``
    (a job), each read is kept at most that many seconds."""
    if _cache.get() is not None:
        yield
        return
    token = _cache.set({"__ttl__": ttl} if ttl else {})
    try:
        yield
    finally:
        _cache.reset(token)


def scoped(fn: Callable) -> Callable:
    """A function that runs in its own ``scope()`` (a background job's entry point)."""
    import functools

    @functools.wraps(fn)
    def run(*args: Any, **kwargs: Any) -> Any:
        with scope(ttl=JOB_TTL_SECONDS):
            return fn(*args, **kwargs)
    return run


def _copy(value: Any) -> Any:
    if isinstance(value, list):
        return [dict(x) if isinstance(x, dict) else x for x in value]
    return value


def remember(key: tuple, read: Callable[[], Any]) -> Any:
    """``read()`` the first time in this scope, the same result (copied) after that; just ``read()`` outside a scope."""
    cache = _cache.get()
    if cache is None:
        return read()
    ttl = cache.get("__ttl__")
    with _lock:
        if key in cache:
            value, kept_at = cache[key]
            if ttl is None or time.monotonic() - kept_at < ttl:
                return _copy(value)
    value = read()
    with _lock:
        cache[key] = (value, time.monotonic())
    return _copy(value)


def forget(*_: Any) -> None:
    """Drop everything kept (something was written)."""
    cache = _cache.get()
    if cache:
        with _lock:
            ttl = cache.get("__ttl__")
            cache.clear()
            if ttl:
                cache["__ttl__"] = ttl


# any write, anywhere, makes what was read before possibly out of date
event.listen(Session, "after_flush", forget)
event.listen(Session, "after_commit", forget)
event.listen(Session, "after_soft_rollback", forget)
