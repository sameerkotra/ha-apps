"""The games this version has, as the server knows them (SPEC §3).

The browser draws and runs the games (static/games/, one registry entry each);
the server only needs each game's id, name, modes and the limits that make a
score "impossible" (SPEC §5.1). Adding a game later = its files in
static/games/ plus one entry here. App settings and the per-child "allowed
games" only ever list the games in this table.
"""

# Seconds of play at most (a longer game is refused as impossible).
MAX_SECONDS = 6 * 3600
MAX_LEVEL = 100          # games without a level list; with one, see max_level()

# Wave 7's modes (static/games/boardkit.js MODES): the computer's three levels, one screen, two phones.
TURN_GAME_MODES = [
    {"id": "easy", "label": "Computer · Easy (gentle)"},
    {"id": "medium", "label": "Computer · Medium"},
    {"id": "hard", "label": "Computer · Hard"},
    {"id": "two", "label": "Two players (one screen)"},
    {"id": "phones", "label": "Two phones (turn by turn)"},
]

# Wave 8's dice games (static/games/dicekit.js MODES): you and the computer, everyone on one phone, 2–4 phones.
DICE_GAME_MODES = [
    {"id": "cpu", "label": "You and the computer"},
    {"id": "pass", "label": "One phone (pass and play)"},
    {"id": "phones", "label": "Phones (turn by turn, 2–4)"},
]

