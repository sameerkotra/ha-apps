"""Household Phones: the household's phones through the Home Assistant Companion app — check-ins, Check now, alerts,
and forwarding a phone's messages into Household Chat (spec/SPEC.md).

The app is reached only through the Supervisor's ingress proxy (guard below); identity comes from Home Assistant's
headers (auth.py). Background work starts in the lifespan unless BACKGROUND_LOOPS=0 (the tests drive each pass).
"""
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from . import app_messages, config, db, ha_sensors, housekeeping, monitor, phones, sidebar
from .common import auth_core, deeplinks, ha_people, ha_time, web_security
from .common import housekeeping as jobs_core
from .routers import admin, api

jobs_core.setup_logging()
logger = logging.getLogger("main")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    monitor.started_at()                                   # the start-up grace counts from here
    await ha_time.load(config.ZONE, log=logger)
    await run_in_threadpool(sidebar.load_blocking)
    try:
        await run_in_threadpool(ha_people.refresh_blocking, True)
    except Exception:
        logger.exception("Reading the people from Home Assistant failed")
    try:
        await run_in_threadpool(app_messages.start)        # Home Assistant's events and the household apps bus
    except Exception:
        logger.exception("Starting the event connection failed")
    jobs = jobs_core.Jobs()
    if config.BACKGROUND_LOOPS:
        jobs.add("discovery", housekeeping.discovery_loop)
        jobs.add("watcher", monitor.loop)
        jobs.add("poll", monitor.poll_loop)
        jobs.every("quick", 5, housekeeping.quick, tick=True, at_start=False, log=logger, error="The quick round failed")
        jobs.every("hourly", 3600, housekeeping.hourly, log=logger, error="Housekeeping failed")
        jobs.add("ha_sensors", ha_sensors.loop)
        jobs.add("ha_people", ha_people.loop)
    else:
        logger.info("Background loops are off (BACKGROUND_LOOPS=0)")
    jobs.start()
    try:
        yield
    finally:
        await jobs.stop()
        await run_in_threadpool(app_messages.stop)


app = FastAPI(title=config.APP_TITLE, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(api.router)
app.include_router(admin.router)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    msgs = []
    for e in exc.errors():
        loc = ".".join(str(x) for x in e.get("loc", []) if x not in ("body", "query", "path"))
        msgs.append(f"{loc}: {e.get('msg')}" if loc else str(e.get("msg")))
    return JSONResponse(status_code=422, content={"detail": "; ".join(msgs) or "Invalid request."})


# Strict: scripts and styles only from the app itself (no inline scripts, no eval), no outside hosts at all.
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
       "font-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'self'")
# The HTML shell points at versioned ?v= assets: never cache the shell itself; the assets are kept for good.
HEADERS = web_security.SecurityHeaders(CSP, referrer="no-referrer", api_no_store="default", pragma=True,
                                       static_immutable=True, static_prefixes=("/",))

RESTORE_PATH = "/api/admin-storage-import-db"
# Request bodies: 64 KB (the forms are tiny), a restored backup up to 512 MB, counted as it arrives.
app.add_middleware(web_security.BodyLimit, default_max=64 * 1024, routes={RESTORE_PATH: 512 << 20},
                   streamed_prefixes=(RESTORE_PATH,))


@app.middleware("http")
async def guard(request: Request, call_next):
    blocked = auth_core.refuse_outsiders(request, auth_core.INGRESS_HOSTS) or web_security.refuse_cross_site(request)
    if blocked is not None:
        return blocked
    return HEADERS.apply(request, await call_next(request))


@app.get("/api/health")
def health():
    return {"status": "ok", "version": config.APP_VERSION}


# Links from Household Chat, the Household Assistant and notifications: /<page>/check/<phone> → #/check/<phone>.
deeplinks.add(app, {"check": 1, "phone": 1, "phones": 0, "mine": 1})

_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(_STATIC_DIR):
    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="frontend")

_ = phones
