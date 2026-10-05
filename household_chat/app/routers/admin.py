"""Admin (SPEC §4, §6, §9): people (enable/disable, phones from Home Assistant and extra notify services,
the home/away entity),
App settings, the chat overview (metadata only — never content), storage, backup and restore.

Nothing here returns message text, file names or file contents of any chat.
"""
import itertools
import logging
import os
import re
from datetime import timedelta
import shutil
import sqlite3
import tempfile
import time
import zipfile

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from .. import app_messages, avatars, calls, chats, config, db, disappearing, files, ha_client, presence, settings, shared_folders
from ..common import backup_core, db_core, ha_notify, ha_people, people_admin
from ..auth import require_admin
from ..live import hub
from .conversations import delete_conversation

logger = logging.getLogger("admin")
router = APIRouter(prefix="/api", tags=["admin"])
_last_sync = {"t": 0.0}
ENTITY_RE = re.compile(r"^person\.[a-z0-9_]+$")
MAX_SERVICES_PER_USER = 5
RESTORE_MAX = 20 * 1024 ** 3


def _maybe_sync():
    if time.time() - _last_sync["t"] > 300:
        _last_sync["t"] = time.time()
        try:
            ha_client.sync_users_blocking()
        except Exception:
            pass


def _known(conn, uid: str):
    r = chats.user_row(conn, uid)
    if r is None:
        raise HTTPException(404, "That person isn't known to Household Chat.")
    return r


# ---------- people ----------
ha_person_json = people_admin.ha_person_json          # their person and phones (common/people_admin.py)


def _people_json(admin: dict) -> dict:
    missing = presence.missing()
    with db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM users ORDER BY disabled, name COLLATE NOCASE").fetchall()
        out = []
        for r in rows:
            out.append({"id": r["id"], "name": r["name"], "username": r["username"], "person": r["ha_person"],
                        "disabled": bool(r["disabled"]), "lastSeen": r["last_seen"], "you": r["id"] == admin["id"],
                        "online": hub.is_online(r["id"]),
                        "notify": ha_notify.assigned_services(conn, {"id": r["id"]}),   # extra services added here
                        "ha": ha_person_json(r["id"]),                                  # phones from Home Assistant
                        # home/away + photo: their login's person, or the one chosen here (presenceChosen)
                        "presenceEntity": presence.entity_of(r), "presenceChosen": bool(r["presence_entity"]),
                        "presenceMissing": r["id"] in missing, "isChild": bool(r["is_child"]),
                        "chatCount": conn.execute("SELECT COUNT(*) FROM members WHERE user_id = ?", (r["id"],)).fetchone()[0]})
    return {"people": out}


@router.get("/admin/people")
def people(refresh: bool = Query(default=False), admin: dict = Depends(require_admin)):
    """Everyone, with their phones from Home Assistant and any extra notify services. `refresh=1` reads Home
    Assistant's people again first (no DB connection is open meanwhile)."""
    if refresh:
        _last_sync["t"] = 0.0
    people_admin.refresh_people(refresh)
    _maybe_sync()
    return _people_json(admin)


@router.patch("/admin/people/{uid}")
def patch_person(uid: str, body: dict = Body(...), admin: dict = Depends(require_admin)):
    if not body or set(body) - {"disabled", "isChild"} or any(not isinstance(v, bool) for v in body.values()):
        raise HTTPException(422, "Send {\"disabled\": true|false} and/or {\"isChild\": true|false}.")
    out = chats.Outbox()
    with db.get_conn() as conn:
        r = _known(conn, uid)
        if "isChild" in body and bool(r["is_child"]) != body["isChild"]:
            chats.set_child(conn, out, uid, body["isChild"], admin["id"])
        if body.get("disabled") is True and not r["disabled"]:
            chats.disable_user(conn, out, uid, admin["id"])
        elif body.get("disabled") is False and r["disabled"]:
            chats.enable_user(conn, out, uid, admin["id"])
    out.flush()
    hub.publish_all("presence", {"refresh": True})
    return _people_json(admin)


@router.get("/admin/notify-services")
def notify_services(refresh: bool = False, admin: dict = Depends(require_admin)):
    return people_admin.notify_services(refresh)


_service = people_admin.clean_service


@router.post("/admin/people/{uid}/notify", status_code=201)
def add_notify(uid: str, body: dict = Body(...), admin: dict = Depends(require_admin)):
    svc = _service(body.get("service"))
    with db.get_conn() as conn:
        _known(conn, uid)
        return {"notify": people_admin.add_service(conn, uid, svc, admin.get("username") or admin["id"], config.now_iso(),
                                                   limit=MAX_SERVICES_PER_USER)}