GAMES: dict[str, dict] = {
    "snake": {
        "name": "Snake",
        "icon": "🐍",
        "modes": [
            {"id": "walls-slow", "label": "Walls · Slow"},
            {"id": "walls-normal", "label": "Walls · Normal"},
            {"id": "walls-fast", "label": "Walls · Fast"},
            {"id": "wrap-slow", "label": "Wrap · Slow"},
            {"id": "wrap-normal", "label": "Wrap · Normal"},
            {"id": "wrap-fast", "label": "Wrap · Fast"},
            {"id": "maze", "label": "Maze"},
        ],
        # Modes that play through levels (levels.py); the others have no end.
        "level_modes": ["maze"],
        "levels_end": True,
        "default_mode": "walls-normal",
        # The save format of the game's rules (static/games/snake-logic.js STATE_VERSION); a saved game
        # with another format can't be continued. 0 or missing: games of this kind can't be saved.
        "state_version": 1,
        # score ≤ max_score and ≤ seconds × per_second + base
        "max_score": 20_000,
        "per_second": 40,
        "base": 100,
    },
    "brick": {
        "name": "Brick Breaker",
        "icon": "🧱",
        "modes": [
            {"id": "powerups", "label": "Power-ups"},
            {"id": "classic", "label": "Classic"},
        ],
        "level_modes": ["powerups", "classic"],
        "default_mode": "powerups",
        "state_version": 1,
        "max_score": 500_000,
        "per_second": 400,
        "base": 500,
    },
    "blocks": {
        "name": "Falling Blocks",
        "icon": "🟪",
        "modes": [
            {"id": "classic", "label": "Classic"},
            {"id": "fast", "label": "Fast start"},
            {"id": "rising", "label": "Rising floor"},
            {"id": "challenge", "label": "Challenges"},
        ],
        "level_modes": ["challenge"],
        "levels_end": True,
        "default_mode": "classic",
        "state_version": 1,
        # at most ~6 pieces a second (a piece waits 10 updates to appear), 0.4 rows a piece (+ risen rows),
        # 4 rows at once = 200 a row × level (≤ 20), 2 a row for a drop
        "max_score": 75_000_000,
        "per_second": 10_000,
        "base": 2_000,
    },
    "duel": {
        "name": "Paddle Duel",
        "icon": "🏓",
        "modes": [
            {"id": "easy", "label": "Easy"},
            {"id": "normal", "label": "Normal"},
            {"id": "hard", "label": "Hard"},
        ],
        "level_modes": ["easy", "normal", "hard"],
        "levels_end": True,
        "default_mode": "normal",
        "state_version": 1,
        # a point takes the ball at least ~0.6 s across the court: 100 × match, +1,000 × match for a match won
        "max_score": 10_000_000,
        "per_second": 3_500,
        "base": 2_000,
    },
    "racer": {
        "name": "Lane Racer",
        "icon": "🏎️",
        "modes": [
            {"id": "three", "label": "3 lanes"},
            {"id": "four", "label": "4 lanes"},
            {"id": "rush", "label": "Rush (one life)"},
            {"id": "stages", "label": "Stages"},
        ],
        "level_modes": ["stages"],
        "levels_end": True,
        "default_mode": "three",
        "state_version": 1,
        # 1 a 10 px of road at ≤ 7.5 px an update (45 a second) + coins (25, at most one a row)
        "max_score": 3_000_000,
        "per_second": 150,
        "base": 300,
    },
    "flap": {
        "name": "Flap",
        "icon": "🐤",
        "modes": [
            {"id": "easy", "label": "Easy"},
            {"id": "normal", "label": "Normal"},
            {"id": "moving", "label": "Moving gates"},
            {"id": "course", "label": "Courses"},
        ],
        "level_modes": ["course"],
        "levels_end": True,
        "default_mode": "normal",
        "state_version": 1,
        # one point a gate; gates are 124 px apart at ≤ 2.6 px an update (~1.3 a second)
        "max_score": 100_000,
        "per_second": 2,
        "base": 5,
    },
    "mines": {
        "name": "Mines",
        "icon": "💣",
        "modes": [
            {"id": "easy", "label": "Easy"},
            {"id": "medium", "label": "Medium"},
            {"id": "hard", "label": "Hard"},
            {"id": "boards", "label": "Shaped boards"},
        ],
        "level_modes": ["boards"],
        "levels_end": True,
        "default_mode": "easy",
        "state_version": 1,
        # a board is worth at most squares + 15 × mines (810 at the cap), and the next board comes 90 updates
        # after a clear
        "max_score": 2_000_000,
        "per_second": 600,
        "base": 1_000,
    },
    "merge": {
        "name": "Merge",
        "icon": "🔢",
        "modes": [
            {"id": "classic", "label": "4 × 4"},
            {"id": "big", "label": "5 × 5"},
            {"id": "small", "label": "3 × 3 (hard)"},
            {"id": "goals", "label": "Goals"},
        ],
        "level_modes": ["goals"],
        "levels_end": True,
        "default_mode": "classic",
        "state_version": 1,
        # ≤ 10 moves a second (6 updates apart), each adds ≤ 4 to the tile total S; score ≤ S·(log2 S − 1)
        "max_score": 17_000_000,
        "per_second": 800,
        "base": 300,
    },
    "colours": {
        "name": "Colour Memory",
        "icon": "🔴",
        "modes": [
            {"id": "classic", "label": "Classic"},
            {"id": "fast", "label": "Fast"},
            {"id": "reverse", "label": "Backwards"},
            {"id": "challenge", "label": "Challenges"},
        ],
        "level_modes": ["challenge"],
        "levels_end": True,
        "default_mode": "classic",
        "state_version": 1,
        # round n scores 10n and takes ≥ 80 + 20n updates; round 100 right ends the game: 10 × (1 + … + 100)
        "max_score": 2_325_000,
        "per_second": 40,
        "base": 50,
    },
    "cards": {
        "name": "Memory Cards",
        "icon": "🃏",
        "modes": [
            {"id": "small", "label": "4 × 4"},
            {"id": "medium", "label": "4 × 5"},
            {"id": "large", "label": "5 × 6"},
            {"id": "challenge", "label": "Challenges"},
        ],
        "level_modes": ["challenge"],
        "levels_end": True,
        "default_mode": "small",
        "state_version": 1,
        # a card takes 6 updates to turn, 90 between boards; a perfect 5 × 6 board takes ≥ 4.5 s; board 100 ends it
        "max_score": 1_650_000,
        "per_second": 800,
        "base": 2_000,
    },
    "mole": {
        "name": "Tap the Mole",
        "icon": "🔨",
        "modes": [
            {"id": "classic", "label": "Classic"},
            {"id": "big", "label": "Big garden"},
            {"id": "rush", "label": "60 seconds"},
            {"id": "gardens", "label": "Gardens"},
        ],
        "level_modes": ["gardens"],
        "levels_end": True,
        "default_mode": "classic",
        "state_version": 1,
        # a critter ≥ 24 updates after the last, a golden one ≥ 180 after the last golden: ≤ 50 + 38.3 a second
        "max_score": 1_000_000,
        "per_second": 40,
        "base": 60,
    },
    "numbers": {
        "name": "Number Dash",
        "icon": "➗",
        "modes": [
            {"id": "add", "label": "Add & take away"},
            {"id": "times", "label": "Times tables"},
            {"id": "mixed", "label": "Mixed"},
            {"id": "challenge", "label": "Challenges"},
        ],
        "level_modes": ["challenge"],
        "levels_end": True,
        "default_mode": "add",
        "state_version": 1,
        # at most one answer per 12 updates, each ≤ 3 × (10 + 2 × 25) = 180 (level is capped at 25)
        "max_score": 5_000_000,
        "per_second": 900,
        "base": 200,
    },
    "tanks": {
        "name": "Tank Battle",
        "icon": "🛡️",
        "modes": [
            {"id": "classic", "label": "Classic"},
            {"id": "easy", "label": "Easy"},
        ],
        "level_modes": ["classic", "easy"],
        "levels_end": True,
        "default_mode": "classic",
        "state_version": 1,
        # enemies come in at most one every 90 updates; 100 a tank + 500 an arena of ≥ 4 tanks: ≤ 150 a second
        "max_score": 1_500_000,
        "per_second": 160,
        "base": 500,
    },
    "invaders": {
        "name": "Sky Defenders",
        "icon": "👾",
        "modes": [
            {"id": "classic", "label": "Classic"},
            {"id": "easy", "label": "Easy"},
            {"id": "waves", "label": "Waves"},
        ],
        "level_modes": ["waves"],
        "levels_end": True,
        "default_mode": "classic",
        "state_version": 1,
        # one shot ≥ 15 updates after the last (≤ 4 critters a second × 30) + a ship (≤ 300) ≥ 1,200 updates apart
        "max_score": 1_300_000,
        "per_second": 150,
        "base": 400,
    },
    "rocks": {
        "name": "Rocks",
        "icon": "🪨",
        "modes": [
            {"id": "classic", "label": "Classic"},
            {"id": "calm", "label": "Calm"},
            {"id": "waves", "label": "Waves"},
        ],
        "level_modes": ["waves"],
        "levels_end": True,
        "default_mode": "classic",
        "state_version": 1,
        # points only from shots: one every ≥ 10 updates, ≤ 100 each, plus a saucer (300) ≥ 600 updates apart
        "max_score": 15_000_000,
        "per_second": 700,
        "base": 1_000,
    },
    "hop": {
        "name": "Road Hop",
        "icon": "🐸",
        "modes": [
            {"id": "classic", "label": "Classic"},
            {"id": "easy", "label": "Easy"},
            {"id": "levels", "label": "Levels"},
        ],
        "level_modes": ["levels"],
        "levels_end": True,
        "default_mode": "classic",
        "state_version": 1,
        # a hop takes 8 updates; a crossing is ≥ 7 hops + 24 updates for ≤ 120 + 50 + 113 (+250 per 5 homes)
        "max_score": 850_000,
        "per_second": 250,
        "base": 1_000,
    },
    "snakeduel": {
        "name": "Snake Duel",
        "icon": "🐲",
        "modes": [
            {"id": "cpu", "label": "Against the computer"},
            {"id": "two", "label": "Two players"},
        ],
        "level_modes": ["cpu", "two"],
        "levels_end": True,
        "default_mode": "cpu",
        "state_version": 1,
        # food 10 a cell at ≤ 12 cells a second; a match (500 × min(arena, 10) + rounds) takes ≥ 7.25 s
        "max_score": 5_000_000,
        "per_second": 800,
        "base": 500,
    },
    # Wave 4 (the puzzle and word games). Their puzzles are made from the seed, so there is no level list
    # (`level_modes` empty); a score is a number that is higher the better the player did, so the leaderboard
    # and a race need no special case. `unfinished_zero`: a game that wasn't solved scores 0, which isn't stored.
    "sudoku": {
        "name": "Sudoku",
        "icon": "🧩",
        "modes": [
            {"id": "easy", "label": "Easy"},
            {"id": "medium", "label": "Medium"},
            {"id": "hard", "label": "Hard"},
            {"id": "expert", "label": "Expert"},
        ],
        "level_modes": [],
        "default_mode": "easy",
        "state_version": 1,
        "unfinished_zero": True,
        "race": {"rule": "score"},
        # score = 10,000 − the counted seconds (the time played + 30 s a hint + 10 s a mistake shown at once),
        # so the fastest counted time ranks first; it is only ever given for a solved puzzle
        "max_score": 10_000,
        "per_second": 10_000,
        "base": 10_000,
    },
    "wordguess": {
        "name": "Word Guess",
        "icon": "🔤",
        "modes": [
            {"id": "classic", "label": "Six tries"},
            {"id": "easy", "label": "Eight tries"},
            {"id": "strict", "label": "Strict (use every clue)"},
        ],
        "level_modes": [],
        "default_mode": "classic",
        "state_version": 1,
        "unfinished_zero": True,
        "race": {"rule": "score"},
        # score = 1,000 × (tries left + 1) + (999 − seconds, at least 0) for a word found, so fewer tries rank
        # first and then the faster time
        "max_score": 9_999,
        "per_second": 10_000,
        "base": 10_000,
    },
    "wordsearch": {
        "name": "Word Search",
        "icon": "🔎",
        "modes": [
            {"id": "little", "label": "Little ones (5–7)"},
            {"id": "kids", "label": "Kids (8–11)"},
            {"id": "family", "label": "Everyone (12 and up)"},
            {"id": "puzzler", "label": "Puzzler (big grid)"},
        ],
        "level_modes": [],
        "default_mode": "kids",
        "state_version": 1,
        "race": {"rule": "score"},
        # 100 a word (at most 14) + up to 900 for finding them all quickly
        "max_score": 2_500,
        "per_second": 150,
        "base": 1_500,
    },
    # Wave 5. Each has a level list (level_kinds/<game>.py) played by one mode, and is raced from the seed: the same
    # boards, gems, blocks, course, landing sites and missiles on both phones; the higher score wins (Lander's score
    # includes the fuel left, so a landing with more fuel left wins).
    "bubbles": {
        "name": "Bubble Pop",
        "icon": "🫧",
        "modes": [
            {"id": "classic", "label": "Classic"},
            {"id": "relaxed", "label": "Relaxed (gentle)"},
            {"id": "puzzle", "label": "Puzzles"},
            {"id": "endless", "label": "Endless"},
        ],
        "level_modes": ["puzzle"],
        "levels_end": True,
        "default_mode": "classic",
        "state_version": 1,
        "race": {"rule": "score"},
        # points only for bubbles that leave the board (10 popped, 20 dropped) and a cleared board (500): a board
        # holds at most 64 and the next comes 90 updates after a clear (≤ 1,780 per 103 updates), a shot adds one
        # (≥ 13 updates apart), Endless a row of 8 every ≥ 4 shots — at most ~1,320 a second together
        "max_score": 25_000_000,
        "per_second": 1_400,
        "base": 2_000,
    },
    "gems": {
        "name": "Gem Swap",
        "icon": "💎",
        "modes": [
            {"id": "timed", "label": "Timed (90 s)"},
            {"id": "moves", "label": "Moves (levels)"},
            {"id": "zen", "label": "Zen (no clock)"},
        ],
        "level_modes": ["moves"],
        "levels_end": True,
        "default_mode": "timed",
        "state_version": 1,
        "race": {"rule": "score"},
        # a gem cleared ≤ 10 × 5 (cascade), a special made ≤ 200; a step clears ≤ 64 and takes ≥ 12 updates plus
        # the fall that refills it (7 updates a row); a level's moves left ≤ 40 × 50, ≥ 1.5 s apart
        "max_score": 50_000_000,
        "per_second": 5_500,
        "base": 3_000,
    },
    "stack": {
        "name": "Tower Stack",
        "icon": "🏗️",
        "modes": [
            {"id": "classic", "label": "Classic"},
            {"id": "fast", "label": "Fast"},
            {"id": "easy", "label": "Easy (3 tries)"},
            {"id": "towers", "label": "Towers"},
        ],
        "level_modes": ["towers"],
        "levels_end": True,
        "default_mode": "classic",
        "state_version": 1,
        "race": {"rule": "score"},
        # a floor ≤ 10 + 50 (a perfect run), blocks ≥ 10 updates apart (≤ 360 a second); a tower 200 (≥ 8 floors)
        "max_score": 3_000_000,
        "per_second": 450,
        "base": 500,
    },
    "runner": {
        "name": "Runner",
        "icon": "🏃",
        "modes": [
            {"id": "classic", "label": "Endless"},
            {"id": "easy", "label": "Easy (3 lives)"},
            {"id": "courses", "label": "Courses"},
        ],
        "level_modes": ["courses"],
        "levels_end": True,
        "default_mode": "classic",
        "state_version": 1,
        "race": {"rule": "score"},
        # 1 a 10 px at ≤ 8 px an update (48 a second), coins ≤ 5 × 10 a 60 px segment (≤ 400 a second),
        # a course 100 (≥ 14 segments)
        "max_score": 5_000_000,
        "per_second": 500,
        "base": 500,
    },
    "lander": {
        "name": "Lander",
        "icon": "🚀",
        "modes": [
            {"id": "classic", "label": "Classic"},
            {"id": "easy", "label": "Easy"},
            {"id": "levels", "label": "Levels"},
        ],
        "level_modes": ["levels"],
        "levels_end": True,
        "default_mode": "classic",
        "state_version": 1,
        "race": {"rule": "score"},
        # a landing ≤ 5 × 100 + 1,000 fuel / 2; the fall from the start takes ≥ 60 updates (the engine can only
        # slow it) and the next level comes 120 updates after a landing
        "max_score": 5_000_000,
        "per_second": 350,
        "base": 1_000,
    },
    "defense": {
        "name": "City Defense",
        "icon": "🏙️",
        "modes": [
            {"id": "classic", "label": "Classic"},
            {"id": "easy", "label": "Easy"},
            {"id": "waves", "label": "Waves"},
        ],
        "level_modes": ["waves"],
        "levels_end": True,
        "default_mode": "classic",
        "state_version": 1,
        "race": {"rule": "score"},
        # a wave sends ≤ 50 missiles (25), 4 fliers (100) and pays ≤ 45 × 5 ammo + 6 × 100 cities (≤ 2,475 in all);
        # it lasts ≥ 360 updates and the next starts 150 updates after it
        "max_score": 5_000_000,
        "per_second": 300,
        "base": 2_500,
    },
    # Wave 6. The four puzzles and Code Breaker make their boards, heaps, pictures and codes from the seed (no level
    # list); Type Rain has a stage list. All are raced from the seed. Slide Puzzle and Lights Out rank the fewest moves
    # (score = 10,000 − moves) and a race goes to whoever solves first, fewer moves breaking a tie; Picture Logic and
    # Tile Match score 10,000 − the counted seconds like Sudoku; Code Breaker 1,000 × (rows left + 1) + up to 999 for
    # speed like Word Guess; Type Rain's higher score wins, and the shorter game (more words a minute) breaks a tie.
    "slide": {
        "name": "Slide Puzzle",
        "icon": "🖼️",
        "modes": [
            {"id": "three", "label": "3 × 3"},
            {"id": "four", "label": "4 × 4"},
            {"id": "five", "label": "5 × 5"},
            {"id": "picture", "label": "Picture 3 × 3 (gentle)"},
            {"id": "picture4", "label": "Picture 4 × 4"},
        ],
        "level_modes": [],
        "default_mode": "four",
        "state_version": 1,
        "unfinished_zero": True,
        "race": {"rule": "fastest", "tiebreak": "score"},
        # score = 10,000 − tiles moved (at least 10), only for a solved tray; it can come at once
        "max_score": 10_000,
        "per_second": 10_000,
        "base": 10_000,
    },
    "lights": {
        "name": "Lights Out",
        "icon": "💡",
        "modes": [
            {"id": "little", "label": "Little 3 × 3 (gentle)"},
            {"id": "classic", "label": "5 × 5"},
            {"id": "big", "label": "7 × 7"},
            {"id": "climb", "label": "Climb (3 × 3 to 7 × 7)"},
        ],
        "level_modes": [],
        "default_mode": "classic",
        "state_version": 1,
        "unfinished_zero": True,
        "race": {"rule": "fastest", "tiebreak": "score"},
        # score = 10,000 − presses − 5 × hints (at least 10), only when every light is off; level = the Climb board (≤ 5)
        "max_score": 10_000,
        "per_second": 10_000,
        "base": 10_000,
    },
    "nonogram": {
        "name": "Picture Logic",
        "icon": "🎨",
        "modes": [
            {"id": "five", "label": "5 × 5 (gentle)"},
            {"id": "eight", "label": "8 × 8"},
            {"id": "ten", "label": "10 × 10"},
            {"id": "fifteen", "label": "15 × 15"},
        ],
        "level_modes": [],
        "default_mode": "ten",
        "state_version": 1,
        "unfinished_zero": True,
        "race": {"rule": "score"},
        # score = 10,000 − the counted seconds (time + 30 s a hint + 10 s a mistake shown at once), only when solved
        "max_score": 10_000,
        "per_second": 10_000,
        "base": 10_000,
    },
    "tiles": {
        "name": "Tile Match",
        "icon": "🔷",
        "modes": [
            {"id": "little", "label": "Little (gentle)"},
            {"id": "classic", "label": "Classic"},
            {"id": "big", "label": "Big heap"},
        ],
        "level_modes": [],
        "default_mode": "classic",
        "state_version": 1,
        "unfinished_zero": True,
        "race": {"rule": "score"},
        # score = 10,000 − the counted seconds (time + 15 s a hint + 30 s a shuffle), only when the heap is cleared
        "max_score": 10_000,
        "per_second": 10_000,
        "base": 10_000,
    },
    "codebreak": {
        "name": "Code Breaker",
        "icon": "🔐",
        "modes": [
            {"id": "little", "label": "Little (3 of 4, gentle)"},
            {"id": "classic", "label": "Classic (4 of 6)"},
            {"id": "norepeat", "label": "No repeats (4 of 6)"},
            {"id": "master", "label": "Master (5 of 8)"},
        ],
        "level_modes": [],
        "default_mode": "classic",
        "state_version": 1,
        "unfinished_zero": True,
        "race": {"rule": "score"},
        # score = 1,000 × (rows left + 1) + (999 − seconds, at least 0) for a cracked code: at most 12 rows (Master)
        "max_score": 12_999,
        "per_second": 13_000,
        "base": 13_000,
    },
    "typerain": {
        "name": "Type Rain",
        "icon": "⌨️",
        "modes": [
            {"id": "letters", "label": "Little ones (letters)"},
            {"id": "easy", "label": "Easy (short words)"},
            {"id": "classic", "label": "Classic"},
            {"id": "stages", "label": "Stages"},
        ],
        "level_modes": ["stages"],
        "levels_end": True,
        "default_mode": "classic",
        "state_version": 1,
        "race": {"rule": "score", "tiebreak": "faster"},
        # a word ≤ 10 letters × 10 × 3 (a run of 25), words ≥ 40 updates apart (≤ 450 a second); a stage 200 (≥ 5 words
        # and a 2 s break: ≤ 38 a second)
        "max_score": 5_000_000,
        "per_second": 600,
        "base": 1_000,
    },
    # Wave 7: two-player board games, played against the computer (Easy / Medium / Hard), two players on one screen,
    # or turn by turn from two phones (SPEC §13.5): the `turns` modes are played only with someone, each move checked by
    # the server's rules (app/rules/<game>.py). Score: a win 100 / 250 / 500 (Easy / Medium / Hard; 500 against a person)
    # plus a bonus for how well, at most as much again; a draw a quarter; a loss and one-screen games 0 (not kept,
    # `unfinished_zero`). A win can come within seconds, so the base is the most (as for the puzzles). No race, no levels.
    "fourrow": {
        "name": "Four in a Row",
        "icon": "🔴",
        "modes": TURN_GAME_MODES,
        "level_modes": [],
        "default_mode": "easy",
        "state_version": 1,
        "unfinished_zero": True,
        "race": False,
        "turns": {"modes": ["phones"]},
        # 10 for each empty place left at a win (at most the win's base again)
        "max_score": 1_000,
        "per_second": 1_000,
        "base": 1_000,
    },
    "tictactoe": {
        "name": "Tic-tac-toe",
        "icon": "⭕",
        "modes": TURN_GAME_MODES,
        "level_modes": [],
        "default_mode": "easy",
        "state_version": 1,
        "unfinished_zero": True,
        "race": False,
        "turns": {"modes": ["phones"]},
        # 25 for each empty square left at a win (at most the win's base again)
        "max_score": 1_000,
        "per_second": 1_000,
        "base": 1_000,
    },
    "checkers": {
        "name": "Checkers",
        "icon": "🏁",
        "modes": TURN_GAME_MODES,
        "level_modes": [],
        "default_mode": "easy",
        "state_version": 1,
        "unfinished_zero": True,
        "race": False,
        "turns": {"modes": ["phones"]},
        # 30 for each of your pieces left at a win (at most the win's base again)
        "max_score": 1_000,
        "per_second": 1_000,
        "base": 1_000,
    },
    "reversi": {
        "name": "Reversi",
        "icon": "⚫",
        "modes": TURN_GAME_MODES,
        "level_modes": [],
        "default_mode": "easy",
        "state_version": 1,
        "unfinished_zero": True,
        "race": False,
        "turns": {"modes": ["phones"]},
        # 5 for each disc more than the other side's at a win (at most the win's base again)
        "max_score": 1_000,
        "per_second": 1_000,
        "base": 1_000,
    },
    "dots": {
        "name": "Dots and Boxes",
        "icon": "🔲",
        "modes": TURN_GAME_MODES,
        "level_modes": [],
        "default_mode": "easy",
        "state_version": 1,
        "unfinished_zero": True,
        "race": False,
        "turns": {"modes": ["phones"]},
        # 20 for each box more than the other side's at a win (at most the win's base again)
        "max_score": 1_000,
        "per_second": 1_000,
        "base": 1_000,
    },
    "seabattle": {
        "name": "Sea Battle",
        "icon": "🚢",
        "modes": TURN_GAME_MODES,
        "level_modes": [],
        "default_mode": "easy",
        "state_version": 1,
        "unfinished_zero": True,
        "race": False,
        "turns": {"modes": ["phones"]},
        # 15 for each square of your fleet not hit, at a win (at most the win's base again)
        "max_score": 1_000,
        "per_second": 1_000,
        "base": 1_000,
    },
    # Wave 8. Ludo and Snakes and Ladders: 2–4 players, against the computer, on one phone, or turn by turn from 2–4
    # phones with the server's dice (SPEC §13.5).
    "ludo": {
        "name": "Ludo",
        "icon": "🎲",
        "modes": DICE_GAME_MODES,
        "level_modes": [],
        "default_mode": "cpu",
        "state_version": 1,
        "unfinished_zero": True,
        "race": False,
        "turns": {"modes": ["phones"]},
        # a win: against the computer 100 a computer player (+10 a token of theirs not home), from phones 500 (+25 a
        # token), at most the base again; a win can come at once (a saved game continued), so the base is the most
        "max_score": 1_000,
        "per_second": 1_000,
        "base": 1_000,
    },
    "snakes": {
        "name": "Snakes and Ladders",
        "icon": "🪜",
        "modes": DICE_GAME_MODES,
        "level_modes": [],
        "default_mode": "cpu",
        "state_version": 1,
        "unfinished_zero": True,
        "race": False,
        "turns": {"modes": ["phones"]},
        # a win: 100 against the computer, 500 from phones, + 5 a square the nearest other still had to go (≤ the base)
        "max_score": 1_000,
        "per_second": 1_000,
        "base": 1_000,
    },
    # Carrom: the computer (three levels), two or four (two teams) on one phone (whole-number physics).
    "carrom": {
        "name": "Carrom",
        "icon": "🎯",
        "modes": [
            {"id": "easy", "label": "Computer · Easy (gentle)"},
            {"id": "medium", "label": "Computer · Medium"},
            {"id": "hard", "label": "Computer · Hard"},
            {"id": "two", "label": "Two players (one screen)"},
            {"id": "doubles", "label": "Doubles: four, two teams (one screen)"},
        ],
        "level_modes": [],
        "default_mode": "easy",
        "state_version": 1,
        "unfinished_zero": True,
        "race": False,
        # a win: the level's base (100 / 250 / 500; 500 from two phones) + 20 a board point (the other's coins left,
        # +3 for the queen), at most the base again
        "max_score": 1_000,
        "per_second": 1_000,
        "base": 1_000,
    },
    "chess": {
        "name": "Chess",
        "icon": "♞",
        "modes": TURN_GAME_MODES,
        "level_modes": [],
        "default_mode": "easy",
        "state_version": 1,
        "unfinished_zero": True,
        "race": False,
        "turns": {"modes": ["phones"]},
        # a win: the level's base (100 / 250 / 500; 500 from two phones) + 10 a point of material left (≤ the base);
        # a draw a quarter of the base
        "max_score": 1_000,
        "per_second": 1_000,
        "base": 1_000,
    },
    # Waves 9 and 10 (spec/GAMES.md): calm puzzles made from the seed, each with a Levels mode of 200 numbered boards
    # that carries on from the next one (SPEC §14).
    "arrows": {
        "name": "Arrow Release",
        "icon": "🏹",
        "modes": [
            {"id": "little", "label": "Little 5 × 5 (gentle)"},
            {"id": "classic", "label": "8 × 8"},
            {"id": "big", "label": "12 × 12"},
            {"id": "twisty", "label": "Twisty 10 × 10"},
            {"id": "levels", "label": "Levels"},
        ],
        "level_modes": [],
        "default_mode": "classic",
        "state_version": 1,
        "unfinished_zero": True,
        "race": {"rule": "fastest", "tiebreak": "score"},
        # score = 10,000 − 10 × seconds − 300 × mistakes − 200 × hints a board (at least 10), only for cleared boards; Levels adds the boards up
        "max_score": 2_000_000,
        "per_second": 10_000,
        "base": 10_000,
    },
    "parking": {
        "name": "Car Park",
        "icon": "🚗",
        "modes": [
            {"id": "little", "label": "Little (gentle)"},
            {"id": "classic", "label": "Classic"},
            {"id": "hard", "label": "Hard"},
            {"id": "levels", "label": "Levels"},
        ],
        "level_modes": [],
        "default_mode": "classic",
        "state_version": 1,
        "unfinished_zero": True,
        "race": {"rule": "fastest", "tiebreak": "score"},
        # score = 10,000 − 5 × seconds − 50 × moves over the fewest − 200 × hints a board (at least 10), only when the red car is out
        "max_score": 2_000_000,
        "per_second": 10_000,
        "base": 10_000,
    },
    "watersort": {
        "name": "Colour Sort",
        "icon": "🧪",
        "modes": [
            {"id": "little", "label": "3 colours (gentle)"},
            {"id": "classic", "label": "7 colours"},
            {"id": "big", "label": "10 colours"},
            {"id": "levels", "label": "Levels"},
        ],
        "level_modes": [],
        "default_mode": "classic",
        "state_version": 1,
        "unfinished_zero": True,
        "race": {"rule": "fastest", "tiebreak": "score"},
        # score = 10,000 − 5 × seconds − 10 × pours − 200 × hints a board (at least 10), only when sorted
        "max_score": 2_000_000,
        "per_second": 10_000,
        "base": 10_000,
    },
    "bolts": {
        "name": "Bolt Sort",
        "icon": "🔩",
        "modes": [
            {"id": "little", "label": "3 colours (gentle)"},
            {"id": "classic", "label": "6 colours"},
            {"id": "hidden", "label": "6 colours, hidden"},
            {"id": "levels", "label": "Levels"},
        ],
        "level_modes": [],
        "default_mode": "classic",
        "state_version": 1,
        "unfinished_zero": True,
        "race": {"rule": "fastest", "tiebreak": "score"},
        # score = 10,000 − 5 × seconds − 10 × moves − 200 × hints a board (at least 10), only when sorted
        "max_score": 2_000_000,
        "per_second": 10_000,
        "base": 10_000,
    },
    "connect": {
        "name": "Dot Connect",
        "icon": "🔵",
        "modes": [
            {"id": "little", "label": "Little 5 × 5 (gentle)"},
            {"id": "classic", "label": "7 × 7"},
            {"id": "big", "label": "9 × 9"},
            {"id": "levels", "label": "Levels"},
        ],
        "level_modes": [],
        "default_mode": "classic",
        "state_version": 1,
        "unfinished_zero": True,
        "race": {"rule": "fastest", "tiebreak": "score"},
        # score = 10,000 − 10 × seconds − 200 × hints a board (at least 10), only when every pair is joined (and every square filled)
        "max_score": 2_000_000,
        "per_second": 10_000,
        "base": 10_000,
    },
    "untangle": {
        "name": "Untangle",
        "icon": "🕸️",
        "modes": [
            {"id": "little", "label": "6 points (gentle)"},
            {"id": "classic", "label": "10 points"},
            {"id": "big", "label": "16 points"},
            {"id": "levels", "label": "Levels"},
        ],
        "level_modes": [],
        "default_mode": "classic",
        "state_version": 1,
        "unfinished_zero": True,
        "race": {"rule": "fastest", "tiebreak": "score"},
        # score = 10,000 − 10 × seconds − 20 × moves − 200 × hints a board (at least 10), only when nothing crosses
        "max_score": 2_000_000,
        "per_second": 10_000,
        "base": 10_000,
    },
}

