"""Background jobs (one tick a minute): the documents folder check every 5 minutes, the index walk every
`scan_minutes`, and once an hour Trash past `trash_days`, nodes gone for 30 days, previous copies of replaced
files past 7 days, and leftover temp files (in the documents folder's and every admin shared folder's .tmp).
Every tick also runs the app-messages outbox (app_messages.py) and the filing rules for files that arrived
(filing.py); the clean-up rules run once a day (checked hourly). Nothing writes while the documents folder isn't
usable or a restore is running; filing and clean-up wait while read-only mode is on."""
import logging
import os
import time

from . import activity, ai, ai_usage, app_messages, db, filing, follows, ha_client, pdftext, settings, storage_report
from .store import index, moving, roots, trash, versions

logger = logging.getLogger("housekeeping")
TICK_SECONDS = 60
TEXT_SECONDS = 60
_state = {"last_scan": 0.0}


def _safe(fn, *args):
    try:
        return fn(*args)
    except Exception:
        logger.exception("%s failed", getattr(fn, "__name__", "A job"))


def remove_stale_tmp(max_age: int = 3600) -> int:
    """Half-written temp files a crash (or a dropped upload) left in a .tmp folder."""
    if moving.is_read_only():
        return 0
    dirs = []
    try:
        dirs.append(os.path.join(roots.docs_real(), ".tmp"))
    except roots.DocsUnavailable:
        pass
    with db.get_conn() as conn:
        shared = conn.execute("SELECT * FROM roots WHERE kind = 'shared'").fetchall()
    for r in shared:
        try:
            dirs.append(os.path.join(roots.root_real(r), ".tmp"))
        except Exception:
            continue
    n = 0
    now = time.time()
    for d in dirs:
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for name in names:
            p = os.path.join(d, name)
            try:
                if os.path.isfile(p) and not os.path.islink(p) and now - os.path.getmtime(p) > max_age:
                    os.remove(p)
                    n += 1
            except OSError:
                pass
    return n


def scan_due(force: bool = False) -> bool:
    minutes = int(settings.get("scan_minutes"))
    if force or time.monotonic() - _state["last_scan"] >= minutes * 60:
        _state["last_scan"] = time.monotonic()
        index.scan_all()
        _safe(filing.run_queue)                   # files the scan found wait for their folder's rules (§17.18)
        return True
    return False


def hourly() -> None:
    if moving.is_read_only():
        return                                # paused while the documents are being moved (§5.7)
    if roots.is_ok():
        _safe(trash.purge_old)
        _safe(remove_stale_tmp)
        _safe(versions.prune_replaced)
        _safe(filing.run_cleanup)            # §17.19: each rule once a day
    _safe(index.purge_gone)
    _safe(storage_report.snapshot)
    with db.get_conn() as conn:
        _safe(activity.purge, conn)
        _safe(follows.purge, conn)
        _safe(ai_usage.prune, conn)
        _safe(app_messages.prune, conn)       # §17.15–§17.16: old requests
        _safe(filing.prune, conn)             # §17.18–§17.19: old filing records, rules of folders gone for good


def text_jobs() -> None:
    """PDF text for search (§17.5) and reading text in opted-in folders' scans with AI (§17.6) — in a worker
    thread, a few files a run."""
    if db.RESTORING.is_set():
        return
    _safe(pdftext.run_pending)
    _safe(ai.run_folder_jobs)


def tick(n: int) -> None:
    if db.RESTORING.is_set():
        return
    if n % 5 == 0:
        _safe(roots.check)
    if n % 5 == 2:
        _safe(ha_client.sync_people)          # Home Assistant's people (read by the ha_people loop)
    _safe(scan_due)
    _safe(filing.run_queue)                   # arrived files and their folder's rules (§17.18)
    _safe(follows.dispatch)                   # batched follow notifications (§17.4)
    _safe(app_messages.run_outbox)            # messages to Chat and Todo: re-sends, expiry, hello (§17.15–§17.16)
    if n % 60 == 30:
        hourly()
