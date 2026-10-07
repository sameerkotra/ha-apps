import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from . import app_messages, db, features, geocode, ha_client, housekeeping, inbox, kidmode, media, reminders, settings
from .common import auth_core, ha_people, web_security
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


HEADERS = web_security.SecurityHeaders(lambda _request: csp(), referrer="same-origin")


@app.middleware("http")
async def guard(request: Request, call_next):
    blocked = auth_core.refuse_outsiders(request) or web_security.refuse_cross_site(request)
    if blocked is not None:
        return blocked
    # Starlette spools a whole multipart body to disk before the route (and its
    # own size check) runs, so refuse oversized bodies up front. Backup restores
    # are admin-only and legitimately large (database + photos).
    if request.method in ("POST", "PUT", "PATCH") and request.url.path != "/api/admin-storage-import-db":
        try:
            length = int(request.headers.get("content-length") or 0)
        except ValueError:
            length = 0
        limit_mb = settings.get("max_upload_mb")
        if length > (limit_mb + 1) * 1024 * 1024:
            return JSONResponse(status_code=413, content={"detail": media.too_large_message(limit_mb)})
    device = kidmode.device_id(request.headers)
    if device and request.url.path.startswith("/api/"):                # kids mode (§13.13): enforced here
        blocked = await run_in_threadpool(kidmode.check_blocking, request.method, request.url.path, device)
        if blocked:
            return JSONResponse(status_code=blocked[0], content={"detail": blocked[1], "kidMode": True})
    return HEADERS.apply(request, await call_next(request))


@app.get("/api/health")
def health():
    return {"status": "ok"}


_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(_STATIC_DIR):
    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="frontend")
