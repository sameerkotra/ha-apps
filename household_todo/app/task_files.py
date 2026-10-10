"""Files on tasks (SPEC §17): tickets, booking confirmations, photos — attached to any task and kept in the files
folder (maint_files.py), under Tasks/<title (id8)>/ (Jobs/… for a task in the Maintenance list).

A file without *Keep after done* goes when its task is ticked off: it moves to _deleted/<date>/ (emptied after
30 days) and comes back if the task is unticked within those 30 days. Deleting a task moves all its files there
(no undo; they can be fetched from the folder by hand for 30 days). The moves wait while the folder isn't
connected (`pending`), and are made by flush() once it is: after the request that caused them, every 5 minutes
with the folder check, and in the nightly housekeeping pass.

A maint_files row with a task_id is in one of four states:
    listed           removed_at NULL, pending NULL        the file is at rel_path
    to remove        removed_at NULL, pending 'remove'    still at rel_path; moves on the next flush
    removed          removed_at set,  pending NULL        at rel_path under _deleted; removed_rel = where it was
    to restore       removed_at set,  pending 'restore'   moves back to removed_rel's folder on the next flush
"""
import logging
import os
import shutil
import threading
import time
from datetime import timedelta

from starlette.exceptions import HTTPException

from . import config, db, maint_files

logger = logging.getLogger("task_files")

MAX_FILES = 10
UNDO_DAYS = maint_files.KEEP_DELETED_DAYS
LISTED = "((removed_at IS NULL AND pending IS NULL) OR pending = 'restore')"
_flush_lock = threading.Lock()


def file_json(r) -> dict:
    return {"id": r["id"], "name": r["name"], "size": r["size"], "mime": r["mime"], "thumb": bool(r["thumb"]),
            "createdAt": r["created_at"], "keepAfterDone": bool(r["keep_after_done"])}


def _marks(ids) -> str:
    return ",".join("?" * len(ids))


# ---------- reading ----------
def listed(conn, task_id: str) -> list[dict]:
    return [dict(r) for r in conn.execute(
        f"SELECT * FROM maint_files WHERE task_id = ? AND {LISTED} ORDER BY created_at", (task_id,))]


def counts(conn, task_ids: list[str]) -> dict[str, int]:
    """{task id: number of files listed on it} for the tasks that have any."""
    out: dict[str, int] = {}
    for i in range(0, len(task_ids), 500):
        chunk = task_ids[i:i + 500]
        for r in conn.execute(f"SELECT task_id, COUNT(*) AS n FROM maint_files WHERE task_id IN ({_marks(chunk)}) "
                              f"AND {LISTED} GROUP BY task_id", chunk):
            out[r["task_id"]] = r["n"]
    return out


def the_ticket(conn, task_id: str) -> dict | None:
    """The task's one PDF, when it has exactly one listed PDF (the reminder's *Open ticket* button)."""
    rows = conn.execute(f"SELECT id, name FROM maint_files WHERE task_id = ? AND {LISTED} AND mime = 'application/pdf'",
                        (task_id,)).fetchall()
    return dict(rows[0]) if len(rows) == 1 else None


def load(conn, task_id: str, file_id: str) -> dict:
    r = conn.execute(f"SELECT * FROM maint_files WHERE id = ? AND task_id = ? AND {LISTED}", (file_id, task_id)).fetchone()
    if not r:
        raise HTTPException(404, "That file isn't on this task (any more).")
    return dict(r)


def folder_for(conn, task: dict, list_role: str | None) -> str:
    """Where a new file on the task goes: next to its other files, else its own folder."""
    r = conn.execute("SELECT rel_path FROM maint_files WHERE task_id = ? AND removed_at IS NULL ORDER BY created_at "
                     "LIMIT 1", (task["id"],)).fetchone()
    if r:
        return os.path.dirname(r["rel_path"])
    if list_role == "maintenance":
        return maint_files.job_folder_name(task)
    return os.path.join(maint_files.TASKS, maint_files.clean(task["title"], 60, "Task") + f" ({task['id'][:8]})")


# ---------- what ticking off, unticking and deleting do (in the caller's transaction) ----------
def on_done(conn, task_ids) -> int:
    """The tasks were ticked off: their files without Keep go (on the next flush)."""
    ids = [i for i in task_ids if i]
    if not ids:
        return 0
    n = conn.execute(f"UPDATE maint_files SET pending = 'remove' WHERE task_id IN ({_marks(ids)}) "
                     "AND removed_at IS NULL AND pending IS NULL AND keep_after_done = 0", ids).rowcount
    # unticked and ticked off again while the folder was away: those never left _deleted
    conn.execute(f"UPDATE maint_files SET pending = NULL WHERE task_id IN ({_marks(ids)}) AND pending = 'restore' "
                 "AND keep_after_done = 0", ids)
    return n


def on_reopen(conn, task_id: str) -> int:
    """The task was unticked: files still waiting to go stay; files removed in the last 30 days come back."""
    conn.execute("UPDATE maint_files SET pending = NULL WHERE task_id = ? AND pending = 'remove'", (task_id,))
    since = (config.utcnow() - timedelta(days=UNDO_DAYS)).isoformat(timespec="seconds")
    return conn.execute("UPDATE maint_files SET pending = 'restore' WHERE task_id = ? AND removed_at IS NOT NULL "
                        "AND pending IS NULL AND removed_at >= ?", (task_id, since)).rowcount


