"""Sea Battle: each player places a fleet on their own square sea, hidden from the other; then they take turns firing
one shot at a square of the other's sea and are told only "miss", "hit" or "sunk" (with the ship's size — a sunk
ship's squares are shown). Sinking every ship of the other fleet wins.

Fleets (the match's Fleet option): *classic* 10 × 10 with ships of 5, 4, 3, 3 and 2 squares; *small* 8 × 8 with 4, 3,
3 and 2. Ships lie across or up and down, inside the sea, and may touch but never overlap. Both players place at the
same time (in any order); then the one chosen by the seed fires first, and turns alternate (a hit doesn't give another
shot).

Squares are row × size + column. Moves: {"place": [[row, column, length, 0 across | 1 down], …]} (once each) and
{"shot": square}. The server keeps both fleets; a phone only ever gets its own (`view`) and the results of shots —
never where the other's ships are, until the game is over. Mirrors static/games/seabattle-logic.js."""
from __future__ import annotations

from .base import Illegal, copy, margin_score, whole

PLAYERS = (2, 2)
OPTIONS = {"fleet": (("classic", "small"), "classic")}
FLEETS = {"classic": (10, [5, 4, 3, 3, 2]), "small": (8, [4, 3, 3, 2])}
DICE = 0
BASE = 500
BONUS_CELL = 15               # each square of your own fleet not hit, at a win


def new(seed: int, players: int = 2, options=None) -> dict:
    fleet = (options or {}).get("fleet", OPTIONS["fleet"][1])
    if fleet not in FLEETS:
        fleet = OPTIONS["fleet"][1]
    size, ships = FLEETS[fleet]
    first = int(seed) % 2
    return {"g": "seabattle", "n": 2, "fleetId": fleet, "size": size, "fleet": list(ships), "first": first,
            "turn": first, "phase": "place", "ships": [None, None], "shots": [[], []], "ply": 0,
            "over": False, "winner": None, "last": -1}


def to_move(st: dict) -> list[int]:
    if st["over"]:
        return []
    if st["phase"] == "place":
        return [s for s in (0, 1) if st["ships"][s] is None]
    return [st["turn"]]


def moves(st: dict, seat: int) -> list[dict]:
    if st["over"] or st["phase"] != "play" or seat != st["turn"]:
        return []
    shot = set(st["shots"][seat])
    return [{"shot": c} for c in range(st["size"] ** 2) if c not in shot]


def ship_cells(size: int, r: int, c: int, length: int, down: int) -> list[int]:
    return [(r + (i if down else 0)) * size + c + (0 if down else i) for i in range(length)]


def check_fleet(st: dict, place) -> list[list[int]]:
    """The placed fleet as lists of squares, or Illegal."""
    size, fleet = st["size"], st["fleet"]
    if not isinstance(place, list) or len(place) != len(fleet):
        raise Illegal(f"Place all {len(fleet)} ships.")
    used, out = set(), []
    for item in place:
        if not isinstance(item, list) or len(item) != 4:
            raise Illegal("A ship is [row, column, length, 0 across | 1 down].")
        r, c, length, down = item
        if not (whole(r, 0, size - 1) and whole(c, 0, size - 1) and whole(length, 1, 5) and whole(down, 0, 1)):
            raise Illegal("A ship is [row, column, length, 0 across | 1 down].")
        if (r + length if down else c + length) > size:
            raise Illegal("A ship must lie inside the sea.")
        cells = ship_cells(size, r, c, length, down)
        if used & set(cells):
            raise Illegal("Ships can't overlap.")
        used |= set(cells)
        out.append(cells)
    if sorted(len(x) for x in out) != sorted(fleet):
        raise Illegal("That isn't the fleet: " + ", ".join(str(n) for n in fleet) + ".")
    return out


def _hit_ship(ships: list[list[int]], cell: int):
    for cells in ships:
        if cell in cells:
            return cells
    return None


