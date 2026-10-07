"""City Defense · Waves: a wave is how many missiles fall, how fast, how many of them split in three, how many
fliers cross and drop one more, and each base's ammo."""
from ..level_common import LevelError
from . import Int, Num


def _check(w):
    if w["splits"] > w["missiles"]:
        raise LevelError("splits can't be more than missiles")
    if w["missiles"] + 2 * w["splits"] + 2 * w["fliers"] > 3 * w["ammo"] * 1.5:
        raise LevelError("too little ammo: missiles + 2 × splits + 2 × fliers must be at most 4.5 × ammo")
    if w["speed"] > 1 and w["missiles"] > 20:
        raise LevelError("fast waves (speed over 1) may have at most 20 missiles")


KIND = {
    "game": "defense",
    "label": "City Defense · Waves",
    "noun": "wave",
    "modes": ["waves"],
    # A wave pays at most 50 missiles × 25 + 4 fliers × 100 + 45 ammo × 5 + 6 cities × 100 and lasts at least 8.5 s
    # with its bonus count, whatever the wave: within the honest-score limits.
    "fields": {
        "missiles": Int(4, 30),
        "speed": Num(0.3, 1.4),
        "splits": Int(0, 8),
        "fliers": Int(0, 4),
        "ammo": Int(6, 15),
    },
    "check": _check,
    "difficulty": lambda w: w["missiles"] * 1.6 + (w["speed"] - 0.3) * 40 + w["splits"] * 3 + w["fliers"] * 3
    - (w["ammo"] - 6) * 1.5,
    "about": """You design waves for City Defense, a family arcade game. Missiles fall from the top of the screen toward
six cities and three launch bases at the bottom; the player taps the sky to send an interceptor from the nearest
base, which bursts into a cloud that stops every missile inside it. Some missiles split into three on the way
down; fliers cross the sky and each drops one missile. A wave ends when everything it sent is gone.
"missiles" is how many missiles the wave sends (spread over at least 4 seconds, longer for more), "speed" how
fast they fall in px per 1/60 s (0.3 slow, 1.4 very fast), "splits" how many of them split in three, "fliers"
how many fliers cross, "ammo" each base's interceptors for the wave. missiles + 2 × splits + 2 × fliers must be at
most 4.5 × ammo, and a wave faster than 1 may have at most 20 missiles.""",
    "target": lambda n: f"Wave {n} should be about: missiles {min(30, 6 + 2 * n)}, speed {min(1.4, round(0.4 + 0.06 * n, 2))}, "
    f"splits {min(8, max(0, n - 2))}, fliers {min(4, n // 3)}, ammo {min(15, 10 + n // 3)}.",
    "example": {"name": "Meteor night", "missiles": 14, "speed": 0.65, "splits": 3, "fliers": 1, "ammo": 11},
    "builtin": [
        {"name": "First light", "missiles": 6, "speed": 0.4, "splits": 0, "fliers": 0, "ammo": 10},
        {"name": "Evening rain", "missiles": 8, "speed": 0.45, "splits": 0, "fliers": 1, "ammo": 10},
        {"name": "Split sky", "missiles": 10, "speed": 0.5, "splits": 2, "fliers": 0, "ammo": 10},
        {"name": "Night watch", "missiles": 12, "speed": 0.55, "splits": 2, "fliers": 1, "ammo": 10},
        {"name": "Shower", "missiles": 16, "speed": 0.6, "splits": 3, "fliers": 1, "ammo": 11},
        {"name": "Fast hail", "missiles": 12, "speed": 0.85, "splits": 2, "fliers": 2, "ammo": 10},
        {"name": "Starfall", "missiles": 18, "speed": 0.75, "splits": 4, "fliers": 2, "ammo": 12},
        {"name": "Long night", "missiles": 22, "speed": 0.8, "splits": 5, "fliers": 3, "ammo": 13},
        {"name": "Last stand", "missiles": 26, "speed": 0.9, "splits": 6, "fliers": 3, "ammo": 14},
    ],
}
