"""Bubble Pop · Puzzles: a puzzle is a board of bubbles hanging from the ceiling, how many colours its bubbles
come in and how many shots go by before the ceiling comes down a row."""
from ..level_common import LevelError
from . import Grid, Int

COLS = 8


def _neighbours(r, c):
    """Rows alternate: row 0 sits flush left, row 1 half a bubble to the right, and so on."""
    out = [(r, c - 1), (r, c + 1)]
    if r % 2 == 1:
        out += [(r - 1, c), (r - 1, c + 1), (r + 1, c), (r + 1, c + 1)]
    else:
        out += [(r - 1, c - 1), (r - 1, c), (r + 1, c - 1), (r + 1, c)]
    return out


def _check(p):
    rows = p["rows"]
    cells = {(r, c) for r, row in enumerate(rows) for c, ch in enumerate(row) if ch != "."}
    if len(cells) < 12:
        raise LevelError("a puzzle needs at least 12 bubbles")
    if any(int(rows[r][c]) > p["colours"] for r, c in cells):
        raise LevelError(f"bubbles may only use colours 1-{p['colours']} (the puzzle's colours)")
    seen = {(0, c) for c in range(COLS) if (0, c) in cells}
    todo = list(seen)
    while todo:
        cell = todo.pop()
        for n in _neighbours(*cell):
            if n in cells and n not in seen:
                seen.add(n)
                todo.append(n)
    if seen != cells:
        raise LevelError("every bubble must hang from the top row (touching it through other bubbles)")


def _bubbles(p):
    return sum(len(r) - r.count(".") for r in p["rows"])


KIND = {
    "game": "bubbles",
    "label": "Bubble Pop · Puzzles",
    "noun": "puzzle",
    "modes": ["puzzle"],
    # Points come only from bubbles that leave the board (10 or 20 each) and a cleared board (500), so no puzzle
    # can break the honest-score limits; 3-8 rows leave room before the line the bubbles must not cross.
    "fields": {
        "rows": Grid(".123456", (3, 8), (COLS, COLS)),
        "colours": Int(3, 6),
        "drop": Int(4, 12),
    },
    "check": _check,
    "difficulty": lambda p: (p["colours"] - 3) * 14 + len(p["rows"]) * 5 + (12 - p["drop"]) * 4 + _bubbles(p) / 8,
    "about": """You design puzzles for Bubble Pop, a family arcade game. Coloured bubbles hang from the ceiling in rows
of 8; every other row (rows 1, 3, 5 …, counting from 0 at the top) sits half a bubble to the right, so a bubble
touches two bubbles in the row above and two in the row below. The player shoots one bubble at a time from the
bottom; three or more of a colour touching each other pop, and bubbles left hanging from nothing drop. The next
bubbles to shoot are always colours still on the board. Every "drop" shots the ceiling comes down one row; a bubble
pushed below the line near the bottom ends the game. Clearing the board finishes the puzzle.
"rows" is the board: 3 to 8 strings of exactly 8 characters, "." no bubble and "1"-"6" a bubble of that colour.
Every bubble must hang from the top row (touching it through other bubbles), there must be at least 12 bubbles,
and only colours 1 to "colours" may be used. Clusters of a colour are easy, mixed colours and long hanging
strands are harder; patterns and pictures make good puzzles.""",
    "target": lambda n: f"Puzzle {n} should be about: {min(8, 3 + n // 2)} rows, colours {min(6, 3 + n // 3)}, "
    f"drop {max(5, 11 - n // 2)}.",
    "example": {"name": "Zig zag", "rows": ["12121212", "21212121", "33333333", "4.4.4.4."], "colours": 4, "drop": 9},
    "builtin": [
        {"name": "Warm up", "rows": ["11223344", "11223344", "22334411"], "colours": 4, "drop": 10},
        {"name": "Stripes", "rows": ["11111111", "22222222", "33333333", "11111111"], "colours": 3, "drop": 9},
        {"name": "Checks", "rows": ["11221122", "22112211", "11221122", "33443344", "33443344"], "colours": 4, "drop": 9},
        {"name": "Hanging garden", "rows": ["12341234", "1.3.1.3.", "1...1...", "4...4...", "4...4..."], "colours": 4, "drop": 8},
        {"name": "Rainbow", "rows": ["11223355", "11223355", "44556611", "44556611", "22334466"], "colours": 6, "drop": 8},
        {"name": "Arrow", "rows": ["55555555", "4444444.", ".333333.", "..2222..", "..111...", "...6...."], "colours": 6, "drop": 8},
        {"name": "Grapes", "rows": ["12121212", "21212121", "33333333", "45454545", "54545454", "66666666"], "colours": 6, "drop": 7},
        {"name": "Big wall", "rows": ["11223344", "22334455", "33445566", "44556611", "55661122", "66112233", "11223344"],
         "colours": 6, "drop": 7},
    ],
}
