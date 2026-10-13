# Shared file: edit common/python/sandbox_run.py and run tools/sync_common.py; don't edit this copy. sha256=e4e2ff8861fe28f79588c252bc3c7eb66b64c6a6edb64b2d6780f19f1636d8ec
"""Run a command-line tool on untrusted input as an unprivileged user with resource limits (shared:
common/python/sandbox_run.py). Used for poppler (`pdftotext`, `pdftoppm`, `pdfinfo`) on uploaded PDFs.

The apps run as root in their containers. A file someone uploads is handed to the tool as a COPY in a
scratch folder of its own, and the tool runs:

* as the user `pdfworker` (made in the app's Dockerfile: no home, no login shell), with no supplementary
  groups — so it can't read the app's /data (the scratch folder is the only place it can write, and
  `lock_down` keeps /data closed to everyone but root);
* with limits: address space (memory), CPU seconds, the size of any file it writes, open files,
  processes, no core dumps — and the caller's own timeout, as before;
* with a small environment (PATH, a HOME and cache folder in the scratch folder, LANG).

When the app isn't running as root (tests, development) the user switch is skipped and the limits still
apply. If `pdfworker` doesn't exist the tool runs as `nobody` (and this is logged once): it never runs
as root.

    from .common import sandbox_run
    with sandbox_run.Scratch() as box:
        src = box.add_file(upload_path, "in.pdf")            # a copy the tool may read
        res = box.run(["pdftotext", "-layout", src, "-"], timeout=30, text=True)
        png = box.read("page-1.png")                          # what the tool wrote (a regular file, at most 64 MB)
        for name in box.files("page-*.png"): ...              # the regular files the tool wrote, sorted

The tool's output is read by the app (root) from a folder the tool's user owns, so `read()` and `files()`
only ever open a REGULAR file by that name — never a symbolic link, a device or a FIFO a hijacked tool could
have planted there to make the app read /data — and `read()` refuses anything over `max_bytes`
(SandboxError). The caller's glob/open on `box.path` has none of that: use these.

`Scratch.run(...)` takes subprocess.run's usual capture/text/timeout arguments (list-form commands only)
and returns a CompletedProcess; FileNotFoundError and subprocess.TimeoutExpired come through as they do
from subprocess.run. Needs nothing from the app.
"""
from __future__ import annotations

import fnmatch
import logging
import os
import pwd
import stat
import resource
import shutil
import subprocess
import tempfile

logger = logging.getLogger("sandbox_run")

USER = "pdfworker"
FALLBACK_USER = "nobody"

MEMORY_BYTES = 1024 * 1024 * 1024       # address space: 1 GiB
CPU_SECONDS = 120                        # a hard ceiling under the caller's own (wall-clock) timeout
FILE_BYTES = 512 * 1024 * 1024           # the largest file the tool may write
OPEN_FILES = 64
PROCESSES = 64                           # per user: pdfworker's own processes only
PATH = "/usr/local/bin:/usr/bin:/bin"
MAX_READ_BYTES = 64 * 1024 * 1024        # the largest output file read() hands back

_warned = False


class SandboxError(RuntimeError):
    """The tool could not be started safely."""


def is_root() -> bool:
    return os.geteuid() == 0


def worker() -> tuple[int, int] | None:
    """(uid, gid) the tools run as when the app runs as root; None when it doesn't (no switch)."""
    global _warned
    if not is_root():
        return None
    for name in (USER, FALLBACK_USER):
        try:
            pw = pwd.getpwnam(name)
        except KeyError:
            continue
        if pw.pw_uid == 0:
            continue
        if name != USER and not _warned:
            logger.warning("The user %s is missing; running PDF tools as %s", USER, name)
            _warned = True
        return pw.pw_uid, pw.pw_gid
    raise SandboxError(f"No unprivileged user ({USER} or {FALLBACK_USER}) to run the tool as")


def limits(memory: int = MEMORY_BYTES, cpu: int = CPU_SECONDS, file_size: int = FILE_BYTES,
           open_files: int = OPEN_FILES, processes: int = PROCESSES):
    """A preexec_fn that lowers the child's resource limits (never raises one above what it already is)."""
    wanted = [(resource.RLIMIT_AS, memory), (resource.RLIMIT_CPU, cpu), (resource.RLIMIT_FSIZE, file_size),
              (resource.RLIMIT_NOFILE, open_files), (resource.RLIMIT_NPROC, processes),
              (resource.RLIMIT_CORE, 0)]

    def apply():
        for which, value in wanted:
            soft, hard = resource.getrlimit(which)
            if hard != resource.RLIM_INFINITY:
                value = min(value, hard)
            resource.setrlimit(which, (value, value))
        os.umask(0o077)
    return apply


