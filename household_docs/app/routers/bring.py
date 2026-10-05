"""Bringing things in (SPEC §17.5, §17.7, §17.11): a scan from the phone's camera, Google Keep and .zip imports, and a
file's searchable text (a PDF's, or text read by AI)."""
import os

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from .. import db, files, imports, pdftext
from ..auth import require_user
from ..store import paths
from .nodes import node_info_json

router = APIRouter(prefix="/api", tags=["bring"])

SCAN_TYPES = {"pdf": (b"%PDF-", ".pdf"), "jpg": (b"\xff\xd8\xff", ".jpg")}


async def _stream_to(request: Request, fd: int, tmp: str, limit: int) -> int:
    size = 0
    try:
        with os.fdopen(fd, "wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, f"That file is bigger than the {limit // (1024 * 1024)} MB upload limit.")
                await run_in_threadpool(f.write, chunk)
            await run_in_threadpool(lambda: (f.flush(), os.fsync(f.fileno())))
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    return size


def _length(request: Request) -> int | None:
    try:
        return int(request.headers.get("content-length")) if request.headers.get("content-length") else None
    except ValueError:
        return None


# ---------------------------------------------------------------- 📷 Scan (§17.7)
@router.post("/nodes/{ref}/scan", status_code=201)
async def scan(ref: str, request: Request, name: str = Query(min_length=1, max_length=200),
               type: str = Query(default="pdf", pattern="^(pdf|jpg)$"), user: dict = Depends(require_user)):
    """One scanned page (a JPEG) or the whole scan (a PDF made in the browser), saved into a folder the person can
    edit (`ref` as for uploads). The bytes must really be a PDF or a JPEG; the name gets that extension."""
    if len(ref) > 70:
        raise HTTPException(404, "Not found.")
    magic, ext = SCAN_TYPES[type]
    stem = name.strip()
    if stem.lower().endswith(ext) or (type == "jpg" and stem.lower().endswith(".jpeg")):
        stem = stem.rsplit(".", 1)[0]
    try:
        clean = paths.check_name(paths.clean_name(stem, "Scan") + ext)
    except paths.PathError as e:
        raise HTTPException(422, str(e))
    prep = await run_in_threadpool(files.prepare_upload, user, None if ref == "mine" else ref, clean, _length(request))
    fd, tmp = await run_in_threadpool(files.new_temp, prep)
    size = await _stream_to(request, fd, tmp, prep["limit"])
    with open(tmp, "rb") as f:
        head = f.read(8)
    if not head.startswith(magic):
        os.remove(tmp)
        raise HTTPException(415, "That isn't a PDF." if type == "pdf" else "That isn't a JPEG picture.")
    nid = await run_in_threadpool(files.finish_upload, user, prep, tmp, size, "keep")
    if type == "pdf":
        await run_in_threadpool(pdftext.index_one, nid)        # searchable straight away (usually: scanned)
    with db.get_conn() as conn:
        return node_info_json(conn, user, nid)


# ---------------------------------------------------------------- Google Keep and .zip imports (§17.11)
@router.post("/import/{kind}/upload", status_code=201)
async def import_upload(kind: str, request: Request, name: str | None = Query(default=None, max_length=255),
                        user: dict = Depends(require_user)):
    """The .zip (the body is the file): checked, and a preview with a token for the import."""
    if kind not in ("keep", "zip"):
        raise HTTPException(404, "Not found.")
    prep = await run_in_threadpool(imports.prepare, user, kind, _length(request))
    fd, tmp = await run_in_threadpool(imports.new_temp, prep)
    await _stream_to(request, fd, tmp, prep["limit"])
    return await run_in_threadpool(imports.receive, user, kind, tmp, name)


@router.post("/import/{kind}/{token}", status_code=202)
def import_start(kind: str, token: str, body: dict = Body(default={}), user: dict = Depends(require_user)):
    if kind not in ("keep", "zip"):
        raise HTTPException(404, "Not found.")
    parent = body.get("parentId") if isinstance(body, dict) else None
    if parent is not None and (not isinstance(parent, str) or len(parent) > 70):
        raise HTTPException(422, "Choose a folder.")
    return imports.start(user, kind, token[:64], parent if parent != "mine" else None)


@router.get("/import/jobs/{job_id}")
def import_job(job_id: str, user: dict = Depends(require_user)):
    return imports.job(user, job_id[:32])


# ---------------------------------------------------------------- a file's searchable text (§17.5, §17.6)
@router.get("/nodes/{node_id}/text")
def get_text(node_id: str, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        return pdftext.text_info(conn, user, node_id)


@router.put("/nodes/{node_id}/text")
def put_text(node_id: str, body: dict = Body(...), user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        return pdftext.edit_ai_text(conn, user, node_id, body.get("text") if isinstance(body, dict) else None)
