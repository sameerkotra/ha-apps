"""One limit for the app's heavy worker processes together (review 2026-10 DP7): reading an .xlsx, reading a PDF's
text and making a note's PDF each run in their own process with up to about 1 GB of memory. Each kind already
runs few at a time; together at most HEAVY_SLOTS run at once, so a small machine (a Raspberry Pi) is never asked
for several gigabytes by them at the same moment. Regular-expression search workers are small (256 MB each) and
have their own limit (search/regex_worker.py)."""
import threading
from contextlib import contextmanager

HEAVY_SLOTS = 2
_heavy = threading.BoundedSemaphore(HEAVY_SLOTS)


class Busy(Exception):
    """No slot came free in time."""


@contextmanager
def heavy(timeout: float | None = 120):
    if not _heavy.acquire(timeout=timeout):
        raise Busy()
    try:
        yield
    finally:
        _heavy.release()
