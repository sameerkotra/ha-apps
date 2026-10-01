"""Soft-delete flows for statements, CSV imports, accounts and transactions,
plus the full-user wipe (SPEC.md section 11, Scope A / Scope B).

Both scopes only ever set deleted_at — nothing here issues a DELETE against
a live row. The periodic purge (app/purge.py) is what eventually
hard-deletes anything past the 30-day grace period; this module's job is
just moving rows into and out of that soft-deleted state, plus the
type-to-confirm friction section 11 asks for before either scope fires
("counts shown up front... then a type-to-confirm field... before the
button enables").

Every route is scoped to the acting user's own rows via user_id/account_id
in every WHERE clause — mirrors every other route in this app (see
routes/transactions.py's bulk_action docstring).
"""
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from ..auth import User, get_current_user, get_acting_user, require_admin
from ..db import get_db
from ..toll_trips import rebuild_trips
from ..version import APP_VERSION
from ..matching import cascade_unlink_on_delete
from ..purge import (
    account_has_live_rows, hard_delete_account, hard_delete_csv_import, hard_delete_statement,
    hard_delete_transaction, hard_delete_utility_bill, hard_delete_toll_statement,
)
from .accounts import _acting_banner, _acting_qs

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
templates.env.globals["app_version"] = APP_VERSION


def _statement_confirm_label(account_name: str, statement_period: str | None) -> str:
    """The exact string the user must type to confirm a Scope-A delete
    (SPEC.md section 11: "account name/period"). A statement
    missing its period (never fully parsed, say) still needs SOME typeable
    label, so this falls back to a fixed placeholder rather than leaving
    the confirm phrase blank — an empty phrase would make the type-to-
    confirm check trivially satisfiable by an empty input."""
    return f"{account_name} {statement_period or '(no period on file)'}"


def _csv_import_confirm_label(original_filename: str | None, csv_import_id: int) -> str:
    """The text to type to delete a CSV import: its filename (an import can
    span several accounts, so there's no account name), else "CSV import #N"."""
    return original_filename or f"CSV import #{csv_import_id}"


def _with_qs(path: str, current: User, acting: User) -> str:
    qs = _acting_qs(current, acting)
    return f"{path}?{qs}" if qs else path


@router.get("/recently-deleted")
def recently_deleted(
    request: Request,
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
    _admin: User = Depends(require_admin),  # Recently deleted is admin only (SPEC.md section 11)
):
    with get_db() as conn:
        statements = conn.execute(
            """
            SELECT s.id, s.statement_period, s.deleted_at, a.name AS account_name,
                   (SELECT COUNT(*) FROM transactions t
                    WHERE t.statement_id = s.id AND t.deleted_at IS NOT NULL) AS txn_count
            FROM statements s
            JOIN accounts a ON a.id = s.account_id
            WHERE s.user_id = ? AND s.deleted_at IS NOT NULL
            ORDER BY s.deleted_at DESC
            """,
            (acting.id,),
        ).fetchall()

        # A transaction whose CSV import is also deleted comes back with the import.
        csv_imports = conn.execute(
            """
            SELECT id, original_filename, imported_at, deleted_at,
                   (SELECT COUNT(*) FROM transactions t
                    WHERE t.csv_import_id = csv_imports.id AND t.deleted_at IS NOT NULL) AS txn_count
            FROM csv_imports
            WHERE user_id = ? AND deleted_at IS NOT NULL
            ORDER BY deleted_at DESC
            """,
            (acting.id,),
        ).fetchall()

        # Standalone deleted transactions only (ones whose statement or CSV import
        # is also deleted come back with their parent).
        transactions = conn.execute(
            """
            SELECT t.id, t.date, t.description, t.amount, t.deleted_at, a.name AS account_name
            FROM transactions t
            JOIN accounts a ON a.id = t.account_id
            LEFT JOIN statements s ON s.id = t.statement_id
            LEFT JOIN csv_imports ci ON ci.id = t.csv_import_id
            WHERE t.user_id = ? AND t.deleted_at IS NOT NULL
              AND (t.statement_id IS NULL OR s.deleted_at IS NULL)
              AND (t.csv_import_id IS NULL OR ci.deleted_at IS NULL)
            ORDER BY t.deleted_at DESC
            """,
            (acting.id,),
        ).fetchall()

        accounts = conn.execute(
            """
            SELECT id, name, type, deleted_at
            FROM accounts
            WHERE user_id = ? AND deleted_at IS NOT NULL
            ORDER BY deleted_at DESC
            """,
            (acting.id,),
        ).fetchall()

        # Utility bills (never linked to anything; restored by /utility-restore).
        utility_bills = conn.execute(
            """
            SELECT id, provider, utility_type, period_start_date, period_end_date,
                   original_filename, deleted_at
            FROM utility_bills
            WHERE user_id = ? AND deleted_at IS NOT NULL
            ORDER BY deleted_at DESC
            """,
            (acting.id,),
        ).fetchall()

        # E-470 statements (restored with their passes by /toll-restore).
        toll_statements = conn.execute(
            """
            SELECT id, original_filename, period_start_date, period_end_date, deleted_at,
                   (SELECT COUNT(*) FROM toll_transactions t WHERE t.statement_id = toll_statements.id) AS pass_count
            FROM toll_statements
            WHERE user_id = ? AND deleted_at IS NOT NULL
            ORDER BY deleted_at DESC
            """,
            (acting.id,),
        ).fetchall()

    return templates.TemplateResponse(
        request,
        "recently_deleted.html",
        {
            "user": acting,
            "statements": statements,
            "csv_imports": csv_imports,
            "transactions": transactions,
            "accounts": accounts,
            "utility_bills": utility_bills,
            "toll_statements": toll_statements,
            "acting_as_banner": _acting_banner(current, acting),
            "acting_qs": _acting_qs(current, acting),
            "active_page": "recently_deleted",
        },
    )


