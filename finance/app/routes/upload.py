import asyncio
import json
import logging
import os
import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .. import ai_client as _ai_client

from .. import settings as _settings

from ..auth import User, get_current_user, get_acting_user, require_admin
from ..categorize import list_all_categories, menu_groups
from ..db import get_db
from ..version import APP_VERSION
from .. import ai_usage, card_payments, jobs, notices, ofx, qfx_import, settings, statement_due, storage, upload_page
from ..jobs import start_job
from ..parser.documents import prepare_restart
from ..parser.pipeline import check_duplicate, finalize_if_ready
from ..parser.vision import MAX_NOTES_CHARS, debug_call, statement_prompt, VisionExtractionError
from ..paging import TableView
from ..matching import cascade_unlink_on_delete, relink
from .accounts import _acting_banner, _acting_qs

logger = logging.getLogger(__name__)
router = APIRouter()
from ..template_filters import install as _install_filters
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
_install_filters(templates.env)
templates.env.globals["app_version"] = APP_VERSION
templates.env.globals["ai_privacy"] = _ai_client.privacy
templates.env.globals["ai_configured"] = _settings.ai_configured
templates.env.globals["ai_usage_view"] = ai_usage.view
templates.env.globals["period_label"] = ofx.period_label


def _name(filename: str | None) -> str | None:
    """The uploaded file's own name, as shown in the history (cut to a sane length)."""
    return filename[:255] if filename else None


MATCH_BY_NUMBER = "match"       # the form's "Match by account number" (QFX only)
PDF_NEEDS_ACCOUNT = "Pick an account for PDF files."
NOT_A_STATEMENT = "That file isn't a PDF or a QFX/OFX file."


