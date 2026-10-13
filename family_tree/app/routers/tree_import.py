"""Admin → Import (§13.6.3, v2.1.0): bring in part of another Family Tree from its website export.

Two steps, so nothing changes until the admin has seen what will happen:
  POST /api/admin/import          upload the zip (or its family-tree.json) → a token and the preview
  POST /api/admin/import/preview  the preview again with some matches undone
  POST /api/admin/import/apply    do it (one history batch), with the deletions chosen to remove
The upload waits in /data/imports for an hour at most, and goes as soon as it's applied or cancelled.
"""
import os
import re
import shutil
import threading
import time

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import Field
from starlette.concurrency import run_in_threadpool

from .. import config, db, tree_data
from ..auth import require_admin
from ..models import Strict

router = APIRouter(prefix="/api", tags=["import"])
TTL = 3600
CHUNK = 1024 * 1024
_TOKEN = re.compile(r"^[0-9a-f]{32}$")
_lock = threading.Lock()          # one import at a time


def _dir() -> str:
    d = os.path.join(config.DATA_DIR, "imports")
    os.makedirs(d, exist_ok=True)
    return d


def cleanup() -> None:
    now = time.time()
    try:
        for fn in os.listdir(_dir()):
            p = os.path.join(_dir(), fn)
            if now - os.path.getmtime(p) > TTL:
                os.remove(p)
    except OSError:
        pass


def _path(token: str) -> str:
    if not _TOKEN.match(token or ""):
        raise HTTPException(404, "That upload has expired — choose the file again.")
    p = os.path.join(_dir(), token)
    if not os.path.isfile(p):
        raise HTTPException(404, "That upload has expired — choose the file again.")
    return p


def _load(token: str):
    try:
        return tree_data.read_package(_path(token))
    except tree_data.ImportError_ as e:
        raise HTTPException(422, str(e))


def _public(plan: dict) -> dict:
    return {k: v for k, v in plan.items() if not k.startswith("_")}


def _plan(conn, data, unmatch):
    try:
        return _public(tree_data.plan(conn, data, unmatch))
    except tree_data.ImportError_ as e:
        raise HTTPException(422, str(e))


@router.post("/admin/import", status_code=201)
async def upload(file: UploadFile = File(...), admin: dict = Depends(require_admin)):
    cleanup()
    token = db.new_id()
    path = os.path.join(_dir(), token)
    size = 0
    with open(path, "wb") as out:
        while chunk := await file.read(CHUNK):
            size += len(chunk)
            if size > tree_data.MAX_ZIP_BYTES:
                out.close()
                os.remove(path)
                raise HTTPException(413, "That file is too large to import.")
            out.write(chunk)
    if not size:
        os.remove(path)
        raise HTTPException(422, "That file is empty.")
    try:
        # reading a large file and planning take a while: in a thread, never on the event loop
        plan = await run_in_threadpool(_read_and_plan, path)
    except tree_data.ImportError_ as e:
        os.remove(path)
        raise HTTPException(422, str(e))
    return dict(_public(plan), token=token, fileName=(file.filename or "")[:200])


def _read_and_plan(path: str) -> dict:
    data, _ = tree_data.read_package(path)
    with db.get_conn() as conn:
        return tree_data.plan(conn, data, set())


class PreviewIn(Strict):
    token: str
    unmatch: list[str] = Field(default_factory=list, max_length=20000)


class ApplyIn(PreviewIn):
    remove: list[str] = Field(default_factory=list, max_length=100000)


@router.post("/admin/import/preview")
def preview(body: PreviewIn, admin: dict = Depends(require_admin)):
    data, _ = _load(body.token)
    with db.get_conn() as conn:
        return dict(_plan(conn, data, set(body.unmatch)), token=body.token)


@router.post("/admin/import/apply")
def apply(body: ApplyIn, admin: dict = Depends(require_admin)):
    if not _lock.acquire(blocking=False):
        raise HTTPException(409, "Another import is running — wait for it to finish.")
    try:
        path = _path(body.token)
        data, zpath = _load(body.token)
        # photos are decoded and stored first, so the write transaction below doesn't wait for them
        prepared = tree_data.prepare_media(data, zpath)
        try:
            with db.get_conn() as conn:
                stats = tree_data.apply(conn, admin["id"], data, zpath, set(body.unmatch), set(body.remove), prepared)
        except tree_data.ImportError_ as e:
            tree_data.discard_prepared(prepared)
            raise HTTPException(422, str(e))
        except BaseException:
            tree_data.discard_prepared(prepared)
            raise
        tree_data.discard_prepared(prepared, keep={r["id"] for r in prepared.values() if r.get("used")})
        try:
            os.remove(path)
        except OSError:
            pass
        return stats
    finally:
        _lock.release()


@router.delete("/admin/import/{token}")
def cancel(token: str, admin: dict = Depends(require_admin)):
    try:
        os.remove(_path(token))
    except HTTPException:
        pass
    return {"ok": True}


@router.get("/admin/import/sources")
def sources(admin: dict = Depends(require_admin)):
    with db.get_conn() as conn:
        rows = conn.execute("SELECT s.*, (SELECT COUNT(*) FROM import_links l WHERE l.source_id = s.id AND l.kind = 'person') AS people "
                            "FROM import_sources s ORDER BY s.last_import_at DESC").fetchall()
        return {"installId": tree_data.install_id(conn),
                "sources": [{"id": r["id"], "title": r["title"], "lastImportAt": r["last_import_at"], "imports": r["imports"],
                             "people": r["people"]} for r in rows]}


@router.delete("/admin/import/sources/{source_id}")
def forget_source(source_id: str, admin: dict = Depends(require_admin)):
    """Forget what was imported from a tree (the people stay). Its next file is treated as a first import."""
    with db.get_conn() as conn:
        conn.execute("DELETE FROM import_links WHERE source_id = ?", (source_id,))
        if not conn.execute("DELETE FROM import_sources WHERE id = ?", (source_id,)).rowcount:
            raise HTTPException(404, "That source isn't known.")
    return {"ok": True}
