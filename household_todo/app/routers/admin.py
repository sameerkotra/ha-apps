"""The Admin area's server side (all admin-only): App settings and
database backup and restore. The Users tab's routes are in
users.py."""
import json
import os
import tempfile
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Body, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from .. import config, db, geocode, ha_sensors, maint_files, settings
from ..auth import require_admin

router = APIRouter(prefix="/api", tags=["admin"])


# ---------------------------------------------------------------------------
# App settings
# ---------------------------------------------------------------------------

@router.get("/admin/settings")
def get_settings(admin: dict = Depends(require_admin)):
    return dict(settings.payload(), maintenanceFiles=maint_files.admin_status())


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


@router.get("/admin-storage-download-db")
def admin_storage_download_db(admin: dict = Depends(require_admin)):
    """A full, consistent snapshot of the whole database — every household
    member's data. Never a plain file copy: the DB is in WAL mode, so see
    db.backup_to_tempfile."""
    tmp_path = db.backup_to_tempfile()
    filename = f"household-todo-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
    return FileResponse(
        tmp_path,
        media_type="application/vnd.sqlite3",
        filename=filename,
        background=BackgroundTask(os.remove, tmp_path),
    )


@router.post("/admin-storage-import-db")
async def admin_storage_import_db(background: BackgroundTasks, file: UploadFile = File(...),
                                  admin: dict = Depends(require_admin)):
    """Replaces the ENTIRE database with an uploaded .db file — no merge, no
    undo. The upload is validated before the live database is touched.

    The scratch file is created inside config.DATA_DIR (not /tmp) so the
    final os.replace() is a same-filesystem rename: /tmp and /data are
    separate mounts in the app's container, and os.replace() can't cross
    devices (OSError 18)."""
    fd, tmp_path = tempfile.mkstemp(suffix=".db", dir=config.DATA_DIR)
    try:
        with os.fdopen(fd, "wb") as out:
            while chunk := await file.read(1024 * 1024):
                out.write(chunk)
        try:
            db.validate_backup_file(tmp_path)
        except ValueError as e:
            raise HTTPException(400, str(e))
        # this install's maintenance files folder stays in use: the restored file may name another one
        keep_path, keep_store = maint_files.configured(), maint_files.store_id()
        db.import_from_tempfile(tmp_path)
        settings.invalidate()
        with db.get_conn() as conn:
            conn.execute("INSERT INTO app_settings (key, value, updated_at, updated_by) VALUES "
                         "('maintenance_files_path', ?, ?, NULL) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                         (json.dumps(keep_path), config.now_iso()))
        maint_files.restore_store_id(keep_store)
        settings.invalidate()
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