def on_delete(conn, task_ids) -> int:
    """The tasks are about to be deleted: all their files go, Keep or not (no undo)."""
    ids = [i for i in task_ids if i]
    if not ids:
        return 0
    conn.execute(f"UPDATE maint_files SET pending = NULL WHERE task_id IN ({_marks(ids)}) AND pending = 'restore'", ids)
    return conn.execute(f"UPDATE maint_files SET pending = 'remove' WHERE task_id IN ({_marks(ids)}) "
                        "AND removed_at IS NULL", ids).rowcount


# ---------- the moves ----------
def _move(root: str, rel: str, dest_dir_rel: str) -> str | None:
    """Move the file `rel` into the folder `dest_dir_rel` (a free name); its new relative path, or None when it
    isn't there any more."""
    try:
        src = maint_files._within(root, rel)
        dest_dir = maint_files._within(root, dest_dir_rel)
    except HTTPException:
        return None
    if not os.path.isfile(src):
        return None
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, maint_files._unique(dest_dir, os.path.basename(src)))
    shutil.move(src, dest)
    return os.path.relpath(dest, root)


def _drop(conn, root: str, row: dict) -> None:
    conn.execute("DELETE FROM maint_files WHERE id = ?", (row["id"],))
    maint_files.remove_thumbs(root, [row["thumb"]])


def _flush_one(root: str, file_id: str) -> None:
    with db.get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")            # nobody changes the row while its file moves
        r = conn.execute("SELECT f.*, t.id AS task_there FROM maint_files f LEFT JOIN tasks t ON t.id = f.task_id "
                         "WHERE f.id = ?", (file_id,)).fetchone()
        if not r or not r["pending"]:
            return
        r = dict(r)
        if r["pending"] == "remove":
            day = config.today().isoformat()
            new = _move(root, r["rel_path"], os.path.join(maint_files.DELETED, day, os.path.dirname(r["rel_path"])))
            if new is None or not r["task_there"]:
                _drop(conn, root, r)                # gone already, or its task is: nothing to bring back
            else:
                conn.execute("UPDATE maint_files SET rel_path = ?, removed_rel = ?, removed_at = ?, pending = NULL "
                             "WHERE id = ?", (new, r["rel_path"], config.now_iso(), r["id"]))
        elif r["pending"] == "restore":
            back = os.path.dirname(r["removed_rel"] or "") or maint_files.TASKS
            new = _move(root, r["rel_path"], back) if r["task_there"] else None
            if new is None:
                _drop(conn, root, r)                # emptied from _deleted, or moved by hand
            else:
                conn.execute("UPDATE maint_files SET rel_path = ?, name = ?, removed_rel = NULL, removed_at = NULL, "
                             "pending = NULL WHERE id = ?", (new, os.path.basename(new), r["id"]))


def flush() -> int:
    """Make the moves waiting for the folder, and forget files removed more than 30 days ago (their folder in
    _deleted is emptied by maint_files.purge_deleted). Returns how many moves were made; 0 while offline."""
    if not maint_files.is_online():
        return 0
    root = os.path.realpath(maint_files.configured())
    n = 0
    with _flush_lock:
        with db.get_conn() as conn:
            ids = [r["id"] for r in conn.execute(
                "SELECT id FROM maint_files WHERE task_id IS NOT NULL AND pending IS NOT NULL ORDER BY created_at")]
        for fid in ids:
            try:
                _flush_one(root, fid)
                n += 1
            except Exception:
                logger.exception("Couldn't move a task's file")
        cutoff = (config.utcnow() - timedelta(days=UNDO_DAYS + 1)).isoformat(timespec="seconds")
        with db.get_conn() as conn:
            for r in [dict(x) for x in conn.execute(
                    "SELECT id, thumb FROM maint_files WHERE task_id IS NOT NULL AND removed_at IS NOT NULL "
                    "AND pending IS NULL AND removed_at < ?", (cutoff,))]:
                _drop(conn, root, r)
            # rows of tasks that are gone (deleted with a list, purged) once nothing waits on them
            for r in [dict(x) for x in conn.execute(
                    "SELECT id, thumb FROM maint_files WHERE task_id IS NOT NULL AND removed_at IS NOT NULL "
                    "AND pending IS NULL AND task_id NOT IN (SELECT id FROM tasks)")]:
                _drop(conn, root, r)
    return n


def flush_soon(background=None) -> None:
    """After a change that queued moves: flush once the caller's transaction is committed — as a FastAPI
    background task when there is one, else a moment later on a thread (the Household Assistant's tools)."""
    if background is not None:
        background.add_task(_flush_quietly)
    elif config.BACKGROUND_LOOPS:
        threading.Thread(target=lambda: (time.sleep(2), _flush_quietly()), daemon=True, name="task_files").start()


def _flush_quietly() -> None:
    try:
        flush()
    except Exception:
        logger.exception("Moving tasks' files failed")
