"""What Splitpot answers the Household Assistant (HOUSEHOLD_ASSISTANT_SPEC.md §4.2; the shared
app/common/assist_tools.py).

- `splitpot.balances`: who owes whom in the groups the asking person is in (the group page's settle-up), and
  where they stand overall;
- `splitpot.recent`: the newest expenses and payments in those groups (what, who paid, how much, their share),
  like the group page's first page of 20;
- `splitpot.expense.add` (acts): adds an expense to one of their groups, split equally — only after the person taps
  the proposed change in the assistant, checked as the Add expense form checks it, logged and notified the same.

Only the groups the person is a member of, matched to their Home Assistant login (`users.ha_user_id`), never by
name. Money is private, so the admin's *Answer the Household Assistant* is **off** until turned on; each person can
also turn it off for themselves (My settings). Links open each group on the app's sidebar page (`/group/<id>`).
"""
import re
from datetime import datetime, timedelta, timezone

from . import config
from .common import app_bus as bus
from .common import assist_tools
from .common.assist_tools import Arg

PAGE = 20                                     # the group page's first page (main.LEDGER_PAGE)
_PANEL_RE = re.compile(r"^/[a-z0-9]{1,16}_splitpot$")


def _main():
    from . import main                        # at call time: main imports this module
    return main


def _actor(conn, uid: str):
    r = conn.execute("SELECT id, name, disabled, assistant_ok FROM users WHERE ha_user_id = ?",
                     (uid.strip().lower(),)).fetchone()
    if r is None or r["disabled"]:
        return None
    return {"id": r["id"], "name": r["name"], "assistant_ok": bool(r["assistant_ok"])}


def _panel():
    return config.SIDEBAR_PAGE if _PANEL_RE.match(config.SIDEBAR_PAGE or "") else None


tools = assist_tools.Catalogue(
    "splitpot", targets=[r"/group/[A-Za-z0-9_-]{1,64}"], actor=_actor, enabled=lambda conn: bool(_main().get_setting("assistant_answers")),
    person_enabled=lambda conn, user: user["assistant_ok"], panel=_panel)

_GROUP = Arg("string", "a group's name; leave out for all the person's groups", max_length=60)


def _groups(ctx) -> list:
    """The person's groups (id, name), or the one named in `group` (any case); Nack not_found otherwise."""
    rows = ctx.conn.execute("SELECT g.id, g.name FROM groups g JOIN group_members m ON m.group_id = g.id "
                            "WHERE m.user_id = ? ORDER BY g.is_default DESC, g.created_at", (ctx.user["id"],)).fetchall()
    want = ctx.args.get("group")
    if want is not None:
        rows = [r for r in rows if r["name"].strip().lower() == want.strip().lower()]
        if not rows:
            raise bus.Nack("not_found", "group")
    return rows


def _links(ctx, groups) -> list:
    """Each group on the app's page, or the app itself when there is none."""
    return [ctx.link(f"{g['name']} in Splitpot", f"/group/{g['id']}") for g in groups[:assist_tools.MAX_LINKS]] \
        or [ctx.link("Splitpot")]


