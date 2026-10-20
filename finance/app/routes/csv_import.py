"""CSV import (SPEC.md section 10): one csv_imports row per file; flexible column matching (Date / Description / Amount, or Debit + Credit), an optional Status column that keeps only posted/cleared rows, per-row duplicate skipping, and categorization of rows with no Category."""
import asyncio
import logging
import csv
import io
import json
from pathlib import Path
from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates

from ..auth import User, get_current_user, get_acting_user
from ..categorize import apply_categories, flow_of, plan_categories
from ..matching import relink
from ..db import get_db
from ..version import APP_VERSION
from ..datenorm import normalize_date
from ..amounts import parse_number
from .. import storage
from .. import ai_usage, settings, upload_page
from .transactions import _insert_manual_transaction
from .accounts import _acting_banner, _acting_qs

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
templates.env.globals["app_version"] = APP_VERSION


# Status-column values that mean "settled" (compared case-insensitively).
_SETTLED_STATUSES = {"posted", "cleared"}


def _debit_credit_amount(debit_raw: str, credit_raw: str, account_type: str) -> tuple[float | None, str | None]:
    """Turns a Debit/Credit column pair into this app's single signed
    `amount` — the same convention `matching.py` and `dashboard.py` already
    rely on everywhere else: on checking/savings a debit is money leaving
    (stored negative) and a credit is money arriving (stored positive);
    credit_card is the opposite — a debit is a purchase (stored positive,
    it increases what's owed) and a credit is a payment or refund (stored
    negative, it reduces what's owed). Whichever cell actually has a
    nonzero value supplies both the sign (via which column it's in — a +/-
    already in the cell itself is ignored, since these exports write plain
    magnitudes) and the size; exactly one side filled in is the expected
    shape. Returns (amount, error) — error is a short row-level reason
    when both or neither side is usable, mirroring the plain-Amount
    column's own "left for _insert_manual_transaction to report" pattern
    for anything else wrong with the value."""
    def _magnitude(raw: str) -> float | None:
        raw = (raw or "").strip()
        if not raw:
            return None
        value = parse_number(raw)
        return abs(value) if value else None  # a literal 0.00 counts as "not filled in"

    debit = _magnitude(debit_raw)
    credit = _magnitude(credit_raw)
    if debit is not None and credit is not None:
        return None, "both Debit and Credit have an amount — exactly one should"
    if debit is None and credit is None:
        return None, "neither Debit nor Credit has an amount"
    if account_type == "credit_card":
        return (debit if debit is not None else -credit), None
    return (-debit if debit is not None else credit), None


# The row errors / duplicates / skipped statuses kept on the import for its debug page: the first
# MAX_KEPT_LINES of each (a 100,000-row file of errors would otherwise be one huge column).
MAX_KEPT_LINES = 1000


def _kept_json(lines: list[str]) -> str | None:
    if not lines:
        return None
    kept = lines[:MAX_KEPT_LINES]
    if len(lines) > MAX_KEPT_LINES:
        kept.append(f"... and {len(lines) - MAX_KEPT_LINES} more")
    return json.dumps(kept)


# How far down an export's own heading lines (account holder, card, "Time period of report") are looked through
# for the real header row when the first row isn't it, and how many rows the row picker shows.
HEADER_SEARCH_ROWS = 30
PREVIEW_ROWS = 15
PREVIEW_CELL = 60          # characters of a cell shown in the picker


def _map_fields(fieldnames: list[str], date_column: str | None):
    """The column matching for one header row: (field_map, fatal_error, needs_date_column, date candidates).
    `field_map` is lower-cased header -> the header as written, plus "date" when a date column was settled."""
    field_map = {(name or "").strip().lower(): name for name in fieldnames if (name or "").strip()}
    fatal_error = None
    needs_date_column = False
    candidates: list[str] = []
    if date_column:
        match = next((n for n in fieldnames if n == date_column), None)
        if match:
            field_map["date"] = match
        else:
            fatal_error = f"Column “{date_column}” was not found in this file."
    elif "date" not in field_map:
        date_like = [name for lower, name in field_map.items() if "date" in lower]
        if len(date_like) == 1:
            field_map["date"] = date_like[0]
        elif len(date_like) > 1:
            trans_matches = [n for n in date_like if "trans" in n.lower()]
            if len(trans_matches) == 1:
                field_map["date"] = trans_matches[0]
            else:
                needs_date_column = True
                candidates = date_like
    return field_map, fatal_error, needs_date_column, candidates


