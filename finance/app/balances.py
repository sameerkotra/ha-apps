"""Balance over time and net worth (SPEC.md section 28).

Every figure comes from statements, never from adding up transactions: a statement's closing balance
(`statements.new_balance`: the PDF's printed New Balance, a QFX file's LEDGERBAL) at the statement's end date
(`period_end`, set for QFX; else its last live transaction's date; else the last day of `statement_period`). Only live
statements that didn't fail and have a balance count — one still awaiting confirmation or review does.

The app's sign convention (section 8) is kept: a checking or savings balance is what is held; a credit card's is
what is owed (positive = owed, negative = a credit, the way the balance check and the Household Assistant's
balances read it). Net worth is the cash accounts' balances minus what the cards owe.

    statement_points(conn, account_id)        [{date, balance, statement_id, source}] oldest first
    account_chart(account, points)            the account page's chart, its summary and its table rows
    net_worth(conn, user_id, today)           Home's Net worth card: total, per account, 12 month-ends
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta

from . import statement_due
from .charts import multi_series_chart

MONTHS = 12


def _month_end(y: int, m: int) -> date:
    return date(y, m, calendar.monthrange(y, m)[1])


def _iso(value) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def end_date(row) -> date | None:
    """A statement's end date: period_end (QFX), else its last transaction's date, else its month's last day."""
    d = _iso(row["period_end"]) or _iso(row["last_txn"])
    if d is not None:
        return d
    ym = statement_due.parse_month(row["statement_period"])
    return _month_end(*ym) if ym else None


def statement_points(conn, account_id: int) -> list[dict]:
    """The account's closing balances, oldest first; one per end date (the newest statement wins a tie)."""
    rows = conn.execute(
        "SELECT s.id, s.source, s.period_end, s.statement_period, s.new_balance, "
        "(SELECT MAX(t.date) FROM transactions t WHERE t.statement_id = s.id AND t.deleted_at IS NULL) AS last_txn "
        "FROM statements s WHERE s.account_id = ? AND s.deleted_at IS NULL AND s.status != 'error' "
        "AND s.new_balance IS NOT NULL ORDER BY s.id", (account_id,)).fetchall()
    by_date: dict[date, dict] = {}
    for r in rows:
        d = end_date(r)
        if d is None:
            continue
        by_date[d] = {"date": d, "balance": round(float(r["new_balance"]), 2), "statement_id": r["id"],
                      "source": r["source"]}
    return [by_date[d] for d in sorted(by_date)]


def date_label(d: date) -> str:
    return f"{calendar.month_abbr[d.month]} {d.day}, {d.year}"


def money(v: float) -> str:
    return f"{v:,.2f}"


def balance_at(points: list[dict], on: date) -> dict | None:
    """The last point on or before `on` (the balance carried forward), or None."""
    found = None
    for p in points:
        if p["date"] > on:
            break
        found = p
    return found


def summary(points: list[dict]) -> dict | None:
    """{latest, since, change} for the accessible text: the latest balance and its change since a year before it
    (the balance carried to that day; with less than a year of statements, since the first one)."""
    if not points:
        return None
    latest = points[-1]
    year_ago = balance_at(points, latest["date"] - timedelta(days=365))
    full_year = year_ago is not None
    base = year_ago or points[0]
    if base is latest:
        return {"latest": latest, "since": None, "change": None, "full_year": False}
    return {"latest": latest, "since": base, "change": round(latest["balance"] - base["balance"], 2),
            "full_year": full_year}


def is_card(account) -> bool:
    return account["type"] == "credit_card"


def series_label(account) -> str:
    return "Owed" if is_card(account) else "Balance"


