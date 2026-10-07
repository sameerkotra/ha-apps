"""Carrying on from the next level (SPEC §14).

Each person's highest level cleared, per game and mode, for the modes whose levels are puzzles or goals
(games.CONTINUE_LEVELS). The next game of that mode starts at the first level they haven't cleared (or any cleared
one they pick); once every level is cleared it starts at level 1 again. Progress only ever goes up; it is saved from
the heartbeats (the level went up: the one before it was cleared) and from the final result. Races, daily challenges
and the two-phone modes never touch it.
"""
from . import config, games, levels


def total(conn, game: str, mode: str) -> int:
    """How many levels the mode has: the game's fixed count, or its level list's length."""
    fixed = games.continue_modes(game).get(mode)
    if fixed:
        return int(fixed)
    set_id = levels.mode_info(game, mode)["set"]
    return levels.playable_count(conn, set_id) if set_id else 0


def cleared(conn, user_id: str, game: str, mode: str) -> int:
    row = conn.execute("SELECT cleared FROM level_progress WHERE user_id = ? AND game = ? AND mode = ?",
                       (user_id, game, mode)).fetchone()
    return int(row["cleared"]) if row else 0


def next_level(done: int, count: int) -> int:
    """Where the next game starts: after the last level cleared, or level 1 when every level is cleared."""
    if count <= 0:
        return 1
    done = min(done, count)
    return 1 if done >= count else done + 1


def record(conn, user_id: str, game: str, mode: str, done) -> int:
    """At least `done` levels cleared (never less than before, never more than the mode has). Returns the count."""
    if not games.continues(game, mode) or isinstance(done, bool) or not isinstance(done, int) or done <= 0:
        return cleared(conn, user_id, game, mode)
    count = total(conn, game, mode)
    if count:
        done = min(done, count)
    conn.execute(
        "INSERT INTO level_progress (user_id, game, mode, cleared, updated_at) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(user_id, game, mode) DO UPDATE SET cleared = MAX(cleared, excluded.cleared), "
        "updated_at = CASE WHEN excluded.cleared > cleared THEN excluded.updated_at ELSE updated_at END",
        (user_id, game, mode, done, config.now_iso()))
    return cleared(conn, user_id, game, mode)


def counts(session) -> bool:
    """Does this play session move the person's progress? Not in a race, a daily challenge or two phones."""
    return games.continues(session["game"], session["mode"]) and not session["match_id"] and not session["daily"]


def for_game(conn, user_id: str, game: str) -> dict:
    """{mode: {cleared, total, next}} for the game's modes that carry on."""
    out = {}
    for mode in games.continue_modes(game):
        count = total(conn, game, mode)
        done = min(cleared(conn, user_id, game, mode), count) if count else cleared(conn, user_id, game, mode)
        out[mode] = {"cleared": done, "total": count, "next": next_level(done, count)}
    return out


def mine(conn, user_id: str) -> list[dict]:
    """Every game and mode this person has cleared a level of (My scores → Level progress)."""
    out = []
    for r in conn.execute("SELECT game, mode, cleared, updated_at FROM level_progress WHERE user_id = ? AND cleared > 0 "
                          "ORDER BY updated_at DESC", (user_id,)):
        if not games.continues(r["game"], r["mode"]):
            continue
        count = total(conn, r["game"], r["mode"])
        done = min(r["cleared"], count) if count else r["cleared"]
        out.append({"game": r["game"], "name": games.name(r["game"]), "icon": games.GAMES[r["game"]]["icon"],
                    "mode": r["mode"], "modeLabel": games.mode_label(r["game"], r["mode"]), "cleared": done,
                    "total": count, "next": next_level(done, count), "updatedAt": r["updated_at"]})
    return out


def reset(conn, user_id: str, game: str, mode: str) -> bool:
    return conn.execute("DELETE FROM level_progress WHERE user_id = ? AND game = ? AND mode = ?",
                        (user_id, game, mode)).rowcount > 0
