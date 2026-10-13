"""Transfer reconciliation (SPEC.md section 8), Phase 4.

Two kinds of transfer this module reconciles, both money moving between
two of the user's OWN accounts rather than real spending or income:

  - a payment to a credit card: an outflow leaving checking/savings, and
    the matching credit that lands on the card.
  - a transfer between two checking/savings accounts (e.g. moving money
    from one bank to another): an outflow leaving one account, an
    inflow landing on a *different* account.

Both are the same shape underneath — an outflow (a negative-amount row
on a checking/savings account: the only kind of row that can be the
"paying" side of a transfer) matched against an inflow (the receiving
side): a positive-amount row on checking/savings, OR a NEGATIVE-amount
row on a credit_card account. That credit-card sign is not a typo —
real statement data extracted by this app's own parser shows credit-
card purchases as positive (they increase what's owed) and payments as
negative (they reduce it), the opposite of a checking/savings account's
"negative leaves, positive enters" convention. Account type is what
disambiguates the two: the sign alone never tells you which side of a
transfer a row could be. This module treats both transfer kinds
uniformly once that's accounted for: find candidate pairs, auto-link
the unambiguous ones, and provide the building blocks for a human to
confirm an ambiguous one, or undo a wrong link, or force-check one
specific transaction (app/routes/transfers.py is the UI on top of
this).

Matching is amount + date-proximity, not description-based — a bank's
statement text for "payment to card ending 1234" / "transfer to
savings" and the receiving account's own statement text for the same
movement rarely share any substring worth matching on, so amount + a
short date window is the only signal that's actually reliable here.

Only ever matches transactions at review_status = 'clean' — a still-
flagged transaction's amount/date may yet be corrected during review
(SPEC.md section 7), and matching against a value that might
still change risks linking the wrong pair permanently.

**Linking, mechanically** (SPEC.md section 8): both sides here
always already exist as rows (this module only ever links two
already-imported transactions, never inserts fresh ones), so linking is
just the two UPDATEs — the caller is expected to run them inside one
DB transaction, same discipline as the rest of this app's writes.

**Unlinking** (SPEC.md section 11's "transfer-pair cascade"):
breaking a link — whether by an explicit undo (`unlink_transfer`) or
because one side got soft-deleted (`cascade_unlink_on_delete`, called
from routes/transactions.py's bulk delete before the delete itself) —
reverts each side to what it would have been without the link, keyed
off the row's OWN amount sign rather than its account type (account
type alone no longer determines direction, now that both sides of a
checking-to-checking transfer are the same type):
  - a negative-amount side (money that was leaving) -> plain 'expense'.
  - a positive-amount side (money that was arriving, whether onto a
    credit card or into another checking/savings account) -> 'income',
    but `is_excluded` is set at the same time too, so a routine
    unlink/delete can't silently inflate a dashboard income total the
    user never actually decided was real income.
"""
from collections import Counter
from datetime import date as date_cls

from .amounts import to_cents

AMOUNT_TOLERANCE = 0.01  # cents-rounding only; a real mismatch is never this close
AMOUNT_TOLERANCE_CENTS = 1   # the same, compared in whole cents (0.07 - 0.06 > 0.01 in floating point)
DATE_WINDOW_DAYS = 5     # a transfer commonly posts a day or two off on the other side


def _parse_date(s: str) -> date_cls | None:
    try:
        return date_cls.fromisoformat(s[:10])
    except (ValueError, TypeError):
        return None


