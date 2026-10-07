"""Bolt Sort · Puzzle book: bolts drawn as strings of colour letters, checked by a search that sorts them."""
from . import Bool, List, Text
from . import _sorting as S
from ..level_common import LevelError

MOST = 10


def _check(c):
    bolts = S.parse(c["bolts"], MOST)
    if not S.solvable(bolts, pour_all=False):
        raise LevelError("these bolts can't be sorted (or it takes too long to work out)")


def _difficulty(c):
    colours = len({ch for t in c["bolts"] for ch in t})
    spare = len(c["bolts"]) - colours
    mixed = sum(1 for t in c["bolts"] for a, b in zip(t, t[1:]) if a != b)
    return min(100, (colours - 2) * 7 + (3 - spare) * 6 + mixed * 1.2 + (20 if c["hidden"] else 0))


def _fingerprint(c):
    return [str(c["hidden"]) + "|" + "|".join(sorted(c["bolts"]))]


KIND = {
    "game": "bolts",
    "label": "Bolt Sort · Puzzle book",
    "noun": "puzzle",
    "modes": ["book"],
    "fields": {"bolts": List(Text(S.LETTERS[:MOST], 0, 4), 3, 13), "hidden": Bool()},
    "check": _check,
    "difficulty": _difficulty,
    "fingerprint": _fingerprint,
    "about": """You design puzzles for Bolt Sort, a calm family puzzle. There are bolts with coloured nuts on them; each bolt
holds 4 nuts. The player moves one nut at a time: the top nut of a bolt onto an empty bolt, or onto a nut of the same
colour on a bolt with room. The goal is every colour on a full bolt of its own.
"bolts" lists the bolts, each a string of colour letters from the bottom of the bolt to the top ("" is an empty bolt,
"ABBA" is A at the bottom, then B, B and A on top). Use the colours A, B, C … in order without skipping one (2-10
colours), every colour exactly 4 times, and 1-3 more bolts than colours (usually 2 empty ones). Mix the colours well
(not already sorted). "hidden": true hides every nut under the top one until it comes to the top (much harder:
the player has to guess); use it only for the harder puzzles. The app checks that it can be sorted. More colours,
fewer spare bolts, more mixing and hidden nuts are harder.""",
    "target": lambda n: f"Puzzle {n} should have about {min(10, 3 + n // 4)} colours, "
    f"{'hidden nuts' if n >= 15 and n % 2 else 'every nut shown'}.",
    "example": {"name": "Toolbox", "bolts": ["ABCA", "BCAB", "CABC", "", ""], "hidden": False},
    "builtin": [
        {"name": "Spare parts", "bolts": ["BBBA", "ACAC", "CACB", "", ""], "hidden": False},
        {"name": "Workbench", "bolts": ["CBDB", "CAAC", "BDDB", "ADAC", "", ""], "hidden": False},
        {"name": "Bike shed", "bolts": ["EBCE", "ABDB", "DCAC", "DDEA", "EBCA", "", ""], "hidden": False},
        {"name": "Garage", "bolts": ["CBEF", "DCDA", "ADBD", "FFEF", "BBCC", "EEAA", "", ""], "hidden": False},
        {"name": "Mystery jar", "bolts": ["DDFA", "DCAF", "BFEE", "BCAD", "BFEE", "CCBA", "", ""], "hidden": True},
        {"name": "Robot kit", "bolts": ["FGDF", "CGGA", "EABA", "BFCG", "DABC", "DEFE", "EBDC", "", ""], "hidden": True},
    ],
}
