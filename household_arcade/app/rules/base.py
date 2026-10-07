"""What every turn-by-turn game's rules share: the refusal, copies, a move's shape."""
from __future__ import annotations

import json

MAX_MOVE_BYTES = 2048


class Illegal(ValueError):
    """A move that isn't allowed now (the API answers 422)."""


def copy(st: dict) -> dict:
    return json.loads(json.dumps(st))


def canonical(move) -> str:
    """One spelling of a move, for comparing lists of moves."""
    return json.dumps(move, sort_keys=True, separators=(",", ":"))


def check_shape(move) -> None:
    if not isinstance(move, dict) or not move:
        raise Illegal("A move is an object.")
    if len(json.dumps(move)) > MAX_MOVE_BYTES:
        raise Illegal("That move is too big.")


def whole(v, lo: int, hi: int) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi


def need_turn(st: dict, seat: int) -> None:
    if st.get("over"):
        raise Illegal("The game is over.")
    if seat != st.get("turn"):
        raise Illegal("It isn't your move.")


def margin_score(won: bool, draw: bool, base: int, bonus: int) -> int:
    """A win: the base and a bonus for how well (at most the base again); a draw a quarter of the base; a loss 0."""
    if won:
        return base + max(0, min(base, bonus))
    if draw:
        return base // 4
    return 0
