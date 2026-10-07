"""Colour Sort · Puzzle book: tubes drawn as strings of colour letters, checked by a search that sorts them."""
from . import List, Text
from . import _sorting as S
from ..level_common import LevelError

MOST = 12


def _check(c):
    tubes = S.parse(c["tubes"], MOST)
    if not S.solvable(tubes, pour_all=True):
        raise LevelError("these tubes can't be sorted (or it takes too long to work out)")


def _difficulty(c):
    colours = len({ch for t in c["tubes"] for ch in t})
    spare = len(c["tubes"]) - colours
    mixed = sum(1 for t in c["tubes"] for a, b in zip(t, t[1:]) if a != b)
    return min(100, (colours - 2) * 8 + (3 - spare) * 6 + mixed * 1.5)


def _fingerprint(c):
    return ["|".join(sorted(c["tubes"]))]          # the same tubes in any order


KIND = {
    "game": "watersort",
    "label": "Colour Sort · Puzzle book",
    "noun": "puzzle",
    "modes": ["book"],
    "fields": {"tubes": List(Text(S.LETTERS, 0, 4), 3, 15)},
    "check": _check,
    "difficulty": _difficulty,
    "fingerprint": _fingerprint,
    "about": """You design puzzles for Colour Sort, a calm family puzzle. There are tubes of coloured water; each tube holds
4 layers. The player taps a tube, then another: the top colour pours across (every layer of it that fits) if the
other tube is empty or has the same colour on top and room. The goal is every colour in a full tube of its own.
"tubes" lists the tubes, each a string of colour letters from the bottom of the tube to the top ("" is an empty tube,
"ABBA" is A at the bottom, then B, B and A on top). Use the colours A, B, C … in order without skipping one (2-12
colours), every colour exactly 4 times, and 1-3 more tubes than colours (usually 2 empty ones). Mix the colours well
(not already sorted). The app checks that it can be sorted. More colours, fewer spare tubes and more mixing are
harder.""",
    "target": lambda n: f"Puzzle {n} should have about {min(12, 3 + n // 3)} colours and "
    f"{2 if n < 30 else 1 if n % 3 == 0 else 2} spare tube(s).",
    "example": {"name": "Pond", "tubes": ["ABCA", "BCAB", "CABC", "", ""]},
    "builtin": [
        {"name": "Three jars", "tubes": ["ABAA", "BCBC", "CCAB", "", ""]},
        {"name": "Lemonade", "tubes": ["BCAC", "DADD", "CBAD", "CBBA", "", ""]},
        {"name": "Paint pots", "tubes": ["BEED", "ABDB", "DEAC", "AABC", "ECDC", "", ""]},
        {"name": "Rainbow", "tubes": ["CDDA", "DBBA", "DCFA", "CFEE", "BAEE", "FFBC", "", ""]},
        {"name": "Fruit punch", "tubes": ["FFGB", "AEBE", "GCCA", "EHBH", "HDAB", "GDCF", "CAED", "HDFG", "", ""]},
        {"name": "Potion shelf", "tubes": ["GCGA", "BIEE", "AGHH", "BHFH", "DFFD", "DABE", "BICA", "CECI", "IDFG", "", ""]},
    ],
}
