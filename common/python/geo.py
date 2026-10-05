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

    def wait(self) -> None:
        """Block until the interval has passed since the last request started; this one starts now."""
        with self._lock:
            self._pause()
            self.last = time.monotonic()

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
