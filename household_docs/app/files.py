"""Files people bring in and take out (SPEC §3.3, §9.2): uploads, image previews and .zip downloads.

- **Uploads** go into My docs or any folder the person can edit (a folder shared with Can edit, an admin shared
  folder with Read and write). One file per request, the request body is the file itself (no multipart), streamed
  to the root's hidden `.tmp/` with a byte count, so nothing bigger than `upload_mb` is ever kept; then renamed
  into place. The name is cleaned (characters Windows can't hold become "_"). A name already taken gets " (2)"
  unless the person chose *Replace*: then the previous copy is kept as a version (§9.2: 7 days for files,
  History for documents) and the file keeps its id, shares and favourites.
- **Previews** only for PNG, JPEG, GIF and WebP, recognised by their first bytes (never by the name), served
  from the bytes that were checked. Everything else downloads (`attachment`, `nosniff`, `CSP: sandbox`).
- **.zip downloads** of a folder, an admin shared folder or several items, streamed while it's written; names
  inside are built from the path parts the walk found (never "..", never absolute, no backslashes); hidden
  entries and links are left out, every file is realpath-checked inside its root and opened without following
  links. At most ZIP_MAX_FILES files and ZIP_MAX_BYTES bytes, else 413 before anything is sent.
"""
import os
import tempfile
import time
import zipfile
from urllib.parse import quote

from fastapi import HTTPException

from . import config, db, docops, settings, sharing
from .store import fileio, kinds, marker, nodes, paths, roots, versions

PREVIEW_MAX = 25 * 1024 * 1024
ZIP_MAX_FILES = 10_000
ZIP_MAX_BYTES = 4 * 1024 ** 3
ZIP_DEPTH = 12
CHUNK = 1024 * 1024
RASTER_EXTS = ("png", "jpg", "jpeg", "gif", "webp")


# ---------- headers ----------
def disposition(kind: str, name: str) -> str:
    ascii_name = "".join(c if 32 <= ord(c) < 127 and c not in '"\\;' else "_" for c in name) or "download"
    return f"{kind}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"


def download_headers(name: str) -> dict:
    return {"Content-Disposition": disposition("attachment", name), "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox; default-src 'none'", "Cache-Control": "no-store"}


# ---------- previews ----------
def raster_type(head: bytes) -> str | None:
    return kinds.raster_type(head)


def preview(conn, user: dict, node_id: str) -> tuple[bytes, str]:
    """(the image bytes, their type) — 415 when the file isn't one of the raster images."""
    node, _role = sharing.require(conn, user, node_id, "viewer")
    if node["kind"] == "folder":
        raise HTTPException(415, "Folders have no preview.")
    _root, real = nodes.real_path(conn, node)
    if (node["size"] or 0) > PREVIEW_MAX:
        raise HTTPException(413, "This image is too big to preview — download it instead.")
    try:
        fd = fileio.open_fd(real)
    except (OSError, paths.PathError):
        raise HTTPException(404, sharing.NOT_FOUND)
    with os.fdopen(fd, "rb") as f:
        data = f.read(PREVIEW_MAX + 1)
    if len(data) > PREVIEW_MAX:
        raise HTTPException(413, "This image is too big to preview — download it instead.")
    mime = raster_type(data[:16])
    if mime is None:
        raise HTTPException(415, "Only PNG, JPEG, GIF and WebP images are previewed. Download this file instead.")
    return data, mime


# ---------- downloads (a checked descriptor, never the path again) ----------
def _stream_fd(f):
    with f:
        while True:
            chunk = f.read(CHUNK)
            if not chunk:
                break
            yield chunk


def download_response(path: str, name: str, base: str | None = None):
    """The file at `path` as a download: opened once (no link at the end, checked inside /share) and sent from
    that descriptor, so a file or folder swapped for a link after the path check is never what's sent."""
    from fastapi.responses import StreamingResponse
    import stat as stat_mod
    try:
        f = fileio.open_read(path, base)
    except (OSError, paths.PathError):
        raise HTTPException(404, sharing.NOT_FOUND)
    st = os.fstat(f.fileno())
    if not stat_mod.S_ISREG(st.st_mode):
        f.close()
        raise HTTPException(404, sharing.NOT_FOUND)
    headers = dict(download_headers(name), **{"Content-Length": str(st.st_size)})
    return StreamingResponse(_stream_fd(f), media_type="application/octet-stream", headers=headers)