def summary_text(account, sm: dict | None) -> str:
    """The chart's words: "Balance 1,234.56 on Mar 31, 2026; up 200.00 since a year before (1,034.56 on Mar 31,
    2025)." — a card's is "Owed …"."""
    if sm is None:
        return "No statement balances yet."
    latest = sm["latest"]
    text = f"{series_label(account)} {money(latest['balance'])} on {date_label(latest['date'])}"
    if sm["change"] is not None:
        c = sm["change"]
        move = "no change" if c == 0 else f"{'up' if c > 0 else 'down'} {money(abs(c))}"
        since = "a year before" if sm["full_year"] else "the first statement"
        text += f"; {move} since {since} ({money(sm['since']['balance'])} on {date_label(sm['since']['date'])})"
    tail = ("What the card owed at the end of each statement (below zero: a credit)." if is_card(account)
            else "What the account held at the end of each statement.")
    return f"{text}. {tail}"


def account_chart(account, points: list[dict]) -> dict:
    """{svg, summary, text, rows, label}: the Balance over time chart (newest last), or svg None with no balances."""
    label = series_label(account)
    sm = summary(points)
    out = {"svg": None, "summary": sm, "text": summary_text(account, sm), "rows": list(reversed(points)),
           "label": label, "card": is_card(account)}
    if points:
        out["svg"] = multi_series_chart(
            [date_label(p["date"]) for p in points],
            [{"label": label, "values": [p["balance"] for p in points], "css_class": "chart-cat-1"}],
            kind="line", label=f"{account['name']}: {'what is owed' if is_card(account) else 'balance'} per statement",
            decimals=0)
    return out


def _month_ends(today: date, months: int = MONTHS) -> list[date]:
    """The last `months` month-ends, oldest first, the current month's being today (it hasn't ended yet)."""
    out = []
    y, m = today.year, today.month
    for _ in range(months):
        out.append(min(_month_end(y, m), today))
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    return list(reversed(out))


def net_worth(conn, user_id: str, today: date | None = None) -> dict:
    """Home's Net worth card for one person's live, unarchived accounts.

    {"accounts": [{id, name, type, balance, as_of, signed}] (balance None: "no balance yet", left out of the total),
     "total", "counted", "missing", "months": [{date, value}] (None before any account has a balance), "svg"}.
    `signed` is what the account adds to the total: a cash balance, or minus what a card owes. Each month-end
    carries every account's last known balance forward."""
    today = today or statement_due.today()
    accounts = conn.execute(
        "SELECT id, name, type FROM accounts WHERE user_id = ? AND deleted_at IS NULL AND is_archived = 0 "
        "ORDER BY name COLLATE NOCASE", (user_id,)).fetchall()
    rows, histories = [], []
    for a in accounts:
        points = [p for p in statement_points(conn, a["id"]) if p["date"] <= today]
        sign = -1 if is_card(a) else 1
        latest = points[-1] if points else None
        rows.append({"id": a["id"], "name": a["name"], "type": a["type"],
                     "balance": latest["balance"] if latest else None,
                     "as_of": latest["date"] if latest else None,
                     "signed": round(sign * latest["balance"], 2) if latest else None})
        if points:
            histories.append((sign, points))
    counted = [r for r in rows if r["balance"] is not None]
    months = []
    for d in _month_ends(today):
        known = [(sign, balance_at(points, d)) for sign, points in histories]
        known = [sign * p["balance"] for sign, p in known if p is not None]
        months.append({"date": d, "value": round(sum(known), 2) if known else None})
    out = {"accounts": rows, "total": round(sum(r["signed"] for r in counted), 2) if counted else None,
           "counted": len(counted), "missing": len(rows) - len(counted), "months": months, "svg": None}
    if any(m["value"] is not None for m in months):
        out["svg"] = multi_series_chart(
            [f"{calendar.month_abbr[m['date'].month]} {m['date'].year}" for m in months],
            [{"label": "Net worth", "values": [m["value"] for m in months], "css_class": "chart-cat-1"}],
            kind="line", label="Net worth at each month-end, the last 12 months", decimals=0)
    return out
