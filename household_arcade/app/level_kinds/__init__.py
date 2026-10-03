"""Level lists for every game besides Brick Breaker and Snake · Maze (SPEC §11.9).

Each game describes its levels in one module here (`<game>.py`) as a `KIND` dict, and this package checks
them all the same way: the model writes data, never code, and every field is checked against its range
before anyone can play a level. A kind is:

    KIND = {
        "game": "flap",                      # the game id (and the list's id)
        "label": "Flap · Courses",           # shown on Admin → Levels
        "noun": "course",                    # what one level is called ("level", "opponent", "course" …)
        "modes": ["course"],                 # the game's modes that play the list
        "fields": {"gates": Int(10, 40), …}, # every field but "name" (added here), with its check
        "check": fn(clean) or None,          # extra rules across fields; raises LevelError
        "difficulty": fn(clean) -> 0–100,
        "fingerprint": fn(clean) -> [str],   # optional: the same for two levels that play the same
        "about": "…",                        # for the model: the game, what a level is, the rules
        "target": fn(number) -> "…",         # for the model: how hard level `number` should be
        "example": {…},                      # one level in the answer format
        "builtin": [{…}, …],                 # the built-in levels: the same as the game file's LEVELS
    }

The game's rules file (`static/games/<game>-logic.js`) has the same built-in list (`LEVELS`, checked
equal by the tests) and checks the shape again (`usableLevels`) before playing a level.
"""
from __future__ import annotations

import importlib
import json
import math

from ..level_common import LevelError, check_name

# The games with a kind module here, in the order of the game table.
GAMES = ("blocks", "duel", "racer", "flap", "mines", "merge", "colours", "cards", "mole", "numbers",
         "tanks", "invaders", "rocks", "hop", "snakeduel")


# ---------------------------------------------------------------------------
# Field checks: each is called with (value, field name) and returns the clean value or raises LevelError.
# ---------------------------------------------------------------------------

def _num(v, field):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise LevelError(f"{field} must be a number")
    return v


def Int(lo: int, hi: int):
    def check(v, field):
        v = _num(v, field)
        if v != int(v) or not lo <= v <= hi:
            raise LevelError(f"{field} must be a whole number {lo}–{hi}")
        return int(v)
    check.describe = f"whole number {lo}-{hi}"
    return check


def Num(lo: float, hi: float, places: int = 2):
    def check(v, field):
        v = _num(v, field)
        if not lo <= v <= hi:
            raise LevelError(f"{field} must be a number {lo}–{hi}")
        return round(float(v), places)
    check.describe = f"number {lo}-{hi}"
    return check


def Bool():
    def check(v, field):
        if not isinstance(v, bool):
            raise LevelError(f"{field} must be true or false")
        return v
    check.describe = "true or false"
    return check


def Choice(*values):
    def check(v, field):
        if v not in values:
            raise LevelError(f"{field} must be one of " + ", ".join(json.dumps(x) for x in values))
        return v
    check.describe = "one of " + ", ".join(json.dumps(x) for x in values)
    return check


def Text(chars: str, lo: int, hi: int):
    """A string of only these characters, lo–hi long (e.g. a pattern of digits)."""
    allowed = set(chars)

    def check(v, field):
        if not isinstance(v, str):
            raise LevelError(f"{field} must be text")
        v = v.strip()
        if not lo <= len(v) <= hi or not set(v) <= allowed:
            raise LevelError(f'{field} must be {lo}–{hi} characters of "{chars}"')
        return v
    check.describe = f'text of {lo}-{hi} characters from "{chars}"'
    return check


def Grid(chars: str, rows: tuple[int, int], width: tuple[int, int]):
    """A list of rows (strings) of the same length, only these characters."""
    allowed = set(chars)

    def check(v, field):
        if not isinstance(v, list) or not all(isinstance(r, str) for r in v):
            raise LevelError(f"{field} must be a list of strings")
        v = [r.strip() for r in v]
        if not rows[0] <= len(v) <= rows[1]:
            raise LevelError(f"{field} must have {rows[0]}–{rows[1]} rows")
        if v and (len({len(r) for r in v}) != 1 or not width[0] <= len(v[0]) <= width[1]):
            raise LevelError(f"every row of {field} must be the same length, {width[0]}–{width[1]} characters")
        if any(not set(r) <= allowed for r in v):
            raise LevelError(f'{field} may only use the characters "{chars}"')
        return v
    check.describe = f'list of {rows[0]}-{rows[1]} rows, each {width[0]}-{width[1]} characters of "{chars}"'
    check.grid = True
    return check


