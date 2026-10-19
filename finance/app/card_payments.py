"""Credit card payment due dates (SPEC.md section 30).

A credit card can say when its payment is due: monthly on a day (1–31; 29–31 fall on a shorter month's last day) or
on the last day of the month — set by the person, never read from statements. Due dates are worked out here, never
stored, by the same rules as the expected statement dates (statement_due.py).

A due date D counts as **paid** when a payment to the card was recorded on or after the previous due date: a live
negative transaction on the card (a payment, a credit, or the card's side of a linked transfer). One payment pays one
due date only: each due date takes the latest payment from the previous due date up to it, else (a late payment) the
first one after it before the next due date — so a payment on the due day pays that due date, not the next one as
well. Or the person taps
**Mark as paid** (payment_dismissals).

Only two due dates are ever looked at: the latest one before today (overdue until the next due date arrives) and the
next one (today or later; noticed from 7 days before). Due dates before the day the setting was made, changed, or the
card restored / unarchived (`payment_due_set_at`) never count. "Today" is statement_due's clock (tests replace it).

    due_dates(account, start, end)     the due dates in [start, end]
    status(conn, account, on)          {overdue, upcoming, ...}: the card's cycles today
    notices(conn, current, acting)     Home's and Upload's Payment due notices
    column(conn, rows, current, acting) {account id: (text, css class)} for the accounts lists
    latest_statement(conn, account_id, on)  {date, balance}: the latest statement's printed balance (what was owed)
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta

from . import settings, statement_due

NOTICE_DAYS = 7                 # a due date is noticed from 7 days before
LOOK_BACK_CYCLES = 4            # due dates processed before the latest one, to assign payments to them


def enabled() -> bool:
    """Admin → App settings → Card payment reminders (on by default)."""
    return settings.get_bool("card_payment_reminders")


def is_set(account) -> bool:
    keys = account.keys() if hasattr(account, "keys") else account
    return ("payment_due_day" in keys and account["type"] == "credit_card"
            and account["payment_due_day"] is not None)


def _add_months(y: int, m: int, n: int) -> tuple[int, int]:
    idx = y * 12 + (m - 1) + n
    return idx // 12, idx % 12 + 1


def due_dates(account, start: date, end: date) -> list[date]:
    """Every due date from `start` to `end` (both included), oldest first; [] when Off."""
    day = account["payment_due_day"]
    if day is None or end < start:
        return []
    out = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        d = statement_due._on_day(y, m, day)
        if start <= d <= end:
            out.append(d)
        y, m = _add_months(y, m, 1)
    return out


def _previous(account, d: date) -> date:
    """The due date before `d`."""
    y, m = _add_months(d.year, d.month, -1)
    return statement_due._on_day(y, m, account["payment_due_day"])


def _next(account, d: date) -> date:
    """The due date after `d`."""
    y, m = _add_months(d.year, d.month, 1)
    return statement_due._on_day(y, m, account["payment_due_day"])


def _payments(conn, account_id: int, since: date) -> list[date]:
    rows = conn.execute(
        "SELECT date FROM transactions WHERE account_id = ? AND deleted_at IS NULL AND amount < 0 AND date >= ? "
        "ORDER BY date, id", (account_id, since.isoformat())).fetchall()
    out = []
    for r in rows:
        try:
            out.append(date.fromisoformat(str(r["date"])[:10]))
        except ValueError:
            continue
    return out


def paid_dates(conn, account, dues: list[date], set_on: date | None = None) -> dict[date, date | None]:
    """{due date: the payment date that paid it, or None}. `dues` oldest first. Each due date D (previous due date
    P, next N) takes the latest unused payment dated P … D, else (late) the first unused one after D up to N; a
    payment pays one due date. A due date before the setting (`set_on`) takes no late payment: it would be taking
    the next one's."""
    if not dues:
        return {}
    payments = _payments(conn, account["id"], _previous(account, dues[0]))
    used = [False] * len(payments)
    out = {}
    for d in dues:
        p = _previous(account, d)
        pick = None
        for i in range(len(payments) - 1, -1, -1):
            if not used[i] and p <= payments[i] <= d:
                pick = i
                break
        if pick is None and (set_on is None or d >= set_on):     # a late payment: before the next due date
            nxt = _next(account, d)
            pick = next((i for i, x in enumerate(payments) if not used[i] and d < x <= nxt), None)
        if pick is not None:
            used[pick] = True
        out[d] = payments[pick] if pick is not None else None
    return out


