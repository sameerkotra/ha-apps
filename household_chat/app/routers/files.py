"""Uploading and serving files (SPEC §4 serving rules, §5.3, §15.4, §15.6).

An upload is the file itself as the request body (streamed, so the size limit
is enforced as it arrives), with its name in the query string. It stays
*pending* until it's sent with a message; unsent uploads are removed after 24 h.
"""
import hashlib
import os
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from starlette.concurrency import run_in_threadpool

from .. import chats, config, db, files, settings
from ..auth import require_user

router = APIRouter(prefix="/api", tags=["files"])


WRITE_BATCH = 1024 * 1024        # the body is written to disk a megabyte at a time, off the event loop


@router.post("/conversations/{cid}/uploads", status_code=201)
async def upload(cid: str, request: Request, name: str = Query(min_length=1, max_length=255),
                 original: bool = False, voice: bool = False, duration: float | None = Query(default=None, ge=0, le=3600),
                 user: dict = Depends(require_user)):
    s = settings.all_values()
    max_bytes = s["max_upload_mb"] * 1024 * 1024
    ext = files.ext_of(files.clean(os.path.basename(name), 120))
    if not voice and ext in settings.blocked_extensions():
        raise HTTPException(422, f"“.{ext}” files can't be shared here.")
    if voice:
        if duration is None or duration <= 0:
            raise HTTPException(422, "A voice message needs its length.")
        if duration > s["voice_max_seconds"] + 1:
            raise HTTPException(422, f"Voice messages can be at most {s['voice_max_seconds'] // 60} min {s['voice_max_seconds'] % 60} s.")
    try:
        length = int(request.headers.get("content-length") or 0)
    except ValueError:
        length = 0
    if length > max_bytes:
        raise HTTPException(413, f"That file is bigger than the {s['max_upload_mb']} MB limit.")
    expected = length or max_bytes          # no Content-Length (chunked): assume the largest it may be

    def prepare():
        with db.get_conn() as conn:
            conv, m = chats.access(conn, cid, user)
            chats.require_post(conn, conv, user["id"])
            files.check_quota(conn, expected)
            files.check_pending(conn, user["id"], expected)
        files.check_free_space(files.share_root(), expected)
        return dict(conv)
    await run_in_threadpool(files.require_online)          # 503 before any of the body is read
    conv = await run_in_threadpool(prepare)
    chats.upload_limit.take(user["id"])
    inc = await run_in_threadpool(files.Incoming, None, conv, name, max_bytes)
    try:
        buf = bytearray()
        async for chunk in request.stream():
            if chunk:
                buf += chunk
                if inc.size + len(buf) > max_bytes:
                    inc.too_big()
                if len(buf) >= WRITE_BATCH:
                    await run_in_threadpool(inc.write, bytes(buf))
                    buf.clear()
        if buf:
            await run_in_threadpool(inc.write, bytes(buf))
        fields = await run_in_threadpool(inc.finish, voice=voice, original=original, max_px=s["photo_max_px"])
    except BaseException:
        await run_in_threadpool(inc.discard)
        raise
    aid = db.new_id()
    committed = []

    def record():
        with files.quota_lock, db.get_conn() as conn:
            now_conv, _ = chats.access(conn, cid, user)        # still a member?
            # the real size, now that it's known (a chunked upload had no Content-Length to check before)
            files.check_quota(conn, fields["size"])
            files.check_pending(conn, user["id"], fields["size"])
            if not fields["rel_path"].startswith(now_conv["folder"] + "/"):
                # the chat was renamed while this was uploading: move the file into the new folder
                rel_dir, full_dir = files.month_dir(now_conv["folder"])
                name_now = files.place(files.resolve(fields["rel_path"]), full_dir, fields["rel_path"].rsplit("/", 1)[1])
                fields["rel_path"] = f"{rel_dir}/{name_now}"
            conn.execute(
                "INSERT INTO attachments (id, message_id, conversation_id, rel_path, original_name, mime, size, original_size, "
                "sha256, width, height, duration, voice, uploaded_by, created_at) VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (aid, cid, fields["rel_path"], fields["original_name"], fields["mime"], fields["size"], fields["original_size"],
                 fields["sha256"], fields["width"], fields["height"], round(duration, 1) if voice else None,
                 1 if voice else 0, user["id"], config.now_iso()))
            row = conn.execute("SELECT * FROM attachments WHERE id = ?", (aid,)).fetchone()
        committed.append(True)
        if files.is_image(row["mime"]):
            files.ensure_thumb(row)
        return chats.attachment_out(row)
    try:
        return await run_in_threadpool(record)
    except BaseException:
        # whatever went wrong before the row was saved (a refusal, a database error): the file isn't left behind
        if committed:
            raise
        try:
            os.remove(files.resolve(fields["rel_path"]))
        except (OSError, HTTPException):
            pass
        raise


