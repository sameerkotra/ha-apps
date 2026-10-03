"""Lane Racer · Stages: a stage is a stretch of road with its own lanes, speed, number of traffic rows to the
finish line, and how much traffic, how many trucks and how many coins it has."""
from . import Choice, Int, Num

KIND = {
    "game": "racer",
    "label": "Lane Racer · Stages",
    "noun": "stage",
    "modes": ["stages"],
    # Ranges keep the honest-score limit (150 a second) true: 1 point a 10 px of road at ≤ 7.5 px an update
    # is ≤ 45 a second, and at most one 25-point coin a row of traffic, rows ≥ 212 px apart at that speed
    # (≤ 2.2 a second, ~53 points); slower roads have fewer rows a second.
    "fields": {
        "lanes": Choice(3, 4),
        "speed": Num(2.5, 7.5),
        "rows": Int(15, 80),
        "traffic": Num(0.2, 0.9),
        "trucks": Num(0.0, 0.5),
        "coins": Num(0.0, 0.8),
    },
    "check": None,
    "difficulty": lambda c: (c["speed"] - 2.5) * 11 + (c["traffic"] - 0.2) * 30 + c["trucks"] * 20
    + (c["rows"] - 15) * 0.2 + (5 if c["lanes"] == 3 else 0),
    "about": """You design stages for Lane Racer, a family driving game: the player's car drives up a straight road
of 3 or 4 lanes and changes lanes to get past rows of slower traffic coming down the screen (the road is 240 px
tall; a car is 38 px long, a truck 62 px). Every row leaves a way through that one lane change can reach, so a
careful driver never has to crash. A stage ends at a finish line after its rows of traffic; then the next stage
starts. The player has 3 lives for the whole run; a crash costs one.
"lanes" is 3 or 4. "speed" is how fast the road moves in px per 1/60 s (2.5 gentle, 5 brisk, 7.5 very fast).
"rows" is how many rows of traffic there are before the finish line (about 1-2 rows a second). "traffic" is how
full each row is beyond the one car every row has (0.2 mostly single cars, 0.9 nearly every lane but the way
through). "trucks" is the share of vehicles that are long trucks (0-0.5; trucks are harder to get round).
"coins" is the chance of a bonus coin on the road after each row (0-0.8).""",
    "target": lambda n: f"Stage {n} should be about as hard as: speed {min(7.5, round(3 + 0.4 * (n - 1), 2))}, "
    f"rows {min(80, 15 + 5 * n)}, traffic {min(0.9, round(0.2 + 0.07 * n, 2))}, trucks {min(0.5, round(0.1 + 0.04 * n, 2))}; "
    "3 lanes is harder than 4 at the same traffic.",
    "example": {"name": "Seaside", "lanes": 3, "speed": 4, "rows": 28, "traffic": 0.4, "trucks": 0.2, "coins": 0.5},
    "builtin": [
        {"name": "Sunday drive", "lanes": 3, "speed": 3, "rows": 15, "traffic": 0.2, "trucks": 0.1, "coins": 0.5},
        {"name": "Country road", "lanes": 3, "speed": 3.5, "rows": 20, "traffic": 0.3, "trucks": 0.15, "coins": 0.5},
        {"name": "Market day", "lanes": 4, "speed": 3.5, "rows": 25, "traffic": 0.4, "trucks": 0.2, "coins": 0.45},
        {"name": "Coast road", "lanes": 3, "speed": 4.25, "rows": 30, "traffic": 0.4, "trucks": 0.2, "coins": 0.5},
        {"name": "Rush hour", "lanes": 4, "speed": 4.5, "rows": 35, "traffic": 0.6, "trucks": 0.25, "coins": 0.4},
        {"name": "Truck stop", "lanes": 3, "speed": 5, "rows": 40, "traffic": 0.5, "trucks": 0.45, "coins": 0.4},
        {"name": "Night run", "lanes": 4, "speed": 5.75, "rows": 45, "traffic": 0.6, "trucks": 0.3, "coins": 0.45},
        {"name": "Highway home", "lanes": 4, "speed": 6.5, "rows": 50, "traffic": 0.7, "trucks": 0.3, "coins": 0.5},
    ],
}