# Carrying on from the next level (SPEC §14): the modes whose levels are puzzles or goals, so the next game starts at
# the first level the person hasn't cleared. {game: {mode: how many levels}} — None: the mode's level list (levels.py)
# decides; a number: the game's own fixed set of boards. Score chases (Brick Breaker, the wave shooters) and opponents
# (Paddle Duel, Snake Duel, Tank Battle) always start at level 1.
CONTINUE_LEVELS: dict[str, dict[str, int | None]] = {
    "snake": {"maze": None}, "flap": {"course": None}, "mines": {"boards": None}, "merge": {"goals": None},
    "colours": {"challenge": None}, "cards": {"challenge": None}, "mole": {"gardens": None},
    "numbers": {"challenge": None}, "racer": {"stages": None}, "hop": {"levels": None}, "bubbles": {"puzzle": None},
    "gems": {"moves": None}, "stack": {"towers": None}, "runner": {"courses": None}, "lander": {"levels": None},
    "typerain": {"stages": None}, "lights": {"climb": 5},
    "arrows": {"levels": 200}, "parking": {"levels": 200}, "watersort": {"levels": 200}, "bolts": {"levels": 200},
    "connect": {"levels": 200}, "untangle": {"levels": 200},
}
for _g, _modes in CONTINUE_LEVELS.items():
    GAMES[_g]["continue_levels"] = dict(_modes)

