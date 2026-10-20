"""Admin: users (enable and set up, disable, reset), App settings, backup and restore (SPEC §4, §9.1).
Admins can never open anyone's vault through these routes.

Security review 2026-10: every page an ingress app serves shares Home Assistant's origin with every other
app, custom card and proxied site, so a request that only needs the admin's HA identity could be sent by any
of them. Once an admin is set up, every admin route therefore needs their **unlocked session** (the token lives
only in the Vault tab's memory), and the ones that hand out access or the data — set up, reset, backup download,
restore — need their **master password** again (counted like an unlock). Before the admin is set up
(nobody has a session yet) the people routes still work, so the first admin can set up themselves and others."""
import itertools
import json
import logging
import os
import shutil
import sqlite3
import tempfile
import threading
import time

from fastapi import APIRouter, Body, Depends, File, Form, Header, HTTPException, UploadFile
from pydantic import Field
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from .. import alerts, config, copies, db, ha_client, kdbx, reminders, service, sessions, settings
from ..db import GATE
from ..common import backup_core, db_core, ha_notify, ha_people, people_admin
from ..auth import require_admin, require_user
from .common import Strict

NEEDS_SETUP = "Set yourself up (Enable and set up on your own row) and unlock first."


def _active(conn, admin: dict) -> bool:
    u = service.user_row(conn, admin["id"])
    return u is not None and u["status"] == "active" and not u["disabled"]


def admin_session(x_vault_session: str | None = Header(default=None), admin: dict = Depends(require_admin)) -> dict:
    """An admin, with their unlocked session once they are set up (401 "Locked." otherwise). Before they are set
    up — or while their own access is off — there is no session to show, and the people routes still work."""
    with db.get_conn() as conn:
        active = _active(conn, admin)
    if active:
        s = sessions.get(x_vault_session, admin)
        if s.must_change:
            raise HTTPException(409, "Choose your own master password first.")
        return dict(admin, session=s)
    return dict(admin, session=None)


def confirm_master(conn, admin: dict, password: str | None, bootstrap_ok: bool = False) -> None:
    """The admin's master password, checked against their open Personal vault (counted and blocked like an
    unlock). Before the admin is set up there is no password: allowed only where `bootstrap_ok` (setting people
    up), 403 everywhere else."""
    if admin.get("session") is None:
        if bootstrap_ok and not _active(conn, admin):
            return
        raise HTTPException(403, NEEDS_SETUP)
    personal = service.personal_of(conn, admin["id"])
    ov = sessions.get_open(personal["id"]) if personal else None
    service.check_master_password(conn, admin["id"], ov, password or "", "That isn't your master password.")

router = APIRouter(prefix="/api", tags=["admin"])
APP_VERSION = "2.3.1"
_last_sync = {"t": 0.0}


def _maybe_sync():
    if time.time() - _last_sync["t"] > 300:
        _last_sync["t"] = time.time()
        try:
            ha_client.sync_users_blocking()
        except Exception:
            pass


ha_person_json = people_admin.ha_person_json          # their person and phones (common/people_admin.py)


@router.get("/admin/users")
def admin_users(refresh: bool = False, admin: dict = Depends(admin_session)):
    """Everyone known, with their phones from Home Assistant and any extra notify services. `refresh=1` reads
    Home Assistant's people again first (no DB connection or vault lock is held meanwhile)."""
    _maybe_sync()
    people_admin.refresh_people(refresh)
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
def notify_services(refresh: bool = False, admin: dict = Depends(admin_session)):
    """Notify actions and entities Home Assistant has, for the picker."""
    return people_admin.notify_services(refresh)


_service = people_admin.clean_service


def _known_user(conn, uid: str):
    row = service.user_row(conn, uid)
    if row is None:
        raise HTTPException(404, "That person isn't known to Household Vault.")
    return row


@router.post("/admin/users/{uid}/notify", status_code=201)
def add_notify(uid: str, body: dict = Body(...), admin: dict = Depends(admin_session)):
    svc = _service(body.get("service"))
    with db.get_conn() as conn:
        row = _known_user(conn, uid)
        before = ha_notify.assigned_services(conn, {"id": uid})
        if svc not in before and uid != admin["id"]:
            # the person hears about it on what they already have (phones, earlier extras) — before the new one
            # can receive anything of theirs (security review 2026-10, #6: an admin adding their own device)
            alerts.send(conn, [uid], f"An admin added the notify service {svc} to your Household Vault alerts. "
                        "If that isn't a device of yours, tell them.", kind="admin")
        return {"notify": people_admin.add_service(conn, uid, svc, admin.get("username") or admin["id"], config.now_iso(),
                                                   limit=MAX_SERVICES_PER_USER)}


