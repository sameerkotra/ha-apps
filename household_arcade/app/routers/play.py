"""Games, play sessions, scores and the leaderboard (SPEC §5, §6, §10.1).

A game on screen is a *play session*:

    POST /api/sessions              start (checks: switched on, game on, mode, child's limits)
    POST /api/sessions/{id}/beat    every ~15 s while it runs, and on pause/resume:
                                    the browser's count of active (unpaused) seconds
    POST /api/sessions/{id}/end     a game left without a result (quit, page closed)
    POST /api/scores                the result of a finished game (also ends the session)

Active seconds only ever grow and are capped by the wall time since the
session started, so a child's play time can't be talked down and a score
can't claim more time than really passed. Sessions the browser never ended are
closed by housekeeping.
"""
from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Query

import json

from .. import config, daily, db, games, ha_sensors, level_builder, levels, limits, notify, saves, scores, settings, together
from ..auth import get_current_user

router = APIRouter(prefix="/api", tags=["play"])

WALL_SLACK_SECONDS = 2


def _require_enabled(current: dict) -> None:
    if current["disabled"]:
        raise HTTPException(403, "An admin has switched you off in Household Arcade, so you can't play just now.")


@router.get("/games")
def list_games(current: dict = Depends(get_current_user)):
    """The games that are on (and, for a child, allowed), with their modes."""
    with db.get_conn() as conn:
        mine = scores.bests_for(conn, current["id"])
        child_limits = limits.load_limits(conn, current["id"]) if current["is_child"] else None
        bests_by_mode = {(r["game"], r["mode"]): r["m"] for r in conn.execute(
            "SELECT game, mode, MAX(score) AS m FROM scores WHERE user_id = ? GROUP BY game, mode", (current["id"],))}
        saved = saves.mine(conn, current["id"])
        played = {r["game"]: (r["n"], r["last"]) for r in conn.execute(
            "SELECT game, COUNT(*) AS n, MAX(ended_at) AS last FROM scores WHERE user_id = ? GROUP BY game", (current["id"],))}
        level_counts = {s: levels.playable_count(conn, s) for s in levels.SETS}
        todays = daily.for_games(conn, current)           # {} while daily challenges are off
    out = []
    for gid in settings.enabled_games():
        if child_limits and not limits.game_allowed(child_limits, gid):
            continue
        g = games.GAMES[gid]
        modes = settings.modes_for(gid)
        default = g["default_mode"] if any(m["id"] == g["default_mode"] for m in modes) else modes[0]["id"]
        out.append({"id": gid, "name": g["name"], "icon": g["icon"], "modes": modes, "defaultMode": default,
                    "best": mine.get(gid),
                    "bestByMode": {m["id"]: bests_by_mode.get((gid, m["id"])) for m in modes},
                    "canSave": bool(g.get("state_version")), "saved": saved.get(gid), "race": together.race_ok(gid), "daily": todays.get(gid),
                    # live duels on two phones (SPEC §13.4): these modes are played only with someone, each on a phone
                    "liveModes": [m["id"] for m in modes if games.is_live(gid, m["id"])],
                    # turn by turn from two phones (SPEC §13.5): these modes are played only with someone
                    "turnModes": [m["id"] for m in modes if games.is_turns(gid, m["id"])],
                    "plays": played.get(gid, (0, None))[0], "lastPlayed": played.get(gid, (0, None))[1],
                    "levels": level_counts.get(gid, 0),
                    "levelModes": [m["label"] for m in modes if m["id"] in g.get("level_modes", [])]})
    return {"games": out}


def _session(conn, session_id: str, current: dict):
    row = conn.execute("SELECT * FROM play_sessions WHERE id = ?", (session_id,)).fetchone()
    if not row or row["user_id"] != current["id"]:
        raise HTTPException(404, "That game isn't known.")
    return row


def _wall(row) -> float:
    """Seconds since the session started, with a little slack."""
    started = config.parse_ts(row["started_at"])
    return max(0.0, (config.utcnow() - started).total_seconds()) + WALL_SLACK_SECONDS


