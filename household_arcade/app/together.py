"""Playing together from two phones: step 1, Race, and step 2, live duels (SPEC §13).

Two people in the household play the same single-player game at the same moment, each on their own phone, with
the same seed, mode and level list. The server is a relay and referee, not a game engine:

- **Invite** (`create`): the one who invites picks a person who may play that game now; the invite lasts 5 minutes
  and reaches them as a phone notification (with Join / Not now), a banner on the Games page and a "Waiting for you"
  card. A person has at most one live invite out. Accepting starts the match: both phones count in 3-2-1 from a
  server time (`startsInMs`, relative, so the phones' clocks never need to agree).
- **Playing**: each phone starts a normal play session with `matchId` (the match's seed, mode and levels come with
  it; limits, quiet hours and Practice apply to each person as for any game) and sends its score, level and "still
  playing" a few times a second (`set_state`, over a WebSocket, or POSTs plus a long poll when the socket can't
  open). Those live numbers are only shown to the other player; they are never scores.
- **Result**: each person's final score is the one they send to `POST /api/scores` like any game (so it is saved
  as a normal game and checked by the usual honest-score limits); `record_result` copies it into the match. When
  both have finished the winner is decided (`decide`); a player whose phone went away (`gone`) loses to the one who
  finished.
- **Head to head** (`against`): wins, losses and draws per person and game, from finished matches that weren't
  Practice.

- **Turn by turn** (kind `turns`, SPEC §13.5: wave 7's board games' "Two phones" modes): the moves go to the server,
  are checked by the game's Python rules and stored (`turns.py`); an invite lasts 7 days, a match can last days, a
  person can have several going at once, and they don't stop anyone playing anything else meanwhile.

- **Live duels** (kind `live`, SPEC §13.4: Snake Duel, Paddle Duel and Tank Battle's "Two phones" modes): both
  phones run the same game in lockstep; `live.py` relays their inputs and referees (checksums, a phone that went
  quiet or away, pauses). The match starts when both phones are ready; each player's score is saved like any
  game's, sent with the end of the game as their phone saw it (`report`); the two must agree for a result
  (`_settle_live`), otherwise the match ends "out of step" with none.

The live numbers live in memory (`LIVE`); the match and its results are in `matches` / `match_players`
(migration 6). Blocking functions (they take a DB connection or open their own) - call them from a thread.
"""
from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime, timedelta

from . import config, db, games, levels, limits, notify, settings
from .common import ha_notify

logger = logging.getLogger("together")

# The single-player games a race is offered for (SPEC §13.3: every game whose randomness comes from the seed).
# A game's `games.py` entry can say otherwise with a `race` key: `False` (no race) or a dict with the rule
# (`{"rule": "score"}`, `{"rule": "score", "tiebreak": "faster"}`, `{"rule": "fastest"}`, `{"rule": "fastest", "tiebreak":
# "score"}`). A game that isn't in
# this list and has no `race` key can't be raced.
RACE_DEFAULT = ("snake", "brick", "blocks", "racer", "flap", "mines", "merge", "colours", "cards", "mole",
                "numbers", "invaders", "rocks", "hop", "bubbles", "gems", "stack", "runner", "lander", "defense")
RULES = ("score", "fastest")
KINDS = ("race", "live", "turns")

INVITE_SECONDS = 5 * 60            # an invite to a live game lasts 5 minutes
COUNT_IN_SECONDS = 4.0             # 3 - 2 - 1 - go, from the moment the invite is accepted
GONE_AFTER = 120                   # a player with no session this long after the start, or a stale one, has left
CONNECTED_WITHIN = 6.0             # a phone that spoke in the last 6 s is "connected" (they send every 2 s)
MATCH_MAX_SECONDS = 7 * 3600       # nobody plays a race longer than the longest game
REPORT_GRACE = 60                  # live: one phone's result in, the other's not this long after → it alone decides
ACTIVE_WITHIN = 180                # "recently open" for the people list
RECENT_N = 10

TERMINAL = ("done", "declined", "expired", "cancelled")


