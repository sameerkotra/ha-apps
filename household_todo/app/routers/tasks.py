"""Tasks and their checklist items (SPEC §6, §8d, §8g, §8i, §8l)."""
import re

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Query, Response

from .. import config, db, links, recurrence, reminders, task_files, taskview
from ..auth import get_acting_user

router = APIRouter(prefix="/api", tags=["tasks"])

TIME_RE = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
PRIORITIES = ("low", "medium", "high")
SORTS = ("manual", "due", "priority", "assignee", "created", "completed")
MAX_ITEMS = 100


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _item_text(value) -> str:
    if not isinstance(value, str) or not (1 <= len(value.strip()) <= 200):
        raise HTTPException(422, "A checklist item must be 1–200 characters.")
    return value.strip()


def validated_url(value):
    """The optional link, shared with schedule items: null/blank -> None, else
    an absolute http(s) URL of at most 2000 characters (422 otherwise)."""
    try:
        return links.clean_url(value)
    except ValueError as e:
        raise HTTPException(422, str(e))


def validated_place_id(conn, pid):
    """An optional place, shared with schedule items. Every place is shared
    (§8h), so existence is the only check (404 otherwise)."""
    if pid is not None:
        if not isinstance(pid, str) or not conn.execute("SELECT 1 FROM places WHERE id = ?", (pid,)).fetchone():
            raise HTTPException(404, "Place not found.")
    return pid


def _validate_task_fields(conn, body: dict, lst: dict, acting: dict, existing: dict | None) -> dict:
    """Validate the fields present in `body` and return {column: value} for
    them. `existing` is the current row on PATCH (None on create). Only
    `title` is ever required (§8g)."""
    updates: dict = {}
    creating = existing is None

    if creating or "title" in body:
        title = body.get("title")
        if not isinstance(title, str) or not (1 <= len(title.strip()) <= 200):
            raise HTTPException(422, "A task needs a title (1–200 characters).")
        updates["title"] = title.strip()

    if "notes" in body:
        notes = body["notes"]
        if notes is not None and (not isinstance(notes, str) or len(notes) > 5000):
            raise HTTPException(422, "Notes must be text of at most 5000 characters.")
        updates["notes"] = (notes.strip() or None) if isinstance(notes, str) else None

    if "url" in body:
        updates["url"] = validated_url(body["url"])

    if "due_date" in body:
        d = body["due_date"]
        if d is not None and recurrence.parse_date(d) is None:
            raise HTTPException(422, "due_date must be a valid date (YYYY-MM-DD).")
        updates["due_date"] = d

    if "due_time" in body:
        t = body["due_time"]
        if t is not None and (not isinstance(t, str) or not TIME_RE.match(t)):
            raise HTTPException(422, "due_time must be HH:MM in 24-hour form.")
        updates["due_time"] = t

    if "priority" in body:
        p = body["priority"]
        if p is not None and p not in PRIORITIES:
            raise HTTPException(422, "priority must be low, medium, high or null.")
        updates["priority"] = p

    if "completion_required" in body:
        cr = body["completion_required"]
        if not isinstance(cr, bool):
            raise HTTPException(422, "completion_required must be true or false.")
        updates["completion_required"] = 1 if cr else 0

    if "type_id" in body:
        tid = body["type_id"]
        if tid is not None:
            if not isinstance(tid, str) or not conn.execute("SELECT 1 FROM task_types WHERE id = ?", (tid,)).fetchone():
                raise HTTPException(422, "That task type doesn't exist.")
        updates["type_id"] = tid

    if "place_id" in body:
        updates["place_id"] = validated_place_id(conn, body["place_id"])

    if "assigned_to" in body:
        uid = body["assigned_to"]
        old = existing["assigned_to"] if existing else None
        if uid is not None:
            user = conn.execute("SELECT id, disabled FROM users WHERE id = ?", (uid,)).fetchone() if isinstance(uid, str) else None
            if not user:
                raise HTTPException(422, "That person isn't known to this app.")
            if uid != old:
                if lst["kind"] == "personal" and uid != lst["owner_user_id"]:
                    raise HTTPException(400, "A task in a personal list can only be assigned to its owner.")
                if user["disabled"]:
                    raise HTTPException(400, "That person is disabled and can't be assigned tasks.")
        updates["assigned_to"] = uid

    # a time needs a date; clearing the date clears the time (§8g)
    eff_date = updates["due_date"] if "due_date" in updates else (existing["due_date"] if existing else None)
    if eff_date is None:
        if updates.get("due_time") is not None:
            raise HTTPException(422, "A due time needs a due date.")
        if "due_date" in updates or "due_time" in updates or creating:
            updates["due_time"] = None
    return updates


