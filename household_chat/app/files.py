"""Files in the chat files folder, /share/household_chat by default (SPEC §5.3, §5.3.1, §15.4, §15.6).

Every chat has a folder named after it (`Family (a1b2c3d4)`), with a folder
per month inside. File paths always come from the database and are
`realpath`-checked under that folder; a client only ever names an attachment id.
"""
import hashlib
import io
import json
import logging
import os
import re
import secrets
import shutil
import threading
import time
import unicodedata

from fastapi import HTTPException

from . import config, db, settings

logger = logging.getLogger("files")

THUMBS = "_thumbs"
DELETED = "_deleted"
THUMB_PX = 320
CHUNK = 256 * 1024
MAX_NAME = 60

INLINE_SANDBOXED = {"image/png", "image/jpeg", "image/gif", "image/webp", "audio/webm", "audio/ogg", "audio/mp4",
                    "audio/mpeg", "text/plain"}
INLINE_PDF = "application/pdf"
EXT_MIME = {
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif", "webp": "image/webp",
    "heic": "image/heic", "heif": "image/heif", "bmp": "image/bmp", "tif": "image/tiff", "tiff": "image/tiff",
    "ogg": "audio/ogg", "oga": "audio/ogg", "opus": "audio/ogg", "m4a": "audio/mp4", "mp3": "audio/mpeg",
    "wav": "audio/wav", "aac": "audio/aac",
    "mp4": "video/mp4", "mov": "video/quicktime", "webm": "video/webm", "mkv": "video/x-matroska",
    "pdf": "application/pdf", "txt": "text/plain", "md": "text/plain", "csv": "text/plain", "log": "text/plain",
    "doc": "application/msword", "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xls": "application/vnd.ms-excel", "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "ppt": "application/vnd.ms-powerpoint", "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "odt": "application/vnd.oasis.opendocument.text", "ods": "application/vnd.oasis.opendocument.spreadsheet",
    "zip": "application/zip", "json": "application/json",
}
VOICE_MIME = {"webm": "audio/webm", "ogg": "audio/ogg", "oga": "audio/ogg", "mp4": "audio/mp4", "m4a": "audio/mp4",
              "mp3": "audio/mpeg"}
RESIZABLE = {"image/jpeg", "image/png", "image/webp"}
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


# ---------- names ----------
def clean(name: str, limit: int = MAX_NAME, fallback: str = "file") -> str:
    """Safe for Windows/Samba: no \\/:*?"<>| or control characters, no leading dot,
    no trailing dot or space, not a reserved device name, at most `limit` characters."""
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


def ext_of(name: str) -> str:
    base = os.path.basename(name or "")
    return base.rpartition(".")[2].lower() if "." in base else ""


def folder_name(conn, conv) -> str:
    """What the chat's folder should be called now."""
    cid = conv["id"]
    id8 = cid[:8]
    if conv["kind"] == "group":
        label = conv["name"] or "Group"
    elif conv["kind"] == "personal":
        u = conn.execute("SELECT name FROM users WHERE id = ?", (conv["created_by"],)).fetchone()
        label = f"{u['name'] if u else 'Someone'} - personal"
    else:
        names = sorted(r["name"] for r in conn.execute(
            "SELECT u.name FROM members m JOIN users u ON u.id = m.user_id WHERE m.conversation_id = ?", (cid,)))
        label = " & ".join(names) or "Direct chat"
    suffix = f" ({id8})"
    return clean(label, MAX_NAME - len(suffix), "Chat") + suffix


# ---------- the chat files folder (App setting `files_path`, SPEC §5.3.1) ----------
# Changing the folder never moves files. The app writes only into a folder whose
# .household_chat_store marker matches the database's `files_store_id`: an
# unmounted NAS path would otherwise silently fill Home Assistant's own disk and
# be hidden once the mount comes back. Anything else means the folder is "not
# connected" (offline): chats keep working, files wait.
MARKER = ".household_chat_store"
STORE_KEY = "files_store_id"
RECHECK_S = 30                       # a request may re-check an offline folder at most this often

_state_lock = threading.Lock()
_check_lock = threading.Lock()
_state = {"online": False, "reason": "Not checked yet.", "checked_at": None, "path": None, "mono": 0.0}
_jobs_lock = threading.Lock()


class StorageOffline(HTTPException):
    pass


def configured() -> str:
    """The folder chosen in App settings (it may not be reachable)."""
    return settings.get("files_path")


