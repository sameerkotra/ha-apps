import asyncio
import contextlib
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from . import (config, db, drive_time, geocode, ha_client, ha_notify, ha_people, ha_sensors, housekeeping, maint_files,
               maint_notify, reminders)
from .routers import admin, calendar, dashboard, lists, maintenance, me, places, prefs, schedule, task_types, tasks, users

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: create (and migrate) the schema, read Home Assistant's time
    zone once, geocode home
    (§8n, best effort), then start the four background loops (digest,
    sensors, housekeeping, drive-time) unless BACKGROUND_LOOPS=0 (the tests).
    They are cancelled on shutdown, and shutdown waits for any pass already
    running in a worker thread — no separate process, no cron."""
    db.init_db()
    await ha_client.load_timezone()
    await geocode.load_home()
    try:   # people and their phones, from Home Assistant (Settings → People)
        await run_in_threadpool(ha_people.refresh_blocking, True)
    except Exception:
        logger.exception("reading the people from Home Assistant failed")
    try:   # the maintenance files folder, if one is set
        await run_in_threadpool(maint_files.check)
    except Exception:
        logger.exception("checking the maintenance files folder failed")
    try:   # flag assigned notify services Home Assistant doesn't have (best effort)
        await run_in_threadpool(ha_notify.check_targets_blocking)
    except Exception:
        logger.exception("notify service check failed")
    loops = []
    if config.BACKGROUND_LOOPS:
        loops = [
            asyncio.create_task(reminders.loop(), name="reminders"),
            asyncio.create_task(ha_sensors.loop(), name="ha_sensors"),
            asyncio.create_task(housekeeping.loop(), name="housekeeping"),
            asyncio.create_task(drive_time.loop(), name="drive_time"),
            asyncio.create_task(ha_people.loop(), name="ha_people"),
            asyncio.create_task(maint_files.loop(), name="maint_files"),
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


app = FastAPI(title="Household Todo", lifespan=lifespan)

app.include_router(me.router)
app.include_router(users.router)
app.include_router(lists.router)
app.include_router(tasks.router)
app.include_router(task_types.router)
app.include_router(places.router)
app.include_router(schedule.router)
app.include_router(calendar.router)
app.include_router(dashboard.router)
app.include_router(prefs.router)
app.include_router(maintenance.router)
app.include_router(admin.router)

# Only Home Assistant's own Supervisor ingress proxy should ever be able to
# reach this container directly (it has no `ports:` mapping in config.yaml,
# but other apps on the same internal Docker network still can unless we
# check the source). Every request's X-Remote-User-* headers are trusted
# completely (see auth.py) precisely because Ingress is assumed to be the
# only path in — so this check is what actually makes that assumption true,
# rather than just a comment. Matches the same allowlist Splitpot uses.
_INGRESS_ALLOWED_HOSTS = {"172.30.32.2", "127.0.0.1", "::1"}


@app.middleware("http")
async def require_ha_ingress_auth(request: Request, call_next):
    """The X-Remote-User-* headers auth.py trusts are only trustworthy if
    they actually came from Supervisor's ingress proxy — anyone else who can
    reach this container (e.g. another app on the same internal Docker
    network) could set them to whatever they want. This is a network-origin
    check only; it deliberately doesn't also require the header itself, so
    /api/health stays reachable with no user context (SPEC §2) and every
    route's existing get_current_user/get_acting_user dependency keeps
    producing its own (more specific) 401 for a missing/unrecognized user."""
    client_host = request.client.host if request.client else None
    if client_host not in _INGRESS_ALLOWED_HOSTS:
        return JSONResponse(status_code=403, content={"detail": "Forbidden — access only via Home Assistant"})
    return await call_next(request)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.middleware("http")
async def no_cache_html_shell(request: Request, call_next):
    """The HTML shell must never be browser-cached: it points at the
    versioned style.css?v=... / app.js?v=..., so a stale cached copy of just
    this one file can make a rebuilt app look like the update never took
    effect. The assets themselves keep normal caching — their ?v= query
    string (bumped with config.yaml's version) invalidates them."""
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.endswith(".html"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
    return response


# Static frontend, mounted LAST so /api/* always wins on route-match order.
_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(_STATIC_DIR):
    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="frontend")
