"""Admin (SPEC §4, §5.6, §13, §13.1): People (access, folder name, document count and size, phones and notify
services, delete their documents), App settings, Documents folder (status, folder browser, first-run choice),
Rescan now, Backup and restore. Admins see counts and sizes only — never anyone's documents.
"""
import itertools
import logging
import os
import tempfile
import time
import zipfile

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from .. import auth, config, db, files, ha_client, notify, settings, share_folders
from ..common import backup_core, csv_export, db_core, ha_notify, people_admin
from ..auth import require_admin
from ..store import fileio, index, marker, moving, nodes, paths, roots, trash

logger = logging.getLogger("admin")
router = APIRouter(prefix="/api/admin", tags=["admin"])
MAX_SERVICES_PER_USER = 5
RESTORE_MAX = 20 * 1024 ** 3
_LIMITER = people_admin.TestLimiter(10)
PRESERVED_STATE = ("install_id", "docs_confirmed", "docs_path")


def _who(admin: dict) -> str:
    return admin.get("username") or admin["id"]


def _known(conn, uid: str):
    r = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    if r is None:
        raise HTTPException(404, "That person isn't known to Household Docs.")
    return r


# ---------- people ----------
def _person_json(conn, r, admin_id: str) -> dict:
    root = roots.person_root(conn, r["id"])
    count, size = (0, 0)
    if root is not None:
        c = conn.execute("SELECT COUNT(*), COALESCE(SUM(size), 0) FROM nodes WHERE root_id = ? AND gone_at IS NULL "
                         "AND trash_id IS NULL AND kind != 'folder'", (root["id"],)).fetchone()
        count, size = c[0], c[1]
    parents = [x["parent_id"] for x in conn.execute("SELECT parent_id FROM kid_parents WHERE child_id = ?", (r["id"],))]
    return {"id": r["id"], "name": r["name"], "username": r["username"], "disabled": bool(r["disabled"]),
            "isAdmin": auth.is_admin(r["id"], r["username"]), "you": r["id"] == admin_id,
            "isChild": bool(r["is_child"]), "parents": parents,
            "folder": r["folder"], "docCount": count, "docSize": size, "lastSeen": r["last_seen"],
            "notify": ha_notify.assigned_services(conn, {"id": r["id"]}), "ha": people_admin.ha_person_json(r["id"])}


@router.get("/users")
def list_users(refresh: bool = Query(default=False), admin: dict = Depends(require_admin)):
    people_admin.refresh_people(refresh)
    if refresh:
        ha_client.sync_people()
    with db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM users ORDER BY name COLLATE NOCASE").fetchall()
        return {"users": [_person_json(conn, r, admin["id"]) for r in rows]}


def _root_id_of(conn, uid: str) -> str | None:
    r = roots.person_root(conn, uid)
    return r["id"] if r is not None else None


