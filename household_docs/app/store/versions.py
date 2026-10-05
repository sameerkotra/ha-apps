"""Versions (SPEC §7.3, §9.2): before the app overwrites a document, the file as it is now is copied to
`.versions/<node-id>/<n>.<ext>` (the root's hidden .versions folder) — at most one per editor every 5 minutes,
plus every conflict, restore, and every first save after a change made outside the app (so outside edits are
in History too). Up to `versions_kept`; older ones are deleted. Versions are keyed by node id, so they follow
the document through renames and moves. Only documents get versions — except that replacing an uploaded file
keeps the previous copy (`keep_replaced`) for REPLACED_DAYS days (`prune_replaced`, housekeeping).
"""
import hashlib
import os
import shutil
from datetime import timedelta

from .. import config, settings
from . import fileio, kinds, paths, roots

EDITOR_WINDOW = timedelta(minutes=5)
REPLACED_DAYS = 7


def version_dir(root, node_id: str) -> str:
    return os.path.join(roots.hidden_dir(root, ".versions"), node_id)


def _next_n(conn, node_id: str) -> int:
    r = conn.execute("SELECT MAX(n) FROM versions WHERE node_id = ?", (node_id,)).fetchone()
    return (r[0] or 0) + 1


def _open_source(real: str):
    """(open file, its stat, its SHA-256) — opened once, safely (fileio.open_read: no link at the end, checked inside
    /share); the version is then copied from this same descriptor, never from the path again (security review
    2026-10). None when it can't be opened."""
    try:
        f = fileio.open_read(real)
    except (OSError, paths.PathError):
        return None
    st = os.fstat(f.fileno())
    h = hashlib.sha256()
    while True:
        chunk = f.read(1024 * 1024)
        if not chunk:
            break
        h.update(chunk)
    return f, st, h.hexdigest()


def _store(f, d: str, name: str) -> None:
    """Copy the open file `f` (from its start) to `d/name`, through a temporary name, relative to the version
    folder's checked descriptor (like fileio.write_atomic)."""
    os.makedirs(d, exist_ok=True)
    dfd = fileio.open_dir(d)
    tmp = f".{name}.part"
    try:
        f.seek(0)
        out = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | fileio.NOFOLLOW | fileio.CLOEXEC, 0o664, dir_fd=dfd)
        try:
            with os.fdopen(out, "wb") as w:
                shutil.copyfileobj(f, w, 1024 * 1024)
                w.flush()
                os.fsync(w.fileno())
            os.replace(tmp, name, src_dir_fd=dfd, dst_dir_fd=dfd)
        except BaseException:
            try:
                os.unlink(tmp, dir_fd=dfd)
            except OSError:
                pass
            raise
    finally:
        os.close(dfd)


def snapshot(conn, root, node, real: str, user_id: str | None, *, force: bool = False) -> int | None:
    """Keep the current file as a version before it is overwritten, when the rules say so. Returns its number."""
    if not kinds.is_document(node["kind"]):
        return None
    src = _open_source(real)
    if src is None:
        return None
    f, st, sha = src
    with f:
        return _snapshot(conn, root, node, f, st, sha, user_id, force)


def _snapshot(conn, root, node, f, st, sha, user_id, force) -> int | None:
    if st.st_size == 0:
        return None
    if conn.execute("SELECT 1 FROM versions WHERE node_id = ? AND sha256 = ?", (node["id"], sha)).fetchone():
        return None                                  # this content is in History already
    author = node["updated_by"] if (node["sha256"] == sha and node["mtime_ns"] == st.st_mtime_ns) else None
    if not force and author is not None and author == user_id:
        cutoff = (config.utcnow() - EDITOR_WINDOW).isoformat(timespec="seconds")
        recent = conn.execute("SELECT 1 FROM versions WHERE node_id = ? AND saved_by = ? AND created_at >= ?",
                              (node["id"], user_id, cutoff)).fetchone()
        if recent:
            return None
    n = _next_n(conn, node["id"])
    d = version_dir(root, node["id"])
    ext = node["ext"] or "bin"
    name = f"{n}.{ext}"
    _store(f, d, name)
    conn.execute("INSERT INTO versions (node_id, n, file, size, sha256, made_by, saved_by, created_at) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (node["id"], n, name, st.st_size, sha, author, user_id,
                                                     config.iso_of(st.st_mtime) if author is None else config.now_iso()))
    prune(conn, root, node["id"])
    return n


