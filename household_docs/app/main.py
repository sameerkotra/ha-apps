import logging
import os
import sqlite3
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from . import app_messages, config, db, ha_client, ha_sensors, housekeeping, wiring
from .common import auth_core, ha_notify, ha_people, web_security
from .common import housekeeping as jobs_core
from .routers import (activity, admin, ai, bring, connect, docs, files, me, nodes, organise, people_data, search,
                      share_folders, shares, sheets, tidy)
from .store import paths, roots

jobs_core.setup_logging()
logger = logging.getLogger("main")
wiring.install()                               # steps 8–11: activity, follows, sensors, PDF text, AI


def startup_blocking() -> None:
    ha_client.load_time_zone_blocking()        # logs the zone in use
    st = roots.check()                         # the documents folder: set up on the very first start (§5.6)
    if not st["ok"]:
        logger.warning("Starting without the documents folder: %s", st["message"])
    try:
        ha_client.sync_people()
    except Exception:
        logger.exception("Adding Home Assistant's people failed")
    try:
        ha_notify.check_targets_blocking()
    except Exception:
        logger.exception("Checking notify services failed")


async def housekeeping_step(n: int) -> None:
    if not db.RESTORING.is_set():
        await run_in_threadpool(housekeeping.tick, n)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    try:   # people and their phones, from Home Assistant (Settings → People)
        await run_in_threadpool(ha_people.refresh_blocking, True)
    except Exception:
        logger.exception("Reading the people from Home Assistant failed")
    await run_in_threadpool(startup_blocking)
    jobs = jobs_core.Jobs()
    if config.BACKGROUND_LOOPS:
        # messages to the other household apps (Send to chat, Make a Todo list); off without Home Assistant
        await run_in_threadpool(app_messages.start)
        jobs.every("housekeeping", housekeeping.TICK_SECONDS, housekeeping_step, thread=False, tick=True, log=logger,
                   error="Housekeeping failed")
        jobs.add("ha_people", ha_people.loop)
        jobs.every("ha_sensors", ha_sensors.TICK_SECONDS, ha_sensors.tick, log=logger, error="Home Assistant sensors failed")
        jobs.every("background_text", housekeeping.TEXT_SECONDS, housekeeping.text_jobs, log=logger,
                   error="Reading PDFs and scans failed")
    else:
        logger.info("Background loops are off (BACKGROUND_LOOPS=0)")
    jobs.start()
    try:
        yield
    finally:
        await jobs.stop()
        await run_in_threadpool(app_messages.stop)


app = FastAPI(title="Household Docs", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
for r in (me, nodes, sheets, docs, shares, files, search, share_folders, organise, activity, bring, ai, admin, connect,
          tidy, people_data):
    app.include_router(r.router)
app.include_router(shares.links_router)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    """Plain messages; never echo input back (it may be someone's document)."""
    msgs = []
    for e in exc.errors():
        loc = ".".join(str(x) for x in e.get("loc", []) if x not in ("body", "query", "path"))
        msgs.append(f"{loc}: {e.get('msg')}" if loc else str(e.get("msg")))
    return JSONResponse(status_code=422, content={"detail": "; ".join(msgs) or "Invalid request."})


@app.exception_handler(paths.PathError)
async def path_error(request: Request, exc: paths.PathError):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.exception_handler(FileNotFoundError)
async def file_gone(request: Request, exc: FileNotFoundError):
    return JSONResponse(status_code=404, content={"detail": "That isn't there any more."})


@app.exception_handler(sqlite3.OperationalError)
async def db_busy(request: Request, exc: sqlite3.OperationalError):
    """The database stayed locked longer than the busy timeout (a long scan or restore): "try again", not a
    crash (review 2026-10 DP1). Any other database error is a real error."""
    if "locked" in str(exc) or "busy" in str(exc):
        logger.warning("The database was busy on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=503, content={"detail": "The app is busy — try again in a moment."},
                            headers={"Retry-After": "2"})
    raise exc


@app.exception_handler(OSError)
async def os_error(request: Request, exc: OSError):
    # the error number and its text only: the exception's own text holds the file's path in /share, and a file's
    # name can say what it is about (review 2026-10 D11)
    logger.warning("File system error on %s %s: [Errno %s] %s", request.method, request.url.path, exc.errno,
                   exc.strerror or type(exc).__name__)
    return JSONResponse(status_code=500, content={"detail": f"The file system refused that ({exc.strerror or 'error'})."})


# Strict (SPEC §3.3): scripts and styles only from the app itself; nothing user-written is ever parsed as HTML.
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; connect-src 'self'; "
       "object-src 'none'; base-uri 'none'; frame-ancestors 'self'")