@router.patch("/users/{uid}")
def patch_user(uid: str, background: BackgroundTasks, body: dict = Body(...), admin: dict = Depends(require_admin)):
    """{disabled?: bool, folder?: "new folder name", isChild?: bool, parents?: [person ids]} — a child's account and
    who may view their My docs (Kids' space, §17.20)."""
    if not isinstance(body, dict) or not body or set(body) - {"disabled", "folder", "isChild", "parents"}:
        raise HTTPException(422, "Send disabled, folder, isChild and/or parents.")
    if "isChild" in body and not isinstance(body["isChild"], bool):
        raise HTTPException(422, "isChild must be true or false.")
    if "parents" in body and (not isinstance(body["parents"], list) or len(body["parents"]) > 20
                              or not all(isinstance(x, str) and 0 < len(x) <= 128 for x in body["parents"])):
        raise HTTPException(422, "parents is a list of people.")
    if "disabled" in body and not isinstance(body["disabled"], bool):
        raise HTTPException(422, "disabled must be true or false.")
    if "folder" in body and not isinstance(body["folder"], str):
        raise HTTPException(422, "folder must be a name.")
    with db.get_conn() as conn:
        r = _known(conn, uid)
        if "disabled" in body and bool(r["disabled"]) != body["disabled"]:
            conn.execute("UPDATE users SET disabled = ? WHERE id = ?", (1 if body["disabled"] else 0, uid))
            db.audit(conn, "access_off" if body["disabled"] else "access_on", admin["id"])
        tell_child = False
        if "isChild" in body and bool(r["is_child"]) != body["isChild"]:
            conn.execute("UPDATE users SET is_child = ?, child_since = ? WHERE id = ?",
                         (1 if body["isChild"] else 0, config.now_iso() if body["isChild"] else None, uid))
            if not body["isChild"]:
                conn.execute("DELETE FROM kid_parents WHERE child_id = ?", (uid,))
            db.audit(conn, "child_on" if body["isChild"] else "child_off", admin["id"], None, _root_id_of(conn, uid))
            tell_child = True
        if "parents" in body:
            if not conn.execute("SELECT is_child FROM users WHERE id = ?", (uid,)).fetchone()["is_child"]:
                raise HTTPException(409, "Only a child's My docs can be opened to parents — mark them as a child first.")
            wanted = list(dict.fromkeys(body["parents"]))
            for pid in wanted:
                if pid == uid:
                    raise HTTPException(422, "A child can't be their own parent here.")
                if pid == admin["id"]:              # admins get no content access as admins (§3.2)
                    raise HTTPException(403, "You can't name yourself as a parent — another admin has to do that.")
                _known(conn, pid)
            before = {x["parent_id"] for x in conn.execute("SELECT parent_id FROM kid_parents WHERE child_id = ?", (uid,))}
            tell_child = tell_child or before != set(wanted)
            conn.execute("DELETE FROM kid_parents WHERE child_id = ?", (uid,))
            for pid in wanted:
                conn.execute("INSERT INTO kid_parents (child_id, parent_id, added_by, added_at) VALUES (?, ?, ?, ?)",
                             (uid, pid, admin["id"], config.now_iso()))
            db.audit(conn, "child_parents_set", admin["id"], None, _root_id_of(conn, uid))
        if "folder" in body:
            try:
                roots.rename_person_folder(conn, uid, body["folder"].strip())
            except paths.PathError as e:
                raise HTTPException(422, str(e))
            db.audit(conn, "person_folder_renamed", admin["id"])
        if tell_child:
            background.add_task(notify.send_child_status, uid)
        return _person_json(conn, conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone(), admin["id"])


@router.post("/users/{uid}/docs/delete")
def delete_docs(uid: str, body: dict = Body(...), admin: dict = Depends(require_admin)):
    """Move everything in a person's folder to Trash (their Trash; emptied after trash_days). The admin types the
    folder's name to confirm."""
    with db.get_conn() as conn:
        r = _known(conn, uid)
        root = roots.person_root(conn, uid)
        if root is None or not r["folder"]:
            raise HTTPException(404, f"{r['name']} has no folder yet.")
        if (body or {}).get("confirm") != r["folder"]:
            raise HTTPException(422, f"Type the folder's name ({r['folder']}) to confirm.")
        roots.docs_real()
        fileio.ensure_writable()
        top = nodes.children(conn, root["id"], None)
        for n in top:
            trash.trash_node(conn, admin["id"], n)
        db.audit(conn, "person_docs_deleted", admin["id"], None, root["id"])
        return {"ok": True, "moved": len(top), "user": _person_json(conn, r, admin["id"])}


@router.get("/notify-services")
def notify_services(refresh: bool = False, admin: dict = Depends(require_admin)):
    return people_admin.notify_services(refresh)


@router.post("/users/{uid}/notify", status_code=201)
def add_notify(uid: str, body: dict = Body(...), admin: dict = Depends(require_admin)):
    svc = people_admin.clean_service((body or {}).get("service"))
    with db.get_conn() as conn:
        _known(conn, uid)
        return {"notify": people_admin.add_service(conn, uid, svc, _who(admin), config.now_iso(), limit=MAX_SERVICES_PER_USER)}


@router.delete("/users/{uid}/notify/{svc}")
def remove_notify(uid: str, svc: str, admin: dict = Depends(require_admin)):
    svc = people_admin.clean_service(svc)
    with db.get_conn() as conn:
        _known(conn, uid)
        return {"notify": people_admin.remove_service(
            conn, uid, svc, "That isn't one of their extra services (phones are set in Home Assistant).")}