# ---------- uploads ----------
def upload_limit(conn=None) -> int:
    return int(settings.get("upload_mb", conn)) * 1024 * 1024


def clean_upload_name(name: str) -> str:
    """A name from someone's computer → a name the folder can hold (or PathError)."""
    base = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = paths.clean_name(base, "")
    if not cleaned:
        raise paths.PathError("That file has no usable name.")
    return paths.check_name(cleaned)


def prepare_upload(user: dict, ref: str | None, name: str, length: int | None) -> dict:
    """Before reading the body: the place may be written by this person, the name is usable, the size (when
    the browser said it) fits the limit and the quota. Returns what finish_upload needs."""
    try:
        clean = clean_upload_name(name)
    except paths.PathError as e:
        raise HTTPException(422, str(e))
    with db.get_conn() as conn:
        root, folder, _role = docops.target_folder(conn, user, ref)
        limit = upload_limit(conn)
        if length is not None and length > limit:
            raise HTTPException(413, f"That file is bigger than the {settings.get('upload_mb', conn)} MB upload limit.")
        fileio.ensure_writable()
        docops.check_quota(conn, root, length or 0)
        base = roots.root_real(root)
        prel = folder["rel"] if folder else ""
        folder_real = paths.resolve(base, prel)
        if not os.path.isdir(folder_real):
            raise HTTPException(409, "That folder isn't there any more.")
        tmp_dir = roots.hidden_dir(root, ".tmp")
    return {"ref": ref, "name": clean, "limit": limit, "tmp_dir": tmp_dir}


def new_temp(prep: dict) -> tuple[int, str]:
    return tempfile.mkstemp(prefix="up-", suffix=".part", dir=prep["tmp_dir"])


def finish_upload(user: dict, prep: dict, tmp: str, size: int, on_clash: str = "keep") -> str:
    """Put an uploaded temp file in place and index it. Returns the node id."""
    try:
        os.chmod(tmp, fileio.FILE_MODE)
        with db.get_conn() as conn:
            root, folder, _role = docops.target_folder(conn, user, prep["ref"])      # checked again: still allowed?
            fileio.ensure_writable()
            base = roots.root_real(root)
            prel = folder["rel"] if folder else ""
            folder_real = paths.resolve(base, prel)
            limit = int(settings.get("content_index_mb", conn)) * 1024 * 1024
            with fileio.file_lock("folder:" + folder_real):
                name = prep["name"]
                if on_clash == "replace":            # the same name in another case is the same file on Windows
                    try:
                        name = next((x for x in os.listdir(folder_real) if x.casefold() == name.casefold()), name)
                    except OSError:
                        pass
                rel = paths.join_rel(prel, name)
                dest = paths.resolve(base, rel)
                existing = nodes.by_rel(conn, root["id"], rel)
                live = existing is not None and existing["gone_at"] is None and existing["trash_id"] is None
                if (on_clash == "replace" and os.path.isfile(dest) and not os.path.islink(dest)
                        and live and existing["kind"] != "folder"):
                    old_size = os.stat(dest).st_size
                    docops.check_quota(conn, root, size - old_size)
                    with fileio.file_lock(existing["id"]):
                        versions.keep_replaced(conn, root, existing, dest, user["id"])
                        os.replace(tmp, dest)
                        st = os.stat(dest)
                        kind, sha, body, cols = _info(dest, existing["name"], st, existing["kind"], limit)
                        nodes.update_stat(conn, existing["id"], st, sha, updated_by=user["id"], kind=kind, body=body,
                                          set_body=True, stats=cols)
                    db.audit(conn, "replaced", user["id"], existing["id"], root["id"])
                    from . import activity
                    activity.record_id(conn, "edited", user["id"], existing["id"])
                    return existing["id"]
                docops.check_quota(conn, root, size)
                name = paths.unique_name(folder_real, name)
                rel = paths.join_rel(prel, name)
                dest = paths.resolve(base, rel)
                nodes.free_rel(conn, root["id"], rel)
                try:
                    os.link(tmp, dest)            # never over a name someone took meanwhile
                    os.remove(tmp)
                except FileExistsError:
                    raise HTTPException(409, "Something with that name appeared just now — try again.")
                except OSError:
                    if os.path.lexists(dest):
                        raise HTTPException(409, "Something with that name appeared just now — try again.")
                    os.replace(tmp, dest)
                st = os.stat(dest)
                kind, sha, body, cols = _info(dest, name, st, None, limit)
                nid = nodes.insert(conn, root_id=root["id"], rel=rel, parent_id=folder["id"] if folder else None,
                                   kind=kind, st=st, sha=sha, created_by=user["id"], updated_by=user["id"], body=body,
                                   stats=cols)
                db.audit(conn, "uploaded", user["id"], nid, root["id"])
                from . import activity, filing
                activity.record_id(conn, "created", user["id"], nid)
                filing.queue(conn, nid, "upload")         # an arrival: the folder's filing rules (§17.18)
        filing.run_queue()
        return nid
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _info(path, name, st, previous, limit):
    from .store import index
    return index.read_file_info(path, name, st, previous, limit)


