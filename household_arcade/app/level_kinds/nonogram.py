"""Picture Logic · Picture book: a picture drawn with "#" on a square grid; the numbers come from it. A line solver
(the reasoning a person uses, one row or column at a time) checks how much of it the numbers decide; the game gives
the few squares they don't at the start, so every puzzle has one answer."""
from functools import lru_cache

from ..level_common import LevelError
from . import Grid

MAX_GIVEN_SHARE = 0.2        # at most a fifth of the squares may have to be given


def runs(line) -> tuple:
    out, n = [], 0
    for v in line:
        if v:
            n += 1
        elif n:
            out.append(n)
            n = 0
    if n:
        out.append(n)
    return tuple(out)


@lru_cache(maxsize=4096)
def _placements(clue: tuple, n: int) -> tuple:
    """Every line of length n with these runs, as tuples of 0/1."""
    if not clue:
        return ((0,) * n,)
    out = []
    first, rest = clue[0], clue[1:]
    need_rest = sum(rest) + len(rest)
    for start in range(0, n - first - need_rest + 1):
        head = (0,) * start + (1,) * first
        if rest:
            for tail in _placements(rest, n - start - first - 1):
                out.append(head + (0,) + tail)
        else:
            out.append(head + (0,) * (n - start - first))
    return tuple(out)


def unknown_after_solving(grid) -> int:
    """Squares a line-by-line solver can't decide from the numbers alone."""
    n = len(grid)
    sol = [[1 if ch == "#" else 0 for ch in row] for row in grid]
    rows = [runs(r) for r in sol]
    cols = [runs([sol[r][c] for r in range(n)]) for c in range(n)]
    cells = [[None] * n for _ in range(n)]
    changed = True
    while changed:
        changed = False
        for kind in ("r", "c"):
            for k in range(n):
                line = [cells[k][i] if kind == "r" else cells[i][k] for i in range(n)]
                fits = [p for p in _placements(rows[k] if kind == "r" else cols[k], n)
                        if all(v is None or v == q for v, q in zip(line, p))]
                for i in range(n):
                    if line[i] is None and all(p[i] == fits[0][i] for p in fits):
                        if kind == "r":
                            cells[k][i] = fits[0][i]
                        else:
                            cells[i][k] = fits[0][i]
                        changed = True
    return sum(v is None for row in cells for v in row)


def _check(c):
    g = c["picture"]
    n = len(g)
    if any(len(r) != n for r in g):
        raise LevelError("the picture must be square (as many rows as characters in a row)")
    filled = sum(r.count("#") for r in g)
    if not 0.25 <= filled / (n * n) <= 0.8:
        raise LevelError(f"fill 25-80 % of the squares with \"#\" (this has {filled} of {n * n})")
    if n > 10:
        lines = [runs([ch == "#" for ch in r]) for r in g] + [runs([r[k] == "#" for r in g]) for k in range(n)]
        if any(v > 9 for line in lines for v in line):
            raise LevelError("on grids bigger than 10 × 10, no row or column may have more than 9 \"#\" in a row")
    if unknown_after_solving(g) > MAX_GIVEN_SHARE * n * n:
        raise LevelError("the numbers leave too much of this picture undecided; give it clearer shapes")


def _difficulty(c):
    n = len(c["picture"])
    return min(100, (n - 5) * 7 + unknown_after_solving(c["picture"]) / (n * n) * 100)


KIND = {
    "game": "nonogram",
    "label": "Picture Logic · Picture book",
    "noun": "picture",
    "modes": ["pictures"],
    "fields": {"picture": Grid("#.", (5, 15), (5, 15))},
    "check": _check,
    "difficulty": _difficulty,
    "about": """You draw pictures for Picture Logic, a family logic puzzle (a nonogram). A square grid hides a picture; the
numbers beside each row and above each column tell how many filled squares there are in a row in that line, in order.
The player works out the picture from the numbers alone. The name is shown once the picture is solved, so make the
picture look like its name.
"picture" draws it: a square list of rows ("#" filled, "." empty), 5-15 rows, each row as long as there are rows.
Fill 25-80 % of the squares. On grids bigger than 10 × 10, no row or column may have more than 9 "#" in a row. Use
clear, chunky shapes (a cat, a house, an umbrella, a fish, a tree, a rocket, a heart, a smiling face): lots of lone
single squares make the numbers vague. Bigger grids are harder.""",
    "target": lambda n: f"Picture {n} should be about {min(15, 5 + n // 3)} × {min(15, 5 + n // 3)}.",
    "example": {"name": "Umbrella", "picture": ["..###..", ".#####.", "#######", "...#...", "...#...", ".#.#...", "..##..."]},
    "builtin": [
        {"name": "Heart", "picture": [".#.#.", "#####", "#####", ".###.", "..#.."]},
        {"name": "House", "picture": ["...##...", "..####..", ".######.", "########", ".######.", ".##..##.",
                                      ".##..##.", ".######."]},
        {"name": "Cat", "picture": ["#.....#.", "##...##.", "#######.", "#.###.#.", "#######.", ".#####..",
                                    "..###...", ".#####.#"]},
        {"name": "Tree", "picture": ["....##....", "...####...", "..######..", ".########.", "..######..",
                                     ".########.", "##########", "....##....", "....##....", "...####..."]},
        {"name": "Rocket", "picture": ["....##....", "...####...", "...####...", "...#..#...", "...####...",
                                       "...####...", "..######..", ".##.##.##.", ".#..##..#.", "....##...."]},
    ],
}
