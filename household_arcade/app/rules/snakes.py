"""Snakes and Ladders for 2–4 players (the rule set this app plays, SPEC §13.5 / spec/GAMES.md):

- A 10 × 10 board numbered 1–100 (boustrophedon: 1 at the bottom left). Everyone starts off the board (0); who rolls
  first comes from the seed; then in seat order.
- The server rolls the die (1–6) for the player whose move it is; the token moves that many squares. Ending on the
  foot of a ladder climbs it; ending on a snake's head slides down to its tail. Several tokens may share a square.
- Reaching 100 wins. The match's option `finish`: "any" (default — a roll that would go past 100 also wins) or
  "exact" (it must land on 100 exactly; a roll that would go past it is lost and the token stays).
- Two boards (`board`): "classic" (9 ladders, 10 snakes) and "gentle" (7 ladders, 6 short snakes), both original.
- No extra rolls. A move is {"go": true}; it always is the only move, so the server plays it as soon as it rolls.

Mirrors static/games/snakes-logic.js."""
from __future__ import annotations

from .base import Illegal, copy, margin_score, need_turn, whole

PLAYERS = (2, 4)
OPTIONS = {"board": (("classic", "gentle"), "classic"), "finish": (("any", "exact"), "any")}
DICE = 6
BASE = 500
BONUS_SQUARE = 5              # each square the nearest other player still has to go, at a win (at most the base)
GOAL = 100

BOARDS = {
    "classic": {
        "ladders": {3: 18, 6: 16, 14: 34, 20: 39, 28: 52, 36: 56, 54: 70, 55: 73, 75: 87},
        "snakes": {26: 17, 38: 23, 58: 45, 62: 41, 77: 64, 79: 57, 89: 72, 94: 66, 96: 85, 99: 83},
    },
    "gentle": {
        "ladders": {4: 24, 9: 30, 23: 43, 39: 64, 41: 59, 51: 68, 70: 90},
        "snakes": {15: 7, 28: 16, 47: 36, 54: 46, 85: 75, 97: 86},
    },
}


def jumps(board: str) -> dict:
    b = BOARDS[board]
    out = dict(b["ladders"])
    out.update(b["snakes"])
    return out


def new(seed: int, players: int = 2, options=None) -> dict:
    n = max(2, min(4, int(players)))
    o = options if isinstance(options, dict) else {}
    board = o.get("board") if o.get("board") in BOARDS else "classic"
    finish = o.get("finish") if o.get("finish") in ("any", "exact") else "any"
    return {"g": "snakes", "n": n, "board": board, "finish": finish, "turn": int(seed) % n, "pos": [0] * n,
            "over": False, "winner": None}


def to_move(st: dict) -> list[int]:
    return [] if st["over"] else [st["turn"]]


def _roll(st: dict, seat: int) -> int:
    r = st.get("_roll")
    if not r or r.get("seat") != seat + 1 or not whole(r.get("value"), 1, 6):
        raise Illegal("Roll the dice first.")
    return r["value"]


def moves(st: dict, seat: int) -> list[dict]:
    if st["over"] or seat != st["turn"]:
        return []
    r = st.get("_roll")
    return [{"go": True}] if r and r.get("seat") == seat + 1 else []


def forced(st: dict, seat: int):
    try:
        _roll(st, seat)
    except Illegal:
        return None
    return {"go": True}


def land(st: dict, pos: int, r: int) -> tuple[int, int]:
    """Where a roll of r from pos ends: (the square reached by counting, the square after a ladder or snake)."""
    np = pos + r
    if np > GOAL:
        np = GOAL if st["finish"] == "any" else pos
    return np, jumps(st["board"]).get(np, np)


def apply(st: dict, seat: int, move) -> tuple[dict, dict]:
    need_turn(st, seat)
    r = _roll(st, seat)
    if not canonical_go(move):
        raise Illegal("A move is {go: true}.")
    s = copy(st)
    s.pop("_roll", None)
    _, to = land(s, s["pos"][seat], r)
    s["pos"][seat] = to
    if to == GOAL:
        s["over"], s["winner"] = True, seat
    else:
        s["turn"] = (seat + 1) % s["n"]
    return s, {"go": True}


def canonical_go(move) -> bool:
    return isinstance(move, dict) and list(move) == ["go"] and move["go"] is True


def result(st: dict):
    if not st["over"]:
        return None
    w = st["winner"]
    others = sorted((s for s in range(st["n"]) if s != w), key=lambda s: (-st["pos"][s], s))
    return {"winner": w, "places": [w] + others}


def score(st: dict, seat: int) -> int:
    others = [st["pos"][o] for o in range(st["n"]) if o != seat]
    behind = GOAL - max(others) if others else 0
    return margin_score(st["winner"] == seat, False, BASE, BONUS_SQUARE * behind)


def view(st: dict, seat: int) -> dict:
    return digest(st)


def visible(record: dict, seat: int, mover: int) -> dict:
    return record


def digest(st: dict) -> dict:
    return {"turn": st["turn"], "pos": st["pos"], "over": st["over"], "winner": st["winner"]}


def computer(st: dict, seat: int, rnd) -> dict:
    return {"go": True}
