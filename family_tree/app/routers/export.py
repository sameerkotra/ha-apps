"""Export (§13.6): live preview, background jobs, downloads, presets.

The website is the one export format; the print pages (wall chart, family
book) use the same filtered projection. The whole page is the Website export
switch, the print data the Wall chart and family book switch (Features).
"""
import json
import logging
import os
import tempfile
import threading
import time
import zipfile
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import Field

from .. import config, db, features, media
from ..auth import require_user
from ..models import Strict
from ..export_view import ExportOptions, build, included_ids, summary_label
from ..history import Batch
from ..site_export import Site

router = APIRouter(prefix="/api", tags=["export"])
logger = logging.getLogger("export")

FORMATS = ("site",)
JOB_TTL = 3600
_jobs: dict = {}
_lock = threading.Lock()


class ExportIn(Strict):
    format: str = "site"
    options: ExportOptions = Field(default_factory=ExportOptions)


class PresetIn(Strict):
    name: str = Field(min_length=1, max_length=80)
    format: str = "site"
    options: ExportOptions


def _check_format(fmt):
    if fmt not in FORMATS:
        raise HTTPException(422, "Only the website export is available.")


def exports_dir() -> str:
    """Written on the share, never in /data (so it can't end up in an HA backup)."""
    if media.is_online():
        d = os.path.join(media.root(), ".exports")
        os.makedirs(d, exist_ok=True)
        return d
    d = os.path.join(tempfile.gettempdir(), "family_tree_exports")
    os.makedirs(d, exist_ok=True)
    return d


def cleanup() -> None:
    """Forget finished jobs and delete their files after an hour."""
    now = time.time()
    with _lock:
        for jid in [j for j, job in _jobs.items()
                    if now - job.get("finished", job["created"]) > JOB_TTL and job["status"] != "running"]:
            path = _jobs[jid].get("path")
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
            del _jobs[jid]
    share_dir = os.path.join(media.root(), ".exports")
    for d in (share_dir, os.path.join(tempfile.gettempdir(), "family_tree_exports")):
        if d == share_dir and not media.is_online():
            continue                                  # never touch an unmounted share
        try:
            for fn in os.listdir(d):
                fp = os.path.join(d, fn)
                if fn.startswith("export-") and now - os.path.getmtime(fp) > JOB_TTL:
                    os.remove(fp)
        except OSError:
            pass


@router.post("/export/preview", dependencies=[Depends(features.required("export"))])
def preview(body: ExportIn, user: dict = Depends(require_user)):
    _check_format(body.format)
    with db.get_conn() as conn:
        v = build(conn, body.options, user["me_person_id"])
    out = v.stats()
    out.update(included_ids(v))
    out["warnings"] = v.warnings
    out["relativeToName"] = v.relative_to_name
    out["mediaOnline"] = media.is_online()
    return out


def _run(jid: str, user: dict, fmt: str, options: ExportOptions):
    def progress(pct, msg):
        with _lock:
            if jid in _jobs:
                _jobs[jid].update(progress=pct, message=msg)
    try:
        progress(2, "Collecting the family")
        with db.get_conn() as conn:
            v = build(conn, options, user["me_person_id"])
        if (options.photos != "none" or options.documents) and v.media and not media.is_online():
            raise RuntimeError("Photo storage isn't reachable right now. Try again later, or export without photos.")
        stats = v.stats()
        if not stats["people"]:
            raise RuntimeError("Nobody is included with these choices.")
        stamp = config.now().strftime("%Y%m%d")
        path = os.path.join(exports_dir(), f"export-{jid}.zip")
        progress(5, "Writing pages")
        site = Site(v, options, progress)
        site.write(path)
        if options.importData:            # the same filtered data, for another Family Tree to import (§13.6.3)
            from .. import tree_data
            with db.get_conn() as conn:
                data = tree_data.export_data(conn, v, options, site.files, site.title)
            with zipfile.ZipFile(path, "a", zipfile.ZIP_DEFLATED) as z:
                z.writestr(tree_data.DATA_NAME, json.dumps(data, ensure_ascii=False))
        with db.get_conn() as conn:
            Batch(conn, user["id"], summary_label(options, stats, fmt))
        with _lock:
            _jobs[jid].update(status="done", progress=100, message="Ready", path=path, finished=time.time(),
                              filename=f"family-tree-site-{stamp}.zip", size=os.path.getsize(path), stats=stats)
        logger.info("Website export %s done: %s people, %s bytes", jid, stats["people"], os.path.getsize(path))
    except Exception as e:
        if isinstance(e, RuntimeError):
            logger.warning("Export %s stopped: %s", jid, e)
        else:
            logger.exception("Export %s failed", jid)
        with _lock:
            _jobs[jid].update(status="failed", finished=time.time(), error=str(e) if isinstance(e, RuntimeError) else "The export failed — see the app's Log tab.")


