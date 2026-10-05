"""Background jobs and logging set-up (shared by the household apps: common/python/housekeeping.py,
copied into each app's app/common/ by tools/sync_common.py). Standard library + Starlette; nothing
is imported from the app.

An app registers its jobs, starts them in its FastAPI lifespan and stops them on shutdown:

    async def lifespan(app):
        ...
        jobs = housekeeping.Jobs()
        jobs.every("housekeeping", 600, run_blocking, log=logger, error="Housekeeping failed")  # in a thread
        jobs.every("ha_sync", 900, periodic_sync, thread=False, at_start=False, traceback=False)
        jobs.add("reminders", reminders.loop)      # a loop the app already has (its own timing)
        jobs.start()                               # tasks start in the order registered
        try:
            yield
        finally:
            await jobs.stop()                      # cancel all, then wait for each to end

(Registering in the lifespan looks the functions up at start-up, so tests that replace a module's
`loop` or job function before starting the app still work.) `periodic(seconds, fn, …)` builds the
same loop without registering it, for a module that keeps its own `loop`.

`every()` runs `fn` (a blocking function in Starlette's thread pool, or with `thread=False` an async
function), first straight away (or after one interval with `at_start=False`), then every `seconds`.
With `tick=True`, `fn(n)` gets the run number (0, 1, 2, …) for jobs that do some things only every
n-th time. A failure is logged — `log.exception(error)`, or with `traceback=False`
`log.warning("<error>: <exception>")` — and the job carries on; cancelling the task ends it.

`setup_logging()` is the one logging set-up: "2026-09-21 12:00:00,000 INFO main: …" at INFO.
"""
import asyncio
import contextlib
import logging

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

logger = logging.getLogger("housekeeping")


def setup_logging(level: int = logging.INFO, fmt: str = LOG_FORMAT) -> None:
    """Configure the root logger (logging.basicConfig: a no-op if something configured it already)."""
    logging.basicConfig(level=level, format=fmt)


def periodic(seconds: float, fn, *, thread: bool = True, at_start: bool = True, tick: bool = False,
             log: logging.Logger | None = None, error: str = "Background job failed", traceback: bool = True):
    """An async loop function that runs `fn` every `seconds` (see the module docstring), for Jobs.add()
    — or for an app module to keep as its own `loop` (tests may replace it)."""
    log = log or logger

    async def loop():
        n = 0
        while True:
            if not at_start:
                await asyncio.sleep(seconds)
            try:
                args = (n,) if tick else ()
                if thread:
                    from starlette.concurrency import run_in_threadpool
                    await run_in_threadpool(fn, *args)
                else:
                    await fn(*args)
            except Exception as e:
                if traceback:
                    log.exception(error)
                else:
                    log.warning("%s: %s", error, e)
            n += 1
            if at_start:
                await asyncio.sleep(seconds)

    return loop


class Jobs:
    """An app's background jobs: registered and started in the lifespan, cancelled on shutdown."""

    def __init__(self):
        self._jobs: list[tuple[str, object]] = []      # (name, zero-argument coroutine function)
        self.tasks: list[asyncio.Task] = []

    def add(self, name: str, loop) -> None:
        """Run `loop()` (an async function that runs until cancelled) as a task called `name`."""
        self._jobs.append((name, loop))

    def every(self, name: str, seconds: float, fn, **kw) -> None:
        """Run `fn` every `seconds`: add(name, periodic(seconds, fn, **kw))."""
        self.add(name, periodic(seconds, fn, error=kw.pop("error", None) or f"{name} failed", **kw))

    @property
    def names(self) -> list[str]:
        return [name for name, _ in self._jobs]

    def start(self) -> None:
        """Start every registered job (in registration order) on the running event loop."""
        self.tasks = [asyncio.create_task(loop(), name=name) for name, loop in self._jobs]

    async def stop(self) -> None:
        """Cancel every job, then wait for each to finish (a pass already running in a worker thread
        is not interrupted; the task ends at its next await)."""
        tasks, self.tasks = self.tasks, []
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
