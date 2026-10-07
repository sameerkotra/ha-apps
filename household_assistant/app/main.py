"""Household Assistant: the FastAPI app, its start-up and the ingress-only guard."""
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles

from . import app_messages, catalogue, config, db, engine
from .common import auth_core, ha_time, web_security
from .common import housekeeping as jobs_core
from .routers import api

jobs_core.setup_logging()
logger = logging.getLogger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start-up: the database, Home Assistant's time zone, the bus; then every minute ask apps with a new or
    missing tool list, and every hour the housekeeping (old questions, questions left running by a restart)."""
    db.init_db()
    await ha_time.load(config.ZONE, log=logger)
    try:
        await run_in_threadpool(app_messages.start)
    except Exception:
        logger.exception("starting the app bus failed")
    jobs = jobs_core.Jobs()
    jobs.every("catalogue", 60, catalogue.check, at_start=False, log=logger, error="Asking for tool lists failed")
    jobs.every("housekeeping", 3600, engine.housekeeping, log=logger, error="Housekeeping failed")
    jobs.start()
    try:
        yield
    finally:
        await jobs.stop()
        await run_in_threadpool(app_messages.stop)


app = FastAPI(title="Household Assistant", lifespan=lifespan)
app.include_router(api.router)


@app.middleware("http")
async def require_ha_ingress_auth(request: Request, call_next):
    """Only the Supervisor's ingress proxy may reach the app (the identity headers are trusted because of it), and
    no write may come from another site."""
    blocked = auth_core.refuse_outsiders(request) or web_security.refuse_cross_site(request)
    if blocked is not None:
        return blocked
    return await call_next(request)


@app.get("/api/health")
def health():
    return {"status": "ok"}


# Scripts only from the app itself (no inline scripts or handlers).
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; "
       "frame-ancestors 'self'")
HEADERS = web_security.SecurityHeaders(CSP, pragma=True)
HEADERS.install(app)

_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(_STATIC_DIR):
    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="frontend")