class TogetherError(ValueError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------------------
# Which games, and who wins
# ---------------------------------------------------------------------------

def race_rule(game: str) -> dict | None:
    """The race rule of a game as a dict ({"rule": ...}), or None when it can't be raced."""
    g = games.GAMES.get(game)
    if not g:
        return None
    r = g.get("race", game in RACE_DEFAULT)
    if r is True:
        return {"rule": "score"}
    if isinstance(r, dict) and r.get("rule", "score") in RULES:
        return {"rule": r.get("rule", "score"), "tiebreak": r.get("tiebreak")}
    return None


def race_ok(game: str) -> bool:
    return race_rule(game) is not None


def live_ok(game: str) -> bool:
    """Can this game be played as a live duel on two phones (SPEC §13.4)?"""
    return bool(games.live_modes(game))


def turns_ok(game: str) -> bool:
    """Can this game be played turn by turn from two (or more) phones (SPEC §13.5)?"""
    from . import turns
    return turns.ok(game)


def together_ok(game: str) -> bool:
    return race_ok(game) or live_ok(game) or turns_ok(game)


def kind_for(game: str, mode=None) -> str:
    """The kind of match a game (and mode) is played as together: a live mode is a live duel, a turn mode turn by
    turn, a game that can only be played turn by turn too; anything else a race."""
    if games.is_live(game, mode):
        return "live"
    if games.is_turns(game, mode) or (mode is None and turns_ok(game) and not race_ok(game) and not live_ok(game)):
        return "turns"
    return "race"


def decide(game: str, players: list[dict]) -> tuple[str | None, dict[str, str]]:
    """The winner (a user id, None for a draw) and each person's result ('won' | 'lost' | 'draw').
    `players` carry user_id, score, seconds and won (the game was won / the puzzle solved)."""
    rule = race_rule(game) or {"rule": "score"}
    a, b = players[0], players[1]

    def key(p):
        if rule["rule"] == "fastest":
            # solved beats not solved; both solved: the shorter game (then, with "tiebreak": "score", the higher score —
            # Slide Puzzle and Lights Out: fewer moves); neither: the higher score
            if p.get("won"):
                return (2, -(p.get("seconds") or 0), (p.get("score") or 0) if rule.get("tiebreak") == "score" else 0)
            return (1, 0, p.get("score") or 0)
        faster = -(p.get("seconds") or 0) if rule.get("tiebreak") == "faster" else 0
        return (0, p.get("score") or 0, faster)

    ka, kb = key(a), key(b)
    if ka == kb:
        return None, {a["user_id"]: "draw", b["user_id"]: "draw"}
    win, lose = (a, b) if ka > kb else (b, a)
    return win["user_id"], {win["user_id"]: "won", lose["user_id"]: "lost"}


# ---------------------------------------------------------------------------
# The live numbers (memory only)
# ---------------------------------------------------------------------------

LIVE: dict[str, dict] = {}


def _mono() -> float:
    import time
    return time.monotonic()


def _live(match_id: str) -> dict:
    return LIVE.setdefault(match_id, {"v": 0, "players": {}})


def touch(match_id: str) -> None:
    """Something about the match changed: every waiting phone gets a fresh picture."""
    _live(match_id)["v"] += 1


def _player_live(match_id: str, user_id: str) -> dict:
    return _live(match_id)["players"].setdefault(
        user_id, {"score": 0, "level": 1, "over": False, "paused": False, "seen": None})


def mark_seen(match_id: str, user_id: str) -> None:
    """A live duel's phone spoke (any message): it is "connected" for the picture. No database."""
    st = _player_live(match_id, user_id)
    was = st["seen"] is not None and _mono() - st["seen"] < CONNECTED_WITHIN
    st["seen"] = _mono()
    if not was:
        touch(match_id)


def signature(match_id: str) -> int:
    """Changes whenever the match changes or someone's connection comes or goes (what a long poll waits on)."""
    lv = LIVE.get(match_id)
    if not lv:
        return 0
    now = _mono()
    bits = 0
    for i, uid in enumerate(sorted(lv["players"])):
        seen = lv["players"][uid]["seen"]
        if seen is not None and now - seen < CONNECTED_WITHIN:
            bits |= 1 << i
    return lv["v"] * 8 + bits


def set_state(match_id: str, user_id: str, body) -> None:
    """A phone's own score / level / still-playing, a few times a second. Display only: clamped, never a score."""
    if not isinstance(body, dict):
        return
    with db.get_conn() as conn:
        m = conn.execute("SELECT game, status FROM matches WHERE id = ?", (match_id,)).fetchone()
        mine = conn.execute("SELECT 1 FROM match_players WHERE match_id = ? AND user_id = ?",
                            (match_id, user_id)).fetchone()
    if not m or not mine:
        return
    st = _player_live(match_id, user_id)
    was = (st["score"], st["level"], st["over"], st["paused"], st["seen"] is not None
           and _mono() - st["seen"] < CONNECTED_WITHIN)
    st["seen"] = _mono()
    if m["status"] == "playing":
        top = games.GAMES[m["game"]]["max_score"]
        score, level = body.get("score"), body.get("level")
        if isinstance(score, (int, float)) and not isinstance(score, bool) and score == score:
            st["score"] = int(max(0, min(top, score)))
        if isinstance(level, (int, float)) and not isinstance(level, bool) and level == level:
            st["level"] = int(max(1, min(games.MAX_LEVEL * 5, level)))
        st["over"] = bool(body.get("over", False)) or st["over"]
        st["paused"] = bool(body.get("paused", False))
    now = (st["score"], st["level"], st["over"], st["paused"], True)
    if now != was:
        touch(match_id)


# ---------------------------------------------------------------------------
# Who can be invited
# ---------------------------------------------------------------------------

def _user_status(conn, row) -> dict:
    return limits.status(conn, {"id": row["id"], "is_child": bool(row["is_child"]) and not _is_admin(row)})


def _is_admin(row) -> bool:
    from . import auth
    return auth.is_admin_identity(row["id"], row["username"])


def why_not(conn, row, game: str, who: str = "them", kind: str = "race") -> str | None:
    """Why this person can't start a race of `game` right now (a short sentence), or None. `row` is a users row.
    A turn-by-turn match (kind "turns") is played over days: a child out of time or in quiet hours can still be
    invited (each move checks their limits), and other matches don't get in the way."""
    if row["disabled"]:
        return "Switched off in the app."
    if not settings.game_enabled(game):
        return "That game is switched off."
    st = _user_status(conn, row)
    if st["isChild"]:
        if not limits.game_allowed(st["limits"], game):
            return f"{games.name(game)} isn't one of their games." if who == "them" else f"{games.name(game)} isn't one of your games."
        if not st["canStart"] and kind != "turns":
            return st["reason"] if who != "them" else _their_reason(st)
    if kind == "turns":
        return None
    busy = in_match(conn, row["id"])
    if busy:
        return "Already playing a match." if who == "them" else "You're already in a match."
    return None


def _their_reason(st: dict) -> str:
    if st.get("quiet"):
        return f"Quiet hours until {st['quietUntil']}."
    return "No play time left today."


def in_match(conn, user_id: str) -> str | None:
    """The id of a match this person is playing right now (started, not finished, not left), else None."""
    for r in conn.execute(
            "SELECT m.id FROM matches m JOIN match_players p ON p.match_id = m.id "
            "WHERE p.user_id = ? AND m.status = 'playing' AND m.kind != 'turns' AND p.finished_at IS NULL",
            (user_id,)).fetchall():
        m = settle(conn, r["id"])
        if m and m["status"] == "playing":
            return r["id"]
    return None


def players_for(conn, current: dict, game: str, kind: str = "race") -> list[dict]:
    """Everyone else in the household with whether they can be invited to `game` now, and why not."""
    out = []
    cutoff = (config.utcnow() - timedelta(seconds=ACTIVE_WITHIN)).isoformat(timespec="seconds")
    for r in conn.execute("SELECT * FROM users WHERE id != ? ORDER BY name COLLATE NOCASE", (current["id"],)):
        reason = why_not(conn, r, game, kind=kind)
        out.append({"id": r["id"], "name": r["name"], "canPlay": reason is None, "reason": reason,
                    "active": bool(r["last_seen"] and r["last_seen"] >= cutoff)})
    # the ones who can play first
    out.sort(key=lambda p: (not p["canPlay"], not p["active"], p["name"].lower()))
    return out


# ---------------------------------------------------------------------------
# Matches
# ---------------------------------------------------------------------------

def _ts(dt: datetime) -> str:
    return dt.isoformat(timespec="milliseconds")


def get(conn, match_id: str):
    return conn.execute("SELECT * FROM matches WHERE id = ?", (match_id,)).fetchone()


def _players(conn, match_id: str) -> list:
    return conn.execute("SELECT p.*, u.name, u.username FROM match_players p JOIN users u ON u.id = p.user_id "
                        "WHERE p.match_id = ? ORDER BY p.seat", (match_id,)).fetchall()


def is_player(conn, match_id: str, user_id: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM match_players WHERE match_id = ? AND user_id = ?",
                             (match_id, user_id)).fetchone())


