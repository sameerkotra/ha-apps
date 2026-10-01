import asyncio
import json
import logging
import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .. import ai_client as _ai_client

from .. import settings as _settings

from ..auth import User, get_current_user, get_acting_user, require_admin
from ..categorize import list_all_categories
from ..db import get_db
from ..version import APP_VERSION
from .. import ai_usage, jobs, settings, storage
from ..jobs import start_job
from ..parser.documents import prepare_restart
from ..parser.pipeline import check_duplicate, finalize_if_ready
from ..parser.vision import MAX_NOTES_CHARS, debug_call, statement_prompt, VisionExtractionError
from ..paging import TableView
from ..matching import cascade_unlink_on_delete
from .accounts import _acting_banner, _acting_qs

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
templates.env.globals["app_version"] = APP_VERSION
templates.env.globals["ai_privacy"] = _ai_client.privacy
templates.env.globals["ai_configured"] = _settings.ai_configured
templates.env.globals["ai_usage_view"] = ai_usage.view

@router.post("/upload")
async def upload_statements(
    request: Request,
    account_id: int = Form(...),
    files: list[UploadFile] = File(...),
    skip_confirmation: str = Form(""),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """Several PDFs for one account per request. Unless `skip_confirmation`
    is ticked, each statement waits for a human to open the PDF and confirm
    its money in/out before it completes (finalize_if_ready)."""
    needs_confirmation = 0 if skip_confirmation else 1
    results = []
    conn = get_db()
    try:
        account = conn.execute(
            "SELECT id FROM accounts WHERE id = ? AND user_id = ? AND deleted_at IS NULL AND is_archived = 0",
            (account_id, acting.id),
        ).fetchone()
        if account is None:
            return JSONResponse({"error": "Account not found"}, status_code=404)

        for upload in files:
            try:
                content = await storage.read_upload(upload, storage.MAX_PDF_BYTES, pdf=True)
            except storage.UploadRejected as e:
                results.append({"filename": upload.filename, "error": str(e)})
                continue
            tmp_path, hash_ = await asyncio.to_thread(
                storage.save_file, storage.user_dir(acting.id), "upload", ".pdf", content)
            dup = check_duplicate(conn, acting.id, hash_)
            if dup is not None:
                storage.remove_stored_file(tmp_path)
                if dup["status"] == "error":
                    results.append({
                        "filename": upload.filename,
                        "duplicate_of": dup["id"],
                        "message": "Already uploaded and failed to process — restart it instead of uploading again.",
                    })
                else:
                    results.append({
                        "filename": upload.filename,
                        "duplicate_of": dup["id"],
                        "message": "Already uploaded.",
                    })
                continue

            cur = conn.execute(
                "INSERT INTO statements "
                "(user_id, account_id, original_filename, pdf_path, file_hash, status, needs_confirmation) "
                "VALUES (?, ?, ?, ?, ?, 'processing', ?)",
                (acting.id, account_id, upload.filename, tmp_path, hash_, needs_confirmation),
            )
            conn.commit()
            statement_id = cur.lastrowid

            start_job("statement", statement_id, acting.id, account_id)
            results.append({"filename": upload.filename, "statement_id": statement_id, "status": "processing"})
    finally:
        conn.close()

    return JSONResponse({"results": results})


# Columns _status_fragment.html / _statement_row.html read. flagged_count
# separates "needs review" (flagged rows) from "awaiting confirmation" (the
# confirm-against-PDF step).
_STATUS_COLUMNS = """
    s.id, s.status, s.error_message, s.original_filename, s.current_step,
    s.balance_mismatch, s.needs_confirmation, s.confirmed_at, (s.pdf_path IS NOT NULL) AS has_pdf, s.extraction_notes,
    (SELECT COUNT(*) FROM transactions t
     WHERE t.statement_id = s.id AND t.deleted_at IS NULL
       AND t.review_status = 'pending_review') AS flagged_count,
    (SELECT COUNT(*) FROM skipped_duplicates d
     WHERE d.statement_id = s.id AND d.status = 'skipped') AS skipped_count
"""
_HISTORY_COLUMNS = f"{_STATUS_COLUMNS}, s.uploaded_at, a.name AS account_name"
_HISTORY_FROM = "FROM statements s JOIN accounts a ON a.id = s.account_id"
_NO_FLOW = {"money_in": 0, "money_out": 0, "first_date": None, "last_date": None}


def statement_flows(conn: sqlite3.Connection, user_id: str, statement_id: int | None = None) -> dict[int, dict]:
    """statement_id -> {"money_in", "money_out", "first_date", "last_date"}:
    what each statement's own non-deleted transactions add up to and the dates
    they span, whatever their review_status (this is "what the statement
    contains", not a dashboard total). A credit card's totals are swapped — a
    purchase (positive there) reads as money IN, a payment as money OUT —
    while checking/savings read directly."""
    rows = conn.execute(
        f"""
        SELECT t.statement_id AS statement_id, a.type AS account_type,
               COALESCE(SUM(CASE WHEN t.amount > 0 THEN t.amount ELSE 0 END), 0) AS positive_total,
               COALESCE(SUM(CASE WHEN t.amount < 0 THEN -t.amount ELSE 0 END), 0) AS negative_total,
               MIN(t.date) AS first_date, MAX(t.date) AS last_date
        FROM transactions t
        JOIN accounts a ON a.id = t.account_id
        WHERE t.user_id = ? AND t.deleted_at IS NULL AND t.statement_id IS NOT NULL
          {"AND t.statement_id = ?" if statement_id is not None else ""}
        GROUP BY t.statement_id, a.type
        """,
        (user_id, statement_id) if statement_id is not None else (user_id,),
    ).fetchall()
    flows = {}
    for r in rows:
        card = r["account_type"] == "credit_card"
        flows[r["statement_id"]] = {
            "money_in": r["negative_total"] if card else r["positive_total"],
            "money_out": r["positive_total"] if card else r["negative_total"],
            "first_date": r["first_date"], "last_date": r["last_date"],
        }
    return flows


def _statement_row(conn: sqlite3.Connection, user_id: str, statement_id: int) -> dict | None:
    """One Statement-history row, ready for _statement_row.html."""
    row = conn.execute(
        f"SELECT {_HISTORY_COLUMNS} {_HISTORY_FROM} WHERE s.user_id = ? AND s.deleted_at IS NULL AND s.id = ?",
        (user_id, statement_id),
    ).fetchone()
    if row is None:
        return None
    return {**dict(row), **statement_flows(conn, user_id, statement_id).get(statement_id, _NO_FLOW)}


def _row_response(request: Request, current: User, acting: User, statement: dict | None):
    """The refreshed row, or (statement gone) an instruction to drop it and
    stop polling — htmx treats status 286 as "stop polling"."""
    if statement is None:
        return HTMLResponse("", status_code=286, headers={"HX-Reswap": "delete"})
    return templates.TemplateResponse(
        request, "_statement_row.html",
        {"s": statement, "acting_qs": _acting_qs(current, acting), "is_admin": current.is_admin},
    )


@router.get("/statement-status")
def statement_status(
    request: Request,
    id: int,
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """Polled by htmx (hx-trigger="every 3s") — returns the whole history row
    as HTML, not JSON, so it can be swapped straight into the table."""
    conn = get_db()
    try:
        statement = _statement_row(conn, acting.id, id)
    finally:
        conn.close()
    return _row_response(request, current, acting, statement)


@router.post("/statement-restart")
def statement_restart(
    request: Request,
    id: int,
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """Reuses the already-uploaded PDF — no re-upload needed. Not
    destructive, so none of the deletion confirmation machinery applies.
    Returns the status fragment (HTML), same shape as /statement-status —
    the restart button's htmx swap target needs renderable HTML back, not
    JSON, and the returned fragment's own hx-trigger picks polling back up
    automatically since it reflects the new 'processing' status."""
    conn = get_db()
    try:
        row, problem = prepare_restart("statements", conn, id, acting.id)
        if row is None:
            return _row_response(request, current, acting, None)
        if problem:
            return JSONResponse({"error": problem}, status_code=400)
        updated = _statement_row(conn, acting.id, id)
    finally:
        conn.close()

    start_job("statement", id, acting.id, row["account_id"])
    return _row_response(request, current, acting, updated)


@router.post("/statement-reextract")
def statement_reextract(
    request: Request,
    id: int,
    notes: str = Form(""),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """Re-run the vision pass on a not-yet-confirmed statement's stored PDF
    (SPEC.md section 4): the model isn't deterministic, and a second
    reading is often right. Only `pending_review` (Awaiting confirmation or
    Needs review); Restart covers `error`, and `complete` was signed off.

    Its current transactions are hard-deleted first — they were never
    confirmed, and a differently-wrong second reading wouldn't match them as
    duplicates — after unlinking any transfer partner. The job's own final
    UPDATE resets the reconciliation columns; needs_confirmation (the
    person's choice at upload) is kept. `notes` (what was wrong with this
    reading) are saved on the statement and sent to the model with the PDF;
    empty notes clear the previous ones."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id, account_id, pdf_path, status FROM statements "
            "WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
            (id, acting.id),
        ).fetchone()
        if row is None:
            return _row_response(request, current, acting, None)
        if row["status"] != "pending_review":
            return JSONResponse(
                {"error": "Only a not-yet-confirmed statement can be re-extracted"}, status_code=400
            )
        if not storage.stored_path(row["pdf_path"]):
            return JSONResponse({"error": "Source PDF no longer exists — re-upload instead"}, status_code=400)

        txn_ids = [
            r["id"] for r in conn.execute(
                "SELECT id FROM transactions WHERE statement_id = ? AND user_id = ? AND deleted_at IS NULL",
                (id, acting.id),
            ).fetchall()
        ]
        cascade_unlink_on_delete(conn, acting.id, txn_ids)
        if txn_ids:
            placeholders = ",".join("?" for _ in txn_ids)
            conn.execute(f"DELETE FROM transactions WHERE id IN ({placeholders})", tuple(txn_ids))

        conn.execute(
            "UPDATE statements SET status = 'processing', error_message = NULL, extraction_notes = ? WHERE id = ?",
            (notes.strip()[:MAX_NOTES_CHARS] or None, id),
        )
        conn.commit()
        updated = _statement_row(conn, acting.id, id)
    finally:
        conn.close()

    start_job("statement", id, acting.id, row["account_id"])
    return _row_response(request, current, acting, updated)


@router.get("/statement-debug")
def statement_debug(
    request: Request,
    id: int,
    admin: User = Depends(require_admin),
):
    """Admin-only (SPEC.md section 13) — the raw Ollama request
    prompt/response for a statement, purely a troubleshooting tool for
    when parsing accuracy looks off and it's unclear whether the
    deterministic or LLM path is at fault. Deliberately NOT scoped to
    the acting user (an admin debugging needs to see any statement,
    including ones belonging to a user they aren't currently viewing
    as) — require_admin alone is the gate here, not get_acting_user."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT s.*, a.name AS account_name, a.type AS account_type FROM statements s "
            "JOIN accounts a ON a.id = s.account_id WHERE s.id = ?",
            (id,),
        ).fetchone()
        transactions = conn.execute(
            "SELECT * FROM transactions WHERE statement_id = ? AND deleted_at IS NULL "
            "ORDER BY date, id",
            (id,),
        ).fetchall() if row else []
    finally:
        conn.close()

    if row is None:
        return templates.TemplateResponse(request, "not_found.html", {"user": admin}, status_code=404)

    # JSON list of lines that weren't inserted (duplicates, $0.00 lines).
    duplicate_transactions = json.loads(row["duplicate_transactions"]) if row["duplicate_transactions"] else []

    return templates.TemplateResponse(
        request, "statement_debug.html",
        {
            "user": admin, "statement": row, "transactions": transactions,
            "duplicate_transactions": duplicate_transactions, "active_page": "uploads",
        },
    )


@router.post("/statement-debug-resend")
def statement_debug_resend(
    request: Request,
    id: int,
    admin: User = Depends(require_admin),
):
    """Admin debug action, deliberately synchronous — the whole point is
    to wait for the real response with no timeout and see exactly how
    long it takes, not to fire-and-forget through the normal async job
    system. This request genuinely blocks for however long Ollama takes;
    that's intentional, not a bug, for a one-off manual diagnostic action
    (never used by the normal upload flow, which keeps its real timeouts)."""
    def _load(conn):
        r = conn.execute(
            "SELECT s.*, a.name AS account_name, a.type AS account_type FROM statements s "
            "JOIN accounts a ON a.id = s.account_id WHERE s.id = ?",
            (id,),
        ).fetchone()
        txns = conn.execute(
            "SELECT * FROM transactions WHERE statement_id = ? AND deleted_at IS NULL "
            "ORDER BY date, id",
            (id,),
        ).fetchall() if r else []
        return r, txns

    conn = get_db()
    try:
        row, transactions = _load(conn)
    finally:
        conn.close()

    if row is None:
        return templates.TemplateResponse(request, "not_found.html", {"user": admin}, status_code=404)

    if not storage.stored_path(row["pdf_path"]):
        return templates.TemplateResponse(
            request, "statement_debug.html",
            {
                "user": admin, "statement": row, "transactions": transactions, "active_page": "uploads",
                "resend_error": "Source PDF no longer exists (already resolved and deleted) — can't resend.",
            },
        )

    try:
        (elapsed, raw), resend_usage = jobs.exclusive_recorded(
            debug_call, row["pdf_path"], settings.ai_url(), settings.ai_model(),
            statement_prompt(row["account_type"], row["extraction_notes"] or ""))
    except VisionExtractionError as e:
        return templates.TemplateResponse(
            request, "statement_debug.html",
            {"user": admin, "statement": row, "transactions": transactions, "active_page": "uploads", "resend_error": str(e)},
        )

    conn = get_db()
    try:
        conn.execute("UPDATE statements SET llm_raw_response = ? WHERE id = ?", (raw, id))
        conn.commit()
        row, transactions = _load(conn)
    finally:
        conn.close()

    return templates.TemplateResponse(
        request, "statement_debug.html",
        {"user": admin, "statement": row, "transactions": transactions, "active_page": "uploads", "resend_elapsed": elapsed,
         "resend_usage": resend_usage},
    )


@router.get("/csv-import-debug")
def csv_import_debug(
    request: Request,
    id: int,
    admin: User = Depends(require_admin),
):
    """Admin-only, cross-user debug view for one CSV import: its outcome (row
    errors, duplicates, skipped statuses) and the categorization breakdown.
    Read-only — the file is gone and there's no vision step to re-run."""
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM csv_imports WHERE id = ?", (id,)).fetchone()
        transactions = conn.execute(
            "SELECT * FROM transactions WHERE csv_import_id = ? AND deleted_at IS NULL ORDER BY id",
            (id,),
        ).fetchall() if row else []
    finally:
        conn.close()

    if row is None:
        return templates.TemplateResponse(request, "not_found.html", {"user": admin}, status_code=404)

    # Stored as JSON lists (NULL when empty).
    row_errors = json.loads(row["row_errors"]) if row["row_errors"] else []
    duplicates = json.loads(row["duplicates"]) if row["duplicates"] else []
    skipped_status = json.loads(row["skipped_status"]) if row["skipped_status"] else []

    return templates.TemplateResponse(
        request, "csv_import_debug.html",
        {
            "user": admin, "csv_import": row, "transactions": transactions,
            "row_errors": row_errors, "duplicates": duplicates, "skipped_status": skipped_status,
            "active_page": "uploads",
        },
    )


def _statement_awaiting_confirmation(conn: sqlite3.Connection, statement_id: int, user_id: str):
    return conn.execute(
        "SELECT s.*, a.name AS account_name, a.type AS account_type FROM statements s "
        "JOIN accounts a ON a.id = s.account_id "
        "WHERE s.id = ? AND s.user_id = ? AND s.deleted_at IS NULL "
        "AND s.status = 'pending_review' AND s.needs_confirmation = 1 AND s.confirmed_at IS NULL",
        (statement_id, user_id),
    ).fetchone()


@router.get("/statement-confirm")
def statement_confirm_page(
    request: Request,
    id: int,
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """The optional last step before a parsed statement completes: the
    person opens the source PDF, compares its money in/out with what was
    extracted, and confirms (POST below)."""
    with get_db() as conn:
        statement = _statement_awaiting_confirmation(conn, id, acting.id)
        if statement is None:
            return templates.TemplateResponse(request, "not_found.html", {"user": acting}, status_code=404)
        flow = statement_flows(conn, acting.id).get(id, {"money_in": 0, "money_out": 0})
        txn_count, flagged_count = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(review_status = 'pending_review'), 0) FROM transactions "
            "WHERE statement_id = ? AND deleted_at IS NULL",
            (id,),
        ).fetchone()
        # Listed so an obviously duplicated line can be removed on its own.
        transactions = conn.execute(
            "SELECT id, date, description, amount FROM transactions "
            "WHERE statement_id = ? AND deleted_at IS NULL ORDER BY date, id",
            (id,),
        ).fetchall()

    return templates.TemplateResponse(
        request, "statement_confirm.html",
        {
            "user": acting, "statement": statement, "flow": flow,
            "txn_count": txn_count, "flagged_count": flagged_count,
            "transactions": transactions,
            "pdf_available": bool(storage.stored_path(statement["pdf_path"])),
            "acting_as_banner": _acting_banner(current, acting),
            "acting_qs": _acting_qs(current, acting),
            "active_page": "uploads",
        },
    )


@router.post("/statement-confirm")
def statement_confirm(
    request: Request,
    id: int = Form(...),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """Records the confirmation, then lets finalize_if_ready complete the
    statement and delete its PDF — unless flagged transactions are still
    waiting on review, in which case it completes once those clear.

    Answers with the refreshed history row for an htmx request (the Confirm
    button in Statement history) and with a redirect to Uploads otherwise (the
    confirm page and the PDF viewer's form)."""
    with get_db() as conn:
        if _statement_awaiting_confirmation(conn, id, acting.id) is None:
            return templates.TemplateResponse(request, "not_found.html", {"user": acting}, status_code=404)
        conn.execute("UPDATE statements SET confirmed_at = datetime('now') WHERE id = ?", (id,))
        conn.commit()
        finalize_if_ready(conn, id)
        conn.commit()
        updated = _statement_row(conn, acting.id, id)

    if request.headers.get("hx-request") == "true":
        # The Confirm button in the Statement-history row: swap in the refreshed row.
        return _row_response(request, current, acting, updated)

    qs = _acting_qs(current, acting)
    return RedirectResponse(url="uploads" + (f"?{qs}" if qs else ""), status_code=303)


def _lower(text):
    return text.lower() if text else None


def _awaiting_confirmation(r) -> bool:
    """True for exactly the rows _status_fragment.html labels "Awaiting
    confirmation" (the pending-confirm-against-PDF step) — status is
    pending_review, needs_confirmation is set, it hasn't been confirmed yet,
    and there are no flagged transactions still ahead of it (those show
    "Needs review" instead, with no Confirm button, until they're resolved)."""
    return bool(r["status"] == "pending_review" and r["needs_confirmation"] and not r["confirmed_at"]
                and not r["flagged_count"])


# Sort keys per history table. None sorts last whichever way the column runs.
_STATEMENT_KEYS = {
    "file": lambda r: _lower(r["original_filename"]),
    "account": lambda r: _lower(r["account_name"]),
    "uploaded": lambda r: r["uploaded_at"],
    "first": lambda r: r["first_date"],
    "last": lambda r: r["last_date"],
    "status": lambda r: r["status"],
    "in": lambda r: r["money_in"],
    "out": lambda r: r["money_out"],
    # Default landing order only (not a clickable column — see STATEMENT_SORT_COLUMNS):
    # statements awaiting confirmation float to the top since they're the ones
    # waiting on you, everything else falls in behind them; within each group,
    # most-recently-uploaded first. Sorted "desc" (TableView's default_dir below),
    # so reversing this (awaiting, uploaded_at) tuple puts True (1) before False (0)
    # and, within a tie, the later uploaded_at first — both wanted, in one sort.
    "default_order": lambda r: (_awaiting_confirmation(r), r["uploaded_at"]),
}
_CSV_KEYS = {
    "file": lambda r: _lower(r["original_filename"]),
    "account": lambda r: _lower(r["account_name"]),
    "imported": lambda r: r["imported_at"],
    "first": lambda r: r["first_date"],
    "last": lambda r: r["last_date"],
    "result": lambda r: (r["fatal_error"] is not None, r["row_count"] or 0),
}
STATEMENT_SORT_COLUMNS = [
    ("file", "File"), ("account", "Account"), ("uploaded", "Uploaded"), ("first", "First transaction"),
    ("last", "Last transaction"), ("status", "Status"), ("in", "Money in"), ("out", "Money out"),
]
CSV_SORT_COLUMNS = [
    ("file", "File"), ("account", "Account"), ("imported", "Imported"), ("first", "First transaction"),
    ("last", "Last transaction"), ("result", "Result"),
]


def _csv_import_rows(conn: sqlite3.Connection, user_id: str) -> list[dict]:
    """CSV import history with the date span of the transactions each import
    still holds (deleted ones don't count), and which account(s) they're in.

    A csv_imports row has no account_id of its own — one CSV can mix several
    accounts per row (its own Account column, or a default plus per-row
    overrides, section 10) — so, like the date span above, the account(s)
    shown are derived from the import's still-live transactions rather than
    stored anywhere: every distinct account name among them, alphabetically,
    joined with ", " for a mixed-account import. An import with nothing live
    left (every row failed or was a duplicate, or everything since got
    deleted) shows no account, same as it shows no date span."""
    spans = {
        r["csv_import_id"]: (r["first_date"], r["last_date"])
        for r in conn.execute(
            "SELECT csv_import_id, MIN(date) AS first_date, MAX(date) AS last_date FROM transactions "
            "WHERE user_id = ? AND deleted_at IS NULL AND csv_import_id IS NOT NULL GROUP BY csv_import_id",
            (user_id,),
        )
    }
    account_names: dict[int, list[str]] = {}
    for r in conn.execute(
        "SELECT DISTINCT t.csv_import_id, a.name AS account_name FROM transactions t "
        "JOIN accounts a ON a.id = t.account_id "
        "WHERE t.user_id = ? AND t.deleted_at IS NULL AND t.csv_import_id IS NOT NULL "
        "ORDER BY a.name",
        (user_id,),
    ):
        account_names.setdefault(r["csv_import_id"], []).append(r["account_name"])
    rows = conn.execute(
        "SELECT * FROM csv_imports WHERE user_id = ? AND deleted_at IS NULL", (user_id,)
    ).fetchall()
    return [
        {
            **dict(r), "first_date": spans.get(r["id"], (None, None))[0], "last_date": spans.get(r["id"], (None, None))[1],
            "account_name": ", ".join(account_names.get(r["id"], [])) or None,
        }
        for r in rows
    ]


@router.get("/uploads")
def uploads_list(
    request: Request,
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    conn = get_db()
    try:
        flows = statement_flows(conn, acting.id)
        statements = [
            {**dict(r), **flows.get(r["id"], _NO_FLOW)}
            for r in conn.execute(
                f"SELECT {_HISTORY_COLUMNS} {_HISTORY_FROM} WHERE s.user_id = ? AND s.deleted_at IS NULL",
                (acting.id,),
            )
        ]
        accounts = conn.execute(
            "SELECT id, name, type, preferred_upload_method FROM accounts"
            " WHERE user_id = ? AND deleted_at IS NULL AND is_archived = 0",
            (acting.id,),
        ).fetchall()
        categories = list_all_categories(conn, acting.id)
        # CSV import history (its debug link is the categorization view's entry point).
        csv_imports = _csv_import_rows(conn, acting.id)
    finally:
        conn.close()

    qs = _acting_qs(current, acting)
    statement_view = TableView(request, "uploads", "st_", statements, _STATEMENT_KEYS, "default_order", extra_qs=qs)
    csv_view = TableView(request, "uploads", "csv_", csv_imports, _CSV_KEYS, "imported", extra_qs=qs)

    return templates.TemplateResponse(
        request, "uploads_list.html",
        {
            "user": acting, "accounts": accounts, "categories": categories,
            "statement_view": statement_view, "csv_view": csv_view,
            "statement_sort_columns": STATEMENT_SORT_COLUMNS, "csv_sort_columns": CSV_SORT_COLUMNS,
            "acting_as_banner": _acting_banner(current, acting),
            "acting_qs": qs,
            "is_admin": current.is_admin,  # current, NOT acting — acting.is_admin
                                            # is always False while acting-as someone
                                            # else, which is exactly when an admin
                                            # would want the debug link most
            "active_page": "uploads",
        },
    )