@router.delete("/admin/people/{uid}/notify/{svc}")
def remove_notify(uid: str, svc: str, admin: dict = Depends(require_admin)):
    svc = _service(svc)
    with db.get_conn() as conn:
        _known(conn, uid)
        return {"notify": people_admin.remove_service(
            conn, uid, svc, "That service isn't one of their extra services (phones are set in Home Assistant).")}


@router.post("/admin/people/{uid}/notify/test")
def test_notify(uid: str, admin: dict = Depends(require_admin)):
    with db.get_conn() as conn:
        row = _known(conn, uid)
        services = ha_notify.services_for({"id": uid}, conn)          # phones + extras
    if not services:
        raise HTTPException(409, f"{row['name']} has no phone linked in Home Assistant (Settings → People → "
                                 "Track device) and no extra notify service here.")
    results = ha_notify.send_to_services(services, config.APP_TITLE, f"Test from Household Chat for {row['name']} — "
                                         "new messages will arrive like this.", {"url": config.INGRESS_URL,
                                                                                 "clickAction": config.INGRESS_URL})
    return {"results": people_admin.test_results(results, as_list=True)}


# ---------- home / away (§15.3) ----------
@router.get("/admin/person-entities")
def person_entities(admin: dict = Depends(require_admin)):
    states = ha_client.fetch_states_blocking(max_age=10)
    if states is None:
        return {"available": False, "entities": []}
    return {"available": True, "entities": [{"entityId": p["entityId"], "name": p["name"], "state": p["state"],
                                             "linkedUserId": p["userId"]} for p in ha_client.person_entities(states)]}


@router.put("/admin/people/{uid}/presence")
def set_presence(uid: str, body: dict = Body(...), admin: dict = Depends(require_admin)):
    """Choose a different person.* for their home/away and photo, or `null` for automatic — the person
    linked to their Home Assistant login (users.ha_person)."""
    entity = body.get("entity")
    if entity is not None:
        if not isinstance(entity, str) or not ENTITY_RE.match(entity):
            raise HTTPException(422, "Choose a person.* entity (like person.priya), or null for automatic.")
        states = ha_client.fetch_states_blocking(max_age=0)
        if states is not None and entity not in {p["entityId"] for p in ha_client.person_entities(states)}:
            raise HTTPException(422, f"Home Assistant has no {entity}.")
    with db.get_conn() as conn:
        r = _known(conn, uid)
        if entity == r["ha_person"]:
            entity = None                # their own person: automatic, so it follows their login
        conn.execute("UPDATE users SET presence_entity = ? WHERE id = ?", (entity, uid))
        db.audit(conn, "presence_assigned", uid, None, admin["id"])
    try:
        presence.refresh_blocking(force=True)
    except Exception:
        pass
    try:
        avatars.sync_blocking()          # their photo is this entity's picture
    except Exception:
        logger.exception("Syncing photos failed")
    return _people_json(admin)


# ---------- App settings ----------
def _settings_out() -> dict:
    return dict(settings.payload(), storage=files.storage_info())


def _shared_paths() -> list[str]:
    with db.get_conn() as conn:
        rels = [r["path"] for r in conn.execute("SELECT path FROM folders")]
    root = "/" + os.path.normpath(config.SHARE_ROOT).lstrip("/")
    return [root.rstrip("/") + "/" + r for r in rels]


def _clean_path(value) -> str:
    try:
        return files.clean_files_path(value)
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.get("/admin/settings")
def get_settings(admin: dict = Depends(require_admin)):
    return _settings_out()


@router.get("/admin/connected-apps")
def connected_apps(admin: dict = Depends(require_admin)):
    """The other household apps this one exchanges messages with (APP_MESSAGES_SPEC §5): read only."""
    return app_messages.connected_apps()


@router.put("/admin/settings")
def put_settings(body: dict = Body(...), admin: dict = Depends(require_admin)):
    """Any subset of the settings. Changing the chat files folder never moves files: a folder that belongs to
    another install, is shared into a group, is read-only or can't be created is refused (409); one without this
    install's marker while chats already have files needs `"confirm": true` (409 otherwise)."""
    body = dict(body)
    confirm = body.pop("confirm", False)
    if not isinstance(confirm, bool):
        raise HTTPException(422, "confirm must be true or false.")
    unknown = set(body) - set(settings.DEFAULTS) - set(settings.REGISTRY.flags)
    if unknown:
        raise HTTPException(422, settings.unknown_message(unknown))
    switching = False
    if "files_path" in body:
        body["files_path"] = _clean_path(body["files_path"])
        switching = body["files_path"] != files.configured()
        if switching:
            info = files.inspect(body["files_path"], _shared_paths())
            if info["refused"]:
                raise HTTPException(409, info["message"])
            if info["needsConfirm"] and not confirm:
                raise HTTPException(409, info["message"] + " Confirm to switch anyway.")
    try:
        settings.update(body, admin.get("username") or admin["id"])
    except settings.SettingsError as e:
        raise HTTPException(422, str(e))
    with db.get_conn() as conn:
        db.audit(conn, "settings_changed", None, None, admin["id"])
    if switching:
        logger.info("Chat files folder changed to %s by %s", body["files_path"], admin.get("username") or admin["id"])
        files.check(allow_setup=True)          # applies at once: creates the folder and what the app needs in it
    hub.publish_all("settings", {})
    return _settings_out()


