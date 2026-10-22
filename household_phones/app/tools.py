"""What Household Phones answers the Household Assistant (HOUSEHOLD_ASSISTANT_SPEC.md §4; the shared
app/common/assist_tools.py; spec/SPEC.md §10).

- `phones.status`: "is my phone online?", "which phones are offline?", "what's Asha's phone battery?" — the phones the
  person may see: admins every phone, everyone else their own and the phones that forward into their chats. A phone
  whose owner paused being watched answers "paused by <owner>"; one in its owner's do-not-monitor hours "not monitored
  until 07:00". Devices no admin added to monitoring aren't known here at all.
- `phones.check`: "check Asha's phone" runs Check now (same rights) and answers with the result when one came in the
  last minute, else that the phone was asked, with a link to the live result card (#/check/<phone>).

- `phones.find`: "ring my phone", "find Asha's phone" — Find my phone with the same rights as the button (the owner;
  admins when the owner allows it), after the answer is committed.

Forwarded messages are never answered: they aren't stored.
"""
import re
import threading

from . import actions, auth, checknow, config, dnm, monitor, phones, settings
from .common import app_bus as bus
from .common import assist_tools
from .common.assist_tools import Arg

_PANEL_RE = re.compile(r"^/[a-z0-9]{1,16}_household_phones$")


def _busy() -> None:
    from . import db
    if db._import_lock.locked():
        raise bus.Nack("busy", "restoring")


def _actor(conn, uid: str):
    r = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    return auth.user_dict(r) if r else None


def _panel():
    from . import sidebar
    return sidebar.PAGE if _PANEL_RE.match(sidebar.PAGE or "") else None


tools = assist_tools.Catalogue(
    "phones", targets=[r"/", r"/check/[a-z0-9]{1,16}", r"/phone/[a-z0-9]{1,16}", r"/phones"],
    actor=_actor, enabled=lambda conn: bool(settings.get("assistant_answers")), panel=_panel, busy=_busy)


def visible(conn, user: dict) -> list:
    rows = conn.execute("SELECT * FROM phones WHERE retired = 0 AND monitored = 1 ORDER BY first_seen").fetchall()
    return [p for p in rows if user["is_admin"] or p["owner_id"] == user["id"] or checknow.may_check(p, user)]


def _match(conn, rows, who: str | None, user: dict) -> list:
    if not who:
        return rows
    w = who.lower().strip()
    if w in ("me", "my", "mine", "i", "myself", "my phone"):
        return [p for p in rows if p["owner_id"] == user["id"]]
    w = re.sub(r"('s|’s)?( phone)?$", "", w).strip()
    out = []
    for p in rows:
        owner = (phones.owner_name(conn, p["owner_id"]) or "").lower()
        first = owner.split(" ")[0] if owner else ""
        if w and (w in (owner, first) or w in phones.display_name(conn, p).lower()):
            out.append(p)
    return out