def _dismissed(conn, account_id: int, d: date) -> bool:
    return conn.execute("SELECT 1 FROM payment_dismissals WHERE account_id = ? AND due_date = ?",
                        (account_id, d.isoformat())).fetchone() is not None


def _cycle(conn, account, d: date | None, paid: dict, set_on: date | None, on: date) -> dict | None:
    if d is None:
        return None
    state = "unpaid"
    if set_on is not None and d < set_on:
        state = "before_setting"
    elif paid.get(d) is not None:
        state = "paid"
    elif _dismissed(conn, account["id"], d):
        state = "dismissed"
    return {"due_date": d, "state": state, "paid_on": paid.get(d), "days": (d - on).days}


def status(conn, account, on: date | None = None) -> dict | None:
    """The card's due dates today, or None (not a card, Off, archived or deleted).

    {"account_id", "name", "previous": cycle | None (the latest due date before today), "next": cycle (today or
    later), "notice": the cycle to show ("overdue" previous, else the next one when it's within 7 days and unpaid) or
    None, "kind": "overdue" | "today" | "soon" | None}. A cycle: {due_date, state (unpaid, paid, dismissed,
    before_setting), paid_on, days (from today: negative = past)}."""
    if not is_set(account) or account["is_archived"] or account["deleted_at"] is not None:
        return None
    on = on or statement_due.today()
    nxt = due_dates(account, on, on + timedelta(days=40))[0]
    prev = _previous(account, nxt)
    dues = [nxt]
    d = nxt
    for _ in range(LOOK_BACK_CYCLES + 1):
        d = _previous(account, d)
        dues.insert(0, d)
    set_at = statement_due.local_from_utc(account["payment_due_set_at"])
    set_on = set_at.date() if set_at else None
    paid = paid_dates(conn, account, dues, set_on)
    out = {"account_id": account["id"], "name": account["name"],
           "previous": _cycle(conn, account, prev, paid, set_on, on),
           "next": _cycle(conn, account, nxt, paid, set_on, on), "notice": None, "kind": None}
    # The previous due date is overdue only until the next one arrives (on a due day, that day's is the notice).
    if out["previous"]["state"] == "unpaid" and out["next"]["days"] > 0:
        out["notice"], out["kind"] = out["previous"], "overdue"
    elif out["next"]["state"] == "unpaid" and out["next"]["days"] <= NOTICE_DAYS:
        out["notice"], out["kind"] = out["next"], ("today" if out["next"]["days"] == 0 else "soon")
    return out


def _in_days(n: int) -> str:
    return "today" if n == 0 else ("in 1 day" if n == 1 else f"in {n} days")


def _ago(n: int) -> str:
    return "1 day ago" if n == 1 else f"{n} days ago"


def notice_text(st: dict) -> str:
    """"Due Nov 5 (in 3 days)", "Due today", "Was due 2 days ago" (Nov 5)."""
    c = st["notice"]
    if st["kind"] == "overdue":
        return f"Was due {_ago(-c['days'])}"
    if st["kind"] == "today":
        return "Due today"
    return f"Due {statement_due.label(c['due_date'])} ({_in_days(c['days'])})"


def column_label(st: dict | None) -> tuple[str, str]:
    """The Payment due column: ("Nov 5 (in 3 days)" | "Due today" | "Was due 2 days ago" | "Paid · Nov 5" | "", css)."""
    if st is None:
        return "", ""
    if st["kind"] == "overdue":
        return f"Was due {_ago(-st['notice']['days'])}", "due-overdue"
    n = st["next"]
    if n["state"] == "paid":
        return f"Paid · due {statement_due.label(n['due_date'])}", "due-paid"
    if n["state"] == "dismissed":
        return f"Marked paid · due {statement_due.label(n['due_date'])}", "due-paid"
    if n["days"] == 0:
        return "Due today", "due-today"
    return (f"{statement_due.label(n['due_date'])} ({_in_days(n['days'])})",
            "due-soon" if n["days"] <= NOTICE_DAYS else "")


