"""Car Park · Puzzle book: a car park drawn as six rows of six letters, checked by a search for the fewest moves."""
from collections import deque

from ..level_common import LevelError
from . import Grid

N, EXIT_ROW = 6, 2
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
MAX_STATES = 40_000          # the game's own search looks further, so a board passed here is always solved there
MIN_MOVES, MAX_MOVES = 2, 50


def vehicles(grid):
    """[(across, length, lane, start)] with the red car "A" first. Raises LevelError for a bad drawing."""
    cells = {}
    for r, row in enumerate(grid):
        for c, ch in enumerate(row):
            if ch != ".":
                cells.setdefault(ch, []).append((r, c))
    if "A" not in cells:
        raise LevelError('the red car "A" is missing')
    out = []
    for ch in sorted(cells, key=lambda x: (x != "A", x)):
        sq = sorted(cells[ch])
        rows, cols = {r for r, _ in sq}, {c for _, c in sq}
        if len(sq) not in (2, 3):
            raise LevelError(f'"{ch}" must cover 2 squares (a car) or 3 (a lorry), not {len(sq)}')
        if len(rows) == 1 and max(cols) - min(cols) == len(sq) - 1:
            out.append((True, len(sq), sq[0][0], min(cols)))
        elif len(cols) == 1 and max(rows) - min(rows) == len(sq) - 1:
            out.append((False, len(sq), sq[0][1], min(rows)))
        else:
            raise LevelError(f'"{ch}" must be one straight line of squares next to each other')
    red = out[0]
    if not (red[0] and red[1] == 2 and red[2] == EXIT_ROW):
        raise LevelError('the red car "A" must be 2 squares long, lying across the third row (the exit row)')
    if red[3] == N - 2:
        raise LevelError("the red car mustn't start at the exit")
    if len(out) < 3:
        raise LevelError("use at least two other vehicles")
    return out


def fewest(vs):
    """The fewest moves to get the red car to the exit (a move slides one vehicle any distance), or None."""
    shape = [(h, ln, lane) for h, ln, lane, _ in vs]
    start = tuple(p for *_, p in vs)
    seen, queue = {start: 0}, deque([start])
    while queue:
        pos = queue.popleft()
        d = seen[pos]
        if pos[0] == N - 2:
            return d
        if len(seen) > MAX_STATES:
            return None
        grid = [[-1] * N for _ in range(N)]
        for i, (h, ln, lane) in enumerate(shape):
            for k in range(ln):
                if h:
                    grid[lane][pos[i] + k] = i
                else:
                    grid[pos[i] + k][lane] = i
        for i, (h, ln, lane) in enumerate(shape):
            p = pos[i]
            for step in (-1, 1):
                q = p
                while True:
                    edge = q - 1 if step < 0 else q + ln
                    if edge < 0 or edge >= N:
                        break
                    if (grid[lane][edge] if h else grid[edge][lane]) >= 0:
                        break
                    q += step
                    nxt = pos[:i] + (q,) + pos[i + 1:]
                    if nxt not in seen:
                        seen[nxt] = d + 1
                        queue.append(nxt)
    return None


def _check(c):
    vs = vehicles(c["grid"])
    moves = fewest(vs)
    if moves is None:
        raise LevelError("the red car can't get out (or it takes too long to work out)")
    if not MIN_MOVES <= moves <= MAX_MOVES:
        raise LevelError(f"the fewest moves is {moves}; it must be {MIN_MOVES}–{MAX_MOVES}")
    c["_moves"] = moves


def _difficulty(c):
    moves = c.pop("_moves", None)
    if moves is None:
        moves = fewest(vehicles(c["grid"])) or 0
    return min(100, moves * 3 + len(vehicles(c["grid"])) * 2)


def _fingerprint(c):
    # the same car park whatever the letters: vehicles renamed in reading order, the red car staying "A"
    names, out = {"A": "A"}, []
    for row in c["grid"]:
        for ch in row:
            if ch != "." and ch not in names:
                names[ch] = LETTERS[len(names)]
        out.append("".join(names.get(ch, ".") for ch in row))
    return ["/".join(out)]


KIND = {
    "game": "parking",
    "label": "Car Park · Puzzle book",
    "noun": "car park",
    "modes": ["book"],
    "fields": {"grid": Grid("." + LETTERS, (6, 6), (6, 6))},
    "check": _check,
    "difficulty": _difficulty,
    "fingerprint": _fingerprint,
    "about": """You design car parks for Car Park, a calm family sliding puzzle. The car park is 6 squares by 6 with one
exit, on the right-hand end of the third row. Cars are 2 squares long and lorries 3; each lies either across (left to
right) or along (top to bottom) and can only slide forwards and backwards along its own line, never turn, and never
pass through another vehicle. The goal is to slide vehicles until the red car can drive out of the exit. Sliding one
vehicle any distance is one move.
"grid" draws the car park: exactly 6 rows of 6 characters. "." is an empty square; each vehicle is drawn with its own
capital letter on the 2 or 3 squares it covers, in one straight line. "A" is the red car: exactly 2 squares, lying
across the third row (row 3 of 6), not already at the right-hand end. Use B, C, D … for the other vehicles, each
letter for one vehicle only. A vehicle lying across the third row to the right of the red car can never leave that
row and blocks the exit for ever, so don't put one there. The app works out the fewest moves to free the red car;
it must be 2-50. More vehicles in the way of each other, and more moves, are harder.""",
    "target": lambda n: f"Car park {n} should need about {min(40, 3 + n)} moves, with about "
    f"{min(13, 4 + n // 3)} vehicles besides the red car.",
    "example": {"name": "School run", "grid": ["BB..C.", "....C.", "AA..C.", "..DDD.", "......", "......"]},
    "builtin": [],      # filled in below
}

KIND["builtin"] = [
    {"name": "Quick exit", "grid": ["......", "..BCCD", "AAB..D", "......", "......", "......"]},
    {"name": "Morning rush", "grid": ["......", "..BCCD", "AABE.D", "FF.E..", "...EGG", "...HH."]},
    {"name": "Lorry in the way", "grid": ["....B.", "...CBD", ".AACBD", "..EEE.", "......", "...FF."]},
    {"name": "Market day", "grid": ["BBCCDE", "...FDE", "GAAF.E", "GH....", ".H.III", ".H..JJ"]},
    {"name": "Full house", "grid": ["...BCC", ".D.BE.", "FDAAE.", "FGGGHH", "IIJKKL", "..J..L"]},
    {"name": "Rush hour", "grid": ["...BCC", "DE.BF.", "DEAAF.", ".GHHF.", ".GIJJJ", "KKI.LL"]},
]
