"""Untangle · Puzzle book: points on a 9 × 9 grid and the lines between them, drawn untangled (no line crosses
another); the game shuffles the points onto a circle, and the player puts them back."""
from ..level_common import LevelError
from . import Int, List

SPAN = 8                     # points sit at whole numbers 0-8 across and down


def _orient(a, b, c):
    v = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    return (v > 0) - (v < 0)


def _on(a, b, p):
    return min(a[0], b[0]) <= p[0] <= max(a[0], b[0]) and min(a[1], b[1]) <= p[1] <= max(a[1], b[1])


def _segments_cross(p1, p2, p3, p4):
    o1, o2, o3, o4 = _orient(p1, p2, p3), _orient(p1, p2, p4), _orient(p3, p4, p1), _orient(p3, p4, p2)
    if o1 != o2 and o3 != o4 and o1 and o2 and o3 and o4:
        return True
    return (not o1 and _on(p1, p2, p3)) or (not o2 and _on(p1, p2, p4)) or (not o3 and _on(p3, p4, p1)) \
        or (not o4 and _on(p3, p4, p2))


def edges_cross(pts, e, f):
    """The game's rule (untangle-logic.js edgesCross): lines sharing a point cross only when they overlap."""
    shared = set(e) & set(f)
    if shared:
        s = shared.pop()
        a, b = (e[1] if e[0] == s else e[0]), (f[1] if f[0] == s else f[0])
        P, A, B = pts[s], pts[a], pts[b]
        if _orient(P, A, B) != 0:
            return False
        return (A[0] - P[0]) * (B[0] - P[0]) + (A[1] - P[1]) * (B[1] - P[1]) > 0
    return _segments_cross(pts[e[0]], pts[e[1]], pts[f[0]], pts[f[1]])


def _check(c):
    pts, edges = [tuple(p) for p in c["points"]], [tuple(e) for e in c["edges"]]
    k = len(pts)
    if len(set(pts)) != k:
        raise LevelError("two points are in the same place")
    seen = set()
    for a, b in edges:
        if a >= k or b >= k:
            raise LevelError(f"lines join points 0-{k - 1} (there are {k} points)")
        if a == b:
            raise LevelError("a line must join two different points")
        if (min(a, b), max(a, b)) in seen:
            raise LevelError("the same line is listed twice")
        seen.add((min(a, b), max(a, b)))
    deg = [0] * k
    for a, b in edges:
        deg[a] += 1
        deg[b] += 1
    if min(deg) < 2:
        raise LevelError("every point needs at least 2 lines")
    todo, reach = [0], {0}
    while todo:
        p = todo.pop()
        for a, b in edges:
            for x, y in ((a, b), (b, a)):
                if x == p and y not in reach:
                    reach.add(y)
                    todo.append(y)
    if len(reach) != k:
        raise LevelError("all the points must be joined into one drawing")
    for a, b in edges:
        for i, p in enumerate(pts):
            if i not in (a, b) and _orient(pts[a], pts[b], p) == 0 and _on(pts[a], pts[b], p):
                raise LevelError("a line passes straight through another point")
    for i in range(len(edges)):
        for j in range(i + 1, len(edges)):
            if edges_cross(pts, edges[i], edges[j]):
                raise LevelError(f"lines {list(edges[i])} and {list(edges[j])} cross: the drawing must have no crossings")


def _difficulty(c):
    k, e = len(c["points"]), len(c["edges"])
    return min(100, (k - 6) * 3 + (e - k) * 1.5)


def _fingerprint(c):
    pts = [tuple(p) for p in c["points"]]
    return [repr(sorted(tuple(sorted((pts[a], pts[b]))) for a, b in c["edges"]))]


