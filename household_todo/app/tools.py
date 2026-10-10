"""What Todo answers the Household Assistant (HOUSEHOLD_ASSISTANT_SPEC.md §4.2; the shared app/common/assist_tools.py).

- `todo.tasks`: the person's open tasks — today's and overdue, tomorrow's, this week's, overdue only, or all — as the
  dashboard counts them (shared lists and their own personal lists); for today, tomorrow and the week also what's on
  the schedule those days (people ask for "tasks" meaning both), and when the days asked about have no tasks, the
  next ones due after them;
- `todo.lists`: the lists they can see, with open counts;
- `todo.schedule`: what's coming up on the schedule in the next days (trash day, …), private items only their own;
- `todo.items.add` (acts): adds a task to one of their lists — only after the person taps the proposed change in
  the assistant (`confirm`), and checked as if they did it here;
- `todo.items.done` (acts): ticks off one of their open tasks, found by its title (and list);
- `todo.items.due` (acts): moves one of their open tasks to another day;
- `todo.reminder.add` (acts): "remind me at 5 pm to call the plumber" — a task on their own list (or one named) due
  then, and a ping to their phones at that time (`task_alarms`, reminders.run_alarm_pass_blocking).

`todo.tasks` also takes `person`: only the tasks assigned to someone ("Meera", "me", or "nobody").

Never task notes or links: titles, dates, lists and who. "Acting as" someone else never applies. Answered only while
the admin's *Answer the Household Assistant* is on and the person hasn't turned off *Let the Household Assistant
answer for me* (Settings). Links open the Dashboard, Lists (or the list), or Schedule on the app's sidebar page.
"""
import re
from datetime import date, timedelta

from . import config, db, schedule_logic, task_files, taskview
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
    items = schedule_logic.attach_turns(ctx.conn, [dict(r) for r in ctx.conn.execute(
        f"SELECT * FROM schedule_items WHERE {schedule_logic.VISIBLE_SQL}", (ctx.user["id"],))])
    excs = schedule_logic.load_exceptions(ctx.conn)
    names = taskview.users_by_id(ctx.conn)
    out = []
    for it in items:
        for d, turn in schedule_logic.effective_turns_between(it, excs.get(it["id"], []), start, end):
            who = it["assigned_to"] or turn                 # taken in turns: whose turn it is (§5.4b)
            out.append({"date": d.isoformat(), "what": it["name"], "time": it["start_time"],
                        "for": names.get(who) if who else None})
    out.sort(key=lambda e: (e["date"], e["time"] is not None, e["time"] or "", e["what"].lower()))
    return out


def _whose(ctx) -> str:
    want = ctx.args["person"].strip().casefold()
    if want in ("me", "mine", "myself", ctx.user["name"].casefold()):
        return "assigned to you"
    return "assigned to nobody" if want in ("nobody", "no one", "noone", "unassigned") else f"for {ctx.args['person'].strip()}"


def _for_person(ctx, rows: list[dict]) -> list[dict]:
    """The tasks assigned to the person named in `person` (first name, full name, "me" or "nobody")."""
    want = ctx.args["person"].strip().casefold()
    if want in ("nobody", "no one", "noone", "unassigned"):
        return [t for t in rows if not t["assigneeName"]]
    if want in ("me", "mine", "myself"):
        want = ctx.user["name"].casefold()

    def fits(name):
        n = (name or "").casefold()
        return bool(n) and (n == want or n.split()[0] == want or n.startswith(want + " "))
    return [t for t in rows if fits(t["assigneeName"])]


def _task_item(t: dict) -> dict:
    out = {"title": t["title"], "due": t["dueDate"], "time": t["dueTime"], "list": t["listName"],
           "who": t["assigneeName"], "overdue": t["overdue"]}
    if t.get("fileCount"):                       # files on the task (SPEC §17): said, not sent
        out["files"] = t["fileCount"]
    return out


def _files_words(n: int) -> str:
    return f", {n} file{'s' if n != 1 else ''} attached" if n else ""


