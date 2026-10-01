"""Dashboard buckets and the household workload cards (SPEC §8e, §8l)."""
from datetime import timedelta

from fastapi import APIRouter, Depends

from .. import config, db, schedule_logic, taskview
from ..auth import get_acting_user

router = APIRouter(prefix="/api", tags=["dashboard"])


@router.get("/dashboard")
def get_dashboard(acting: dict = Depends(get_acting_user)):
    now = config.now()
    today = now.date()
    today_iso = today.isoformat()
    week_iso = (today + timedelta(days=7)).isoformat()
    with db.get_conn() as conn:
        rows = conn.execute(
            taskview.TASK_SELECT + f" WHERE {taskview.VISIBLE_SQL} AND t.completed = 0",
            (acting["id"],),
        ).fetchall()
        tasks = taskview.serialize_tasks(conn, rows, today)
        items = [dict(r) for r in conn.execute(
            f"SELECT * FROM schedule_items WHERE {schedule_logic.VISIBLE_SQL}", (acting["id"],))]
        excs = schedule_logic.load_exceptions(conn)

    # overdue counts completion-required tasks only; an optional past task is nowhere here (§8l)
    overdue = taskview.sort_for_dashboard([t for t in tasks if t["overdue"]])
    due_today = taskview.sort_for_dashboard([t for t in tasks if t["dueDate"] == today_iso])
    upcoming = taskview.sort_for_dashboard([t for t in tasks if t["dueDate"] and today_iso < t["dueDate"] <= week_iso])

    # "Coming up": items whose sensor is on, plus the acting user's own items
    # occurring today or tomorrow (a timed item's sensor is off outside its window)
    coming_up = []
    for item in items:
        st = schedule_logic.item_state(item, excs.get(item["id"], []), today, now)
        mine_soon = item["assigned_to"] == acting["id"] and st["daysUntil"] is not None and st["daysUntil"] <= 1
        if st["sensorOn"] or mine_soon:
            coming_up.append({
                "itemId": item["id"], "name": item["name"], "icon": item["icon"] or schedule_logic.DEFAULT_ICON,
                "nextDate": st["nextDate"], "daysUntil": st["daysUntil"], "occursToday": st["occursToday"],
                "startTime": item["start_time"], "endTime": item["end_time"], "sensorOn": st["sensorOn"],
                "private": item["visibility"] == "private",
            })
    coming_up.sort(key=lambda c: (c["nextDate"], c["startTime"] is not None, c["startTime"] or "", c["name"].lower()))

    return {
        "today": today_iso,
        "overdue": overdue,
        "dueToday": due_today,
        "upcoming": upcoming,
        "counts": {"overdue": len(overdue), "dueToday": len(due_today), "upcoming": len(upcoming), "open": len(tasks)},
        "schedule": coming_up,
    }


@router.get("/workload")
def get_workload(acting: dict = Depends(get_acting_user)):
    """One card per active person plus "Unassigned". For other people the
    counts include shared-list tasks only; the acting user's own card also
    includes their personal lists (§8e)."""
    today = config.today()
    today_iso = today.isoformat()
    soon_iso = (today + timedelta(days=7)).isoformat()
    week_ago = config.days_ago_iso(7)
    me = acting["id"]

    with db.get_conn() as conn:
        users = [dict(r) for r in conn.execute("SELECT id, name, disabled FROM users")]
        open_rows = conn.execute(
            "SELECT t.assigned_to, t.due_date, t.completion_required, l.kind AS list_kind, l.owner_user_id AS list_owner "
            f"FROM tasks t JOIN lists l ON l.id = t.list_id WHERE {taskview.VISIBLE_SQL} AND t.completed = 0",
            (me,),
        ).fetchall()
        done_rows = conn.execute(
            "SELECT t.completed_by, l.kind AS list_kind FROM tasks t JOIN lists l ON l.id = t.list_id "
            f"WHERE {taskview.VISIBLE_SQL} AND t.completed = 1 AND t.completed_at >= ? AND t.completed_by IS NOT NULL",
            (me, week_ago),
        ).fetchall()

    def counts(rows) -> dict:
        return {
            "open": len(rows),
            "overdue": sum(1 for r in rows if r["due_date"] and r["due_date"] < today_iso and r["completion_required"]),
            "dueSoon": sum(1 for r in rows if r["due_date"] and today_iso <= r["due_date"] <= soon_iso),
        }

    people = []
    for u in users:
        is_me = u["id"] == me
        # "user U's task": assigned to U, or in a personal list U owns
        if is_me:
            mine = [r for r in open_rows if r["assigned_to"] == u["id"] or (r["list_kind"] == "personal" and r["list_owner"] == u["id"])]
            done = sum(1 for r in done_rows if r["completed_by"] == u["id"])
        else:
            mine = [r for r in open_rows if r["assigned_to"] == u["id"] and r["list_kind"] == "shared"]
            done = sum(1 for r in done_rows if r["completed_by"] == u["id"] and r["list_kind"] == "shared")
        c = counts(mine)
        if u["disabled"] and c["open"] == 0:
            continue   # a disabled person shows only while they still have open tasks
        people.append({"userId": u["id"], "name": u["name"], "disabled": bool(u["disabled"]), "isMe": is_me,
                       **c, "doneThisWeek": done})
    people.sort(key=lambda p: (not p["isMe"], p["name"].lower()))

    unassigned_rows = [r for r in open_rows if r["assigned_to"] is None and r["list_kind"] == "shared"]
    return {"people": people, "unassigned": counts(unassigned_rows)}