@router.post("/export/print", dependencies=[Depends(features.required("printing"))])
def print_data(body: ExportIn, user: dict = Depends(require_user)):
    """The filtered family for the wall chart and family book (§13.5), laid out in the browser."""
    with db.get_conn() as conn:
        v = build(conn, body.options, user["me_person_id"])
    if not v.stats()["people"]:
        raise HTTPException(422, "Nobody is included with these choices.")
    media_out = {mid: {k: m[k] for k in ("id", "kind", "title", "date", "description", "tags", "pages")}
                 for mid, m in v.media.items() if m["kind"] == "photo"}
    return {"people": v.people, "families": v.families, "media": media_out, "warnings": v.warnings,
            "relativeToName": v.relative_to_name, "home": v.home, "today": config.today().isoformat()}


@router.post("/export", status_code=202, dependencies=[Depends(features.required("export"))])
def start_export(body: ExportIn, user: dict = Depends(require_user)):
    _check_format(body.format)
    cleanup()
    with _lock:
        if any(j["user"] == user["id"] and j["status"] == "running" for j in _jobs.values()):
            raise HTTPException(409, "You already have an export running — wait for it to finish.")
        jid = db.new_id()
        _jobs[jid] = {"id": jid, "user": user["id"], "status": "running", "progress": 0, "message": "Starting",
                      "created": time.time(), "format": body.format}
    with db.get_conn() as conn:
        row = conn.execute("SELECT export_last FROM users WHERE id = ?", (user["id"],)).fetchone()
        last = json.loads(row["export_last"]) if row and row["export_last"] else {}
        last[body.format] = body.options.model_dump()
        conn.execute("UPDATE users SET export_last = ? WHERE id = ?", (json.dumps(last), user["id"]))
    threading.Thread(target=_run, args=(jid, user, body.format, body.options), daemon=True, name=f"export-{jid}").start()
    return {"jobId": jid}


def _job_for(jid: str, user: dict) -> dict:
    with _lock:
        job = dict(_jobs.get(jid) or {})
    if not job or (job["user"] != user["id"] and not user["is_admin"]):
        raise HTTPException(404, "That export has expired or doesn't exist.")
    return job


@router.get("/export/last")
def last_options(format: str = "site", user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        row = conn.execute("SELECT export_last FROM users WHERE id = ?", (user["id"],)).fetchone()
    last = json.loads(row["export_last"]) if row and row["export_last"] else {}
    return {"options": last.get(format)}


@router.get("/export/presets", dependencies=[Depends(features.required("export"))])
def list_presets(user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        rows = conn.execute("SELECT p.*, u.name AS by_name FROM export_presets p LEFT JOIN users u ON u.id = p.created_by "
                            "ORDER BY p.name COLLATE NOCASE").fetchall()
    return [{"id": r["id"], "name": r["name"], "format": r["format"], "options": json.loads(r["options"]),
             "createdBy": r["by_name"], "updatedAt": r["updated_at"]} for r in rows]


@router.post("/export/presets", status_code=201, dependencies=[Depends(features.required("export"))])
def save_preset(body: PresetIn, user: dict = Depends(require_user)):
    name = " ".join(body.name.split())
    with db.get_conn() as conn:
        row = conn.execute("SELECT id FROM export_presets WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
        pid = row["id"] if row else db.new_id()
        conn.execute("INSERT INTO export_presets (id, name, format, options, created_by, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
                     "ON CONFLICT(id) DO UPDATE SET options = excluded.options, format = excluded.format, "
                     "updated_at = excluded.updated_at",
                     (pid, name, body.format, json.dumps(body.options.model_dump()), user["id"], config.now_iso()))
    return {"id": pid, "name": name}


@router.delete("/export/presets/{pid}", dependencies=[Depends(features.required("export"))])
def delete_preset(pid: str, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        if not conn.execute("DELETE FROM export_presets WHERE id = ?", (pid,)).rowcount:
            raise HTTPException(404, "That preset doesn't exist.")
    return {"ok": True}


@router.get("/export/{jid}", dependencies=[Depends(features.required("export"))])
def job_status(jid: str, user: dict = Depends(require_user)):
    job = _job_for(jid, user)
    return {k: job.get(k) for k in ("id", "status", "progress", "message", "error", "filename", "size", "stats")}


@router.get("/export/{jid}/file", dependencies=[Depends(features.required("export"))])
def job_file(jid: str, user: dict = Depends(require_user)):
    job = _job_for(jid, user)
    if job["status"] != "done" or not job.get("path") or not os.path.isfile(job["path"]):
        raise HTTPException(404, "That export isn't ready, or it has expired.")
    return FileResponse(job["path"], media_type="application/zip", filename=job["filename"])