@router.post("/upload")
async def upload_statements(
    request: Request,
    account_id: str = Form(...),
    files: list[UploadFile] = File(...),
    skip_confirmation: str | None = Form(None),
    block_accounts: str = Form(""),
    confirm_last4: str = Form(""),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """PDFs and QFX/OFX files for one account (or, for QFX, "Match by account number") per request; each file
    goes its own way by its content. A PDF is queued for the AI and, unless `skip_confirmation` is ticked, waits
    for a human to confirm its money in/out (finalize_if_ready). A QFX/OFX file is read and saved right here
    (qfx_import, SPEC.md section 4a); when its account can't be settled the result says `needs_account` and the
    page sends the file again with `block_accounts` (the person's answers) or `confirm_last4`. The files and the
    database are handled in a worker thread, never on the event loop.

    `skip_confirmation` left out (or blank) means the account's own saved choice (accounts.skip_confirmation,
    remembered from the Upload page or set on the account's page; never chosen = confirm). A plain browser form
    (the page without its script) is sent back to Upload instead of getting JSON."""
    if len(files) > storage.MAX_FILES_PER_REQUEST:
        return JSONResponse({"error": storage.TOO_MANY_FILES}, status_code=400)
    skip = _skip_choice(skip_confirmation)
    picked = account_id.strip()
    if picked != MATCH_BY_NUMBER and not picked.isdigit():
        if notices.wants_page(request):
            return notices.form_error(request, templates, acting, _acting_qs(current, acting),
                                      PDF_NEEDS_ACCOUNT if not picked or picked == "__new__" else "Account not found",
                                      "uploads")
        return JSONResponse({"error": "Account not found"}, status_code=404)
    results = await asyncio.to_thread(
        _store_statements, acting.id, None if picked == MATCH_BY_NUMBER else int(picked), files, skip,
        qfx_import.parse_choices(block_accounts), confirm_last4 == "1")
    if results is None:
        return JSONResponse({"error": "Account not found"}, status_code=404)
    if notices.wants_page(request):
        failed = sum(1 for r in results if r.get("error") or r.get("needs_account"))
        qs = _acting_qs(current, acting)
        return RedirectResponse(url=f"uploads?notice={'upload_some_failed' if failed else 'upload_sent'}"
                                    + (f"&{qs}" if qs else ""), status_code=303)
    return JSONResponse({"results": results})


_YES = ("1", "on", "true", "yes")
_NO = ("0", "off", "false", "no")


def _skip_choice(value: str | None) -> bool | None:
    """The upload's "Skip the PDF confirmation step": True, False, or None when it doesn't say (blank or left out:
    the account's saved choice applies)."""
    v = (value or "").strip().lower()
    return True if v in _YES else False if v in _NO else None


def _store_statements(user_id: str, account_id: int | None, files: list, skip: bool | int | None,
                      choices: dict | None = None, confirm_last4: bool = False) -> list[dict] | None:
    """Save each file (streamed to disk while hashing) and tell PDFs from QFX/OFX by their content: a PDF already
    uploaded is skipped and the rest queued; a QFX/OFX file is imported at once and deleted. None when a picked
    account isn't the user's live, unarchived one. `skip` None: the account's saved skip_confirmation."""
    results = []
    conn = get_db()
    try:
        saved_skip = 0
        if account_id is not None:
            account = conn.execute(
                "SELECT id, skip_confirmation FROM accounts "
                "WHERE id = ? AND user_id = ? AND deleted_at IS NULL AND is_archived = 0",
                (account_id, user_id),
            ).fetchone()
            if account is None:
                return None
            saved_skip = account["skip_confirmation"] or 0
        needs_confirmation = 0 if (saved_skip if skip is None else skip) else 1

        limit = max(storage.MAX_PDF_BYTES, storage.MAX_CSV_BYTES)
        for upload in files:
            try:
                tmp_path, hash_ = storage.save_upload(upload, storage.user_dir(user_id), "upload", ".upload",
                                                      limit, pdf=False)
            except storage.UploadRejected as e:
                results.append({"filename": upload.filename, "error": str(e)})
                continue
            with open(tmp_path, "rb") as fh:
                head = fh.read(ofx.SNIFF_BYTES)
            size = os.path.getsize(tmp_path)
            if b"%PDF-" not in head:
                try:
                    if not ofx.looks_like_ofx(head):
                        results.append({"filename": upload.filename,
                                        "error": ofx.HTML_REFUSED if ofx.looks_like_html(head) else NOT_A_STATEMENT})
                    elif size > storage.MAX_CSV_BYTES:
                        results.append({"filename": upload.filename, "kind": "qfx",
                                        "error": f"File is larger than {storage.MAX_CSV_BYTES // (1024 * 1024)} MB."})
                    else:
                        with open(tmp_path, "rb") as fh:
                            raw = fh.read()
                        results.append(qfx_import.import_file(conn, user_id, _name(upload.filename), raw, hash_,
                                                              account_id, choices, confirm_last4))
                finally:
                    storage.remove_stored_file(tmp_path)       # a QFX file is never kept
                continue

            if size > storage.MAX_PDF_BYTES:
                storage.remove_stored_file(tmp_path)
                results.append({"filename": upload.filename,
                                "error": f"File is larger than {storage.MAX_PDF_BYTES // (1024 * 1024)} MB."})
                continue
            if account_id is None:
                storage.remove_stored_file(tmp_path)
                results.append({"filename": upload.filename, "error": PDF_NEEDS_ACCOUNT})
                continue
            pdf_path = tmp_path[:-len(".upload")] + ".pdf"
            os.replace(tmp_path, pdf_path)
            dup = check_duplicate(conn, user_id, hash_)
            if dup is not None:
                storage.remove_stored_file(pdf_path)
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
                (user_id, account_id, _name(upload.filename), pdf_path, hash_, needs_confirmation),
            )
            conn.commit()
            statement_id = cur.lastrowid

            start_job("statement", statement_id, user_id, account_id)
            results.append({"filename": upload.filename, "statement_id": statement_id, "status": "processing"})
    finally:
        conn.close()
    return results


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
_HISTORY_COLUMNS = f"""{_STATUS_COLUMNS}, s.uploaded_at, s.account_id, a.name AS account_name, s.source, s.period_start, s.period_end,
    (SELECT COUNT(*) FROM transactions t WHERE t.statement_id = s.id AND t.deleted_at IS NULL) AS txn_count"""
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
            relink(conn, acting.id)

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
    # A QFX statement's reading (section 4a): the file's blocks, the account each went to, every row.
    try:
        qfx = json.loads(row["import_details"]) if row["source"] == "qfx" and row["import_details"] else None
    except ValueError:
        qfx = None

    return templates.TemplateResponse(
        request, "statement_debug.html",
        {
            "user": admin, "statement": row, "transactions": transactions,
            "duplicate_transactions": duplicate_transactions, "qfx": qfx, "active_page": "uploads",
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
        flow = statement_flows(conn, acting.id, id).get(id, {"money_in": 0, "money_out": 0})
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


def _csv_import_rows(conn: sqlite3.Connection, user_id: str) -> list[dict]:
    """CSV import history with the date span of the transactions each import
    still holds (deleted ones don't count), and which account(s) they're in.

    A csv_imports row has no account_id of its own — one CSV can mix several
    accounts per row (its own Account column, or a default plus per-row
    overrides, section 10) — so, like the date span above, the account(s)
    shown are derived from the import's still-live transactions rather than
    stored anywhere: every distinct account name among them, alphabetically,
    joined with ", " for a mixed-account import (and their ids, for the
    history's account filter). An import with nothing live left (every row
    failed or was a duplicate, or everything since got deleted) shows no
    account, same as it shows no date span."""
    spans = {
        r["csv_import_id"]: (r["first_date"], r["last_date"])
        for r in conn.execute(
            "SELECT csv_import_id, MIN(date) AS first_date, MAX(date) AS last_date FROM transactions "
            "WHERE user_id = ? AND deleted_at IS NULL AND csv_import_id IS NOT NULL GROUP BY csv_import_id",
            (user_id,),
        )
    }
    accounts: dict[int, list] = {}
    for r in conn.execute(
        "SELECT DISTINCT t.csv_import_id, a.id AS account_id, a.name AS account_name FROM transactions t "
        "JOIN accounts a ON a.id = t.account_id "
        "WHERE t.user_id = ? AND t.deleted_at IS NULL AND t.csv_import_id IS NOT NULL "
        "ORDER BY a.name",
        (user_id,),
    ):
        accounts.setdefault(r["csv_import_id"], []).append((r["account_id"], r["account_name"]))
    # money in / out of what each import still holds, from each account's point of view (the same swap for a
    # credit card as statement_flows; a mixed-account import adds up each account its own way)
    flows: dict[int, dict] = {}
    for r in conn.execute(
        "SELECT t.csv_import_id, a.type AS account_type, "
        "COALESCE(SUM(CASE WHEN t.amount > 0 THEN t.amount ELSE 0 END), 0) AS positive_total, "
        "COALESCE(SUM(CASE WHEN t.amount < 0 THEN -t.amount ELSE 0 END), 0) AS negative_total "
        "FROM transactions t JOIN accounts a ON a.id = t.account_id "
        "WHERE t.user_id = ? AND t.deleted_at IS NULL AND t.csv_import_id IS NOT NULL "
        "GROUP BY t.csv_import_id, a.type",
        (user_id,),
    ):
        card = r["account_type"] == "credit_card"
        f = flows.setdefault(r["csv_import_id"], {"money_in": 0, "money_out": 0})
        f["money_in"] += r["negative_total"] if card else r["positive_total"]
        f["money_out"] += r["positive_total"] if card else r["negative_total"]
    rows = conn.execute(
        "SELECT * FROM csv_imports WHERE user_id = ? AND deleted_at IS NULL", (user_id,)
    ).fetchall()
    return [
        {
            **dict(r), **flows.get(r["id"], {"money_in": 0, "money_out": 0}),
            "first_date": spans.get(r["id"], (None, None))[0], "last_date": spans.get(r["id"], (None, None))[1],
            "account_name": ", ".join(n for _, n in accounts.get(r["id"], [])) or None,
            "account_ids": [i for i, _ in accounts.get(r["id"], [])],
        }
        for r in rows
    ]


def _statement_rows(conn: sqlite3.Connection, user_id: str) -> list[dict]:
    flows = statement_flows(conn, user_id)
    return [
        {**dict(r), **flows.get(r["id"], _NO_FLOW)}
        for r in conn.execute(
            f"SELECT {_HISTORY_COLUMNS} {_HISTORY_FROM} WHERE s.user_id = ? AND s.deleted_at IS NULL", (user_id,))
    ]


def _live_accounts(conn: sqlite3.Connection, user_id: str) -> list:
    return conn.execute(
        "SELECT id, name, type, preferred_upload_method, account_number_last4, csv_layout, skip_confirmation "
        "FROM accounts WHERE user_id = ? AND deleted_at IS NULL AND is_archived = 0 ORDER BY name COLLATE NOCASE",
        (user_id,),
    ).fetchall()


def _history_context(request: Request, conn: sqlite3.Connection, current: User, acting: User) -> dict:
    """What _upload_history.html needs: the waiting list, the filter chips and one sorted, paged page of the
    history (TableView, prefix h_). Its links point at the page itself (uploads?…), so they work without the
    page's script; the script fetches the same address from uploads-history instead."""
    qs = _acting_qs(current, acting)
    rows = upload_page.unify(_statement_rows(conn, acting.id), _csv_import_rows(conn, acting.id))
    filters = upload_page.parse_filters(request.query_params)
    all_accounts = conn.execute("SELECT id, name FROM accounts WHERE user_id = ? AND deleted_at IS NULL",
                                (acting.id,)).fetchall()
    shown = upload_page.filtered(rows, filters)
    view = TableView(request, "uploads", "h_", shown, upload_page.SORT_KEYS, "when", extra_qs=qs)
    waiting = upload_page.waiting(rows)
    keep = {k: v for k, v in request.query_params.items()
            if k in ("h_type", "h_account", "h_status", "h_sort", "h_dir", "h_size")}
    return {
        "history_view": view, "history_total": len(rows), "history_filters": filters,
        "history_sort_columns": upload_page.SORT_COLUMNS,
        "type_filters": upload_page.TYPE_FILTERS, "status_filters": upload_page.STATUS_FILTERS,
        "account_filters": upload_page.account_filters(rows, all_accounts),
        "filter_keep": keep,
        "waiting": waiting[:upload_page.WAITING_SHOWN], "waiting_more": max(0, len(waiting) - upload_page.WAITING_SHOWN),
        "kind_label": upload_page.KIND_LABEL,
        "acting_qs": qs, "is_admin": current.is_admin,
    }


@router.get("/uploads-history")
def uploads_history(
    request: Request,
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """The Upload page's "Waiting for you" list and upload history, as the HTML fragment the page swaps in place
    (after an upload, a filter chip, a sort or a page link) — the same filters, sort and paging as the page's own
    address (h_type, h_account, h_status, h_sort, h_dir, h_page, h_size)."""
    with get_db() as conn:
        ctx = _history_context(request, conn, current, acting)
    return templates.TemplateResponse(request, "_upload_history.html", ctx)


@router.get("/uploads-strip")
def uploads_strip(
    request: Request,
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """The accounts strip (one card per live, unarchived account), as the fragment the page swaps in after an
    upload."""
    with get_db() as conn:
        cards = upload_page.strip_cards(conn, current, acting)
    return templates.TemplateResponse(request, "_accounts_strip.html", {
        "strip": cards, "acting_qs": _acting_qs(current, acting)})


@router.get("/statement-progress")
def statement_progress(
    ids: str = "",
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """How the statements just uploaded are getting on, for the Upload page's file rows (polled every 3 s while
    one is being read): {"statements": [{id, state, step, error, count, flagged, skipped}]}. Only the acting
    person's statements; unknown ids are left out. At most 50 ids."""
    wanted = [int(x) for x in ids.split(",") if x.strip().isdigit()][:50]
    out = []
    if wanted:
        with get_db() as conn:
            marks = ",".join("?" for _ in wanted)
            for r in conn.execute(
                    f"SELECT {_HISTORY_COLUMNS} {_HISTORY_FROM} WHERE s.user_id = ? AND s.deleted_at IS NULL "
                    f"AND s.id IN ({marks})", (acting.id, *wanted)):
                out.append({"id": r["id"], "state": upload_page.statement_state(r), "step": r["current_step"],
                            "error": r["error_message"], "count": r["txn_count"], "flagged": r["flagged_count"],
                            "skipped": r["skipped_count"]})
    return JSONResponse({"statements": out})


@router.get("/uploads")
def uploads_list(
    request: Request,
    account: str = "",
    notice: str = "",
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """Upload → Statements (SPEC.md sections 4 and 13): overdue notice, accounts strip, the drop zone (with
    Add by hand as its second tab), Waiting for you and one upload history."""
    with get_db() as conn:
        accounts = _live_accounts(conn, acting.id)
        categories = list_all_categories(conn, acting.id)
        category_groups = menu_groups(conn, acting.id)
        overdue = statement_due.visible_overdue(conn, current, acting)
        payments_due = card_payments.notices(conn, current, acting)
        strip = upload_page.strip_cards(conn, current, acting)
        history = _history_context(request, conn, current, acting)
    # Which account the drop zone starts on: ?account=<id> (a notification's tap, an Upload button), else the
    # first overdue one, else none ("work it out for each file").
    ids = {a["id"] for a in accounts}
    preselect = int(account) if account.isdigit() and int(account) in ids else None
    if preselect is None and overdue and overdue[0]["account_id"] in ids:
        preselect = overdue[0]["account_id"]
    upload_accounts = [{
        "id": a["id"], "name": a["name"], "type": a["type"], "last4": a["account_number_last4"] or "",
        "pref": a["preferred_upload_method"] or "both", "layout": upload_page.read_layout(a["csv_layout"]),
        "skip": a["skip_confirmation"],
    } for a in accounts]

    return templates.TemplateResponse(
        request, "uploads_list.html",
        {
            **history,
            "user": acting, "accounts": accounts, "categories": categories, "category_groups": category_groups,
            "upload_accounts": upload_accounts,
            "preselect": preselect, "overdue": overdue, "due_next": "uploads", "strip": strip,
            "payments_due": payments_due,
            "all_accept": upload_page.ALL_ACCEPT, "notice": notices.text(notice),
            "acting_as_banner": _acting_banner(current, acting),
            # is_admin (in history): current, NOT acting — acting.is_admin is always False while acting-as
            # someone else, which is exactly when an admin would want the debug link most
            "active_page": "uploads",
        },
    )
