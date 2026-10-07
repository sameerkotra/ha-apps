"""What Finance Dashboard answers the Household Assistant (HOUSEHOLD_ASSISTANT_SPEC.md §4.2; the shared
app/common/assist_tools.py).

- `finance.summary`: a month's income, spending, net and top categories — the Overview's numbers;
- `finance.spending`: a month's spending by category, or one category's largest charges;
- `finance.recurring`: the recurring charges with their next expected date and amount;
- `finance.bills`: the recurring charges expected in the next N days.

Whose data: the asking person's own, or — with `person` — one whose data an admin shared with them here (the same
user_access grants the pages use; an admin's "view as anyone" never applies to the assistant). Only clean rows,
with transfers and excluded rows left out, as the Overview counts them. **Never** a transaction's note (text a person
typed): only the bank's description (the merchant), date, amount, category and account. Money is private, so the
admin's *Answer the Household Assistant* is **off** until turned on, and each person can turn off *Let the Household
Assistant answer for me* (Who am I). Links open the month's dashboard or Recurring on the app's sidebar page.
"""
from __future__ import annotations

import calendar
import re
from datetime import date, timedelta

from . import recurring, settings
from .common import app_bus as bus
from .common import assist_tools
from .common.assist_tools import Arg

_PANEL_RE = re.compile(r"^/[a-z0-9]{1,16}_finance$")
PANEL: dict = {"value": None}                         # set at start-up (app_messages.start)

WHERE = ("t.user_id = ? AND t.deleted_at IS NULL AND t.review_status = 'clean' AND t.date >= ? AND t.date <= ? "
         "AND t.txn_type != 'transfer' AND t.is_excluded = 0")
SPEND = "((a.type = 'credit_card' AND t.amount > 0) OR (a.type != 'credit_card' AND t.amount < 0))"
SPEND_AMOUNT = "CASE WHEN a.type = 'credit_card' THEN t.amount ELSE -t.amount END"
INCOME = "(t.amount > 0 AND a.type IN ('checking', 'savings'))"


def _actor(conn, uid: str):
    r = conn.execute("SELECT id, name, assistant_ok FROM known_users WHERE id = ?", (uid,)).fetchone()
    return {"id": r["id"], "name": r["name"], "assistant_ok": bool(r["assistant_ok"])} if r else None


def _panel():
    return PANEL["value"] if _PANEL_RE.match(PANEL["value"] or "") else None


tools = assist_tools.Catalogue(
    "finance", targets=[r"/month/\d{4}-(0[1-9]|1[0-2])", r"/recurring"], actor=_actor,
    enabled=lambda conn: settings.get_bool("assistant_answers"), person_enabled=lambda conn, user: user["assistant_ok"],
    panel=_panel)

_PERSON = Arg("string", "whose data, when an admin shared someone's with the asker (default their own)", max_length=100)
_MONTH = Arg("month", "the month (default this month)")


def _owner(ctx) -> tuple[str, str]:
    """(user id, name) whose data to answer from: the person's own, or a shared owner named in `person`."""
    want = ctx.args.get("person")
    if not want or want.casefold() in (ctx.user["name"].casefold(), ctx.user["id"].casefold(), "me"):
        return ctx.user["id"], ctx.user["name"]
    rows = ctx.conn.execute(
        "SELECT a.owner_id AS id, COALESCE(k.name, a.owner_id) AS name FROM user_access a "
        "LEFT JOIN known_users k ON k.id = a.owner_id WHERE a.viewer_id = ?", (ctx.user["id"],)).fetchall()
    for r in rows:
        if want.casefold() in (r["name"].casefold(), r["id"].casefold()):
            return r["id"], r["name"]
    raise bus.Nack("not_found", "person")                  # not shared with them looks like nobody


def _month(ctx) -> tuple[str, str, str]:
    m = ctx.args.get("month") or date.today().strftime("%Y-%m")
    y, mo = int(m[:4]), int(m[5:7])
    return m, f"{m}-01", f"{m}-{calendar.monthrange(y, mo)[1]:02d}"


