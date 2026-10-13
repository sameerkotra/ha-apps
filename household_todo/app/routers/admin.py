"""The Admin area's server side (all admin-only): App settings and
database backup and restore. The Users tab's routes are in
users.py."""
import json
import logging
import os

from fastapi import APIRouter, BackgroundTasks, Body, Depends, File, HTTPException, Response, UploadFile
from starlette.concurrency import run_in_threadpool

from .. import app_messages, config, db, geocode, ha_sensors, list_bin, maint_files, settings, task_files
from ..auth import require_admin
from ..common import backup_core

router = APIRouter(prefix="/api", tags=["admin"])
logger = logging.getLogger("admin")

RESTORE_PATH = "/api/admin-storage-import-db"
RESTORE_MAX = 2 * 1024 * 1024 * 1024          # a restored database file, at most (2 GB)


# ---------------------------------------------------------------------------
# App settings
# ---------------------------------------------------------------------------

@router.get("/admin/settings")
def get_settings(admin: dict = Depends(require_admin)):
    return dict(settings.payload(), maintenanceFiles=maint_files.admin_status())


@router.get("/admin/connected-apps")
def connected_apps(admin: dict = Depends(require_admin)):
    """The other household apps this one exchanges messages with (APP_MESSAGES_SPEC §5): read only."""
    return app_messages.connected_apps()


def _refresh_home_blocking() -> None:
    try:
        geocode.ensure_home_blocking()
    except Exception:   # best effort; the drive-time loop tries again
        pass


@router.put("/admin/settings")
def put_settings(background: BackgroundTasks, body=Body(...), admin: dict = Depends(require_admin)):
    """Any subset of the keys. 422 with a readable message for an unknown key
    or a bad value (nothing is saved then). Changes apply at once:
    - drive_times_enabled: off forgets the home location at once (nothing is
      looked up or sent any more); on geocodes home in the background;
    - home_address: the cached home location and every place's cached drive
      time are dropped; home is geocoded again in the background;
    - osrm_url / nominatim_url: used from the next request; places whose
      estimate failed (and a failed home lookup) are retried straight away;
    - expose_schedule_sensors: sensors are removed / re-published now;
    - sensor_refresh_minutes, avoid_tolls: read by their loops on the next
      tick (avoid_tolls re-queues every place by itself)."""
    if not isinstance(body, dict):
        raise HTTPException(422, "Send an object of settings to change.")
    body = dict(body)
    confirm = body.pop("confirm", False) is True
    if "maintenance_files_path" in body:
        # like Household Chat's files folder — refused / needs confirming as Check says; never moves files
        try:
            body["maintenance_files_path"] = maint_files.clean_path(body["maintenance_files_path"])
        except ValueError as e:
            raise HTTPException(422, str(e))
        if body["maintenance_files_path"] != maint_files.configured():
            info = maint_files.inspect(body["maintenance_files_path"])
            if info["refused"] or (info["needsConfirm"] and not confirm):
                raise HTTPException(409, info["message"])
    if isinstance(body.get("maintenance_recipients"), list):
        with db.get_conn() as conn:
            known = {r["id"] for r in conn.execute("SELECT id FROM users")}
        if any(not isinstance(x, str) or x not in known for x in body["maintenance_recipients"]):
            raise HTTPException(422, "Who gets maintenance notifications: unknown person.")
    try:
        _, changed = settings.update(body, admin)
    except settings.SettingsError as e:
        raise HTTPException(422, str(e))
    if "maintenance_files_path" in changed:
        maint_files.check(allow_setup=True)
    if "maintenance_enabled" in changed and settings.get("maintenance_enabled"):
        from .. import maintenance
        with db.get_conn() as conn:
            maintenance.ensure_list(conn)
    if changed & {"maintenance_enabled", "maintenance_sensor"}:
        background.add_task(ha_sensors.push_maintenance_blocking)
    if "drive_times_enabled" in changed:
        geocode.reset_home()
        if settings.drive_times_enabled():
            with db.get_conn() as conn:   # anything that failed while it was off gets another try
                conn.execute("UPDATE places SET drive_checked_at = NULL WHERE drive_minutes IS NULL")
    if "home_address" in changed:
        geocode.reset_home()
        with db.get_conn() as conn:
            conn.execute("UPDATE places SET drive_minutes = NULL, drive_checked_at = NULL, drive_tolls_avoided = NULL")
    if changed & {"osrm_url", "nominatim_url"}:
        geocode.retry_home_soon()
        with db.get_conn() as conn:
            conn.execute("UPDATE places SET drive_checked_at = NULL WHERE drive_minutes IS NULL")
    if (changed & {"drive_times_enabled", "home_address", "osrm_url", "nominatim_url"}
            and settings.drive_times_enabled() and settings.home_address()):
        background.add_task(_refresh_home_blocking)
    if "expose_schedule_sensors" in changed:
        background.add_task(ha_sensors.apply_exposure_change_blocking)
    return dict(settings.payload(), maintenanceFiles=maint_files.admin_status())


