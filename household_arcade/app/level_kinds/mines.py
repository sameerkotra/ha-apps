"""Mines · Shaped boards: a board is a shape drawn with "#" (a square) and "." (no square), and a mine count."""
from ..level_common import LevelError
from . import Grid, Int

MIN_SQUARES = 30
MIN_DENSITY, MAX_DENSITY = 0.08, 0.22


def _squares(shape):
    return [(x, y) for y, row in enumerate(shape) for x, ch in enumerate(row) if ch == "#"]


def _check(c):
    shape, mines = c["shape"], c["mines"]
    sq = _squares(shape)
    if len(sq) < MIN_SQUARES:
        raise LevelError(f"the shape needs at least {MIN_SQUARES} squares (\"#\")")
    if "#" not in shape[0] or "#" not in shape[-1] or not any(r[0] == "#" for r in shape) \
            or not any(r[-1] == "#" for r in shape):
        raise LevelError("the shape must touch all four sides of its box (no empty first or last row or column)")
    # every square reachable from every other, side by side
    todo, seen, have = [sq[0]], {sq[0]}, set(sq)
    while todo:
        x, y = todo.pop()
        for n in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if n in have and n not in seen:
                seen.add(n)
                todo.append(n)
    if len(seen) != len(sq):
        raise LevelError("all the squares must be joined side by side (one piece, no islands)")
    lo, hi = -(-len(sq) * 8 // 100), len(sq) * 22 // 100
    if not lo <= mines <= hi:
        raise LevelError(f"this shape has {len(sq)} squares, so mines must be {lo}–{hi} (8–22 % of the squares)")


def _difficulty(c):
    n = len(_squares(c["shape"]))
    return (c["mines"] / n - MIN_DENSITY) / (MAX_DENSITY - MIN_DENSITY) * 75 + n / 180 * 25


KIND = {
    "game": "mines",
    "label": "Mines · Shaped boards",
    "noun": "board",
    "modes": ["boards"],
    # Ranges keep the honest-score limit true: a board is at most 12 × 15 = 180 squares and 22 % mines
    # (39), so it is worth at most 180 + 15 × 39 = 765 points, and the next board comes 90 updates after a
    # clear (≤ 510 a second, under the 600 allowed; one board ≤ 1000, the base).
    "fields": {
        "shape": Grid("#.", (6, 15), (6, 12)),
        "mines": Int(3, 39),
    },
    "check": _check,
    "difficulty": _difficulty,
    "about": """You design boards for Mines, a gentle family logic game. A board is a grid of covered squares;
some hide mines. The player opens squares one at a time: an opened square shows how many mines touch it
(of the up to 8 squares around it, sideways and corners), and opening a mine ends the game. The first square
opened on a board is never a mine nor next to one. Opening every square without a mine clears the board and
starts the next board.
"shape" draws the board: a list of 6-15 rows, all the same length (6-12 characters), where "#" is a square and
"." is no square. Use the dots to make a picture or an outline: a heart, a ring with a hole in the middle,
stairs, letters, a boat, a tree. Rules for the shape: at least 30 squares; every square joined to the others
side by side (one piece, no islands; touching only at corners doesn't count); the first and last row and the
first and last column each contain at least one "#".
"mines" is how many mines hide in it: between 8 % (easy) and 22 % (very hard) of the number of "#" squares.
More squares and a higher share of mines are harder; holes and thin parts make it trickier too.""",
    "target": lambda n: f"Board {n} should have about {min(180, 50 + 10 * n)} squares and about "
    f"{min(21, 11 + n)} % of them mines.",
    "example": {"name": "Little boat", "shape": [
        ".....#....",
        "....##....",
        "...###....",
        "..####....",
        ".#####....",
        ".....#....",
        "##########",
        ".########.",
        "..######..",
    ], "mines": 7},
    "builtin": [
        {"name": "Picnic blanket", "shape": [
            "########",
            "########",
            "########",
            "########",
            "########",
            "########",
            "########",
            "########",
        ], "mines": 8},
        {"name": "Plus sign", "shape": [
            "...###...",
            "...###...",
            "...###...",
            "#########",
            "#########",
            "#########",
            "...###...",
            "...###...",
            "...###...",
        ], "mines": 6},
        {"name": "Stairs", "shape": [
            "##........",
            "###.......",
            "####......",
            "#####.....",
            "######....",
            "#######...",
            "########..",
            "#########.",
            "##########",
            "##########",
        ], "mines": 9},
        {"name": "Big heart", "shape": [
            ".###..###.",
            "##########",
            "##########",
            "##########",
            ".########.",
            "..######..",
            "...####...",
            "....##....",
        ], "mines": 9},
        {"name": "Two ponds", "shape": [
            "####....####",
            "#####..#####",
            "############",
            "############",
            "#####..#####",
            "####....####",
        ], "mines": 10},
        {"name": "Ring", "shape": [
            "..######..",
            ".########.",
            "##########",
            "###....###",
            "###....###",
            "###....###",
            "###....###",
            "##########",
            ".########.",
            "..######..",
        ], "mines": 12},
        {"name": "Diamond", "shape": [
            ".....#.....",
            "....###....",
            "...#####...",
            "..#######..",
            ".#########.",
            "###########",
            ".#########.",
            "..#######..",
            "...#####...",
            "....###....",
            ".....#.....",
        ], "mines": 11},
        {"name": "Tall tree", "shape": [
            ".....#.....",
            "....###....",
            "...#####...",
            "..#######..",
            "....###....",
            "...#####...",
            "..#######..",
            ".#########.",
            "....###....",
            "..#######..",
            ".#########.",
            "###########",
            "....###....",
            "....###....",
            "...#####...",
        ], "mines": 15},
        {"name": "Grand field", "shape": [
            "############",
            "############",
            "############",
            "############",
            "#####..#####",
            "####....####",
            "####....####",
            "#####..#####",
            "############",
            "############",
            "############",
            "############",
            "############",
        ], "mines": 30},
    ],
}
