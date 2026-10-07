"""Shared by Colour Sort and Bolt Sort: tubes (or bolts) drawn as strings of colour letters, bottom to top, four
layers each, and a search that sorts them (the same rules and limits as the game files)."""
import sys

from ..level_common import LevelError

CAP = 4
LETTERS = "ABCDEFGHIJKL"


def parse(stacks, most_colours: int) -> list[list[int]]:
    out = [[LETTERS.index(ch) for ch in t] for t in stacks]
    counts = {}
    for t in out:
        for c in t:
            counts[c] = counts.get(c, 0) + 1
    colours = len(counts)
    if not 2 <= colours <= most_colours:
        raise LevelError(f"use 2-{most_colours} colours")
    if sorted(counts) != list(range(colours)):
        raise LevelError(f'use the first {colours} colour letters ({LETTERS[:colours]}) without skipping one')
    bad = [LETTERS[c] for c, n in sorted(counts.items()) if n != CAP]
    if bad:
        raise LevelError("every colour must appear exactly 4 times; not " + ", ".join(bad))
    if len(out) < colours + 1:
        raise LevelError("there must be at least one more tube than colours (some room to move)")
    if len(out) > colours + 3:
        raise LevelError("at most 3 more tubes than colours")
    if is_sorted(out):
        raise LevelError("it is already sorted")
    return out


def is_sorted(tubes) -> bool:
    return all(not t or (len(t) == CAP and len(set(t)) == 1) for t in tubes)


def _run(t) -> int:
    n = 0
    for c in reversed(t):
        if c != t[-1]:
            break
        n += 1
    return n


def solvable(tubes, pour_all: bool, budget: int = 120_000) -> bool:
    """Depth first over the moves, remembering positions seen (pour_all: Colour Sort pours every layer of the top
    colour that fits; else one nut at a time)."""
    work = [list(t) for t in tubes]
    seen = set()
    count = [0]
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 5000))

    def dfs() -> bool:
        if is_sorted(work):
            return True
        count[0] += 1
        if count[0] > budget:
            return False
        key = "|".join(sorted(",".join(map(str, t)) for t in work))
        if key in seen:
            return False
        seen.add(key)
        for a, A in enumerate(work):
            if not A:
                continue
            if len(A) == CAP and _run(A) == CAP:
                continue
            for b, B in enumerate(work):
                if a == b or len(B) >= CAP or (B and B[-1] != A[-1]):
                    continue
                if not B and _run(A) == len(A):
                    continue
                n = min(_run(A), CAP - len(B)) if pour_all else 1
                for _ in range(n):
                    B.append(A.pop())
                if dfs():
                    return True
                for _ in range(n):
                    A.append(B.pop())
        return False

    return dfs()