def clean_files_path(v) -> str:
    """Absolute, normalised, no . or .. parts, strictly inside Home Assistant's /share (config.SHARE_ROOT)."""
    if not isinstance(v, str):
        raise ValueError("The chat files folder must be a path like /share/household_chat.")
    v = v.strip()
    if not v or len(v) > 400 or any(ord(c) < 32 or ord(c) == 127 for c in v):
        raise ValueError("The chat files folder must be a path like /share/household_chat.")
    if not v.startswith("/"):
        raise ValueError("The chat files folder must be a full path starting with /share/, like /share/household_chat.")
    if any(part in (".", "..") for part in v.split("/")):
        raise ValueError("The chat files folder can't contain . or .. parts.")
    v = "/" + os.path.normpath(v).lstrip("/")
    share = "/" + os.path.normpath(config.SHARE_ROOT).lstrip("/")
    if not v.startswith(share.rstrip("/") + "/") or v == share:
        raise ValueError(f"The chat files folder must be a folder inside {share} (not {share} itself), "
                         f"like {share}/household_chat or {share}/nas/household_chat.")
    return v


def status() -> dict:
    with _state_lock:
        return {k: v for k, v in _state.items() if k != "mono"}


def is_online() -> bool:
    """The last check passed *for the folder in use now*: after a change nothing counts until it's checked."""
    current = configured()
    with _state_lock:
        return _state["online"] and _state["path"] == current


def offline_reason() -> str:
    s = status()
    if s["path"] != configured():
        return f"The chat files folder {configured()} hasn't been checked yet."
    return s["reason"] or f"The chat files folder {configured()} isn't connected."


def require_online() -> None:
    """For requests that need the files: re-check an offline folder now and then (a NAS may be back).
    Never call it with a database connection open: check() writes through its own."""
    if is_online():
        return
    with _state_lock:
        stale = time.monotonic() - _state["mono"] > RECHECK_S
    if stale:
        check()
    if not is_online():
        raise StorageOffline(503, offline_reason() + " Files can't be opened or sent until it's back.")


def share_root() -> str:
    """The chat files folder — only while it's connected (StorageOffline, 503, otherwise)."""
    if not is_online():
        raise StorageOffline(503, offline_reason() + " Files can't be opened or sent until it's back.")
    return os.path.realpath(configured())


def _set(online: bool, reason: str | None, path: str) -> None:
    with _state_lock:
        before = (_state["online"], _state["path"], _state["reason"])
        _state.update(online=online, reason=reason, checked_at=config.now_iso(), path=path, mono=time.monotonic())
    if before != (online, path, reason):
        if online:
            logger.info("Chat files folder %s is connected.", path)
        else:
            logger.warning("Chat files folder not connected: %s", reason)
        try:
            from .live import hub
            hub.publish_all("storage", {"online": online, "reason": reason})
        except Exception:
            pass


def _db_state() -> tuple[str | None, int]:
    with db.get_conn() as conn:
        raw = db.get_setting(conn, STORE_KEY)
        n = conn.execute("SELECT COUNT(*) FROM attachments").fetchone()[0]
    return (json.loads(raw) if raw else None), n


def _save_store_id(store_id: str) -> None:
    with db.get_conn() as conn:
        db.set_setting(conn, STORE_KEY, json.dumps(store_id))


def read_marker(path: str) -> str | None:
    try:
        with open(os.path.join(path, MARKER), encoding="utf-8") as f:
            return f.read().strip()[:64] or None
    except OSError:
        return None


