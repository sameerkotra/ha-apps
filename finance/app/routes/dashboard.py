"""Dashboard and Compare (SPEC.md section 16b).

Totals leave out transfers and excluded rows (section 8) and count only clean
(reviewed) rows, since a flagged amount may still change. Both are account-type
aware (section 8's sign convention):
- Income: positive rows on checking/savings only (a positive card row is a
  payment received, never income).
- Spend: negative rows on checking/savings, positive rows on credit cards.

Category rows and the cards link to Transactions with the same filters
(`flow`, category, dates), so a figure and its drill-down always agree.
The transfer auto-link sweep runs on load, and what is still ambiguous is
counted and shown, because an unlinked transfer looks like spend on one side
and income on the other until it is linked.
"""
import calendar
import re
from datetime import date, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates

from .. import ai_client as _ai_client

from ..auth import User, get_current_user, get_acting_user
from ..db import get_db
from ..version import APP_VERSION
from ..matching import _candidates_for, auto_link_unambiguous, find_transfer_candidates
from .accounts import _acting_banner, _acting_qs

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
templates.env.globals["app_version"] = APP_VERSION
templates.env.globals["ai_privacy"] = _ai_client.privacy


_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_YEAR_RE = re.compile(r"^\d{4}$")


def month_label(month: str) -> str:
    """'2026-03' -> 'March 2026'."""
    return f"{calendar.month_name[int(month[5:7])]} {month[:4]}"


def month_range(month: str) -> tuple[str, str]:
    # "-31" is a deliberate overestimate rather than the month's real last day (28-31) —
    # every next-month date starts with a lexicographically greater prefix regardless, so
    # this never actually reaches into the following month (dates are ISO strings).
    return f"{month}-01", f"{month}-31"


def year_range(year: str) -> tuple[str, str]:
    return f"{year}-01-01", f"{year}-12-31"


def resolve_period(period: str, today: date) -> tuple[str, str, str, str]:
    """(safe period key, start date, end date, label) for the Dashboard's Period filter.

    Keys: week, month (this month), last_month, year (this year), last_year, all,
    `m:YYYY-MM` (one specific month) and `y:YYYY` (one specific year). Anything else falls
    back to this month. start/end are ISO strings (both empty for "all"); the label finishes
    the sentence "No spending ..." on the empty state."""
    if period == "week":
        # Calendar week, Monday-Sunday — consistent with "month" meaning the calendar
        # month, not a rolling "last 7 days" window.
        start = today - timedelta(days=today.weekday())
        return "week", start.isoformat(), (start + timedelta(days=6)).isoformat(), "this week"
    if period == "all":
        return "all", "", "", "yet"
    if period == "last_month":
        month = (today.replace(day=1) - timedelta(days=1)).isoformat()[:7]
        return "last_month", *month_range(month), "last month"
    if period == "year":
        return "year", *year_range(str(today.year)), "this year"
    if period == "last_year":
        return "last_year", *year_range(str(today.year - 1)), "last year"
    if period.startswith("m:") and _MONTH_RE.match(period[2:]):
        return period, *month_range(period[2:]), "in " + month_label(period[2:])
    if period.startswith("y:") and _YEAR_RE.match(period[2:]):
        return period, *year_range(period[2:]), "in " + period[2:]
    return "month", *month_range(today.isoformat()[:7]), "this month"


def available_periods(conn, user_id: str) -> tuple[list[str], list[str]]:
    """(months, years) that have at least one transaction, newest first — what the
    "specific month / year" choices offer."""
    rows = conn.execute(
        "SELECT DISTINCT substr(date, 1, 7) AS m FROM transactions "
        "WHERE user_id = ? AND deleted_at IS NULL AND date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]*' ORDER BY m DESC",
        (user_id,),
    ).fetchall()
    months = [r["m"] for r in rows if _MONTH_RE.match(r["m"])]
    years = sorted({m[:4] for m in months}, reverse=True)
    return months, years


