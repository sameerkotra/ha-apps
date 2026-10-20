import asyncio
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import HTTPException, FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import app_messages, config, features, statement_due, tools, jobs, purge, security, settings, storage
from .common import ha_people, sandbox_run, web_security
from .version import APP_VERSION
from .db import DB_PATH, normalize_legacy_transaction_dates, run_migrations
from .routes import (accounts, admin_storage, ai_usage_page, categories, csv_import, dashboard, deletion, pdf_view, query, recurring,
                     people_data, reports, settings_page, review, statement_reminders, toll_statements, tolls, transactions, transfers, upload, users, utilities)


# The largest backup Admin → Storage → Import accepts (a database of many years is a few hundred MB).
MAX_BACKUP_BYTES = 2 * 1024 * 1024 * 1024


@asynccontextmanager
async def lifespan(app: FastAPI):
    # /data closed to every user but root: the PDF tools run as pdfworker (common/python/sandbox_run.py)
    sandbox_run.lock_down(os.path.dirname(os.path.abspath(DB_PATH)))
    run_migrations()
    settings.seed_from_addon_options()  # first start: AI address/model from the old app options
    normalize_legacy_transaction_dates()  # cheap no-op once every stored date is ISO
    jobs.recover_interrupted_jobs()       # the in-memory queue did not survive the restart
    jobs.start(asyncio.get_running_loop())
    jobs.resume_waiting()                 # uploads that were only waiting their turn go back in the queue
    purge.start_purge_worker()            # 30-day soft-delete purge (SPEC.md section 11)
    app_messages.start()                  # the household apps bus: the Household Assistant asks (tools.py)
    # Overdue statement reminders (statement_due.py) and the people and phones they go to (common/ha_people.py):
    # only inside Home Assistant — without the Supervisor's token nothing could be sent (the tests).
    loops = []
    if config.SUPERVISOR_TOKEN:
        loops = [asyncio.ensure_future(ha_people.loop()), asyncio.ensure_future(statement_due.loop())]
    yield
    for task in loops:
        task.cancel()
    app_messages.stop()


app = FastAPI(title="Finance Dashboard", lifespan=lifespan)
from .template_filters import install as _install_filters
_templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
_install_filters(_templates.env)
_templates.env.globals["app_version"] = APP_VERSION

# Request bodies are counted as they arrive (a chunked upload has no Content-Length to check first), and
# refused past their route's limit before the route reads them. The upload routes allow their most files
# of the largest size (generous on purpose); everything else is a form of a few KB.
app.add_middleware(
    web_security.BodyLimit,
    default_max=2 * 1024 * 1024,
    routes={
        "/upload": storage.MAX_UPLOAD_REQUEST_BYTES,
        "/utility-upload": storage.MAX_UPLOAD_REQUEST_BYTES,
        "/toll-upload": storage.MAX_UPLOAD_REQUEST_BYTES,
        "/transactions-import-csv": storage.MAX_CSV_REQUEST_BYTES,
        "/admin-storage-import-db": MAX_BACKUP_BYTES,
        "/api/admin/people/data/import": MAX_BACKUP_BYTES,      # one person's data (routes/people_data.py)
    },
    no_chunked_prefixes=("/admin-storage-import-db",),
)


@app.exception_handler(features.FeatureOff)
async def _feature_off(request: Request, exc: features.FeatureOff):
    return _templates.TemplateResponse(request, "feature_off.html", {
        "user": exc.user, "feature_label": features.NAMES.get(exc.name, exc.name), "acting_qs": ""}, status_code=404)


@app.middleware("http")
async def _features(request, call_next):
    """Which optional parts are on (App settings → Features), for every page's nav."""
    if not request.url.path.startswith(("/static", request.scope.get("root_path", "") + "/static")):
        request.state.features = features.current()
        request.state.sidebar_page = tools.PANEL["value"]      # links open a page there (base.js, deeplink.js)
    return await call_next(request)


# Scripts only from the app itself: the page scripts are static/*.js and static/pages/*.js, the templates
# carry no inline scripts or on* handlers, and no htmx attribute needs eval (no hx-on, no js: values, no
# trigger filters). Styles: htmx adds its indicator <style> and the templates use style attributes, so
# inline styles stay allowed. PDFs are shown as rendered page images, so nothing needs a plugin.
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
       "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; "
       "frame-ancestors 'self'")

# Pages, fragments and everything else are `no-store`: the Home Assistant companion app's WebView was
# seen serving stale pages. A static file asked for with ?v=<version> (every link in the templates
# carries the app version) is kept for good instead — the version in its address changes with every
# release, so a browser can never mix two versions — which saves fetching every script and stylesheet
# again on each page. A route that sets its own Cache-Control (the PDF viewer's page images) keeps it.
HEADERS = web_security.SecurityHeaders(CSP, static_immutable=True, other_no_store=True)
HEADERS.install(app)


security.install(app)  # added last, so it runs first: ingress-only + cross-site POST refusal

app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
for module in (accounts, transactions, csv_import, review, categories, transfers, dashboard, upload,
               admin_storage, ai_usage_page, query, reports, users, people_data, deletion, recurring, utilities, tolls, toll_statements, pdf_view, settings_page,
               statement_reminders):
    app.include_router(module.router)


@app.get("/api/health", include_in_schema=False)
def health():
    """For the Supervisor's watchdog (config.yaml): the app answers HTTP, not just accepts connections."""
    return {"status": "ok"}


@app.get("/")
def root():
    return RedirectResponse(url="accounts")  # no leading slash — see SPEC.md section 1


# "/<page>/uploads/<account id>" (an overdue statement's notification, statement_due.tap_link): Upload with that
# account picked. The page's own deeplink handling (static/base.js) opens the same.
@app.get("/uploads/{account_id}", include_in_schema=False)
def upload_link(account_id: str):
    if not re.fullmatch(r"\d{1,12}", account_id):
        raise HTTPException(404, "Not found.")
    return RedirectResponse(url=f"../uploads?account={account_id}", status_code=307, headers={"Cache-Control": "no-store"})


# "/<page>/accounts/<account id>" (a card payment reminder, alerts.card_link): that account's page.
@app.get("/accounts/{account_id}", include_in_schema=False)
def account_link(account_id: str):
    if not re.fullmatch(r"\d{1,12}", account_id):
        raise HTTPException(404, "Not found.")
    return RedirectResponse(url=f"../account?id={account_id}", status_code=307, headers={"Cache-Control": "no-store"})


# "/<page>/month/<YYYY-MM>" asked of the app itself (a link from Home Assistant): that month's dashboard. /dashboard
# and /recurring are pages already.
@app.get("/month/{month}", include_in_schema=False)
def month_link(month: str):
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise HTTPException(404, "Not found.")
    # The dashboard's own key for one month is "m:YYYY-MM" (dashboard.resolve_period); a bare month fell back to this one.
    return RedirectResponse(url=f"../dashboard?period=m:{month}", status_code=307, headers={"Cache-Control": "no-store"})
