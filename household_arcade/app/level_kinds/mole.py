"""Tap the Mole · Gardens: a garden is a shape of holes with its own pace, hedgehogs and golden moles."""
from ..level_common import LevelError
from . import Grid, Int, Num


def _holes(g):
    return sum(r.count("o") for r in g["holes"])


def _check(g):
    if _holes(g) < 5:
        raise LevelError('holes must have at least 5 holes ("o")')


KIND = {
    "game": "mole",
    "label": "Tap the Mole · Gardens",
    "noun": "garden",
    "modes": ["gardens"],
    # Ranges keep the honest-score limit true: the rules never let critters come less than 24 updates
    # apart (every ≥ 24) nor golden moles less than 180 apart, whatever the garden; points are 10 and 50.
    "fields": {
        "holes": Grid("o.", (3, 5), (3, 5)),
        "bonks": Int(8, 40),
        "up": Int(36, 120),
        "every": Int(24, 90),
        "hedgehogs": Num(0, 0.3),
        "golden": Num(0, 0.2),
    },
    "check": _check,
    "difficulty": lambda g: (120 - g["up"]) * 0.45 + (90 - g["every"]) * 0.35 + g["hedgehogs"] * 60
    + (g["bonks"] - 8) * 0.3 + _holes(g) * 0.3,
    "about": """You design gardens for Tap the Mole, a gentle family tapping game: friendly moles pop up out of holes
in a garden, stay up a moment and duck down again; the player taps a mole while it is up to bonk it (10
points; a golden mole, up for a shorter time, is worth 50). Sometimes a spiky hedgehog pops up instead and
must be left alone. The player has three lives for the whole game: a mole that gets away or a tapped hedgehog
costs one. Bonking enough moles clears the garden and the next garden starts.
"holes" is the garden's shape: 3 to 5 rows, all the same length of 3 to 5 characters, "o" a hole and "." plain
grass, with at least 5 holes (more holes and odd shapes are harder to watch). "bonks" is how many moles to bonk
to clear it. "up" is how long a mole stays up, in 1/60 s (120 = 2 seconds, easy; 36 = 0.6 s, very quick).
"every" is the time between one critter and the next, in 1/60 s (90 = slow; 24 = very busy). "hedgehogs" is
the share of critters that are hedgehogs (0 to 0.3) and "golden" the share that are golden moles (0 to 0.2).""",
    "target": lambda n: f"Garden {n} should be about as hard as: up {max(36, 112 - 7 * n)}, every "
    f"{max(24, 72 - 4 * n)}, hedgehogs {min(0.3, round(0.03 * n, 2))}, bonks {min(40, 8 + 2 * n)}, "
    f"about {min(25, 5 + n)} holes.",
    "example": {"name": "Corner beds", "holes": ["oo.oo", "o...o", "oo.oo"], "bonks": 15, "up": 80, "every": 48,
                "hedgehogs": 0.12, "golden": 0.1},
    "builtin": [
        {"name": "Little patch", "holes": [".o.", "ooo", ".o."], "bonks": 8, "up": 110, "every": 70, "hedgehogs": 0, "golden": 0.1},
        {"name": "Square bed", "holes": ["ooo", "ooo", "ooo"], "bonks": 10, "up": 100, "every": 60, "hedgehogs": 0, "golden": 0.12},
        {"name": "Two long rows", "holes": ["oooo", "....", "oooo"], "bonks": 12, "up": 92, "every": 56, "hedgehogs": 0.1, "golden": 0.12},
        {"name": "Flower ring", "holes": ["oooo", "o..o", "o..o", "oooo"], "bonks": 14, "up": 84, "every": 50, "hedgehogs": 0.12, "golden": 0.1},
        {"name": "Cabbage rows", "holes": ["o.o.o", "o.o.o", "o.o.o"], "bonks": 16, "up": 76, "every": 46, "hedgehogs": 0.15, "golden": 0.1},
        {"name": "Diamond", "holes": ["..o..", ".ooo.", "ooooo", ".ooo.", "..o.."], "bonks": 18, "up": 70, "every": 42, "hedgehogs": 0.18, "golden": 0.1},
        {"name": "Big lawn", "holes": ["oooo", "oooo", "oooo", "oooo"], "bonks": 20, "up": 62, "every": 38, "hedgehogs": 0.2, "golden": 0.12},
        {"name": "Checkers", "holes": ["o.o.o", ".o.o.", "o.o.o", ".o.o.", "o.o.o"], "bonks": 22, "up": 56, "every": 34, "hedgehogs": 0.22, "golden": 0.1},
        {"name": "Hedgehog hill", "holes": ["ooooo", "o.o.o", "ooooo"], "bonks": 24, "up": 50, "every": 30, "hedgehogs": 0.28, "golden": 0.08},
        {"name": "Grand garden", "holes": ["ooooo", "ooooo", "ooooo", "ooooo", "ooooo"], "bonks": 30, "up": 44, "every": 26, "hedgehogs": 0.25, "golden": 0.15},
    ],
}