@router.post("/users/{uid}/notify/test")
def test_notify(uid: str, admin: dict = Depends(require_admin)):
    _LIMITER.check(uid)
    with db.get_conn() as conn:
        row = _known(conn, uid)
        services = ha_notify.services_for({"id": uid}, conn)
    if not services:
        raise HTTPException(409, f"{row['name']} has no phone linked in Home Assistant (Settings → People → Track "
                                 "device) and no extra notify service here.")
    results = ha_notify.send_to_services(services, config.APP_TITLE, f"Test from Household Docs for {row['name']} — "
                                         "“shared with you” notifications arrive like this.",
                                         {"url": config.INGRESS_URL, "clickAction": config.INGRESS_URL})
    return {"results": people_admin.test_results(results, as_list=True)}


# ---------- App settings ----------
@router.get("/settings")
def get_settings(admin: dict = Depends(require_admin)):
    return settings.payload(**settings.payload_extra())


@router.put("/settings")
def put_settings(body: dict = Body(...), admin: dict = Depends(require_admin)):
    if not isinstance(body, dict):
        raise HTTPException(422, "Send the settings as a JSON object.")
    if "docs_path" in body:
        raise HTTPException(422, "The documents folder is chosen on Admin → Documents folder.")
    try:
        settings.update(body, _who(admin))
    except settings.SettingsError as e:
        raise HTTPException(422, str(e))
    with db.get_conn() as conn:
        db.audit(conn, "settings_changed", admin["id"])
    return settings.payload(**settings.payload_extra())


# ---------- the documents folder (§5.6) ----------
def _folder_json() -> dict:
    st = roots.check()
    out = dict(st)
    out["free"] = None
    out["size"] = out["files"] = 0
    with db.get_conn() as conn:
        c = conn.execute("SELECT COUNT(*), COALESCE(SUM(n.size), 0) FROM nodes n JOIN roots r ON r.id = n.root_id "
                         "WHERE r.kind = 'person' AND n.gone_at IS NULL AND n.trash_id IS NULL AND n.kind != 'folder'").fetchone()
        out["files"], out["size"] = c[0], c[1]
        out["people"] = conn.execute("SELECT COUNT(*) FROM roots WHERE kind = 'person'").fetchone()[0]
        out["previous"] = moving.previous(conn)
        out["lastScanAt"] = conn.execute("SELECT MAX(last_scan_at) FROM roots").fetchone()[0]
        out["readOnly"] = moving.state(conn)
    if st["ok"]:
        out["free"] = marker.free_space(roots.docs_real())
    out["default"] = config.DEFAULT_DOCS_PATH
    return out


@router.get("/docs-folder")
def docs_folder(admin: dict = Depends(require_admin)):
    return _folder_json()


@router.get("/share-dirs")
def share_dirs(path: str = Query(default="", max_length=400), admin: dict = Depends(require_admin)):
    rel = config.share_rel(path) if path.startswith(config.SHARE_PREFIX) else path
    with db.get_conn() as conn:
        shared = share_folders.shared_reals(conn)
    try:
        return marker.browse(rel, shared)
    except paths.PathError as e:
        raise HTTPException(404, str(e))


@router.post("/docs-folder/inspect")
def inspect(body: dict = Body(...), admin: dict = Depends(require_admin)):
    """What a location is and whether it can be used — changes nothing."""
    with db.get_conn() as conn:
        iid, shared = roots.install_id(conn), roots.shared_reals(conn)
    out = marker.check((body or {}).get("path"), iid, shared, current_display=roots.configured())
    out.pop("real", None)
    return out


