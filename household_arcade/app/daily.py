"""The daily challenge (SPEC "Daily challenge"): the same seeded games for everyone each day.

Off unless an admin turns on App settings → Show daily challenges. While it is off nothing about daily
challenges is offered or accepted: the pages are hidden and every route here answers 404 "Daily challenges are
turned off". Turning it off keeps the scores already saved; turning it back on shows them again.

When on, the first request of a day (Home Assistant's time zone) picks `PER_DAY` games from `POOL` — games an
admin has switched off are never picked — and stores them in `daily_challenges` with a seed each, so the day's
picks don't change if a setting does. Each person gets one *ranked* try per challenge (a Practice replay isn't
saved; any ranked session for it is the try, finished or not). A daily score is an ordinary `scores` row whose
mode is `daily-<date>`, so it never mixes with the game's usual leaderboard; its personal best and records are
left out too.
"""
from __future__ import annotations

import hashlib
import random
import re
from datetime import date

from . import config, games, limits, scores, settings

# The games that suit a one-try challenge, with the mode each day uses.
POOL: tuple[tuple[str, str], ...] = (
    ("snake", "walls-normal"), ("blocks", "classic"), ("mines", "medium"), ("merge", "classic"),
    ("sudoku", "medium"), ("wordguess", "classic"),
)
PER_DAY = 3
OFF_MESSAGE = "Daily challenges are turned off."
_PREFIX = "daily-"
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class DailyError(ValueError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def enabled() -> bool:
    return bool(settings.get("show_daily_challenges"))


def require_enabled() -> None:
    if not enabled():
        raise DailyError(404, OFF_MESSAGE)


def mode_key(day: str) -> str:
    """The `mode` a daily score is stored with."""
    return _PREFIX + day


def is_daily_mode(mode: str) -> bool:
    return isinstance(mode, str) and mode.startswith(_PREFIX)


def day_of(mode: str) -> str | None:
    return mode[len(_PREFIX):] if is_daily_mode(mode) and _DATE_RE.match(mode[len(_PREFIX):]) else None


def seed_for(day: str, game: str) -> int:
    return int(hashlib.sha256(f"daily:{day}:{game}".encode()).hexdigest()[:8], 16) % 2_147_483_647


def pick(day: str, candidates: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """The day's games from the candidates, the same for everyone (a seeded choice, not the clock)."""
    rng = random.Random(int(hashlib.sha256(f"daily-pick:{day}".encode()).hexdigest()[:12], 16))
    k = min(PER_DAY, len(candidates))
    chosen = rng.sample(candidates, k)
    return [c for c in candidates if c in chosen]            # in the pool's order


def ensure_day(conn, day: str | None = None) -> list[dict]:
    """Today's challenges (made on first use): [{date, game, mode, seed}], in the pool's order."""
    day = day or config.today().isoformat()
    rows = conn.execute("SELECT date, game, mode, seed FROM daily_challenges WHERE date = ?", (day,)).fetchall()
    if not rows:
        candidates = [(g, m) for g, m in POOL if games.exists(g) and settings.game_enabled(g)]
        for g, m in pick(day, candidates):
            conn.execute("INSERT OR IGNORE INTO daily_challenges (date, game, mode, seed) VALUES (?, ?, ?, ?)",
                         (day, g, m, seed_for(day, g)))
        rows = conn.execute("SELECT date, game, mode, seed FROM daily_challenges WHERE date = ?", (day,)).fetchall()
    order = {g: i for i, (g, _m) in enumerate(POOL)}
    return sorted((dict(r) for r in rows), key=lambda r: order.get(r["game"], 99))


def visible(conn, current: dict, day: str | None = None) -> list[dict]:
    """Today's challenges this person can play: the game is on, and (for a child) allowed."""
    rows = [r for r in ensure_day(conn, day) if settings.game_enabled(r["game"]) and settings.mode_allowed(r["game"], r["mode"])]
    if current.get("is_child"):
        lim = limits.load_limits(conn, current["id"])
        rows = [r for r in rows if limits.game_allowed(lim, r["game"])]
    return rows


def tried(conn, user_id: str, game: str, day: str) -> bool:
    """Has this person already used their ranked try? (Any ranked session for the challenge counts.)"""
    return conn.execute("SELECT 1 FROM play_sessions WHERE user_id = ? AND game = ? AND daily = ? AND practice = 0 LIMIT 1",
                        (user_id, game, day)).fetchone() is not None


def my_score(conn, user_id: str, game: str, day: str) -> int | None:
    r = conn.execute("SELECT MAX(score) AS s FROM scores WHERE user_id = ? AND game = ? AND mode = ?",
                     (user_id, game, mode_key(day))).fetchone()
    return r["s"]


def challenge_for(conn, game: str, current: dict, practice: bool) -> dict:
    """The challenge a person starts now: today's for `game`. Raises DailyError (404 off / not today's game,
    409 the ranked try is used)."""
    require_enabled()
    day = config.today().isoformat()
    for r in visible(conn, current, day):
        if r["game"] == game:
            if not practice and tried(conn, current["id"], game, day):
                raise DailyError(409, f"You've already had your try at today's {games.name(game)} challenge. "
                                      "You can still play it as Practice.")
            return r
    raise DailyError(404, f"{games.name(game)} isn't one of today's challenges.")


def card(conn, current: dict) -> dict:
    """Home's "Today's challenges": the day, each challenge with whether this person has played it and their
    score, and how many days this month they have played."""
    require_enabled()
    day = config.today().isoformat()
    out = []
    for r in visible(conn, current, day):
        g = games.GAMES[r["game"]]
        played = tried(conn, current["id"], r["game"], day)
        out.append({"game": r["game"], "name": g["name"], "icon": g["icon"], "mode": r["mode"],
                    "modeLabel": games.mode_label(r["game"], r["mode"]), "seed": r["seed"],
                    "played": played, "score": my_score(conn, current["id"], r["game"], day)})
    return {"date": day, "challenges": out, "daysPlayed": days_played(conn, current["id"])}


def for_games(conn, current: dict) -> dict[str, dict]:
    """{game: {date, mode, played, score}} for the start screens; empty while daily challenges are off."""
    if not enabled():
        return {}
    day = config.today().isoformat()
    return {r["game"]: {"date": day, "mode": r["mode"], "modeLabel": games.mode_label(r["game"], r["mode"]),
                        "played": tried(conn, current["id"], r["game"], day),
                        "score": my_score(conn, current["id"], r["game"], day)}
            for r in visible(conn, current, day)}


def _month_start() -> str:
    t = config.today()
    return date(t.year, t.month, 1).isoformat()


def days_played(conn, user_id: str) -> int:
    """Days this month on which the person has a saved daily score (any game)."""
    start = _month_start()
    row = conn.execute("SELECT COUNT(DISTINCT mode) AS n FROM scores WHERE user_id = ? AND mode LIKE 'daily-%' "
                       "AND substr(mode, 7) >= ?", (user_id, start)).fetchone()
    return row["n"]


def board(conn, game: str, day: str, viewer: dict, names: str = "full") -> list[dict]:
    """The day's top 10 for a challenge (best score per person once; people switched off left out)."""
    return scores.leaderboard(conn, game, mode_key(day), "all", viewer, names)


def days_board(conn, viewer: dict, names: str = "full", limit: int = 10) -> list[dict]:
    """This month's "days played" ranking."""
    start = _month_start()
    rows = conn.execute(
        "SELECT s.user_id, u.name, COUNT(DISTINCT s.mode) AS n FROM scores s JOIN users u ON u.id = s.user_id "
        "WHERE s.mode LIKE 'daily-%' AND substr(s.mode, 7) >= ? AND u.disabled = 0 GROUP BY s.user_id "
        "ORDER BY n DESC, u.name COLLATE NOCASE LIMIT ?", (start, limit)).fetchall()
    return [{"rank": i + 1, "name": scores.first_name(r["name"]) if names == "first" else r["name"], "days": r["n"],
             "me": r["user_id"] == viewer["id"]} for i, r in enumerate(rows)]
