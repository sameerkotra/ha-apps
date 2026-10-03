import asyncio
import contextlib
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from . import config, db, ha_client, ha_notify, ha_people, ha_sensors, housekeeping
from .routers import admin, levels, me, play, prefs, users

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: create/migrate the schema, read Home Assistant's time zone
    (UTC if it can't be read) and people, then start the background loops
    (sensors, housekeeping, people) unless BACKGROUND_LOOPS=0 (the tests)."""
    db.init_db()
    await ha_client.load_timezone()
    try:
        await run_in_threadpool(ha_people.refresh_blocking, True)
    except Exception:
        logger.exception("reading the people from Home Assistant failed")
    try:
        await run_in_threadpool(ha_notify.check_targets_blocking)
    except Exception:
        logger.exception("notify service check failed")
    loops = []
    if config.BACKGROUND_LOOPS:
        loops = [
            asyncio.create_task(ha_sensors.loop(), name="ha_sensors"),
            asyncio.create_task(housekeeping.loop(), name="housekeeping"),
            asyncio.create_task(ha_people.loop(), name="ha_people"),
        ]
    else:
        logger.info("Background loops are off (BACKGROUND_LOOPS=0)")
    try:
        yield
    finally:
        for t in loops:
            t.cancel()
        for t in loops:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t


app = FastAPI(title="Household Arcade", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

app.include_router(me.router)
app.include_router(prefs.router)
app.include_router(play.router)
app.include_router(users.router)
app.include_router(admin.router)
app.include_router(levels.router)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    msgs = []
    for e in exc.errors():
        loc = ".".join(str(x) for x in e.get("loc", []) if x not in ("body", "query", "path"))
        msgs.append(f"{loc}: {e.get('msg')}" if loc else str(e.get("msg")))
    return JSONResponse(status_code=422, content={"detail": "; ".join(msgs) or "Invalid request."})


# Only Home Assistant's Supervisor ingress proxy (or loopback) may reach the
# app: that is what makes the X-Remote-User-* headers trustworthy (auth.py).
_INGRESS_ALLOWED_HOSTS = {"172.30.32.2", "127.0.0.1", "::1"}
# Strict: scripts and styles only from the app itself (no inline scripts, no
# eval), no outside hosts at all. Games are drawn on a canvas in code.
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; "
       "connect-src 'self'; font-src 'self'; media-src 'self'; object-src 'none'; base-uri 'none'; "
       "form-action 'self'; frame-ancestors 'self'")
MAX_BODY = 64 * 1024
STREAMED = ("/api/admin-storage-import-db",)


@app.middleware("http")
async def guard(request: Request, call_next):
    client_host = request.client.host if request.client else None
    if client_host not in _INGRESS_ALLOWED_HOSTS:
        return JSONResponse(status_code=403, content={"detail": "Forbidden — access only via Home Assistant"})
    path = request.url.path
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and path not in STREAMED:
        try:
            length = int(request.headers.get("content-length") or 0)
        except ValueError:
            length = 0
        if length > MAX_BODY:
            return JSONResponse(status_code=413, content={"detail": "That request is too big."})
    response = await call_next(request)
    response.headers.setdefault("Content-Security-Policy", CSP)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    if path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    elif path == "/" or path.endswith(".html"):
        # the HTML shell points at versioned ?v= assets; never cache the shell itself
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
    return response


@app.get("/api/health")
def health():
    return {"status": "ok", "version": config.APP_VERSION}


# Static frontend, mounted LAST so /api/* always wins.
_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(_STATIC_DIR):
    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="frontend")
