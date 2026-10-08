"""What Family Tree answers the Household Assistant (HOUSEHOLD_ASSISTANT_SPEC.md §4.2; the shared
app/common/assist_tools.py).

Two tools:

- `tree.birthdays`: the birthdays and anniversaries coming up, as the Upcoming view lists them for the asking person
  (close family when they've said "This is me", else everyone; relationships in their own language).
- `tree.relation`: how two people are related, as "How are they related?" shows it — both ways, in the person's
  own language, with the chain that links them. Names may be misspelt or partial (find_people.py); "me", "my
  mother" or "Ravi's wife" work too. Several people with the name → the choices, so the assistant can ask which.

Names, dates, ages and relationships only — no photos, contact details or notes. Answered only while the admin's
*Answer the Household Assistant* is on and the person hasn't turned off *Let the Household Assistant answer for me*
(Settings). Its link opens Upcoming on the app's sidebar page.
"""
import re
from datetime import date

from . import config, dates, db, find_people, graph as graph_mod, kin, relations, settings, upcoming
from .common import app_bus as bus
from .common import assist_tools
from .common.assist_tools import Arg

_PANEL_RE = re.compile(r"^/[a-z0-9]{1,16}_family_tree$")


def _busy() -> None:
    if db._import_lock.locked():                        # a backup is being restored right now
        raise bus.Nack("busy", "restoring")


def _actor(conn, uid: str):
    r = conn.execute("SELECT id, name, disabled, me_person_id, kin_lang, assistant_ok FROM users WHERE id = ?",
                     (uid,)).fetchone()
    if r is None or r["disabled"]:
        return None
    return {"id": r["id"], "name": r["name"], "me_person_id": r["me_person_id"], "kin_lang": r["kin_lang"],
            "assistant_ok": bool(r["assistant_ok"])}


def _panel():
    return config.INGRESS_PANEL if _PANEL_RE.match(config.INGRESS_PANEL or "") else None


tools = assist_tools.Catalogue(
    "tree", targets=[r"/upcoming", r"/relate/[A-Za-z0-9_-]{1,64}/[A-Za-z0-9_-]{1,64}"], actor=_actor, enabled=lambda conn: bool(settings.get("assistant_answers")),
    person_enabled=lambda conn, user: user["assistant_ok"], panel=_panel, busy=_busy)


def _when(d: str, days_away: int) -> str:
    if days_away == 0:
        return "today"
    if days_away == 1:
        return "tomorrow"
    day = date.fromisoformat(d)
    return f"{day.strftime('%a')} {day.day} {day.strftime('%b')}"


@tools.tool("tree.birthdays",
            "Birthdays and wedding anniversaries coming up in the family tree, soonest first, with ages and how each "
            "person is related to the asker. `days` looks ahead (default 30).",
            args={"days": Arg("number", "how many days ahead, 1 to 90", min=1, max=90),
                  "everyone": Arg("boolean", "the whole tree instead of close family")},
            returns="dates with who, what (birthday, anniversary), age or years, relationship",
            examples=("Whose birthday is coming up?",), children=True)
def birthdays(ctx):
    days = int(ctx.args.get("days", 30))
    scope = "all" if ctx.args.get("everyone") else "close"
    lang = kin.user_lang(ctx.user)
    g = graph_mod.get(ctx.conn)
    me = ctx.user["me_person_id"] if ctx.user["me_person_id"] in g.people else None
    rows = upcoming.entries(g, config.today(), days, me, scope, remembrance=False, lang=lang)
    link = ctx.link("Upcoming in Family Tree", "/upcoming")
    whose = "the whole tree" if scope == "all" or not me else "close family"
    if not rows:
        return ctx.result(f"No birthdays or anniversaries in the next {days} days ({whose}).", links=[link])
    items = []
    for e in rows:
        rel = ", ".join(p["relationship"] for p in e["people"] if p.get("relationship"))
        items.append({"date": e["date"], "in_days": e["daysAway"], "what": e["kind"], "title": e["title"],
                      "years": e["years"], "relationship": rel or None})
    text = (f"{len(rows)} coming up in the next {days} days ({whose}): "
            + "; ".join(f"{_when(e['date'], e['daysAway'])}: {e['title']}" for e in rows[:8])
            + ("…" if len(rows) > 8 else "") + ".")
    return ctx.result(text, items=items, links=[link])


MAX_PATH = 12          # links of the chain shown


def _who(g, pid: str, me: str | None, lang: str) -> str:
    """A name with enough to tell two people of that name apart: born when, whose child, what they are to you."""
    p = g.people[pid]
    bits = []
    year = (dates.to_parts(p.birth) or {}).get("y") if p.birth else None
    if year:
        bits.append(f"born {year}")
    parents = [g.people[x].given or g.people[x].name for x, _f, rel in g.parents(pid) if x and rel == "birth"]
    if parents:
        bits.append(f"{'son' if p.gender == 'male' else 'daughter' if p.gender == 'female' else 'child'} of "
                    + " and ".join(parents[:2]))
    if me and me != pid:
        label = kin.label_for(g, me, pid, lang)
        if label and label not in ("not related",):
            bits.append(f"your {label}")
    return p.name + (f" ({', '.join(bits)})" if bits else "")


