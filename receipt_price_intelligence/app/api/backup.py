"""Export and import of the whole database (administrators only)."""

import asyncio
import os
import tempfile
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from app.auth import CurrentUser, require_admin
from app.services import backup as backup_service
from app.services import deals

router = APIRouter(prefix="/api/v1/admin/backup", tags=["backup"])

CHUNK = 1024 * 1024
DB_MEDIA_TYPE = "application/vnd.sqlite3"


@router.get("/info")
async def backup_info(_: CurrentUser = Depends(require_admin)):
    """What the database holds now, and the safety copies kept from earlier imports."""
    path = backup_service.db_path()
    return {
        "database_bytes": os.path.getsize(path) if os.path.exists(path) else 0,
        "counts": backup_service.summarize(path, live=True) if os.path.exists(path) else {},
        "safety_copies": backup_service.list_safety_copies(),
        "max_import_mb": backup_service.MAX_IMPORT_BYTES // (1024 * 1024),
    }


@router.get("/export")
async def export_database(_: CurrentUser = Depends(require_admin)):
    """Download the whole database as one file (a consistent snapshot, safe while the app is in use).

    It contains every home's receipts, prices, stores and people, so keep it private.
    """
    try:
        path, filename = await asyncio.to_thread(backup_service.new_export)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Could not create the export: {e}")
    return FileResponse(path, media_type=DB_MEDIA_TYPE, filename=filename, background=BackgroundTask(os.remove, path))


@router.get("/safety-copies/{name}")
async def download_safety_copy(name: str, _: CurrentUser = Depends(require_admin)):
    """Download a copy of the database as it was just before an import."""
    path = backup_service.safety_copy_path(name)
    if path is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Copy not found")
    return FileResponse(path, media_type=DB_MEDIA_TYPE, filename=name)


@router.post("/import")
async def import_database(
    file: Annotated[UploadFile, ...],
    admin: CurrentUser = Depends(require_admin),
):
    """Replace everything with the contents of an exported file.

    The file is checked first (a real, intact database from this app). A copy of the current
    database is kept before it is replaced, and restored if the import fails. Older exports are
    upgraded automatically. App settings come from the file when it has them; otherwise the current ones are kept.
    """
    folder = os.path.dirname(backup_service.db_path()) or "."
    os.makedirs(folder, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix="import-", suffix=".db", dir=folder)
    try:
        size = 0
        with os.fdopen(fd, "wb") as out:
            while chunk := await file.read(CHUNK):
                size += len(chunk)
                if size > backup_service.MAX_IMPORT_BYTES:
                    raise backup_service.BackupError(f"The file is larger than {backup_service.MAX_IMPORT_BYTES // (1024 * 1024)} MB")
                out.write(chunk)
        result = await asyncio.to_thread(backup_service.import_backup, temp_path, admin.id, admin.display_name or admin.id)
    except backup_service.BackupError as e:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise

    # Recompute the daily best-price results from the imported data (best effort)
    try:
        await asyncio.to_thread(deals.refresh_all, False)
    except Exception:  # noqa: BLE001
        pass
    return result
