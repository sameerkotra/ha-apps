"""Admin → Storage: download a backup, restore one (shared by the household apps).

The database copy itself is `db_core.snapshot` / `snapshot_to_tempfile`; its checks are
`db_core.validate_file`. This module adds what goes around them. Each app keeps its own routes, file
names, zip layout, messages and status codes, and its post-restore steps (re-applying settings that
belong to this install, telling people, …).

Download
  `file_name(prefix, ext)`         "<prefix>-YYYYmmdd-HHMMSS<ext>" (the app passes its own clock / format)
  `send_file(path, name, type)`    the file as a download, deleted once sent
  `write_zip(target, members)`     a zip (path or file object) from (path, name[, compress_type]) members
                                   and `Text(name, data)` entries, in order; members may be a generator
                                   (e.g. `walk`)
  `walk(root, …)`                  the files under a folder as (path, "prefix/relative/name") pairs, with
                                   hooks to skip folders or files

Restore
  `receive(source, dir, …)`        stream an upload (UploadFile or the raw Request body) into a temp file in
                                   `dir` (on the database's filesystem, so the final swap is a rename),
                                   refusing it past `max_bytes`; `receive_sync` for a sync route
  `open_zip(source, not_zip)`      a ZipFile from a path or bytes, or the app's own error
  `check_members(z, …)`            every member's name is safe (no absolute path, no "..", no backslash —
                                   or the app's own rule) and passes the app's `check(info)`
  `copy_out(z, name, dest)`        extract one member, streamed, replacing `dest` atomically
  `restore_file(tmp, db_path, …)`  swap an already-validated database file in (db_core.swap_in) and run the
                                   app's migrations, under the app's lock

Secret settings (access keys, passwords) never leave in a backup, and a restore never wipes the ones this
install has. The secret keys and their "not set" stored text come from the app (settings_core
`Registry.secret_blanks()`; an app without the registry passes its own dict):
  `blank_settings(conn, blanks)`   in the backup COPY (e.g. `snapshot`'s `after=`): every secret row is
                                   set to its blank text
  `saved_settings(conn, blanks)`   before a restore: this install's secrets that are set {key: stored text}
  `keep_settings(conn, saved, blanks)`  after the restore (migrations done): a secret the restored file
                                   leaves blank or lacks gets this install's value back; one the file
                                   does carry (an older backup) is used as it is

Needs nothing from the app (Starlette for the responses; db_core for `restore_file`).
"""
from __future__ import annotations

import io
import os
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone

from starlette.background import BackgroundTask
from starlette.responses import FileResponse

DB_MEDIA_TYPE = "application/vnd.sqlite3"
ZIP_MEDIA_TYPE = "application/zip"
STAMP = "%Y%m%d-%H%M%S"
CHUNK = 1024 * 1024


# ---------------------------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------------------------

def file_name(prefix: str, ext: str, *, now: datetime | None = None, fmt: str = STAMP, suffix: str = "") -> str:
    """"<prefix>-<now formatted with fmt><suffix><ext>"; `now` defaults to the process's local time."""
    return f"{prefix}-{(now or datetime.now()).strftime(fmt)}{suffix}{ext}"


def send_file(path: str, filename: str, media_type: str = DB_MEDIA_TYPE) -> FileResponse:
    """`path` as a download named `filename`; the file is deleted once it has been sent."""
    return FileResponse(path, media_type=media_type, filename=filename, background=BackgroundTask(os.remove, path))


def walk(root: str, *, prefix: str = "", prune=None, keep=None):
    """Yield (path, name) for every file under `root` (os.walk order), `name` being `prefix` + the path
    relative to `root` with "/" separators. `prune(dirpath, dirnames)` may empty/trim `dirnames` (and return
    True to skip this folder's own files); `keep(path, rel) -> bool` filters files."""
    for dirpath, dirnames, filenames in os.walk(root):
        if prune is not None and prune(dirpath, dirnames):
            continue
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            if keep is None or keep(full, rel):
                yield full, prefix + rel


