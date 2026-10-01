"""The maintenance files folder (SPEC §14.6): manuals, receipts and photos, in a folder an admin
picks inside /share (App setting `maintenance_files_path`; blank = attaching files is off).

It works like Household Chat's files folder. The app writes only into a folder whose hidden
`.household_todo_store` marker matches the id kept in the database, so an unmounted network share never
fills Home Assistant's own disk. Anything else means "not connected": uploads and downloads say why, and
nothing is marked missing or deleted. Checked at start-up, every 5 minutes, and when an admin saves.

Layout (readable without the app):
    <folder>/<Item name>/                    manuals, warranty papers
    <folder>/<Item name>/<YYYY-MM-DD>/       what was attached when marking it done
    <folder>/Jobs/<Job title (id8)>/         one-off jobs
    <folder>/_deleted/<YYYY-MM-DD>/…         removed files, emptied after 30 days
    <folder>/_thumbs/                        small previews of photos (safe to delete)
Only files uploaded through the app are listed; anything else in the folder is left alone.
Changing the folder never moves files.
"""
import asyncio
import json
import logging
import os
import re
import shutil
import threading
import time
import unicodedata
from datetime import date, timedelta

from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException

from . import config, db, settings

logger = logging.getLogger("maint_files")

MARKER = ".household_todo_store"
STORE_KEY = "maint_files_store_id"          # in app_settings, not an App setting anyone edits
DELETED = "_deleted"
THUMBS = "_thumbs"
JOBS = "Jobs"
MAX_BYTES = 25 * 1024 * 1024
KEEP_DELETED_DAYS = 30
CHECK_EVERY_S = 300
RECHECK_S = 30
MAX_NAME = 80
CHUNK = 256 * 1024
THUMB_PX = 320
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
EXT_MIME = {
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif", "webp": "image/webp",
    "heic": "image/heic", "heif": "image/heif", "pdf": "application/pdf", "txt": "text/plain",
    "csv": "text/csv", "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xls": "application/vnd.ms-excel", "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "mp4": "video/mp4", "mov": "video/quicktime", "zip": "application/zip",
}
INLINE = {"image/png", "image/jpeg", "image/gif", "image/webp", "application/pdf", "text/plain"}
THUMBABLE = {"image/png", "image/jpeg", "image/webp", "image/gif"}

_lock = threading.Lock()
_check_lock = threading.Lock()
_state = {"online": False, "reason": "Not checked yet.", "checkedAt": None, "path": None, "mono": 0.0}


class Offline(HTTPException):
    pass


# ---------- names ----------
def clean(name: str, limit: int = MAX_NAME, fallback: str = "file") -> str:
    """Safe for Windows/Samba shares: no \\/:*?"<>| or control characters, no leading dot, no trailing dot
    or space, not a reserved device name, at most `limit` characters (the extension is kept)."""
    name = unicodedata.normalize("NFC", str(name or ""))
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f\x7f]', "-", name)
    name = re.sub(r"\s+", " ", name).strip().lstrip(".").strip()
    if len(name) > limit:
        stem, dot, ext = name.rpartition(".")
        if dot and 0 < len(ext) <= 10 and stem:
            name = stem[: max(1, limit - len(ext) - 1)].rstrip() + "." + ext
        else:
            name = name[:limit]
    name = name.rstrip(". ")
    if not name:
        name = fallback
    if name.split(".")[0].lower() in _RESERVED:
        name = "_" + name
    return name


def mime_of(name: str) -> str:
    ext = name.rpartition(".")[2].lower() if "." in name else ""
    return EXT_MIME.get(ext, "application/octet-stream")


# ---------- the setting ----------
def configured() -> str:
    return (settings.get("maintenance_files_path") or "").strip()


