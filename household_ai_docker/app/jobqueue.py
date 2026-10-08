"""The gateway's queue: one request runs at a time (SPEC.md §6.1).

- Callers on *Answer first* go ahead of the others; then arrival order. A running request is never interrupted.
- No limit on waiting time: a waiting request stays until its turn. The bound is the caller's own timeout — the
  gateway drops a request whose caller has gone (`leave()`).
- At most `limit` requests wait (the *Queue length* setting), and at most PER_CALLER from one caller; beyond
  that `enter()` raises Full with a Retry-After: the running job's expected time left, from the model's recent
  seconds per request, between 10 and 60 s.
"""
import asyncio
import itertools
import time

PER_CALLER = 2
RETRY_MIN, RETRY_MAX = 10, 60


class Full(Exception):
    def __init__(self, message: str, retry_after: int):
        super().__init__(message)
        self.retry_after = retry_after


class Ticket:
    _ids = itertools.count(1)

    def __init__(self, caller: str, model: str, first: bool, now: float):
        self.id = next(self._ids)
        self.caller, self.model, self.first = caller, model, first
        self.entered = now
        self.started: float | None = None
        self.granted = asyncio.Event()

    def waited(self, now: float) -> float:
        return (self.started if self.started is not None else now) - self.entered


class RequestQueue:
    def __init__(self, limit=lambda: 4, clock=time.monotonic):
        self.limit = limit
        self.clock = clock
        self.running: Ticket | None = None
        self.waiting: list[Ticket] = []
        self.seconds: dict[str, float] = {}      # model → recent seconds per request (moving average)

    # ------------------------------------------------------------------ entering and leaving
    def enter(self, caller: str, model: str, first: bool = False) -> Ticket:
        """A ticket for this request: granted at once when nothing runs, else waiting. Raises Full."""
        t = Ticket(caller, model, first, self.clock())
        if self.running is None and not self.waiting:
            self._grant(t)
            return t
        if len(self.waiting) >= max(1, int(self.limit())):
            raise Full("The model is busy and the queue is full — try again shortly.", self.retry_after())
        if sum(1 for w in self.waiting if w.caller == caller) >= PER_CALLER:
            raise Full("This app already has 2 requests waiting — try again shortly.", self.retry_after())
        self.waiting.append(t)
        return t

    async def wait(self, t: Ticket) -> None:
        """Until the ticket is granted. Cancelling it (the caller went away) leaves the queue."""
        try:
            await t.granted.wait()
        except asyncio.CancelledError:
            self.leave(t)
            raise

    def leave(self, t: Ticket) -> None:
        """The request is done (or its caller went away): free its place, grant the next."""
        if t in self.waiting:
            self.waiting.remove(t)
            return
        if self.running is t:
            if t.started is not None:
                took = self.clock() - t.started
                old = self.seconds.get(t.model)
                self.seconds[t.model] = took if old is None else 0.7 * old + 0.3 * took
            self.running = None
            self._next()

    def _grant(self, t: Ticket) -> None:
        self.running = t
        t.started = self.clock()
        t.granted.set()

    def _next(self) -> None:
        if self.running is not None or not self.waiting:
            return
        first = [w for w in self.waiting if w.first]
        nxt = (first or self.waiting)[0]
        self.waiting.remove(nxt)
        self._grant(nxt)

    # ------------------------------------------------------------------ what the page and 503s say
    def retry_after(self) -> int:
        r = self.running
        if r is None or r.started is None:
            return RETRY_MIN
        expected = self.seconds.get(r.model, 30.0)
        left = expected - (self.clock() - r.started)
        return int(min(RETRY_MAX, max(RETRY_MIN, left)))

    def idle(self) -> bool:
        return self.running is None and not self.waiting

    def snapshot(self) -> dict:
        now = self.clock()
        r = self.running
        return {"running": None if r is None else {"caller": r.caller, "model": r.model,
                                                    "seconds": round(now - (r.started or now), 1)},
                "waiting": [{"caller": w.caller, "model": w.model, "seconds": round(now - w.entered, 1)}
                            for w in self.waiting]}
