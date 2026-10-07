"""Tank Battle · Arenas: an arena is a 13 × 13 map of walls, water and bushes, a number of enemy tanks, their
speed and how often they shoot. Every mode of the game plays the arena list (the arenas are the levels)."""
from ..level_common import LevelError
from . import Grid, Int, Num

SIZE = 13
PLAYER = (12, 4)                       # (row, column) of your tank's start
FLAG = (12, 6)                         # the home flag
ENTRIES = ((0, 0), (0, 6), (0, 12))    # where enemy tanks come in
FLAG_AROUND = ((11, 5), (11, 6), (11, 7), (12, 5), (12, 7))


def _check(c):
    m = c["map"]
    for (r, col), what in [(PLAYER, "your start (row 13, column 5)"), (FLAG, "the flag (row 13, column 7)")] + \
            [(e, f"the enemy entry at row 1, column {e[1] + 1}") for e in ENTRIES]:
        if m[r][col] != ".":
            raise LevelError(f"{what} must be empty (\".\")")
    for r, col in FLAG_AROUND:
        if m[r][col] not in ".b":
            raise LevelError("the squares next to the flag may only be empty or brick")
    # Tanks drive through empty squares and bushes and can shoot bricks away; steel and water stop them.
    seen, todo = {PLAYER}, [PLAYER]
    while todo:
        r, col = todo.pop()
        for nr, nc in ((r - 1, col), (r + 1, col), (r, col - 1), (r, col + 1)):
            if 0 <= nr < SIZE and 0 <= nc < SIZE and (nr, nc) not in seen and (nr, nc) != FLAG and m[nr][nc] not in "sw":
                seen.add((nr, nc))
                todo.append((nr, nc))
    for e in ENTRIES:
        if e not in seen:
            raise LevelError(f"there must be a way (not through steel or water) from your start to the enemy entry at "
                             f"row 1, column {e[1] + 1}")


def _difficulty(c):
    walls = sum(row.count("s") for row in c["map"])
    bushes = sum(row.count("g") for row in c["map"])
    return (c["enemies"] - 4) * 2.6 + (c["speed"] - 0.4) * 30 + (c["fire"] - 6) * 0.6 + bushes * 0.15 - walls * 0.05