# Tags for the Games page search (SPEC §9), from a fixed set. The kind of game is listed here; `levels` (a level list
# or numbered boards), `turn by turn` (modes from several phones) and `gentle` (a mode marked gentle, or one for
# little ones) are added from the game's own entry so they can't drift.
TAG_SET = ("arcade", "puzzle", "word", "board", "cards", "dice", "two players", "turn by turn", "levels", "gentle")
_KIND_TAGS: dict[str, tuple[str, ...]] = {
    "snake": ("arcade",), "brick": ("arcade",), "blocks": ("arcade", "puzzle"), "duel": ("arcade",),
    "racer": ("arcade",), "flap": ("arcade",), "mines": ("puzzle",), "merge": ("puzzle",), "colours": ("puzzle",),
    "cards": ("puzzle", "cards"), "mole": ("arcade",), "numbers": ("arcade", "puzzle"), "tanks": ("arcade",),
    "invaders": ("arcade",), "rocks": ("arcade",), "hop": ("arcade",), "snakeduel": ("arcade", "two players"),
    "sudoku": ("puzzle",), "wordguess": ("word", "puzzle"), "wordsearch": ("word", "puzzle"), "bubbles": ("arcade", "puzzle"),
    "gems": ("puzzle",), "stack": ("arcade",), "runner": ("arcade",), "lander": ("arcade",), "defense": ("arcade",),
    "slide": ("puzzle",), "lights": ("puzzle",), "nonogram": ("puzzle",), "tiles": ("puzzle",), "codebreak": ("puzzle",),
    "typerain": ("arcade", "word"),
    "fourrow": ("board", "two players"), "tictactoe": ("board", "two players"), "checkers": ("board", "two players"),
    "reversi": ("board", "two players"), "dots": ("board", "two players"), "seabattle": ("board", "two players"),
    "ludo": ("board", "dice", "two players"), "snakes": ("board", "dice", "two players", "gentle"),
    "carrom": ("board", "two players"), "chess": ("board", "two players"),
    "arrows": ("puzzle",), "parking": ("puzzle",), "watersort": ("puzzle",), "bolts": ("puzzle",),
    "connect": ("puzzle",), "untangle": ("puzzle",),
}