def _missing_columns(field_map: dict, needs_date_column: bool, default_account_id: str) -> list[str]:
    """The required columns a header row lacks (an undecided date column counts as present: it's there)."""
    required = (["description"] + ([] if needs_date_column else ["date"])
                + ([] if default_account_id else ["account"]))
    if not ("debit" in field_map and "credit" in field_map):
        required.append("amount")
    return [r for r in required if r not in field_map]


def _find_header_row(records: list[list[str]], date_column: str | None, default_account_id: str) -> int | None:
    """The 0-based index of the first of the top rows that has every required column, or None. Many bank
    exports put a few lines about the account and the period above the real header ("Name: …", "Card: …",
    "Time period of report: …"); those are skipped this way without the person having to say so."""
    for idx, cells in enumerate(records[:HEADER_SEARCH_ROWS]):
        if not any((c or "").strip() for c in cells):
            continue
        fm, err, needs, _ = _map_fields(cells, date_column)
        if err is None and not _missing_columns(fm, needs, default_account_id):
            return idx
    return None


def _preview(records: list[list[str]]) -> list[dict]:
    """The first rows, numbered as the person sees them (1 = the first row), for the row picker."""
    return [{"row": i + 1, "cells": [(c or "")[:PREVIEW_CELL] for c in cells]}
            for i, cells in enumerate(records[:PREVIEW_ROWS])]


def _row_number(value) -> int | None:
    """A row number typed by the person: a whole number from 1, or None for "work it out"."""
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return n if n >= 1 else None


