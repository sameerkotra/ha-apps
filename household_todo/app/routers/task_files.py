"""Files on tasks (SPEC §17): list, attach, open, rename, Keep after done, remove. Whoever can see the task can
do all of it; Maintenance doesn't need to be on, only the files folder (App settings → Files folder)."""
import os

from fastapi import APIRouter, BackgroundTasks, Body, Depends, File, HTTPException, Query, Response, UploadFile
from fastapi.responses import FileResponse

from .. import db, maint_files, task_files, taskview
from ..auth import get_acting_user

router = APIRouter(prefix="/api", tags=["task files"])


def _task(conn, task_id: str, acting_id: str) -> tuple[dict, dict]:
    return taskview.load_task(conn, task_id, acting_id)


@router.get("/tasks/{task_id}/files")
def list_files(task_id: str, acting: dict = Depends(get_acting_user)):
    with db.get_conn() as conn:
        task, _ = _task(conn, task_id, acting["id"])
        files = [task_files.file_json(r) for r in task_files.listed(conn, task_id)]
    return {"taskId": task_id, "title": task["title"], "completed": bool(task["completed"]), "files": files,
            "maxFiles": task_files.MAX_FILES, "folder": maint_files.public_status()}


@router.post("/tasks/{task_id}/files", status_code=201)
def attach(task_id: str, file: UploadFile = File(...), acting: dict = Depends(get_acting_user)):
    with db.get_conn() as conn:
        task, lst = _task(conn, task_id, acting["id"])
        if task["completed"]:
            raise HTTPException(409, "This task is done. Untick it to attach files.")
        if len(task_files.listed(conn, task_id)) >= task_files.MAX_FILES:
            raise HTTPException(422, f"A task can have at most {task_files.MAX_FILES} files.")
    root = maint_files.require_online()          # never holding a connection
    with db.get_conn() as conn:
        rel_dir = task_files.folder_for(conn, task, lst.get("role"))
    keep = lst.get("role") == "maintenance"       # a job's receipts and photos stay, as before
    row = maint_files.save_upload(root, rel_dir, file.filename, file.file.read, acting["id"], {"task_id": task_id},
                                  keep=keep)
    return task_files.file_json(row)


@router.get("/tasks/{task_id}/files/{file_id}")
def download(task_id: str, file_id: str, thumb: int = Query(default=0), download: int = Query(default=0),
             acting: dict = Depends(get_acting_user)):
    with db.get_conn() as conn:
        _task(conn, task_id, acting["id"])
        f = task_files.load(conn, task_id, file_id)
    root = maint_files.require_online()
    use_thumb = bool(thumb and f["thumb"])
    path = maint_files._within(root, f["thumb"] if use_thumb else f["rel_path"])
    if not os.path.isfile(path):
        raise HTTPException(404, "The file is no longer in the files folder.")
    mime = "image/jpeg" if use_thumb else (f["mime"] or "application/octet-stream")
    inline = mime in maint_files.INLINE and not download
    return FileResponse(path, media_type=mime, filename=f["name"], content_disposition_type="inline" if inline else "attachment",
                        headers={"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox",
                                 "Cache-Control": "private, max-age=3600"})


@router.patch("/tasks/{task_id}/files/{file_id}")
def change(task_id: str, file_id: str, background: BackgroundTasks, body: dict = Body(...),
           acting: dict = Depends(get_acting_user)):
    """`name`: rename it (in the folder too; the extension stays). `keepAfterDone`: whether it stays when the task
    is ticked off — turned off on a task that's already done, the file goes now."""
    unknown = set(body) - {"name", "keepAfterDone"}
    if unknown or not body:
        raise HTTPException(422, "Send name and/or keepAfterDone.")
    keep = body.get("keepAfterDone")
    if "keepAfterDone" in body and not isinstance(keep, bool):
        raise HTTPException(422, "keepAfterDone must be true or false.")
    name = body.get("name")
    if "name" in body and (not isinstance(name, str) or not name.strip() or len(name) > 200):
        raise HTTPException(422, "The name must be 1–200 characters.")
    with db.get_conn() as conn:
        task, _ = _task(conn, task_id, acting["id"])
        f = task_files.load(conn, task_id, file_id)
    if name is not None and f["removed_at"]:
        raise HTTPException(409, "This file is still being put back. Try again in a moment.")
    if name is not None:
        root = maint_files.require_online()
        new = maint_files.clean(name.strip())
        ext = f["name"].rpartition(".")[2] if "." in f["name"] else ""
        if ext and not new.lower().endswith("." + ext.lower()):
            new = maint_files.clean(f"{new}.{ext}")
        if new != f["name"]:
            src = maint_files._within(root, f["rel_path"])
            if not os.path.isfile(src):
                raise HTTPException(404, "The file is no longer in the files folder.")
            d = os.path.dirname(src)
            new = maint_files._unique(d, new)
            os.rename(src, os.path.join(d, new))
            with db.get_conn() as conn:
                conn.execute("UPDATE maint_files SET name = ?, rel_path = ?, mime = ? WHERE id = ?",
                             (new, os.path.join(os.path.dirname(f["rel_path"]), new), maint_files.mime_of(new), file_id))
    if keep is not None:
        with db.get_conn() as conn:
            conn.execute("UPDATE maint_files SET keep_after_done = ? WHERE id = ?", (int(keep), file_id))
            if not keep and task["completed"]:
                task_files.on_done(conn, [task_id])
        if not keep and task["completed"]:
            task_files.flush_soon(background)
    with db.get_conn() as conn:
        r = conn.execute("SELECT * FROM maint_files WHERE id = ?", (file_id,)).fetchone()
    return task_files.file_json(r)


@router.delete("/tasks/{task_id}/files/{file_id}", status_code=204)
def remove(task_id: str, file_id: str, acting: dict = Depends(get_acting_user)):
    """Takes it off the task: the file moves to _deleted in the folder (emptied after 30 days)."""
    with db.get_conn() as conn:
        _task(conn, task_id, acting["id"])
        f = task_files.load(conn, task_id, file_id)
    root = maint_files.require_online()
    with db.get_conn() as conn:
        conn.execute("DELETE FROM maint_files WHERE id = ?", (file_id,))
    if not f["removed_at"]:                      # one being put back is in _deleted already
        maint_files.to_deleted(root, [f["rel_path"]])
    maint_files.remove_thumbs(root, [f["thumb"]])
    return Response(status_code=204)
