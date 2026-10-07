"""Admin: App settings, backup / restore (§11.1), media storage status and checks (§5.1)."""
import json
import logging
import os
import re
import itertools
import shutil
import tempfile
import zipfile

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, UploadFile
from pydantic import Field
from starlette.concurrency import run_in_threadpool

from .. import config, db, kin, media, settings
from ..auth import require_admin
from ..common import backup_core
from ..models import Strict
from ..history import Batch

router = APIRouter(prefix="/api", tags=["admin"])
logger = logging.getLogger("admin")

APP_VERSION = "2.3.0"
_MEDIA_NAME = re.compile(r"^media/([0-9a-f]{2})/([0-9a-f]{32})/(original|thumb1024\.jpg|thumb256\.jpg)$")
MAX_DB_BYTES = 2 * 1024 ** 3
MAX_MEDIA_FILE_BYTES = 200 * 1024 ** 2


# ---------- App settings ----------
def _settings_out() -> dict:
    out = settings.public()
    s = media.status()
    here = s["path"] == media.root()
    out["media"] = {"path": media.root(), "online": media.is_online(),
                    "reason": s["reason"] if here else "Not checked yet."}
    return out


@router.get("/admin/settings")
def get_settings(admin: dict = Depends(require_admin)):
    return _settings_out()


@router.put("/admin/settings")
def put_settings(body: dict = Body(...), admin: dict = Depends(require_admin)):
    """Any subset of the settings. Changing the photo folder never moves files:
    a folder with another tree's marker is refused (409), and a folder without
    this tree's marker needs `"confirm": true` while the tree has photos (409
    otherwise), so an API call can't quietly point the app at an empty folder."""
    body = dict(body)
    confirm = body.pop("confirm", False)
    if not isinstance(confirm, bool):
        raise HTTPException(422, "confirm must be true or false.")
    try:
        checked = {k: settings.validate_one(k, v) for k, v in body.items()}
    except settings.SettingsError as e:
        raise HTTPException(422, str(e))
    new_path = checked.get("media_path")
    switching = new_path is not None and new_path != settings.get("media_path")
    if switching:
        info = media.inspect(new_path)
        if info["refused"]:
            raise HTTPException(409, info["message"])
        if info["needsConfirm"] and not confirm:
            raise HTTPException(409, info["message"] + " Confirm to switch anyway.")
    try:
        settings.update(body, admin)
    except settings.SettingsError as e:
        raise HTTPException(422, str(e))
    if switching:
        logger.info("Photo folder changed to %s by %s", new_path, admin.get("name"))
        media.check(allow_setup=True)          # applies at once; sets up a new folder only for an empty tree
    return _settings_out()


class MediaPathIn(Strict):
    path: str = Field(max_length=1000)


@router.post("/admin/settings/check-media-path")
def check_media_path(body: MediaPathIn, admin: dict = Depends(require_admin)):
    """What a candidate photo folder holds — before saving it. No side effects."""
    try:
        path = settings.validate_one("media_path", body.path)
    except settings.SettingsError as e:
        raise HTTPException(422, str(e))
    return media.inspect(path)


def _scratch_dir(big: bool) -> str:
    """Large (with-media) backups are built on the share, never in /data."""
    if big and media.is_online():
        d = os.path.join(media.root(), ".exports")
        os.makedirs(d, exist_ok=True)
        return d
    return config.DATA_DIR


@router.get("/admin-storage-download-db")
def download_backup(media_files: bool = Query(False, alias="media"), admin: dict = Depends(require_admin)):
    if media_files:
        media.require_online()
    fd, zpath = tempfile.mkstemp(suffix=".zip", dir=_scratch_dir(media_files))
    os.close(fd)
    dbtmp = db.temp_path_in_data(".db")
    try:
        db.backup_to_file(dbtmp)
        members = [(dbtmp, "family.db"),
                   backup_core.Text("backup.json", json.dumps({"app": "family_tree", "version": APP_VERSION,
                                                               "created": config.now_iso(), "withMedia": media_files}))]
        if media_files:          # the photos, stored as they are (already compressed)
            members = itertools.chain(members, (
                (full, name, zipfile.ZIP_STORED) for full, name in backup_core.walk(
                    os.path.join(media.root(), "media"), prefix="media/",
                    keep=lambda _full, rel: _MEDIA_NAME.match("media/" + rel))))
        backup_core.write_zip(zpath, members)
    except Exception:
        os.remove(zpath)
        raise
    finally:
        if os.path.exists(dbtmp):
            os.remove(dbtmp)
    name = backup_core.file_name("family-tree-backup", ".zip", suffix="-with-photos" if media_files else "")
    return backup_core.send_file(zpath, name, backup_core.ZIP_MEDIA_TYPE)


def _validate_zip(zpath: str) -> dict:
    """Check everything before touching anything. Returns {db_member, media_members}."""
    media_members = []

    def check(info):
        n = info.filename
        if n in ("family.db", "backup.json"):
            if n == "family.db" and info.file_size > MAX_DB_BYTES:
                raise ValueError("The database in that backup is unreasonably large.")
            return
        m = _MEDIA_NAME.match(n)
        if not m or m.group(1) != m.group(2)[:2]:
            raise ValueError(f"Unexpected file in the backup: {n}")
        if info.file_size > MAX_MEDIA_FILE_BYTES:
            raise ValueError(f"A media file in the backup is too large: {n}")
        media_members.append((n, m.group(2), m.group(3)))

    with backup_core.open_zip(zpath, lambda: ValueError("That file isn't a Family Tree backup (.zip).")) as z:
        if "family.db" not in z.namelist():
            raise ValueError("That zip has no family.db — it isn't a Family Tree backup.")
        backup_core.check_members(z, skip_dirs=True, check=check,
                                  unsafe=lambda n: ValueError(f"Refusing a backup with an unsafe path: {n}"))
    return {"media": media_members}


