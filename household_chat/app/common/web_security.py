# Shared file: edit common/python/web_security.py and run tools/sync_common.py; don't edit this copy. sha256=91ccf281cf10ce12b332a21b67cd814c9a17d69304ce0871127d916a529751d7
"""Security and caching headers for every response (shared by the household apps).

One `SecurityHeaders` object per app, built from its own policy, then either

* `HEADERS.apply(request, response)` at the end of the app's own guard middleware (the apps that
  check the ingress source there), or
* `HEADERS.install(app)` to add it as its own `@app.middleware("http")`.

What it sets, each part optional:

* `Content-Security-Policy` — `csp` is the policy string, or a function `csp(request) -> str` for a
  policy that depends on the request or on a setting. Never replaces a policy a route set itself
  (e.g. `sandbox` on a user's file). `csp_skip(request, response) -> bool` leaves it off a response
  (e.g. files served for download/viewing, or PDFs, which the browser's viewer would refuse).
* `X-Content-Type-Options: nosniff` (`nosniff=True`).
* `Referrer-Policy` (`referrer="no-referrer"` / `"same-origin"` / None).
* `Cache-Control`, first rule that matches:
  - `static_immutable=True` (opt-in): a versioned asset — a successful (200/304) answer for a path under
    one of the `static_prefixes` (default "/static/" and "/common/") whose query carries `v=` — gets
    `public, max-age=31536000, immutable` and none of the no-store rules below (a route's own
    Cache-Control is kept). Turn it on only when every page links its assets with a `?v=` that changes
    on every release (e.g. the app's version), or a browser keeps the old file for a year. Never
    applied to the HTML shell (`is_page`), the API or an error answer;
  - `all_no_store`: every other response `no-store` (replaces whatever the route set);
  - `api_no_store`: paths under `/api/` get `no-store` — `"set"` replaces a route's own
    Cache-Control, `"default"` (the default: personal JSON must never come from a cache) keeps it;
    `None` leaves the API alone; `api_skip(path) -> bool` exempts some API paths (files, photos that
    may be cached);
  - `pages_no_cache`: the HTML shell (`/` and `*.html`) gets `no-cache, no-store, must-revalidate`
    (plus `Pragma: no-cache` with `pragma=True`) — it points at the ?v= assets, so it must never be
    cached;
  - `other_no_store`: everything else outside `/api/` gets `no-store` unless the route set its own.

Cross-site requests (a second line of defence behind the secret ingress URL and the session cookie):

* `refuse_cross_site(request)` — for the app's guard middleware, right after the ingress-source check:
  a 403 for a state-changing request (any method but GET, HEAD and OPTIONS) that the browser labels
  `Sec-Fetch-Site: cross-site` or `same-site` (a form or fetch from another site), else None.
  `same-origin`, `none` (typed / bookmarked) and a missing header (older Companion-app web views, curl)
  are allowed. Messages between the apps go over Home Assistant's event bus, not HTTP, so they never
  meet this check.
* `cross_origin_websocket(ws)` — a WebSocket whose `Origin` names another host than the one it
  connected to (HTTP middleware doesn't see WebSockets, so the route checks it).

Request bodies (`BodyLimit`, a pure ASGI middleware). Add it BEFORE the app's own http middleware
(`@app.middleware("http")` with refuse_outsiders) and `SecurityHeaders.install(app)`: Starlette runs the
middleware added last first, so an outsider still gets its 403 first and a 413 still gets the security
headers. (Added last, outermost, it works as well; its answers then carry only `Cache-Control: no-store`.)

    app.add_middleware(web_security.BodyLimit, default_max=1 << 20,
                       routes={"/api/files": 50 << 20, "/api/admin/restore": 2 << 30},
                       no_chunked_prefixes=("/api/admin/restore",))
    @app.middleware("http")
    async def guard(request, call_next): ...            # refuse_outsiders / refuse_cross_site
    HEADERS.install(app)

  A request whose Content-Length is over its limit (the longest matching `routes` prefix, else
  `default_max`) is answered 413 before the route runs. A body without Content-Length (chunked) is
  counted as it arrives and cut off past the limit: the route sees a `ClientDisconnect`/short body and
  the client a 413 when nothing has been sent yet. Routes in `no_chunked_prefixes` refuse chunked
  bodies with 411 (they need the length up front, e.g. to pick a temp folder). `streamed_prefixes`:
  routes that stream very large bodies themselves and keep their own limit (no counting, but the
  Content-Length check still applies with their `routes` limit). GET/HEAD/OPTIONS bodies get the
  default limit too. Limits are bytes.

Needs nothing from the app.
"""
from __future__ import annotations

import logging
from typing import Callable, Optional, Union
from urllib.parse import urlparse

from starlette.responses import JSONResponse

logger = logging.getLogger("web_security")

PAGE_CACHE = "no-cache, no-store, must-revalidate"
IMMUTABLE = "public, max-age=31536000, immutable"
STATIC_PREFIXES = ("/static/", "/common/")