def _import_csv_file(
    conn, user_id: str, filename: str | None, raw: bytes,
    accounts_by_name: dict[str, int], default_account_id: str,
    date_column: str | None = None,
    account_types: dict[int, str] | None = None,
    rejected: str | None = None,
    header_row: int | None = None,
    data_row: int | None = None,
) -> dict:
    """Imports one CSV file and records it as exactly one `csv_imports`
    row, whether it succeeded, partly failed or was rejected outright.
    Returns the outcome: filename, csv_import_id, imported, fatal_error,
    row_errors, duplicates, needs_date_column, date_column_candidates.

    A missing required column, an unreadable file or a bad default account
    rejects the whole file (nothing imported); any other per-row problem
    fails only that row and the rest still import. Rows are checked for
    duplicates (same account, date, amount and description as any live
    transaction, including earlier rows of this same file) and skipped.
    Rows with a blank Category are categorized afterwards in one batch
    through the same cascade a parsed statement gets; an explicit Category
    is used as given.

    A bank export often has more than one date-ish column (a credit card's
    "Trans. Date" and "Post Date," say) — an exact "Date" header still wins
    outright; short of that, a single date-like column (anything containing
    "date") is unambiguous and used as-is. More than one is resolved to
    whichever contains "trans" — the actual transaction date, not when the
    bank posted it — if exactly one of them does; otherwise this can't be
    guessed safely, and the file is neither imported nor rejected: the
    result comes back with needs_date_column=True and the candidate column
    names, and the caller is expected to ask the person which to use and
    call this again with that choice as `date_column` (used verbatim as the
    date field, bypassing all of the above). No `csv_imports` row is
    written for a needs_date_column outcome — nothing was actually
    attempted yet, so there's nothing to record until the real import, on
    the follow-up call, either succeeds or fails on its own terms.

    A single "Amount" column is still the default expectation, but a file
    with separate Debit and Credit columns instead (a credit card export's
    "Debit"/"Credit" pair, say) is accepted too when BOTH are present —
    see `_debit_credit_amount` for the sign convention, which
    needs each row's account type, so `account_types` (account id -> type)
    must be given whenever the caller wants that path available. A file
    with only one of the two (no "Amount" either) still gets the plain
    "missing column(s): amount" error, the same as before this existed —
    a lone Debit or Credit column alone doesn't unambiguously mean this
    shape.

    The header is the first row unless `header_row` (1 = the first row of
    the file) says otherwise; the data starts on the row after it unless
    `data_row` says otherwise, and anything between the two is ignored.
    With no `header_row` and a first row that lacks the required columns,
    the top HEADER_SEARCH_ROWS rows are searched for one that has them (a
    bank's own lines about the account and period above the real header).
    When none is found the result also carries `preview` — the first rows,
    numbered — so the page can ask which row is the header and where the
    data starts, and call this again with both."""
    fatal_error: str | None = None
    reader = None
    layout = None              # the header / data rows and date column actually used (for "Remember this layout")
    preview: list[dict] = []
    needs_rows = False         # the header wasn't found: the page shows the first rows to choose from
    field_map: dict[str, str] = {}
    default_account_id = (default_account_id or "").strip()
    needs_date_column = False
    date_column_candidates: list[str] = []
    use_debit_credit = False

    try:
        if rejected:  # refused before reading (too large) — still recorded like any failed file
            raise storage.UploadRejected(rejected)
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        fatal_error = "Could not read that file as UTF-8 text."
    except storage.UploadRejected as e:
        fatal_error = str(e)
    else:
        records = list(csv.reader(io.StringIO(text)))
        preview = _preview(records)
        if not records or not any(any((c or "").strip() for c in r) for r in records):
            fatal_error = "That CSV file is empty or has no header row."
        else:
            if header_row is not None:
                if header_row > len(records):
                    fatal_error = f"The file has only {len(records)} rows, so row {header_row} can't be the header."
                header_idx = header_row - 1
            else:
                header_idx = 0
                fm0, err0, needs0, _ = _map_fields(records[0], date_column)
                # Not the header: required columns missing, or (a date column picked after the header was found
                # further down) that column isn't on the first row.
                if err0 is not None or _missing_columns(fm0, needs0, default_account_id):
                    found = _find_header_row(records, date_column, default_account_id)
                    if found is not None:
                        header_idx = found
            if fatal_error is None:
                first_data = (data_row - 1) if data_row is not None else header_idx + 1
                if first_data <= header_idx:
                    fatal_error = (f"The data has to start below the header (row {header_idx + 1}), "
                                   f"not on row {first_data + 1}.")
            if fatal_error is None:
                fieldnames = records[header_idx]
                field_map, fatal_error, needs_date_column, date_column_candidates = _map_fields(
                    fieldnames, date_column)
                use_debit_credit = "debit" in field_map and "credit" in field_map
                several_dates = sum(1 for n in fieldnames if "date" in (n or "").lower()) > 1
                layout = {"header_row": header_idx + 1, "data_row": first_data + 1,
                          "date_column": field_map.get("date") if several_dates else None,
                          "debit_credit": use_debit_credit}
                if fatal_error is None and not needs_date_column:
                    # Account is only a required column when no default account was picked. A single Amount
                    # column is required UNLESS both Debit and Credit are present -- a lone Debit or Credit
                    # alone doesn't unambiguously replace it.
                    missing = _missing_columns(field_map, False, default_account_id)
                    if missing:
                        fatal_error = (f"CSV is missing required column(s): {', '.join(missing)}"
                                       + (f" (looked for them in row {header_idx + 1})." if header_row
                                          else "."))
                        needs_rows = True

                def _rows():
                    """(row number as in the file, the row as {header: cell}) for every data row."""
                    width = len(fieldnames)
                    for idx in range(first_data, len(records)):
                        cells = records[idx]
                        if not any((c or "").strip() for c in cells):
                            continue           # blank lines (csv.DictReader skipped them too)
                        cells = list(cells) + [""] * (width - len(cells))
                        yield idx + 1, dict(zip(fieldnames, cells))
                reader = _rows()

    resolved_default: int | None = None
    if fatal_error is None and not needs_date_column and default_account_id:
        if default_account_id.isdigit() and int(default_account_id) in accounts_by_name.values():
            resolved_default = int(default_account_id)
        else:
            fatal_error = "Selected default account wasn't found."

    inserted_ids: list[int] = []
    inserted_set: set[int] = set()       # the same ids, for the "repeated in this file" check below
    to_categorize: list[tuple[int, str, str | None]] = []      # (id, description, which way the money goes)
    row_errors: list[str] = []
    duplicates: list[str] = []
    skipped_rows: list[tuple] = []   # (account_id, date, amount, description, category, reason, matched id)
    skipped_status: list[str] = []

    for i, row in reader if (fatal_error is None and not needs_date_column) else []:     # i: the row in the file
        if "status" in field_map:
            # Only settled rows (posted / cleared) are imported: a pending one can
            # still change or vanish. Checked first, so a skipped row never needs
            # a valid account.
            raw_status = (row.get(field_map["status"]) or "").strip()
            if raw_status.lower() not in _SETTLED_STATUSES:
                skipped_status.append(
                    f"Row {i}: status “{raw_status or '(blank)'}” — not posted/cleared, skipped"
                )
                continue
        account_name = (row.get(field_map["account"]) or "").strip() if "account" in field_map else ""
        if account_name:
            account_id = accounts_by_name.get(account_name.lower())
            if account_id is None:
                row_errors.append(f"Row {i}: unrecognized account \u201c{account_name}\u201d")
                continue
        elif resolved_default is not None:
            account_id = resolved_default
        else:
            row_errors.append(f"Row {i}: no account given, and no default account selected")
            continue

        date = row.get(field_map["date"]) or ""
        if use_debit_credit:
            account_type = (account_types or {}).get(account_id, "checking")
            debit_raw = row.get(field_map["debit"]) or ""
            credit_raw = row.get(field_map["credit"]) or ""
            amount_value, dc_error = _debit_credit_amount(debit_raw, credit_raw, account_type)
            if dc_error:
                row_errors.append(f"Row {i}: {dc_error}")
                continue
            amount = str(amount_value)
        else:
            amount = row.get(field_map["amount"]) or ""
        description = row.get(field_map["description"]) or ""
        raw_category = (row.get(field_map["category"], "") if "category" in field_map else "").strip()

        # Compare against what would actually be stored (trimmed, amount to
        # 2dp); a row whose amount doesn't parse is left for
        # _insert_manual_transaction's own validation to report.
        date_norm = normalize_date(date.strip()) or date.strip()
        description_norm = description.strip()
        amount_norm = parse_number(amount)
        amount_norm = round(amount_norm, 2) if amount_norm is not None else None
        if date_norm and description_norm and amount_norm is not None:
            dup = conn.execute(
                "SELECT id FROM transactions WHERE user_id = ? AND account_id = ? "
                "AND date = ? AND amount = ? AND description = ? AND deleted_at IS NULL LIMIT 1",
                (user_id, account_id, date_norm, amount_norm, description_norm),
            ).fetchone()
            if dup is not None:
                repeated = dup[0] in inserted_set
                duplicates.append(
                    f"Row {i}: same date, amount and description as an earlier row of this file \u2014 skipped"
                    if repeated else
                    f"Row {i}: already exists (same date, amount, description, account) \u2014 skipped")
                skipped_rows.append((account_id, date_norm, amount_norm, description_norm, raw_category or None,
                                     "repeated" if repeated else "on_file", None if repeated else dup[0]))
                continue

        ok, result = _insert_manual_transaction(
            conn, user_id, account_id, date, amount, description, raw_category,
            category_source="csv_explicit" if raw_category else None,
        )
        if ok:
            inserted_ids.append(result)
            inserted_set.add(result)
            if not raw_category:
                signed = parse_number(amount)
                to_categorize.append((result, description,
                                      flow_of(account_types.get(account_id), signed) if account_types else None))
        else:
            row_errors.append(f"Row {i}: {result}")

    csv_import_id = None
    if not needs_date_column:
        csv_import_id = conn.execute(
            "INSERT INTO csv_imports "
            "(user_id, original_filename, row_count, fatal_error, error_count, duplicate_count, row_errors, duplicates, "
            "skipped_status_count, skipped_status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                user_id, filename, len(inserted_ids), fatal_error, len(row_errors), len(duplicates),
                _kept_json(row_errors), _kept_json(duplicates),
                len(skipped_status),
                _kept_json(skipped_status),
            ),
        ).lastrowid
        if inserted_ids:
            conn.executemany(
                "UPDATE transactions SET csv_import_id = ? WHERE id = ?",
                [(csv_import_id, tid) for tid in inserted_ids],
            )
        if skipped_rows:   # kept for the Review page's "Insert anyway" (skipped_duplicates)
            conn.executemany(
                "INSERT INTO skipped_duplicates (user_id, account_id, csv_import_id, date, amount, description, "
                "category, reason, matched_transaction_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(user_id, a, csv_import_id, d, amt, desc, cat, reason, m)
                 for a, d, amt, desc, cat, reason, m in skipped_rows],
            )
        relink(conn, user_id)      # an imported row can be one side of a transfer
        # The rows are saved (committed) before the categorization's AI call,
        # which can take minutes: no write may wait on the model meanwhile.
        # They show as uncategorized until the categories are written, in one
        # short transaction after the call.
        conn.commit()
        if to_categorize:
            plan = None
            with ai_usage.recording() as calls:
                try:
                    plan = plan_categories(conn, user_id, settings.ai_url(), settings.ai_model(), to_categorize)
                except Exception:      # the rows are saved already: they just stay Uncategorized
                    logger.exception("Categorizing CSV import %s failed", csv_import_id)
            conn.commit()
            if plan is not None:
                apply_categories(conn, plan, csv_import_id=csv_import_id)
            if calls:
                conn.execute("UPDATE csv_imports SET ai_usage = ? WHERE id = ?", (ai_usage.to_json(calls), csv_import_id))
            conn.commit()

    return {
        "filename": filename, "csv_import_id": csv_import_id, "imported": len(inserted_ids),
        "fatal_error": fatal_error, "row_errors": row_errors, "duplicates": duplicates,
        "skipped_status": skipped_status,
        "needs_date_column": needs_date_column, "date_column_candidates": date_column_candidates,
        "needs_rows": needs_rows, "preview": preview if needs_rows else [],
        "layout": layout if (fatal_error is None and not needs_date_column) else None,
    }


