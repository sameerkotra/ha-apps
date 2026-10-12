"""Household Proxy: the gate every connection passes, the proxied sites (/s/<slug>/…) and the app's own pages.

The gate (spec/SPEC.md §4.1) runs before anything else, for plain requests and websockets alike:
- only the Supervisor's ingress proxy may connect (Home Assistant has already checked who the person is);
- no state-changing request labelled cross-site by the browser — on the app's own API *and* on proxied sites, which
  can't defend themselves here because their Origin/Referer is rewritten to their own address (§3.1);
- no websocket opened by a page on another site.
Then /s/… goes to the proxy (the site's own headers, cleaned) and everything else to the app's pages and API (the
app's own Content-Security-Policy and caching headers).
"""
import logging
import os
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles
from starlette.requests import HTTPConnection, Request
from starlette.responses import RedirectResponse
from starlette.websockets import WebSocket, WebSocketClose

from . import auth, config, db, proxy, rewrite, settings, sidebar, sites, watchdog, wsproxy
from .common import auth_core, deeplinks, ha_people, ha_time, web_security
from .common import housekeeping as jobs_core
from .routers import api

jobs_core.setup_logging()
logger = logging.getLogger("main")

SITE_PATH = re.compile(r"^/s/([a-z0-9-]{1,40})(/.*)?$")


def housekeeping() -> None:
    """Old access-log lines, old checks and incidents."""
    days = int(settings.get("access_log_days"))
    with db.get_conn() as conn:
        conn.execute("DELETE FROM access_log WHERE at < strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ?)", (f"-{days} days",))
    watchdog.housekeeping()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    await ha_time.load(config.ZONE, log=logger)
    await run_in_threadpool(sidebar.load_blocking)
    jobs = jobs_core.Jobs()
    jobs.every("housekeeping", 3600, housekeeping, log=logger, error="Housekeeping failed")
    jobs.add("ha_people", ha_people.loop)                 # people's phones, for alerts
    if os.environ.get("PROXY_NO_WATCHDOG") != "1":        # the tests run rounds by hand
        jobs.add("watchdog", watchdog.loop)
    jobs.start()
    try:
        yield
    finally:
        await jobs.stop()
        await proxy.close_all()


pages = FastAPI(title=config.APP_TITLE, lifespan=lifespan)
pages.include_router(api.router)


@pages.get("/api/health")
def health():
    return {"status": "ok"}


deeplinks.add(pages, {"site": 1, "monitor": 1})

# The app's own pages: scripts only from the app itself; frames only from the app itself (the viewer shows a
# proxied site, which is on the same address, in a frame).
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "connect-src 'self'; font-src 'self'; frame-src 'self'; object-src 'none'; base-uri 'self'; "
       "form-action 'self'; frame-ancestors 'self'")
HEADERS = web_security.SecurityHeaders(CSP, pragma=True, api_no_store="default")
HEADERS.install(pages)

_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(_STATIC_DIR):
    pages.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="frontend")


# --------------------------------------------------------------------------------------------- the proxied sites

async def proxied_http(scope, receive, send) -> None:
    request = Request(scope, receive)
    m = SITE_PATH.match(scope["path"])
    if not m:
        return await proxy.error_page(404, "No such site", "Check the address.")(scope, receive, send)
    slug = m.group(1)
    if m.group(2) is None:                                       # /s/komodo → /s/komodo/ (relative, inside ingress)
        qs = scope.get("query_string", b"").decode("latin-1")
        return await RedirectResponse(f"{slug}/" + (f"?{qs}" if qs else ""), status_code=307)(scope, receive, send)
    user = await run_in_threadpool(auth.cached_user, request)
    if user is None:
        return await proxy.error_page(401, "Not signed in", auth_core.no_user_message(config.APP_TITLE))(
            scope, receive, send)
    site = sites.proxied(slug)
    if site is None:
        return await proxy.error_page(404, "No such site", "This site was removed or renamed.")(scope, receive, send)
    if not sites.can_open(user, site):
        return await proxy.error_page(403, "Not for you", "You aren't on this site's list of people. An admin can "
                                                          "add you in Admin → Sites.")(scope, receive, send)
    if request.method.upper() in ("TRACE", "TRACK", "CONNECT"):     # a TRACE echo would show the extra headers
        return await proxy.error_page(405, "Not allowed", "That request method isn't passed on.")(scope, receive, send)
    rest = proxy.split_path(scope.get("raw_path") or scope["path"].encode(), slug)
    if rest is None:
        return await proxy.error_page(400, "Bad address", "That path isn't allowed.")(scope, receive, send)
    ctx = rewrite.Ctx(ingress=rewrite.ingress_path(request.headers.get("x-ingress-path")), slug=slug,
                      scheme=site.scheme, host=site.host, port=site.port)
    log = proxy.log_writer(site, user, request.method, rest)
    response = await proxy.exchange(request, site, user, ctx, rest, log)
    await response(scope, receive, send)


async def proxied_websocket(scope, receive, send) -> None:
    ws = WebSocket(scope, receive, send)
    m = SITE_PATH.match(scope["path"])
    user = await run_in_threadpool(auth.cached_user, ws)
    site = sites.proxied(m.group(1)) if m else None
    if not m or m.group(2) is None or user is None or site is None or not sites.can_open(user, site):
        return await WebSocketClose(code=1008)(scope, receive, send)
    rest = proxy.split_path(scope.get("raw_path") or scope["path"].encode(), site.slug)
    if rest is None:
        return await WebSocketClose(code=1008)(scope, receive, send)
    await wsproxy.relay(ws, site, user, rest)


# --------------------------------------------------------------------------------------------- the gate

class Gate:
    """The outermost ASGI app (uvicorn runs app.main:app)."""

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        kind = scope["type"]
        if kind not in ("http", "websocket"):
            return await self.inner(scope, receive, send)        # lifespan
        conn = HTTPConnection(scope)
        if not auth_core.from_ingress(conn):
            if kind == "websocket":
                return await WebSocketClose(code=1008)(scope, receive, send)
            return await auth_core.forbidden()(scope, receive, send)
        if kind == "http":
            request = Request(scope)
            blocked = web_security.refuse_cross_site(request, log=logger)
            if blocked is not None:
                return await blocked(scope, receive, send)
        elif web_security.cross_origin_websocket(conn):
            logger.warning("Refused a websocket from another site: %s", scope["path"][:100])
            return await WebSocketClose(code=1008)(scope, receive, send)
        if scope["path"].startswith("/s/") or scope["path"] == "/s":
            if kind == "websocket":
                return await proxied_websocket(scope, receive, send)
            return await proxied_http(scope, receive, send)
        return await self.inner(scope, receive, send)


app = Gate(pages)