@router.post("/admin/settings/check-files-path")
def check_files_path(body: dict = Body(...), admin: dict = Depends(require_admin)):
    """What a candidate chat files folder holds — before saving it. No side effects."""
    return files.inspect(_clean_path(body.get("path")), _shared_paths())


@router.post("/admin/files-storage/check")
def files_storage_check(body: dict = Body(default={}), admin: dict = Depends(require_admin)):
    """"Check again" — or, with `useThisFolder`, set up the folder in use now for this install (its marker file
    was lost, or it came from another install). Never moves or deletes files."""
    use = (body or {}).get("useThisFolder") is True
    files.check(allow_setup=use)
    if use:
        with db.get_conn() as conn:
            db.audit(conn, "files_folder_adopted", None, None, admin["id"])
    return files.storage_info()


# ---------- chat overview: metadata only ----------
def _overview(conn) -> list:
    out = []
    for c in conn.execute("SELECT * FROM conversations ORDER BY kind, COALESCE(last_activity_at, created_at) DESC").fetchall():
        members = conn.execute("SELECT u.id, u.name, u.disabled FROM members m JOIN users u ON u.id = m.user_id "
                               "WHERE m.conversation_id = ? ORDER BY u.name COLLATE NOCASE", (c["id"],)).fetchall()
        size = files.usage_bytes(conn, c["id"])
        count = conn.execute("SELECT COUNT(*) FROM attachments WHERE conversation_id = ? AND message_id IS NOT NULL",
                             (c["id"],)).fetchone()[0]
        if c["kind"] == "personal":
            owner = chats.user_row(conn, c["created_by"])
            out.append({"id": c["id"], "kind": "personal", "name": f"{owner['name'] if owner else 'Someone'}'s room",
                        "bytes": size, "files": count})
            continue
        enabled = [m for m in members if not m["disabled"]]
        name = c["name"] if c["kind"] == "group" else "Direct: " + " & ".join(m["name"] for m in members)
        out.append({"id": c["id"], "kind": c["kind"], "name": name, "household": bool(c["is_household"]),
                    "members": [m["name"] + (" (no access)" if m["disabled"] else "") for m in members],
                    "createdAt": c["created_at"], "lastActivityAt": c["last_activity_at"],
                    "messages": conn.execute("SELECT COUNT(*) FROM messages WHERE conversation_id = ? AND kind != 'system' "
                                             "AND deleted_at IS NULL", (c["id"],)).fetchone()[0],
                    "bytes": size, "files": count,
                    "canDelete": c["kind"] == "group" and not enabled})
    return out


@router.get("/admin/conversations")
def overview(admin: dict = Depends(require_admin)):
    with db.get_conn() as conn:
        return {"conversations": _overview(conn)}


@router.delete("/admin/conversations/{cid}")
def admin_delete(cid: str, body: dict = Body(default={}), admin: dict = Depends(require_admin)):
    out = chats.Outbox()
    with db.get_conn() as conn:
        conv = chats.conv_row(conn, cid)
        if conv is None:
            raise HTTPException(404, "That chat doesn't exist.")
        enabled = conn.execute("SELECT COUNT(*) FROM members m JOIN users u ON u.id = m.user_id WHERE m.conversation_id = ? "
                               "AND u.disabled = 0", (cid,)).fetchone()[0]
        if conv["kind"] != "group" or enabled:
            raise HTTPException(409, "Admins can only delete a group nobody with access is in any more.")
        if (body or {}).get("confirmName") != conv["name"]:
            raise HTTPException(422, "Type the group's name exactly to delete it.")
        delete_conversation(conn, out, conv, admin["id"], "admin_deleted_chat")
    out.flush()
    return overview(admin)


# ---------- storage ----------
def _db_bytes() -> int:
    return sum(os.path.getsize(config.DB_PATH + ext) for ext in ("", "-wal") if os.path.exists(config.DB_PATH + ext))


