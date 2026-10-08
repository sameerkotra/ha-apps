"""What Arcade answers the Household Assistant (HOUSEHOLD_ASSISTANT_SPEC.md §4.2; the shared
app/common/assist_tools.py).

- `arcade.scores` (household): the leaderboard as the Leaderboard tab shows it — one game's top 10, or who holds
  the record in each game. Only while the leaderboard is on, never to children (their leaderboard can be hidden).
- `arcade.mine` (the person, children too): their own bests and how much they played this week, as My scores.

Answered only while the admin's *Answer the Household Assistant* is on and the person hasn't turned off *Let the
Household Assistant answer for me* (Settings).
"""
import re

from . import config, db, games, scores, settings
from .auth import is_admin_identity
from .common import app_bus as bus
from .common import assist_tools
from .common.assist_tools import Arg

_PANEL_RE = re.compile(r"^/[a-z0-9]{1,16}_household_arcade$")


def _busy() -> None:
    if db._import_lock.locked():                        # a backup is being restored right now
        raise bus.Nack("busy", "restoring")


def _actor(conn, uid: str):
    r = conn.execute("SELECT id, name, username, disabled, is_child, assistant_ok FROM users WHERE id = ?",
                     (uid,)).fetchone()
    if r is None or r["disabled"]:
        return None
    return {"id": r["id"], "name": r["name"], "assistant_ok": bool(r["assistant_ok"]),
            "is_child": bool(r["is_child"]) and not is_admin_identity(r["id"], r["username"])}


def _panel():
    return config.INGRESS_PANEL if _PANEL_RE.match(config.INGRESS_PANEL or "") else None


tools = assist_tools.Catalogue(
    "arcade", targets=[r"/leaderboard", r"/scores"],
    actor=_actor, enabled=lambda conn: bool(settings.get("assistant_answers")),
    person_enabled=lambda conn, user: user["assistant_ok"], panel=_panel, busy=_busy)


def _squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def find_game(text: str) -> str | None:
    """A game by its id or name, ignoring case, spaces and punctuation ("snake duel", "Snake-Duel", "snakeduel")."""
    want = _squash(text)
    for gid in games.GAME_IDS:
        if want in (_squash(gid), _squash(games.name(gid))):
            return gid
    return None


def _num(n) -> str:
    return f"{n:,}"


@tools.tool("arcade.scores",
            "The household leaderboard in Household Arcade. With `game`: that game's top 10. Without: who holds the "
            "record in each game.",
            args={"game": Arg("string", "a game's name, e.g. Snake or Falling Blocks", max_length=40),
                  "period": Arg("enum", "all time (default) or this month", values=("all", "month"))},
            returns="ranked scores with names, or each game's record holder", scope="household",
            examples=("Who has the high score in Snake?",))
def leaderboard(ctx):
    if not settings.get("leaderboard"):
        return ctx.result("The leaderboard is switched off in Household Arcade.")
    period = ctx.args.get("period", "all")
    when = " this month" if period == "month" else ""
    if "game" in ctx.args:
        gid = find_game(ctx.args["game"])
        if gid is None or not settings.game_enabled(gid):
            raise bus.Nack("not_found", "game")
        mode = games.GAMES[gid]["default_mode"]
        rows = scores.leaderboard(ctx.conn, gid, mode, period, ctx.user)
        name = games.name(gid)
        link = ctx.link("Leaderboard in Household Arcade", "/leaderboard")
        if not rows:
            return ctx.result(f"No scores in {name}{when} yet.", links=[link])
        top = rows[0]
        text = (f"{name}{when} ({games.mode_label(gid, mode)}): {top['name']} leads with {_num(top['score'])}"
                + "".join(f"; {r['rank']}. {r['name']} {_num(r['score'])}" for r in rows[1:5]) + ".")
        items = [{"rank": r["rank"], "name": r["name"], "score": r["score"], "level": r["level"],
                  "at": (d.isoformat() if (d := config.to_local_date(r["at"])) else None)} for r in rows]
        return ctx.result(text, items=items, links=[link])
    if period == "month":                              # records are all-time; this month is per game
        return ctx.result("Ask about one game for this month's leaderboard.",
                          links=[ctx.link("Leaderboard in Household Arcade", "/leaderboard")])
    items = []
    for gid, rec in scores.records(ctx.conn).items():
        if rec["score"] is None or not settings.game_enabled(gid):
            continue
        items.append({"game": games.name(gid), "record": rec["score"], "by": rec["name"],
                      "mode": games.mode_label(gid, rec["mode"])})
    items.sort(key=lambda i: i["game"])
    if not items:
        text = "Nobody has a score in Household Arcade yet."
    else:
        by = {}
        for i in items:
            by[i["by"]] = by.get(i["by"], 0) + 1
        lead = sorted(by.items(), key=lambda kv: -kv[1])[:3]
        text = (f"Records in {len(items)} games. Most records: "
                + ", ".join(f"{n} ({c})" for n, c in lead) + ".")
    return ctx.result(text, items=items, links=[ctx.link("Leaderboard in Household Arcade", "/leaderboard")])


@tools.tool("arcade.mine",
            "The person's own Household Arcade scores: their best in each game and how much they played this week.",
            returns="best score per game and mode, games played, minutes this week",
            examples=("What's my best score in Falling Blocks?",), children=True)
def mine(ctx):
    m = scores.mine(ctx.conn, ctx.user["id"])
    minutes = round(m["totals"]["secondsThisWeek"] / 60)
    bests = [b for b in m["bests"] if settings.game_enabled(b["game"])]
    bests.sort(key=lambda b: -b["games"])
    link = ctx.link("My scores in Household Arcade", "/scores")
    if not bests:
        return ctx.result(f"{ctx.user['name']} hasn't finished a game in Household Arcade yet.", links=[link])
    text = (f"{ctx.user['name']} has played {m['totals']['games']} games ({minutes} minutes this week). Most played: "
            + ", ".join(f"{games.name(b['game'])} (best {_num(b['score'])})" for b in bests[:5]) + ".")
    items = [{"game": games.name(b["game"]), "mode": b["modeLabel"], "best": b["score"], "games": b["games"]}
             for b in bests]
    return ctx.result(text, items=items, links=[link])
