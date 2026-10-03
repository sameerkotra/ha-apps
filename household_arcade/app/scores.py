"""Scores, personal bests and the household leaderboard (SPEC §5).

- Every finished game that isn't Practice and lasted at least 3 seconds is
  stored, with the app version that produced it.
- "Personal best" is per person, game and mode; the "household record" is the
  best score of everyone who isn't switched off, per game and mode.
- The leaderboard shows the top 10 of all time or of this month (Home
  Assistant's time zone), each person's best only once; people switched off
  on Admin → Users don't appear.
- Keep scores for (App settings): older games are removed, but each person's
  best score per game and mode is always kept, so personal bests and records
  survive.
"""
from datetime import date, datetime, timedelta, timezone

from . import config, games, settings

MIN_SECONDS = 3
TOP_N = 10
RECENT_N = 20


def first_name(name: str) -> str:
    return (name or "").strip().split(" ")[0] or name


def best(conn, game: str, mode: str, user_id: str | None = None) -> int | None:
    sql = ("SELECT MAX(s.score) AS m FROM scores s JOIN users u ON u.id = s.user_id "
           "WHERE s.game = ? AND s.mode = ? AND u.disabled = 0")
    args: list = [game, mode]
    if user_id:
        sql += " AND s.user_id = ?"
        args.append(user_id)
    row = conn.execute(sql, args).fetchone()
    return row["m"]


def insert(conn, user_id: str, session: dict, score: int, level: int, seconds: int) -> dict:
    """Store a validated result. Returns {id, personalBest, householdRecord, previousRecord}."""
    prev_mine = best(conn, session["game"], session["mode"], user_id)
    prev_house = best(conn, session["game"], session["mode"])
    cur = conn.execute(
        "INSERT INTO scores (user_id, game, mode, score, level, seconds, started_at, ended_at, app_version, session_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (user_id, session["game"], session["mode"], score, level, seconds, session["started_at"],
         config.now_iso(), config.APP_VERSION, session["id"]))
    return {
        "id": cur.lastrowid,
        "personalBest": score > 0 and (prev_mine is None or score > prev_mine),
        "householdRecord": score > 0 and (prev_house is None or score > prev_house),
        "previousRecord": prev_house,
    }


def _month_start_utc() -> str:
    today = config.today()
    start, _ = config.local_day_bounds_utc(date(today.year, today.month, 1))
    return start


def _week_start_utc() -> str:
    today = config.today()
    start, _ = config.local_day_bounds_utc(today - timedelta(days=today.isoweekday() - 1))
    return start


def leaderboard(conn, game: str, mode: str, period: str, viewer: dict, names: str = "full") -> list[dict]:
    sql = ("SELECT s.id, s.user_id, s.score, s.level, s.seconds, s.ended_at, u.name FROM scores s "
           "JOIN users u ON u.id = s.user_id WHERE s.game = ? AND s.mode = ? AND u.disabled = 0")
    args: list = [game, mode]
    if period == "month":
        sql += " AND s.ended_at >= ?"
        args.append(_month_start_utc())
    sql += " ORDER BY s.score DESC, s.ended_at ASC, s.id ASC"
    out, seen = [], set()
    for r in conn.execute(sql, args):
        if r["user_id"] in seen:
            continue
        seen.add(r["user_id"])
        out.append({
            "rank": len(out) + 1, "scoreId": r["id"], "score": r["score"], "level": r["level"],
            "seconds": r["seconds"], "at": r["ended_at"],
            "name": first_name(r["name"]) if names == "first" else r["name"],
            "me": r["user_id"] == viewer["id"],
        })
        if len(out) >= TOP_N:
            break
    return out


def _row(r) -> dict:
    return {"id": r["id"], "game": r["game"], "mode": r["mode"], "modeLabel": games.mode_label(r["game"], r["mode"]),
            "score": r["score"], "level": r["level"], "seconds": r["seconds"], "at": r["ended_at"]}


def mine(conn, user_id: str) -> dict:
    bests = []
    for r in conn.execute(
            "SELECT game, mode, MAX(score) AS score, COUNT(*) AS games FROM scores WHERE user_id = ? "
            "GROUP BY game, mode ORDER BY game, mode", (user_id,)):
        if not games.exists(r["game"]):
            continue
        bests.append({"game": r["game"], "mode": r["mode"], "modeLabel": games.mode_label(r["game"], r["mode"]),
                      "score": r["score"], "games": r["games"]})
    recent = [_row(r) for r in conn.execute(
        "SELECT * FROM scores WHERE user_id = ? ORDER BY ended_at DESC, id DESC LIMIT ?", (user_id, RECENT_N))]
    total = conn.execute("SELECT COUNT(*) AS n FROM scores WHERE user_id = ?", (user_id,)).fetchone()["n"]
    week = conn.execute("SELECT COALESCE(SUM(active_seconds), 0) AS s FROM play_sessions "
                        "WHERE user_id = ? AND started_at >= ?", (user_id, _week_start_utc())).fetchone()["s"]
    return {"bests": bests, "recent": recent, "totals": {"games": total, "secondsThisWeek": round(week or 0)}}


def bests_for(conn, user_id: str) -> dict:
    """{game: best score in any mode} — for Home."""
    return {r["game"]: r["m"] for r in conn.execute(
        "SELECT game, MAX(score) AS m FROM scores WHERE user_id = ? GROUP BY game", (user_id,))}


def records(conn) -> dict:
    """{game: {"score", "mode", "name", "at", "modes": {mode: {...}}}} for the record sensors."""
    out: dict = {}
    for game in games.GAME_IDS:
        per_mode = {}
        for mode in games.mode_ids(game):
            r = conn.execute(
                "SELECT s.score, s.ended_at, u.name FROM scores s JOIN users u ON u.id = s.user_id "
                "WHERE s.game = ? AND s.mode = ? AND u.disabled = 0 ORDER BY s.score DESC, s.ended_at ASC LIMIT 1",
                (game, mode)).fetchone()
            if r:
                per_mode[mode] = {"score": r["score"], "name": r["name"], "at": r["ended_at"]}
        top = max(per_mode.items(), key=lambda kv: kv[1]["score"], default=None)
        out[game] = {"score": top[1]["score"] if top else None, "mode": top[0] if top else None,
                     "name": top[1]["name"] if top else None, "at": top[1]["at"] if top else None,
                     "modes": per_mode}
    return out


def prune(conn, now: datetime | None = None) -> int:
    """Keep scores for N years (App settings): remove older games except each
    person's best per game and mode. Returns how many were removed."""
    years = settings.get("keep_scores_years")
    if not years:
        return 0
    now = now or config.utcnow()
    cutoff = (now.astimezone(timezone.utc) - timedelta(days=365 * years)).isoformat(timespec="seconds")
    n = conn.execute(
        "DELETE FROM scores WHERE ended_at < ? AND id NOT IN ("
        "  SELECT (SELECT s2.id FROM scores s2 WHERE s2.user_id = s1.user_id AND s2.game = s1.game "
        "          AND s2.mode = s1.mode ORDER BY s2.score DESC, s2.ended_at ASC, s2.id ASC LIMIT 1) "
        "  FROM scores s1 GROUP BY s1.user_id, s1.game, s1.mode)", (cutoff,)).rowcount
    conn.execute("DELETE FROM play_sessions WHERE ended_at IS NOT NULL AND started_at < ?", (cutoff,))
    return n
