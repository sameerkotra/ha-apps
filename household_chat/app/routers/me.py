"""Me, whoami, my settings and photo, people, presence pings, starred messages,
reminders and my files."""
import os
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import Field

from .. import chats, config, db, files, presence, settings
from ..common import ha_notify, whoami as whoami_core
from ..auth import get_current_user, require_user
from ..live import hub
from .common import Strict

router = APIRouter(prefix="/api", tags=["me"])
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def app_public(conn) -> dict:
    s = settings.all_values(conn)
    return {"editWindowMinutes": s["edit_window_minutes"], "maxUploadMb": s["max_upload_mb"],
            "voiceMaxSeconds": s["voice_max_seconds"], "photoMaxPx": s["photo_max_px"],
            "showPresence": s["show_presence"], "notificationReply": s["notification_reply"],
            "whoCanCreateGroups": s["who_can_create_groups"], "blockedExtensions": s["blocked_extensions"],
            "whoCanAnnounce": s["who_can_announce"], "childrenCanMessageEachOther": s["children_can_message_each_other"],
            "exportMaxMb": s["export_max_mb"], "callsEnabled": s["calls_enabled"],
            "callRingSeconds": s["calls_ring_seconds"]}


@router.get("/me")
def me(user: dict = Depends(get_current_user)):
    with db.get_conn() as conn:
        u = chats.user_row(conn, user["id"])
        personal = None
        if not u["disabled"]:
            personal = chats.ensure_personal(conn, user["id"])
        return {"id": u["id"], "name": u["name"], "username": u["username"], "isAdmin": user["is_admin"],
                "displayNameOnly": user["display_name_only"], "disabled": bool(u["disabled"]), "isChild": bool(u["is_child"]),
                "personalRoomId": personal, "avatar": u["avatar_version"] if u["avatar_file"] else None,
                "settings": {"notifyLevel": u["notify_level"], "notifyPreview": u["notify_preview"],
                             "quietStart": u["quiet_start"], "quietEnd": u["quiet_end"], "hideOnline": bool(u["hide_online"])},
                "notifyLinked": bool(ha_notify.services_for({"id": u["id"]}, conn)),    # phones + extras
                "app": app_public(conn), "version": config.APP_VERSION, "files": files.public_status(),
                "noAdmin": not config.ADMIN_NAMES,        # first run: nobody can open Admin yet (SPEC §4.2)
                "timeZone": getattr(config.tz(), "key", "UTC"),
                "page": config.INGRESS_URL}          # the app's page in Home Assistant (deep links, §15.14)


@router.get("/whoami")
def whoami(request: Request, user: dict = Depends(get_current_user)):
    """Works for disabled people too, so they can see why (common/whoami.py)."""
    with db.get_conn() as conn:
        linked = bool(ha_notify.services_for({"id": user["id"]}, conn))    # phones from HA + extras
        chats_n = conn.execute("SELECT COUNT(*) FROM members WHERE user_id = ?", (user["id"],)).fetchone()[0]
    return whoami_core.build(
        request, user_id=user["id"], username=user["username"], display_name=user["name"],
        is_admin=user["is_admin"], admin_entries=len(config.ADMIN_NAMES),
        display_name_only=user["display_name_only"], notify_linked=linked,
        extras=[whoami_core.row("Access", "Not yet — ask an admin" if user["disabled"] else "Enabled"),
                whoami_core.row("Chats you're in", chats_n),
                whoami_core.notify_row(linked)],
        disabled=user["disabled"])


class SettingsIn(Strict):
    notifyLevel: str | None = Field(default=None, pattern="^(all|direct_mentions|off)$")
    notifyPreview: str | None = Field(default=None, pattern="^(full|sender|none)$")
    quietStart: str | None = Field(default=None, max_length=5)
    quietEnd: str | None = Field(default=None, max_length=5)
    hideOnline: bool | None = None


