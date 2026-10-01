"""Admin: users (enable and set up, disable, reset), App settings, backup and restore (SPEC §4, §9.1).
Admins can never open anyone's vault through these routes."""
import io
import json
import os
import shutil
import sqlite3
import tempfile
import time
import zipfile

from fastapi import APIRouter, Body, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response

from .. import alerts, config, copies, db, ha_client, ha_notify, ha_people, kdbx, service, sessions, settings
from ..auth import require_admin, require_user
from .common import Strict

router = APIRouter(prefix="/api", tags=["admin"])
APP_VERSION = "2.0.0"
_last_sync = {"t": 0.0}


def _maybe_sync():
    if time.time() - _last_sync["t"] > 300:
        _last_sync["t"] = time.time()
        try:
            ha_client.sync_users_blocking()
        except Exception:
            pass


def ha_person_json(user_id: str) -> dict:
    """What Home Assistant says about the person (Settings → People): their person and phones."""
    p = ha_people.person_for(user_id)
    return {"known": ha_people.known(), "person": p["entityId"] if p else None, "personName": p["name"] if p else None,
            "phones": [{"label": ph["label"], "service": f"notify.{ph['service']}" if ph["service"] else None,
                        "tracker": ph["tracker"]} for ph in (p or {}).get("phones", [])]}


@router.get("/admin/users")
def admin_users(refresh: bool = False, admin: dict = Depends(require_admin)):
    """Everyone known, with their phones from Home Assistant and any extra notify services. `refresh=1` reads
    Home Assistant's people again first (no DB connection or vault lock is held meanwhile)."""
    _maybe_sync()
    if refresh:
        ha_people.refresh_blocking(True)
    with db.get_conn() as conn:
        rows = conn.execute("SELECT u.*, (SELECT COUNT(*) FROM vault_members m WHERE m.user_id = u.id) AS vaults "
                            "FROM users u ORDER BY u.name COLLATE NOCASE").fetchall()
        notify = {r["id"]: ha_notify.assigned_services(conn, {"id": r["id"]}) for r in rows}
    return {"users": [{"id": r["id"], "name": r["name"], "username": r["username"], "person": r["ha_person"],
                       "disabled": bool(r["disabled"]), "status": r["status"], "vaultCount": r["vaults"],
                       "lastSeen": r["last_seen"], "lastUnlock": r["last_unlock"], "you": r["id"] == admin["id"],
                       "blockedUntil": r["unlock_blocked_until"],
                       "notify": notify[r["id"]],                  # extra services added here
                       "ha": ha_person_json(r["id"])} for r in rows],   # phones from Home Assistant
            "unlocked": {u: len(sessions.user_sessions(u)) > 0 for u in [r["id"] for r in rows]}}


# ---------- notify services per person (as in Household Todo) ----------
MAX_SERVICES_PER_USER = 5


@router.get("/admin/notify-services")
def notify_services(refresh: bool = False, admin: dict = Depends(require_admin)):
    """Notify actions and entities Home Assistant has, for the picker."""
    try:
        return {"available": True, "error": None, **ha_notify.list_notify_services_blocking(force=refresh)}
    except ha_notify.NotifyListError as e:
        return {"available": False, "error": f"Couldn't read the notify services from Home Assistant: {e}",
                "services": [], "entities": []}


def _service(value) -> str:
    try:
        return ha_notify.normalize_service(value)
    except ValueError as e:
        raise HTTPException(422, str(e))


def _known_user(conn, uid: str):
    row = service.user_row(conn, uid)
    if row is None:
        raise HTTPException(404, "That person isn't known to Household Vault.")
    return row


@router.post("/admin/users/{uid}/notify", status_code=201)
def add_notify(uid: str, body: dict = Body(...), admin: dict = Depends(require_admin)):
    svc = _service(body.get("service"))
    with db.get_conn() as conn:
        _known_user(conn, uid)
        current = ha_notify.assigned_services(conn, {"id": uid})
        if svc not in current and len(current) >= MAX_SERVICES_PER_USER:
            raise HTTPException(422, f"At most {MAX_SERVICES_PER_USER} notify services per person.")
        conn.execute("INSERT OR IGNORE INTO user_notify (user_id, service, created_at, created_by) VALUES (?, ?, ?, ?)",
                     (uid, svc, config.now_iso(), admin.get("username") or admin["id"]))
        return {"notify": ha_notify.assigned_services(conn, {"id": uid})}


@router.delete("/admin/users/{uid}/notify/{svc}")
def remove_notify(uid: str, svc: str, admin: dict = Depends(require_admin)):
    svc = _service(svc)
    with db.get_conn() as conn:
        _known_user(conn, uid)
        if not conn.execute("DELETE FROM user_notify WHERE user_id = ? AND service = ?", (uid, svc)).rowcount:
            raise HTTPException(404, "That service isn't assigned to them.")
        return {"notify": ha_notify.assigned_services(conn, {"id": uid})}


