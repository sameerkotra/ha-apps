"""Household Proxy: the gate every connection passes, the proxied sites (/s/<slug>/…) and the app's own pages.

The gate (spec/SPEC.md §4.1) runs before anything else, for plain requests and websockets alike:
- only the Supervisor's ingress proxy may connect (Home Assistant has already checked who the person is);
- no state-changing request labelled cross-site by the browser — on the app's own API *and* on proxied sites, which
  can't defend themselves here because their Origin/Referer is rewritten to their own address (§3.1);
- no websocket opened by a page on another site;
- no call to the app's admin API (nor any change through its API) from a page opened through the proxy (§4.3). Such
  pages run on Home Assistant's own address, so this is a speed bump, not a wall: it catches the page that names the
  API, not one written to get around it (see DOCS.md, "Trusted software only").
Then /s/… goes to the proxy (the site's own headers, cleaned) and everything else to the app's pages and API (the
app's own Content-Security-Policy and caching headers).
"""
import logging
import os
import re
import tempfile
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles
from starlette.requests import HTTPConnection, Request
from starlette.responses import JSONResponse, RedirectResponse
from starlette.websockets import WebSocket, WebSocketClose

from . import auth, config, db, proxy, rewrite, settings, sidebar, sites, watchdog, wsproxy
from .common import auth_core, backup_core, deeplinks, ha_people, ha_time, web_security
from .common import housekeeping as jobs_core
from .routers import api

jobs_core.setup_logging()
logger = logging.getLogger("main")

SITE_PATH = re.compile(r"^/s/([a-z0-9-]{1,40})(/.*)?$")


def housekeeping() -> None:
    """Old access-log lines, old checks and incidents, backup copies a browser never finished downloading."""
    days = int(settings.get("access_log_days"))
    proxy.flush_access_log()
    with db.get_conn() as conn:
        conn.execute("DELETE FROM access_log WHERE at < strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ?)", (f"-{days} days",))
    watchdog.housekeeping()
    backup_core.remove_stale(tempfile.gettempdir(), db.SNAPSHOT_PREFIX, 6 * 3600)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    await ha_time.load(config.ZONE, log=logger)
    await run_in_threadpool(sidebar.load_blocking)
    jobs = jobs_core.Jobs()
    jobs.every("housekeeping", 3600, housekeeping, log=logger, error="Housekeeping failed")
    jobs.every("access_log", 5, proxy.flush_access_log, at_start=False, log=logger,
               error="Writing the access log failed")
    jobs.add("ha_people", ha_people.loop)                 # people's phones, for alerts
    if os.environ.get("PROXY_NO_WATCHDOG") != "1":        # the tests run rounds by hand
        jobs.add("watchdog", watchdog.loop)
    jobs.start()
    try:
        yield
    finally:
        await jobs.stop()
        await proxy.close_all()
        await watchdog.close_clients()
        await run_in_threadpool(proxy.flush_access_log)


pages = FastAPI(title=config.APP_TITLE, lifespan=lifespan)
pages.include_router(api.router)
# Bodies of the app's own API: 1 MB (the sites' form is a few KB), a restored backup up to 2 GB with its length given.
# Chunked bodies are counted as they arrive. (Uploads through a site, /s/…, have their own App setting.)
RESTORE_PATH = "/api/admin-storage-import-db"
pages.add_middleware(web_security.BodyLimit, default_max=1 << 20, routes={RESTORE_PATH: 2 << 30},
                     no_chunked_prefixes=(RESTORE_PATH,))


@pages.get("/api/health")
def health():
    return {"status": "ok"}


deeplinks.add(pages, {"site": 1, "monitor": 1})

# The app's own pages: scripts only from the app itself; frames only from the app itself (the viewer shows a
# proxied site, which is on the same address, in a frame).
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "connect-src 'self'; font-src 'self'; frame-src 'self'; object-src 'none'; base-uri 'self'; "
       "form-action 'self'; frame-ancestors 'self'")
# Every page links its scripts and styles with ?v=<version> (index.html, the shim's tag): those are kept for good.
HEADERS = web_security.SecurityHeaders(CSP, pragma=True, api_no_store="default", static_immutable=True,
                                       static_prefixes=("/",))
HEADERS.install(pages)

_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(_STATIC_DIR):
    pages.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="frontend")


# --------------------------------------------------------------------------------------------- the proxied sites

async def who(conn) -> dict | None:
    """The person (from the cache on the event loop, else from the database in a thread)."""
    user = auth.peek_user(conn)
    return user if user is not None else await run_in_threadpool(auth.cached_user, conn)


async def proxied_site(slug: str) -> sites.Site | None:
    """The proxied site (from the cache on the event loop, else loaded in a thread after a change)."""
    cached = sites.peek()
    if cached is None:
        cached = await run_in_threadpool(sites.all_sites)
    s = cached.get(slug)
    return s if s is not None and s.use == "proxy" else None


NO_SUCH_SITE = ("No such site", "Check the address. If it is right, the site was removed or renamed, or it isn't on "
                                "your list: an admin can add you in Admin → Sites.")