def find_transfer_candidates(conn, user_id: str):
    """Returns (outflows, inflows) — both lists of sqlite3.Row,
    restricted to unlinked, fully-imported, non-deleted, non-excluded
    transactions on the user's own accounts:

      - outflows: negative-amount rows on a checking/savings account —
        the only kind of row that can be the "paying" side of a
        transfer (a credit-card purchase is a negative row too, but
        it's an expense, never the source of a transfer out).
      - inflows: a deposit landing in a different checking/savings
        account (positive amount), OR a payment received on a credit
        card (NEGATIVE amount there — see this module's docstring for
        why). `_candidates_for` is what excludes an inflow on the SAME
        account as a given outflow (that's not a transfer to anywhere,
        just two rows in one account that happen to net out).

    A standalone read (not just an internal helper of the sweep below)
    so the Transfers page can build its "still ambiguous" display from
    exactly the same candidate pool the auto-link sweep itself used.

    is_excluded = 0 is deliberate, not incidental: unlink_transfer sets
    is_excluded = 1 on the inflow side specifically so that a human's
    "undo" sticks — without this filter, the very next sweep (the next
    page load, or the next statement import) would see the exact same
    amount/date match and silently re-link the pair the human just
    chose to break. A row a human has excluded any other way is
    excluded from candidacy for the same reason: it's a deliberate
    signal not to auto-fold it into anything."""
    outflows = conn.execute(
        """
        SELECT t.*, a.name AS account_name, a.type AS account_type FROM transactions t
        JOIN accounts a ON a.id = t.account_id
        WHERE t.user_id = ? AND t.deleted_at IS NULL AND t.review_status = 'clean'
          AND t.transfer_pair_id IS NULL AND t.is_excluded = 0
          AND t.amount < 0 AND a.type IN ('checking', 'savings')
        ORDER BY t.date
        """,
        (user_id,),
    ).fetchall()
    inflows = conn.execute(
        """
        SELECT t.*, a.name AS account_name, a.type AS account_type FROM transactions t
        JOIN accounts a ON a.id = t.account_id
        WHERE t.user_id = ? AND t.deleted_at IS NULL AND t.review_status = 'clean'
          AND t.transfer_pair_id IS NULL AND t.is_excluded = 0
          AND (
              (a.type IN ('checking', 'savings') AND t.amount > 0)
              OR (a.type = 'credit_card' AND t.amount < 0)
          )
        ORDER BY t.date
        """,
        (user_id,),
    ).fetchall()
    return outflows, inflows


def _amounts_match(a: float, b: float) -> bool:
    """Same size within a cent, compared in whole cents."""
    return abs(abs(to_cents(a)) - abs(to_cents(b))) <= AMOUNT_TOLERANCE_CENTS


class InflowIndex:
    """The inflows grouped by size in whole cents, so finding one outflow's candidates looks at three
    small groups (a cent either side) instead of every inflow. Candidates come back in the inflows'
    own order, exactly as a scan of the whole list would give them."""

    def __init__(self, inflows):
        self._by_cents: dict[int, list] = {}
        for position, row in enumerate(inflows):
            self._by_cents.setdefault(abs(to_cents(row["amount"])), []).append(
                (position, row, _parse_date(row["date"])))

    def candidates(self, outflow) -> list:
        outflow_date = _parse_date(outflow["date"])
        if outflow_date is None:
            return []
        cents = abs(to_cents(outflow["amount"]))
        found = []
        for c in range(cents - AMOUNT_TOLERANCE_CENTS, cents + AMOUNT_TOLERANCE_CENTS + 1):
            for position, row, inflow_date in self._by_cents.get(c, ()):
                if row["account_id"] == outflow["account_id"]:
                    continue
                if inflow_date is None or abs((inflow_date - outflow_date).days) > DATE_WINDOW_DAYS:
                    continue
                found.append((position, row))
        found.sort(key=lambda pair: pair[0])
        return [row for _, row in found]


def _candidates_for(outflow, inflows) -> list:
    """Inflow rows within a cent (AMOUNT_TOLERANCE_CENTS) and DATE_WINDOW_DAYS of one
    outflow row, on a DIFFERENT account than the outflow — an inflow on
    the very same account the outflow left from isn't a transfer
    anywhere, it's just two rows in one account (e.g. a same-day refund
    against a purchase) that happen to net out by coincidence.
    `inflows` is a list of rows or an InflowIndex built from them (much
    faster when many outflows are checked against the same inflows)."""
    if isinstance(inflows, InflowIndex):
        return inflows.candidates(outflow)
    outflow_date = _parse_date(outflow["date"])
    if outflow_date is None:
        return []
    matches = []
    for i in inflows:
        if i["account_id"] == outflow["account_id"]:
            continue
        if not _amounts_match(i["amount"], outflow["amount"]):
            continue
        inflow_date = _parse_date(i["date"])
        if inflow_date is None or abs((inflow_date - outflow_date).days) > DATE_WINDOW_DAYS:
            continue
        matches.append(i)
    return matches


