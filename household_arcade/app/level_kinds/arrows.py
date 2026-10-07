"""Arrow Release · Picture boards: a shape drawn with "#" that the game fills with arrows (always solvable: the
board maker checks every board, as for the numbered boards)."""
from ..level_common import LevelError
from . import Bool, Grid, Int

MIN_SQUARES = 12


def _check(c):
    shape = c["shape"]
    sq = sum(row.count("#") for row in shape)
    if sq < MIN_SQUARES:
        raise LevelError(f"the shape needs at least {MIN_SQUARES} squares (\"#\")")
    if "#" not in shape[0] or "#" not in shape[-1] or not any(r[0] == "#" for r in shape) \
            or not any(r[-1] == "#" for r in shape):
        raise LevelError("the shape must touch all four sides of its box (no empty first or last row or column)")


def _difficulty(c):
    sq = sum(row.count("#") for row in c["shape"])
    return min(100, sq / 400 * 45 + (c["longest"] - 2) * 5 + (c["fill"] - 60) * 0.4 + (12 if c["twisty"] else 0))


KIND = {
    "game": "arrows",
    "label": "Arrow Release · Picture boards",
    "noun": "board",
    "modes": ["book"],
    "fields": {
        "shape": Grid("#.", (5, 20), (5, 20)),
        "longest": Int(2, 10),
        "fill": Int(60, 100),
        "twisty": Bool(),
    },
    "check": _check,
    "difficulty": _difficulty,
    "about": """You design boards for Arrow Release, a calm family puzzle. The board is a grid covered in arrows; each arrow
covers a short straight line of squares (or, when twisty, a bent path) and points one way. Tapping an arrow sends it
flying off the board the way it points, if nothing is in its way; if something is, it bumps and the player loses a
heart. The goal is to clear every arrow. The game places the arrows itself and makes sure every board can be cleared;
you draw the picture the arrows fill.
"shape" draws it: a list of 5-20 rows, all the same length (5-20 characters), "#" a square arrows may cover and "."
an empty square (arrows fly over empty squares). Draw a picture or outline: a heart, a fish, a rocket, a tree, a cat's
face, a letter. At least 12 "#"; the first and last row and the first and last column each contain at least one "#".
"longest" is the longest arrow in squares (2-10; every arrow is at least 2), "fill" how much of the shape is covered
by arrows in % (60-100), "twisty": true for bent arrows. Bigger shapes, longer arrows, fuller and twisty are
harder.""",
    "target": lambda n: f"Board {n} should be about {min(20, 8 + n // 3)} squares across, longest "
    f"{min(10, 3 + n // 5)}, fill {min(95, 70 + n)}, twisty {'true' if n >= 10 and n % 2 == 0 else 'false'}.",
    "example": {"name": "Sail boat", "shape": [
        "...#....", "..##....", ".###....", "####....", "...#....", "########", ".######.", "..####.."],
        "longest": 3, "fill": 80, "twisty": False},
    "builtin": [
        {"name": "Little heart", "shape": [
            ".##.##.", "#######", "#######", ".#####.", "..###..", "...#..."], "longest": 2, "fill": 75, "twisty": False},
        {"name": "Fish", "shape": [
            "...####...", ".########.", "##########", ".########.", "...####...", "......##.."],
         "longest": 3, "fill": 80, "twisty": False},
        {"name": "Rocket", "shape": [
            "...##...", "..####..", "..####..", "..####..", "..####..", ".######.", "########", "##.##.##"],
         "longest": 3, "fill": 85, "twisty": False},
        {"name": "Christmas tree", "shape": [
            "....##....", "...####...", "..######..", "...####...", "..######..", ".########.", "##########",
            "....##....", "....##...."], "longest": 4, "fill": 85, "twisty": False},
        {"name": "Snake pit", "shape": [
            "##########", "#........#", "#.######.#", "#.#....#.#", "#.#.##.#.#", "#.#.##.#.#", "#.#....#.#",
            "#.######.#", "#........#", "##########"], "longest": 4, "fill": 90, "twisty": True},
        {"name": "Big smile", "shape": [
            "...######...", ".##########.", "############", "###..##..###", "############", "############",
            "##.######.##", "###.####.###", ".####..####.", "...######..."], "longest": 5, "fill": 90, "twisty": True},
    ],
}
