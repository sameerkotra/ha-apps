"""Media storage on /share (§5.1) and the image upload pipeline (§4).

Layout under the photo folder (the `media_path` App setting, read live —
changing it in Admin → App settings re-runs check() against the new folder):
    media/<id[0:2]>/<id>/original          re-encoded image (or the PDF)
    media/<id[0:2]>/<id>/thumb1024.jpg
    media/<id[0:2]>/<id>/thumb256.jpg
    .family_tree_store                     marker: this tree's store id

The app never writes into a folder whose marker doesn't match the database's
`media_store_id`: an unmounted NAS path would otherwise silently fill HA's own
disk and be hidden once the mount comes back. That state is "offline".
"""
import hashlib
import io
import logging
import os
import re
import shutil
import threading
import warnings
from datetime import datetime

from . import config, db, settings

logger = logging.getLogger("media")

MARKER = ".family_tree_store"
MAX_EDGE = 4096
MAX_PIXELS = 60_000_000
THUMBS = (256, 1024)
_ID_RE = re.compile(r"^[0-9a-f]{32}$")

_state_lock = threading.Lock()
_check_lock = threading.Lock()          # one check() at a time (first-start set-up writes a store id)
_state = {"online": False, "reason": "Not checked yet.", "checked_at": None, "store_id": None, "foreign_id": None,
          "path": None}


# ---------- where things are ----------
def root() -> str:
    """The photo folder in use now (App setting `media_path`)."""
    return settings.get("media_path")


def valid_id(media_id) -> bool:
    return isinstance(media_id, str) and bool(_ID_RE.match(media_id))


def _dir_for(media_id: str) -> str:
    # Ids come from the database, which a restored backup can replace: never let
    # one like "../.." turn into a path outside media/ (remove_files rmtree's it).
    if not valid_id(media_id):
        raise ValueError(f"Invalid media id {media_id!r}")
    return os.path.join(root(), "media", media_id[:2], media_id)


def file_path(media_id: str, size: str = "original") -> str:
    name = {"original": "original", "1024": "thumb1024.jpg", "256": "thumb256.jpg"}[size]
    return os.path.join(_dir_for(media_id), name)


def path_allowed(path: str) -> bool:
    return config.ALLOW_ANY_MEDIA_PATH or path == "/share" or path.startswith("/share/")


def is_network_mount(path: str) -> bool:
    """Best effort: is `path` (or a parent) a network filesystem mount?"""
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


# ---------- online / offline ----------
def status() -> dict:
    with _state_lock:
        return dict(_state)


def is_online() -> bool:
    """Online means: the last check passed *for the folder in use now*. After the
    photo folder changes, nothing counts as online until check() has run on it."""
    current = root()
    with _state_lock:
        return _state["online"] and _state["path"] == current


def _set(online: bool, reason: str | None, store_id=None, foreign_id=None, path=None):
    with _state_lock:
        _state.update(online=online, reason=reason, checked_at=config.now_iso(),
                      store_id=store_id, foreign_id=foreign_id, path=path)
    if not online:
        logger.warning("Media offline: %s", reason)


def _db_state() -> tuple[str | None, int]:
    """(media_store_id, number of media rows incl. trashed), in one short connection."""
    with db.get_conn() as conn:
        return (db.get_setting(conn, "media_store_id"),
                conn.execute("SELECT COUNT(*) FROM media").fetchone()[0])


def _save_store_id(store_id: str) -> None:
    with db.get_conn() as conn:
        db.set_setting(conn, "media_store_id", store_id)


def _count_originals(path: str) -> int:
    n = 0
    for _dirpath, _dirs, files in os.walk(os.path.join(path, "media")):
        n += sum(1 for fn in files if fn == "original")
    return n


def _other_tree(path: str) -> str:
    return (f"{path} belongs to another Family Tree (its {MARKER} marker is from a different install). "
            "If those are this tree's photos, use “Use this folder” in Admin → Storage.")