def _active(row, reported) -> float:
    """New active seconds: never less than before, never more than the wall
    time since the session started."""
    if isinstance(reported, bool) or not isinstance(reported, (int, float)) or reported != reported:
        reported = row["active_seconds"]
    started = config.parse_ts(row["started_at"])
    wall = max(0.0, (config.utcnow() - started).total_seconds()) + WALL_SLACK_SECONDS
    return round(max(row["active_seconds"], min(float(reported), wall)), 1)


def _status_json(conn, current: dict) -> dict:
    st = limits.status(conn, current)
    st.pop("limits", None)
    st["warn"] = st["leftSeconds"] is not None and st["leftSeconds"] <= limits.WARN_SECONDS
    return st


@router.post("/sessions", status_code=201)
def start_session(background: BackgroundTasks, body: dict = Body(...), current: dict = Depends(get_current_user)):
    _require_enabled(current)
    if not isinstance(body, dict):
        raise HTTPException(422, "Send {game, mode, practice}.")
    game, mode, practice = body.get("game"), body.get("mode"), body.get("practice", False)
    resume = body.get("resume", False)
    match_id = body.get("matchId")        # a race (SPEC §13.3): the match decides mode, Practice, seed and levels
    if match_id is not None and body.get("resume"):
        raise HTTPException(422, "A match can't be continued from a saved game.")
    if not isinstance(game, str) or not games.exists(game):
        raise HTTPException(404, "Unknown game.")
    if not settings.game_enabled(game):
        raise HTTPException(409, f"{games.name(game)} is switched off on App settings.")
    if not isinstance(resume, bool):
        raise HTTPException(422, "resume must be true or false.")
    saved = None
    if resume:                       # continue the saved game: its mode and Practice come with it
        with db.get_conn() as conn:
            saved = saves.get(conn, current["id"], game)
        if not saved:
            raise HTTPException(404, "There's no saved game to continue.")
        if not saves.can_resume(saved):
            raise HTTPException(409, "That saved game was made by an older version and can't be continued. End it to keep its score.")
        mode, practice = saved["mode"], bool(saved["practice"])
    match = None
    if match_id is not None:
        with db.get_conn() as conn:
            try:
                match = together.for_session(conn, current, match_id, game)
            except together.TogetherError as e:
                raise HTTPException(e.status, str(e))
        mode, practice = match["match"]["mode"], bool(match["match"]["practice"])
    daily_row = None
    if body.get("daily", False) is not False:        # today's challenge: the day decides mode and seed
        if body.get("daily") is not True or resume or match_id is not None:
            raise HTTPException(422, "daily must be true, and can't be combined with a saved game or a race.")
        if not isinstance(practice, bool):
            raise HTTPException(422, "practice must be true or false.")
        with db.get_conn() as conn:
            try:
                daily_row = daily.challenge_for(conn, game, current, practice)
            except daily.DailyError as e:
                raise HTTPException(e.status, str(e))
        mode = daily_row["mode"]
    if not isinstance(mode, str) or not settings.mode_allowed(game, mode):
        raise HTTPException(422, "That mode isn't available.")
    if (games.is_live(game, mode) or games.is_turns(game, mode)) and not match:
        raise HTTPException(422, "That mode is played on two phones: start it with Play with someone.")
    if not isinstance(practice, bool):
        raise HTTPException(422, "practice must be true or false.")
    now = config.now_iso()
    with db.get_conn() as conn:
        if current["is_child"]:
            st = limits.status(conn, current)
            if not limits.game_allowed(st["limits"], game):
                raise HTTPException(403, f"{games.name(game)} isn't one of your games.")
            if not st["canStart"]:
                raise HTTPException(409, st["reason"])
        # one game at a time: anything still open ends at its last heartbeat
        conn.execute("UPDATE play_sessions SET ended_at = last_beat_at WHERE user_id = ? AND ended_at IS NULL",
                     (current["id"],))
        sid = db.new_id()
        set_id = levels.mode_info(game, mode)["set"]
        if saved:
            level_list = json.loads(saved["levels"]) if saved["levels"] else None
        elif match:
            level_list = match["levels"]          # both phones play the same level list
        else:
            level_list = levels.playable(conn, set_id) if set_id else None
        conn.execute("INSERT INTO play_sessions (id, user_id, game, mode, practice, started_at, last_beat_at, "
                     "level_count, base_seconds, resumed, first_started_at, match_id, daily) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (sid, current["id"], game, mode, 1 if practice else 0, now, now,
                      len(level_list) if level_list is not None else None,
                      saved["seconds"] if saved else 0, 1 if saved else 0, saved["started_at"] if saved else now,
                      match_id if match else None, daily_row["date"] if daily_row else None))
        if match:
            together.attach_session(conn, match_id, current["id"], sid)
        best = None if daily_row else scores.best(conn, game, mode, current["id"])
        record = None if daily_row else scores.best(conn, game, mode)
        status = _status_json(conn, current)
    background.add_task(ha_sensors.changed_blocking)
    return {"id": sid, "game": game, "mode": mode, "practice": practice, "best": best, "record": record,
            "playTime": status,
            "seed": match["match"]["seed"] if match else daily_row["seed"] if daily_row
            else int(config.utcnow().timestamp() * 1000) % 2_147_483_647,
            "matchId": match_id if match else None,
            # a live duel: which player this phone is (1 or 2) and the other player's name
            "seat": match["seat"] if match else None,
            "daily": daily_row["date"] if daily_row else None,
            # the app's settings a game reads: Sudoku's hints per puzzle (none = unlimited, as in Practice)
            "config": {"hints": None if practice else settings.get("sudoku_hints")},
            # the levels this game plays through (SPEC §11); fixed for the whole game
            "levels": level_list, "levelsBuilding": level_builder.building(set_id),
            "saved": {"state": json.loads(saved["state"]), "score": saved["score"], "level": saved["level"],
                      "seconds": saved["seconds"]} if saved else None}


