"""Dots and Boxes: a grid of dots (3 × 3, 4 × 4 or 5 × 5 boxes — the match's Size option). Players take turns
joining two neighbouring dots with a line. Whoever draws the fourth side of a box claims it and must draw another line
(an extra turn for every move that completes a box, one or two at once). When every line is drawn, the one with more
boxes wins (the same number: a draw).

Lines are numbered: the across lines first, row by row (row 0–size, column 0–size−1), then the up-and-down lines
(row 0–size−1, column 0–size). A move is {"edge": number}. Mirrors static/games/dots-logic.js."""
from __future__ import annotations

from .base import Illegal, copy, margin_score, need_turn, whole

PLAYERS = (2, 2)
OPTIONS = {"size": (("3", "4", "5"), "4")}
DICE = 0
BASE = 500
BONUS_BOX = 20                # each box more than the other side's at a win


def dims(size: int) -> tuple[int, int, int]:
    """(across lines, up-and-down lines, all lines) for a size × size board of boxes."""
    h = (size + 1) * size
    v = size * (size + 1)
    return h, v, h + v


def box_edges(size: int, r: int, c: int) -> tuple[int, int, int, int]:
    h, _, _ = dims(size)
    return (r * size + c, (r + 1) * size + c, h + r * (size + 1) + c, h + r * (size + 1) + c + 1)


def edge_boxes(size: int, e: int) -> list[int]:
    """The boxes (row × size + column) a line is a side of."""
    h, _, _ = dims(size)
    out = []
    if e < h:
        r, c = divmod(e, size)
        if r > 0:
            out.append((r - 1) * size + c)
        if r < size:
            out.append(r * size + c)
    else:
        r, c = divmod(e - h, size + 1)
        if c > 0:
            out.append(r * size + c - 1)
        if c < size:
            out.append(r * size + c)
    return out


def new(seed: int, players: int = 2, options=None) -> dict:
    size = int((options or {}).get("size", OPTIONS["size"][1]))
    if str(size) not in OPTIONS["size"][0]:
        size = int(OPTIONS["size"][1])
    first = int(seed) % 2
    return {"g": "dots", "n": 2, "size": size, "first": first, "turn": first, "edges": [0] * dims(size)[2],
            "boxes": [0] * (size * size), "ply": 0, "over": False, "winner": None, "last": -1}


def to_move(st: dict) -> list[int]:
    return [] if st["over"] else [st["turn"]]


def moves(st: dict, seat: int) -> list[dict]:
    if st["over"] or seat != st["turn"]:
        return []
    return [{"edge": e} for e, v in enumerate(st["edges"]) if v == 0]


def apply(st: dict, seat: int, move) -> tuple[dict, dict]:
    need_turn(st, seat)
    n = len(st["edges"])
    if not isinstance(move, dict) or set(move) != {"edge"} or not whole(move["edge"], 0, n - 1):
        raise Illegal("Pick a line.")
    e = move["edge"]
    if st["edges"][e]:
        raise Illegal("That line is already drawn.")
    s = copy(st)
    size = s["size"]
    s["edges"][e] = seat + 1
    s["ply"] += 1
    s["last"] = e
    made = 0
    for b in edge_boxes(size, e):
        r, c = divmod(b, size)
        if s["boxes"][b] == 0 and all(s["edges"][k] for k in box_edges(size, r, c)):
            s["boxes"][b] = seat + 1
            made += 1
    if all(s["edges"]):
        s["over"] = True
        a, z = s["boxes"].count(1), s["boxes"].count(2)
        s["winner"] = 0 if a > z else 1 if z > a else -1
    elif not made:
        s["turn"] = 1 - seat
    return s, {"edge": e}


def result(st: dict):
    if not st["over"]:
        return None
    w = st["winner"]
    return {"winner": None if w == -1 else w, "places": [w, 1 - w] if w in (0, 1) else [0, 1]}


def score(st: dict, seat: int) -> int:
    mine, theirs = st["boxes"].count(seat + 1), st["boxes"].count(2 - seat)
    return margin_score(st["winner"] == seat, st["winner"] == -1, BASE, BONUS_BOX * (mine - theirs))


def view(st: dict, seat: int) -> dict:
    return digest(st)


def visible(record: dict, seat: int, mover: int) -> dict:
    return record


def digest(st: dict) -> dict:
    return {"size": st["size"], "edges": st["edges"], "boxes": st["boxes"], "turn": st["turn"], "over": st["over"],
            "winner": st["winner"]}


def computer(st: dict, seat: int, rnd) -> dict:
    """Takes a box when it can, else a line that doesn't give one away, else any."""
    size, ms = st["size"], moves(st, seat)

    def sides(b):
        r, c = divmod(b, size)
        return sum(1 for k in box_edges(size, r, c) if st["edges"][k])
    take = [m for m in ms if any(sides(b) == 3 for b in edge_boxes(size, m["edge"]))]
    safe = [m for m in ms if all(sides(b) < 2 for b in edge_boxes(size, m["edge"]))]
    pool = take or safe or ms
    return pool[int(rnd.random() * len(pool))]
