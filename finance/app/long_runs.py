"""Queries with no time limit (SPEC.md section 19.11).

They run in the background so no page request is held open (the ingress proxy could
cut a long one); the page polls for the outcome. Only one runs at a time across the
whole app. The runner can cancel their own; the admin can cancel anyone's. Nothing
survives an app restart.
"""
from __future__ import annotations

import asyncio
import logging
import secrets
import threading
import time
from dataclasses import dataclass, field

from . import query_engine as qe

logger = logging.getLogger(__name__)
KEEP_FINISHED_SECONDS = 600


@dataclass
class LongRun:
    runner_id: str
    acting_id: str
    key: str
    label: str
    id: str = field(default_factory=lambda: secrets.token_urlsafe(10))
    started: float = field(default_factory=time.monotonic)
    finished: float | None = None
    canceller: qe.Canceller = field(default_factory=qe.Canceller)
    result_id: str = ""
    error: str = ""
    cancelled: bool = False

    @property
    def running(self) -> bool:
        return self.finished is None

    @property
    def elapsed(self) -> float:
        return (self.finished or time.monotonic()) - self.started


_lock = threading.Lock()
_runs: dict[str, LongRun] = {}
_tasks: set = set()          # strong references, so a running task is never garbage-collected


def _tidy_locked() -> None:
    now = time.monotonic()
    for rid in [k for k, r in _runs.items() if r.finished and now - r.finished > KEEP_FINISHED_SECONDS]:
        del _runs[rid]


def active() -> LongRun | None:
    with _lock:
        return next((r for r in _runs.values() if r.running), None)


def get(run_id: str) -> LongRun | None:
    with _lock:
        _tidy_locked()
        return _runs.get(run_id)


def start(runner_id: str, acting_id: str, sql: str, params: dict, key: str, label: str,
          on_done=None) -> tuple[LongRun | None, LongRun | None]:
    """(new run, None), or (None, the run in the way) when one is already going.

    A request for the same query by the same person while it's running returns that run."""
    with _lock:
        _tidy_locked()
        busy = next((r for r in _runs.values() if r.running), None)
        if busy is not None:
            if busy.runner_id == runner_id and busy.acting_id == acting_id and busy.key == key:
                return busy, None
            return None, busy
        run = LongRun(runner_id=runner_id, acting_id=acting_id, key=key, label=label)
        _runs[run.id] = run
    task = asyncio.get_running_loop().create_task(_work(run, sql, params, on_done))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return run, None


async def _work(run: LongRun, sql: str, params: dict, on_done) -> None:
    try:
        result = await asyncio.to_thread(qe.execute, run.acting_id, sql, params, time_limit=False,
                                         canceller=run.canceller)
        qe.remember(result, run.runner_id, run.acting_id, run.key)
        run.result_id = result.id
        if on_done:
            await asyncio.to_thread(on_done)
    except qe.QueryCancelled:
        run.cancelled = True
        run.error = "Cancelled."
    except qe.QueryError as err:
        run.error = str(err)
    except Exception:                       # never leave the slot stuck as "running"
        logger.exception("Long query failed")
        run.error = "The query failed unexpectedly."
    finally:
        run.finished = time.monotonic()


def cancel(run_id: str, user_id: str, is_admin: bool) -> bool:
    run = get(run_id)
    if run is None or not run.running or (run.runner_id != user_id and not is_admin):
        return False
    run.canceller.cancel()
    return True


def reset() -> None:
    """Tests only."""
    with _lock:
        for r in _runs.values():
            r.canceller.cancel()
        _runs.clear()