def _label(m: str) -> str:
    return f"{calendar.month_name[int(m[5:7])]} {m[:4]}"


def _money(x: float) -> str:
    return f"{x:,.2f}"


def _whose(owner_name: str, ctx) -> str:
    return "" if owner_name == ctx.user["name"] else f" ({owner_name}'s)"


def _link(ctx, target: str):
    label = "Recurring charges" if target == "/recurring" else f"{_label(target[7:])}"
    return ctx.link(f"{label} in Finance Dashboard", target)


def _by_category(ctx, uid: str, start: str, end: str, condition: str, amount: str) -> dict[str, float]:
    rows = ctx.conn.execute(
        f"SELECT COALESCE(t.category, 'Uncategorized') AS category, SUM({amount}) AS total FROM transactions t "
        f"JOIN accounts a ON a.id = t.account_id WHERE {WHERE} AND {condition} "
        "GROUP BY COALESCE(t.category, 'Uncategorized')", (uid, start, end)).fetchall()
    return {r["category"]: round(r["total"] or 0.0, 2) for r in rows}


@tools.tool("finance.summary",
            "A month's income, spending, net and the top spending categories, from the person's bank and card "
            "statements.",
            args={"month": _MONTH, "person": _PERSON},
            returns="income, spending, net, the top 5 categories", examples=("How did we do this month?",))
def summary(ctx):
    uid, owner = _owner(ctx)
    m, start, end = _month(ctx)
    spend = _by_category(ctx, uid, start, end, SPEND, SPEND_AMOUNT)
    income = round(sum(_by_category(ctx, uid, start, end, INCOME, "t.amount").values()), 2)
    total = round(sum(spend.values()), 2)
    top = sorted(spend.items(), key=lambda kv: -kv[1])[:5]
    if not spend and not income:
        return ctx.result(f"No transactions in {_label(m)}{_whose(owner, ctx)} yet.", links=[_link(ctx, f"/month/{m}")])
    text = (f"{_label(m)}{_whose(owner, ctx)}: income {_money(income)}, spending {_money(total)}, net "
            f"{_money(income - total)}. Top categories: " + ", ".join(f"{c} {_money(v)}" for c, v in top) + ".")
    items = [{"category": c, "spend": v} for c, v in top]
    return ctx.result(text, items=items, links=[_link(ctx, f"/month/{m}")])


@tools.tool("finance.spending",
            "A month's spending by category; with `category`, that category's 10 largest charges (merchant, date, "
            "amount).",
            args={"month": _MONTH, "category": Arg("string", "a spending category, e.g. Groceries", max_length=100),
                  "person": _PERSON},
            returns="spending per category, or a category's largest charges",
            examples=("How much did we spend on food in September?",))
