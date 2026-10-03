"""Admin → App settings (with holidays) and Admin → Storage. All admin-only.
The Users tab's routes are in users.py."""
import os
import re
import tempfile
from datetime import date, datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Body, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from .. import auth, config, db, games, ha_sensors, settings
from ..auth import require_admin

router = APIRouter(prefix="/api", tags=["admin"])

MAX_HOLIDAY_RANGE_DAYS = 120
MAX_IMPORT_BYTES = 200 * 1024 * 1024
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _admins(conn) -> list[dict]:
    return [{"id": r["id"], "name": r["name"]} for r in conn.execute(
        "SELECT id, name, username FROM users WHERE disabled = 0 ORDER BY name COLLATE NOCASE")
        if auth.is_admin_identity(r["id"], r["username"])]


def _payload() -> dict:
    with db.get_conn() as conn:
        admins = _admins(conn)
    return dict(settings.payload(),
                games=[{"id": g, "name": games.name(g)} for g in games.GAME_IDS],
                looks=[{"id": k, "label": v} for k, v in games.LOOKS.items()],
                admins=admins, hasToken=bool(config.SUPERVISOR_TOKEN), timeZone=config.timezone_name())


@router.get("/admin/settings")
def get_settings(admin: dict = Depends(require_admin)):
    return _payload()


@router.put("/admin/settings")
def put_settings(background: BackgroundTasks, body=Body(...), admin: dict = Depends(require_admin)):
    """Any subset of the keys; 422 for an unknown key or a bad value (nothing
    is saved then). Changes apply at once."""
    if not isinstance(body, dict):
        raise HTTPException(422, "Send an object of settings to change.")
    if isinstance(body.get("limit_warning_admins"), list):
        with db.get_conn() as conn:
            known = {a["id"] for a in _admins(conn)}
        if any(not isinstance(x, str) or x not in known for x in body["limit_warning_admins"]):
            raise HTTPException(422, "Who gets limit warnings: pick admins only.")
    try:
        _, changed = settings.update(body, admin)
    except settings.SettingsError as e:
        raise HTTPException(422, str(e))
    if "ha_sensors" in changed:
        background.add_task(ha_sensors.apply_switch_change_blocking)
    elif changed & {"disabled_games", "school_days"}:
        background.add_task(ha_sensors.changed_blocking)
    return _payload()


# ---------------------------------------------------------------------------
# Holidays (dates when children get weekend limits)
# ---------------------------------------------------------------------------

def _holidays(conn) -> list[dict]:
    return [{"date": r["date"], "name": r["name"]} for r in conn.execute("SELECT * FROM holidays ORDER BY date")]


def _date(v, label) -> date:
    if not isinstance(v, str) or not _DATE_RE.match(v):
        raise HTTPException(422, f"{label} must be a date (YYYY-MM-DD).")
    try:
        return date.fromisoformat(v)
    except ValueError:
        raise HTTPException(422, f"{label} isn't a real date.")


@router.get("/admin/holidays")
def list_holidays(admin: dict = Depends(require_admin)):
    with db.get_conn() as conn:
        return {"holidays": _holidays(conn)}


@router.post("/admin/holidays", status_code=201)
def add_holidays(background: BackgroundTasks, body: dict = Body(...), admin: dict = Depends(require_admin)):
    """{from, to?, name?}: one date, or every date from–to (at most 120)."""
    if not isinstance(body, dict):
        raise HTTPException(422, "Send {from, to, name}.")
    start = _date(body.get("from"), "From")
    end = _date(body["to"], "To") if body.get("to") else start
    if end < start:
        raise HTTPException(422, "To must be on or after From.")
    if (end - start).days + 1 > MAX_HOLIDAY_RANGE_DAYS:
        raise HTTPException(422, f"At most {MAX_HOLIDAY_RANGE_DAYS} days at once.")
    name = body.get("name") or ""
    if not isinstance(name, str) or len(name.strip()) > 80:
        raise HTTPException(422, "The name can be at most 80 characters.")
    with db.get_conn() as conn:
        d = start
        while d <= end:
            conn.execute("INSERT INTO holidays (date, name) VALUES (?, ?) ON CONFLICT(date) DO UPDATE SET "
                         "name = excluded.name", (d.isoformat(), name.strip()))
            d += timedelta(days=1)
        out = _holidays(conn)
    background.add_task(ha_sensors.changed_blocking)
    return {"holidays": out}


@router.delete("/admin/holidays/{day}")
def delete_holiday(day: str, background: BackgroundTasks, admin: dict = Depends(require_admin)):
    _date(day, "The date")
    with db.get_conn() as conn:
        if not conn.execute("DELETE FROM holidays WHERE date = ?", (day,)).rowcount:
            raise HTTPException(404, "That date isn't a holiday.")
        out = _holidays(conn)
    background.add_task(ha_sensors.changed_blocking)
    return {"holidays": out}


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

@router.get("/admin-storage-download-db")
def admin_storage_download_db(admin: dict = Depends(require_admin)):
    """A full, consistent snapshot of the whole database (sqlite's online
    backup, never a plain copy of a WAL-mode file)."""
    tmp_path = db.backup_to_tempfile()
    filename = f"household-arcade-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
    return FileResponse(tmp_path, media_type="application/vnd.sqlite3", filename=filename,
                        background=BackgroundTask(os.remove, tmp_path))


@router.post("/admin-storage-import-db")
async def admin_storage_import_db(background: BackgroundTasks, file: UploadFile = File(...),
                                  admin: dict = Depends(require_admin)):
    """Replaces the ENTIRE database with an uploaded .db file — no merge, no
    undo. Validated first; an older database is migrated straight away. The
    scratch file is in DATA_DIR so the final os.replace() is a same-filesystem
    rename."""
    fd, tmp_path = tempfile.mkstemp(suffix=".db", dir=config.DATA_DIR)
    try:
        size = 0
        with os.fdopen(fd, "wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_IMPORT_BYTES:
                    raise HTTPException(413, "That file is too big to be a Household Arcade database.")
                out.write(chunk)
        try:
            db.validate_backup_file(tmp_path)
        except ValueError as e:
            raise HTTPException(400, str(e))
        db.import_from_tempfile(tmp_path)
        settings.invalidate()
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    background.add_task(ha_sensors.apply_switch_change_blocking)
    return {"status": "ok"}