def is_page(path: str) -> bool:
    """The HTML shell: "/" or any *.html path."""
    return path == "/" or path.endswith(".html")


def is_versioned_asset(path: str, query: str, prefixes=STATIC_PREFIXES) -> bool:
    """A file under one of `prefixes` asked for with a `v=` query: the version is in the address, so
    the browser may keep it for good. The HTML shell and anything under /api/ never are (an app whose
    static files are mounted at "/" passes prefixes=("/",))."""
    if is_page(path) or path.startswith("/api/") or not any(path.startswith(p) for p in prefixes):
        return False
    return any(part.startswith("v=") and len(part) > 2 for part in (query or "").split("&"))


def is_pdf(_request, response) -> bool:
    """For `csp_skip`: a PDF response (Chrome's PDF viewer refuses to show a PDF whose own CSP has
    object-src 'none')."""
    return (response.headers.get("content-type") or "").split(";")[0].strip().lower() == "application/pdf"


class SecurityHeaders:
    def __init__(self, csp: Union[str, Callable, None] = None, *,
                 csp_skip: Optional[Callable] = None,
                 nosniff: bool = True,
                 referrer: Optional[str] = None,
                 all_no_store: bool = False,
                 api_no_store: Optional[str] = "default",
                 api_skip: Optional[Callable[[str], bool]] = None,
                 pages_no_cache: bool = True,
                 pragma: bool = False,
                 other_no_store: bool = False,
                 static_immutable: bool = False,
                 static_prefixes=STATIC_PREFIXES):
        if api_no_store not in (None, "set", "default"):
            raise ValueError("api_no_store must be None, 'set' or 'default'")
        self.static_immutable = static_immutable
        self.static_prefixes = tuple(static_prefixes)
        self.csp = csp
        self.csp_skip = csp_skip
        self.nosniff = nosniff
        self.referrer = referrer
        self.all_no_store = all_no_store
        self.api_no_store = api_no_store
        self.api_skip = api_skip
        self.pages_no_cache = pages_no_cache
        self.pragma = pragma
        self.other_no_store = other_no_store

    def policy(self, request) -> Optional[str]:
        """The CSP for this request (None: no CSP)."""
        return self.csp(request) if callable(self.csp) else self.csp

    def apply(self, request, response):
        """Add the headers to `response` (for `request`) and return it."""
        headers = response.headers
        if self.csp is not None and "content-security-policy" not in headers and not (
                self.csp_skip is not None and self.csp_skip(request, response)):
            policy = self.policy(request)
            if policy:
                headers["Content-Security-Policy"] = policy
        if self.nosniff:
            headers["X-Content-Type-Options"] = "nosniff"
        if self.referrer:
            headers["Referrer-Policy"] = self.referrer
        path = request.url.path
        if (self.static_immutable and response.status_code in (200, 304)
                and is_versioned_asset(path, getattr(request.url, "query", ""), self.static_prefixes)):
            if "cache-control" not in headers:
                headers["Cache-Control"] = IMMUTABLE
        elif self.all_no_store:
            headers["Cache-Control"] = "no-store"
        elif (self.api_no_store and path.startswith("/api/")
              and not (self.api_skip is not None and self.api_skip(path))):
            if self.api_no_store == "set" or "cache-control" not in headers:
                headers["Cache-Control"] = "no-store"
        elif self.pages_no_cache and is_page(path):
            headers["Cache-Control"] = PAGE_CACHE
            if self.pragma:
                headers["Pragma"] = "no-cache"
        elif self.other_no_store and not path.startswith("/api/") and "cache-control" not in headers:
            headers["Cache-Control"] = "no-store"
        return response

    def install(self, app) -> None:
        """Add `apply` as an HTTP middleware of `app` (outermost of those added so far)."""
        async def security_headers(request, call_next):
            return self.apply(request, await call_next(request))
        app.middleware("http")(security_headers)


# ---------------------------------------------------------------------------------------------
# Cross-site requests
# ---------------------------------------------------------------------------------------------
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
CROSS_SITE = frozenset({"cross-site", "same-site"})
CROSS_SITE_DETAIL = "Forbidden: cross-site request"


def cross_site(request) -> bool:
    """A state-changing request (not GET / HEAD / OPTIONS) that the browser says came from another site
    (`Sec-Fetch-Site: cross-site` or `same-site`). No header, `same-origin` or `none`: False."""
    if request.method in SAFE_METHODS:
        return False
    return (request.headers.get("sec-fetch-site") or "").strip().lower() in CROSS_SITE


def refuse_cross_site(request, *, detail: str = CROSS_SITE_DETAIL, response: Optional[Callable] = None,
                      log: Optional[logging.Logger] = None):
    """For the guard middleware: the 403 for a `cross_site` request (JSON {"detail": detail}, or
    `response()` — the app's own), logged as a warning on `log`; None otherwise."""
    if not cross_site(request):
        return None
    (log or logger).warning("Refused cross-site %s %s", request.method, request.url.path)
    if response is not None:
        return response()
    return JSONResponse(status_code=403, content={"detail": detail})