def _tags(gid: str) -> list[str]:
    g = GAMES[gid]
    tags = set(_KIND_TAGS.get(gid, ()))
    if g.get("level_modes") or g.get("continue_levels"):
        tags.add("levels")
    if g.get("turns", {}).get("modes"):
        tags.add("turn by turn")
    if any("gentle" in m["label"].lower() or "little ones" in m["label"].lower() for m in g["modes"]):
        tags.add("gentle")
    return [t for t in TAG_SET if t in tags]


for _g in GAMES:
    GAMES[_g]["tags"] = _tags(_g)

GAME_IDS = tuple(GAMES)


def continue_modes(game: str) -> dict:
    """{mode: fixed level count or None} for the modes of `game` that carry on from the next level."""
    return dict(GAMES.get(game, {}).get("continue_levels") or {})


def continues(game: str, mode) -> bool:
    return isinstance(mode, str) and mode in continue_modes(game)


def exists(game: str) -> bool:
    return game in GAMES


def mode_ids(game: str) -> list[str]:
    return [m["id"] for m in GAMES[game]["modes"]]


def mode_label(game: str, mode: str) -> str:
    for m in GAMES.get(game, {}).get("modes", []):
        if m["id"] == mode:
            return m["label"]
    if isinstance(mode, str) and mode.startswith("daily-"):          # a daily challenge's scores (daily.py)
        return "Daily challenge · " + mode[6:]
    return mode


