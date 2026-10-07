"""Lander · Levels: a level is the ground (13 heights 20 px apart), where its pads are and what they're worth,
where the lander starts and how it drifts, gravity, wind and fuel."""
import math

from ..level_common import LevelError
from . import Int, List, Num, Text

FLOOR, START_Y, FOOT, THRUST = 290, 76, 7, 0.06


def fuel_needed(gravity):
    """A free fall from the start to the lowest ground, then a full burn — twice, + 100 (the game's own rule)."""
    dd = FLOOR - START_Y - FOOT
    net = THRUST - gravity
    v = math.sqrt(2 * dd * gravity * net / THRUST)
    return 100 + 2 * v / net


def _check(lv):
    h, pads = lv["heights"], lv["pads"]
    if not any(ch != "." for ch in pads):
        raise LevelError("a level needs at least one pad")
    for i, ch in enumerate(pads):
        if ch != "." and h[i] != h[i + 1]:
            raise LevelError(f"pad at stretch {i} isn't flat: heights {i} and {i + 1} must be equal")
    if lv["fuel"] < fuel_needed(lv["gravity"]) - 1:
        raise LevelError(f"too little fuel for that gravity (at least {math.ceil(fuel_needed(lv['gravity']))})")


def _difficulty(lv):
    best = max(int(ch) for ch in lv["pads"] if ch != ".")
    rough = sum(abs(a - b) for a, b in zip(lv["heights"], lv["heights"][1:])) / 12
    return (lv["gravity"] - 0.01) * 1500 + abs(lv["wind"]) * 2000 + (1000 - lv["fuel"]) / 25 + rough / 4 + \
        abs(lv["drift"]) * 6 - best


KIND = {
    "game": "lander",
    "label": "Lander · Levels",
    "noun": "level",
    "modes": ["levels"],
    # A landing scores at most 5 × 100 + 1,000 / 2 and takes at least a second plus the 2 s before the next level,
    # so the honest-score limits hold for any level.
    "fields": {
        "heights": List(Int(0, 150), 13, 13),
        "pads": Text(".12345", 12, 12),
        "start": Int(10, 230),
        "drift": Num(-1, 1),
        "gravity": Num(0.01, 0.04, 3),
        "wind": Num(-0.01, 0.01, 3),
        "fuel": Int(150, 1000),
    },
    "check": _check,
    "difficulty": _difficulty,
    "about": """You design levels for Lander, a family arcade game. A small lander starts near the top of a screen
240 px wide and must touch down gently and upright on a landing pad. Gravity pulls it down; its engine pushes it
the way it points (it turns up to 90° either way) and uses one unit of fuel per 1/60 s. Wind pushes it sideways.
"heights" is the ground: exactly 13 whole numbers 0-150, the ground's height in px at x = 0, 20, 40 … 240 (0 is
the bottom of the screen, 150 a high peak). "pads" is exactly 12 characters, one for each stretch between two
heights: "." no pad, or "1"-"5" a pad worth that many times 100 (×1 for a wide easy pad, ×5 for a small hard one);
a pad's two heights must be equal (flat), and next to each other the same digit makes one wider pad. "start" is the
lander's starting x (10-230), "drift" its starting sideways speed (-1 to 1 px per 1/60 s), "gravity" 0.01 (gentle)
to 0.04 (heavy), "wind" -0.01 to 0.01 (0 = none), "fuel" 150-1000 units; a level needs at least
100 + 2 × the burn a full stop from the top takes (about 200 fuel at gravity 0.015, 270 at 0.03, 330 at 0.04).""",
    "target": lambda n: f"Level {n} should be about: gravity {min(0.04, round(0.015 + 0.002 * n, 3))}, fuel "
    f"{max(300, 800 - 30 * n)}, wind up to {min(0.01, round(0.001 * n, 3))}, pads worth up to ×{min(5, 2 + n // 2)}.",
    "example": {"name": "Long valley", "heights": [80, 60, 40, 40, 40, 70, 100, 90, 60, 60, 90, 120, 140],
                "pads": "..22....3...", "start": 180, "drift": -0.2, "gravity": 0.02, "wind": 0.002, "fuel": 600},
    "builtin": [
        {"name": "Practice field", "heights": [40, 40, 40, 30, 30, 30, 30, 30, 50, 60, 60, 70, 80], "pads": "...111......",
         "start": 70, "drift": 0, "gravity": 0.015, "wind": 0, "fuel": 800},
        {"name": "Two valleys", "heights": [90, 60, 30, 30, 70, 110, 80, 40, 40, 60, 100, 120, 130], "pads": "..2....3....",
         "start": 120, "drift": 0.3, "gravity": 0.018, "wind": 0, "fuel": 700},
        {"name": "Crater rim", "heights": [120, 100, 70, 40, 20, 20, 20, 40, 80, 80, 110, 130, 140], "pads": "....11..4...",
         "start": 200, "drift": -0.4, "gravity": 0.02, "wind": 0, "fuel": 650},
        {"name": "Windy ridge", "heights": [30, 50, 80, 80, 60, 30, 30, 50, 90, 120, 120, 90, 60], "pads": "..3..2...4..",
         "start": 40, "drift": 0.2, "gravity": 0.02, "wind": 0.004, "fuel": 650},
        {"name": "Needle peaks", "heights": [20, 90, 140, 60, 60, 130, 50, 50, 120, 140, 140, 70, 20], "pads": "...4..3..5..",
         "start": 120, "drift": 0, "gravity": 0.024, "wind": 0, "fuel": 600},
        {"name": "Deep canyon", "heights": [140, 140, 130, 100, 50, 10, 10, 10, 60, 110, 130, 140, 140], "pads": "5....22.....",
         "start": 180, "drift": -0.5, "gravity": 0.026, "wind": -0.003, "fuel": 600},
        {"name": "High shelf", "heights": [60, 80, 120, 150, 150, 110, 70, 40, 40, 40, 80, 100, 100], "pads": "...4...11..3",
         "start": 60, "drift": 0.6, "gravity": 0.03, "wind": 0.002, "fuel": 550},
        {"name": "Storm plain", "heights": [50, 50, 70, 40, 40, 80, 100, 60, 60, 30, 30, 50, 70], "pads": "3..2...4.5..",
         "start": 200, "drift": -0.3, "gravity": 0.03, "wind": -0.007, "fuel": 550},
        {"name": "Last stop", "heights": [130, 100, 60, 60, 120, 150, 140, 90, 90, 140, 80, 20, 20], "pads": "..3....5...2",
         "start": 120, "drift": 0.5, "gravity": 0.036, "wind": 0.005, "fuel": 500},
    ],
}