KIND = {
    "game": "tanks",
    "label": "Tank Battle · Arenas",
    "noun": "arena",
    "modes": ["classic", "easy", "together"],          # Against each other has its own arenas (VERSUS_LEVELS)
    # Ranges keep the honest-score limit true for any arena: enemies come in at most one every 1.5 s whatever
    # the arena, so points (100 a tank, 500 an arena of at least 4 tanks) stay under 150 a second.
    "fields": {
        "map": Grid(".bswg", (SIZE, SIZE), (SIZE, SIZE)),
        "enemies": Int(4, 20),
        "speed": Num(0.4, 1.2),
        "fire": Int(6, 40),
    },
    "check": _check,
    "difficulty": _difficulty,
    "about": """You design arenas for Tank Battle, a family arcade game seen from above. The arena is 13 by 13 squares.
The player's tank starts at the bottom (row 13, column 5) next to a home flag (row 13, column 7) that it must
protect; enemy tanks drive in from the top, one at a time, at row 1 columns 1, 7 and 13 (at most four on the arena
at once). Tanks drive up, down, left and right and fire one shell at a time. Destroying every enemy tank of the
arena clears it and the next arena starts; the game ends when the player has no lives left or a shell hits the flag.
"map" is 13 strings of 13 characters, row 1 (the top) first: "." empty ground, "b" brick wall (shells break it,
a little at a time), "s" steel wall (shells can't break it), "w" water (stops tanks, shells fly over it), "g" bushes
(tanks drive through them and are hidden under them). The player's start, the flag and the three entries must be
"."; the five squares around the flag ((row 12, columns 6-8) and (row 13, columns 6 and 8)) may only be "." or "b";
there must be a way that avoids steel and water from the player's start to every entry.
"enemies" is how many enemy tanks come in the arena, "speed" how fast they drive in px per 1/60 s (a square is
18 px; the player drives 1.25), "fire" how many times a minute each enemy tries to shoot (more is harder).""",
    "target": lambda n: f"Arena {n} should be about as hard as: enemies {min(20, 5 + n)}, speed "
    f"{min(1.2, round(0.5 + 0.05 * n, 2))}, fire {min(40, 10 + 3 * n)}, with a few more walls to hide behind than "
    "open ground.",
    "example": {"name": "Twin towers", "enemies": 8, "speed": 0.6, "fire": 14, "map": [
        ".............",
        ".............",
        "..bb.....bb..",
        "..bb.....bb..",
        "..ss.....ss..",
        ".............",
        "....bbbbb....",
        ".............",
        "..gg.....gg..",
        "..gg..s..gg..",
        ".............",
        ".....bbb.....",
        ".....b.b.....",
    ]},
    "builtin": [
        {"name": "Open field", "enemies": 5, "speed": 0.5, "fire": 10, "map": [
            ".............",
            ".............",
            "..b..b.b..b..",
            "..b..b.b..b..",
            "..b.......b..",
            ".............",
            "....bb.bb....",
            ".............",
            "..b..s.s..b..",
            "..b.......b..",
            ".............",
            ".....bbb.....",
            ".....b.b.....",
        ]},
        {"name": "Brick town", "enemies": 6, "speed": 0.55, "fire": 12, "map": [
            ".............",
            ".bb.bb.bb.bb.",
            ".bb.bb.bb.bb.",
            ".............",
            "bb.bbb.bbb.bb",
            ".............",
            ".b.b.b.b.b.b.",
            ".b.b.b.b.b.b.",
            ".............",
            "bbb.bb.bb.bbb",
            ".............",
            ".b...bbb...b.",
            ".b...b.b...b.",
        ]},
        {"name": "River crossing", "enemies": 8, "speed": 0.6, "fire": 14, "map": [
            ".............",
            "..b.......b..",
            "..b..bbb..b..",
            ".............",
            "ww.wwwwwww.ww",
            "ww.wwwwwww.ww",
            ".............",
            "..s..bbb..s..",
            "..b.......b..",
            "..b..g.g..b..",
            ".....g.g.....",
            ".....bbb.....",
            "..g..b.b..g..",
        ]},
        {"name": "Leafy hideout", "enemies": 9, "speed": 0.65, "fire": 16, "map": [
            ".............",
            ".ggg.....ggg.",
            ".ggg.bbb.ggg.",
            ".....bsb.....",
            "bb.........bb",
            "..ggg...ggg..",
            "..ggg.s.ggg..",
            "..ggg...ggg..",
            "bb....b....bb",
            "...bb...bb...",
            ".gg.......gg.",
            ".gg..bbb..gg.",
            ".....b.b.....",
        ]},
        {"name": "Steel garden", "enemies": 10, "speed": 0.7, "fire": 18, "map": [
            ".............",
            ".s.s.....s.s.",
            ".............",
            "..bbsbbbsbb..",
            ".............",
            "s.b.s...s.b.s",
            "..b.......b..",
            "..b.ss.ss.b..",
            ".............",
            ".sbb.....bbs.",
            ".............",
            "..s..bbb..s..",
            ".....b.b.....",
        ]},
        {"name": "Lake fort", "enemies": 12, "speed": 0.75, "fire": 20, "map": [
            ".............",
            ".....bbb.....",
            ".ww.......ww.",
            ".ww.bb.bb.ww.",
            ".....b.b.....",
            "b.ss.....ss.b",
            "b.....g.....b",
            "..ww.ggg.ww..",
            "..ww..g..ww..",
            ".............",
            ".bb.s...s.bb.",
            ".....bbb.....",
            "..b..b.b..b..",
        ]},
        {"name": "Winding road", "enemies": 14, "speed": 0.8, "fire": 22, "map": [
            ".............",
            "bbbbb.b.bbbbb",
            ".............",
            ".bbbbbbbbbbb.",
            ".s.........s.",
            ".s.bbbbbbb.s.",
            "...b.....b...",
            "ggg.b.s.b.ggg",
            "ggg.......ggg",
            ".bbbbb.bbbbb.",
            ".............",
            "..s..bbb..s..",
            ".....b.b.....",
        ]},
        {"name": "Castle gates", "enemies": 16, "speed": 0.85, "fire": 24, "map": [
            ".............",
            ".sss.....sss.",
            ".s.........s.",
            ".s.bbbbbbb.s.",
            "...b.....b...",
            "ww.b.ggg.b.ww",
            "ww...ggg...ww",
            "...b.ggg.b...",
            ".s.bbb.bbb.s.",
            ".s.........s.",
            ".sss.....sss.",
            "....bbbbb....",
            "..g..b.b..g..",
        ]},
        {"name": "Last stand", "enemies": 20, "speed": 0.95, "fire": 28, "map": [
            ".............",
            ".b.b.b.b.b.b.",
            ".b.b.bsb.b.b.",
            ".............",
            "ss.bbb.bbb.ss",
            "...g.....g...",
            ".w.ggg.ggg.w.",
            ".w...s.s...w.",
            ".....s.s.....",
            "bb.b.....b.bb",
            ".....bbb.....",
            ".s..bbbbb..s.",
            "..b..b.b..b..",
        ]},
    ],
}
