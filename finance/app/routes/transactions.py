"""Transactions page, manual entry, per-row edits and bulk actions
(SPEC.md sections 8, 9, 10, 13). The Review queue lives in
routes/review.py and CSV import in routes/csv_import.py.
"""
import csv
import io
from pathlib import Path
from urllib.parse import urlencode
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from ..auth import User, get_current_user, get_acting_user
from ..categorize import UNCATEGORIZED, list_all_categories
from ..db import get_db
from ..version import APP_VERSION
from ..matching import cascade_unlink_on_delete
from ..datenorm import normalize_date
from ..parser.pipeline import finalize_if_ready
from .accounts import _acting_banner, _acting_qs, htmx_fragment_requested

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
templates.env.globals["app_version"] = APP_VERSION

MAX_NOTE_LENGTH = 1000  # generous for a short note; just a sanity bound, not a design constraint

def _insert_manual_transaction(
    conn, user_id: str, account_id: int, date: str, amount: str, description: str, category: str,
    *, category_source: str | None = "manual",
) -> tuple[bool, int | str]:
    """Validate and insert one hand-entered or CSV-imported transaction
    (SPEC.md section 10) — both paths share this, so a CSV row is
    checked exactly like a typed one. Returns (True, new_id) or
    (False, reason) with nothing written. The account must be the user's
    own, live and not archived.

    category_source: 'manual' for the form; the CSV import passes
    'csv_explicit' when the file had a Category, or None to leave the row
    for categorize_transactions. statement_id is NULL and review_status
    'clean' (there's no source document to check against).

    The date is normalised to ISO (app/datenorm.py) — every date filter
    and sort compares the stored string — and a date in neither accepted
    format fails just that row."""
    account = conn.execute(
        "SELECT id FROM accounts WHERE id = ? AND user_id = ? AND deleted_at IS NULL AND is_archived = 0",
        (account_id, user_id),
    ).fetchone()
    if account is None:
        return False, "Account not found (or archived)"

    date = date.strip()
    if not date:
        return False, "Date is required"
    normalized_date = normalize_date(date)
    if normalized_date is None:
        return False, f"Date \"{date}\" isn't in a recognized format (expected YYYY-MM-DD or MM/DD/YYYY)"
    date = normalized_date

    try:
        amount_value = round(float(amount), 2)
    except (TypeError, ValueError):
        return False, "Amount must be a number"
    if amount_value == 0:
        return False, "Amount can't be zero"

    description = description.strip()
    if not description:
        return False, "Description is required"

    category = category.strip() or UNCATEGORIZED

    cur = conn.execute(
        "INSERT INTO transactions "
        "(user_id, account_id, statement_id, date, amount, description, category, category_source, txn_type, review_status) "
        "VALUES (?, ?, NULL, ?, ?, ?, ?, ?, 'expense', 'clean')",
        (user_id, account_id, date, amount_value, description, category, category_source),
    )
    return True, cur.lastrowid