def _end(conn, m, status: str, reason: str | None, winner: str | None = None, results: dict | None = None) -> None:
    conn.execute("UPDATE matches SET status = ?, end_reason = ?, winner = ?, ended_at = ? WHERE id = ?",
                 (status, reason, winner, config.now_iso(), m["id"]))
    for uid, res in (results or {}).items():
        conn.execute("UPDATE match_players SET result = ? WHERE match_id = ? AND user_id = ?", (res, m["id"], uid))
    touch(m["id"])
    if m["kind"] == "live":                # both phones hear it on the live link
        from . import live
        seat = None
        if winner:
            seat = next((p["seat"] for p in _players(conn, m["id"]) if p["user_id"] == winner), None)
        elif results:
            seat = 0                       # a draw
        live.on_end(m["id"], reason, seat)


def _gone(conn, m, p) -> bool:
    """A player who hasn't finished and isn't coming back: no session long after the start, or a session that
    was ended (left the page, another game started) or went stale without a score."""
    if p["finished_at"]:
        return False
    started = config.parse_ts(m["started_at"]) if m["started_at"] else None
    if not p["session_id"]:
        return bool(started and (config.utcnow() - started).total_seconds() > GONE_AFTER)
    s = conn.execute("SELECT ended_at, scored, last_beat_at FROM play_sessions WHERE id = ?",
                     (p["session_id"],)).fetchone()
    if not s:
        return True
    if s["ended_at"]:
        return True
    beat = config.parse_ts(s["last_beat_at"])
    return bool(beat and (config.utcnow() - beat).total_seconds() > GONE_AFTER)


def settle(conn, match_id: str):
    """Bring a match up to date: an invite that ran out, results that are all in, a player who left, a match
    nobody finished. Returns the (fresh) match row, or None."""
    m = get(conn, match_id)
    if not m:
        return None
    now = config.utcnow()
    if m["status"] == "invited":
        exp = config.parse_ts(m["expires_at"]) if m["expires_at"] else None
        if exp and now >= exp:
            if m["kind"] == "turns":
                from . import turns
                if turns.can_start_now(conn, m):          # those who joined in the 7 days play
                    turns.start(conn, m)
                    return get(conn, match_id)
            _end(conn, m, "expired", None)
            return get(conn, match_id)
        return m
    if m["status"] != "playing":
        return m
    if m["kind"] == "live":
        return _settle_live(conn, m)
    if m["kind"] == "turns":
        from . import turns
        turns.timeouts(conn, m)                           # 7 days without a move
        return get(conn, match_id)
    ps = _players(conn, match_id)
    done = [p for p in ps if p["finished_at"]]
    if len(done) == len(ps):
        winner, results = decide(m["game"], [dict(p) for p in ps])
        _end(conn, m, "done", "finished", winner, results)
        return get(conn, match_id)
    started = config.parse_ts(m["started_at"]) if m["started_at"] else None
    gone = [p for p in ps if _gone(conn, m, p)]
    if gone and len(gone) + len(done) == len(ps):
        if done:                                    # the one still there wins
            winner = done[0]["user_id"]
            results = {p["user_id"]: ("won" if p["user_id"] == winner else "lost") for p in ps}
            _end(conn, m, "done", "left", winner, results)
        else:                                       # both left: nothing to decide
            _end(conn, m, "done", "left", None, None)
        return get(conn, match_id)
    if started and (now - started).total_seconds() > MATCH_MAX_SECONDS:
        _end(conn, m, "done", "timeout", None, None)
        return get(conn, match_id)
    return m


