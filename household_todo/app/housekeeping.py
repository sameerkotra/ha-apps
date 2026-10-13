"""Daily clean-up (SPEC §8m): completed tasks are deleted 60 days after they
were completed, schedule exceptions and schedule-reminder log rows whose
dates have all passed are pruned (§8k), the digest's and weekly summary's
"sent" rows go after 30 days, and leftover backup temp files after 6 hours.

The loop ticks every 60 seconds and runs a full pass on its first tick after
startup and again whenever the local date has changed. Each pass is one
transaction. Every tick also runs the app-messages outbox (app_messages.py).
"""
import logging
import os
import tempfile
from datetime import date, timedelta

from starlette.concurrency import run_in_threadpool

from . import config, db
from .common import backup_core
from .common import housekeeping as jobs_core

logger = logging.getLogger("housekeeping")

TICK_SECONDS = 60


def purge_completed_tasks(conn, today: date) -> int:
    """Delete tasks completed more than COMPLETED_RETENTION_DAYS ago (by the
    local date of `completed_at`). Open tasks are never touched; a completed
    row with no `completed_at` is left alone. Checklist items go with the
    task (ON DELETE CASCADE). Returns the number of tasks deleted."""
    days = config.COMPLETED_RETENTION_DAYS
    # Cheap SQL pre-filter with a day of slack either side of any UTC offset,
    # then the exact local-date test in Python. A plain range on completed_at
    # (an ISO timestamp: "completed on or before day X" is "< day X+1") can use
    # idx_tasks_completed, where substr() could not.
    before = (today - timedelta(days=days - 2)).isoformat()
    rows = conn.execute(
        "SELECT id, completed_at FROM tasks WHERE completed = 1 AND completed_at < ?",
        (before,),
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


def prune_maintenance(conn, today: date, folder_online: bool = True) -> list[str]:
    """A purged task's kept files stay in the folder, but their rows go (not while a move waits for the folder:
    task_files.flush makes it, then forgets the row); notification log rows older than a year can't matter any
    more. Returns the previews (thumbnails) of the forgotten rows, for the caller to delete once this is saved;
    while the folder isn't connected, rows with a preview wait, so no preview is left behind."""
    from .list_bin import NOT_BINNED            # a deleted list's tasks keep their files while it's in the bin
    rows = conn.execute("SELECT id, thumb FROM maint_files WHERE task_id IS NOT NULL AND pending IS NULL "
                        f"AND task_id NOT IN (SELECT id FROM tasks) AND {NOT_BINNED}").fetchall()
    gone = [r for r in rows if folder_online or not r["thumb"]]
    for i in range(0, len(gone), 500):
        chunk = [r["id"] for r in gone[i:i + 500]]
        conn.execute(f"DELETE FROM maint_files WHERE id IN ({','.join('?' * len(chunk))})", chunk)
    conn.execute("DELETE FROM maint_notify_log WHERE sent_on < ?", ((today - timedelta(days=400)).isoformat(),))
    return [r["thumb"] for r in gone if r["thumb"]]


LOG_DAYS = 30


def prune_sent_logs(conn, today: date) -> int:
    """The daily digest's and the weekly summary's "already sent" rows only matter for the day they were sent
    (a digest is never sent for a past day): rows older than LOG_DAYS go."""
    cutoff = (today - timedelta(days=LOG_DAYS)).isoformat()
    n = conn.execute("DELETE FROM notification_log WHERE sent_on < ?", (cutoff,)).rowcount
    n += conn.execute("DELETE FROM weekly_summary_log WHERE sent_on < ?", (cutoff,)).rowcount
    return n


STALE_TEMP_S = 6 * 3600


def remove_stale_temp() -> int:
    """Backup downloads a browser never finished and restores cut short leave a temp file behind; gone after
    six hours (only this app's own: their names start with db.TEMP_PREFIX)."""
    n = 0
    for folder in {tempfile.gettempdir(), config.DATA_DIR}:
        n += backup_core.remove_stale(folder, db.TEMP_PREFIX, STALE_TEMP_S)
    return n


def run_pass_blocking(today: date | None = None) -> dict:
    from . import maint_files, task_files
    today = today or config.today()
    online = maint_files.is_online()
    with db.get_conn() as conn:
        tasks = purge_completed_tasks(conn, today)
        excs = prune_exceptions(conn, today)
        prune_schedule_reminder_log(conn, today)
        thumbs = prune_maintenance(conn, today, online)
        prune_sent_logs(conn, today)
        from . import list_bin
        list_bin.purge(conn, today)        # shared lists deleted 30+ days ago (their files move on the flush below)
    try:
        if thumbs:
            maint_files.remove_thumbs(os.path.realpath(maint_files.configured()), thumbs)
        task_files.flush()                 # moves waiting for the folder; forgets files removed 30+ days ago
        maint_files.purge_deleted(today)
    except Exception:
        logger.exception("Emptying old deleted files failed")
    try:
        remove_stale_temp()
    except Exception:
        logger.exception("Removing old temp files failed")
    if tasks:
        logger.info("housekeeping: removed %d completed tasks older than %d days", tasks, config.COMPLETED_RETENTION_DAYS)
    return {"tasks": tasks, "exceptions": excs}


async def loop() -> None:
    """Started from main.py's lifespan; cancelled on shutdown. A pass on the
    first tick and whenever the local date has changed."""
    last = {"date": None}

    async def step() -> None:
        today = config.today()
        if last["date"] != today:
            await run_in_threadpool(run_pass_blocking, today)
            last["date"] = today
        try:     # the app-messages outbox (APP_MESSAGES_SPEC §4): re-sends, expiry, the six-hourly hello
            from . import app_messages
            await run_in_threadpool(app_messages.run_outbox)
        except Exception:
            logger.exception("App messages outbox run failed")

    await jobs_core.periodic(TICK_SECONDS, step, thread=False, log=logger, error="Housekeeping pass failed")()
