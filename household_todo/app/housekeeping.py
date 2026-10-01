"""Daily clean-up (SPEC §8m): completed tasks are deleted 60 days after they
were completed, and schedule exceptions and schedule-reminder log rows whose
dates have all passed are pruned (§8k).

The loop ticks every 60 seconds and runs a full pass on its first tick after
startup and again whenever the local date has changed. Each pass is one
transaction.
"""
import asyncio
import logging
from datetime import date, timedelta

from starlette.concurrency import run_in_threadpool

from . import config, db

logger = logging.getLogger("housekeeping")

TICK_SECONDS = 60


def purge_completed_tasks(conn, today: date) -> int:
    """Delete tasks completed more than COMPLETED_RETENTION_DAYS ago (by the
    local date of `completed_at`). Open tasks are never touched; a completed
    row with no `completed_at` is left alone. Checklist items go with the
    task (ON DELETE CASCADE). Returns the number of tasks deleted."""
    days = config.COMPLETED_RETENTION_DAYS
    # Cheap SQL pre-filter with a day of slack either side of any UTC offset,
    # then the exact local-date test in Python.
    prefilter = (today - timedelta(days=days - 1)).isoformat()
    rows = conn.execute(
        "SELECT id, completed_at FROM tasks WHERE completed = 1 AND completed_at IS NOT NULL "
        "AND substr(completed_at, 1, 10) <= ?",
        (prefilter,),
    ).fetchall()
    doomed = []
    for r in rows:
        d = config.to_local_date(r["completed_at"])
        if d is not None and (today - d).days > days:
            doomed.append(r["id"])
    for i in range(0, len(doomed), 500):
        chunk = doomed[i:i + 500]
        conn.execute(f"DELETE FROM tasks WHERE id IN ({','.join('?' * len(chunk))})", chunk)
    return len(doomed)


def prune_exceptions(conn, today: date) -> int:
    """An exception whose dates have all passed no longer affects anything
    (for a move, both `date` and `to_date`)."""
    iso = today.isoformat()
    cur = conn.execute(
        "DELETE FROM schedule_exceptions WHERE (kind IN ('skip', 'add') AND date < ?) "
        "OR (kind = 'move' AND date < ? AND to_date < ?)",
        (iso, iso, iso),
    )
    return cur.rowcount


def prune_schedule_reminder_log(conn, today: date) -> int:
    """A schedule reminder for an occurrence before today can never fire
    again, so its dedupe row is no longer needed."""
    return conn.execute("DELETE FROM schedule_reminder_log WHERE date < ?", (today.isoformat(),)).rowcount


def prune_maintenance(conn, today: date) -> None:
    """A purged one-off job's files stay in the folder, but their rows go; notification log rows
    older than a year can't matter any more."""
    conn.execute("DELETE FROM maint_files WHERE task_id IS NOT NULL AND task_id NOT IN (SELECT id FROM tasks)")
    conn.execute("DELETE FROM maint_notify_log WHERE sent_on < ?", ((today - timedelta(days=400)).isoformat(),))


def run_pass_blocking(today: date | None = None) -> dict:
    today = today or config.today()
    with db.get_conn() as conn:
        tasks = purge_completed_tasks(conn, today)
        excs = prune_exceptions(conn, today)
        prune_schedule_reminder_log(conn, today)
        prune_maintenance(conn, today)
    try:
        from . import maint_files
        maint_files.purge_deleted(today)
    except Exception:
        logger.exception("Emptying old deleted maintenance files failed")
    if tasks:
        logger.info("housekeeping: removed %d completed tasks older than %d days", tasks, config.COMPLETED_RETENTION_DAYS)
    return {"tasks": tasks, "exceptions": excs}


async def loop() -> None:
    """Started from main.py's lifespan; cancelled on shutdown."""
    last_date = None
    while True:
        try:
            today = config.today()
            if last_date != today:
                await run_in_threadpool(run_pass_blocking, today)
                last_date = today
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Housekeeping pass failed")
        await asyncio.sleep(TICK_SECONDS)
