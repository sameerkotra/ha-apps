"""What Todo answers the Household Assistant (HOUSEHOLD_ASSISTANT_SPEC.md §4.2; the shared app/common/assist_tools.py).

- `todo.tasks`: the person's open tasks — today's and overdue, tomorrow's, this week's, overdue only, or all — as the
  dashboard counts them (shared lists and their own personal lists); for today, tomorrow and the week also what's on
  the schedule those days (people ask for "tasks" meaning both), and when the days asked about have no tasks, the
  next ones due after them;
- `todo.lists`: the lists they can see, with open counts;
- `todo.schedule`: what's coming up on the schedule in the next days (trash day, …), private items only their own;
- `todo.items.add` (acts): adds a task to one of their lists — only after the person taps the proposed change in
  the assistant (`confirm`), and checked as if they did it here.

Never task notes or links: titles, dates, lists and who. "Acting as" someone else never applies. Answered only while
the admin's *Answer the Household Assistant* is on and the person hasn't turned off *Let the Household Assistant
answer for me* (Settings). Links open the Dashboard, Lists (or the list), or Schedule on the app's sidebar page.
"""
import re
from datetime import date, timedelta

from . import config, db, schedule_logic, taskview
from .common import app_bus as bus
from .common import assist_tools
from .common.assist_tools import Arg

_PANEL_RE = re.compile(r"^/[a-z0-9]{1,16}_household_todo$")
MAX_TASKS = 50
MAX_SCHEDULE = 30


def _busy() -> None:
    if not db._import_lock.acquire(blocking=False):            # a backup is being restored right now
        raise bus.Nack("busy", "restoring")
    db._import_lock.release()


def _actor(conn, uid: str):
    r = conn.execute("SELECT id, name, disabled, assistant_ok FROM users WHERE id = ?", (uid,)).fetchone()
    if r is None or r["disabled"]:
        return None
    return {"id": r["id"], "name": r["name"], "assistant_ok": bool(r["assistant_ok"])}


def _panel():
    return config.SIDEBAR_PAGE if _PANEL_RE.match(config.SIDEBAR_PAGE or "") else None


def _enabled(conn) -> bool:
    from . import settings
    return bool(settings.get("assistant_answers"))


tools = assist_tools.Catalogue(
    "todo", targets=[r"/dashboard", r"/schedule", r"/lists", r"/lists/[A-Za-z0-9_-]{1,64}"], actor=_actor, enabled=_enabled, person_enabled=lambda conn, user: user["assistant_ok"],
    panel=_panel, busy=_busy)

_LIST = Arg("string", "a list's name", max_length=60)


def _visible_lists(conn, uid: str) -> list:
    return conn.execute(f"SELECT l.* FROM lists l WHERE {taskview.VISIBLE_SQL} "
                        "ORDER BY (l.kind = 'personal'), l.position, l.created_at", (uid,)).fetchall()


def _find_list(conn, uid: str, name: str):
    want = name.strip().lower()
    for r in _visible_lists(conn, uid):
        if r["name"].strip().lower() == want:
            return r
    raise bus.Nack("not_found", "list")                     # someone else's private list looks like none


def _when(d: str | None, today: date) -> str:
    if not d:
        return "no date"
    day = date.fromisoformat(d)
    if day == today:
        return "today"
    if day == today + timedelta(days=1):
        return "tomorrow"
    if day < today:
        n = (today - day).days
        return f"overdue {n} day{'s' if n != 1 else ''}"
    return f"{day.strftime('%a')} {day.day} {day.strftime('%b')}"


