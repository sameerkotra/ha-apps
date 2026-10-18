"""Admin → People → ⋯ Data (PERSON_DATA_SPEC.md §2.6, §7; SPEC §18): one person's data exported, imported or deleted.
The paths are the shared dialogs' (app/static/common/person-data.js). Admins only — the real signed-in admin
(require_admin reads the ingress identity; there is no "acting as" here) — and the app's cross-site guard applies
as everywhere (main.py). The work is in people_data.py."""
import os
import re

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.concurrency import run_in_threadpool

from .. import config, files, people_data
from ..auth import require_admin
from ..common import person_data as pd

router = APIRouter(prefix="/api/admin/people", tags=["admin"])
IMPORT_PATH = "/api/admin/people/data/import"          # streamed (main.py's STREAMED): it counts its own bytes
HEADER_MAX = 16 * 1024


@router.get("/{uid}/data")
def data_summary(uid: str, admin: dict = Depends(require_admin)):
    from .. import db
    with db.get_conn() as conn:
        return people_data.summary(conn, uid[:128])


@router.post("/{uid}/data/export")
def export(uid: str, body: dict = Body(default={}), admin: dict = Depends(require_admin)):
    out = people_data.export(admin, uid[:128], body)
    if "job" in out:
        return JSONResponse({"job": out["job"], "filename": out["filename"]}, headers={"Cache-Control": "no-store"})
    return Response(out["bytes"], media_type="application/zip", headers=files.download_headers(out["filename"]))


@router.get("/data/jobs/{job}")
def job_status(job: str, admin: dict = Depends(require_admin)):
    st = people_data.JOBS.status(job[:64], admin["id"])
    if st is None:
        raise HTTPException(404, "That export isn't here any more (it's kept for an hour).")
    return st


@router.get("/data/jobs/{job}/file")
def job_file(job: str, admin: dict = Depends(require_admin)):
    j = people_data.JOBS.get(job[:64], admin["id"])
    if j is None or j["state"] != "done" or not j.get("path") or not os.path.exists(j["path"]):
        raise HTTPException(404, "That export isn't here any more (it's kept for an hour).")
    headers = dict(files.download_headers(j["filename"]), **{"Content-Length": str(os.path.getsize(j["path"]))})
    return StreamingResponse(pd.stream(j["path"]), media_type="application/zip", headers=headers)


async def receive_file(request: Request, dest_dir: str, max_bytes: int, field: str = "file") -> str:
    """The `file` part of a multipart/form-data body, streamed into a new temp file in `dest_dir` (the app has no
    multipart library: its uploads are raw bodies; the shared dialog sends a form). Past `max_bytes` → 413; no such
    part → 422. Returns the temp file's path (the caller keeps or deletes it)."""
    ctype = request.headers.get("content-type", "")
    m = re.search(r'boundary="?([^";]+)"?', ctype)
    if not ctype.lower().startswith("multipart/form-data") or not m or len(m.group(1)) > 200:
        raise HTTPException(422, "Send the export file as a form upload (the field “file”).")
    delim = b"\r\n--" + m.group(1).encode("latin-1")
    path = pd.temp_path(dest_dir, prefix=people_data.UPLOAD_PREFIX)
    out = open(path, "wb")
    buf = b"\r\n"                 # so the first boundary looks like every other one
    state, writing, found, size = "seek", False, False, 0
    try:
        async for chunk in request.stream():
            buf += chunk
            while True:
                if state == "seek":                 # looking for the next boundary
                    i = buf.find(delim)
                    if i < 0:
                        buf = buf[-len(delim):]
                        break
                    buf = buf[i + len(delim):]
                    state = "after"
                if state == "after":                # "--" ends the body, CRLF starts a part
                    if len(buf) < 2:
                        break
                    if buf[:2] == b"--":
                        state = "end"
                        break
                    if buf[:2] != b"\r\n":
                        raise HTTPException(422, "That upload couldn't be read.")
                    buf = buf[2:]
                    state = "headers"
                if state == "headers":
                    i = buf.find(b"\r\n\r\n")
                    if i < 0:
                        if len(buf) > HEADER_MAX:
                            raise HTTPException(422, "That upload couldn't be read.")
                        break
                    head = buf[:i].decode("latin-1")
                    buf = buf[i + 4:]
                    name = re.search(r'(?im)^content-disposition:.*?\bname="([^"]*)"', head)
                    writing = bool(name and name.group(1) == field and not found)
                    state = "data"
                if state == "data":
                    i = buf.find(delim)
                    part, rest = (buf, b"") if i < 0 else (buf[:i], buf[i:])
                    if i < 0:                       # keep what could be the start of the boundary
                        keep = len(delim) - 1
                        part, rest = (buf[:-keep], buf[-keep:]) if len(buf) > keep else (b"", buf)
                    if writing and part:
                        size += len(part)
                        if size > max_bytes:
                            raise HTTPException(413, "That file is too big.")
                        await run_in_threadpool(out.write, part)
                    buf = rest
                    if i < 0:
                        break
                    if writing:
                        found = True
                    writing = False
                    state = "seek"
                if state == "end":
                    break
        out.close()
        if not found:
            raise HTTPException(422, "Choose an export file (.zip).")
        return path
    except BaseException:
        out.close()
        if os.path.exists(path):
            os.remove(path)
        raise


@router.post("/data/import")
async def import_upload(request: Request, admin: dict = Depends(require_admin)):
    people_data.uploads().sweep()
    tmp = await receive_file(request, config.DATA_DIR, people_data.UPLOAD_MAX)
    return await run_in_threadpool(people_data.preview, admin, tmp)


@router.post("/{uid}/data/import/{token}")
def import_run(uid: str, token: str, body: dict = Body(default={}), admin: dict = Depends(require_admin)):
    return people_data.run_import(admin, uid[:128], token[:64], body)


@router.post("/{uid}/data/delete")
def delete(uid: str, body: dict = Body(default={}), admin: dict = Depends(require_admin)):
    return people_data.delete(admin, uid[:128], body)