@router.post("/docs-folder/choose")
def choose(body: dict = Body(...), admin: dict = Depends(require_admin)):
    """First run (§5.6): confirm the default or choose another location. Choosing a different folder is allowed
    while nobody has documents yet; moving existing documents is Change location (§5.7)."""
    try:
        path = marker.clean_location((body or {}).get("path"))
    except paths.PathError as e:
        raise HTTPException(422, str(e))
    moving.guard()
    with db.get_conn() as conn:
        current = roots.configured(conn)
        iid, shared = roots.install_id(conn), roots.shared_reals(conn)
        if path != current:
            has_docs = conn.execute("SELECT 1 FROM nodes n JOIN roots r ON r.id = n.root_id WHERE r.kind = 'person' "
                                    "AND n.trash_id IS NULL LIMIT 1").fetchone()
            if has_docs:
                raise HTTPException(409, f"Documents are already kept in {current}. Moving them to another folder is "
                                         "done with Change location, which checks your copy first.")
    info = marker.check(path, iid, shared, current_display=current)
    if not info["ok"]:
        raise HTTPException(409, info["message"])
    try:
        marker.setup(info["real"], iid)
    except OSError as e:
        raise HTTPException(409, f"The app couldn't set up {path}: {e.strerror or e}.")
    with db.get_conn() as conn:
        if path != current:
            settings.update({"docs_path": path}, _who(admin))
            for r in conn.execute("SELECT r.id, u.folder FROM roots r JOIN users u ON u.id = r.user_id "
                                  "WHERE r.kind = 'person'").fetchall():
                conn.execute("UPDATE roots SET path = ? WHERE id = ?",
                             (paths.join_rel(config.share_rel(path), marker.PEOPLE, r["folder"]), r["id"]))
        db.state_set(conn, "docs_confirmed", True, _who(admin))
        db.audit(conn, "docs_location_chosen", admin["id"])
    settings.invalidate()
    roots.check(allow_setup=True)
    with db.get_conn() as conn:
        for r in conn.execute("SELECT user_id FROM roots WHERE kind = 'person'").fetchall():
            roots.ensure_person_root(conn, r["user_id"])
    return _folder_json()


# ---------- moving to a new location (§5.7) ----------
@router.post("/docs-folder/read-only")
def read_only(body: dict = Body(...), admin: dict = Depends(require_admin)):
    """{on: bool, target?: "/share/…"} — pause every change while the files are copied."""
    on = (body or {}).get("on")
    if not isinstance(on, bool):
        raise HTTPException(422, "Send on: true or false.")
    target = (body or {}).get("target")
    with db.get_conn() as conn:
        moving.set_read_only(conn, on, admin, target if isinstance(target, str) else None)
    return _folder_json()


@router.post("/docs-folder/target")
def move_target(body: dict = Body(...), admin: dict = Depends(require_admin)):
    """Change location, step 1: can this folder be the new location? With the copy instructions' paths."""
    info = moving.target_info((body or {}).get("path"))
    info.pop("real", None)
    if info["ok"]:
        info["copy"] = moving.copy_commands(roots.configured(), info["path"])
    return info


@router.post("/docs-folder/check")
def move_check(body: dict = Body(...), admin: dict = Depends(require_admin)):
    """Compare the documents folder with the copy (a background job; poll GET …/check/{job})."""
    roots.docs_real()
    return moving.start_check((body or {}).get("path"), bool((body or {}).get("deep")), admin)


@router.get("/docs-folder/check/{job_id}")
def move_check_state(job_id: str, admin: dict = Depends(require_admin)):
    return moving.job_json(job_id)


@router.get("/docs-folder/check/{job_id}/report.csv")
def move_check_report(job_id: str, admin: dict = Depends(require_admin)):
    header, rows = moving.report_rows(job_id)
    with db.get_conn() as conn:          # the report names files (paths, no content): who downloaded it is kept
        db.audit(conn, "docs_location_report_downloaded", admin["id"])
    data = csv_export.to_bytes(header, rows, bom=True)
    return Response(data, media_type="text/csv; charset=utf-8", headers=files.download_headers("copy-check.csv"))


@router.post("/docs-folder/switch")
def move_switch(background: BackgroundTasks, body: dict = Body(...), admin: dict = Depends(require_admin)):
    """{path, jobId, force?, confirm?}: use the checked copy as the documents folder (§5.7 step 5)."""
    body = body or {}
    if not isinstance(body.get("jobId"), str):
        raise HTTPException(422, "Send the check's jobId.")
    out = moving.switch(body.get("path"), body["jobId"], bool(body.get("force")), body.get("confirm"), admin)
    background.add_task(notify.send_moved, out.pop("tell"))
    out["folder"] = _folder_json()
    return out