@router.get("/admin/storage")
def storage(admin: dict = Depends(require_admin)):
    with db.get_conn() as conn:
        chats_ = sorted(_overview(conn), key=lambda c: -c["bytes"])
        total = files.usage_bytes(conn)
        missing = conn.execute("SELECT COUNT(*) FROM attachments WHERE missing = 1").fetchone()[0]
        pending = conn.execute("SELECT COUNT(*) FROM attachments WHERE message_id IS NULL").fetchone()[0]
        quota = settings.get("share_folder_quota_gb", conn)
        retention = _retention_report(conn)
    online = files.is_online()
    return {"dbBytes": _db_bytes(), "filesBytes": total, "folder": files.configured(), "quotaGb": quota,
            "missing": missing, "pending": pending, "retention": retention,
            "storage": files.storage_info(), "waitingJobs": files.pending_jobs(),
            "deletedBytes": _dir_bytes(os.path.join(files.share_root(), files.DELETED)) if online else None,
            "chats": [{"name": c["name"], "kind": c["kind"], "bytes": c["bytes"], "files": c["files"]} for c in chats_]}


def _dir_bytes(path: str) -> int:
    n = 0
    for dirpath, _, filenames in os.walk(path):
        for f in filenames:
            try:
                n += os.path.getsize(os.path.join(dirpath, f))
            except OSError:
                pass
    return n


def _retention_report(conn) -> dict:
    """How many messages (counts only) are older than 30 / 90 / 365 days, and what the retention setting removes."""
    now = config.utcnow()
    out = {"olderThan": {}}
    for days in (30, 90, 365):
        cutoff = config.iso(now - timedelta(days=days))
        out["olderThan"][str(days)] = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE kind != 'system' AND created_at < ?", (cutoff,)).fetchone()[0]
    days = settings.get("message_retention_days", conn)
    out["settingDays"] = days
    if days:
        kinds = "('group','direct','personal')" if settings.get("retention_includes_personal", conn) else "('group','direct')"
        cutoff = config.iso(now - timedelta(days=days))
        out["wouldRemove"] = conn.execute(
            f"SELECT COUNT(*) FROM messages m JOIN conversations c ON c.id = m.conversation_id WHERE m.created_at < ? "
            f"AND c.kind IN {kinds} AND m.pinned_at IS NULL", (cutoff,)).fetchone()[0]
    return out


# ---------- storage clean-up (§15.10): names only for chats the admin is in ----------
TYPE_SQL = {
    "photos": "a.mime IN ('image/png','image/jpeg','image/gif','image/webp')",
    "videos": "a.mime LIKE 'video/%'",
    "audio": "(a.voice = 1 OR a.mime LIKE 'audio/%')",
    "documents": "(a.mime = 'application/pdf' OR a.mime LIKE 'text/%' OR a.mime LIKE 'application/vnd.%' OR a.mime = 'application/msword')",
}


def _type_label(a) -> str:
    if a["voice"]:
        return "Voice message"
    m = a["mime"]
    if files.is_image(m):
        return "Photo"
    if m.startswith("video/"):
        return "Video"
    if m.startswith("audio/"):
        return "Audio"
    if m == "application/pdf":
        return "PDF"
    if m.startswith("text/") or m.startswith("application/vnd.") or m == "application/msword":
        return "Document"
    return "File"


@router.get("/admin/storage/usage")
def storage_usage(admin: dict = Depends(require_admin)):
    with db.get_conn() as conn:
        by_type = {}
        other = "NOT (" + " OR ".join(TYPE_SQL.values()) + ")"
        for k, cond in list(TYPE_SQL.items()) + [("other", other)]:
            by_type[k] = conn.execute(f"SELECT COALESCE(SUM(a.size), 0) FROM attachments a WHERE a.admin_deleted_at IS NULL "
                                      f"AND {cond}").fetchone()[0]
        chats_ = sorted(_overview(conn), key=lambda c: -c["bytes"])
    return {"byType": by_type, "chats": [{"name": c["name"], "kind": c["kind"], "bytes": c["bytes"], "files": c["files"]} for c in chats_]}