def _task_words(ts: list[dict], today: date) -> str:
    return "; ".join(f"{t['title']} ({_when(t['dueDate'], today)}"
                     + (f", {t['assigneeName']}" if t["assigneeName"] else "") + _files_words(t.get("fileCount") or 0)
                     + ")" for t in ts)


def _schedule_words(out: list[dict], today: date) -> str:
    return "; ".join(f"{_when(e['date'], today)}: {e['what']}" + (f" at {e['time']}" if e["time"] else "")
                     for e in out[:10]) + ("…" if len(out) > 10 else "")


@tools.tool("todo.tasks",
            "Open tasks on the person's lists, and for today, tomorrow and the week what's on the household schedule "
            "those days. `when: today` is today's and overdue tasks, `tomorrow` tomorrow's, `week` the next 7 days "
            "(overdue included), `overdue` only overdue ones, `all` every open task.",
            args={"when": Arg("enum", "which tasks", values=("today", "tomorrow", "week", "overdue", "all"),
                              required=True),
                  "list": _LIST,
                  "person": Arg("string", "only tasks assigned to this person (a name, \"me\" or \"nobody\")",
                                max_length=60)},
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
    if "person" in ctx.args:
        every = _for_person(ctx, every)
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
    if "person" in ctx.args:
        label += f" {_whose(ctx)}"
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


def _open_tasks(ctx) -> list[dict]:
    sql = taskview.TASK_SELECT + f" WHERE {taskview.VISIBLE_SQL} AND t.completed = 0"
    args = [ctx.user["id"]]
    if "list" in ctx.args:
        lst = _find_list(ctx.conn, ctx.user["id"], ctx.args["list"])
        sql += " AND t.list_id = ?"
        args.append(lst["id"])
    return taskview.serialize_tasks(ctx.conn, ctx.conn.execute(sql, args).fetchall(), config.now().date())


def _find_task(ctx):
    """(the one open task the words in `task` mean, None) or (None, a result saying why nothing was changed)."""
    want = " ".join(ctx.args["task"].split()).casefold()
    rows = _open_tasks(ctx)
    exact = [t for t in rows if " ".join(t["title"].split()).casefold() == want]
    found = exact or [t for t in rows if want in t["title"].casefold()]
    link = ctx.link("Dashboard in Household Todo", "/dashboard")
    if len(found) == 1:
        return found[0], None
    where = f" on {ctx.args['list']}" if "list" in ctx.args else ""
    if not found:
        return None, ctx.result(f"No open task called “{ctx.args['task']}”{where}. Nothing was changed.", links=[link])
    names = "; ".join(f"{t['title']} ({t['listName']})" for t in found[:6])
    return None, ctx.result(f"{len(found)} open tasks match “{ctx.args['task']}”{where}: {names}. Nothing was "
                            "changed — say which one (the list's name helps).",
                            items=[_task_item(t) for t in found[:10]], links=[link])


_TASK = Arg("string", "the task's title, as todo.tasks lists it", required=True, max_length=200)


@tools.tool("todo.items.done", "Ticks off one of the person's open tasks (after they confirm it).",
            args={"task": _TASK, "list": _LIST}, acts=True, returns="the task that was ticked off", children=True)
def done(ctx):
    t, why = _find_task(ctx)
    if t is None:
        return why
    ctx.conn.execute("UPDATE tasks SET completed = 1, completed_at = ?, completed_by = ? WHERE id = ? AND completed = 0",
                     (config.now_iso(), ctx.user["id"], t["id"]))
    if task_files.on_done(ctx.conn, [t["id"]]):          # its files go, unless kept (SPEC §17)
        task_files.flush_soon()
    return ctx.result(f"Ticked off “{t['title']}” on {t['listName']}.",
                      items=[{"title": t["title"], "list": t["listName"], "done": True}],
                      links=[ctx.link(f"{t['listName']} in Household Todo", f"/lists/{t['listId']}")])


@tools.tool("todo.items.due", "Moves one of the person's open tasks to another day (after they confirm it).",
            args={"task": _TASK, "due": Arg("date", "the new day", required=True), "list": _LIST},
            acts=True, returns="the task with its new day", children=True)
def due(ctx):
    t, why = _find_task(ctx)
    if t is None:
        return why
    ctx.conn.execute("UPDATE tasks SET due_date = ? WHERE id = ? AND completed = 0", (ctx.args["due"], t["id"]))
    when = _when(ctx.args["due"], config.now().date())
    return ctx.result(f"“{t['title']}” on {t['listName']} is now due {when}.",
                      items=[{"title": t["title"], "list": t["listName"], "due": ctx.args["due"]}],
                      links=[ctx.link(f"{t['listName']} in Household Todo", f"/lists/{t['listId']}")])


_TIME_RE = re.compile(r"^\s*(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?\s*$", re.I)


def parse_time(text: str) -> str | None:
    """"17:00", "5pm", "5:30 pm", "9.15" → "HH:MM" (24-hour), or None."""
    m = _TIME_RE.match(text or "")
    if not m:
        return None
    h, mi, ap = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower().replace(".", "")
    if ap:
        if not 1 <= h <= 12:
            return None
        h = (h % 12) + (12 if ap == "pm" else 0)
    if h > 23 or mi > 59:
        return None
    return f"{h:02d}:{mi:02d}"


@tools.tool("todo.reminder.add",
            "Reminds the person at a time (after they confirm it): adds the task to their own list (or the list named) "
            "due then, and their phone gets a notification at that time. Without `date`: today, or tomorrow when the "
            "time has passed.",
            args={"text": Arg("string", "what to be reminded of", required=True, max_length=200),
                  "time": Arg("string", "the time, 24-hour HH:MM (e.g. 17:00)", required=True, max_length=10),
                  "date": Arg("date", "the day (default today, or tomorrow when the time has passed)"),
                  "list": _LIST},
            acts=True, returns="the reminder: task, list, day and time", children=True)
def reminder_add(ctx):
    hhmm = parse_time(ctx.args["time"])
    if hhmm is None:
        raise bus.Nack("invalid", "time")
    now = config.now()
    day = date.fromisoformat(ctx.args["date"]) if ctx.args.get("date") else now.date()
    if not ctx.args.get("date") and hhmm <= now.strftime("%H:%M"):
        day += timedelta(days=1)                          # "at 7" said at 8 pm means tomorrow
    if (day.isoformat(), hhmm) <= (now.date().isoformat(), now.strftime("%H:%M")):
        return ctx.result(f"{day.isoformat()} {hhmm} has already passed. Nothing was added.")
    if "list" in ctx.args:
        lst = dict(_find_list(ctx.conn, ctx.user["id"], ctx.args["list"]))
    else:
        lst = dict(ctx.conn.execute("SELECT * FROM lists WHERE id = ?",
                                    (taskview.default_list_id(ctx.conn, ctx.user["id"]),)).fetchone())
    text = " ".join(ctx.args["text"].split())
    if not text:
        raise bus.Nack("invalid", "text")
    tid = db.new_id()
    stamp = config.now_iso()
    ctx.conn.execute(
        "INSERT INTO tasks (id, list_id, title, due_date, due_time, completed, created_by, created_at, position, "
        "source, assigned_to) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, 'Assistant', ?)",
        (tid, lst["id"], text, day.isoformat(), hhmm, ctx.user["id"], stamp,
         taskview.next_task_position(ctx.conn, lst["id"]), ctx.user["id"]))
    ctx.conn.execute("INSERT INTO task_alarms (id, task_id, user_id, at, created_at) VALUES (?, ?, ?, ?, ?)",
                     (db.new_id(), tid, ctx.user["id"], f"{day.isoformat()}T{hhmm}", stamp))
    when = _when(day.isoformat(), now.date())
    return ctx.result(f"I'll remind you {when} at {hhmm}: “{text}” (on {lst['name']}).",
                      items=[{"title": text, "list": lst["name"], "due": day.isoformat(), "time": hhmm}],
                      links=[ctx.link(f"{lst['name']} in Household Todo", f"/lists/{lst['id']}")])