def _settle_live(conn, m):
    """A live duel: both results in and the same → its result; different → out of step. The room's own ends (a
    phone that went away, checksums that differ) are written here too; no room at all means the app restarted
    in the middle (the game can't go on: no result)."""
    from . import live
    mid = m["id"]
    ps = _players(conn, mid)
    done = [p for p in ps if p["finished_at"]]
    reports = live.REPORTS.get(mid, {})
    if done and (len(done) == len(ps) or _older_than(done[0]["finished_at"], REPORT_GRACE)):
        mine = {p["seat"]: reports.get(p["seat"]) for p in done}
        first = next(iter(mine.values()))
        same = first is not None and all(r == first for r in mine.values())
        honest = same and all(first["scores"][p["seat"] - 1] == p["score"] for p in done)
        if honest:
            end_live(conn, mid, "finished", first["winner"])
        else:
            logger.warning("Live match %s (%s): the two results differ: %s", mid, m["game"], mine)
            end_live(conn, mid, "out_of_step", None)
        return get(conn, mid)
    room = live.get(mid)
    if room is None:
        end_live(conn, mid, "timeout", None)
        return get(conn, mid)
    room.check()
    ended = room.take_end()
    if ended:
        end_live(conn, mid, *ended)
        return get(conn, mid)
    started = config.parse_ts(m["started_at"]) if m["started_at"] else None
    if started and (config.utcnow() - started).total_seconds() > MATCH_MAX_SECONDS:
        end_live(conn, mid, "timeout", None)
    return get(conn, mid)


def _older_than(ts: str | None, seconds: float) -> bool:
    t = config.parse_ts(ts) if ts else None
    return bool(t and (config.utcnow() - t).total_seconds() > seconds)


def end_live(conn, match_id: str, reason: str, winner_seat) -> None:
    """Write the end of a live duel: `winner_seat` 1 or 2 wins, 0 is a draw, None no result (out of step, nobody
    there). Left / resigned / finished name a winner; a child out of time is a draw for both (SPEC §13.1)."""
    m = get(conn, match_id)
    if not m or m["status"] != "playing":
        return
    ps = _players(conn, match_id)
    by_seat = {p["seat"]: p["user_id"] for p in ps}
    winner, results = None, None
    if winner_seat in (1, 2) and winner_seat in by_seat:
        winner = by_seat[winner_seat]
        results = {uid: ("won" if uid == winner else "lost") for uid in by_seat.values()}
    elif winner_seat == 0:
        results = {uid: "draw" for uid in by_seat.values()}
    _end(conn, m, "done", reason, winner, results)


def store_shots(conn, match_id: str, shots: list) -> None:
    """The shots of a match whose players take turns live (Carrom, SPEC §13.6), as the room accepted them."""
    for sh in shots:
        conn.execute("INSERT OR IGNORE INTO match_moves (match_id, number, seat, move, dice, at) VALUES (?, ?, ?, ?, NULL, ?)",
                     (match_id, sh["k"], sh["seat"], json.dumps({"shot": sh["value"], "timer": bool(sh["timer"])}),
                      config.now_iso()))


def resign(conn, match_id: str, current: dict):
    """Giving up a live duel (or leaving its page): the other player wins. Turn by turn: turns.resign."""
    m = _need(conn, match_id, current)
    if m["kind"] == "turns":
        from . import turns
        try:
            return turns.resign(conn, match_id, current)
        except turns.TurnsError as e:
            raise TogetherError(e.status, str(e))
    if m["kind"] != "live" or m["status"] != "playing":
        raise TogetherError(409, "That match isn't being played live.")
    seat = next(p["seat"] for p in _players(conn, match_id) if p["user_id"] == current["id"])
    end_live(conn, match_id, "resigned", 3 - seat)
    return get(conn, match_id)


def live_watch(match_id: str) -> None:
    """Every few seconds during a live duel: a child whose play time ran out (or whose quiet hours began) ends it
    for both, as a draw (SPEC §13.1); the room's own ends are written."""
    from . import live
    with db.get_conn() as conn:
        m = get(conn, match_id)
        if not m or m["kind"] != "live" or m["status"] != "playing":
            return
        room = live.get(match_id)
        if room is not None and room.phase in ("running", "paused", "countin"):
            for p in conn.execute("SELECT u.* FROM match_players p JOIN users u ON u.id = p.user_id "
                                  "WHERE p.match_id = ?", (match_id,)).fetchall():
                st = _user_status(conn, p)
                if st["isChild"] and not st["canStart"]:
                    end_live(conn, match_id, "time_limit", 0)
                    return
        settle(conn, match_id)