def _update(conn, row, reported, end: bool) -> None:
    active = _active(row, reported)
    now = config.now_iso()
    if row["ended_at"]:
        return
    conn.execute("UPDATE play_sessions SET active_seconds = ?, last_beat_at = ?, ended_at = ? WHERE id = ?",
                 (active, now, now if end else None, row["id"]))


def _maybe_warn(background: BackgroundTasks, current: dict, status: dict) -> None:
    if current["is_child"] and status["warn"] and status["usedSeconds"] > 0:
        background.add_task(notify.limit_warning_blocking, current["id"])


@router.post("/sessions/{session_id}/beat")
def beat(session_id: str, background: BackgroundTasks, body: dict = Body(default={}),
         current: dict = Depends(get_current_user)):
    with db.get_conn() as conn:
        row = _session(conn, session_id, current)
        _update(conn, row, body.get("activeSeconds") if isinstance(body, dict) else None, end=False)
        status = _status_json(conn, current)
    _maybe_warn(background, current, status)
    background.add_task(ha_sensors.changed_blocking)
    # nearly at the end of the level list? build the next levels now, before anyone needs them
    level = body.get("level") if isinstance(body, dict) else None
    _auto_levels(row, level)
    return {"playTime": status, "ended": bool(row["ended_at"])}


def _auto_levels(row, level) -> bool:
    """Start an automatic level build if this game reached a level near the end of its list. True if levels
    are being built for this game now."""
    set_id = levels.mode_info(row["game"], row["mode"])["set"]
    if not set_id:
        return False
    if isinstance(level, int) and not isinstance(level, bool) and 0 < level <= games.max_level(
            row["game"], row["mode"], row["level_count"]):
        level_builder.maybe_auto(set_id, level, row["level_count"])
    return level_builder.building(set_id)