# ---------- .zip downloads ----------
def safe_part(name: str) -> str:
    """One path part inside a zip: no separators, no control characters, never "." or ".."."""
    t = "".join("_" if (c in "/\\" or ord(c) < 32 or ord(c) == 127) else c for c in str(name))
    t = t.strip()
    if t in ("", ".", ".."):
        t = "_"
    return t


def _walk_folder(base_real: str, folder_real: str, prefix: str, out: list, totals: dict) -> None:
    """Every file under folder_real (hidden entries and links left out), each realpath-checked inside the root."""
    stack = [(folder_real, prefix, 0)]
    while stack:
        real, arc, depth = stack.pop()
        try:
            entries = sorted(os.scandir(real), key=lambda e: e.name)
        except OSError:
            continue
        for e in entries:
            if e.name.startswith("."):
                continue
            try:
                if e.is_symlink():
                    continue
                if e.is_dir(follow_symlinks=False):
                    if marker.foreign_store(e.path):          # another app's store (§9.3): left out
                        continue
                    if depth + 1 < ZIP_DEPTH:
                        stack.append((e.path, arc + "/" + safe_part(e.name), depth + 1))
                    continue
                if not e.is_file(follow_symlinks=False):
                    continue
                st = e.stat(follow_symlinks=False)
            except OSError:
                continue
            p = os.path.realpath(e.path)
            if not paths.inside(base_real, p):
                continue
            _add(out, totals, p, arc + "/" + safe_part(e.name), st)


def _parent_view(conn, user: dict, root) -> bool:
    """A parent downloading from a child's My docs (§17.20): only what their view reaches goes in the .zip."""
    return (root["kind"] == "person" and root["user_id"] != user["id"]
            and sharing.is_parent_of(conn, user["id"], root["user_id"]))


def _index_entries(conn, user: dict, root, base_real: str, folder, prefix: str, out: list, totals: dict) -> None:
    """The files under `folder` (None: the root's top) that the person may open, from the index."""
    if folder is None:
        rows = conn.execute(f"SELECT * FROM nodes n WHERE n.root_id = ? AND n.kind != 'folder' AND {nodes.LIVE}",
                            (root["id"],)).fetchall()
        cut = 0
    else:
        rows = conn.execute(f"SELECT * FROM nodes n WHERE n.root_id = ? AND substr(n.rel, 1, ?) = ? "
                            f"AND n.kind != 'folder' AND {nodes.LIVE}",
                            (root["id"], len(folder["rel"]) + 1, folder["rel"] + "/")).fetchall()
        cut = len(folder["rel"]) + 1
    for r in sorted(rows, key=lambda x: x["rel"]):
        if sharing.role_of(conn, user, r) is None:
            continue
        try:
            real = paths.resolve(base_real, r["rel"])
            roots.check_unmarked(base_real, real)
            st = os.lstat(real)
        except (OSError, paths.PathError):
            continue
        if not os.path.isfile(real) or os.path.islink(real):
            continue
        arc = prefix + "/" + "/".join(safe_part(p) for p in r["rel"][cut:].split("/"))
        _add(out, totals, real, arc, st)