def candidates_for_transaction(conn, user_id: str, txn_id: int):
    """Single-transaction lookup for the manual "Find match" action
    (routes/transfers.py's /transfer-match-one) — same eligibility
    rules as find_transfer_candidates (unlinked, clean, non-excluded,
    non-deleted, owned by user_id), applied to just one row instead of
    a whole-user sweep.

    Returns (row, candidates, role, reason):
      - row is None (candidates=[], role=None, reason="not_found") if
        txn_id doesn't exist, isn't user_id's, or was deleted.
      - row is set but candidates=[] and role=None when the row itself
        exists but currently ISN'T eligible to be matched — reason is
        one of "already_linked", "not_clean" (still pending review),
        "excluded" (the is_excluded checkbox is on), or "wrong_shape"
        (a negative row on a credit_card account: a plain purchase,
        never either side of a transfer). Distinguishing these from a
        genuine "no candidate exists" was added after a real pair
        wasn't matching and the old generic message gave no way to
        tell which of these it actually was.
      - otherwise reason is None, role is "outflow" or "inflow" per the
        row's own sign and account type, and candidates is the list of
        transactions on the OTHER side this row could match — exactly
        what auto-link would have looked at, so a human clicking this
        button never sees a wider or narrower pool than the automatic
        sweep uses."""
    row = conn.execute(
        """
        SELECT t.*, a.type AS account_type, a.name AS account_name FROM transactions t
        JOIN accounts a ON a.id = t.account_id
        WHERE t.id = ? AND t.user_id = ? AND t.deleted_at IS NULL
        """,
        (txn_id, user_id),
    ).fetchone()
    if row is None:
        return None, [], None, "not_found"

    if row["transfer_pair_id"] is not None:
        return row, [], None, "already_linked"
    if row["review_status"] != "clean":
        return row, [], None, "not_clean"
    if row["is_excluded"]:
        return row, [], None, "excluded"

    outflows, inflows = find_transfer_candidates(conn, user_id)

    if row["amount"] < 0 and row["account_type"] in ("checking", "savings"):
        return row, _candidates_for(row, inflows), "outflow", None

    is_credit_card_payment = row["amount"] < 0 and row["account_type"] == "credit_card"
    is_plain_inflow = row["amount"] > 0 and row["account_type"] in ("checking", "savings")
    if is_credit_card_payment or is_plain_inflow:
        candidates = [o for o in outflows if _candidates_for(o, [row])]
        return row, candidates, "inflow", None

    # A positive row on a credit_card account (a plain purchase — see
    # this module's docstring for the sign convention) can never be
    # either side of a transfer.
    return row, [], None, "wrong_shape"


