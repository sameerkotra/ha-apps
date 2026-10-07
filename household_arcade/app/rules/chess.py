"""Chess (the rules this app plays, SPEC §13.5 / spec/GAMES.md): the FIDE laws of chess for moving pieces —

- castling (king and rook unmoved, the squares between empty, the king not in check and not passing through or
  landing on an attacked square), en passant (only on the move right after the double step), promotion (to a queen,
  rook, bishop or knight — the move names which: {"promo": "q" | "r" | "b" | "n"}, required);
- a move may never leave your own king in check; no legal move: checkmate (in check, a loss) or stalemate (a draw);
- draws are automatic (nobody has to claim them): the same position for the third time (the same pieces on the same
  squares, the same side to move, the same castling rights and en passant possible), 50 moves each (100 plies)
  without a capture or a pawn move, and material that can't mate (king against king, king and one bishop or knight
  against king, or only bishops all on squares of one colour);
- the seat that plays White moves first and comes from the seed (seed % 2), as the other turn-by-turn games.

Squares are named "a1"–"h8"; a move is {"from": "e2", "to": "e4"} (+ "promo"). The state keeps the position as a
FEN string. `perft(fen, depth)` counts the positions `depth` plies ahead (the tests check the well-known numbers).
Mirrors static/games/chess-logic.js (move for move: the same legal moves, the same FEN after every move)."""
from __future__ import annotations

from .base import Illegal, canonical, copy, margin_score, need_turn

PLAYERS = (2, 2)
OPTIONS: dict = {}
DICE = 0
BASE = 500
BONUS_POINT = 10              # each point of material you still have at a win (P 1, N B 3, R 5, Q 9)
START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
FILES = "abcdefgh"
VALUE = {"p": 1, "n": 3, "b": 3, "r": 5, "q": 9, "k": 0}

KNIGHT = (-21, -19, -12, -8, 8, 12, 19, 21)
KING = (-11, -10, -9, -1, 1, 9, 10, 11)
DIAG = (-11, -9, 9, 11)
ORTHO = (-10, -1, 1, 10)
PROMOS = "qrbn"


# ---------------------------------------------------------------------------
# Squares: outside the 10 × 12 mailbox ("x") the board is 0–63, a8 = 0 … h1 = 63
# ---------------------------------------------------------------------------

