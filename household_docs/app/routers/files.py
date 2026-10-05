"""Files (SPEC §9.2, §12): uploads into folders you can edit, image previews, .zip downloads, and the admin shared
folders you can use. Every item is checked for the caller first; anything they can't open is a 404."""
import os

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from starlette.concurrency import run_in_threadpool

from .. import db, files, share_folders
from ..auth import require_user
from .nodes import node_info_json

router = APIRouter(prefix="/api", tags=["files"])
MAX_ZIP_ITEMS = 200


@router.get("/shared-folders")
def shared_folders(user: dict = Depends(require_user)):
    """The admin shared folders you can use: {rootId, label, mode ro|rw, exists}. A folder whose storage is away
    is left out (admins see it, marked, so they know)."""
    with db.get_conn() as conn:
        return {"folders": share_folders.for_user(conn, user, include_missing=user["is_admin"])}


@router.post("/nodes/{ref}/upload", status_code=201)
async def upload(ref: str, request: Request, name: str = Query(min_length=1, max_length=255),
                 onClash: str = Query(default="keep", pattern="^(keep|replace)$"), user: dict = Depends(require_user)):
    """One file; the request body is the file itself. `ref`: a folder's id, "root:<id>" (an admin shared folder's
    top) or "mine" (your My docs top). onClash=replace replaces a file of the same name (its previous copy is
    kept), keep (the default) adds " (2)"."""
    if len(ref) > 70:
        raise HTTPException(404, "Not found.")
    try:
        length = int(request.headers.get("content-length")) if request.headers.get("content-length") else None
    except ValueError:
        length = None
    prep = await run_in_threadpool(files.prepare_upload, user, None if ref == "mine" else ref, name, length)
    fd, tmp = await run_in_threadpool(files.new_temp, prep)
    size = 0
    try:
        with os.fdopen(fd, "wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > prep["limit"]:
                    raise HTTPException(413, f"That file is bigger than the {prep['limit'] // (1024 * 1024)} MB upload limit.")
                await run_in_threadpool(f.write, chunk)
            await run_in_threadpool(_sync, f)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    nid = await run_in_threadpool(files.finish_upload, user, prep, tmp, size, onClash)
    with db.get_conn() as conn:
        return node_info_json(conn, user, nid)


def _sync(f) -> None:
    f.flush()
    os.fsync(f.fileno())


@router.get("/nodes/{node_id}/preview")
def preview(node_id: str, user: dict = Depends(require_user)):
    """PNG, JPEG, GIF or WebP only — recognised by their first bytes, sent from the bytes that were checked."""
    with db.get_conn() as conn:
        data, mime = files.preview(conn, user, node_id)
    return Response(data, media_type=mime, headers={
        "Content-Disposition": "inline", "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "sandbox; default-src 'none'", "Cache-Control": "private, no-cache"})


def _zip_response(user: dict, refs: list[str]):
    with db.get_conn() as conn:
        entries, name = files.zip_plan(conn, user, refs)
    return StreamingResponse(files.stream_zip(entries), media_type="application/zip",
                             headers=files.download_headers(name))


@router.get("/nodes/{ref}/zip")
def zip_one(ref: str, user: dict = Depends(require_user)):
    """A folder (or "root:<id>", a whole admin shared folder; "mine", your My docs) as a .zip."""
    if len(ref) > 70:
        raise HTTPException(404, "Not found.")
    return _zip_response(user, ["" if ref == "mine" else ref])


@router.get("/zip")
def zip_many(ids: str = Query(min_length=1, max_length=MAX_ZIP_ITEMS * 71), user: dict = Depends(require_user)):
    """Several items (comma-separated ids) in one .zip."""
    refs = [i for i in dict.fromkeys(x.strip() for x in ids.split(",")) if i]
    if not refs or len(refs) > MAX_ZIP_ITEMS:
        raise HTTPException(422, f"Choose 1 to {MAX_ZIP_ITEMS} items.")
    if any(len(r) > 70 or r == "mine" for r in refs):
        raise HTTPException(404, "Not found.")
    return _zip_response(user, refs)