@router.post("/admin/users/{uid}/notify/test")
def test_notify(uid: str, admin: dict = Depends(require_admin)):
    """A short test message to their phones (from Home Assistant, while they're active here) and every extra
    service — sent now, so the result can be shown."""
    with db.get_conn() as conn:
        row = _known_user(conn, uid)
        services = alerts.services_for(conn, row)      # phones + extras
    # the DB connection is closed before Home Assistant is called
    if not services:
        if ha_people.phones_for(uid) and not alerts.reachable(row):
            raise HTTPException(409, f"{row['name']} isn't enabled and set up in Household Vault, so their phone "
                                     "doesn't get anything yet.")
        raise HTTPException(409, f"{row['name']} has no phone linked in Home Assistant (Settings → People → "
                                 f"{row['name']} → Track device) and no extra notify service here.")
    results = ha_notify.send_to_services(services, alerts.TITLE, f"Test from Household Vault for {row['name']} — "
                                         "alerts will arrive like this.")
    return {"results": [{"service": f"notify.{k}", "ok": v, "hint": "" if v else ha_notify.explain_failure(k)}
                        for k, v in results.items()]}


@router.post("/users/{uid}/setup")
def setup_user(uid: str, admin: dict = Depends(require_admin)):
    with db.get_conn() as conn:
        one_time = service.admin_setup(conn, uid, admin["id"])
    return {"oneTimePassword": one_time}


class UserPatch(Strict):
    disabled: bool


@router.patch("/users/{uid}")
def patch_user(uid: str, body: UserPatch, admin: dict = Depends(require_admin)):
    with db.get_conn() as conn:
        service.set_disabled(conn, uid, body.disabled, admin["id"])
    return {"ok": True}


@router.post("/users/{uid}/reset")
def reset_user(uid: str, admin: dict = Depends(require_admin)):
    with db.get_conn() as conn:
        if service.user_row(conn, uid) is None:
            raise HTTPException(404, "That person isn't known to Household Vault.")
        service.reset_user(conn, uid, admin["id"])
    return {"ok": True}


@router.post("/users/{uid}/unblock")
def unblock(uid: str, admin: dict = Depends(require_admin)):
    with db.get_conn() as conn:
        conn.execute("UPDATE users SET unlock_failures = 0, unlock_blocked_until = NULL WHERE id = ?", (uid,))
    return {"ok": True}


@router.get("/admin/settings")
def get_settings(admin: dict = Depends(require_admin)):
    with db.get_conn() as conn:
        values = settings.all_values(conn)
        tuned = {"iterations": db.get_setting(conn, "argon2_iterations"),
                 "memoryMiB": config.KDF_MEMORY_KIB // 1024,
                 "secondsPerIteration": db.get_setting(conn, "argon2_seconds_per_iteration")}
        pc = copies.admin_status(conn)
    return {"values": values, "defaults": settings.DEFAULTS, "labels": settings.LABELS, "kdf": tuned,
            "version": APP_VERSION, "personalCopies": pc, **sessions.stats()}


@router.put("/admin/settings")
def put_settings(body: dict = Body(...), admin: dict = Depends(require_admin)):
    unknown = [k for k in body if k not in settings.DEFAULTS]
    if unknown:
        raise HTTPException(422, f"Unknown setting: {', '.join(unknown)}")
    before = settings.all_values()
    try:
        values = settings.update(body)
    except Exception as e:
        raise HTTPException(422, str(e).splitlines()[0] if str(e) else "Invalid value.")
    if values["personal_copies"] != before["personal_copies"]:
        with db.get_conn() as conn:
            copies.setting_changed(conn, values["personal_copies"])      # off: the files are deleted
    return {"values": values}


# ---------- backup and restore ----------
REQUIRED_TABLES = {"users", "vaults", "vault_members", "vault_keys", "vault_versions", "audit_log", "app_settings"}


@router.get("/admin-storage-download-db")
def download_backup(admin: dict = Depends(require_admin)):
    """One zip: a consistent vault.db snapshot plus every (encrypted) vault file."""
    buf = io.BytesIO()
    with tempfile.TemporaryDirectory() as tmp:
        snap = os.path.join(tmp, "vault.db")
        src = sqlite3.connect(config.DB_PATH)
        dst = sqlite3.connect(snap)
        with dst:
            src.backup(dst)
        src.close()
        dst.close()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(snap, "vault.db")
            for root, _dirs, files in os.walk(config.VAULT_DIR):
                for f in files:
                    if f.endswith(".kdbx"):
                        full = os.path.join(root, f)
                        z.write(full, os.path.join("vaults", os.path.relpath(full, config.VAULT_DIR)))
            cdir = copies.copy_dir()                    # each person's one-file copy (their master password)
            if os.path.isdir(cdir):
                for f in sorted(os.listdir(cdir)):
                    if f.endswith(".kdbx"):
                        z.write(os.path.join(cdir, f), "copies/" + f)
    with db.get_conn() as conn:
        db.audit(conn, "backup_downloaded", None, None, admin["id"])
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="household-vault-backup-{stamp}.zip"'})


