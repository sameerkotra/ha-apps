import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles

from . import app_messages, db, ha_sync
from .common import auth_core, web_security
from .common import housekeeping as jobs_core
from .routers import admin, ai, goals, logs, me, saved_foods, users, weight

jobs_core.setup_logging()
logger = logging.getLogger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: create/migrate the schema, read Home Assistant's time zone
    once, start the periodic sensor sync; cancel it cleanly on shutdown.
    (Replaces the deprecated @app.on_event("startup") hook.)"""
    db.init_db()
    await ha_sync.load_timezone()
    try:
        await run_in_threadpool(app_messages.start)     # the household apps bus (the Household Assistant asks)
    except Exception:
        logger.exception("starting the app bus failed")
    jobs = jobs_core.Jobs()
    # Re-push every known user's daily-calories sensor every 15 minutes, mainly
    # so the value rolls over to 0 shortly after midnight even if nobody has
    # the app open, rather than waiting for their next food log change. Reads
    # the App setting every tick: while publishing is off it pushes nothing
    # (and retries removing the sensors if that failed when it was turned off).
    jobs.every("ha_sync", 900, ha_sync.periodic_sync, thread=False, at_start=False, log=logger,
               error="Periodic HA sync failed", traceback=False)
    jobs.start()
    try:
        yield
    finally:
        await jobs.stop()
        await run_in_threadpool(app_messages.stop)


app = FastAPI(title="Calorie Tracker", lifespan=lifespan)

app.include_router(me.router)
app.include_router(me.whoami_router)
app.include_router(users.router)
app.include_router(goals.router)
app.include_router(weight.router)
app.include_router(saved_foods.router)
app.include_router(logs.router)
app.include_router(ai.router)
app.include_router(admin.router)

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
    /api/health stays reachable with no user context and every route's
    existing get_current_user/get_acting_user dependency keeps producing its
    own (more specific) 401 for a missing/unrecognized user."""
    blocked = auth_core.refuse_outsiders(request) or web_security.refuse_cross_site(request)
    if blocked is not None:
        return blocked
    return await call_next(request)


@app.get("/api/health")
def health():
    return {"status": "ok"}


# Scripts only from the app itself (no inline scripts or handlers); a few inline style attributes remain.
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
       "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; "
       "frame-ancestors 'self'")

# The HTML shell (index.html, served for "/") must never be cached by
# the browser — it's what points at the versioned style.css?v=.../app.js?v=...
# below, so a stale cached copy of JUST this one file can make a rebuilt
# app look like the update never took effect, even after a normal
# refresh. Static assets themselves aren't touched here: their
# cache-busting query string (bumped alongside config.yaml's version) is
# what invalidates them, so they can keep normal caching.
HEADERS = web_security.SecurityHeaders(CSP, pragma=True)
HEADERS.install(app)


# Static frontend, built into the image at app/static/. Registered last so
# /api/* routes above always take priority over this catch-all.
_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(_STATIC_DIR):
    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="frontend")