def apply(st: dict, seat: int, move) -> tuple[dict, dict]:
    if st["over"]:
        raise Illegal("The game is over.")
    if not isinstance(move, dict) or len(move) != 1:
        raise Illegal("Place your fleet or fire a shot.")
    if "place" in move:
        if st["phase"] != "place" or st["ships"][seat] is not None:
            raise Illegal("Your fleet is already placed.")
        ships = check_fleet(st, move["place"])
        s = copy(st)
        s["ships"][seat] = ships
        if all(x is not None for x in s["ships"]):
            s["phase"] = "play"
            s["turn"] = s["first"]
        return s, {"place": move["place"]}
    if "shot" not in move:
        raise Illegal("Place your fleet or fire a shot.")
    if st["phase"] != "play":
        raise Illegal("Both fleets must be placed first.")
    if seat != st["turn"]:
        raise Illegal("It isn't your move.")
    cell = move["shot"]
    if not whole(cell, 0, st["size"] ** 2 - 1):
        raise Illegal("Pick a square of their sea.")
    if cell in st["shots"][seat]:
        raise Illegal("You've already fired there.")
    s = copy(st)
    s["shots"][seat].append(cell)
    s["ply"] += 1
    s["last"] = cell
    other = 1 - seat
    ship = _hit_ship(s["ships"][other], cell)
    shot = set(s["shots"][seat])
    sunk = ship is not None and all(c in shot for c in ship)
    rec = {"shot": cell, "hit": ship is not None, "sunk": len(ship) if sunk else 0, "cells": list(ship) if sunk else []}
    if all(c in shot for x in s["ships"][other] for c in x):
        s["over"], s["winner"] = True, seat
    else:
        s["turn"] = other
    return s, rec


def result(st: dict):
    if not st["over"]:
        return None
    w = st["winner"]
    return {"winner": None if w == -1 else w, "places": [w, 1 - w] if w in (0, 1) else [0, 1]}


def unhit(st: dict, seat: int) -> int:
    shot = set(st["shots"][1 - seat])
    return sum(1 for x in (st["ships"][seat] or []) for c in x if c not in shot)


def score(st: dict, seat: int) -> int:
    return margin_score(st["winner"] == seat, st["winner"] == -1, BASE, BONUS_CELL * unhit(st, seat))


def _shots_view(st: dict, by: int) -> tuple[list, list]:
    """The shots `by` fired: [[square, hit], …], and the target's ships they sank (their squares)."""
    target = st["ships"][1 - by] or []
    shot = set(st["shots"][by])
    marks = [[c, _hit_ship(target, c) is not None] for c in st["shots"][by]]
    sunk = [list(x) for x in target if all(c in shot for c in x)]
    return marks, sunk


def view(st: dict, seat: int) -> dict:
    """What `seat` may see: its own fleet, the shots both have fired and what they hit, the ships sunk — never where
    the other fleet's ships are while the game is on."""
    mine, mine_sunk = _shots_view(st, seat)
    theirs, their_sunk = _shots_view(st, 1 - seat)
    out = {"size": st["size"], "fleet": st["fleet"], "fleetId": st["fleetId"], "phase": st["phase"], "turn": st["turn"],
           "first": st["first"], "placed": [x is not None for x in st["ships"]], "over": st["over"],
           "winner": st["winner"], "ships": st["ships"][seat], "myShots": mine, "sunk": mine_sunk,
           "atMe": theirs, "lost": their_sunk, "last": st["last"]}
    if st["over"]:
        out["theirShips"] = st["ships"][1 - seat]
    return out


def visible(record: dict, seat: int, mover: int) -> dict:
    """Another player's placed fleet is never sent: only that it was placed."""
    if "place" in record and seat != mover:
        return {"placed": True}
    return record


def digest(st: dict) -> dict:
    return {"ships": st["ships"], "shots": st["shots"], "phase": st["phase"], "turn": st["turn"], "over": st["over"],
            "winner": st["winner"]}


def random_fleet(size: int, fleet: list[int], rnd) -> list[list[int]]:
    while True:
        used, out = set(), []
        for length in fleet:
            for _ in range(200):
                down = int(rnd.random() * 2)
                r = int(rnd.random() * (size - (length - 1 if down else 0)))
                c = int(rnd.random() * (size - (0 if down else length - 1)))
                cells = ship_cells(size, r, c, length, down)
                if not used & set(cells):
                    used |= set(cells)
                    out.append([r, c, length, down])
                    break
        if len(out) == len(fleet):
            return out


def computer(st: dict, seat: int, rnd) -> dict:
    if st["phase"] == "place":
        return {"place": random_fleet(st["size"], st["fleet"], rnd)}
    size = st["size"]
    marks, sunk = _shots_view(st, seat)
    shot = {c for c, _ in marks}
    sunk_cells = {c for x in sunk for c in x}
    open_hits = [c for c, hit in marks if hit and c not in sunk_cells]
    near = []
    for c in open_hits:
        r, k = divmod(c, size)
        for nr, nk in ((r - 1, k), (r + 1, k), (r, k - 1), (r, k + 1)):
            if 0 <= nr < size and 0 <= nk < size and nr * size + nk not in shot:
                near.append(nr * size + nk)
    pool = near or [c for c in range(size * size) if c not in shot]
    return {"shot": pool[int(rnd.random() * len(pool))]}