def _validated_items(body: dict) -> list[str]:
    items = body.get("items")
    if items is None:
        return []
    if not isinstance(items, list) or len(items) > MAX_ITEMS:
        raise HTTPException(422, f"items must be a list of at most {MAX_ITEMS} strings.")
    return [_item_text(i) for i in items]


def _create_task(conn, lst: dict, body: dict, acting: dict) -> tuple[str, dict]:
    updates = _validate_task_fields(conn, body, lst, acting, None)
    items = _validated_items(body)
    task_id = db.new_id()
    cols = {
        "id": task_id, "list_id": lst["id"], "title": updates["title"],
        "notes": updates.get("notes"), "url": updates.get("url"), "due_date": updates.get("due_date"), "due_time": updates.get("due_time"),
        "priority": updates.get("priority"), "type_id": updates.get("type_id"), "place_id": updates.get("place_id"),
        "assigned_to": updates.get("assigned_to"), "completion_required": updates.get("completion_required", 0),
        "completed": 0, "created_by": acting["id"], "created_at": config.now_iso(),
        "position": taskview.next_task_position(conn, lst["id"]),
    }
    conn.execute(f"INSERT INTO tasks ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", list(cols.values()))
    for pos, text in enumerate(items):
        conn.execute(
            "INSERT INTO task_items (id, task_id, text, done, position, created_at) VALUES (?, ?, ?, 0, ?, ?)",
            (db.new_id(), task_id, text, pos, config.now_iso()),
        )
    return task_id, cols


def _queue_ping(background: BackgroundTasks, acting: dict, old_assignee, cols_or_row: dict) -> None:
    """An assignment ping goes out only when the assignee actually changed
    to a *different person than the one acting* (§8b). Best effort, after
    the response."""
    new = cols_or_row.get("assigned_to")
    if new and new != old_assignee and new != acting["id"]:
        background.add_task(
            reminders.send_assignment_ping_blocking, new, acting["name"], cols_or_row["title"], cols_or_row.get("due_date"),
            url=cols_or_row.get("url"), place_id=cols_or_row.get("place_id"),
        )


# ---------------------------------------------------------------------------
# Reading tasks
# ---------------------------------------------------------------------------

@router.get("/lists/{list_id}/tasks")
def list_tasks(
    list_id: str,
    show: str = Query(default="open"),
    sort: str | None = Query(default=None),
    assignee: str | None = Query(default=None),
    priority: str | None = Query(default=None),
    type: str | None = Query(default=None),
    place: str | None = Query(default=None),
    completion: str | None = Query(default=None),
    acting: dict = Depends(get_acting_user),
):
    if show not in ("open", "completed"):
        raise HTTPException(422, "show must be 'open' or 'completed'.")
    if sort is None:
        sort = "manual" if show == "open" else "completed"
    if sort not in SORTS:
        raise HTTPException(422, f"sort must be one of {', '.join(SORTS)}.")
    if sort == "completed" and show != "completed":
        raise HTTPException(422, "sort=completed only works with show=completed.")
    if sort == "manual" and show == "completed":
        raise HTTPException(422, "sort=manual isn't available in the Completed view.")
    if priority is not None and priority not in (*PRIORITIES, "none"):
        raise HTTPException(422, "priority must be low, medium, high or none.")
    if completion is not None and completion not in ("required", "optional"):
        raise HTTPException(422, "completion must be 'required' or 'optional'.")

    with db.get_conn() as conn:
        lst = taskview.load_list(conn, list_id)
        taskview.check_list_content_access(lst, acting["id"])
        rows = conn.execute(
            taskview.TASK_SELECT + " WHERE t.list_id = ? AND t.completed = ?",
            (list_id, 1 if show == "completed" else 0),
        ).fetchall()
        tasks = taskview.serialize_tasks(conn, rows, config.today())

    def keep(t: dict) -> bool:
        if assignee == "me" and t["assignedTo"] != acting["id"]:
            return False
        if assignee == "unassigned" and t["assignedTo"] is not None:
            return False
        if assignee not in (None, "", "me", "unassigned") and t["assignedTo"] != assignee:
            return False
        if priority == "none" and t["priority"] is not None:
            return False
        if priority in PRIORITIES and t["priority"] != priority:
            return False
        if type == "none" and t["typeId"] is not None:
            return False
        if type not in (None, "", "none") and t["typeId"] != type:
            return False
        if place and t["placeId"] != place:
            return False
        if completion == "required" and not t["completionRequired"]:
            return False
        if completion == "optional" and t["completionRequired"]:
            return False
        return True

    return taskview.sort_tasks([t for t in tasks if keep(t)], sort)