def clean_path(v) -> str:
    """'' (off), or an absolute, normalised folder strictly inside /share (config.SHARE_ROOT)."""
    if v is None:
        return ""
    if not isinstance(v, str):
        raise ValueError("The maintenance files folder must be a path like /share/household/maintenance.")
    v = v.strip()
    if not v:
        return ""
    if len(v) > 400 or any(ord(c) < 32 or ord(c) == 127 for c in v):
        raise ValueError("The maintenance files folder must be a path like /share/household/maintenance.")
    if not v.startswith("/"):
        raise ValueError("The maintenance files folder must be a full path starting with /share/.")
    if any(part in (".", "..") for part in v.split("/")):
        raise ValueError("The maintenance files folder can't contain . or .. parts.")
    v = "/" + os.path.normpath(v).lstrip("/")
    share = "/" + os.path.normpath(config.SHARE_ROOT).lstrip("/")
    if not v.startswith(share.rstrip("/") + "/"):
        raise ValueError(f"The maintenance files folder must be inside {share} (not {share} itself), "
                         f"like {share}/household/maintenance.")
    return v


def _store_id() -> str | None:
    with db.get_conn() as conn:
        r = conn.execute("SELECT value FROM app_settings WHERE key = ?", (STORE_KEY,)).fetchone()
    try:
        return json.loads(r["value"]) if r else None
    except ValueError:
        return None