def _read_and_import(user_id: str, csv_dir: str, upload, default_account_id: str, date_column: str | None,
                     header_row: int | None = None, data_row: int | None = None) -> dict:
    try:
        raw, rejected = storage.read_upload_sync(upload, storage.MAX_CSV_BYTES), None
    except storage.UploadRejected as e:
        raw, rejected = b"", str(e)
    return _import_one(user_id, csv_dir, upload.filename[:255] if upload.filename else None, raw, default_account_id, date_column,
                       rejected, header_row, data_row)


def _import_one(user_id: str, csv_dir: str, filename: str | None, raw: bytes, default_account_id: str,
                date_column: str | None, rejected: str | None, header_row: int | None = None,
                data_row: int | None = None) -> dict:
    """One file, on its own connection (called from a worker thread). The file
    is kept in PENDING_DIR/<user>/csv-imports only while it is imported."""
    with get_db() as conn:
        accounts = conn.execute(
            "SELECT id, name, type FROM accounts WHERE user_id = ? AND deleted_at IS NULL AND is_archived = 0",
            (user_id,),
        ).fetchall()
        accounts_by_name = {r["name"].strip().lower(): r["id"] for r in accounts}
        account_types = {r["id"]: r["type"] for r in accounts}
        if rejected:
            return _import_csv_file(conn, user_id, filename, b"", accounts_by_name, default_account_id,
                                    rejected=rejected)
        saved_path, _hash = storage.save_file(csv_dir, "import", ".csv", raw)
        try:
            return _import_csv_file(conn, user_id, filename, raw, accounts_by_name, default_account_id,
                                    date_column=date_column, account_types=account_types,
                                    header_row=header_row, data_row=data_row)
        finally:
            storage.remove_stored_file(saved_path)  # a leftover shows on the admin Storage page


