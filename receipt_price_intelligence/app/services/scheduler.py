"""Runs the best-price check once a day.

A single asyncio task started with the app: at start-up it checks any home whose snapshot
is missing or older than a day (so an app that was off overnight catches up), then it
sleeps until the configured hour each day. The work itself runs in a thread so the web app
stays responsive. Times are the server's local time (Home Assistant's time zone), worked out on the wall clock so
a daylight-saving change doesn't move the hour. The hour is an App setting, read again every few minutes.

Every job runs inside the maintenance gate (services/maintenance.py), so a backup restore never swaps the database
under one. At start-up, receipts that were being read when the app stopped are read again.
"""

import asyncio
import functools
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from app.config import get_settings
from app.logging_config import get_logger
from app.db import get_db_session
from app.services import backup, deals, importer, lookout, maintenance, webinfo, shoplist, shoplist_nearby
from app.services import drafts as drafts_service

logger = get_logger("scheduler")


def next_run(hour: int, now: datetime | None = None) -> datetime:
    """The next time it is ``hour``:00 local time (today if still ahead, else tomorrow).

    Worked out on the wall clock and only then given the zone's offset for that day: adding a day to a time that
    carries today's offset would land an hour off after a daylight-saving change."""
    now = now or datetime.now().astimezone()
    wall = now.replace(tzinfo=None)
    candidate = wall.replace(hour=hour, minute=0, second=0, microsecond=0)
    if candidate <= wall:
        candidate += timedelta(days=1)
    if now.tzinfo is None:
        return candidate
    zone = getattr(now.tzinfo, "key", None) and now.tzinfo     # a ZoneInfo knows the rules of every day
    return candidate.replace(tzinfo=zone) if zone else candidate.astimezone()


def cleanup_drafts() -> int:
    """Delete unsaved receipts (and their photos) older than the retention period, discarded scans, and left-over
    temporary files."""
    with get_db_session()() as db:
        removed = drafts_service.cleanup_expired_drafts(db)
        photos = drafts_service.purge_finished_draft_images(db)
        drafts_service.purge_rejected_drafts(db)
    if removed:
        logger.info("Removed %d expired unsaved receipt(s)", removed)
    if photos:
        logger.info("Deleted %d leftover photo(s) from saved or discarded receipts", photos)
    try:
        backup.sweep_temp_files()
    except Exception:  # noqa: BLE001 - only tidying
        logger.exception("Could not tidy left-over temporary files")
    return removed


def purge_web_rows() -> int:
    """Ended deals and old online prices (the pages leave them out; they are deleted here, once a day)."""
    with get_db_session()() as db:
        return webinfo.purge_old_deals(db)


def interrupted_drafts() -> tuple[list[str], int]:
    """Receipts left "Reading…" by a stop or crash: the ids of those with photos (to read again), and how many
    without photos were marked as failed ("read again")."""
    from app.api.extraction import get_image_paths_for_draft
    from app.db.models import ReceiptDraft
    from app.services.extraction import get_extraction_service
    again, failed = [], 0
    with get_db_session()() as db:
        for draft in db.query(ReceiptDraft).filter(ReceiptDraft.status == "PROCESSING").all():
            if get_image_paths_for_draft(db, draft.id):
                again.append(draft.id)
            elif draft.created_at and datetime.utcnow() - draft.created_at > timedelta(minutes=10):
                # no photo at all, long ago: the scan was abandoned half-way
                get_extraction_service().mark_failed(db, draft, "Reading was interrupted (the app restarted). Read it again.")
                failed += 1
    return again, failed


# The scheduled jobs (some run for many minutes) have threads of their own, so they never take the threads that
# reading receipts and the event loop's other work use
_JOBS = ThreadPoolExecutor(max_workers=3, thread_name_prefix="rpi-scheduled")


async def _thread(fn, *args):
    return await asyncio.get_running_loop().run_in_executor(_JOBS, functools.partial(fn, *args))