def create(conn, current: dict, body) -> dict:
    """An invite: {game, mode, kind?, opponents: [id], practice?, rematchOf?}. Returns the new match row."""
    if not isinstance(body, dict):
        raise TogetherError(422, "Send {game, mode, opponents, practice}.")
    game, mode = body.get("game"), body.get("mode")
    practice, kind = body.get("practice", False), body.get("kind")
    opponents, rematch = body.get("opponents"), body.get("rematchOf")
    if not isinstance(game, str) or not games.exists(game):
        raise TogetherError(404, "Unknown game.")
    if kind is None:                       # the mode says it: a live mode is a live duel, a turn mode turn by turn
        kind = kind_for(game, mode)
    if kind not in KINDS:
        raise TogetherError(422, "kind must be race, live or turns.")
    if kind == "race" and not race_ok(game):
        raise TogetherError(409, f"{games.name(game)} can't be raced yet.")
    if kind == "live" and not live_ok(game):
        raise TogetherError(409, f"{games.name(game)} can't be played live on two phones.")
    if kind == "turns" and not turns_ok(game):
        raise TogetherError(409, f"{games.name(game)} can't be played turn by turn.")
    if not settings.game_enabled(game):
        raise TogetherError(409, f"{games.name(game)} is switched off on App settings.")
    if not isinstance(mode, str) or not settings.mode_allowed(game, mode):
        raise TogetherError(422, "That mode isn't available.")
    if kind == "live" and not games.is_live(game, mode):
        raise TogetherError(422, "Pick one of the game's two-phone modes for a live duel.")
    if kind == "race" and games.is_live(game, mode):
        raise TogetherError(422, "That mode is played live on two phones, not raced.")
    if kind == "turns" and not games.is_turns(game, mode):
        raise TogetherError(422, "Pick one of the game's two-phone modes to play turn by turn.")
    if kind != "turns" and games.is_turns(game, mode):
        raise TogetherError(422, "That mode is played turn by turn.")
    if not isinstance(practice, bool):
        raise TogetherError(422, "practice must be true or false.")
    if rematch is not None and not isinstance(rematch, str):
        raise TogetherError(422, "rematchOf must be a match id.")
    most = 1
    if kind == "turns":
        from . import turns
        most = turns.players_range(game)[1] - 1
    if not isinstance(opponents, list) or not 1 <= len(opponents) <= most \
            or not all(isinstance(o, str) for o in opponents) or len(set(opponents)) != len(opponents):
        raise TogetherError(422, "Pick one person to play with." if most == 1 else f"Pick one to {most} people to play with.")
    if current["id"] in opponents:
        raise TogetherError(422, "Pick someone else to play with.")
    others = []
    for other_id in opponents:
        other = conn.execute("SELECT * FROM users WHERE id = ?", (other_id,)).fetchone()
        if not other:
            raise TogetherError(404, "That person isn't known to the app yet (they need to open it once).")
        others.append(other)
    me = conn.execute("SELECT * FROM users WHERE id = ?", (current["id"],)).fetchone()
    mine = why_not(conn, me, game, who="me", kind=kind)
    if mine:
        raise TogetherError(409, mine)
    for other in others:
        theirs = why_not(conn, other, game, kind=kind)
        if theirs:
            raise TogetherError(409, f"{other['name']} can't play right now: {theirs[0].lower()}{theirs[1:]}")
    # one live invite out at a time (turn-by-turn invites last days, and several can be out)
    if kind != "turns":
        for r in conn.execute("SELECT id FROM matches WHERE created_by = ? AND status = 'invited' AND kind != 'turns'",
                              (current["id"],)).fetchall():
            m = settle(conn, r["id"])
            if m and m["status"] == "invited":
                raise TogetherError(409, "You already have an invite out. Cancel it first, or wait for an answer.")
    match_options = None
    if kind == "turns":
        from . import rules, turns
        match_options = json.dumps(rules.options_for(rules.get(game), body.get("options")))
    set_id = levels.mode_info(game, mode)["set"]
    level_list = levels.playable(conn, set_id) if set_id else None
    now = config.utcnow()
    mid = db.new_id()
    lasts = INVITE_SECONDS
    if kind == "turns":
        lasts = turns.INVITE_SECONDS
    conn.execute(
        "INSERT INTO matches (id, game, mode, kind, seed, levels, level_count, practice, status, created_by, "
        "created_at, expires_at, rematch_of, options) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'invited', ?, ?, ?, ?, ?)",
        (mid, game, mode, kind, secrets.randbelow(2_147_483_646) + 1,
         json.dumps(level_list) if level_list is not None else None,
         len(level_list) if level_list is not None else None, 1 if practice else 0, current["id"],
         config.now_iso(), _ts(now + timedelta(seconds=lasts)), rematch, match_options))
    conn.execute("INSERT INTO match_players (match_id, user_id, seat, invite_status) VALUES (?, ?, 1, 'accepted')",
                 (mid, current["id"]))
    for seat, other in enumerate(others, start=2):
        conn.execute("INSERT INTO match_players (match_id, user_id, seat, invite_status) VALUES (?, ?, ?, 'invited')",
                     (mid, other["id"], seat))
    touch(mid)
    return get(conn, mid)


def _need(conn, match_id: str, current: dict):
    m = settle(conn, match_id)
    if not m or not is_player(conn, match_id, current["id"]):
        raise TogetherError(404, "That match isn't known.")
    return m