def keep_replaced(conn, root, node, real: str, user_id: str | None) -> int | None:
    """An upload is about to replace this file: keep the current copy (any kind of file) as a version, whatever
    the 5-minute rule says. Documents keep it like any version; other files for REPLACED_DAYS days."""
    if kinds.is_document(node["kind"]):
        return snapshot(conn, root, node, real, user_id, force=True)
    src = _open_source(real)
    if src is None:
        return None
    f, st, sha = src
    with f:
        return _keep(conn, root, node, f, st, sha, user_id)


def _keep(conn, root, node, f, st, sha, user_id) -> int | None:
    if conn.execute("SELECT 1 FROM versions WHERE node_id = ? AND sha256 = ?", (node["id"], sha)).fetchone():
        return None
    author = node["updated_by"] if (node["sha256"] == sha and node["mtime_ns"] == st.st_mtime_ns) else None
    n = _next_n(conn, node["id"])
    d = version_dir(root, node["id"])
    name = f"{n}.{node['ext'] or 'bin'}"
    _store(f, d, name)
    conn.execute("INSERT INTO versions (node_id, n, file, size, sha256, made_by, saved_by, created_at) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (node["id"], n, name, st.st_size, sha, author, user_id, config.now_iso()))
    prune(conn, root, node["id"])
    return n


def prune_replaced(days: int = REPLACED_DAYS) -> int:
    """Housekeeping: previous copies of replaced files (not documents) go after `days` days (paused while the
    documents are moved, §5.7)."""
    from .. import db
    from . import moving
    if moving.is_read_only():
        return 0
    cutoff = config.ago_iso(days=days)
    done = 0
    doc_kinds = [k.id for k in kinds.all_kinds() if k.document]
    with db.get_conn() as conn:
        q = ",".join("?" * len(doc_kinds))
        rows = conn.execute(f"SELECT v.node_id, v.n, v.file, n.root_id FROM versions v JOIN nodes n ON n.id = v.node_id "
                            f"WHERE v.created_at < ? AND n.kind NOT IN ({q})", (cutoff, *doc_kinds)).fetchall()
        for r in rows:
            root = roots.root_row(conn, r["root_id"])
            try:
                os.remove(os.path.join(version_dir(root, r["node_id"]), os.path.basename(r["file"])))
            except FileNotFoundError:
                pass
            except Exception:
                continue                     # storage away: try again next time
            conn.execute("DELETE FROM versions WHERE node_id = ? AND n = ?", (r["node_id"], r["n"]))
            done += 1
    return done


def path_of(conn, root, node_id: str, n: int) -> str:
    """The real path of one kept version's file (FileNotFoundError when it isn't there)."""
    r = conn.execute("SELECT file FROM versions WHERE node_id = ? AND n = ?", (node_id, n)).fetchone()
    if r is None:
        raise FileNotFoundError(n)
    path = os.path.join(version_dir(root, node_id), os.path.basename(r["file"]))
    if not os.path.isfile(path):
        raise FileNotFoundError(n)
    return path


def prune(conn, root, node_id: str, keep: int | None = None) -> int:
    keep = keep if keep is not None else int(settings.get("versions_kept", conn))
    old = conn.execute("SELECT n, file FROM versions WHERE node_id = ? ORDER BY n DESC LIMIT -1 OFFSET ?",
                       (node_id, keep)).fetchall()
    d = version_dir(root, node_id)
    for r in old:
        try:
            os.remove(os.path.join(d, r["file"]))
        except OSError:
            pass
        conn.execute("DELETE FROM versions WHERE node_id = ? AND n = ?", (node_id, r["n"]))
    return len(old)


def listing(conn, node_id: str) -> list[dict]:
    rows = conn.execute("SELECT v.*, u.name AS who FROM versions v LEFT JOIN users u ON u.id = v.made_by "
                        "WHERE v.node_id = ? ORDER BY v.n DESC", (node_id,)).fetchall()
    return [{"n": r["n"], "size": r["size"], "createdAt": r["created_at"], "madeBy": r["made_by"],
             "who": r["who"] if r["made_by"] else None, "outside": r["made_by"] is None} for r in rows]


def read(conn, root, node_id: str, n: int) -> bytes:
    r = conn.execute("SELECT file FROM versions WHERE node_id = ? AND n = ?", (node_id, n)).fetchone()
    if r is None:
        raise FileNotFoundError(n)
    path = os.path.join(version_dir(root, node_id), os.path.basename(r["file"]))
    with fileio.open_read(path) as f:
        return f.read()


def remove_all(root, node_id: str) -> None:
    """When a node goes for good (Trash emptied)."""
    try:
        shutil.rmtree(version_dir(root, node_id), ignore_errors=True)
    except Exception:
        pass
