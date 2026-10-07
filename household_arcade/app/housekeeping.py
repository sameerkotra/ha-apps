"""Housekeeping (every 10 minutes, and once at startup):

- close play sessions the browser never ended (a closed tab, a phone that
  went to sleep): a session without a heartbeat for 2 minutes ends at its last
  heartbeat, keeping the active seconds it had reported;
- Keep scores for (App settings): remove older games, keeping each person's
  best per game and mode (scores.prune);
- playing together: invites that ran out, matches left behind, turn-by-turn matches with no move for 7 days, and the
  your-move notifications that had to wait.
"""
import logging
from datetime import timedelta

from . import ai_usage, config, db, ha_sensors, scores, settings, together, turns
from .common import housekeeping as jobs_core

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
        together.housekeeping(conn)
        years = settings.get("keep_scores_years")
        if years:
            together.prune(conn, (config.utcnow() - timedelta(days=365 * years)).isoformat(timespec="seconds"))
        ai_usage.prune(conn)
    turns.send_due_blocking()        # your-move notifications that waited (quiet hours, at most one every 15 minutes)
    if removed:
        logger.info("Keep scores for: removed %d old game(s)", removed)
    if closed:
        ha_sensors.changed_blocking()
    return {"closed": closed, "removed": removed}


# Every TICK_SECONDS from main.py's lifespan, once straight away (tests may replace `loop`).
loop = jobs_core.periodic(TICK_SECONDS, run_blocking, log=logger, error="Housekeeping failed")
