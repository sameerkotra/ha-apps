"""Admin-only endpoints behind the Admin page: App settings
(GET/PUT /api/admin/settings, the AI "Test connection" probe) and
Storage (full-database backup and restore).

Route name (`admin-storage-download-db`) matches the naming used for this
same pattern in the other household apps — see HA_ADDON_PATTERNS.md
section 3.
"""
import asyncio
import os

from fastapi import APIRouter, BackgroundTasks, Body, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, ValidationError

from .. import ai_client, config, db, ha_sync, settings
from ..auth import require_admin
from ..common import backup_core

router = APIRouter(prefix="/api", tags=["admin"])

_AI_KEYS = {"ai_provider", "ai_url", "ai_model", "ai_api_key", "ai_max_tokens"}


# ---------- App settings ----------

@router.get("/admin/settings")
def get_app_settings(admin: dict = Depends(require_admin)):
    """{values, defaults, meta, secrets, providers}. The access key is never
    included — only secrets.ai_api_key = {saved, hint: "…last 4"}."""
    return settings.payload()


@router.put("/admin/settings")
def put_app_settings(background: BackgroundTasks, body: dict = Body(...), admin: dict = Depends(require_admin)):
    """Body: any subset of the setting keys, plus `clear_ai_api_key: true` to
    remove the saved access key (a blank `ai_api_key` keeps it). Unknown keys
    or bad values → 422 with a readable message; nothing is stored then.

    Turning expose_daily_calories_sensor on publishes every user's sensor
    right away; turning it off removes them from Home Assistant. That runs
    as a background task just after the response (it's up to one HA call per
    user), with no DB connection held during the HTTP calls."""
    try:
        changed = settings.update(body, admin)
    except settings.SettingsError as e:
        raise HTTPException(422, str(e))
    if _AI_KEYS & set(changed):
        ai_client.forget_state()
    if "expose_daily_calories_sensor" in changed:
        background.add_task(ha_sync.apply_exposure_change)
    return settings.payload()


class AIProbe(BaseModel):
    model_config = ConfigDict(extra="ignore")
    ai_provider: str | None = None
    ai_url: str | None = None
    ai_model: str | None = None
    ai_api_key: str | None = None
    clear_ai_api_key: bool = False


@router.post("/admin/settings/test-ai")
async def test_ai(payload: AIProbe | None = None, admin: dict = Depends(require_admin)):
    """The "Test connection" button: asks the provider which models it offers
    (GET /api/tags, /models or /v1/models, 10 s timeout). Uses the values
    typed on the page (unsaved), each missing one = the saved value; a blank
    key box means the saved key (unless "Remove the saved key" is ticked).
    Generates nothing, saves nothing, and holds no DB connection during the
    network call."""
    p = payload or AIProbe()
    current = settings.all()
    probe = {k: v for k, v in p.model_dump().items() if k != "clear_ai_api_key" and v is not None}
    if p.clear_ai_api_key:
        probe["ai_api_key"] = ""
    elif not (probe.get("ai_api_key") or "").strip():
        probe.pop("ai_api_key", None)
    try:
        v = settings.AppSettings(**{**current, **probe}).model_dump()
    except ValidationError as e:
        raise HTTPException(422, settings.readable(e))
    cfg = ai_client.Config(provider=v["ai_provider"], url=settings.ai_url(v["ai_provider"], v["ai_url"]),
                           model=v["ai_model"], api_key=v["ai_api_key"], max_tokens=v["ai_max_tokens"])
    return await asyncio.to_thread(ai_client.test_connection, cfg)


# ---------- Storage ----------


@router.get("/admin-storage-download-db")
def admin_storage_download_db(admin: dict = Depends(require_admin)):
    """Admin-only: downloads a full, consistent snapshot of the app's
    entire SQLite database (every household member's data, not just the
    acting user's) — for a manual backup or to open with an external
    SQLite tool. See db.backup_to_tempfile for why this isn't a plain
    file copy. The AI access key is blanked in the copy: a backup file
    never carries it."""
    tmp_path = db.backup_to_tempfile(after=settings.REGISTRY.scrub_secrets)
    return backup_core.send_file(tmp_path, backup_core.file_name("calorie-tracker-backup", ".db", now=config.now()))


@router.post("/admin-storage-import-db")
async def admin_storage_import_db(file: UploadFile = File(...), admin: dict = Depends(require_admin)):
    """Admin-only: restores the entire database from a .db file previously
    produced by the download button above — meant for "I reinstalled the
    app / lost the volume, here's my last backup" recovery, not routine
    use. This REPLACES every user's food logs, weights, goals and saved
    foods currently stored; there is no merge and no undo once it
    succeeds. The upload is validated (see db.validate_backup_file) before
    anything about the live database is touched.

    The scratch file is created inside config.DATA_DIR (not the default
    /tmp) specifically so the final os.replace() (in
    db.import_from_tempfile) is a same-filesystem rename: /tmp and /data
    are typically separate mounts in the app's container (Supervisor
    bind-mounts /data as its own volume), and os.replace() can't do a
    cross-device move — it fails with "OSError: [Errno 18] Cross-device
    link" if the two paths aren't on the same filesystem.

    The restored file's App settings apply at once — except the AI access
    key: a downloaded backup has none, so this install keeps its own."""
    saved_secrets = settings.REGISTRY.saved_secrets()
    tmp_path = await backup_core.receive(file, config.DATA_DIR)
    try:
        try:
            db.validate_backup_file(tmp_path)
        except ValueError as e:
            raise HTTPException(400, str(e))
        db.import_from_tempfile(tmp_path)
        settings.REGISTRY.keep_secrets(saved_secrets)
        # The restored file may carry different App settings (init_db bumps
        # db.generation, which drops settings' cache); a warm-up recorded
        # against the old values no longer means anything.
        ai_client.forget_state()
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    return {"status": "ok"}