@router.post("/transaction-manual")
def create_manual_transaction(
    request: Request,
    account_id: int = Form(...),
    date: str = Form(...),
    amount: str = Form(...),
    description: str = Form(...),
    category: str = Form(""),
    next: str = Form("uploads"),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """Manual transaction entry (SPEC.md section 10) — the "add
    one by hand" escape hatch so a delayed statement or a cash receipt
    doesn't block you, and a provider changing their PDF format
    overnight doesn't leave you stuck. Same shape as a parsed
    transaction (date/amount/description/category on an existing
    account), just entered directly rather than extracted from a PDF.
    Lives on the Uploads page alongside statement upload and CSV import
    (moved there from the Transactions page) — `next` defaults to
    "uploads" accordingly, same pattern every other next-aware redirect
    in this app already uses."""
    with get_db() as conn:
        ok, result = _insert_manual_transaction(conn, acting.id, account_id, date, amount, description, category)
        if ok:
            conn.commit()
        else:
            return JSONResponse({"error": result}, status_code=400)

    qs = _acting_qs(current, acting)
    return RedirectResponse(url=f"{next}{'?' + qs if qs else ''}", status_code=303)


def _parsed_transaction_filters(
    q: str, account_id: str, category: str, txn_type: str, flow: str,
    start_date: str, end_date: str, min_amount: str, max_amount: str, desc: str, excluded: str = "",
):
    """Parses/strips every /transactions filter query param into its
    typed form, in one place — shared by list_transactions and the CSV
    export route (transactions-export) so a filtered export can never
    drift from what the page itself is showing for the same query
    string. Returns a dict of the cleaned values, keyed by the same
    names used as this route's own local variables."""
    return {
        "q": q.strip(),
        "account_id_int": int(account_id) if account_id.strip().isdigit() else None,
        "category": category.strip(),
        "txn_type": txn_type.strip(),
        "flow": flow.strip(),
        "start_date": start_date.strip(),
        "end_date": end_date.strip(),
        "min_amount": min_amount.strip(),
        "max_amount": max_amount.strip(),
        "desc": desc.strip(),
        # "only" = just the rows marked Exclude, "hide" = leave them out, else both
        "excluded": excluded.strip() if excluded.strip() in ("only", "hide") else "",
    }


def _build_transaction_conditions(user_id: str, f: dict) -> tuple[list[str], list]:
    """Builds the WHERE-clause conditions + params for a filtered
    transaction query from an already-parsed filter dict (see
    _parsed_transaction_filters) — the single source of truth for what
    "the current filter" means, shared by the Transactions page itself
    and its CSV export, so exporting always exports exactly the rows
    currently on screen, never a second, slightly different query.

    q searches transaction notes only (SPEC.md section 8) —
    note text is the one free-text field a user writes themselves.
    `desc` (section 13's combinable search) is a separate substring
    filter on the transaction's own description text, distinct from a
    note.

    category/flow/txn_type/start_date/end_date (Phase 4) double as the
    dashboard's drill-down filters — a dashboard category row or the
    Income card links here with these set, so "175.00 in Groceries" and
    "click through" always agree on which rows they're counting.

    flow is deliberately NOT the same thing as filtering on the
    txn_type column, and takes priority over it when both are given.
    Found from real usage: the dashboard's income/spend totals sum by
    amount SIGN (amount > 0 / < 0) plus "not a transfer, not excluded,
    fully imported" — they never filter on txn_type = 'income' or
    'expense' literally, because ordinary imported transactions are
    never actually given that classification (txn_type defaults to
    'expense' for every row regardless of amount sign — see
    parser/pipeline.py's _insert_transaction — and only gets set to
    'income'/'transfer' by the matching engine in app/matching.py).
    Linking the Income card to `txn_type=income` therefore matched
    almost nothing even though the card itself showed a real total.
    flow=income/expense reproduces the dashboard's actual counting rule
    exactly (see routes/dashboard.py's `counted_where`), so a drill-down
    always shows precisely the rows a dashboard figure summed.

    min_amount/max_amount (section 13) filter on the transaction's
    MAGNITUDE (ABS(t.amount)), not the raw signed value — a checking
    purchase is negative and a credit-card purchase is positive in this
    app's sign convention (app/matching.py's docstring), so "show me
    everything between $50 and $200" has to mean the size of the
    transaction, not its DB sign, to behave the same way across account
    types without the user needing to know that convention at all."""
    conditions = ["t.user_id = ?", "t.deleted_at IS NULL"]
    params: list = [user_id]

    if f["account_id_int"]:
        conditions.append("t.account_id = ?")
        params.append(f["account_id_int"])

    if f["q"]:
        # note is only ever non-NULL on review_status = 'clean' rows (the
        # DB's own CHECK constraint guarantees this — migrations/0008),
        # so this naturally never surfaces a still-flagged transaction
        # without needing a separate review_status filter here.
        conditions.append("t.note IS NOT NULL AND t.note LIKE ?")
        params.append(f"%{f['q']}%")

    if f["desc"]:
        conditions.append("t.description LIKE ?")
        params.append(f"%{f['desc']}%")

    if f["category"]:
        conditions.append("t.category = ?")
        params.append(f["category"])

    if f["flow"] == "income":
        # Restricted to checking/savings, matching the dashboard's own
        # income math exactly (routes/dashboard.py) — a positive-amount
        # row on a credit_card account is a payment RECEIVED on the
        # card, never real income, whether or not it's currently linked
        # as a transfer.
        conditions.append(_flow_sql(
            "t.amount > 0 AND t.txn_type != 'transfer' AND t.is_excluded = 0 "
            "AND t.review_status = 'clean' AND a.type IN ('checking', 'savings')", f))
    elif f["flow"] == "expense":
        # Account-type aware, matching the dashboard's own spend math
        # exactly (routes/dashboard.py): a negative amount on checking/
        # savings, or a POSITIVE amount on a credit_card account — this
        # app's credit-card sign convention is the opposite of a
        # checking/savings account's (see app/matching.py's docstring).
        conditions.append(_flow_sql(
            "((a.type = 'credit_card' AND t.amount > 0) OR (a.type != 'credit_card' AND t.amount < 0)) "
            "AND t.txn_type != 'transfer' AND t.is_excluded = 0 AND t.review_status = 'clean'", f))
    elif f["txn_type"] in ("expense", "income", "transfer"):
        conditions.append("t.txn_type = ?")
        params.append(f["txn_type"])

    if f["excluded"] == "only":
        conditions.append("t.is_excluded = 1")
    elif f["excluded"] == "hide":
        conditions.append("t.is_excluded = 0")

    if f["start_date"]:
        conditions.append("t.date >= ?")
        params.append(f["start_date"])

    if f["end_date"]:
        conditions.append("t.date <= ?")
        params.append(f["end_date"])

    if f["min_amount"]:
        try:
            min_amount_value = float(f["min_amount"])
        except ValueError:
            pass  # a garbage value in the query string is silently ignored, not a 400 on a GET/list route
        else:
            conditions.append("ABS(t.amount) >= ?")
            params.append(min_amount_value)
    if f["max_amount"]:
        try:
            max_amount_value = float(f["max_amount"])
        except ValueError:
            pass
        else:
            conditions.append("ABS(t.amount) <= ?")
            params.append(max_amount_value)

    return conditions, params


def _flow_sql(sql: str, f: dict) -> str:
    """Money in / Money out normally leave out excluded rows (like the dashboard); with
    "Excluded only" they are exactly what's wanted, so that part is dropped."""
    return sql.replace(" AND t.is_excluded = 0", "") if f["excluded"] == "only" else sql


def _transactions_filter_qs(f: dict, searched: bool = False) -> str:
    """The querystring for the filters in `f`, using this route's param names
    (acting_qs is added by callers). Its truthiness is the gate for running
    the query (section 13: nothing is queried on a plain page load), and it
    is what the bulk-action bar returns to afterwards.

    Every "All ..." option submits a blank value, so a Search with nothing
    else chosen would look exactly like a plain page load; the Search button
    therefore always sends searched=1, and when every real filter is blank
    this returns "searched=1" instead of an empty string."""
    params = {
        "q": f["q"], "account_id": f["account_id_int"] or "", "category": f["category"],
        "txn_type": f["txn_type"], "flow": f["flow"], "start_date": f["start_date"],
        "end_date": f["end_date"], "min_amount": f["min_amount"], "max_amount": f["max_amount"],
        "desc": f["desc"], "excluded": f["excluded"],
    }
    qs = {k: v for k, v in params.items() if v}
    if not qs and searched:
        qs = {"searched": "1"}
    return urlencode(qs)


@router.get("/transactions")
def list_transactions(
    request: Request,
    q: str = "",
    account_id: str = "",
    category: str = "",
    txn_type: str = "",
    flow: str = "",
    start_date: str = "",
    end_date: str = "",
    min_amount: str = "",
    max_amount: str = "",
    desc: str = "",
    excluded: str = "",
    searched: str = "",
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """See _build_transaction_conditions for what each filter means.
    account_id is a string, not int: "All accounts" submits "" and FastAPI
    would reject that with a 422. searched: see _transactions_filter_qs."""
    f = _parsed_transaction_filters(q, account_id, category, txn_type, flow, start_date, end_date, min_amount, max_amount, desc,
                                    excluded)
    q, account_id_int = f["q"], f["account_id_int"]
    category, txn_type, flow = f["category"], f["txn_type"], f["flow"]
    start_date, end_date = f["start_date"], f["end_date"]
    conditions, params = _build_transaction_conditions(acting.id, f)
    filter_qs = _transactions_filter_qs(f, searched=bool(searched))
    has_filter = bool(filter_qs)

    with get_db() as conn:
        # Only query once a filter (or an explicit Search) is present; the
        # accounts and categories for the dropdowns are cheap and always loaded.
        transactions = conn.execute(
            f"""
            SELECT t.*, a.name AS account_name, a.type AS account_type
            FROM transactions t
            JOIN accounts a ON a.id = t.account_id
            WHERE {' AND '.join(conditions)}
            ORDER BY t.date DESC, t.id DESC
            """,
            params,
        ).fetchall() if has_filter else []
        accounts = conn.execute(
            "SELECT id, name, type FROM accounts WHERE user_id = ? AND deleted_at IS NULL AND is_archived = 0",
            (acting.id,),
        ).fetchall()
        all_categories = list_all_categories(conn, acting.id)

    # Total for whatever's currently filtered/displayed (Phase 4/13) —
    # computed over the exact same rows just fetched, so it can never
    # drift from what's actually shown in the table below it. For the
    # `flow=expense` drill-down specifically, this uses the same
    # account-type-aware magnitude as the dashboard's own Spend total
    # (routes/dashboard.py's spend_amount_expr) rather than a raw signed
    # sum — a credit-card purchase (positive there) and a checking
    # purchase (negative there) must both add POSITIVELY to "how much
    # was spent", or this total would silently disagree with the exact
    # dashboard figure that linked here in the first place (a raw sum
    # would partially cancel the two signs instead of adding them).
    # Every other view (flow=income, or no flow at all — just browsing/
    # filtering by account, category, or date) sums the amount column
    # exactly as displayed: flow=income is already all-positive/
    # checking-savings-only so a raw sum already matches the dashboard,
    # and a plain browse has no dashboard figure to match at all, so the
    # most transparent total is simply what the visible numbers add up
    # to.
    if flow == "expense":
        total = sum(
            (t["amount"] if t["account_type"] == "credit_card" else -t["amount"])
            for t in transactions
        )
    else:
        total = sum(t["amount"] for t in transactions)

    # The CSV export button lives inside the #txn-filters search form
    # itself now (transactions_list.html) as a native form submit
    # (formaction="transactions-export"), so it always exports exactly
    # whatever is currently typed/selected in that form — no export_qs
    # needs computing/threading through this route anymore.
    context = {
        "user": acting, "transactions": transactions, "accounts": accounts,
        "categories": all_categories,
        "total": total,
        "q": q, "selected_account_id": account_id_int,
        "filter_category": category, "filter_txn_type": txn_type, "filter_flow": flow,
        "filter_start_date": start_date, "filter_end_date": end_date,
        "filter_min_amount": min_amount, "filter_max_amount": max_amount, "filter_desc": desc,
        "filter_excluded": f["excluded"],
        "filter_qs": filter_qs, "no_filter_yet": not has_filter,
        "acting_as_banner": _acting_banner(current, acting),
        "acting_qs": _acting_qs(current, acting),
        "active_page": "transactions",
    }

    # The filter inputs (search box, account select) are htmx-driven —
    # hx-get back to this same route, swapping just #transactions-table-wrap
    # (hx-swap="outerHTML"). htmx replaces the target with whatever HTML
    # comes back verbatim; it does NOT extract a matching element from a
    # full page. Returning the full transactions_list.html (nav, header,
    # filter form and all) on every filter change was nesting an entire
    # second copy of the page inside the table wrapper on every keystroke/
    # selection. A direct page load/refresh (no HX-Request header, or a Back/Forward
    # history restore — see htmx_fragment_requested) still
    # gets the full page; an htmx-driven filter change gets just the
    # fragment it actually asked to swap in.
    if htmx_fragment_requested(request):
        return templates.TemplateResponse(request, "_transactions_table.html", context)
    return templates.TemplateResponse(request, "transactions_list.html", context)


_CSV_FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value: str) -> str:
    """CSV/formula injection guard: spreadsheets run a cell starting with
    = + - @ (or tab/CR) as a formula, so such a cell gets a leading quote
    (the OWASP mitigation). Only the exported bytes change."""
    return "'" + value if value.startswith(_CSV_FORMULA_TRIGGERS) else value


@router.get("/transactions-export")
def export_transactions_csv(
    request: Request,
    q: str = "",
    account_id: str = "",
    category: str = "",
    txn_type: str = "",
    flow: str = "",
    start_date: str = "",
    end_date: str = "",
    min_amount: str = "",
    max_amount: str = "",
    desc: str = "",
    excluded: str = "",
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """CSV export (SPEC.md section 10) on the exact same
    filtered set list_transactions would show — same
    _parsed_transaction_filters/_build_transaction_conditions helpers,
    same query, so "export" can never silently disagree with what's on
    screen. stdlib csv, zero new dependencies, per section 10 and
    section 12's tech-stack constraint."""
    f = _parsed_transaction_filters(q, account_id, category, txn_type, flow, start_date, end_date, min_amount, max_amount, desc,
                                    excluded)
    conditions, params = _build_transaction_conditions(acting.id, f)

    with get_db() as conn:
        transactions = conn.execute(
            f"""
            SELECT t.date, a.name AS account_name, a.type AS account_type, t.description,
                   t.amount, t.category, t.txn_type, t.is_excluded, t.note
            FROM transactions t
            JOIN accounts a ON a.id = t.account_id
            WHERE {' AND '.join(conditions)}
            ORDER BY t.date DESC, t.id DESC
            """,
            params,
        ).fetchall()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Date", "Account", "Account Type", "Description", "Amount", "Category", "Type", "Excluded", "Note"])
    for t in transactions:
        writer.writerow([
            t["date"], _csv_safe(t["account_name"]), t["account_type"], _csv_safe(t["description"]),
            f"{t['amount']:.2f}", _csv_safe(t["category"] or ""), t["txn_type"],
            "yes" if t["is_excluded"] else "no", _csv_safe(t["note"] or ""),
        ])
    buffer.seek(0)

    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=transactions.csv"},
    )


@router.post("/transaction-note")
def set_transaction_note(
    request: Request,
    id: int = Form(...),
    note: str = Form(""),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """Returns the same _note_field.html fragment the list page includes
    per row (htmx outerHTML swap on the <form> itself) — same pattern as
    /statement-status's _status_fragment.html, not a JSON response.

    Scoped to the acting user's own transaction throughout — never trusts
    the id alone. The review_status gate is enforced twice: the DB's own
    CHECK (migrations/0008 — the real guarantee, holds regardless of
    which code path writes here) and this explicit check, purely so a
    rejected save gets a clear inline message instead of a raw
    IntegrityError. In practice a normal user can't even reach this
    branch through the UI — a non-'clean' row never renders the note
    form to submit in the first place (see _note_field.html) — so this
    mainly guards a hand-crafted request."""
    note = note.strip() or None
    note_error = None

    with get_db() as conn:
        row = conn.execute(
            "SELECT id, review_status, note FROM transactions WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
            (id, acting.id),
        ).fetchone()
        if row is None:
            return templates.TemplateResponse(request, "not_found.html", {"user": acting}, status_code=404)

        display_note = note  # what was just typed — shown back on error, not silently reverted
        if note is not None and len(note) > MAX_NOTE_LENGTH:
            note_error = f"Note is too long (max {MAX_NOTE_LENGTH} characters)."
        elif note is not None and row["review_status"] != "clean":
            note_error = "Notes are only available on fully imported transactions."
        else:
            conn.execute("UPDATE transactions SET note = ? WHERE id = ?", (note, id))
            conn.commit()

    return templates.TemplateResponse(
        request, "_note_field.html",
        {
            "t": {"id": row["id"], "review_status": row["review_status"], "note": display_note},
            "note_error": note_error,
            "acting_qs": _acting_qs(current, acting),
        },
    )


@router.post("/transaction-category")
def set_transaction_category(
    request: Request,
    id: int = Form(...),
    category: str = Form(...),
    next: str = Form("transactions"),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """Manual category edit (SPEC.md section 9) — allowed whatever the
    review_status. Just the category: rules are made on the Categories page."""
    if not category.strip():
        return JSONResponse({"error": "Category is required"}, status_code=400)

    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM transactions WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
            (id, acting.id),
        ).fetchone()
        if row is None:
            return templates.TemplateResponse(request, "not_found.html", {"user": acting}, status_code=404)

        # A direct human edit is now the reason for this category.
        conn.execute(
            "UPDATE transactions SET category = ?, category_source = 'manual', "
            "category_matched_pattern = NULL WHERE id = ?",
            (category.strip(), id),
        )
        conn.commit()

    qs = _acting_qs(current, acting)
    return RedirectResponse(url=f"{next}{'?' + qs if qs else ''}", status_code=303)


@router.post("/transaction-exclude")
def set_transaction_exclude(
    request: Request,
    id: int = Form(...),
    is_excluded: str = Form(""),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """Toggles the dashboard-exclusion flag (SPEC.md section 8's
    `is_excluded`) — the row stays visible everywhere exactly as before;
    this only controls whether it's counted in dashboard totals (see
    routes/dashboard.py). Allowed regardless of review_status, same as
    category — "don't count this" is useful on a still-flagged row too,
    unlike notes.

    Returns the same _exclude_field.html fragment the table includes per
    row (htmx outerHTML swap on the <form> itself), same pattern as
    _note_field.html/_category_field.html. A plain checkbox omits its
    field entirely when unchecked, so is_excluded is read as a Form
    string rather than bool and treated as "1" == checked."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM transactions WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
            (id, acting.id),
        ).fetchone()
        if row is None:
            return templates.TemplateResponse(request, "not_found.html", {"user": acting}, status_code=404)

        excluded = 1 if is_excluded == "1" else 0
        conn.execute("UPDATE transactions SET is_excluded = ? WHERE id = ?", (excluded, id))
        conn.commit()

    return templates.TemplateResponse(
        request, "_exclude_field.html",
        {
            "t": {"id": id, "is_excluded": excluded},
            "acting_qs": _acting_qs(current, acting),
        },
    )


@router.post("/transactions-bulk")
def bulk_action(
    request: Request,
    ids: str = Form(...),
    action: str = Form(...),
    category: str = Form(""),
    next: str = Form("transactions"),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """Bulk actions (SPEC.md sections 7, 13): approve, categorize,
    exclude, un-exclude, delete. `ids` is a comma-joined list of ids. Every
    UPDATE is scoped by user_id, so a tampered list can't reach other users."""
    if action not in ("approve", "categorize", "delete", "exclude", "unexclude"):
        return JSONResponse({"error": "Unknown action"}, status_code=400)
    try:
        id_list = [int(x) for x in ids.split(",") if x.strip()]
    except ValueError:
        return JSONResponse({"error": "Invalid transaction ids"}, status_code=400)
    if not id_list:
        return JSONResponse({"error": "No transactions selected"}, status_code=400)

    placeholders = ",".join("?" for _ in id_list)
    with get_db() as conn:
        if action == "approve":
            # Fetch affected statement ids BEFORE clearing review_status,
            # so finalize_if_ready below can check each one —
            # after the UPDATE, "which statements did these rows belong
            # to" is unchanged, but doing it in this order keeps the two
            # steps clearly separate rather than relying on that.
            statement_ids = [
                r["statement_id"] for r in conn.execute(
                    f"SELECT DISTINCT statement_id FROM transactions "
                    f"WHERE id IN ({placeholders}) AND user_id = ? AND deleted_at IS NULL",
                    (*id_list, acting.id),
                ).fetchall()
            ]
            conn.execute(
                f"UPDATE transactions SET review_status = 'clean', flag_reason = NULL "
                f"WHERE id IN ({placeholders}) AND user_id = ? AND deleted_at IS NULL",
                (*id_list, acting.id),
            )
            conn.commit()
            for sid in statement_ids:
                finalize_if_ready(conn, sid)
            conn.commit()
        elif action == "categorize":
            if not category.strip():
                return JSONResponse({"error": "No category selected"}, status_code=400)
            # A bulk human override is now the reason for each row's category.
            conn.execute(
                f"UPDATE transactions SET category = ?, category_source = 'manual', "
                f"category_matched_pattern = NULL "
                f"WHERE id IN ({placeholders}) AND user_id = ? AND deleted_at IS NULL",
                (category.strip(), *id_list, acting.id),
            )
            conn.commit()
        elif action == "exclude":
            conn.execute(
                f"UPDATE transactions SET is_excluded = 1 "
                f"WHERE id IN ({placeholders}) AND user_id = ? AND deleted_at IS NULL",
                (*id_list, acting.id),
            )
            conn.commit()
        elif action == "unexclude":
            conn.execute(
                f"UPDATE transactions SET is_excluded = 0 "
                f"WHERE id IN ({placeholders}) AND user_id = ? AND deleted_at IS NULL",
                (*id_list, acting.id),
            )
            conn.commit()
        elif action == "delete":
            # Phase 4 (SPEC.md section 11) — before soft-deleting,
            # revert the surviving side of any transfer pair this batch is
            # breaking, so a routine delete can't silently leave a
            # transfer-typed row with a dangling transfer_pair_id, or leave
            # the surviving partner mis-classified as a transfer with
            # nothing on the other end.
            cascade_unlink_on_delete(conn, acting.id, id_list)
            conn.execute(
                f"UPDATE transactions SET deleted_at = datetime('now') "
                f"WHERE id IN ({placeholders}) AND user_id = ? AND deleted_at IS NULL",
                (*id_list, acting.id),
            )
            conn.commit()

    # `next` may already carry a querystring (the filtered view to return to).
    qs = _acting_qs(current, acting)
    separator = "&" if "?" in next else "?"
    return RedirectResponse(url=f"{next}{separator + qs if qs else ''}", status_code=303)


@router.get("/transactions-accounts")
def transactions_by_account(
    request: Request,
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """Transactions → Accounts tab (SPEC.md section 13): each live, unarchived account with its
    latest live transaction and preferred upload method — which accounts are due a new statement.
    Oldest first, accounts with no transactions at the top."""
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT a.id, a.name, a.type, a.bank_name, a.account_number_last4, a.preferred_upload_method, a.website_url,
                   (SELECT MAX(t.date) FROM transactions t
                    WHERE t.account_id = a.id AND t.deleted_at IS NULL) AS latest_txn_date,
                   (SELECT COUNT(*) FROM transactions t
                    WHERE t.account_id = a.id AND t.deleted_at IS NULL) AS txn_count
            FROM accounts a
            WHERE a.user_id = ? AND a.deleted_at IS NULL AND a.is_archived = 0
            ORDER BY latest_txn_date IS NOT NULL, latest_txn_date, a.name COLLATE NOCASE
            """,
            (acting.id,),
        ).fetchall()
    return templates.TemplateResponse(request, "transactions_accounts.html", {
        "user": acting, "accounts": rows, "acting_as_banner": _acting_banner(current, acting),
        "acting_qs": _acting_qs(current, acting), "active_page": "transactions_accounts",
    })
