"""Recurring-charge detection (SPEC.md section 16).

Charges only — money OUT, never income. A row only enters a candidate
group when it's a real charge: a negative amount on checking/savings, or a
POSITIVE amount on a credit_card account (this app's sign convention — see
app/matching.py: a credit-card purchase is positive, a payment toward the
card is negative). Deposits, paychecks, card payments, transfers and
excluded rows are never candidates, so there is no "recurring income".

Everything is computed from existing transactions at query time —
recurrence is a property of a *group* of transactions, not a stored flag —
so the only stored state is the manual-override table (recurring_overrides).

A **series** is the unit that gets detected, shown and overridden:

    account  ->  merchant family  ->  amount cluster  =  one series

* Merchant family. `merchant_key` strips digits, punctuation and reference
  numbers from a description and keeps a fixed-length prefix (the same
  "prefix" idea category_rules use). Banks also truncate the same payee
  differently from month to month ("Direct Payment - Acme Premium" vs "...
  Acme Premium Insurance"), so a key that is a word-prefix of a longer,
  specific-enough key on the same account joins that key's family.
* Amount cluster. Inside a family, charges are split by amount, so one
  merchant that takes two different fixed amounts each month (a base
  premium and a rider) yields two series, each with its own cadence.
  Amounts that repeat exactly are anchors; other charges join the nearest
  anchor within tolerance; whatever is left is clustered by tolerance
  (a variable bill such as electricity).
* Cadence. Weekly / Monthly / Quarterly / Annual, judged on the gaps
  between distinct charge dates, tolerant of normal date drift and of a
  skipped period (a statement that was never uploaded).

A series is `recurring` (3+ charges on a cadence, or forced by the user),
`suggested` (2 charges on a cadence — worth a look, not yet trusted),
`dismissed` (the user said no) or `none`.

Overrides are stored in recurring_overrides.merchant_pattern as either a
merchant key (the whole family) or "<merchant key>@<amount>" (one amount
series). A series-level override beats a merchant-level one.
"""
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from .datenorm import normalize_date

_KEY_LEN = 32
_DIGITS = re.compile(r"[0-9]+")
_NON_MERCHANT_CHARS = re.compile(r"[^A-Z& ]+")
_WHITESPACE = re.compile(r"\s+")

# (label, center in days, tolerance in days).
_FREQUENCY_BUCKETS = [
    ("Weekly", 7, 3),
    ("Monthly", 30, 6),
    ("Quarterly", 91, 12),
    ("Annual", 365, 20),
]
_MAX_SKIPPED_PERIODS = 3   # a gap of 2-3 periods still counts (missing statement)
_MIN_ON_TIME_SHARE = 0.5   # ...as long as half the gaps are a single period
_MIN_GAPS_MATCHING = 0.8   # share of gaps that must fit the cadence at all

# Charges in one series stay within this fraction of the anchor amount (or
# this flat dollar amount, whichever is more forgiving).
_AMOUNT_TOLERANCE_RATIO = 0.20
_AMOUNT_TOLERANCE_FLOOR = 3.00

_MIN_OCCURRENCES_AUTO = 3
_MIN_OCCURRENCES_SUGGEST = 2

# A key may absorb longer keys only if it is specific enough that "starts
# with it" still means "same payee" ("DIRECT PAYMENT" alone would not be).
_FAMILY_MIN_WORDS = 3
_FAMILY_MIN_CHARS = 20

# Same "spend" direction test dashboard.py's totals use.
_MONEY_OUT_FILTER = "((a.type = 'credit_card' AND t.amount > 0) OR (a.type != 'credit_card' AND t.amount < 0))"


def _is_reference(token: str) -> bool:
    """Masked account numbers ("XXXXXXXX0000a0a") and mostly-digit ids."""
    return token.startswith("XXX") or sum(c.isdigit() for c in token) * 2 >= len(token)


