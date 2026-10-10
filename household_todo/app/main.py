import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from . import (app_messages, config, db, drive_time, geocode, ha_client, ha_sensors, housekeeping, maint_files,
               maint_notify, reminders, task_files)
from .common import auth_core, deeplinks, ha_notify, ha_people, web_security
from .common import housekeeping as jobs_core
from .routers import (admin, calendar, dashboard, lists, maintenance, me, places, prefs, schedule, task_files as task_files_router,
                      task_types, tasks, users)

jobs_core.setup_logging()
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
    try:   # the files folder, if one is set, and tasks' files waiting for it
        await run_in_threadpool(maint_files.check)
        await run_in_threadpool(task_files.flush)
    except Exception:
        logger.exception("checking the files folder failed")
    try:   # flag assigned notify services Home Assistant doesn't have (best effort)
        await run_in_threadpool(ha_notify.check_targets_blocking)
    except Exception:
        logger.exception("notify service check failed")
    jobs = jobs_core.Jobs()                      # started in this order, cancelled on shutdown
    if config.BACKGROUND_LOOPS:
        # messages from the other household apps (Docs → "Make a Todo list"); off without Home Assistant
        await run_in_threadpool(app_messages.start)
        jobs.add("reminders", reminders.loop)
        jobs.add("ha_sensors", ha_sensors.loop)
        jobs.add("housekeeping", housekeeping.loop)
        jobs.add("drive_time", drive_time.loop)
        jobs.add("ha_people", ha_people.loop)
        jobs.add("maint_files", maint_files.loop)
    else:
        logger.info("Background loops are off (BACKGROUND_LOOPS=0)")
    jobs.start()
    try:
        yield
    finally:
        await jobs.stop()
        await run_in_threadpool(app_messages.stop)


app = FastAPI(title="Household Todo", lifespan=lifespan)

app.include_router(me.router)
app.include_router(users.router)
app.include_router(lists.router)
app.include_router(tasks.router)
app.include_router(task_files_router.router)
app.include_router(task_types.router)
app.include_router(places.router)
app.include_router(schedule.router)
app.include_router(calendar.router)
app.include_router(dashboard.router)
app.include_router(prefs.router)
app.include_router(maintenance.router)
app.include_router(admin.router)
# "/<page>/lists/<id>", "/<page>/dashboard" … asked of the app itself: a redirect to the page's "#/…" (deeplink.js)
deeplinks.add(app, {"dashboard": 0, "calendar": 0, "schedule": 0, "lists": 0, "maintenance": 0, "ticket": 2})
app.add_api_route("/lists/{a}", lambda a: deeplinks.redirect(["lists", a]), methods=["GET"], include_in_schema=False)

# Only Home Assistant's own Supervisor ingress proxy should ever be able to
# reach this container directly (it has no `ports:` mapping in config.yaml,
# but other apps on the same internal Docker network still can unless we
# check the source). Every request's X-Remote-User-* headers are trusted
# completely (see auth.py) precisely because Ingress is assumed to be the
# only path in — so this check is what actually makes that assumption true,
# rather than just a comment. Matches the same allowlist Splitpot uses.
# The allowlist (Supervisor's ingress proxy and loopback) is app/common/auth_core.INGRESS_HOSTS.


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
    blocked = auth_core.refuse_outsiders(request) or web_security.refuse_cross_site(request)
    if blocked is not None:
        return blocked
    return await call_next(request)


@app.get("/api/health")
def health():
    return {"status": "ok"}


# Scripts and styles only from the app itself (no inline scripts, handlers or style attributes).
# A maintenance file sets its own policy (sandbox).
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; "
       "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; "
       "frame-ancestors 'self'")

# The HTML shell must never be browser-cached: it points at the
# versioned style.css?v=... / app.js?v=..., so a stale cached copy of just
# this one file can make a rebuilt app look like the update never took
# effect. The assets themselves keep normal caching — their ?v= query
# string (bumped with config.yaml's version) invalidates them.
HEADERS = web_security.SecurityHeaders(CSP, pragma=True)
HEADERS.install(app)


# Static frontend, mounted LAST so /api/* always wins on route-match order.
_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(_STATIC_DIR):
    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="frontend")