def _save_store_id(v: str) -> None:
    with db.get_conn() as conn:
        conn.execute("INSERT INTO app_settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, NULL) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                     (STORE_KEY, json.dumps(v), config.now_iso()))


def store_id() -> str | None:
    return _store_id()


def restore_store_id(v: str | None) -> None:
    """After a database restore: keep this install's folder (the restored file may carry another id)."""
    if v:
        _save_store_id(v)


def read_marker(path: str) -> str | None:
    try:
        with open(os.path.join(path, MARKER), encoding="utf-8") as f:
            return f.read().strip()[:64] or None
    except OSError:
        return None


def _write_marker(path: str, sid: str) -> None:
    tmp = os.path.join(path, MARKER + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(sid + "\n")
    os.replace(tmp, os.path.join(path, MARKER))


def ensure_layout(root: str) -> None:
    for sub in (DELETED, THUMBS):
        os.makedirs(os.path.join(root, sub), exist_ok=True)
    p = os.path.join(root, "README.txt")
    if not os.path.exists(p):
        with open(p, "w", encoding="utf-8") as f:
            f.write("Household Todo keeps its maintenance files here: one folder per maintenance item (manuals,\n"
                    "warranty papers), with a dated folder inside for what was attached when it was marked done,\n"
                    "and Jobs/ for one-off jobs. Only files uploaded through the app are listed in it; please\n"
                    "don't rename or move them. _thumbs holds small previews (safe to delete); _deleted holds\n"
                    f"removed files for 30 days. {MARKER} tells the app this folder is its own: keep it when you\n"
                    "copy the folder.\n")


# ---------- state ----------
def status() -> dict:
    with _lock:
        return {k: v for k, v in _state.items() if k != "mono"}


def is_online() -> bool:
    path = configured()
    with _lock:
        return bool(path) and _state["online"] and _state["path"] == path


def _set(online: bool, reason: str | None, path: str) -> None:
    with _lock:
        before = (_state["online"], _state["path"], _state["reason"])
        _state.update(online=online, reason=reason, checkedAt=config.now_iso(), path=path, mono=time.monotonic())
    if before != (online, path, reason) and path:
        if online:
            logger.info("Maintenance files folder %s is connected.", path)
        else:
            logger.warning("Maintenance files folder not connected: %s", reason)


def reason() -> str:
    path = configured()
    if not path:
        return "No maintenance files folder is set. An admin can choose one in Admin → App settings."
    s = status()
    if s["path"] != path:
        return f"The maintenance files folder {path} hasn't been checked yet."
    return s["reason"] or f"The maintenance files folder {path} isn't connected."


def require_online() -> str:
    """The folder's real path, re-checking an offline folder now and then; 503 while it isn't connected.
    Never call it holding a database connection (check() uses its own)."""
    if not is_online() and configured():
        with _lock:
            stale = time.monotonic() - _state["mono"] > RECHECK_S
        if stale:
            check()
    if not is_online():
        raise Offline(503, reason() + " Files can't be added or opened until it's connected.")
    return os.path.realpath(configured())


def _setup(path: str, sid: str | None) -> str | None:
    parent = os.path.dirname(path.rstrip("/")) or "/"
    if not os.path.isdir(path):
        if not os.path.isdir(parent):
            return f"The folder {parent} doesn't exist. Is the share or network storage mounted?"
        try:
            os.mkdir(path)
        except OSError as e:
            return f"Couldn't create {path}: {e.strerror}."
    new = sid or db.new_id()
    try:
        _write_marker(path, new)
        ensure_layout(path)
    except OSError as e:
        return f"Couldn't write to {path}: {e.strerror}."
    if new != sid:
        _save_store_id(new)
    logger.info("Maintenance files folder set up at %s", path)
    return None


def check(allow_setup: bool = False) -> dict:
    """Connected / not connected for the folder in use now, and why. `allow_setup` (an admin just chose it,
    or pressed "Use this folder") sets up a folder without our marker; otherwise nothing is created."""
    with _check_lock:
        path = configured()
        if not path:
            _set(False, None, "")
            return status()
        try:
            clean_path(path)
        except ValueError as e:
            _set(False, f"{e} Change it in Admin → App settings.", path)
            return status()
        sid = _store_id()
        marker = read_marker(path) if os.path.isdir(path) else None
        if allow_setup and (marker is None or marker != sid):
            err = _setup(path, sid)
            if err:
                _set(False, err, path)
                return status()
            sid, marker = _store_id(), read_marker(path)
        if not os.path.isdir(path):
            _set(False, f"The maintenance files folder {path} doesn't exist. Is the share or network storage mounted?", path)
            return status()
        if marker is None:
            _set(False, f"The maintenance files folder {path} isn't connected: its {MARKER} file is missing. Is the "
                        "network storage mounted? If you moved the files, copy the whole folder including that file.", path)
            return status()
        if marker != sid:
            _set(False, f"The maintenance files folder {path} belongs to another Household Todo (its {MARKER} file "
                        "is from a different install).", path)
            return status()
        if not os.access(path, os.W_OK):
            _set(False, f"The maintenance files folder {path} is read-only.", path)
            return status()
        try:
            ensure_layout(path)
        except OSError as e:
            _set(False, f"Couldn't write to the maintenance files folder {path}: {e.strerror}.", path)
            return status()
        _set(True, None, path)
    try:
        reconcile_folders()
    except Exception:
        logger.exception("Renaming maintenance folders failed")
    return status()


def inspect(path: str) -> dict:
    """What a candidate folder holds, for Admin → App settings. No side effects. `path` is cleaned."""
    current = configured()
    if not path:
        n = _count_db_files()
        return {"path": "", "verdict": "off", "ok": True, "needsConfirm": False, "refused": False,
                "message": "Attaching files will be off." + (f" The {n} files already attached stay where they are."
                                                             if n else "")}
    sid = _store_id()
    exists = os.path.isdir(path)
    parent = os.path.dirname(path.rstrip("/")) or "/"
    parent_exists = os.path.isdir(parent)
    mv = read_marker(path) if exists else None
    marker = "none" if mv is None else ("this" if sid and mv == sid else "other")
    writable = os.access(path if exists else parent, os.W_OK) if (exists or parent_exists) else False
    found = sum(1 for e in os.scandir(path) if not e.name.startswith(".")) if exists else 0
    n_db = _count_db_files()
    refused = needs_confirm = False
    if path == current:
        verdict, msg = "current", "This is the maintenance files folder in use now."
    elif marker == "other":
        verdict, refused = "other", True
        msg = f"This folder belongs to another Household Todo (its {MARKER} file is from a different install). Pick another folder."
    elif not exists and not parent_exists:
        verdict, refused = "missing_parent", True
        msg = f"Neither this folder nor {parent} exists. Is the share or network storage mounted?"
    elif not writable:
        verdict, refused = "read_only", True
        msg = f"{path if exists else parent} is read-only."
    elif marker == "this":
        verdict, msg = "this", "This folder already holds this install's maintenance files. Safe to switch."
    elif n_db or found:
        verdict, needs_confirm = "confirm", True
        parts = []
        if found:
            parts.append(f"This folder already has {found} item{'s' if found != 1 else ''} in it; they're left alone.")
        if n_db:
            parts.append(f"The {n_db} file{'s' if n_db != 1 else ''} already attached stay in the old folder"
                         f"{' (' + current + ')' if current else ''} and show as missing until it's chosen again.")
        msg = " ".join(parts)
    elif exists:
        verdict, msg = "new", "This folder will be set up for maintenance files when you save."
    else:
        verdict, msg = "new", "This folder doesn't exist yet. It will be created when you save."
    return {"path": path, "current": current, "exists": exists, "verdict": verdict, "message": msg,
            "ok": verdict in ("current", "this", "new"), "needsConfirm": needs_confirm, "refused": refused}


def _count_db_files() -> int:
    with db.get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM maint_files").fetchone()[0]


def public_status() -> dict:
    path = configured()
    online = is_online()
    return {"configured": bool(path), "online": online, "reason": None if online else reason(),
            "maxBytes": MAX_BYTES}


def admin_status() -> dict:
    s = status()
    path = configured()
    info = dict(public_status(), path=path, checkedAt=s["checkedAt"], freeBytes=None)
    if info["online"]:
        try:
            info["freeBytes"] = shutil.disk_usage(path).free
        except OSError:
            pass
    return info


# ---------- where things go ----------
def _within(root: str, rel: str) -> str:
    p = os.path.realpath(os.path.join(root, rel))
    if not (p == root or p.startswith(root + os.sep)):
        raise HTTPException(400, "Bad file path.")
    return p


def item_folder_name(conn, item: dict) -> str:
    base = clean(item["name"], 60, "Item")
    name, n = base, 1
    while conn.execute("SELECT 1 FROM maint_items WHERE folder = ? AND id != ?", (name, item["id"])).fetchone() \
            or name in (DELETED, THUMBS, JOBS):
        n += 1
        name = f"{base} ({n})"
    return name


def job_folder_name(task: dict) -> str:
    return os.path.join(JOBS, clean(task["title"], 60, "Job") + f" ({task['id'][:8]})")


def _unique(dirpath: str, name: str) -> str:
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    cand, n = name, 1
    while os.path.exists(os.path.join(dirpath, cand)):
        n += 1
        cand = f"{stem} ({n}).{ext}" if ext else f"{stem} ({n})"
    return cand


def target_dir(conn, *, item_id=None, done_id=None, task_id=None) -> tuple[str, dict]:
    """(folder relative to the root, owner columns). Gives an item its folder name on first use."""
    if task_id:
        t = conn.execute("SELECT id, title FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return job_folder_name(dict(t)), {"task_id": task_id}
    if done_id:
        d = conn.execute("SELECT item_id, done_on FROM maint_done WHERE id = ?", (done_id,)).fetchone()
        item_id_ = d["item_id"]
        folder = _item_folder(conn, item_id_)
        return os.path.join(folder, d["done_on"]), {"done_id": done_id}
    return _item_folder(conn, item_id), {"item_id": item_id}


def _item_folder(conn, item_id: str) -> str:
    it = dict(conn.execute("SELECT id, name, folder FROM maint_items WHERE id = ?", (item_id,)).fetchone())
    if not it["folder"]:
        it["folder"] = item_folder_name(conn, it)
        conn.execute("UPDATE maint_items SET folder = ? WHERE id = ?", (it["folder"], item_id))
    return it["folder"]


def save_upload(root: str, rel_dir: str, filename: str, stream_read, uploaded_by: str, owner: dict) -> dict:
    """Write an upload (reading `stream_read(n)` until b''), returning the new maint_files row. The caller
    has checked the owner exists and holds no DB connection."""
    name = clean(filename or "file")
    d = _within(root, rel_dir)
    os.makedirs(d, exist_ok=True)
    final = _unique(d, name)
    path = os.path.join(d, final)
    tmp = path + ".part"
    size = 0
    try:
        with open(tmp, "wb") as f:
            while True:
                chunk = stream_read(CHUNK)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_BYTES:
                    raise HTTPException(413, f"A file can be at most {MAX_BYTES // (1024 * 1024)} MB.")
                f.write(chunk)
        if size == 0:
            raise HTTPException(422, "That file is empty.")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    mime = mime_of(final)
    rel = os.path.relpath(path, root)
    thumb = _make_thumb(root, path, mime)
    row = {"id": db.new_id(), "item_id": owner.get("item_id"), "done_id": owner.get("done_id"),
           "task_id": owner.get("task_id"), "rel_path": rel, "name": final, "size": size, "mime": mime,
           "thumb": thumb, "uploaded_by": uploaded_by, "created_at": config.now_iso()}
    with db.get_conn() as conn:
        conn.execute("INSERT INTO maint_files (id, item_id, done_id, task_id, rel_path, name, size, mime, thumb, "
                     "uploaded_by, created_at) VALUES (:id, :item_id, :done_id, :task_id, :rel_path, :name, :size, "
                     ":mime, :thumb, :uploaded_by, :created_at)", row)
    return row


def _make_thumb(root: str, path: str, mime: str) -> str | None:
    if mime not in THUMBABLE:
        return None
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return None
    try:
        Image.MAX_IMAGE_PIXELS = 80_000_000
        rel = os.path.join(THUMBS, db.new_id() + ".jpg")
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail((THUMB_PX, THUMB_PX))
            im.save(os.path.join(root, rel), "JPEG", quality=80)
        return rel
    except Exception:
        return None


def to_deleted(root: str, rels) -> None:
    """Move files or folders (relative paths) into _deleted/<today>/, keeping their layout."""
    day = config.today().isoformat()
    for rel in rels:
        if not rel:
            continue
        try:
            src = _within(root, rel)
        except HTTPException:
            continue
        if not os.path.exists(src):
            continue
        dest_dir = os.path.join(root, DELETED, day, os.path.dirname(rel))
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, _unique(dest_dir, os.path.basename(rel)))
        try:
            shutil.move(src, dest)
        except OSError:
            logger.exception("Couldn't move %s to %s", rel, DELETED)


def remove_thumbs(root: str, rels) -> None:
    for rel in rels:
        if rel:
            try:
                os.remove(_within(root, rel))
            except (OSError, HTTPException):
                pass


def reconcile_folders() -> int:
    """Item folders follow their item's name (a rename while not connected catches up here)."""
    if not is_online():
        return 0
    root = os.path.realpath(configured())
    n = 0
    with db.get_conn() as conn:
        for it in [dict(r) for r in conn.execute("SELECT id, name, folder FROM maint_items WHERE folder IS NOT NULL")]:
            want = item_folder_name(conn, it)
            if want == it["folder"]:
                continue
            src, dst = os.path.join(root, it["folder"]), os.path.join(root, want)
            if os.path.exists(dst):
                continue
            try:
                if os.path.isdir(src):
                    os.rename(src, dst)
            except OSError:
                logger.exception("Couldn't rename %s", src)
                continue
            old = it["folder"] + os.sep
            conn.execute("UPDATE maint_items SET folder = ? WHERE id = ?", (want, it["id"]))
            for f in conn.execute("SELECT id, rel_path FROM maint_files WHERE rel_path LIKE ? ESCAPE '\\'",
                                  (_like_prefix(old),)).fetchall():
                conn.execute("UPDATE maint_files SET rel_path = ? WHERE id = ?", (want + os.sep + f["rel_path"][len(old):], f["id"]))
            n += 1
    return n


def _like_prefix(p: str) -> str:
    return p.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def purge_deleted(today: date | None = None) -> int:
    if not is_online():
        return 0
    today = today or config.today()
    base = os.path.join(os.path.realpath(configured()), DELETED)
    n = 0
    try:
        entries = list(os.scandir(base))
    except OSError:
        return 0
    for e in entries:
        try:
            d = date.fromisoformat(e.name)
        except ValueError:
            continue
        if (today - d).days > KEEP_DELETED_DAYS and e.is_dir():
            shutil.rmtree(e.path, ignore_errors=True)
            n += 1
    return n


async def loop() -> None:
    """Re-check the folder every 5 minutes (a NAS may come and go)."""
    while True:
        await asyncio.sleep(CHECK_EVERY_S)
        try:
            await run_in_threadpool(check)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Checking the maintenance files folder failed")


def reset() -> None:
    """Tests."""
    with _lock:
        _state.update(online=False, reason="Not checked yet.", checkedAt=None, path=None, mono=0.0)