def visible(current, acting) -> bool:
    """Owners only (the person themself, or an admin acting as them), while the App setting is on."""
    return enabled() and statement_due.can_see(current, acting)


def _cards(conn, user_id: str) -> list:
    return conn.execute(
        "SELECT * FROM accounts WHERE user_id = ? AND type = 'credit_card' AND deleted_at IS NULL AND is_archived = 0 "
        "AND payment_due_day IS NOT NULL ORDER BY name COLLATE NOCASE", (user_id,)).fetchall()


def due_for(conn, user_id: str, on: date | None = None) -> list[dict]:
    """The person's cards with a notice (overdue, due today or within 7 days), the soonest due date first."""
    on = on or statement_due.today()
    out = []
    for a in _cards(conn, user_id):
        st = status(conn, a, on)
        if st and st["notice"]:
            st["text"] = notice_text(st)
            st["due_label"] = statement_due.label(st["notice"]["due_date"])
            out.append(st)
    return sorted(out, key=lambda s: (s["notice"]["due_date"], s["name"].casefold()))


def notices(conn, current, acting) -> list[dict]:
    """What Home and Upload show for this request ([] when the App setting is off or the viewer isn't the owner)."""
    if not visible(current, acting):
        return []
    return due_for(conn, acting.id)


def column(conn, rows, current, acting) -> dict:
    """{account id: (text, css class)} for the accounts lists, or {} when the column isn't shown."""
    if not visible(current, acting):
        return {}
    on = statement_due.today()
    out = {}
    for r in rows:
        if is_set(r):
            keys = r.keys()
            if "is_archived" in keys and "deleted_at" in keys:
                out[r["id"]] = column_label(status(conn, r, on))
    return out


def mark_paid(conn, account, due_date: str, by: str) -> bool:
    """Mark as paid: only the cycle the card's notice shows (an older or later date is ignored)."""
    st = status(conn, account)
    if st is None or st["notice"] is None or st["notice"]["due_date"].isoformat() != due_date:
        return False
    conn.execute("INSERT OR IGNORE INTO payment_dismissals (account_id, due_date, dismissed_by) VALUES (?, ?, ?)",
                 (account["id"], due_date, by))
    return True


# ------------------------------------------------------------------ the account form

def parse_form(value: str) -> tuple[int | None, str | None]:
    """The Payment due menu: "" / "off" → None (Off), "1"–"31", "last" → 0. (day, error)."""
    value = str(value or "").strip()
    if value in ("", "off", "0"):
        return None, None
    if value == "last":
        return 0, None
    if value.isdigit() and 1 <= int(value) <= 31:
        return int(value), None
    return None, "Pick the day the card's payment is due (1–31, or the last day of the month)."


# ------------------------------------------------------------------ the latest statement's balance (what was owed)

def iso_date(value) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def statement_end(row) -> date | None:
    """A statement's end date: period_end (QFX), else its last transaction's date, else its month's last day."""
    d = iso_date(row["period_end"]) or iso_date(row["last_txn"])
    if d is not None:
        return d
    ym = statement_due.parse_month(row["statement_period"])
    return date(ym[0], ym[1], calendar.monthrange(*ym)[1]) if ym else None


def latest_statement(conn, account_id: int, on: date) -> dict | None:
    """{date, balance}: the closing balance (statements.new_balance) of the account's statement with the latest end
    date on or before `on` (the newest statement wins a tie). Only live statements that didn't fail and have a
    balance count. A card's balance is what it owed (negative: a credit)."""
    rows = conn.execute(
        "SELECT s.id, s.period_end, s.statement_period, s.new_balance, "
        "(SELECT MAX(t.date) FROM transactions t WHERE t.statement_id = s.id AND t.deleted_at IS NULL) AS last_txn "
        "FROM statements s WHERE s.account_id = ? AND s.deleted_at IS NULL AND s.status != 'error' "
        "AND s.new_balance IS NOT NULL ORDER BY s.id", (account_id,)).fetchall()
    found = None
    for r in rows:
        d = statement_end(r)
        if d is None or d > on:
            continue
        if found is None or d >= found["date"]:
            found = {"date": d, "balance": round(float(r["new_balance"]), 2)}
    return found
