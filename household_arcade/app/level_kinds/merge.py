"""Merge · Goals: a goal is a board size, stone squares that never move, and the tile to make."""
from ..level_common import LevelError
from . import Choice, Grid, Int

GOALS = (32, 64, 128, 256, 512, 1024, 2048, 4096)


def _free(c):
    return [(x, y) for y, row in enumerate(c["stones"]) for x, ch in enumerate(row) if ch == "."]


def power(size, rows):
    """The biggest goal allowed is 2 ** power: the free squares, less 2 × (size − 2), less 2 for each stone on
    an edge (not a corner) and 3 for each stone away from the edges (those split lines in the middle and
    make big tiles much harder; measured with a look-ahead player)."""
    p = sum(r.count(".") for r in rows) - 2 * (size - 2)
    for y, r in enumerate(rows):
        for x, ch in enumerate(r):
            if ch == "#":
                edges = (x in (0, size - 1)) + (y in (0, size - 1))
                p -= 0 if edges == 2 else 2 if edges == 1 else 3
    return p


def _check(c):
    n, rows = c["size"], c["stones"]
    if len(rows) != n or len(rows[0]) != n:
        raise LevelError(f"stones must be {n} rows of {n} characters (the size)")
    stones = sum(r.count("#") for r in rows)
    if stones > 3:
        raise LevelError("at most 3 stones")
    if any(r.count("#") > n - 1 for r in rows) or any(sum(r[x] == "#" for r in rows) > n - 1 for x in range(n)):
        raise LevelError(f"at most {n - 1} stones in one row or column")
    free = _free(c)
    if len(free) < 7:
        raise LevelError("at least 7 free squares")
    have, seen, todo = set(free), {free[0]}, [free[0]]
    while todo:
        x, y = todo.pop()
        for p in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if p in have and p not in seen:
                seen.add(p)
                todo.append(p)
    if len(seen) != len(free):
        raise LevelError("every free square must be joined to the others side by side (stones may not shut one in)")
    top = 2 ** power(n, rows)
    if c["goal"] > top:
        raise LevelError(f"this board can only have a goal up to {top} (see the rule for the goal); "
                         "choose a smaller goal or fewer stones")


def _difficulty(c):
    e = c["goal"].bit_length() - 1
    return e * 7 + e / power(c["size"], c["stones"]) * 40 - 30


KIND = {
    "game": "merge",
    "label": "Merge · Goals",
    "noun": "goal",
    "modes": ["goals"],
    # Ranges keep the honest-score limit true: moves stay ≥ 6 updates apart and a board ends at its goal
    # (≤ 4096), so its tile total S stays under 25 × 4096 and a move scores ≤ 4 × (log2 S − 1) < 64:
    # under 650 a second, inside the 800 allowed.
    "fields": {
        "size": Int(3, 5),
        "goal": Choice(*GOALS),
        "stones": Grid("#.", (3, 5), (3, 5)),
    },
    "check": _check,
    "difficulty": _difficulty,
    "about": """You design goals for Merge, a calm family number puzzle. Number tiles sit on a square board; each move
slides every tile as far as it goes in one direction, and two equal tiles that meet join into one tile worth
double (2+2 makes 4, 4+4 makes 8 ...). After each move a new 2 (sometimes a 4) appears on a free square. A goal
is one board: make a tile of the goal's value and the next goal starts on a fresh board; if the board fills
up and nothing can move, the game is over.
"size" is the board's width and height in squares (3, 4 or 5). "goal" is the tile to make (32 up to 4096;
bigger is much harder). "stones" is the board: exactly "size" rows of "size" characters, "." a free square
and "#" a stone, a fixed square that never moves; tiles can't slide past a stone, so stones split rows and
columns. Rules: 0-3 stones, at most size-1 stones in one row or column, at least 7 free squares, all free
squares joined side by side, and the goal at most 2 to the power P, where P = the number of free squares,
minus 2 x (size - 2), minus 2 for every stone on an edge that isn't a corner, minus 3 for every stone not on
an edge (corner stones cost nothing more). So with no stones: 3 x 3 up to 128, 4 x 4 up to 4096; a 4 x 4
board with one stone in the middle area up to 256; a 3 x 3 board can't have a stone in its centre.""",
    "target": lambda n: f"Goal {n} should be about as hard as: size {4 if n < 8 else 5}, goal "
    f"{GOALS[min(len(GOALS) - 1, 1 + n // 2)]}, {min(3, n // 3)} stone(s).",
    "example": {"name": "Pebble pond", "size": 4, "goal": 256, "stones": ["....", ".#..", "....", "...."]},
    "builtin": [
        {"name": "Warm up", "size": 4, "goal": 64, "stones": ["....", "....", "....", "...."]},
        {"name": "Little box", "size": 3, "goal": 64, "stones": ["...", "...", "..."]},
        {"name": "One stone", "size": 4, "goal": 128, "stones": ["....", ".#..", "....", "...."]},
        {"name": "Garden path", "size": 5, "goal": 256, "stones": [".....", ".#...", ".....", "...#.", "....."]},
        {"name": "Corner rocks", "size": 4, "goal": 256, "stones": ["#...", "....", "....", "...#"]},
        {"name": "Twin stones", "size": 4, "goal": 128, "stones": ["#...", ".#..", "....", "...."]},
        {"name": "Big meadow", "size": 5, "goal": 512, "stones": [".....", ".....", "#...#", ".....", "....."]},
        {"name": "Stepping stone", "size": 5, "goal": 1024, "stones": [".....", ".....", "...#.", ".....", "....."]},
        {"name": "Mountain", "size": 4, "goal": 1024, "stones": ["....", "....", "....", "...."]},
        {"name": "Summit", "size": 5, "goal": 2048, "stones": [".....", ".....", "..#..", ".....", "....."]},
    ],
}
