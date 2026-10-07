"""Type Rain · Stages: a stage is how many words fall, how fast, how far apart, and how long the words are
(1 and 1: single letters, for little ones)."""
from ..level_common import LevelError
from . import Int, Num

GROUND, TOP_Y = 196, 46


def fall_seconds(speed: float) -> float:
    return (GROUND - TOP_Y) / speed / 60


def _check(t):
    if t["shortest"] > t["longest"]:
        raise LevelError("shortest must not be more than longest")
    if not ((t["shortest"] == 1 and t["longest"] == 1) or t["shortest"] >= 3):
        raise LevelError("shortest and longest are both 1 (single letters), or shortest is at least 3 (words)")
    if fall_seconds(t["speed"]) < 1 + 0.35 * t["longest"]:
        raise LevelError("too fast for its longest words: a word must take at least 1 + 0.35 × longest seconds to fall "
                         "(150 px at speed px per 1/60 s)")


KIND = {
    "game": "typerain",
    "label": "Type Rain · Stages",
    "noun": "stage",
    "modes": ["stages"],
    # A word scores at most 10 a letter × 10 letters × 3 and words come at least 40 updates apart; a stage 200
    # (≥ 5 words): the honest-score limits hold for any stage.
    "fields": {
        "words": Int(5, 40),
        "speed": Num(0.15, 1.4, 3),
        "gap": Int(40, 200),
        "shortest": Int(1, 10),
        "longest": Int(1, 10),
    },
    "check": _check,
    "difficulty": lambda t: t["words"] * 0.5 + t["speed"] * 40 + (200 - t["gap"]) * 0.12 + t["longest"] * 3,
    "about": """You design stages for Type Rain, a family typing game: words fall from the top of the screen (150 px
down to the ground) and the player types each one before it lands; a word that lands costs one of 3 lives for the
whole list. The words come from a family-friendly list, 3 to 10 letters long; a stage can instead drop single
letters for little ones.
"words" is how many words fall in the stage (5 to 40). "speed" is how fast they fall in px per 1/60 s (0.15 slow,
0.6 quick; words of 7 letters or more fall at 85 % of it). "gap" is the time between two words in 1/60 s (40 to
200). "shortest" and "longest" are the word lengths: both 1 means single letters, otherwise shortest is at least 3
and at most longest. Leave time to type: a word must take at least 1 + 0.35 × longest seconds to fall, that is
150 / speed / 60 >= 1 + 0.35 × longest.""",
    "target": lambda n: f"Stage {n} should be about: words {min(40, 10 + 2 * n)}, speed {min(0.6, round(0.22 + 0.04 * n, 2))}, "
    f"gap {max(45, 140 - 8 * n)}, shortest {min(6, 3 + n // 4)}, longest {min(10, 4 + n // 2)}.",
    "example": {"name": "Light rain", "words": 12, "speed": 0.3, "gap": 120, "shortest": 3, "longest": 5},
    "builtin": [
        {"name": "First drops", "words": 10, "speed": 0.25, "gap": 120, "shortest": 1, "longest": 1},
        {"name": "Little words", "words": 10, "speed": 0.25, "gap": 140, "shortest": 3, "longest": 3},
        {"name": "Four letters", "words": 12, "speed": 0.3, "gap": 130, "shortest": 3, "longest": 4},
        {"name": "Drizzle", "words": 14, "speed": 0.35, "gap": 110, "shortest": 4, "longest": 5},
        {"name": "Shower", "words": 15, "speed": 0.4, "gap": 100, "shortest": 4, "longest": 6},
        {"name": "Long words", "words": 12, "speed": 0.3, "gap": 140, "shortest": 6, "longest": 8},
        {"name": "Downpour", "words": 18, "speed": 0.45, "gap": 80, "shortest": 3, "longest": 6},
        {"name": "Storm", "words": 20, "speed": 0.5, "gap": 70, "shortest": 4, "longest": 7},
        {"name": "Big storm", "words": 20, "speed": 0.4, "gap": 90, "shortest": 6, "longest": 10},
        {"name": "Cloudburst", "words": 25, "speed": 0.55, "gap": 60, "shortest": 3, "longest": 8},
    ],
}
