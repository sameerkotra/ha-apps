"""Processes one utility-bill upload (SPEC.md section 15), reusing the
statement pipeline's text pass (extract_deterministic, _consume_amount).

- Before the slow vision call, _validate_looks_like_bill rejects a PDF with no
  amounts at all, or that never names the provider picked on the form.
- The vision pass (utility_vision.py) is the only source of a bill's fields.
  One PDF gives one bill (Aurora Water) or two (Xcel electric + gas): the
  upload's placeholder row is updated for the first bill and a row inserted for
  any other, in one write, all sharing the file's hash and path.
- Checks: each bill's cost must be printed in the PDF text, and for a dual-fuel
  statement the combined total too; otherwise the bill waits for review.
- The electric meter table is also read from the PDF's own text
  (utility_meter.py): a printed figure replaces the AI's, the rest follow the
  meter arithmetic, and what changed is kept in `corrections`.
- Unless "Skip confirmation" was ticked at upload (`needs_confirmation` 0),
  every bill waits for the person to check it against the PDF and Confirm
  (status pending_review, review_status clean, no error message); the PDF is
  kept until then.
"""
import logging
import sqlite3
from functools import partial
from collections import Counter

from ..db import get_db
from ..storage import forget_pdf
from . import documents
from .deterministic import extract_deterministic, NoTextLayer
from .pipeline import _consume_amount  # generic, not statement-specific — reused as-is
from .utility_meter import meter_from_text, reconcile
from .utility_vision import ElectricMeterDetails, extract_vision_utility, UtilityVisionResult
from .vision import VisionExtractionError

logger = logging.getLogger(__name__)


_METER_COLUMNS = [
    "grid_import_on_peak_kwh", "grid_import_off_peak_kwh", "grid_import_total_kwh",
    "solar_export_on_peak_kwh", "solar_export_off_peak_kwh", "solar_export_total_kwh",
    "net_on_peak_kwh", "net_off_peak_kwh", "net_total_kwh",
    "net_generated_on_peak_kwh", "net_generated_off_peak_kwh", "net_generated_total_kwh",
    "on_peak_rate", "on_peak_charge", "off_peak_rate", "off_peak_charge",
]

# The full two-word company name: Xcel statements print "AURORA, CO" in the
# address, so a bare "aurora" would pass an Xcel bill under "Aurora Water".
PROVIDER_KEYWORDS = {
    "Xcel Energy": "xcel energy",
    "Aurora Water": "aurora water",
}


def _validate_looks_like_bill(raw_text: str, amount_counts, provider: str) -> str | None:
    """A rejection message if this PDF can't be a `provider` bill (no amounts at
    all, or the provider is never named), else None. A presence test only —
    layouts vary too much to check sections."""
    if not amount_counts:
        return (
            "This PDF doesn't contain any recognizable dollar amounts — "
            "it doesn't look like a bill at all. Check you uploaded the "
            "correct file."
        )

    keyword = PROVIDER_KEYWORDS.get(provider)
    if keyword and keyword not in raw_text.lower():
        return (
            f"This PDF never mentions \"{provider}\" anywhere in its text — "
            "check you selected the right provider, or that this is "
            "actually a statement from them."
        )

    return None


check_duplicate_utility = partial(documents.find_duplicate, "utility_bills")
_set_step = partial(documents.set_step, "utility_bills")
_fail = partial(documents.fail, "utility_bills")
# Several rows can share one PDF (a dual-fuel statement's electric + gas bills).
delete_pdf_utility = partial(forget_pdf, "utility_bills")


def _upload_identity(conn: sqlite3.Connection, utility_bill_id: int) -> tuple[str, str | None]:
    row = conn.execute("SELECT user_id, file_hash FROM utility_bills WHERE id = ?", (utility_bill_id,)).fetchone()
    return row["user_id"], row["file_hash"]


