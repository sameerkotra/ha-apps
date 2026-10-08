"""Household AI on a Docker host: the settings page (WEB_PORT, no login), the model server it starts, and the
gateway on port 11434.

Start-up: the database, the gateway (its own uvicorn server on 11434, in this
process), then the model server when *Model server* is on. Every minute: the callers' host names and the
day/night schedule; every hour: housekeeping (usage older than 30 days).
"""
import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from . import callers, config, db, gateway, ollama, server, settings, usage
from .common import web_security
from .common import housekeeping as jobs_core
from .routers import api

jobs_core.setup_logging()
logger = logging.getLogger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    logger.info("Household AI %s: settings page on port %s, gateway on %s, time zone %s", config.APP_VERSION,
                config.WEB_PORT, config.GATEWAY_PORT, config.ZONE.name)
    server.SCHEDULE = server.Schedule(gateway.QUEUE, gateway.LAST_USED)
    server.SERVER.on_ready.append(server.SCHEDULE.tick)
    gw = None
    gw_task = None
    if config.GATEWAY_PORT:
        gw = await gateway.serve(config.GATEWAY_PORT, config.GATEWAY_HOST)
        gw_task = asyncio.create_task(gw.serve(), name="gateway")
    if settings.get("server_on"):
        await server.SERVER.start()
    jobs = jobs_core.Jobs()
    jobs.every("callers", 60, callers.RESOLVER.refresh, thread=False, log=logger, error="Resolving the apps failed",
               traceback=False)
    jobs.every("schedule", 60, server.SCHEDULE.tick, thread=False, at_start=False, log=logger,
               error="The schedule failed")
    jobs.every("housekeeping", 3600, usage.housekeeping, log=logger, error="Housekeeping failed")
    jobs.start()
    try:
        yield
    finally:
        await jobs.stop()
        if gw is not None:
            gw.should_exit = True
            try:
                await asyncio.wait_for(gw_task, 15)
            except (asyncio.TimeoutError, Exception):
                gw_task.cancel()
        await server.SERVER.stop("Shutting down")
        await ollama.close()


app = FastAPI(title="Household AI", lifespan=lifespan)
app.include_router(api.router)


@app.middleware("http")
async def refuse_cross_site(request: Request, call_next):
    """No login (the page is for whoever can reach it), but no write may come from another site: a web page
    elsewhere can't change the settings through your browser."""
    blocked = web_security.refuse_cross_site(request)
    if blocked is not None:
        return blocked
    return await call_next(request)


@app.get("/api/health")
def health():
    return {"status": "ok"}


CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; "
       "frame-ancestors 'self'")
HEADERS = web_security.SecurityHeaders(CSP, pragma=True)
HEADERS.install(app)

_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(_STATIC_DIR):
    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="frontend")
