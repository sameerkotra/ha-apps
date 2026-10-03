"""Number Dash · Challenges: a challenge is a kind of sums, how big the numbers get, a target and a clock."""
from ..level_common import LevelError
from . import Int, Text


def _min_time(c):
    big_sums = any(op in c["ops"] for op in "+-") and c["top"] > 99
    return c["right"] * (3 if big_sums else 2)


def _check(c):
    if len(set(c["ops"])) != len(c["ops"]):
        raise LevelError("ops must not repeat a sign")
    if c["time"] < _min_time(c):
        raise LevelError("time must be at least 2 seconds a right answer (3 with + or - and top over 99)")


KIND = {
    "game": "numbers",
    "label": "Number Dash · Challenges",
    "noun": "challenge",
    "modes": ["challenge"],
    # Ranges keep the honest-score limit true: answers still come at most one per 12 updates and the level
    # in the points is capped at 25 (≤ 180 an answer), however long the list is.
    "fields": {
        "ops": Text("+-x/", 1, 4),
        "top": Int(5, 999),
        "right": Int(5, 25),
        "time": Int(20, 90),
    },
    "check": _check,
    "difficulty": lambda c: min(40, c["top"] / 12 if any(o in c["ops"] for o in "+-") else 0)
    + (min(12, c["top"]) * 2 if any(o in c["ops"] for o in "x/") else 0) + len(c["ops"]) * 4
    + c["right"] * 0.8 + max(0, 60 - c["time"] + c["right"]) * 0.4,
    "about": """You design challenges for Number Dash, a quick family maths game: a sum appears with four answers
around it and the player picks the right one before the clock runs out. A right answer adds 1 second (never
above the starting time), a wrong one takes 3 seconds off. Getting enough right answers clears the challenge
and the next one starts with a fresh clock; if the clock reaches zero the game is over.
"ops" is which sums come up, 1 to 4 different signs: "+" adding, "-" taking away, "x" times, "/" sharing
(dividing exactly). "top" is how big the numbers get: for "+" and "-" the biggest number in the sum (the total,
or the number taken away from), 5 to 999; for "x" and "/" the biggest times table, min(top, 12), with the
other number 1 to 10 (1 to 12 for the 11 and 12 tables) - so a top of 12 or more means every table up to 12.
"right" is how many right answers clear it (5 to 25). "time" is the seconds on the clock at its start (20 to
90); give at least 2 seconds a right answer, 3 when there are "+" or "-" sums with top over 99.""",
    "target": lambda n: f"Challenge {n} should be about as hard as: top {min(999, 10 * n)} for adding and "
    f"taking away (or tables up to {min(12, 4 + n)}), right {min(25, 6 + n)}, time "
    f"{max(30, min(90, 2 * min(25, 6 + n) + max(0, 40 - 2 * n)))}, with {min(4, 1 + n // 3)} kinds of sum.",
    "example": {"name": "Quick tables", "ops": "x", "top": 9, "right": 10, "time": 40},
    "builtin": [
        {"name": "Warm up", "ops": "+", "top": 10, "right": 6, "time": 60},
        {"name": "Take away", "ops": "-", "top": 10, "right": 6, "time": 50},
        {"name": "Small tables", "ops": "x", "top": 5, "right": 8, "time": 60},
        {"name": "Up to twenty", "ops": "+-", "top": 20, "right": 8, "time": 50},
        {"name": "Sharing out", "ops": "/", "top": 6, "right": 8, "time": 60},
        {"name": "Fifty", "ops": "+-", "top": 50, "right": 10, "time": 60},
        {"name": "Times tables", "ops": "x/", "top": 10, "right": 12, "time": 60},
        {"name": "Hundred club", "ops": "+-", "top": 100, "right": 12, "time": 60},
        {"name": "All sorts", "ops": "+-x/", "top": 12, "right": 15, "time": 60},
        {"name": "Big numbers", "ops": "+-x/", "top": 500, "right": 20, "time": 90},
    ],
}