def mb(i: int) -> int:
    return 21 + (i // 8) * 10 + i % 8


def ext(m: int) -> int:
    r, c = divmod(m - 21, 10)
    return r * 8 + c


def sq_name(i: int) -> str:
    return FILES[i % 8] + str(8 - i // 8)


def sq_index(name) -> int:
    if not isinstance(name, str) or len(name) != 2 or name[0] not in FILES or name[1] not in "12345678":
        return -1
    return (8 - int(name[1])) * 8 + FILES.index(name[0])


class Pos:
    __slots__ = ("b", "white", "castle", "ep", "half", "full")

    def __init__(self, b, white, castle, ep, half, full):
        self.b, self.white, self.castle, self.ep, self.half, self.full = b, white, castle, ep, half, full


def from_fen(fen: str) -> Pos:
    parts = fen.split()
    rows = parts[0].split("/")
    b = ["x"] * 120
    for r, row in enumerate(rows):
        c = 0
        for ch in row:
            if ch.isdigit():
                for _ in range(int(ch)):
                    b[21 + r * 10 + c] = "."
                    c += 1
            else:
                b[21 + r * 10 + c] = ch
                c += 1
    castle = "" if parts[2] == "-" else parts[2]
    ep = 0 if parts[3] == "-" else mb(sq_index(parts[3]))
    return Pos(b, parts[1] == "w", castle, ep, int(parts[4]), int(parts[5]))


def board_text(p: Pos) -> str:
    rows = []
    for r in range(8):
        row, empty = "", 0
        for c in range(8):
            ch = p.b[21 + r * 10 + c]
            if ch == ".":
                empty += 1
            else:
                if empty:
                    row += str(empty)
                    empty = 0
                row += ch
        rows.append(row + (str(empty) if empty else ""))
    return "/".join(rows)


def to_fen(p: Pos) -> str:
    return " ".join([board_text(p), "w" if p.white else "b", p.castle or "-", sq_name(ext(p.ep)) if p.ep else "-",
                     str(p.half), str(p.full)])


# ---------------------------------------------------------------------------
# Moves
# ---------------------------------------------------------------------------

def attacked(b, sq: int, by_white: bool) -> bool:
    if by_white:
        if b[sq + 9] == "P" or b[sq + 11] == "P":
            return True
        n, k, bq, rq = "N", "K", "BQ", "RQ"
    else:
        if b[sq - 9] == "p" or b[sq - 11] == "p":
            return True
        n, k, bq, rq = "n", "k", "bq", "rq"
    for d in KNIGHT:
        if b[sq + d] == n:
            return True
    for d in KING:
        if b[sq + d] == k:
            return True
    for d in DIAG:
        t = sq + d
        while b[t] == ".":
            t += d
        if b[t] in bq:
            return True
    for d in ORTHO:
        t = sq + d
        while b[t] == ".":
            t += d
        if b[t] in rq:
            return True
    return False


def _mine(ch: str, white: bool) -> bool:
    return ch.isupper() if white else ch.islower()


def _theirs(ch: str, white: bool) -> bool:
    return (ch.islower() and ch != "x") if white else ch.isupper()


def pseudo(p: Pos) -> list:
    """Moves as (from, to, promo, flag): flag 0 plain, 1 en passant, 2 castling, 3 a pawn's double step."""
    b, w, out = p.b, p.white, []
    fwd = -10 if w else 10
    start_row = 6 if w else 1
    last_row = 0 if w else 7
    for f in range(21, 99):
        ch = b[f]
        if ch in ".x" or not _mine(ch, w):
            continue
        k = ch.lower()
        if k == "p":
            t = f + fwd
            row = (t - 21) // 10
            if b[t] == ".":
                if row == last_row:
                    for pr in PROMOS:
                        out.append((f, t, pr, 0))
                else:
                    out.append((f, t, "", 0))
                    if (f - 21) // 10 == start_row and b[t + fwd] == ".":
                        out.append((f, t + fwd, "", 3))
            for d in (fwd - 1, fwd + 1):
                t = f + d
                if _theirs(b[t], w):
                    if (t - 21) // 10 == last_row:
                        for pr in PROMOS:
                            out.append((f, t, pr, 0))
                    else:
                        out.append((f, t, "", 0))
                elif t == p.ep and p.ep:
                    out.append((f, t, "", 1))
        elif k == "n" or k == "k":
            for d in (KNIGHT if k == "n" else KING):
                t = f + d
                if b[t] == "." or _theirs(b[t], w):
                    out.append((f, t, "", 0))
            if k == "k":
                _castles(p, f, out)
        else:
            dirs = DIAG if k == "b" else ORTHO if k == "r" else DIAG + ORTHO
            for d in dirs:
                t = f + d
                while b[t] == ".":
                    out.append((f, t, "", 0))
                    t += d
                if _theirs(b[t], w):
                    out.append((f, t, "", 0))
    return out


def _castles(p: Pos, f: int, out: list) -> None:
    b, w = p.b, p.white
    home = 95 if w else 25                       # e1 / e8
    if f != home or attacked(b, home, not w):
        return
    rights = "KQ" if w else "kq"
    rook = "R" if w else "r"
    if rights[0] in p.castle and b[home + 1] == "." and b[home + 2] == "." and b[home + 3] == rook \
            and not attacked(b, home + 1, not w) and not attacked(b, home + 2, not w):
        out.append((home, home + 2, "", 2))
    if rights[1] in p.castle and b[home - 1] == "." and b[home - 2] == "." and b[home - 3] == "." \
            and b[home - 4] == rook and not attacked(b, home - 1, not w) and not attacked(b, home - 2, not w):
        out.append((home, home - 2, "", 2))


_CASTLE_LOSS = {95: "KQ", 25: "kq", 98: "K", 91: "Q", 28: "k", 21: "q"}


def make(p: Pos, mv) -> tuple:
    f, t, pr, flag = mv
    b = p.b
    undo = (f, t, b[f], b[t], p.castle, p.ep, p.half, p.full, flag)
    piece = b[f]
    captured = b[t]
    b[t] = (pr.upper() if p.white else pr) if pr else piece
    b[f] = "."
    if flag == 1:                                # en passant: the pawn taken is beside the mover
        b[t + (10 if p.white else -10)] = "."
        captured = "p"
    elif flag == 2:
        if t > f:
            b[t - 1], b[t + 1] = b[t + 1], "."
        else:
            b[t + 1], b[t - 2] = b[t - 2], "."
    if p.castle:
        lost = _CASTLE_LOSS.get(f, "") + _CASTLE_LOSS.get(t, "")
        if lost:
            p.castle = "".join(c for c in p.castle if c not in lost)
    p.ep = (f + t) // 2 if flag == 3 else 0
    p.half = 0 if piece in "Pp" or captured != "." else p.half + 1
    if not p.white:
        p.full += 1
    p.white = not p.white
    return undo


def unmake(p: Pos, undo) -> None:
    f, t, piece, captured, castle, ep, half, full, flag = undo
    b = p.b
    p.white = not p.white
    b[f], b[t] = piece, captured
    if flag == 1:
        b[t + (10 if p.white else -10)] = "p" if p.white else "P"
    elif flag == 2:
        if t > f:
            b[t + 1], b[t - 1] = b[t - 1], "."
        else:
            b[t - 2], b[t + 1] = b[t + 1], "."
    p.castle, p.ep, p.half, p.full = castle, ep, half, full


def legal(p: Pos) -> list:
    out = []
    king = "K" if p.white else "k"
    them = not p.white
    for mv in pseudo(p):
        u = make(p, mv)
        if not attacked(p.b, p.b.index(king), them):
            out.append(mv)
        unmake(p, u)
    return out


def in_check(p: Pos) -> bool:
    return attacked(p.b, p.b.index("K" if p.white else "k"), not p.white)


def perft(fen_or_pos, depth: int) -> int:
    p = from_fen(fen_or_pos) if isinstance(fen_or_pos, str) else fen_or_pos
    if depth == 0:
        return 1
    ms = legal(p)
    if depth == 1:
        return len(ms)
    n = 0
    for mv in ms:
        u = make(p, mv)
        n += perft(p, depth - 1)
        unmake(p, u)
    return n


def as_move(mv) -> dict:
    out = {"from": sq_name(ext(mv[0])), "to": sq_name(ext(mv[1]))}
    if mv[2]:
        out["promo"] = mv[2]
    return out


def key(p: Pos) -> str:
    """The position for repetitions: pieces, side to move, castling rights, and the en passant square only when an en
    passant capture is actually possible."""
    ep = "-"
    if p.ep and any(mv[3] == 1 for mv in legal(p)):
        ep = sq_name(ext(p.ep))
    return " ".join([board_text(p), "w" if p.white else "b", p.castle or "-", ep])


def insufficient(p: Pos) -> bool:
    minors = []
    for m in range(21, 99):
        ch = p.b[m]
        if ch in ".xKk":
            continue
        if ch in "PpRrQq":
            return False
        minors.append((ch, (ext(m) // 8 + ext(m) % 8) % 2))
    if len(minors) <= 1:
        return True
    return all(ch in "Bb" for ch, _ in minors) and len({c for _, c in minors}) == 1


# ---------------------------------------------------------------------------
# The match
# ---------------------------------------------------------------------------

def new(seed: int, players: int = 2, options=None) -> dict:
    white = int(seed) % 2
    p = from_fen(START)
    return {"g": "chess", "n": 2, "white": white, "turn": white, "fen": START, "keys": {key(p): 1},
            "over": False, "winner": None, "reason": None, "ply": 0}


def to_move(st: dict) -> list[int]:
    return [] if st["over"] else [st["turn"]]


def moves(st: dict, seat: int) -> list[dict]:
    if st["over"] or seat != st["turn"]:
        return []
    return [as_move(mv) for mv in legal(from_fen(st["fen"]))]


def apply(st: dict, seat: int, move) -> tuple[dict, dict]:
    need_turn(st, seat)
    if not isinstance(move, dict) or not ({"from", "to"} <= set(move) <= {"from", "to", "promo"}):
        raise Illegal("A move is {from, to} (and promo when a pawn reaches the last row).")
    p = from_fen(st["fen"])
    want = canonical(move)
    found = None
    for mv in legal(p):
        if canonical(as_move(mv)) == want:
            found = mv
            break
    if found is None:
        f = sq_index(move.get("from"))
        promo_missing = f >= 0 and p.b[mb(f)] in "Pp" and "promo" not in move and \
            any(mv[2] and ext(mv[0]) == f and sq_name(ext(mv[1])) == move.get("to") for mv in legal(p))
        raise Illegal("Choose what the pawn becomes." if promo_missing else "That move isn't allowed.")
    make(p, found)
    s = copy(st)
    s["fen"] = to_fen(p)
    s["ply"] += 1
    s["turn"] = 1 - seat
    k = key(p)
    s["keys"][k] = s["keys"].get(k, 0) + 1
    replies = legal(p)
    if not replies:
        s["over"] = True
        if in_check(p):
            s["winner"], s["reason"] = seat, "checkmate"
        else:
            s["winner"], s["reason"] = -1, "stalemate"
    elif p.half >= 100:
        s["over"], s["winner"], s["reason"] = True, -1, "fifty"
    elif s["keys"][k] >= 3:
        s["over"], s["winner"], s["reason"] = True, -1, "repetition"
    elif insufficient(p):
        s["over"], s["winner"], s["reason"] = True, -1, "material"
    return s, dict(move)


def result(st: dict):
    if not st["over"]:
        return None
    w = st["winner"]
    return {"winner": None if w == -1 else w, "places": [w, 1 - w] if w in (0, 1) else [0, 1]}


def material(st: dict, seat: int) -> int:
    white = seat == st["white"]
    board = st["fen"].split()[0]
    return sum(VALUE[ch.lower()] for ch in board if ch.isalpha() and ch.isupper() == white)


def score(st: dict, seat: int) -> int:
    return margin_score(st["winner"] == seat, st["winner"] == -1, BASE, BONUS_POINT * material(st, seat))


def view(st: dict, seat: int) -> dict:
    return digest(st)


def visible(record: dict, seat: int, mover: int) -> dict:
    return record


def digest(st: dict) -> dict:
    return {"fen": st["fen"], "turn": st["turn"], "over": st["over"], "winner": st["winner"], "reason": st["reason"]}


def computer(st: dict, seat: int, rnd) -> dict:
    """Chess is played by two: a player who leaves loses and the match ends, so this is only a fallback (a capture
    if there is one, else any move)."""
    ms = legal(from_fen(st["fen"]))
    caps = [mv for mv in ms if mv[3] == 1 or from_fen(st["fen"]).b[mv[1]] != "."]
    pool = caps or ms
    return as_move(pool[int(rnd.random() * len(pool))])