def _occurrences(ctx, start: date, end: date) -> list[dict]:
    """What's on the schedule from `start` to `end` (both included) that the person may see, in date order."""
    items = [dict(r) for r in ctx.conn.execute(
        f"SELECT * FROM schedule_items WHERE {schedule_logic.VISIBLE_SQL}", (ctx.user["id"],))]
    excs = schedule_logic.load_exceptions(ctx.conn)
    names = taskview.users_by_id(ctx.conn)
    out = []
    for it in items:
        for d in schedule_logic.effective_dates_between(it, excs.get(it["id"], []), start, end):
            out.append({"date": d.isoformat(), "what": it["name"], "time": it["start_time"],
                        "for": names.get(it["assigned_to"]) if it["assigned_to"] else None})
    out.sort(key=lambda e: (e["date"], e["time"] is not None, e["time"] or "", e["what"].lower()))
    return out


def _task_item(t: dict) -> dict:
    return {"title": t["title"], "due": t["dueDate"], "time": t["dueTime"], "list": t["listName"],
            "who": t["assigneeName"], "overdue": t["overdue"]}


def _task_words(ts: list[dict], today: date) -> str:
    return "; ".join(f"{t['title']} ({_when(t['dueDate'], today)}"
                     + (f", {t['assigneeName']}" if t["assigneeName"] else "") + ")" for t in ts)


def _schedule_words(out: list[dict], today: date) -> str:
    return "; ".join(f"{_when(e['date'], today)}: {e['what']}" + (f" at {e['time']}" if e["time"] else "")
                     for e in out[:10]) + ("…" if len(out) > 10 else "")


@tools.tool("todo.tasks",
            "Open tasks on the person's lists, and for today, tomorrow and the week what's on the household schedule "
            "those days. `when: today` is today's and overdue tasks, `tomorrow` tomorrow's, `week` the next 7 days "
            "(overdue included), `overdue` only overdue ones, `all` every open task.",
            args={"when": Arg("enum", "which tasks", values=("today", "tomorrow", "week", "overdue", "all"),
                              required=True),
                  "list": _LIST},
            returns="tasks with title, due date, list and who it's assigned to; schedule entries with list Schedule",
            examples=("What's on my list today?",), children=True)
def tasks(ctx):
    today = config.now().date()
    t_iso, week_iso = today.isoformat(), (today + timedelta(days=7)).isoformat()
    tomorrow_iso = (today + timedelta(days=1)).isoformat()
    sql = taskview.TASK_SELECT + f" WHERE {taskview.VISIBLE_SQL} AND t.completed = 0"
    args = [ctx.user["id"]]
    if "list" in ctx.args:
        lst = _find_list(ctx.conn, ctx.user["id"], ctx.args["list"])
        sql += " AND t.list_id = ?"
        args.append(lst["id"])
    every = taskview.serialize_tasks(ctx.conn, ctx.conn.execute(sql, args).fetchall(), today)
    rows = every
    when = ctx.args["when"]
    last = {"today": t_iso, "tomorrow": tomorrow_iso, "week": week_iso}.get(when)
    if when == "today":
        rows = [t for t in rows if t["overdue"] or t["dueDate"] == t_iso]
    elif when == "tomorrow":
        rows = [t for t in rows if t["dueDate"] == tomorrow_iso]
    elif when == "week":
        rows = [t for t in rows if t["overdue"] or (t["dueDate"] and t_iso <= t["dueDate"] <= week_iso)]
    elif when == "overdue":
        rows = [t for t in rows if t["overdue"]]
    rows = taskview.sort_for_dashboard(rows) if when != "all" else taskview.sort_tasks(rows, "due")
    link = (ctx.link(f"{lst['name']} in Household Todo", f"/lists/{lst['id']}") if "list" in ctx.args
            else ctx.link("Dashboard in Household Todo", "/dashboard"))
    label = {"today": "for today", "tomorrow": "for tomorrow", "week": "this week", "overdue": "overdue",
             "all": "open"}[when]
    if rows:
        items = [_task_item(t) for t in rows[:MAX_TASKS]]
        text = (f"{len(rows)} task{'s' if len(rows) != 1 else ''} {label}: " + _task_words(rows[:10], today)
                + ("…" if len(rows) > 10 else "") + ".")
    else:
        items = []
        text = f"No tasks {label}" + (f" on {ctx.args['list']}" if "list" in ctx.args else "") + "."
        if last:                                         # so "none today" isn't read as "none for days"
            later = taskview.sort_tasks([t for t in every if t["dueDate"] and t["dueDate"] > last], "due")[:3]
            if later:
                text += " Next due: " + _task_words(later, today) + "."
    links = [link]
    if last and "list" not in ctx.args:                  # the schedule belongs to no list
        start = today + timedelta(days=1) if when == "tomorrow" else today
        sched = _occurrences(ctx, start, date.fromisoformat(last))
        if sched:
            text += " On the schedule: " + _schedule_words(sched, today) + "."
            items += [{"title": e["what"], "due": e["date"], "time": e["time"], "list": "Schedule", "who": e["for"],
                       "overdue": False} for e in sched[:MAX_SCHEDULE]]
            links.append(ctx.link("Schedule in Household Todo", "/schedule"))
    return ctx.result(text, items=items, links=links, more=len(rows) > MAX_TASKS)