def accept(conn, match_id: str, current: dict):
    m = _need(conn, match_id, current)
    p = conn.execute("SELECT * FROM match_players WHERE match_id = ? AND user_id = ?",
                     (match_id, current["id"])).fetchone()
    if m["created_by"] == current["id"] or p["invite_status"] != "invited":
        raise TogetherError(409, "That invite isn't waiting for you.")
    if m["status"] == "expired":
        raise TogetherError(409, "That invite has run out. Ask for a new one.")
    if m["status"] != "invited":
        raise TogetherError(409, "That invite is no longer open.")
    me = conn.execute("SELECT * FROM users WHERE id = ?", (current["id"],)).fetchone()
    reason = why_not(conn, me, m["game"], who="me", kind=m["kind"])
    if reason:
        raise TogetherError(409, reason)
    if m["kind"] == "turns":               # everyone in: the game starts; else it waits for the others (or a Start)
        from . import turns
        conn.execute("UPDATE match_players SET invite_status = 'accepted' WHERE match_id = ? AND user_id = ?",
                     (match_id, current["id"]))
        waiting = conn.execute("SELECT COUNT(*) FROM match_players WHERE match_id = ? AND invite_status = 'invited'",
                               (match_id,)).fetchone()[0]
        if not waiting:
            turns.start(conn, get(conn, match_id), actor=current["id"])
        touch(match_id)
        return get(conn, match_id)
    start = config.utcnow() + timedelta(seconds=COUNT_IN_SECONDS)
    conn.execute("UPDATE match_players SET invite_status = 'accepted' WHERE match_id = ? AND user_id = ?",
                 (match_id, current["id"]))
    conn.execute("UPDATE matches SET status = 'playing', started_at = ? WHERE id = ?", (_ts(start), match_id))
    LIVE.pop(match_id, None)
    if m["kind"] == "live":                # the relay for the two phones; play starts when both are ready
        from . import live
        seats = {p["user_id"]: p["seat"] for p in _players(conn, match_id)}
        live.open_room(match_id, m["game"], games.live_actions(m["game"]), seats,
                       turns=games.live_turns(m["game"]), first=int(m["seed"]) % 2 + 1)
    touch(match_id)
    return get(conn, match_id)


def decline(conn, match_id: str, current: dict):
    m = _need(conn, match_id, current)
    if m["created_by"] == current["id"] or m["status"] != "invited":
        raise TogetherError(409, "That invite isn't waiting for you.")
    conn.execute("UPDATE match_players SET invite_status = 'declined' WHERE match_id = ? AND user_id = ?",
                 (match_id, current["id"]))
    if m["kind"] == "turns":               # 3–4 players: the others may still join, or play without them
        from . import turns
        waiting = conn.execute("SELECT COUNT(*) FROM match_players WHERE match_id = ? AND invite_status = 'invited'",
                               (match_id,)).fetchone()[0]
        if waiting:
            touch(match_id)
            return get(conn, match_id)
        if turns.can_start_now(conn, m):
            turns.start(conn, m)
            return get(conn, match_id)
    _end(conn, m, "declined", None)
    return get(conn, match_id)


def start_now(conn, match_id: str, current: dict):
    """The inviter starts a turn-by-turn match with those who have joined (2–4 players; the rest's invites end)."""
    from . import turns
    m = _need(conn, match_id, current)
    if m["created_by"] != current["id"]:
        raise TogetherError(409, "Only the one who sent the invite can start it.")
    if m["kind"] != "turns" or m["status"] != "invited":
        raise TogetherError(409, "That invite is no longer open.")
    if not turns.can_start_now(conn, m):
        lo = turns.players_range(m["game"])[0]
        raise TogetherError(409, f"Wait until at least {lo - 1} {'person has' if lo == 2 else 'people have'} joined.")
    turns.start(conn, m, actor=current["id"])
    return get(conn, match_id)


def cancel(conn, match_id: str, current: dict):
    m = _need(conn, match_id, current)
    if m["created_by"] != current["id"]:
        raise TogetherError(409, "Only the one who sent the invite can cancel it.")
    if m["status"] != "invited":
        raise TogetherError(409, "That invite is no longer open.")
    _end(conn, m, "cancelled", None)
    return get(conn, match_id)


def for_session(conn, current: dict, match_id, game) -> dict:
    """Checks for starting a play session in a match; returns {match, seat}. Raises TogetherError."""
    if not isinstance(match_id, str):
        raise TogetherError(422, "matchId must be a match id.")
    m = settle(conn, match_id)
    if not m or not is_player(conn, match_id, current["id"]):
        raise TogetherError(404, "That match isn't known.")
    if m["game"] != game:
        raise TogetherError(422, "That match is for another game.")
    if m["status"] != "playing":
        raise TogetherError(409, "That match isn't being played.")
    p = conn.execute("SELECT * FROM match_players WHERE match_id = ? AND user_id = ?",
                     (match_id, current["id"])).fetchone()
    if m["kind"] == "turns":               # every visit to the match's page is a session (its time is play time)
        if p["left_at"]:
            raise TogetherError(409, "You've left that match.")
        return {"match": m, "levels": None, "seat": p["seat"]}
    if p["session_id"] or p["finished_at"]:
        raise TogetherError(409, "You've already started that match.")
    return {"match": m, "levels": json.loads(m["levels"]) if m["levels"] else None, "seat": p["seat"]}


def attach_session(conn, match_id: str, user_id: str, session_id: str) -> None:
    conn.execute("UPDATE match_players SET session_id = ? WHERE match_id = ? AND user_id = ?",
                 (session_id, match_id, user_id))
    touch(match_id)


