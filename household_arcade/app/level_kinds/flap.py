"""Flap · Courses: a course is a run of gates with its own gap, speed, sway, spacing and a pattern of heights."""
from ..level_common import LevelError
from . import Int, Num, Text


def _check(c):
    h = c["heights"]
    if any(abs(int(a) - int(b)) > 4 for a, b in zip(h, h[1:])):
        raise LevelError("heights next to each other may differ by at most 4 (a flyer can't climb or drop more)")


KIND = {
    "game": "flap",
    "label": "Flap · Courses",
    "noun": "course",
    "modes": ["course"],
    # Ranges keep the honest-score limit true: at most 2.6 px an update with gates ≥ 110 px apart is
    # under 1.5 gates (points) a second.
    "fields": {
        "gates": Int(8, 40),
        "gap": Int(66, 110),
        "speed": Num(1.2, 2.6),
        "sway": Int(0, 24),
        "spacing": Int(110, 170),
        "heights": Text("0123456789", 4, 40),
    },
    "check": _check,
    "difficulty": lambda c: (110 - c["gap"]) * 1.2 + (c["speed"] - 1.2) * 25 + c["sway"] * 0.8
    + (170 - c["spacing"]) * 0.25 + max(abs(int(a) - int(b)) for a, b in zip(c["heights"], c["heights"][1:])) * 3,
    "about": """You design courses for Flap, a gentle family arcade game: a small flyer falls unless the player taps to
flap (one flap lifts it about 24 px) and must fly through the gaps in gates that scroll in from the right.
The play area is 240 px tall between the ceiling and the ground. Passing a gate scores 1. A course is a run of
gates; finishing it starts the next course.
"gap" is the opening's height in px (bigger is easier), "speed" how fast gates move in px per 1/60 s, "sway"
how far gaps drift up and down in px (0 = still), "spacing" the px from one gate to the next (more is easier),
"heights" a pattern of digits 0 (gap high up) to 9 (gap low down) used in turn for the gates, repeating; two
digits next to each other may differ by at most 4.""",
    "target": lambda n: f"Course {n} should be about as hard as: gap {max(66, 104 - 3 * n)}, speed "
    f"{min(2.6, round(1.4 + 0.06 * n, 2))}, spacing {max(110, 150 - 3 * n)}, sway {min(24, n)}.",
    "example": {"name": "Hill hop", "gates": 14, "gap": 88, "speed": 1.7, "sway": 6, "spacing": 128,
                "heights": "357531"},
    "builtin": [
        {"name": "First flight", "gates": 8, "gap": 104, "speed": 1.4, "sway": 0, "spacing": 150, "heights": "4455"},
        {"name": "Gentle hills", "gates": 10, "gap": 100, "speed": 1.4, "sway": 0, "spacing": 140, "heights": "34565432"},
        {"name": "Up and down", "gates": 12, "gap": 96, "speed": 1.5, "sway": 0, "spacing": 136, "heights": "2468642"},
        {"name": "Breeze", "gates": 12, "gap": 94, "speed": 1.5, "sway": 8, "spacing": 134, "heights": "5544332211"},
        {"name": "Staircase", "gates": 14, "gap": 90, "speed": 1.6, "sway": 0, "spacing": 130, "heights": "0123456789876543"},
        {"name": "Zigzag", "gates": 14, "gap": 88, "speed": 1.7, "sway": 0, "spacing": 128, "heights": "373737"},
        {"name": "Windy valley", "gates": 16, "gap": 88, "speed": 1.7, "sway": 14, "spacing": 126, "heights": "6789876"},
        {"name": "Treetops", "gates": 16, "gap": 84, "speed": 1.8, "sway": 0, "spacing": 124, "heights": "1212343"},
        {"name": "Storm", "gates": 18, "gap": 82, "speed": 1.9, "sway": 18, "spacing": 122, "heights": "48484"},
        {"name": "Long way home", "gates": 20, "gap": 80, "speed": 2.0, "sway": 10, "spacing": 120, "heights": "5373537573"},
    ],
}
