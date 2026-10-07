"""Runner · Courses: a course is a string of 60 px segments (each empty, a hazard or coins), its starting speed
and how much faster it gets every 10 segments."""
import math

from ..level_common import LevelError
from . import Num, Text

SEG, VMAX = 60, 8
HAZARDS = "bBphf"


def gap_segs(v):
    """The fewest segments from one hazard to the next at speed v (room to land and jump again)."""
    return math.ceil(50 * v / SEG)


def speed_at(c, i):
    """The speed at segment i of the pattern (the game adds 6 empty segments in front)."""
    return min(VMAX, c["speed"] + c["ramp"] * (i // 10))


def _check(c):
    last, hazards = None, 0
    for i, ch in enumerate(c["pattern"]):
        if ch not in HAZARDS:
            continue
        hazards += 1
        if last is not None and i - last < gap_segs(speed_at(c, i)):
            raise LevelError(f"hazards at segments {last} and {i} are too close: at that speed they must be at least "
                             f"{gap_segs(speed_at(c, i))} segments apart")
        last = i
    if hazards < 3:
        raise LevelError("a course needs at least 3 hazards")


KIND = {
    "game": "runner",
    "label": "Runner · Courses",
    "noun": "course",
    "modes": ["courses"],
    # Points: 1 a 10 px at ≤ 8 px an update and coins (10, at most 5 a segment), a course 100 — within the limits
    # whatever the course.
    "fields": {
        "speed": Num(3, 7),
        "ramp": Num(0, 1),
        "pattern": Text("._bBphfco", 10, 120),
    },
    "check": _check,
    "difficulty": lambda c: (c["speed"] - 3) * 12 + c["ramp"] * 25 + sum(ch in HAZARDS for ch in c["pattern"]) * 1.2
    + c["pattern"].count("p") * 0.8,
    "about": """You design courses for Runner, a family arcade game. A runner races to the right on its own; the player
jumps (hold for higher) and ducks. The course is a string of segments 60 px long, one character each:
"." (or "_") nothing, "b" a box to jump, "B" a tall box (a high jump), "p" a pit to jump, "h" a hanging bar to duck
under (it can't be jumped), "f" a flier at head height (duck under it or jump over it), "c" three coins on the
ground, "o" an arc of five coins in the air (jump for them). The runner has 3 lives for all the courses.
"speed" is the starting speed in px per 1/60 s (3 is gentle, 7 very fast), "ramp" how much faster it gets every
10 segments (the speed never goes above 8). "pattern" is 10 to 120 characters. After a hazard (b, B, p, h, f) the
next hazard must be at least ceil(50 × speed / 60) segments further on, using the speed at that later segment:
3 segments at speed 3.6, 4 up to 4.8, 5 up to 6, 6 up to 7.2, 7 up to 8. At least 3 hazards.""",
    "target": lambda n: f"Course {n} should be about: speed {min(7, round(3.2 + 0.3 * n, 2))}, ramp "
    f"{min(1, round(0.1 * n, 2))}, {min(120, 24 + 6 * n)} segments, a hazard every {max(4, 7 - n // 3)} segments.",
    "example": {"name": "High street", "speed": 4, "ramp": 0.2, "pattern": "..b...f..c.p....h..o..B....p..."},
    "builtin": [
        {"name": "Park run", "speed": 3.2, "ramp": 0, "pattern": "..c..b...o...b...c..b...."},
        {"name": "Hop and skip", "speed": 3.4, "ramp": 0.2, "pattern": "..b...b..c.B...o..b...f...c..b..."},
        {"name": "Duck season", "speed": 3.6, "ramp": 0.2, "pattern": "..f...h..c.f...b..o..h...f...c..b..."},
        {"name": "Mind the gap", "speed": 3.8, "ramp": 0.2, "pattern": "..p...b..c.p...o..B...p...f..c..h...p...."},
        {"name": "Market street", "speed": 4, "ramp": 0.3, "pattern": "..b...f...h..c.p...B...o..f....b....h..c..p....b...."},
        {"name": "Rooftops", "speed": 4.4, "ramp": 0.3,
         "pattern": "..p....p....c.b....h....o..f....B....p..c..h....b....p...."},
        {"name": "Night train", "speed": 4.8, "ramp": 0.3,
         "pattern": "..h....f....b..c..p....B....o..h....p....f..c..b.....h.....p......"},
        {"name": "Express", "speed": 5.2, "ramp": 0.4,
         "pattern": "..b.....p.....h..c..f.....B.....o..p.....h.....b..c..f.....p......B......"},
    ],
}
