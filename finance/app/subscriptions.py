"""Subscription price changes and new subscriptions (SPEC.md section 29).

Built on the recurring detection (recurring.py, section 16): only series that are recurring (money out, 3+ charges
on a cadence, or confirmed by hand). Nothing is stored but what the person dismissed (subscription_dismissals) and
what was sent (subscription_alerts, alerts.py).

* **Price change.** The series' latest charge differs from the one before it by more than a cent, after at least 2
  charges in a row at the old amount: "Netflix 15.49 → 17.99 (+16%) since Sep 12". A new price far from the old one
  can land in a series of its own (recurring.py splits amounts more than 20% / $3 apart); a later series of the same
  merchant on the same account that starts where the old one stops, on its cadence, is read as the old one going on.
* **Variable bills** (a series whose amounts mostly differ from one charge to the next, e.g. electricity) aren't a
  price change unless the latest charge is outside the range of the earlier ones (the last 12).
* **New subscription.** A series that became recurring within the last 45 days (its third charge, or the day it
  was confirmed by hand), and isn't a merchant's earlier series going on at a new price.
* A price change is shown while its charge is from the last 90 days; dismissing one keeps it away for good, and a
  later, different change of the same series (another amount or date) shows again.

    changes(conn, user_id, today)           every current change, dismissed ones left out
    visible(conn, current, acting, ...)     what Home and Recurring show (owner only, App setting on)
    dismiss(conn, account_id, series, key)  Dismiss
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from . import recurring, settings, statement_due

NEW_DAYS = 45                   # "became recurring within the last 45 days"
CHANGE_DAYS = 90                # a price change is shown while its charge is this recent
MIN_OLD_CHARGES = 2             # charges in a row at the old amount before a change counts
RANGE_CHARGES = 12              # a variable bill's usual range: its last 12 charges before the latest
_CENT = 0.01


def enabled() -> bool:
    """Admin → App settings → Subscription change alerts (on by default)."""
    return settings.get_bool("subscription_alerts")


def _differs(a: float, b: float) -> bool:
    return abs(round(a - b, 2)) > _CENT


def _period(s: dict) -> tuple[float, float] | None:
    """(days per charge, tolerance) of the series' cadence."""
    for label, center, tol in recurring._FREQUENCY_BUCKETS:
        if label == s.get("frequency"):
            return float(center), float(tol)
    gap = s.get("avg_gap_days") or 0
    return (float(gap), max(3.0, gap * 0.25)) if gap else None


def _follows(s: dict, t: dict) -> bool:
    """Series t starts after series s stopped, on s's cadence (a skipped charge or two allowed)."""
    period = _period(s)
    if period is None:
        return False
    gap = (t["txns"][0]["date"] - s["txns"][-1]["date"]).days
    center, tol = period
    return gap > 0 and any(abs(gap - k * center) <= tol for k in (1, 2, 3))


def _variable(amounts: list[float]) -> bool:
    """Mostly a different amount from one charge to the next."""
    if len(amounts) < 2:
        return False
    same = sum(1 for a, b in zip(amounts, amounts[1:]) if not _differs(a, b))
    return same * 2 < len(amounts) - 1


def price_change(charges: list[tuple[date, float]]) -> dict | None:
    """{kind: "price" | "range", old, low, high, new, pct, since} for a series' charges (date, amount) oldest first,
    or None."""
    if len(charges) < MIN_OLD_CHARGES + 1:
        return None
    amounts = [a for _, a in charges]
    new, old = amounts[-1], amounts[-2]
    if not _differs(new, old):
        return None
    prior = amounts[:-1][-RANGE_CHARGES:]
    since = charges[-1][0]
    if _variable(prior):
        low, high = min(prior), max(prior)
        if low - _CENT <= new <= high + _CENT:
            return None
        base = high if new > high else low
        return {"kind": "range", "old": base, "low": low, "high": high, "new": new,
                "pct": round((new - base) / base * 100) if base else None, "since": since}
    run = 0
    for a in reversed(amounts[:-1]):
        if _differs(a, old):
            break
        run += 1
    if run < MIN_OLD_CHARGES:
        return None
    return {"kind": "price", "old": old, "low": old, "high": old, "new": new,
            "pct": round((new - old) / old * 100) if old else None, "since": since}


def _override_days(conn, user_id: str) -> dict[str, list[tuple[float | None, date]]]:
    """{merchant key: [(amount or None for the whole merchant, the day it was confirmed)]} — "mark recurring"."""
    out: dict = defaultdict(list)
    for r in conn.execute("SELECT merchant_pattern, created_at FROM recurring_overrides WHERE user_id = ? "
                          "AND is_recurring = 1", (user_id,)):
        when = statement_due.local_from_utc(r["created_at"])
        if when is None:
            continue
        key, sep, amount = r["merchant_pattern"].rpartition("@")
        try:
            if not sep:
                raise ValueError
            out[recurring.merchant_key(key)].append((float(amount), when.date()))
        except ValueError:
            out[recurring.merchant_key(r["merchant_pattern"])].append((None, when.date()))
    return out


def _became_recurring(s: dict, overrides: dict) -> date | None:
    dates = sorted({t["date"] for t in s["txns"]})
    days = []
    if s.get("frequency") not in (None, "Manual") and len(dates) >= 3:
        days.append(dates[2])
    low, high = abs(s["amount_min"]), abs(s["amount_max"])
    for amount, day in overrides.get(s["merchant_key"], ()):
        if amount is None or low - _CENT <= amount <= high + _CENT:
            days.append(day)
    return min(days) if days else None


