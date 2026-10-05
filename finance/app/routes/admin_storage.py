"""Admin Storage page: every stored PDF / leftover CSV across all users, per-file and bulk delete, and full-database backup download / restore. Admin only."""
import logging
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request, UploadFile, File, Form
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from ..auth import User, get_acting_user, require_admin
from ..db import get_db, DB_PATH, run_migrations
from ..version import APP_VERSION
from .. import settings as app_settings, storage
from ..common import backup_core, db_core
from ..parser.pipeline import delete_pdf
from .accounts import _acting_banner, _acting_qs
from ..parser.utility_pipeline import delete_pdf_utility
from ..parser.toll_pipeline import delete_pdf_toll

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
templates.env.globals["app_version"] = APP_VERSION


@router.get("/admin-storage")
def admin_storage(
    request: Request,
    purged: str = "",
    imported: str = "",
    import_error: str = "",
    tab: str = "storage",
    report: str = "",
    admin: User = Depends(require_admin),
    acting: User = Depends(get_acting_user),
):
    """Admin-only (SPEC.md section 6/11): a place to see what's
    actually still sitting on disk — every statement PDF not yet cleaned
    up, plus any CSV import file that outlived its own import request
    (should normally be empty; a leftover one only means its own
    try/finally delete failed, e.g. a permissions or disk error, not
    that CSVs are meant to persist). Deliberately cross-user, same as
    statement_debug above — an admin auditing storage needs to see
    every user's files, not just whichever one they're currently acting
    as. `tab=query` is the Query tab instead (routes/query.py), on the
    acting user's data."""
    if tab == "query":
        from .query import query_tab
        return query_tab(request, admin, acting, report)
    if tab == "users":
        from .users import users_tab
        return users_tab(request, admin, acting, request.query_params.get("message", ""),
                         request.query_params.get("error", ""))
    conn = get_db()
    try:
        stored_pdfs = conn.execute(
            "SELECT s.id, s.user_id, s.original_filename, s.pdf_path, s.status, "
            "s.uploaded_at, a.name AS account_name "
            "FROM statements s JOIN accounts a ON a.id = s.account_id "
            "WHERE s.pdf_path IS NOT NULL "
            "ORDER BY s.uploaded_at DESC"
        ).fetchall()
        stored_utility = conn.execute(
            "SELECT id, user_id, provider, utility_type, status, original_filename, uploaded_at, pdf_path, deleted_at "
            "FROM utility_bills WHERE pdf_path IS NOT NULL ORDER BY uploaded_at DESC, id"
        ).fetchall()
        stored_toll = conn.execute(
            "SELECT s.id, s.user_id, s.status, s.original_filename, s.uploaded_at, s.pdf_path, s.deleted_at, "
            "(SELECT COUNT(DISTINCT t.device_ref) FROM toll_transactions t WHERE t.statement_id = s.id) AS car_count "
            "FROM toll_statements s WHERE s.pdf_path IS NOT NULL ORDER BY s.uploaded_at DESC, s.id"
        ).fetchall()
    finally:
        conn.close()

    pdf_rows = []
    total_bytes = 0
    for r in stored_pdfs:
        size = None
        if storage.stored_path(r["pdf_path"]):
            try:
                size = os.path.getsize(r["pdf_path"])
                total_bytes += size
            except OSError:
                pass
        pdf_rows.append({**dict(r), "size_bytes": size})

    # Utility bill PDFs. A dual-fuel statement (electric + gas) is ONE file shared by two bill
    # rows, so rows are grouped by file: one line, one size, one delete for the pair.
    utility_groups: dict[str, dict] = {}
    for r in stored_utility:
        g = utility_groups.get(r["pdf_path"])
        if g is None:
            g = utility_groups[r["pdf_path"]] = {
                "id": r["id"], "user_id": r["user_id"], "provider": r["provider"],
                "original_filename": r["original_filename"], "uploaded_at": r["uploaded_at"],
                "pdf_path": r["pdf_path"], "types": [], "statuses": [], "deleted": True,
            }
        if r["utility_type"] and r["utility_type"] not in g["types"]:
            g["types"].append(r["utility_type"])
        if r["status"] not in g["statuses"]:
            g["statuses"].append(r["status"])
        g["deleted"] = g["deleted"] and r["deleted_at"] is not None
    utility_rows = []
    for g in utility_groups.values():
        size = None
        exists = bool(storage.stored_path(g["pdf_path"]))
        if exists:
            try:
                size = os.path.getsize(g["pdf_path"])
                total_bytes += size
            except OSError:
                pass
        utility_rows.append({**g, "size_bytes": size, "missing": not exists,
                             "all_complete": g["statuses"] == ["complete"]})

    # toll statement PDFs: one file per statement row.
    toll_rows = []
    for r in stored_toll:
        size = None
        exists = bool(storage.stored_path(r["pdf_path"]))
        if exists:
            try:
                size = os.path.getsize(r["pdf_path"])
                total_bytes += size
            except OSError:
                pass
        toll_rows.append({**dict(r), "size_bytes": size, "missing": not exists, "deleted": r["deleted_at"] is not None})

    # Orphaned CSV imports — every CSV import deletes its own file in a
    # finally block right after processing (routes/csv_import.py), so
    # anything found here means that delete itself failed, not that
    # CSVs are expected to accumulate.
    orphan_csvs = []
    if os.path.isdir(storage.PENDING_DIR):
        for user_dir in os.listdir(storage.PENDING_DIR):
            csv_dir = os.path.join(storage.PENDING_DIR, user_dir, "csv-imports")
            if not os.path.isdir(csv_dir):
                continue
            for fname in os.listdir(csv_dir):
                fpath = os.path.join(csv_dir, fname)
                try:
                    size = os.path.getsize(fpath)
                    total_bytes += size
                except OSError:
                    size = None
                orphan_csvs.append({"user_id": user_dir, "path": fpath, "filename": fname, "size_bytes": size})

    return templates.TemplateResponse(
        request, "admin_storage.html",
        {
            "user": acting,
            "pdf_rows": pdf_rows,
            "utility_rows": utility_rows,
            "toll_rows": toll_rows,
            "orphan_csvs": orphan_csvs,
            "total_bytes": total_bytes,
            "purged": int(purged) if purged.isdigit() else None,
            "imported": bool(imported),
            "import_error": import_error or None,
            "acting_as_banner": _acting_banner(admin, acting),
            "acting_qs": _acting_qs(admin, acting),
            "active_page": "admin_storage",
        },
    )


