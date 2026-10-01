"""Soft-delete purge (SPEC.md section 11): a background task that
permanently removes anything soft-deleted more than PURGE_GRACE_DAYS ago, once
at startup and then daily, plus the hard_delete_* helpers used by Recently
deleted's Purge.

Each row is purged on its own deleted_at clock (never inferred from a
parent's). Files are removed with the rows. An account is purged only when
nothing live still references it — its foreign keys cascade, and a row
restored after the account was deleted must not be taken with it.
"""
import asyncio
import logging

from .db import get_db
from .storage import remove_stored_file

logger = logging.getLogger(__name__)

PURGE_GRACE_DAYS = 30
PURGE_INTERVAL_SECONDS = 24 * 60 * 60
_CUTOFF_MODIFIER = f"-{PURGE_GRACE_DAYS} days"


_remove_file = remove_stored_file  # best effort; never touches a file outside PENDING_DIR


# --- Immediate, user-requested permanent deletion (Recently deleted page) ---
# Each function hard-deletes ONE row the person already soft-deleted, scoped
# by the caller to that user. The schema does the rest: transactions.statement_id
# / csv_import_id / transfer_pair_id are ON DELETE SET NULL, and utility_bills
# cascades to its electric_meter_details. Callers commit.

def hard_delete_statement(conn, statement_id: int) -> None:
    row = conn.execute("SELECT pdf_path FROM statements WHERE id = ?", (statement_id,)).fetchone()
    if row is not None:
        _remove_file(row["pdf_path"])
    # Only its own soft-deleted rows: a transaction someone restored on its
    # own is live data and just loses the link (SET NULL).
    conn.execute("DELETE FROM transactions WHERE statement_id = ? AND deleted_at IS NOT NULL", (statement_id,))
    conn.execute("DELETE FROM statements WHERE id = ?", (statement_id,))


def hard_delete_csv_import(conn, csv_import_id: int) -> None:
    conn.execute("DELETE FROM transactions WHERE csv_import_id = ? AND deleted_at IS NOT NULL", (csv_import_id,))
    conn.execute("DELETE FROM csv_imports WHERE id = ?", (csv_import_id,))


def hard_delete_transaction(conn, transaction_id: int) -> None:
    conn.execute("DELETE FROM transactions WHERE id = ?", (transaction_id,))


def _remove_utility_pdf_if_unused(conn, pdf_path: str | None) -> None:
    """A dual-fuel statement's electric and gas bills share one PDF: keep it
    while any other bill row still points at it."""
    if pdf_path and conn.execute("SELECT 1 FROM utility_bills WHERE pdf_path = ?", (pdf_path,)).fetchone() is None:
        _remove_file(pdf_path)


def hard_delete_utility_bill(conn, bill_id: int) -> None:
    row = conn.execute("SELECT pdf_path FROM utility_bills WHERE id = ?", (bill_id,)).fetchone()
    conn.execute("DELETE FROM utility_bills WHERE id = ?", (bill_id,))
    if row is not None:
        _remove_utility_pdf_if_unused(conn, row["pdf_path"])


def drop_orphan_toll_overrides(conn) -> None:
    """A manual trip choice (toll_trip_overrides) is keyed by trip, and the trip is gone for good once
    no pass with its first pass's key exists any more (not even a soft-deleted one that could be restored)."""
    conn.execute(
        "DELETE FROM toll_trip_overrides WHERE NOT EXISTS ("
        "SELECT 1 FROM toll_transactions t WHERE t.user_id = toll_trip_overrides.user_id "
        "AND t.dedupe_key = toll_trip_overrides.first_dedupe_key)"
    )


def hard_delete_toll_statement(conn, statement_id: int) -> None:
    """A soft-deleted toll statement, for good: its file, its passes (ON DELETE CASCADE) and any manual
    trip choice that no longer points at anything. Trips are a rebuilt cache and already left it out."""
    row = conn.execute("SELECT pdf_path FROM toll_statements WHERE id = ?", (statement_id,)).fetchone()
    if row is not None:
        _remove_file(row["pdf_path"])
    conn.execute("DELETE FROM toll_statements WHERE id = ?", (statement_id,))
    drop_orphan_toll_overrides(conn)


def account_has_live_rows(conn, account_id: int) -> bool:
    """The account delete cascades to its statements and transactions, so it
    is refused while anything live points at it (the sweep below applies the
    same rule)."""
    return conn.execute(
        "SELECT 1 WHERE EXISTS (SELECT 1 FROM transactions WHERE account_id = ? AND deleted_at IS NULL) "
        "OR EXISTS (SELECT 1 FROM statements WHERE account_id = ? AND deleted_at IS NULL)",
        (account_id, account_id),
    ).fetchone() is not None


