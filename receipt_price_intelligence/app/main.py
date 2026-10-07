"""Main FastAPI application entry point."""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.api import admin, alerts, analysis, backup, budgets, deals, drafts, export, extraction, homes, images, imports, items, lookout, nearby, notifications, planner, shoplist, stores, webdebug, webinfo
from app import app_messages, app_settings
from app.common import sandbox_run, web_security
from app.auth import ingress_gate, sync_roles
from app.config import APP_VERSION, get_settings
from app.db import get_db_session, init_models
from app.services import reqcache
from app.logging_config import get_logger, setup_logging
from app.services import homes as homes_service
from app.services import scheduler



@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events."""
    # Startup
    logger = get_logger("main")
    logger.info("Starting Receipt Price Intelligence...")

    # Initialize database and models
    try:
        init_models()
        logger.info("Database models initialized")
    except Exception as e:
        logger.error("Failed to initialize database models: %s", e)
        raise

    # The role column mirrors the admin_users option
    try:
        sync_roles()
    except Exception as e:
        logger.error("Could not refresh administrators: %s", e)

    # Data created before homes existed gets attached to a home
    try:
        with get_db_session()() as db:
            homes_service.adopt_legacy_data(db)
    except Exception as e:
        logger.error("Could not attach existing data to a home: %s", e)

    # Ensure image directories exist
    settings = get_settings()
    os.makedirs(settings.image_path, exist_ok=True)
    os.makedirs(os.path.join(settings.image_path, "drafts"), exist_ok=True)
    os.makedirs(os.path.join(settings.image_path, "receipts"), exist_ok=True)
    logger.info("Image storage directories created")

    # The database and photos closed to every user but root: the PDF tools run as pdfworker (sandbox_run)
    for folder in {os.path.dirname(os.path.abspath(settings.database_path)), os.path.abspath(settings.image_path)}:
        sandbox_run.lock_down(folder)

    # A changed log level applies straight away (the other App settings are read when used)
    app_settings.on_change(_apply_log_level)

    # Daily best-price check (also catches up on anything out of date right now)
    scheduler.start()

    # The household apps bus: the Household Assistant asks (app_messages.py, tools.py)
    try:
        app_messages.start()
    except Exception as e:
        logger.error("Could not join the household apps bus: %s", e)

    yield

    # Shutdown
    app_messages.stop()
    scheduler.stop()
    logger.info("Shutting down Receipt Price Intelligence...")


# Create FastAPI application
app = FastAPI(
    title="Receipt Price Intelligence",
    version=APP_VERSION,
    description="Home Assistant app for receipt capture, AI extraction, and price intelligence",
    lifespan=lifespan,
)

# Setup logging
setup_logging()
logger = get_logger("main")


# Mount static files for frontend
frontend_path = os.path.join(os.path.dirname(__file__), "static")
frontend_path = os.path.abspath(frontend_path)
if os.path.exists(frontend_path):
    try:
        app.mount("/static", StaticFiles(directory=frontend_path), name="static")
        logger.info("Static files mounted from %s", frontend_path)
    except Exception as e:
        logger.warning("Failed to mount static files: %s", e)


# Pages: no inline scripts (the page scripts are static/pages/*.js); inline styles are still used (the
# pages' <style> blocks and style attributes). PDFs (receipt photos can be PDFs) get no CSP: Chrome's
# viewer refuses a PDF whose own policy has object-src 'none'.
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
       "media-src 'self' blob:; connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'self'; "
       "form-action 'self'; frame-ancestors 'self'")

# Pages and static files are never cached: "no-cache" (the previous setting) only requires a cache to
# check back with the server before reusing a copy; some webviews, including the Home Assistant
# companion app's on some platforms, have been seen to skip that check and just reuse whatever they
# already have. "no-store" is a stronger instruction that a compliant cache cannot reuse at all, only
# serve fresh each time. (A route's own Cache-Control is kept; the API's answers are left alone.)
HEADERS = web_security.SecurityHeaders(CSP, csp_skip=web_security.is_pdf, pages_no_cache=False, other_no_store=True)
HEADERS.install(app)


app.include_router(admin.router)
app.include_router(homes.router)
app.include_router(stores.router)
app.include_router(items.router)
app.include_router(analysis.router)
app.include_router(deals.router)
app.include_router(backup.router)
app.include_router(planner.router)
app.include_router(budgets.router)
app.include_router(export.router)
app.include_router(webinfo.router)
app.include_router(nearby.router)
app.include_router(webdebug.router)
app.include_router(shoplist.router)
app.include_router(lookout.router)
app.include_router(notifications.router)
app.include_router(alerts.router)
app.include_router(imports.router)
app.include_router(drafts.router)
app.include_router(extraction.router)
app.include_router(images.router)


@app.middleware("http")
async def read_once_per_request(request, call_next):
    """Expensive reads (the home's stores, its receipt history) are made once per request; see reqcache."""
    with reqcache.scope():
        return await call_next(request)


# Outermost: only requests through Home Assistant's ingress get in, and the API knows who is asking.
app.middleware("http")(ingress_gate)


def _apply_log_level(changed, values):
    if "log_level" in changed:
        import logging
        level = getattr(logging, values["log_level"], logging.INFO)
        logging.getLogger().setLevel(level)
        for handler in logging.getLogger().handlers:
            handler.setLevel(level)


def _page(name: str) -> HTMLResponse:
    """A frontend page, with its scripts and stylesheet tagged with the app version.

    Phones (the Home Assistant app in particular) keep old copies of scripts and styles for a long
    time; without this an update can leave a page with the old navigation bar. The version in the
    URL forces a fresh copy after every update, and the page itself is never cached. The version is
    also baked into the page (``data-app-version`` on app.js's script tag: no inline script, see the
    Content-Security-Policy) so app.js can notice, on its own, if it
    is somehow still running against a stale copy (see ``checkVersion`` in app.js) and recover
    without the person needing to know to clear a cache by hand.
    """
    with open(_frontend_file(name), encoding="utf-8") as f:
        html = f.read()
    for asset in ("app.css", "app.js", "common/backnav.js", "common/whoami.js", "common/theme-boot.js", "common/themes.css",
                  "common/ui.js", "common/settings.js", "common/settings.css", f"pages/{name[:-5]}.js"):
        html = html.replace(f'static/{asset}"', f'static/{asset}?v={APP_VERSION}"')
    html = html.replace('<script src="static/app.js', f'<script data-app-version="{APP_VERSION}" src="static/app.js', 1)
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


def _frontend_file(name: str) -> str:
    """Absolute path of a frontend page, or a 404 if it is missing."""
    path = os.path.abspath(os.path.join(os.path.dirname(__file__), "static", name))
    if not os.path.exists(path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Frontend not found. Make sure app/static contains {name}",
        )
    return path


# Root: the receipts home page (also what Home Assistant ingress opens)
@app.get("/", include_in_schema=False)
async def serve_start():
    """What opens first (also what Home Assistant ingress opens): the shopping list."""
    return _page("list.html")


@app.get("/index.html", include_in_schema=False)
async def serve_index():
    """Serve the receipts page."""
    return _page("index.html")


@app.get("/api/v1/archive")
async def archive_status():
    """Whether scanned receipts are also kept in a folder (the *Receipt archive folder* option), where, and whether
    that folder can be written to."""
    from app.services import archive
    return archive.status()


@app.get("/api/info")
async def api_info():
    """API information."""
    return {
        "name": "Receipt Price Intelligence API",
        "version": APP_VERSION,
        "docs": "docs",
        "health": "health",
    }


# Health endpoints (the Supervisor's watchdog checks /health)
@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok"}


@app.get("/health/model")
async def model_health():
    """Whether a model is set up (Admin → App settings). Answered without a user, so it never names the model."""
    settings = get_settings()
    return {"configured": settings.model_ready}


@app.get("/health/database")
async def database_health():
    """Database health check. Answered without a user: the error itself goes to the log, not the answer."""
    try:
        db_session = get_db_session()
        with db_session() as db:
            db.execute(text("SELECT 1"))
        return {"status": "ok", "connected": True}
    except Exception:
        get_logger("main").exception("Database health check failed")
        return {"status": "error", "connected": False}


# Frontend pages, served as they are (with the app version, see _page)
_PAGES = ("admin.html", "whoami.html", "capture.html", "review.html", "deals.html", "backup.html", "trip.html", "import.html", "list.html",
          "debug.html", "stores.html", "analysis.html", "notifications.html")


def _page_route(name: str):
    async def serve_page():
        return _page(name)
    return serve_page


for _name in _PAGES:
    app.add_api_route(f"/{_name}", _page_route(_name), methods=["GET"], include_in_schema=False)


# "/<page>/insights" … asked of the app itself (a link from Home Assistant): a relative redirect to that page
_LINK_PAGES = {"list": "list.html", "receipts": "index.html", "insights": "analysis.html", "deals": "deals.html",
               "trip": "trip.html", "stores": "stores.html"}


def _link_route(name: str):
    async def link():
        return RedirectResponse(url=_LINK_PAGES[name], status_code=307, headers={"Cache-Control": "no-store"})
    return link


for _name in _LINK_PAGES:
    app.add_api_route(f"/{_name}", _link_route(_name), methods=["GET"], include_in_schema=False)


@app.get("/draft/{draft_id}", include_in_schema=False)
async def serve_draft_review(draft_id: str):
    """Older review URL. Redirects (relatively, so it works under an ingress prefix)."""
    return RedirectResponse(url=f"../review.html?id={draft_id}")


# Error handlers
@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """Global exception handler for logging."""
    logger = get_logger("main")
    logger.error("Unhandled exception: %s", exc, exc_info=True)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=os.environ.get("RECEIPT_DEV_RELOAD") == "1",
        # The ingress check needs the real TCP peer (Supervisor's proxy), never an address taken from
        # X-Forwarded-For (tools/check_build.py checks this).
        proxy_headers=False,
    )