@router.delete("/admin/users/{uid}/notify/{svc}")
def remove_notify(uid: str, svc: str, admin: dict = Depends(admin_session)):
    svc = _service(svc)
    with db.get_conn() as conn:
        _known_user(conn, uid)
        return {"notify": people_admin.remove_service(conn, uid, svc, "That service isn't assigned to them.")}


@router.post("/admin/users/{uid}/notify/test")
def test_notify(uid: str, admin: dict = Depends(admin_session)):
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
    return {"results": people_admin.test_results(results, as_list=True)}


class ConfirmIn(Strict):
    password: str | None = Field(default=None, max_length=1000)      # the admin's master password (once set up)


@router.post("/users/{uid}/setup")
def setup_user(uid: str, body: ConfirmIn = Body(default=ConfirmIn()), admin: dict = Depends(admin_session)):
    with db.get_conn() as conn:
        confirm_master(conn, admin, body.password, bootstrap_ok=True)
        one_time = service.admin_setup(conn, uid, admin["id"])
    return {"oneTimePassword": one_time}


class UserPatch(Strict):
    disabled: bool


@router.patch("/users/{uid}")
def patch_user(uid: str, body: UserPatch, admin: dict = Depends(admin_session)):
    with db.get_conn() as conn:
        service.set_disabled(conn, uid, body.disabled, admin["id"])
    return {"ok": True}


@router.post("/users/{uid}/reset")
def reset_user(uid: str, body: ConfirmIn = Body(default=ConfirmIn()), admin: dict = Depends(admin_session)):
    with db.get_conn() as conn:
        if service.user_row(conn, uid) is None:
            raise HTTPException(404, "That person isn't known to Household Vault.")
        confirm_master(conn, admin, body.password)
        service.reset_user(conn, uid, admin["id"])
    return {"ok": True}


@router.post("/users/{uid}/unblock")
def unblock(uid: str, admin: dict = Depends(admin_session)):
    with db.get_conn() as conn:
        conn.execute("UPDATE users SET unlock_failures = 0, unlock_blocked_until = NULL WHERE id = ?", (uid,))
    return {"ok": True}


def _settings_out() -> dict:
    with db.get_conn() as conn:
        values = settings.all_values(conn)
        tuned = {"iterations": db.get_setting(conn, "argon2_iterations"),
                 "memoryMiB": config.KDF_MEMORY_KIB // 1024,
                 "secondsPerIteration": db.get_setting(conn, "argon2_seconds_per_iteration")}
        pc = copies.admin_status(conn)
    return settings.payload(values, kdf=tuned, version=APP_VERSION, personalCopies=pc, **sessions.stats())


@router.get("/admin/settings")
def get_settings(admin: dict = Depends(admin_session)):
    return _settings_out()


@router.put("/admin/settings")
def put_settings(body: dict = Body(...), admin: dict = Depends(admin_session)):
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
    if before["reminder_titles"] and not values["reminder_titles"]:
        with db.get_conn() as conn:
            reminders.forget_titles(conn)        # the names kept so far go now, not at each vault's next save
    return _settings_out()


# ---------- backup and restore ----------
REQUIRED_TABLES = {"users", "vaults", "vault_members", "vault_keys", "vault_versions", "audit_log", "app_settings"}


BACKUP_PREFIX = "household-vault-backup-"           # temp files: swept by housekeeping if a download never ends
MAX_BACKUP_BYTES = 300 * 1024 * 1024
RESTORE_LOCK = threading.Lock()
STAGING = ".restore-staging"


