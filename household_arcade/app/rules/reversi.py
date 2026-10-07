"""Reversi: an 8 × 8 board starting with two discs each in the middle (the side that moves first plays dark). A disc
must be placed so that it closes at least one straight line (across, up and down, or slanting) of the other side's
discs between it and another of yours; those discs turn over. A player with no such place passes (automatically);
when neither can place, the game ends and the one with more discs wins (the same number: a draw).

Squares are 0–63 (row × 8 + column). A move is {"cell": 0–63}. Mirrors static/games/reversi-logic.js."""
from __future__ import annotations

from .base import Illegal, copy, margin_score, need_turn, whole

PLAYERS = (2, 2)
OPTIONS: dict = {}
DICE = 0
BASE = 500
BONUS_DISC = 5                # each disc more than the other side's at a win
DIRS = ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1))


def new(seed: int, players: int = 2, options=None) -> dict:
    first = int(seed) % 2
    other = 1 - first
    board = [0] * 64
    board[27], board[36] = other + 1, other + 1          # d4, e5: light
    board[28], board[35] = first + 1, first + 1          # e4, d5: dark
    return {"g": "reversi", "n": 2, "first": first, "turn": first, "board": board, "ply": 0, "passed": -1,
            "over": False, "winner": None, "last": -1}


def to_move(st: dict) -> list[int]:
    return [] if st["over"] else [st["turn"]]


def flips(board: list[int], cell: int, seat: int) -> list[int]:
    if board[cell] != 0:
        return []
    me, out = seat + 1, []
    r0, c0 = divmod(cell, 8)
    for dr, dc in DIRS:
        run, r, c = [], r0 + dr, c0 + dc
        while 0 <= r < 8 and 0 <= c < 8 and board[r * 8 + c] not in (0, me):
            run.append(r * 8 + c)
            r, c = r + dr, c + dc
        if run and 0 <= r < 8 and 0 <= c < 8 and board[r * 8 + c] == me:
            out.extend(run)
    return out


def _cells(board: list[int], seat: int) -> list[int]:
    return [i for i in range(64) if board[i] == 0 and flips(board, i, seat)]


def moves(st: dict, seat: int) -> list[dict]:
    if st["over"] or seat != st["turn"]:
        return []
    return [{"cell": i} for i in _cells(st["board"], seat)]


def apply(st: dict, seat: int, move) -> tuple[dict, dict]:
    need_turn(st, seat)
    if not isinstance(move, dict) or set(move) != {"cell"} or not whole(move["cell"], 0, 63):
        raise Illegal("Pick a square, 0 to 63.")
    cell = move["cell"]
    fl = flips(st["board"], cell, seat)
    if not fl:
        raise Illegal("A disc must turn over at least one of the other side's.")
    s = copy(st)
    b = s["board"]
    b[cell] = seat + 1
    for i in fl:
        b[i] = seat + 1
    s["ply"] += 1
    s["last"] = cell
    s["passed"] = -1
    other = 1 - seat
    if _cells(b, other):
        s["turn"] = other
    elif _cells(b, seat):
        s["turn"] = seat                     # the other has no place: they pass
        s["passed"] = other
    else:
        s["over"] = True
        a, z = b.count(seat + 1), b.count(other + 1)
        s["winner"] = seat if a > z else other if z > a else -1
    return s, {"cell": cell}


def result(st: dict):
    if not st["over"]:
        return None
    w = st["winner"]
    return {"winner": None if w == -1 else w, "places": [w, 1 - w] if w in (0, 1) else [0, 1]}


def discs(st: dict, seat: int) -> int:
    return st["board"].count(seat + 1)


def score(st: dict, seat: int) -> int:
    return margin_score(st["winner"] == seat, st["winner"] == -1, BASE,
                        BONUS_DISC * (discs(st, seat) - discs(st, 1 - seat)))


def view(st: dict, seat: int) -> dict:
    return digest(st)


def visible(record: dict, seat: int, mover: int) -> dict:
    return record


def digest(st: dict) -> dict:
    return {"board": st["board"], "turn": st["turn"], "over": st["over"], "winner": st["winner"], "passed": st["passed"]}


CORNERS = (0, 7, 56, 63)


def computer(st: dict, seat: int, rnd) -> dict:
    ms = moves(st, seat)
    ms.sort(key=lambda m: (m["cell"] not in CORNERS, -len(flips(st["board"], m["cell"], seat)), rnd.random()))
    return ms[0]
