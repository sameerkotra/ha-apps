"""Ludo for 2–4 players (the rule set this app plays, SPEC §13.5 / spec/GAMES.md):

- The usual cross-shaped board: a track of 52 squares around it, a yard in each corner, a home column of 5 squares
  for each colour and the home in the middle. Each player has 4 tokens of one colour (2 players: opposite corners,
  red and yellow; 3: red, green and yellow; 4: red, green, yellow and blue). Who rolls first comes from the seed.
- The server rolls the die (1–6) for the player whose move it is; nobody can choose a roll.
- A token leaves the yard only with a 6, onto its colour's start square. A token moves the number rolled along the
  track (clockwise), then up its own home column; it reaches home only with the exact roll (no overshooting).
- Landing on a square held by other colours' tokens sends all of them back to their yards — except on the eight
  safe squares (the four start squares and the four star squares, eight squares after each start), where tokens of
  any colours may share. A player's own tokens may share any square.
- A 6 gives another roll; a third 6 in a row loses the turn (that roll isn't played). A roll that no token can use
  is passed (a pass after a 6 still gives the next roll).
- The first player with all four tokens home wins (the game ends there; the others are placed by tokens home, then
  by how far their tokens have come).

A token's place is its progress from its own start: −1 in the yard, 0–50 along the track (0 = the start square, 50 =
the square before it), 51–55 its home column, 56 home. A move is {"token": 0–3} or {"pass": true} (only when no
token can use the roll, or for a third 6). Mirrors static/games/ludo-logic.js."""
from __future__ import annotations

from .base import Illegal, canonical, copy, margin_score, need_turn, whole

PLAYERS = (2, 4)
OPTIONS: dict = {}
DICE = 6
BASE = 500
BONUS_TOKEN = 25              # each of the others' tokens not home, at a win (at most the base again)
TRACK = 52
LAST_TRACK = 50               # the square before a token's own start
FINISH = 56                   # home
SAFE = (0, 8, 13, 21, 26, 34, 39, 47)
COLOURS = {2: [0, 2], 3: [0, 1, 2], 4: [0, 1, 2, 3]}


def new(seed: int, players: int = 2, options=None) -> dict:
    n = max(2, min(4, int(players)))
    return {"g": "ludo", "n": n, "cols": list(COLOURS[n]), "turn": int(seed) % n, "tokens": [[-1] * 4 for _ in range(n)],
            "sixes": 0, "over": False, "winner": None}


def to_move(st: dict) -> list[int]:
    return [] if st["over"] else [st["turn"]]


def square(st: dict, seat: int, p: int) -> int:
    """The track square (0–51, red's start = 0) of a token of `seat` at progress p (0–50)."""
    return (13 * st["cols"][seat] + p) % TRACK


def _roll(st: dict, seat: int) -> int:
    r = st.get("_roll")
    if not r or r.get("seat") != seat + 1 or not whole(r.get("value"), 1, 6):
        raise Illegal("Roll the dice first.")
    return r["value"]


def legal_for(st: dict, seat: int, r: int) -> list[dict]:
    if st["sixes"] == 2 and r == 6:                  # a third 6: the turn is lost
        return [{"pass": True}]
    out = []
    for i, p in enumerate(st["tokens"][seat]):
        if p == -1:
            if r == 6:
                out.append({"token": i})
        elif p < FINISH and p + r <= FINISH:
            out.append({"token": i})
    return out or [{"pass": True}]


def moves(st: dict, seat: int) -> list[dict]:
    if st["over"] or seat != st["turn"]:
        return []
    r = st.get("_roll")
    if not r or r.get("seat") != seat + 1:
        return []
    return legal_for(st, seat, r["value"])


def forced(st: dict, seat: int):
    """The move to play at once when the roll leaves no choice (a pass, or tokens that would all end up the same)."""
    try:
        r = _roll(st, seat)
    except Illegal:
        return None
    ms = legal_for(st, seat, r)
    if len(ms) == 1:
        return ms[0]
    places = {st["tokens"][seat][m["token"]] for m in ms}
    return ms[0] if len(places) == 1 else None