# ---------------------------------------------------------------------------
# Creating, changing and deleting tasks
# ---------------------------------------------------------------------------

@router.post("/lists/{list_id}/tasks", status_code=201)
def create_task_in_list(
    list_id: str, background: BackgroundTasks, body: dict = Body(...), acting: dict = Depends(get_acting_user)
):
    return _add_task(list_id, body, acting, background)


@router.post("/tasks", status_code=201)
def quick_add(background: BackgroundTasks, body: dict = Body(...), acting: dict = Depends(get_acting_user)):
    """Quick add: same body plus an optional `list_id` (default: the acting
    user's first personal list). Only `title` is required."""
    list_id = body.get("list_id")
    if list_id is not None and not isinstance(list_id, str):
        raise HTTPException(422, "list_id must be a list id.")
    return _add_task(list_id, body, acting, background)


def _add_task(list_id: str | None, body: dict, acting: dict, background: BackgroundTasks) -> dict:
    """Create a task in a list the acting user may add to (None = their first personal list)."""
    with db.get_conn() as conn:
        if list_id is None:
            list_id = taskview.default_list_id(conn, acting["id"])
        lst = taskview.load_list(conn, list_id)
        taskview.check_list_content_access(lst, acting["id"])
        task_id, cols = _create_task(conn, lst, body, acting)
        result = taskview.get_task_json(conn, task_id, config.today())
    _queue_ping(background, acting, None, cols)
    return result


@router.patch("/tasks/{task_id}")
def update_task(task_id: str, background: BackgroundTasks, body: dict = Body(...), acting: dict = Depends(get_acting_user)):
    with db.get_conn() as conn:
        task, lst = taskview.load_task(conn, task_id, acting["id"])
        moving = "list_id" in body and body["list_id"] != task["list_id"]
        if moving:
            # move to another list the acting user may add to (a shared list, or their own personal one)
            if not isinstance(body["list_id"], str):
                raise HTTPException(422, "list_id must be a list id.")
            lst = taskview.load_list(conn, body["list_id"])
            taskview.check_list_content_access(lst, acting["id"])
        updates = _validate_task_fields(conn, body, lst, acting, task)
        if moving:
            updates["list_id"] = lst["id"]
            updates["position"] = taskview.next_task_position(conn, lst["id"])        # to the end of the new list
            assignee = updates.get("assigned_to", task["assigned_to"])
            if lst["kind"] == "personal" and assignee not in (None, lst["owner_user_id"]):
                updates["assigned_to"] = None     # a personal list's tasks can only be its owner's

        if "completed" in body:
            done = body["completed"]
            if not isinstance(done, bool):
                raise HTTPException(422, "completed must be true or false.")
            if done and not task["completed"]:
                updates.update(completed=1, completed_at=config.now_iso(), completed_by=acting["id"])
                if task_files.on_done(conn, [task_id]):       # its files go, unless kept (SPEC §17)
                    task_files.flush_soon(background)
            elif not done and task["completed"]:
                updates.update(completed=0, completed_at=None, completed_by=None)
                if task_files.on_reopen(conn, task_id):       # …and come back within 30 days
                    task_files.flush_soon(background)

        if updates:
            conn.execute(
                f"UPDATE tasks SET {', '.join(k + ' = ?' for k in updates)} WHERE id = ?",
                [*updates.values(), task_id],
            )
        result = taskview.get_task_json(conn, task_id, config.today())
    if "assigned_to" in updates:
        _queue_ping(background, acting, task["assigned_to"],
                    {"assigned_to": updates["assigned_to"], "title": result["title"], "due_date": result["dueDate"],
                     "url": result["url"]})
    return result