def turn_modes(game: str) -> list[str]:
    """The modes of a game played turn by turn from two (or more) phones (SPEC §13.5); [] for most games."""
    return list(GAMES.get(game, {}).get("turns", {}).get("modes", []))


def is_turns(game: str, mode) -> bool:
    return isinstance(mode, str) and mode in turn_modes(game)


def name(game: str) -> str:
    return GAMES.get(game, {}).get("name", game)


def keeps_nothing(game: str, score) -> bool:
    """True for a game that scores 0 until it is won (Sudoku, Word Guess): a 0 isn't stored as a result."""
    return bool(GAMES.get(game, {}).get("unfinished_zero")) and score == 0


def uses_levels(game: str, mode: str) -> bool:
    """True if this game and mode play through the game's level list (levels.py)."""
    return mode in GAMES.get(game, {}).get("level_modes", [])


def max_level(game: str, mode: str, level_count: int | None) -> int:
    """The highest level a result can claim: the game's level count when it ends there (Snake's mazes),
    a few rounds of it when the levels repeat (Brick Breaker), else MAX_LEVEL."""
    fixed = GAMES.get(game, {}).get("continue_levels", {}).get(mode)
    if fixed:                                  # a fixed set of numbered boards (SPEC §14)
        return max(MAX_LEVEL, int(fixed))
    if not level_count or not uses_levels(game, mode):
        return MAX_LEVEL
    if GAMES[game].get("levels_end"):          # the game ends after the list's last level
        return level_count
    return max(MAX_LEVEL, level_count * 5)


