"""Tile Match · Layouts: a heap drawn as layers of "#" rows, each layer shifted by half tiles. The game deals the
symbols so that the heap can always be cleared; here the heap itself is checked to be clearable two tiles at a time."""
from ..level_common import LevelError
from . import Grid, Int

MIN_TILES, MAX_TILES = 16, 144
MAX_W, MAX_H = 26, 20        # in half tiles (the game draws the heap to fit; bigger would be too small to tap)
_ROWS = Grid("#.", (1, 10), (1, 12))
_SHIFT = Int(0, 12)


def _layers(v, field):
    if not isinstance(v, list) or not 1 <= len(v) <= 5:
        raise LevelError(f"{field} must be a list of 1-5 layers")
    out = []
    for i, ly in enumerate(v):
        if not isinstance(ly, dict) or set(ly) != {"dx", "dy", "rows"}:
            raise LevelError(f'{field}[{i}] must be an object with "dx", "dy" and "rows"')
        out.append({"dx": _SHIFT(ly["dx"], f"{field}[{i}].dx"), "dy": _SHIFT(ly["dy"], f"{field}[{i}].dy"),
                    "rows": _ROWS(ly["rows"], f"{field}[{i}].rows")})
    return out


_layers.describe = ('list of 1-5 layers, bottom first, each {"dx": whole number 0-12, "dy": whole number 0-12, '
                    '"rows": list of 1-10 rows of 1-12 characters of "#."}')
_layers.grid = True


def places(layers):
    """[(x, y, z)] in half tiles, as tiles-logic.js places()."""
    return [(ly["dx"] + 2 * c, ly["dy"] + 2 * r, z) for z, ly in enumerate(layers)
            for r, row in enumerate(ly["rows"]) for c, ch in enumerate(row) if ch == "#"]


def _free(pl, alive, i):
    x, y, z = pl[i]
    left = right = False
    for j in alive:
        if j == i:
            continue
        a, b, w = pl[j]
        if w == z + 1 and abs(a - x) < 2 and abs(b - y) < 2:
            return False
        if w == z and abs(b - y) < 2:
            left = left or a == x - 2
            right = right or a == x + 2
    return not (left and right)


def clearable(pl) -> bool:
    """Taking free tiles away only ever frees others, so take two free ones at a time until none are left."""
    alive = set(range(len(pl)))
    while alive:
        free = sorted(i for i in alive if _free(pl, alive, i))
        if len(free) < 2:
            return False
        alive -= set(free[:2])
    return True


def _check(c):
    pl = places(c["layers"])
    if not MIN_TILES <= len(pl) <= MAX_TILES or len(pl) % 2:
        raise LevelError(f"use an even number of tiles, {MIN_TILES}-{MAX_TILES} (this has {len(pl)})")
    if len(set(pl)) != len(pl):
        raise LevelError("two tiles are in the same place")
    cover = {}
    for x, y, z in pl:
        for a in (x, x + 1):
            for b in (y, y + 1):
                if (a, b, z) in cover:
                    raise LevelError("two tiles on one layer overlap")
                cover[(a, b, z)] = True
    w, h = max(x for x, _, _ in pl) + 2, max(y for _, y, _ in pl) + 2
    if w > MAX_W or h > MAX_H:
        raise LevelError(f"the heap is {w} × {h} half tiles; it may be at most {MAX_W} × {MAX_H}")
    for x, y, z in pl:
        if z and not any(w2 == z - 1 and abs(a - x) < 2 and abs(b - y) < 2 for a, b, w2 in pl):
            raise LevelError(f"a tile on layer {z + 1} floats: every tile above the first layer must lie on a tile")
    if not clearable(pl):
        raise LevelError("this heap can't be cleared two tiles at a time")


def _difficulty(c):
    pl = places(c["layers"])
    return min(100, len(pl) / MAX_TILES * 70 + (len(c["layers"]) - 1) * 7)


def _fingerprint(c):
    return [repr(sorted(places(c["layers"])))]


KIND = {
    "game": "tiles",
    "label": "Tile Match · Layouts",
    "noun": "layout",
    "modes": ["layouts"],
    "fields": {"layers": _layers},
    "check": _check,
    "difficulty": _difficulty,
    "fingerprint": _fingerprint,
    "about": """You design heaps for Tile Match, a calm family tile game. Tiles lie in a heap of layers; the player takes away
two free tiles with the same symbol at a time. A tile is free when no tile lies on it and its left or right side is
open. The game puts the symbols on the tiles itself so that every heap can be cleared; you draw the heap's shape.
"layers" lists the layers from the bottom up. Each layer is {"dx", "dy", "rows"}: "rows" draws it ("#" a tile, "."
none; 1-10 rows of the same length, 1-12 characters) and "dx", "dy" shift it right and down in HALF tiles, so a layer
shifted by 1 sits across the joins of the one below. Rules: an even number of tiles in all, 16-144; every tile above
the bottom layer must lie on (overlap) a tile of the layer just below; the whole heap at most 26 half tiles wide and
20 high (about 13 × 10 tiles). Shapes: a pyramid, a turtle, a castle, a butterfly, a fortress, a cat. More tiles and
more layers are harder.""",
    "target": lambda n: f"Layout {n} should have about {min(144, 28 + 8 * n)} tiles in {min(5, 2 + n // 4)} layers.",
    "example": {"name": "Little pyramid", "layers": [
        {"dx": 0, "dy": 0, "rows": ["######", "######", "######", "######"]},
        {"dx": 2, "dy": 2, "rows": ["####", "####"]},
        {"dx": 4, "dy": 3, "rows": ["##"]}]},
    "builtin": [
        {"name": "Tea tray", "layers": [
            {"dx": 0, "dy": 0, "rows": ["######", "######", "######"]},
            {"dx": 2, "dy": 2, "rows": ["####"]}]},
        {"name": "Turtle", "layers": [
            {"dx": 0, "dy": 0, "rows": ["..##..", ".####.", "######", "######", ".####.", "#....#"]},
            {"dx": 3, "dy": 3, "rows": ["##", "##"]}]},
        {"name": "Castle", "layers": [
            {"dx": 0, "dy": 0, "rows": ["#.#.#.#.", "########", "########", "########", "########", "###..###"]},
            {"dx": 2, "dy": 2, "rows": ["######", "######", "######"]},
            {"dx": 4, "dy": 4, "rows": ["####"]}]},
        {"name": "Butterfly", "layers": [
            {"dx": 0, "dy": 0, "rows": ["###..###", "########", "########", ".######.", "###..###",
                                        "##....##"]},
            {"dx": 1, "dy": 2, "rows": ["##.##.#", "#######"]},
            {"dx": 6, "dy": 4, "rows": ["##"]}]},
        {"name": "Fortress", "layers": [
            {"dx": 0, "dy": 0, "rows": ["##########", "##########", "##########", "##########", "##########",
                                        "##########"]},
            {"dx": 2, "dy": 2, "rows": ["########", "########", "########", "########"]},
            {"dx": 4, "dy": 4, "rows": ["######", "######"]},
            {"dx": 6, "dy": 5, "rows": ["####"]}]},
    ],
}