class Text:
    """A zip member written from a string (e.g. a small backup.json), for `write_zip`'s members."""
    def __init__(self, name: str, data: str):
        self.name, self.data = name, data


def write_zip(target, members=(), *, compression=zipfile.ZIP_DEFLATED, allow_zip64: bool = True) -> None:
    """Write a zip to `target` (a path or a binary file object) from `members`, an iterable consumed in
    order (e.g. chained `walk`s): (path, name), (path, name, compress_type) or `Text(name, data)`."""
    with zipfile.ZipFile(target, "w", compression, allowZip64=allow_zip64) as z:
        for m in members:
            if isinstance(m, Text):
                z.writestr(m.name, m.data)
            elif len(m) == 3:
                z.write(m[0], m[1], compress_type=m[2])
            else:
                z.write(m[0], m[1])


# ---------------------------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------------------------

def _temp(dest_dir, suffix, prefix):
    os.makedirs(dest_dir, exist_ok=True)
    return tempfile.mkstemp(suffix=suffix, prefix=prefix, dir=dest_dir)


async def receive(source, dest_dir, *, suffix: str = ".db", prefix: str | None = None,
                  max_bytes: int | None = None, too_big=None) -> str:
    """Stream an upload into a new temp file in `dest_dir` and return its path (the caller deletes it).
    `source` is an UploadFile (read in 1 MB chunks) or a Request (its raw body). Past `max_bytes` the file
    is deleted and `too_big()` is raised (an exception the app builds: its own message and status)."""
    fd, path = _temp(dest_dir, suffix, prefix)
    size = 0
    try:
        with os.fdopen(fd, "wb") as out:
            chunks = _upload_chunks(source) if hasattr(source, "read") else source.stream()
            async for chunk in chunks:
                size += len(chunk)
                if max_bytes is not None and size > max_bytes:
                    raise too_big()
                out.write(chunk)
    except BaseException:
        os.remove(path)
        raise
    return path


async def _upload_chunks(upload):
    while chunk := await upload.read(CHUNK):
        yield chunk


def receive_sync(fileobj, dest_dir, *, suffix: str = ".db", prefix: str | None = None) -> str:
    """`receive` for a sync route: copy a file object (e.g. UploadFile.file) into a new temp file in
    `dest_dir`, streamed, and return its path (the caller deletes it)."""
    fd, path = _temp(dest_dir, suffix, prefix)
    try:
        with os.fdopen(fd, "wb") as out:
            shutil.copyfileobj(fileobj, out, CHUNK)
    except BaseException:
        os.remove(path)
        raise
    return path


def open_zip(source, not_zip) -> zipfile.ZipFile:
    """A ZipFile for `source` (a path, or the upload's bytes); raises `not_zip()` if it isn't a zip."""
    try:
        if isinstance(source, (bytes, bytearray)):
            return zipfile.ZipFile(io.BytesIO(source))
        return zipfile.ZipFile(source)
    except zipfile.BadZipFile:
        raise not_zip()


def unsafe_name(name: str) -> bool:
    """A member name that could escape the folder it is extracted into."""
    return name.startswith("/") or ".." in name.split("/") or "\\" in name


def check_members(z: zipfile.ZipFile, *, unsafe, check=None, check_first: bool = False, skip_dirs: bool = False,
                  is_unsafe=unsafe_name) -> list:
    """Check every member of `z` before anything is extracted; returns their ZipInfos (in zip order).
    A name `is_unsafe` raises `unsafe(name)`; `check(info)` raises the app's own error for a member it
    doesn't expect (run before the name check with `check_first`). `skip_dirs`: folder entries are
    neither checked nor returned."""
    out = []
    for info in z.infolist():
        name = info.filename
        if skip_dirs and name.endswith("/"):
            continue
        if check_first and check is not None:
            check(info)
        if is_unsafe(name):
            raise unsafe(name)
        if not check_first and check is not None:
            check(info)
        out.append(info)
    return out


