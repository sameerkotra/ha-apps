"""The app's own background work besides the watcher (spec/SPEC.md §11): discovery every few minutes (sooner when
Home Assistant's registries change), the quick round (Check-now results, held forwards, re-sent forwards, alerts
after quiet hours) and the hourly clean-up (history kept N days, the online line its own days, forwarding chats' members asked again)."""
import asyncio
import logging
import tempfile

from starlette.concurrency import run_in_threadpool

from . import alerts, app_messages, checknow, config, db, forwarding, phones, settings, timeline
from .common import backup_core

logger = logging.getLogger("housekeeping")

DISCOVERY_EVERY = 300


def prune(now: float | None = None) -> dict:
    """History, Check now, forwards and finished requests older than the History setting; the online line's status
    changes older than its own setting (the last one before the cut stays)."""
    now = config.clock() if now is None else now
    cutoff = now - settings.get("history_days") * 86400
    with db.get_conn() as conn:
        out = {t: conn.execute(f"DELETE FROM {t} WHERE {col} < ?", (cutoff,)).rowcount
               for t, col in (("history", "at"), ("checks", "started"), ("forwards", "at"), ("actions", "at"))}
        out["requests"] = conn.execute("DELETE FROM bus_requests WHERE created < ?", (now - 86400,)).rowcount
        out["status_log"] = timeline.prune(conn, now, settings.get("online_history_days"))
    return out


def hourly() -> None:
    prune()
    backup_core.remove_stale(tempfile.gettempdir(), db.SNAPSHOT_PREFIX, 6 * 3600)
    try:
        app_messages.refresh_members()
    except Exception:
        logger.exception("Asking Household Chat for the chats' members failed")


def quick(n: int = 0) -> None:
    checknow.finish_due()
    forwarding.release_held()
    app_messages.resend_forwards()
    if n % 12 == 0:
        alerts.release_queue()


async def discovery_loop() -> None:
    while True:
        try:
            await run_in_threadpool(phones.refresh)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Reading the phones from Home Assistant failed")
        app_messages.REFRESH.clear()
        for _ in range(DISCOVERY_EVERY):
            if app_messages.REFRESH.is_set():
                await asyncio.sleep(5)              # registry events come in bursts
                break
            await asyncio.sleep(1)
