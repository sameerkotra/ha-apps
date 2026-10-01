"""Review workflow (SPEC.md section 7): the Review queue, confirming/correcting a flagged transaction, acknowledging a balance mismatch, and removing a wrongly extracted row from an unconfirmed statement."""
from pathlib import Path
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..auth import User, get_current_user, get_acting_user
from ..categorize import categorize_transactions, list_all_categories
from ..db import get_db
from ..version import APP_VERSION
from ..matching import auto_link_unambiguous, cascade_unlink_on_delete
from ..parser.pipeline import RECONCILIATION_TOLERANCE, delete_pdf, finalize_if_ready
from .accounts import _acting_banner, _acting_qs

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
templates.env.globals["app_version"] = APP_VERSION


@router.get("/review")
def review_queue(
    request: Request,
    statement_id: int | None = None,
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """statement_id (optional, from the Uploads "Needs review" row) narrows
    both tables to that one statement."""
    with get_db() as conn:
        txn_params: list = [acting.id]
        txn_filter = ""
        if statement_id is not None:
            txn_filter = "AND t.statement_id = ?"
            txn_params.append(statement_id)
        transactions = conn.execute(
            f"""
            SELECT t.*, a.name AS account_name
            FROM transactions t
            JOIN accounts a ON a.id = t.account_id
            WHERE t.user_id = ? AND t.deleted_at IS NULL AND t.review_status = 'pending_review' {txn_filter}
            ORDER BY t.date DESC, t.id DESC
            """,
            txn_params,
        ).fetchall()
        # Statements flagged ONLY by balance reconciliation — none of
        # their own transactions individually failed the amount check
        # (if one had, it would already be in `transactions` above, and
        # this statement's own status would stay pending_review even
        # after acknowledge_mismatch clears the reconciliation note,
        # until that row also resolves — see finalize_if_ready).
        stmt_params: list = [acting.id]
        stmt_filter = ""
        if statement_id is not None:
            stmt_filter = "AND s.id = ?"
            stmt_params.append(statement_id)
        mismatched_statements = conn.execute(
            f"""
            SELECT s.*, a.name AS account_name
            FROM statements s
            JOIN accounts a ON a.id = s.account_id
            WHERE s.user_id = ? AND s.deleted_at IS NULL AND s.status = 'pending_review'
              AND s.balance_mismatch IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM transactions t2
                  WHERE t2.statement_id = s.id AND t2.deleted_at IS NULL AND t2.review_status = 'pending_review'
              )
              {stmt_filter}
            ORDER BY s.uploaded_at DESC
            """,
            stmt_params,
        ).fetchall()
        all_categories = list_all_categories(conn, acting.id)
        skipped = _skipped_duplicates(conn, acting.id, statement_id)

    return templates.TemplateResponse(
        request, "review_queue.html",
        {
            "skipped": skipped,
            "user": acting, "transactions": transactions,
            "mismatched_statements": mismatched_statements,
            "categories": all_categories,
            "acting_as_banner": _acting_banner(current, acting),
            "acting_qs": _acting_qs(current, acting),
            "active_page": "review",
            "statement_filter_id": statement_id,
        },
    )


@router.post("/transaction-resolve")
def resolve_transaction(
    request: Request,
    id: int = Form(...),
    date: str = Form(...),
    amount: str = Form(...),
    description: str = Form(...),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """Resolves a flagged transaction: the value shown — possibly edited
    by the user if they caught something the automated checks didn't
    pinpoint exactly, possibly unchanged if they just verified it against
    the source PDF and it was right — becomes authoritative, and the row
    clears to review_status = 'clean'. There's no second extraction's
    value to pick between (see this module's docstring); the workflow is
    confirm-or-correct against the source PDF, not choose-between-two-
    values."""
    try:
        amount_value = round(float(amount), 2)
    except ValueError:
        return JSONResponse({"error": "Amount must be a number"}, status_code=400)
    if amount_value == 0:
        return JSONResponse({"error": "Amount can't be zero"}, status_code=400)
    if not date.strip() or not description.strip():
        return JSONResponse({"error": "Date and description are required"}, status_code=400)

    with get_db() as conn:
        row = conn.execute(
            "SELECT id, statement_id FROM transactions WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
            (id, acting.id),
        ).fetchone()
        if row is None:
            return templates.TemplateResponse(request, "not_found.html", {"user": acting}, status_code=404)
        conn.execute(
            "UPDATE transactions SET date = ?, amount = ?, description = ?, "
            "review_status = 'clean', flag_reason = NULL WHERE id = ?",
            (date.strip(), amount_value, description.strip(), id),
        )
        conn.commit()
        finalize_if_ready(conn, row["statement_id"])
        conn.commit()

    qs = _acting_qs(current, acting)
    return RedirectResponse(url=f"review{'?' + qs if qs else ''}", status_code=303)


@router.post("/statement-acknowledge-mismatch")
def acknowledge_mismatch(
    request: Request,
    id: int = Form(...),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """The adapted "approve all agreed rows" shortcut (see this module's
    docstring) — closes out a statement flagged purely by balance
    reconciliation once a human has checked the source PDF and confirmed
    the transactions are right (or fixed what wasn't). balance_mismatch
    stays on the row afterward as a historical record of what was found,
    it isn't cleared — only status changes."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, pdf_path FROM statements WHERE id = ? AND user_id = ? AND deleted_at IS NULL "
            "AND status = 'pending_review' AND balance_mismatch IS NOT NULL AND needs_confirmation = 0",
            (id, acting.id),
        ).fetchone()
        if row is None:
            return templates.TemplateResponse(request, "not_found.html", {"user": acting}, status_code=404)
        conn.execute("UPDATE statements SET status = 'complete' WHERE id = ?", (id,))
        conn.commit()
        delete_pdf(conn, id, row["pdf_path"])

    qs = _acting_qs(current, acting)
    return RedirectResponse(url=f"review{'?' + qs if qs else ''}", status_code=303)


@router.post("/statement-remove-transaction")
def statement_remove_transaction(
    request: Request,
    transaction_id: int = Form(...),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """Permanently remove one wrongly extracted transaction (typically a line
    read twice) from a statement that is still `pending_review` — a hard
    delete, since it was never a real transaction. Refused once the
    statement is complete (use the normal restorable Delete). Afterwards the
    balance check is recomputed and the statement finalized if nothing else
    is pending."""
    with get_db() as conn:
        txn = conn.execute(
            "SELECT t.id, t.statement_id FROM transactions t "
            "JOIN statements s ON s.id = t.statement_id "
            "WHERE t.id = ? AND t.user_id = ? AND t.deleted_at IS NULL "
            "AND s.status = 'pending_review' AND s.deleted_at IS NULL",
            (transaction_id, acting.id),
        ).fetchone()
        if txn is None:
            return JSONResponse(
                {"error": "That transaction wasn't found, was already removed, or its statement has already completed."},
                status_code=404,
            )
        statement_id = txn["statement_id"]

        cascade_unlink_on_delete(conn, acting.id, [transaction_id])
        conn.execute("DELETE FROM transactions WHERE id = ?", (transaction_id,))

        stmt = conn.execute(
            "SELECT previous_balance, new_balance FROM statements WHERE id = ?", (statement_id,)
        ).fetchone()
        if stmt["previous_balance"] is not None and stmt["new_balance"] is not None:
            remaining = conn.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE statement_id = ? AND deleted_at IS NULL",
                (statement_id,),
            ).fetchone()[0]
            printed_delta = round(stmt["new_balance"] - stmt["previous_balance"], 2)
            actual_delta = round(remaining, 2)
            if abs(actual_delta - printed_delta) <= RECONCILIATION_TOLERANCE:
                conn.execute("UPDATE statements SET balance_mismatch = NULL WHERE id = ?", (statement_id,))
            else:
                conn.execute(
                    "UPDATE statements SET balance_mismatch = ? WHERE id = ?",
                    (
                        f"Statement's printed balance moved {printed_delta:+.2f} "
                        f"(previous {stmt['previous_balance']:.2f} -> new {stmt['new_balance']:.2f}), but the "
                        f"remaining extracted transactions account for {actual_delta:+.2f} — a "
                        "transaction may still be missing, duplicated, or misread.",
                        statement_id,
                    ),
                )
        conn.commit()
        completed = finalize_if_ready(conn, statement_id)
        conn.commit()

    if request.headers.get("hx-request") == "true":
        return JSONResponse({"completed": completed})

    qs = _acting_qs(current, acting)
    redirect_to = "uploads" if completed else f"statement-confirm?id={statement_id}"
    return RedirectResponse(url=redirect_to + (("&" if "?" in redirect_to else "?") + qs if qs else ""), status_code=303)


# ---- transactions an import skipped as possible duplicates (skipped_duplicates) ------------------

def _skipped_duplicates(conn, user_id: str, statement_id: int | None) -> list:
    """Still-skipped rows whose statement or CSV import is live, newest first."""
    params: list = [user_id]
    extra = ""
    if statement_id is not None:
        extra = "AND d.statement_id = ?"
        params.append(statement_id)
    return conn.execute(
        f"""
        SELECT d.*, a.name AS account_name, s.original_filename AS statement_file, c.original_filename AS csv_file,
               m.date AS matched_date, m.description AS matched_description, m.amount AS matched_amount
        FROM skipped_duplicates d
        JOIN accounts a ON a.id = d.account_id AND a.deleted_at IS NULL
        LEFT JOIN statements s ON s.id = d.statement_id
        LEFT JOIN csv_imports c ON c.id = d.csv_import_id
        LEFT JOIN transactions m ON m.id = d.matched_transaction_id AND m.deleted_at IS NULL
        WHERE d.user_id = ? AND d.status = 'skipped'
          AND ((d.statement_id IS NOT NULL AND s.deleted_at IS NULL) OR (d.csv_import_id IS NOT NULL AND c.deleted_at IS NULL))
          {extra}
        ORDER BY d.date DESC, d.id DESC
        """,
        params,
    ).fetchall()


def _back_to_review(current: User, acting: User, statement_id: str = "") -> RedirectResponse:
    qs = _acting_qs(current, acting)
    parts = [p for p in (f"statement_id={statement_id}" if statement_id.isdigit() else "", qs) if p]
    return RedirectResponse(url="review" + ("?" + "&".join(parts) if parts else "") + "#skipped", status_code=303)


@router.post("/skipped-duplicate-insert")
def skipped_duplicate_insert(
    id: int = Form(...),
    statement_filter: str = Form(""),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """Insert a skipped possible duplicate after all: the charge was real (SPEC.md section 7)."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT d.*, a.type AS account_type FROM skipped_duplicates d JOIN accounts a ON a.id = d.account_id "
            "WHERE d.id = ? AND d.user_id = ? AND d.status = 'skipped' AND a.deleted_at IS NULL",
            (id, acting.id),
        ).fetchone()
        if row is not None:
            cur = conn.execute(
                "INSERT INTO transactions (user_id, account_id, statement_id, csv_import_id, date, amount, description, "
                "category, category_source, review_status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'clean')",
                (acting.id, row["account_id"], row["statement_id"], row["csv_import_id"], row["date"], row["amount"],
                 row["description"], row["category"], "csv_explicit" if row["category"] else None),
            )
            txn_id = cur.lastrowid
            if not row["category"]:
                if row["account_type"] == "credit_card" and row["amount"] < 0:
                    conn.execute("UPDATE transactions SET category = 'Payments', category_source = 'card_payment' "
                                 "WHERE id = ?", (txn_id,))
                else:   # rules only: no model call while the page waits
                    categorize_transactions(conn, acting.id, "", "", [(txn_id, row["description"])])
            conn.execute(
                "UPDATE skipped_duplicates SET status = 'inserted', inserted_transaction_id = ?, "
                "resolved_at = datetime('now') WHERE id = ?", (txn_id, id))
            auto_link_unambiguous(conn, acting.id)
            conn.commit()
    return _back_to_review(current, acting, statement_filter)


@router.post("/skipped-duplicate-dismiss")
def skipped_duplicate_dismiss(
    id: int = Form(...),
    statement_filter: str = Form(""),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """It really was a duplicate: keep it skipped and take it off the list."""
    with get_db() as conn:
        conn.execute(
            "UPDATE skipped_duplicates SET status = 'dismissed', resolved_at = datetime('now') "
            "WHERE id = ? AND user_id = ? AND status = 'skipped'", (id, acting.id))
    return _back_to_review(current, acting, statement_filter)