def _no_photos_here(path: str, n: int) -> str:
    return (f"{path} doesn't hold this tree's photos (no {MARKER} marker), but the tree has "
            f"{n} photo{'s' if n != 1 else ''} or document{'s' if n != 1 else ''}. Copy the whole old photo folder, "
            f"including {MARKER}, to {path} — or change the photo folder back in Admin → App settings.")


def _read_marker(path: str) -> str | None:
    try:
        with open(os.path.join(path, MARKER), encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None


def _write_marker(path: str, store_id: str) -> None:
    tmp = os.path.join(path, MARKER + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(store_id + "\n")
    os.replace(tmp, os.path.join(path, MARKER))


def check(allow_setup: bool = False) -> dict:
    """Decide online/offline for the folder in use now.

    The very first start creates the folder (only if its parent exists) and the
    marker. Afterwards a missing or different marker means offline, never
    "create a new one" — an unmounted NAS path must not fill HA's own disk.
    `allow_setup` is passed only right after an admin saved a new photo folder:
    then a folder without a marker is set up for this store — but only while the
    tree has no photos at all (otherwise it stays offline and says why).
    The database is only touched in short connections, never during file-system
    work on a possibly slow NAS path."""
    with _check_lock:
        path = root()
        if not path_allowed(path):
            _set(False, f"The photo folder must be inside /share (it is {path}). Change it in Admin → App settings.",
                 path=path)
            return status()
        store_id, n_media = _db_state()
        if store_id is None or allow_setup:
            parent = os.path.dirname(path.rstrip("/")) or "/"
            existing = _read_marker(path) if os.path.isdir(path) else None
            if existing is None:
                if n_media:
                    if store_id is None:
                        _set(False, _no_photos_here(path, n_media), path=path)
                        return status()
                else:
                    if not os.path.isdir(path):
                        if not os.path.isdir(parent):
                            _set(False, f"The folder {parent} doesn't exist. Is the share or network storage mounted?",
                                 store_id, path=path)
                            return status()
                        try:
                            os.makedirs(path, exist_ok=True)
                        except OSError as e:
                            _set(False, f"Couldn't create {path}: {e.strerror}.", store_id, path=path)
                            return status()
                    new_store = store_id or db.new_id()
                    try:
                        _write_marker(path, new_store)
                    except OSError as e:
                        _set(False, f"Couldn't write to {path}: {e.strerror}.", store_id, path=path)
                        return status()
                    if store_id is None:
                        _save_store_id(new_store)
                    store_id = new_store
                    logger.info("Media storage set up at %s", path)
            elif store_id is None:
                _set(False, _other_tree(path), foreign_id=existing, path=path)
                return status()
        moved = (f" If you just changed the photo folder, copy the whole old folder there, including {MARKER}."
                 if n_media else "")
        if not os.path.isdir(path):
            _set(False, f"Photo storage at {path} isn't reachable: the folder doesn't exist. "
                        f"Is the share or network storage mounted?{moved}", store_id, path=path)
            return status()
        marker = _read_marker(path)
        if marker is None:
            _set(False, f"Photo storage at {path} isn't reachable (its {MARKER} marker is missing — "
                        f"is the network storage mounted?).{moved}", store_id, path=path)
            return status()
        if marker != store_id:
            _set(False, _other_tree(path), store_id, foreign_id=marker, path=path)
            return status()
        if not os.access(path, os.W_OK):
            _set(False, f"Photo storage at {path} is read-only.", store_id, path=path)
            return status()
        _set(True, None, store_id, path=path)
        return status()


def inspect(path: str) -> dict:
    """What a candidate photo folder holds, for Admin → App settings. No side
    effects: nothing is created or written. `path` must already be validated."""
    store_id, n_media = _db_state()                 # short DB read first; the scan below may be slow
    current = root()
    exists = os.path.isdir(path)
    parent = os.path.dirname(path.rstrip("/")) or "/"
    parent_exists = os.path.isdir(parent)
    marker_value = _read_marker(path) if exists else None
    marker = "none" if marker_value is None else ("this" if store_id and marker_value == store_id else "other")
    writable = os.access(path if exists else parent, os.W_OK) if (exists or parent_exists) else False
    files = _count_originals(path) if exists else 0
    photos = f"{n_media} photo{'s' if n_media != 1 else ''} or document{'s' if n_media != 1 else ''}"
    refused = needs_confirm = False
    if path == current:
        verdict, message = "current", "This is the photo folder in use now."
    elif marker == "other":
        verdict, refused = "other", True
        message = (f"This folder belongs to another Family Tree (its {MARKER} marker is from a different install), "
                   f"so it can't be used here. Pick another folder.")
    elif marker == "this":
        verdict = "this"
        message = f"This folder holds this tree's photos ({files} photo file{'s' if files != 1 else ''}). Safe to switch."
        if not writable:
            message += " It's read-only, though: photo storage would be offline until it's writable."
    elif n_media:
        verdict, needs_confirm = "no_marker", True
        message = ((f"This folder has {files} photo files but no {MARKER} marker. " if files else
                    "This folder doesn't hold this tree's photos. " if exists else "This folder doesn't exist yet. ")
                   + f"Your {photos} {'stays' if n_media == 1 else 'stay'} in the old folder ({current}). Copy the whole folder, including {MARKER}, "
                     "to the new place first — otherwise photos will show as unavailable until you do.")
    elif exists:
        verdict = "new"
        message = "This folder has no Family Tree photos yet. It will be set up for this tree when you save."
        if not writable:
            verdict, message = "read_only", "This folder is read-only, so photos couldn't be stored there."
    elif parent_exists:
        verdict = "new"
        message = "This folder doesn't exist yet. It will be created when you save."
        if not writable:
            verdict, message = "read_only", f"{parent} is read-only, so the folder couldn't be created."
    else:
        verdict = "missing_parent"
        message = (f"Neither this folder nor {parent} exists. Is the share or network storage mounted? "
                   "Photo storage will be offline until it is.")
    return {"path": path, "current": current, "exists": exists, "parentExists": parent_exists, "writable": writable,
            "marker": marker, "files": files, "dbMedia": n_media, "verdict": verdict, "message": message,
            "ok": verdict in ("current", "this", "new"), "needsConfirm": needs_confirm, "refused": refused,
            "networkMount": is_network_mount(path)}


def adopt_foreign() -> dict:
    """Admin 'Use this folder': take over the marker found in media_path
    (e.g. after restoring the database onto a fresh HA)."""
    marker = _read_marker(root())
    if not marker:
        raise ValueError("There's no Family Tree marker in that folder to adopt.")
    _save_store_id(marker)
    return check()


def require_online() -> None:
    from fastapi import HTTPException
    if not is_online():
        check()
    if not is_online():
        raise HTTPException(503, status()["reason"] or "Photo storage isn't reachable.")


def storage_info() -> dict:
    s = status()
    here = s["path"] == root()                    # the last check was for the folder in use now
    info = {"path": root(), "online": here and s["online"], "reason": s["reason"] if here else "Not checked yet.",
            "checkedAt": s["checked_at"], "foreignMarker": here and bool(s["foreign_id"]),
            "networkMount": is_network_mount(root()), "files": 0, "bytes": 0, "freeBytes": None}
    if info["online"]:
        try:
            usage = shutil.disk_usage(root())
            info["freeBytes"] = usage.free
        except OSError:
            pass
        base = os.path.join(root(), "media")
        for dirpath, _dirs, files in os.walk(base):
            for fn in files:
                if fn == "original":
                    info["files"] += 1
                try:
                    info["bytes"] += os.path.getsize(os.path.join(dirpath, fn))
                except OSError:
                    pass
    return info


# ---------- upload pipeline ----------
class MediaError(ValueError):
    def __init__(self, status: int, msg: str):
        super().__init__(msg)
        self.status = status


def sniff(data: bytes) -> str | None:
    """Content type from magic bytes only — never from the name or header."""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:5] == b"%PDF-":
        return "application/pdf"
    if data[4:8] == b"ftyp" and data[8:12] in (b"heic", b"heix", b"mif1", b"msf1", b"heim", b"heis"):
        return "image/heic"
    return None


def _exif_date(img) -> str | None:
    """Original date taken → a GEDCOM-style date text, read before EXIF is dropped."""
    try:
        exif = img.getexif()
        raw = exif.get_ifd(0x8769).get(36867) or exif.get(306)       # DateTimeOriginal, DateTime
        if not raw:
            return None
        dt = datetime.strptime(str(raw).strip()[:19], "%Y:%m:%d %H:%M:%S")
        return f"{dt.day} {['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'][dt.month-1]} {dt.year}"
    except Exception:
        return None


def process_image(data: bytes) -> dict:
    """Re-encode (drops all metadata incl. GPS), cap at 4096 px, make thumbnails.
    Returns {content_type, original: bytes, thumbs: {256: bytes, 1024: bytes}, width, height, date_text}."""
    from PIL import Image, ImageOps
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        # only the formats sniff() lets through, whatever Pillow could guess from the bytes
        with warnings.catch_warnings():          # oversize is refused just below, not warned about
            warnings.simplefilter("ignore", Image.DecompressionBombWarning)
            img = Image.open(io.BytesIO(data), formats=["JPEG", "PNG", "WEBP", "GIF"])
    except Image.DecompressionBombError:
        raise MediaError(413, "That image is too large to process.")
    except Exception:
        raise MediaError(415, "That image couldn't be read — it may be damaged.")
    # Pillow only *errors* above twice MAX_IMAGE_PIXELS (it just warns in between),
    # so check the header's size ourselves before decoding anything.
    if img.width * img.height > MAX_PIXELS:
        raise MediaError(413, "That image is too large to process.")
    try:
        if img.format == "JPEG":
            img.draft("RGB", (MAX_EDGE, MAX_EDGE))     # decode big JPEGs at a reduced scale
        img.load()
    except Image.DecompressionBombError:
        raise MediaError(413, "That image is too large to process.")
    except Exception:
        raise MediaError(415, "That image couldn't be read — it may be damaged.")
    date_text = _exif_date(img)
    try:
        img = ImageOps.exif_transpose(img)          # apply the camera's rotation before EXIF goes
    except Exception:
        pass
    if getattr(img, "is_animated", False):
        img.seek(0)
    has_alpha = img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info)
    img = img.convert("RGBA" if has_alpha else "RGB")
    img.thumbnail((MAX_EDGE, MAX_EDGE))
    out = io.BytesIO()
    if has_alpha:
        img.save(out, "PNG", optimize=True)
        ctype = "image/png"
    else:
        img.save(out, "JPEG", quality=90, optimize=True)
        ctype = "image/jpeg"
    flat = img
    if has_alpha:
        flat = Image.new("RGB", img.size, (255, 255, 255))
        flat.paste(img, mask=img.split()[-1])
    thumbs = {}
    for size in THUMBS:
        t = flat.copy()
        t.thumbnail((size, size))
        b = io.BytesIO()
        t.save(b, "JPEG", quality=85)
        thumbs[size] = b.getvalue()
    return {"content_type": ctype, "original": out.getvalue(), "thumbs": thumbs,
            "width": img.width, "height": img.height, "date_text": date_text}