KIND = {
    "game": "untangle",
    "label": "Untangle · Puzzle book",
    "noun": "puzzle",
    "modes": ["book"],
    "fields": {
        "points": List(List(Int(0, SPAN), 2, 2), 6, 30),
        "edges": List(List(Int(0, 29), 2, 2), 6, 80),
    },
    "check": _check,
    "difficulty": _difficulty,
    "fingerprint": _fingerprint,
    "about": """You design puzzles for Untangle, a calm family puzzle. Points are joined by straight lines. The game puts the
points on a circle in a muddled order so the lines cross; the player drags the points until no two lines cross.
You draw the untangled answer.
"points" lists the points as [x, y], whole numbers 0-8 (a 9 × 9 grid), 6-30 points, no two in the same place.
"edges" lists the lines as [a, b], the numbers of two points in the "points" list (counting from 0).
Rules: in your drawing no two lines may cross (lines that meet at a shared point are fine) and no line may pass
straight through another point; every point has at least 2 lines; all points are joined into one drawing; no line
listed twice. Nice drawings: a house, a star, a web, a ladder, triangles and squares sharing sides. More points and
more lines (lots of triangles) are harder.""",
    "target": lambda n: f"Puzzle {n} should have about {min(30, 6 + n // 2)} points and about "
    f"{min(30, 6 + n // 2) * 3 // 2 + min(8, n // 4)} lines.",
    "example": {"name": "Kite", "points": [[4, 0], [8, 4], [4, 8], [0, 4], [4, 4], [4, 6]],
                "edges": [[0, 1], [1, 2], [2, 3], [3, 0], [0, 4], [4, 5], [5, 2], [1, 4], [3, 4], [1, 5], [3, 5]]},
    "builtin": [
        {"name": "Little house", "points": [[2, 4], [6, 4], [6, 8], [2, 8], [4, 1], [4, 6]],
         "edges": [[0, 1], [1, 2], [2, 3], [3, 0], [0, 4], [1, 4], [5, 0], [5, 1], [5, 2], [5, 3]]},
        {"name": "Fishing net", "points": [[1, 8], [8, 0], [0, 8], [3, 5], [1, 6], [7, 0], [6, 3], [6, 6]],
         "edges": [[1, 5], [0, 2], [0, 4], [2, 4], [3, 4], [6, 7], [3, 7], [5, 6], [3, 6], [0, 3], [1, 6], [0, 7]]},
        {"name": "Spider web", "points": [[7, 3], [0, 0], [5, 4], [8, 3], [0, 5], [3, 4], [2, 1], [0, 4], [8, 7], [4, 8]],
         "edges": [[4, 7], [0, 3], [2, 5], [0, 2], [1, 6], [5, 7], [2, 3], [5, 6], [4, 5], [6, 7], [1, 7], [8, 9],
                   [3, 8], [2, 9], [0, 5], [5, 9]]},
        {"name": "Climbing frame", "points": [[3, 3], [8, 3], [7, 6], [1, 7], [5, 2], [6, 6], [8, 2], [0, 8], [0, 1], [8, 7],
                                              [3, 6], [3, 2]],
         "edges": [[0, 11], [2, 9], [2, 5], [1, 6], [3, 7], [4, 11], [5, 9], [0, 4], [3, 10], [4, 6], [8, 11], [0, 10],
                   [5, 10], [1, 2], [1, 4], [1, 5], [0, 8], [7, 10], [1, 9], [4, 5]]},
        {"name": "Star map", "points": [[7, 4], [4, 1], [5, 7], [2, 7], [6, 7], [8, 5], [3, 3], [6, 2], [8, 6], [6, 8], [0, 4],
                                        [3, 1], [5, 8], [8, 1], [3, 4]],
         "edges": [[6, 14], [4, 9], [0, 5], [2, 12], [1, 11], [4, 12], [2, 4], [9, 12], [5, 8], [1, 7], [7, 13], [4, 8],
                   [6, 11], [0, 7], [1, 6], [0, 8], [8, 9], [10, 14], [6, 10], [2, 3], [0, 4], [1, 14], [0, 13], [3, 14],
                   [3, 12], [2, 14], [0, 2]]},
    ],
}
