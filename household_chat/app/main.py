import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from . import (app_messages, avatars, calls, chats, config, db, disappearing, files, ha_client, ha_events, housekeeping,
               notifier, presence, settings)
from .common import auth_core, ha_notify, ha_people, web_security
from .common import housekeeping as jobs_core
from .live import hub
from .routers import admin, calls as calls_router, conversations, extras, files as files_router, me, messages, search, stream

jobs_core.setup_logging()
logger = logging.getLogger("main")


hub.on_online_change = presence.online_changed      # one person's online state, not "everyone reload"


def remove_uploaded_photos() -> None:
    """Photos come only from Home Assistant. Older databases may still hold photos people uploaded
    themselves (no avatar_src) and chat photos (/data/chat_photos): remove them."""
    import shutil
    shutil.rmtree(os.path.join(config.DATA_DIR, "chat_photos"), ignore_errors=True)
    with db.get_conn() as conn:
        rows = conn.execute("SELECT id, avatar_file FROM users WHERE avatar_file IS NOT NULL AND avatar_src IS NULL").fetchall()
        for r in rows:
            try:
                os.remove(os.path.join(config.AVATAR_DIR, r["avatar_file"]))
            except OSError:
                pass
            conn.execute("UPDATE users SET avatar_file = NULL WHERE id = ?", (r["id"],))
        cols = {c["name"] for c in conn.execute("PRAGMA table_info(conversations)")}
        if "photo_file" in cols:
            conn.execute("UPDATE conversations SET photo_file = NULL WHERE photo_file IS NOT NULL")


remove_stale_temp = housekeeping.remove_stale_temp


def keep_no_limit_if_over() -> None:
    """The chat folder's limit is 50 GB unless an admin chose another. An install that already holds more and
    never chose one keeps "no limit", so nobody's uploads stop working after an update."""
    with db.get_conn() as conn:
        if db.get_setting(conn, "share_folder_quota_gb") is not None:
            return
        if files.usage_bytes(conn) > 0.9 * settings.DEFAULTS["share_folder_quota_gb"] * 1024 ** 3:
            db.set_setting(conn, "share_folder_quota_gb", "0", "household_chat")
            logger.info("The chat folder already holds more than the new default limit: it keeps no limit "
                        "(Admin → App settings → Files).")
    settings.invalidate()


def startup_blocking() -> None:
    remove_stale_temp(0)
    keep_no_limit_if_over()
    # is the chat files folder there and ours? (creates it on the very first start; SPEC §5.3.1)
    st = files.check()
    if not st["online"]:
        logger.warning("Starting without the chat files folder: %s", st["reason"])
    # before anything is served: nothing past its time may come back after a restore (SPEC §15.8)
    disappearing.run_expiry()
    if calls.close_unfinished():         # calls that were on when the app stopped (SPEC §15.12)
        logger.info("Closed calls that were on when the app stopped.")
    ha_client.learn_page_blocking()      # where phone notifications open (SPEC §7)
    ha_client.load_time_zone_blocking()  # logs the zone in use
    try:
        n = ha_client.sync_users_blocking()
        logger.info("Synced %s Home Assistant people.", n)
    except Exception:
        logger.exception("Syncing Home Assistant persons failed")
    with db.get_conn() as conn:   # a 🏠 choice that is their own login's person means "automatic"
        conn.execute("UPDATE users SET presence_entity = NULL WHERE presence_entity = ha_person")
    remove_uploaded_photos()
    try:
        avatars.sync_blocking()          # people's photos are their Home Assistant pictures (SPEC §15.3.1)
    except Exception:
        logger.exception("Syncing photos failed")
    out = chats.Outbox()
    with db.get_conn() as conn:
        # everyone enabled has a personal room (also for people enabled before rooms existed)
        for r in conn.execute("SELECT id FROM users WHERE disabled = 0").fetchall():
            chats.ensure_personal(conn, r["id"])
    out.flush()
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
    await run_in_threadpool(notifier.load_held)      # quiet-hour and batched pushes held when the app stopped
    # one WebSocket to Home Assistant for the phone's notification buttons and the other apps' messages
    await run_in_threadpool(app_messages.start, ha_events.connection())
    ha_events.start()
    jobs = jobs_core.Jobs()                      # started in this order, cancelled on shutdown
    jobs.every("housekeeping", 20, housekeeping_step, thread=False, tick=True, log=logger, error="Housekeeping failed")
    jobs.every("calls", 2, calls.tick, log=logger, error="Checking calls failed")   # rings that time out
    jobs.add("ha_people", ha_people.loop)
    jobs.start()
    try:
        yield
    finally:
        await jobs.stop()
        try:
            await run_in_threadpool(notifier.save_held, True)
        except Exception:
            logger.exception("Saving the held notifications failed")
        app_messages.stop()
        ha_events.stop()
        hub.reset()
        calls.reset()


app = FastAPI(title="Household Chat", lifespan=lifespan)
for r in (me, conversations, messages, files_router, search, stream, admin, extras, calls_router):
    app.include_router(r.router)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    """Plain messages; never echo input back (it may be someone's message)."""
    msgs = []
    for e in exc.errors():
        loc = ".".join(str(x) for x in e.get("loc", []) if x not in ("body", "query", "path"))
        msgs.append(f"{loc}: {e.get('msg')}" if loc else str(e.get("msg")))
    return JSONResponse(status_code=422, content={"detail": "; ".join(msgs) or "Invalid request."})


# Only Supervisor's ingress proxy gets in: app/common/auth_core.INGRESS_HOSTS.
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "
       "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'self'")
MAX_BODY = 1024 * 1024
# File uploads (a chat's /uploads, a shared folder's /upload) and the restore enforce their own, smaller limits
# while they read; the body limit only keeps them under the largest those settings allow.
UPLOADS = ("/uploads", "/upload")
UPLOAD_MAX = 1024 * 1024 * 1024 + MAX_BODY          # max_upload_mb's highest value
RESTORE_PATH = "/api/admin-storage-import-db"


class BodyLimit(web_security.BodyLimit):
    """The shared limit (counted as it arrives, also without a Content-Length): 1 MB for everything but uploads."""

    def limit_for(self, path: str) -> int:
        if path.startswith("/api/") and path.endswith(UPLOADS):
            return UPLOAD_MAX
        return super().limit_for(path)


app.add_middleware(BodyLimit, default_max=MAX_BODY, routes={RESTORE_PATH: admin.RESTORE_MAX})


def _is_file(path: str) -> bool:
    """A chat file or shared-folder file: its route sets its own CSP (sandbox) and caching."""
    return path.startswith("/api/files/") or path.startswith("/api/folders/")


# Every page asset is linked with ?v=<version>, so a versioned one is cached for good (static files are served at /).
HEADERS = web_security.SecurityHeaders(
    CSP, csp_skip=lambda request, _response: _is_file(request.url.path), referrer="no-referrer",
    api_no_store="set", api_skip=lambda path: _is_file(path) or path.startswith("/api/avatars/"),
    static_immutable=True, static_prefixes=("/",))


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
if os.path.isdir(_STATIC_DIR):
    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="frontend")