@router.get("/dashboard")
def dashboard(
    request: Request,
    period: str = "month",
    account_id: str = "",
    matched: str = "",
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    # Drill-down bounds for the transactions-page links below, and this
    # page's own period filter — every date in this app is stored as an
    # ISO 'YYYY-MM-DD' string, so plain >= / <= string comparison IS
    # real date comparison here, no date function needed.
    safe_period, drill_start_date, drill_end_date, period_label = resolve_period(period, date.today())

    # Optional account filter (SPEC.md section 13's combinable-
    # filter spirit, applied here too) — validated as a plain int and
    # otherwise ignored rather than erroring, same as every other
    # int-from-query-param spot in this app. No ownership check needed
    # beyond that: every query below already scopes by t.user_id = ?,
    # so an id belonging to another user just yields zero rows, never a
    # cross-user leak.
    safe_account_id = int(account_id) if account_id.strip().isdigit() else None

    # Every query below is written against transactions aliased "t" (and
    # most join accounts "a") — spend needs the account type now, same
    # as income already did, so this file uses one consistent shape
    # throughout instead of switching between prefixed/unprefixed.
    where = "t.user_id = ? AND t.deleted_at IS NULL AND t.review_status = 'clean'"
    params: list = [acting.id]
    if drill_start_date:
        where += " AND t.date >= ? AND t.date <= ?"
        params.append(drill_start_date)
        params.append(drill_end_date)
    if safe_account_id is not None:
        where += " AND t.account_id = ?"
        params.append(safe_account_id)

    counted_where = where + " AND t.txn_type != 'transfer' AND t.is_excluded = 0"

    income_where = (
        counted_where
        + " AND t.amount > 0 AND a.type IN ('checking', 'savings')"
    )

    # A "spend" row, and its magnitude, both depend on account type: a
    # negative amount on checking/savings, or a POSITIVE amount on a
    # credit_card account (see this file's module docstring).
    spend_filter = "((a.type = 'credit_card' AND t.amount > 0) OR (a.type != 'credit_card' AND t.amount < 0))"
    spend_amount_expr = "CASE WHEN a.type = 'credit_card' THEN t.amount ELSE -t.amount END"

    with get_db() as conn:
        accounts = conn.execute(
            "SELECT id, name, type FROM accounts WHERE user_id = ? AND deleted_at IS NULL "
            "AND is_archived = 0 ORDER BY name COLLATE NOCASE",
            (acting.id,),
        ).fetchall()

        spend_row = conn.execute(
            f"""
            SELECT COALESCE(SUM({spend_amount_expr}), 0) AS total
            FROM transactions t JOIN accounts a ON a.id = t.account_id
            WHERE {counted_where} AND {spend_filter}
            """,
            params,
        ).fetchone()
        income_row = conn.execute(
            f"""
            SELECT COALESCE(SUM(t.amount), 0) AS total FROM transactions t
            JOIN accounts a ON a.id = t.account_id
            WHERE {income_where}
            """,
            params,
        ).fetchone()
        category_rows = conn.execute(
            f"""
            SELECT t.category AS category, COALESCE(SUM({spend_amount_expr}), 0) AS total
            FROM transactions t JOIN accounts a ON a.id = t.account_id
            WHERE {counted_where} AND {spend_filter}
            GROUP BY t.category ORDER BY total DESC
            """,
            params,
        ).fetchall()
        months, years = available_periods(conn, acting.id)
        excluded_row = conn.execute(
            f"""
            SELECT COALESCE(SUM(ABS(t.amount)), 0) AS total, COUNT(*) AS n
            FROM transactions t
            WHERE {where} AND (t.txn_type = 'transfer' OR t.is_excluded = 1)
            """,
            params,
        ).fetchone()

        # Self-healing sweep (see this file's module docstring) — same
        # call the Transfers page already makes on every load. Anything
        # unambiguous links immediately; whatever's left with at least
        # one candidate is genuinely ambiguous and needs a human choice,
        # which is what unmatched_transfer_count reports below. Run
        # AFTER the totals above are computed so a pair this very sweep
        # just linked is reflected as excluded in the numbers the user
        # sees on this same page load, not one page load behind.
        auto_link_unambiguous(conn, acting.id)
        conn.commit()
        outflows, inflows = find_transfer_candidates(conn, acting.id)
        unmatched_transfer_count = sum(1 for o in outflows if _candidates_for(o, inflows))

    total_spend = spend_row["total"]
    selected_account_name = next(
        (a["name"] for a in accounts if a["id"] == safe_account_id), None
    ) if safe_account_id is not None else None
    categories = []
    for r in category_rows:
        pct = (r["total"] / total_spend * 100) if total_spend else 0.0
        categories.append({"category": r["category"], "total": r["total"], "pct": pct})

    return templates.TemplateResponse(
        request, "dashboard.html",
        {
            "user": acting,
            "period": safe_period,
            "period_label": period_label,
            "months": months, "years": years,
            "month_labels": {m: month_label(m) for m in months},
            "accounts": accounts,
            "selected_account_id": safe_account_id,
            "selected_account_name": selected_account_name,
            "total_spend": total_spend,
            "total_income": income_row["total"],
            "categories": categories,
            "excluded_total": excluded_row["total"],
            "excluded_count": excluded_row["n"],
            "drill_start_date": drill_start_date,
            "drill_end_date": drill_end_date,
            "unmatched_transfer_count": unmatched_transfer_count,
            "just_matched": int(matched) if matched.isdigit() else None,
            "acting_as_banner": _acting_banner(current, acting),
            "acting_qs": _acting_qs(current, acting),
            "active_page": "dashboard",
        },
    )


# ---------------------------------------------------------------- Compare tab

def period_totals(conn, user_id: str, start: str, end: str, account_id: int | None) -> dict:
    """Income and spend for one date range, each split by category — the same rules as the
    Overview totals (clean rows only; transfers and excluded rows left out; income only on
    checking/savings; a card purchase counts as spend)."""
    where = "t.user_id = ? AND t.deleted_at IS NULL AND t.review_status = 'clean' AND t.date >= ? AND t.date <= ?"
    params: list = [user_id, start, end]
    if account_id is not None:
        where += " AND t.account_id = ?"
        params.append(account_id)
    where += " AND t.txn_type != 'transfer' AND t.is_excluded = 0"
    spend_filter = "((a.type = 'credit_card' AND t.amount > 0) OR (a.type != 'credit_card' AND t.amount < 0))"
    spend_amount = "CASE WHEN a.type = 'credit_card' THEN t.amount ELSE -t.amount END"
    income_filter = "(t.amount > 0 AND a.type IN ('checking', 'savings'))"

    def by_category(condition: str, amount: str) -> dict[str, float]:
        rows = conn.execute(
            f"SELECT COALESCE(t.category, 'Uncategorized') AS category, SUM({amount}) AS total "
            f"FROM transactions t JOIN accounts a ON a.id = t.account_id "
            f"WHERE {where} AND {condition} GROUP BY COALESCE(t.category, 'Uncategorized')",
            params,
        ).fetchall()
        return {r["category"]: round(r["total"] or 0.0, 2) for r in rows}

    spend = by_category(spend_filter, spend_amount)
    income = by_category(income_filter, "t.amount")
    return {"spend_by_cat": spend, "income_by_cat": income,
            "spend": round(sum(spend.values()), 2), "income": round(sum(income.values()), 2)}


def compare_rows(a: dict[str, float], b: dict[str, float]) -> list[dict]:
    """One row per category present in either period: both amounts, the change and the
    change as a percentage of the first period (None when the first period had nothing)."""
    rows = []
    for category in set(a) | set(b):
        va, vb = a.get(category, 0.0), b.get(category, 0.0)
        rows.append({
            "category": category, "a": va, "b": vb, "change": round(vb - va, 2),
            "pct": round((vb - va) / va * 100, 1) if va else None,
        })
    rows.sort(key=lambda r: (-max(r["a"], r["b"]), r["category"]))
    return rows


def _default_periods(mode: str, today: date) -> tuple[str, str]:
    """(earlier, later) to compare when none is chosen: last month vs this month, or last year
    vs this year."""
    if mode == "year":
        return str(today.year - 1), str(today.year)
    this_month = today.isoformat()[:7]
    return (today.replace(day=1) - timedelta(days=1)).isoformat()[:7], this_month


@router.get("/dashboard-compare")
def dashboard_compare(
    request: Request,
    mode: str = "month",
    a: str = "",
    b: str = "",
    account_id: str = "",
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """Two months (or two years) side by side: income and spend, each divided into categories,
    with the change between them."""
    mode = "year" if mode == "year" else "month"
    valid = _YEAR_RE if mode == "year" else _MONTH_RE
    default_a, default_b = _default_periods(mode, date.today())
    a = a if valid.match(a) else default_a
    b = b if valid.match(b) else default_b
    to_range = year_range if mode == "year" else month_range
    label = (lambda p: p) if mode == "year" else month_label
    safe_account_id = int(account_id) if account_id.strip().isdigit() else None

    with get_db() as conn:
        accounts = conn.execute(
            "SELECT id, name, type FROM accounts WHERE user_id = ? AND deleted_at IS NULL "
            "AND is_archived = 0 ORDER BY name COLLATE NOCASE",
            (acting.id,),
        ).fetchall()
        months, years = available_periods(conn, acting.id)
        (a_start, a_end), (b_start, b_end) = to_range(a), to_range(b)
        totals_a = period_totals(conn, acting.id, a_start, a_end, safe_account_id)
        totals_b = period_totals(conn, acting.id, b_start, b_end, safe_account_id)

    options = years if mode == "year" else months
    options = sorted(set(options) | {a, b}, reverse=True)

    def summary(name: str) -> dict:
        va, vb = totals_a[name], totals_b[name]
        return {"a": va, "b": vb, "change": round(vb - va, 2), "pct": round((vb - va) / va * 100, 1) if va else None}

    spend_rows = compare_rows(totals_a["spend_by_cat"], totals_b["spend_by_cat"])
    income_rows = compare_rows(totals_a["income_by_cat"], totals_b["income_by_cat"])
    net_a, net_b = round(totals_a["income"] - totals_a["spend"], 2), round(totals_b["income"] - totals_b["spend"], 2)

    return templates.TemplateResponse(
        request, "dashboard_compare.html",
        {
            "user": acting, "mode": mode, "a": a, "b": b, "label_a": label(a), "label_b": label(b),
            "options": [(o, label(o)) for o in options],
            "accounts": accounts, "selected_account_id": safe_account_id,
            "income": summary("income"), "spend": summary("spend"),
            "net": {"a": net_a, "b": net_b, "change": round(net_b - net_a, 2)},
            "spend_rows": spend_rows, "income_rows": income_rows,
            "spend_max": max([max(r["a"], r["b"]) for r in spend_rows] or [0]),
            "income_max": max([max(r["a"], r["b"]) for r in income_rows] or [0]),
            "range_a": (a_start, a_end), "range_b": (b_start, b_end),
            "acting_as_banner": _acting_banner(current, acting),
            "acting_qs": _acting_qs(current, acting),
            "active_page": "dashboard",
        },
    )
