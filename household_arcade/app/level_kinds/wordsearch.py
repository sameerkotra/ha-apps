"""Word Search · Themes: a theme's name and its words, for one age group's grid; the game places the words."""
import re

from ..level_common import _BLOCKED, LevelError
from . import Choice, List, Text

# The age groups (wordsearch-logic.js MODES): grid size, words hidden, word lengths.
GROUPS = {"little": (7, 5, 3, 5), "kids": (9, 8, 4, 6), "family": (11, 10, 5, 8), "puzzler": (13, 12, 6, 11)}
SPARE = 4                    # words beyond what the grid hides, so each game picks a different few


def _check(c):
    words = c["words"]
    if len(set(words)) != len(words):
        raise LevelError("a word is listed twice")
    if any(w in _BLOCKED or any(b in w for b in _BLOCKED if len(b) > 3) for w in words):
        raise LevelError("a word isn't suitable for children")
    n, hidden, lo, hi = GROUPS[c["group"]]
    usable = [w for w in words if lo <= len(w) <= hi and w != w[::-1]]
    if len(usable) < hidden + SPARE:
        raise LevelError(f'for "{c["group"]}" give at least {hidden + SPARE} words of {lo}-{hi} letters '
                         f"(this has {len(usable)})")
    if re.search(r"[^a-z ]", c["name"].lower()):
        raise LevelError("the theme name may only use letters and spaces")


def _difficulty(c):
    return {"little": 5, "kids": 30, "family": 60, "puzzler": 85}[c["group"]]


def _fingerprint(c):
    return [c["group"] + ":" + ",".join(sorted(c["words"]))]


KIND = {
    "game": "wordsearch",
    "label": "Word Search · Themes",
    "noun": "theme",
    "modes": ["themes"],
    "fields": {
        "group": Choice("little", "kids", "family", "puzzler"),
        "words": List(Text("abcdefghijklmnopqrstuvwxyz", 3, 11), 9, 40),
    },
    "check": _check,
    "difficulty": _difficulty,
    "fingerprint": _fingerprint,
    "about": """You write themes for Word Search, a family word game. A grid of letters hides words from one theme; the
player finds them. The game picks a few words from the theme's list each time and places them, so give more words
than one grid hides. The name is the theme ("Under the sea", "At the farm", "Space trip"), shown to the player.
"group" is who it's for and sets the grid: "little" (age 5-7: 7 × 7 grid, 5 words of 3-5 letters), "kids" (8-11:
9 × 9, 8 words of 4-6 letters), "family" (everyone: 11 × 11, 10 words of 5-8 letters), "puzzler" (13 × 13, 12 words
of 6-11 letters).
"words": 9-40 words, lower case letters a-z only (no spaces, hyphens or accents), every word different, everyday
words a child would know for that age, nothing rude or frightening. At least 4 more words of the group's lengths
than the grid hides (little 9, kids 12, family 14, puzzler 16 words of the right length).""",
    "target": lambda n: f"Theme {n} should be for the {['little', 'kids', 'family', 'puzzler'][n % 4]} group.",
    "example": {"name": "Pond life", "group": "kids", "words": [
        "frog", "duck", "newt", "reed", "lily", "swan", "snail", "toad", "fish", "heron", "water", "mud", "beetle",
        "insect", "ripple"]},
    "builtin": [
        {"name": "At the farm", "group": "little", "words": [
            "cow", "pig", "hen", "goat", "duck", "lamb", "barn", "egg", "hay", "sheep", "horse", "mud", "dog"]},
        {"name": "Under the sea", "group": "kids", "words": [
            "fish", "crab", "shark", "whale", "coral", "squid", "shell", "wave", "reef", "seal", "eel", "kelp",
            "turtle", "diver", "oyster", "pearl"]},
        {"name": "Space trip", "group": "kids", "words": [
            "moon", "star", "comet", "orbit", "rocket", "planet", "alien", "mars", "earth", "venus", "space",
            "solar", "galaxy", "crater"]},
        {"name": "In the kitchen", "group": "family", "words": [
            "kettle", "spoon", "fridge", "oven", "teapot", "saucepan", "grater", "ladle", "plates", "toaster",
            "whisk", "napkin", "spatula", "freezer", "blender", "cupboard", "cutlery", "colander"]},
        {"name": "Music room", "group": "puzzler", "words": [
            "guitar", "violin", "trumpet", "piano", "drummer", "cymbals", "melody", "rhythm", "harmony", "concert",
            "orchestra", "trombone", "clarinet", "keyboard", "saxophone", "conductor", "xylophone", "tambourine",
            "harmonica", "recorder", "singer", "choir"]},
    ],
}
