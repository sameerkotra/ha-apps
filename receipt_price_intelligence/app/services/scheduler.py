"""Runs the best-price check once a day.

A single asyncio task started with the app: at start-up it checks any home whose snapshot
is missing or older than a day (so an app that was off overnight catches up), then it
sleeps until the configured hour each day. The work itself runs in a thread so the web app
stays responsive. Times are the server's local time (Home Assistant's time zone). The hour is an App
setting, read again every few minutes.
"""

import asyncio
from datetime import datetime, timedelta

from app.config import get_settings
from app.logging_config import get_logger
from app.db import get_db_session
from app.services import deals, importer, lookout, webinfo, shoplist, shoplist_nearby
from app.services import drafts as drafts_service

logger = get_logger("scheduler")


def next_run(hour: int, now: datetime | None = None) -> datetime:
    """The next time it is ``hour``:00 local time (today if still ahead, else tomorrow)."""
    now = now or datetime.now().astimezone()
    candidate = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    return candidate if candidate > now else candidate + timedelta(days=1)


def cleanup_drafts() -> int:
    """Delete unsaved receipts (and their photos) older than the retention period."""
    with get_db_session()() as db:
        removed = drafts_service.cleanup_expired_drafts(db)
        photos = drafts_service.purge_finished_draft_images(db)
    if removed:
        logger.info("Removed %d expired unsaved receipt(s)", removed)
    if photos:
        logger.info("Deleted %d leftover photo(s) from saved or discarded receipts", photos)
    return removed


def _run_hour() -> int:
    return get_settings().recommendation_run_hour


async def _run() -> None:
    try:
        await asyncio.to_thread(cleanup_drafts)
    except Exception:  # noqa: BLE001
        logger.exception("Start-up clean-up failed")
    try:
        await asyncio.to_thread(deals.refresh_all, True)
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
            await asyncio.to_thread(cleanup_drafts)
            await asyncio.to_thread(deals.refresh_all, False)
            await asyncio.to_thread(webinfo.run_scheduled)  # deals and store hours from the web, when a search server is set up
            await asyncio.to_thread(lookout.run_daily)  # what is due to restock
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
            await asyncio.to_thread(shoplist_nearby.check_all)
            if tick % 2 == 0:
                await asyncio.to_thread(shoplist.sync_all)
            if tick % 60 == 30:
                await asyncio.to_thread(lookout.run_hourly)  # list items now cheaper than usual
        except Exception:  # noqa: BLE001 - keep the loop alive
            logger.exception("Shopping list check failed")
        try:
            created = await asyncio.to_thread(importer.scan_folder)
            if tick % 5 == 0:
                created += await asyncio.to_thread(importer.poll_mailbox)
            if created:
                await importer.start_reading(created)
        except Exception:  # noqa: BLE001 - keep the loop alive
            logger.exception("Receipt import check failed")


_tasks: list["asyncio.Task[None]"] = []  # kept so the tasks are not garbage collected


def start() -> None:
    """Start the daily check and the import loop."""
    _tasks.append(asyncio.create_task(_run(), name="daily-best-prices"))
    _tasks.append(asyncio.create_task(_import_loop(), name="receipt-import"))


def stop() -> None:
    for task in _tasks:
        task.cancel()
    _tasks.clear()
