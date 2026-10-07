"""What Family Tree answers the Household Assistant (HOUSEHOLD_ASSISTANT_SPEC.md §4.2; the shared
app/common/assist_tools.py).

One tool, `tree.birthdays`: the birthdays and anniversaries coming up, as the Upcoming view lists them for the
asking person (close family when they've said "This is me", else everyone; relationships in their own language).
Names, dates, ages and relationships only — no photos, contact details or notes. Answered only while the admin's
*Answer the Household Assistant* is on and the person hasn't turned off *Let the Household Assistant answer for me*
(Settings). Family Tree's page doesn't open sub-paths, so its links open the app itself.
"""
import re
from datetime import date

from . import config, db, graph as graph_mod, kin, settings, upcoming
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
    "tree", actor=_actor, enabled=lambda conn: bool(settings.get("assistant_answers")),
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
    link = ctx.link("Upcoming in Family Tree")
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
