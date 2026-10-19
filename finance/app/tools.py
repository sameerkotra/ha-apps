"""What Finance Dashboard answers the Household Assistant (HOUSEHOLD_ASSISTANT_SPEC.md §4.2; the shared
app/common/assist_tools.py).

- `finance.summary`: a month's income, spending, net and top categories — the Overview's numbers;
- `finance.spending`: a month's spending by category, or one category's largest charges;
- `finance.recurring`: the recurring charges with their next expected date and amount;
- `finance.bills`: the recurring charges expected in the next N days;
- `finance.merchant`: what was spent at one shop or company (matched in the bank's description, any case, spaces
  ignored) over the last N months: the total, each month's, and the latest charges;
- `finance.balances`: each account's balance as its latest statement printed it (cards: what's owed);
- `finance.statements`: each account's expected statement schedule, its last upload, the next expected date, the
  latest cycle's status (statement_due.cycle_status) and the Upload strip's missing months;
- `finance.payments_due`: each credit card's payment due date and whether it's paid (card_payments.status), the last
  payment (date and amount, never its description) and what the latest statement said was owed;
- `finance.balance_history`: one account's statement balances over time, or net worth (balances.py);
- `finance.subscription_changes`: price changes and new subscriptions (subscriptions.py), dismissed ones marked;
- `finance.dates`: every statement, card payment and recurring charge coming up, overdue first;
- `finance.overview`: net worth, what's due, this month so far and subscription changes, each part short;
- `finance.expense.add` (acts): a hand-entered spend on one of the person's own accounts ("I spent 200 on fuel at
  Shell"), the same row the Uploads page's *Add one by hand* makes — only after the person taps the proposed change,
  and never on someone else's accounts.

Whose data: the asking person's own, or — with `person` — one whose data an admin shared with them here (the same
user_access grants the pages use; an admin's "view as anyone" never applies to the assistant). Only clean rows,
with transfers and excluded rows left out, as the Overview counts them. **Never** a transaction's note (text a person
typed): only the bank's description (the merchant), date, amount, category and account. Money is private, so the
admin's *Answer the Household Assistant* is **off** until turned on, and each person can turn off *Let the Household
Assistant answer for me* (Who am I). Links open the month's dashboard, Home, Recurring, an account's page or Upload on
the app's sidebar page. Only live, unarchived accounts; an account number never shows beyond its last 4 digits.

Quick answers (`quick`, HOUSEHOLD_ASSISTANT_SPEC.md §17.4): the new tools give plain words only, no concept. The
Assistant reads a tool's concept alone as strongly as its built-in rule for that concept, so BILL, BALANCE, RECURRING
or PRICE here would turn "Any bills due?", "What's the balance on my accounts?" or "What subscriptions do we pay for?"
into a Did-you-mean; and no word the Assistant already uses for Todo or the older tools ("what's due", "overdue",
"payment due", "how much do I owe", …). household_assistant/tests/test_finance_quick.py routes them with this catalogue
(its tests/data/finance_tools.json, which tests/test_assistant_tools.py checks is this one). The tool list must stay
within assist_tools.MAX_CATALOGUE (7 KB), hence the short descriptions.
"""
from __future__ import annotations

import calendar
import re
from datetime import date, timedelta
from types import SimpleNamespace

from . import balances as balances_mod
from . import card_payments, recurring, settings, statement_due, subscriptions, upload_page
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
    "finance", targets=[r"/month/\d{4}-(0[1-9]|1[0-2])", r"/recurring", r"/dashboard", r"/accounts/\d{1,12}",
                        r"/uploads(/\d{1,12})?"], actor=_actor,
    enabled=lambda conn: settings.get_bool("assistant_answers"), person_enabled=lambda conn, user: user["assistant_ok"],
    panel=_panel)

_PERSON = Arg("string", "a shared owner (default the asker)", max_length=100)
_MONTH = Arg("month", "the month (default this month)")
_ACCOUNT = Arg("string", "one account (name or last 4)", max_length=100)


def _owner(ctx) -> tuple[str, str]:
    """(user id, name) whose data to answer from: the person's own, or a shared owner named in `person` --
    only while that owner hasn't turned off "Let the Household Assistant answer for me" (an owner who has
    looks the same as one who isn't shared)."""
    want = ctx.args.get("person")
    if not want or want.casefold() in (ctx.user["name"].casefold(), ctx.user["id"].casefold(), "me"):
        return ctx.user["id"], ctx.user["name"]
    rows = ctx.conn.execute(
        "SELECT a.owner_id AS id, COALESCE(k.name, a.owner_id) AS name FROM user_access a "
        "LEFT JOIN known_users k ON k.id = a.owner_id WHERE a.viewer_id = ? AND COALESCE(k.assistant_ok, 1) = 1",
        (ctx.user["id"],)).fetchall()
    # The user id decides first; a display name only when exactly one shared owner has it (two
    # people may share a name, and a name may look like someone else's id).
    wanted = want.casefold()
    by_id = [r for r in rows if r["id"].casefold() == wanted]
    by_name = [r for r in rows if r["name"].casefold() == wanted]
    found = by_id or (by_name if len(by_name) == 1 else [])
    if found:
        return found[0]["id"], found[0]["name"]
    raise bus.Nack("not_found", "person")                  # not shared with them looks like nobody


def _month(ctx) -> tuple[str, str, str]:
    m = ctx.args.get("month") or statement_due.today().strftime("%Y-%m")
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


def _month_numbers(ctx, uid: str, start: str, end: str) -> tuple[dict, float, float, list]:
    """(spend per category, income, spending, the top 5): the Overview's numbers for one month."""
    spend = _by_category(ctx, uid, start, end, SPEND, SPEND_AMOUNT)
    income = round(sum(_by_category(ctx, uid, start, end, INCOME, "t.amount").values()), 2)
    total = round(sum(spend.values()), 2)
    return spend, income, total, sorted(spend.items(), key=lambda kv: -kv[1])[:5]


@tools.tool("finance.summary",
            "One month's income, spending, net and top categories (how did we do, a named month).",
            args={"month": _MONTH, "person": _PERSON},
            returns="income, spending, net, the top 5 categories", examples=("How did we do this month?",))
def summary(ctx):
    uid, owner = _owner(ctx)
    m, start, end = _month(ctx)
    spend, income, total, top = _month_numbers(ctx, uid, start, end)
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


