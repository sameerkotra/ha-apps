"""Colour Memory · Challenges: grow a sequence of lit pads to a length, at its own speed and direction."""
from ..level_common import LevelError
from . import Bool, Int


def _check(c):
    if c["start"] > c["length"]:
        raise LevelError("start can't be more than length (the sequence only grows)")


KIND = {
    "game": "colours",
    "label": "Colour Memory · Challenges",
    "noun": "challenge",
    "modes": ["challenge"],
    # Ranges keep the honest-score limits true: a pad lights ≥ 10 updates with ≥ 10 between, so a round of
    # n pads (worth 10n) takes ≥ 80 + 20n updates (< 30 points a second); a challenge is worth at most
    # 10 × (1 + … + 30) = 4,650.
    "fields": {
        "length": Int(4, 30),
        "light": Int(10, 40),
        "gap": Int(10, 30),
        "reverse": Bool(),
        "start": Int(1, 5),
    },
    "check": _check,
    "difficulty": lambda c: (c["length"] - 4) * 2.2 + (40 - c["light"]) * 0.7 + (30 - c["gap"]) * 0.5
    + (12 if c["reverse"] else 0) + (c["start"] - 1) * 1.5,
    "about": """You design challenges for Colour Memory, a gentle family memory game: four coloured pads (red up, green
left, blue right, yellow down) light up one after another, then the player repeats the order by tapping them.
Each round the sequence plays again with one more pad added. A challenge is cleared when the player repeats a
sequence of "length" pads correctly; the next challenge starts a fresh sequence. One wrong pad ends the game.
"length" is how many pads the sequence grows to (more is harder; 4-8 is easy, 12 and up is hard for most
people), "start" how many pads the sequence has in its first round (1-5; it may not be more than length),
"light" how long each pad stays lit and "gap" the pause between two pads, both in 1/60 s updates (smaller is
faster and harder; 36 and 24 are relaxed, 12 and 10 are very quick), "reverse" true means the player must
repeat the sequence from last to first (harder).""",
    "target": lambda n: f"Challenge {n} should be about as hard as: length {min(30, 4 + n)}, start "
    f"{min(5, 1 + n // 3)}, light {max(10, 36 - 2 * n)}, gap {max(10, 24 - n)}, reverse "
    f"{'true' if n % 3 == 0 else 'false'}.",
    "example": {"name": "Bird song", "length": 8, "light": 26, "gap": 18, "reverse": False, "start": 3},
    "builtin": [
        {"name": "First steps", "length": 4, "light": 36, "gap": 24, "reverse": False, "start": 1},
        {"name": "Little tune", "length": 6, "light": 32, "gap": 22, "reverse": False, "start": 2},
        {"name": "Look back", "length": 5, "light": 34, "gap": 22, "reverse": True, "start": 2},
        {"name": "Quick fingers", "length": 7, "light": 24, "gap": 16, "reverse": False, "start": 3},
        {"name": "Long song", "length": 10, "light": 28, "gap": 18, "reverse": False, "start": 4},
        {"name": "Mirror walk", "length": 8, "light": 26, "gap": 18, "reverse": True, "start": 3},
        {"name": "Hummingbird", "length": 10, "light": 16, "gap": 12, "reverse": False, "start": 5},
        {"name": "Upside down", "length": 10, "light": 20, "gap": 14, "reverse": True, "start": 4},
        {"name": "Marathon", "length": 14, "light": 20, "gap": 14, "reverse": False, "start": 5},
        {"name": "Grand finale", "length": 14, "light": 12, "gap": 10, "reverse": True, "start": 5},
    ],
}
