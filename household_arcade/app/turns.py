"""Playing turn by turn from two (or up to four) phones (SPEC §13.5) — the moves, checked by the server.

A turn-by-turn match is a `matches` row of kind `turns` (invites, accept, decline, cancel, rematch and head to head
are together.py's, as for races). The phones needn't be online at the same time: a match can last days.

- **Start**: when everyone invited has joined — or the inviter starts with those who have (2–4 players: the dummy game
  in the tests, Ludo and Snakes and Ladders later) — the seats are numbered 1… in invite order and the game's rules
  (`app/rules/<game>.py`) make the start from the match's seed and options. Who moves first comes from the seed.
- **A move** (`move`): from a player whose move it is (else 409), checked by the rules (422 if it isn't legal), stored
  in `match_moves`, the state saved; a child must be allowed that game and within their play time and outside quiet
  hours to move (the page itself is an ordinary play session, so time spent on it counts as play time, SPEC §7).
  Then seats played by the computer (a player who left a game of three or four) move at once, and the next player is
  told "Your move in Four in a Row against Asha" (`notify_blocking`: at most one every 15 minutes per match and
  player, none in a child's quiet hours — those wait and go out later from housekeeping; App setting "Your-move
  notifications" and the person's "Receive notifications").
- **Dice** (`roll`, games whose rules have DICE): the server rolls for the player whose move it is; asking again
  gives the same roll until the move is made (a player can't choose or repeat a roll); the move uses it and it is
  stored with the move.
- **The end**: the rules say who won; each player's score (the rules' `score`) is saved as a normal game in the
  match's mode (not Practice; a loss scores 0 and keeps nothing), with the seconds they spent on the match's page.
  Resigning: in a two-player game the other wins; with more, the one who left loses and the computer plays their
  seat. 7 days without a move: the one(s) who didn't move lose (or are played by the computer, with more players).

Blocking functions (they take a DB connection); `notify_blocking` opens its own and must be called without one.
"""
from __future__ import annotations

import json
import logging
import random
import secrets
from datetime import timedelta

from . import config, db, games, limits, notify, rules, scores, settings
from .common import ha_notify

logger = logging.getLogger("together")

INVITE_SECONDS = 7 * 24 * 3600         # an invite to a turn-by-turn game lasts 7 days
TIMEOUT_SECONDS = 7 * 24 * 3600        # 7 days without a move: the one who didn't move loses
NOTIFY_EVERY = 15 * 60                 # at most one your-move notification a match and person in this long
MAX_SECONDS = games.MAX_SECONDS


