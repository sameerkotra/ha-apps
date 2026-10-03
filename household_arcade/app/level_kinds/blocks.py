"""Falling Blocks · Challenges: a challenge is a well that starts with some rows already filled, a number of
rows to clear and a fall speed."""
from ..level_common import LevelError
from . import Grid, Int


def _check(c):
    for i, r in enumerate(c["rows"]):
        filled = r.count("#")
        if filled < 3 or filled > 9:
            raise LevelError(f"rows[{i}] must have at least 3 \"#\" and at least one \".\" (a full row can't be a start)")


def _difficulty(c):
    rows = c["rows"]
    gaps = sum(r.count(".") for r in rows)
    return (c["speed"] - 1) * 4.5 + (c["goal"] - 5) * 0.8 + len(rows) * 1.5 + gaps * 0.15


KIND = {
    "game": "blocks",
    "label": "Falling Blocks · Challenges",
    "noun": "challenge",
    "modes": ["challenge"],
    # Ranges keep the honest-score limit (10,000 a second) true: rows score 100/300/500/800 × speed and
    # speed ≤ 15 (below the 20 the limit allows). The ≤ 12 starting rows can be cleared faster than rows
    # built from pieces, so every challenge starts with a 2.5 s pause (INTRO) that pays for them:
    # 12 rows × 200 × 15 = 36,000 points take at least 2.5 s + 3 clears × 0.5 s = 4 s (40,000 allowed).
    "fields": {
        "rows": Grid(".#", (0, 12), (10, 10)),
        "goal": Int(5, 40),
        "speed": Int(1, 15),
    },
    "check": _check,
    "difficulty": _difficulty,
    "about": """You design challenges for Falling Blocks, a family puzzle game: shapes made of four squares fall into a
well 10 squares wide and 20 tall; the player moves and turns them, and a row that is filled all the way across
clears (the rows above drop down). The game ends if the stack reaches the top.
A challenge starts on an empty well with some rows already filled at the bottom, and is won by clearing a
number of rows; then the next challenge starts on a fresh well.
"rows" is the starting rows, top to bottom, each exactly 10 characters: "#" a filled square, "." an empty one;
every row needs at least 3 "#" and at least one "." (gaps the player has to fill); use 0-12 rows (an empty list
is a plain empty well). "goal" is how many rows the player must clear to win the challenge. "speed" is how fast
pieces fall: 1 is a gentle 0.8 seconds a row, 10 about 0.17 seconds a row, 15 about 0.08 seconds a row (very
fast). Starting rows with gaps lined up in one column are easy to clear; scattered gaps are harder.""",
    "target": lambda n: f"Challenge {n} should be about as hard as: speed {min(15, 1 + n)}, goal {min(40, 4 + 2 * n)}, "
    f"{min(12, n)} starting rows with {min(5, 1 + n // 3)} gaps each.",
    "example": {"name": "Zigzag", "rows": ["##.#######", "###.######", "##.#######"], "goal": 12, "speed": 5},
    "builtin": [
        {"name": "First rows", "rows": [], "goal": 5, "speed": 1},
        {"name": "Little hill", "rows": ["...###....", "..######.."], "goal": 8, "speed": 2},
        {"name": "Steps", "rows": ["###.......", "####......", "#####....."], "goal": 10, "speed": 3},
        {"name": "Two wells", "rows": ["##.####.##", "##.####.##", "##.####.##", "##.####.##"], "goal": 10, "speed": 4},
        {"name": "Checkers", "rows": ["#.#.#.#.#.", ".#.#.#.#.#", "#.#.#.#.#.", ".#.#.#.#.#"], "goal": 12, "speed": 5},
        {"name": "Valley", "rows": ["###....###", "####..####", "####..####", "#####.####", "#####.####"],
         "goal": 15, "speed": 6},
        {"name": "Swiss cheese", "rows": [".###.##.##", "##.###.###", "#.####.##.", "###.##.###", ".##.####.#",
                                          "####.##.##"], "goal": 15, "speed": 7},
        {"name": "Tall tower", "rows": ["...###....", "...####...", "...####...", "..######..", "..######..",
                                        ".########.", ".########.", "#########."], "goal": 20, "speed": 8},
        {"name": "Speedway", "rows": ["#########.", ".#########", "#########.", ".#########"], "goal": 20, "speed": 10},
        {"name": "Grand finale", "rows": ["#.#.#.#.#.", "##..##..##", "#.##.##.##", "###.###.##", ".####.####",
                                          "##.####.##", "####.#####", "#.########", "########.#", "#####.####"],
         "goal": 25, "speed": 12},
    ],
}