def _saved_layout(user_id: str, default_account_id: str) -> dict | None:
    """The default account's saved CSV layout (accounts.csv_layout), or None."""
    picked = (default_account_id or "").strip()
    if not picked.isdigit():
        return None
    with get_db() as conn:
        row = conn.execute(
            "SELECT csv_layout FROM accounts WHERE id = ? AND user_id = ? AND deleted_at IS NULL AND is_archived = 0",
            (int(picked), user_id)).fetchone()
    return upload_page.read_layout(row["csv_layout"]) if row else None


@router.post("/transactions-import-csv")
async def import_transactions_csv(
    request: Request,
    files: list[UploadFile] = File(...),
    default_account_id: str = Form(""),
    date_column: str = Form(""),
    header_row: str = Form(""),
    data_row: str = Form(""),
    saved_layout: str = Form(""),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """CSV import (SPEC.md section 10): one or several files, each
    imported on its own (_import_csv_file). The Uploads page posts with
    `Accept: application/json` and shows the per-file summaries inline; other
    clients get a results page. `date_column` is set when the page resubmits a
    single file after the person picked its date column; `header_row` /
    `data_row` (1 = the file's first row; blank = worked out) when they chose
    where the header and the data are. Each file sits in
    PENDING_DIR/<user>/csv-imports only while it is imported.

    With a default account that has a saved layout (accounts.csv_layout, "Remember this layout" on the Upload
    page) whatever the request leaves blank comes from it: the header and data rows (as a pair: giving either
    one overrides both) and the date column. `saved_layout=0` imports without it. Each result says where its
    layout came from (`layout_source`: saved, given or auto) and, for a file that imported, the `layout` used."""
    wants_json = "application/json" in request.headers.get("accept", "")
    csv_dir = storage.user_dir(acting.id, "csv-imports")

    if len(files) > storage.MAX_FILES_PER_REQUEST:
        return JSONResponse({"error": storage.TOO_MANY_FILES}, status_code=400)
    header_n, data_n, date_col = _row_number(header_row), _row_number(data_row), date_column.strip() or None
    source = "given" if (header_n or data_n or date_col) else "auto"
    saved = None if saved_layout.strip() == "0" else _saved_layout(acting.id, default_account_id)
    if saved:
        if header_n is None and data_n is None:
            header_n, data_n = saved["header_row"], saved["data_row"]
            source = "saved"
        if date_col is None and saved["date_column"]:
            date_col = saved["date_column"]
            source = "saved"
    results = []
    for upload in files:
        # Reading, parsing, inserts and the categorization LLM call are blocking:
        # run them in a thread so the whole app doesn't freeze meanwhile.
        results.append(await asyncio.to_thread(
            _read_and_import, acting.id, csv_dir, upload, default_account_id, date_col, header_n, data_n,
        ))

    if wants_json:
        return JSONResponse({"results": [
            {
                "filename": r["filename"], "csv_import_id": r["csv_import_id"], "imported": r["imported"],
                "error_count": len(r["row_errors"]), "duplicate_count": len(r["duplicates"]),
                "skipped_status_count": len(r["skipped_status"]),
                "fatal_error": r["fatal_error"],
                "needs_date_column": r["needs_date_column"], "date_column_candidates": r["date_column_candidates"],
                "needs_rows": r["needs_rows"], "preview": r["preview"],
                "layout": r["layout"], "layout_source": source,
            }
            for r in results
        ]})

    return templates.TemplateResponse(
        request, "import_results.html",
        {
            "user": acting, "results": results,
            "acting_as_banner": _acting_banner(current, acting),
            "acting_qs": _acting_qs(current, acting),
            "active_page": "uploads",
        },
    )
