"""Upload → Statements (SPEC.md sections 4 and 13): what the page shows besides its forms.

    strip_cards(conn, current, acting)   the accounts strip: one card per live, unarchived account (latest
                                         period, missing months, overdue, preferred method)
    missing_months(account, dates, on)   the gap warning of one account
    unify(statements, csv_imports)       one upload history: statements (PDF and QFX) and CSV imports
    parse_filters(params)                the history's filter chips (type, account, status) from the address
    filtered(rows, filters)              the rows the chips let through
    waiting(rows)                        "Waiting for you": statements to confirm, review or restart
    clean_layout(value)                  a CSV layout to save on an account (accounts.csv_layout), or None
    read_layout(text)                    the saved layout, or None
"""
from __future__ import annotations

import bisect
import calendar
import json
from datetime import date, timedelta

from . import ofx, statement_due

ALL_ACCEPT = ".pdf,application/pdf,.qfx,.ofx,.qbo,.csv,text/csv"
ACCEPT = {"pdf": ".pdf,application/pdf", "qfx": ".qfx,.ofx,.qbo", "csv": ".csv,text/csv", "both": ALL_ACCEPT}
METHOD_LABEL = {"both": "Any", "pdf": "PDF only", "csv": "CSV only", "qfx": "QFX only"}
KIND_LABEL = {"pdf": "PDF", "qfx": "QFX", "csv": "CSV"}

GAP_LOOK_BACK_MONTHS = 12        # how far back the gap warning looks
WAITING_SHOWN = 8                # rows of "Waiting for you" before "and N more"


# ------------------------------------------------------------------ the accounts strip

def _month_label(y: int, m: int, on: date) -> str:
    return calendar.month_abbr[m] if y == on.year else f"{calendar.month_abbr[m]} {y}"


def _parse(d: str | None) -> date | None:
    try:
        return date.fromisoformat(str(d)[:10])
    except (TypeError, ValueError):
        return None