def _names(conn) -> dict:
    return {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM users")}


@tools.tool("splitpot.balances",
            "Who owes whom in the Splitpot groups the person is in, after the simplest settle-up, and what the person "
            "owes or is owed overall.",
            args={"group": _GROUP},
            returns="per group: transfers (from, to, amount) and the person's net",
            examples=("Who owes me money?",))
def balances(ctx):
    m = _main()
    groups = _groups(ctx)
    link = ctx.link("Splitpot")
    if not groups:
        return ctx.result(f"{ctx.user['name']} isn't in any Splitpot group.", links=[link])
    names, me = _names(ctx.conn), ctx.user["id"]
    items, lines, overall = [], [], 0.0
    for g in groups:
        net, transfers = m.settle_up(m.group_net(ctx.conn, g["id"], m.group_member_ids(ctx.conn, g["id"])))
        mine = net.get(me, 0.0)
        overall += mine
        for t in transfers:
            items.append({"group": g["name"], "from": names.get(t["from"], "Unknown"),
                          "to": names.get(t["to"], "Unknown"), "amount": t["amount"]})
        if not transfers:
            lines.append(f"{g['name']}: all settled")
            continue
        parts = []
        for t in transfers:
            who_from = "you" if t["from"] == me else names.get(t["from"], "Unknown")
            who_to = "you" if t["to"] == me else names.get(t["to"], "Unknown")
            verb = "owe" if t["from"] == me else "owes"
            parts.append(f"{who_from} {verb} {who_to} {m.money(t['amount'])}")
        lines.append(f"{g['name']}: " + "; ".join(parts))
    if abs(overall) < 0.005:
        stand = f"{ctx.user['name']} is square overall"
    elif overall > 0:
        stand = f"{ctx.user['name']} is owed {m.money(overall)} overall"
    else:
        stand = f"{ctx.user['name']} owes {m.money(-overall)} overall"
    return ctx.result(f"{stand}. " + ". ".join(lines) + ".", items=items, links=_links(ctx, groups))


@tools.tool("splitpot.recent",
            "The newest expenses and settle-up payments in the person's Splitpot groups: what, who paid, how much and "
            "the person's share.",
            args={"group": _GROUP, "days": Arg("number", "only the last N days, 1 to 90", min=1, max=90)},
            returns="entries newest first (at most 20): date, group, what, paid by, amount, your share",
            examples=("What did we spend on the trip?",))
def recent(ctx):
    m = _main()
    groups = _groups(ctx)
    link = ctx.link("Splitpot")
    since = None
    if "days" in ctx.args:
        since = datetime.now(timezone.utc) - timedelta(days=int(ctx.args["days"]))
    entries, more = [], False
    for g in groups:
        page, cursor = m.ledger_page(ctx.conn, g["id"], PAGE)
        more = more or bool(cursor)
        for e in page:
            at = m.parse_date(e["date"])                    # a UTC timestamp or a local calendar day
            if since and at < since:
                continue
            share = next((s["amount"] for s in e["splits"] if s["userId"] == ctx.user["id"]), None)
            entries.append({"date": at.astimezone(m.tz()).date().isoformat(), "group": g["name"],
                            "what": "payment" if e["splitType"] == "payment" else e["description"],
                            "paid_by": e["paidByName"], "amount": e["amount"], "your_share": share,
                            "_at": at})
    entries.sort(key=lambda x: x["_at"], reverse=True)
    if len(entries) > PAGE:
        entries, more = entries[:PAGE], True
    for x in entries:
        del x["_at"]
    if not entries:
        when = f" in the last {int(ctx.args['days'])} days" if "days" in ctx.args else ""
        return ctx.result(f"Nothing in {ctx.user['name']}'s Splitpot groups{when}.", links=[link])
    spent = sum(x["amount"] for x in entries if x["what"] != "payment")
    text = (f"{len(entries)} recent entries (spent {m.money(spent)}): "
            + "; ".join(f"{x['date']} {x['what']} {m.money(x['amount'])} paid by {x['paid_by']} ({x['group']})"
                        for x in entries[:6]) + ("…" if len(entries) > 6 else "") + ".")
    shown = {x["group"] for x in entries}
    return ctx.result(text, items=entries, links=_links(ctx, [g for g in groups if g["name"] in shown]), more=more)


def _member(ctx, members: list, text: str):
    """The group member (id, name) a name means: "me", a full name or a first name (any case); None if nobody."""
    want = " ".join(text.split()).casefold()
    if want in ("me", "i", "myself", "you"):
        return next(((u, n) for u, n in members if u == ctx.user["id"]), None)
    exact = [(u, n) for u, n in members if n.casefold() == want]
    first = [(u, n) for u, n in members if n.casefold().split()[:1] == [want]]
    hits = exact or first
    return hits[0] if len(hits) == 1 else None


@tools.tool("splitpot.expense.add",
            "Adds an expense to one of the person's Splitpot groups, split equally (after they confirm it). Leave out "
            "`paid_by` when the person paid and `split_with` to split with everyone in the group.",
            args={"description": Arg("string", "what it was for", required=True, max_length=200),
                  "amount": Arg("number", "the amount paid", required=True, min=0.01, max=1_000_000),
                  "group": _GROUP,
                  "paid_by": Arg("string", "who paid (default the person asking)", max_length=60),
                  "split_with": Arg("string", "who shares it, names separated by commas (default everyone in the "
                                              "group); include \"me\" when the person asking shares it",
                                    max_length=300),
                  "date": Arg("date", "the day (default today)")},
            acts=True, returns="the added expense: group, who paid, each share")
def expense_add(ctx):
    m = _main()
    groups = _groups(ctx)
    link = ctx.link("Splitpot")
    if not groups:
        return ctx.result(f"{ctx.user['name']} isn't in any Splitpot group. Nothing was added.", links=[link])
    if len(groups) > 1 and "group" not in ctx.args:
        default = ctx.conn.execute("SELECT id, name FROM groups WHERE is_default = 1 AND id IN (%s)"
                                   % ",".join("?" * len(groups)), [g["id"] for g in groups]).fetchone()
        if default is None:
            return ctx.result("Which group? " + ", ".join(g["name"] for g in groups) + ". Nothing was added.",
                              links=_links(ctx, groups))
        groups = [default]
    g = groups[0]
    ids = m.group_member_ids(ctx.conn, g["id"])
    names = _names(ctx.conn)
    members = [(u, names.get(u, "Unknown")) for u in ids]
    payer = _member(ctx, members, ctx.args.get("paid_by") or "me")
    if payer is None:
        return ctx.result(f"Nobody in {g['name']} is called “{ctx.args.get('paid_by')}”. Members: "
                          + ", ".join(n for _, n in members) + ". Nothing was added.", links=_links(ctx, [g]))
    participants = list(ids)
    if ctx.args.get("split_with"):
        participants = []
        for part in [p for p in ctx.args["split_with"].replace(" and ", ",").split(",") if p.strip()]:
            who = _member(ctx, members, part)
            if who is None:
                return ctx.result(f"Nobody in {g['name']} is called “{part.strip()}”. Members: "
                                  + ", ".join(n for _, n in members) + ". Nothing was added.", links=_links(ctx, [g]))
            participants.append(who[0])
    description = " ".join(ctx.args["description"].split())
    if not description:
        raise bus.Nack("invalid", "description")
    try:
        when = m.resolve_expense_date(ctx.args.get("date"))
        payload = m.ExpenseCreate(description=description, amount=float(ctx.args["amount"]), paidBy=payer[0],
                                  splitType="equal", participants=participants)
        splits, split_type = m.build_splits(ids, payload)
    except m.HTTPException as e:
        return ctx.result(f"{e.detail}. Nothing was added.", links=_links(ctx, [g]))
    except ValueError:
        raise bus.Nack("invalid", "amount") from None
    eid = m.new_id()
    amount = round(payload.amount, 2)
    ctx.conn.execute("INSERT INTO expenses (id, group_id, description, amount, paid_by, split_type, date) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?)", (eid, g["id"], description, amount, payer[0], split_type, when))
    m.write_splits(ctx.conn, eid, splits)
    actor = ctx.user["name"]
    message = f"{actor} added '{description}' ({m.money(amount)}) to {g['name']} with the Household Assistant"
    if payer[1].casefold() != actor.casefold():
        message += f", paid by {payer[1]}"
    m.log_event(ctx.conn, "expense_added", message, group_id=g["id"], actor=actor)
    ha_id = ctx.msg.data.get("requested_by")
    ctx.msg.after_commit(m.push_balances_to_ha)
    ctx.msg.after_commit(lambda: m.notify_new_charge(eid, ha_id, actor))
    shares = [{"who": names.get(sp["userId"], "Unknown"), "share": sp["amount"]} for sp in splits]
    text = (f"Added “{description}” ({m.money(amount)}) to {g['name']}, paid by "
            f"{'you' if payer[0] == ctx.user['id'] else payer[1]}, split equally: "
            + ", ".join(f"{x['who']} {m.money(x['share'])}" for x in shares) + ".")
    return ctx.result(text, items=shares, links=_links(ctx, [g]))
