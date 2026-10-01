"""Chats: list, create, change, membership, my per-chat settings, reading and posting (SPEC §6)."""
from datetime import timedelta

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import Field

from .. import chats, config, db, presence, shared_folders
from ..auth import require_user
from ..live import hub
from .common import Strict
from .me import files_query

router = APIRouter(prefix="/api", tags=["conversations"])


def _icon(v: str | None) -> str | None:
    if v is None:
        return None
    v = v.strip()
    if not v:
        return None
    if len(v) > 16 or any(c.isalnum() and c.isascii() for c in v) or any(c in "<>&\"'" for c in v):
        raise HTTPException(422, "The icon is one emoji.")
    return v


def _name(v: str) -> str:
    v = " ".join((v or "").split())
    if not 1 <= len(v) <= 60:
        raise HTTPException(422, "A group name is 1–60 characters.")
    return v


def _desc(v: str | None) -> str | None:
    if v is None:
        return None
    v = v.strip()
    if len(v) > 500:
        raise HTTPException(422, "A description can be at most 500 characters.")
    return v or None


@router.get("/conversations")
def list_conversations(user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        convs = chats.list_conversations(conn, user["id"])
        online = presence.online_map(conn)
    return {"conversations": convs, "online": online, "homeAway": presence.home_away()}


@router.get("/conversations/{cid}")
def get_conversation(cid: str, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        conv, m = chats.access(conn, cid, user)
        out = chats.conversation_out(conn, conv, m, user["id"])
        out["members"] = chats.members_out(conn, cid)
        out["pinCount"] = conn.execute("SELECT COUNT(*) FROM messages WHERE conversation_id = ? AND pinned_at IS NOT NULL "
                                       "AND id > ?", (cid, m["joined_message_id"])).fetchone()[0]
        out["createdAt"] = conv["created_at"]
        out["canAnnounce"] = chats.can_announce(conn, conv, m, user)
        rows = conn.execute("SELECT * FROM messages WHERE conversation_id = ? AND announcement = 1 AND announcement_closed_at IS NULL "
                            "AND deleted_at IS NULL AND id > ? ORDER BY id DESC LIMIT 20", (cid, m["joined_message_id"])).fetchall()
        out["announcements"] = [x for x in chats.messages_out(conn, rows, user["id"], m["joined_message_id"])
                                if not x["announcement"]["closed"]][:5]
        out["folders"] = shared_folders.links_of(conn, cid)
        return out


class DirectIn(Strict):
    userId: str = Field(min_length=1, max_length=200)


@router.post("/conversations/direct")
def open_direct(body: DirectIn, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        return {"id": chats.get_or_create_direct(conn, user["id"], body.userId)}


class GroupIn(Strict):
    name: str = Field(max_length=200)
    icon: str | None = Field(default=None, max_length=32)
    description: str | None = Field(default=None, max_length=2000)
    memberIds: list[str] = Field(default_factory=list, max_length=50)


@router.post("/conversations", status_code=201)
def create_group(body: GroupIn, user: dict = Depends(require_user)):
    out = chats.Outbox()
    with db.get_conn() as conn:
        cid = chats.create_group(conn, out, user, _name(body.name), _icon(body.icon), body.memberIds, _desc(body.description))
    out.flush()
    return {"id": cid}


class PatchIn(Strict):
    disappearSeconds: int | None = None
    name: str | None = Field(default=None, max_length=200)
    icon: str | None = Field(default=None, max_length=32)
    description: str | None = Field(default=None, max_length=2000)
    newMembersSeeHistory: bool | None = None


@router.patch("/conversations/{cid}")
def patch_conversation(cid: str, body: PatchIn, user: dict = Depends(require_user)):
    out = chats.Outbox()
    fs = body.model_fields_set
    # validate everything before writing anything (a folder rename on disk can't be rolled back by SQLite)
    name = _name(body.name) if "name" in fs and body.name is not None else None
    icon = _icon(body.icon) if "icon" in fs else None
    desc = _desc(body.description) if "description" in fs else None
    disappear = body.disappearSeconds or None
    if disappear is not None and disappear not in chats.DURATIONS:
        raise HTTPException(422, "Disappear after 1 hour, 1 day, 7 days or 30 days, or turn it off.")
    with db.get_conn() as conn:
        conv, m = chats.access(conn, cid, user)
        if fs - {"description"}:
            chats.not_personal(conv)          # a personal room can only have a description
        if "disappearSeconds" in fs:
            chats.require_adult(user, "change disappearing messages")
        if conv["kind"] == "direct":
            if fs - {"description", "disappearSeconds"}:
                raise HTTPException(409, "A direct chat can't be renamed.")
            chats.require_post(conn, conv, user["id"])
        elif conv["kind"] == "group" and not chats.is_manager(conv, m, user):
            raise HTTPException(403, "Only the group's owner and group admins can change it.")
        renamed = conv["kind"] == "group" and name is not None and name != conv["name"]
        if "disappearSeconds" in fs and disappear != conv["disappear_seconds"]:
            conn.execute("UPDATE conversations SET disappear_seconds = ? WHERE id = ?", (disappear, cid))
            chats.add_system(conn, out, conv, {"event": "disappear_on" if disappear else "disappear_off",
                                                "actor": user["id"], "seconds": disappear})
        if "icon" in fs and conv["kind"] == "group" and icon != conv["icon"]:
            conn.execute("UPDATE conversations SET icon = ? WHERE id = ?", (icon, cid))
            chats.add_system(conn, out, conv, {"event": "icon", "actor": user["id"]})
        if "description" in fs and desc != conv["description"]:
            conn.execute("UPDATE conversations SET description = ? WHERE id = ?", (desc, cid))
            chats.add_system(conn, out, conv, {"event": "description", "actor": user["id"]})
        if body.newMembersSeeHistory is not None and conv["kind"] == "group":
            v = 1 if body.newMembersSeeHistory else 0
            if v != conv["new_members_see_history"]:
                conn.execute("UPDATE conversations SET new_members_see_history = ? WHERE id = ?", (v, cid))
                chats.add_system(conn, out, conv, {"event": "history", "actor": user["id"]})
        if renamed:
            conn.execute("UPDATE conversations SET name = ? WHERE id = ?", (name, cid))
            chats.add_system(conn, out, conv, {"event": "renamed", "actor": user["id"], "name": name})
            from .. import files
            files.refresh_folder(conn, cid)          # last: nothing after it can fail before the commit
        chats.conversation_members_event(conn, out, chats.conv_row(conn, cid))
    out.flush()
    return get_conversation(cid, user)


# ---------- membership ----------
class AddIn(Strict):
    userIds: list[str] = Field(min_length=1, max_length=20)


@router.post("/conversations/{cid}/members")
def add_members(cid: str, body: AddIn, user: dict = Depends(require_user)):
    out = chats.Outbox()
    with db.get_conn() as conn:
        conv, m = chats.access(conn, cid, user)
        chats.not_personal(conv)
        if conv["kind"] != "group":
            raise HTTPException(409, "Nobody can be added to a direct chat — start a group instead.")
        if not chats.is_manager(conv, m, user):
            raise HTTPException(403, "Only the group's owner and group admins can add people.")
        count = conn.execute("SELECT COUNT(*) FROM members WHERE conversation_id = ?", (cid,)).fetchone()[0]
        new = [u for u in dict.fromkeys(body.userIds) if not chats.member_row(conn, cid, u)]
        if count + len(new) > chats.MAX_GROUP_MEMBERS:
            raise HTTPException(422, f"A group can have at most {chats.MAX_GROUP_MEMBERS} people.")
        for u in new:
            r = chats.user_row(conn, u)
            if r is None or r["disabled"]:
                raise HTTPException(422, "Only people with access to Household Chat can be added.")
        for u in new:
            chats.join(conn, out, chats.conv_row(conn, cid), u, "member", user["id"])
    out.flush()
    return get_conversation(cid, user)


@router.delete("/conversations/{cid}/members/{uid}")
def remove_member(cid: str, uid: str, user: dict = Depends(require_user)):
    out = chats.Outbox()
    with db.get_conn() as conn:
        conv, m = chats.access(conn, cid, user)
        chats.not_personal(conv)
        if conv["kind"] != "group":
            raise HTTPException(409, "Nobody can be removed from a direct chat.")
        target = chats.member_row(conn, cid, uid)
        if target is None:
            raise HTTPException(404, "They aren't in this group.")
        if uid == user["id"]:
            raise HTTPException(409, "Use Leave to leave a group.")
        chats.require_adult(user, "remove people")
        allowed = m["role"] == "owner" or (m["role"] == "admin" and target["role"] == "member")
        if not allowed:
            raise HTTPException(403, "Only the owner can remove group admins; group admins can remove members.")
        chats.remove_member(conn, out, conv, uid, "removed", user["id"])
    out.flush()
    return get_conversation(cid, user)


class RoleIn(Strict):
    role: str = Field(pattern="^(admin|member)$")


@router.patch("/conversations/{cid}/members/{uid}")
def set_role(cid: str, uid: str, body: RoleIn, user: dict = Depends(require_user)):
    out = chats.Outbox()
    with db.get_conn() as conn:
        conv, m = chats.access(conn, cid, user)
        chats.not_personal(conv)
        if conv["kind"] != "group" or not chats.is_manager(conv, m, user):
            raise HTTPException(403, "Only the group's owner and group admins can do this.")
        if body.role == "admin" and (chats.user_row(conn, uid) or {"is_child": 0})["is_child"]:
            raise HTTPException(409, "A child can't be a group admin.")
        target = chats.member_row(conn, cid, uid)
        if target is None:
            raise HTTPException(404, "They aren't in this group.")
        if target["role"] == "owner":
            raise HTTPException(409, "The owner stays the owner — hand the group over instead.")
        if target["role"] != body.role:
            conn.execute("UPDATE members SET role = ? WHERE conversation_id = ? AND user_id = ?", (body.role, cid, uid))
            chats.add_system(conn, out, conv, {"event": "admin_on" if body.role == "admin" else "admin_off",
                                                "actor": user["id"], "target": uid})
            chats.conversation_members_event(conn, out, conv)
    out.flush()
    return get_conversation(cid, user)


@router.post("/conversations/{cid}/leave")
def leave(cid: str, user: dict = Depends(require_user)):
    out = chats.Outbox()
    with db.get_conn() as conn:
        conv, m = chats.access(conn, cid, user)
        chats.not_personal(conv)
        if conv["kind"] != "group":
            raise HTTPException(409, "You can't leave a direct chat.")
        chats.remove_member(conn, out, conv, user["id"], "left", user["id"])
    out.flush()
    return {"ok": True}


class TransferIn(Strict):
    userId: str = Field(min_length=1, max_length=200)


@router.post("/conversations/{cid}/transfer")
def transfer(cid: str, body: TransferIn, user: dict = Depends(require_user)):
    out = chats.Outbox()
    with db.get_conn() as conn:
        conv, m = chats.access(conn, cid, user)
        chats.not_personal(conv)
        if conv["kind"] != "group" or m["role"] != "owner":
            raise HTTPException(403, "Only the group's owner can hand it over.")
        chats.require_adult(user, "hand a group over")
        t = chats.member_row(conn, cid, body.userId)
        tu = chats.user_row(conn, body.userId)
        if t is None or tu is None or tu["disabled"] or body.userId == user["id"]:
            raise HTTPException(422, "Choose someone else in the group.")
        conn.execute("UPDATE members SET role = 'admin' WHERE conversation_id = ? AND user_id = ?", (cid, user["id"]))
        conn.execute("UPDATE members SET role = 'owner' WHERE conversation_id = ? AND user_id = ?", (cid, body.userId))
        chats.add_system(conn, out, conv, {"event": "owner", "target": body.userId})
        chats.conversation_members_event(conn, out, conv)
    out.flush()
    return get_conversation(cid, user)


@router.delete("/conversations/{cid}")
def delete_group(cid: str, body: dict = Body(default={}), user: dict = Depends(require_user)):
    out = chats.Outbox()
    with db.get_conn() as conn:
        conv, m = chats.access(conn, cid, user)
        chats.not_personal(conv)
        if conv["kind"] != "group" or m["role"] != "owner":
            raise HTTPException(403, "Only the group's owner can delete it.")
        chats.require_adult(user, "delete groups")
        if (body or {}).get("confirmName") != conv["name"]:
            raise HTTPException(422, "Type the group's name exactly to delete it.")
        delete_conversation(conn, out, conv, user["id"], "group_deleted")
    out.flush()
    return {"ok": True}


def delete_conversation(conn, out, conv, actor: str, action: str) -> None:
    from .. import files
    uids = chats.member_ids(conn, conv["id"], enabled_only=False)
    for a in conn.execute("SELECT id FROM attachments WHERE conversation_id = ?", (conv["id"],)).fetchall():
        files.remove_thumb(a["id"])
    files.remove_disappearing_dir(conv["folder"])          # disappearing files never go to _deleted
    files.move_folder_to_deleted(conv["folder"])
    conn.execute("DELETE FROM conversations WHERE id = ?", (conv["id"],))
    db.audit(conn, action, None, conv["id"], actor)
    out.event(uids, "conversation", {"id": conv["id"], "removed": True})


# ---------- my settings for a chat ----------
class MineIn(Strict):
    notify: str | None = Field(default=None, pattern="^(default|all|mentions|off)$")
    mutedUntil: str | None = Field(default=None, max_length=40)
    pinned: bool | None = None


@router.put("/conversations/{cid}/me")
def put_mine(cid: str, body: MineIn, user: dict = Depends(require_user)):
    fs = body.model_fields_set
    with db.get_conn() as conn:
        conv, m = chats.access(conn, cid, user)
        if body.notify:
            chats.not_personal(conv)
            conn.execute("UPDATE members SET notify = ? WHERE conversation_id = ? AND user_id = ?", (body.notify, cid, user["id"]))
        if "mutedUntil" in fs:
            chats.not_personal(conv)
            v = None
            if body.mutedUntil:
                t = config.parse_iso(body.mutedUntil)
                if t is None or t > config.utcnow() + timedelta(days=3660):
                    raise HTTPException(422, "Mute until a date and time, or not at all.")
                v = config.iso(t)
            conn.execute("UPDATE members SET muted_until = ? WHERE conversation_id = ? AND user_id = ?", (v, cid, user["id"]))
        if body.pinned is not None:
            conn.execute("UPDATE members SET pinned = ? WHERE conversation_id = ? AND user_id = ?",
                         (1 if body.pinned else 0, cid, user["id"]))
    return get_conversation(cid, user)


class ReadIn(Strict):
    upTo: int = Field(ge=0)


@router.post("/conversations/{cid}/read")
def read(cid: str, body: ReadIn, user: dict = Depends(require_user)):
    out = chats.Outbox()
    with db.get_conn() as conn:
        conv, m = chats.access(conn, cid, user)
        last = chats.mark_read(conn, out, conv, m, user["id"], body.upTo)
    out.flush()
    return {"lastReadId": last}


# ---------- messages ----------
@router.get("/conversations/{cid}/messages")
def messages(cid: str, before: int | None = Query(default=None, ge=0), after: int | None = Query(default=None, ge=0),
             around: int | None = Query(default=None, ge=0), user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        conv, m = chats.access(conn, cid, user)
        return chats.page(conn, conv, m, user["id"], before, after, around)


class MessageIn(Strict):
    expiresIn: int | None = None
    announcement: bool = False
    body: str = Field(default="", max_length=20000)
    replyTo: int | None = None
    attachmentIds: list[str] = Field(default_factory=list, max_length=20)
    mentions: list[str] = Field(default_factory=list, max_length=50)


@router.post("/conversations/{cid}/messages", status_code=201)
def post(cid: str, body: MessageIn, user: dict = Depends(require_user)):
    out = chats.Outbox()
    with db.get_conn() as conn:
        conv, m = chats.access(conn, cid, user)
        chats.message_limit.take(user["id"])
        if conv["kind"] == "personal":
            body.mentions = []
        mid = chats.post_message(conn, out, conv, m, user, body.body, body.replyTo, body.attachmentIds, body.mentions,
                                 expires_in=body.expiresIn, announcement=body.announcement)
        msg = chats.messages_out(conn, [chats.message_row(conn, mid)], user["id"], m["joined_message_id"])[0]
    out.flush()
    return msg


@router.post("/conversations/{cid}/typing")
def typing(cid: str, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        conv, m = chats.access(conn, cid, user)
        chats.not_personal(conv)
        others = [u for u in chats.member_ids(conn, cid) if u != user["id"]]
    hub.publish(others, "typing", {"conversationId": cid, "userId": user["id"], "name": user["name"]})
    return {"ok": True}


@router.get("/conversations/{cid}/pins")
def pins(cid: str, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        conv, m = chats.access(conn, cid, user)
        rows = conn.execute("SELECT * FROM messages WHERE conversation_id = ? AND pinned_at IS NOT NULL AND id > ? "
                            "ORDER BY pinned_at DESC", (cid, m["joined_message_id"])).fetchall()
        return {"messages": chats.messages_out(conn, rows, user["id"], m["joined_message_id"])}


@router.get("/conversations/{cid}/files")
def conversation_files(cid: str, type: str | None = None, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        chats.access(conn, cid, user)
        return {"files": files_query(conn, " AND m.conversation_id = ?", [cid], user["id"], type, 500)}


# ---------- polls ----------
class PollIn(Strict):
    expiresIn: int | None = None
    question: str = Field(min_length=1, max_length=200)
    options: list[str] = Field(min_length=2, max_length=10)
    multi: bool = False
    closesAt: str | None = Field(default=None, max_length=40)


@router.post("/conversations/{cid}/polls", status_code=201)
def create_poll(cid: str, body: PollIn, user: dict = Depends(require_user)):
    q, opts, closes = chats.check_poll(body.question, body.options, body.closesAt)
    out = chats.Outbox()
    with db.get_conn() as conn:
        conv, m = chats.access(conn, cid, user)
        chats.message_limit.take(user["id"])
        mid = chats.create_poll(conn, out, conv, user, q, opts, body.multi, closes, body.expiresIn)
        res = chats.messages_out(conn, [chats.message_row(conn, mid)], user["id"], m["joined_message_id"])[0]
    out.flush()
    return res