def _write_marker(path: str, store_id: str) -> None:
    tmp = os.path.join(path, MARKER + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(store_id + "\n")
    os.replace(tmp, os.path.join(path, MARKER))


def ensure_layout(root: str) -> None:
    """The folders the app needs, in a folder that is ours (marker checked)."""
    for sub in (THUMBS, DELETED):
        os.makedirs(os.path.join(root, sub), exist_ok=True)
    p = os.path.join(root, "README.txt")
    if not os.path.exists(p):
        with open(p, "w", encoding="utf-8") as f:
            f.write("Household Chat keeps the files shared in its chats here: one folder per chat, a folder per\n"
                    "month inside. Please don't rename or move files — the chat would show them as no longer\n"
                    "available. _thumbs holds small previews (safe to delete); _deleted holds removed files for\n"
                    f"30 days. {MARKER} tells the app this folder is its own: keep it when you copy the folder.\n")


def _setup(path: str, store_id: str | None) -> str | None:
    """Create the folder (its parent must exist), the marker and the layout. → an error, or None."""
    parent = os.path.dirname(path.rstrip("/")) or "/"
    if not os.path.isdir(path):
        if not os.path.isdir(parent):
            return f"The folder {parent} doesn't exist. Is the share or network storage mounted?"
        try:
            os.mkdir(path)
        except OSError as e:
            return f"Couldn't create {path}: {e.strerror}."
    new_store = store_id or db.new_id()
    try:
        _write_marker(path, new_store)
        ensure_layout(path)
    except OSError as e:
        return f"Couldn't write to {path}: {e.strerror}."
    if store_id != new_store:
        _save_store_id(new_store)
    logger.info("Chat files folder set up at %s", path)
    return None


def check(allow_setup: bool = False) -> dict:
    """Decide connected / not connected for the folder in use now, and say why.

    - Never set up yet (a new install, or an older one from before the folder was a setting): the folder is created if needed and gets the
      marker — or takes over the marker already there.
    - `allow_setup` (an admin just chose this folder, or pressed "Use this folder"): a folder without a marker,
      or with another one, is set up for this install.
    - Otherwise a missing folder, a missing or different marker, or a read-only folder means not connected; the
      app never creates anything then.
    Coming (back) online flushes the file jobs that waited, renames chat folders that changed meanwhile and
    re-checks which files are missing."""
    with _check_lock:
        was_online = is_online()
        path = configured()
        try:
            clean_files_path(path)
        except ValueError as e:
            _set(False, f"{e} Change it in Admin → App settings.", path)
            return status()
        store_id, n_files = _db_state()
        marker = read_marker(path) if os.path.isdir(path) else None
        if store_id is None and marker:
            _save_store_id(marker)                    # a fresh database on an existing folder: it's ours
            store_id = marker
        elif store_id is None or (allow_setup and marker != store_id):
            err = _setup(path, store_id)
            if err:
                _set(False, err, path)
                return status()
            store_id, marker = _db_state()[0], read_marker(path)
        if not os.path.isdir(path):
            _set(False, f"The chat files folder {path} doesn't exist. Is the share or network storage mounted?", path)
            return status()
        if marker is None:
            _set(False, f"The chat files folder {path} isn't connected: its {MARKER} file is missing. Is the network "
                        "storage mounted? If you moved the files, copy the whole folder including that file.", path)
            return status()
        if marker != store_id:
            _set(False, f"The chat files folder {path} belongs to another Household Chat (its {MARKER} file is "
                        "from a different install).", path)
            return status()
        if not os.access(path, os.W_OK):
            _set(False, f"The chat files folder {path} is read-only.", path)
            return status()
        try:
            ensure_layout(path)
        except OSError as e:
            _set(False, f"Couldn't write to the chat files folder {path}: {e.strerror}.", path)
            return status()
        _set(True, None, path)
    if not was_online:
        _back_online()
    return status()


def _back_online() -> None:
    try:
        flush_jobs()
        with db.get_conn() as conn:
            for r in conn.execute("SELECT id FROM conversations").fetchall():
                refresh_folder(conn, r["id"])
            check_missing(conn)
    except Exception:
        logger.exception("Catching up after the chat files folder came back failed")


def _count_files(path: str, limit: int = 200_000) -> int:
    n = 0
    for dirpath, dirnames, filenames in os.walk(path):
        rel = os.path.relpath(dirpath, path)
        if rel == ".":
            dirnames[:] = [d for d in dirnames if d not in (THUMBS, DELETED) and not d.startswith(".")]
            continue
        n += sum(1 for f in filenames if not f.startswith("."))
        if n >= limit:
            break
    return n


def inspect(path: str, shared_paths: list[str] | None = None) -> dict:
    """What a candidate folder holds, for Admin → App settings. No side effects. `path` is already cleaned."""
    store_id, n_files = _db_state()
    current = configured()
    exists = os.path.isdir(path)
    parent = os.path.dirname(path.rstrip("/")) or "/"
    parent_exists = os.path.isdir(parent)
    marker_value = read_marker(path) if exists else None
    marker = "none" if marker_value is None else ("this" if store_id and marker_value == store_id else "other")
    writable = os.access(path if exists else parent, os.W_OK) if (exists or parent_exists) else False
    found = _count_files(path) if exists else 0
    shared = next((s for s in shared_paths or [] if path == s or path.startswith(s + "/") or s.startswith(path + "/")), None)
    plural = "file" if n_files == 1 else "files"
    refused = needs_confirm = False
    if path == current:
        verdict, message = "current", "This is the chat files folder in use now."
    elif shared:
        verdict, refused = "shared", True
        message = f"{shared} is shared into a group (Admin → Shared folders), so it can't also be the chat files folder."
    elif marker == "other":
        verdict, refused = "other", True
        message = (f"This folder belongs to another Household Chat (its {MARKER} file is from a different install), "
                   "so it can't be used here. Pick another folder.")
    elif not exists and not parent_exists:
        verdict, refused = "missing_parent", True
        message = f"Neither this folder nor {parent} exists. Is the share or network storage mounted?"
    elif not writable:
        verdict, refused = "read_only", True
        message = f"{path if exists else parent} is read-only, so the chat's files couldn't be kept there."
    elif marker == "this":
        verdict = "this"
        message = f"This folder already holds this install's chat files ({found} found). Safe to switch."
    elif n_files:
        verdict, needs_confirm = "no_marker", True
        message = ((f"This folder has {found} files but no {MARKER} file. " if found else
                    "This folder doesn't exist yet; it will be created. " if not exists else
                    "This folder has no chat files. ")
                   + f"The {n_files} {plural} already shared stay in the old folder ({current}) and show as "
                     "“no longer available” until you copy them here. Copy the whole old folder, including "
                     f"{MARKER}, first.")
    elif exists:
        verdict, message = "new", "This folder will be set up for the chat's files when you save."
    else:
        verdict, message = "new", "This folder doesn't exist yet. It will be created with the folders the app needs when you save."
    return {"path": path, "current": current, "exists": exists, "parentExists": parent_exists, "writable": writable,
            "marker": marker, "files": found, "dbFiles": n_files, "verdict": verdict, "message": message,
            "ok": verdict in ("current", "this", "new"), "needsConfirm": needs_confirm, "refused": refused,
            "networkMount": is_network_mount(path)}


def is_network_mount(path: str) -> bool:
    """Best effort: is `path` (or a parent) on a network filesystem?"""
    try:
        best, fstype = "", ""
        with open("/proc/mounts", encoding="utf-8") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 3 and (path == parts[1] or path.startswith(parts[1].rstrip("/") + "/")):
                    if len(parts[1]) > len(best):
                        best, fstype = parts[1], parts[2]
        return fstype in ("cifs", "smb3", "nfs", "nfs4", "smbfs", "fuse.sshfs")
    except OSError:
        return False


def storage_info() -> dict:
    s = status()
    path = configured()
    online = is_online()
    info = {"path": path, "online": online, "reason": None if online else offline_reason(), "checkedAt": s["checked_at"],
            "networkMount": is_network_mount(path), "freeBytes": None, "default": settings.DEFAULTS["files_path"]}
    if online:
        try:
            info["freeBytes"] = shutil.disk_usage(path).free
        except OSError:
            pass
    return info


def public_status() -> dict:
    """For everyone (/me): connected or not, without the path."""
    online = is_online()
    return {"online": online, "reason": None if online else "The chat's file storage isn't connected."}


# ---------- file jobs that wait while the folder isn't connected ----------
def _jobs_path() -> str:
    return os.path.join(config.DATA_DIR, "pending_file_jobs.json")


def _read_jobs() -> list:
    try:
        with open(_jobs_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _queue(op: str, arg: str) -> None:
    """Deletes asked for while offline: done when the folder is back (paths are relative to it)."""
    with _jobs_lock:
        jobs = _read_jobs()
        if len(jobs) < 50_000:
            jobs.append([op, arg])
        tmp = _jobs_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(jobs, f)
        os.replace(tmp, _jobs_path())


def pending_jobs() -> int:
    with _jobs_lock:
        return len(_read_jobs())


def flush_jobs() -> int:
    if not is_online():
        return 0
    with _jobs_lock:
        jobs = _read_jobs()
        try:
            os.remove(_jobs_path())
        except OSError:
            pass
    run = {"deleted": move_to_deleted, "remove": remove_now, "folder": move_folder_to_deleted,
           "disappearing_dir": remove_disappearing_dir}
    for job in jobs:
        if isinstance(job, list) and len(job) == 2 and job[0] in run and isinstance(job[1], str):
            try:
                run[job[0]](job[1])
            except Exception:
                logger.exception("A waiting file job failed")
    return len(jobs)


def reset() -> None:
    """Tests: forget the connected state."""
    with _state_lock:
        _state.update(online=False, reason="Not checked yet.", checked_at=None, path=None, mono=0.0)
    try:
        os.remove(_jobs_path())
    except OSError:
        pass


def resolve(rel_path: str) -> str:
    """Absolute path for a stored relative path, refused if it leaves the chat files folder (symlinks included).
    StorageOffline (503) while the folder isn't connected."""
    root = share_root()
    full = os.path.realpath(os.path.join(root, rel_path))
    if full != root and not full.startswith(root + os.sep):
        raise HTTPException(404, "File no longer available.")
    return full


def month_dir(folder: str) -> tuple[str, str]:
    month = config.local_now().strftime("%Y-%m")
    rel = f"{folder}/{month}"
    full = resolve(rel)
    os.makedirs(full, exist_ok=True)
    return rel, full


def unique_name(full_dir: str, name: str) -> str:
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    candidate, n = name, 2
    while os.path.lexists(os.path.join(full_dir, candidate)):
        candidate = f"{stem} ({n}).{ext}" if ext else f"{stem} ({n})"
        n += 1
    return candidate


def thumb_path(att_id: str) -> str:
    d = os.path.join(share_root(), THUMBS)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{att_id}.jpg")


# ---------- folders follow names ----------
def refresh_folder(conn, conv_id: str) -> None:
    """Rename a chat's folder when its name (or a person's name) changed; paths updated in the same transaction."""
    conv = conn.execute("SELECT * FROM conversations WHERE id = ?", (conv_id,)).fetchone()
    if conv is None:
        return
    new = folder_name(conn, conv)
    old = conv["folder"]
    if new == old or not is_online():
        return          # not connected: renamed when the folder is back (check() → _back_online)
    root = share_root()
    old_full, new_full = os.path.join(root, old), os.path.join(root, new)
    if not _inside(root, old_full) or not _inside(root, new_full):
        logger.warning("Refusing to rename a chat folder outside the share folder.")
        return
    moved = False
    if os.path.isdir(old_full) and not os.path.exists(new_full):
        try:
            os.rename(old_full, new_full)
            moved = True
        except OSError as e:
            logger.warning("Couldn't rename a chat folder: %s", e)
            return
    elif os.path.isdir(old_full):
        return      # both exist: leave it, don't mix folders
    try:
        conn.execute("UPDATE conversations SET folder = ? WHERE id = ?", (new, conv_id))
        conn.execute("UPDATE attachments SET rel_path = ? || substr(rel_path, ?) WHERE conversation_id = ? "
                     "AND substr(rel_path, 1, ?) = ?", (new, len(old) + 1, conv_id, len(old) + 1, old + "/"))
    except Exception:
        if moved:
            os.rename(new_full, old_full)
        raise


def refresh_folders_of_user(conn, user_id: str) -> None:
    for r in conn.execute("SELECT c.id FROM conversations c JOIN members m ON m.conversation_id = c.id "
                          "WHERE m.user_id = ? AND c.kind IN ('personal','direct')", (user_id,)).fetchall():
        refresh_folder(conn, r["id"])


# ---------- usage ----------
def usage_bytes(conn, conversation_id: str | None = None) -> int:
    if conversation_id:
        r = conn.execute("SELECT COALESCE(SUM(size), 0) FROM attachments WHERE conversation_id = ?", (conversation_id,))
    else:
        r = conn.execute("SELECT COALESCE(SUM(size), 0) FROM attachments")
    return int(r.fetchone()[0])


def check_quota(conn, incoming: int) -> None:
    gb = settings.get("share_folder_quota_gb", conn)
    if gb and usage_bytes(conn) + incoming > gb * 1024 ** 3:
        raise HTTPException(507, f"The chat folder has reached its {gb} GB limit. Ask an admin to make room.")


# ---------- images ----------
def _pil():
    from PIL import Image, ImageOps
    Image.MAX_IMAGE_PIXELS = 80_000_000
    return Image, ImageOps


def sniff_image(path: str) -> tuple[str | None, int | None, int | None, bool]:
    """(mime, width, height, animated) if Pillow recognises it as an image we show; else (None, …)."""
    try:
        Image, ImageOps = _pil()
        with Image.open(path) as im:
            fmt = (im.format or "").upper()
            mime = {"JPEG": "image/jpeg", "PNG": "image/png", "GIF": "image/gif", "WEBP": "image/webp"}.get(fmt)
            if not mime:
                return None, None, None, False
            w, h = im.size
            if (im.getexif() or {}).get(0x0112) in (5, 6, 7, 8):
                w, h = h, w
            return mime, w, h, bool(getattr(im, "is_animated", False))
    except Exception:
        return None, None, None, False


def shrink_photo(path: str, max_px: int) -> tuple[bytes, int, int] | None:
    """A resized JPEG (quality 85, orientation applied, EXIF — GPS included — dropped, colour profile kept),
    or None when it's already small enough or can't be read."""
    try:
        Image, ImageOps = _pil()
        with Image.open(path) as im:
            if getattr(im, "is_animated", False):
                return None
            icc = im.info.get("icc_profile")
            im = ImageOps.exif_transpose(im)
            if max(im.size) <= max_px:
                return None
            if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
                im = im.convert("RGBA")
                bg = Image.new("RGB", im.size, (255, 255, 255))
                bg.paste(im, mask=im.split()[-1])
                im = bg
            elif im.mode != "RGB":
                im = im.convert("RGB")
            im.thumbnail((max_px, max_px), Image.LANCZOS)
            out = io.BytesIO()
            kw = {"quality": 85, "optimize": True}
            if icc:
                kw["icc_profile"] = icc
            im.save(out, "JPEG", **kw)
            return out.getvalue(), im.size[0], im.size[1]
    except Exception as e:
        logger.info("Couldn't resize a photo: %s", type(e).__name__)
        return None


def make_thumb(att_id: str, src: str) -> bool:
    try:
        Image, ImageOps = _pil()
        with Image.open(src) as im:
            im.seek(0)
            im = ImageOps.exif_transpose(im)
            if im.mode not in ("RGB", "L"):
                rgba = im.convert("RGBA")
                bg = Image.new("RGB", rgba.size, (255, 255, 255))
                bg.paste(rgba, mask=rgba.split()[-1])
                im = bg
            im.thumbnail((THUMB_PX, THUMB_PX), Image.LANCZOS)
            tmp = thumb_path(att_id) + ".part"
            im.save(tmp, "JPEG", quality=80)
            os.replace(tmp, thumb_path(att_id))
            return True
    except Exception:
        return False


# ---------- upload ----------
class Incoming:
    """A file being received: streamed to a temporary file next to where it will live."""

    def __init__(self, conn, conv, name: str, max_bytes: int):
        self.name = clean(os.path.basename(name or ""), 120)
        self.max_bytes = max_bytes
        self.rel_dir, self.full_dir = month_dir(conv["folder"])
        self.tmp = os.path.join(self.full_dir, f".upload-{secrets.token_hex(8)}.part")
        self.f = open(self.tmp, "wb")
        self.size = 0
        self.sha = hashlib.sha256()

    def write(self, chunk: bytes) -> None:
        self.size += len(chunk)
        if self.size > self.max_bytes:
            raise HTTPException(413, f"That file is bigger than the {self.max_bytes // (1024 * 1024)} MB limit.")
        self.sha.update(chunk)
        self.f.write(chunk)

    def discard(self) -> None:
        try:
            self.f.close()
        except Exception:
            pass
        try:
            os.remove(self.tmp)
        except OSError:
            pass

    def finish(self, *, voice: bool, original: bool, max_px: int) -> dict:
        """Decide the type, make photos smaller, move into place. → attachment fields."""
        self.f.close()
        if self.size == 0:
            self.discard()
            raise HTTPException(422, "That file is empty.")
        ext = ext_of(self.name)
        mime = EXT_MIME.get(ext, "application/octet-stream")
        width = height = None
        original_size = None
        name = self.name
        sha = self.sha.hexdigest()
        size = self.size
        if voice:
            mime = VOICE_MIME.get(ext)
            if not mime:
                self.discard()
                raise HTTPException(422, "A voice message must be WebM, Ogg, MP4/M4A or MP3 audio.")
        elif mime.startswith("image/") or ext in ("png", "jpg", "jpeg", "gif", "webp"):
            sniffed, width, height, animated = sniff_image(self.tmp)
            if sniffed:
                mime = sniffed
                if not original and max_px and sniffed in RESIZABLE and not animated and max(width, height) > max_px:
                    shrunk = shrink_photo(self.tmp, max_px)
                    if shrunk:
                        data, width, height = shrunk
                        original_size = size
                        with open(self.tmp, "wb") as f:
                            f.write(data)
                        size = len(data)
                        sha = hashlib.sha256(data).hexdigest()
                        mime = "image/jpeg"
                        stem = name.rpartition(".")[0] if "." in name else name
                        name = clean(stem + ".jpg", 120)
            elif ext in ("png", "jpg", "jpeg", "gif", "webp"):
                mime = "application/octet-stream"          # says image, isn't one: never shown inline
        elif mime == "application/pdf":
            with open(self.tmp, "rb") as f:
                if not f.read(1024).lstrip().startswith(b"%PDF-"):
                    mime = "application/octet-stream"
        final = unique_name(self.full_dir, name)
        os.replace(self.tmp, os.path.join(self.full_dir, final))
        return {"rel_path": f"{self.rel_dir}/{final}", "original_name": name, "mime": mime, "size": size,
                "original_size": original_size, "sha256": sha, "width": width, "height": height}


# ---------- after an upload / on demand ----------
def is_image(mime: str) -> bool:
    return mime in ("image/png", "image/jpeg", "image/gif", "image/webp")


def ensure_thumb(att) -> str | None:
    p = thumb_path(att["id"])
    if os.path.exists(p):
        return p
    if not is_image(att["mime"]):
        return None
    try:
        src = resolve(att["rel_path"])
    except HTTPException:
        return None
    if os.path.isfile(src) and make_thumb(att["id"], src):
        return p
    return None


def remove_thumb(att_id: str) -> None:
    if not is_online():
        return          # an orphan preview is removed by the nightly clean-up
    try:
        os.remove(thumb_path(att_id))
    except OSError:
        pass


def move_to_deleted(rel_path: str) -> None:
    """Into _deleted/<date time>/<chat folder>/<month>/<name>, purged after 30 days (later, if not connected)."""
    if not is_online():
        _queue("deleted", rel_path)
        return
    try:
        src = resolve(rel_path)
    except HTTPException:
        return
    if not os.path.isfile(src):
        return
    stamp = config.utcnow().strftime("%Y%m%dT%H%M%S")
    dest_dir = os.path.join(share_root(), DELETED, stamp, os.path.dirname(rel_path))
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, unique_name(dest_dir, os.path.basename(rel_path)))
    try:
        shutil.move(src, dest)
    except OSError as e:
        logger.warning("Couldn't move a deleted file aside: %s", e)


def _inside(root: str, path: str) -> bool:
    p = os.path.realpath(path)
    return p != root and p.startswith(root + os.sep) and os.path.dirname(p) == root


def move_folder_to_deleted(folder: str) -> None:
    if not is_online():
        if folder:
            _queue("folder", folder)
        return
    root = share_root()
    src = os.path.join(root, folder)
    if not folder or not _inside(root, src) or not os.path.isdir(src):
        return
    stamp = config.utcnow().strftime("%Y%m%dT%H%M%S")
    dest_dir = os.path.join(root, DELETED, stamp)
    os.makedirs(dest_dir, exist_ok=True)
    try:
        shutil.move(src, os.path.join(dest_dir, folder))
    except OSError as e:
        logger.warning("Couldn't move a deleted chat's folder aside: %s", e)


def copy_into(att, conv) -> dict | None:
    """Copy a file into another chat's folder (forwarding). → new attachment fields, or None if it's gone.
    Runs inside the caller's transaction, so no re-check here (check() may write through its own connection)."""
    share_root()
    try:
        src = resolve(att["rel_path"])
    except HTTPException:
        return None
    if not os.path.isfile(src):
        return None
    rel_dir, full_dir = month_dir(conv["folder"])
    final = unique_name(full_dir, os.path.basename(att["rel_path"]))
    shutil.copyfile(src, os.path.join(full_dir, final))
    return {"rel_path": f"{rel_dir}/{final}"}


def purge_deleted(days: int = 30) -> int:
    if not is_online():
        return 0
    root = os.path.join(share_root(), DELETED)
    if not os.path.isdir(root):
        return 0
    cutoff = time.time() - days * 86400
    n = 0
    for entry in os.listdir(root):
        p = os.path.join(root, entry)
        try:
            if os.path.getmtime(p) < cutoff:
                shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
                n += 1
        except OSError:
            pass
    return n


def remove_orphan_thumbs(conn) -> int:
    if not is_online():
        return 0
    d = os.path.join(share_root(), THUMBS)
    if not os.path.isdir(d):
        return 0
    known = {r["id"] for r in conn.execute("SELECT id FROM attachments")}
    n = 0
    for f in os.listdir(d):
        if f.endswith(".jpg") and f[:-4] not in known:
            try:
                os.remove(os.path.join(d, f))
                n += 1
            except OSError:
                pass
    return n


def check_missing(conn) -> int:
    """Mark attachments whose file was deleted or moved outside the app (and un-mark ones that came back).
    Nothing is marked while the folder isn't connected."""
    if not is_online():
        return 0
    n = 0
    for r in conn.execute("SELECT id, rel_path, missing FROM attachments WHERE message_id IS NOT NULL").fetchall():
        try:
            present = os.path.isfile(resolve(r["rel_path"]))
        except HTTPException:
            present = False
        if present == bool(r["missing"]):
            conn.execute("UPDATE attachments SET missing = ? WHERE id = ?", (0 if present else 1, r["id"]))
        n += 0 if present else 1
    return n


def remove_stale_parts(max_age_s: int = 86400) -> None:
    """Leftover .upload-*.part files (the app stopped mid-upload)."""
    if not is_online():
        return
    root = share_root()
    cutoff = time.time() - max_age_s
    for dirpath, dirnames, filenames in os.walk(root):
        if os.path.relpath(dirpath, root).split(os.sep)[0] in (DELETED,):
            continue
        for f in filenames:
            if f.startswith(".upload-") and f.endswith(".part"):
                p = os.path.join(dirpath, f)
                try:
                    if os.path.getmtime(p) < cutoff:
                        os.remove(p)
                except OSError:
                    pass




# ---------- disappearing messages (§15.8) ----------
DISAPPEARING = "_disappearing"


def stamp_of(expires_at: str) -> str:
    t = config.parse_iso(expires_at)
    return t.strftime("%Y%m%dT%H%MZ") if t else "00000000T0000Z"


def move_to_disappearing(conn, att_id: str, conv, expires_at: str) -> None:
    """A disappearing message's file lives in the chat's _disappearing folder, named with its expiry
    (`20261003T1805Z__Lease.pdf`), so it's removed on time even if a restore loses its database row."""
    a = conn.execute("SELECT * FROM attachments WHERE id = ?", (att_id,)).fetchone()
    if a is None:
        return
    rel_dir = f"{conv['folder']}/{DISAPPEARING}"
    full_dir = resolve(rel_dir)
    os.makedirs(full_dir, exist_ok=True)
    name = unique_name(full_dir, f"{stamp_of(expires_at)}__{os.path.basename(a['rel_path'])}")
    src = resolve(a["rel_path"])
    if os.path.isfile(src):
        os.replace(src, os.path.join(full_dir, name))
    conn.execute("UPDATE attachments SET rel_path = ? WHERE id = ?", (f"{rel_dir}/{name}", att_id))


def remove_now(rel_path: str) -> None:
    """Straight away, not to _deleted (disappearing files, unsent uploads) — or once the folder is back."""
    if not is_online():
        _queue("remove", rel_path)
        return
    try:
        os.remove(resolve(rel_path))
    except (OSError, HTTPException):
        pass


def remove_disappearing_dir(folder: str) -> None:
    """A deleted chat's _disappearing folder: removed at once, never kept in _deleted."""
    if not is_online():
        if folder:
            _queue("disappearing_dir", folder)
        return
    root = share_root()
    d = os.path.join(root, folder, DISAPPEARING)
    if folder and _inside(root, os.path.join(root, folder)) and os.path.isdir(d) and not os.path.islink(d):
        shutil.rmtree(d, ignore_errors=True)


def sweep_disappearing(now_stamp: str) -> int:
    """Delete every file in any chat's _disappearing folder whose name says it's past due — with or
    without a database row."""
    if not is_online():
        return 0
    root = share_root()
    n = 0
    for entry in os.listdir(root):
        d = os.path.join(root, entry, DISAPPEARING)
        if entry in (DELETED, THUMBS) or not os.path.isdir(d) or os.path.islink(d):
            continue
        for f in os.listdir(d):
            stamp = f.split("__", 1)[0]
            if "__" in f and re.fullmatch(r"\d{8}T\d{4}Z", stamp) and stamp < now_stamp:
                try:
                    os.remove(os.path.join(d, f))
                    n += 1
                except OSError:
                    pass
    return n


def unique_name_in(taken: set, name: str) -> str:
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    candidate, n = name, 2
    while candidate in taken:
        candidate = f"{stem} ({n}).{ext}" if ext else f"{stem} ({n})"
        n += 1
    return candidate