@router.get("/statement-delete-confirm")
def statement_delete_confirm(
    request: Request,
    id: int,
    error: str = "",
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    with get_db() as conn:
        stmt = conn.execute(
            "SELECT s.*, a.name AS account_name FROM statements s JOIN accounts a ON a.id = s.account_id "
            "WHERE s.id = ? AND s.user_id = ? AND s.deleted_at IS NULL",
            (id, acting.id),
        ).fetchone()
        if stmt is None:
            return RedirectResponse(url=_with_qs("uploads", current, acting), status_code=303)

        counts = conn.execute(
            "SELECT COUNT(*) AS n, SUM(CASE WHEN transfer_pair_id IS NOT NULL THEN 1 ELSE 0 END) AS transfers "
            "FROM transactions WHERE statement_id = ? AND deleted_at IS NULL",
            (id,),
        ).fetchone()

    return templates.TemplateResponse(
        request,
        "statement_delete_confirm.html",
        {
            "user": acting,
            "statement": stmt,
            "txn_count": counts["n"],
            "transfer_count": counts["transfers"] or 0,
            "confirm_label": _statement_confirm_label(stmt["account_name"], stmt["statement_period"]),
            "error": error,
            "acting_as_banner": _acting_banner(current, acting),
            "acting_qs": _acting_qs(current, acting),
            "active_page": "uploads",
        },
    )


@router.post("/statement-delete")
def statement_delete(
    request: Request,
    id: int = Form(...),
    confirm_text: str = Form(""),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    with get_db() as conn:
        stmt = conn.execute(
            "SELECT s.*, a.name AS account_name FROM statements s JOIN accounts a ON a.id = s.account_id "
            "WHERE s.id = ? AND s.user_id = ? AND s.deleted_at IS NULL",
            (id, acting.id),
        ).fetchone()
        if stmt is None:
            return RedirectResponse(url=_with_qs("uploads", current, acting), status_code=303)

        expected = _statement_confirm_label(stmt["account_name"], stmt["statement_period"])
        if confirm_text.strip() != expected:
            target = "statement-delete-confirm?" + urlencode(
                {"id": id, "error": "Typed text didn't match — nothing was deleted."}
            )
            qs = _acting_qs(current, acting)
            if qs:
                target += "&" + qs
            return RedirectResponse(url=target, status_code=303)

        # SPEC.md section 11's transfer-pair cascade — a statement
        # being deleted can hold one side of a pair whose partner lives
        # OUTSIDE it (a different statement, or a manually-entered row), so
        # this must run before the soft-delete below exactly like the
        # transactions-bulk delete action already does.
        txn_ids = [
            r["id"] for r in conn.execute(
                "SELECT id FROM transactions WHERE statement_id = ? AND user_id = ? AND deleted_at IS NULL",
                (id, acting.id),
            ).fetchall()
        ]
        cascade_unlink_on_delete(conn, acting.id, txn_ids)
        if txn_ids:
            placeholders = ",".join("?" for _ in txn_ids)
            conn.execute(
                f"UPDATE transactions SET deleted_at = datetime('now') WHERE id IN ({placeholders})",
                tuple(txn_ids),
            )
        conn.execute("UPDATE statements SET deleted_at = datetime('now') WHERE id = ?", (id,))
        conn.commit()

    return RedirectResponse(url=_with_qs("uploads", current, acting), status_code=303)


@router.post("/statement-restore")
def statement_restore(
    request: Request,
    id: int = Form(...),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
    _admin: User = Depends(require_admin),  # Recently deleted is admin only (SPEC.md section 11)
):
    with get_db() as conn:
        conn.execute(
            "UPDATE statements SET deleted_at = NULL WHERE id = ? AND user_id = ? AND deleted_at IS NOT NULL",
            (id, acting.id),
        )
        conn.execute(
            "UPDATE transactions SET deleted_at = NULL "
            "WHERE statement_id = ? AND user_id = ? AND deleted_at IS NOT NULL",
            (id, acting.id),
        )
        conn.commit()
    return RedirectResponse(url=_with_qs("recently-deleted", current, acting), status_code=303)


@router.get("/csv-import-delete-confirm")
def csv_import_delete_confirm(
    request: Request,
    id: int,
    error: str = "",
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """Delete confirmation for one CSV import: counts first, then its filename
    typed to confirm (SPEC.md section 11)."""
    with get_db() as conn:
        csv_import = conn.execute(
            "SELECT * FROM csv_imports WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
            (id, acting.id),
        ).fetchone()
        if csv_import is None:
            return RedirectResponse(url=_with_qs("uploads", current, acting), status_code=303)

        counts = conn.execute(
            "SELECT COUNT(*) AS n, SUM(CASE WHEN transfer_pair_id IS NOT NULL THEN 1 ELSE 0 END) AS transfers "
            "FROM transactions WHERE csv_import_id = ? AND deleted_at IS NULL",
            (id,),
        ).fetchone()

    return templates.TemplateResponse(
        request,
        "csv_import_delete_confirm.html",
        {
            "user": acting,
            "csv_import": csv_import,
            "txn_count": counts["n"],
            "transfer_count": counts["transfers"] or 0,
            "confirm_label": _csv_import_confirm_label(csv_import["original_filename"], csv_import["id"]),
            "error": error,
            "acting_as_banner": _acting_banner(current, acting),
            "acting_qs": _acting_qs(current, acting),
            "active_page": "uploads",
        },
    )


@router.post("/csv-import-delete")
def csv_import_delete(
    request: Request,
    id: int = Form(...),
    confirm_text: str = Form(""),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    with get_db() as conn:
        csv_import = conn.execute(
            "SELECT * FROM csv_imports WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
            (id, acting.id),
        ).fetchone()
        if csv_import is None:
            return RedirectResponse(url=_with_qs("uploads", current, acting), status_code=303)

        expected = _csv_import_confirm_label(csv_import["original_filename"], csv_import["id"])
        if confirm_text.strip() != expected:
            target = "csv-import-delete-confirm?" + urlencode(
                {"id": id, "error": "Typed text didn't match — nothing was deleted."}
            )
            qs = _acting_qs(current, acting)
            if qs:
                target += "&" + qs
            return RedirectResponse(url=target, status_code=303)

        # Same transfer-pair cascade as statement delete above — an
        # imported row can be one side of a pair whose partner lives
        # outside this import (a different CSV import, a parsed
        # statement, or a manually-entered row).
        txn_ids = [
            r["id"] for r in conn.execute(
                "SELECT id FROM transactions WHERE csv_import_id = ? AND user_id = ? AND deleted_at IS NULL",
                (id, acting.id),
            ).fetchall()
        ]
        cascade_unlink_on_delete(conn, acting.id, txn_ids)

        # One shared timestamp for both UPDATEs (rather than two separate
        # datetime('now') calls) so csv_import_restore below can match
        # transactions back to this exact delete operation, same
        # paired-timestamp approach account_restore already relies on.
        now = conn.execute("SELECT datetime('now') AS now").fetchone()["now"]
        if txn_ids:
            placeholders = ",".join("?" for _ in txn_ids)
            conn.execute(
                f"UPDATE transactions SET deleted_at = ? WHERE id IN ({placeholders})",
                (now, *txn_ids),
            )
        conn.execute("UPDATE csv_imports SET deleted_at = ? WHERE id = ?", (now, id))
        conn.commit()

    return RedirectResponse(url=_with_qs("uploads", current, acting), status_code=303)


@router.post("/csv-import-restore")
def csv_import_restore(
    request: Request,
    id: int = Form(...),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
    _admin: User = Depends(require_admin),  # Recently deleted is admin only (SPEC.md section 11)
):
    with get_db() as conn:
        row = conn.execute(
            "SELECT deleted_at FROM csv_imports WHERE id = ? AND user_id = ? AND deleted_at IS NOT NULL",
            (id, acting.id),
        ).fetchone()
        if row is not None:
            deleted_at = row["deleted_at"]
            conn.execute("UPDATE csv_imports SET deleted_at = NULL WHERE id = ?", (id,))
            # Only restores transactions soft-deleted in the SAME
            # operation as this import (matched by the shared timestamp
            # csv_import_delete stamped on both) — same reasoning as
            # account_restore above: a transaction from this import that
            # was independently deleted at a different time (via the
            # regular transaction-delete path) keeps its own deleted_at
            # and stays deleted, rather than coming back just because
            # the import did.
            conn.execute(
                "UPDATE transactions SET deleted_at = NULL WHERE csv_import_id = ? AND deleted_at = ?",
                (id, deleted_at),
            )
            conn.commit()
    return RedirectResponse(url=_with_qs("recently-deleted", current, acting), status_code=303)


@router.post("/transaction-restore")
def transaction_restore(
    request: Request,
    id: int = Form(...),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
    _admin: User = Depends(require_admin),  # Recently deleted is admin only (SPEC.md section 11)
):
    with get_db() as conn:
        conn.execute(
            "UPDATE transactions SET deleted_at = NULL WHERE id = ? AND user_id = ? AND deleted_at IS NOT NULL",
            (id, acting.id),
        )
        conn.commit()
    return RedirectResponse(url=_with_qs("recently-deleted", current, acting), status_code=303)


# --- Deleting one account ---------------------------------------------------

_ACCOUNT_SQL = "SELECT * FROM accounts WHERE id = ? AND user_id = ? AND deleted_at IS NULL"


def _account_delete_counts(conn, account_id: int) -> dict:
    return dict(conn.execute(
        """
        SELECT
          (SELECT COUNT(*) FROM statements WHERE account_id = ? AND deleted_at IS NULL) AS statements,
          (SELECT COUNT(*) FROM transactions WHERE account_id = ? AND deleted_at IS NULL) AS transactions,
          (SELECT COUNT(*) FROM transactions
             WHERE account_id = ? AND deleted_at IS NULL AND transfer_pair_id IS NOT NULL) AS transfers,
          (SELECT COUNT(*) FROM statements WHERE account_id = ? AND deleted_at IS NULL
             AND status = 'processing') AS processing
        """,
        (account_id,) * 4,
    ).fetchone())


@router.get("/account-delete-confirm")
def account_delete_confirm(
    request: Request,
    id: int,
    error: str = "",
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    with get_db() as conn:
        account = conn.execute(_ACCOUNT_SQL, (id, acting.id)).fetchone()
        if account is None:
            return RedirectResponse(url=_with_qs("accounts", current, acting), status_code=303)
        counts = _account_delete_counts(conn, id)

    return templates.TemplateResponse(
        request,
        "account_delete_confirm.html",
        {
            "user": acting, "account": account, "counts": counts, "confirm_label": account["name"],
            "error": error,
            "acting_as_banner": _acting_banner(current, acting),
            "acting_qs": _acting_qs(current, acting),
            "active_page": "accounts",
        },
    )


@router.post("/account-delete")
def account_delete(
    id: int = Form(...),
    confirm_text: str = Form(""),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """Soft-deletes the account together with its live statements and
    transactions, all stamped with ONE timestamp so account_restore can bring
    back exactly this batch (a statement deleted earlier keeps its own,
    earlier stamp and stays deleted). A transfer whose partner lives in
    another account is unlinked first, same cascade as deleting a statement."""
    def _back_to_confirm(message: str) -> RedirectResponse:
        target = "account-delete-confirm?" + urlencode({"id": id, "error": message})
        qs = _acting_qs(current, acting)
        return RedirectResponse(url=target + ("&" + qs if qs else ""), status_code=303)

    with get_db() as conn:
        account = conn.execute(_ACCOUNT_SQL, (id, acting.id)).fetchone()
        if account is None:
            return RedirectResponse(url=_with_qs("accounts", current, acting), status_code=303)
        if confirm_text.strip() != account["name"]:
            return _back_to_confirm("Typed name didn't match \u2014 nothing was deleted.")
        if _account_delete_counts(conn, id)["processing"]:
            return _back_to_confirm(
                "A statement for this account is still being processed \u2014 wait for it to finish, then try again."
            )

        txn_ids = [r["id"] for r in conn.execute(
            "SELECT id FROM transactions WHERE account_id = ? AND user_id = ? AND deleted_at IS NULL", (id, acting.id)
        ).fetchall()]
        cascade_unlink_on_delete(conn, acting.id, txn_ids)

        stamp = conn.execute("SELECT datetime('now')").fetchone()[0]
        for table, column in (("transactions", "account_id"), ("statements", "account_id"), ("accounts", "id")):
            conn.execute(
                f"UPDATE {table} SET deleted_at = ? WHERE {column} = ? AND user_id = ? AND deleted_at IS NULL",
                (stamp, id, acting.id),
            )
        conn.commit()

    return RedirectResponse(url=_with_qs("accounts", current, acting), status_code=303)


@router.post("/account-restore")
def account_restore(
    request: Request,
    id: int = Form(...),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
    _admin: User = Depends(require_admin),  # Recently deleted is admin only (SPEC.md section 11)
):
    with get_db() as conn:
        row = conn.execute(
            "SELECT deleted_at FROM accounts WHERE id = ? AND user_id = ? AND deleted_at IS NOT NULL",
            (id, acting.id),
        ).fetchone()
        if row is not None:
            deleted_at = row["deleted_at"]
            conn.execute("UPDATE accounts SET deleted_at = NULL WHERE id = ?", (id,))
            # Only restore statements/transactions soft-deleted in the SAME
            # operation as this account (Scope B stamps accounts,
            # statements and transactions with the same deleted_at value
            # in one go — sqlite's datetime('now') has second resolution,
            # so three UPDATEs issued back-to-back in the same request
            # land on the same string). A statement that was independently
            # deleted earlier (Scope A), then later swept up in a full
            # wipe, keeps its own earlier deleted_at and stays deleted —
            # it doesn't come back just because the account did.
            conn.execute(
                "UPDATE statements SET deleted_at = NULL WHERE account_id = ? AND deleted_at = ?",
                (id, deleted_at),
            )
            conn.execute(
                "UPDATE transactions SET deleted_at = NULL WHERE account_id = ? AND deleted_at = ?",
                (id, deleted_at),
            )
            conn.commit()
    return RedirectResponse(url=_with_qs("recently-deleted", current, acting), status_code=303)


# --- Permanent deletion from "Recently deleted" -----------------------------
# One confirm page + one action for every kind of soft-deleted row. Only a row
# that is already soft-deleted and belongs to the acting user can be purged.

def _purge_kind(label, select_sql, details, confirm_text, children_sql, purge):
    return {
        "label": label, "select_sql": select_sql, "details": details,
        "confirm_text": confirm_text, "children_sql": children_sql, "purge": purge,
    }


_PURGEABLE = {
    "statement": _purge_kind(
        "statement",
        "SELECT s.*, a.name AS account_name FROM statements s JOIN accounts a ON a.id = s.account_id "
        "WHERE s.id = ? AND s.user_id = ? AND s.deleted_at IS NOT NULL",
        lambda r: [("Account", r["account_name"]), ("Period", r["statement_period"] or "\u2014"),
                   ("File", r["original_filename"] or "\u2014")],
        lambda r: _statement_confirm_label(r["account_name"], r["statement_period"]),
        "SELECT COUNT(*) FROM transactions WHERE statement_id = ? AND deleted_at IS NOT NULL",
        hard_delete_statement,
    ),
    "csv_import": _purge_kind(
        "CSV import",
        "SELECT * FROM csv_imports WHERE id = ? AND user_id = ? AND deleted_at IS NOT NULL",
        lambda r: [("File", r["original_filename"] or "\u2014"), ("Imported", r["imported_at"])],
        lambda r: _csv_import_confirm_label(r["original_filename"], r["id"]),
        "SELECT COUNT(*) FROM transactions WHERE csv_import_id = ? AND deleted_at IS NOT NULL",
        hard_delete_csv_import,
    ),
    "transaction": _purge_kind(
        "transaction",
        "SELECT t.*, a.name AS account_name FROM transactions t JOIN accounts a ON a.id = t.account_id "
        "WHERE t.id = ? AND t.user_id = ? AND t.deleted_at IS NOT NULL",
        lambda r: [("Date", r["date"]), ("Account", r["account_name"]),
                   ("Description", r["description"]), ("Amount", f"{r['amount']:.2f}")],
        lambda r: "delete",
        None,
        hard_delete_transaction,
    ),
    "utility_bill": _purge_kind(
        "utility bill",
        "SELECT * FROM utility_bills WHERE id = ? AND user_id = ? AND deleted_at IS NOT NULL",
        lambda r: [("File", r["original_filename"] or "\u2014"), ("Provider", r["provider"]),
                   ("Type", r["utility_type"] or "\u2014"),
                   ("Period", f"{r['period_start_date']} \u2013 {r['period_end_date']}" if r["period_start_date"] else "\u2014")],
        lambda r: "delete",
        None,
        hard_delete_utility_bill,
    ),
    "toll_statement": _purge_kind(
        "E-470 toll statement",
        "SELECT * FROM toll_statements WHERE id = ? AND user_id = ? AND deleted_at IS NOT NULL",
        lambda r: [("File", r["original_filename"] or "\u2014"),
                   ("Period", f"{r['period_start_date']} \u2013 {r['period_end_date']}" if r["period_start_date"] else "\u2014")],
        lambda r: "delete",
        "SELECT COUNT(*) FROM toll_transactions WHERE statement_id = ?",
        hard_delete_toll_statement,
    ),
    "account": _purge_kind(
        "account",
        "SELECT * FROM accounts WHERE id = ? AND user_id = ? AND deleted_at IS NOT NULL",
        lambda r: [("Name", r["name"]), ("Type", r["type"])],
        lambda r: r["name"],
        "SELECT (SELECT COUNT(*) FROM transactions WHERE account_id = ?) + (SELECT COUNT(*) FROM statements WHERE account_id = ?)",
        hard_delete_account,
    ),
}


def _purge_target(conn, kind: str, item_id: int, user_id: str):
    spec = _PURGEABLE.get(kind)
    row = conn.execute(spec["select_sql"], (item_id, user_id)).fetchone() if spec else None
    return spec, row


def _purge_child_count(conn, spec, item_id: int) -> int:
    if spec["children_sql"] is None:
        return 0
    return conn.execute(spec["children_sql"], (item_id,) * spec["children_sql"].count("?")).fetchone()[0]


@router.get("/permanent-delete-confirm")
def permanent_delete_confirm(
    request: Request,
    kind: str,
    id: int,
    error: str = "",
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
    _admin: User = Depends(require_admin),  # Recently deleted is admin only (SPEC.md section 11)
):
    with get_db() as conn:
        spec, row = _purge_target(conn, kind, id, acting.id)
        if row is None:
            return RedirectResponse(url=_with_qs("recently-deleted", current, acting), status_code=303)
        child_count = _purge_child_count(conn, spec, id)

    return templates.TemplateResponse(
        request,
        "permanent_delete_confirm.html",
        {
            "user": acting, "kind": kind, "item_id": id, "label": spec["label"],
            "details": spec["details"](row), "child_count": child_count,
            "confirm_label": spec["confirm_text"](row), "error": error,
            "acting_as_banner": _acting_banner(current, acting),
            "acting_qs": _acting_qs(current, acting),
            "active_page": "recently_deleted",
        },
    )


@router.post("/permanent-delete")
def permanent_delete(
    request: Request,
    kind: str = Form(...),
    id: int = Form(...),
    confirm_text: str = Form(""),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
    _admin: User = Depends(require_admin),  # Recently deleted is admin only (SPEC.md section 11)
):
    def _back_to_confirm(message: str) -> RedirectResponse:
        target = "permanent-delete-confirm?" + urlencode({"kind": kind, "id": id, "error": message})
        qs = _acting_qs(current, acting)
        return RedirectResponse(url=target + ("&" + qs if qs else ""), status_code=303)

    with get_db() as conn:
        spec, row = _purge_target(conn, kind, id, acting.id)
        if row is None:
            return RedirectResponse(url=_with_qs("recently-deleted", current, acting), status_code=303)
        if confirm_text.strip() != spec["confirm_text"](row):
            return _back_to_confirm("Typed text didn't match \u2014 nothing was deleted.")
        if kind == "account" and account_has_live_rows(conn, id):
            return _back_to_confirm(
                "This account still has live statements or transactions \u2014 restore or delete them first."
            )
        spec["purge"](conn, id)
        conn.commit()

    return RedirectResponse(url=_with_qs("recently-deleted", current, acting), status_code=303)


@router.get("/user-wipe")
def user_wipe_confirm(
    request: Request,
    error: str = "",
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
    _admin: User = Depends(require_admin),  # Recently deleted is admin only (SPEC.md section 11)
):
    with get_db() as conn:
        counts = conn.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM accounts WHERE user_id = ? AND deleted_at IS NULL) AS accounts,
              (SELECT COUNT(*) FROM statements WHERE user_id = ? AND deleted_at IS NULL) AS statements,
              (SELECT COUNT(*) FROM transactions WHERE user_id = ? AND deleted_at IS NULL) AS transactions,
              (SELECT COUNT(*) FROM transactions
                 WHERE user_id = ? AND deleted_at IS NULL AND transfer_pair_id IS NOT NULL) AS transfers,
              (SELECT COUNT(*) FROM utility_bills WHERE user_id = ? AND deleted_at IS NULL) AS utility_bills,
              (SELECT COUNT(*) FROM toll_statements WHERE user_id = ? AND deleted_at IS NULL) AS toll_statements
            """,
            (acting.id,) * 6,
        ).fetchone()

    return templates.TemplateResponse(
        request,
        "user_wipe.html",
        {
            "user": acting,
            "counts": counts,
            "error": error,
            "acting_as_banner": _acting_banner(current, acting),
            "acting_qs": _acting_qs(current, acting),
            "active_page": "recently_deleted",
        },
    )


@router.post("/user-wipe")
def user_wipe(
    request: Request,
    confirm_text: str = Form(""),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
    _admin: User = Depends(require_admin),  # Recently deleted is admin only (SPEC.md section 11)
):
    if confirm_text.strip() != acting.name:
        target = "user-wipe?" + urlencode({"error": "Typed name didn't match — nothing was deleted."})
        qs = _acting_qs(current, acting)
        if qs:
            target += "&" + qs
        return RedirectResponse(url=target, status_code=303)

    with get_db() as conn:
        stamp = conn.execute("SELECT datetime('now')").fetchone()[0]  # one batch, one timestamp
        # No cascade_unlink_on_delete call here (unlike statement delete /
        # transactions-bulk's delete action) — every transfer pair links
        # two transactions in this SAME user's own accounts, so a full
        # wipe always takes both sides of any pair together. There's never
        # a surviving partner left needing a revert.
        conn.execute(
            "UPDATE transactions SET deleted_at = ? WHERE user_id = ? AND deleted_at IS NULL",
            (stamp, acting.id),
        )
        conn.execute(
            "UPDATE statements SET deleted_at = ? WHERE user_id = ? AND deleted_at IS NULL",
            (stamp, acting.id),
        )
        # CSV import history goes too, or it would list imports with no transactions.
        conn.execute(
            "UPDATE csv_imports SET deleted_at = ? WHERE user_id = ? AND deleted_at IS NULL",
            (stamp, acting.id),
        )
        conn.execute(
            "UPDATE accounts SET deleted_at = ? WHERE user_id = ? AND deleted_at IS NULL",
            (stamp, acting.id),
        )
        # Utility bills and E-470 statements (with their passes) go too; each
        # is restored from Recently deleted on its own, like any single delete.
        for table in ("utility_bills", "toll_transactions", "toll_statements"):
            conn.execute(
                f"UPDATE {table} SET deleted_at = ? WHERE user_id = ? AND deleted_at IS NULL",
                (stamp, acting.id),
            )
        conn.commit()
        rebuild_trips(conn, acting.id)

    return RedirectResponse(url=_with_qs("accounts", current, acting), status_code=303)