@router.put("/me/settings")
def put_settings(body: SettingsIn, user: dict = Depends(require_user)):
    fs = body.model_fields_set
    with db.get_conn() as conn:
        if body.notifyLevel:
            conn.execute("UPDATE users SET notify_level = ? WHERE id = ?", (body.notifyLevel, user["id"]))
        if body.notifyPreview:
            conn.execute("UPDATE users SET notify_preview = ? WHERE id = ?", (body.notifyPreview, user["id"]))
        if "quietStart" in fs or "quietEnd" in fs:
            qs, qe = body.quietStart or None, body.quietEnd or None
            if bool(qs) != bool(qe) or (qs and (not TIME_RE.match(qs) or not TIME_RE.match(qe))):
                raise HTTPException(422, "Quiet hours need a start and an end, like 22:00 and 07:00 (or neither).")
            conn.execute("UPDATE users SET quiet_start = ?, quiet_end = ? WHERE id = ?", (qs, qe, user["id"]))
        if body.hideOnline is not None:
            conn.execute("UPDATE users SET hide_online = ? WHERE id = ?", (1 if body.hideOnline else 0, user["id"]))
    hub.publish_all("presence", {"refresh": True})
    return me(user)


# ---------- photos: copies of each person's Home Assistant picture (avatars.py) ----------
@router.get("/avatars/{uid}")
def avatar(uid: str, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        r = chats.user_row(conn, uid)
    if r is None or not r["avatar_file"]:
        raise HTTPException(404, "No photo.")
    path = os.path.join(config.AVATAR_DIR, r["avatar_file"])
    if not os.path.isfile(path):
        raise HTTPException(404, "No photo.")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400",
                                                               "Content-Security-Policy": "sandbox; default-src 'none'"})


# ---------- people and presence ----------
@router.get("/people")
def people(user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        rows = conn.execute("SELECT id, name, avatar_file, avatar_version FROM users WHERE disabled = 0 "
                            "ORDER BY name COLLATE NOCASE").fetchall()
        online = presence.online_map(conn)
    ha = presence.home_away()
    return {"people": [{"id": r["id"], "name": r["name"], "avatar": r["avatar_version"] if r["avatar_file"] else None,
                        "you": r["id"] == user["id"],
                        "online": online.get(r["id"]), "homeAway": ha.get(r["id"])} for r in rows]}


class PresenceIn(Strict):
    tab: str = Field(min_length=4, max_length=40)
    conversationId: str | None = Field(default=None, max_length=64)
    visible: bool = False


@router.post("/presence")
def ping(body: PresenceIn, user: dict = Depends(require_user)):
    """Which chat this tab shows (for "already looking", §7) and a heartbeat (online, §8)."""
    was = hub.is_online(user["id"])
    hub.set_tab(user["id"], body.tab, body.conversationId, body.visible)
    if not was:
        hub.publish_all("presence", {"refresh": True})
    return {"ok": True}


# ---------- starred, reminders, my files ----------
@router.get("/me/starred")
def starred(user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT m.*, mb.joined_message_id AS vf FROM stars s JOIN messages m ON m.id = s.message_id "
            "JOIN members mb ON mb.conversation_id = m.conversation_id AND mb.user_id = s.user_id "
            "WHERE s.user_id = ? AND m.deleted_at IS NULL AND m.id > mb.joined_message_id ORDER BY s.created_at DESC LIMIT 500",
            (user["id"],)).fetchall()
        msgs = [chats.messages_out(conn, [r], user["id"], r["vf"])[0] for r in rows]
        names = {}
        for r in rows:
            if r["conversation_id"] not in names:
                names[r["conversation_id"]] = chats.display_name_of(conn, chats.conv_row(conn, r["conversation_id"]), user["id"])
        for mm in msgs:
            mm["conversationName"] = names[mm["conversationId"]]
    return {"messages": msgs}


@router.get("/me/calls")
def my_calls(user: dict = Depends(require_user)):
    """My recent calls, newest first (up to 100), for the Calls list (§15.12): who, when, how it ended."""
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT c.*, m.deleted_at FROM calls c JOIN members mb ON mb.conversation_id = c.conversation_id AND mb.user_id = ? "
            "LEFT JOIN messages m ON m.id = c.message_id WHERE (c.caller_id = ? OR c.callee_id = ?) AND c.ended_at IS NOT NULL "
            "ORDER BY c.started_at DESC LIMIT 100", (user["id"], user["id"], user["id"])).fetchall()
        out = []
        for r in rows:
            other = r["callee_id"] if r["caller_id"] == user["id"] else r["caller_id"]
            conv = chats.conv_row(conn, r["conversation_id"])
            out.append({"id": r["id"], "conversationId": r["conversation_id"], "messageId": r["message_id"],
                        "peerId": other, "peerName": chats.shown_name(chats.user_row(conn, other)),
                        "outgoing": r["caller_id"] == user["id"], "outcome": r["outcome"],
                        "missed": r["callee_id"] == user["id"] and r["outcome"] in ("missed", "busy"),
                        "startedAt": r["started_at"], "seconds": chats.call_seconds(r),
                        "canCallBack": conv is not None and chats.can_post(conn, conv, user["id"])})
    return {"calls": out}