MAX_BODY = 256 * 1024
MAX_DOC_BODY = 27 * 1024 * 1024          # a note save (max_doc_mb ≤ 25 MB, as JSON)
STREAMED = ("/api/admin/restore", "/api/import/keep/upload", "/api/import/zip/upload",   # their own limits
            "/api/import",                                   # a sheet file: max_doc_mb, counted while it's read
            people_data.IMPORT_PATH)                         # a person's data export file (Admin → People)
MAX_AI_BODY = 12 * 1024 * 1024           # AI: a photo of a table (§17.6)


def _streamed(path: str) -> bool:
    """Routes that read their body as a stream and enforce their own limit (restore, uploads)."""
    return path in STREAMED or (path.startswith("/api/nodes/") and path.endswith(("/upload", "/scan")))


def _is_file(path: str) -> bool:
    """A file download, preview or .zip: its route sets its own CSP (sandbox) and caching."""
    if path.startswith("/api/nodes/") and path.endswith(("/file", "/preview", "/zip")):
        return True
    if path.startswith("/api/admin/people/") and path.endswith(("/data/export", "/file")):
        return True                                  # a person's data export (Admin → People → ⋯ Data)
    return path == "/api/zip" or (path.startswith("/api/docs/") and path.endswith("/file"))


def _body_limit(path: str) -> int:
    return MAX_DOC_BODY if path.startswith("/api/docs") else MAX_AI_BODY if path.startswith("/api/ai/") else MAX_BODY


STREAM_MAX = 32 * 1024 ** 3            # streamed routes: only an outer bound here; each counts its own limit


class BodyLimit(web_security.BodyLimit):
    """The shared request-size middleware (app/common/web_security.py: a Content-Length over the limit is refused
    before the route runs, a chunked body is counted as it arrives) with this app's limits: notes' saves, AI
    photos, the rest 256 KB — and the routes that stream their body and count their own bytes (uploads, scans,
    imports, restore), whose path names a node, so they are told by `_streamed`, not by a prefix."""

    def limit_for(self, path: str) -> int:
        return STREAM_MAX if _streamed(path) else _body_limit(path)


app.add_middleware(BodyLimit, detail="That request is too big ({limit} at most).")   # inside `guard` (added below)

# Every page links each asset with ?v=<version> (tests/test_packaging.py checks it), so a versioned asset is kept
# by the browser for good: a release changes the address (review 2026-10 DFP7).
HEADERS = web_security.SecurityHeaders(
    CSP, csp_skip=lambda request, _response: _is_file(request.url.path), referrer="no-referrer",
    api_no_store="set", api_skip=_is_file, pragma=True, static_immutable=True, static_prefixes=("/",))


@app.middleware("http")
async def guard(request: Request, call_next):
    blocked = auth_core.refuse_outsiders(request) or web_security.refuse_cross_site(request)
    if blocked is not None:
        return blocked
    path = request.url.path
    if db.RESTORING.is_set() and path.startswith("/api/"):
        return JSONResponse(status_code=503, content={"detail": "A backup is being restored — try again in a moment."})
    return HEADERS.apply(request, await call_next(request))


@app.get("/api/health")
def health():
    return {"status": "ok", "version": config.APP_VERSION}


_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


@app.get("/quick-note")
def quick_note_page():
    """⚡ Quick note from outside (§17.13): Home Assistant's ingress panel passes a sub-path on to the app
    (`/<full slug>/quick-note`), so a dashboard button can open a new quick note. The page is the
    app itself (its relative URLs still work from here); it sees the path and opens #quick-note."""
    from fastapi.responses import FileResponse
    return FileResponse(os.path.join(_STATIC_DIR, "index.html"), media_type="text/html",
                        headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


def deep_link(place: str, node_id: str):
    """"Open in Docs" (APP_MESSAGES_SPEC §6.5): when Home Assistant hands the app a sub-path like /doc/<id>, send the
    page to the app's own route for it (#/doc/<id>, #/folder/<id>, #/file/<id>, a view link's #/view/<token>). A relative redirect, so it stays
    inside Ingress. Nothing is looked up here: the page shows the item, or its own 🔒 No access page."""
    from fastapi import HTTPException
    from fastapi.responses import RedirectResponse
    if not app_messages.ID_RE.match(node_id):
        raise HTTPException(404, "Not found.")
    return RedirectResponse(f"../#/{place}/{node_id}", status_code=307,
                            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


for _place in ("doc", "folder", "file", "view"):
    app.add_api_route(f"/{_place}/{{node_id}}", (lambda p: lambda node_id: deep_link(p, node_id))(_place),
                      methods=["GET"], include_in_schema=False)


if os.path.isdir(_STATIC_DIR):
    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="frontend")

