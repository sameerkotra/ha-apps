"""Memory Cards · Challenges: a board is a grid of face-down cards with its own size, time, peek and symbol kinds."""
from ..level_common import LevelError
from . import Int


def _check(c):
    n = c["cols"] * c["rows"]
    if n % 2:
        raise LevelError("cols × rows must be even (every card has a partner)")
    if n > 30:
        raise LevelError("cols × rows may be at most 30 cards")
    if c["symbols"] > n // 2:
        raise LevelError(f"symbols may be at most the number of pairs ({n // 2} for this board)")


def _target(n):
    cols, rows = min(6, 3 + n // 3), min(5, 4 + n // 6)
    if cols * rows % 2:
        rows += 1
    cards = cols * rows
    secs = max(20, min(150, round(cards * max(2.5, 4.2 - 0.1 * n))))
    return (f"Board {n} should be about as hard as: cols {cols}, rows {rows}, time {secs}, "
            f"peek {max(0, 3 - n // 3)}, symbols {min(15, cards // 2, 6 + n)}.")


KIND = {
    "game": "cards",
    "label": "Memory Cards · Challenges",
    "noun": "board",
    "modes": ["challenge"],
    # Ranges keep the honest-score limits true: ≤ 15 pairs (pairs 20 × streak ≤ 2,400, miss bonus ≤ 150) and
    # ≤ 150 s (time bonus ≤ 750) make a board worth ≤ 3,300; a card takes 6 updates to turn and boards are
    # 90 updates apart, so even a 12-card board takes ≥ 156 updates (under 500 points a second).
    "fields": {
        "cols": Int(3, 6),
        "rows": Int(3, 6),
        "time": Int(20, 150),
        "peek": Int(0, 5),
        "symbols": Int(4, 15),
    },
    "check": _check,
    "difficulty": lambda c: c["cols"] * c["rows"] * 1.6 + (150 - c["time"]) * 0.25 + (5 - c["peek"]) * 4
    + c["symbols"] * 1.2 - 20,
    "about": """You design boards for Memory Cards, a gentle family memory game: cards lie face down in a grid; the player
turns two at a time, and two with the same symbol stay face up as a pair, otherwise both turn back. Clearing
the board before the time runs out starts the next board; time running out ends the game.
"cols" and "rows" are the grid's columns (3-6) and rows (3-6); cols × rows must be even and at most 30 (more
cards is harder). "time" is the seconds to clear the board (less is harder; about 4 s a card is relaxed,
3 s a card is quick). "peek" is how many seconds every face is shown before play starts (0-5; the clock
waits; 0 is hardest). "symbols" is how many different symbols the board uses (4-15, at most cols × rows / 2):
with fewer symbols than pairs some symbols are on 4 or 6 cards and any two alike make a pair, which is easier.""",
    "target": _target,
    "example": {"name": "Toy box", "cols": 4, "rows": 5, "time": 75, "peek": 2, "symbols": 10},
    "builtin": [
        {"name": "Warm up", "cols": 3, "rows": 4, "time": 60, "peek": 3, "symbols": 6},
        {"name": "Garden path", "cols": 4, "rows": 4, "time": 70, "peek": 3, "symbols": 8},
        {"name": "Twins", "cols": 4, "rows": 4, "time": 50, "peek": 2, "symbols": 4},
        {"name": "Picnic", "cols": 4, "rows": 5, "time": 80, "peek": 2, "symbols": 10},
        {"name": "Quick look", "cols": 4, "rows": 5, "time": 70, "peek": 1, "symbols": 10},
        {"name": "Shoebox", "cols": 6, "rows": 4, "time": 90, "peek": 2, "symbols": 12},
        {"name": "No peeking", "cols": 4, "rows": 6, "time": 85, "peek": 0, "symbols": 12},
        {"name": "Big table", "cols": 5, "rows": 6, "time": 120, "peek": 1, "symbols": 15},
        {"name": "Look alikes", "cols": 6, "rows": 5, "time": 90, "peek": 0, "symbols": 8},
        {"name": "Grand memory", "cols": 5, "rows": 6, "time": 95, "peek": 0, "symbols": 15},
    ],
}
