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
    "solitaire": {
        "name": "Solitaire",
        "icon": "♠️",
        "modes": [
            {"id": "draw1", "label": "Draw one"},
            {"id": "draw3", "label": "Draw three"},
        ],
        "level_modes": [],
        "default_mode": "draw1",
        "state_version": 1,
        "race": {"rule": "score", "tiebreak": "faster"},
        # ≤ 52 × 10 to the foundations, 21 × 5 for turned cards, 24 × 5 from the waste, and a bonus for a quick win
        # of at most 1,000: under 1,800 in all; a win takes well over 20 s
        "max_score": 2_000,
        "per_second": 100,
        "base": 1_500,
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
}

GAME_IDS = tuple(GAMES)


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