def _build_backup() -> str:
    """The backup zip in a temp file (streamed to the browser, then deleted): a consistent vault.db snapshot plus
    every (encrypted) vault file and personal copy. Not built in memory — it can be hundreds of MB."""
    fd, path = tempfile.mkstemp(prefix=BACKUP_PREFIX, suffix=".zip")
    os.close(fd)
    try:
        with tempfile.TemporaryDirectory(prefix=BACKUP_PREFIX) as tmp:
            snap = os.path.join(tmp, "vault.db")
            src = sqlite3.connect(config.DB_PATH)
            try:
                db_core.snapshot(src, snap)
            finally:
                src.close()
            cdir = copies.copy_dir()                    # each person's one-file copy (their master password)
            backup_core.write_zip(path, itertools.chain(
                [(snap, "vault.db")],
                backup_core.walk(config.VAULT_DIR, prefix="vaults/", keep=lambda full, _rel: full.endswith(".kdbx")),
                ((os.path.join(cdir, f), "copies/" + f) for f in (sorted(os.listdir(cdir)) if os.path.isdir(cdir) else [])
                 if f.endswith(".kdbx"))))
    except BaseException:
        os.remove(path)
        raise
    return path


@router.post("/admin-storage-download-db")
def download_backup(body: ConfirmIn, admin: dict = Depends(admin_session)):
    """One zip: a consistent vault.db snapshot plus every (encrypted) vault file. A POST with the admin's master
    password (security review 2026-10): a link anyone on Home Assistant's origin could follow is not enough."""
    with db.get_conn() as conn:
        confirm_master(conn, admin, body.password)
    path = _build_backup()
    with db.get_conn() as conn:
        db.audit(conn, "backup_downloaded", None, None, admin["id"])
    name = backup_core.file_name("household-vault-backup", ".zip")
    return FileResponse(path, media_type=backup_core.ZIP_MEDIA_TYPE, filename=name,
                        background=BackgroundTask(_remove_quietly, path))