@router.get("/admin/storage/files")
def storage_files(type: str | None = None, olderThanDays: int = 0, limit: int = 100, admin: dict = Depends(require_admin)):
    limit = max(1, min(limit, 100))
    where = ["a.message_id IS NOT NULL", "a.admin_deleted_at IS NULL"]
    args: list = []
    if type in TYPE_SQL:
        where.append(TYPE_SQL[type])
    elif type == "other":
        where.append("NOT (" + " OR ".join(TYPE_SQL.values()) + ")")
    if olderThanDays > 0:
        where.append("a.created_at < ?")
        args.append(config.iso(config.utcnow() - timedelta(days=olderThanDays)))
    with db.get_conn() as conn:
        rows = conn.execute("SELECT a.*, m.user_id AS sender, m.created_at AS sent_at, c.kind AS ckind, c.name AS cname, "
                            "c.created_by AS cowner FROM attachments a JOIN messages m ON m.id = a.message_id "
                            "JOIN conversations c ON c.id = a.conversation_id WHERE " + " AND ".join(where) +
                            " ORDER BY a.size DESC LIMIT ?", (*args, limit)).fetchall()
        mine = {r["conversation_id"] for r in conn.execute("SELECT conversation_id FROM members WHERE user_id = ?", (admin["id"],))}
        overview_names = {c["id"]: c["name"] for c in _overview(conn)}
        out = []
        for a in rows:
            member = a["conversation_id"] in mine
            # for chats the admin isn't in: only the type and size (§15.10)
            out.append({"id": a["id"], "chat": overview_names.get(a["conversation_id"], "Chat") if member else None,
                        "sender": (chats.shown_name(chats.user_row(conn, a["sender"])) if a["sender"] else "") if member else None,
                        "sentAt": a["sent_at"] if member else None, "type": _type_label(a), "size": a["size"],
                        "name": a["original_name"] if member else None, "yourChat": member,
                        "disappearing": files.DISAPPEARING in a["rel_path"].split("/")})
    return {"files": out}


@router.post("/admin/storage/delete")
def storage_delete(body: dict = Body(...), admin: dict = Depends(require_admin)):
    ids = body.get("attachmentIds")
    if not isinstance(ids, list) or not ids or len(ids) > 200 or not all(isinstance(i, str) for i in ids):
        raise HTTPException(422, "Choose up to 200 files.")
    out = chats.Outbox()
    done = 0
    with db.get_conn() as conn:
        for aid in dict.fromkeys(ids):
            a = conn.execute("SELECT * FROM attachments WHERE id = ? AND message_id IS NOT NULL AND admin_deleted_at IS NULL",
                             (aid,)).fetchone()
            if a is None:
                continue
            if files.DISAPPEARING in a["rel_path"].split("/"):
                files.remove_now(a["rel_path"])          # never kept in _deleted
            else:
                files.move_to_deleted(a["rel_path"])
            files.remove_thumb(aid)
            conn.execute("UPDATE attachments SET admin_deleted_at = ? WHERE id = ?", (config.now_iso(), aid))
            db.audit(conn, "file_deleted_by_admin", None, a["conversation_id"], admin["id"])
            msg = chats.message_row(conn, a["message_id"])
            chats.publish_message(conn, out, chats.conv_row(conn, a["conversation_id"]), msg, "message_updated")
            done += 1
    out.flush()
    return {"deleted": done}


@router.post("/admin/storage/empty-deleted")
def storage_empty_deleted(admin: dict = Depends(require_admin)):
    d = os.path.join(files.share_root(), files.DELETED)
    if os.path.isdir(d):
        for entry in os.listdir(d):
            p = os.path.join(d, entry)
            shutil.rmtree(p, ignore_errors=True) if os.path.isdir(p) else os.remove(p)
    with db.get_conn() as conn:
        db.audit(conn, "deleted_emptied", None, None, admin["id"])
    return {"ok": True}


@router.post("/admin/storage/thumbs-delete")
def storage_thumbs_delete(admin: dict = Depends(require_admin)):
    d = os.path.join(files.share_root(), files.THUMBS)
    shutil.rmtree(d, ignore_errors=True)
    return {"ok": True}


# ---------- shared folders (§12) ----------
@router.get("/admin/share-dirs")
def share_dirs(path: str = "", admin: dict = Depends(require_admin)):
    return shared_folders.browse_share(path)


def _chat_choices(conn, admin_id: str) -> list[dict]:
    """Every chat a folder can be shared into: groups, direct chats and personal rooms (names only)."""
    out = []
    for c in _overview(conn):
        if c["kind"] == "personal":
            owner = conn.execute("SELECT created_by FROM conversations WHERE id = ?", (c["id"],)).fetchone()
            name = "📌 My room" if owner and owner["created_by"] == admin_id else "📌 " + c["name"]
        elif c["kind"] == "group":
            name = ("🏠 " if c.get("household") else "👥 ") + c["name"]
        else:
            name = "💬 " + c["name"].removeprefix("Direct: ")
        out.append({"id": c["id"], "kind": c["kind"], "name": name})
    order = {"personal": 0, "group": 1, "direct": 2}
    out.sort(key=lambda c: (order.get(c["kind"], 3), c["name"] != "📌 My room", c["name"].lower()))
    return out


