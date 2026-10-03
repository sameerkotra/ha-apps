"""Paddle Duel · Opponents: an opponent is the computer player of one match, with its own paddle speed,
mistakes, aim, serve speed and points to win."""
from . import Int, Num

KIND = {
    "game": "duel",
    "label": "Paddle Duel · Opponents",
    "noun": "opponent",
    "modes": ["easy", "normal", "hard"],
    # Ranges keep the honest-score limit (3,500 a second) true: points are 100 and a match 1,000, each
    # × min(match number, 10), so a long list can't raise them; the quickest a match can go (a 3.6 serve,
    # 3 points, a computer that never touches the ball) is about 3.7 s for 13,000 points (~3,200 a second).
    "fields": {
        "speed": Num(1.0, 4.4),
        "miss": Num(0.05, 0.45, 3),
        "aim": Num(0.4, 0.9),
        "ball": Num(2.0, 3.6),
        "toWin": Int(3, 7),
    },
    "check": None,
    "difficulty": lambda o: (o["speed"] - 1.0) * 14 + (0.45 - o["miss"]) * 90 + (o["aim"] - 0.4) * 30
    + (o["ball"] - 2.0) * 8 + (o["toWin"] - 3) * 2,
    "about": """You design opponents for Paddle Duel, a family bat-and-ball game: the player moves a paddle along the
bottom of a 240 px wide court and the computer moves one along the top; a ball bounces between them and off the
side walls. Letting the ball past your paddle gives the other side a point. Each opponent plays one match; win
it and the next opponent comes on, lose it and the game ends.
"speed" is how fast the computer's paddle moves in px per 1/60 s (1.0 slow, 4.4 very quick; the ball moves
2-6 px per 1/60 s). "miss" is the chance (0.05-0.45) it misjudges a ball and lets it past; lower is harder.
"aim" is how far from the middle of its paddle it hits the ball, as a share of the paddle (0.4 near the middle:
gentle, straight returns; 0.9 near the edge: steep, angled returns that are harder for the player). "ball" is
the serve speed in px per 1/60 s (2.0 gentle, 3.6 fast). "toWin" is the points needed to win the match (3-7).
The game's Easy and Normal and Hard settings make every opponent a little slower or quicker on top of this.""",
    "target": lambda n: f"Opponent {n} should be about as hard as: speed {min(4.4, round(1.7 + 0.24 * (n - 1), 2))}, "
    f"miss {max(0.05, round(0.3 - 0.024 * (n - 1), 3))}, aim {min(0.9, round(0.5 + 0.04 * n, 2))}, "
    f"ball {min(3.6, round(2.4 + 0.12 * (n - 1), 2))}, toWin {min(7, 5 + n // 12)}.",
    "example": {"name": "Busy Bee", "speed": 2.5, "miss": 0.2, "aim": 0.7, "ball": 2.8, "toWin": 5},
    "builtin": [
        {"name": "Rookie", "speed": 1.7, "miss": 0.3, "aim": 0.54, "ball": 2.4, "toWin": 5},
        {"name": "Rally", "speed": 1.94, "miss": 0.276, "aim": 0.58, "ball": 2.52, "toWin": 5},
        {"name": "Swift", "speed": 2.18, "miss": 0.252, "aim": 0.62, "ball": 2.64, "toWin": 5},
        {"name": "Wall", "speed": 2.42, "miss": 0.228, "aim": 0.66, "ball": 2.76, "toWin": 5},
        {"name": "Spin", "speed": 2.66, "miss": 0.204, "aim": 0.7, "ball": 2.88, "toWin": 5},
        {"name": "Echo", "speed": 2.9, "miss": 0.18, "aim": 0.74, "ball": 3.0, "toWin": 5},
        {"name": "Blaze", "speed": 3.14, "miss": 0.156, "aim": 0.78, "ball": 3.12, "toWin": 5},
        {"name": "Ace", "speed": 3.38, "miss": 0.132, "aim": 0.82, "ball": 3.24, "toWin": 5},
        {"name": "Storm", "speed": 3.62, "miss": 0.108, "aim": 0.86, "ball": 3.36, "toWin": 5},
        {"name": "Champion", "speed": 3.86, "miss": 0.084, "aim": 0.9, "ball": 3.48, "toWin": 5},
    ],
}