def _find_existing_bill(conn: sqlite3.Connection, utility_bill_id: int, provider: str, bill) -> sqlite3.Row | None:
    """A live bill from a DIFFERENT upload with the same provider, type and
    billing period (and the same account number when both are known) -- the
    same bill downloaded twice, or re-sent after an edit, produces a PDF with a
    different file hash that the upload-time hash check can't catch. Rows
    sharing this upload's hash are the same PDF's own sibling rows (a dual-fuel
    statement's second bill), never duplicates of it."""
    if not (bill.utility_type and bill.period_start_date and bill.period_end_date):
        return None
    user_id, file_hash = _upload_identity(conn, utility_bill_id)
    return conn.execute(
        """
        SELECT id, cost FROM utility_bills
        WHERE user_id = ? AND provider = ? AND utility_type = ?
          AND period_start_date = ? AND period_end_date = ?
          AND deleted_at IS NULL AND status != 'error' AND id != ?
          AND (file_hash IS NULL OR file_hash IS NOT ?)
          AND (account_number_last4 IS NULL OR ? IS NULL OR account_number_last4 = ?)
        ORDER BY id LIMIT 1
        """,
        (user_id, provider, bill.utility_type, bill.period_start_date, bill.period_end_date,
         utility_bill_id, file_hash, bill.account_number_last4, bill.account_number_last4),
    ).fetchone()


def _drop_duplicate_bills(conn: sqlite3.Connection, utility_bill_id: int, provider: str,
                          bills: list, flags: list[str | None]) -> tuple[list, list[str | None], list[str]]:
    """(kept bills, their flags, messages for the ones dropped).

    A bill is dropped when the model listed it twice on this PDF, or when an
    identical bill (same period AND cost) is already on file. Same period but
    a different total is kept and flagged for review instead -- possibly a
    corrected statement, and silently discarding a real charge is worse than
    asking."""
    kept, kept_flags, dropped, seen = [], [], [], set()
    for bill, flag in zip(bills, flags):
        identity = (bill.utility_type, bill.period_start_date, bill.period_end_date,
                    round(bill.cost, 2), bill.account_number_last4)
        span = f"the {bill.utility_type} bill for {bill.period_start_date} to {bill.period_end_date}"
        if identity in seen:
            dropped.append(f"{span} appeared twice on this PDF")
            continue
        seen.add(identity)

        existing = _find_existing_bill(conn, utility_bill_id, provider, bill)
        if existing is not None and abs((existing["cost"] or 0) - bill.cost) < 0.005:
            dropped.append(f"{span} (${bill.cost:.2f}) is already on file as bill #{existing['id']}")
            continue
        if existing is not None:
            note = (f"Bill #{existing['id']} already covers this provider, type and billing period "
                    f"with a different total (${existing['cost'] or 0:.2f}) -- a corrected statement? "
                    "Delete whichever one is wrong.")
            flag = f"{flag} {note}" if flag else note
        kept.append(bill)
        kept_flags.append(flag)
    return kept, kept_flags, dropped


def _clear_previous_run(conn: sqlite3.Connection, utility_bill_id: int) -> None:
    """Restarting a bill re-parses its PDF from scratch. Rows a previous run
    inserted for the SAME PDF's extra bills (same file hash, not this row) and
    this row's own meter details would otherwise be written a second time."""
    user_id, file_hash = _upload_identity(conn, utility_bill_id)
    conn.execute("DELETE FROM electric_meter_details WHERE utility_bill_id = ?", (utility_bill_id,))
    if file_hash is None:
        return
    siblings = [r["id"] for r in conn.execute(
        "SELECT id FROM utility_bills WHERE user_id = ? AND file_hash = ? AND id != ? AND deleted_at IS NULL",
        (user_id, file_hash, utility_bill_id),
    )]
    for sibling_id in siblings:
        conn.execute("DELETE FROM electric_meter_details WHERE utility_bill_id = ?", (sibling_id,))
        conn.execute("DELETE FROM utility_bills WHERE id = ?", (sibling_id,))