def diagnose_no_match(conn, user_id: str, row, role: str) -> str:
    """Only called when candidates_for_transaction found row eligible
    in principle (role is set) but returned zero candidates — explains
    WHY by re-running the same amount/date/different-account search
    with the OTHER side's transfer_pair_id/is_excluded/review_status
    filters relaxed. Distinguishes "nothing like this exists at all"
    from "something matches but can't be used yet" (that something is
    already linked elsewhere, excluded, or still pending review) — in
    the second case the fix is on the OTHER transaction, not this one,
    which a bare "No match found" can't tell you."""
    row_date = _parse_date(row["date"])
    if row_date is None:
        return "This transaction's date could not be read."

    target_amount = abs(row["amount"])
    if role == "outflow":
        # Looking for the inflow/receiving side: a deposit on checking/
        # savings, or a payment (negative amount) on a credit card.
        other_shape_sql = (
            "((a.type IN ('checking', 'savings') AND t.amount > 0) "
            "OR (a.type = 'credit_card' AND t.amount < 0))"
        )
    else:
        # Looking for the outflow/paying side: only ever a negative row
        # on checking/savings.
        other_shape_sql = "(a.type IN ('checking', 'savings') AND t.amount < 0)"

    raw = conn.execute(
        f"""
        SELECT t.*, a.name AS account_name, a.type AS account_type FROM transactions t
        JOIN accounts a ON a.id = t.account_id
        WHERE t.user_id = ? AND t.deleted_at IS NULL AND t.account_id != ?
          AND {other_shape_sql}
        """,
        (user_id, row["account_id"]),
    ).fetchall()

    near = []
    for o in raw:
        if not _amounts_match(o["amount"], target_amount):
            continue
        d = _parse_date(o["date"])
        if d is None or abs((d - row_date).days) > DATE_WINDOW_DAYS:
            continue
        near.append(o)

    if not near:
        return (
            f"No transaction for {target_amount:.2f} was found on a different "
            f"account within {DATE_WINDOW_DAYS} days of {row['date']}."
        )

    parts = []
    for o in near:
        if o["transfer_pair_id"] is not None:
            parts.append(f"{o['account_name']} {o['date']} matches but is already linked to something else")
        elif o["review_status"] != "clean":
            parts.append(f"{o['account_name']} {o['date']} matches but is still pending review")
        elif o["is_excluded"]:
            parts.append(f"{o['account_name']} {o['date']} matches but is marked “Exclude”")
        else:
            parts.append(f"{o['account_name']} {o['date']} looks eligible but wasn't offered as a candidate — worth reporting as a bug")
    return "; ".join(parts) + "."


def link_transfer(conn, txn_id_a: int, txn_id_b: int) -> None:
    """The actual two-sided link — order doesn't matter, both sides get
    the same treatment. Callers are responsible for validating
    ownership/eligibility first (see routes/transfers.py's manual-
    confirm route) — this function just writes the two UPDATEs."""
    conn.execute(
        "UPDATE transactions SET transfer_pair_id = ?, txn_type = 'transfer' WHERE id = ?",
        (txn_id_b, txn_id_a),
    )
    conn.execute(
        "UPDATE transactions SET transfer_pair_id = ?, txn_type = 'transfer' WHERE id = ?",
        (txn_id_a, txn_id_b),
    )


def auto_link_unambiguous(conn, user_id: str) -> int:
    """The whole-user sweep — called in the same transaction as every change
    that can make a row a candidate (a statement's processing, a CSV import, a
    manual transaction, review, exclude/include, delete/restore, undoing a
    link — see relink) and by the Transfers page's "Link unambiguous" button,
    so pairs are already linked by the time a human looks at the page. Pages
    only read: opening one never writes.

    Links an outflow to an inflow only when the match is mutually
    unique: the outflow has exactly one inflow candidate, AND that
    inflow isn't also a candidate for some OTHER still-unlinked outflow.
    An inflow that could match two different outflows is exactly as
    ambiguous as an outflow with two candidate inflows — checking only
    one direction would silently pick an arbitrary winner.

    One pass is enough. Think of every possible (outflow, inflow) match as
    a line between the two: a pair linked here is a line with nothing else
    touching either end. Linking it removes just those two rows, and no
    other row had either of them as a candidate, so no other row's
    candidates change — a fresh look after each link (what this used to
    do, refetching everything every time) finds exactly the same pairs.
    The inflows are grouped by size in whole cents (InflowIndex), so this
    takes time in proportion to the number of rows, not their square.
    Returns the number of pairs linked."""
    outflows, inflows = find_transfer_candidates(conn, user_id)
    if not outflows or not inflows:
        return 0
    index = InflowIndex(inflows)
    matches = {o["id"]: index.candidates(o) for o in outflows}
    times_offered = Counter(i["id"] for found in matches.values() for i in found)
    linked = 0
    for outflow in outflows:
        found = matches[outflow["id"]]
        if len(found) == 1 and times_offered[found[0]["id"]] == 1:
            link_transfer(conn, outflow["id"], found[0]["id"])
            linked += 1
    return linked


