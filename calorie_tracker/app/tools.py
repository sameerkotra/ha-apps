"""What Calorie Tracker answers the Household Assistant (HOUSEHOLD_ASSISTANT_SPEC.md §4.2; the shared
app/common/assist_tools.py).

- `calorie.today`: the asking person's own day — calories and macros against their goals, and what they logged at
  each meal — exactly as their own Today page shows it;
- `calorie.week`: their last 7 days (or the 7 days up to a date): each day's calories against the goal, and the
  average;
- `calorie.food.log` (acts): logs a food on their own day, only after they tap the proposed change. The calories
  come from the proposal (the model's estimate, shown on the button before the tap) or, when it gives none, from
  the person's saved food or the last time they logged that food, scaled by the servings.

Only the person's own log. Never anyone else's day (the assistant asks as the
person; "acting as" another person is for admins on the page only), and never weight. Answered only while the
admin's *Answer the Household Assistant* is on and the person hasn't turned off *Let the Household Assistant answer
for me* (Goals). The link opens that day's Food Log on the app's sidebar page.
"""
import re
import asyncio
import logging
from datetime import date, timedelta

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
    r = conn.execute("SELECT id, name, assistant_ok FROM users WHERE id = ?", (uid,)).fetchone()
    return {"id": r["id"], "name": r["name"], "assistant_ok": bool(r["assistant_ok"])} if r else None


def _panel():
    return config.INGRESS_PANEL if _PANEL_RE.match(config.INGRESS_PANEL or "") else None


tools = assist_tools.Catalogue(
    "calorie", targets=[r"/foodlog/\d{4}-\d{2}-\d{2}"], actor=_actor,
    enabled=lambda conn: bool(settings.get("assistant_answers")), person_enabled=lambda conn, user: user["assistant_ok"], panel=_panel, busy=_busy)


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


@tools.tool("calorie.week",
            "The person's own last 7 days of food (or the 7 days up to `date`): calories each day against their goal, "
            "and the daily average.",
            args={"date": Arg("date", "the last day (default today)")},
            returns="each day's calories and the goal, the average", children=True)
def week(ctx):
    last = date.fromisoformat(ctx.args.get("date") or config.today().isoformat())
    if last > config.today():
        raise bus.Nack("invalid", "date")
    first = last - timedelta(days=6)
    rows = {r["date"]: r for r in ctx.conn.execute(
        "SELECT date, SUM(calories) AS kcal, SUM(protein) AS protein, SUM(carbs) AS carbs, SUM(fat) AS fat "
        "FROM food_logs WHERE user_id = ? AND date >= ? AND date <= ? GROUP BY date",
        (ctx.user["id"], first.isoformat(), last.isoformat()))}
    g = ctx.conn.execute("SELECT calorie_goal FROM goals WHERE user_id = ?", (ctx.user["id"],)).fetchone()
    goal = g["calorie_goal"] if g else DEFAULT_GOALS["calorie_goal"]
    items = []
    for i in range(7):
        d = (first + timedelta(days=i)).isoformat()
        r = rows.get(d)
        items.append({"date": d, "kcal": _r(r["kcal"]) if r else 0, "goal": _r(goal), "logged": bool(r),
                      "protein": _r(r["protein"]) if r else 0, "carbs": _r(r["carbs"]) if r else 0,
                      "fat": _r(r["fat"]) if r else 0})
    logged = [i for i in items if i["logged"]]
    link = ctx.link("Food Log in Calorie Tracker", f"/foodlog/{last.isoformat()}")
    if not logged:
        return ctx.result(f"Nothing logged from {first.isoformat()} to {last.isoformat()}.", items=items, links=[link])
    avg = sum(i["kcal"] for i in logged) / len(logged)
    over = sum(1 for i in logged if i["kcal"] > goal)
    text = (f"{first.isoformat()} to {last.isoformat()}: {len(logged)} of 7 days logged, {_r(avg)} kcal a day on "
            f"average (goal {_r(goal)}); {over} day{'s' if over != 1 else ''} over the goal. "
            + "; ".join(f"{date.fromisoformat(i['date']).strftime('%a')} {i['kcal'] if i['logged'] else '—'}"
                        for i in items) + ".")
    return ctx.result(text, items=items, links=[link])