@router.post("/sessions/{session_id}/end")
def end_session(session_id: str, background: BackgroundTasks, body: dict = Body(default={}),
                current: dict = Depends(get_current_user)):
    with db.get_conn() as conn:
        row = _session(conn, session_id, current)
        _update(conn, row, body.get("activeSeconds") if isinstance(body, dict) else None, end=True)
        status = _status_json(conn, current)
    _maybe_warn(background, current, status)
    background.add_task(ha_sensors.changed_blocking)
    return {"playTime": status}


@router.post("/scores")
def post_score(background: BackgroundTasks, body: dict = Body(...), current: dict = Depends(get_current_user)):
    """A finished game: {sessionId, score, level, seconds}. Practice games and
    games under 3 seconds end the session but aren't stored; an impossible
    result (SPEC §5.1) is refused with 422."""
    _require_enabled(current)
    if not isinstance(body, dict):
        raise HTTPException(422, "Send the result as an object.")
    sid = body.get("sessionId")
    if not isinstance(sid, str):
        raise HTTPException(422, "sessionId is required.")
    score, level, seconds = body.get("score"), body.get("level", 1), body.get("seconds")
    with db.get_conn() as conn:
        row = _session(conn, sid, current)
        if row["scored"]:
            raise HTTPException(409, "That game's score was already sent.")
        if row["match_id"] and games.is_turns(row["game"], row["mode"]):
            raise HTTPException(409, "A turn-by-turn match's result is worked out by the app from the moves.")
        wall = _wall(row)
        base = row["base_seconds"] or 0
        reason = games.check_score(row["game"], row["mode"], score, level, seconds, row["level_count"])
        if reason is None and seconds > wall + base:
            reason = "That game is longer than the time since it started."
        _update(conn, row, seconds - base if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) else None,
                end=True)
        conn.execute("UPDATE play_sessions SET scored = 1 WHERE id = ?", (sid,))
        if reason:
            if row["match_id"]:         # an impossible score loses the race: it counts as nothing
                together.record_result(conn, row, 0, 1, 0, False)
            conn.commit()
            raise HTTPException(422, reason)
        result = {"saved": False, "reason": None, "personalBest": False, "householdRecord": False, "scoreId": None,
                  "best": None if row["daily"] else scores.best(conn, row["game"], row["mode"], current["id"])}
        if row["practice"]:
            result["reason"] = "practice"
        elif seconds < scores.MIN_SECONDS:
            result["reason"] = "short"
        elif games.keeps_nothing(row["game"], score):
            result["reason"] = "unfinished"
        else:
            daily_day = row["daily"]
            saved = scores.insert(conn, current["id"], dict(row, started_at=row["first_started_at"] or row["started_at"],
                                                           mode=daily.mode_key(daily_day) if daily_day else row["mode"]),
                                  int(score), int(level), int(round(seconds)))
            result.update(saved=True, personalBest=saved["personalBest"] and not daily_day,
                          householdRecord=saved["householdRecord"] and not daily_day,    # a daily isn't a record
                          scoreId=saved["id"], best=None if daily_day else max(int(score), result["best"] or 0))
        saves.consumed_by(conn, current["id"], row)
        if row["match_id"]:             # a race: the same score is the player's result in the match
            together.record_result(conn, row, score, level, seconds, body.get("won"), body.get("report"))
        status = _status_json(conn, current)
    result["playTime"] = status
    result["levelsComing"] = _auto_levels(row, int(level))
    if result["householdRecord"]:
        background.add_task(notify.record_blocking, current["id"], row["game"], row["mode"], int(score))
    _maybe_warn(background, current, status)
    background.add_task(ha_sensors.changed_blocking)
    return result


@router.get("/scores/mine")
def my_scores(current: dict = Depends(get_current_user)):
    with db.get_conn() as conn:
        return scores.mine(conn, current["id"])


@router.delete("/scores/{score_id}")
def delete_score(score_id: int, background: BackgroundTasks, current: dict = Depends(get_current_user)):
    """Your own score, or (admins) anyone's."""
    with db.get_conn() as conn:
        row = conn.execute("SELECT user_id FROM scores WHERE id = ?", (score_id,)).fetchone()
        if not row or (row["user_id"] != current["id"] and not current["is_admin"]):
            raise HTTPException(404, "That score isn't known.")
        conn.execute("DELETE FROM scores WHERE id = ?", (score_id,))
    background.add_task(ha_sensors.changed_blocking)
    return {"status": "ok"}


