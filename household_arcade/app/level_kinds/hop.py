"""Road Hop · Levels: a level is a stack of road, river and grass lanes and a time limit for each crossing."""
import math

from ..level_common import LevelError
from . import Int, List

LETTERS = {"road": "ct", "river": "ls", "grass": ""}
RUNS = {"c": (1, 2), "t": (2, 4), "l": (2, 6), "s": (2, 4)}      # how long one car / truck / log / raft may be
SPEEDS = {"road": (0.3, 2.0), "river": (0.3, 1.2), "grass": (0.3, 2.0)}
LANE_KEYS = {"kind", "pattern", "speed", "dir"}


def runs(pattern: str) -> list[tuple[str, int]]:
    """The runs of the same letter going round the pattern (it repeats), as (letter, length), without gaps."""
    n = len(pattern)
    first = next((i for i in range(n) if pattern[i] != pattern[i - 1]), None)
    if first is None:
        return [] if pattern[0] == "." else [(pattern[0], n)]
    out, k = [], 0
    while k < n:
        at = (first + k) % n
        ch, length = pattern[at], 0
        while length < n and pattern[(at + length) % n] == ch:
            length += 1
        if ch != ".":
            out.append((ch, length))
        k += length
    return out


def longest_gap(pattern: str) -> int:
    if "." not in pattern:
        return 0
    if set(pattern) == {"."}:
        return len(pattern)
    n, best = len(pattern), 0
    for i in range(n):
        if pattern[i] != "." or pattern[i - 1] == ".":
            continue
        length = 0
        while pattern[(i + length) % n] == ".":
            length += 1
        best = max(best, length)
    return best


def Lane():
    def check(v, field):
        if not isinstance(v, dict):
            raise LevelError(f"{field} must be an object")
        if set(v) != LANE_KEYS:
            raise LevelError(f'{field} must have exactly "kind", "pattern", "speed" and "dir"')
        kind, pattern, speed, direction = v["kind"], v["pattern"], v["speed"], v["dir"]
        if kind not in LETTERS:
            raise LevelError(f'{field}.kind must be "road", "river" or "grass"')
        if not isinstance(pattern, str) or not 13 <= len(pattern.strip()) <= 26:
            raise LevelError(f"{field}.pattern must be 13–26 characters")
        pattern = pattern.strip()
        if not set(pattern) <= set("." + LETTERS[kind]):
            raise LevelError(f'{field}.pattern of a {kind} lane may only use "." and "{LETTERS[kind]}"')
        if direction not in ("left", "right"):
            raise LevelError(f'{field}.dir must be "left" or "right"')
        lo, hi = SPEEDS[kind]
        if isinstance(speed, bool) or not isinstance(speed, (int, float)) or not math.isfinite(speed) or not lo <= speed <= hi:
            raise LevelError(f"{field}.speed must be a number {lo}–{hi} for a {kind} lane")
        rs = runs(pattern)
        for ch, length in rs:
            a, b = RUNS[ch]
            if not a <= length <= b:
                raise LevelError(f'{field}.pattern: a run of "{ch}" must be {a}–{b} long')
        filled = sum(length for _, length in rs)
        if kind == "road" and (not rs or filled * 2 > len(pattern) or longest_gap(pattern) < 3):
            raise LevelError(f"{field}: a road needs traffic on at most half of it and a gap of 3 or more")
        if kind == "river" and (not rs or longest_gap(pattern) > 4):
            raise LevelError(f"{field}: a river needs floaters with gaps of at most 4")
        return {"kind": kind, "pattern": pattern, "speed": round(float(speed), 2), "dir": direction}
    check.describe = ('an object {"kind": "road" | "river" | "grass", "pattern": 13-26 characters, '
                      '"speed": number 0.3-2 (river 0.3-1.2), "dir": "left" | "right"}')
    return check