def _said(spoken: str, g, pid: str, how: str) -> str | None:
    """A note when the name was taken loosely, so the answer can say who was meant."""
    if how == "close":
        return f"“{spoken}” taken as {g.people[pid].name}"
    return None


def _line(who: str, whose: str, r: dict) -> str:
    label = r.get("label") or r.get("english")
    extra = r.get("meaning") if r.get("term") and r.get("meaning") else (
        r.get("english") if r.get("english") and r.get("english") != label else None)
    poss = "your" if whose == "you" else f"{whose}'s"
    verb = "are" if who == "You" else "is"
    return f"{who} {verb} {poss} {label}" + (f" ({extra})" if extra else "")


@tools.tool("tree.relation",
            "How two people in the family tree are related to each other, both ways, with the family words in the "
            "asker's language and the chain of parents, children and marriages that links them. Names can be "
            "misspelt, partial or nicknames; \"me\", \"my mother\" or \"Ravi's wife\" work too. Leave out `to` to "
            "compare with the asker. If several people fit a name it lists them so you can ask which one.",
            args={"person": Arg("string", "the first person, as the asker said it", required=True, max_length=80),
                  "to": Arg("string", "the second person; leave out to mean the asker", max_length=80)},
            returns="each person's relationship to the other, the linking chain, or the people a name could mean",
            examples=("How is Ravi related to Sita?", "What is Lakshmi to me?", "How am I related to Venkat?"),
            children=True)
def relation(ctx):
    lang = kin.user_lang(ctx.user)
    g = graph_mod.get(ctx.conn)
    me = ctx.user["me_person_id"] if ctx.user["me_person_id"] in g.people else None
    found, notes = [], []
    for spoken in (ctx.args["person"], ctx.args.get("to") or "me"):
        r = find_people.find(ctx.conn, g, spoken, me, lang)
        if "id" in r:
            found.append(r["id"])
            note = _said(spoken, g, r["id"], r["how"])
            if note:
                notes.append(note)
            continue
        if r.get("me"):
            return ctx.result("I don't know who you are in the family tree: say both names, or choose yourself "
                              "with “This is me” on your person in Family Tree.",
                              links=[ctx.link("How are they related? in Family Tree")])
        if "choices" in r:
            choices = [_who(g, pid, me, lang) for pid in r["choices"]]
            return ctx.result(f"More than one person could be “{spoken}”: " + "; ".join(choices)
                              + ". Ask which one is meant (a surname or a parent's name will do).",
                              items=[{"could_be": c} for c in choices],
                              links=[ctx.link("How are they related? in Family Tree")])
        near = [g.people[pid].name for pid in r["none"]]
        return ctx.result(f"Nobody in the family tree is called “{spoken}”."
                          + (f" The nearest names: {', '.join(near)}." if near else ""),
                          items=[{"nearest": n} for n in near],
                          links=[ctx.link("How are they related? in Family Tree")])
    b, a = found                                        # what `person` (b) is to `to` (a)
    link = ctx.link("How are they related? in Family Tree", f"/relate/{a}/{b}")
    name = lambda pid: "you" if pid == me else g.people[pid].name
    lead = ("; ".join(notes) + ". ") if notes else ""
    if a == b:
        return ctx.result(lead + f"{name(a).capitalize() if a == me else name(a)} — that's the same person twice.",
                          links=[link])
    ab = kin.describe(g, a, relations.relationship(g, a, b), lang, ctx.conn)
    ba = kin.describe(g, b, relations.relationship(g, b, a), lang, ctx.conn)
    if ab["kind"] == "none":
        return ctx.result(lead + f"{name(b).capitalize() if b == me else name(b)} and {name(a)} aren't related in "
                          "the family tree: no chain of parents, children or marriages links them yet.",
                          items=[{"person": g.people[b].name, "to": g.people[a].name, "relationship": None}],
                          links=[link])
    path = ab.get("path") or []
    chain = " → ".join([path[0]["name"]] + [f"{s['rel']}: {s['name']}" for s in path[1:MAX_PATH + 1]]) \
        + (" → …" if len(path) > MAX_PATH + 1 else "") if path else ""
    who_b = "You" if b == me else g.people[b].name
    who_a = "You" if a == me else g.people[a].name
    text = (lead + _line(who_b, name(a), ab) + ". " + _line(who_a, name(b), ba) + "."
            + (f" The link: {chain}." if len(path) > 2 else ""))
    item = {"person": g.people[b].name, "to": g.people[a].name, "relationship": ab.get("label"),
            "english": ab.get("english"), "meaning": ab.get("meaning"), "back": ba.get("label"),
            "back_english": ba.get("english"), "link": chain or None}
    return ctx.result(text, items=[item], links=[link])