def copy_out(z: zipfile.ZipFile, name: str, dest: str, *, part: str = ".part") -> None:
    """Extract member `name` to `dest` (its folder is created): streamed into `dest + part`, then moved
    into place, so a reader never sees half a file."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with z.open(name) as src, open(dest + part, "wb") as dst:
        shutil.copyfileobj(src, dst, CHUNK)
    os.replace(dest + part, dest)


def restore_file(tmp_path, db_path, get_conn, *, migrate, lock=None) -> None:
    """Swap the live database for an already-validated file (db_core.swap_in: checkpoint, rename, drop the
    old -wal/-shm), then run the app's start-up migrations at once so an older backup gets this version's
    schema — all under `lock` (the app's guard against concurrent writers), if given."""
    from . import db_core       # (only here: an app on another database layer uses the rest without it)
    if lock is None:
        db_core.swap_in(tmp_path, db_path, get_conn)
        migrate()
        return
    with lock:
        db_core.swap_in(tmp_path, db_path, get_conn)
        migrate()


# ---------------------------------------------------------------------------------------------
# Secret settings in backups
# ---------------------------------------------------------------------------------------------
SETTINGS_TABLE = "app_settings"
KEPT_BY = "kept on restore"


def _is_blank(value, blank: str) -> bool:
    return value is None or value == "" or value == blank


def blank_settings(conn, blanks: dict, *, table: str = SETTINGS_TABLE) -> None:
    """In a backup copy (a sqlite3 connection to it): set each `blanks` key's row to its blank stored text
    ({key: text}, e.g. '""' for a JSON-stored empty string) and commit. A file without the settings
    table is left as it is."""
    if not blanks:
        return
    try:
        for key, text in blanks.items():
            conn.execute(f"UPDATE {table} SET value = ? WHERE key = ?", (text, key))
        conn.commit()
    except sqlite3.OperationalError:
        pass            # an old database without the settings table


def saved_settings(conn, blanks: dict, *, table: str = SETTINGS_TABLE) -> dict:
    """The `blanks` keys that are set (not blank) in the database `conn` is connected to: {key: stored
    text}. {} when the table is missing or unreadable."""
    if not blanks:
        return {}
    try:
        rows = conn.execute(f"SELECT key, value FROM {table} WHERE key IN ({','.join('?' * len(blanks))})",
                            tuple(blanks)).fetchall()
    except sqlite3.DatabaseError:
        return {}
    return {r[0]: r[1] for r in rows if not _is_blank(r[1], blanks[r[0]])}


def keep_settings(conn, saved: dict, blanks: dict, *, table: str = SETTINGS_TABLE, by: str = KEPT_BY,
                  now: str | None = None) -> list:
    """After a restore: put back each `saved` secret ({key: stored text}, from `saved_settings` before the
    restore) whose row in the restored database is blank or missing, and commit. A secret the restored
    file carries itself is left alone. A missing row is inserted with `updated_at` (`now`, default UTC now)
    and `updated_by` (`by`) when the table has those columns. Returns the keys put back ([] when the
    table is missing)."""
    if not saved:
        return []
    kept = []
    try:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if not cols:
            return []
        for key, text in saved.items():
            row = conn.execute(f"SELECT value FROM {table} WHERE key = ?", (key,)).fetchone()
            if row is not None and not _is_blank(row[0], blanks.get(key, "")):
                continue
            extra = {}
            if "updated_at" in cols:
                extra["updated_at"] = now or datetime.now(timezone.utc).isoformat(timespec="seconds")
            if "updated_by" in cols:
                extra["updated_by"] = by
            if row is None:
                names = ["key", "value", *extra]
                conn.execute(f"INSERT INTO {table} ({', '.join(names)}) VALUES ({', '.join('?' * len(names))})",
                             (key, text, *extra.values()))
            else:
                sets = ", ".join(f"{c} = ?" for c in ["value", *extra])
                conn.execute(f"UPDATE {table} SET {sets} WHERE key = ?", (text, *extra.values(), key))
            kept.append(key)
        conn.commit()
    except sqlite3.OperationalError:
        return []
    return kept
