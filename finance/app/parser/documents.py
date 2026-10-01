"""Row helpers shared by the three document pipelines: bank statements
(`statements`), utility bills (`utility_bills`) and E-470 toll statements
(`toll_statements`). All three tables have the same status / current_step /
error_message / file_hash columns."""
import sqlite3

from .. import storage

TABLES = ("statements", "utility_bills", "toll_statements")


def _table(table: str) -> str:
    if table not in TABLES:
        raise ValueError(table)
    return table


def find_duplicate(table: str, conn: sqlite3.Connection, user_id: str, hash_: str) -> sqlite3.Row | None:
    """The same file already uploaded by this user (deleted rows don't count).
    The caller decides what to do — block it, or offer Restart for a failed one."""
    return conn.execute(
        f"SELECT id, status FROM {_table(table)} WHERE user_id = ? AND file_hash = ? AND deleted_at IS NULL",
        (user_id, hash_),
    ).fetchone()


def set_step(table: str, conn: sqlite3.Connection, row_id: int, step: str) -> None:
    """Committed at once so the Uploads page's status polling sees it."""
    conn.execute(f"UPDATE {_table(table)} SET current_step = ? WHERE id = ?", (step, row_id))
    conn.commit()


def fail(table: str, conn: sqlite3.Connection, row_id: int, message: str) -> None:
    conn.execute(f"UPDATE {_table(table)} SET status = 'error', error_message = ? WHERE id = ?", (message, row_id))
    conn.commit()


def prepare_restart(table: str, conn: sqlite3.Connection, row_id: int, user_id: str) -> tuple[sqlite3.Row | None, str | None]:
    """Shared by the three Restart routes: re-run a FAILED upload from the PDF
    already on disk. Returns (row, None) after resetting it to 'processing' —
    the caller then queues the job — or (row or None, reason it can't)."""
    row = conn.execute(
        f"SELECT * FROM {_table(table)} WHERE id = ? AND user_id = ? AND deleted_at IS NULL", (row_id, user_id),
    ).fetchone()
    if row is None:
        return None, "Not found"
    if row["status"] != "error":
        return row, "Only a failed upload can be restarted"
    if not storage.stored_path(row["pdf_path"]):
        return row, "Source PDF no longer exists — re-upload instead"
    conn.execute(
        f"UPDATE {table} SET status = 'processing', error_message = NULL, current_step = NULL WHERE id = ?", (row_id,),
    )
    conn.commit()
    return row, None