def _check(lv):
    lanes = lv["lanes"]
    if sum(1 for ln in lanes if ln["kind"] != "grass") < 2:
        raise LevelError("lanes needs at least 2 road or river lanes")
    for a, b in zip(lanes, lanes[1:]):
        if a["kind"] == b["kind"] == "river" and a["dir"] == b["dir"] and abs(a["speed"] - b["speed"]) < 0.3 - 1e-9:
            raise LevelError("two river lanes next to each other going the same way must differ in speed by 0.3 or more")


def _difficulty(lv):
    lanes = lv["lanes"]
    busy = [ln for ln in lanes if ln["kind"] != "grass"]
    fill = sum(sum(n for _, n in runs(ln["pattern"])) / len(ln["pattern"]) for ln in lanes if ln["kind"] == "road")
    river_gaps = sum(longest_gap(ln["pattern"]) for ln in lanes if ln["kind"] == "river")
    speed = sum(ln["speed"] for ln in busy) / max(1, len(busy))
    return len(busy) * 3 + fill * 8 + river_gaps * 2 + speed * 20 + (90 - lv["time"]) * 0.4


def _road(p, s, d):
    return {"kind": "road", "pattern": p, "speed": s, "dir": d}


def _river(p, s, d):
    return {"kind": "river", "pattern": p, "speed": s, "dir": d}


_G = {"kind": "grass", "pattern": ".............", "speed": 0.5, "dir": "left"}

