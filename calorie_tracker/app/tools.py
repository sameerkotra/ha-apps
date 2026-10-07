"""What Calorie Tracker answers the Household Assistant (HOUSEHOLD_ASSISTANT_SPEC.md §4.2; the shared
app/common/assist_tools.py).

One tool, `calorie.today`: the asking person's own day — calories and macros against their goals, and what they
logged at each meal — exactly as their own Today page shows it. Never anyone else's day (the assistant asks as the
person; "acting as" another person is for admins on the page only), and never weight. Answered only while the
admin's *Answer the Household Assistant* is on. Calorie Tracker has no per-person settings, so there is no
per-person switch. The link opens that day's Food Log on the app's sidebar page.
"""
import re
from datetime import date

from . import config, db, settings
from .common import app_bus as bus
from .common import assist_tools
from .common.assist_tools import Arg

_PANEL_RE = re.compile(r"^/[a-z0-9]{1,16}_calorie_tracker$")
MEALS = ("breakfast", "lunch", "dinner", "snack")
DEFAULT_GOALS = {"calorie_goal": 2000, "protein_goal": 150, "carb_goal": 200, "fat_goal": 65}


def _busy() -> None:
    if db._import_lock.locked():                        # a backup is being restored right now
        raise bus.Nack("busy", "restoring")


def _actor(conn, uid: str):
    """Anyone who has opened Calorie Tracker (it has no access switch of its own)."""
    r = conn.execute("SELECT id, name FROM users WHERE id = ?", (uid,)).fetchone()
    return {"id": r["id"], "name": r["name"]} if r else None


def _panel():
    return config.INGRESS_PANEL if _PANEL_RE.match(config.INGRESS_PANEL or "") else None


tools = assist_tools.Catalogue(
    "calorie", targets=[r"/foodlog/\d{4}-\d{2}-\d{2}"], actor=_actor, enabled=lambda conn: bool(settings.get("assistant_answers")), panel=_panel, busy=_busy)


def _r(x) -> int:
    return int(round(x or 0))


@tools.tool("calorie.today",
            "The person's own food log for a day (default today): calories, protein, carbs and fat against their "
            "goals, and what they ate at each meal.",
            args={"date": Arg("date", "the day (default today)")},
            returns="totals and goals, calories left, the foods logged per meal",
            examples=("How many calories do I have left today?",), children=True)
def today(ctx):
    day = ctx.args.get("date") or config.today().isoformat()
    if date.fromisoformat(day) > config.today():
        raise bus.Nack("invalid", "date")
    logs = ctx.conn.execute("SELECT meal_type, food_name, servings, calories, protein, carbs, fat FROM food_logs "
                            "WHERE user_id = ? AND date = ? ORDER BY created_at, id", (ctx.user["id"], day)).fetchall()
    g = ctx.conn.execute("SELECT * FROM goals WHERE user_id = ?", (ctx.user["id"],)).fetchone()
    goal = {k: (g[k] if g else v) for k, v in DEFAULT_GOALS.items()}
    tot = {k: sum(r[k] for r in logs) for k in ("calories", "protein", "carbs", "fat")}
    left = goal["calorie_goal"] - tot["calories"]
    when = "today" if day == config.today().isoformat() else day
    link = ctx.link("Food Log in Calorie Tracker", f"/foodlog/{day}")
    if not logs:
        return ctx.result(f"Nothing logged {when}; the goal is {_r(goal['calorie_goal'])} kcal.", links=[link])
    text = (f"{when.capitalize() if when == 'today' else when}: {_r(tot['calories'])} of {_r(goal['calorie_goal'])} "
            f"kcal ({_r(abs(left))} {'left' if left >= 0 else 'over'}); protein {_r(tot['protein'])}/"
            f"{_r(goal['protein_goal'])} g, carbs {_r(tot['carbs'])}/{_r(goal['carb_goal'])} g, fat {_r(tot['fat'])}/"
            f"{_r(goal['fat_goal'])} g.")
    per_meal = []
    for meal in MEALS + tuple(sorted({r["meal_type"] for r in logs} - set(MEALS))):
        rows = [r for r in logs if r["meal_type"] == meal]
        if rows:
            per_meal.append(f"{meal}: " + ", ".join(r["food_name"] for r in rows[:6]) + ("…" if len(rows) > 6 else "")
                            + f" ({_r(sum(r['calories'] for r in rows))} kcal)")
    text += " " + "; ".join(per_meal) + "."
    items = [{"meal": r["meal_type"], "food": r["food_name"], "servings": r["servings"], "kcal": _r(r["calories"]),
              "protein": _r(r["protein"]), "carbs": _r(r["carbs"]), "fat": _r(r["fat"])} for r in logs]
    return ctx.result(text, items=items, links=[link])
