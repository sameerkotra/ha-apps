# Shared file: edit common/python/geo.py and run tools/sync_common.py; don't edit this copy. sha256=615fd8beb762d7dbd91ece3dcd66aea31fb00fae0611afd75c43091875d07ebf
"""Places on the map: OpenStreetMap Nominatim (address -> coordinates) and OSRM-compatible routing
servers (driving times), the polite way (shared by the household apps: common/python/geo.py, copied
into each app's app/common/ by tools/sync_common.py). Standard library only; nothing is imported
from the app.

The public servers run on donated resources: at most one address search per second, an identifying
User-Agent, and each answer kept by the app (its own cache table) so an address is looked up once.
Each app keeps its own server settings, User-Agent, queries (and how it retries a query), cache
tables, timeouts and error handling; this module is the mechanics.

    THROTTLE = geo.Throttle(1.05)                  # or a function, read each time (tests set it to 0)
    THROTTLE.wait()                                # before a request: sleeps until 1.05 s after the last one
    with THROTTLE.spaced():                        # or: one request at a time, spaced from the end of the last
        body = geo.fetch(url, headers={...}, timeout=20)

Where to call it: `wait()` books the next slot under the lock and sleeps OUTSIDE it, so ten callers
sleep for their own slots at once (1, 2, … 10 s) instead of queueing on the lock — but each still
occupies its thread. Geocode from ONE background job (the app's housekeeping, a queue table) rather
than from request threads: twenty members saving addresses at once would otherwise hold twenty of
the server's worker threads for twenty seconds. `wait(max_wait=…)` raises Busy instead of sleeping
longer than that — for a request that must answer now. `spaced()` serialises the request itself (for
servers that allow one connection); it holds the lock while the request runs.

    url = geo.search_url(base, "12 Main St, Springfield")       # {base}/search?q=…&format=json&limit=1
    lat, lon = geo.first_result(body)              # the first hit of a Nominatim answer (raises if none)
    geo.lonlat([(lat, lon), …])                    # "lon,lat;lon,lat" for an OSRM URL

`fetch()` raises what urllib raises (HTTPError, URLError, TimeoutError, OSError); the caller decides
what a failure means for it.
"""
import contextlib
import json
import threading
import time
import urllib.parse
import urllib.request


class Busy(RuntimeError):
    """wait(max_wait=…): the next free slot is further away than the caller can wait."""


class Throttle:
    """Keeps requests to one server at least `interval` seconds apart, across every thread."""

    def __init__(self, interval):
        self._interval = interval
        self._lock = threading.Lock()
        self.last = 0.0                  # time.monotonic() of the last request (see wait / spaced)

    def interval(self) -> float:
        return self._interval() if callable(self._interval) else self._interval

    def _pause(self) -> None:
        wait = self.interval() - (time.monotonic() - self.last)
        if wait > 0:
            time.sleep(wait)

    def wait(self, max_wait: float | None = None) -> None:
        """Block until the interval has passed since the last request started; this one starts now. The
        slot is booked under the lock and the sleep happens outside it, so waiting callers don't queue on
        the lock. With `max_wait`, a slot further away than that raises Busy (nothing is booked)."""
        with self._lock:
            now = time.monotonic()
            slot = max(now, self.last + self.interval())
            if max_wait is not None and slot - now > max_wait:
                raise Busy(f"next slot in {slot - now:.1f} s")
            self.last = slot
        delay = slot - time.monotonic()
        if delay > 0:
            time.sleep(delay)

    @contextlib.contextmanager
    def spaced(self):
        """One request at a time (the lock is held while it runs), starting at least the interval after
        the previous one ended."""
        with self._lock:
            self._pause()
            try:
                yield
            finally:
                self.last = time.monotonic()


def fetch(url: str, *, headers: dict | None = None, timeout: float = 10, data: bytes | None = None,
          method: str | None = None) -> bytes:
    """The body of one HTTP request (GET, or POST with `data`). Raises urllib's errors. A plain GET
    (no headers) is urlopen(url) itself, as Python sends it."""
    if headers is None and data is None and method is None:
        req = url
    else:
        req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def search_url(base: str, query: str, *, format: str = "json", limit=1, extra: dict | None = None) -> str:
    """A Nominatim search: {base}/search?q=…&format=…&limit=…[&extra…]."""
    return base + "/search?" + urllib.parse.urlencode({"q": query, "format": format, "limit": limit, **(extra or {})})


def first_result(body) -> tuple[float, float]:
    """(lat, lon) of the first hit in a Nominatim JSON answer. Raises ValueError / KeyError / IndexError /
    TypeError when there is none or the answer is odd."""
    results = json.loads(body)
    return float(results[0]["lat"]), float(results[0]["lon"])


def lonlat(points, precision: int | None = None) -> str:
    """(lat, lon) points as OSRM wants them: "lon,lat;lon,lat" (with `precision` decimals if given)."""
    if precision is None:
        return ";".join(f"{lon},{lat}" for lat, lon in points)
    return ";".join(f"{lon:.{precision}f},{lat:.{precision}f}" for lat, lon in points)