def missing_months(account, dates: list[date], on: date) -> list[str]:
    """The months with no uploaded transactions between the account's first and latest upload ("Aug", "Dec 2025"),
    oldest first, at most GAP_LOOK_BACK_MONTHS back.

    With an expected statement date (SPEC.md section 27) each statement's own span counts: cycle Ek runs from the
    day after Ek-1 to Ek and is missing when no uploaded transaction falls in it (labelled by Ek's month). The latest
    cycle is left out — that one is statement_due's overdue notice. Without one, a calendar month with nothing in it
    between the first and the newest month that has something counts as missing."""
    if not dates:
        return []
    dates = sorted(dates)
    first, last = dates[0], dates[-1]
    if statement_due.is_set(account):
        start = max(first, on - timedelta(days=31 * (GAP_LOOK_BACK_MONTHS + 1)))
        ends = statement_due.expected_dates(account, start - timedelta(days=statement_due.LOOK_BACK_DAYS), on)
        out = []
        for i in range(1, len(ends) - 1):                  # the latest cycle (ends[-1]) is the overdue notice's
            lo, hi = ends[i - 1], ends[i]
            if hi <= first or hi < start:                  # before the first upload, or too long ago
                continue
            k = bisect.bisect_right(dates, lo)               # the first date after lo
            if k >= len(dates) or dates[k] > hi:
                out.append(_month_label(hi.year, hi.month, on))
        return out
    months = {(d.year, d.month) for d in dates}
    lo = first.year * 12 + first.month - 1
    hi = last.year * 12 + last.month - 1
    lo = max(lo, hi - GAP_LOOK_BACK_MONTHS)
    return [_month_label(i // 12, i % 12 + 1, on) for i in range(lo, hi + 1) if (i // 12, i % 12 + 1) not in months]


def gap_text(missing: list[str]) -> str:
    if not missing:
        return ""
    if len(missing) <= 2:
        return ", ".join(missing) + " missing"
    return f"{len(missing)} months missing"


def strip_cards(conn, current, acting, on: date | None = None) -> list[dict]:
    """One card per live, unarchived account of the acting person, by name."""
    on = on or statement_due.today()
    accounts = conn.execute(
        "SELECT * FROM accounts WHERE user_id = ? AND deleted_at IS NULL AND is_archived = 0 "
        "ORDER BY name COLLATE NOCASE", (acting.id,)).fetchall()
    dates: dict[int, list[date]] = {}
    for r in conn.execute(
            "SELECT DISTINCT account_id, date FROM transactions WHERE user_id = ? AND deleted_at IS NULL "
            "AND (statement_id IS NOT NULL OR csv_import_id IS NOT NULL)", (acting.id,)):
        d = _parse(r["date"])
        if d is not None:
            dates.setdefault(r["account_id"], []).append(d)
    period_end = {r["account_id"]: _parse(r["p"]) for r in conn.execute(
        "SELECT account_id, MAX(period_end) AS p FROM statements WHERE user_id = ? AND deleted_at IS NULL "
        "AND status != 'error' AND period_end IS NOT NULL GROUP BY account_id", (acting.id,))}
    show_due = statement_due.enabled() and statement_due.can_see(current, acting)
    cards = []
    for a in accounts:
        ds = dates.get(a["id"], [])
        latest = max([d for d in (max(ds) if ds else None, period_end.get(a["id"])) if d is not None], default=None)
        missing = missing_months(a, ds, on)
        due_text, due_class = "", ""
        if show_due:
            st = statement_due.cycle_status(conn, a, on)
            if st and st["status"] in ("overdue", "due"):
                due_text, due_class = statement_due.column_label(st)
                if st["status"] == "due":
                    due_text = "Due today"
        pref = a["preferred_upload_method"] or "both"
        cards.append({
            "id": a["id"], "name": a["name"], "type": a["type"], "last4": a["account_number_last4"],
            "latest": latest, "latest_label": f"up to {calendar.month_abbr[latest.month]} {latest.year}" if latest else "",
            "missing": missing, "gap_text": gap_text(missing),
            "due_text": due_text, "due_class": due_class,
            "pref": pref, "pref_label": METHOD_LABEL.get(pref, "Any"), "accept": ACCEPT.get(pref, ALL_ACCEPT),
        })
    return cards


# ------------------------------------------------------------------ one upload history

def statement_state(r) -> str:
    """processing | awaiting (confirm against the PDF) | review | error | complete — the same split as
    _status_fragment.html."""
    if r["status"] == "processing":
        return "processing"
    if r["status"] == "error":
        return "error"
    if r["status"] == "pending_review":
        if r["needs_confirmation"] and not r["confirmed_at"] and not r["flagged_count"]:
            return "awaiting"
        return "review"
    return "complete"


def csv_state(r) -> str:
    if r["fatal_error"]:
        return "error"
    if r["error_count"] or r["duplicate_count"] or r["skipped_status_count"]:
        return "partial"
    return "complete"


def unify(statements: list[dict], csv_imports: list[dict]) -> list[dict]:
    """Every statement and CSV import as one kind of history row: kind (pdf | qfx | csv), when, file, account,
    period, count, state, plus the original row (`s` for a statement, `c` for a CSV import) for the template."""
    out = []
    for s in statements:
        kind = "qfx" if s.get("source") == "qfx" else "pdf"
        period = (ofx.period_label(s.get("period_start"), s.get("period_end")) if kind == "qfx" else "") \
            or ofx.period_label(s.get("first_date"), s.get("last_date"))
        out.append({"kind": kind, "id": s["id"], "when": s["uploaded_at"], "file": s.get("original_filename"),
                    "account": s.get("account_name"), "account_ids": [s.get("account_id")],
                    "period": period, "first": s.get("first_date"), "count": s.get("txn_count") or 0,
                    "state": statement_state(s), "s": s})
    for c in csv_imports:
        out.append({"kind": "csv", "id": c["id"], "when": c["imported_at"], "file": c.get("original_filename"),
                    "account": c.get("account_name"), "account_ids": c.get("account_ids") or [],
                    "period": ofx.period_label(c.get("first_date"), c.get("last_date")), "first": c.get("first_date"),
                    "count": c.get("row_count") or 0, "state": csv_state(c), "c": c})
    return out


TYPE_FILTERS = [("", "All types"), ("pdf", "PDF"), ("qfx", "QFX"), ("csv", "CSV")]
STATUS_FILTERS = [("", "Any status"), ("waiting", "Waiting for you"), ("processing", "Being read"),
                  ("done", "Done"), ("error", "Failed")]
_STATUS_OF = {"waiting": {"awaiting", "review"}, "processing": {"processing"}, "done": {"complete", "partial"},
              "error": {"error"}}
SORT_COLUMNS = [("when", "Uploaded"), ("file", "File"), ("account", "Account"), ("period", "Period"),
                ("count", "Transactions"), ("status", "Status"), ("type", "Type")]


def _lower(text):
    return text.lower() if text else None


SORT_KEYS = {
    "when": lambda r: r["when"],
    "file": lambda r: _lower(r["file"]),
    "account": lambda r: _lower(r["account"]),
    "period": lambda r: r["first"],
    "count": lambda r: r["count"],
    "status": lambda r: r["state"],
    "type": lambda r: r["kind"],
}


def parse_filters(params) -> dict:
    """{"type", "account", "status"} from h_type / h_account / h_status; anything unknown is "" (all)."""
    t = (params.get("h_type") or "").strip()
    s = (params.get("h_status") or "").strip()
    a = (params.get("h_account") or "").strip()
    return {"type": t if t in dict(TYPE_FILTERS) else "", "status": s if s in dict(STATUS_FILTERS) else "",
            "account": a if a.isdigit() else ""}


def _waiting_state(r) -> bool:
    return r["kind"] != "csv" and r["state"] in ("awaiting", "review", "error")


def filtered(rows: list[dict], f: dict) -> list[dict]:
    out = rows
    if f["type"]:
        out = [r for r in out if r["kind"] == f["type"]]
    if f["account"]:
        aid = int(f["account"])
        out = [r for r in out if aid in r["account_ids"]]
    if f["status"] == "waiting":
        out = [r for r in out if _waiting_state(r)]
    elif f["status"]:
        out = [r for r in out if r["state"] in _STATUS_OF[f["status"]]]
    return out


def waiting(rows: list[dict]) -> list[dict]:
    """Statements waiting on the person — awaiting confirmation, needs review, failed — newest first. A failed CSV
    import isn't here: there's nothing to do with it but import the file again."""
    return sorted((r for r in rows if _waiting_state(r)), key=lambda r: r["when"] or "", reverse=True)


def account_filters(rows: list[dict], accounts) -> list[tuple[str, str]]:
    """The account chips: every live account the history has a row for (or that is picked), by name."""
    seen = {aid for r in rows for aid in r["account_ids"] if aid is not None}
    return [("", "All accounts")] + [(str(a["id"]), a["name"]) for a in
                                     sorted(accounts, key=lambda a: a["name"].casefold()) if a["id"] in seen]


# ------------------------------------------------------------------ the saved CSV layout

MAX_ROW = 100_000


def _row(value, low: int) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return n if low <= n <= MAX_ROW else None


def clean_layout(value) -> dict | None:
    """A layout from the page ({header_row, data_row, date_column, debit_credit}, as a dict or JSON text) made
    safe to store, or None when it isn't one: header_row a row number from 1, data_row below it (or none: the row
    after the header), date_column a column name (or none: worked out), debit_credit true or false."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    if not isinstance(value, dict):
        return None
    header = _row(value.get("header_row"), 1)
    if header is None:
        return None
    data = value.get("data_row")
    data = None if data in (None, "") else _row(data, 2)
    if data is not None and data <= header:
        return None
    col = value.get("date_column")
    col = str(col).strip()[:200] if col not in (None, "") else None
    return {"header_row": header, "data_row": data, "date_column": col or None,
            "debit_credit": bool(value.get("debit_credit"))}


def read_layout(text) -> dict | None:
    return clean_layout(text) if text else None


def layout_json(layout: dict | None) -> str | None:
    return json.dumps(layout, separators=(",", ":")) if layout else None