def _revert_transfer_side(conn, txn_id: int) -> None:
    """Reverts ONE side of a broken transfer link back to its pre-link
    classification and clears its own transfer_pair_id — see this
    module's docstring for the reversion rule. Which side a row WAS
    (the paying side vs. the receiving side) is no longer just "amount
    sign" on its own, now that a credit card's payment side is a
    NEGATIVE row (see the sign-convention note at the top of this
    file): the receiving side is a positive row on checking/savings OR
    a negative row on a credit card; the paying side is always a
    negative row on checking/savings. Does not touch the other side;
    callers decide which side(s) actually need reverting."""
    row = conn.execute(
        "SELECT t.id, t.amount, a.type AS account_type FROM transactions t "
        "JOIN accounts a ON a.id = t.account_id WHERE t.id = ?",
        (txn_id,),
    ).fetchone()
    if row is None:
        return
    was_receiving_side = (
        (row["account_type"] == "credit_card" and row["amount"] < 0)
        or (row["account_type"] != "credit_card" and row["amount"] > 0)
    )
    if was_receiving_side:
        conn.execute(
            "UPDATE transactions SET transfer_pair_id = NULL, txn_type = 'income', is_excluded = 1 WHERE id = ?",
            (txn_id,),
        )
    else:
        conn.execute(
            "UPDATE transactions SET transfer_pair_id = NULL, txn_type = 'expense' WHERE id = ?",
            (txn_id,),
        )


def unlink_transfer(conn, txn_id: int) -> bool:
    """Manual "undo" (Transfers page) — reverts BOTH sides of whichever
    pair txn_id belongs to. Returns False (no-op, nothing written) if
    txn_id isn't currently linked to anything."""
    row = conn.execute("SELECT transfer_pair_id FROM transactions WHERE id = ?", (txn_id,)).fetchone()
    if row is None or row["transfer_pair_id"] is None:
        return False
    partner_id = row["transfer_pair_id"]
    _revert_transfer_side(conn, txn_id)
    _revert_transfer_side(conn, partner_id)
    return True


def cascade_unlink_on_delete(conn, user_id: str, deleting_ids: list[int]) -> None:
    """Call BEFORE soft-deleting a batch of transactions (SPEC.md
    section 11's transfer-pair-cascade rule). For each linked transaction
    in the batch whose PARTNER is not also being deleted in the same
    batch, reverts just the surviving partner — the row actually being
    deleted doesn't need its own fields reverted, since deleted_at
    already hides it everywhere else in the app. If both sides of a pair
    are deleted together, there's no surviving side and nothing to
    revert.

    Scoped to user_id in its own lookup (not just trusting the caller's
    id list) — the same discipline every other write in this app
    applies, so a ids list that happened to include a foreign id can
    never cause this to touch a transaction outside the deleting user's
    own accounts."""
    if not deleting_ids:
        return
    deleting_set = set(deleting_ids)
    placeholders = ",".join("?" for _ in deleting_ids)
    rows = conn.execute(
        f"SELECT id, transfer_pair_id FROM transactions "
        f"WHERE id IN ({placeholders}) AND user_id = ? AND transfer_pair_id IS NOT NULL",
        (*deleting_ids, user_id),
    ).fetchall()
    for r in rows:
        partner_id = r["transfer_pair_id"]
        if partner_id not in deleting_set:
            _revert_transfer_side(conn, partner_id)


def relink(conn, user_id: str) -> int:
    """After a change that can make a transaction a transfer candidate (or stop one being one): link
    whatever is now unambiguous, in the caller's transaction (the caller commits)."""
    return auto_link_unambiguous(conn, user_id)