async def proxied_http(scope, receive, send) -> None:
    request = Request(scope, receive)
    m = SITE_PATH.match(scope["path"])
    if not m:
        return await proxy.error_page(404, *NO_SUCH_SITE)(scope, receive, send)
    slug = m.group(1)
    if m.group(2) is None:                                       # /s/komodo → /s/komodo/ (relative, inside ingress)
        qs = scope.get("query_string", b"").decode("latin-1")
        return await RedirectResponse(f"{slug}/" + (f"?{qs}" if qs else ""), status_code=307)(scope, receive, send)
    user = await who(request)
    if user is None:
        return await proxy.error_page(401, "Not signed in", auth_core.no_user_message(config.APP_TITLE))(
            scope, receive, send)
    if not user["enabled"]:                     # the same for every site: says nothing about which sites exist
        return await proxy.error_page(403, "Turned off", "An admin has turned you off in this app.")(
            scope, receive, send)
    site = await proxied_site(slug)
    if site is None or not sites.can_open(user, site):
        # the same answer for a site that doesn't exist and one you may not open: no guessing which short names exist
        return await proxy.error_page(404, *NO_SUCH_SITE)(scope, receive, send)
    if request.method.upper() in ("TRACE", "TRACK", "CONNECT"):     # a TRACE echo would show the extra headers
        return await proxy.error_page(405, "Not allowed", "That request method isn't passed on.")(scope, receive, send)
    rest = proxy.split_path(scope.get("raw_path") or scope["path"].encode(), slug)
    if rest is None:
        return await proxy.error_page(400, "Bad address", "That path isn't allowed.")(scope, receive, send)
    key = (user["id"], site.id)
    if not proxy.IN_FLIGHT.enter(key):
        logger.warning("%s: %s has %d requests open on it; refused one more", slug, user["id"], proxy.MAX_IN_FLIGHT)
        return await proxy.error_page(429, "Too many at once", "This page has too many requests open on the site at "
                                                               "once. Wait a moment, then reload.")(scope, receive, send)
    try:
        ctx = rewrite.Ctx(ingress=rewrite.ingress_path(request.headers.get("x-ingress-path")), slug=slug,
                          scheme=site.scheme, host=site.host, port=site.port)
        log = proxy.log_writer(site, user, request.method, rest)
        response = await proxy.exchange(request, site, user, ctx, rest, log)
        await response(scope, receive, send)            # a streamed answer counts until it has ended
    finally:
        proxy.IN_FLIGHT.leave(key)


async def proxied_websocket(scope, receive, send) -> None:
    ws = WebSocket(scope, receive, send)
    m = SITE_PATH.match(scope["path"])
    user = await who(ws)
    site = await proxied_site(m.group(1)) if m else None
    if not m or m.group(2) is None or user is None or site is None or not sites.can_open(user, site):
        return await WebSocketClose(code=1008)(scope, receive, send)
    rest = proxy.split_path(scope.get("raw_path") or scope["path"].encode(), site.slug)
    if rest is None:
        return await WebSocketClose(code=1008)(scope, receive, send)
    await wsproxy.relay(ws, site, user, rest)


# --------------------------------------------------------------------------------------------- the gate

FROM_SITE_DETAIL = ("Refused: this came from a site opened through the proxy, not from the app's own page. Sites "
                    "can't use the app's admin pages or change anything in it.")


def from_proxied_page(request: Request) -> bool:
    """A call to the app's admin API — or any change through its API — that a page opened through the proxy made,
    not the app's own page: its Referer is a proxied page (/s/…) or no page of the app at all (a single-page app mode
    site shows its own path), or the browser says it isn't a script's request (a form or a frame: Sec-Fetch-Dest).
    Requests without these headers (not a browser, or a page that hides its Referer) aren't caught: proxied pages run
    on Home Assistant's own address, so this is not a hard boundary (spec §4.3)."""
    path = request.url.path
    if not path.startswith("/api/"):
        return False
    change = request.method not in web_security.SAFE_METHODS
    if not (change or path.startswith("/api/admin")):
        return False
    dest = (request.headers.get("sec-fetch-dest") or "").strip().lower()
    if change and dest and dest != "empty":
        return True
    referer = request.headers.get("referer")
    if not referer:
        return False
    try:
        page = urlsplit(referer).path
    except ValueError:
        return True
    if page in ("", "/"):
        return False                      # an origin-only Referer (a referrer policy): it says nothing about the page
    ingress = rewrite.ingress_path(request.headers.get("x-ingress-path"))
    if ingress:
        own = (page == ingress or page.startswith(ingress + "/")) and not page.startswith(ingress + "/s/")
    else:
        own = not (page == "/s" or page.startswith("/s/"))
    return not own

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
            if from_proxied_page(request):
                logger.warning("Refused %s %s from a proxied page", request.method, scope["path"][:100])
                return await JSONResponse({"detail": FROM_SITE_DETAIL}, status_code=403,
                                          headers={"Cache-Control": "no-store"})(scope, receive, send)
        elif web_security.cross_origin_websocket(conn):
            logger.warning("Refused a websocket from another site: %s", scope["path"][:100])
            return await WebSocketClose(code=1008)(scope, receive, send)
        if scope["path"].startswith("/s/") or scope["path"] == "/s":
            if kind == "websocket":
                return await proxied_websocket(scope, receive, send)
            return await proxied_http(scope, receive, send)
        return await self.inner(scope, receive, send)


app = Gate(pages)
