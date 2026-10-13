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
import os
import tempfile

from . import db as _db
from .common import backup_core
from .db import GATE, get_db
from .storage import remove_stored_file

BACKUP_PREFIX = "finance-backup-"     # a database download's temporary copy
IMPORT_PREFIX = "finance-import-"     # an uploaded backup, before it is swapped in

logger = logging.getLogger(__name__)

PURGE_GRACE_DAYS = 30
PURGE_INTERVAL_SECONDS = 24 * 60 * 60
_CUTOFF_MODIFIER = f"-{PURGE_GRACE_DAYS} days"


_remove_file = remove_stored_file  # best effort; never touches a file outside PENDING_DIR


def remove_files(paths) -> None:
    """Remove the files of rows already deleted for good. Called AFTER the commit: a file removed
    before a transaction that then fails would leave a live row whose file is gone."""
    for path in paths or ():
        _remove_file(path)


# --- Immediate, user-requested permanent deletion (Recently deleted page) ---
# Each function hard-deletes ONE row the person already soft-deleted, scoped
# by the caller to that user. The schema does the rest: transactions.statement_id
# / csv_import_id / transfer_pair_id are ON DELETE SET NULL, and utility_bills
# cascades to its electric_meter_details. Callers commit, then remove the
# returned files (remove_files).

def hard_delete_statement(conn, statement_id: int) -> list[str]:
    row = conn.execute("SELECT pdf_path FROM statements WHERE id = ?", (statement_id,)).fetchone()
    # Only its own soft-deleted rows: a transaction someone restored on its
    # own is live data and just loses the link (SET NULL).
    conn.execute("DELETE FROM transactions WHERE statement_id = ? AND deleted_at IS NOT NULL", (statement_id,))
    conn.execute("DELETE FROM statements WHERE id = ?", (statement_id,))
    return [row["pdf_path"]] if row is not None and row["pdf_path"] else []


def hard_delete_csv_import(conn, csv_import_id: int) -> list[str]:
    conn.execute("DELETE FROM transactions WHERE csv_import_id = ? AND deleted_at IS NOT NULL", (csv_import_id,))
    conn.execute("DELETE FROM csv_imports WHERE id = ?", (csv_import_id,))
    return []


def hard_delete_transaction(conn, transaction_id: int) -> list[str]:
    conn.execute("DELETE FROM transactions WHERE id = ?", (transaction_id,))
    return []


def _utility_pdf_if_unused(conn, pdf_path: str | None) -> list[str]:
    """A dual-fuel statement's electric and gas bills share one PDF: keep it
    while any other bill row still points at it."""
    if pdf_path and conn.execute("SELECT 1 FROM utility_bills WHERE pdf_path = ?", (pdf_path,)).fetchone() is None:
        return [pdf_path]
    return []


def hard_delete_utility_bill(conn, bill_id: int) -> list[str]:
    row = conn.execute("SELECT pdf_path FROM utility_bills WHERE id = ?", (bill_id,)).fetchone()
    conn.execute("DELETE FROM utility_bills WHERE id = ?", (bill_id,))
    return _utility_pdf_if_unused(conn, row["pdf_path"]) if row is not None else []


def drop_orphan_toll_overrides(conn) -> None:
    """A manual trip choice (toll_trip_overrides) is keyed by trip, and the trip is gone for good once
    no pass with its first pass's key exists any more (not even a soft-deleted one that could be restored)."""
    conn.execute(
        "DELETE FROM toll_trip_overrides WHERE NOT EXISTS ("
        "SELECT 1 FROM toll_transactions t WHERE t.user_id = toll_trip_overrides.user_id "
        "AND t.dedupe_key = toll_trip_overrides.first_dedupe_key)"
    )


def hard_delete_toll_statement(conn, statement_id: int) -> list[str]:
    """A soft-deleted toll statement, for good: its file, its passes (ON DELETE CASCADE) and any manual
    trip choice that no longer points at anything. Trips are a rebuilt cache and already left it out."""
    row = conn.execute("SELECT pdf_path FROM toll_statements WHERE id = ?", (statement_id,)).fetchone()
    conn.execute("DELETE FROM toll_statements WHERE id = ?", (statement_id,))
    drop_orphan_toll_overrides(conn)
    return [row["pdf_path"]] if row is not None and row["pdf_path"] else []


def account_has_live_rows(conn, account_id: int) -> bool:
    """The account delete cascades to its statements and transactions, so it
    is refused while anything live points at it (the sweep below applies the
    same rule)."""
    return conn.execute(
        "SELECT 1 WHERE EXISTS (SELECT 1 FROM transactions WHERE account_id = ? AND deleted_at IS NULL) "
        "OR EXISTS (SELECT 1 FROM statements WHERE account_id = ? AND deleted_at IS NULL)",
        (account_id, account_id),
    ).fetchone() is not None


def hard_delete_account(conn, account_id: int) -> list[str]:
    files = [r["pdf_path"] for r in conn.execute(
        "SELECT pdf_path FROM statements WHERE account_id = ? AND pdf_path IS NOT NULL", (account_id,)).fetchall()]
    conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
    return files


