"""Work that runs after a request has been answered, on the app's own few threads.

FastAPI runs the plain (``def``) routes on the event loop's default thread pool; long jobs started from a route (a
home's best-price refresh, a to-do list sync) go here instead, so they never take the threads the pages need.

``submit(fn, *args)`` runs it once, soon. ``coalesce(key, fn, *args)`` runs it once for many calls: while a job with
that key is waiting or running, another call only asks for one more run after it (a shopping list ticked ten times
in a row is synced twice, not ten times). Exceptions are logged, never raised.
"""

from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable

from app.logging_config import get_logger

logger = get_logger("background")

WORKERS = 3
_pool = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="rpi-background")
_lock = threading.Lock()
_state: dict[Any, str] = {}          # key -> "queued" (one run to come) or "again" (one more run after this one)


def _safe(fn: Callable, *args) -> None:
    try:
        fn(*args)
    except Exception:  # noqa: BLE001 - background work never stops anything else
        logger.exception("Background job %s failed", getattr(fn, "__name__", fn))


def submit(fn: Callable, *args) -> Future:
    """Run ``fn(*args)`` on a background thread (errors are logged)."""
    return _pool.submit(_safe, fn, *args)


def coalesce(key: Any, fn: Callable, *args) -> bool:
    """Run ``fn(*args)`` soon, at most once at a time per ``key``. True when a new run was started."""
    with _lock:
        if key in _state:
            _state[key] = "again"
            return False
        _state[key] = "queued"
    _pool.submit(_run_coalesced, key, fn, args)
    return True


def _run_coalesced(key: Any, fn: Callable, args: tuple) -> None:
    while True:
        with _lock:
            _state[key] = "running"
        _safe(fn, *args)
        with _lock:
            if _state.get(key) == "again":
                continue
            _state.pop(key, None)
            return


def pending(key: Any) -> bool:
    with _lock:
        return key in _state
