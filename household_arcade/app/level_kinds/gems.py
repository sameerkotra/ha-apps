"""Gem Swap · Levels (the Moves mode): a level is a number of moves, the points to make in them, how many kinds
of gem there are and which gems start locked."""
from ..level_common import LevelError
from . import Grid, Int

SIZE = 8


def _locks(lv):
    return sum(r.count("L") for r in lv["locks"])


def _check(lv):
    if lv["goal"] > lv["moves"] * 80:
        raise LevelError("the goal needs more moves (at most 80 points a move)")
    if _locks(lv) > 16:
        raise LevelError("at most 16 locked gems")
    if _locks(lv) * 2 > lv["moves"]:
        raise LevelError("too many locks for the moves (at most one for every two moves)")


KIND = {
    "game": "gems",
    "label": "Gem Swap · Levels",
    "noun": "level",
    "modes": ["moves"],
    # Points come only from gems cleared (10 × at most 5 for a cascade), special gems made and the moves left at
    # the end (50 each, at most 40), so the honest-score limits hold for any level.
    "fields": {
        "moves": Int(10, 40),
        "goal": Int(300, 6000),
        "kinds": Int(4, 6),
        "locks": Grid(".L", (SIZE, SIZE), (SIZE, SIZE)),
    },
    "check": _check,
    "difficulty": lambda lv: lv["goal"] / lv["moves"] * 0.8 + (lv["kinds"] - 4) * 12 + _locks(lv) * 2.5,
    "about": """You design levels for Gem Swap, a family puzzle game on an 8 × 8 board of gems. The player swaps two
neighbouring gems to make a line of three or more of a kind; the line clears (10 points a gem, more when new
lines form as gems fall: a cascade), gems above fall and new ones drop in. Four in a line leaves a gem that clears
its row or column, an L or T shape one that clears the 3 × 3 around it, five in a line a star that clears every
gem of one kind. A locked gem can't be moved; a line through it breaks the lock.
A level is won by making "goal" points within "moves" swaps and breaking every lock; moves left over score 50
each. "kinds" is how many kinds of gem there are (4 is easy, 6 is hard: lines are rarer). "locks" is the board:
exactly 8 strings of exactly 8 characters, "." a free gem and "L" a gem that starts locked. The goal may be at
most 80 points a move, there may be at most 16 locks, and at most one lock for every two moves. Locks in the
middle are easier to break than locks at the edges and corners.""",
    "target": lambda n: f"Level {n} should be about: moves {min(40, 18 + n)}, goal {min(6000, 500 + 150 * n)}, "
    f"kinds {min(6, 4 + n // 3)}, {min(16, max(0, n - 2) * 2)} locks.",
    "example": {"name": "Diamond", "moves": 26, "goal": 1300, "kinds": 5,
                "locks": ["........", "........", "...LL...", "..L..L..", "...LL...", "........", "........", "........"]},
    "builtin": [
        {"name": "First sparkle", "moves": 20, "goal": 600, "kinds": 4, "locks": ["........"] * 8},
        {"name": "Five colours", "moves": 20, "goal": 900, "kinds": 5, "locks": ["........"] * 8},
        {"name": "Middle lock", "moves": 22, "goal": 900, "kinds": 5,
         "locks": ["........", "........", "........", "...LL...", "...LL...", "........", "........", "........"]},
        {"name": "Full set", "moves": 25, "goal": 1300, "kinds": 6, "locks": ["........"] * 8},
        {"name": "Four spots", "moves": 25, "goal": 1200, "kinds": 5,
         "locks": ["........", "........", "..L..L..", "........", "........", "..L..L..", "........", "........"]},
        {"name": "Ring road", "moves": 30, "goal": 1400, "kinds": 6,
         "locks": ["........", "........", "...LL...", "..L..L..", "..L..L..", "...LL...", "........", "........"]},
        {"name": "Low shelf", "moves": 26, "goal": 1400, "kinds": 6,
         "locks": ["........", "........", "........", "........", "........", "........", "..LLLL..", "........"]},
        {"name": "Treasure box", "moves": 34, "goal": 1800, "kinds": 6,
         "locks": ["........", "........", "..LLLL..", "..L..L..", "..L..L..", "..LLLL..", "........", "........"]},
    ],
}
