"""Checkers — English draughts (the rule set this app plays, SPEC §13.5 / spec/GAMES.md):

- An 8 × 8 board; pieces stand on the dark squares only ((row + column) odd, row 0 at the top). Each side has 12.
  The side that moves first plays dark and starts on the three bottom rows; the other starts on the three top rows.
- A man moves one square diagonally forward; a king (a man that reached the far row) one square diagonally either way.
- Capturing is compulsory: if any of your pieces can jump (over a diagonal neighbour of the other side onto the empty
  square behind it), you must jump. A jump that can carry on must carry on (multi-jumps) until no further jump is
  possible; when several captures are possible you may choose any of them (not necessarily the longest). Jumped pieces
  come off at the end of the move and can't be jumped twice. A man that reaches the far row is crowned and its move
  ends there.
- You lose when you have no legal move (no pieces left, or all blocked). A draw when 40 moves each (80 in all) pass
  without a capture or a man moving.

Squares are 0–63 (row × 8 + column). A move is {"path": [from, to, (to, …)]} — every square the piece lands on.
Cell values: 0 empty, 1 / 2 a man of seat 0 / 1, 3 / 4 a king of seat 0 / 1. Mirrors static/games/checkers-logic.js."""
from __future__ import annotations

from .base import Illegal, canonical, copy, margin_score, need_turn

PLAYERS = (2, 2)
OPTIONS: dict = {}
DICE = 0
BASE = 500
BONUS_PIECE = 30              # each of your pieces left at a win
DRAW_PLIES = 80


def owner(v: int) -> int:
    return -1 if v == 0 else (v - 1) % 2


def is_king(v: int) -> bool:
    return v >= 3


def forward(st: dict, seat: int) -> int:
    """Up the board (−1 a row) for the side that moves first (dark), down for the other."""
    return -1 if seat == st["first"] else 1


def new(seed: int, players: int = 2, options=None) -> dict:
    first = int(seed) % 2
    other = 1 - first
    board = [0] * 64
    for r in range(8):
        for c in range(8):
            if (r + c) % 2 == 1:
                if r <= 2:
                    board[r * 8 + c] = other + 1
                elif r >= 5:
                    board[r * 8 + c] = first + 1
    return {"g": "checkers", "n": 2, "first": first, "turn": first, "board": board, "ply": 0, "quiet": 0,
            "over": False, "winner": None, "last": []}


def to_move(st: dict) -> list[int]:
    return [] if st["over"] else [st["turn"]]


def _jumps(board, sq, seat, king, fwd, captured, start):
    out = []
    r, c = divmod(sq, 8)
    dirs = [(fwd, -1), (fwd, 1)] + ([(-fwd, -1), (-fwd, 1)] if king else [])
    for dr, dc in dirs:
        lr, lc = r + 2 * dr, c + 2 * dc
        if not (0 <= lr < 8 and 0 <= lc < 8):
            continue
        mid, land = (r + dr) * 8 + c + dc, lr * 8 + lc
        mv = board[mid]
        if mv == 0 or owner(mv) == seat or mid in captured:
            continue
        if board[land] != 0 and land != start:
            continue
        if not king and lr == (0 if fwd == -1 else 7):        # crowned: the move ends here
            out.append([land])
            continue
        more = _jumps(board, land, seat, king, fwd, captured | {mid}, start)
        if more:
            out.extend([land] + p for p in more)
        else:
            out.append([land])
    return out


def moves(st: dict, seat: int) -> list[dict]:
    if st["over"] or seat != st["turn"]:
        return []
    return _all_moves(st, seat)


def _all_moves(st: dict, seat: int) -> list[dict]:
    board, fwd = st["board"], forward(st, seat)
    jumps, steps = [], []
    for sq in range(64):
        v = board[sq]
        if v == 0 or owner(v) != seat:
            continue
        king = is_king(v)
        for p in _jumps(board, sq, seat, king, fwd, frozenset(), sq):
            jumps.append({"path": [sq] + p})
        if jumps:
            continue
        r, c = divmod(sq, 8)
        for dr in ((fwd, -fwd) if king else (fwd,)):
            for dc in (-1, 1):
                nr, nc = r + dr, c + dc
                if 0 <= nr < 8 and 0 <= nc < 8 and board[nr * 8 + nc] == 0:
                    steps.append({"path": [sq, nr * 8 + nc]})
    return jumps if jumps else steps


def apply(st: dict, seat: int, move) -> tuple[dict, dict]:
    need_turn(st, seat)
    if not isinstance(move, dict) or set(move) != {"path"} or not isinstance(move["path"], list) \
            or not 2 <= len(move["path"]) <= 13:
        raise Illegal("A move is the squares the piece lands on.")
    want = canonical({"path": move["path"]})
    if not any(canonical(m) == want for m in _all_moves(st, seat)):
        raise Illegal("That move isn't allowed (a capture may be possible — captures are compulsory).")
    s = copy(st)
    b, path = s["board"], move["path"]
    piece = b[path[0]]
    b[path[0]] = 0
    captured = False
    for a, z in zip(path, path[1:]):
        if abs(a // 8 - z // 8) == 2:
            b[(a + z) // 2] = 0
            captured = True
    end = path[-1]
    crowned = not is_king(piece) and end // 8 == (0 if forward(s, seat) == -1 else 7)
    b[end] = piece + 2 if crowned else piece
    s["quiet"] = 0 if captured or not is_king(piece) else s["quiet"] + 1
    s["ply"] += 1
    s["last"] = list(path)
    other = 1 - seat
    s["turn"] = other
    if not _all_moves(s, other):
        s["over"], s["winner"] = True, seat
    elif s["quiet"] >= DRAW_PLIES:
        s["over"], s["winner"] = True, -1
    return s, {"path": list(path)}


def result(st: dict):
    if not st["over"]:
        return None
    w = st["winner"]
    return {"winner": None if w == -1 else w, "places": [w, 1 - w] if w in (0, 1) else [0, 1]}


def pieces(st: dict, seat: int) -> int:
    return sum(1 for v in st["board"] if v and owner(v) == seat)


def score(st: dict, seat: int) -> int:
    return margin_score(st["winner"] == seat, st["winner"] == -1, BASE, BONUS_PIECE * pieces(st, seat))


def view(st: dict, seat: int) -> dict:
    return digest(st)


def visible(record: dict, seat: int, mover: int) -> dict:
    return record


def digest(st: dict) -> dict:
    return {"board": st["board"], "turn": st["turn"], "over": st["over"], "winner": st["winner"], "quiet": st["quiet"]}


def computer(st: dict, seat: int, rnd) -> dict:
    ms = moves(st, seat)
    ms.sort(key=lambda m: (-len(m["path"]), rnd.random()))
    return ms[0]