def _ago(ts: float | None) -> str:
    if not ts:
        return "never"
    m = int((config.clock() - ts) // 60)
    return "just now" if m < 1 else f"{m} min ago" if m < 120 else f"{m // 60} h ago"


def line(conn, p) -> tuple[str, dict]:
    name = phones.display_name(conn, p)
    now = config.clock()
    if monitor.watching_paused(p, now):
        owner = phones.first_name(phones.owner_name(conn, p["owner_id"]))
        return f"{name}: paused by {owner} {monitor.until_text(p)}".strip(), {"phone": name, "status": "paused"}
    on = dnm.active(p, now)
    if on:
        return f"{name}: not monitored until {on['untilText']} (do-not-monitor hours)", {"phone": name, "status": "dnm"}
    d = monitor.display(p, now)
    bits = [d["label"], f"last check-in {_ago(p['last_checkin'])}"]
    if p["battery"] is not None:
        bits.append(f"battery {p['battery']}%" + (", charging" if p["charging"] else ""))
    return f"{name}: " + " · ".join(bits), {"phone": name, "status": d["status"], "battery": p["battery"],
                                             "lastCheckIn": config.iso(p["last_checkin"]) if p["last_checkin"] else None}


@tools.tool("phones.status",
            "Whether the household's phones are online, their battery and last check-in (Household Phones). `person`: "
            "a name or me; `which: problems` lists only phones that are offline or not reporting.",
            args={"person": Arg("string", "whose phone: a first name, or me", max_length=60),
                  "which": Arg("enum", "all phones (default) or only the ones with a problem",
                               values=("all", "problems"))},
            returns="each phone's status, battery and last check-in", scope="household", children=True,
            examples=("Is my phone online?", "Which phones are offline?", "What's Asha's phone battery?"),
            quick={"words": ["phone", "phones", "phone online", "phone battery", "phones offline"]})
def status(ctx):
    rows = _match(ctx.conn, visible(ctx.conn, ctx.user), ctx.args.get("person"), ctx.user)
    if ctx.args.get("which") == "problems":
        rows = [p for p in rows if p["status"] in ("offline", "app_not_reporting", "cant_check", "late")]
    link = ctx.link("Phones", "/phones" if ctx.user["is_admin"] else "/")
    if not rows:
        what = "No phone with a problem" if ctx.args.get("which") == "problems" else "No phone you can see"
        return ctx.result(f"{what} matches that in Household Phones.", links=[link])
    lines = [line(ctx.conn, p) for p in rows]
    return ctx.result("; ".join(t for t, _ in lines) + ".", items=[i for _, i in lines], links=[link])


@tools.tool("phones.check",
            "Check now: asks a phone to report at once, as Household Phones' Check now button does. `person`: whose "
            "phone (a first name, or me).",
            args={"person": Arg("string", "whose phone: a first name, or me", required=True, max_length=60)},
            returns="the phone's current status, or that it was asked; a link to the live result", scope="household",
            children=True, examples=("Check Asha's phone", "Is my phone online right now?"))
def check(ctx):
    rows = _match(ctx.conn, visible(ctx.conn, ctx.user), ctx.args["person"], ctx.user)
    if not rows:
        return ctx.result("No phone you can check matches that in Household Phones.",
                          links=[ctx.link("Phones", "/")])
    p = rows[0]
    name = phones.display_name(ctx.conn, p)
    now = config.clock()
    if monitor.watching_paused(p, now) and p["owner_id"] != ctx.user["id"]:
        owner = phones.first_name(phones.owner_name(ctx.conn, p["owner_id"]))
        return ctx.result(f"{name} is paused by {owner} {monitor.until_text(p)}.".replace("  ", " "),
                          links=[ctx.link("Phones", "/")])
    on = dnm.active(p, now)
    if on:
        return ctx.result(checknow.not_monitored_text(ctx.conn, p, on, ctx.user), links=[ctx.link("Phones", "/")])
    recent = ctx.conn.execute("SELECT * FROM checks WHERE phone_id = ? AND state != 'waiting' AND finished >= ? "
                              "ORDER BY started DESC LIMIT 1", (p["id"], now - 60)).fetchone()
    link = ctx.link(f"Check {name}", f"/check/{p['id']}")
    if recent is not None:
        fwd = ctx.conn.execute("SELECT at, chat_name FROM forwards WHERE phone_id = ? ORDER BY at DESC LIMIT 1",
                               (p["id"],)).fetchone()
        c = checknow.card(recent, p, name, None, fwd, None, now)
        bits = [c["title"].split(" ", 1)[-1]] + ([c["text"]] if c["text"] else [])
        if c["battery"] is not None:
            bits.append(f"battery {c['battery']}%")
        if c["forward"]:
            bits.append(c["forward"])
        return ctx.result(f"{name}: " + "; ".join(bits) + ".", links=[link])
    user = dict(ctx.user)

    def run():
        try:
            checknow.start(p["id"], user, via="assistant")
        except Exception:                                              # noqa: BLE001 — the card says why
            pass
    ctx.msg.after_commit(lambda: threading.Thread(target=run, daemon=True).start())
    d = monitor.display(p, now)
    return ctx.result(f"Asked {name} to report now. Until it answers: {d['label']}, last check-in "
                      f"{_ago(p['last_checkin'])}. The result shows in Phones within a minute.", links=[link])


@tools.tool("phones.find",
            "Find my phone: rings a phone loudly, even on silent or Do Not Disturb, until someone taps Found it "
            "(Household Phones). `person`: whose phone (a first name, or me). Only the owner, or admins when the owner "
            "allows it.",
            args={"person": Arg("string", "whose phone: a first name, or me", required=True, max_length=60)},
            returns="whether the phone was rung", acts=True, scope="household", children=True,
            examples=("Ring my phone", "Find Asha's phone", "Where's my phone? Make it ring"))
def find(ctx):
    rows = _match(ctx.conn, [p for p in ctx.conn.execute("SELECT * FROM phones WHERE retired = 0 AND monitored = 1 "
                                                         "ORDER BY first_seen").fetchall()
                             if ctx.user["is_admin"] or p["owner_id"] == ctx.user["id"]],
                  ctx.args["person"], ctx.user)
    if not rows:
        return ctx.result("No phone you can ring matches that in Household Phones.", links=[ctx.link("Phones", "/")])
    p = rows[0]
    name = phones.display_name(ctx.conn, p)
    now = config.clock()
    try:
        actions.check(ctx.conn, p, ctx.user, "find", now)
        actions.rate(ctx.conn, p, "find", now)
    except actions.Refused as e:
        return ctx.result(f"{name}: {e.text}", links=[ctx.link("Phones", "/")])
    user = dict(ctx.user)

    def run():
        try:
            actions.run(p["id"], "find", {}, user, via="assistant")
        except Exception:                                              # noqa: BLE001 — the log says why
            pass
    ctx.msg.after_commit(lambda: threading.Thread(target=run, daemon=True).start())
    return ctx.result(f"Ringing {name} now — loudly, even on silent. Tap Found it on the phone to stop it.",
                      links=[ctx.link("Phones", "/")])