# The secret settings (the AI access key) and the stored text of "not set": a downloaded backup never
# carries them, and importing one keeps the ones this install has (SPEC.md section 22).
SECRET_SETTINGS = {key: "" for key, s in app_settings.SETTINGS.items() if s.kind == "secret"}


def _blank_secrets(conn: sqlite3.Connection) -> None:
    """A downloaded backup never carries the AI access key (SPEC.md section 22)."""
    backup_core.blank_settings(conn, SECRET_SETTINGS)


def _saved_secrets() -> dict:
    """This install's secrets that are set, read before an import replaces the database."""
    try:
        cur = sqlite3.connect(DB_PATH, timeout=10.0)
        try:
            return backup_core.saved_settings(cur, SECRET_SETTINGS)
        finally:
            cur.close()
    except sqlite3.DatabaseError:
        return {}


def _keep_secrets(saved: dict) -> None:
    """Importing a backup (which has no access key) keeps the key this install already has."""
    if not saved:
        return
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10.0)
        try:
            backup_core.keep_settings(conn, saved, SECRET_SETTINGS, by="kept on import",
                                      now=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        logger.exception("Could not keep the access key over the imported database; enter it again")


@router.get("/admin-storage-download-db")
def admin_storage_download_db(
    request: Request,
    admin: User = Depends(require_admin),
):
    """Admin-only: a consistent copy of the whole database (every user's data)
    made with SQLite's backup API — a plain copy of DB_PATH could miss data
    still in the WAL. Written to a temp file that is deleted once sent."""
    fd, tmp_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    src = sqlite3.connect(DB_PATH)
    try:
        db_core.snapshot(src, tmp_path, after=_blank_secrets)
    finally:
        src.close()

    return backup_core.send_file(tmp_path, backup_core.file_name("finance-backup", ".db"))


@router.post("/admin-storage-import-db")
def admin_storage_import_db(
    request: Request,
    admin: User = Depends(require_admin),
    confirm: str = Form(""),
    db_file: UploadFile = File(...),
):
    """Admin-only: replace the whole database with an uploaded backup (for a
    reinstall). The upload is streamed to a temp file next to DB_PATH (same
    filesystem, so os.replace is atomic — /tmp is a different volume in the
    app's container) and must open as SQLite, pass integrity_check and contain the core
    tables before anything is touched. The live WAL is then checkpointed and
    its -wal/-shm files removed (stale frames would corrupt the new file) and
    the file swapped in. Connections already open keep the old file until
    reopened, so the page tells the admin to restart the app. Migrations run on the imported
    file at once, so an older backup gets the tables this version needs."""
    if confirm != "yes":
        return RedirectResponse(
            url="admin-storage?import_error=" + quote("Import not confirmed."),
            status_code=303,
        )

    db_dir = os.path.dirname(DB_PATH) or "."
    tmp_path = backup_core.receive_sync(db_file.file, db_dir)  # streamed, never fully in memory
    try:
        try:
            db_core.validate_file(
                tmp_path, {"schema_version", "accounts", "transactions", "statements"},
                integrity_msg="Failed integrity check: {integrity}",
                missing_msg="Doesn't look like a Finance Dashboard database (missing tables: {missing}).",
                invalid_msg="That file isn't a valid SQLite database.")
        except ValueError as e:
            return RedirectResponse(url="admin-storage?import_error=" + quote(str(e)), status_code=303)

        saved_secrets = _saved_secrets()

        # Flush and empty the CURRENT db's WAL before swapping the file
        # out from under it — see docstring.
        try:
            cur = sqlite3.connect(DB_PATH, timeout=10.0)
            try:
                cur.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            finally:
                cur.close()
        except sqlite3.DatabaseError:
            pass  # current DB missing/unreadable -- nothing to checkpoint, proceed anyway

        for suffix in ("-wal", "-shm"):
            sidecar = DB_PATH + suffix
            if os.path.exists(sidecar):
                os.remove(sidecar)

        os.replace(tmp_path, DB_PATH)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise

    # Bring an older backup up to date now (the restart does it too), so pages that need newer
    # tables, such as Admin → AI usage, work straight away.
    try:
        run_migrations()
    except Exception:
        logger.exception("Migrations after the database import failed; they run again on restart")
    _keep_secrets(saved_secrets)

    return RedirectResponse(url="admin-storage?imported=1", status_code=303)


@router.post("/admin-storage-delete-pdf")
def admin_storage_delete_pdf(
    request: Request,
    id: int = Form(...),
    admin: User = Depends(require_admin),
):
    """Deletes ONE statement's stored PDF on demand — the per-row action
    next to a completed statement that still has a file sitting around
    for whatever reason. Reuses delete_pdf exactly (pipeline.py), the
    same function that runs automatically once a statement's review
    clears — this is just that same cleanup, triggered by hand instead
    of by the finalize step, for a statement that somehow still has one
    despite already being complete."""
    with get_db() as conn:
        row = conn.execute("SELECT pdf_path FROM statements WHERE id = ?", (id,)).fetchone()
        if row is not None:
            delete_pdf(conn, id, row["pdf_path"])
    return RedirectResponse(url="admin-storage", status_code=303)


@router.post("/admin-storage-purge-pdfs")
def admin_storage_purge_pdfs(
    request: Request,
    admin: User = Depends(require_admin),
):
    """Bulk version of the above — every statement whose review is
    already fully done (status = 'complete') but which still has a
    pdf_path set. Never touches a 'processing'/'pending_review'/'error'
    statement's PDF, whatever the button label says — "completed"
    means the statement, not just clicking the button."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, pdf_path FROM statements WHERE status = 'complete' AND pdf_path IS NOT NULL"
        ).fetchall()
        for row in rows:
            delete_pdf(conn, row["id"], row["pdf_path"])
    return RedirectResponse(url=f"admin-storage?purged={len(rows)}", status_code=303)


@router.post("/admin-storage-delete-utility-pdf")
def admin_storage_delete_utility_pdf(
    request: Request,
    id: int = Form(...),
    admin: User = Depends(require_admin),
):
    """Deletes ONE utility bill PDF from disk. A dual-fuel statement's electric and gas bills
    share one file, so every bill row pointing at that file is cleared together. Cross-user, like
    the rest of this page."""
    with get_db() as conn:
        row = conn.execute("SELECT pdf_path FROM utility_bills WHERE id = ?", (id,)).fetchone()
        if row is not None and row["pdf_path"]:
            ids = [r["id"] for r in conn.execute("SELECT id FROM utility_bills WHERE pdf_path = ?", (row["pdf_path"],)).fetchall()]
            delete_pdf_utility(conn, ids, row["pdf_path"])
    return RedirectResponse(url="admin-storage", status_code=303)


@router.post("/admin-storage-purge-utility-pdfs")
def admin_storage_purge_utility_pdfs(
    request: Request,
    admin: User = Depends(require_admin),
):
    """Bulk version: every stored utility PDF whose bills are ALL 'complete'. A file still tied to
    a bill that is processing, waiting for review or failed is left alone."""
    purged = 0
    with get_db() as conn:
        paths = [r["pdf_path"] for r in conn.execute(
            "SELECT DISTINCT pdf_path FROM utility_bills WHERE pdf_path IS NOT NULL"
        ).fetchall()]
        for path in paths:
            rows = conn.execute("SELECT id, status FROM utility_bills WHERE pdf_path = ?", (path,)).fetchall()
            if rows and all(r["status"] == "complete" for r in rows):
                delete_pdf_utility(conn, [r["id"] for r in rows], path)
                purged += 1
    return RedirectResponse(url=f"admin-storage?purged={purged}", status_code=303)


@router.post("/admin-storage-delete-toll-pdf")
def admin_storage_delete_toll_pdf(
    request: Request,
    id: int = Form(...),
    admin: User = Depends(require_admin),
):
    """Deletes ONE toll statement PDF from disk (cross-user, like the rest of this page). The statement
    row and its passes stay; only the file goes."""
    with get_db() as conn:
        row = conn.execute("SELECT pdf_path FROM toll_statements WHERE id = ?", (id,)).fetchone()
        if row is not None and row["pdf_path"]:
            delete_pdf_toll(conn, [id], row["pdf_path"])
    return RedirectResponse(url="admin-storage", status_code=303)


@router.post("/admin-storage-purge-toll-pdfs")
def admin_storage_purge_toll_pdfs(
    request: Request,
    admin: User = Depends(require_admin),
):
    """Bulk version: every stored toll PDF whose statement is 'complete'. A file still tied to a
    statement that is processing, waiting for review or failed is left alone."""
    purged = 0
    with get_db() as conn:
        for r in conn.execute("SELECT id, pdf_path FROM toll_statements WHERE pdf_path IS NOT NULL AND status = 'complete'").fetchall():
            delete_pdf_toll(conn, [r["id"]], r["pdf_path"])
            purged += 1
    return RedirectResponse(url=f"admin-storage?purged={purged}", status_code=303)


@router.post("/admin-storage-delete-csv-orphan")
def admin_storage_delete_csv_orphan(
    request: Request,
    path: str = Form(...),
    admin: User = Depends(require_admin),
):
    """Removes one leftover CSV-import file directly from disk — there's
    no DB row for these at all (see admin_storage's own docstring), so
    this is a plain file delete, not a soft-delete/DB update like every
    other delete action in this app. Restricted to paths actually inside
    PENDING_DIR's csv-imports subfolders, so this can never be pointed
    at an arbitrary file on the host regardless of what a tampered form
    field claims."""
    real_pending = os.path.realpath(storage.PENDING_DIR)
    real_target = os.path.realpath(path)
    if (
        real_target.startswith(real_pending + os.sep)
        and os.path.basename(os.path.dirname(real_target)) == "csv-imports"
        and os.path.isfile(real_target)
    ):
        try:
            os.remove(real_target)
        except OSError:
            pass
    return RedirectResponse(url="admin-storage", status_code=303)
