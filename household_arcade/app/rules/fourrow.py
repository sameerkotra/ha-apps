"""Four in a Row: a 7 × 6 frame stood upright; a disc dropped into a column falls to the lowest free place. Four of
your discs in a row (across, up and down, or slanting) wins; a full frame with no row is a draw.

Cells are numbered row by row from the top (row 0) left: cell = row × 7 + column. A move is {"col": 0–6}.
Mirrors static/games/fourrow-logic.js."""
from __future__ import annotations

from .base import Illegal, copy, margin_score, need_turn, whole

COLS, ROWS = 7, 6
PLAYERS = (2, 2)
OPTIONS: dict = {}
DICE = 0
BASE = 500                    # a win against a person; the computer levels score 100 / 250 / 500 in the browser
BONUS_CELL = 10               # … and 10 for each empty place left (a quicker win)


def new(seed: int, players: int = 2, options=None) -> dict:
    first = int(seed) % 2
    return {"g": "fourrow", "n": 2, "first": first, "turn": first, "board": [0] * (COLS * ROWS), "ply": 0,
            "over": False, "winner": None, "line": [], "last": -1}


def to_move(st: dict) -> list[int]:
    return [] if st["over"] else [st["turn"]]


def moves(st: dict, seat: int) -> list[dict]:
    if st["over"] or seat != st["turn"]:
        return []
    return [{"col": c} for c in range(COLS) if st["board"][c] == 0]


def line_through(board: list[int], cell: int) -> list[int]:
    """The four (or more) in a row through `cell`, or []."""
    who = board[cell]
    r0, c0 = divmod(cell, COLS)
    for dr, dc in ((0, 1), (1, 0), (1, 1), (1, -1)):
        run = [cell]
        for sgn in (1, -1):
            r, c = r0 + dr * sgn, c0 + dc * sgn
            while 0 <= r < ROWS and 0 <= c < COLS and board[r * COLS + c] == who:
                run.append(r * COLS + c)
                r, c = r + dr * sgn, c + dc * sgn
        if len(run) >= 4:
            return sorted(run)
    return []


def apply(st: dict, seat: int, move) -> tuple[dict, dict]:
    need_turn(st, seat)
    if not isinstance(move, dict) or set(move) != {"col"} or not whole(move["col"], 0, COLS - 1):
        raise Illegal("Pick a column, 0 to 6.")
    col = move["col"]
    if st["board"][col] != 0:
        raise Illegal("That column is full.")
    s = copy(st)
    row = max(r for r in range(ROWS) if s["board"][r * COLS + col] == 0)
    cell = row * COLS + col
    s["board"][cell] = seat + 1
    s["ply"] += 1
    s["last"] = cell
    line = line_through(s["board"], cell)
    if line:
        s["over"], s["winner"], s["line"] = True, seat, line
    elif all(v != 0 for v in s["board"]):
        s["over"], s["winner"] = True, -1
    else:
        s["turn"] = 1 - seat
    return s, {"col": col}


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
    """Wins at once if it can, else blocks, else a column near the middle."""
    ms = moves(st, seat)
    for who in (seat, 1 - seat):
        for m in ms:
            t = copy(st)
            t["turn"] = who
            t2, _ = apply(t, who, m)
            if t2["winner"] == who:
                return m
    ms.sort(key=lambda m: (abs(m["col"] - 3), rnd.random()))
    return ms[0]
