"""Rocks · Waves: a wave is a number of big and medium rocks at the start, how fast they drift and how likely a
saucer is to fly across."""
from ..level_common import LevelError
from . import Int, Num


def _check(c):
    if c["big"] * 2 + c["medium"] > 24:
        raise LevelError("too many rocks at once: big × 2 + medium must be at most 24")
    if c["speed"] > 1.2 and c["big"] * 2 + c["medium"] > 18:
        raise LevelError("fast waves (speed over 1.2) may have at most big × 2 + medium = 18")


KIND = {
    "game": "rocks",
    "label": "Rocks · Waves",
    "noun": "wave",
    "modes": ["waves"],
    # Ranges keep the honest-score limit true for any wave: points come only from your shots (at most one every
    # 10 updates, 100 a rock at most) and saucers (300, at most one every 10 s), whatever the wave.
    "fields": {
        "big": Int(2, 10),
        "medium": Int(0, 6),
        "speed": Num(0.4, 1.6),
        "saucer": Int(0, 60),
    },
    "check": _check,
    "difficulty": lambda c: (c["big"] - 2) * 4 + c["medium"] * 2.5 + (c["speed"] - 0.4) * 30 + c["saucer"] * 0.3,
    "about": """You design waves for Rocks, a family arcade game in space. The player's small ship sits in the middle of a
screen 240 px wide and 246 px tall whose edges wrap around; it turns, thrusts and shoots short-lived shots. Rocks
drift across the screen; a big rock that is shot breaks into two medium rocks, a medium rock into two small ones,
and a small one is gone. Bumping into a rock costs a life (3 lives). Clearing every rock ends the wave and the
next wave starts; clearing the last wave wins.
"big" is how many big rocks (22 px across from the middle) the wave starts with, "medium" how many medium rocks
(13 px) it starts with as well, "speed" the fastest a big rock drifts in px per 1/60 s (each one gets a speed
between half that and that; smaller rocks are a little faster), "saucer" the chance in percent, checked every
10 seconds, that a small saucer crosses the screen and shoots at the ship (0 = never). Big × 2 + medium must be at
most 24 (at most 18 when speed is over 1.2).""",
    "target": lambda n: f"Wave {n} should be about as hard as: big {min(10, 2 + n)}, medium {min(6, n // 2)}, speed "
    f"{min(1.6, round(0.5 + 0.08 * n, 2))}, saucer {0 if n < 3 else min(60, 10 + 5 * n)}.",
    "example": {"name": "Pebble storm", "big": 5, "medium": 3, "speed": 0.9, "saucer": 20},
    "builtin": [
        {"name": "First drift", "big": 2, "medium": 0, "speed": 0.5, "saucer": 0},
        {"name": "Quiet belt", "big": 3, "medium": 1, "speed": 0.6, "saucer": 0},
        {"name": "Stone circle", "big": 4, "medium": 2, "speed": 0.7, "saucer": 10},
        {"name": "Busy sky", "big": 5, "medium": 2, "speed": 0.8, "saucer": 20},
        {"name": "Moon crumbs", "big": 6, "medium": 3, "speed": 0.9, "saucer": 25},
        {"name": "Comet trail", "big": 6, "medium": 4, "speed": 1.05, "saucer": 30},
        {"name": "Boulder field", "big": 8, "medium": 3, "speed": 1.1, "saucer": 35},
        {"name": "Star dust", "big": 7, "medium": 4, "speed": 1.3, "saucer": 40},
        {"name": "Big crunch", "big": 9, "medium": 5, "speed": 1.2, "saucer": 50},
    ],
}