def process_utility_bill(utility_bill_id: int, user_id: str, ollama_url: str, ollama_model: str) -> None:
    """The background job (run by app/jobs.py's shared worker). Never raises out to the caller in a way that would crash
    the queue silently — every failure path updates this row's own
    status/error_message before returning, same "failure is visible and
    restartable" design section 4 already established for statements."""
    conn = None
    try:
        conn = get_db()
        row = conn.execute(
            "SELECT pdf_path, provider, extraction_notes, needs_confirmation FROM utility_bills WHERE id = ?",
            (utility_bill_id,)
        ).fetchone()
        needs_confirmation = bool(row["needs_confirmation"])
        pdf_path = row["pdf_path"]
        provider = row["provider"]
        known = provider in PROVIDER_KEYWORDS

        _set_step(conn, utility_bill_id, "Extracting text (pdftotext)")
        try:
            det_result = extract_deterministic(pdf_path)
        except NoTextLayer as e:
            _fail(conn, utility_bill_id, f"No text layer (scanned PDF, no OCR support): {e}")
            return
        except Exception as e:
            _fail(conn, utility_bill_id, f"Deterministic extraction failed: {e}")
            return

        # Cheap format check, ahead of the slow vision call.
        format_error = _validate_looks_like_bill(det_result.raw_text, det_result.amount_counts, provider)
        if format_error:
            _fail(conn, utility_bill_id, format_error)
            return

        try:
            vision_result: UtilityVisionResult = extract_vision_utility(
                pdf_path, ollama_url, ollama_model,
                on_step=lambda msg: _set_step(conn, utility_bill_id, msg),
                provider=provider, notes=row["extraction_notes"] or "",
            )
            conn.execute("UPDATE utility_bills SET llm_raw_response = ? WHERE id = ?",
                         (vision_result.raw_response, utility_bill_id))
            conn.commit()
        except VisionExtractionError as e:
            conn.execute("UPDATE utility_bills SET llm_raw_response = ? WHERE id = ?",
                         (e.raw_response, utility_bill_id))
            conn.commit()
            _fail(conn, utility_bill_id, f"LLM extraction failed: {e}")
            return

        if not vision_result.bills:
            _fail(conn, utility_bill_id, "AI extraction found no recognizable bill on this PDF.")
            return

        # The meter table as printed (pdftotext) wins over the AI's reading of it.
        printed = meter_from_text(det_result.raw_text)
        corrections: list[str | None] = []
        for bill in vision_result.bills:
            if bill.utility_type != "electric" or not (printed or bill.electric_meter_details):
                corrections.append(None)
                continue
            if bill.electric_meter_details is None:
                bill.electric_meter_details = ElectricMeterDetails()
            changes = reconcile(bill.electric_meter_details, printed)
            if bill.usage_amount is None and bill.electric_meter_details.grid_import_total_kwh is not None:
                bill.usage_amount = bill.electric_meter_details.grid_import_total_kwh
                bill.usage_unit = bill.usage_unit or "kWh"
            corrections.append("; ".join(changes) or None)
            if changes:
                logger.info("Utility bill %s: meter corrected: %s", utility_bill_id, "; ".join(changes))
        fixes = {id(b): c for b, c in zip(vision_result.bills, corrections)}

        _set_step(conn, utility_bill_id, "Validating amounts against bill text")
        working_counts = Counter(det_result.amount_counts)
        bill_flags: list[str | None] = []
        for bill in vision_result.bills:
            if _consume_amount(working_counts, bill.cost):
                bill_flags.append(None)
            else:
                bill_flags.append(
                    f"This bill's total (${bill.cost:.2f}) wasn't found anywhere in the PDF's "
                    "own extracted text — it may have been misread by the AI extraction."
                )

        # Dual-fuel-specific check: the statement's own combined total
        # (e.g. Xcel's "Current Charges") should also be printed
        # somewhere verbatim. Checked against the ORIGINAL (unconsumed)
        # counts, not the working copy above — this is a genuinely
        # separate figure from either fuel's own total, not a re-use of
        # the same token.
        if len(vision_result.bills) > 1:
            combined = round(sum(b.cost for b in vision_result.bills), 2)
            if det_result.amount_counts.get(combined, 0) < 1:
                note = (
                    f"The combined total (${combined:.2f}, this statement's "
                    f"{len(vision_result.bills)} bills added together) wasn't found printed "
                    "anywhere on the statement — one bill's cost may be misread."
                )
                bill_flags = [note if f is None else f"{f} {note}" for f in bill_flags]

        if not known:
            # A provider the app has no tuned reading for: always checked by a person (section 23).
            note = (f"{provider} isn't one of the providers the app reads by itself, so check each figure "
                    "against the PDF, correct anything wrong, then Confirm (or Re-extract with notes).")
            bill_flags = [note if f is None else f"{f} {note}" for f in bill_flags]

        bills, bill_flags, dropped = _drop_duplicate_bills(
            conn, utility_bill_id, provider, vision_result.bills, bill_flags
        )
        if not bills:
            _fail(conn, utility_bill_id, "Duplicate -- " + "; ".join(dropped) + ".")
            return

        _set_step(conn, utility_bill_id, "Saving bill(s)")
        conn.execute("BEGIN")
        try:
            _clear_previous_run(conn, utility_bill_id)
            saved: list[tuple[int, str | None]] = []  # (id, flag)
            for i, (bill, flag) in enumerate(zip(bills, bill_flags)):
                review_status = "pending_review" if flag else "clean"
                status = "pending_review" if (flag or needs_confirmation) else "complete"
                fix = fixes.get(id(bill))

                if i == 0:
                    conn.execute(
                        "UPDATE utility_bills SET utility_type = ?, provider = ?, period_start_date = ?, "
                        "period_end_date = ?, usage_amount = ?, usage_unit = ?, cost = ?, due_date = ?, "
                        "account_number_last4 = ?, status = ?, raw_text_length = ?, review_status = ?, "
                        "error_message = ?, corrections = ?, processed_at = datetime('now') WHERE id = ?",
                        (
                            bill.utility_type, provider, bill.period_start_date, bill.period_end_date,
                            bill.usage_amount, bill.usage_unit, bill.cost, bill.due_date,
                            bill.account_number_last4, status, det_result.raw_text_length, review_status,
                            flag, fix, utility_bill_id,
                        ),
                    )
                    bill_id = utility_bill_id
                else:
                    # A second (or later) bill from the SAME PDF (Xcel's
                    # gas entry alongside the electric one already
                    # written to the placeholder row above) — a brand
                    # new row, sharing the original upload's file_hash/
                    # pdf_path/original_filename so it's recognized as
                    # part of the same upload (dedup, PDF lifecycle) even
                    # though it never went through its own 'processing'
                    # placeholder state. It also shares the upload's raw
                    # Ollama response (one AI call produced both bills) —
                    # without it the gas row's debug view said "no response
                    # captured".
                    cur = conn.execute(
                        "INSERT INTO utility_bills "
                        "(user_id, utility_type, provider, period_start_date, period_end_date, "
                        "usage_amount, usage_unit, cost, due_date, account_number_last4, "
                        "status, raw_text_length, review_status, error_message, corrections, "
                        "file_hash, pdf_path, original_filename, llm_raw_response, needs_confirmation, processed_at) "
                        "SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, file_hash, pdf_path, original_filename, "
                        "llm_raw_response, needs_confirmation, datetime('now') FROM utility_bills WHERE id = ?",
                        (
                            user_id, bill.utility_type, provider, bill.period_start_date, bill.period_end_date,
                            bill.usage_amount, bill.usage_unit, bill.cost, bill.due_date,
                            bill.account_number_last4, status, det_result.raw_text_length, review_status,
                            flag, fix, utility_bill_id,
                        ),
                    )
                    bill_id = cur.lastrowid

                saved.append((bill_id, flag))

                if bill.utility_type == "electric" and bill.electric_meter_details is not None:
                    m = bill.electric_meter_details
                    values = [getattr(m, col) for col in _METER_COLUMNS]
                    placeholders = ", ".join("?" for _ in _METER_COLUMNS)
                    columns = ", ".join(_METER_COLUMNS)
                    conn.execute(
                        f"INSERT INTO electric_meter_details (utility_bill_id, {columns}) "
                        f"VALUES (?, {placeholders})",
                        [bill_id, *values],
                    )

            conn.commit()
        except Exception:
            conn.rollback()
            raise

        any_pending = needs_confirmation or any(flag is not None for _, flag in saved)
        if not any_pending:
            delete_pdf_utility(conn, [bid for bid, _ in saved], pdf_path)

    except Exception as e:
        logger.exception("process_utility_bill(%s) failed unexpectedly", utility_bill_id)
        if conn is not None:
            _fail(conn, utility_bill_id, f"Unexpected error: {e}")
    finally:
        if conn is not None:
            conn.close()
