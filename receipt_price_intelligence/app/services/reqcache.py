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
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable

from sqlalchemy import event
from sqlalchemy.orm import Session

_cache: ContextVar[dict | None] = ContextVar("reqcache", default=None)
_lock = threading.Lock()


@contextmanager
def scope():
    """Keep reads for the length of this block (a request, a job). Nested scopes share the outer one."""
    if _cache.get() is not None:
        yield
        return
    token = _cache.set({})
    try:
        yield
    finally:
        _cache.reset(token)


def scoped(fn: Callable) -> Callable:
    """A function that runs in its own ``scope()`` (a background job's entry point)."""
    import functools

    @functools.wraps(fn)
    def run(*args: Any, **kwargs: Any) -> Any:
        with scope():
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
    with _lock:
        if key in cache:
            return _copy(cache[key])
    value = read()
    with _lock:
        cache[key] = value
    return _copy(value)


def forget(*_: Any) -> None:
    """Drop everything kept (something was written)."""
    cache = _cache.get()
    if cache:
        with _lock:
            cache.clear()


# any write, anywhere, makes what was read before possibly out of date
event.listen(Session, "after_flush", forget)
event.listen(Session, "after_commit", forget)
event.listen(Session, "after_soft_rollback", forget)