def _name(s: dict) -> str:
    text = " ".join(str(s.get("sample_description") or s["merchant_key"]).split())
    return text if len(text) <= 48 else text[:47] + "…"


def _dismissed(conn, user_id: str) -> set:
    return {(r["account_id"], r["series"], r["change_key"]) for r in conn.execute(
        "SELECT d.account_id, d.series, d.change_key FROM subscription_dismissals d JOIN accounts a ON a.id = d.account_id "
        "WHERE a.user_id = ?", (user_id,))}


def _live_accounts(conn, user_id: str) -> set:
    return {r[0] for r in conn.execute("SELECT id FROM accounts WHERE user_id = ? AND deleted_at IS NULL "
                                       "AND is_archived = 0", (user_id,))}


def changes(conn, user_id: str, today: date | None = None, *, include_dismissed: bool = False,
            account_id: int | None = None) -> list[dict]:
    """Every current price change and new subscription on the person's live, unarchived accounts, newest first.

    Each: {kind ("price" | "range" | "new"), account_id, account_name, series (the merchant key), series_key,
    change_key, name, old, low, high, new, pct, since, frequency, text}."""
    today = today or statement_due.today()
    series, _ = recurring._load_series(conn, user_id)
    live = _live_accounts(conn, user_id)
    families: dict = defaultdict(list)
    for s in series:
        families[(s["account_id"], s["merchant_key"])].append(s)
    overrides = _override_days(conn, user_id)
    dismissed = set() if include_dismissed else _dismissed(conn, user_id)
    out = []
    for s in series:
        if s["status"] != "recurring" or s["account_id"] not in live or not s["txns"]:
            continue
        if account_id is not None and s["account_id"] != account_id:
            continue
        family = [t for t in families[(s["account_id"], s["merchant_key"])] if t is not s and t["txns"]
                  and t["status"] != "dismissed"]
        # a later series of the same merchant that carries on where this one stopped: the same subscription
        later = sorted((t for t in family if t["txns"][0]["date"] > s["txns"][-1]["date"] and _follows(s, t)),
                       key=lambda t: t["txns"][0]["date"])
        txns = list(s["txns"]) + (list(later[0]["txns"]) if later else [])
        charges = [(t["date"], round(abs(t["amount"]), 2)) for t in txns]
        base = {"account_id": s["account_id"], "account_name": s["account_name"], "series": s["merchant_key"],
                "series_key": s["series_key"], "name": _name(s), "frequency": s.get("frequency")}
        pc = price_change(charges)
        if pc and pc["since"] >= today - timedelta(days=CHANGE_DAYS) and pc["since"] <= today:
            key = f"price:{pc['old']:.2f}>{pc['new']:.2f}:{pc['since'].isoformat()}"
            out.append({**base, **pc, "change_key": key})
        # a new subscription — unless an earlier series of the same merchant was there before it
        earlier = any(t["txns"][0]["date"] < s["txns"][0]["date"] and len({x["date"] for x in t["txns"]}) >= 2
                      for t in family)
        became = _became_recurring(s, overrides)
        if not earlier and became is not None and today - timedelta(days=NEW_DAYS) <= became <= today:
            amount = round(abs(s["txns"][-1]["amount"]), 2)
            out.append({**base, "kind": "new", "old": None, "low": None, "high": None, "new": amount, "pct": None,
                        "since": s["txns"][0]["date"], "became": became, "change_key": f"new:{s['series_key']}"})
    seen = set()
    unique = []
    for c in out:                 # a series and the one carrying it on can both see the same change
        k = (c["account_id"], c["series"], c["change_key"])
        if k not in seen and k not in dismissed:
            seen.add(k)
            unique.append(c)
    out = unique
    for c in out:
        c["text"] = text(c)
    out.sort(key=lambda c: (c["since"], c["name"]), reverse=True)
    return out


def _money(v: float) -> str:
    return f"{v:,.2f}"


def _pct(c: dict) -> str:
    return f" ({c['pct']:+d}%)" if c.get("pct") is not None else ""


def text(c: dict, name: str | None = None) -> str:
    """"Netflix 15.49 → 17.99 (+16%) since Sep 12" / "Electric usually 80.12–95.40 → 140.00 (+47%) on Sep 12" /
    "New: Spotify 9.99 monthly since Aug 1"."""
    name = name if name is not None else c["name"]
    when = statement_due.label(c["since"])
    if c["kind"] == "price":
        return f"{name} {_money(c['old'])} → {_money(c['new'])}{_pct(c)} since {when}"
    if c["kind"] == "range":
        return f"{name} usually {_money(c['low'])}–{_money(c['high'])} → {_money(c['new'])}{_pct(c)} on {when}"
    freq = (c.get("frequency") or "").lower()
    freq = "" if freq in ("", "manual") else f" {freq}"
    return f"New: {name} {_money(c['new'])}{freq} since {when}"


def visible(conn, current, acting, account_id: int | None = None) -> list[dict]:
    """Home's and Recurring's Subscription changes: [] when the App setting is off or the viewer isn't the owner."""
    if not enabled() or not statement_due.can_see(current, acting):
        return []
    return changes(conn, acting.id, account_id=account_id)


def dismiss(conn, user_id: str, account_id: int, series: str, change_key: str, by: str) -> bool:
    """Dismiss one change of one of the person's series (only a change that is there now)."""
    current = changes(conn, user_id, include_dismissed=True)
    if not any(c["account_id"] == account_id and c["series"] == series and c["change_key"] == change_key
               for c in current):
        return False
    conn.execute("INSERT OR IGNORE INTO subscription_dismissals (account_id, series, change_key, dismissed_by) "
                 "VALUES (?, ?, ?, ?)", (account_id, series, change_key, by))
    return True
