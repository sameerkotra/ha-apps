"""The background job queue.

Every uploaded PDF — bank statement, utility bill or toll statement — is
parsed here, strictly one at a time, in upload order. The vision extraction is
a multi-minute call to a local Ollama model, and several at once would only
make each one slower (they fight over the same GPU). OLLAMA_LOCK is held for
the whole of every job and every admin debug re-send, so nothing in this app
ever asks the model to do two vision jobs at once.

The queue lives in memory, so a restart loses it. recover_interrupted_jobs()
runs at startup and turns anything left 'processing' into an error the person
can Restart (SPEC.md section 4).
"""
import asyncio
import logging
import threading

from . import ai_usage, settings
from .db import get_db
from .parser.pipeline import process_statement
from .parser.toll_pipeline import process_toll_statement
from .parser.utility_pipeline import process_utility_bill

logger = logging.getLogger(__name__)

OLLAMA_LOCK = threading.Lock()

TABLES = {"statement": "statements", "utility_bill": "utility_bills", "toll": "toll_statements"}
INTERRUPTED_MESSAGE = "Processing was interrupted (the app restarted). Click Restart to run it again."

_loop: asyncio.AbstractEventLoop | None = None
_queue: "asyncio.Queue[tuple[str, int, str, int | None]] | None" = None
_busy = False  # True while a job is actually running, not just waiting on the queue
_task: "asyncio.Task | None" = None


def exclusive(fn, *args):
    """Run fn(*args) while holding OLLAMA_LOCK (blocking; call from a thread)."""
    with OLLAMA_LOCK:
        return fn(*args)


def exclusive_recorded(fn, *args):
    """exclusive(), also returning the AI usage of that call: (result, calls)."""
    with OLLAMA_LOCK, ai_usage.recording() as calls:
        return fn(*args), list(calls)


def _get_queue() -> asyncio.Queue:
    global _queue
    if _queue is None:
        _queue = asyncio.Queue()
    return _queue


def _run(kind: str, job_id: int, user_id: str, account_id: int | None) -> None:
    """The address and model are read from Admin → App settings for every job, so a change there
    applies to the next job without a restart. Every AI request the job makes is stored on
    its row (ai_usage) for the debug page — this run's only, replacing an earlier run's."""
    url, model = settings.ai_url(), settings.ai_model()
    with ai_usage.recording() as calls:
        try:
            if kind == "statement":
                process_statement(job_id, user_id, account_id, url, model)
            elif kind == "toll":
                process_toll_statement(job_id, user_id, url, model)
            else:
                process_utility_bill(job_id, user_id, url, model)
        finally:
            try:
                with get_db() as conn:
                    conn.execute(f"UPDATE {TABLES[kind]} SET ai_usage = ? WHERE id = ?", (ai_usage.to_json(calls), job_id))
            except Exception:
                logger.exception("Couldn't store AI usage for %s %s", kind, job_id)


async def _worker_loop() -> None:
    """The single consumer. Each pipeline already records its own failures on
    its row; the except here only guards the loop itself, so one bad job can
    never stop everything queued behind it."""
    global _busy
    queue = _get_queue()
    while True:
        kind, job_id, user_id, account_id = await queue.get()
        _busy = True
        try:
            await asyncio.to_thread(exclusive, _run, kind, job_id, user_id, account_id)
        except Exception:
            logger.exception("Background job for %s %s crashed outside its own handling", kind, job_id)
        finally:
            _busy = False
            queue.task_done()


def start(loop: asyncio.AbstractEventLoop) -> None:
    """Called once from main.py's lifespan, on the running event loop."""
    global _loop, _task
    _loop = loop
    if _task is None:
        _task = loop.create_task(_worker_loop())


async def _enqueue(kind: str, job_id: int, user_id: str, account_id: int | None) -> None:
    """Queue the job; if others are ahead of it, say so in current_step (the
    Uploads page shows it) — overwritten by the job's own steps once it starts."""
    queue = _get_queue()
    ahead = queue.qsize() + (1 if _busy else 0)
    await queue.put((kind, job_id, user_id, account_id))  # first, so the job is never lost
    if ahead > 0:
        note = (f"Queued \u2014 waiting for {ahead} earlier item{'s' if ahead != 1 else ''} to "
                "finish (processed one at a time so they don't contend for the same local model)")
        try:
            await asyncio.to_thread(_set_queued_note, TABLES[kind], job_id, note)
        except Exception:
            logger.warning("Could not record the queue position of %s %s", kind, job_id)


def _set_queued_note(table: str, job_id: int, note: str) -> None:
    with get_db() as conn:
        conn.execute(f"UPDATE {table} SET current_step = ? WHERE id = ?", (note, job_id))


def start_job(kind: str, job_id: int, user_id: str, account_id: int | None = None) -> None:
    """Queue a job from any thread (sync routes run in a worker thread, not on
    the event loop). kind: "statement" (needs account_id), "utility_bill", "toll"."""
    if kind not in TABLES:
        raise ValueError(kind)
    if _loop is None:
        raise RuntimeError("Job queue not started — main.py's lifespan must call jobs.start()")
    future = asyncio.run_coroutine_threadsafe(_enqueue(kind, job_id, user_id, account_id), _loop)

    def _log_if_failed(f) -> None:
        exc = f.exception()
        if exc is not None:
            logger.error("Failed to enqueue %s %s for processing: %r", kind, job_id, exc)

    future.add_done_callback(_log_if_failed)


def recover_interrupted_jobs() -> int:
    """Anything still 'processing' at startup was cut off by the restart (the
    queue that held it is gone). Mark it as an error so Restart is offered;
    Restart already knows how to clean up a partial earlier run."""
    total = 0
    with get_db() as conn:
        for table in TABLES.values():
            total += conn.execute(
                f"UPDATE {table} SET status = 'error', error_message = ?, current_step = NULL "
                "WHERE status = 'processing'",
                (INTERRUPTED_MESSAGE,),
            ).rowcount
    if total:
        logger.warning("Marked %d interrupted job(s) as needing Restart", total)
    return total


def busy_reason() -> str | None:
    """Why the local model is busy right now (a PDF being read or queued), or None when it's free."""
    waiting = _queue.qsize() if _queue is not None else 0
    if _busy or OLLAMA_LOCK.locked():
        more = f", with {waiting} more waiting" if waiting else ""
        return f"it is reading an uploaded PDF{more}"
    if waiting:
        return f"{waiting} uploaded PDF{'s are' if waiting != 1 else ' is'} waiting to be read"
    return None