@router.get("/leaderboard")
def leaderboard(game: str = Query(...), mode: str | None = Query(default=None),
                period: str = Query(default="all"), current: dict = Depends(get_current_user)):
    if not settings.get("leaderboard"):
        raise HTTPException(403, "The leaderboard is switched off on App settings.")
    names = "full"
    if current["is_child"]:
        with db.get_conn() as conn:
            names = limits.load_limits(conn, current["id"])["leaderboard"]
        if names == "hidden":
            raise HTTPException(403, "The leaderboard isn't shown for you.")
    if not games.exists(game) or not settings.game_enabled(game):
        raise HTTPException(404, "Unknown game.")
    mode = mode or games.GAMES[game]["default_mode"]
    if mode not in games.mode_ids(game):
        raise HTTPException(422, "Unknown mode for this game.")
    if period not in ("all", "month"):
        raise HTTPException(422, "period must be all or month.")
    with db.get_conn() as conn:
        rows = scores.leaderboard(conn, game, mode, period, current, names)
    return {"game": game, "mode": mode, "period": period, "rows": rows,
            "canDelete": current["is_admin"]}


# ---------------------------------------------------------------------------
# Saved games (SPEC §12)
# ---------------------------------------------------------------------------

@router.post("/sessions/{session_id}/save")
def save_game(session_id: str, background: BackgroundTasks, body: dict = Body(...),
              current: dict = Depends(get_current_user)):
    """Put the game aside to finish later: {state, stateVersion, score, level, seconds}. Ends the session without a
    score. Replaces this person's saved game of the same kind (whose score is kept)."""
    _require_enabled(current)
    if not isinstance(body, dict):
        raise HTTPException(422, "Send the game as an object.")
    with db.get_conn() as conn:
        row = _session(conn, session_id, current)
        if row["ended_at"] or row["scored"]:
            raise HTTPException(409, "That game has already ended.")
        if row["daily"]:
            raise HTTPException(409, "A daily challenge can't be put aside: finish it, or play it as Practice to take your time.")
        set_id = levels.mode_info(row["game"], row["mode"])["set"]
        levels_json = None
        if set_id and not row["resumed"]:
            levels_json = json.dumps(levels.playable(conn, set_id)[:row["level_count"] or None])
        try:
            out = saves.store(conn, current["id"], row, body, levels_json, _wall(row))
        except saves.SaveError as e:
            raise HTTPException(e.status, str(e))
        seconds = body.get("seconds")
        _update(conn, row, seconds - (row["base_seconds"] or 0), end=True)
        conn.execute("UPDATE play_sessions SET scored = 1 WHERE id = ?", (session_id,))
        out["playTime"] = _status_json(conn, current)
    background.add_task(ha_sensors.changed_blocking)
    return out


@router.get("/saved")
def my_saved(current: dict = Depends(get_current_user)):
    with db.get_conn() as conn:
        return {"saved": saves.mine(conn, current["id"])}


@router.delete("/saved/{game}")
def end_saved(game: str, background: BackgroundTasks, keepScore: bool = Query(default=True),
              current: dict = Depends(get_current_user)):
    """End a saved game: keepScore=true stores its score as it stands; false throws it away."""
    with db.get_conn() as conn:
        row = saves.get(conn, current["id"], game)
        if not row:
            raise HTTPException(404, "There's no saved game of that kind.")
        if keepScore:
            out = saves.finalize(conn, current["id"], row)
        else:
            conn.execute("DELETE FROM saved_games WHERE user_id = ? AND game = ?", (current["id"], game))
            out = {"scoreSaved": False, "score": row["score"], "reason": "thrown away"}
    if out.get("householdRecord"):
        background.add_task(notify.record_blocking, current["id"], row["game"], row["mode"], row["score"])
    background.add_task(ha_sensors.changed_blocking)
    return out