@router.post("/rescan")
def rescan(admin: dict = Depends(require_admin)):
    roots.check()
    t0 = time.monotonic()
    res = index.scan_all()
    totals = {"new": 0, "changed": 0, "moved": 0, "gone": 0, "back": 0}
    for r in res.values():
        for k in totals:
            totals[k] += (r or {}).get(k, 0)
    return {"ok": True, "roots": len(res), "ms": int((time.monotonic() - t0) * 1000), **totals}


# ---------- backup and restore (§13.1) ----------
def _backup_members(tmp_db: str, include_files: bool):
    members = [(tmp_db, "docs.db")]
    if not include_files:
        return members
    base = roots.docs_real()

    def prune(dirpath, dirnames):
        rel = os.path.relpath(dirpath, base)
        if rel == ".":
            dirnames[:] = [d for d in dirnames if d in ("people", ".trash", ".versions")]
            return True                     # the marker belongs to this install's folder, not to a backup
        return False

    def keep(full, rel):
        return not os.path.islink(full)

    return itertools.chain(members, ((full, name, zipfile.ZIP_STORED) for full, name in
                                     backup_core.walk(base, prefix="files/", prune=prune, keep=keep)))


def _tree_size(base: str) -> tuple[int, int]:
    """(files, bytes) under the documents folder's people/, .trash and .versions — what a backup with files holds."""
    files = total = 0
    for top in (marker.PEOPLE, ".trash", ".versions"):
        stack = [os.path.join(base, top)]
        while stack:
            d = stack.pop()
            try:
                with os.scandir(d) as it:
                    for e in it:
                        try:
                            if e.is_symlink():
                                continue
                            if e.is_dir(follow_symlinks=False):
                                stack.append(e.path)
                            elif e.is_file(follow_symlinks=False):
                                files += 1
                                total += e.stat(follow_symlinks=False).st_size
                        except OSError:
                            continue
            except OSError:
                continue
    return files, total


@router.get("/backup/size")
def backup_size(admin: dict = Depends(require_admin)):
    """What Download backup will hold, before it's made: the database's size, and the files' when
    `backup_files` is on (a zip of them is about as big, as most documents are stored as they are)."""
    db_size = sum(os.path.getsize(p) for p in (config.DB_PATH, config.DB_PATH + "-wal") if os.path.exists(p))
    include = bool(settings.get("backup_files"))
    out = {"database": db_size, "includeFiles": include, "files": 0, "filesSize": 0}
    if include:
        try:
            out["files"], out["filesSize"] = _tree_size(roots.docs_real())
        except roots.DocsUnavailable as e:
            out["error"] = e.detail
    out["total"] = out["database"] + out["filesSize"]
    return out


@router.get("/backup")
def backup(admin: dict = Depends(require_admin)):
    """docs.db (a consistent snapshot) — plus the documents themselves when `backup_files` is on. The AI
    access key is blanked in the copy: a backup never carries it."""
    include = bool(settings.get("backup_files"))
    os.makedirs(config.DATA_DIR, exist_ok=True)
    tmp_db = db_core.snapshot_to_tempfile(db._connect, prefix=".backup-", dir=config.DATA_DIR,
                                          after=settings.REGISTRY.scrub_secrets)
    fd, tmp_zip = tempfile.mkstemp(prefix=".backup-", suffix=".zip", dir=config.DATA_DIR)
    os.close(fd)
    try:
        backup_core.write_zip(tmp_zip, _backup_members(tmp_db, include))
    except BaseException:
        os.remove(tmp_zip)
        raise
    finally:
        os.remove(tmp_db)
    with db.get_conn() as conn:
        db.audit(conn, "backup_downloaded", admin["id"])
    name = backup_core.file_name("household-docs-backup", ".zip", now=config.now(), fmt="%Y-%m-%d")
    return backup_core.send_file(tmp_zip, name, backup_core.ZIP_MEDIA_TYPE)


