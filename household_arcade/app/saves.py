"""Saved games (SPEC §12): a game put aside to finish later, at most one per person and game.

A save holds the game's rules state (data only, from the game's own `save()`), the level list it plays, and
its score, level and seconds so far. Continuing it starts a new play session that carries those seconds
(`base_seconds`), so the usual checks still hold: the time a result claims can't be more than the time
really played. When a continued game ends, its score is stored and the save is gone.

Saving another game of the same kind replaces the save; the replaced game's score is kept, as if it had
ended there. "End it" on the start screen does the same; "Throw away" drops it without a score.
"""
from __future__ import annotations

import json

from . import config, games, scores

MAX_STATE_BYTES = 48 * 1024


class SaveError(ValueError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def get(conn, user_id: str, game: str):
    return conn.execute("SELECT * FROM saved_games WHERE user_id = ? AND game = ?", (user_id, game)).fetchone()


def can_resume(row) -> bool:
    return row["state_version"] == games.GAMES.get(row["game"], {}).get("state_version")


def public(row) -> dict:
    return {"game": row["game"], "mode": row["mode"], "modeLabel": games.mode_label(row["game"], row["mode"]),
            "practice": bool(row["practice"]), "score": row["score"], "level": row["level"],
            "seconds": row["seconds"], "startedAt": row["started_at"], "savedAt": row["saved_at"],
            "canResume": can_resume(row)}


def mine(conn, user_id: str) -> dict:
    return {r["game"]: public(r) for r in conn.execute("SELECT * FROM saved_games WHERE user_id = ?", (user_id,))}


def finalize(conn, user_id: str, row) -> dict:
    """End a saved game where it is: its score is stored (unless it was Practice, under 3 seconds or not a
    possible result) and the save is removed. Returns what happened."""
    out = {"scoreSaved": False, "score": row["score"], "reason": None}
    if row["practice"]:
        out["reason"] = "practice"
    elif row["seconds"] < scores.MIN_SECONDS:
        out["reason"] = "short"
    elif games.keeps_nothing(row["game"], row["score"]):
        out["reason"] = "unfinished"
    else:
        problem = games.check_score(row["game"], row["mode"], row["score"], row["level"], row["seconds"],
                                    row["level_count"])
        if problem:
            out["reason"] = problem
        else:
            saved = scores.insert(conn, user_id, {"game": row["game"], "mode": row["mode"],
                                                 "started_at": row["started_at"], "id": row["session_id"]},
                                  row["score"], row["level"], row["seconds"])
            out.update(scoreSaved=True, personalBest=saved["personalBest"], householdRecord=saved["householdRecord"])
    conn.execute("DELETE FROM saved_games WHERE user_id = ? AND game = ?", (user_id, row["game"]))
    return out


def store(conn, user_id: str, session, body: dict, levels_json: str | None, wall_seconds: float) -> dict:
    """Save the game of an open session. Raises SaveError. Returns {saved, replaced}."""
    game = session["game"]
    version = games.GAMES[game].get("state_version")
    if not version:
        raise SaveError(422, f"{games.name(game)} games can't be saved.")
    if body.get("stateVersion") != version:
        raise SaveError(422, "This page is out of date: reload it and try again.")
    state = body.get("state")
    if not isinstance(state, dict):
        raise SaveError(422, "state must be an object.")
    blob = json.dumps(state, separators=(",", ":"))
    if len(blob) > MAX_STATE_BYTES:
        raise SaveError(413, "That game is too big to save.")
    score, level, seconds = body.get("score"), body.get("level", 1), body.get("seconds")
    problem = games.check_score(game, session["mode"], score, level, seconds, session["level_count"])
    if problem is None and seconds > wall_seconds + (session["base_seconds"] or 0):
        problem = "That game is longer than the time it has been played."
    if problem:
        raise SaveError(422, problem)
    replaced = None
    old = get(conn, user_id, game)
    own = old is not None and (old["session_id"] == session["id"] or
                               (session["resumed"] and old["saved_at"] <= session["started_at"]))
    if old is not None and own and levels_json is None:
        levels_json = old["levels"]
    if old is not None and not own:
        replaced = finalize(conn, user_id, old)
    started = session["first_started_at"] or session["started_at"]
    conn.execute(
        "INSERT INTO saved_games (user_id, game, mode, practice, state, state_version, levels, score, level, seconds, "
        "level_count, started_at, saved_at, session_id, app_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(user_id, game) DO UPDATE SET mode = excluded.mode, practice = excluded.practice, "
        "state = excluded.state, state_version = excluded.state_version, levels = excluded.levels, "
        "score = excluded.score, level = excluded.level, seconds = excluded.seconds, level_count = excluded.level_count, "
        "started_at = excluded.started_at, saved_at = excluded.saved_at, session_id = excluded.session_id, "
        "app_version = excluded.app_version",
        (user_id, game, session["mode"], session["practice"], blob, version, levels_json, int(score), int(level),
         int(round(seconds)), session["level_count"], started, config.now_iso(), session["id"], config.APP_VERSION))
    return {"saved": public(get(conn, user_id, game)), "replaced": replaced}


def consumed_by(conn, user_id: str, session) -> None:
    """A continued game ended with a score: its save is used up (unless a newer save has replaced it)."""
    if not session["resumed"]:
        return
    conn.execute("DELETE FROM saved_games WHERE user_id = ? AND game = ? AND (session_id = ? OR saved_at <= ?)",
                 (user_id, session["game"], session["id"], session["started_at"]))