@router.get("/admin/folders")
def list_folders(admin: dict = Depends(require_admin)):
    with db.get_conn() as conn:
        choices = _chat_choices(conn, admin["id"])
        names = {c["id"]: c["name"] for c in choices}
        folders = []
        for f in conn.execute("SELECT * FROM folders ORDER BY label COLLATE NOCASE").fetchall():
            links = conn.execute("SELECT id, conversation_id, mode FROM folder_links WHERE folder_id = ?", (f["id"],)).fetchall()
            folders.append({"id": f["id"], "path": f["path"], "label": f["label"], "announce": bool(f["announce"]),
                            "lastScanAt": f["last_scan_at"],
                            "exists": os.path.isdir(os.path.join(shared_folders.share_root(), f["path"])),
                            "chats": sorted([{"linkId": l["id"], "id": l["conversation_id"], "name": names.get(l["conversation_id"], ""),
                                              "mode": l["mode"]} for l in links], key=lambda x: x["name"].lower())})
        return {"folders": folders, "chats": choices}


def _clean_label(value, fallback: str) -> str:
    label = " ".join(str(value or "").split())[:60]
    return label or fallback


def _wanted_chats(conn, raw) -> dict:
    """{conversation id: mode} from [{"id", "mode"}], every chat checked to exist."""
    if not isinstance(raw, list) or not raw:
        raise HTTPException(422, "Pick at least one chat to share it with (or stop sharing it).")
    if len(raw) > 200:
        raise HTTPException(422, "Too many chats.")
    wanted = {}
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise HTTPException(422, "Each chat needs an id.")
        mode = item.get("mode", "ro")
        if mode not in ("ro", "rw"):
            raise HTTPException(422, "Read-only (ro) or read-write (rw).")
        if chats.conv_row(conn, item["id"]) is None:
            raise HTTPException(404, "One of those chats doesn't exist any more.")
        wanted[item["id"]] = mode
    return wanted


def _apply_links(conn, out, folder, wanted: dict, admin: dict, new_label: str | None = None) -> None:
    """Make the folder's chats exactly `wanted`: add, remove and change access, with a note in each chat
    (chats it joins hear its new name, chats it leaves the one they knew)."""
    have = {l["conversation_id"]: l for l in conn.execute("SELECT * FROM folder_links WHERE folder_id = ?", (folder["id"],))}
    for cid, mode in wanted.items():
        conv = chats.conv_row(conn, cid)
        if cid not in have:
            n = conn.execute("SELECT COUNT(*) FROM folder_links WHERE conversation_id = ?", (cid,)).fetchone()[0]
            if n >= 10:
                raise HTTPException(409, "A chat can have at most 10 shared folders.")
            conn.execute("INSERT INTO folder_links (id, folder_id, conversation_id, mode, created_by, created_at) "
                         "VALUES (?, ?, ?, ?, ?, ?)", (db.new_id(), folder["id"], cid, mode, admin["id"], config.now_iso()))
            chats.add_system(conn, out, conv, {"event": "folder_added", "actor": admin["id"], "label": new_label or folder["label"]})
            db.audit(conn, "folder_shared", None, cid, admin["id"])
        elif have[cid]["mode"] != mode:
            conn.execute("UPDATE folder_links SET mode = ? WHERE id = ?", (mode, have[cid]["id"]))
        else:
            continue
        chats.conversation_members_event(conn, out, conv)
    for cid, link in have.items():
        if cid not in wanted:
            conn.execute("DELETE FROM folder_links WHERE id = ?", (link["id"],))
            conv = chats.conv_row(conn, cid)
            chats.add_system(conn, out, conv, {"event": "folder_removed", "label": folder["label"]})
            chats.conversation_members_event(conn, out, conv)
            db.audit(conn, "folder_unshared", None, cid, admin["id"])


