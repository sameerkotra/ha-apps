"""Export and import of the whole database.

Everything the app keeps lives in one SQLite file (receipt photos are deleted once a receipt is
saved, so they are not part of it). *Export* writes a consistent snapshot using SQLite's online
backup, which is safe while the app is running. *Import* replaces the database with an
uploaded file after checking it, keeping a safety copy of the current one first, and puts
the old one back if anything goes wrong.
"""

import os
import shutil
import sqlite3
import tempfile
import threading
from datetime import datetime
from typing import Any

from app import app_settings
from app.config import get_settings
from app.db import dispose_engine, get_db_session, init_models
from app.db.models import ReceiptImage, User
from app.logging_config import get_logger

logger = get_logger("backup")

MAX_IMPORT_BYTES = 500 * 1024 * 1024
KEEP_SAFETY_COPIES = 3
SQLITE_HEADER = b"SQLite format 3\x00"
# Tables every backup of this app has. Older backups may lack newer tables; those are added on import.
REQUIRED_TABLES = {"users", "receipts", "receipt_items", "receipt_drafts", "store_chains", "store_locations"}
COUNTED_TABLES = ("homes", "users", "receipts", "receipt_items", "price_observations", "common_items", "store_chains", "store_locations")

_lock = threading.Lock()


class BackupError(ValueError):
    """The backup could not be exported or imported. The message is safe to show."""


def db_path() -> str:
    return get_settings().database_path


def backups_dir() -> str:
    return os.path.join(os.path.dirname(db_path()) or ".", "backups")


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #

def _connect(path: str, read_only: bool = False) -> sqlite3.Connection:
    """Open a database file. ``read_only`` is for files nothing else is writing (uploads, exports):
    they are opened immutable, so no journal files are needed next to them."""
    if read_only:
        return sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
    return sqlite3.connect(path)


def summarize(path: str, live: bool = False) -> dict[str, int]:
    """Row counts for the main tables of a database file (tables that are missing count as 0).

    ``live`` is for the database the app is using right now.
    """
    counts: dict[str, int] = {}
    conn = _connect(path, read_only=not live)
    try:
        existing = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in COUNTED_TABLES:
            counts[table] = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] if table in existing else 0
    finally:
        conn.close()
    return counts


def make_snapshot(dest: str) -> None:
    """Write a consistent copy of the live database to ``dest`` (safe while the app is running)."""
    src = _connect(db_path())
    dst = _connect(dest)
    try:
        src.backup(dst)
        dst.execute("PRAGMA journal_mode=DELETE")  # a single self-contained file
    finally:
        dst.close()
        src.close()


def new_export() -> tuple[str, str]:
    """Create an export in a temporary file. Returns (path, download filename); the caller deletes the file."""
    folder = os.path.dirname(db_path()) or "."
    os.makedirs(folder, exist_ok=True)
    fd, path = tempfile.mkstemp(prefix="export-", suffix=".db", dir=folder)
    os.close(fd)
    try:
        make_snapshot(path)
    except Exception:
        os.remove(path)
        raise
    name = f"receipt-price-intelligence-{datetime.now().strftime('%Y%m%d-%H%M')}.db"
    logger.info("Exported the database (%d bytes)", os.path.getsize(path))
    return path, name


def list_safety_copies() -> list[dict[str, Any]]:
    """Copies of the database kept from before each import, newest first."""
    folder = backups_dir()
    if not os.path.isdir(folder):
        return []
    out = []
    for name in os.listdir(folder):
        if name.startswith("before-import-") and name.endswith(".db"):
            path = os.path.join(folder, name)
            out.append({"name": name, "size": os.path.getsize(path), "created": datetime.fromtimestamp(os.path.getmtime(path)).isoformat(timespec="seconds")})
    return sorted(out, key=lambda x: x["created"], reverse=True)


def safety_copy_path(name: str) -> str | None:
    """Path of a listed safety copy, or None. Only names from the list are accepted."""
    if name in {c["name"] for c in list_safety_copies()}:
        return os.path.join(backups_dir(), name)
    return None


