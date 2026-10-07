"""Orchestrates one statement's processing end to end (SPEC.md
section 4): dedup check, LLM extraction, amount validation + balance
reconciliation against the statement's own text, categorization, atomic
write. Runs in a background thread (see app/jobs.py) — every call
here is synchronous/blocking on purpose, offloaded off the event loop by
the caller, not made async itself.

The LLM (vision) path is the sole source of transactions — date,
description and amount all come from it. The deterministic path doesn't
attempt to independently reconstruct transactions (see deterministic.py's
module docstring for why the old row-level parser is gone, not just
disabled). It runs two independent, cheaper checks against the same
extracted text instead:

  - _consume_amount: does each transaction's amount actually appear on
    the statement at all (per-transaction; catches a misread/fabricated
    value). A failure here sets flag_reason (migration 0009) so the
    review queue (routes/review.py) can explain itself.
  - _check_reconciliation: does the sum of transactions actually account
    for the statement's own printed previous -> new balance movement
    (whole-statement; catches a missing or duplicated transaction that
    per-row agreement alone can never catch, since every row can
    individually check out and the total still be wrong).

Categorization (categorize_transactions, SPEC.md section 9) runs
last, inside the same atomic transaction as the inserts — every row gets
a category, explicit "Uncategorized" rather than a silent NULL.
"""
import dataclasses
import json
import logging
import re
import sqlite3
from collections import Counter
from functools import partial

from ..categorize import categorize_transactions
from ..db import get_db
from ..matching import auto_link_unambiguous, cascade_unlink_on_delete
from ..storage import forget_pdf
from . import documents
from .deterministic import extract_deterministic, NoTextLayer
from .vision import extract_vision, VisionExtractionError, ParsedTransaction

logger = logging.getLogger(__name__)


RECONCILIATION_TOLERANCE = 0.01  # cents-rounding only; a real missing/duplicated transaction is never this close

AMOUNT_NOT_FOUND_REASON = (
    "This amount wasn't found anywhere in the statement's own extracted text "
    "— it may have been misread or fabricated by the AI extraction."
)


check_duplicate = partial(documents.find_duplicate, "statements")
_set_step = partial(documents.set_step, "statements")
_fail = partial(documents.fail, "statements")


def _flatten_amounts(counts: Counter) -> list[float]:
    """Every extracted amount, expanded back out with multiplicity and
    sorted — persisted verbatim so the admin debug view (statement_debug.html)
    can show exactly what the deterministic path found, the same way
    llm_raw_response shows the LLM path's raw output. Snapshotted BEFORE
    _consume_amount starts decrementing the working counter below, so
    the debug view always shows the full original list, not whatever's
    left over after validation consumed matches out of it."""
    return sorted(counts.elements())


def _consume_amount(counts: Counter, amount: float) -> bool:
    """True if this magnitude was found somewhere in the statement's own
    extracted text, decrementing its count so a real repeated amount
    can't be reused to wave through more transactions than were actually
    printed. Magnitude only — sign isn't part of this check (see
    deterministic.py's module docstring for what this does and doesn't
    catch)."""
    key = round(abs(amount), 2)
    if counts[key] > 0:
        counts[key] -= 1
        return True
    return False