# ---------------------------------------------------------------------------
# Deleted lists (Admin → Storage): shared lists are kept 30 days after they're deleted
# ---------------------------------------------------------------------------

@router.get("/admin/deleted-lists")
def deleted_lists(admin: dict = Depends(require_admin)):
    with db.get_conn() as conn:
        return {"keepDays": list_bin.KEEP_DAYS, "lists": list_bin.entries(conn)}


@router.post("/admin/deleted-lists/{list_id}/restore")
def restore_deleted_list(list_id: str, admin: dict = Depends(require_admin)):
    with db.get_conn() as conn:
        row = list_bin.restore(conn, list_id, admin["id"])
    logger.info("AUDIT %s brought back the deleted list %r", admin["name"], row["name"])
    return {"id": row["id"], "name": row["name"]}


@router.delete("/admin/deleted-lists/{list_id}", status_code=204)
def remove_deleted_list(list_id: str, background: BackgroundTasks, admin: dict = Depends(require_admin)):
    with db.get_conn() as conn:
        list_bin.remove(conn, list_id)
    task_files.flush_soon(background)
    return Response(status_code=204)


@router.get("/admin-storage-download-db")
def admin_storage_download_db(admin: dict = Depends(require_admin)):
    """A full, consistent snapshot of the whole database — every household
    member's data. Never a plain file copy: the DB is in WAL mode, so see
    db.backup_to_tempfile. Secret App settings (the app-messages signing key)
    are blanked in the copy."""
    return backup_core.send_file(db.backup_to_tempfile(after=settings.REGISTRY.scrub_secrets),
                                 backup_core.file_name("household-todo-backup", ".db"))


def _too_big() -> HTTPException:
    return HTTPException(413, f"A backup file can be at most {RESTORE_MAX // (1024 * 1024 * 1024)} GB.")


def _restore_blocking(tmp_path: str) -> None:
    """Validate and swap in the uploaded file (in a worker thread: both read the whole file)."""
    try:
        db.validate_backup_file(tmp_path)
    except ValueError as e:
        raise HTTPException(400, str(e))
    # this install's maintenance files folder stays in use: the restored file may name another one
    keep_path, keep_store = maint_files.configured(), maint_files.store_id()
    secrets = settings.REGISTRY.saved_secrets()          # a backup carries no secrets: keep this install's
    db.import_from_tempfile(tmp_path)
    settings.invalidate()
    with db.get_conn() as conn:
        conn.execute("INSERT INTO app_settings (key, value, updated_at, updated_by) VALUES "
                     "('maintenance_files_path', ?, ?, NULL) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                     (json.dumps(keep_path), config.now_iso()))
    maint_files.restore_store_id(keep_store)
    settings.REGISTRY.keep_secrets(secrets)
    settings.invalidate()


@router.post("/admin-storage-import-db")
async def admin_storage_import_db(background: BackgroundTasks, file: UploadFile = File(...),
                                  admin: dict = Depends(require_admin)):
    """Replaces the ENTIRE database with an uploaded .db file — no merge, no
    undo. The upload is validated before the live database is touched.

    The scratch file is created inside config.DATA_DIR (not /tmp) so the
    final os.replace() is a same-filesystem rename: /tmp and /data are
    separate mounts in the app's container, and os.replace() can't cross
    devices (OSError 18). Checking and swapping run in a worker thread, so
    the rest of the app keeps answering meanwhile."""
    tmp_path = await backup_core.receive(file, config.DATA_DIR, prefix=db.TEMP_PREFIX, max_bytes=RESTORE_MAX,
                                         too_big=_too_big)
    try:
        await run_in_threadpool(_restore_blocking, tmp_path)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    # The restored file brings its own App settings: forget the old
    # home location, and make Home Assistant reflect the restored schedule
    # items (or their removal, if that backup had exposure off) straight away.
    # Background tasks, not fire-and-forget executor jobs: they run right
    # after the response and are tied to this request (an untracked job could
    # still be writing while the database is swapped again, e.g. in tests).
    geocode.reset_home()
    background.add_task(ha_sensors.apply_exposure_change_blocking)
    background.add_task(_refresh_home_blocking)
    background.add_task(maint_files.check)
    return {"status": "ok"}