def record_result(conn, session, score, level, seconds, won=None, report=None) -> None:
    """A player's final result, from the score they sent for their match session (0 if it wasn't a possible score).
    A live duel's phone sends the end of the game as it saw it with it (`report`, compared in _settle_live)."""
    mid = session["match_id"]
    if not mid:
        return
    m = get(conn, mid)
    if m and m["kind"] == "live":
        from . import live
        seat = conn.execute("SELECT seat FROM match_players WHERE match_id = ? AND user_id = ?",
                            (mid, session["user_id"])).fetchone()
        if seat:
            live.store_report(mid, seat["seat"], report)
    ok = isinstance(score, (int, float)) and not isinstance(score, bool) and score == score
    conn.execute(
        "UPDATE match_players SET score = ?, level = ?, seconds = ?, won = ?, finished_at = ? "
        "WHERE match_id = ? AND user_id = ? AND finished_at IS NULL",
        (int(score) if ok else 0, int(level) if isinstance(level, (int, float)) and not isinstance(level, bool) else 1,
         int(round(seconds)) if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) else 0,
         1 if won is True else 0, config.now_iso(), mid, session["user_id"]))
    st = _player_live(mid, session["user_id"])
    st["over"] = True
    st["seen"] = _mono()
    settle(conn, mid)
    touch(mid)


# ---------------------------------------------------------------------------
# What a phone sees
# ---------------------------------------------------------------------------

def view(conn, m, user_id: str) -> dict:
    lv = LIVE.get(m["id"], {"players": {}})
    now = _mono()
    ps = []
    for p in _players(conn, m["id"]):
        live = lv["players"].get(p["user_id"], {})
        seen = live.get("seen")
        final = bool(p["finished_at"])
        ps.append({
            "id": p["user_id"], "name": p["name"], "seat": p["seat"], "you": p["user_id"] == user_id,
            "invite": p["invite_status"],
            "score": p["score"] if final else live.get("score", 0),
            "level": p["level"] if final else live.get("level", 1),
            "over": final or bool(live.get("over")), "final": final, "paused": bool(live.get("paused")) and not final,
            "connected": seen is not None and now - seen < CONNECTED_WITHIN,
            "seconds": p["seconds"], "won": bool(p["won"]) if final else None, "result": p["result"],
            "started": bool(p["session_id"]),
            "computer": bool(p["computer"]), "left": bool(p["left_at"]) if "left_at" in p.keys() else False,
        })
    starts = None
    if m["status"] == "playing" and m["started_at"] and m["kind"] != "live":     # a live duel counts in from `start`
        starts = max(0, int((config.parse_ts(m["started_at"]) - config.utcnow()).total_seconds() * 1000))
    live_pic = None
    if m["kind"] == "live":
        from . import live
        room = live.get(m["id"])
        live_pic = room.picture() if room is not None else None
    expires = None
    if m["status"] == "invited" and m["expires_at"]:
        expires = max(0, int((config.parse_ts(m["expires_at"]) - config.utcnow()).total_seconds()))
    return {
        "id": m["id"], "game": m["game"], "gameName": games.name(m["game"]), "icon": games.GAMES[m["game"]]["icon"],
        "mode": m["mode"], "modeLabel": games.mode_label(m["game"], m["mode"]), "kind": m["kind"],
        "status": m["status"], "practice": bool(m["practice"]), "createdBy": m["created_by"],
        "mine": m["created_by"] == user_id, "createdAt": m["created_at"], "expiresIn": expires,
        "startsInMs": starts, "endReason": m["end_reason"], "winner": m["winner"], "rematchOf": m["rematch_of"],
        "rule": (race_rule(m["game"]) or {}).get("rule") if m["kind"] == "race" else None,
        "live": live_pic,
        "turns": _turns_brief(conn, m, user_id) if m["kind"] == "turns" else None,
        "v": signature(m["id"]), "players": ps,
    }


def _turns_brief(conn, m, user_id: str) -> dict:
    from . import turns
    return turns.brief(conn, m, user_id)


def snapshot(match_id: str, user_id: str) -> dict | None:
    """The match as `user_id` sees it (settled first), or None if they aren't in it."""
    with db.get_conn() as conn:
        if not is_player(conn, match_id, user_id):
            return None
        m = settle(conn, match_id)
        return view(conn, m, user_id) if m else None


def mine(conn, user_id: str) -> dict:
    """The Games page: invites waiting for me, the one I sent, the matches I'm in, and the last few results."""
    out = {"waiting": [], "sent": [], "playing": [], "recent": []}
    ids = [r["match_id"] for r in conn.execute("SELECT match_id FROM match_players WHERE user_id = ?", (user_id,))]
    for mid in ids:
        settle(conn, mid)
    rows = conn.execute(
        "SELECT m.* FROM matches m JOIN match_players p ON p.match_id = m.id WHERE p.user_id = ? "
        "ORDER BY m.created_at DESC", (user_id,)).fetchall()
    for m in rows:
        if m["status"] == "invited":
            mine_p = conn.execute("SELECT invite_status FROM match_players WHERE match_id = ? AND user_id = ?",
                                  (m["id"], user_id)).fetchone()
            if m["created_by"] == user_id or (mine_p and mine_p["invite_status"] == "accepted"):
                out["sent"].append(view(conn, m, user_id))          # mine, or joined and waiting for the others
            elif mine_p and mine_p["invite_status"] == "invited":
                out["waiting"].append(view(conn, m, user_id))
        elif m["status"] == "playing":
            me = next(p for p in _players(conn, m["id"]) if p["user_id"] == user_id)
            if not me["finished_at"]:
                out["playing"].append(view(conn, m, user_id))
        elif m["status"] == "done" and len(out["recent"]) < RECENT_N:
            out["recent"].append(view(conn, m, user_id))
    return out