class Scratch:
    """A scratch folder for one run, owned by the worker user (mode 0700), removed afterwards."""

    def __init__(self, *, prefix: str = "sandbox-", dir: str | None = None, **limit_kw):
        self.prefix, self.parent, self.limit_kw = prefix, dir, limit_kw
        self.path = None
        self.ids = None

    def __enter__(self) -> "Scratch":
        self.ids = worker()
        self.path = tempfile.mkdtemp(prefix=self.prefix, dir=self.parent)
        try:
            os.chmod(self.path, 0o700)
            if self.ids is not None:
                os.chown(self.path, *self.ids)
        except BaseException:
            shutil.rmtree(self.path, ignore_errors=True)
            raise
        return self

    def __exit__(self, *exc):
        shutil.rmtree(self.path, ignore_errors=True)
        return False

    def join(self, name: str) -> str:
        """A path in the scratch folder (a plain file name: no folders)."""
        if not name or name != os.path.basename(name) or name in (".", ".."):
            raise ValueError(f"not a plain file name: {name!r}")
        return os.path.join(self.path, name)

    def _own(self, path: str) -> None:
        if self.ids is not None:
            os.chown(path, *self.ids)
        os.chmod(path, 0o400)

    def add_file(self, source: str, name: str) -> str:
        """Copy `source` (read by the app) into the scratch folder as `name`, readable by the tool only."""
        dest = self.join(name)
        shutil.copyfile(source, dest)
        self._own(dest)
        return dest

    def add_bytes(self, data: bytes, name: str) -> str:
        """Write `data` into the scratch folder as `name`, readable by the tool only."""
        dest = self.join(name)
        with open(dest, "wb") as f:
            f.write(data)
        self._own(dest)
        return dest

    def read(self, name: str, max_bytes: int = MAX_READ_BYTES) -> bytes:
        """The content of a REGULAR file the tool wrote (never through a symlink; over `max_bytes` or anything
        that isn't a plain file is a SandboxError)."""
        path = self.join(name)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
        try:
            fd = os.open(path, flags)
        except OSError as e:
            if e.errno == getattr(os, "ELOOP", 40):
                raise SandboxError(f"{name!r} is a symbolic link, not a file the tool wrote") from None
            raise
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                raise SandboxError(f"{name!r} isn't a regular file")
            if st.st_size > max_bytes:
                raise SandboxError(f"{name!r} is {st.st_size} bytes, more than the {max_bytes} allowed")
            with os.fdopen(fd, "rb") as f:
                fd = -1
                return f.read(max_bytes + 1)[:max_bytes]
        finally:
            if fd >= 0:
                os.close(fd)

    def files(self, pattern: str = "*") -> list[str]:
        """The names (not paths) of the regular files in the scratch folder matching `pattern` (fnmatch),
        sorted; symbolic links and anything else the tool may have planted are left out."""
        out = []
        for name in os.listdir(self.path):
            if not fnmatch.fnmatchcase(name, pattern):
                continue
            try:
                st = os.lstat(os.path.join(self.path, name))
            except OSError:
                continue
            if stat.S_ISREG(st.st_mode):
                out.append(name)
        return sorted(out)

    def run(self, cmd: list, *, timeout: float, **kwargs) -> subprocess.CompletedProcess:
        """subprocess.run(cmd) as the worker user, in the scratch folder, with the limits."""
        if isinstance(cmd, (str, bytes)) or kwargs.get("shell"):
            raise ValueError("list-form commands only")
        for key in ("preexec_fn", "user", "group", "extra_groups", "cwd", "env"):
            if key in kwargs:
                raise ValueError(f"{key} is set by the sandbox")
        env = {"PATH": PATH, "HOME": self.path, "XDG_CACHE_HOME": self.path, "TMPDIR": self.path,
               "LANG": os.environ.get("LANG", "C.UTF-8")}
        extra = {}
        if self.ids is not None:
            extra = {"user": self.ids[0], "group": self.ids[1], "extra_groups": []}
        return subprocess.run(list(cmd), cwd=self.path, env=env, preexec_fn=limits(**self.limit_kw),
                              close_fds=True, timeout=timeout, **extra, **kwargs)


def lock_down(folder: str) -> None:
    """Close `folder` (the app's /data) to everyone but its owner, so the tools' user can't read the
    database or the stored files even if a tool were taken over. Only as root; a folder that doesn't exist
    or can't be changed is logged and left."""
    if not is_root():
        return
    try:
        mode = os.stat(folder).st_mode & 0o777
        if mode & 0o077:
            os.chmod(folder, mode & 0o700)
    except OSError as e:
        logger.warning("Could not close %s to other users: %s", folder, e)
