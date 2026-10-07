"""The server's rules for turn-by-turn games (SPEC §13.5).

A turn-by-turn match is played from two (or up to four) phones that needn't be online at the same time: every move
is sent to the server, checked here, stored in `match_moves` and the other player is told it is their move. These
are the only games whose rules run on the server; each game's JavaScript rules file (`static/games/<game>-logic.js`)
plays the same rules on the phone for drawing, and the tests check the two agree on thousands of random games.

Each game is a small module with the same functions (states are plain JSON-able dicts, never changed in place):

    PLAYERS = (least, most)            how many players a match has
    OPTIONS = {id: (choices, default)} start-screen choices the match fixes (Dots and Boxes' size, Sea Battle's fleet)
    DICE = 0                           sides of the server's die (Ludo, Snakes and Ladders: 6); 0 = no dice
    new(seed, players, options)        the start; who moves first comes from the seed (seed % players)
    to_move(st)                        the seats (0-based) that may move now; [] once it is over
    moves(st, seat)                    the legal moves of that seat now (Sea Battle's placing can't be listed: [])
    apply(st, seat, move)              → (new state, the record stored in match_moves); raises Illegal
    result(st)                         None while playing, else {"winner": seat | None (a draw), "places": [...]}
    score(st, seat)                    the seat's points at the end (a win, its margin; SPEC §13.5)
    view(st, seat)                     what that seat may see (Sea Battle never shows the other fleet)
    visible(record, seat, mover)       a stored record as `seat` may see it (another's placed fleet is hidden)
    digest(st)                         the public state, compared with the JavaScript rules by the tests
    computer(st, seat, rnd)            a move for a seat the computer plays (a player who left; `rnd` a random.Random)
    forced(st, seat)                   optional (dice games): the move to play at once when the roll leaves no choice
                                       (a pass, or Snakes and Ladders' only move) — the server plays it with the roll

A game with dice reads the server's roll from `st["_roll"]` (put there by together.py for the move, never chosen by
a phone) and raises Illegal without one.
"""
from __future__ import annotations

import json

from . import checkers, chess, dots, fourrow, ludo, reversi, seabattle, snakes, tictactoe
from .base import Illegal, canonical, check_shape, copy, whole  # noqa: F401

REGISTRY: dict = {
    "fourrow": fourrow,
    "tictactoe": tictactoe,
    "checkers": checkers,
    "reversi": reversi,
    "dots": dots,
    "seabattle": seabattle,
    "ludo": ludo,
    "snakes": snakes,
    "chess": chess,
}


def get(game: str):
    return REGISTRY.get(game)


def register(game: str, module) -> None:
    """Add a game's rules (the tests' dummy game for 2–4 players uses this)."""
    REGISTRY[game] = module


def options_for(module, given) -> dict:
    """The match's options: each of the game's choices, the given one when it is allowed, else its default."""
    out = {}
    given = given if isinstance(given, dict) else {}
    for key, (choices, default) in getattr(module, "OPTIONS", {}).items():
        v = given.get(key)
        out[key] = v if v in choices else default
    return out
