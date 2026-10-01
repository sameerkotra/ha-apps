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


async def read_upload(upload, max_bytes: int, *, pdf: bool) -> bytes:
    """The upload's bytes, read in chunks so an oversized file is refused before
    it is fully in memory. Raises UploadRejected."""
    parts, size = [], 0
    while True:
        chunk = await upload.read(_CHUNK)
        if not chunk:
            break
        size += len(chunk)
        if size > max_bytes:
            raise UploadRejected(f"File is larger than {max_bytes // (1024 * 1024)} MB.")
        parts.append(chunk)
    content = b"".join(parts)
    if pdf and b"%PDF-" not in content[:1024]:
        raise UploadRejected("That file isn't a PDF.")
    return content


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
