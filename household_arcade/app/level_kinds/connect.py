"""Dot Connect · Puzzle book: the answer drawn as a square of letters, each letter one line from dot to dot."""
from ..level_common import LevelError
from . import Grid

LETTERS = "ABCDEFGHIJKLMNOP"


def paths(grid):
    """{letter: [cells in order from one end to the other]}. Raises LevelError when a letter isn't one simple line."""
    n = len(grid)
    if any(len(r) != n for r in grid):
        raise LevelError("the grid must be square (as many rows as letters in a row)")
    cells = {}
    for r, row in enumerate(grid):
        for c, ch in enumerate(row):
            cells.setdefault(ch, set()).add((r, c))
    if not 2 <= len(cells) <= len(LETTERS):
        raise LevelError(f"use 2-{len(LETTERS)} lines (letters)")
    out = {}
    for ch, sq in cells.items():
        if len(sq) < 3:
            raise LevelError(f'"{ch}" must cover at least 3 squares')
        nb = {p: [q for q in ((p[0] + 1, p[1]), (p[0] - 1, p[1]), (p[0], p[1] + 1), (p[0], p[1] - 1)) if q in sq] for p in sq}
        if any(len(v) > 2 for v in nb.values()) or sum(len(v) for v in nb.values()) != 2 * (len(sq) - 1):
            raise LevelError(f'"{ch}" must be one line that never touches itself side by side (no branches, no blocks of 2 × 2)')
        ends = [p for p, v in nb.items() if len(v) == 1]
        if len(ends) != 2:
            raise LevelError(f'"{ch}" must be one line with two ends')
        order, prev = [min(ends)], None
        while len(order) < len(sq):
            nxt = [q for q in nb[order[-1]] if q != prev]
            if not nxt:
                break
            prev = order[-1]
            order.append(nxt[0])
        if len(order) != len(sq):
            raise LevelError(f'"{ch}" must be one joined-up line')
        out[ch] = [r * n + c for r, c in order]
    return out


def _check(c):
    paths(c["grid"])


def _difficulty(c):
    n, lines = len(c["grid"]), paths(c["grid"])
    longest = max(len(p) for p in lines.values())
    return min(100, (n - 5) * 9 + longest / (n * n) * 40 + max(0, n + 2 - len(lines)) * 2)


def _fingerprint(c):
    names, out = {}, []
    for row in c["grid"]:
        for ch in row:
            names.setdefault(ch, LETTERS[len(names)])
        out.append("".join(names[ch] for ch in row))
    return ["/".join(out)]


KIND = {
    "game": "connect",
    "label": "Dot Connect · Puzzle book",
    "noun": "puzzle",
    "modes": ["book"],
    "fields": {"grid": Grid(LETTERS, (5, 12), (5, 12))},
    "check": _check,
    "difficulty": _difficulty,
    "fingerprint": _fingerprint,
    "about": """You design puzzles for Dot Connect, a calm family puzzle. The board is a square grid. Pairs of dots sit on it;
the player draws a line from each dot to its partner through squares that are next to each other (up, down, left,
right). Lines can't cross, and every square must be covered by a line.
"grid" draws the ANSWER: a square of capital letters (5-12 rows, each as long as there are rows), every square
covered. Each letter is one line: its squares joined one after another like a snake, with two ends (the ends become
the two dots the player sees). A line is at least 3 squares long and must never touch itself side by side: no
branches and no 2 × 2 block of one letter (a line that doubles back must leave a gap of another letter between).
Use 2-16 letters, A, B, C … Long winding lines that snake around each other are harder; many short lines are easier.""",
    "target": lambda n: f"Puzzle {n} should be about {min(12, 5 + n // 4)} squares across with about "
    f"{min(16, 4 + n // 3)} lines.",
    "example": {"name": "Garden path", "grid": ["AAABB", "CCABD", "CEEBD", "CEFFD", "CEEFD"]},
    "builtin": [
        {"name": "Little maze", "grid": ["DDEEE", "DAAAE", "DAEEE", "DABBB", "DCCCB"]},
        {"name": "Snail shell", "grid": ["DDDEEE", "DGFFFE", "DGAAFE", "DGABFE", "CGABBB", "CCCCCB"]},
        {"name": "Fence posts", "grid": ["BBBBAAA", "BEEBAGF", "CDEAAGF", "CDEHHGF", "CDEHGGF", "CDEHHHF", "CDEEFFF"]},
        {"name": "Hedge rows", "grid": ["CDDDDEEE", "CBBBDEDE", "CBABDDDF", "CCABBBAF", "HHAAAAAF", "GHHHHHIF", "GIIIIIIF", "GGGGGFFF"]},
        {"name": "Wool basket", "grid": ["CCCCCEEFF", "CDDDDEJJF", "CDEEEEJIF", "BDEKJJJIF", "BEEKKIIIF", "BAAAKIHHF", "BAKKKIIHF", "BAGHHHHHG", "BBGGGGGGG"]},
    ],
}
