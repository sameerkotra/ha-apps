"""Where uploaded files live, and the helpers every route uses to write, find
and delete them (admin Storage's leftover-CSV delete applies the same rule).

Every stored file sits under PENDING_DIR/<user>/... . Paths recorded in the
database are only ever served or deleted after checking they are still inside
PENDING_DIR, so a bad value in the database (for example from an imported
backup) can't point the app at any other file.
"""
import hashlib
import os
import re
import sqlite3

PENDING_DIR = os.environ.get("PENDING_DIR", "/data/pending")

MAX_PDF_BYTES = int(os.environ.get("MAX_PDF_MB", "50")) * 1024 * 1024
MAX_CSV_BYTES = int(os.environ.get("MAX_CSV_MB", "20")) * 1024 * 1024
_CHUNK = 1024 * 1024
# Files one upload request may carry (the Upload pages pick several at once). The request's whole body is
# capped too, before it is read (main.py: BodyLimit), at this many files of the largest size.
MAX_FILES_PER_REQUEST = 20
MAX_UPLOAD_REQUEST_BYTES = MAX_FILES_PER_REQUEST * MAX_PDF_BYTES + 1024 * 1024
MAX_CSV_REQUEST_BYTES = MAX_FILES_PER_REQUEST * MAX_CSV_BYTES + 1024 * 1024
TOO_MANY_FILES = f"Upload at most {MAX_FILES_PER_REQUEST} files at a time."

# Tables whose rows keep a pdf_path + pdf_deleted_at pair.
_PDF_TABLES = {"statements", "utility_bills", "toll_statements"}


class UploadRejected(ValueError):
    """An uploaded file that is too large or isn't the expected kind."""


def user_dir(user_id: str, *sub: str) -> str:
    """PENDING_DIR/<user>/<sub...>. Home Assistant user ids are hex; anything
    else is reduced to safe characters so an id can never climb out of PENDING_DIR."""
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", user_id) or "_"
    return os.path.join(PENDING_DIR, safe, *sub)


def file_hash(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def save_file(directory: str, prefix: str, suffix: str, content: bytes) -> tuple[str, str]:
    """Write bytes to a new randomly named file; returns (path, sha256). Blocking —
    call it through asyncio.to_thread from async routes."""
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f"{prefix}-{os.urandom(8).hex()}{suffix}")
    with open(path, "wb") as f:
        f.write(content)
    return path, file_hash(path)


def save_upload(upload, directory: str, prefix: str, suffix: str, max_bytes: int, *, pdf: bool) -> tuple[str, str]:
    """Copy an uploaded file to a new randomly named file in `directory`, hashing it on the way:
    (path, sha256). Chunk by chunk, so the file is never whole in memory. Blocking — call it from a
    worker thread. Raises UploadRejected (and leaves nothing behind) for a file over `max_bytes` or,
    with pdf=True, one that isn't a PDF."""
    source = getattr(upload, "file", upload)
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f"{prefix}-{os.urandom(8).hex()}{suffix}")
    h, size, head = hashlib.sha256(), 0, b""
    try:
        with open(path, "wb") as out:
            while True:
                chunk = source.read(_CHUNK)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise UploadRejected(f"File is larger than {max_bytes // (1024 * 1024)} MB.")
                if len(head) < 1024:
                    head += chunk[:1024 - len(head)]
                h.update(chunk)
                out.write(chunk)
        if pdf and b"%PDF-" not in head:
            raise UploadRejected("That file isn't a PDF.")
    except BaseException:
        try:
            os.remove(path)
        except OSError:
            pass
        raise
    return path, h.hexdigest()


def read_upload_sync(upload, max_bytes: int) -> bytes:
    """The upload's bytes (a CSV, decoded whole anyway), read in chunks from a worker thread so an
    oversized file is refused before it is fully in memory. Raises UploadRejected."""
    source = getattr(upload, "file", upload)
    parts, size = [], 0
    while True:
        chunk = source.read(_CHUNK)
        if not chunk:
            break
        size += len(chunk)
        if size > max_bytes:
            raise UploadRejected(f"File is larger than {max_bytes // (1024 * 1024)} MB.")
        parts.append(chunk)
    return b"".join(parts)


def is_stored(path: str | None) -> bool:
    if not path:
        return False
    root = os.path.realpath(PENDING_DIR)
    return os.path.realpath(path).startswith(root + os.sep)


def stored_path(path: str | None) -> str | None:
    """The path if it is an existing file inside PENDING_DIR, else None."""
    return path if is_stored(path) and os.path.isfile(path) else None


def remove_stored_file(path: str | None) -> bool:
    """Delete a stored file. True when nothing of ours is left at that path
    (removed, already gone, or not inside PENDING_DIR — never touched then);
    False only when removing it failed."""
    if not path or not is_stored(path) or not os.path.exists(path):
        return True
    try:
        os.remove(path)
        return True
    except OSError:
        return False


def forget_pdf(table: str, conn: sqlite3.Connection, ids: list[int], pdf_path: str | None) -> None:
    """Delete a finished document's PDF and record that on its row(s). Several
    rows can share one PDF (a dual-fuel utility statement)."""
    if table not in _PDF_TABLES:
        raise ValueError(table)
    if not remove_stored_file(pdf_path):
        return  # keep pdf_path so the file can still be found (and deleted from Storage)
    conn.executemany(
        f"UPDATE {table} SET pdf_path = NULL, pdf_deleted_at = datetime('now') WHERE id = ?",
        [(i,) for i in ids],
    )
    conn.commit()