def spending(ctx):
    uid, owner = _owner(ctx)
    m, start, end = _month(ctx)
    spend = _by_category(ctx, uid, start, end, SPEND, SPEND_AMOUNT)
    if "category" not in ctx.args:
        rows = sorted(spend.items(), key=lambda kv: -kv[1])
        if not rows:
            return ctx.result(f"No spending in {_label(m)}{_whose(owner, ctx)}.", links=[_link(ctx, f"/month/{m}")])
        total = sum(spend.values())
        text = (f"{_label(m)}{_whose(owner, ctx)}: {_money(total)} spent. "
                + "; ".join(f"{c} {_money(v)}" for c, v in rows[:10]) + ("…" if len(rows) > 10 else "") + ".")
        items = [{"category": c, "spend": v, "share": round(v / total * 100, 1) if total else 0.0} for c, v in rows]
        return ctx.result(text, items=items, links=[_link(ctx, f"/month/{m}")])
    want = ctx.args["category"].casefold()
    match = next((c for c in spend if c.casefold() == want), None) \
        or next((c for c in spend if want in c.casefold()), None)
    if match is None:
        return ctx.result(f"No spending on “{ctx.args['category']}” in {_label(m)}{_whose(owner, ctx)}. Categories "
                          "with spending: " + (", ".join(sorted(spend)) or "none") + ".", links=[_link(ctx, f"/month/{m}")])
    rows = ctx.conn.execute(
        f"SELECT t.date, t.description, {SPEND_AMOUNT} AS amount, a.name AS account FROM transactions t "
        f"JOIN accounts a ON a.id = t.account_id WHERE {WHERE} AND {SPEND} "
        "AND COALESCE(t.category, 'Uncategorized') = ? ORDER BY amount DESC, t.date DESC LIMIT 10",
        (uid, start, end, match)).fetchall()
    items = [{"date": r["date"], "merchant": (r["description"] or "")[:80], "amount": round(r["amount"], 2),
              "account": r["account"]} for r in rows]
    text = (f"{match} in {_label(m)}{_whose(owner, ctx)}: {_money(spend[match])}. Largest: "
            + "; ".join(f"{i['merchant']} {_money(i['amount'])} ({i['date']})" for i in items[:5]) + ".")
    return ctx.result(text, items=items, links=[_link(ctx, f"/month/{m}")])


def _series(ctx, uid: str) -> list[dict]:
    rows = recurring.scan_recurring(ctx.conn, uid)["recurring"]
    return [{"merchant": (s["sample_description"] or "")[:80], "amount": s["amount_avg"], "frequency": s["frequency"],
             "next": s["next_date"], "last": s["last_date"], "category": s["category"], "account": s["account_name"]}
            for s in rows]


@tools.tool("finance.recurring",
            "The person's recurring charges (subscriptions, insurance, utilities): typical amount, how often, and when "
            "the next one is expected.",
            args={"person": _PERSON}, returns="charges with amount, frequency, next expected date",
            examples=("What subscriptions do we pay for?",))
def recurring_charges(ctx):
    uid, owner = _owner(ctx)
    rows = sorted(_series(ctx, uid), key=lambda s: (s["next"] or "9999", -s["amount"]))
    if not rows:
        return ctx.result(f"No recurring charges found{_whose(owner, ctx)}.", links=[_link(ctx, "/recurring")])
    monthly = sum(s["amount"] for s in rows if s["frequency"] == "Monthly")
    text = (f"{len(rows)} recurring charges{_whose(owner, ctx)}, {_money(monthly)} a month in monthly ones: "
            + "; ".join(f"{s['merchant']} {_money(s['amount'])} {(s['frequency'] or '').lower()}" for s in rows[:8])
            + ("…" if len(rows) > 8 else "") + ".")
    return ctx.result(text, items=rows, links=[_link(ctx, "/recurring")])


@tools.tool("finance.bills",
            "Recurring charges expected in the next days (default 14), soonest first.",
            args={"days": Arg("number", "how many days ahead, 1 to 60", min=1, max=60), "person": _PERSON},
            returns="charges with expected date and amount", examples=("What bills are coming up?",))
def bills(ctx):
    uid, owner = _owner(ctx)
    days = int(ctx.args.get("days", 14))
    today = date.today()
    until = (today + timedelta(days=days)).isoformat()
    rows = sorted((s for s in _series(ctx, uid) if s["next"] and today.isoformat() <= s["next"] <= until),
                  key=lambda s: s["next"])
    if not rows:
        return ctx.result(f"No recurring charges expected in the next {days} days{_whose(owner, ctx)}.",
                          links=[_link(ctx, "/recurring")])
    total = sum(s["amount"] for s in rows)
    text = (f"{len(rows)} expected in the next {days} days{_whose(owner, ctx)}, about {_money(total)}: "
            + "; ".join(f"{s['next']} {s['merchant']} {_money(s['amount'])}" for s in rows[:10]) + ".")
    return ctx.result(text, items=rows, links=[_link(ctx, "/recurring")])
