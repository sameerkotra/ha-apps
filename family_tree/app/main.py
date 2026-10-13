import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.requests import Request as StarletteRequest
from starlette.concurrency import run_in_threadpool

from . import app_messages, auth, db, features, geocode, ha_client, housekeeping, inbox, kidmode, media, reminders, settings
from .common import auth_core, deeplinks, ha_people, web_security
from .common import housekeeping as jobs_core
from .routers import (admin, events, export, families, history, kin as kin_router, me, media as media_router, people,
                      map as map_router, related as related_router, custom as custom_router,
                      sources as sources_router, contacts as contacts_router, duplicates as dup_router,
                      reminders as reminders_router, stories, tree, tithi as tithi_router, quiz as quiz_router, tree_import)

jobs_core.setup_logging()
logger = logging.getLogger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    await ha_client.load_timezone()
    try:
        await run_in_threadpool(ha_client.sync_users_blocking)
    except Exception:
        logger.exception("Syncing Home Assistant persons failed")
    try:   # people and their phones, from Home Assistant (Settings → People)
        await run_in_threadpool(ha_people.refresh_blocking, True)
    except Exception:
        logger.exception("Reading the people from Home Assistant failed")
    await run_in_threadpool(media.check)
    try:
        await run_in_threadpool(app_messages.start)     # the household apps bus (the Household Assistant asks)
    except Exception:
        logger.exception("starting the app bus failed")
    jobs = jobs_core.Jobs()                      # started in this order, cancelled on shutdown
    jobs.add("housekeeping", housekeeping.loop)  # media check every 5 minutes; persons hourly; purge daily
    jobs.add("reminders", reminders.loop)
    jobs.add("geocode", geocode.loop)
    jobs.add("inbox", inbox.loop)
    jobs.add("ha_people", ha_people.loop)
    jobs.start()
    try:
        yield
    finally:
        await jobs.stop()
        await run_in_threadpool(app_messages.stop)


app = FastAPI(title="Family Tree", lifespan=lifespan)
for r in (me, related_router, people, families, events, stories, tree, history, media_router, export, reminders_router, kin_router,
          map_router, custom_router, sources_router, contacts_router, dup_router, tithi_router, quiz_router, admin, tree_import):
    app.include_router(r.router)
# "/<page>/upcoming", "/<page>/person/<id>" and "/<page>/relate/<id>/<id>" asked of the app itself: a redirect to the
# page's "#/…" (deeplink.js)
deeplinks.add(app, {"upcoming": 0, "person": 1, "relate": 2})


@app.exception_handler(features.FeatureOff)
async def feature_off(request: Request, exc: features.FeatureOff):
    """A switched-off module (Admin → App settings → Features): hidden, not deleted."""
    return JSONResponse(status_code=404, content={"detail": exc.message, "featureOff": exc.name})


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    """Plain messages, and never echo the input back (it may hold NaN/Infinity,
    which JSON can't encode)."""
    msgs = []
    for e in exc.errors():
        loc = ".".join(str(x) for x in e.get("loc", []) if x not in ("body", "query", "path"))
        msgs.append(f"{loc}: {e.get('msg')}" if loc else str(e.get("msg")))
    return JSONResponse(status_code=422, content={"detail": "; ".join(msgs) or "Invalid request."})


# Only the Supervisor's ingress proxy (and local tests) may talk to us: other
# apps on the internal network could otherwise forge X-Remote-User-* headers.
# (the allowlist — the proxy and loopback — is app/common/auth_core.INGRESS_HOSTS)

CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
       "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; "
       "frame-ancestors 'self'")


def csp() -> str:
    """The CSP, widened for the map tile server only while the places map is on (§13.3)."""
    if not features.on("map"):
        return CSP
    tiles = settings.origin(settings.get("map_tiles_url"))
    return CSP.replace("img-src 'self' data: blob:", f"img-src 'self' data: blob: {tiles}")


# Versioned files (`?v=<version>` in index.html, bumped every release) are cached for good; the page
# itself and every /api/ answer without its own Cache-Control are never stored (photos set their own).
HEADERS = web_security.SecurityHeaders(lambda _request: csp(), referrer="same-origin",
                                       static_immutable=True, static_prefixes=("/",))

RESTORE_PATH = "/api/admin-storage-import-db"


class BodyLimit(web_security.BodyLimit):
    """The shared body limit (it counts the bytes of a body sent without a Content-Length too), at the
    upload limit from App settings plus 1 MB for the form around the file. Starlette spools a whole
    multipart body to disk before the route (and its own size check) runs, so this runs first.

    A backup restore has no size limit (a backup with photos is as big as the photo folder), so who may
    send one is decided here, from the identity headers and the admin list, before a byte of the body
    is read: anyone else gets 403 straight away."""

    def limit_for(self, path: str) -> int:
        if path == RESTORE_PATH:
            return 1 << 62
        return (settings.get("max_upload_mb") + 1) * 1024 * 1024

    async def _refuse(self, send, status: int, detail: str) -> None:
        if status == 413:
            detail = media.too_large_message(settings.get("max_upload_mb"))
        await web_security.BodyLimit._refuse(send, status, detail)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("path") == RESTORE_PATH and scope.get("method") != "GET":
            ident = auth_core.identity(StarletteRequest(scope))
            if not (ident.user_id and auth.is_admin(ident.user_id, ident.username)):
                await web_security.BodyLimit._refuse(send, 403, "Only admins can restore a backup.")
                return
        await super().__call__(scope, receive, send)


# Added before the guard below, so the guard runs first (outsiders get its 403) and the 413 still gets
# the security headers.
app.add_middleware(BodyLimit, streamed_prefixes=(RESTORE_PATH,))


@app.middleware("http")
async def guard(request: Request, call_next):
    blocked = auth_core.refuse_outsiders(request) or web_security.refuse_cross_site(request)
    if blocked is not None:
        return blocked
    if request.url.path.startswith("/api/"):                # kids mode (§13.13): enforced here
        device = kidmode.request_device(request)
        user_id = None if device or request.method in ("GET", "HEAD") else auth_core.identity(request).user_id
        if device or user_id:
            blocked = await run_in_threadpool(kidmode.check_blocking, request.method, request.url.path, device, user_id)
            if blocked:
                return JSONResponse(status_code=blocked[0], content={"detail": blocked[1], "kidMode": True})
    return HEADERS.apply(request, await call_next(request))


@app.get("/api/health")
def health():
    return {"status": "ok"}


_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(_STATIC_DIR):
    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="frontend")