def _known_food(ctx, name: str):
    """(calories, protein, carbs, fat, saved_food_id) for one serving of a food the person saved or logged before."""
    want = " ".join(name.split()).casefold()
    r = ctx.conn.execute("SELECT * FROM saved_foods WHERE user_id = ? AND lower(trim(name)) = ? ORDER BY id DESC",
                         (ctx.user["id"], want)).fetchone()
    if r:
        return r["calories"], r["protein"], r["carbs"], r["fat"], r["id"]
    r = ctx.conn.execute("SELECT * FROM food_logs WHERE user_id = ? AND lower(trim(food_name)) = ? AND servings > 0 "
                         "ORDER BY date DESC, id DESC", (ctx.user["id"], want)).fetchone()
    if r:
        n = r["servings"]
        return r["calories"] / n, r["protein"] / n, r["carbs"] / n, r["fat"] / n, r["saved_food_id"]
    return None


def _push_sensor(user: dict) -> None:
    """After the commit: today's calories sensor in Home Assistant (as logging on the page does)."""
    from . import auth, ha_sync
    try:
        asyncio.run(ha_sync.push_for_user(user["id"], auth._slugify(user["name"]), user["name"]))
    except Exception:
        logging.getLogger("tools").exception("Updating the calories sensor failed")


@tools.tool("calorie.food.log",
            "Logs a food on the person's own food log (after they confirm it). Give your best estimate of `calories` "
            "(and protein, carbs, fat in grams) for the servings eaten; leave them out for a food they saved or "
            "logged before.",
            args={"food": Arg("string", "what they ate", required=True, max_length=200),
                  "meal": Arg("enum", "which meal (default: by the time of day)", values=MEALS),
                  "servings": Arg("number", "how many servings (default 1)", min=0.1, max=20),
                  "calories": Arg("number", "kcal for all the servings", min=0, max=10000),
                  "protein": Arg("number", "grams of protein", min=0, max=1000),
                  "carbs": Arg("number", "grams of carbohydrate", min=0, max=1000),
                  "fat": Arg("number", "grams of fat", min=0, max=1000),
                  "date": Arg("date", "the day (default today)")},
            acts=True, returns="what was logged and the day's new total", children=True)
def food_log(ctx):
    day = ctx.args.get("date") or config.today().isoformat()
    if date.fromisoformat(day) > config.today():
        raise bus.Nack("invalid", "date")
    food = " ".join(ctx.args["food"].split())
    servings = float(ctx.args.get("servings", 1))
    meal = ctx.args.get("meal") or _meal_now()
    link = ctx.link("Food Log in Calorie Tracker", f"/foodlog/{day}")
    saved_id = None
    if "calories" in ctx.args:
        kcal = float(ctx.args["calories"])
        protein, carbs, fat = (float(ctx.args.get(k, 0)) for k in ("protein", "carbs", "fat"))
    else:
        known = _known_food(ctx, food)
        if known is None:
            return ctx.result(f"I don't know the calories in “{food}” yet — say how many, or save it once in Calorie "
                              "Tracker. Nothing was logged.", links=[link])
        kcal, protein, carbs, fat = (x * servings for x in known[:4])
        saved_id = known[4]
    ctx.conn.execute("INSERT INTO food_logs (user_id, date, meal_type, food_name, servings, calories, protein, carbs, "
                     "fat, saved_food_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (ctx.user["id"], day, meal, food, servings, round(kcal, 1), round(protein, 1), round(carbs, 1),
                      round(fat, 1), saved_id))
    total = ctx.conn.execute("SELECT SUM(calories) FROM food_logs WHERE user_id = ? AND date = ?",
                             (ctx.user["id"], day)).fetchone()[0] or 0
    g = ctx.conn.execute("SELECT calorie_goal FROM goals WHERE user_id = ?", (ctx.user["id"],)).fetchone()
    goal = g["calorie_goal"] if g else DEFAULT_GOALS["calorie_goal"]
    if day == config.today().isoformat():
        user = dict(ctx.user)
        ctx.msg.after_commit(lambda: _push_sensor(user))
    when = "today" if day == config.today().isoformat() else day
    return ctx.result(f"Logged {food} ({_r(kcal)} kcal) for {meal} {when}. {when.capitalize() if when == 'today' else when}: "
                      f"{_r(total)} of {_r(goal)} kcal.",
                      items=[{"food": food, "meal": meal, "servings": servings, "kcal": _r(kcal), "protein": _r(protein),
                              "carbs": _r(carbs), "fat": _r(fat)}], links=[link])


def _meal_now() -> str:
    h = config.now().hour
    return "breakfast" if 4 <= h < 11 else "lunch" if 11 <= h < 16 else "dinner" if 16 <= h < 22 else "snack"