@router.post("/admin/folders", status_code=201)
def add_folder(body: dict = Body(...), admin: dict = Depends(require_admin)):
    """Share a /share folder into one or more chats: {path, label?, announce?, chats: [{id, mode}]}."""
    rel = shared_folders.check_mapping(body.get("path") or "")
    label = _clean_label(body.get("label"), os.path.basename(rel))
    out = chats.Outbox()
    with db.get_conn() as conn:
        if conn.execute("SELECT 1 FROM folders WHERE path = ?", (rel,)).fetchone():
            raise HTTPException(409, "That folder is already shared. Change its chats in the list instead.")
        wanted = _wanted_chats(conn, body.get("chats"))
        fid = db.new_id()
        conn.execute("INSERT INTO folders (id, path, label, announce, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                     (fid, rel, label, 0 if body.get("announce") is False else 1, admin["id"], config.now_iso()))
        folder = conn.execute("SELECT * FROM folders WHERE id = ?", (fid,)).fetchone()
        _apply_links(conn, out, folder, wanted, admin)
        shared_folders.scan_one(conn, out, folder)
    out.flush()
    return list_folders(admin)


@router.patch("/admin/folders/{fid}")
def patch_folder(fid: str, body: dict = Body(...), admin: dict = Depends(require_admin)):
    """{label?, announce?, chats?: [{id, mode}]} — `chats` replaces the whole list."""
    out = chats.Outbox()
    with db.get_conn() as conn:
        f = conn.execute("SELECT * FROM folders WHERE id = ?", (fid,)).fetchone()
        if f is None:
            raise HTTPException(404, "That shared folder doesn't exist.")
        if "chats" in body:          # first, so a chat it leaves is told the name it knew
            new = _clean_label(body.get("label"), "") if "label" in body else None
            _apply_links(conn, out, f, _wanted_chats(conn, body["chats"]), admin, new or None)
        if "label" in body:
            label = _clean_label(body["label"], "")
            if not label:
                raise HTTPException(422, "Give it a name.")
            if label != f["label"]:
                conn.execute("UPDATE folders SET label = ? WHERE id = ?", (label, fid))
                for l in conn.execute("SELECT conversation_id FROM folder_links WHERE folder_id = ?", (fid,)).fetchall():
                    chats.conversation_members_event(conn, out, chats.conv_row(conn, l["conversation_id"]))
        if "announce" in body:
            conn.execute("UPDATE folders SET announce = ? WHERE id = ?", (1 if body["announce"] else 0, fid))
    out.flush()
    return list_folders(admin)


@router.delete("/admin/folders/{fid}")
def delete_folder(fid: str, admin: dict = Depends(require_admin)):
    """Stops sharing it everywhere; the folder and its files stay where they are."""
    out = chats.Outbox()
    with db.get_conn() as conn:
        f = conn.execute("SELECT * FROM folders WHERE id = ?", (fid,)).fetchone()
        if f is None:
            raise HTTPException(404, "That shared folder doesn't exist.")
        for l in conn.execute("SELECT conversation_id FROM folder_links WHERE folder_id = ?", (fid,)).fetchall():
            conv = chats.conv_row(conn, l["conversation_id"])
            chats.add_system(conn, out, conv, {"event": "folder_removed", "label": f["label"]})
            chats.conversation_members_event(conn, out, conv)
            db.audit(conn, "folder_unshared", None, l["conversation_id"], admin["id"])
        conn.execute("DELETE FROM folders WHERE id = ?", (fid,))
    out.flush()
    return list_folders(admin)


@router.post("/admin/storage/check")
def storage_check(admin: dict = Depends(require_admin)):
    files.require_online()
    with db.get_conn() as conn:
        missing = files.check_missing(conn)
        thumbs = files.remove_orphan_thumbs(conn)
    files.remove_stale_parts(3600)
    return {"missing": missing, "thumbsRemoved": thumbs}


@router.get("/admin-storage-download-db")
def download_backup(includeFiles: bool = False, admin: dict = Depends(require_admin)):
    """The database (a consistent copy), optionally with every chat file. Admins can read this outside the app —
    DOCS says so (§4.1)."""
    if includeFiles:
        files.require_online()
    os.makedirs(config.DATA_DIR, exist_ok=True)
    fd, tmp_db = tempfile.mkstemp(prefix=".backup-", suffix=".db", dir=config.DATA_DIR)
    os.close(fd)
    fd, tmp_zip = tempfile.mkstemp(prefix=".backup-", suffix=".zip", dir=config.DATA_DIR)
    os.close(fd)
    try:
        src = sqlite3.connect(config.DB_PATH)
        try:
            db_core.snapshot(src, tmp_db, after=settings.REGISTRY.scrub_secrets)   # never the relay secrets
        finally:
            src.close()
        skip_files = disappearing.strip_from_copy(tmp_db)      # the app's backup never holds disappearing messages
        members = [(tmp_db, "chat.db")]
        if includeFiles:
            root = files.share_root()

            def prune(dirpath, dirnames):
                rel_dir = os.path.relpath(dirpath, root)
                if rel_dir.split(os.sep)[0] in (files.THUMBS, files.DELETED) or files.DISAPPEARING in rel_dir.split(os.sep):
                    dirnames[:] = []
                    return True

            def keep(full, rel):
                f = os.path.basename(full)
                if f.startswith(".upload-") or ("/" not in rel and f.startswith(".")):
                    return False          # the marker belongs to this install's folder, not to a backup
                return not os.path.islink(full) and rel not in skip_files

            members = itertools.chain(members, ((full, name, zipfile.ZIP_STORED) for full, name in
                                                backup_core.walk(root, prefix="files/", prune=prune, keep=keep)))
        backup_core.write_zip(tmp_zip, members)
    finally:
        os.remove(tmp_db)
    with db.get_conn() as conn:
        db.audit(conn, "backup_downloaded", None, None, admin["id"])
    name = backup_core.file_name("household-chat-backup", ".zip", now=config.local_now(), fmt="%Y-%m-%d")
    return backup_core.send_file(tmp_zip, name, backup_core.ZIP_MEDIA_TYPE)


def _check_db(path: str) -> None:
    try:
        c = sqlite3.connect(path)
        ok = c.execute("PRAGMA integrity_check").fetchone()[0]
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        c.close()
    except sqlite3.Error:
        raise HTTPException(422, "The database in the backup is damaged.")
    if ok != "ok" or not {"users", "conversations", "messages", "members"} <= tables:
        raise HTTPException(422, "That isn't a Household Chat backup.")


@router.post("/admin-storage-import-db")
async def restore(request: Request, admin: dict = Depends(require_admin)):
    """The backup .zip as the request body. Replaces the database (and the files, if the zip has them)."""
    tmp = await backup_core.receive(request, config.DATA_DIR, prefix=".restore-", suffix=".zip", max_bytes=RESTORE_MAX,
                                    too_big=lambda: HTTPException(413, "That backup is too big."))
    try:
        return await run_in_threadpool(_restore_from, tmp, admin)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _restore_from(tmp: str, admin: dict) -> dict:
    if not zipfile.is_zipfile(tmp):
        raise HTTPException(422, "That isn't a Household Chat backup (.zip).")
    with zipfile.ZipFile(tmp) as z:
        names = z.namelist()
        if "chat.db" not in names:
            raise HTTPException(422, "The backup has no chat.db.")

        def expected(info):
            n = info.filename
            if n != "chat.db" and not n.startswith("files/") and not n.startswith("chat_photos/"):   # (chat_photos/ in older backups: ignored)
                raise HTTPException(422, "Unexpected file in the backup.")

        backup_core.check_members(z, check=expected, check_first=True,
                                  unsafe=lambda _n: HTTPException(422, "Unsafe path in the backup."))
        fd, new_db = tempfile.mkstemp(prefix=".restore-", suffix=".db", dir=config.DATA_DIR)
        os.close(fd)
        with z.open("chat.db") as src, open(new_db, "wb") as dst:
            shutil.copyfileobj(src, dst)
        try:
            _check_db(new_db)
            if any(n.startswith("files/") and not n.endswith("/") for n in names):
                files.require_online()          # never half-restore: the files need the folder
        except HTTPException:
            os.remove(new_db)
            raise
        # the chat files folder and its marker belong to this install, not to the backup
        with db.get_conn() as conn:
            keep = [(r["key"], r["value"]) for r in conn.execute(
                "SELECT key, value FROM app_settings WHERE key IN ('files_path', ?)", (files.STORE_KEY,))]
            saved_secrets = settings.REGISTRY.saved_secrets(conn)     # the relay secrets stay this install's
        db.RESTORING.set()
        try:
            hub.reset()
            calls.reset()                    # calls that were on are dropped; the restored database's are closed below
            time.sleep(0.5)          # let requests already running finish
            for ext in ("-wal", "-shm"):
                try:
                    os.remove(config.DB_PATH + ext)
                except OSError:
                    pass
            os.replace(new_db, config.DB_PATH)
            db.init_db()
            with db.get_conn() as conn:
                conn.execute("DELETE FROM app_settings WHERE key IN ('files_path', ?)", (files.STORE_KEY,))
                for k, v in keep:
                    db.set_setting(conn, k, v)
                settings.REGISTRY.keep_secrets(saved_secrets, conn)
            settings.invalidate()
            disappearing.run_expiry()        # before anything is served again
            calls.close_unfinished()
        finally:
            db.RESTORING.clear()
        restored_files = 0
        has_files = any(n.startswith("files/") and not n.endswith("/") for n in names)
        root = files.share_root() if has_files else ""
        for n in names:
            if (not n.startswith("files/") or n.endswith("/") or f"/{files.DISAPPEARING}/" in n
                    or n == f"files/{files.MARKER}"):
                continue          # an older backup's marker must never replace this install's
            rel = n[len("files/"):]
            dest = os.path.realpath(os.path.join(root, rel))
            if not dest.startswith(root + os.sep):
                continue
            backup_core.copy_out(z, n, dest)
            restored_files += 1
    with db.get_conn() as conn:
        files.check_missing(conn)
        db.audit(conn, "backup_restored", None, None, admin["id"])
    try:
        avatars.sync_blocking()          # the restored people's photos follow Home Assistant again
    except Exception:
        logger.exception("Syncing photos after a restore failed")
    return {"ok": True, "files": restored_files}
