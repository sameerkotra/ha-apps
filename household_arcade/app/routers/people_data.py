"""Admin → Users → ⋯ Data: one person's data — export, import, delete (PERSON_DATA_SPEC.md §2.6; what is a
person's is app/people_data.py). Admin only, like every Admin route (the signed-in admin: this app has no "acting
as"); the ingress and cross-site guards in main.py apply as everywhere.

    GET  /api/admin/people/{id}/data                   what they have (the dialogs; the delete's dry run)
    POST /api/admin/people/{id}/data/export            {options} → the .zip (always small here: no files)
    GET  /api/admin/people/data/jobs/{job}             big exports' progress (none in this app: always 404)
    GET  /api/admin/people/data/jobs/{job}/file
    POST /api/admin/people/data/import                 multipart "file" → {token, preview} (kept an hour)
    POST /api/admin/people/{id}/data/import/{token}    {mode: add | replace, confirm?, options} → {message}
    POST /api/admin/people/{id}/data/delete            {confirm, forget} → {message}

The routes are plain `def`s (FastAPI runs them in its thread pool: the zip and the database work never hold up
the event loop), except the upload, which streams the file to disk first.
"""
import os

from fastapi import APIRouter, BackgroundTasks, Body, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from starlette.concurrency import run_in_threadpool

from .. import config, db, ha_sensors
from .. import people_data as data
from ..auth import require_admin
from ..common import backup_core
from ..common import person_data as pd

router = APIRouter(prefix="/api/admin/people", tags=["people-data"])

UPLOAD_PREFIX = "arcade-person-up-"             # the uploaded files in DATA_DIR (swept after an hour)
UPLOADS = pd.Uploads(config.DATA_DIR, prefix=UPLOAD_PREFIX)
JOBS = pd.Jobs()                                # big exports — never started here (every export is small)
IMPORT_PATH = "/api/admin/people/data/import"   # streamed, with the backup import's size limit (main.py)
MAX_UPLOAD_BYTES = data.LIMIT_UPLOAD.max_upload


def _fail(e: Exception) -> HTTPException:
    """DataError → its status; Busy (another import or delete is running) → 423."""
    return HTTPException(e.status if isinstance(e, data.DataError) else 423, str(e))


@router.get("/{user_id}/data")
def person_summary(user_id: str, admin: dict = Depends(require_admin)):
    try:
        with db.get_conn() as conn:
            return data.summary(conn, user_id)
    except data.DataError as e:
        raise _fail(e)


@router.post("/{user_id}/data/export")
def person_export(user_id: str, body: dict = Body(default={}), admin: dict = Depends(require_admin)):
    """{options: {}} (this app has none) → the .zip, as a download."""
    if not isinstance(body, dict) or not isinstance(body.get("options", {}), dict):
        raise HTTPException(422, "Send {options}.")
    try:
        content, name, cs = data.export_bytes(user_id, admin)
    except data.DataError as e:
        raise _fail(e)
    data.logger.info("Person data export: %s exported the data of %s (%s)", data.who(admin), user_id,
                     data.counts_text(cs))
    return Response(content, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


@router.get("/data/jobs/{job}")
def export_job(job: str, admin: dict = Depends(require_admin)):
    st = JOBS.status(job, admin["id"])
    if not st:
        raise HTTPException(404, "That export isn't known (or it was more than an hour ago).")
    return st


@router.get("/data/jobs/{job}/file")
def export_job_file(job: str, admin: dict = Depends(require_admin)):
    j = JOBS.get(job, admin["id"])
    if not j or j["state"] != "done" or not j.get("path") or not os.path.exists(j["path"]):
        raise HTTPException(404, "That export isn't ready (or it was more than an hour ago).")
    return FileResponse(j["path"], media_type="application/zip", filename=j["filename"])


def _check_upload(path: str) -> dict:
    with data.read_upload(path) as pkg:
        prepared = data.prepare(pkg)
        with db.get_conn() as conn:
            return data.preview(conn, pkg, prepared)


@router.post("/data/import")
async def import_upload(file: UploadFile = File(...), admin: dict = Depends(require_admin)):
    """The export file → its preview, and a token for the import (the file is kept an hour, for this admin)."""
    UPLOADS.sweep()
    path = await backup_core.receive(file, config.DATA_DIR, suffix=".zip", prefix=UPLOAD_PREFIX,
                                     max_bytes=MAX_UPLOAD_BYTES,
                                     too_big=lambda: HTTPException(413, "That file is too big to be an export file."))
    try:
        preview = await run_in_threadpool(_check_upload, path)
    except data.DataError as e:
        os.remove(path)
        raise _fail(e)
    except BaseException:
        os.remove(path)
        raise
    token = UPLOADS.add(path, admin["id"])
    return {"token": token, "preview": preview}


@router.post("/{user_id}/data/import/{token}")
def import_into(user_id: str, token: str, background: BackgroundTasks, body: dict = Body(default={}),
                admin: dict = Depends(require_admin)):
    item = UPLOADS.get(token, admin["id"])
    if not item:
        raise HTTPException(404, "That upload has expired — choose the file again.")
    try:
        message, res, slugs = data.do_import(user_id, admin, item["path"], body)
    except (data.DataError, pd.Busy) as e:
        raise _fail(e)
    UPLOADS.drop(token)                         # deleted at once after an import
    background.add_task(ha_sensors.after_person_deleted_blocking, slugs)
    return {"message": message, "added": res["added"], "skipped": res["skipped"]}


@router.post("/{user_id}/data/delete")
def person_delete(user_id: str, background: BackgroundTasks, body: dict = Body(default={}),
                  admin: dict = Depends(require_admin)):
    try:
        message, cs, slugs = data.delete(user_id, admin, body)
    except (data.DataError, pd.Busy) as e:
        raise _fail(e)
    background.add_task(ha_sensors.after_person_deleted_blocking, slugs)
    return {"message": message, "counts": cs}
