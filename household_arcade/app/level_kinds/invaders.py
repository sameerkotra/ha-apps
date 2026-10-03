"""Sky Defenders · Waves: a wave is a formation of critters with its own marching speed, bombs and shields."""
from ..level_common import LevelError
from . import Grid, Int, Num

POINTS = {"a": 30, "b": 20, "c": 10}


def _count(w):
    return sum(len(r) - r.count(".") for r in w["formation"])


def _check(w):
    if _count(w) < 6:
        raise LevelError('formation must have at least 6 critters ("a", "b" or "c")')


def _difficulty(w):
    return (_count(w) / 66 * 30 + (w["speed"] - 0.5) * 25 + w["bombs"] * 3 + (4 - w["shields"]) * 3
            + len(w["formation"]) * 0.5)


KIND = {
    "game": "invaders",
    "label": "Sky Defenders · Waves",
    "noun": "wave",
    "modes": ["waves"],
    # Ranges can't break the honest-score limit: the rules allow one shot at least 15 updates after the last
    # (≤ 4 critters a second, ≤ 30 points each) and a bonus ship (≤ 300) at most every 1,200 updates,
    # whatever the wave; at most 66 critters and 2 ships a wave.
    "fields": {
        "formation": Grid(".abc", (3, 6), (11, 11)),
        "speed": Num(0.5, 2.0),
        "bombs": Int(1, 10),
        "shields": Int(0, 4),
    },
    "check": _check,
    "difficulty": _difficulty,
    "about": """You design waves for Sky Defenders, a friendly family shooting game. A formation of little cartoon
critters marches side to side across the top of the screen, stepping down a little each time it reaches an
edge, and drops bombs. The player moves a cannon along the bottom and fires one shot at a time, hiding behind
shields that wear away. Clearing every critter finishes the wave and the next wave starts; if the formation
reaches the bottom the game ends.
"formation" is the formation as 3 to 6 rows (top row first) of exactly 11 characters: "." is an empty place,
"a" a small critter worth 30 points, "b" a middle one worth 20, "c" a big one worth 10 (usually "a" at the top
and "c" at the bottom; fun shapes are welcome); at least 6 critters. "speed" is how fast the formation marches
(0.5 slow to 2 fast; the formation always speeds up as fewer critters are left). "bombs" is how often bombs
drop (1 rarely, 10 very often; more bombs can fall at once too). "shields" is how many shields protect the
cannon (0 to 4; fewer is harder).""",
    "target": lambda n: f"Wave {n} should be about as hard as: about {min(66, 30 + 4 * n)} critters in "
    f"{min(6, 3 + n // 3)} rows, speed {min(2.0, round(0.6 + 0.08 * n, 2))}, bombs {min(10, 2 + n // 2)}, "
    f"shields {max(0, 4 - n // 6)}.",
    "example": {"name": "Crown", "formation": ["a.a.a.a.a.a", "aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc"],
                "speed": 0.9, "bombs": 4, "shields": 4},
    "builtin": [
        {"name": "First visitors", "formation": [".aaaaaaaaa.", "bbbbbbbbbbb", "ccccccccccc"], "speed": 0.6, "bombs": 2, "shields": 4},
        {"name": "Busy bees", "formation": ["aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc", "ccccccccccc"], "speed": 0.7, "bombs": 3, "shields": 4},
        {"name": "Checkerboard", "formation": ["a.a.a.a.a.a", ".b.b.b.b.b.", "c.c.c.c.c.c", ".c.c.c.c.c.", "c.c.c.c.c.c"], "speed": 0.8, "bombs": 3, "shields": 4},
        {"name": "Big parade", "formation": ["aaaaaaaaaaa", "bbbbbbbbbbb", "bbbbbbbbbbb", "ccccccccccc", "ccccccccccc"], "speed": 0.8, "bombs": 4, "shields": 4},
        {"name": "Arrowhead", "formation": [".....a.....", "....aaa....", "...bbbbb...", "..bbbbbbb..", ".ccccccccc.", "ccccccccccc"], "speed": 0.9, "bombs": 5, "shields": 3},
        {"name": "Twin towers", "formation": ["aaa.....aaa", "bbb.....bbb", "bbb.....bbb", "ccc.....ccc", "ccc.....ccc"], "speed": 1.0, "bombs": 5, "shields": 3},
        {"name": "Storm front", "formation": ["a.a.a.a.a.a", "bbbbbbbbbbb", ".b.b.b.b.b.", "ccccccccccc", "c.c.c.c.c.c"], "speed": 1.1, "bombs": 7, "shields": 3},
        {"name": "Last stand", "formation": ["aaaaaaaaaaa", "aaaaaaaaaaa", "bbbbbbbbbbb", "bbbbbbbbbbb", "ccccccccccc", "ccccccccccc"], "speed": 1.3, "bombs": 8, "shields": 2},
    ],
}
