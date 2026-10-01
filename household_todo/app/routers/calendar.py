"""Calendar data: dated tasks plus the visible schedule-item occurrences in
a range (SPEC §6, §8j)."""

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import config, db, maintenance as mt, recurrence, schedule_logic, taskview
from ..auth import get_acting_user

router = APIRouter(prefix="/api", tags=["calendar"])

MAX_RANGE_DAYS = 92


@router.get("/calendar")
def get_calendar(
    from_: str = Query(alias="from"),
    to: str = Query(),
    type: str | None = Query(default=None),
    assignee: str | None = Query(default=None),
    show: str = Query(default="open"),
    schedule: int = Query(default=1),
    maintenance: int = Query(default=1),
    acting: dict = Depends(get_acting_user),
):
    start, end = recurrence.parse_date(from_), recurrence.parse_date(to)
    if start is None or end is None:
        raise HTTPException(422, "from and to must be dates (YYYY-MM-DD).")
    if end < start:
        raise HTTPException(422, "to must not be before from.")
    if (end - start).days > MAX_RANGE_DAYS:
        raise HTTPException(422, f"The range can be at most {MAX_RANGE_DAYS} days.")
    if show not in ("open", "all"):
        raise HTTPException(422, "show must be 'open' or 'all'.")
    if schedule not in (0, 1):
        raise HTTPException(422, "schedule must be 0 or 1.")

    def keep_assignee(assigned_to) -> bool:
        if assignee == "me":
            return assigned_to == acting["id"]
        if assignee == "unassigned":
            return assigned_to is None
        if assignee not in (None, "", "anyone"):
            return assigned_to == assignee
        return True

    today = config.today()
    sql = (
        taskview.TASK_SELECT
        + f" WHERE {taskview.VISIBLE_SQL} AND t.due_date >= ? AND t.due_date <= ?"
        + ("" if show == "all" else " AND t.completed = 0")
    )
    with db.get_conn() as conn:
        rows = conn.execute(sql, (acting["id"], start.isoformat(), end.isoformat())).fetchall()
        tasks = taskview.serialize_tasks(conn, rows, today)
        undated = conn.execute(
            "SELECT COUNT(*) FROM tasks t JOIN lists l ON l.id = t.list_id "
            f"WHERE {taskview.VISIBLE_SQL} AND t.completed = 0 AND t.due_date IS NULL",
            (acting["id"],),
        ).fetchone()[0]
        entries = []
        if schedule == 1:
            # someone else's private item never shows up (§4)
            items = [dict(r) for r in conn.execute(
                f"SELECT * FROM schedule_items WHERE {schedule_logic.VISIBLE_SQL}", (acting["id"],))]
            excs = schedule_logic.load_exceptions(conn)
            users, _ = schedule_logic.lookups(conn)
            for item in items:
                if keep_assignee(item["assigned_to"]):   # household items count as "unassigned"
                    entries.extend(schedule_logic.calendar_entries(item, excs.get(item["id"], []), start, end, users))
            entries.sort(key=schedule_logic.entry_sort_key)
        # maintenance due dates (an overdue one shows on today) and the ones after them
        maint = []
        if maintenance == 1 and mt.enabled():
            users, _ = schedule_logic.lookups(conn)
            for item in mt.all_items(conn):
                if not keep_assignee(item["assigned_to"]):
                    continue
                st, due = mt.status_of(item, today)
                for d, real in mt.projected(item, start, end, today):
                    maint.append({"itemId": item["id"], "name": item["name"], "icon": item["icon"],
                                  "date": d.isoformat(), "dueDate": due.isoformat() if due else None,
                                  "status": st if real else "projected", "projected": not real,
                                  "assignedTo": item["assigned_to"], "assigneeName": users.get(item["assigned_to"])})
            maint.sort(key=lambda e: (e["date"], e["name"].lower()))

    tasks = [t for t in tasks if (not type or t["typeId"] == type) and keep_assignee(t["assignedTo"])]
    by_date: dict[str, list] = {}
    for t in tasks:
        by_date.setdefault(t["dueDate"], []).append(t)
    ordered = [t for d in sorted(by_date) for t in taskview.sort_for_day(by_date[d])]
    return {"today": today.isoformat(), "tasks": ordered, "schedule": entries, "maintenance": maint,
            "undatedCount": undated}