# ---------------------------------------------------------------------------------------------
# Request body limits
# ---------------------------------------------------------------------------------------------
TOO_LARGE_DETAIL = "The request body is larger than this route allows ({limit})."
LENGTH_REQUIRED_DETAIL = "This route needs the Content-Length of the upload."


def size_text(n: int) -> str:
    """1048576 → "1 MB"; small limits in KB or bytes."""
    for unit, size in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if n >= size:
            return f"{n / size:.3g} {unit}"
    return f"{n} bytes"


def _under(path: str, prefix: str) -> bool:
    """`path` is `prefix` or inside it (a prefix ending in "/" is a folder already)."""
    if prefix.endswith("/"):
        return path.startswith(prefix) or path == prefix.rstrip("/")
    return path == prefix or path.startswith(prefix + "/")


class BodyLimit:
    """ASGI middleware: refuse request bodies over a size (see the module docstring)."""

    def __init__(self, app, default_max: int = 1 << 20, routes: Optional[dict] = None,
                 streamed_prefixes=(), no_chunked_prefixes=(), detail: str = TOO_LARGE_DETAIL,
                 length_required_detail: str = LENGTH_REQUIRED_DETAIL):
        self.app = app
        self.default_max = int(default_max)
        self.routes = sorted(((str(k), int(v)) for k, v in (routes or {}).items()), key=lambda kv: -len(kv[0]))
        self.streamed = tuple(streamed_prefixes)
        self.no_chunked = tuple(no_chunked_prefixes)
        self.detail = detail
        self.length_required_detail = length_required_detail

    def limit_for(self, path: str) -> int:
        """The limit for `path`: the longest `routes` prefix that is the path itself or a folder of it
        ("/api/files" covers "/api/files/7", not "/api/filesystem"), else `default_max`."""
        for prefix, limit in self.routes:            # longest prefix first
            if _under(path, prefix):
                return limit
        return self.default_max

    @staticmethod
    def _header(scope, name: bytes) -> Optional[str]:
        for k, v in scope.get("headers") or ():
            if k.lower() == name:
                return v.decode("latin-1")
        return None

    @staticmethod
    async def _refuse(send, status: int, detail: str) -> None:
        body = JSONResponse(status_code=status, content={"detail": detail}).body
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
                                (b"cache-control", b"no-store"), (b"connection", b"close")]})
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path") or "/"
        limit = self.limit_for(path)
        length = self._header(scope, b"content-length")
        chunked = length is None and "chunked" in (self._header(scope, b"transfer-encoding") or "").lower()
        if length is not None:
            try:
                n = int(length)
            except ValueError:
                n = -1
            if n < 0:
                return await self._refuse(send, 400, "Bad Content-Length.")
            if n > limit:
                return await self._refuse(send, 413, self.detail.format(limit=size_text(limit)))
        elif chunked and any(_under(path, p) for p in self.no_chunked):
            return await self._refuse(send, 411, self.length_required_detail)
        if length is not None or any(_under(path, p) for p in self.streamed):
            return await self.app(scope, receive, send)      # the length (or the route) keeps the limit

        # No Content-Length: count the body as it arrives.
        seen = {"bytes": 0, "started": False, "over": False}

        async def counting_receive():
            message = await receive()
            if message.get("type") == "http.request":
                seen["bytes"] += len(message.get("body") or b"")
                if seen["bytes"] > limit:
                    if not seen["over"]:
                        logger.warning("Refused a chunked body over %s on %s", size_text(limit), path)
                    seen["over"] = True
                    return {"type": "http.disconnect"}
            return message

        async def guarded_send(message):
            if seen["over"] and not seen["started"]:
                # whatever the route answered (usually a 500 for the disconnect it saw): a 413 instead
                seen["started"] = True
                seen["replaced"] = True
                return await self._refuse(send, 413, self.detail.format(limit=size_text(limit)))
            if seen.get("replaced"):
                return                                         # the rest of the replaced answer
            if message.get("type") == "http.response.start":
                seen["started"] = True
            await send(message)

        try:
            await self.app(scope, counting_receive, guarded_send)
        except Exception:
            if not seen["over"]:
                raise                                          # not ours
            # the route failed on the disconnect it was handed (ClientDisconnect): the 413 below answers
        if seen["over"] and not seen["started"]:
            await self._refuse(send, 413, self.detail.format(limit=size_text(limit)))


def cross_origin_websocket(ws) -> bool:
    """A WebSocket whose `Origin` header names another host than its `Host` (a page on another site
    opening it). No Origin (not a browser): False."""
    origin = ws.headers.get("origin")
    return bool(origin) and urlparse(origin).netloc != ws.headers.get("host")