def _partition_duplicates(
    conn: sqlite3.Connection, user_id: str, account_id: int,
    transactions: list[ParsedTransaction],
) -> tuple[list[ParsedTransaction], list[ParsedTransaction], list[str], list[dict]]:
    """Splits a freshly-extracted transaction list into two different
    kinds of "this isn't a new transaction" and a human-readable note per
    one found, mirroring the exact (account, date, amount, description)
    match routes/csv_import.py's CSV import already uses to skip a row
    that's already on file — statement processing had no equivalent check
    at all before this, which is the root cause of two distinct-looking
    but related complaints: the same PDF re-uploaded after being
    downloaded a second time (different bytes, so the file_hash check in
    check_duplicate never catches it) silently created a second complete
    set of transactions; and a transaction a CSV import already brought in
    got re-created when the matching PDF was processed too (or vice
    versa).

    Two kinds, handled differently:

    - A (date, amount, description) that already appeared EARLIER in this
      SAME extraction is treated as the AI reading one real line twice —
      concretely seen reading a totals/summary line as if it were its own
      transaction, which duplicates whichever ordinary transaction happens
      to share its amount. This is excluded from BOTH the reconciliation
      sum and the insert: it was never really there twice, so counting it
      twice in the whole-statement arithmetic would itself produce a false
      "doesn't add up" flag on data that (once de-duplicated) is actually
      fine.
    - A (date, amount, description) that matches an already-LIVE
      transaction from anywhere else (an earlier statement, a CSV import)
      is a real transaction this statement is legitimately reporting —
      it still counts toward reconciling THIS statement's own printed
      balance movement, it just isn't inserted a second time.

    Returns (for_reconciliation, to_insert, notes, skipped) — for_reconciliation
    excludes only the first kind, to_insert excludes both kinds, notes is
    every skip in encounter order for the debug page, and skipped holds the
    same skips as records ({"txn", "reason", "matched"}) for the Review
    page's "Insert anyway" (skipped_duplicates)."""
    seen_within: Counter = Counter()
    for_reconciliation: list[ParsedTransaction] = []
    to_insert: list[ParsedTransaction] = []
    notes: list[str] = []
    skipped: list[dict] = []

    for txn in transactions:
        key = (txn.date, round(txn.amount, 2), txn.description)
        if seen_within[key] > 0:
            seen_within[key] += 1
            notes.append(
                f"{txn.date} {txn.description!r} {txn.amount:.2f} appears more than once in this "
                "statement's own extraction — treated as one transaction, not counted twice "
                "(commonly a totals/summary line read as if it were its own transaction)."
            )
            skipped.append({"txn": txn, "reason": "repeated", "matched": None})
            continue
        seen_within[key] += 1
        for_reconciliation.append(txn)

        existing = conn.execute(
            "SELECT id FROM transactions WHERE user_id = ? AND account_id = ? "
            "AND date = ? AND amount = ? AND description = ? AND deleted_at IS NULL LIMIT 1",
            (user_id, account_id, txn.date, round(txn.amount, 2), txn.description),
        ).fetchone()
        if existing is not None:
            notes.append(
                f"{txn.date} {txn.description!r} {txn.amount:.2f} already exists as transaction "
                f"#{existing['id']} — not imported again."
            )
            skipped.append({"txn": txn, "reason": "on_file", "matched": existing["id"]})
            continue
        to_insert.append(txn)

    return for_reconciliation, to_insert, notes, skipped


def _record_skipped(conn: sqlite3.Connection, user_id: str, account_id: int, statement_id: int,
                    skipped: list[dict]) -> None:
    """Keep what _partition_duplicates skipped, for the Review page's "Insert anyway"."""
    conn.executemany(
        "INSERT INTO skipped_duplicates (user_id, account_id, statement_id, date, amount, description, reason, "
        "matched_transaction_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [(user_id, account_id, statement_id, s["txn"].date, round(s["txn"].amount, 2), s["txn"].description,
          s["reason"], s["matched"]) for s in skipped],
    )


_YEAR_MONTH = re.compile(r"^(\d{4}-\d{2})")


def _infer_statement_period(transactions: list[ParsedTransaction]) -> str | None:
    """The calendar month most of this statement's transactions fall in,
    e.g. "2026-03" (SPEC.md's own field comment: "bank cycles
    align to calendar" — a deliberate simplification, not an attempt to
    capture an exact multi-day billing cycle, which isn't reliably
    extractable the same way across issuers anyway, unlike the two
    balance labels extract_balances looks for). A statement's last day or
    two can spill into the next calendar month, so this takes the most
    common month across all extracted transactions rather than e.g. the
    first or last one, which a boundary transaction could otherwise skew.
    None if there are no transactions to infer from at all (statement_period
    stays NULL, shown as "—" in the UI, not a guessed value)."""
    months = [m.group(1) for t in transactions if (m := _YEAR_MONTH.match(t.date))]
    if not months:
        return None
    return Counter(months).most_common(1)[0][0]


