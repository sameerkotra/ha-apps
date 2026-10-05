import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from . import config, db, emergency, guest_wifi, ha_client, reminders, sessions
from .common import auth_core, ha_people, web_security
from .common import housekeeping as jobs_core
from .routers import admin, emergency as emergency_router, guest as guest_router, quick as quick_router, health as health_router, items as items_router, me, sheet, vaults

jobs_core.setup_logging()
logger = logging.getLogger("main")


def _minutely() -> None:
    with db.get_conn() as conn:
        emergency.release_due(conn)


def _half_hourly() -> None:
    with db.get_conn() as conn:
        reminders.run(conn)


async def housekeeping(n: int) -> None:
    """Every 20 s: idle sessions; every minute: emergency access; every 30 minutes: reminders;
    every 5 minutes: the guest Wi-Fi sensor."""
    await run_in_threadpool(sessions.expire_idle)
    if n % 3 == 0:
        await run_in_threadpool(_minutely)
    if n % 90 == 5:
        await run_in_threadpool(_half_hourly)
    if n % 15 == 1:
        await run_in_threadpool(guest_wifi.ensure_blocking)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    try:
        n = await run_in_threadpool(ha_client.sync_users_blocking)
        logger.info("Synced %s Home Assistant people.", n)
    except Exception:
        logger.exception("Syncing Home Assistant persons failed")
    try:   # people and their phones, from Home Assistant (Settings → People → Track device)
        await run_in_threadpool(ha_people.refresh_blocking, True)
    except Exception:
        logger.exception("Reading the people's phones from Home Assistant failed")
    jobs = jobs_core.Jobs()                      # started in this order, cancelled on shutdown
    jobs.every("housekeeping", 20, housekeeping, thread=False, tick=True, log=logger, error="Housekeeping failed")
    jobs.add("ha_people", ha_people.loop)
    jobs.start()
    try:
        yield
    finally:
        await jobs.stop()
        sessions.end_all()


app = FastAPI(title="Household Vault", lifespan=lifespan)
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


# Only Supervisor's ingress proxy and loopback get in: app/common/auth_core.INGRESS_HOSTS.
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
       "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'self'")
MAX_BODY = 25 * 1024 * 1024
HEADERS = web_security.SecurityHeaders(CSP, referrer="no-referrer", api_no_store="set")


@app.middleware("http")
async def guard(request: Request, call_next):
    blocked = auth_core.refuse_outsiders(request) or web_security.refuse_cross_site(request)
    if blocked is not None:
        return blocked
    if request.method in ("POST", "PUT", "PATCH"):
        try:
            length = int(request.headers.get("content-length") or 0)
        except ValueError:
            length = 0
        limit = 300 * 1024 * 1024 if request.url.path == "/api/admin-storage-import-db" else MAX_BODY
        if length > limit:
            return JSONResponse(status_code=413, content={"detail": "That upload is too big."})
    return HEADERS.apply(request, await call_next(request))


@app.get("/api/health")
def health():
    return {"status": "ok"}


_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(_STATIC_DIR):
    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="frontend")
