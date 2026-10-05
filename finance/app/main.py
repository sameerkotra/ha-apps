import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import features, jobs, purge, security, settings
from .common import sandbox_run, web_security
from .version import APP_VERSION
from .db import DB_PATH, normalize_legacy_transaction_dates, run_migrations
from .routes import (accounts, admin_storage, ai_usage_page, categories, csv_import, dashboard, deletion, pdf_view, query, recurring,
                     reports, settings_page, review, toll_statements, tolls, transactions, transfers, upload, users, utilities)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # /data closed to every user but root: the PDF tools run as pdfworker (common/python/sandbox_run.py)
    sandbox_run.lock_down(os.path.dirname(os.path.abspath(DB_PATH)))
    run_migrations()
    settings.seed_from_addon_options()  # first start: AI address/model from the old app options
    normalize_legacy_transaction_dates()  # cheap no-op once every stored date is ISO
    jobs.recover_interrupted_jobs()       # the in-memory queue did not survive the restart
    jobs.start(asyncio.get_running_loop())
    purge.start_purge_worker()            # 30-day soft-delete purge (SPEC.md section 11)
    yield


app = FastAPI(title="Finance Dashboard", lifespan=lifespan)
_templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
_templates.env.globals["app_version"] = APP_VERSION


@app.exception_handler(features.FeatureOff)
async def _feature_off(request: Request, exc: features.FeatureOff):
    return _templates.TemplateResponse(request, "feature_off.html", {
        "user": exc.user, "feature_label": features.NAMES.get(exc.name, exc.name), "acting_qs": ""}, status_code=404)


@app.middleware("http")
async def _features(request, call_next):
    """Which optional parts are on (App settings → Features), for every page's nav."""
    if not request.url.path.startswith(("/static", request.scope.get("root_path", "") + "/static")):
        request.state.features = features.current()
    return await call_next(request)


# Scripts only from the app itself: the page scripts are static/*.js and static/pages/*.js, the templates
# carry no inline scripts or on* handlers, and no htmx attribute needs eval (no hx-on, no js: values, no
# trigger filters). Styles: htmx adds its indicator <style> and the templates use style attributes, so
# inline styles stay allowed. PDFs are shown as rendered page images, so nothing needs a plugin.
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
       "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; "
       "frame-ancestors 'self'")

# Every response, static files included, is `no-store`. The Home Assistant
# companion app's WebView was seen serving stale pages and even a mix of two
# versions of style.css despite versioned URLs; for a small single-household
# app, never caching costs nothing that matters.
HEADERS = web_security.SecurityHeaders(CSP, all_no_store=True)
HEADERS.install(app)


security.install(app)  # added last, so it runs first: ingress-only + cross-site POST refusal

app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
for module in (accounts, transactions, csv_import, review, categories, transfers, dashboard, upload,
               admin_storage, ai_usage_page, query, reports, users, deletion, recurring, utilities, tolls, toll_statements, pdf_view, settings_page):
    app.include_router(module.router)


@app.get("/")
def root():
    return RedirectResponse(url="accounts")  # no leading slash — see SPEC.md section 1
