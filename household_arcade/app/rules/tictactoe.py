"""Tic-tac-toe: a 3 × 3 grid; players take turns marking an empty square (the one who moves first is ✕). Three in a
row across, down or corner to corner wins; a full grid with no row is a draw.

Squares are numbered 0–8 row by row. A move is {"cell": 0–8}. Mirrors static/games/tictactoe-logic.js."""
from __future__ import annotations

from .base import Illegal, copy, margin_score, need_turn, whole

PLAYERS = (2, 2)
OPTIONS: dict = {}
DICE = 0
BASE = 500
BONUS_CELL = 25               # each empty square left at a win
LINES = ((0, 1, 2), (3, 4, 5), (6, 7, 8), (0, 3, 6), (1, 4, 7), (2, 5, 8), (0, 4, 8), (2, 4, 6))


def new(seed: int, players: int = 2, options=None) -> dict:
    first = int(seed) % 2
    return {"g": "tictactoe", "n": 2, "first": first, "turn": first, "board": [0] * 9, "ply": 0,
            "over": False, "winner": None, "line": [], "last": -1}


def to_move(st: dict) -> list[int]:
    return [] if st["over"] else [st["turn"]]


def moves(st: dict, seat: int) -> list[dict]:
    if st["over"] or seat != st["turn"]:
        return []
    return [{"cell": i} for i in range(9) if st["board"][i] == 0]


def apply(st: dict, seat: int, move) -> tuple[dict, dict]:
    need_turn(st, seat)
    if not isinstance(move, dict) or set(move) != {"cell"} or not whole(move["cell"], 0, 8):
        raise Illegal("Pick a square, 0 to 8.")
    i = move["cell"]
    if st["board"][i] != 0:
        raise Illegal("That square is taken.")
    s = copy(st)
    s["board"][i] = seat + 1
    s["ply"] += 1
    s["last"] = i
    for line in LINES:
        if all(s["board"][k] == seat + 1 for k in line):
            s["over"], s["winner"], s["line"] = True, seat, list(line)
            break
    else:
        if all(v != 0 for v in s["board"]):
            s["over"], s["winner"] = True, -1
        else:
            s["turn"] = 1 - seat
    return s, {"cell": i}


def result(st: dict):
    if not st["over"]:
        return None
    w = st["winner"]
    return {"winner": None if w == -1 else w, "places": [w, 1 - w] if w in (0, 1) else [0, 1]}


def score(st: dict, seat: int) -> int:
    empty = sum(1 for v in st["board"] if v == 0)
    return margin_score(st["winner"] == seat, st["winner"] == -1, BASE, BONUS_CELL * empty)


def view(st: dict, seat: int) -> dict:
    return digest(st)


def visible(record: dict, seat: int, mover: int) -> dict:
    return record


def digest(st: dict) -> dict:
    return {"board": st["board"], "turn": st["turn"], "over": st["over"], "winner": st["winner"], "line": st["line"]}


def computer(st: dict, seat: int, rnd) -> dict:
    ms = moves(st, seat)
    for who in (seat, 1 - seat):
        for m in ms:
            t = copy(st)
            t["turn"] = who
            if apply(t, who, m)[0]["winner"] == who:
                return m
    pref = {4: 0, 0: 1, 2: 1, 6: 1, 8: 1}
    ms.sort(key=lambda m: (pref.get(m["cell"], 2), rnd.random()))
    return ms[0]