def check_score(game: str, mode: str, score, level, seconds, level_count: int | None = None) -> str | None:
    """None if the result is possible for this game and mode, else a readable
    reason (SPEC §5.1). Numbers must be whole and in range. `level_count` is
    the number of levels the game was given (levels.py), for games with levels."""
    g = GAMES.get(game)
    if not g:
        return "Unknown game."
    if mode not in mode_ids(game):
        return "Unknown mode for this game."
    for label, v in (("score", score), ("level", level), ("seconds", seconds)):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or v in (float("inf"), float("-inf")):
            return f"The {label} must be a number."
        if v < 0:
            return f"The {label} can't be negative."
    if score != int(score) or level != int(level):
        return "The score and level must be whole numbers."
    if seconds > MAX_SECONDS:
        return "That game is longer than the app accepts (6 hours)."
    top = max_level(game, mode, level_count)
    if level > top:
        return f"The level can be at most {top}."
    if score > g["max_score"]:
        return f"That score is higher than {g['name']} allows."
    if score > seconds * g["per_second"] + g["base"]:
        return "That score isn't possible in that time."
    return None


# The looks (SPEC §3.4). Drawn by static/games/kit.js; the server only checks the id.
LOOKS: dict[str, str] = {
    "modern": "Modern",
    "lcd": "Retro LCD",
    "neon": "Neon",
    "pixel": "Pixel",
    "paper": "Paper",
    "contrast": "High contrast",
}
DEFAULT_LOOK = "modern"
