"""For members: drafts (§16.8), send later (§16.7), downloading a chat (§15.9)
and shared folders (§12)."""
import json
import os
import re
import secrets

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import Field
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from .. import chats, config, db, exporter, files, scheduled, settings, shared_folders
from ..auth import require_user
from .common import Strict
from .files import file_response

router = APIRouter(prefix="/api", tags=["extras"])


# ---------- drafts ----------
class DraftIn(Strict):
    body: str = Field(max_length=8000)


@router.put("/conversations/{cid}/draft")
def put_draft(cid: str, body: DraftIn, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        chats.access(conn, cid, user)
        if body.body.strip():
            conn.execute("INSERT INTO drafts (user_id, conversation_id, body, updated_at) VALUES (?, ?, ?, ?) "
                         "ON CONFLICT(user_id, conversation_id) DO UPDATE SET body = excluded.body, updated_at = excluded.updated_at",
                         (user["id"], cid, body.body, config.now_iso()))
        else:
            conn.execute("DELETE FROM drafts WHERE user_id = ? AND conversation_id = ?", (user["id"], cid))
    return {"ok": True}


@router.delete("/conversations/{cid}/draft")
def delete_draft(cid: str, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        chats.access(conn, cid, user)
        conn.execute("DELETE FROM drafts WHERE user_id = ? AND conversation_id = ?", (user["id"], cid))
    return {"ok": True}


# ---------- send later ----------
class PollPayload(Strict):
    question: str = Field(min_length=1, max_length=200)
    options: list[str] = Field(min_length=2, max_length=10)
    multi: bool = False
    closesAt: str | None = Field(default=None, max_length=40)


class ScheduleIn(Strict):
    sendAt: str = Field(max_length=40)
    body: str = Field(default="", max_length=8000)
    replyTo: int | None = None
    attachmentIds: list[str] = Field(default_factory=list, max_length=10)
    mentions: list[str] = Field(default_factory=list, max_length=50)
    expiresIn: int | None = None
    announcement: bool = False
    poll: PollPayload | None = None


@router.post("/conversations/{cid}/scheduled", status_code=201)
def schedule(cid: str, body: ScheduleIn, user: dict = Depends(require_user)):
    send_at = scheduled.check_time(body.sendAt)
    with db.get_conn() as conn:
        conv, m = chats.access(conn, cid, user)
        chats.not_personal(conv)
        chats.require_post(conn, conv, user["id"])
        if conn.execute("SELECT COUNT(*) FROM scheduled WHERE user_id = ?", (user["id"],)).fetchone()[0] >= scheduled.MAX_PER_PERSON:
            raise HTTPException(409, f"You have {scheduled.MAX_PER_PERSON} messages waiting to be sent — delete some first.")
        if body.announcement and not chats.can_announce(conn, conv, m, user):
            raise HTTPException(403, "You can't post announcements here.")
        chats.resolve_expiry(conv, user, body.expiresIn)          # checks the choice now; the timer starts when it's sent
        if body.poll:
            chats.check_poll(body.poll.question, body.poll.options, body.poll.closesAt)
            if body.poll.closesAt and config.parse_iso(body.poll.closesAt) <= config.parse_iso(send_at):
                raise HTTPException(422, "The poll would close before it's sent — choose a later closing time.")
        elif not body.body.strip() and not body.attachmentIds:
            raise HTTPException(422, "Type a message or add a file.")
        sid = db.new_id()
        for aid in dict.fromkeys(body.attachmentIds):
            a = conn.execute("SELECT * FROM attachments WHERE id = ?", (aid,)).fetchone()
            if (a is None or a["message_id"] is not None or a["scheduled_id"] or a["conversation_id"] != cid
                    or a["uploaded_by"] != user["id"]):
                raise HTTPException(422, "A file wasn't uploaded to this chat (or was already sent). Add it again.")
            conn.execute("UPDATE attachments SET scheduled_id = ? WHERE id = ?", (sid, aid))
        payload = {"body": body.body, "replyTo": body.replyTo, "mentions": body.mentions, "expiresIn": body.expiresIn,
                   "announcement": body.announcement, "poll": body.poll.model_dump() if body.poll else None}
        conn.execute("INSERT INTO scheduled (id, user_id, conversation_id, payload, send_at, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                     (sid, user["id"], cid, json.dumps(payload), send_at, config.now_iso()))
        conn.execute("DELETE FROM drafts WHERE user_id = ? AND conversation_id = ?", (user["id"], cid))
        return scheduled.item_out(conn, conn.execute("SELECT * FROM scheduled WHERE id = ?", (sid,)).fetchone(), user["id"])


@router.get("/me/scheduled")
def my_scheduled(conversation: str | None = None, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        q = "SELECT * FROM scheduled WHERE user_id = ?" + (" AND conversation_id = ?" if conversation else "") + " ORDER BY send_at"
        rows = conn.execute(q, (user["id"], conversation) if conversation else (user["id"],)).fetchall()
        return {"scheduled": [scheduled.item_out(conn, r, user["id"]) for r in rows]}


def _mine(conn, sid: str, user: dict):
    r = conn.execute("SELECT * FROM scheduled WHERE id = ? AND user_id = ?", (sid, user["id"])).fetchone()
    if r is None:
        raise HTTPException(404, "That scheduled message doesn't exist (it may have been sent).")
    return r


class ScheduleEdit(Strict):
    body: str | None = Field(default=None, max_length=8000)
    sendAt: str | None = Field(default=None, max_length=40)


@router.patch("/scheduled/{sid}")
def edit_scheduled(sid: str, body: ScheduleEdit, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        r = _mine(conn, sid, user)
        if body.sendAt is not None:
            new_at = scheduled.check_time(body.sendAt)
            closes = (json.loads(r["payload"]).get("poll") or {}).get("closesAt")
            if closes and config.parse_iso(closes) <= config.parse_iso(new_at):
                raise HTTPException(422, "The poll would close before it's sent — choose an earlier time.")
            conn.execute("UPDATE scheduled SET send_at = ? WHERE id = ?", (new_at, sid))
        if body.body is not None:
            p = json.loads(r["payload"])
            if p.get("poll"):
                raise HTTPException(409, "A scheduled poll can't be edited — delete it and make a new one.")
            has_files = conn.execute("SELECT 1 FROM attachments WHERE scheduled_id = ?", (sid,)).fetchone()
            if not body.body.strip() and not has_files:
                raise HTTPException(422, "A message can't be empty — delete it instead.")
            p["body"] = body.body
            conn.execute("UPDATE scheduled SET payload = ? WHERE id = ?", (json.dumps(p), sid))
        return scheduled.item_out(conn, conn.execute("SELECT * FROM scheduled WHERE id = ?", (sid,)).fetchone(), user["id"])


@router.delete("/scheduled/{sid}")
def delete_scheduled(sid: str, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        _mine(conn, sid, user)
        paths = scheduled.free_attachments(conn, sid)
        conn.execute("DELETE FROM scheduled WHERE id = ?", (sid,))
    scheduled.remove_files(paths)
    return {"ok": True}


@router.post("/scheduled/{sid}/send-now")
def send_now(sid: str, user: dict = Depends(require_user)):
    out = chats.Outbox()
    with db.get_conn() as conn:
        r = _mine(conn, sid, user)
        chats.message_limit.take(user["id"])
        mid = scheduled.send(conn, out, r)
    out.flush()
    return {"messageId": mid}


# ---------- download a chat (§15.9) ----------
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@router.get("/conversations/{cid}/export")
def export(cid: str, date_from: str | None = Query(default=None, alias="from"), date_to: str | None = Query(default=None, alias="to"),
           user: dict = Depends(require_user)):
    for d in (date_from, date_to):
        if d and not DATE_RE.match(d):
            raise HTTPException(422, "Dates look like 2026-09-28.")
    path, name = exporter.build(cid, user, date_from, date_to)
    return FileResponse(path, media_type="application/zip", filename=name, background=BackgroundTask(os.remove, path))


# ---------- shared folders ----------
@router.get("/conversations/{cid}/folders")
def folders(cid: str, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        chats.access(conn, cid, user)
        return {"folders": shared_folders.links_of(conn, cid)}


@router.get("/folders/{fid}/list")
def folder_list(fid: str, path: str = Query(default="", max_length=1000), user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        f = shared_folders.folder_row(conn, fid, user)
    return shared_folders.listing(f, path)


@router.get("/folders/{fid}/search")
def folder_search(fid: str, q: str = Query(min_length=1, max_length=200), user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        f = shared_folders.folder_row(conn, fid, user)
    return shared_folders.search(f, q)


@router.get("/folders/{fid}/file")
def folder_file(fid: str, path: str = Query(min_length=1, max_length=1000), download: bool = False,
                user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        f = shared_folders.folder_row(conn, fid, user)
    full = shared_folders.resolve_in(f, path)
    if not os.path.isfile(full):
        raise HTTPException(404, "That file is gone.")
    name = os.path.basename(full)
    ext = files.ext_of(name)
    mime = files.EXT_MIME.get(ext, "application/octet-stream")
    if mime.startswith("image/"):
        mime = files.sniff_image(full)[0] or "application/octet-stream"
    elif mime == "application/pdf":
        with open(full, "rb") as fh:
            if not fh.read(1024).lstrip().startswith(b"%PDF-"):
                mime = "application/octet-stream"
    elif mime in ("video/webm", "video/mp4"):
        mime = "application/octet-stream"
    return file_response(full, mime, name, download)


@router.post("/folders/{fid}/upload", status_code=201)
async def folder_upload(fid: str, request: Request, path: str = Query(default="", max_length=1000),
                        name: str = Query(min_length=1, max_length=255), user: dict = Depends(require_user)):
    def check():
        with db.get_conn() as conn:
            f = shared_folders.folder_row(conn, fid, user)
            conv = chats.conv_row(conn, f["conversation_id"])
            chats.require_post(conn, conv, user["id"])
            return dict(f)
    f = await run_in_threadpool(check)
    if f["mode"] != "rw":
        raise HTTPException(403, "This folder is read-only.")
    clean = files.clean(os.path.basename(name), 120)
    if files.ext_of(clean) in settings.blocked_extensions() or clean.startswith("."):
        raise HTTPException(422, f"“.{files.ext_of(clean)}” files can't be shared here.")
    target_dir = shared_folders.resolve_in(f, path)
    if not os.path.isdir(target_dir):
        raise HTTPException(404, "That folder is gone.")
    max_bytes = settings.get("max_upload_mb") * 1024 * 1024
    chats.upload_limit.take(user["id"])
    tmp = os.path.join(target_dir, f".upload-{secrets.token_hex(8)}.part")
    size = 0
    try:
        with open(tmp, "wb") as out:
            async for chunk in request.stream():
                size += len(chunk)
                if size > max_bytes:
                    raise HTTPException(413, f"That file is bigger than the {max_bytes // (1024 * 1024)} MB limit.")
                out.write(chunk)
        if size == 0:
            raise HTTPException(422, "That file is empty.")
        final = files.unique_name(target_dir, clean)
        os.replace(tmp, os.path.join(target_dir, final))
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return {"name": final, "size": size}


class MkdirIn(Strict):
    path: str = Field(default="", max_length=1000)
    name: str = Field(min_length=1, max_length=100)


@router.post("/folders/{fid}/mkdir", status_code=201)
def folder_mkdir(fid: str, body: MkdirIn, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        f = shared_folders.folder_row(conn, fid, user)
        chats.require_post(conn, chats.conv_row(conn, f["conversation_id"]), user["id"])
    if f["mode"] != "rw":
        raise HTTPException(403, "This folder is read-only.")
    parent = shared_folders.resolve_in(f, body.path)
    name = files.clean(body.name, 100)
    if name.startswith(".") or not os.path.isdir(parent):
        raise HTTPException(422, "Choose another name.")
    target = os.path.join(parent, name)
    if os.path.exists(target):
        raise HTTPException(409, "Something with that name is already there.")
    os.mkdir(target)
    return {"name": name}
