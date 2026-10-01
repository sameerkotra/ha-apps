"""Shared read model for tasks: the SQL that decides which tasks a person can
see, the JSON shape every route returns, and the sort orders (SPEC §6, §8d,
§8g–§8l). Used by the tasks, calendar and dashboard routers and by the
reminder digest, so all of them agree on visibility and on what "overdue"
means.

Raises Starlette's HTTPException (FastAPI turns it into `{"detail": ...}`).
"""
from datetime import date

from starlette.exceptions import HTTPException

# A task is visible to person U if it is in a shared list or in a personal
# list U owns. Everything that lists tasks joins `lists l` and uses this.
VISIBLE_SQL = "(l.kind = 'shared' OR l.owner_user_id = ?)"

TASK_SELECT = """
SELECT t.*,
       l.name AS list_name, l.kind AS list_kind, l.owner_user_id AS list_owner, l.role AS list_role,
       ty.name AS type_name, ty.icon AS type_icon, ty.color AS type_color,
       p.name AS place_name, p.address AS place_address, p.phone AS place_phone,
       p.drive_minutes AS place_drive_minutes, p.drive_tolls_avoided AS place_drive_tolls_avoided,
       au.name AS assignee_name, cu.name AS completed_by_name, cr.name AS created_by_name
FROM tasks t
JOIN lists l ON l.id = t.list_id
LEFT JOIN task_types ty ON ty.id = t.type_id
LEFT JOIN places p ON p.id = t.place_id
LEFT JOIN users au ON au.id = t.assigned_to
LEFT JOIN users cu ON cu.id = t.completed_by
LEFT JOIN users cr ON cr.id = t.created_by
"""

PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}


# ---------------------------------------------------------------------------
# Lookups and access checks
# ---------------------------------------------------------------------------

