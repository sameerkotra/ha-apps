import logging
import os
import tempfile
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from . import config, db, emergency, guest_wifi, ha_client, reminders, service, sessions, settings, storage
from .common import auth_core, backup_core, ha_people, ha_time, web_security
from .common import housekeeping as jobs_core
from .routers import admin, emergency as emergency_router, guest as guest_router, quick as quick_router, health as health_router, items as items_router, me, sheet, vaults

jobs_core.setup_logging()
logger = logging.getLogger("main")


def _gated(fn):
    """A background job: it waits while a restore swaps the database (db.GATE)."""
    def run():
        with db.GATE.work():
            fn()
    return run


def _minutely() -> None:
    with db.get_conn() as conn:
        emergency.release_due(conn)


def _half_hourly() -> None:
    with db.get_conn() as conn:
        reminders.run(conn)
        service.expire_setups(conn)        # one-time passwords nobody used in time


def _daily() -> None:
    """Old rows: the audit log past the App setting's age, and recently-used marks of vaults that are gone."""
    days = settings.get("audit_keep_days")
    with db.get_conn() as conn:
        if days:
            cutoff = (config.utcnow() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
            conn.execute("DELETE FROM audit_log WHERE created_at < ?", (cutoff,))
        conn.execute("DELETE FROM recent_items WHERE vault_id NOT IN (SELECT id FROM vaults)")
    backup_core.remove_stale(tempfile.gettempdir(), "household-vault-backup-", 6 * 3600, suffixes=(".zip",))


async def housekeeping(n: int) -> None:
    """Every 20 s: idle sessions; every minute: emergency access; every 30 minutes: reminders;
    every day: old rows and abandoned backup downloads."""
    await run_in_threadpool(_gated(sessions.expire_idle))
    if n % 3 == 0:
        await run_in_threadpool(_gated(_minutely))
    if n % 90 == 5:
        await run_in_threadpool(_gated(_half_hourly))
    if n % 4320 == 7:
        await run_in_threadpool(_gated(_daily))


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    with db.get_conn() as conn:          # finish what a crash interrupted (a re-key), tidy what failed changes left
        storage.recover(conn)
    # "today" (reminders, expiry dates, file names) is Home Assistant's day: the process follows its zone
    await ha_time.load(ha_time.Zone(logger), log=logger)
    try:
        n = await run_in_threadpool(ha_client.sync_users_blocking)
        logger.info("Synced %s Home Assistant people.", n)
    except Exception:
        logger.exception("Syncing Home Assistant persons failed")
    try:   # people and their phones, from Home Assistant (Settings → People → Track device)
        await run_in_threadpool(ha_people.refresh_blocking, True)
    except Exception:
        logger.exception("Reading the people's phones from Home Assistant failed")
    try:   # the guest Wi-Fi sensor older versions kept in Home Assistant goes (it's never sent again)
        await run_in_threadpool(guest_wifi.remove_old_sensor_blocking)
    except Exception:
        logger.exception("Removing the old guest Wi-Fi sensor from Home Assistant failed")
    try:   # Argon2 tuned for this machine now, not inside the first admin's change (review 2026-10)
        await run_in_threadpool(service.calibrate)
    except Exception:
        logger.exception("Tuning Argon2 failed; it's done when first needed")
    jobs = jobs_core.Jobs()                      # started in this order, cancelled on shutdown
    jobs.every("housekeeping", 20, housekeeping, thread=False, tick=True, log=logger, error="Housekeeping failed")
    jobs.add("ha_people", ha_people.loop)
    jobs.start()
    try:
        yield
    finally:
        await jobs.stop()
        sessions.end_all()


# no /docs, /redoc or /openapi.json: every Home Assistant user could read the whole route list (review 2026-10)
app = FastAPI(title="Household Vault", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
for r in (me, vaults, items_router, admin, health_router, sheet, emergency_router, guest_router, quick_router):
    app.include_router(r.router)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    """Plain messages; never echo input back (it may be a password)."""
    msgs = []
    for e in exc.errors():
        loc = ".".join(str(x) for x in e.get("loc", []) if x not in ("body", "query", "path"))
        msgs.append(f"{loc}: {e.get('msg')}" if loc else str(e.get("msg")))
    return JSONResponse(status_code=422, content={"detail": "; ".join(msgs) or "Invalid request."})


@app.exception_handler(storage.TooBig)
async def too_big(request: Request, exc: storage.TooBig):
    """A vault that would grow past 20 MB: refused (and the open vault read back from its file), not a 500."""
    return JSONResponse(status_code=413, content={"detail": str(exc) or "The vault is too big."})


# Only Supervisor's ingress proxy and loopback get in: app/common/auth_core.INGRESS_HOSTS.
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
       "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'self'")
# Request bodies, counted as they arrive (a chunked body without Content-Length is counted too —
# common/web_security.BodyLimit): 5 MB for everything (an item with many long fields fits easily),
# a 20 MB file to import, a 300 MB backup to restore.
MAX_BODY = 5 * 1024 * 1024
BODY_LIMITS = {"/api/import": 25 * 1024 * 1024, "/api/admin-storage-import-db": 300 * 1024 * 1024}
app.add_middleware(web_security.BodyLimit, default_max=MAX_BODY, routes=BODY_LIMITS)
# every page links its files with ?v=<version>: those are kept by the browser for good (static files are served at /)
HEADERS = web_security.SecurityHeaders(CSP, referrer="no-referrer", api_no_store="set", static_immutable=True,
                                       static_prefixes=("/",))


@app.middleware("http")
async def guard(request: Request, call_next):
    blocked = auth_core.refuse_outsiders(request) or web_security.refuse_cross_site(request)
    if blocked is not None:
        return blocked
    return HEADERS.apply(request, await call_next(request))


@app.get("/api/health")
def health():
    return {"status": "ok"}


_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(_STATIC_DIR):
    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="frontend")