# --------------------------------------------------------------------------- #
# Importing
# --------------------------------------------------------------------------- #

def validate_backup(path: str) -> dict[str, int]:
    """Check an uploaded file is an intact database from this app. Returns its row counts."""
    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        raise BackupError("The file is empty")
    if os.path.getsize(path) > MAX_IMPORT_BYTES:
        raise BackupError(f"The file is larger than {MAX_IMPORT_BYTES // (1024 * 1024)} MB")
    with open(path, "rb") as f:
        if f.read(16) != SQLITE_HEADER:
            raise BackupError("This is not a backup file exported from Receipt Price Intelligence")

    try:
        conn = _connect(path, read_only=True)
        try:
            check = conn.execute("PRAGMA integrity_check").fetchall()
            if check != [("ok",)]:
                raise BackupError("The backup file is damaged")
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            conn.close()
    except sqlite3.DatabaseError as e:
        raise BackupError(f"The backup file could not be read: {e}") from e

    missing = REQUIRED_TABLES - tables
    if missing:
        raise BackupError("This does not look like a backup from this app (missing " + ", ".join(sorted(missing)) + ")")
    return summarize(path)


def _swap_in(source: str, keep_source: bool) -> None:
    target = db_path()
    for suffix in ("-wal", "-shm"):
        try:
            os.remove(target + suffix)
        except FileNotFoundError:
            pass
    if keep_source:
        shutil.copy2(source, target)
    else:
        try:
            os.replace(source, target)
        except OSError:  # different filesystem
            shutil.copy2(source, target)
            os.remove(source)


def _prepare_imported_database(admin_id: str, admin_name: str) -> dict[str, int]:
    """Bring a freshly swapped-in database up to date."""
    from app import auth
    from app.services import homes as homes_service

    init_models()   # adds any tables and columns an older backup lacks, as at start-up
    with get_db_session()() as db:
        if db.get(User, admin_id) is None:   # receipts and lists refer to people
            db.add(User(id=admin_id, display_name=admin_name, role="ADMIN"))
            db.commit()

        homes_service.adopt_legacy_data(db)

        # Photos are not part of a backup: forget references to files that are not here
        dropped = 0
        for image in db.query(ReceiptImage).all():
            if not image.file_path or not os.path.exists(image.file_path):
                db.delete(image)
                dropped += 1
        db.commit()
    auth.sync_roles()      # administrators come from admin_users, not from the backup
    return {"photo_references_dropped": dropped}


def import_backup(upload_path: str, admin_id: str, admin_name: str) -> dict[str, Any]:
    """Replace the database with the uploaded backup. ``upload_path`` is consumed."""
    with _lock:
        counts = validate_backup(upload_path)

        os.makedirs(backups_dir(), exist_ok=True)
        safety = os.path.join(backups_dir(), f"before-import-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db")
        make_snapshot(safety)

        current_settings = app_settings.export_rows(db_path())
        dispose_engine()
        try:
            _swap_in(upload_path, keep_source=False)
            # App settings come from the backup if it has them, else the current ones are kept.
            settings_kept = app_settings.adopt_rows(db_path(), current_settings)
            extra = _prepare_imported_database(admin_id, admin_name)
            extra["settings_from_backup"] = not settings_kept and bool(app_settings.export_rows(db_path()))
        except Exception as e:  # put the previous database back
            logger.exception("Import failed; restoring the previous database")
            dispose_engine()
            _swap_in(safety, keep_source=True)
            app_settings.invalidate()
            try:
                _prepare_imported_database(admin_id, admin_name)
            except Exception:  # noqa: BLE001
                logger.exception("Could not re-open the restored database")
            raise BackupError("The import failed and nothing was changed") from e

        # keep only the newest few safety copies
        for old in list_safety_copies()[KEEP_SAFETY_COPIES:]:
            try:
                os.remove(os.path.join(backups_dir(), old["name"]))
            except OSError:
                pass

    logger.info("Imported a backup: %s", counts)
    return {"imported": counts, "safety_copy": os.path.basename(safety), **extra}
