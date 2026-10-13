"""Deleted shared lists are kept for 30 days (SPEC §6): anyone may delete a shared list, so an admin can bring
one back — with its tasks, their checklists and "Remind me at" alarms — from Admin → Storage → Deleted lists.

Deleting puts a copy of the list, its tasks, their checklist items and unsent alarms in `list_bin` (one row, JSON)
and the task ids in `list_bin_tasks`, then deletes the list as before (its tasks go with it). The tasks' files
stay where they are while the list is in the bin (housekeeping and task_files.flush leave the rows of binned tasks
alone); once the 30 days are over, or an admin empties it, they move to _deleted like any deleted task's files.
Personal lists are deleted at once, as before: only their owner can delete them.
"""
import json
import logging
from datetime import date, timedelta

from starlette.exceptions import HTTPException

from . import config, task_files, taskview

logger = logging.getLogger("list_bin")

KEEP_DAYS = 30
NOT_BINNED = "task_id NOT IN (SELECT task_id FROM list_bin_tasks)"      # for queries on maint_files


def _rows(conn, sql: str, args) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, args)]


def put(conn, lst: dict, deleted_by: str) -> int:
    """In the caller's transaction: copy the list into the bin and delete it. Returns how many tasks went."""
    tasks = _rows(conn, "SELECT * FROM tasks WHERE list_id = ?", (lst["id"],))
    ids = [t["id"] for t in tasks]
    items: list[dict] = []
    alarms: list[dict] = []
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        marks = ",".join("?" * len(chunk))
        items += _rows(conn, f"SELECT * FROM task_items WHERE task_id IN ({marks})", chunk)
        alarms += _rows(conn, f"SELECT * FROM task_alarms WHERE task_id IN ({marks}) AND sent_at IS NULL", chunk)
    data = {"list": dict(lst), "tasks": tasks, "items": items, "alarms": alarms}
    conn.execute("DELETE FROM list_bin WHERE id = ?", (lst["id"],))
    conn.execute("INSERT INTO list_bin (id, name, kind, deleted_by, deleted_at, task_count, data) VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (lst["id"], lst["name"], lst["kind"], deleted_by, config.now_iso(), len(tasks), json.dumps(data)))
    conn.executemany("INSERT OR REPLACE INTO list_bin_tasks (task_id, list_id) VALUES (?, ?)", [(t, lst["id"]) for t in ids])
    conn.execute("DELETE FROM lists WHERE id = ?", (lst["id"],))       # cascades its tasks
    return len(tasks)


def entries(conn) -> list[dict]:
    """Admin → Storage → Deleted lists, newest first."""
    out = []
    for r in conn.execute("SELECT b.id, b.name, b.deleted_at, b.task_count, u.name AS by_name FROM list_bin b "
                          "LEFT JOIN users u ON u.id = b.deleted_by ORDER BY b.deleted_at DESC"):
        d = config.to_local_date(r["deleted_at"])
        out.append({"id": r["id"], "name": r["name"], "deletedAt": r["deleted_at"], "deletedBy": r["by_name"],
                    "taskCount": r["task_count"],
                    "goneOn": (d + timedelta(days=KEEP_DAYS)).isoformat() if d else None})
    return out


def _columns(conn, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _insert(conn, table: str, row: dict, cols: set[str]) -> None:
    keep = {k: v for k, v in row.items() if k in cols}
    conn.execute(f"INSERT INTO {table} ({', '.join(keep)}) VALUES ({', '.join('?' * len(keep))})", list(keep.values()))


def restore(conn, list_id: str, admin_id: str) -> dict:
    """Bring a binned list back (at the end of the shared lists). Anything it pointed at that is gone since — a
    place, a task type, a person — is left empty. Returns the restored list row."""
    r = conn.execute("SELECT data FROM list_bin WHERE id = ?", (list_id,)).fetchone()
    if not r:
        raise HTTPException(404, "That deleted list isn't in the bin (any more).")
    data = json.loads(r["data"])
    lst = dict(data["list"])
    if conn.execute("SELECT 1 FROM lists WHERE id = ?", (list_id,)).fetchone():
        raise HTTPException(409, "That list is already there.")
    lst["position"] = taskview.next_list_position(conn, lst["kind"], lst.get("owner_user_id"))
    if lst.get("role") and conn.execute("SELECT 1 FROM lists WHERE role = ?", (lst["role"],)).fetchone():
        lst["role"] = None                        # a new Maintenance list was made meanwhile: this one is a plain list
    _insert(conn, "lists", lst, _columns(conn, "lists"))
    users = {u[0] for u in conn.execute("SELECT id FROM users")}
    places = {p[0] for p in conn.execute("SELECT id FROM places")}
    types = {t[0] for t in conn.execute("SELECT id FROM task_types")}
    cols = _columns(conn, "tasks")
    restored = set()
    for t in data["tasks"]:
        t = dict(t)
        if t.get("place_id") not in places:
            t["place_id"] = None
        if t.get("type_id") not in types:
            t["type_id"] = None
        for k in ("assigned_to", "completed_by"):
            if t.get(k) not in users:
                t[k] = None
        if t.get("created_by") not in users:
            t["created_by"] = admin_id
        if conn.execute("SELECT 1 FROM tasks WHERE id = ?", (t["id"],)).fetchone():
            continue
        _insert(conn, "tasks", t, cols)
        restored.add(t["id"])
    cols = _columns(conn, "task_items")
    for it in data.get("items", []):
        if it.get("task_id") in restored:
            _insert(conn, "task_items", it, cols)
    cols = _columns(conn, "task_alarms")
    for a in data.get("alarms", []):
        if a.get("task_id") in restored and a.get("user_id") in users:
            _insert(conn, "task_alarms", a, cols)
    conn.execute("DELETE FROM list_bin WHERE id = ?", (list_id,))      # and its list_bin_tasks rows
    logger.info("Deleted list %r brought back (%d tasks).", lst["name"], len(restored))
    return dict(conn.execute("SELECT * FROM lists WHERE id = ?", (list_id,)).fetchone())


def _empty(conn, ids: list[str]) -> list[str]:
    """Forget these binned lists; their tasks' files go to _deleted on the next flush. Returns the lists' names."""
    names = []
    for list_id in ids:
        task_ids = [r[0] for r in conn.execute("SELECT task_id FROM list_bin_tasks WHERE list_id = ?", (list_id,))]
        row = conn.execute("SELECT name FROM list_bin WHERE id = ?", (list_id,)).fetchone()
        conn.execute("DELETE FROM list_bin WHERE id = ?", (list_id,))
        task_files.on_delete(conn, task_ids)
        if row:
            names.append(row["name"])
    return names


def remove(conn, list_id: str) -> None:
    """Admin: delete it for good now."""
    if not conn.execute("SELECT 1 FROM list_bin WHERE id = ?", (list_id,)).fetchone():
        raise HTTPException(404, "That deleted list isn't in the bin (any more).")
    _empty(conn, [list_id])


def purge(conn, today: date | None = None) -> int:
    """Lists deleted more than KEEP_DAYS ago go for good (housekeeping)."""
    cutoff = (config.utcnow() - timedelta(days=KEEP_DAYS)).isoformat(timespec="seconds")
    ids = [r[0] for r in conn.execute("SELECT id FROM list_bin WHERE deleted_at < ?", (cutoff,))]
    names = _empty(conn, ids)
    if names:
        logger.info("Deleted lists emptied from the bin after %d days: %s", KEEP_DAYS, ", ".join(names))
    return len(names)