KIND = {
    "game": "hop",
    "label": "Road Hop · Levels",
    "noun": "level",
    "modes": ["levels"],
    # Ranges can't break the honest-score limit: a hop takes 8 updates and scores at most 10, a home at most
    # 50 + 113 (seconds left) and the fifth home 250 more, whatever the lanes; time ≤ 90 s (Easy × 1.25).
    "fields": {
        "lanes": List(Lane(), 6, 11),
        "time": Int(20, 90),
    },
    "check": _check,
    "difficulty": _difficulty,
    "about": """You design levels for Road Hop, a friendly family arcade game. A little hopper starts on a grassy bank
at the bottom of the screen and hops one square at a time (up, down, left or right; the board is 13 squares
wide) across lanes of road traffic and a river, into one of 5 homes at the top. Cars and trucks squash it, the
water swallows it, logs and shell rafts carry it along (it must hop off before it is carried off the edge),
and each crossing has a time limit. Filling all 5 homes finishes the level.
"lanes" is the list of lanes from the bottom (just above the starting bank) to the top (just below the homes),
6 to 11 lanes, at least 2 of them road or river. Each lane is {"kind", "pattern", "speed", "dir"}:
"kind" is "road", "river" or "grass" (a safe resting strip; its pattern is all dots).
"pattern" is 13 to 26 characters that go round and round across the lane, one character a square: "." is
empty; on a road "c" is a car (1 or 2 letters long) and "t" a truck (2 to 4 long); on a river "l" is a log
(2 to 6 long) and "s" a shell raft (2 to 4 long). A road may be at most half full and needs a gap of at least
3 squares; a river's gaps may be at most 4 squares long.
"speed" is in pixels per 1/60 second (a square is 18 pixels): road 0.3 to 2, river 0.3 to 1.2.
"dir" is "left" or "right"; two river lanes next to each other going the same way must differ in speed by at
least 0.3. "time" is the seconds for each crossing, 20 to 90 (more is easier).""",
    "target": lambda n: f"Level {n} should be about as hard as: {min(11, 6 + n // 2)} lanes, road speeds up to "
    f"{min(2.0, round(0.6 + 0.12 * n, 2))}, river speeds up to {min(1.2, round(0.5 + 0.08 * n, 2))}, "
    f"time {max(25, 62 - 2 * n)}.",
    "example": {"name": "Farm track", "time": 55, "lanes": [
        _road("..c....c....c..", 0.6, "left"), _road(".tt......tt....", 0.5, "right"), _G,
        _river("lll...lll....", 0.5, "right"), _river("ss...ss...ss..", 0.6, "left"), _river("llll...lll....", 0.7, "right")]},
    "builtin": [
        {"name": "Quiet lane", "time": 60, "lanes": [
            _road("...c.....c.....", 0.5, "left"), _road("..tt.........", 0.4, "right"), _G,
            _river("llll...llll...", 0.4, "right"), _river("ss..ss..ss..ss..", 0.5, "left"), _river("lllll...lllll...", 0.4, "right")]},
        {"name": "Market day", "time": 60, "lanes": [
            _road("..c...c.....c...", 0.6, "left"), _road(".tt......tt.....", 0.5, "right"), _road("c....c....c...", 0.8, "left"), _G,
            _river("lll...lll....", 0.5, "right"), _river("ss...ss...ss..", 0.6, "left"), _river("llll...llll..", 0.5, "right")]},
        {"name": "Rush hour", "time": 55, "lanes": [
            _road("c..c....c..c....", 0.7, "left"), _road("ttt.....ttt.....", 0.5, "right"), _road("..c...c...c..", 0.9, "left"),
            _road("cc......cc.....", 1.0, "right"), _G,
            _river("llll....llll....", 0.6, "right"), _river("sss...sss....", 0.7, "left"), _river("lll..lll...ll..", 0.8, "right"),
            _river("ss...ss...ss...", 0.5, "left")]},
        {"name": "Duck pond", "time": 55, "lanes": [
            _road("...c....c....", 0.7, "right"), _road(".tt.....tt....", 0.6, "left"), _G,
            _river("ss..ss...ss..", 0.6, "left"), _river("llll...lll....", 0.9, "right"), _river("sss....sss...", 0.5, "left"),
            _river("lll...llll...", 1.0, "right"), _river("ss...ss...ss..", 0.7, "left"), _river("lllll....lll...", 0.6, "right")]},
        {"name": "Motorway", "time": 50, "lanes": [
            _road("c...c...c....", 1.1, "left"), _road("tttt.......tt.....", 0.8, "right"), _road("..cc.....cc...", 1.3, "left"),
            _road("c.....c...c...", 1.5, "right"), _road("ttt......ttt....", 0.9, "left"), _G,
            _river("llll...llll...", 0.7, "right"), _river("ss...ss...ss..", 0.8, "left"), _river("lll....lll...", 1.0, "right"),
            _river("sss...sss....", 0.6, "left")]},
        {"name": "Swift river", "time": 50, "lanes": [
            _road("..c....c...c..", 1.0, "right"), _road("tt.....tt.....", 0.8, "left"), _road("c...c....c...", 1.2, "right"), _G,
            _river("lll...lll....", 1.0, "left"), _river("ss...ss..ss...", 1.1, "right"), _river("llll....lll...", 0.7, "left"),
            _river("sss...ss....s", 1.2, "right"), _river("ll...lll...ll...", 0.8, "left"), _river("ss...sss....s", 1.1, "right")]},
        {"name": "Night drive", "time": 45, "lanes": [
            _road("c..c...c..c...", 1.2, "left"), _road("tttt....ttt......", 1.0, "right"), _road("cc...c....cc...", 1.4, "left"),
            _road("..c..c...c..c..", 1.6, "right"), _road("ttt.....tt......", 1.1, "left"), _G,
            _river("lll...lll....", 0.9, "right"), _river("ss...ss...ss..", 1.1, "left"), _river("llll....llll....", 0.7, "right"),
            _river("sss....ss....", 1.2, "left"), _river("lll...ll....l", 1.0, "right")]},
        {"name": "Long way home", "time": 45, "lanes": [
            _road("c...cc...c....", 1.3, "right"), _road("ttt...tt.......", 1.1, "left"), _road(".c..c..c...c..", 1.7, "right"),
            _road("tt....tttt.......", 1.2, "left"), _road("c..c...cc....", 1.8, "right"), _G,
            _river("ll...ll...ll..", 1.1, "left"), _river("sss...sss....", 0.8, "right"), _river("lll....ll....", 1.2, "left"),
            _river("ss...ss...ss...", 0.9, "right"), _river("llll....lll....", 1.2, "left")]},
    ],
}