def _remove_quietly(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


@router.post("/admin-storage-import-db")
async def import_backup(file: UploadFile = File(...), password: str = Form(""), admin: dict = Depends(admin_session)):
    # the master password first (the upload is read only once that's right), then streamed to a temp file (not
    # 300 MB in memory), then checked and restored in the thread pool, so the app keeps answering meanwhile
    # (review 2026-10)
    def _confirm():
        with db.get_conn() as conn:
            confirm_master(conn, admin, password)
    await run_in_threadpool(_confirm)
    tmp = await backup_core.receive(file, tempfile.gettempdir(), suffix=".zip", prefix=BACKUP_PREFIX,
                                    max_bytes=MAX_BACKUP_BYTES,
                                    too_big=lambda: HTTPException(413, "That backup is bigger than 300 MB."))
    try:
        return await run_in_threadpool(_restore, tmp, admin)
    finally:
        _remove_quietly(tmp)


def _check_backup(z, names: list) -> None:
    if "vault.db" not in names:
        raise HTTPException(422, "The backup has no vault.db.")

    def unexpected(n):
        return HTTPException(422, f"Unexpected file in the backup: {n}")

    def expected(info):
        n = info.filename
        if not (n == "vault.db" or (n.startswith("vaults/") and n.endswith(".kdbx")) or
                (n.startswith("copies/") and n.count("/") == 1 and n.endswith(".kdbx"))):
            raise unexpected(n)

    backup_core.check_members(z, check=expected, unsafe=unexpected,
                              is_unsafe=lambda n: n.startswith("/") or ".." in n.split("/"))


def _restore(tmp_zip: str, admin: dict) -> dict:
    """Restore a backup without ever leaving a half-restored app: everything is unpacked and checked in a staging
    folder next to the live data first, and only then moved into place, folder by folder, with the old folders
    kept until the end (and put back if a move fails) — review 2026-10."""
    with RESTORE_LOCK:
        with open(tmp_zip, "rb") as f:
            z = backup_core.open_zip(f, lambda: HTTPException(422, "That isn't a Household Vault backup (.zip)."))
            names = z.namelist()
            _check_backup(z, names)
            staging = os.path.join(config.DATA_DIR, STAGING)
            shutil.rmtree(staging, ignore_errors=True)
            os.makedirs(os.path.join(staging, "vaults"))
            os.makedirs(os.path.join(staging, "copies"))
            try:
                dbfile = os.path.join(staging, "vault.db")
                backup_core.copy_out(z, "vault.db", dbfile)
                try:
                    c = sqlite3.connect(dbfile)
                    ok = c.execute("PRAGMA integrity_check").fetchone()[0]
                    tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
                    last = (c.execute("SELECT MAX(created_at) FROM audit_log").fetchone()[0] or "") \
                        if "audit_log" in tables else ""
                    c.close()
                except sqlite3.DatabaseError:
                    raise HTTPException(422, "The database in the backup is damaged.")
                if ok != "ok" or not REQUIRED_TABLES <= tables:
                    raise HTTPException(422, "The database in the backup is damaged or from another app.")
                for n in names:
                    if n.startswith("copies/"):
                        dest = os.path.join(staging, "copies", n.split("/", 1)[1])
                    elif n.endswith(".kdbx"):
                        dest = os.path.join(staging, "vaults", os.path.relpath(n, "vaults"))
                    else:
                        continue
                    backup_core.copy_out(z, n, dest)
                    with open(dest, "rb") as kf:
                        if not kdbx.is_kdbx(kf.read(12)):
                            raise HTTPException(422, f"{n} isn't a KeePass file.")
                # what the restore will undo: removals and password changes since the backup
                with db.get_conn() as conn:
                    undone = [dict(r) for r in conn.execute(
                        "SELECT a.action, a.created_at, a.user_id, v.name AS vault, u.name AS person FROM audit_log a "
                        "LEFT JOIN vaults v ON v.id = a.vault_id LEFT JOIN users u ON u.id = a.user_id "
                        "WHERE a.created_at > ? AND a.action IN ('unshared','left','user_disabled','user_reset',"
                        "'rekeyed','password_changed','emergency_revoked','emergency_denied','quick_unlock_added',"
                        "'guest_wifi_removed') ORDER BY a.created_at", (last,))]
                with GATE.pause(timeout=60):                # background jobs wait until the swap is done
                    sessions.end_all()
                    _swap_in(staging, dbfile)
            finally:
                shutil.rmtree(staging, ignore_errors=True)
    db.init_db()
    settings.REGISTRY.invalidate()
    with open(os.path.join(config.DATA_DIR, "restore_notes.json"), "w") as f:
        json.dump({"restoredAt": config.now_iso(), "backupUpTo": last, "undone": undone}, f)
    with db.get_conn() as conn:
        conn.execute("UPDATE user_copies SET stale = 1")      # rewritten from the restored vaults at each unlock
        db.audit(conn, "backup_restored", None, None, admin["id"])
        everyone = [r["id"] for r in conn.execute("SELECT id FROM users WHERE disabled = 0 AND status != 'none'")]
        alerts.send(conn, everyone, "An admin restored a Household Vault backup. Changes made after it was taken "
                    "are gone — check your vaults when you next unlock.", kind="admin")
    # the backup's Argon2 tuning may come from another machine: measure this one again (in the background)
    threading.Thread(target=_retune, daemon=True, name="argon2-tune").start()
    return {"ok": True, "undone": undone}


def _retune() -> None:
    try:
        service.calibrate(force=True)
    except Exception:
        logging.getLogger("admin").exception("Couldn't tune Argon2 after the restore")


def _swap_in(staging: str, dbfile: str) -> None:
    """Move the staged folders and database into place; on a failure part-way, put the old ones back."""
    moved = []
    try:
        for live, new in ((config.VAULT_DIR, os.path.join(staging, "vaults")),
                          (copies.copy_dir(), os.path.join(staging, "copies"))):
            old = live + ".before-restore"
            shutil.rmtree(old, ignore_errors=True)
            if os.path.exists(live):
                os.replace(live, old)
            moved.append((live, old))
            os.replace(new, live)
        db_core.swap_in(dbfile, config.DB_PATH, db.get_conn)
    except BaseException:
        for live, old in reversed(moved):
            shutil.rmtree(live, ignore_errors=True)
            if os.path.exists(old):
                os.replace(old, live)
        raise
    for _live, old in moved:
        shutil.rmtree(old, ignore_errors=True)


@router.get("/restore-notes")
def restore_notes(user: dict = Depends(require_user)):
    """What the last restore undid. An admin sees all of it; anyone else only what was about them (vault names and
    people in the rest are none of their business)."""
    path = os.path.join(config.DATA_DIR, "restore_notes.json")
    if not os.path.exists(path):
        return {"notes": None}
    with open(path) as f:
        notes = json.load(f)
    undone = notes.get("undone") or []
    if not user["is_admin"]:
        undone = [x for x in undone if x.get("user_id") == user["id"]]
    notes["undone"] = [{k: v for k, v in x.items() if k != "user_id"} for x in undone]
    return {"notes": notes}