def purge_soft_deleted(conn) -> dict[str, int]:
    """Permanently removes every soft-deleted row (transactions, statements,
    CSV imports, toll statements, utility bills, accounts) whose deleted_at
    is older than PURGE_GRACE_DAYS.
    Commits internally (this is always the whole unit of work for one
    sweep), and removes the purged rows' files only after that commit.
    Returns how many rows were purged per table, for logging/testing —
    {} counts mean a quiet, uneventful sweep, which is the normal case.

    A statement or CSV import purged here takes ALL its soft-deleted
    transactions with it, whatever their own stamp (one deleted later than
    its statement would otherwise lose its link and linger as a stray
    deleted row); one restored on its own is live and only loses the link."""
    cutoff_sql = "deleted_at IS NOT NULL AND deleted_at <= datetime('now', ?)"
    files: list[str] = []

    stale_statements = conn.execute(
        f"SELECT id, pdf_path FROM statements WHERE {cutoff_sql}", (_CUTOFF_MODIFIER,)
    ).fetchall()
    stale_imports = [r["id"] for r in conn.execute(
        f"SELECT id FROM csv_imports WHERE {cutoff_sql}", (_CUTOFF_MODIFIER,)).fetchall()]

    txn_count = conn.execute(
        f"DELETE FROM transactions WHERE {cutoff_sql}", (_CUTOFF_MODIFIER,)
    ).rowcount
    for column, ids in (("statement_id", [r["id"] for r in stale_statements]), ("csv_import_id", stale_imports)):
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            txn_count += conn.execute(
                f"DELETE FROM transactions WHERE deleted_at IS NOT NULL AND {column} IN ({','.join('?' * len(chunk))})",
                chunk,
            ).rowcount

    files += [r["pdf_path"] for r in stale_statements if r["pdf_path"]]
    conn.execute(f"DELETE FROM statements WHERE {cutoff_sql}", (_CUTOFF_MODIFIER,))

    csv_import_count = conn.execute(f"DELETE FROM csv_imports WHERE {cutoff_sql}", (_CUTOFF_MODIFIER,)).rowcount

    # toll statements (their passes go with them).
    stale_tolls = conn.execute(
        f"SELECT id, pdf_path FROM toll_statements WHERE {cutoff_sql}", (_CUTOFF_MODIFIER,)
    ).fetchall()
    files += [r["pdf_path"] for r in stale_tolls if r["pdf_path"]]
    conn.execute(f"DELETE FROM toll_statements WHERE {cutoff_sql}", (_CUTOFF_MODIFIER,))
    passes = conn.execute(f"DELETE FROM toll_transactions WHERE {cutoff_sql}", (_CUTOFF_MODIFIER,)).rowcount
    if stale_tolls or passes:
        # a manual trip choice can point at passes purged on their own clock too
        drop_orphan_toll_overrides(conn)

    stale_bills = conn.execute(
        f"SELECT id, pdf_path FROM utility_bills WHERE {cutoff_sql}", (_CUTOFF_MODIFIER,)
    ).fetchall()
    conn.execute(f"DELETE FROM utility_bills WHERE {cutoff_sql}", (_CUTOFF_MODIFIER,))
    for path in {r["pdf_path"] for r in stale_bills if r["pdf_path"]}:
        files += _utility_pdf_if_unused(conn, path)

    stale_accounts = f"""
        {cutoff_sql}
          AND NOT EXISTS (SELECT 1 FROM transactions t WHERE t.account_id = accounts.id AND t.deleted_at IS NULL)
          AND NOT EXISTS (SELECT 1 FROM statements s WHERE s.account_id = accounts.id AND s.deleted_at IS NULL)
        """
    # The account's statements go with it (ON DELETE CASCADE) — including ones deleted later than the
    # account, which the sweep above hasn't reached yet — so their files go too.
    files += [r["pdf_path"] for r in conn.execute(
        f"SELECT st.pdf_path FROM statements st WHERE st.pdf_path IS NOT NULL "
        f"AND st.account_id IN (SELECT id FROM accounts WHERE {stale_accounts})",
        (_CUTOFF_MODIFIER,),
    ).fetchall()]
    acct_count = conn.execute(f"DELETE FROM accounts WHERE {stale_accounts}", (_CUTOFF_MODIFIER,)).rowcount

    conn.commit()
    remove_files(files)
    return {
        "transactions": txn_count, "statements": len(stale_statements),
        "csv_imports": csv_import_count, "accounts": acct_count, "toll_statements": len(stale_tolls),
        "utility_bills": len(stale_bills),
    }


# Temporary files a backup download or restore makes (finance-backup-*.db in the temp folder,
# finance-import-*.db next to the database) are normally removed at once; one left by a crash is swept.
STALE_TEMP_SECONDS = 6 * 60 * 60


def _purge_once() -> dict:
    with GATE.work(), get_db() as conn:
        counts = purge_soft_deleted(conn)
    _sweep_temp_files()
    return counts


def _sweep_temp_files() -> None:
    for folder, prefix in ((tempfile.gettempdir(), BACKUP_PREFIX), (os.path.dirname(_db.DB_PATH) or ".", IMPORT_PREFIX)):
        try:
            backup_core.remove_stale(folder, prefix, STALE_TEMP_SECONDS)
        except OSError:
            logger.warning("Couldn't sweep old temporary files in %s", folder)


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