def hard_delete_account(conn, account_id: int) -> None:
    for row in conn.execute("SELECT pdf_path FROM statements WHERE account_id = ?", (account_id,)).fetchall():
        _remove_file(row["pdf_path"])
    conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))


def purge_soft_deleted(conn) -> dict[str, int]:
    """Permanently removes every soft-deleted row (transactions, statements,
    CSV imports, toll statements, utility bills, accounts) whose deleted_at
    is older than PURGE_GRACE_DAYS.
    Commits internally (this is always the whole unit of work for one
    sweep). Returns how many rows were purged per table, for logging/
    testing — {} counts mean a quiet, uneventful sweep, which is the
    normal case."""
    cutoff_sql = "deleted_at IS NOT NULL AND deleted_at <= datetime('now', ?)"

    txn_count = conn.execute(
        f"SELECT COUNT(*) AS n FROM transactions WHERE {cutoff_sql}", (_CUTOFF_MODIFIER,)
    ).fetchone()["n"]
    conn.execute(f"DELETE FROM transactions WHERE {cutoff_sql}", (_CUTOFF_MODIFIER,))

    stale_statements = conn.execute(
        f"SELECT id, pdf_path FROM statements WHERE {cutoff_sql}", (_CUTOFF_MODIFIER,)
    ).fetchall()
    for row in stale_statements:
        _remove_file(row["pdf_path"])
    conn.execute(f"DELETE FROM statements WHERE {cutoff_sql}", (_CUTOFF_MODIFIER,))

    csv_import_count = conn.execute(
        f"SELECT COUNT(*) AS n FROM csv_imports WHERE {cutoff_sql}", (_CUTOFF_MODIFIER,)
    ).fetchone()["n"]
    conn.execute(f"DELETE FROM csv_imports WHERE {cutoff_sql}", (_CUTOFF_MODIFIER,))

    # E-470 toll statements (their passes go with them).
    stale_tolls = conn.execute(
        f"SELECT id, pdf_path FROM toll_statements WHERE {cutoff_sql}", (_CUTOFF_MODIFIER,)
    ).fetchall()
    for row in stale_tolls:
        _remove_file(row["pdf_path"])
    conn.execute(f"DELETE FROM toll_statements WHERE {cutoff_sql}", (_CUTOFF_MODIFIER,))
    conn.execute(f"DELETE FROM toll_transactions WHERE {cutoff_sql}", (_CUTOFF_MODIFIER,))
    if stale_tolls:
        drop_orphan_toll_overrides(conn)

    stale_bills = conn.execute(
        f"SELECT id, pdf_path FROM utility_bills WHERE {cutoff_sql}", (_CUTOFF_MODIFIER,)
    ).fetchall()
    conn.execute(f"DELETE FROM utility_bills WHERE {cutoff_sql}", (_CUTOFF_MODIFIER,))
    for row in stale_bills:
        _remove_utility_pdf_if_unused(conn, row["pdf_path"])

    acct_count = conn.execute(
        f"""
        SELECT COUNT(*) AS n FROM accounts
        WHERE {cutoff_sql}
          AND NOT EXISTS (SELECT 1 FROM transactions t WHERE t.account_id = accounts.id AND t.deleted_at IS NULL)
          AND NOT EXISTS (SELECT 1 FROM statements s WHERE s.account_id = accounts.id AND s.deleted_at IS NULL)
        """,
        (_CUTOFF_MODIFIER,),
    ).fetchone()["n"]
    conn.execute(
        f"""
        DELETE FROM accounts
        WHERE {cutoff_sql}
          AND NOT EXISTS (SELECT 1 FROM transactions t WHERE t.account_id = accounts.id AND t.deleted_at IS NULL)
          AND NOT EXISTS (SELECT 1 FROM statements s WHERE s.account_id = accounts.id AND s.deleted_at IS NULL)
        """,
        (_CUTOFF_MODIFIER,),
    )

    conn.commit()
    return {
        "transactions": txn_count, "statements": len(stale_statements),
        "csv_imports": csv_import_count, "accounts": acct_count, "toll_statements": len(stale_tolls),
        "utility_bills": len(stale_bills),
    }


def _purge_once() -> dict:
    with get_db() as conn:
        return purge_soft_deleted(conn)


async def _purge_loop() -> None:
    while True:
        try:
            counts = await asyncio.to_thread(_purge_once)  # disk/DB work off the event loop
            if any(counts.values()):
                logger.info("Purge sweep removed: %s", counts)
        except Exception:
            logger.exception("Purge sweep failed — will retry on the next interval")
        await asyncio.sleep(PURGE_INTERVAL_SECONDS)


def start_purge_worker() -> None:
    """Called once from main.py's lifespan, on the running event loop."""
    asyncio.ensure_future(_purge_loop())