def List(item, lo: int, hi: int):
    def check(v, field):
        if not isinstance(v, list) or not lo <= len(v) <= hi:
            raise LevelError(f"{field} must be a list of {lo}–{hi} items")
        return [item(x, f"{field}[{i}]") for i, x in enumerate(v)]
    check.describe = f"list of {lo}-{hi} items, each a {getattr(item, 'describe', 'value')}"
    return check


# ---------------------------------------------------------------------------
# The kinds
# ---------------------------------------------------------------------------

KINDS: dict[str, dict] = {}
for _g in GAMES:
    try:
        _m = importlib.import_module(f"{__name__}.{_g}")
    except ModuleNotFoundError as e:          # a game whose kind isn't written yet
        if e.name != f"{__name__}.{_g}":
            raise
        continue
    KINDS[_m.KIND["game"]] = _m.KIND


def validate(kind: dict, data) -> tuple[dict, int, list[str]]:
    """(clean data, difficulty 0–100, fingerprint strings) for a level of this kind. Raises LevelError."""
    if not isinstance(data, dict):
        raise LevelError("a level must be an object")
    fields = kind["fields"]
    keys = set(fields) | {"name"}
    extra, missing = set(data) - keys, keys - set(data)
    if extra:
        raise LevelError("unknown field(s): " + ", ".join(sorted(map(str, extra))))
    if missing:
        raise LevelError("missing field(s): " + ", ".join(sorted(missing)))
    clean = {"name": check_name(data["name"])}
    for f, check in fields.items():
        clean[f] = check(data[f], f)
    if kind.get("check"):
        kind["check"](clean)
    difficulty = max(0, min(100, int(round(kind["difficulty"](clean)))))
    if kind.get("fingerprint"):
        prints = list(kind["fingerprint"](clean))
    else:
        prints = [json.dumps({k: v for k, v in clean.items() if k != "name"}, sort_keys=True)]
    return clean, difficulty, prints


def format_text(kind: dict) -> str:
    """The answer format, field by field, for the model."""
    lines = ['- "name": a short friendly name, 1-4 words, letters and spaces only, fine for children']
    for f, check in kind["fields"].items():
        lines.append(f'- "{f}": {getattr(check, "describe", "value")}')
    return "\n".join(lines)


def prompt(kind: dict, count: int, first_number: int, recent: list[dict]) -> str:
    """The request to the model for `count` new levels starting at number `first_number`."""
    shown = []
    for r in recent[-6:]:
        r = {k: v for k, v in r.items() if k not in ("number", "difficulty")}
        shown.append(f'{kind["noun"]} {len(shown) + 1}: ' + json.dumps(r, separators=(",", ":")))
    example = json.dumps({"levels": [kind["example"]]}, separators=(",", ":"))
    noun = kind["noun"]
    return f"""{kind["about"].strip()}

Each {noun} is a JSON object with these fields:
{format_text(kind)}

These are {noun}s {first_number} to {first_number + count - 1}. {kind["target"](first_number).strip()}
Make each one a little harder than the one before, and make them different from the recent {noun}s below and \
from each other.

The most recent {noun}s:
{chr(10).join(shown) if shown else "(none yet)"}

Answer with JSON only, exactly {count} {noun}(s), in this shape:
{example}"""


def summary(kind: dict, data: dict) -> str:
    """A one-line description for Admin → Levels (the fields that aren't grids)."""
    parts = []
    for f, check in kind["fields"].items():
        if getattr(check, "grid", False):
            continue
        v = data.get(f)
        if isinstance(v, list):
            v = f"{len(v)} items"
        parts.append(f"{f} {v}")
    return " · ".join(parts)