def too_large_message(limit_mb: int) -> str:
    return f"That file is larger than {limit_mb} MB (the upload limit in Admin → App settings)."


def validate_upload(data: bytes, allow_documents: bool = False) -> str:
    limit_mb = settings.get("max_upload_mb")
    if len(data) > limit_mb * 1024 * 1024:
        raise MediaError(413, too_large_message(limit_mb))
    if not data:
        raise MediaError(415, "That file is empty.")
    ctype = sniff(data)
    if ctype == "image/heic":
        raise MediaError(415, "HEIC photos aren't supported yet — share the photo as JPEG (iPhone: Settings → "
                              "Camera → Formats → Most Compatible, or pick it from Photos, which converts it).")
    if ctype == "application/pdf" and not allow_documents:
        raise MediaError(415, "Only photos (JPEG, PNG, WebP or GIF) can be used here.")
    if ctype is None:
        raise MediaError(415, "That file type isn't allowed. Use JPEG, PNG, WebP or GIF"
                              + (", or PDF for documents." if allow_documents else "."))
    return ctype


def _atomic_write(path: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def store_image(media_id: str, processed: dict) -> None:
    _atomic_write(file_path(media_id, "original"), processed["original"])
    for size, blob in processed["thumbs"].items():
        _atomic_write(file_path(media_id, str(size)), blob)


def store_document(media_id: str, data: bytes) -> None:
    """PDFs are kept as uploaded (served only as a download, never inline)."""
    _atomic_write(file_path(media_id, "original"), data)


# ---- edited versions (§13.19): rendered from original + recipe, cached per recipe ----
_EDIT_NAMES = {"display": "display-{k}.jpg", "1024": "thumb1024-{k}.jpg", "256": "thumb256-{k}.jpg"}


def edited_path(media_id: str, edit: dict, size: str) -> str:
    from . import photoedit
    return os.path.join(_dir_for(media_id), _EDIT_NAMES[size].format(k=photoedit.key(edit)))


def ensure_edited(media_id: str, edit: dict) -> None:
    """Render the edited display file and thumbnails if they aren't there yet;
    older recipes' files are removed."""
    from PIL import Image
    from . import photoedit
    k = photoedit.key(edit)
    want = {size: edited_path(media_id, edit, size) for size in _EDIT_NAMES}
    if all(os.path.isfile(p) for p in want.values()):
        return
    with Image.open(file_path(media_id, "original")) as src:
        src.load()
        img = photoedit.render(src.convert("RGB"), edit)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    _atomic_write(want["display"], buf.getvalue())
    for size in THUMBS:
        t = img.copy()
        t.thumbnail((size, size))
        b = io.BytesIO()
        t.save(b, "JPEG", quality=85)
        _atomic_write(want[str(size)], b.getvalue())
    d = _dir_for(media_id)
    for name in os.listdir(d):
        if re.match(r"^(display|thumb1024|thumb256)-[0-9a-f]{12}\.jpg$", name) and k not in name:
            try:
                os.remove(os.path.join(d, name))
            except OSError:
                pass


def served_path(media_id: str, size: str, edit: dict | None) -> str:
    """The file to show: the edited version when there's a recipe ("display" is
    the full-size edited picture; "original" is always the file as uploaded)."""
    if not edit or size == "original":
        return file_path(media_id, "original" if size == "display" else size)
    ensure_edited(media_id, edit)
    return edited_path(media_id, edit, size)


def remove_files(media_id: str) -> None:
    if not valid_id(media_id):
        logger.warning("Not removing files for invalid media id %r", media_id)
        return
    shutil.rmtree(_dir_for(media_id), ignore_errors=True)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def check_consistency() -> dict:
    """Admin media check: DB rows without files, and files without DB rows."""
    base = os.path.join(root(), "media")
    on_disk = set()
    if os.path.isdir(base):
        for shard in os.listdir(base):
            sd = os.path.join(base, shard)
            if os.path.isdir(sd):
                on_disk.update(d for d in os.listdir(sd) if os.path.isdir(os.path.join(sd, d)))
    with db.get_conn() as conn:
        rows = {r["id"] for r in conn.execute("SELECT id FROM media")}
    missing = sorted(m for m in rows if not valid_id(m) or not os.path.isfile(file_path(m)))
    orphans = sorted(on_disk - rows)
    return {"missing": missing, "orphans": orphans}


def move_orphans(ids: list) -> int:
    dest_root = os.path.join(root(), "orphans")
    n = 0
    for mid in ids:
        if not valid_id(mid):
            continue
        src = _dir_for(mid)
        if os.path.isdir(src):
            os.makedirs(dest_root, exist_ok=True)
            shutil.move(src, os.path.join(dest_root, mid))
            n += 1
    return n