def users_by_id(conn) -> dict[str, str]:
    return {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM users")}


def load_list(conn, list_id: str) -> dict:
    row = conn.execute("SELECT * FROM lists WHERE id = ?", (list_id,)).fetchone()
    if not row:
        raise HTTPException(404, "List not found.")
    return dict(row)


def check_list_content_access(lst: dict, acting_id: str) -> None:
    """Personal lists are private: 403 unless the acting user owns it (an
    admin acting as the owner counts as the owner, §8e)."""
    if lst["kind"] == "personal" and lst["owner_user_id"] != acting_id:
        raise HTTPException(403, "That list is private to someone else.")


def load_task(conn, task_id: str, acting_id: str) -> tuple[dict, dict]:
    """(task row, its list) — 404 if missing, 403 if it is in someone else's
    personal list."""
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Task not found.")
    lst = load_list(conn, row["list_id"])
    check_list_content_access(lst, acting_id)
    return dict(row), lst


def default_list_id(conn, user_id: str) -> str:
    """The person's first personal list ("My Tasks"), created if they have
    none (e.g. they deleted it)."""
    row = conn.execute(
        "SELECT id FROM lists WHERE kind = 'personal' AND owner_user_id = ? ORDER BY position, created_at LIMIT 1",
        (user_id,),
    ).fetchone()
    if row:
        return row["id"]
    from . import config, db
    new = db.new_id()
    pos = next_list_position(conn, "personal", user_id)
    conn.execute(
        "INSERT INTO lists (id, name, kind, owner_user_id, created_at, position) VALUES (?, 'My Tasks', 'personal', ?, ?, ?)",
        (new, user_id, config.now_iso(), pos),
    )
    return new


def next_list_position(conn, kind: str, owner_id: str | None) -> int:
    if kind == "personal":
        row = conn.execute(
            "SELECT MAX(position) AS m FROM lists WHERE kind = 'personal' AND owner_user_id = ?", (owner_id,)
        ).fetchone()
    else:
        row = conn.execute("SELECT MAX(position) AS m FROM lists WHERE kind = 'shared'").fetchone()
    return 0 if row["m"] is None else row["m"] + 1


def next_task_position(conn, list_id: str) -> int:
    row = conn.execute("SELECT MAX(position) AS m FROM tasks WHERE list_id = ?", (list_id,)).fetchone()
    return 0 if row["m"] is None else row["m"] + 1


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------

def serialize_tasks(conn, rows, today: date) -> list[dict]:
    """Task rows (from TASK_SELECT) -> JSON dicts, checklist items included
    with one extra query. `overdue` is true only for an open, completion-
    required task whose date has passed; an optional one is merely `past`
    (§8l)."""
    rows = [dict(r) for r in rows]
    items_by_task: dict[str, list[dict]] = {}
    if rows:
        ids = [r["id"] for r in rows]
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            marks = ",".join("?" * len(chunk))
            for it in conn.execute(
                f"SELECT id, task_id, text, done FROM task_items WHERE task_id IN ({marks}) ORDER BY position, created_at",
                chunk,
            ):
                items_by_task.setdefault(it["task_id"], []).append(
                    {"id": it["id"], "text": it["text"], "done": bool(it["done"])}
                )
    today_iso = today.isoformat()
    from . import settings
    drive_on = settings.drive_times_enabled()   # the "Drive times" switch: off = no drive time shown
    out = []
    for r in rows:
        items = items_by_task.get(r["id"], [])
        drive = r["place_drive_minutes"] if drive_on else None
        open_ = not r["completed"]
        date_passed = bool(r["due_date"]) and r["due_date"] < today_iso
        required = bool(r["completion_required"])
        out.append({
            "id": r["id"],
            "listId": r["list_id"],
            "listName": r["list_name"],
            "listKind": r["list_kind"],
            "listRole": r.get("list_role"),         # 'maintenance' for the Maintenance list
            "title": r["title"],
            "notes": r["notes"],
            "url": r["url"],
            "dueDate": r["due_date"],
            "dueTime": r["due_time"],
            "priority": r["priority"],
            "typeId": r["type_id"],
            "type": ({"id": r["type_id"], "name": r["type_name"], "icon": r["type_icon"], "color": r["type_color"]}
                     if r["type_id"] else None),
            "placeId": r["place_id"],
            "place": ({"id": r["place_id"], "name": r["place_name"], "address": r["place_address"], "phone": r["place_phone"],
                       "driveMinutes": drive,
                       "driveTollsAvoided": None if drive is None
                       else bool(r["place_drive_tolls_avoided"])}   # estimated drive time from home, minutes; null if not computed
                      if r["place_id"] else None),
            "assignedTo": r["assigned_to"],
            "assigneeName": r["assignee_name"],
            "completionRequired": required,
            "completed": bool(r["completed"]),
            "completedAt": r["completed_at"],
            "completedBy": r["completed_by"],
            "completedByName": r["completed_by_name"],
            "createdBy": r["created_by"],
            "createdByName": r["created_by_name"],
            "createdAt": r["created_at"],
            "position": r["position"],
            "items": items,
            "itemsDone": sum(1 for i in items if i["done"]),
            "itemsTotal": len(items),
            "overdue": open_ and date_passed and required,
            "past": open_ and date_passed and not required,
        })
    return out


def get_task_json(conn, task_id: str, today: date) -> dict:
    row = conn.execute(TASK_SELECT + " WHERE t.id = ?", (task_id,)).fetchone()
    return serialize_tasks(conn, [row], today)[0]


# ---------------------------------------------------------------------------
# Sorting (§8d). Ties always fall back to manual position, then created_at.
# ---------------------------------------------------------------------------

def _manual_key(t: dict):
    return (t["position"], t["createdAt"])


def _due_key(t: dict):
    return (
        t["dueDate"] is None,
        t["dueDate"] or "",
        t["dueTime"] is not None,      # all-day first, then timed
        t["dueTime"] or "",
    )


def sort_tasks(tasks: list[dict], sort: str) -> list[dict]:
    base = sorted(tasks, key=_manual_key)  # stable base: position, created_at
    if sort == "manual":
        return base
    if sort == "due":
        return sorted(base, key=_due_key)
    if sort == "priority":
        return sorted(base, key=lambda t: PRIORITY_RANK.get(t["priority"], 3))
    if sort == "assignee":
        return sorted(base, key=lambda t: (t["assigneeName"] is None, (t["assigneeName"] or "").lower()))
    if sort == "created":
        return sorted(base, key=lambda t: t["createdAt"], reverse=True)
    if sort == "completed":
        return sorted(base, key=lambda t: t["completedAt"] or "", reverse=True)
    raise ValueError(sort)


def sort_for_day(tasks: list[dict]) -> list[dict]:
    """Within one day: all-day first, then timed by time, then priority (high
    first), then manual position (§8j)."""
    return sorted(
        tasks,
        key=lambda t: (t["dueTime"] is not None, t["dueTime"] or "",
                       PRIORITY_RANK.get(t["priority"], 3), t["position"], t["createdAt"]),
    )


def sort_for_dashboard(tasks: list[dict]) -> list[dict]:
    return sorted(tasks, key=lambda t: (t["dueDate"] or "9999", t["dueTime"] is not None, t["dueTime"] or "",
                                        PRIORITY_RANK.get(t["priority"], 3), t["position"]))