def _squash(text: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", (text or "").casefold())


# ------------------------------------------------------------------ shared by the statement, card and balance tools

def _today() -> date:
    """statement_due's clock (Home Assistant's local day; the tests replace statement_due.now)."""
    return statement_due.today()


def _mask(text) -> str:
    """Any run of five or more digits masked: never an account number."""
    return re.sub(r"\d{5,}", "····", str(text or ""))


def _safe(text) -> str:
    """A bank description or account name, masked (_mask), on one line."""
    return _mask(" ".join(str(text or "").split()))


def _acct(a) -> str:
    """"Visa ••4242": the account's name and, when known, its last 4 digits (never more)."""
    return _safe(a["name"]) + (f" ••{a['account_number_last4']}" if a["account_number_last4"] else "")


def _accounts(ctx, uid: str, cards: bool = False) -> list:
    """The owner's live, unarchived accounts (only credit cards with `cards`), by name."""
    return ctx.conn.execute(
        "SELECT * FROM accounts WHERE user_id = ? AND deleted_at IS NULL AND is_archived = 0"
        + (" AND type = 'credit_card'" if cards else "") + " ORDER BY name COLLATE NOCASE", (uid,)).fetchall()


def _pick(rows, want: str):
    """The account `want` names: its name (any case), its last 4 digits, else the only one whose name contains it."""
    w = want.casefold().strip()
    digits = w.lstrip("•x* ")
    return next(iter([r for r in rows if r["name"].casefold() == w]
                     or [r for r in rows if digits.isdigit() and len(digits) == 4
                         and (r["account_number_last4"] or "") == digits]
                     or ([] if len([r for r in rows if w in r["name"].casefold()]) != 1
                         else [r for r in rows if w in r["name"].casefold()])), None)


def _no_such(ctx, rows, owner: str, links: list):
    return ctx.result(f"No account called “{ctx.args['account']}”{_whose(owner, ctx)}. "
                      + ("Accounts: " + ", ".join(_acct(r) for r in rows[:10]) + "." if rows else ""), links=links)


def _none_yet(ctx, owner: str) -> str:
    return ("You have no accounts in Finance yet." if owner == ctx.user["name"]
            else f"{owner} has no accounts in Finance yet.")


def _home(ctx):
    return ctx.link("Home in Finance Dashboard", "/dashboard")


def _account_link(ctx, a):
    return ctx.link(f"{_safe(a['name'])[:50]} in Finance Dashboard", f"/accounts/{a['id']}")


def _upload_link(ctx, a=None):
    return ctx.link("Upload in Finance Dashboard", f"/uploads/{a['id']}" if a is not None else "/uploads")


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def _on_day(day: int) -> str:
    return "the last day" if day == 0 else f"the {_ordinal(day)}"


def _in_days(n: int) -> str:
    return "today" if n == 0 else (f"in {n} day{'s' if n != 1 else ''}" if n > 0
                                   else f"{-n} day{'s' if n != -1 else ''} ago")


def _owed(v: float) -> str:
    """What a card's balance means: owed, nothing owed, or in credit."""
    return f"{_money(v)} owed" if v > 0 else ("nothing owed" if v == 0 else f"{_money(-v)} in credit")


def _points(ctx, a, on: date) -> list[dict]:
    """The account's statement balances up to today (balances.py: the account page's and Home's)."""
    return [p for p in balances_mod.statement_points(ctx.conn, a["id"]) if p["date"] <= on]


# ------------------------------------------------------------------ recurring charges

def _raw_series(ctx, uid: str, account_id: int | None = None) -> list[dict]:
    """The recurring series on the owner's live, unarchived accounts."""
    live = {a["id"] for a in _accounts(ctx, uid)}
    return [s for s in recurring.scan_recurring(ctx.conn, uid)["recurring"]
            if s["account_id"] in live and (account_id is None or s["account_id"] == account_id)]


def _row(s: dict) -> dict:
    return {"merchant": _mask((s["sample_description"] or "")[:80]), "amount": s["amount_avg"], "frequency": s["frequency"],
            "next": s["next_date"], "last": s["last_date"], "category": s["category"], "account": s["account_name"]}


def _series(ctx, uid: str) -> list[dict]:
    return [_row(s) for s in _raw_series(ctx, uid)]


def _per_year(s: dict) -> float | None:
    """Charges a year: 365.25 days over the cadence's (recurring.frequency_totals' periods)."""
    period = recurring._PERIOD_DAYS.get(s["frequency"]) or s.get("avg_gap_days") or 0
    return 365.25 / period if period > 0 else None


def _last_change(s: dict, price_changes: dict) -> tuple[float, date] | None:
    """(the old amount, the day the new one started) of the series' last price change, or None: the one
    subscriptions.py found (dismissed or not; it follows a series carried on at a new price), else the series' own
    last change of amount. A variable bill (electricity) has none."""
    c = price_changes.get((s["account_id"], s["merchant_key"]))
    latest = round(abs(s["txns"][-1]["amount"]), 2) if s["txns"] else None
    if c is not None and latest is not None and not subscriptions._differs(c["new"], latest):
        return c["old"], c["since"]
    amounts = [round(abs(t["amount"]), 2) for t in s["txns"]]
    if len(amounts) < 2 or subscriptions._variable(amounts):
        return None
    for i in range(len(amounts) - 1, 0, -1):
        if subscriptions._differs(amounts[i], amounts[i - 1]):
            return amounts[i - 1], s["txns"][i]["date"]
    return None


@tools.tool("finance.recurring",
            "Recurring charges (subscriptions, insurance, utilities): amount, how often, next date, yearly cost, "
            "price change.",
            args={"merchant": Arg("string", "one merchant", max_length=100), "account": _ACCOUNT, "person": _PERSON},
            returns="charges, totals a month and a year",
            examples=("What subscriptions do we pay for?", "How much is Netflix?",
                      "How much do we spend on subscriptions a year?"),
            # the assistant's quick answers (HOUSEHOLD_ASSISTANT_SPEC.md §17.4)
            quick={"concepts": ["RECURRING"], "words": ["standing orders", "direct debits", "regular payments",
                                                        "monthly payments", "insurance payments",
                                                        "subscriptions a year"]})
def recurring_charges(ctx):
    uid, owner = _owner(ctx)
    account_id, scope = None, ""
    if ctx.args.get("account"):
        rows = _accounts(ctx, uid)
        a = _pick(rows, ctx.args["account"])
        if a is None:
            return _no_such(ctx, rows, owner, [_link(ctx, "/recurring")])
        account_id, scope = a["id"], f" on {_acct(a)}"
    raw = _raw_series(ctx, uid, account_id)
    if ctx.args.get("merchant"):
        want = _squash(ctx.args["merchant"])
        raw = [s for s in raw if want and (want in _squash(s["sample_description"]) or want in _squash(s["merchant_key"]))]
        scope += f" for “{ctx.args['merchant']}”"
    pairs = sorted(((s, _row(s)) for s in raw), key=lambda p: (p[1]["next"] or "9999", -p[1]["amount"]))
    if not pairs:
        return ctx.result(f"No recurring charges found{_whose(owner, ctx)}{scope}.", links=[_link(ctx, "/recurring")])
    price_changes = {}
    for c in subscriptions.changes(ctx.conn, uid, _today(), include_dismissed=True):
        if c["kind"] == "price":
            price_changes.setdefault((c["account_id"], c["series"]), c)
    rows, parts = [], []
    for s, r in pairs:
        per_year = _per_year(s)
        r["yearly"] = round(s["amount_avg"] * per_year, 2) if per_year else None
        change = _last_change(s, price_changes)
        r["was"], r["until"] = (change[0], change[1].isoformat()) if change else (None, None)
        extra = [f"≈ {_money(r['yearly'])} a year"] if r["yearly"] is not None else []
        if change:
            extra.append(f"was {_money(change[0])} until {statement_due.label(change[1])}")
        parts.append(f"{r['merchant']} {_money(r['amount'])} {(r['frequency'] or '').lower()}"
                     + (f" ({', '.join(extra)})" if extra else ""))
        rows.append(r)
    monthly = sum(s["amount"] for s in rows if s["frequency"] == "Monthly")
    month_total = recurring.frequency_totals([s for s, _r in pairs])["total"]["monthly"]
    text = (f"{len(rows)} recurring charges{_whose(owner, ctx)}{scope}, {_money(monthly)} a month in monthly ones: "
            + "; ".join(parts[:8]) + ("…" if len(rows) > 8 else "") + "."
            + f" In all about {_money(month_total)} a month, {_money(month_total * 12)} a year.")
    return ctx.result(text, items=rows, links=[_link(ctx, "/recurring")])


# ------------------------------------------------------------------ card payments (section 30)

def _card(ctx, a, on: date) -> dict:
    """One credit card's payment, as card_payments.status sees it today: the due date that matters (the previous one
    while it's overdue, else the next), its state, the payment that paid it, the last payment and the latest
    statement's balance. Never a payment's description."""
    out = {"account": _acct(a), "account_id": a["id"], "due_day": a["payment_due_day"], "due_date": None,
           "days": None, "status": "not set", "paid_on": None, "paid_amount": None, "previous": None,
           "last_payment": None, "last_payment_amount": None, "statement_balance": None, "statement_date": None}
    last = ctx.conn.execute(
        "SELECT date, amount FROM transactions WHERE account_id = ? AND deleted_at IS NULL AND amount < 0 "
        "AND date <= ? ORDER BY date DESC, id DESC LIMIT 1", (a["id"], on.isoformat())).fetchone()
    if last is not None:
        out["last_payment"], out["last_payment_amount"] = str(last["date"])[:10], round(-last["amount"], 2)
    points = _points(ctx, a, on)
    if points:
        out["statement_balance"], out["statement_date"] = points[-1]["balance"], points[-1]["date"].isoformat()
    st = card_payments.status(ctx.conn, a, on)
    if st is None:
        return out
    prev, nxt = st["previous"], st["next"]
    cur = prev if prev["state"] == "unpaid" and nxt["days"] > 0 else nxt
    out.update(due_date=cur["due_date"].isoformat(), days=cur["days"],
               status={"paid": "paid", "dismissed": "marked as paid"}.get(cur["state"], "unpaid"))
    if cur["paid_on"] is not None:
        r = ctx.conn.execute("SELECT amount FROM transactions WHERE account_id = ? AND deleted_at IS NULL AND amount < 0 "
                             "AND date = ? ORDER BY id LIMIT 1", (a["id"], cur["paid_on"].isoformat())).fetchone()
        out["paid_on"], out["paid_amount"] = cur["paid_on"].isoformat(), round(-r["amount"], 2) if r else None
    if cur is nxt and prev["state"] in ("paid", "dismissed"):
        out["previous"] = (f"{statement_due.label(prev['due_date'])} "
                           + (f"paid {statement_due.label(prev['paid_on'])}" if prev["state"] == "paid"
                              else "marked as paid"))
    return out


def _card_when(c: dict) -> str:
    d = statement_due.label(date.fromisoformat(c["due_date"]))
    return f"was due {d}, {_in_days(c['days'])}" if c["days"] < 0 else f"{d} ({_in_days(c['days'])})"


def _statement_owed(c: dict) -> str:
    if c["statement_balance"] is None:
        return ""
    return f"last statement {_money(c['statement_balance'])}"


def _card_text(c: dict) -> str:
    if c["due_date"] is None:
        text = f"{c['account']}: payment due day not set"
    else:
        state = c["status"]
        if state == "paid":
            state = f"paid {statement_due.label(date.fromisoformat(c['paid_on']))}" + (
                f" ({_money(c['paid_amount'])})" if c["paid_amount"] is not None else "")
        text = f"{c['account']} (due {_on_day(c['due_day'])}): {_card_when(c)}, {state}"
        if c["previous"]:
            text += f"; before that {c['previous']}"
    if c["statement_balance"] is not None:
        text += (f"; latest statement {_owed(c['statement_balance'])} "
                 f"({statement_due.label(date.fromisoformat(c['statement_date']))})")
    if c["last_payment"] and c["last_payment"] != c["paid_on"]:
        text += (f"; last payment {_money(c['last_payment_amount'])} on "
                 f"{statement_due.label(date.fromisoformat(c['last_payment']))}")
    return text


def _due_cards(ctx, uid: str, on: date) -> list[dict]:
    """The owner's cards with a due day whose payment is still to make: unpaid, not marked as paid."""
    return [c for c in (_card(ctx, a, on) for a in _accounts(ctx, uid, cards=True) if card_payments.is_set(a))
            if c["due_date"] is not None and c["status"] == "unpaid"]


@tools.tool("finance.bills",
            "Recurring charges and card payments due in the next days (default 14), soonest first.",
            args={"days": Arg("number", "how many days ahead, 1 to 60", min=1, max=60), "person": _PERSON},
            returns="charges with expected date and amount", examples=("What bills are coming up?",))
def bills(ctx):
    uid, owner = _owner(ctx)
    days = int(ctx.args.get("days", 14))
    today = _today()
    until = (today + timedelta(days=days)).isoformat()
    rows = sorted((s for s in _series(ctx, uid) if s["next"] and today.isoformat() <= s["next"] <= until),
                  key=lambda s: s["next"])
    cards = [c for c in _due_cards(ctx, uid, today) if 0 <= c["days"] <= days]
    if not rows and not cards:
        return ctx.result(f"No recurring charges expected in the next {days} days{_whose(owner, ctx)}.",
                          links=[_link(ctx, "/recurring")])
    lines = [(s["next"], f"{s['next']} {s['merchant']} {_money(s['amount'])}", dict(s, kind="recurring"))
             for s in rows]
    lines += [(c["due_date"], f"{c['due_date']} {c['account']} payment (card payment"
               + (f", {_statement_owed(c)})" if c["statement_balance"] is not None else ")"),
               {"kind": "card_payment", "merchant": f"{c['account']} payment", "amount": c["statement_balance"],
                "next": c["due_date"], "account": c["account"]}) for c in cards]
    lines.sort(key=lambda x: x[0])
    total = sum(s["amount"] for s in rows)
    plus = f" in recurring charges, plus {len(cards)} card payment{'s' if len(cards) != 1 else ''}" if cards else ""
    head = (f"{len(rows)} expected in the next {days} days{_whose(owner, ctx)}, about {_money(total)}{plus}: " if rows
            else f"{len(cards)} card payment{'s' if len(cards) != 1 else ''} due in the next {days} days"
                 f"{_whose(owner, ctx)}: ")
    text = head + "; ".join(t for _d, t, _i in lines[:10]) + "."
    return ctx.result(text, items=[i for _d, _t, i in lines], links=[_link(ctx, "/recurring")])


@tools.tool("finance.merchant",
            "Spending at one shop or company over the last months: the total, per month, the latest charges.",
            args={"merchant": Arg("string", "the shop or company, e.g. Costco", required=True, max_length=100),
                  "months": Arg("number", "months back (default 12)", min=1, max=24),
                  "person": _PERSON},
            returns="the total, per month, the 10 latest charges",
            examples=("How much did we spend at Costco this year?",))
def merchant(ctx):
    uid, owner = _owner(ctx)
    months = int(ctx.args.get("months", 12))
    today = date.today()
    y, m = today.year, today.month - (months - 1)
    while m < 1:
        y, m = y - 1, m + 12
    start, end = f"{y}-{m:02d}-01", today.isoformat()
    want = _squash(ctx.args["merchant"])
    if len(want) < 2:
        raise bus.Nack("invalid", "merchant")
    rows = [r for r in ctx.conn.execute(
        f"SELECT t.date, t.description, {SPEND_AMOUNT} AS amount, a.name AS account FROM transactions t "
        f"JOIN accounts a ON a.id = t.account_id WHERE {WHERE} AND {SPEND} ORDER BY t.date DESC, t.id DESC",
        (uid, start, end)) if want in _squash(r["description"])]
    span = f"since {_label(start[:7])}"
    if not rows:
        return ctx.result(f"Nothing spent at “{ctx.args['merchant']}” {span}{_whose(owner, ctx)}.",
                          links=[_link(ctx, f"/month/{today.strftime('%Y-%m')}")])
    per_month: dict[str, float] = {}
    for r in rows:
        per_month[r["date"][:7]] = per_month.get(r["date"][:7], 0.0) + r["amount"]
    total = sum(per_month.values())
    items = [{"date": r["date"], "merchant": (r["description"] or "")[:80], "amount": round(r["amount"], 2),
              "account": r["account"]} for r in rows[:10]]
    items += [{"month": k, "total": round(v, 2)} for k, v in sorted(per_month.items())]
    text = (f"{_money(total)} at “{ctx.args['merchant']}” {span}{_whose(owner, ctx)}, in {len(rows)} charge"
            f"{'s' if len(rows) != 1 else ''}: " + "; ".join(f"{_label(k)} {_money(v)}"
                                                              for k, v in sorted(per_month.items(), reverse=True)[:6])
            + ". Latest: " + "; ".join(f"{r['date']} {_money(r['amount'])}" for r in rows[:3]) + ".")
    latest = max(per_month)
    return ctx.result(text, items=items, links=[_link(ctx, f"/month/{latest}")])


@tools.tool("finance.balances",
            "Each account's balance as its latest statement printed it (a card: what was owed).",
            args={"person": _PERSON}, returns="accounts with type, last 4 digits, balance and statement month",
            examples=("What's the balance on my accounts?",),
            quick={"concepts": ["BALANCE"], "words": ["bank balance", "money in the bank", "credit card balance",
                                                      "savings account", "current account"]})
def balances(ctx):
    uid, owner = _owner(ctx)
    accounts = ctx.conn.execute("SELECT id, name, type, account_number_last4 FROM accounts WHERE user_id = ? AND "
                                "deleted_at IS NULL AND is_archived = 0 ORDER BY name", (uid,)).fetchall()
    link = _link(ctx, f"/month/{date.today().strftime('%Y-%m')}")
    if not accounts:
        return ctx.result(f"No accounts{_whose(owner, ctx)} yet.", links=[link])
    items, parts = [], []
    # Each account's latest complete statement with a balance, all in one query.
    latest = {r["account_id"]: r for r in ctx.conn.execute(
        "SELECT account_id, statement_period, new_balance FROM ("
        "  SELECT account_id, statement_period, new_balance, ROW_NUMBER() OVER ("
        "    PARTITION BY account_id ORDER BY statement_period DESC, id DESC) AS n "
        "  FROM statements WHERE user_id = ? AND deleted_at IS NULL AND status = 'complete' "
        "  AND new_balance IS NOT NULL) WHERE n = 1", (uid,)).fetchall()}
    for a in accounts:
        st = latest.get(a["id"])
        name = a["name"] + (f" ••{a['account_number_last4']}" if a["account_number_last4"] else "")
        if st is None:
            items.append({"account": name, "type": a["type"], "balance": None, "statement": None})
            parts.append(f"{name}: no statement balance yet")
            continue
        bal = round(st["new_balance"], 2)
        if a["type"] == "credit_card":
            words = f"{_money(bal)} owed" if bal > 0 else ("nothing owed" if bal == 0 else f"{_money(-bal)} in credit")
        else:
            words = _money(bal)
        items.append({"account": name, "type": a["type"], "balance": bal, "statement": st["statement_period"]})
        parts.append(f"{name}: {words} ({st['statement_period']} statement)")
    return ctx.result(f"Balances{_whose(owner, ctx)} from the latest statements: " + "; ".join(parts) + ".",
                      items=items, links=[link])


def _account_for(ctx, uid: str):
    """The account a hand-entered spend goes on: the one named (its name or last 4 digits), else the person's only
    live account, else the one called Cash. None with the names to choose from when it can't tell."""
    rows = ctx.conn.execute("SELECT id, name, type, account_number_last4 FROM accounts WHERE user_id = ? AND "
                            "deleted_at IS NULL AND is_archived = 0 ORDER BY name", (uid,)).fetchall()
    want = (ctx.args.get("account") or "").casefold().strip()
    if want:
        hits = [r for r in rows if r["name"].casefold() == want] or \
            [r for r in rows if want in r["name"].casefold() or (r["account_number_last4"] or "") == want]
    else:
        hits = rows if len(rows) == 1 else [r for r in rows if "cash" in r["name"].casefold()]
    return (hits[0], rows) if len(hits) == 1 else (None, rows)


@tools.tool("finance.expense.add",
            "Adds a spend by hand to one of the person's own accounts, after they confirm it. With several accounts, "
            "name it.",
            args={"amount": Arg("number", "how much was spent", required=True, min=0.01, max=10_000_000),
                  "description": Arg("string", "the shop or what it was for", required=True, max_length=200),
                  "category": Arg("string", "a spending category, e.g. Fuel", max_length=60),
                  "date": Arg("date", "when (default today)"),
                  "account": _ACCOUNT},
            acts=True, returns="the spend, or the accounts to choose from",
            examples=("I spent 200 on fuel at Shell",))
def expense_add(ctx):
    from .categorize import UNCATEGORIZED
    from .matching import relink
    from .routes.transactions import _insert_manual_transaction
    uid = ctx.user["id"]                                   # always the asker's own accounts
    account, rows = _account_for(ctx, uid)
    if not rows:
        return ctx.result("There are no accounts here to add it to yet — add one in Finance Dashboard first.")
    if account is None:
        names = ", ".join(r["name"] for r in rows[:8])
        return ctx.result(f"Which account was it on? Yours are: {names}. Say it again with the account's name.",
                          items=[{"account": r["name"], "type": r["type"]} for r in rows[:8]])
    amount = round(float(ctx.args["amount"]), 2)
    signed = amount if account["type"] == "credit_card" else -amount   # a card's spend is positive, a bank's negative
    day = ctx.args.get("date") or date.today().isoformat()
    category = (ctx.args.get("category") or "").strip()
    ok, result = _insert_manual_transaction(ctx.conn, uid, account["id"], day, str(signed),
                                            " ".join(ctx.args["description"].split()), category or UNCATEGORIZED,
                                            category_source="manual" if category else None)
    if not ok:
        raise bus.Nack("invalid", "expense")
    relink(ctx.conn, uid)
    month = day[:7]
    return ctx.result(f"Added {_money(amount)} at {ctx.args['description']}" + (f" ({category})" if category else "")
                      + f" on {day} to {account['name']}.",
                      items=[{"account": account["name"], "date": day, "amount": amount,
                              "description": ctx.args["description"], "category": category or None}],
                      links=[_link(ctx, f"/month/{month}")])


# ------------------------------------------------------------------ expected statements (section 27)

def _schedule(a) -> str:
    if not statement_due.is_set(a):
        return "not set"
    every, day = a["expected_every_months"], _on_day(a["expected_day"])
    if every == 1:
        return f"monthly on {day}"
    if every == 12:
        return f"yearly on {day}"
    return f"every {every} months on {day}"


def _last_statement(ctx, a) -> dict | None:
    """The newest upload that counts for the account (statement_due.last_upload's rules): when, what kind, and the
    period it covered (its statement month, or its newest transaction's date)."""
    s = ctx.conn.execute(
        "SELECT s.uploaded_at AS at, s.source, s.statement_period, s.period_end, (SELECT MAX(t.date) FROM transactions t "
        "WHERE t.statement_id = s.id AND t.deleted_at IS NULL) AS last_txn FROM statements s WHERE s.account_id = ? "
        "AND s.deleted_at IS NULL AND s.status != 'error' ORDER BY s.uploaded_at DESC, s.id DESC LIMIT 1",
        (a["id"],)).fetchone()
    c = ctx.conn.execute(
        "SELECT c.imported_at AS at, (SELECT MAX(t.date) FROM transactions t WHERE t.csv_import_id = c.id "
        "AND t.account_id = ? AND t.deleted_at IS NULL) AS last_txn FROM csv_imports c WHERE c.deleted_at IS NULL "
        "AND EXISTS (SELECT 1 FROM transactions t WHERE t.csv_import_id = c.id AND t.account_id = ? "
        "AND t.deleted_at IS NULL) ORDER BY c.imported_at DESC, c.id DESC LIMIT 1", (a["id"], a["id"])).fetchone()
    found = []
    if s is not None:
        ym = statement_due.parse_month(s["statement_period"])
        end = balances_mod._iso(s["period_end"]) or balances_mod._iso(s["last_txn"])
        period = (f"{calendar.month_abbr[ym[1]]} {ym[0]}" if ym else
                  f"to {statement_due.label(end)}" if end else "")
        found.append((statement_due.local_from_utc(s["at"]), "QFX" if s["source"] == "qfx" else "PDF", period))
    if c is not None:
        end = balances_mod._iso(c["last_txn"])
        found.append((statement_due.local_from_utc(c["at"]), "CSV", f"to {statement_due.label(end)}" if end else ""))
    found = [f for f in found if f[0] is not None]
    if not found:
        return None
    at, kind, period = max(found, key=lambda f: f[0])
    return {"uploaded": at.date(), "kind": kind, "period": period}


_STATUS_ORDER = {"overdue": 0, "due": 1, "upcoming": 2, "dismissed": 3, "received": 4, "not set": 5}


def _statement(ctx, a, on: date, missing: list[str]) -> dict:
    st = statement_due.cycle_status(ctx.conn, a, on)
    last = _last_statement(ctx, a)
    status = st["status"] if st else "not set"
    out = {"account": _acct(a), "schedule": _schedule(a), "status": status,
           "days_overdue": st["days_overdue"] if st else 0,
           "expected": st["cycle_date"].isoformat() if st and st["cycle_date"] else None,
           "next": st["next_date"].isoformat() if st and st["next_date"] else None,
           "last_upload": last["uploaded"].isoformat() if last else None, "last_kind": last["kind"] if last else None,
           "last_period": last["period"] if last else None, "missing": ", ".join(missing)}
    parts = [out["schedule"]]
    if status == "overdue":
        n = out["days_overdue"]
        parts.append(f"overdue {n} day{'s' if n != 1 else ''} (expected {statement_due.label(st['cycle_date'])})")
    elif status == "due":
        parts.append("due today")
    elif status in ("dismissed", "received"):
        parts.append(f"{status} (expected {statement_due.label(st['cycle_date'])})")
    if last:
        parts.append(f"last uploaded {statement_due.label(last['uploaded'])} ({last['kind']}"
                     + (f", {last['period']}" if last["period"] else "") + ")")
    else:
        parts.append("nothing uploaded yet")
    if st and st["next_date"]:
        parts.append(f"next expected {statement_due.label(st['next_date'])}")
    if missing:
        parts.append("nothing uploaded for " + ", ".join(missing))
    out["text"] = f"{out['account']}: " + "; ".join(parts)
    return out


@tools.tool("finance.statements",
            "Expected statement dates, overdue or not, the last upload, months missing.",
            args={"account": _ACCOUNT, "person": _PERSON}, returns="accounts with schedule and status",
            examples=("Which statements are overdue?",),
            quick={"words": ["statement due", "statement date", "last statement", "missing statement",
                             "overdue statement", "statements overdue", "statements are overdue",
                             "missing any statements", "last upload"]})
def statements(ctx):
    uid, owner = _owner(ctx)
    on = _today()
    rows = _accounts(ctx, uid)
    if not rows:
        return ctx.result(_none_yet(ctx, owner), links=[_upload_link(ctx)])
    one = None
    if ctx.args.get("account"):
        one = _pick(rows, ctx.args["account"])
        if one is None:
            return _no_such(ctx, rows, owner, [_upload_link(ctx)])
    person = SimpleNamespace(id=uid, is_admin=False)
    gaps = {c["id"]: c["missing"] for c in upload_page.strip_cards(ctx.conn, person, person, on)}
    out = [_statement(ctx, a, on, gaps.get(a["id"], [])) for a in ([one] if one else rows)]
    out.sort(key=lambda s: _STATUS_ORDER.get(s["status"], 9))           # (stable: by name within each)
    links = [_upload_link(ctx, one)]
    items = [{k: v for k, v in s.items() if k != "text"} for s in out]
    if one is None and not any(statement_due.is_set(a) for a in rows):
        mine = "your" if not _whose(owner, ctx) else f"{owner}'s"
        return ctx.result(f"None of {mine} accounts has an expected statement date. You can set one on the account's "
                          "page. Last uploads: "
                          + "; ".join(f"{s['account']} " + (statement_due.label(date.fromisoformat(s["last_upload"]))
                                                            if s["last_upload"] else "none yet") for s in out) + ".",
                          items=items, links=links)
    overdue = sum(1 for s in out if s["status"] == "overdue")
    head = f"Statements{_whose(owner, ctx)}" + (f", {overdue} overdue" if overdue else "") + ": "
    return ctx.result(head + ". ".join(s["text"] for s in out) + ".", items=items, links=links)


@tools.tool("finance.payments_due",
            "Credit card payment due dates, paid or not, and what the statement said was owed.",
            args={"account": _ACCOUNT, "days": Arg("number", "due within N days", min=1, max=60), "person": _PERSON},
            returns="cards with due date and status", examples=("Did I pay the Visa?",),
            quick={"words": ["card payment", "card payments", "pay the card", "credit card bill",
                             "paid the card"], "args": {"days": 60}})
def payments_due(ctx):
    uid, owner = _owner(ctx)
    on = _today()
    rows = _accounts(ctx, uid, cards=True)
    if not rows:
        return ctx.result("You have no credit card accounts in Finance." if owner == ctx.user["name"]
                          else f"{owner} has no credit card accounts in Finance.", links=[_home(ctx)])
    if ctx.args.get("account"):
        a = _pick(rows, ctx.args["account"])
        if a is None:
            return _no_such(ctx, rows, owner, [_home(ctx)])
        rows = [a]
    cards = [(a, _card(ctx, a, on)) for a in rows]
    unset = ""
    if "days" in ctx.args:
        days = int(ctx.args["days"])
        names = [c["account"] for _a, c in cards if c["due_date"] is None]
        unset = f" No payment due day set: {', '.join(names)}." if names else ""
        cards = [(a, c) for a, c in cards if c["days"] is not None and c["days"] <= days]
        if not cards:
            return ctx.result(f"No card payments due in the next {days} days{_whose(owner, ctx)}.{unset}",
                              links=[_home(ctx)])
    cards.sort(key=lambda ac: (ac[1]["due_date"] or "9999", ac[1]["account"].casefold()))
    text = f"Card payments{_whose(owner, ctx)}: " + ". ".join(_card_text(c) for _a, c in cards) + "." + unset
    return ctx.result(text, items=[{k: v for k, v in c.items() if k != "account_id"} for _a, c in cards],
                      links=[_account_link(ctx, a) for a, _c in cards[:5]])


# ------------------------------------------------------------------ balances over time and net worth (section 28)

def _months_back(on: date, months: int) -> date:
    y, m = on.year, on.month - months
    while m < 1:
        y, m = y - 1, m + 12
    return min(on, date(y, m, calendar.monthrange(y, m)[1]))


def _net_series(ctx, uid: str, on: date, months: int) -> list[dict]:
    """Net worth at the last `months` month-ends, as balances.net_worth works out its line (the same accounts, each
    one's balance carried forward)."""
    histories = []
    for a in _accounts(ctx, uid):
        points = _points(ctx, a, on)
        if points:
            histories.append((-1 if balances_mod.is_card(a) else 1, points))
    out = []
    for d in balances_mod._month_ends(on, months):
        known = [sign * p["balance"] for sign, p in ((s, balances_mod.balance_at(ps, d)) for s, ps in histories)
                 if p is not None]
        out.append({"date": d, "value": round(sum(known), 2) if known else None})
    return out


def _move(change: float) -> str:
    return "no change" if change == 0 else f"{'up' if change > 0 else 'down'} {_money(abs(change))}"


def _net_worth(ctx, uid: str, on: date, months: int = balances_mod.MONTHS, limit: int | None = None,
               whose: str = "") -> dict | None:
    """{text, items, total}: net worth and each account's latest balance (balances.net_worth), or None when no
    account has a balance yet."""
    nw = balances_mod.net_worth(ctx.conn, uid, on)
    if nw["total"] is None:
        return None
    series = nw["months"] if months == balances_mod.MONTHS else _net_series(ctx, uid, on, months)
    start = next((m for m in series if m["value"] is not None), None)
    cash = round(sum(r["signed"] for r in nw["accounts"] if r["signed"] is not None and r["type"] != "credit_card"), 2)
    owed = round(sum(r["balance"] for r in nw["accounts"] if r["balance"] is not None and r["type"] == "credit_card"), 2)
    names = {a["id"]: _acct(a) for a in _accounts(ctx, uid)}
    text = f"Net worth{whose} {_money(nw['total'])} (cash {_money(cash)}, cards {_owed(owed)})"
    if start is not None and start["date"] != series[-1]["date"]:
        text += f", {_move(round(nw['total'] - start['value'], 2))} since {balances_mod.date_label(start['date'])}"
    shown = nw["accounts"] if limit is None else nw["accounts"][:limit]
    parts = []
    for r in shown:
        if r["balance"] is None:
            parts.append(f"{names[r['id']]} no balance yet (left out)")
        elif r["type"] == "credit_card":
            parts.append(f"{names[r['id']]} {_owed(r['balance'])} ({balances_mod.date_label(r['as_of'])})")
        else:
            parts.append(f"{names[r['id']]} {_money(r['balance'])} ({balances_mod.date_label(r['as_of'])})")
    more = len(nw["accounts"]) - len(shown)
    text += ". " + "; ".join(parts) + (f"; and {more} more" if more > 0 else "") + "."
    items = [{"account": names[r["id"]], "type": r["type"], "balance": r["balance"],
              "as_of": r["as_of"].isoformat() if r["as_of"] else None, "counts": r["signed"]} for r in nw["accounts"]]
    return {"text": text, "items": items, "total": nw["total"],
            "change": round(nw["total"] - start["value"], 2) if start is not None else None}


NO_BALANCES = "No statement balances yet — balances come from uploaded statements."


@tools.tool("finance.balance_history",
            "Balance history (an account's over time or at a month's end) or, with no account, net worth.",
            args={"account": _ACCOUNT, "month": Arg("month", "at this month's end"),
                  "months": Arg("number", "months back (default 12)", min=1, max=24), "person": _PERSON},
            returns="balances and the change", examples=("What's our net worth?",),
            quick={"words": ["net worth", "balance history", "owe on my cards", "balance changed",
                             "balance over time", "balance at the end"]})
def balance_history(ctx):
    uid, owner = _owner(ctx)
    on = _today()
    months = int(ctx.args.get("months", balances_mod.MONTHS))
    rows = _accounts(ctx, uid)
    if not rows:
        return ctx.result(_none_yet(ctx, owner), links=[_home(ctx)])
    if not ctx.args.get("account"):
        if ctx.args.get("month"):
            m = ctx.args["month"]
            end = min(on, date(int(m[:4]), int(m[5:7]), calendar.monthrange(int(m[:4]), int(m[5:7]))[1]))
            nw = _net_worth(ctx, uid, end, months, whose=_whose(owner, ctx))
            if nw is None:
                return ctx.result(NO_BALANCES, links=[_home(ctx)])
            return ctx.result(f"At the end of {_label(m)}: " + nw["text"], items=nw["items"], links=[_home(ctx)])
        nw = _net_worth(ctx, uid, on, months, whose=_whose(owner, ctx))
        if nw is None:
            return ctx.result(NO_BALANCES, links=[_home(ctx)])
        return ctx.result(nw["text"], items=nw["items"], links=[_home(ctx)])
    a = _pick(rows, ctx.args["account"])
    if a is None:
        return _no_such(ctx, rows, owner, [_home(ctx)])
    name, card = _acct(a), balances_mod.is_card(a)
    words = _owed if card else _money
    points = _points(ctx, a, on)
    if not points:
        return ctx.result(f"{name}: " + NO_BALANCES, links=[_account_link(ctx, a)])
    if ctx.args.get("month"):
        m = ctx.args["month"]
        end = date(int(m[:4]), int(m[5:7]), calendar.monthrange(int(m[:4]), int(m[5:7]))[1])
        p = balances_mod.balance_at(points, end)
        if p is None:
            return ctx.result(f"{name}{_whose(owner, ctx)} had no statement balance by the end of {_label(m)}.",
                              links=[_account_link(ctx, a)])
        st = ctx.conn.execute("SELECT statement_period, source FROM statements WHERE id = ?",
                              (p["statement_id"],)).fetchone()
        text = (f"{name}{_whose(owner, ctx)} at the end of {_label(m)}: {words(p['balance'])}, from the statement "
                f"ending {balances_mod.date_label(p['date'])} ({'QFX' if st['source'] == 'qfx' else 'PDF'}).")
        return ctx.result(text, items=[{"account": name, "date": p["date"].isoformat(), "balance": p["balance"],
                                        "statement": st["statement_period"]}], links=[_account_link(ctx, a)])
    start = _months_back(on, months)
    window = [p for p in points if p["date"] > start][-24:]
    base = balances_mod.balance_at(points, start) or (window[0] if window else None)
    latest = points[-1]
    text = f"{name}{_whose(owner, ctx)}: {words(latest['balance'])} on {balances_mod.date_label(latest['date'])}"
    change = None
    if base is not None and base is not latest:
        change = round(latest["balance"] - base["balance"], 2)
        text += (f", {_move(change)} since {balances_mod.date_label(base['date'])} "
                 f"({words(base['balance'])})")
    if window:
        text += (f". Statements in the last {months} months: "
                 + "; ".join(f"{balances_mod.date_label(p['date'])} {_money(p['balance'])}"
                             for p in reversed(window[-12:])) + ("…" if len(window) > 12 else ""))
    items = [{"account": name, "date": p["date"].isoformat(), "balance": p["balance"]} for p in reversed(window)]
    return ctx.result(text + ".", items=items, links=[_account_link(ctx, a)])


# ------------------------------------------------------------------ subscription changes (section 29)

def _changes(ctx, uid: str, on: date, days: int, merchant: str | None = None) -> list[dict]:
    """subscriptions.changes from the last `days` days, dismissed ones included and marked."""
    dismissed = subscriptions._dismissed(ctx.conn, uid)
    since = on - timedelta(days=days)
    want = _squash(merchant) if merchant else None
    out = []
    for c in subscriptions.changes(ctx.conn, uid, on, include_dismissed=True):
        when = c.get("became") or c["since"]
        if when < since or (want and want not in _squash(c["name"]) and want not in _squash(c["series"])):
            continue
        c = dict(c, dismissed=(c["account_id"], c["series"], c["change_key"]) in dismissed)
        c["line"] = (subscriptions.text(c, _safe(c["name"])) + f", {_safe(c['account_name'])}"
                     + (" (dismissed)" if c["dismissed"] else ""))
        out.append(c)
    return out


def _change_item(c: dict) -> dict:
    return {"kind": c["kind"], "merchant": _safe(c["name"]), "old": c["old"], "new": c["new"], "pct": c["pct"],
            "since": c["since"].isoformat(), "account": _safe(c["account_name"]), "frequency": c.get("frequency"),
            "dismissed": c["dismissed"]}


@tools.tool("finance.subscription_changes",
            "Subscription price changes, bills outside their usual range, new subscriptions.",
            args={"merchant": Arg("string", "one merchant", max_length=100),
                  "days": Arg("number", "days back (default 90)", min=1, max=365), "person": _PERSON},
            returns="changes", examples=("Did any subscription go up?",),
            # ("went up" / "go up" are the Assistant's COMPARE words: those questions go to the AI)
            quick={"words": ["price change", "price increase", "new subscription", "got more expensive",
                             "changed its price"], "args": {"days": 90}})
def subscription_changes(ctx):
    uid, owner = _owner(ctx)
    days = int(ctx.args.get("days", subscriptions.CHANGE_DAYS))
    found = _changes(ctx, uid, _today(), days, ctx.args.get("merchant"))
    link = _link(ctx, "/recurring")
    if not found:
        what = f" for “{ctx.args['merchant']}”" if ctx.args.get("merchant") else ""
        return ctx.result(f"No subscription price changes or new subscriptions{what} in the last {days} days"
                          f"{_whose(owner, ctx)}.", links=[link])
    text = (f"{len(found)} subscription change{'s' if len(found) != 1 else ''} in the last {days} days"
            f"{_whose(owner, ctx)}: " + "; ".join(c["line"] for c in found[:10]) + ("…" if len(found) > 10 else "") + ".")
    return ctx.result(text, items=[_change_item(c) for c in found], links=[link])


# ------------------------------------------------------------------ every date coming up (§6a) and the overview (§6b)

def _dates(ctx, uid: str, on: date, days: int) -> list[dict]:
    """Statements expected, card payments due and recurring charges expected in the next `days` days, plus anything
    overdue; received, paid, marked as paid and dismissed left out. Overdue first, then soonest first."""
    until = on + timedelta(days=days)
    out = []
    for a in _accounts(ctx, uid):
        st = statement_due.cycle_status(ctx.conn, a, on)
        if st is None:
            continue
        name = _acct(a)
        if st["status"] == "overdue":
            n = st["days_overdue"]
            out.append({"date": st["cycle_date"], "kind": "statement", "account": name, "overdue": True,
                        "text": f"{name} statement — expected {statement_due.label(st['cycle_date'])}, "
                                f"overdue {n} day{'s' if n != 1 else ''}"})
        elif st["status"] == "due":
            out.append({"date": on, "kind": "statement", "account": name, "overdue": False,
                        "text": f"{name} statement — expected today ({statement_due.label(on)})"})
        if st["next_date"] and st["next_date"] <= until:
            d = st["next_date"]
            out.append({"date": d, "kind": "statement", "account": name, "overdue": False,
                        "text": f"{name} statement — expected {statement_due.label(d)} ({_in_days((d - on).days)})"})
    for c in _due_cards(ctx, uid, on):
        d = date.fromisoformat(c["due_date"])
        if d > until:
            continue
        owed = f", {_statement_owed(c)}" if c["statement_balance"] is not None else ""
        if c["days"] < 0:
            n = -c["days"]
            text = f"{c['account']} payment — was due {statement_due.label(d)}, overdue {n} day{'s' if n != 1 else ''}"
        else:
            text = f"{c['account']} payment — {statement_due.label(d)} ({_in_days(c['days'])})"
        out.append({"date": d, "kind": "card payment", "account": c["account"], "overdue": c["days"] < 0,
                    "text": text + owed})
    for s in _series(ctx, uid):
        if not s["next"]:
            continue
        d = date.fromisoformat(s["next"])
        if on <= d <= until:
            out.append({"date": d, "kind": "bill", "account": _safe(s["account"]), "overdue": False,
                        "text": f"{s['merchant']} — about {statement_due.label(d)}, {_money(s['amount'])}, "
                                f"{_safe(s['account'])}"})
    kinds = {"statement": 0, "card payment": 1, "bill": 2}
    out.sort(key=lambda x: (not x["overdue"], x["date"], kinds[x["kind"]], x["text"].casefold()))
    return out


def _count_line(found: list[dict], days: int) -> str:
    overdue = sum(1 for x in found if x["overdue"])
    rest = len(found) - overdue
    if overdue and rest:
        return f"{overdue} overdue, {rest} more in the next {days} days."
    if overdue:
        return f"{overdue} overdue, nothing else in the next {days} days."
    return f"{rest} in the next {days} days."


def _date_item(x: dict) -> dict:
    return {"date": x["date"].isoformat(), "kind": x["kind"], "account": x["account"], "overdue": x["overdue"],
            "what": x["text"]}


DATES_DAYS = 14


@tools.tool("finance.dates",
            "Every finance date coming up: statements, card payments, bills; overdue first.",
            args={"days": Arg("number", "days ahead (default 14)", min=1, max=60), "person": _PERSON},
            returns="dates", examples=("What's due?",),
            quick={"words": ["finance dates", "any due dates", "dates coming up", "due in finance", "do in finance",
                             "overdue in finance"], "args": {"days": 14}})
def dates(ctx):
    uid, owner = _owner(ctx)
    days = int(ctx.args.get("days", DATES_DAYS))
    found = _dates(ctx, uid, _today(), days)
    if not found:
        return ctx.result(f"Nothing due in the next {days} days, and nothing overdue{_whose(owner, ctx)}.",
                          links=[_home(ctx)])
    text = (f"Coming up{_whose(owner, ctx)}: " + "; ".join(x["text"] for x in found[:15])
            + ("…" if len(found) > 15 else "") + ". " + _count_line(found, days))
    return ctx.result(text, items=[_date_item(x) for x in found], links=[_home(ctx)])


OVERVIEW_ACCOUNTS, OVERVIEW_DATES, OVERVIEW_CHANGES, OVERVIEW_CHANGE_DAYS = 10, 8, 3, 30


@tools.tool("finance.overview",
            "Finance overview: net worth, what's due, this month so far, subscription changes. Not for one month.",
            args={"person": _PERSON}, returns="the overview", examples=("Give me a finance summary",),
            quick={"words": ["finance summary", "finance overview", "how are my finances", "money overview",
                             "where do i stand", "financial summary"]})
def overview(ctx):
    uid, owner = _owner(ctx)
    on = _today()
    if not _accounts(ctx, uid):
        return ctx.result(_none_yet(ctx, owner), links=[_home(ctx)])
    parts, items = [], []
    nw = _net_worth(ctx, uid, on, limit=OVERVIEW_ACCOUNTS, whose=_whose(owner, ctx))
    if nw is not None:
        parts.append(nw["text"])
        items += [dict(i, part="net worth") for i in nw["items"]]
    found = _dates(ctx, uid, on, DATES_DAYS)
    if found:
        more = len(found) - OVERVIEW_DATES
        parts.append("Due: " + "; ".join(x["text"] for x in found[:OVERVIEW_DATES])
                     + (f"; and {more} more — ask for the finance dates" if more > 0 else "") + ". "
                     + _count_line(found, DATES_DAYS))
        items += [dict(_date_item(x), part="due") for x in found]
    m = on.strftime("%Y-%m")
    spend, income, total, _top = _month_numbers(ctx, uid, f"{m}-01", f"{m}-{calendar.monthrange(on.year, on.month)[1]:02d}")
    if spend or income:
        parts.append(f"{_label(m)} so far: income {_money(income)}, spending {_money(total)}, net "
                     f"{_money(income - total)}.")
        items.append({"part": "this month", "month": m, "income": income, "spending": total,
                      "net": round(income - total, 2)})
    changes = _changes(ctx, uid, on, OVERVIEW_CHANGE_DAYS)
    if changes:
        more = len(changes) - OVERVIEW_CHANGES
        parts.append("Subscription changes: " + "; ".join(c["line"] for c in changes[:OVERVIEW_CHANGES])
                     + (f"; and {more} more" if more > 0 else "") + ".")
        items += [dict(_change_item(c), part="subscription change") for c in changes]
    if not parts:
        return ctx.result(f"Nothing to report yet{_whose(owner, ctx)}: no statement balances, nothing due in the next "
                          f"{DATES_DAYS} days and no transactions in {_label(m)}.", links=[_home(ctx)])
    return ctx.result(" ".join(parts), items=items, links=[_home(ctx)])
