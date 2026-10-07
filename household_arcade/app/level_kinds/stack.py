"""Tower Stack · Towers: a tower is a number of floors to build, the starting width of the blocks, how fast they
slide and how much faster each floor makes them."""
from ..level_common import LevelError
from . import Bool, Int, Num

MAX_V = 5


def _check(t):
    if t["speed"] + t["ramp"] * t["floors"] > MAX_V:
        raise LevelError(f"speed + ramp × floors must be at most {MAX_V} (the top floor's speed)")


KIND = {
    "game": "stack",
    "label": "Tower Stack · Towers",
    "noun": "tower",
    "modes": ["towers"],
    # A floor scores at most 60 and blocks come at least 10 updates apart, a tower 200: the honest-score limits
    # hold for any tower.
    "fields": {
        "floors": Int(8, 60),
        "width": Int(40, 160),
        "speed": Num(1, 3.5),
        "ramp": Num(0, 0.06, 3),
        "grow": Bool(),
    },
    "check": _check,
    "difficulty": lambda t: t["floors"] * 0.6 + (160 - t["width"]) * 0.25 + (t["speed"] + t["ramp"] * t["floors"]) * 10
    + (0 if t["grow"] else 8),
    "about": """You design towers for Tower Stack, a family arcade game. A block slides from side to side above the
tower (the play area is 224 px wide); the player taps to drop it. Whatever hangs over the edge of the floor below
is cut off, so the next block is only as wide as the overlap; a drop within 3 px lines up exactly (a perfect
drop), and with "grow" on, three perfect drops in a row widen the block again a little. Missing the tower costs
one of 3 lives. Building the tower's floors finishes it.
"floors" is how many floors to build, "width" the width in px of the first block (40 is narrow, 160 wide), "speed"
how fast the blocks slide in px per 1/60 s at the start and "ramp" how much faster each floor is; speed + ramp ×
floors must be at most 5. "grow" is true or false.""",
    "target": lambda n: f"Tower {n} should be about: floors {min(60, 8 + 3 * n)}, width {max(60, 150 - 8 * n)}, "
    f"speed {min(3.5, round(1.2 + 0.15 * n, 2))}, ramp {min(0.06, round(0.02 + 0.005 * n, 3))}.",
    "example": {"name": "Water tower", "floors": 16, "width": 110, "speed": 1.7, "ramp": 0.03, "grow": True},
    "builtin": [
        {"name": "Garden shed", "floors": 10, "width": 140, "speed": 1.2, "ramp": 0.02, "grow": True},
        {"name": "Corner shop", "floors": 12, "width": 130, "speed": 1.5, "ramp": 0.02, "grow": True},
        {"name": "Town hall", "floors": 15, "width": 120, "speed": 1.6, "ramp": 0.03, "grow": True},
        {"name": "Clock tower", "floors": 18, "width": 110, "speed": 1.8, "ramp": 0.03, "grow": True},
        {"name": "Lighthouse", "floors": 20, "width": 90, "speed": 1.8, "ramp": 0.04, "grow": True},
        {"name": "Office block", "floors": 24, "width": 120, "speed": 2.2, "ramp": 0.03, "grow": False},
        {"name": "Needle", "floors": 20, "width": 70, "speed": 2, "ramp": 0.04, "grow": True},
        {"name": "Sky hotel", "floors": 30, "width": 130, "speed": 2.4, "ramp": 0.04, "grow": False},
        {"name": "Cloud piercer", "floors": 36, "width": 110, "speed": 2.6, "ramp": 0.06, "grow": True},
    ],
}