def apply(st: dict, seat: int, move) -> tuple[dict, dict]:
    need_turn(st, seat)
    r = _roll(st, seat)
    if not isinstance(move, dict) or not (set(move) == {"token"} and whole(move["token"], 0, 3)
                                          or move == {"pass": True}):
        raise Illegal("A move is {token: 0–3} (or a pass when no token can move).")
    want = canonical(move)
    if not any(canonical(m) == want for m in legal_for(st, seat, r)):
        raise Illegal("That token can't move with this roll." if "token" in move else "A token can move — pick one.")
    s = copy(st)
    s.pop("_roll", None)
    n = s["n"]
    if "pass" in move:
        if r == 6 and s["sixes"] < 2:
            s["sixes"] += 1
        else:
            s["sixes"], s["turn"] = 0, (seat + 1) % n
        return s, {"pass": True}
    i = move["token"]
    p = s["tokens"][seat][i]
    np = 0 if p == -1 else p + r
    s["tokens"][seat][i] = np
    if np <= LAST_TRACK:
        sq = square(s, seat, np)
        if sq not in SAFE:
            for o in range(n):
                if o == seat:
                    continue
                for j, q in enumerate(s["tokens"][o]):
                    if 0 <= q <= LAST_TRACK and square(s, o, q) == sq:
                        s["tokens"][o][j] = -1
    if all(t == FINISH for t in s["tokens"][seat]):
        s["over"], s["winner"] = True, seat
    elif r == 6:
        s["sixes"] += 1
    else:
        s["sixes"], s["turn"] = 0, (seat + 1) % n
    return s, {"token": i}


def progress(st: dict, seat: int) -> tuple[int, int]:
    toks = st["tokens"][seat]
    return sum(1 for t in toks if t == FINISH), sum(t + 1 for t in toks)


def result(st: dict):
    if not st["over"]:
        return None
    w = st["winner"]
    others = sorted((s for s in range(st["n"]) if s != w), key=lambda s: (-progress(st, s)[0], -progress(st, s)[1], s))
    return {"winner": w, "places": [w] + others}


def score(st: dict, seat: int) -> int:
    left = sum(1 for o in range(st["n"]) if o != seat for t in st["tokens"][o] if t != FINISH)
    return margin_score(st["winner"] == seat, False, BASE, BONUS_TOKEN * left)


def view(st: dict, seat: int) -> dict:
    return digest(st)


def visible(record: dict, seat: int, mover: int) -> dict:
    return record


def digest(st: dict) -> dict:
    return {"turn": st["turn"], "tokens": st["tokens"], "sixes": st["sixes"], "over": st["over"], "winner": st["winner"]}


def computer(st: dict, seat: int, rnd) -> dict:
    """A stand-in's move: capture, then get home, then out of the yard, then onto a safe square, then the token
    furthest along."""
    r = _roll(st, seat)
    ms = legal_for(st, seat, r)
    if len(ms) == 1:
        return ms[0]

    def value(m):
        p = st["tokens"][seat][m["token"]]
        np = 0 if p == -1 else p + r
        v = np / 100
        if np == FINISH:
            v += 80
        elif p == -1:
            v += 60
        if np <= LAST_TRACK:
            sq = square(st, seat, np)
            if sq in SAFE:
                v += 20
            else:
                for o in range(st["n"]):
                    if o != seat and any(0 <= q <= LAST_TRACK and square(st, o, q) == sq for q in st["tokens"][o]):
                        v += 100
        elif p <= LAST_TRACK:
            v += 30                                    # into the home column: safe from now on
        return v
    best = max(value(m) for m in ms)
    top = [m for m in ms if value(m) == best]
    return top[int(rnd.random() * len(top))]