async def _in_gate(fn, *args):
    """``fn(*args)`` on the scheduled jobs' threads, inside the maintenance gate (it waits while a backup is restored)."""
    return await _thread(maintenance.run, fn, *args)


async def recover_interrupted() -> None:
    """At start-up: read again the receipts that were being read when the app stopped."""
    again, failed = await _in_gate(interrupted_drafts)
    if failed:
        logger.info("Marked %d interrupted receipt(s) without photos as failed", failed)
    if again:
        logger.info("Reading %d receipt(s) again that were interrupted by a restart", len(again))
        await importer.start_reading(again)


def _run_hour() -> int:
    return get_settings().recommendation_run_hour


async def _run() -> None:
    try:
        await recover_interrupted()
    except Exception:  # noqa: BLE001
        logger.exception("Could not read interrupted receipts again")
    try:
        await _in_gate(cleanup_drafts)
    except Exception:  # noqa: BLE001
        logger.exception("Start-up clean-up failed")
    try:
        await _in_gate(deals.refresh_all, True)
    except Exception:  # noqa: BLE001
        logger.exception("Start-up best-price check failed")

    hour = _run_hour()
    target = next_run(hour)
    logger.info("Next best-price check at %s", target.isoformat(timespec="minutes"))
    while True:
        # Wake at least every few minutes so a changed "Daily check hour" (App settings) applies at once.
        now = datetime.now().astimezone()
        if _run_hour() != hour:
            hour = _run_hour()
            target = next_run(hour, now)
            logger.info("Daily check hour changed; next best-price check at %s", target.isoformat(timespec="minutes"))
        if now < target:
            await asyncio.sleep(min(300.0, max(1.0, (target - now).total_seconds())))
            continue
        try:
            await _in_gate(cleanup_drafts)
            await _in_gate(purge_web_rows)
            await _in_gate(deals.refresh_all, False)
            await _thread(webinfo.run_scheduled)  # deals and store hours from the web (its jobs check the gate)
            await _in_gate(lookout.run_daily)  # what is due to restock
        except Exception:  # noqa: BLE001 - keep the loop alive
            logger.exception("Daily best-price check failed")
        target = next_run(hour)
        logger.info("Next best-price check at %s", target.isoformat(timespec="minutes"))


async def _import_loop() -> None:
    """Check the watched folder every minute and the mailbox every five."""
    tick = 0
    while True:
        await asyncio.sleep(60)
        tick += 1
        try:
            # shopping list: "cheapest here" every minute, the Home Assistant to-do list and sensor every other minute
            await _in_gate(shoplist_nearby.check_all)
            if tick % 2 == 0:
                await _in_gate(shoplist.sync_all)
            if tick % 60 == 30:
                await _in_gate(lookout.run_hourly)  # list items now cheaper than usual
        except Exception:  # noqa: BLE001 - keep the loop alive
            logger.exception("Shopping list check failed")
        await _import_once(tick)


async def _import_once(tick: int) -> None:
    """One import check. Each source has its own try, and the drafts made are always queued for reading: a
    failing mailbox must not leave the folder's new drafts waiting forever."""
    created: list[str] = []
    try:
        created += await _in_gate(importer.scan_folder)
    except Exception:  # noqa: BLE001 - keep the loop alive
        logger.exception("Receipt import from the folder failed")
    if tick % 5 == 0:
        try:
            created += await _in_gate(importer.poll_mailbox)
        except Exception:  # noqa: BLE001 - keep the loop alive
            logger.exception("Receipt import from the mailbox failed")
    if created:
        try:
            await importer.start_reading(created)
        except Exception:  # noqa: BLE001 - keep the loop alive
            logger.exception("Could not start reading the imported receipts")


_tasks: list["asyncio.Task[None]"] = []  # kept so the tasks are not garbage collected


def start() -> None:
    """Start the daily check and the import loop."""
    _tasks.append(asyncio.create_task(_run(), name="daily-best-prices"))
    _tasks.append(asyncio.create_task(_import_loop(), name="receipt-import"))


def stop() -> None:
    for task in _tasks:
        task.cancel()
    _tasks.clear()