@router.delete("/tasks/{task_id}", status_code=204)
def delete_task(task_id: str, background: BackgroundTasks, acting: dict = Depends(get_acting_user)):
    with db.get_conn() as conn:
        taskview.load_task(conn, task_id, acting["id"])
        if task_files.on_delete(conn, [task_id]):              # its files move to _deleted (SPEC §17)
            task_files.flush_soon(background)
        conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    return Response(status_code=204)


@router.post("/lists/{list_id}/tasks/reorder")
def reorder_tasks(list_id: str, body: dict = Body(...), acting: dict = Depends(get_acting_user)):
    """Persist manual drag order. Tasks not in the payload keep their
    position, so a concurrent edit never gets lost (§8d)."""
    ids = body.get("ordered_ids")
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids) or len(set(ids)) != len(ids):
        raise HTTPException(400, "ordered_ids must be a list of distinct task ids.")
    with db.get_conn() as conn:
        lst = taskview.load_list(conn, list_id)
        taskview.check_list_content_access(lst, acting["id"])
        for i in ids:
            if not conn.execute("SELECT 1 FROM tasks WHERE id = ? AND list_id = ?", (i, list_id)).fetchone():
                raise HTTPException(400, "ordered_ids contains a task that isn't in this list.")
        for pos, i in enumerate(ids):
            conn.execute("UPDATE tasks SET position = ? WHERE id = ?", (pos, i))
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Checklist items (§8i) — their own routes so two people ticking different
# items at once never overwrite each other
# ---------------------------------------------------------------------------

def _load_item(conn, item_id: str, acting_id: str) -> dict:
    row = conn.execute("SELECT * FROM task_items WHERE id = ?", (item_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Checklist item not found.")
    taskview.load_task(conn, row["task_id"], acting_id)   # 404 / 403 through the parent
    return dict(row)


@router.post("/tasks/{task_id}/items", status_code=201)
def add_items(task_id: str, body: dict = Body(...), acting: dict = Depends(get_acting_user)):
    if "texts" in body:
        texts = body["texts"]
        if not isinstance(texts, list) or not texts:
            raise HTTPException(422, "texts must be a non-empty list of strings.")
        # pasted lists often contain blank lines; skip those
        cleaned = [_item_text(t) for t in texts if not (isinstance(t, str) and not t.strip())]
    elif "text" in body:
        cleaned = [_item_text(body["text"])]
    else:
        raise HTTPException(422, "Send {\"text\": ...} or {\"texts\": [...]}.")
    if not cleaned:
        raise HTTPException(422, "There was nothing to add.")
    with db.get_conn() as conn:
        taskview.load_task(conn, task_id, acting["id"])
        row = conn.execute("SELECT COUNT(*) AS n, MAX(position) AS m FROM task_items WHERE task_id = ?", (task_id,)).fetchone()
        if row["n"] + len(cleaned) > MAX_ITEMS:
            raise HTTPException(422, f"A task can have at most {MAX_ITEMS} checklist items.")
        pos = 0 if row["m"] is None else row["m"] + 1
        for text in cleaned:
            conn.execute(
                "INSERT INTO task_items (id, task_id, text, done, position, created_at) VALUES (?, ?, ?, 0, ?, ?)",
                (db.new_id(), task_id, text, pos, config.now_iso()),
            )
            pos += 1
        return taskview.get_task_json(conn, task_id, config.today())


@router.patch("/task-items/{item_id}")
def update_item(item_id: str, body: dict = Body(...), acting: dict = Depends(get_acting_user)):
    with db.get_conn() as conn:
        item = _load_item(conn, item_id, acting["id"])
        updates = {}
        if "text" in body:
            updates["text"] = _item_text(body["text"])
        if "done" in body:
            if not isinstance(body["done"], bool):
                raise HTTPException(422, "done must be true or false.")
            updates["done"] = 1 if body["done"] else 0
        if updates:
            conn.execute(
                f"UPDATE task_items SET {', '.join(k + ' = ?' for k in updates)} WHERE id = ?",
                [*updates.values(), item_id],
            )
        return taskview.get_task_json(conn, item["task_id"], config.today())


@router.delete("/task-items/{item_id}", status_code=204)
def delete_item(item_id: str, acting: dict = Depends(get_acting_user)):
    with db.get_conn() as conn:
        _load_item(conn, item_id, acting["id"])
        conn.execute("DELETE FROM task_items WHERE id = ?", (item_id,))
    return Response(status_code=204)