@tools.tool("todo.lists", "The person's task lists (shared and their own personal ones) with how many tasks are open.",
            returns="lists with name, shared or personal, open count", children=True)
def lists(ctx):
    rows = ctx.conn.execute(
        "SELECT l.*, (SELECT COUNT(*) FROM tasks t WHERE t.list_id = l.id AND t.completed = 0) AS open_count "
        f"FROM lists l WHERE {taskview.VISIBLE_SQL} ORDER BY (l.kind = 'personal'), l.position, l.created_at",
        (ctx.user["id"],)).fetchall()
    items = [{"list": r["name"], "kind": r["kind"], "open": r["open_count"]} for r in rows]
    text = f"{len(rows)} lists: " + ", ".join(f"{r['name']} ({r['open_count']} open)" for r in rows) + "."
    return ctx.result(text, items=items, links=[ctx.link("Lists in Household Todo", "/lists")])


@tools.tool("todo.schedule",
            "What's coming up on the household schedule (trash day, recurring chores, appointments) in the next days.",
            args={"days": Arg("number", "how many days ahead, 1 to 14 (default 7)", min=1, max=14)},
            returns="dates with what's on and who it's for", children=True)
def schedule(ctx):
    today = config.now().date()
    days = int(ctx.args.get("days", 7))
    out = _occurrences(ctx, today, today + timedelta(days=days))
    link = ctx.link("Schedule in Household Todo", "/schedule")
    if not out:
        return ctx.result(f"Nothing on the schedule in the next {days} days.", links=[link])
    text = f"Coming up in the next {days} days: " + _schedule_words(out, today) + "."
    return ctx.result(text, items=out, links=[link])


@tools.tool("todo.items.add", "Adds a task to one of the person's lists (after they confirm it).",
            args={"list": Arg("string", "the list's name", required=True, max_length=60),
                  "text": Arg("string", "the task", required=True),
                  "due": Arg("date", "when it's due (optional)")},
            acts=True, returns="the added task with its list", children=True)
def add(ctx):
    lst = _find_list(ctx.conn, ctx.user["id"], ctx.args["list"])
    now = config.now_iso()
    tid = db.new_id()
    ctx.conn.execute(
        "INSERT INTO tasks (id, list_id, title, due_date, completed, created_by, created_at, position, source) "
        "VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?)",
        (tid, lst["id"], ctx.args["text"], ctx.args.get("due"), ctx.user["id"], now,
         taskview.next_task_position(ctx.conn, lst["id"]), "Assistant"))
    due = f", due {_when(ctx.args['due'], config.now().date())}" if ctx.args.get("due") else ""
    return ctx.result(f"Added “{ctx.args['text']}” to {lst['name']}{due}.",
                      items=[{"title": ctx.args["text"], "list": lst["name"], "due": ctx.args.get("due")}],
                      links=[ctx.link(f"{lst['name']} in Household Todo", f"/lists/{lst['id']}")])