def _check_reconciliation(
    previous_balance: float | None, new_balance: float | None,
    transactions: list[ParsedTransaction],
) -> str | None:
    """Section 5's line-item-total vs printed-balance check: does the sum
    of extracted transactions actually account for the statement's own
    previous -> new balance movement? This is independent of, and catches
    a different failure mode than, _consume_amount above — two genuinely
    independent extractions of the same set of transactions can still
    agree on every individual row while missing (or duplicating) a row
    entirely, which agreement alone can never catch but arithmetic
    against the statement's own printed totals will.

    No account-type-conditional sign flip here (an earlier version of
    this function inverted the sum for credit_card, on the assumption
    that a purchase is extracted as a negative amount there same as on
    checking/savings). Real extracted data doesn't bear that out: this
    app's actual sign convention has a credit-card purchase come out
    POSITIVE (it increases what's owed) and a payment NEGATIVE (it
    reduces it) — see app/matching.py's module docstring. Under that
    convention the raw signed sum already points the same direction the
    printed balance moves for EITHER account type — more owed is a
    positive delta on a credit card exactly like more cash is a positive
    delta on checking/savings — so no per-type adjustment is needed at
    all; account_type has been dropped from this function's signature
    accordingly.

    Returns None if reconciled, or if either balance couldn't be found —
    in the latter case this check is simply skipped for this statement
    (see deterministic.py's extract_balances) rather than treated as a
    mismatch. A non-None return is a whole-statement note, not a
    per-transaction one: it doesn't say which row is wrong, only that
    something in aggregate doesn't add up.
    """
    if previous_balance is None or new_balance is None:
        return None

    actual_delta = round(sum(t.amount for t in transactions), 2)
    printed_delta = round(new_balance - previous_balance, 2)

    if abs(actual_delta - printed_delta) > RECONCILIATION_TOLERANCE:
        return (
            f"Statement's printed balance moved {printed_delta:+.2f} "
            f"(previous {previous_balance:.2f} -> new {new_balance:.2f}), but the "
            f"extracted transactions account for {actual_delta:+.2f} — a "
            f"transaction may be missing, duplicated, or misread."
        )
    return None


# A card payment's own wording ("MOBILE PAYMENT - THANK YOU", "AUTOPAY PAYMENT", "ONLINE PAYMENT"): on a card it
# is always a negative line (it reduces what is owed), whatever else the statement says.
_CARD_PAYMENT_WORDS = re.compile(r"\b(?:payment|autopay|auto\s*pay|thank\s*you)\b", re.IGNORECASE)


def _card_payments_backwards(rows) -> bool:
    """Every line worded like a card payment came out positive (a charge) and most other lines negative:
    the model signed the card like a bank account. On a card a payment always reduces what is owed."""
    payments = [t for t in rows if _CARD_PAYMENT_WORDS.search(t.description or "")]
    others = [t for t in rows if not _CARD_PAYMENT_WORDS.search(t.description or "")]
    return bool(payments and others and all(t.amount > 0 for t in payments)
                and sum(1 for t in others if t.amount < 0) * 2 > len(others))


def _fix_card_signs(previous_balance, new_balance, for_reconciliation, to_insert):
    """A card's purchases are stored positive (app/matching.py). If the model signed every line the
    other way round, every sign is flipped. Returns (for_reconciliation, to_insert, note, new_balance) —
    new_balance corrected when the statement's printed credit balance was read without its sign.

    1. The payments' own wording decides first: card payments that came out as charges, with the
       purchases negative, can only be a backwards reading. If the printed balances seemed to agree with
       that reading, they were read the wrong way round too (a credit balance printed without a minus
       sign the label search could see), so the movement is taken the other way round.
    2. Otherwise the printed balances decide when they can: the extracted total exactly the negative of
       the balance movement means backwards."""
    if not for_reconciliation:
        return for_reconciliation, to_insert, None, new_balance
    total = round(sum(t.amount for t in for_reconciliation), 2)
    flip = lambda rows: [dataclasses.replace(t, amount=-t.amount) for t in rows]
    have_balances = previous_balance is not None and new_balance is not None
    printed = round(new_balance - previous_balance, 2) if have_balances else None
    if _card_payments_backwards(for_reconciliation):
        n = sum(1 for t in for_reconciliation if _CARD_PAYMENT_WORDS.search(t.description or ""))
        note = (f"Signs flipped: the model signed purchases and payments the opposite way round ({n} card "
                f"payment(s) came out as charges); purchases are stored as money out on a card.")
        if have_balances and total != 0 and abs(total - printed) <= RECONCILIATION_TOLERANCE:
            new_balance = round(previous_balance - printed, 2)
            note += (f" The printed balance movement ({printed:+.2f}) was read without its sign and is taken as "
                     f"{-printed:+.2f}.")
        return flip(for_reconciliation), flip(to_insert), note, new_balance
    if have_balances and total != 0 and abs(total - printed) > RECONCILIATION_TOLERANCE \
            and abs(-total - printed) <= RECONCILIATION_TOLERANCE:
        return flip(for_reconciliation), flip(to_insert), (
            f"Signs flipped: the model signed purchases and payments the opposite way round (extracted total "
            f"{total:+.2f}, printed balance moved {printed:+.2f}); purchases are stored as money out on a card."), new_balance
    return for_reconciliation, to_insert, None, new_balance