def can_use(conn, user_id: str, match_id: str) -> bool:
    return is_player(conn, match_id, user_id)


# ---------------------------------------------------------------------------
# Head to head (My scores → Against others)
# ---------------------------------------------------------------------------

def against(conn, user_id: str) -> list[dict]:
    """For each person played: games, wins, losses, draws, and the same per game. Finished matches that weren't
    Practice, where both phones played (a match one player never started doesn't count)."""
    rows = conn.execute(
        "SELECT m.game, me.result AS mine, o.user_id AS other_id, u.name AS other_name "
        "FROM matches m JOIN match_players me ON me.match_id = m.id AND me.user_id = ? "
        "JOIN match_players o ON o.match_id = m.id AND o.user_id != me.user_id "
        "JOIN users u ON u.id = o.user_id "
        "WHERE m.status = 'done' AND m.practice = 0 AND me.result IS NOT NULL ORDER BY m.ended_at", (user_id,)).fetchall()
    people: dict[str, dict] = {}
    for r in rows:
        p = people.setdefault(r["other_id"], {"id": r["other_id"], "name": r["other_name"], "games": 0, "wins": 0,
                                             "losses": 0, "draws": 0, "perGame": {}})
        g = p["perGame"].setdefault(r["game"], {"game": r["game"], "name": games.name(r["game"]), "games": 0,
                                               "wins": 0, "losses": 0, "draws": 0})
        key = {"won": "wins", "lost": "losses", "draw": "draws"}[r["mine"]]
        for d in (p, g):
            d["games"] += 1
            d[key] += 1
    out = []
    for p in people.values():
        p["perGame"] = sorted(p["perGame"].values(), key=lambda g: (-g["games"], g["name"]))
        out.append(p)
    out.sort(key=lambda p: (-p["games"], p["name"].lower()))
    return out


# ---------------------------------------------------------------------------
# Notification
# ---------------------------------------------------------------------------

def invite_blocking(match_id: str) -> int:
    """The phone notification for an invite (App setting "Invites by phone notification"; the person's own
    "Receive notifications"): "Asha challenges you to Lane Racer", with Join and Not now when the app knows its
    panel path. Blocking; never while holding a DB connection. Returns how many phones it reached."""
    if not settings.get("notify_invites"):
        return 0
    with db.get_conn() as conn:
        m = get(conn, match_id)
        if not m or m["status"] != "invited":
            return 0
        inviter = conn.execute("SELECT name FROM users WHERE id = ?", (m["created_by"],)).fetchone()
        targets = conn.execute(
            "SELECT u.id, u.name, u.username, u.disabled, u.receive_notifications FROM match_players p "
            "JOIN users u ON u.id = p.user_id WHERE p.match_id = ? AND p.user_id != ? AND p.invite_status = 'invited'",
            (match_id, m["created_by"])).fetchall()
        targets = [t for t in targets if not t["disabled"] and t["receive_notifications"]]
        if not inviter or not targets:
            return 0
        services = [ha_notify.services_for(dict(t), conn) for t in targets]
    verb = "wants a rematch in" if m["rematch_of"] else "challenges you to"
    message = f"{inviter['name']} {verb} {games.name(m['game'])}"
    message += " (Practice, nothing is saved)." if m["practice"] else "."
    data = {"tag": f"arcade-invite-{match_id}"}
    join, later = notify._link(f"#/home/join/{match_id}"), notify._link(f"#/home/decline/{match_id}")
    if join:
        data.update(join)
        data["actions"] = [{"action": "URI", "title": "Join", "uri": join["url"]},
                           {"action": "URI", "title": "Not now", "uri": later["url"]}]
    reached = 0
    for svc in services:
        results = ha_notify.send_to_services(svc, notify.TITLE, message, data)
        reached += sum(1 for ok in results.values() if ok)
    return reached


# ---------------------------------------------------------------------------
# Housekeeping
# ---------------------------------------------------------------------------

def housekeeping(conn) -> int:
    """Expire invites, settle matches that were left, forget live numbers of matches that are over."""
    n = 0
    for r in conn.execute("SELECT id, status FROM matches WHERE status IN ('invited', 'playing')").fetchall():
        m = settle(conn, r["id"])
        if m and m["status"] != r["status"]:
            n += 1
    from . import live
    live.cleanup()
    for mid in list(LIVE):
        m = get(conn, mid)
        if not m:
            LIVE.pop(mid, None)
        elif m["status"] in TERMINAL and m["ended_at"]:
            ended = config.parse_ts(m["ended_at"])
            if ended and (config.utcnow() - ended).total_seconds() > 600:
                LIVE.pop(mid, None)
    return n


def prune(conn, cutoff_iso: str) -> int:
    """Keep scores for N years: finished matches older than the cutoff go too."""
    return conn.execute("DELETE FROM matches WHERE status IN ('done', 'declined', 'expired', 'cancelled') "
                        "AND COALESCE(ended_at, created_at) < ?", (cutoff_iso,)).rowcount
