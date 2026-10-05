"""Writing files (SPEC §7.1): every write goes to the root's `.tmp/` on the same file system, is fsync'd and
then `os.replace`d onto the target, so nobody (Samba, another app, a backup) ever sees half a file. A per-file
lock in the app serialises writes from different people. Files are made with mode 0664.

Etag = size + mtime_ns + SHA-256 of the content; every save sends the etag it started from, and a save whose
etag no longer matches the file on disk (changed in the app or outside it) is a conflict (§7.2).

Opening files (security review 2026-10): `open_fd` / `open_read` open without following a link at the last part
and check where the descriptor really is (inside /share), and `write_atomic` makes the final link / rename relative
to the target folder's descriptor — so a folder swapped for a link (over Samba) after the path check can't make the
app read or write /data (SPEC §3.3).

`WRITE_GUARDS` are checked before every write: functions that raise (an HTTPException) to stop it — the
documents folder must be usable (§5.6), and read-only mode must be off while documents are moved (§5.7,
store/moving.py).
"""
import hashlib
import os
import tempfile
import threading
from contextlib import contextmanager

from .. import config
from . import paths, roots

FILE_MODE = 0o664
NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_locks: dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()


def _docs_folder_usable() -> None:
    roots.docs_real()


def _not_moving() -> None:
    """Read-only mode while the documents are moved (§5.7): every write is refused (423)."""
    from . import moving
    moving.guard()


WRITE_GUARDS: list = [_not_moving, _docs_folder_usable]


def ensure_writable() -> None:
    for guard in WRITE_GUARDS:
        guard()


@contextmanager
def file_lock(key: str):
    """Serialise changes to one file (keyed by node id, or a path for files not indexed yet)."""
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = _locks[key] = threading.RLock()
    with lock:
        yield


# ---------- opening files safely (TOCTOU) ----------
def _opened_path(fd: int) -> str | None:
    """Where an open file really is (Linux: /proc/self/fd), or None when that can't be told."""
    try:
        return os.readlink(f"/proc/self/fd/{fd}")
    except OSError:
        return None


def check_opened(fd: int, path: str, base: str | None = None) -> None:
    """What was opened must be inside `base` (default: /share). A folder on the way swapped for a link (over
    Samba) between the path check and the open would otherwise let the app read or write /data."""
    where = _opened_path(fd) or os.path.realpath(path)
    if not paths.inside(base or config.share_root(), where):
        raise paths.PathError("That file isn't where it should be.")


def open_fd(path: str, flags: int = os.O_RDONLY, base: str | None = None) -> int:
    """os.open without following a link at the last part, then checked inside /share (or `base`)."""
    fd = os.open(path, flags | NOFOLLOW | CLOEXEC)
    try:
        check_opened(fd, path, base)
    except BaseException:
        os.close(fd)
        raise
    return fd


def open_read(path: str, base: str | None = None):
    """A file in /share opened for reading, safely (open_fd)."""
    return os.fdopen(open_fd(path, os.O_RDONLY, base), "rb")


def sha256_file(path: str, limit: int | None = None) -> str | None:
    """SHA-256 of the file's content (None past `limit` bytes)."""
    h = hashlib.sha256()
    n = 0
    with open_read(path) as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            n += len(chunk)
            if limit is not None and n > limit:
                return None
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_etag(size: int, mtime_ns: int, sha: str | None) -> str:
    return f"{size}-{mtime_ns}-{(sha or '')[:20]}"


def current_etag(path: str) -> tuple[str, os.stat_result, str]:
    """(etag, stat, sha256) of the file as it is on disk now."""
    with open_read(path) as f:
        st = os.fstat(f.fileno())
        h = hashlib.sha256()
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    sha = h.hexdigest()
    return make_etag(st.st_size, st.st_mtime_ns, sha), st, sha


def read_with_etag(path: str) -> tuple[bytes, str, os.stat_result, str]:
    """The content and its etag, read consistently (the stat is taken around the read)."""
    for _ in range(3):
        with open_read(path) as f:
            st1 = os.fstat(f.fileno())
            data = f.read()
            st2 = os.fstat(f.fileno())
        if st1.st_mtime_ns == st2.st_mtime_ns and st1.st_size == st2.st_size == len(data):
            sha = sha256_bytes(data)
            return data, make_etag(st2.st_size, st2.st_mtime_ns, sha), st2, sha
    sha = sha256_bytes(data)
    return data, make_etag(len(data), st2.st_mtime_ns, sha), st2, sha


def open_dir(dir_real: str) -> int:
    """The folder a write goes into, opened without following a link and checked inside /share — the final
    rename is made relative to it, so a folder swapped for a link meanwhile can't send the file elsewhere."""
    fd = os.open(dir_real, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | NOFOLLOW | CLOEXEC)
    try:
        check_opened(fd, dir_real)
        if _opened_path(fd) not in (None, dir_real):
            raise paths.PathError("That folder isn't where it should be.")
    except BaseException:
        os.close(fd)
        raise
    return fd


def write_atomic(root, dest_real: str, data: bytes, *, exclusive: bool = False) -> os.stat_result:
    """Write `data` to `dest_real` (inside `root`) through the root's .tmp folder. exclusive: refuse when the
    target already exists (FileExistsError). Returns the new file's stat. The temporary file and the folder it
    goes into are opened and checked (inside /share, no link), and the final link / rename is relative to that
    folder's descriptor."""
    ensure_writable()
    tmp_dir = roots.hidden_dir(root, ".tmp")
    tdfd = open_dir(os.path.realpath(tmp_dir))
    os.close(tdfd)
    fd, tmp = tempfile.mkstemp(prefix="w-", suffix=".part", dir=tmp_dir)
    name = os.path.basename(dest_real)
    pfd = None
    try:
        with os.fdopen(fd, "wb") as f:
            check_opened(f.fileno(), tmp)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, FILE_MODE)
        pfd = open_dir(os.path.dirname(dest_real))
        if exclusive:
            try:
                os.link(tmp, name, dst_dir_fd=pfd, follow_symlinks=False)  # fails if the name is taken
            except FileExistsError:
                raise
            except OSError:                  # a file system without hard links (some network shares)
                try:
                    os.lstat(name, dir_fd=pfd)
                    raise FileExistsError(dest_real) from None
                except FileNotFoundError:
                    pass
                os.replace(tmp, name, dst_dir_fd=pfd)
            else:
                os.remove(tmp)
        else:
            os.replace(tmp, name, dst_dir_fd=pfd)
        try:
            os.fsync(pfd)
        except OSError:
            pass
        return os.stat(name, dir_fd=pfd, follow_symlinks=False)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    finally:
        if pfd is not None:
            os.close(pfd)


def make_dir(path: str) -> os.stat_result:
    ensure_writable()
    os.mkdir(path, 0o775)
    return os.stat(path)