@router.get("/me/reminders")
def reminders(user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT r.*, m.conversation_id, m.body, m.kind, m.user_id AS author_id FROM reminders r "
            "JOIN messages m ON m.id = r.message_id WHERE r.user_id = ? "
            "ORDER BY r.done_at IS NOT NULL, CASE WHEN r.done_at IS NULL THEN r.due_at END ASC, r.done_at DESC LIMIT 200",
            (user["id"],)).fetchall()
        out = []
        for r in rows:
            conv = chats.conv_row(conn, r["conversation_id"])
            if chats.member_row(conn, r["conversation_id"], user["id"]) is None:
                continue
            out.append({"id": r["id"], "messageId": r["message_id"], "conversationId": r["conversation_id"],
                        "conversationName": chats.display_name_of(conn, conv, user["id"]),
                        "author": chats.shown_name(chats.user_row(conn, r["author_id"])) if r["author_id"] else None,
                        "text": chats.preview_of(conn, chats.message_row(conn, r["message_id"])),
                        "dueAt": r["due_at"], "doneAt": r["done_at"]})
    return {"reminders": out}


@router.delete("/me/reminders/{rid}")
def delete_reminder(rid: str, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        if not conn.execute("DELETE FROM reminders WHERE id = ? AND user_id = ?", (rid, user["id"])).rowcount:
            raise HTTPException(404, "That reminder doesn't exist.")
    return reminders(user)


def files_query(conn, where: str, args: list, user_id: str, kind: str | None, limit: int = 300) -> list:
    type_sql = {"images": " AND a.mime IN ('image/png','image/jpeg','image/gif','image/webp')",
                "pdfs": " AND a.mime = 'application/pdf'",
                "voice": " AND a.voice = 1",
                "other": " AND a.voice = 0 AND a.mime NOT IN ('image/png','image/jpeg','image/gif','image/webp','application/pdf')",
                }.get(kind or "", "")
    rows = conn.execute(
        "SELECT a.*, m.user_id AS sender, m.created_at AS sent_at, m.conversation_id AS cid FROM attachments a "
        "JOIN messages m ON m.id = a.message_id JOIN members mb ON mb.conversation_id = m.conversation_id AND mb.user_id = ? "
        "WHERE m.deleted_at IS NULL AND m.id > mb.joined_message_id" + where + type_sql +
        " ORDER BY m.id DESC LIMIT ?", [user_id, *args, limit]).fetchall()
    names: dict = {}
    out = []
    for a in rows:
        d = chats.attachment_out(a)
        if a["sender"] not in names:
            names[a["sender"]] = chats.shown_name(chats.user_row(conn, a["sender"]))
        d.update({"messageId": a["message_id"], "conversationId": a["cid"], "sender": names[a["sender"]],
                  "sentAt": a["sent_at"]})
        out.append(d)
    return out


@router.get("/me/files")
def my_files(type: str | None = None, starred: bool = False, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        if starred:
            where, args = " AND a.message_id IN (SELECT message_id FROM stars WHERE user_id = ?)", [user["id"]]
        else:
            where, args = " AND a.uploaded_by = ?", [user["id"]]
        rows = files_query(conn, where, args, user["id"], type)
        cnames: dict = {}
        for d in rows:
            if d["conversationId"] not in cnames:
                cnames[d["conversationId"]] = chats.display_name_of(conn, chats.conv_row(conn, d["conversationId"]), user["id"])
            d["conversationName"] = cnames[d["conversationId"]]
    return {"files": rows}
