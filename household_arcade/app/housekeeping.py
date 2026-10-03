"""Housekeeping (every 10 minutes, and once at startup):

- close play sessions the browser never ended (a closed tab, a phone that
  went to sleep): a session without a heartbeat for 2 minutes ends at its last
  heartbeat, keeping the active seconds it had reported;
- Keep scores for (App settings): remove older games, keeping each person's
  best per game and mode (scores.prune).
"""
import asyncio
import logging
from datetime import timedelta

from starlette.concurrency import run_in_threadpool

from . import ai_usage, config, db, ha_sensors, scores

logger = logging.getLogger("housekeeping")

STALE_SECONDS = 120
TICK_SECONDS = 600


def close_stale_sessions(conn) -> int:
    cutoff = (config.utcnow() - timedelta(seconds=STALE_SECONDS)).isoformat(timespec="seconds")
    return conn.execute("UPDATE play_sessions SET ended_at = last_beat_at "
                        "WHERE ended_at IS NULL AND last_beat_at < ?", (cutoff,)).rowcount


def run_blocking() -> dict:
    with db.get_conn() as conn:
        closed = close_stale_sessions(conn)
        removed = scores.prune(conn)
        ai_usage.prune(conn)
    if removed:
        logger.info("Keep scores for: removed %d old game(s)", removed)
    if closed:
        ha_sensors.changed_blocking()
    return {"closed": closed, "removed": removed}


async def loop() -> None:
    while True:
        try:
            await run_in_threadpool(run_blocking)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Housekeeping failed")
        await asyncio.sleep(TICK_SECONDS)