def _restore(zpath: str, admin: dict) -> dict:
    info = _validate_zip(zpath)
    dbtmp = db.temp_path_in_data(".db")
    try:
        with zipfile.ZipFile(zpath) as z:
            with z.open("family.db") as src, open(dbtmp, "wb") as dst:
                shutil.copyfileobj(src, dst, backup_core.CHUNK)
            db.validate_db_file(dbtmp)
            import sqlite3
            c = sqlite3.connect(dbtmp)
            try:
                known = {r[0]: r[1] for r in c.execute("SELECT id, content_type FROM media")}
            finally:
                c.close()
            bad = [k for k in known if not media.valid_id(k)]
            if bad:
                raise ValueError(f"The backup's database has an invalid photo id ({str(bad[0])[:40]}).")
            members = info["media"]
            if members:
                media.require_online()
                for name, mid, kind in members:
                    if mid not in known:
                        raise ValueError(f"The backup holds a file for a photo its database doesn't know ({mid}).")
                    with z.open(name) as f:
                        head = f.read(16)
                    sniffed = media.sniff(head)
                    expected = known[mid] if kind == "original" else "image/jpeg"
                    if sniffed != expected:
                        raise ValueError(f"A media file in the backup isn't the type its database says ({name}).")
                for name, _mid, _kind in members:
                    backup_core.copy_out(z, name, os.path.join(media.root(), name), part=".tmp")
        current_store = media.status().get("store_id")
        with db.get_conn() as conn:
            current_settings = settings.rows(conn)
        current_media_path = [r for r in current_settings if r[0] == "media_path"]
        db.replace_db(dbtmp)
    finally:
        if os.path.exists(dbtmp):
            os.remove(dbtmp)
    with db.get_conn() as conn:
        if current_store:
            db.set_setting(conn, "media_store_id", current_store)
        # A backup from an older database with no App settings keeps the ones in use. (Its
        # feature switches were already set from its own data by the migration; any others
        # come from this install.)
        if not conn.execute("SELECT 1 FROM app_settings WHERE key NOT LIKE 'feature!_%' ESCAPE '!'").fetchone():
            settings.put_rows(conn, [r for r in current_settings if not r[0].startswith("feature_")])
            conn.executemany("INSERT OR IGNORE INTO app_settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, ?)",
                             [r for r in current_settings if r[0].startswith("feature_")])
        # The photo folder belongs to this install (like media_store_id): the
        # photos were just written there, so a backup never moves it.
        conn.execute("DELETE FROM app_settings WHERE key = 'media_path'")
        settings.put_rows(conn, current_media_path)
        Batch(conn, admin["id"], "Restored from backup")
        rows = conn.execute("SELECT id FROM media WHERE deleted_at IS NULL").fetchall()
    settings.invalidate()
    kin.invalidate()
    media.check()
    missing = 0
    if media.is_online():
        missing = sum(1 for r in rows if not os.path.isfile(media.file_path(r["id"])))
    return {"ok": True, "mediaRestored": len(info["media"]), "mediaMissing": missing}


@router.post("/admin-storage-import-db")
async def import_backup(file: UploadFile = File(...), admin: dict = Depends(require_admin)):
    zpath = db.temp_path_in_data(".zip")
    try:
        with open(zpath, "wb") as out:
            while chunk := await file.read(1024 * 1024):
                out.write(chunk)
        try:
            return await run_in_threadpool(_restore, zpath, admin)
        except ValueError as e:
            raise HTTPException(400, str(e))
    finally:
        if os.path.exists(zpath):
            os.remove(zpath)


# ---------- media storage ----------
@router.get("/admin/storage")
def storage(admin: dict = Depends(require_admin)):
    media.check()
    info = media.storage_info()
    with db.get_conn() as conn:
        info["dbBytes"] = os.path.getsize(config.DB_PATH) if os.path.exists(config.DB_PATH) else 0
        info["people"] = conn.execute("SELECT COUNT(*) FROM people WHERE deleted_at IS NULL").fetchone()[0]
        info["mediaRows"] = conn.execute("SELECT COUNT(*) FROM media").fetchone()[0]
    info["appVersion"] = APP_VERSION
    return info


@router.post("/admin/media/check")
def media_check(admin: dict = Depends(require_admin)):
    media.require_online()
    return media.check_consistency()


@router.post("/admin/media/adopt")
def media_adopt(admin: dict = Depends(require_admin)):
    try:
        return media.adopt_foreign()
    except ValueError as e:
        raise HTTPException(409, str(e))


class Orphans(Strict):
    ids: list[str] = Field(max_length=10000)


@router.post("/admin/media/orphans")
def move_orphans(body: Orphans, admin: dict = Depends(require_admin)):
    media.require_online()
    current = set(media.check_consistency()["orphans"])
    return {"moved": media.move_orphans([i for i in body.ids if i in current])}
