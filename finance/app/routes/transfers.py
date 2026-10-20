"""Transfer reconciliation UI (SPEC.md section 8) — a place to
see linked transfer pairs (credit-card payments AND checking-to-checking
moves alike), confirm an ambiguous match by hand, force-check one
specific transaction, and undo a link. The actual matching logic lives
in app/matching.py; this module is just the routes on top of it.
"""
import calendar
import logging
from datetime import date
from urllib.parse import parse_qsl, urlencode
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from ..auth import User, get_current_user, get_acting_user
from ..db import get_db
from ..security import safe_next
from ..version import APP_VERSION
from ..matching import (
    InflowIndex,
    _candidates_for,
    auto_link_unambiguous,
    candidates_for_transaction,
    diagnose_no_match,
    find_transfer_candidates,
    link_transfer,
    relink,
    unlink_transfer,
)
from .accounts import _acting_banner, _acting_qs

logger = logging.getLogger(__name__)
router = APIRouter()
from ..template_filters import install as _install_filters
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
_install_filters(templates.env)
templates.env.globals["app_version"] = APP_VERSION


@router.get("/transfers")
def list_transfers(
    request: Request,
    from_account: str = "",
    to_account: str = "",
    start_date: str | None = None,
    end_date: str | None = None,
    highlight: str = "",
    linked: str = "",
    link_error: str = "",
    link_side: str = "",
    link_id: str = "",
    matched: str = "",
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """Filters apply to both sections (linked pairs and waiting candidates).
    from_account is the paying side, to_account the receiving side; the date
    range is checked against the paying side's date (the only date a waiting
    candidate has). highlight=<transaction id> narrows the linked pairs to
    the pair that transaction belongs to (the "View linked transfer" link)."""
    from_account_id = int(from_account) if from_account.strip().isdigit() else None
    to_account_id = int(to_account) if to_account.strip().isdigit() else None
    highlight_txn_id = int(highlight) if highlight.strip().isdigit() else None

    # Default to this month only when no date param was sent at all (None);
    # "show all" sends empty strings, and a highlighted pair skips the default.
    defaulted_to_this_month = start_date is None and end_date is None and not highlight.strip()
    if defaulted_to_this_month:
        today = date.today()
        start_date = today.replace(day=1).isoformat()
        end_date = today.replace(day=calendar.monthrange(today.year, today.month)[1]).isoformat()
    else:
        start_date = (start_date or "").strip()
        end_date = (end_date or "").strip()

    def _in_range(date_str: str) -> bool:
        return (not start_date or date_str >= start_date) and (not end_date or date_str <= end_date)

    with get_db() as conn:
        link_error_text = _link_problem_text(conn, acting.id, link_error, link_side, link_id) if link_error else ""
        # Opening the page only reads: unambiguous pairs were linked when the
        # data changed (matching.relink — every import, review, edit, delete
        # and restore), and "Link unambiguous" runs the sweep on demand.
        accounts = conn.execute(
            "SELECT id, name, type FROM accounts WHERE user_id = ? AND deleted_at IS NULL AND is_archived = 0 "
            "ORDER BY name COLLATE NOCASE",
            (acting.id,),
        ).fetchall()

        outflows, inflows = find_transfer_candidates(conn, acting.id)
        inflows = InflowIndex(inflows)       # grouped by size: each outflow looks at a few rows, not all
        ambiguous = []
        for outflow in outflows:
            if from_account_id and outflow["account_id"] != from_account_id:
                continue
            if not _in_range(outflow["date"]):
                continue
            matches = _candidates_for(outflow, inflows)
            if to_account_id and not any(m["account_id"] == to_account_id for m in matches):
                # Gate the whole GROUP on whether any candidate is on
                # the target account, but never silently narrow the
                # candidate list itself — a human resolving an ambiguous
                # match should still see every option once the group
                # has been filtered into view, not a truncated one.
                matches = []
            if matches:
                ambiguous.append({"outflow": outflow, "candidates": matches})

        # Recently linked pairs, one row per pair anchored on the paying side:
        # a negative row on checking/savings. (A card payment is negative too,
        # so "amount < 0" alone would list every card transfer twice.)
        linked_conditions = [
            "t.user_id = ?", "t.deleted_at IS NULL", "t.txn_type = 'transfer'",
            "t.amount < 0", "a.type IN ('checking', 'savings')",
        ]
        linked_params: list = [acting.id]
        if from_account_id:
            linked_conditions.append("t.account_id = ?")
            linked_params.append(from_account_id)
        if to_account_id:
            linked_conditions.append("p.account_id = ?")
            linked_params.append(to_account_id)
        if start_date:
            linked_conditions.append("t.date >= ?")
            linked_params.append(start_date)
        if end_date:
            linked_conditions.append("t.date <= ?")
            linked_params.append(end_date)
        if highlight_txn_id:
            linked_conditions.append("(t.id = ? OR p.id = ?)")
            linked_params.append(highlight_txn_id)
            linked_params.append(highlight_txn_id)

        just_linked = None
        if linked.isdigit():
            just_linked = conn.execute(
                "SELECT t.date AS out_date, t.amount AS out_amount, a.name AS out_account_name, "
                "p.date AS in_date, pa.name AS in_account_name FROM transactions t "
                "JOIN accounts a ON a.id = t.account_id JOIN transactions p ON p.id = t.transfer_pair_id "
                "JOIN accounts pa ON pa.id = p.account_id WHERE t.id = ? AND t.user_id = ?",
                (int(linked), acting.id)).fetchone()

        linked = conn.execute(
            f"""
            SELECT t.id AS out_id, t.date AS out_date, t.amount AS out_amount,
                   a.name AS out_account_name,
                   p.id AS in_id, p.date AS in_date, p.amount AS in_amount,
                   pa.name AS in_account_name, pa.type AS in_account_type
            FROM transactions t
            JOIN accounts a ON a.id = t.account_id
            JOIN transactions p ON p.id = t.transfer_pair_id
            JOIN accounts pa ON pa.id = p.account_id
            WHERE {' AND '.join(linked_conditions)}
            ORDER BY t.date DESC, t.id DESC
            LIMIT 100
            """,
            linked_params,
        ).fetchall()

    return templates.TemplateResponse(
        request, "transfers.html",
        {
            "user": acting,
            "ambiguous": ambiguous,
            "linked": linked,
            "accounts": accounts,
            "filter_from_account": from_account_id,
            "filter_to_account": to_account_id,
            "filter_start_date": start_date,
            "filter_end_date": end_date,
            "defaulted_to_this_month": defaulted_to_this_month,
            "highlight_txn_id": highlight_txn_id,
            "dates_sent": not defaulted_to_this_month,
            "just_linked": just_linked,
            "just_matched": int(matched) if matched.isdigit() else None,
            "link_error": link_error_text,
            "impersonation_qs_value": acting.id if acting.id != current.id else "",
            "acting_as_banner": _acting_banner(current, acting),
            "acting_qs": _acting_qs(current, acting),
            "active_page": "transfers",
        },
    )


_LINK_PROBLEMS = {
    "missing": "{label} wasn't found in this data (it may belong to another user's view).",
    "deleted": "{label} ({where}) has been deleted.",
    "linked": "{label} ({where}) is already linked to another transfer.",
    "pending": "{label} ({where}) is still pending review.",
    "not_outflow": "{label} must be money out of a checking or savings account.",
    "not_inflow": "{label} must be money into checking/savings or a payment to a card.",
    "same_account": "Both sides are on the same account, so they can't be a transfer.",
}


def _linkable_row(conn, user_id: str, txn_id: int):
    return conn.execute(
        "SELECT t.*, a.type AS account_type, a.name AS account_name FROM transactions t "
        "JOIN accounts a ON a.id = t.account_id WHERE t.id = ? AND t.user_id = ?", (txn_id, user_id)).fetchone()


def _why_not_linkable(conn, user_id: str, txn_id: int, side: str) -> str | None:
    """None if the row can be this side of a transfer, else a short code (_LINK_PROBLEMS)."""
    row = _linkable_row(conn, user_id, txn_id)
    if row is None:
        return "missing"
    if row["deleted_at"]:
        return "deleted"
    if row["transfer_pair_id"]:
        return "linked"
    if row["review_status"] != "clean":
        return "pending"
    if side == "outflow" and not (row["amount"] < 0 and row["account_type"] in ("checking", "savings")):
        return "not_outflow"
    if side == "inflow" and not ((row["account_type"] in ("checking", "savings") and row["amount"] > 0)
                                 or (row["account_type"] == "credit_card" and row["amount"] < 0)):
        return "not_inflow"
    return None


def _link_problem_text(conn, user_id: str, code: str, side: str, txn_id: str) -> str:
    """The sentence for a refused link, rebuilt from the code in the address and the acting user's own
    row (the address never carries the text itself)."""
    template = _LINK_PROBLEMS.get(code)
    if template is None:
        return ""
    label = "The paying side" if side == "outflow" else "The receiving side"
    row = _linkable_row(conn, user_id, int(txn_id)) if txn_id.isdigit() else None
    where = f"{row['account_name']} {row['date']}" if row is not None else "that transaction"
    return template.format(label=label, where=where)


def _transfers_url(current: User, acting: User, filters: dict, **extra) -> str:
    """Back to the Transfers page with the filters the person was using (dates may be empty = all)."""
    from urllib.parse import urlencode
    params = []
    qs = _acting_qs(current, acting)
    for key in ("from_account", "to_account"):
        if filters.get(key):
            params.append((key, filters[key]))
    if filters.get("dates_sent") == "1":
        params += [("start_date", filters.get("start_date") or ""), ("end_date", filters.get("end_date") or "")]
    params += [(k, v) for k, v in extra.items() if v not in (None, "")]
    query = "&".join(x for x in (qs, urlencode(params)) if x)
    return "transfers" + (f"?{query}" if query else "")


@router.post("/transfer-link")
def confirm_transfer_link(
    request: Request,
    outflow_id: int = Form(...),
    inflow_id: int = Form(...),
    from_account: str = Form(""),
    to_account: str = Form(""),
    start_date: str = Form(""),
    end_date: str = Form(""),
    dates_sent: str = Form(""),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """Manual confirm of a match from the Transfers page. Re-validates both
    sides belong to the acting user, are still unlinked and clean, and have
    the sign/account-type the matching engine requires (a negative row on
    checking/savings pays; a positive row on checking/savings or a negative
    row — a payment — on a credit card receives), on different accounts.
    Doesn't re-check amount/date proximity: a human picked this pair.
    Always goes back to the Transfers page, keeping its filters, with a
    message saying what happened (never a bare error page)."""
    filters = {"from_account": from_account, "to_account": to_account, "start_date": start_date,
               "end_date": end_date, "dates_sent": dates_sent}
    with get_db() as conn:
        side, problem_id = "outflow", outflow_id
        problem = _why_not_linkable(conn, acting.id, outflow_id, "outflow")
        if problem is None:
            side, problem_id = "inflow", inflow_id
            problem = _why_not_linkable(conn, acting.id, inflow_id, "inflow")
        if problem is None:
            accounts = conn.execute("SELECT id, account_id FROM transactions WHERE id IN (?, ?)",
                                    (outflow_id, inflow_id)).fetchall()
            if len({r["account_id"] for r in accounts}) != 2:
                problem = "same_account"
        if problem is None:
            link_transfer(conn, outflow_id, inflow_id)
            relink(conn, acting.id)      # taking these two out may leave another pair unambiguous
            conn.commit()
    if problem:
        logger.warning("Transfer link %s -> %s refused for %s: %s", outflow_id, inflow_id, acting.id, problem)
        return RedirectResponse(url=_transfers_url(current, acting, filters, link_error=problem, link_side=side,
                                                   link_id=str(problem_id)), status_code=303)
    return RedirectResponse(url=_transfers_url(current, acting, filters, linked=str(outflow_id)), status_code=303)


@router.post("/transfer-run-sweep")
def run_transfer_sweep(
    request: Request,
    next: str = Form("dashboard"),
    period: str = Form(""),
    account_id: str = Form(""),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """Run the transfer auto-link sweep now (it also runs after every import)
    and return to the page it was started from with matched=<n>."""
    with get_db() as conn:
        linked = auto_link_unambiguous(conn, acting.id)
        conn.commit()

    params = dict(parse_qsl(_acting_qs(current, acting)))
    if period:
        params["period"] = period
    if account_id:
        params["account_id"] = account_id
    params["matched"] = str(linked)
    target = safe_next(next, "dashboard")
    return RedirectResponse(url=target + ("&" if "?" in target else "?") + urlencode(params), status_code=303)


@router.post("/transfer-unlink")
def undo_transfer_link(
    request: Request,
    id: int = Form(...),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """Scoped to the acting user's own transaction — a tampered id can't
    unlink, and thus can't revert the classification of, another user's
    transaction. unlink_transfer itself is a no-op if id isn't actually
    linked to anything."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM transactions WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
            (id, acting.id),
        ).fetchone()
        if row is not None:
            unlink_transfer(conn, id)
            relink(conn, acting.id)
            conn.commit()

    qs = _acting_qs(current, acting)
    return RedirectResponse(url=f"transfers{'?' + qs if qs else ''}", status_code=303)


@router.post("/transfer-match-one")
def match_one_transaction(
    request: Request,
    id: int = Form(...),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """Manual "find a match for just this transaction" trigger — lets a
    human force a check on one row without waiting for the next whole-
    user sweep, and without the sweep's mutual-uniqueness restriction:
    if THIS transaction has exactly one candidate on the other side, it
    links immediately, even if that candidate would otherwise be held
    back as ambiguous relative to some other unrelated pair the sweep
    hasn't gotten to. If there's more than one candidate, this doesn't
    guess — it reports the count and leaves it for the Transfers page.

    Returns the same _match_action.html fragment the transactions table
    includes per row (htmx outerHTML swap on the form itself), same
    pattern as _note_field.html/_category_field.html/_exclude_field.html."""
    linked_partner_id = None
    with get_db() as conn:
        row, candidates, role, reason = candidates_for_transaction(conn, acting.id, id)
        if row is None:
            message = "Transaction not found."
        elif reason == "already_linked":
            message = "Already linked to a transfer \u2014 see the Transfers page."
        elif reason == "not_clean":
            message = "Not yet fully imported (still pending review)."
        elif reason == "excluded":
            message = "This transaction is marked \u201cExclude\u201d \u2014 uncheck that first if you want it matched."
        elif reason == "wrong_shape":
            message = (
                "A purchase on a credit card can't be part of a transfer \u2014 only "
                "a payment toward the card (shown as a negative amount) can."
            )
        elif not candidates:
            # Eligible, but genuinely nothing matched at the normal
            # amount/date rules. diagnose_no_match relaxes the OTHER
            # side's own eligibility filters to say whether that's
            # because nothing like it exists at all, or because a
            # matching row exists but is itself linked elsewhere,
            # excluded, or still pending review.
            message = diagnose_no_match(conn, acting.id, row, role)
        elif len(candidates) == 1:
            other = candidates[0]
            if role == "outflow":
                link_transfer(conn, row["id"], other["id"])
            else:
                link_transfer(conn, other["id"], row["id"])
            relink(conn, acting.id)
            conn.commit()
            linked_partner_id = other["id"]
            message = f"Linked to {other['account_name']} {other['date']} \u2014 {abs(other['amount']):.2f}."
        else:
            message = f"{len(candidates)} possible matches \u2014 resolve on the Transfers page."

    return templates.TemplateResponse(
        request, "_match_action.html",
        {
            "t": {"id": id, "transfer_pair_id": linked_partner_id},
            "review_status": "clean",
            "match_message": message,
            "acting_qs": _acting_qs(current, acting),
        },
    )
