"""Admin → Users → Data: export, import and delete one person's data (PERSON_DATA_SPEC.md §2.6; SPEC.md section
20a). The JSON API the shared dialogs (static/common/person-data.js) call; the rules are app/people_data.py.

Admin only — the real signed-in admin (require_admin reads the Home Assistant identity, never ?as_user=) — and
behind the app's ingress-only and cross-site guards like every route. One import or delete at a time
(person_data.one_at_a_time → 423), each in one database transaction with the files moved after the commit.
"""
import io
import logging
import os
import shutil
import tempfile

from fastapi import APIRouter, Body, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response

from .. import auth, people_data as pdata, storage
from ..auth import User, require_admin
from ..common import backup_core, person_data
from ..db import DB_PATH, GATE, get_db
from ..matching import relink
from ..purge import PERSON_PREFIX
from ..routes.deletion import _anything_processing
from ..toll_trips import rebuild_trips
from ..version import APP_VERSION

logger = logging.getLogger(__name__)
router = APIRouter()

# The largest file an import accepts: the whole-app backup's own upload size (main.py's BodyLimit has this path).
MAX_IMPORT_BYTES = 2 * 1024 * 1024 * 1024
EXPORT_PREFIX = PERSON_PREFIX               # finished big exports (Jobs), next to the database, kept an hour
UPLOAD_PREFIX = PERSON_PREFIX + "up-"       # uploaded files between the preview and the import
STAGING_PREFIX = "finance-person-files-"    # an import's PDFs, before they are moved in after the commit
LIMITS = person_data.Limits(max_upload=MAX_IMPORT_BYTES)
WAIT_SECONDS = 60                           # for a backup restore that is swapping the database file


def data_dir() -> str:
    return os.path.dirname(DB_PATH) or "."


JOBS = person_data.Jobs()
UPLOADS = person_data.Uploads(data_dir(), prefix=UPLOAD_PREFIX)


def _person(conn, uid: str) -> dict:
    row = conn.execute("SELECT id, name FROM known_users WHERE id = ?", (uid,)).fetchone()
    if row is None:
        raise HTTPException(404, "That person isn't in the Users list.")
    return {"id": row["id"], "name": row["name"]}


def _busy(e: Exception):
    raise HTTPException(423, str(e)) from None


def _refuse_while_reading(conn, uid: str, name: str) -> None:
    """As Wipe all data: a statement being read would save its rows anyway, after the delete or import."""
    if _anything_processing(conn, uid):
        raise HTTPException(400, f"An upload of {name}'s is being read: wait until it is done (or failed), then "
                                 "try again.")


@router.get("/api/admin/people/{uid}/data")
def person_data_summary(uid: str, admin: User = Depends(require_admin)):
    with get_db() as conn:
        person = _person(conn, uid)
        return pdata.summary(conn, uid, person["name"])


# ------------------------------------------------------------------ export

def _write_export(uid: str, person: dict, options: dict, admin: User, dest, progress=None) -> dict:
    with get_db() as conn:
        tables, files = pdata.collect(conn, uid, options, when=_now_db(conn))
    return person_data.write_package(
        dest, app=pdata.APP, app_version=APP_VERSION, schema=pdata.SCHEMA, person=person, exported_by=admin.name,
        tables=tables, files=files, options=options,
        title="Finance Dashboard", what="bank statements, transactions and bills", progress=progress)


def _now_db(conn) -> str:
    return conn.execute("SELECT datetime('now')").fetchone()[0]


def _log_export(admin: User, person: dict, manifest: dict) -> None:
    logger.info("Person data: %s exported %s (%s): %s", admin.name, person["name"], person["id"],
                pdata.counts_text(manifest.get("counts") or {}))


