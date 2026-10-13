"""Holding everything still while a backup is restored.

Importing a backup replaces the database file. A connection opened before the swap keeps writing to the old,
deleted file, and those writes are lost; a job half-way through could also mix old and new data. So while a restore
runs, nothing else touches the database:

* every API request (except the restore itself) is counted in ``work()``; while a restore is waiting or running, a
  new request is answered 503 "try again in a moment" (``ingress_gate`` in main.py);
* every background job (the daily check, imports, the minute loop, saving a model's reading) runs inside
  ``GATE.work()`` too, and waits while a restore runs;
* the restore takes ``GATE.pause()``: it waits for the requests and jobs already running to finish (60 s at most),
  then runs alone.

``generation`` goes up by one per restore, so a job that read something before can tell it is out of date.

(The household apps' shared ``db_core.MaintenanceGate`` does the same; it isn't part of this app's shared code yet.)
"""

from __future__ import annotations

import contextlib
import threading
import time


class Busy(RuntimeError):
    """A restore is running (or waiting to): try again in a moment."""


class Gate:
    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._active = 0
        self._paused = False
        self.generation = 0

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def active(self) -> int:
        return self._active

    def try_enter(self) -> bool:
        """Count one piece of work in, unless a restore is waiting or running (then False, nothing counted)."""
        with self._cond:
            if self._paused:
                return False
            self._active += 1
            return True

    def leave(self) -> None:
        with self._cond:
            self._active -= 1
            self._cond.notify_all()

    @contextlib.contextmanager
    def work(self, timeout: float | None = None):
        """Run a piece of work; waits while a restore runs (TimeoutError after ``timeout`` seconds)."""
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._cond:
            while self._paused:
                left = None if deadline is None else deadline - time.monotonic()
                if left is not None and left <= 0:
                    raise TimeoutError("A backup is being restored")
                self._cond.wait(left)
            self._active += 1
        try:
            yield
        finally:
            self.leave()

    @contextlib.contextmanager
    def pause(self, timeout: float = 60, own: int = 0):
        """Run alone: new work waits (or is refused), and work already running (other than ``own`` pieces the caller
        is itself counted as) is waited for, ``timeout`` seconds at most (then Busy, and nothing was paused)."""
        deadline = time.monotonic() + timeout
        with self._cond:
            while self._paused:                       # another restore first
                left = deadline - time.monotonic()
                if left <= 0:
                    raise Busy("Another restore is running")
                self._cond.wait(left)
            self._paused = True
            self.generation += 1
            while self._active > own:
                left = deadline - time.monotonic()
                if left <= 0:
                    self._paused = False
                    self._cond.notify_all()
                    raise Busy("The app is busy; try the restore again in a moment")
                self._cond.wait(left)
        try:
            yield
        finally:
            with self._cond:
                self._paused = False
                self._cond.notify_all()


GATE = Gate()


def run(fn, *args):
    """``fn(*args)`` inside ``GATE.work()`` (for background jobs handed to a thread)."""
    with GATE.work():
        return fn(*args)