class TurnsError(ValueError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def ok(game: str) -> bool:
    """Can this game be played turn by turn (it has turn modes and server rules)?"""
    return bool(games.turn_modes(game)) and rules.get(game) is not None


def players_range(game: str) -> tuple[int, int]:
    mod = rules.get(game)
    return tuple(getattr(mod, "PLAYERS", (2, 2))) if mod else (2, 2)


def _players(conn, match_id: str) -> list:
    return conn.execute("SELECT p.*, u.name, u.username, u.disabled, u.receive_notifications, u.is_child "
                        "FROM match_players p JOIN users u ON u.id = p.user_id WHERE p.match_id = ? ORDER BY p.seat",
                        (match_id,)).fetchall()


def state_of(m) -> dict | None:
    return json.loads(m["state"]) if m["state"] else None


def options_of(m) -> dict:
    return json.loads(m["options"]) if m["options"] else {}


def _now_iso() -> str:
    return config.now_iso()


# ---------------------------------------------------------------------------
# Starting
# ---------------------------------------------------------------------------

def start(conn, m, actor: str | None = None) -> None:
    """Everyone who joined plays: seats 1… in invite order, the rules' start, the first turn's clock. `actor` (the one
    who just joined or started it, and is looking at it) isn't sent a your-move notification."""
    from . import together
    mid = m["id"]
    joined = [p for p in _players(conn, mid) if p["invite_status"] == "accepted"]
    conn.execute("DELETE FROM match_players WHERE match_id = ? AND invite_status != 'accepted'", (mid,))
    actor_seat = None
    for i, p in enumerate(joined, start=1):
        conn.execute("UPDATE match_players SET seat = ? WHERE match_id = ? AND user_id = ?", (i, mid, p["user_id"]))
        if p["user_id"] == actor:
            actor_seat = i
    mod = rules.get(m["game"])
    st = mod.new(m["seed"], len(joined), options_of(m))
    now = _now_iso()
    conn.execute("UPDATE matches SET status = 'playing', started_at = ?, state = ?, turn_at = ? WHERE id = ?",
                 (now, json.dumps(st), now, mid))
    together.LIVE.pop(mid, None)
    _mark_due(conn, mid, st, mover=actor_seat)
    together.touch(mid)


def can_start_now(conn, m) -> bool:
    lo, _ = players_range(m["game"])
    n = conn.execute("SELECT COUNT(*) FROM match_players WHERE match_id = ? AND invite_status = 'accepted'",
                     (m["id"],)).fetchone()[0]
    return n >= lo


# ---------------------------------------------------------------------------
# What a phone sees
# ---------------------------------------------------------------------------

def to_move_seats(m, st=None) -> list[int]:
    """The seats (1-based) that may move now; [] when the match isn't being played."""
    if m["status"] != "playing" or m["kind"] != "turns":
        return []
    st = st if st is not None else state_of(m)
    if not st:
        return []
    return [i + 1 for i in rules.get(m["game"]).to_move(st)]


def brief(conn, m, user_id: str) -> dict:
    """The part of the match picture (together.view) about turns: whose move, how many moves, the last one."""
    st = state_of(m)
    last = conn.execute("SELECT MAX(number) AS n, MAX(at) AS at FROM match_moves WHERE match_id = ?", (m["id"],)).fetchone()
    seat = conn.execute("SELECT seat FROM match_players WHERE match_id = ? AND user_id = ?", (m["id"], user_id)).fetchone()
    tm = to_move_seats(m, st)
    lo, hi = players_range(m["game"])
    return {"number": last["n"] or 0, "lastMoveAt": last["at"] or m["started_at"], "toMove": tm,
            "myTurn": bool(seat and seat["seat"] in tm), "options": options_of(m), "minPlayers": lo, "maxPlayers": hi,
            "turnSince": m["turn_at"]}


def picture(conn, m, user_id: str) -> dict:
    """GET /api/matches/{id}/turns: everything a phone needs to draw the match — its seat, the moves as it may see
    them, the rules' view for its seat (Sea Battle: its own fleet only), whose move, the result."""
    from . import together
    mid = m["id"]
    me = conn.execute("SELECT * FROM match_players WHERE match_id = ? AND user_id = ?", (mid, user_id)).fetchone()
    if not me:
        raise TurnsError(404, "That match isn't known.")
    mod = rules.get(m["game"])
    st = state_of(m)
    seat = me["seat"]
    rows = conn.execute("SELECT number, seat, move, dice, at FROM match_moves WHERE match_id = ? ORDER BY number",
                        (mid,)).fetchall()
    moves = [{"n": r["number"], "seat": r["seat"], "dice": r["dice"], "at": r["at"],
              "move": mod.visible(json.loads(r["move"]), seat - 1, r["seat"] - 1)} for r in rows]
    result = None
    if m["status"] == "done":
        ps = _players(conn, mid)
        win = next((p["seat"] for p in ps if m["winner"] and p["user_id"] == m["winner"]), None)
        draw = any(p["result"] == "draw" for p in ps)
        # the winner's seat, 0 for a draw, None for no result
        result = {"winner": win if win else (0 if draw else None), "reason": m["end_reason"]}
    players = [{"seat": p["seat"], "id": p["user_id"], "name": p["name"], "you": p["user_id"] == user_id,
                "computer": bool(p["computer"]), "left": bool(p["left_at"]), "result": p["result"], "score": p["score"]}
               for p in _players(conn, mid)]
    roll = st.get("_roll") if st else None
    return {
        "id": mid, "game": m["game"], "mode": m["mode"], "seed": m["seed"], "options": options_of(m),
        "status": m["status"], "seat": seat, "players": players, "number": len(moves), "moves": moves,
        # a match that ended another way (resigned, 7 days) shows what is left hidden, as a finished game does
        "view": mod.view(dict(st, over=True) if m["status"] == "done" and not st.get("over") else st, seat - 1) if st else None, "toMove": to_move_seats(m, st),
        "over": m["status"] == "done", "result": result, "endReason": m["end_reason"],
        "roll": roll if roll and roll.get("seat") == seat else None, "dice": getattr(mod, "DICE", 0),
        "practice": bool(m["practice"]), "turnSince": m["turn_at"], "v": together.signature(mid),
    }


def seen(conn, match_id: str, user_id: str) -> None:
    """The player has the match open: a waiting your-move notification isn't needed any more."""
    conn.execute("UPDATE match_players SET notify_due = 0 WHERE match_id = ? AND user_id = ?", (match_id, user_id))


# ---------------------------------------------------------------------------
# Moves
# ---------------------------------------------------------------------------

def _need_playing(conn, match_id: str, current: dict):
    from . import together
    m = together.settle(conn, match_id)
    if not m or not together.is_player(conn, match_id, current["id"]):
        raise TurnsError(404, "That match isn't known.")
    if m["kind"] != "turns":
        raise TurnsError(409, "That match isn't played turn by turn.")
    if m["status"] != "playing":
        raise TurnsError(409, "That match isn't being played." if m["status"] != "done" else "That match is over.")
    me = conn.execute("SELECT * FROM match_players WHERE match_id = ? AND user_id = ?", (match_id, current["id"])).fetchone()
    if me["left_at"] or me["computer"]:
        raise TurnsError(409, "You've left that match.")
    return m, me


def _may_play(conn, current: dict, game: str) -> None:
    """Children's limits apply to every move: their games, their time and quiet hours (SPEC §7)."""
    if current.get("disabled"):
        raise TurnsError(403, "An admin has switched you off in Household Arcade, so you can't play just now.")
    if not settings.game_enabled(game):
        raise TurnsError(409, f"{games.name(game)} is switched off on App settings.")
    if current.get("is_child"):
        st = limits.status(conn, current)
        if not limits.game_allowed(st["limits"], game):
            raise TurnsError(403, f"{games.name(game)} isn't one of your games.")
        if not st["canStart"]:
            raise TurnsError(409, st["reason"])


def _store(conn, m, st_before: dict, seat: int, move, *, dice=None) -> dict:
    """Check and play one move for `seat` (1-based); store it. Returns the new state."""
    mod = rules.get(m["game"])
    rules.check_shape(move)
    st = rules.copy(st_before)
    if dice is not None:
        st["_roll"] = {"seat": seat, "value": dice}
    try:
        new, record = mod.apply(st, seat - 1, move)
    except rules.Illegal:
        raise
    except (KeyError, TypeError, ValueError, IndexError) as e:      # a malformed move the rules didn't expect
        raise rules.Illegal("That move isn't allowed.") from e
    new.pop("_roll", None)
    number = conn.execute("SELECT COUNT(*) FROM match_moves WHERE match_id = ?", (m["id"],)).fetchone()[0] + 1
    try:
        conn.execute("INSERT INTO match_moves (match_id, number, seat, move, dice, at) VALUES (?, ?, ?, ?, ?, ?)",
                     (m["id"], number, seat, json.dumps(record), dice, _now_iso()))
    except Exception as e:                           # the other phone's move came first (same number)
        raise TurnsError(409, "Another move came first — the board is updated.") from e
    return new


def _save_state(conn, m, st: dict, turn_changed: bool) -> None:
    sets = "state = ?" + (", turn_at = ?" if turn_changed else "")
    args = [json.dumps(st)] + ([_now_iso()] if turn_changed else []) + [m["id"]]
    conn.execute(f"UPDATE matches SET {sets} WHERE id = ?", args)


def _computer_moves(conn, m, st: dict) -> dict:
    """Seats the computer plays (a player who left a game of three or four) move at once, until a person's turn."""
    mod = rules.get(m["game"])
    computer = {p["seat"] for p in _players(conn, m["id"]) if p["computer"]}
    guard = 0
    while computer and guard < 500:
        tm = [s + 1 for s in mod.to_move(st)]
        if not tm or not all(s in computer for s in tm):
            break
        seat = tm[0]
        n = conn.execute("SELECT COUNT(*) FROM match_moves WHERE match_id = ?", (m["id"],)).fetchone()[0]
        rnd = random.Random(int(m["seed"]) * 1000 + n)
        dice = secrets.randbelow(mod.DICE) + 1 if getattr(mod, "DICE", 0) else None
        probe = rules.copy(st)
        if dice is not None:
            probe["_roll"] = {"seat": seat, "value": dice}
        move = mod.computer(probe, seat - 1, rnd)
        st = _store(conn, m, st, seat, move, dice=dice)
        guard += 1
    return st


def _session_time(conn, match_id: str, user_id: str, session_id, reported) -> None:
    """A move carries the page's active seconds so far (as a heartbeat does), so a match that ends with this move has
    the mover's time up to now. Only grows, never more than the wall time since the session began."""
    if not isinstance(session_id, str) or isinstance(reported, bool) or not isinstance(reported, (int, float)) \
            or reported != reported:
        return
    row = conn.execute("SELECT * FROM play_sessions WHERE id = ? AND user_id = ? AND match_id = ? AND ended_at IS NULL",
                       (session_id, user_id, match_id)).fetchone()
    if not row:
        return
    wall = max(0.0, (config.utcnow() - config.parse_ts(row["started_at"])).total_seconds()) + 2
    active = round(max(row["active_seconds"], min(float(reported), wall)), 1)
    conn.execute("UPDATE play_sessions SET active_seconds = ?, last_beat_at = ? WHERE id = ?", (active, _now_iso(), session_id))


def move(conn, match_id: str, current: dict, body) -> tuple[dict, bool]:
    """A player's move: {move, n?} (n = the number of moves the phone has seen; a different one is 409, the board has
    changed). Returns (the new picture, whether anyone needs telling it's their move)."""
    if not isinstance(body, dict) or "move" not in body:
        raise TurnsError(422, "Send {move}.")
    m, me = _need_playing(conn, match_id, current)
    _may_play(conn, current, m["game"])
    _session_time(conn, match_id, current["id"], body.get("sessionId"), body.get("activeSeconds"))
    mod = rules.get(m["game"])
    st = state_of(m)
    seen_n = body.get("n")
    have = conn.execute("SELECT COUNT(*) FROM match_moves WHERE match_id = ?", (match_id,)).fetchone()[0]
    if seen_n is not None and seen_n != have:
        raise TurnsError(409, "The board has changed — it's up to date now.")
    seat = me["seat"]
    if seat - 1 not in mod.to_move(st):
        raise TurnsError(409, "It isn't your move.")
    dice = None
    if getattr(mod, "DICE", 0):
        roll_ = st.get("_roll")
        if not roll_ or roll_.get("seat") != seat:
            raise TurnsError(409, "Roll the dice first.")
        dice = roll_["value"]
    tell = _play(conn, m, current["id"], seat, st, body["move"], dice)
    m = conn.execute("SELECT * FROM matches WHERE id = ?", (match_id,)).fetchone()
    return picture(conn, m, current["id"]), tell


def _play(conn, m, user_id: str, seat: int, st: dict, mv, dice) -> bool:
    """Check, store and play a move of `seat` (with the server's roll, for a dice game); then the computer's seats;
    then the end, or who is told it's their move (returned: whether anyone needs telling)."""
    mod = rules.get(m["game"])
    match_id = m["id"]
    before_turn = mod.to_move(st)
    try:
        st = _store(conn, m, st, seat, mv, dice=dice)
    except rules.Illegal as e:
        raise TurnsError(422, str(e) or "That move isn't allowed.")
    st = _computer_moves(conn, m, st)
    _save_state(conn, m, st, mod.to_move(st) != before_turn)
    conn.execute("UPDATE match_players SET notify_due = 0 WHERE match_id = ? AND user_id = ?", (match_id, user_id))
    m = conn.execute("SELECT * FROM matches WHERE id = ?", (match_id,)).fetchone()
    tell = False
    if mod.result(st) is not None:
        finish(conn, m, st, "finished")
    else:
        tell = _mark_due(conn, match_id, st, mover=seat)
    from . import together
    together.touch(match_id)
    return tell


def roll(conn, match_id: str, current: dict) -> tuple[dict, bool]:
    """The server's dice roll for the player whose move it is (the same roll again until the move is made). When the
    roll leaves no choice (the rules' `forced`: a pass, tokens that would all end up alike, or Snakes and Ladders'
    only move) that move is played at once with it. Returns ({roll, seat, moved, picture}, whether anyone needs
    telling it's their move)."""
    m, me = _need_playing(conn, match_id, current)
    _may_play(conn, current, m["game"])
    mod = rules.get(m["game"])
    if not getattr(mod, "DICE", 0):
        raise TurnsError(409, f"{games.name(m['game'])} has no dice.")
    st = state_of(m)
    if me["seat"] - 1 not in mod.to_move(st):
        raise TurnsError(409, "It isn't your move.")
    r = st.get("_roll")
    if not r or r.get("seat") != me["seat"]:
        r = {"seat": me["seat"], "value": secrets.randbelow(mod.DICE) + 1}
        st["_roll"] = r
        conn.execute("UPDATE matches SET state = ? WHERE id = ?", (json.dumps(st), match_id))
        from . import together
        together.touch(match_id)
    tell, moved = False, False
    auto = mod.forced(st, me["seat"] - 1) if hasattr(mod, "forced") else None
    if auto is not None:
        tell = _play(conn, m, current["id"], me["seat"], st, auto, r["value"])
        moved = True
    m = conn.execute("SELECT * FROM matches WHERE id = ?", (match_id,)).fetchone()
    return {"roll": r["value"], "seat": r["seat"], "moved": moved, "picture": picture(conn, m, current["id"])}, tell


# ---------------------------------------------------------------------------
# Leaving, timeouts, the end
# ---------------------------------------------------------------------------

def _humans(conn, match_id: str) -> list:
    return [p for p in _players(conn, match_id) if not p["computer"] and not p["left_at"]]


def leave(conn, m, user_ids: list[str], reason: str) -> None:
    """Players who resigned or didn't move for 7 days: in a two-player match (or when one person is left) the match
    ends and they lose; with more players they lose and the computer plays their seats."""
    mid = m["id"]
    now = _now_iso()
    for uid in user_ids:
        conn.execute("UPDATE match_players SET left_at = ?, computer = 1, result = 'lost', finished_at = ? "
                     "WHERE match_id = ? AND user_id = ? AND left_at IS NULL", (now, now, mid, uid))
    humans = _humans(conn, mid)
    if len(humans) <= 1:
        winner = humans[0]["user_id"] if humans else None
        results = {p["user_id"]: ("won" if p["user_id"] == winner else "lost") for p in _players(conn, mid)}
        st = state_of(m)
        _record_scores(conn, m, st, results, winner)
        from . import together
        together._end(conn, m, "done", reason, winner, results)
        return
    st = _computer_moves(conn, m, state_of(m))
    mod = rules.get(m["game"])
    _save_state(conn, m, st, True)
    fresh = conn.execute("SELECT * FROM matches WHERE id = ?", (mid,)).fetchone()
    if mod.result(st) is not None:
        finish(conn, fresh, st, "finished")
    else:
        _mark_due(conn, mid, st, mover=None)
        from . import together
        together.touch(mid)


def resign(conn, match_id: str, current: dict):
    m, me = _need_playing(conn, match_id, current)
    leave(conn, m, [current["id"]], "resigned")
    return conn.execute("SELECT * FROM matches WHERE id = ?", (match_id,)).fetchone()


def timeouts(conn, m) -> bool:
    """7 days without a move: whoever should have moved loses (SPEC §13.5). True if the match changed."""
    if m["kind"] != "turns" or m["status"] != "playing" or not m["turn_at"]:
        return False
    since = config.parse_ts(m["turn_at"])
    if not since or (config.utcnow() - since).total_seconds() < TIMEOUT_SECONDS:
        return False
    tm = set(to_move_seats(m))
    stale = [p["user_id"] for p in _humans(conn, m["id"]) if p["seat"] in tm]
    if not stale:
        return False
    if len(stale) == len(_humans(conn, m["id"])):           # nobody moved (both still placing): no result
        now = _now_iso()
        for p in _players(conn, m["id"]):
            conn.execute("UPDATE match_players SET finished_at = ? WHERE match_id = ? AND user_id = ?", (now, m["id"], p["user_id"]))
        from . import together
        together._end(conn, m, "done", "timeout", None, None)
        return True
    leave(conn, m, stale, "timeout")
    return True


def _seconds_on(conn, match_id: str, user_id: str) -> tuple[int, str | None]:
    """The time a player spent on the match's page (their sessions for it), and their latest session."""
    row = conn.execute("SELECT COALESCE(SUM(active_seconds), 0) AS s, MAX(id) AS sid FROM play_sessions "
                       "WHERE match_id = ? AND user_id = ?", (match_id, user_id)).fetchone()
    latest = conn.execute("SELECT id FROM play_sessions WHERE match_id = ? AND user_id = ? ORDER BY started_at DESC LIMIT 1",
                          (match_id, user_id)).fetchone()
    return min(MAX_SECONDS, int(round(row["s"] or 0))), latest["id"] if latest else None


def _record_scores(conn, m, st, results: dict, winner) -> None:
    """Each person still in the match: their score (the rules' own) saved as a normal game of the match's mode."""
    mod = rules.get(m["game"])
    for p in _players(conn, m["id"]):
        if p["left_at"] and p["user_id"] != winner:
            continue
        seconds, sid = _seconds_on(conn, m["id"], p["user_id"])
        sc = 0
        if st is not None and mod.result(st) is not None:
            sc = int(mod.score(st, p["seat"] - 1))
        if results.get(p["user_id"]) == "won" and sc == 0:
            # the others left or ran out of time — or the rules' winner was a seat the computer plays (a player who
            # left a game of three or four): the best-placed person still playing wins the base
            sc = int(getattr(mod, "BASE", 0))
        conn.execute("UPDATE match_players SET score = ?, level = 1, seconds = ?, finished_at = COALESCE(finished_at, ?) "
                     "WHERE match_id = ? AND user_id = ?", (sc, seconds, _now_iso(), m["id"], p["user_id"]))
        if m["practice"] or games.keeps_nothing(m["game"], sc):
            continue
        reason = games.check_score(m["game"], m["mode"], sc, 1, max(seconds, scores.MIN_SECONDS), None)
        if reason:
            logger.warning("Turn-by-turn match %s: %s's score %s not kept: %s", m["id"], p["user_id"], sc, reason)
            continue
        scores.insert(conn, p["user_id"], {"game": m["game"], "mode": m["mode"], "started_at": m["started_at"],
                                           "id": sid}, sc, 1, max(seconds, scores.MIN_SECONDS))


def finish(conn, m, st: dict, reason: str) -> None:
    """The rules say the game is over: results (the best-placed person still playing wins; a draw for all), scores."""
    from . import together
    mod = rules.get(m["game"])
    res = mod.result(st) or {}
    ps = _players(conn, m["id"])
    by_seat = {p["seat"]: p for p in ps}
    winner = None
    if res.get("winner") is not None:
        for s in [res["winner"]] + [x for x in res.get("places", []) if x != res["winner"]]:
            p = by_seat.get(s + 1)
            if p and not p["left_at"]:
                winner = p["user_id"]
                break
    results = {}
    for p in ps:
        if p["left_at"]:
            results[p["user_id"]] = "lost"
        elif res.get("winner") is None:
            results[p["user_id"]] = "draw"
        else:
            results[p["user_id"]] = "won" if p["user_id"] == winner else "lost"
    _record_scores(conn, m, st, results, winner)
    together._end(conn, m, "done", reason, winner, results)


# ---------------------------------------------------------------------------
# "Your move" notifications
# ---------------------------------------------------------------------------

def _mark_due(conn, match_id: str, st: dict, mover) -> bool:
    """The people whose move it is now (not the one who just moved) are owed a notification. True if any."""
    m = conn.execute("SELECT game FROM matches WHERE id = ?", (match_id,)).fetchone()
    tm = {s + 1 for s in rules.get(m["game"]).to_move(st)}
    any_due = False
    for p in _players(conn, match_id):
        due = p["seat"] in tm and p["seat"] != mover and not p["computer"] and not p["left_at"]
        conn.execute("UPDATE match_players SET notify_due = ? WHERE match_id = ? AND user_id = ?",
                     (1 if due else 0, match_id, p["user_id"]))
        any_due = any_due or due
    return any_due


def _quiet_now(conn, p) -> bool:
    if not p["is_child"]:
        return False
    from . import auth
    if auth.is_admin_identity(p["user_id"], p["username"]):
        return False
    return bool(limits.status(conn, {"id": p["user_id"], "is_child": True})["quiet"])


def notify_blocking(match_id: str) -> int:
    """Tell the players whose move it is (those owed one): "Your move in Four in a Row against Asha". At most one a
    15 minutes per match and person; none in a child's quiet hours (it stays owed and goes out later). Blocking: call
    without holding a DB connection. Returns how many phones it reached."""
    if not settings.get("notify_turns"):
        return 0
    sends = []
    with db.get_conn() as conn:
        m = conn.execute("SELECT * FROM matches WHERE id = ?", (match_id,)).fetchone()
        if not m or m["kind"] != "turns" or m["status"] != "playing":
            return 0
        ps = _players(conn, match_id)
        now = config.utcnow()
        tm = set(to_move_seats(m))
        for p in ps:
            if not p["notify_due"]:
                continue
            if p["seat"] not in tm or p["disabled"] or not p["receive_notifications"]:
                conn.execute("UPDATE match_players SET notify_due = 0 WHERE match_id = ? AND user_id = ?", (match_id, p["user_id"]))
                continue
            last = config.parse_ts(p["notified_at"]) if p["notified_at"] else None
            if last and (now - last).total_seconds() < NOTIFY_EVERY:
                continue                                        # stays owed
            if _quiet_now(conn, p):
                continue                                        # stays owed until the quiet hours end
            others = [q["name"].split(" ")[0] for q in ps if q["user_id"] != p["user_id"]]
            sends.append((dict(p), ha_notify.services_for({"id": p["user_id"], "name": p["name"], "username": p["username"]}, conn),
                          " and ".join(others) if len(others) <= 2 else ", ".join(others[:-1]) + " and " + others[-1]))
            conn.execute("UPDATE match_players SET notified_at = ?, notify_due = 0 WHERE match_id = ? AND user_id = ?",
                         (config.now_iso(), match_id, p["user_id"]))
    reached = 0
    for p, services, against in sends:
        message = f"Your move in {games.name(m['game'])} against {against}."
        data = {"tag": f"arcade-turn-{match_id}"}
        link = notify._link(f"#/home/turn/{match_id}")
        if link:
            data.update(link)
            data["actions"] = [{"action": "URI", "title": "Open", "uri": link["url"]}]
        results = ha_notify.send_to_services(services, notify.TITLE, message, data)
        reached += sum(1 for ok_ in results.values() if ok_)
    return reached


def due_matches(conn) -> list[str]:
    return [r["match_id"] for r in conn.execute(
        "SELECT DISTINCT p.match_id FROM match_players p JOIN matches m ON m.id = p.match_id "
        "WHERE p.notify_due = 1 AND m.status = 'playing' AND m.kind = 'turns'").fetchall()]


def send_due_blocking() -> int:
    """Housekeeping: notifications that waited (quiet hours, the 15 minutes) go out once they may."""
    with db.get_conn() as conn:
        ids = due_matches(conn)
    return sum(notify_blocking(mid) for mid in ids)
