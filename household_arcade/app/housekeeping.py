"""Housekeeping (every 10 minutes, and once at startup):

- close play sessions the browser never ended (a closed tab, a phone that
  went to sleep): a session without a heartbeat for 2 minutes ends, its time
  counted up to its last heartbeat and 2 minutes more, less the pauses it may
  leave out (however little the page had reported);
- Keep scores for (App settings): remove older games, keeping each person's
  best per game and mode (scores.prune);
- playing together: invites that ran out, matches left behind, turn-by-turn matches with no move for 7 days, and the
  your-move notifications that had to wait;
- whatever "Keep scores for" says, the app's own working records don't grow for ever: play sessions over 90 days
  old, the moves of matches that ended over 90 days ago, sent-notification marks and extra time over 30 / 90 days,
  and daily challenges over 90 days (their scores stay);
- download and import snapshots left behind (a download never finished) are removed after 6 hours.
"""
import logging
import tempfile
from datetime import timedelta

from . import ai_usage, auth, config, db, ha_sensors, scores, settings, together, turns
from .common import backup_core
from .common import housekeeping as jobs_core

logger = logging.getLogger("housekeeping")

STALE_SECONDS = 120
TICK_SECONDS = 600
KEEP_SESSIONS_DAYS = 90
KEEP_MOVES_DAYS = 90
KEEP_NOTIFY_DAYS = 30
KEEP_EXTRA_DAYS = 90
KEEP_DAILY_DAYS = 90
SNAPSHOT_MAX_AGE = 6 * 3600


def close_stale_sessions(conn) -> int:
    from .routers import play
    cutoff = (config.utcnow() - timedelta(seconds=STALE_SECONDS)).isoformat(timespec="seconds")
    rows = conn.execute("SELECT s.*, u.username, u.is_child AS child FROM play_sessions s JOIN users u ON u.id = s.user_id "
                        "WHERE s.ended_at IS NULL AND s.last_beat_at < ?", (cutoff,)).fetchall()
    for r in rows:
        user = {"id": r["user_id"], "is_child": bool(r["child"]) and not auth.is_admin_identity(r["user_id"], r["username"])}
        play.close_session(conn, r, user, "stale")
    return len(rows)


def prune_working_records(conn) -> dict:
    """The app's own records that would otherwise grow for ever (whatever "Keep scores for" says)."""
    now = config.utcnow()

    def ago(days: int) -> str:
        return (now - timedelta(days=days)).isoformat(timespec="seconds")

    out = {
        "sessions": conn.execute("DELETE FROM play_sessions WHERE ended_at IS NOT NULL AND started_at < ?",
                                 (ago(KEEP_SESSIONS_DAYS),)).rowcount,
        "moves": conn.execute("DELETE FROM match_moves WHERE match_id IN (SELECT id FROM matches WHERE status IN "
                              "('done', 'declined', 'expired', 'cancelled') AND COALESCE(ended_at, created_at) < ?)",
                              (ago(KEEP_MOVES_DAYS),)).rowcount,
        "notify_log": conn.execute("DELETE FROM notify_log WHERE created_at < ?", (ago(KEEP_NOTIFY_DAYS),)).rowcount,
        "extra_time": conn.execute("DELETE FROM extra_time WHERE created_at < ?", (ago(KEEP_EXTRA_DAYS),)).rowcount,
        "daily": conn.execute("DELETE FROM daily_challenges WHERE date < ?",
                              ((config.today() - timedelta(days=KEEP_DAILY_DAYS)).isoformat(),)).rowcount,
    }
    return out


def sweep_snapshots() -> int:
    """Download and import snapshots left behind (a download the browser never finished, an import cut off)."""
    n = 0
    for folder in (tempfile.gettempdir(), config.DATA_DIR):
        n += backup_core.remove_stale(folder, db.SNAPSHOT_PREFIX, SNAPSHOT_MAX_AGE)
    return n


def run_blocking() -> dict:
    with db.GATE.work(), db.get_conn() as conn:       # never while a backup is being restored
        closed = close_stale_sessions(conn)
        removed = scores.prune(conn)
        together.housekeeping(conn)
        years = settings.get("keep_scores_years")
        if years:
            together.prune(conn, (config.utcnow() - timedelta(days=365 * years)).isoformat(timespec="seconds"))
        pruned = prune_working_records(conn)
        ai_usage.prune(conn)
    sweep_snapshots()
    turns.send_due_blocking()        # your-move notifications that waited (quiet hours, at most one every 15 minutes)
    if any(pruned.values()):
        logger.info("Old working records removed: %s", ", ".join(f"{k} {v}" for k, v in pruned.items() if v))
    if removed:
        logger.info("Keep scores for: removed %d old game(s)", removed)
    if closed:
        ha_sensors.changed_blocking()
    return {"closed": closed, "removed": removed}


# Every TICK_SECONDS from main.py's lifespan, once straight away (tests may replace `loop`).
loop = jobs_core.periodic(TICK_SECONDS, run_blocking, log=logger, error="Housekeeping failed")