def merchant_key(description: str) -> str:
    tokens = [t for t in description.upper().split() if not _is_reference(t)]
    d = _DIGITS.sub(" ", " ".join(tokens))
    d = _NON_MERCHANT_CHARS.sub(" ", d)
    return _WHITESPACE.sub(" ", d).strip()[:_KEY_LEN]


def _parse_date(s: str) -> date | None:
    """Every stored date is supposed to be ISO already (datenorm), but this
    is the one place that parses them, so a leftover bad row is skipped
    rather than crashing the page."""
    normalized = normalize_date(s)
    return datetime.strptime(normalized, "%Y-%m-%d").date() if normalized else None


def _add_months(d: date, months: int) -> date:
    """Calendar-aware month add with end-of-month clamping (Jan 31 + 1
    month is Feb 28/29) — stdlib only, no dateutil."""
    total = d.month - 1 + months
    year, month = d.year + total // 12, total % 12 + 1
    leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
    days_in_month = [31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
    return date(year, month, min(d.day, days_in_month))


def _classify_frequency(dates_sorted: list[date]) -> tuple[str | None, float]:
    """(label, average gap in days). label is None when the gaps don't fit
    a known cadence; the average is still returned so a manually forced
    series can project a next date."""
    if len(dates_sorted) < 2:
        return None, 0.0
    gaps = [(b - a).days for a, b in zip(dates_sorted, dates_sorted[1:])]
    avg = sum(gaps) / len(gaps)
    for label, center, tol in _FREQUENCY_BUCKETS:
        on_time = sum(1 for g in gaps if abs(g - center) <= tol)
        skipped = 0
        if label != "Weekly":  # a "skipped week" would match almost any gap
            skipped = sum(
                1 for g in gaps
                if any(abs(g - m * center) <= tol for m in range(2, _MAX_SKIPPED_PERIODS + 1))
            )
        if on_time >= len(gaps) * _MIN_ON_TIME_SHARE and on_time + skipped >= len(gaps) * _MIN_GAPS_MATCHING:
            return label, avg
    return None, avg


def _project_next(last: date, frequency: str | None, avg_gap_days: float) -> date | None:
    months = {"Monthly": 1, "Quarterly": 3, "Annual": 12}.get(frequency)
    if months:
        return _add_months(last, months)
    if frequency == "Weekly":
        return last + timedelta(days=7)
    return last + timedelta(days=round(avg_gap_days)) if avg_gap_days else None


# --- grouping -------------------------------------------------------------

def _family_roots(keys: set[str]) -> dict[str, str]:
    """member key -> family root key (shortest specific key it starts with)."""
    roots: list[str] = []
    result: dict[str, str] = {}
    for key in sorted(keys, key=len):
        root = next(
            (r for r in roots
             if key.startswith(r + " ") and (len(r.split()) >= _FAMILY_MIN_WORDS or len(r) >= _FAMILY_MIN_CHARS)),
            None,
        )
        if root is None:
            roots.append(key)
            root = key
        result[key] = root
    return result


def _tolerance(anchor: float) -> float:
    return max(_AMOUNT_TOLERANCE_FLOOR, _AMOUNT_TOLERANCE_RATIO * anchor)


def _cluster_by_amount(txns: list[dict]) -> list[tuple[float, list[dict]]]:
    """Split one merchant family into [(anchor amount, txns)] — see module
    docstring. Amounts are compared by absolute value."""
    exact: dict[float, int] = defaultdict(int)
    for t in txns:
        exact[round(abs(t["amount"]), 2)] += 1
    anchors = sorted((a for a, n in exact.items() if n >= 2), key=lambda a: (-exact[a], a))

    clusters: dict[float, list[dict]] = {a: [] for a in anchors}
    leftovers = []
    for t in txns:
        amount = abs(t["amount"])
        near = [a for a in anchors if abs(amount - a) <= _tolerance(a)]
        if near:
            clusters[min(near, key=lambda a: abs(amount - a))].append(t)
        else:
            leftovers.append(t)

    variable: list[tuple[float, list[dict]]] = []
    for t in sorted(leftovers, key=lambda t: abs(t["amount"])):
        if variable and abs(t["amount"]) - variable[-1][0] <= _tolerance(variable[-1][0]):
            variable[-1][1].append(t)
        else:
            variable.append((abs(t["amount"]), [t]))

    return [(a, ts) for a, ts in clusters.items() if ts] + variable


# --- overrides ------------------------------------------------------------

def _load_overrides(conn, user_id: str) -> dict:
    """{"merchant": {key: bool}, "series": {key: [(amount, bool)]}}. Patterns
    are re-normalised through merchant_key so overrides saved before the key
    rules changed still line up."""
    merchant: dict[str, bool] = {}
    series: dict[str, list[tuple[float, bool]]] = defaultdict(list)
    for r in conn.execute(
        "SELECT merchant_pattern, is_recurring FROM recurring_overrides WHERE user_id = ?", (user_id,)
    ).fetchall():
        flag = bool(r["is_recurring"])
        key, sep, amount = r["merchant_pattern"].rpartition("@")
        try:
            if not sep:
                raise ValueError
            series[merchant_key(key)].append((float(amount), flag))
        except ValueError:  # no "@<amount>" suffix: a whole-merchant override
            merchant[merchant_key(r["merchant_pattern"])] = flag
    return {"merchant": merchant, "series": series}


def _resolve_override(overrides: dict, member_keys: set[str], low: float, high: float) -> bool | None:
    for key in member_keys:
        for amount, flag in overrides["series"].get(key, ()):
            if low - 0.01 <= amount <= high + 0.01:
                return flag
    for key in sorted(member_keys, key=len):
        if key in overrides["merchant"]:
            return overrides["merchant"][key]
    return None


def series_pattern(merchant: str, amount: float) -> str:
    return f"{merchant}@{abs(amount):.2f}"


def upsert_override(conn, user_id: str, merchant_pattern: str, is_recurring: bool) -> None:
    """Upserts on (user_id, merchant_pattern) — same pattern as
    categorize.py's save_rule, so re-toggling updates the existing row."""
    existing = conn.execute(
        "SELECT id FROM recurring_overrides WHERE user_id = ? AND merchant_pattern = ?",
        (user_id, merchant_pattern),
    ).fetchone()
    if existing:
        conn.execute("UPDATE recurring_overrides SET is_recurring = ? WHERE id = ?", (int(is_recurring), existing["id"]))
    else:
        conn.execute(
            "INSERT INTO recurring_overrides (user_id, merchant_pattern, is_recurring) VALUES (?, ?, ?)",
            (user_id, merchant_pattern, int(is_recurring)),
        )


def clear_override(conn, user_id: str, merchant_pattern: str) -> None:
    conn.execute(
        "DELETE FROM recurring_overrides WHERE user_id = ? AND merchant_pattern = ?", (user_id, merchant_pattern)
    )


# --- series ---------------------------------------------------------------

def _make_series(account: dict, root: str, member_keys: set[str], anchor: float, txns: list[dict], overrides: dict) -> dict:
    txns = sorted(txns, key=lambda t: (t["date"], t["id"]))
    dates = sorted({t["date"] for t in txns})
    frequency, avg_gap = _classify_frequency(dates)
    amounts = [t["amount"] for t in txns]
    last = dates[-1]

    override = _resolve_override(overrides, member_keys, min(map(abs, amounts)), max(map(abs, amounts)))
    if override is False:
        status = "dismissed"
    elif override is True:
        status = "recurring"
    elif frequency and len(dates) >= _MIN_OCCURRENCES_AUTO:
        status = "recurring"
    elif frequency and len(dates) >= _MIN_OCCURRENCES_SUGGEST:
        status = "suggested"
    else:
        status = "none"

    next_date = _project_next(last, frequency, avg_gap)
    return {
        "account_id": account["id"], "account_name": account["name"], "account_type": account["type"],
        "merchant_key": root, "series_key": series_pattern(root, anchor),
        "sample_description": txns[-1]["description"],
        "amount_min": min(amounts), "amount_max": max(amounts),
        # What one charge typically costs, and the category most of them carry: the inputs to
        # category_totals().
        "amount_avg": round(sum(abs(a) for a in amounts) / len(amounts), 2),
        "category": Counter(t["category"] for t in txns).most_common(1)[0][0],
        "avg_gap_days": round(avg_gap, 1),
        # A user-forced series with no detectable cadence is labelled "Manual".
        "frequency": (frequency or "Manual") if status == "recurring" else frequency,
        "occurrence_count": len(dates),
        "last_date": last.isoformat(), "next_date": next_date.isoformat() if next_date else None,
        "manually_overridden": override is not None,
        "status": status,
        "txns": txns,
    }


def _load_series(conn, user_id: str, account_id: int | None = None) -> tuple[list[dict], int]:
    """(every series for this user, number of candidate charges scanned)."""
    params: list = [user_id]
    account_filter = ""
    if account_id is not None:
        account_filter = "AND t.account_id = ?"
        params.append(account_id)
    rows = conn.execute(
        f"""
        SELECT t.id, t.account_id, a.name AS account_name, a.type AS account_type,
               t.date, t.amount, t.description, t.category
        FROM transactions t JOIN accounts a ON a.id = t.account_id
        WHERE t.user_id = ? AND t.deleted_at IS NULL
          AND t.txn_type != 'transfer' AND t.is_excluded = 0
          AND {_MONEY_OUT_FILTER}
          {account_filter}
        """,
        params,
    ).fetchall()

    by_account: dict[int, dict] = {}
    scanned = 0
    for r in rows:
        key, when = merchant_key(r["description"]), _parse_date(r["date"])
        if not key or when is None:
            continue
        scanned += 1
        acct = by_account.setdefault(
            r["account_id"], {"info": {"id": r["account_id"], "name": r["account_name"], "type": r["account_type"]}, "txns": []}
        )
        acct["txns"].append({"id": r["id"], "date": when, "amount": round(r["amount"], 2),
                             "description": r["description"], "key": key,
                             "category": r["category"] or "Uncategorized"})

    overrides = _load_overrides(conn, user_id)
    series = []
    for acct in by_account.values():
        roots = _family_roots({t["key"] for t in acct["txns"]})
        families: dict[str, list[dict]] = defaultdict(list)
        for t in acct["txns"]:
            families[roots[t["key"]]].append(t)
        for root, members in families.items():
            member_keys = {t["key"] for t in members}
            for anchor, txns in _cluster_by_amount(members):
                series.append(_make_series(acct["info"], root, member_keys, anchor, txns, overrides))
    series.sort(key=lambda s: (s["account_name"], s["sample_description"].upper(), s["series_key"]))
    return series, scanned


# Days per period, to turn "one charge every <period>" into a per-month / per-week amount.
_PERIOD_DAYS = {"Weekly": 7.0, "Monthly": 365.25 / 12, "Quarterly": 365.25 / 4, "Annual": 365.25}


_FREQUENCY_ORDER = ["Weekly", "Monthly", "Quarterly", "Annual"]


def frequency_totals(series: list[dict]) -> dict:
    """Totals for the recurring charges, grouped by how often they repeat.

    Weekly charges add up to a weekly total, monthly charges to a monthly total, and so on;
    every group is also shown scaled to a per-month and a per-week figure so they can be
    added together (a weekly 10.00 is 43.48 a month, an annual 120.00 is 10.00 a month).
    A charge marked recurring by hand with no regular cadence is grouped as "Other" using its
    average gap; one with a single charge and no cadence has nothing to scale by and is left
    out (counted in `skipped`).
    Returns {"rows": [{frequency, count, cycle, monthly, weekly}, ...], "total": {...}, "skipped": n};
    `cycle` is the sum charged each time the group repeats (None for "Other")."""
    groups: dict[str, dict] = {}
    skipped = 0
    for s in series:
        period = _PERIOD_DAYS.get(s["frequency"]) or s.get("avg_gap_days") or 0
        if period <= 0:
            skipped += 1
            continue
        label = s["frequency"] if s["frequency"] in _PERIOD_DAYS else "Other"
        row = groups.setdefault(label, {"frequency": label, "count": 0, "cycle": 0.0 if label != "Other" else None,
                                         "monthly": 0.0, "weekly": 0.0})
        row["count"] += 1
        if row["cycle"] is not None:
            row["cycle"] += s["amount_avg"]
        row["monthly"] += s["amount_avg"] * (365.25 / 12) / period
        row["weekly"] += s["amount_avg"] * 7 / period
    rows = [groups[k] for k in _FREQUENCY_ORDER + ["Other"] if k in groups]
    return {
        "rows": rows, "skipped": skipped,
        "total": {"frequency": "Total", "count": sum(r["count"] for r in rows),
                  "monthly": sum(r["monthly"] for r in rows), "weekly": sum(r["weekly"] for r in rows)},
    }


def scan_recurring(conn, user_id: str, account_id: int | None = None) -> dict:
    """Everything the Recurring page shows, from one pass over the
    transactions: {recurring, suggested, dismissed, scanned}."""
    series, scanned = _load_series(conn, user_id, account_id)
    by_status = defaultdict(list)
    for s in series:
        by_status[s["status"]].append(s)
    return {
        "recurring": by_status["recurring"],
        "suggested": by_status["suggested"],
        "dismissed": by_status["dismissed"],
        "scanned": scanned,
    }


def series_for_transaction(conn, user_id: str, transaction_id: int) -> dict | None:
    """The series one transaction belongs to, plus the other amount series
    of the same merchant on that account. None when the transaction isn't
    found; `series` is None when it isn't a charge (income, transfer,
    excluded) and so can't be recurring."""
    txn = conn.execute(
        "SELECT id, account_id FROM transactions WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
        (transaction_id, user_id),
    ).fetchone()
    if txn is None:
        return None
    all_series, _ = _load_series(conn, user_id, txn["account_id"])
    mine = next((s for s in all_series if any(t["id"] == transaction_id for t in s["txns"])), None)
    siblings = [s for s in all_series if mine and s is not mine and s["merchant_key"] == mine["merchant_key"]]
    return {"series": mine, "siblings": siblings}


def list_unique_merchants(conn, user_id: str, account_id: int) -> list[dict]:
    """The card-migration view's secondary list (SPEC.md section 16):
    every unique merchant that has EVER CHARGED this account, recurring or
    not — catches a saved card on file with no clean pattern yet. Money out
    only, same as the detection above."""
    rows = conn.execute(
        f"""
        SELECT t.date, t.amount, t.description FROM transactions t
        JOIN accounts a ON a.id = t.account_id
        WHERE t.user_id = ? AND t.account_id = ? AND t.deleted_at IS NULL
          AND t.txn_type != 'transfer' AND t.is_excluded = 0
          AND {_MONEY_OUT_FILTER}
        ORDER BY t.date DESC
        """,
        (user_id, account_id),
    ).fetchall()

    seen: dict[str, dict] = {}
    for r in rows:
        key = merchant_key(r["description"])
        if key and key not in seen:
            seen[key] = {
                "merchant_key": key, "sample_description": r["description"],
                "last_amount": abs(r["amount"]), "last_date": r["date"],
            }
    return sorted(seen.values(), key=lambda m: m["last_date"], reverse=True)