def _add(out, totals, real, arc, st) -> None:
    totals["files"] += 1
    totals["bytes"] += st.st_size
    if totals["files"] > ZIP_MAX_FILES:
        raise HTTPException(413, f"That's more than {ZIP_MAX_FILES:,} files — download smaller parts.")
    if totals["bytes"] > ZIP_MAX_BYTES:
        raise HTTPException(413, f"That's more than {ZIP_MAX_BYTES // 1024 ** 3} GB — download smaller parts.")
    out.append((real, arc, st.st_size, st.st_mtime))


def _unique_arc(name: str, taken: set) -> str:
    if name.casefold() not in taken:
        taken.add(name.casefold())
        return name
    stem, ext = paths.split_ext(name)
    n = 2
    while True:
        cand = f"{stem} ({n})" + (f".{ext}" if ext else "")
        if cand.casefold() not in taken:
            taken.add(cand.casefold())
            return cand
        n += 1


def zip_plan(conn, user: dict, refs: list[str]) -> tuple[list, str]:
    """What a .zip will hold — [(real path, name inside, size, mtime)] — and its file name. Every item is
    checked for the person first (404 for anything they can't open)."""
    out, totals, taken = [], {"files": 0, "bytes": 0}, set()
    names = []
    for ref in refs:
        if ref.startswith(docops.ROOT_REF) or not ref:
            root, _folder, _role = docops.place(conn, user, ref, "viewer")
            base = roots.root_real(root)
            label = root["label"] if root["kind"] == "shared" else "My docs"
            top = _unique_arc(safe_part(label), taken)
            names.append(label)
            if _parent_view(conn, user, root):
                _index_entries(conn, user, root, base, None, top, out, totals)
            else:
                _walk_folder(base, base, top, out, totals)
            continue
        node, _role = sharing.require(conn, user, ref, "viewer")
        root, real = nodes.real_path(conn, node)
        base = roots.root_real(root)
        names.append(node["name"])
        top = _unique_arc(safe_part(node["name"]), taken)
        if node["kind"] == "folder":
            if not os.path.isdir(real) or os.path.islink(real):
                raise HTTPException(404, sharing.NOT_FOUND)
            if _parent_view(conn, user, root):
                _index_entries(conn, user, root, base, node, top, out, totals)
            else:
                _walk_folder(base, real, top, out, totals)
        else:
            try:
                st = os.lstat(real)
            except OSError:
                raise HTTPException(404, sharing.NOT_FOUND)
            if not os.path.isfile(real) or os.path.islink(real):
                raise HTTPException(404, sharing.NOT_FOUND)
            _add(out, totals, real, top, st)
    if len(names) == 1:
        zip_name = names[0] + ".zip"
    else:
        zip_name = f"Household Docs {config.now().strftime('%Y-%m-%d')}.zip"
    return out, zip_name


class _Sink:
    """A write-only target for zipfile (no seek, no tell): what was written waits here to be sent."""
    def __init__(self):
        self.buf = bytearray()

    def write(self, b) -> int:
        self.buf += b
        return len(b)

    def flush(self) -> None:
        pass

    def take(self) -> bytes:
        out = bytes(self.buf)
        self.buf.clear()
        return out


def _zip_time(mtime: float):
    t = time.gmtime(max(mtime, 315532800 + 86400))       # zip times start in 1980
    return t[:6]


def stream_zip(entries):
    """The .zip, a chunk at a time (written while it's sent)."""
    sink = _Sink()
    with zipfile.ZipFile(sink, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as z:
        for real, arc, size, mtime in entries:
            try:
                fd = fileio.open_fd(real)
            except (OSError, paths.PathError):
                continue                       # gone (or swapped for a link) since the plan: left out
            zi = zipfile.ZipInfo(arc, date_time=_zip_time(mtime))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.file_size = size
            zi.external_attr = 0o644 << 16
            with os.fdopen(fd, "rb") as f, z.open(zi, "w", force_zip64=size > 2 ** 31 - CHUNK) as w:
                while True:
                    chunk = f.read(CHUNK)
                    if not chunk:
                        break
                    w.write(chunk)
                    if len(sink.buf) >= CHUNK:
                        yield sink.take()
            if sink.buf:
                yield sink.take()
    if sink.buf:
        yield sink.take()