@router.post("/admin-storage-import-db")
async def import_backup(file: UploadFile = File(...), admin: dict = Depends(require_admin)):
    raw = await file.read()
    try:
        z = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        raise HTTPException(422, "That isn't a Household Vault backup (.zip).")
    names = z.namelist()
    if "vault.db" not in names:
        raise HTTPException(422, "The backup has no vault.db.")
    for n in names:
        if n.startswith("/") or ".." in n.split("/") or not (n == "vault.db" or
                                                           (n.startswith("vaults/") and n.endswith(".kdbx")) or
                                                           (n.startswith("copies/") and n.count("/") == 1 and n.endswith(".kdbx"))):
            raise HTTPException(422, f"Unexpected file in the backup: {n}")
    with tempfile.TemporaryDirectory() as tmp:
        dbfile = os.path.join(tmp, "vault.db")
        with open(dbfile, "wb") as f:
            f.write(z.read("vault.db"))
        try:
            c = sqlite3.connect(dbfile)
            ok = c.execute("PRAGMA integrity_check").fetchone()[0]
            tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            c.close()
        except sqlite3.DatabaseError:
            raise HTTPException(422, "The database in the backup is damaged.")
        if ok != "ok" or not REQUIRED_TABLES <= tables:
            raise HTTPException(422, "The database in the backup is damaged or from another app.")
        for n in names:
            if n.endswith(".kdbx") and not kdbx.is_kdbx(z.read(n)[:12]):
                raise HTTPException(422, f"{n} isn't a KeePass file.")
        # what the restore will undo: removals and password changes since the backup
        with db.get_conn() as conn:
            c = sqlite3.connect(dbfile)
            last = c.execute("SELECT MAX(created_at) FROM audit_log").fetchone()[0] or ""
            c.close()
            undone = [dict(r) for r in conn.execute(
                "SELECT a.action, a.created_at, v.name AS vault, u.name AS person FROM audit_log a "
                "LEFT JOIN vaults v ON v.id = a.vault_id LEFT JOIN users u ON u.id = a.user_id WHERE a.created_at > ? "
                "AND a.action IN ('unshared','left','user_disabled','user_reset','rekeyed','password_changed',"
                "'emergency_revoked','emergency_denied','quick_unlock_added','guest_wifi_removed') "
                "ORDER BY a.created_at", (last,))]
        sessions.end_all()
        shutil.rmtree(config.VAULT_DIR, ignore_errors=True)
        os.makedirs(config.VAULT_DIR, exist_ok=True)
        shutil.rmtree(copies.copy_dir(), ignore_errors=True)
        for n in names:
            if n.startswith("copies/"):
                os.makedirs(copies.copy_dir(), exist_ok=True)
                with open(os.path.join(copies.copy_dir(), n.split("/", 1)[1]), "wb") as f:
                    f.write(z.read(n))
            elif n.endswith(".kdbx"):
                dest = os.path.join(config.VAULT_DIR, os.path.relpath(n, "vaults"))
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, "wb") as f:
                    f.write(z.read(n))
        for ext in ("", "-wal", "-shm"):
            try:
                os.remove(config.DB_PATH + ext)
            except FileNotFoundError:
                pass
        shutil.copyfile(dbfile, config.DB_PATH)
    db.init_db()
    with open(os.path.join(config.DATA_DIR, "restore_notes.json"), "w") as f:
        json.dump({"restoredAt": config.now_iso(), "backupUpTo": last, "undone": undone}, f)
    with db.get_conn() as conn:
        conn.execute("UPDATE user_copies SET stale = 1")      # rewritten from the restored vaults at each unlock
        db.audit(conn, "backup_restored", None, None, admin["id"])
        everyone = [r["id"] for r in conn.execute("SELECT id FROM users WHERE disabled = 0 AND status != 'none'")]
        alerts.send(conn, everyone, "An admin restored a Household Vault backup. Changes made after it was taken "
                    "are gone — check your vaults when you next unlock.", kind="admin")
    return {"ok": True, "undone": undone}


@router.get("/restore-notes")
def restore_notes(user: dict = Depends(require_user)):
    path = os.path.join(config.DATA_DIR, "restore_notes.json")
    if not os.path.exists(path):
        return {"notes": None}
    with open(path) as f:
        return {"notes": json.load(f)}