@router.delete("/uploads/{aid}")
def cancel_upload(aid: str, user: dict = Depends(require_user)):
    """Remove a pending upload (✕ in the composer)."""
    with db.get_conn() as conn:
        a = conn.execute("SELECT * FROM attachments WHERE id = ? AND uploaded_by = ? AND message_id IS NULL "
                         "AND scheduled_id IS NULL", (aid, user["id"])).fetchone()
        if a is None:
            raise HTTPException(404, "That upload doesn't exist.")
        conn.execute("DELETE FROM attachments WHERE id = ?", (aid,))
    files.remove_now(a["rel_path"])
    files.remove_thumb(aid)
    return {"ok": True}


def _attachment(conn, aid: str, user: dict):
    """The attachment, if it's in a chat you're in and a message you can see (or your own pending upload)."""
    a = conn.execute("SELECT * FROM attachments WHERE id = ?", (aid,)).fetchone()
    if a is None or a["admin_deleted_at"]:
        raise HTTPException(404, "That file doesn't exist.")
    m = chats.member_row(conn, a["conversation_id"], user["id"])
    if m is None:
        raise HTTPException(404, "That file doesn't exist.")
    if a["message_id"] is None:
        if a["uploaded_by"] != user["id"]:
            raise HTTPException(404, "That file doesn't exist.")
    else:
        msg = chats.message_row(conn, a["message_id"])
        if msg is None or msg["deleted_at"] or msg["id"] <= m["joined_message_id"]:
            raise HTTPException(404, "That file doesn't exist.")
    return a


def _disposition(kind: str, name: str) -> str:
    ascii_name = "".join(c if 32 <= ord(c) < 127 and c not in '"\\' else "_" for c in name) or "file"
    return f"{kind}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"


@router.get("/files/{aid}")
def serve(aid: str, request: Request, download: bool = False, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        _attachment(conn, aid, user)          # 404 before telling anyone the folder is offline
    files.require_online()                    # 503 — never mark files missing just because the NAS is off
    with db.get_conn() as conn:
        a = _attachment(conn, aid, user)
        try:
            path = files.resolve(a["rel_path"])
            present = os.path.isfile(path)
        except HTTPException:
            present = False
        if present == bool(a["missing"]):
            conn.execute("UPDATE attachments SET missing = ? WHERE id = ?", (0 if present else 1, aid))
    if not present:
        raise HTTPException(404, "File no longer available.")
    return file_response(path, a["mime"], a["original_name"], download, request)


def _etag(path: str) -> str | None:
    """The same ETag Starlette's FileResponse sends (from the file's time and size)."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return '"' + hashlib.md5(f"{st.st_mtime}-{st.st_size}".encode(), usedforsecurity=False).hexdigest() + '"'


def file_response(path: str, mime: str, name: str, download: bool, request: Request | None = None):
    """The serving rules of SPEC §4: inline only for the allow-list (sandboxed), PDFs inline without
    the sandbox, everything else as a download; always nosniff. The browser asks again every time
    (no-cache) and gets "304 Not Modified" while the file is the same: a file deleted or taken away from
    someone is never shown from the cache."""
    headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-cache"}
    tag = _etag(path)
    if request is not None and tag and tag in [t.strip() for t in (request.headers.get("if-none-match") or "").split(",")]:
        return Response(status_code=304, headers={"ETag": tag, **headers})
    if not download and mime in files.INLINE_SANDBOXED:
        headers["Content-Disposition"] = _disposition("inline", name)
        headers["Content-Security-Policy"] = "sandbox; default-src 'none'; img-src 'self'; media-src 'self'; style-src 'unsafe-inline'"
        media = mime + ("; charset=utf-8" if mime == "text/plain" else "")
    elif not download and mime == files.INLINE_PDF:
        headers["Content-Disposition"] = _disposition("inline", name)
        media = mime
    else:
        headers["Content-Disposition"] = _disposition("attachment", name)
        headers["Content-Security-Policy"] = "sandbox; default-src 'none'"
        media = "application/octet-stream" if mime in ("text/html", "image/svg+xml") else mime
    return FileResponse(path, media_type=media, headers=headers)


@router.get("/files/{aid}/thumb")
def thumb(aid: str, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        a = _attachment(conn, aid, user)
    p = files.ensure_thumb(a)
    if not p:
        raise HTTPException(404, "No preview.")
    return FileResponse(p, media_type="image/jpeg", headers={
        "Cache-Control": "private, max-age=86400", "Content-Security-Policy": "sandbox; default-src 'none'"})


@router.post("/files/{aid}/heard")
def heard(aid: str, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        a = _attachment(conn, aid, user)
        if a["voice"]:
            conn.execute("INSERT OR IGNORE INTO heard (attachment_id, user_id, at) VALUES (?, ?, ?)", (aid, user["id"], config.now_iso()))
    return {"ok": True}
