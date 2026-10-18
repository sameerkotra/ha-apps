"""Household Assistant: the FastAPI app, its start-up and the ingress-only guard."""
import logging
import os
import tempfile
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles

from . import app_messages, briefing, catalogue, config, db, engine
from .common import auth_core, backup_core, ha_people, ha_time, web_security
from .common import housekeeping as jobs_core
from .routers import api, people_data

jobs_core.setup_logging()
logger = logging.getLogger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start-up: the database, Home Assistant's time zone, the bus; then every minute ask apps with a new or
    missing tool list and send the morning briefings that are due, every few minutes the people and their phones
    from Home Assistant, and every hour the housekeeping (old questions, questions left running by a restart)."""
    db.init_db()
    await ha_time.load(config.ZONE, log=logger)
    try:
        await run_in_threadpool(app_messages.start)
    except Exception:
        logger.exception("starting the app bus failed")
    jobs = jobs_core.Jobs()
    jobs.every("catalogue", 60, catalogue.check, at_start=False, log=logger, error="Asking for tool lists failed")
    jobs.every("briefings", 60, briefing.check, at_start=False, log=logger, error="Sending morning briefings failed")
    jobs.every("housekeeping", 3600, engine.housekeeping, log=logger, error="Housekeeping failed")
    jobs.every("sweep", 3600, lambda: backup_core.remove_stale(tempfile.gettempdir(), db.SNAPSHOT_PREFIX, 6 * 3600),
               log=logger, error="Removing old download copies failed")
    jobs.every("person_uploads", 3600, people_data.uploads.sweep, at_start=False, log=logger,
               error="Removing old person-data uploads failed")
    jobs.add("ha_people", ha_people.loop)
    jobs.start()
    try:
        yield
    finally:
        await jobs.stop()
        await run_in_threadpool(app_messages.stop)


app = FastAPI(title="Household Assistant", lifespan=lifespan)
app.include_router(api.router)
app.include_router(people_data.router)       # Admin → People → Data (export, import, delete)

# Request bodies: 1 MB for everything (questions are 1000 characters), a restore up to 1 GB with its length given,
# a person's export file (Admin → People → Data) up to people_data.MAX_UPLOAD (plus the form's own few bytes).
# Counted as they arrive (a chunked body can't slip past). Added before the guard below, so outsiders still get
# its 403 first.
RESTORE_PATH = "/api/admin-storage-import-db"
PERSON_IMPORT_PATH = "/api/admin/people/data/import"
app.add_middleware(web_security.BodyLimit, default_max=1 << 20,
                   routes={RESTORE_PATH: 1 << 30, PERSON_IMPORT_PATH: people_data.MAX_UPLOAD + (1 << 20)},
                   no_chunked_prefixes=(RESTORE_PATH,))


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
# The page links every file with ?v=<version> (tests/test_packaging.py checks it), so those are cached for good.
HEADERS = web_security.SecurityHeaders(CSP, pragma=True, static_immutable=True, static_prefixes=("/",))
HEADERS.install(app)

_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(_STATIC_DIR):
    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="frontend")
