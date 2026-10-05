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
  - `all_no_store`: every response `no-store` (replaces whatever the route set);
  - `api_no_store`: paths under `/api/` get `no-store` — `"set"` replaces a route's own
    Cache-Control, `"default"` keeps it; `api_skip(path) -> bool` exempts some API paths (files,
    photos that may be cached);
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

Needs nothing from the app.
"""
from __future__ import annotations

import logging
from typing import Callable, Optional, Union
from urllib.parse import urlparse

from starlette.responses import JSONResponse

logger = logging.getLogger("web_security")

PAGE_CACHE = "no-cache, no-store, must-revalidate"


def is_page(path: str) -> bool:
    """The HTML shell: "/" or any *.html path."""
    return path == "/" or path.endswith(".html")


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
                 api_no_store: Optional[str] = None,
                 api_skip: Optional[Callable[[str], bool]] = None,
                 pages_no_cache: bool = True,
                 pragma: bool = False,
                 other_no_store: bool = False):
        if api_no_store not in (None, "set", "default"):
            raise ValueError("api_no_store must be None, 'set' or 'default'")
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
        if self.all_no_store:
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


def cross_origin_websocket(ws) -> bool:
    """A WebSocket whose `Origin` header names another host than its `Host` (a page on another site
    opening it). No Origin (not a browser): False."""
    origin = ws.headers.get("origin")
    return bool(origin) and urlparse(origin).netloc != ws.headers.get("host")