def process_statement(statement_id: int, user_id: str, account_id: int, ollama_url: str, ollama_model: str) -> None:
    """The background job itself. Never raises out to the caller in a way
    that would crash the thread silently — every failure path updates the
    statement's own status/error_message before returning, per section 4's
    "failure is visible and restartable" design.

    Runs in a worker thread from app/jobs.py. The whole body is wrapped in
    one try/except with an explicit log call, so nothing escapes silently.

    Transactions left on this statement by an earlier, failed or interrupted
    run are removed first, so Restart always starts clean (they were never
    confirmed). current_step is updated at each stage — a multi-minute
    vision call would otherwise look like a hung job."""
    conn = None
    try:
        conn = get_db()
        row = conn.execute(
            "SELECT pdf_path, needs_confirmation, extraction_notes FROM statements WHERE id = ?", (statement_id,)
        ).fetchone()
        pdf_path = row["pdf_path"]
        extraction_notes = row["extraction_notes"] or ""
        needs_confirmation = bool(row["needs_confirmation"])
        # Needed below to tell a credit-card payment (a negative amount
        # there) apart from an ordinary purchase when auto-categorizing
        # freshly-inserted rows as "Payments" (see the categorization
        # step further down).
        account_type = conn.execute(
            "SELECT type FROM accounts WHERE id = ?", (account_id,)
        ).fetchone()["type"]

        _clear_previous_run(conn, user_id, statement_id)
        _set_step(conn, statement_id, "Extracting text (pdftotext)")
        try:
            det_result = extract_deterministic(pdf_path)
        except NoTextLayer as e:
            _fail(conn, statement_id, f"No text layer (scanned PDF, no OCR support): {e}")
            return
        except Exception as e:
            _fail(conn, statement_id, f"Deterministic extraction failed: {e}")
            return

        # Snapshot for the debug view before the validation loop below
        # starts consuming matches out of the working counter.
        deterministic_amounts_json = json.dumps(_flatten_amounts(det_result.amount_counts))

        try:
            vision_result = extract_vision(
                pdf_path, ollama_url, ollama_model,
                on_step=lambda msg: _set_step(conn, statement_id, msg),
                account_type=account_type, notes=extraction_notes,
            )
            conn.execute("UPDATE statements SET llm_raw_response = ? WHERE id = ?",
                         (vision_result.raw_response, statement_id))
            conn.commit()
        except VisionExtractionError as e:
            # Persist whatever Ollama actually said, even on failure — the
            # debug view needs this exactly when things went wrong, not
            # only when they went right.
            conn.execute("UPDATE statements SET llm_raw_response = ? WHERE id = ?",
                         (e.raw_response, statement_id))
            conn.commit()
            _fail(conn, statement_id, f"LLM extraction failed: {e}")
            return

        _set_step(conn, statement_id, "Checking for transactions already on file")
        # $0.00 lines (a card's "INTEREST CHARGED ON ..." when nothing was
        # charged) are never real transactions (amount != 0 is a constraint);
        # drop them before anything else. A nonzero credit-card interest charge
        # increases what's owed, so it is stored positive whatever sign the
        # extraction gave it.
        zero_amount_notes: list[str] = []
        nonzero_transactions: list[ParsedTransaction] = []
        for txn in vision_result.transactions:
            if round(txn.amount, 2) == 0:
                zero_amount_notes.append(
                    f"{txn.date} {txn.description!r} — extracted as $0.00 (no charge this period), not imported."
                )
                continue
            if account_type == "credit_card" and "interest charged" in txn.description.lower() and txn.amount < 0:
                txn = dataclasses.replace(txn, amount=abs(txn.amount))
            nonzero_transactions.append(txn)

        # Split out lines repeated within this extraction and transactions
        # already on file (see _partition_duplicates for how each is treated).
        for_reconciliation, to_insert, true_duplicate_notes, skipped = _partition_duplicates(
            conn, user_id, account_id, nonzero_transactions,
        )

        if not to_insert and true_duplicate_notes:
            # Every single extracted transaction turned out to already be
            # on file — this upload is a duplicate of an existing
            # statement (or of a CSV import covering the same period),
            # not a new one. Same shape as a plain hash-match duplicate
            # (check_duplicate above): nothing is written, the PDF is
            # kept so Restart still works, and the reason is explicit
            # rather than a generic failure. The skipped lines are kept so
            # Review can still insert any that are real.
            _record_skipped(conn, user_id, account_id, statement_id, skipped)
            conn.commit()
            _fail(
                conn, statement_id,
                "Duplicate — every transaction in this statement already exists "
                "(" + "; ".join(true_duplicate_notes) + ").",
            )
            return

        # Combined only for storage/display (statements.duplicate_transactions,
        # shown on the statement debug page) -- the fail-fast check just above
        # deliberately used true_duplicate_notes alone, so a statement that's
        # entirely $0.00 interest lines and nothing else (no real activity
        # this period) is never mislabeled "Duplicate" when it isn't one.
        duplicate_notes = zero_amount_notes + true_duplicate_notes

        _set_step(conn, statement_id, "Validating amounts against statement text")
        amount_counts = det_result.amount_counts  # mutated by _consume_amount below

        _set_step(conn, statement_id, "Reconciling against statement balance")
        if account_type == "credit_card":
            for_reconciliation, to_insert, flip_note, card_new_balance = _fix_card_signs(
                det_result.previous_balance, det_result.new_balance, for_reconciliation, to_insert)
            if flip_note:
                duplicate_notes.append(flip_note)
                skipped = [dict(s, txn=dataclasses.replace(s["txn"], amount=-s["txn"].amount)) for s in skipped]
        reconciliation_note = _check_reconciliation(
            det_result.previous_balance, card_new_balance if account_type == "credit_card" else det_result.new_balance,
            for_reconciliation,
        )
        statement_period = _infer_statement_period(for_reconciliation)

        _set_step(conn, statement_id, "Saving transactions")
        # The rows are committed when the "Categorizing" step is shown (so the
        # slow LLM call doesn't hold the write lock); a failure or interruption
        # after that leaves rows that _clear_previous_run removes on Restart.
        conn.execute("BEGIN")
        try:
            # A reconciliation mismatch is a whole-statement flag, not tied
            # to any one row — it alone is enough to keep this statement in
            # pending_review even if every individual transaction's amount
            # checked out.
            any_pending = reconciliation_note is not None
            inserted: list[tuple[int, str]] = []  # (transaction_id, description) — fed to categorization below
            for txn in to_insert:
                if _consume_amount(amount_counts, txn.amount):
                    txn_id = _insert_transaction(conn, user_id, account_id, statement_id, txn, review_status="clean")
                else:
                    # This amount doesn't appear anywhere in the statement's
                    # own extracted text — flag it rather than silently
                    # trusting a figure that may have been misread or
                    # fabricated (see deterministic.py's module docstring
                    # for the real case this caught: $287.00 -> $2187).
                    any_pending = True
                    txn_id = _insert_transaction(
                        conn, user_id, account_id, statement_id, txn,
                        review_status="pending_review", flag_reason=AMOUNT_NOT_FOUND_REASON,
                    )
                inserted.append((txn_id, txn.description))

            _record_skipped(conn, user_id, account_id, statement_id, skipped)

            # Categorization (section 9) runs whatever the review status. A
            # credit-card payment (negative on a card) isn't a merchant purchase,
            # so it goes straight to "Payments" instead of through the cascade.
            _set_step(conn, statement_id, "Categorizing transactions")
            # Pair with to_insert (what was actually written), not the raw extraction.
            card_payment_ids = [
                txn_id for (txn_id, _), txn in zip(inserted, to_insert)
                if account_type == "credit_card" and txn.amount < 0
            ]
            if card_payment_ids:
                conn.executemany(
                    "UPDATE transactions SET category = 'Payments', category_source = 'card_payment' WHERE id = ?",
                    [(tid,) for tid in card_payment_ids],
                )
            card_payment_id_set = set(card_payment_ids)
            remaining = [
                (txn_id, description) for txn_id, description in inserted
                if txn_id not in card_payment_id_set
            ]
            categorize_transactions(conn, user_id, ollama_url, ollama_model, remaining, statement_id=statement_id)

            # Transfer auto-link over the whole user (section 8): the other side
            # of a new card payment is often an older, still-unmatched outflow.
            auto_link_unambiguous(conn, user_id)

            conn.execute(
                "UPDATE statements SET status = ?, raw_text_length = ?, "
                "deterministic_amounts = ?, previous_balance = ?, new_balance = ?, "
                "balance_mismatch = ?, statement_period = ?, duplicate_transactions = ?, "
                "processed_at = datetime('now') WHERE id = ?",
                (
                    "pending_review" if (any_pending or needs_confirmation) else "complete",
                    det_result.raw_text_length,
                    deterministic_amounts_json,
                    det_result.previous_balance,
                    det_result.new_balance,
                    reconciliation_note,
                    statement_period,
                    json.dumps(duplicate_notes) if duplicate_notes else None,
                    statement_id,
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

        # Delete the PDF now only if nothing needs review or confirmation;
        # otherwise the review actions (routes/review.py, the confirm step)
        # end in finalize_if_ready, which deletes it.
        if not (any_pending or needs_confirmation):
            delete_pdf(conn, statement_id, pdf_path)

    except Exception as e:
        logger.exception("process_statement(%s) failed unexpectedly", statement_id)
        if conn is not None:
            try:
                _clear_previous_run(conn, user_id, statement_id)  # no half-saved rows on a failed statement
            except Exception:
                logger.exception("Could not remove partial rows of statement %s", statement_id)
            _fail(conn, statement_id, f"Unexpected error: {e}")
        # If get_db() itself failed there is nothing to record the failure
        # with; the row stays 'processing' until the next start marks it.
    finally:
        if conn is not None:
            conn.close()


def _clear_previous_run(conn: sqlite3.Connection, user_id: str, statement_id: int) -> None:
    """Remove the live transactions an earlier run of this statement left behind
    (unlinking any transfer partner first). A statement being processed has no
    confirmed rows, so there is nothing here worth keeping."""
    ids = [r[0] for r in conn.execute(
        "SELECT id FROM transactions WHERE statement_id = ? AND user_id = ? AND deleted_at IS NULL",
        (statement_id, user_id),
    )]
    if ids:
        cascade_unlink_on_delete(conn, user_id, ids)
        conn.execute(f"DELETE FROM transactions WHERE id IN ({','.join('?' * len(ids))})", ids)
    conn.execute("DELETE FROM skipped_duplicates WHERE statement_id = ? AND user_id = ?", (statement_id, user_id))
    conn.commit()


def _insert_transaction(
    conn: sqlite3.Connection, user_id: str, account_id: int, statement_id: int,
    txn: ParsedTransaction, review_status: str, flag_reason: str | None = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO transactions
            (user_id, account_id, statement_id, date, amount, description, review_status, flag_reason)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (user_id, account_id, statement_id, txn.date, txn.amount, txn.description, review_status, flag_reason),
    )
    return cur.lastrowid


def finalize_if_ready(conn: sqlite3.Connection, statement_id: int | None) -> bool:
    """Completes a `pending_review` statement and deletes its PDF once
    nothing is left for a human to do. The one place that decides this
    (resolve, bulk-approve and the confirm step all call it):

      - no flagged transactions left, and
      - if the statement asked for the confirm-against-PDF step
        (needs_confirmation), it has been confirmed — and that
        confirmation also covers a balance mismatch, since it is the same
        "I checked the PDF" judgement; otherwise
      - a balance mismatch still needs its own explicit acknowledge
        (routes/review.py's acknowledge_mismatch), never a silent
        close-out just because the per-row flags happened to clear.

    Returns True when it completed the statement (delete_pdf commits)."""
    if statement_id is None:
        return False
    stmt = conn.execute(
        "SELECT status, pdf_path, balance_mismatch, needs_confirmation, confirmed_at "
        "FROM statements WHERE id = ? AND deleted_at IS NULL",
        (statement_id,),
    ).fetchone()
    if stmt is None or stmt["status"] != "pending_review":
        return False
    still_flagged = conn.execute(
        "SELECT 1 FROM transactions WHERE statement_id = ? AND deleted_at IS NULL "
        "AND review_status = 'pending_review' LIMIT 1",
        (statement_id,),
    ).fetchone()
    if still_flagged is not None:
        return False
    if stmt["needs_confirmation"]:
        if stmt["confirmed_at"] is None:
            return False
    elif stmt["balance_mismatch"] is not None:
        return False
    conn.execute("UPDATE statements SET status = 'complete' WHERE id = ?", (statement_id,))
    delete_pdf(conn, statement_id, stmt["pdf_path"])
    return True


def delete_pdf(conn: sqlite3.Connection, statement_id: int, pdf_path: str | None) -> None:
    """Remove a finished statement's PDF. A file that can't be removed never
    turns a successful statement into an error."""
    forget_pdf("statements", conn, [statement_id], pdf_path)