@router.post("/restore")
async def restore(request: Request, replaceFiles: bool = Query(default=False), admin: dict = Depends(require_admin)):
    """The backup .zip as the request body. Replaces the database, keeps this install's documents-folder
    settings and its AI access key (a backup has none), re-runs migrations and re-scans every root. Files in
    the zip are restored only into an empty documents folder, or with replaceFiles."""
    moving.guard()
    tmp = await backup_core.receive(request, config.DATA_DIR, prefix=".restore-", suffix=".zip", max_bytes=RESTORE_MAX,
                                    too_big=lambda: HTTPException(413, "That backup is too big."))
    try:
        return await run_in_threadpool(_restore_from, tmp, admin, replaceFiles)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _restore_from(tmp: str, admin: dict, replace_files: bool) -> dict:
    if not zipfile.is_zipfile(tmp):
        raise HTTPException(422, "That isn't a Household Docs backup (.zip).")
    with zipfile.ZipFile(tmp) as z:
        names = z.namelist()
        if "docs.db" not in names:
            raise HTTPException(422, "The backup has no docs.db.")

        def expected(info):
            if info.filename != "docs.db" and not info.filename.startswith("files/"):
                raise HTTPException(422, "Unexpected file in the backup.")

        backup_core.check_members(z, check=expected, check_first=True,
                                  unsafe=lambda _n: HTTPException(422, "Unsafe path in the backup."))
        file_members = [n for n in names if n.startswith("files/") and not n.endswith("/")]
        fd, new_db = tempfile.mkstemp(prefix=".restore-", suffix=".db", dir=config.DATA_DIR)
        os.close(fd)
        try:
            backup_core.copy_out(z, "docs.db", new_db)
            db_core.validate_file(new_db, db.REQUIRED_TABLES, app_name="Household Docs")
        except ValueError as e:
            os.remove(new_db)
            raise HTTPException(422, str(e))
        base = None
        if file_members:
            base = roots.docs_real()
            people = os.path.join(base, marker.PEOPLE)
            empty = not os.listdir(people) if os.path.isdir(people) else True
            if not empty and not replace_files:
                os.remove(new_db)
                raise HTTPException(409, "The documents folder isn't empty. Tick Replace files to restore the "
                                         "backup's files over it, or restore without them.")
        with db.get_conn() as conn:
            keep = {k: db.state_get(conn, k) for k in PRESERVED_STATE if k != "docs_path"}
            keep_path = settings.get("docs_path", conn)
            saved_secrets = settings.REGISTRY.saved_secrets(conn)
        db.RESTORING.set()
        try:
            time.sleep(0.3)                     # let requests already running finish
            db_core.swap_in(new_db, config.DB_PATH, db.get_conn)
            db.init_db()
            with db.get_conn() as conn:
                for k, v in keep.items():          # this install's identity and folder, not the backup's
                    if v is not None:
                        db.state_set(conn, k, v)
                db.state_set(conn, "docs_path", keep_path)   # the App setting's own row (JSON text, like the registry)
                settings.REGISTRY.keep_secrets(saved_secrets, conn)
                for r in conn.execute("SELECT r.id, u.folder FROM roots r JOIN users u ON u.id = r.user_id "
                                      "WHERE r.kind = 'person' AND u.folder IS NOT NULL").fetchall():
                    conn.execute("UPDATE roots SET path = ? WHERE id = ?",
                                 (paths.join_rel(config.share_rel(keep_path), marker.PEOPLE, r["folder"]), r["id"]))
            settings.invalidate()
        finally:
            db.RESTORING.clear()
        restored = 0
        if file_members and base:
            base_real = os.path.realpath(base)
            for n in file_members:
                rel = n[len("files/"):]
                top = rel.split("/")[0]
                if top not in (marker.PEOPLE, ".trash", ".versions"):
                    continue
                dest = os.path.realpath(os.path.join(base_real, rel))
                if not paths.inside(base_real, dest) or dest == base_real:
                    continue
                backup_core.copy_out(z, n, dest)
                restored += 1
    roots.check()
    index.scan_all()
    with db.get_conn() as conn:
        db.audit(conn, "backup_restored", admin["id"])
    return {"ok": True, "files": restored}