@router.post("/api/admin/people/{uid}/data/export")
def person_data_export(uid: str, payload: dict = Body(default={}), admin: User = Depends(require_admin)):
    """Small (no PDFs going in): the .zip at once. With PDFs: a background job (person-data.js polls it)."""
    options = pdata.options_from((payload or {}).get("options"))
    with get_db() as conn:
        person = _person(conn, uid)
        has_files = options["pdfs"] and bool(pdata._pdf_files(conn, uid))
    filename = person_data.file_name(pdata.APP_LABEL, person["name"])
    if not has_files:
        buf = io.BytesIO()
        manifest = _write_export(uid, person, options, admin, buf)
        _log_export(admin, person, manifest)
        return Response(buf.getvalue(), media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    def run(progress):
        path = person_data.temp_path(data_dir(), prefix=EXPORT_PREFIX)
        try:
            manifest = _write_export(uid, person, options, admin, path, progress)
        except BaseException:
            os.remove(path)
            raise
        _log_export(admin, person, manifest)
        return path

    return {"job": JOBS.start(run, by=admin.id, filename=filename)}


@router.get("/api/admin/people/data/jobs/{job}")
def person_data_job(job: str, admin: User = Depends(require_admin)):
    JOBS.sweep()
    status = JOBS.status(job, admin.id)
    if status is None:
        raise HTTPException(404, "That export isn't here any more (they are kept an hour).")
    return status


@router.get("/api/admin/people/data/jobs/{job}/file")
def person_data_job_file(job: str, admin: User = Depends(require_admin)):
    found = JOBS.get(job, admin.id)
    if not found or found["state"] != "done" or not found.get("path") or not os.path.exists(found["path"]):
        raise HTTPException(404, "That export isn't here any more (they are kept an hour).")
    return FileResponse(found["path"], media_type="application/zip", filename=found["filename"])


# ------------------------------------------------------------------ import

def _read(path: str):
    try:
        return person_data.read_package(path, app=pdata.APP, schema=pdata.SCHEMA, limits=LIMITS,
                                        tables=list(pdata.BY_NAME))
    except person_data.PackageError as e:
        raise HTTPException(400, str(e)) from None


def _labelled(counts: dict, files: int) -> list:
    out = [{"key": t.name, "label": t.label, "count": counts.get(t.name, 0)} for t in pdata.TABLES
           if counts.get(t.name)]
    if files:
        out.append({"key": "files", "label": "PDFs", "count": files})
    return out


@router.post("/api/admin/people/data/import")
def person_data_upload(file: UploadFile = File(...), admin: User = Depends(require_admin)):
    """The file is kept (an hour, for this admin) between the preview and the import."""
    path = backup_core.receive_sync(file.file, data_dir(), suffix=".zip", prefix=UPLOAD_PREFIX)
    try:
        with _read(path) as pkg, get_db() as conn:
            try:
                checked = pdata.check_package(conn, pkg)
            except pdata.Refused as e:
                raise HTTPException(400, str(e)) from None
            source = pkg.person
            here = conn.execute("SELECT 1 FROM known_users WHERE id = ?", (str(source.get("id") or ""),)).fetchone()
            warnings = list(checked["warnings"])
            if not here:
                warnings.append("The person it came from isn't in this app's Users list: pick who it goes to.")
            preview = {
                "app": "Finance Dashboard", "appVersion": str(pkg.manifest.get("appVersion") or ""),
                "exportedAt": pkg.manifest.get("exportedAt"), "exportedBy": pkg.manifest.get("exportedBy") or "",
                "person": {"id": source.get("id"), "name": source.get("name") or ""},
                "counts": _labelled(checked["counts"], checked["files"]), "warnings": warnings, "options": [],
                "target": source.get("id") if here else None,
            }
    except BaseException:
        os.remove(path)
        raise
    token = UPLOADS.add(path, by=admin.id, info={"name": file.filename or ""})
    return {"token": token, "preview": preview}


@router.post("/api/admin/people/{uid}/data/import/{token}")
def person_data_import(uid: str, token: str, payload: dict = Body(default={}), admin: User = Depends(require_admin)):
    payload = payload or {}
    mode = payload.get("mode") or "add"
    if mode not in ("add", "replace"):
        raise HTTPException(422, "Pick Add to what they have or Replace what they have.")
    item = UPLOADS.get(token, admin.id)
    if item is None:
        raise HTTPException(404, "The uploaded file isn't here any more (it is kept an hour): choose it again.")
    try:
        with person_data.one_at_a_time(), GATE.work(timeout=WAIT_SECONDS):
            result = _import(uid, item["path"], mode, payload.get("confirm"), admin)
    except person_data.Busy as e:
        _busy(e)
    except TimeoutError:
        raise HTTPException(423, "A backup is being restored — try again in a minute.") from None
    UPLOADS.drop(token)
    return result


def _import(uid: str, path: str, mode: str, confirm, admin: User) -> dict:
    staging = tempfile.mkdtemp(prefix=STAGING_PREFIX, dir=data_dir())
    try:
        with _read(path) as pkg:
            conn = get_db()
            try:
                person = _person(conn, uid)
                if mode == "replace" and not person_data.names_match(confirm, person["name"]):
                    raise HTTPException(409, f"To replace, type {person['name']} exactly.")
                _refuse_while_reading(conn, uid, person["name"])
                try:
                    pdata.check_package(conn, pkg)
                    conn.execute("BEGIN IMMEDIATE")
                    removed = pdata.delete_rows(conn, uid, forget=False) if mode == "replace" else {}
                    added, skipped, moves = pdata.import_package(conn, pkg, uid, staging)
                    relink(conn, uid)                  # pair the transfers the file's rows make with theirs
                except pdata.Refused as e:
                    conn.rollback()
                    raise HTTPException(400, str(e)) from None
                except BaseException:
                    conn.rollback()
                    raise
                conn.commit()
                # After the commit: their old files (Replace), then the new ones moved in. A failure here leaves
                # the database complete and only a file to tidy (Admin → Storage lists missing PDFs).
                if mode == "replace":
                    pdata.remove_folder(uid)
                for tmp, final in moves:
                    try:
                        os.makedirs(os.path.dirname(final), exist_ok=True)
                        os.replace(tmp, final)
                    except OSError:
                        logger.warning("Person data import: couldn't move a PDF into place (%s)", final)
                rebuild_trips(conn, uid)               # the trips cache (commits)
            finally:
                conn.close()
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    logger.info("Person data: %s imported into %s (%s), %s: %s; %d already there skipped%s", admin.name,
                person["name"], uid, mode, pdata.counts_text(added), skipped,
                f"; replaced {pdata.counts_text(removed)}" if mode == "replace" else "")
    message = f"Imported {pdata.counts_text(added)}"
    if skipped:
        message += f"; {skipped} already there {'was' if skipped == 1 else 'were'} skipped"
    return {"message": message + ".", "added": added, "skipped": skipped}


# ------------------------------------------------------------------ delete

@router.post("/api/admin/people/{uid}/data/delete")
def person_data_delete(uid: str, payload: dict = Body(default={}), admin: User = Depends(require_admin)):
    payload = payload or {}
    forget = bool(payload.get("forget"))
    try:
        with person_data.one_at_a_time(), GATE.work(timeout=WAIT_SECONDS):
            with get_db() as conn:
                person = _person(conn, uid)
                if not person_data.names_match(payload.get("confirm"), person["name"]):
                    raise HTTPException(409, f"To delete, type {person['name']} exactly.")
                _refuse_while_reading(conn, uid, person["name"])
                conn.execute("BEGIN IMMEDIATE")
                counts = pdata.delete_rows(conn, uid, forget)
            # committed (the with block): now their files
            pdata.remove_folder(uid)
    except person_data.Busy as e:
        _busy(e)
    except TimeoutError:
        raise HTTPException(423, "A backup is being restored — try again in a minute.") from None
    if forget:
        auth._known_user_seen.pop(uid, None)     # so they are added again, at once, if they open the app
    logger.info("Person data: %s deleted %s (%s)%s: %s", admin.name, person["name"], uid,
                " and forgot them" if forget else "", pdata.counts_text(counts))
    message = f"Deleted {pdata.counts_text(counts)}"
    if counts.get("user_access"):
        message += f", {counts['user_access']} shared-access grants"
    return {"message": message + (f"; {person['name']} is no longer listed." if forget else "."), "counts": counts}
