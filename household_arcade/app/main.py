import logging
import os
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from . import auth, config, db, ha_client, ha_sensors, housekeeping, panel
from .common import auth_core, ha_notify, ha_people, web_security
from .common import housekeeping as jobs_core
from .routers import admin, levels, me, play, prefs, together, users
from .routers import daily as daily_router

jobs_core.setup_logging()
logger = logging.getLogger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: create/migrate the schema, read Home Assistant's time zone
    (UTC if it can't be read) and people, then start the background loops
    (sensors, housekeeping, people) unless BACKGROUND_LOOPS=0 (the tests)."""
    db.init_db()
    try:
        await run_in_threadpool(panel.learn)            # where notifications open (the sidebar page)
    except Exception:
        logger.exception("working out the app's page failed")
    await ha_client.load_timezone()
    try:
        await run_in_threadpool(ha_people.refresh_blocking, True)
    except Exception:
        logger.exception("reading the people from Home Assistant failed")
    try:
        await run_in_threadpool(ha_notify.check_targets_blocking)
    except Exception:
        logger.exception("notify service check failed")
    jobs = jobs_core.Jobs()                      # started in this order, cancelled on shutdown
    if config.BACKGROUND_LOOPS:
        jobs.add("ha_sensors", ha_sensors.loop)
        jobs.add("housekeeping", housekeeping.loop)
        jobs.add("ha_people", ha_people.loop)
    else:
        logger.info("Background loops are off (BACKGROUND_LOOPS=0)")
    jobs.start()
    try:
        yield
    finally:
        await jobs.stop()


app = FastAPI(title="Household Arcade", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

app.include_router(me.router)
app.include_router(prefs.router)
app.include_router(play.router)
app.include_router(users.router)
app.include_router(admin.router)
app.include_router(levels.router)
app.include_router(daily_router.router)
app.include_router(together.router)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    msgs = []
    for e in exc.errors():
        loc = ".".join(str(x) for x in e.get("loc", []) if x not in ("body", "query", "path"))
        msgs.append(f"{loc}: {e.get('msg')}" if loc else str(e.get("msg")))
    return JSONResponse(status_code=422, content={"detail": "; ".join(msgs) or "Invalid request."})


# Only Home Assistant's Supervisor ingress proxy (or loopback) may reach the
# app: that is what makes the X-Remote-User-* headers trustworthy (auth.py).
_INGRESS_ALLOWED_HOSTS = auth.INGRESS_ALLOWED_HOSTS
# Strict: scripts and styles only from the app itself (no inline scripts, no
# eval), no outside hosts at all. Games are drawn on a canvas in code.
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; "
       "connect-src 'self'; font-src 'self'; media-src 'self'; object-src 'none'; base-uri 'none'; "
       "form-action 'self'; frame-ancestors 'self'")
_HOST_RE = re.compile(r"^[A-Za-z0-9.\-\[\]:]{1,255}$")


def _csp_for(request: Request) -> str:
    """The CSP, with this app's own host named for the live link's WebSocket: 'self' covers it in current
    browsers, but some older ones only match http(s) for 'self'."""
    host = request.headers.get("host", "")
    if not _HOST_RE.match(host):
        return CSP
    return CSP.replace("connect-src 'self';", f"connect-src 'self' ws://{host} wss://{host};")


# The HTML shell points at versioned ?v= assets: never cache the shell itself. API answers: no-store
# unless the route says otherwise.
HEADERS = web_security.SecurityHeaders(_csp_for, referrer="no-referrer", api_no_store="default", pragma=True)


MAX_BODY = 64 * 1024
STREAMED = ("/api/admin-storage-import-db",)


@app.middleware("http")
async def guard(request: Request, call_next):
    blocked = auth_core.refuse_outsiders(request, _INGRESS_ALLOWED_HOSTS) or web_security.refuse_cross_site(request)
    if blocked is not None:
        return blocked
    path = request.url.path
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and path not in STREAMED:
        try:
            length = int(request.headers.get("content-length") or 0)
        except ValueError:
            length = 0
        if length > MAX_BODY:
            return JSONResponse(status_code=413, content={"detail": "That request is too big."})
    return HEADERS.apply(request, await call_next(request))


@app.get("/api/health")
def health():
    return {"status": "ok", "version": config.APP_VERSION}


# A link from a notification (SPEC §7.3): Home Assistant opens the app's sidebar page and hands the rest of the
# path on — /leaderboard, /home/join/<id>, /admin/users/<id> — which the page itself turns into its #/ route
# (app.js). When the frontend asks this app for the sub-path instead, a relative redirect to the route keeps it
# inside Ingress; nothing is looked up here. Only the page's own tabs, so the static files (common/, games/) and
# /api are never caught.
_DEEP_TABS = ("home", "scores", "leaderboard", "settings", "play", "admin")
_DEEP_ARG = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _deep_link(tab: str, arg: str | None = None, arg2: str | None = None):
    from fastapi import HTTPException
    from fastapi.responses import RedirectResponse
    if tab not in _DEEP_TABS or any(a is not None and not _DEEP_ARG.match(a) for a in (arg, arg2)):
        raise HTTPException(404, "Not found.")
    parts = [p for p in (tab, arg, arg2) if p is not None]
    return RedirectResponse("../" * len(parts) + "#/" + "/".join(parts), status_code=307,
                            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


for _tab in _DEEP_TABS:                     # fixed paths, so the static files keep their own
    app.add_api_route(f"/{_tab}", (lambda t: lambda: _deep_link(t))(_tab), methods=["GET"], include_in_schema=False)
    app.add_api_route(f"/{_tab}/{{arg}}", (lambda t: lambda arg: _deep_link(t, arg))(_tab), methods=["GET"], include_in_schema=False)
    app.add_api_route(f"/{_tab}/{{arg}}/{{arg2}}", (lambda t: lambda arg, arg2: _deep_link(t, arg, arg2))(_tab), methods=["GET"],
                      include_in_schema=False)

# Static frontend, mounted LAST so /api/* always wins.
_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(_STATIC_DIR):
    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="frontend")
