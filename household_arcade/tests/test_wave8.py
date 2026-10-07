"""Wave 8 (Ludo, Snakes and Ladders, Carrom, Chess): the game table, the server's rules modules (Ludo's sixes, captures,
safe squares, the exact roll home, three sixes; Snakes and Ladders' boards and finishes; Chess's castling, en passant,
promotion, check, mate, stalemate, repetition, 50 moves, too little material and the perft counts), the JavaScript and
Python rules agreeing move by move on random games (tests/js/wave8-fuzz.js), 2–4 player matches through the API with the
server's dice (a roll the player can't choose or repeat, moves the roll leaves no choice about played at once, starting
with those who joined, a leaver played by the computer) and a chess match from two phones. (Carrom's live turn-taking
room went with the live duels in 1.8.0.)"""
import _env  # noqa: F401  (must be first)

import hashlib
import json
import os
import random
import shutil
import subprocess
import unittest

from app import db, games, level_kinds, rules, together
from app.rules import chess, ludo, snakes
from base import ASHA, DEV, KABIR, MEERA, ApiBase

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NEW = {"ludo": "Ludo", "snakes": "Snakes and Ladders", "carrom": "Carrom", "chess": "Chess"}
WHO = {"u_kabir": KABIR, "u_meera": MEERA, "u_asha": ASHA, "u_dev": DEV}
FUZZ = {"ludo": 400, "snakes": 400, "chess": 160}


def roll(st, seat, value):
    s = rules.copy(st)
    s["_roll"] = {"seat": seat + 1, "value": value}
    return s


def play(mod, st, seat, move, dice=None):
    if dice is not None:
        st = roll(st, seat, dice)
    return mod.apply(st, seat, move)[0]


class TestGameTable(ApiBase):
    def test_entries(self):
        listed = {g["id"]: g for g in self.get("/api/games").json()["games"]}
        self.assertEqual(list(games.GAMES)[38:42], list(NEW))   # the wave 9–10 puzzles come after
        for gid, name in NEW.items():
            with self.subTest(game=gid):
                g = games.GAMES[gid]
                self.assertEqual(g["name"], name)
                self.assertEqual(g["state_version"], 1)
                self.assertTrue(g["unfinished_zero"])
                self.assertEqual(g["level_modes"], [])
                self.assertNotIn(gid, level_kinds.KINDS)
                self.assertFalse(together.race_ok(gid))
                self.assertTrue(listed[gid]["canSave"])
                self.assertEqual((g["max_score"], g["per_second"], g["base"]), (1000, 1000, 1000))
        for gid in ("ludo", "snakes"):
            self.assertEqual([m["id"] for m in games.GAMES[gid]["modes"]], ["cpu", "pass", "phones"])
            self.assertEqual(games.turn_modes(gid), ["phones"])
            self.assertEqual(listed[gid]["turnModes"], ["phones"])
            self.assertEqual(rules.get(gid).PLAYERS, (2, 4))
            self.assertEqual(rules.get(gid).DICE, 6)
        self.assertEqual(games.turn_modes("chess"), ["phones"])
        self.assertEqual(rules.get("chess").PLAYERS, (2, 2))
        self.assertIsNone(rules.get("carrom"))
        self.assertEqual([m["id"] for m in games.GAMES["carrom"]["modes"]], ["easy", "medium", "hard", "two", "doubles"])
        self.assertNotIn("live", games.GAMES["carrom"])
        self.assertIn("gentle", games.GAMES["carrom"]["modes"][0]["label"])
        self.assertIn("gentle", games.GAMES["chess"]["modes"][0]["label"])

    def test_players_and_kinds(self):
        self.get("/api/me", DEV)
        self.assertEqual(self.get("/api/players?game=ludo&mode=phones").json()["maxPlayers"], 4)
        self.assertEqual(self.get("/api/players?game=snakes").json()["kind"], "turns")
        self.assertEqual(self.get("/api/players?game=chess&mode=phones").json()["maxPlayers"], 2)
        self.assertEqual(self.get("/api/players?game=carrom").status_code, 409)         # one screen only

    def test_honest_limits_and_scores(self):
        for gid in NEW:
            self.assertIsNone(games.check_score(gid, games.GAMES[gid]["default_mode"], 1000, 1, 3))
            self.assertIsNotNone(games.check_score(gid, games.GAMES[gid]["default_mode"], 1001, 1, 3))
            self.assertTrue(games.keeps_nothing(gid, 0))
        r = self.play(150, 300, game="ludo", mode="cpu")
        self.assertTrue(r.json()["saved"], r.text)
        r = self.play(0, 300, game="snakes", mode="pass")
        self.assertEqual(r.json()["reason"], "unfinished")
        r = self.play(740, 600, game="carrom", mode="hard")
        self.assertTrue(r.json()["saved"], r.text)
        r = self.play(620, 900, game="chess", mode="hard")
        self.assertTrue(r.json()["saved"], r.text)

    def test_a_two_phone_mode_is_never_played_alone(self):
        for gid in ("ludo", "snakes", "chess"):
            self.assertEqual(self.post("/api/sessions", {"game": gid, "mode": "phones"}).status_code, 422)
        self.assertEqual(self.post("/api/sessions", {"game": "carrom", "mode": "doubles"}).status_code, 201)


# ---------------------------------------------------------------------------
# Ludo
# ---------------------------------------------------------------------------

class TestLudo(unittest.TestCase):
    def test_start_colours_and_first(self):
        self.assertEqual(ludo.new(7, 2)["cols"], [0, 2])
        self.assertEqual(ludo.new(7, 3)["cols"], [0, 1, 2])
        st = ludo.new(7, 4)
        self.assertEqual(st["cols"], [0, 1, 2, 3])
        self.assertEqual(st["turn"], 7 % 4)
        self.assertEqual(st["tokens"], [[-1] * 4] * 4)
        self.assertEqual(ludo.new(9, 3)["turn"], 0)

    def test_dice_are_needed_and_six_to_enter(self):
        st = ludo.new(0, 2)
        self.assertEqual(ludo.moves(st, 0), [])                                   # no roll: nothing to list
        with self.assertRaises(rules.Illegal):
            ludo.apply(st, 0, {"token": 0})
        with self.assertRaises(rules.Illegal):
            ludo.apply(roll(st, 0, 6), 1, {"token": 0})                           # not their move
        with self.assertRaises(rules.Illegal):
            ludo.apply(roll(st, 1, 6), 0, {"token": 0})                           # the roll is someone else's
        self.assertEqual(ludo.moves(roll(st, 0, 5), 0), [{"pass": True}])
        self.assertEqual(ludo.forced(roll(st, 0, 5), 0), {"pass": True})
        self.assertEqual(ludo.forced(roll(st, 0, 6), 0), {"token": 0})            # all four alike: no choice
        st2 = play(ludo, st, 0, {"pass": True}, 5)
        self.assertEqual(st2["turn"], 1)
        st3 = play(ludo, st, 0, {"token": 2}, 6)
        self.assertEqual((st3["tokens"][0], st3["turn"], st3["sixes"]), ([-1, -1, 0, -1], 0, 1))   # a 6 rolls again
        with self.assertRaises(rules.Illegal):
            ludo.apply(roll(st3, 0, 4), 0, {"token": 1})                          # still in the yard: needs a 6
        with self.assertRaises(rules.Illegal):
            ludo.apply(roll(st3, 0, 4), 0, {"pass": True})                        # a token can move: no pass
        for bad in ({"token": 4}, {"token": "1"}, {"token": True}, {"token": 1.0}, {"pass": 1}, {"pass": True, "token": 0}, {}, []):
            with self.assertRaises(rules.Illegal, msg=bad):
                ludo.apply(roll(st3, 0, 4), 0, bad)

    def test_three_sixes_lose_the_turn(self):
        st = ludo.new(0, 2)
        st = play(ludo, st, 0, {"token": 0}, 6)
        st = play(ludo, st, 0, {"token": 0}, 6)
        self.assertEqual(st["sixes"], 2)
        self.assertEqual(ludo.moves(roll(st, 0, 6), 0), [{"pass": True}])
        st = play(ludo, st, 0, {"pass": True}, 6)
        self.assertEqual((st["turn"], st["sixes"], st["tokens"][0][0]), (1, 0, 6))
        # a 6 that can't be used still rolls again
        st2 = ludo.new(0, 2)
        st2["tokens"][0] = [55, 56, 56, 56]
        self.assertEqual(ludo.moves(roll(st2, 0, 6), 0), [{"pass": True}])
        st2 = play(ludo, st2, 0, {"pass": True}, 6)
        self.assertEqual((st2["turn"], st2["sixes"]), (0, 1))

    def test_captures_safe_squares_and_stacks(self):
        st = ludo.new(0, 2)                                       # red (seat 0) and yellow (seat 1, start square 26)
        st["tokens"][0] = [18, -1, -1, -1]
        st["tokens"][1] = [46, -1, -1, -1]                        # yellow's progress 46 is track square (26 + 46) % 52 = 20
        self.assertEqual(ludo.square(st, 1, 46), 20)
        after = play(ludo, st, 0, {"token": 0}, 2)
        self.assertEqual((after["tokens"][0][0], after["tokens"][1][0]), (20, -1))   # captured, back to the yard
        # on a star square (8) nobody is captured
        st["tokens"][0] = [5, -1, -1, -1]
        st["tokens"][1] = [34, -1, -1, -1]                          # (26 + 34) % 52 = 8, a star
        self.assertEqual(ludo.square(st, 1, 34), 8)
        after = play(ludo, st, 0, {"token": 0}, 3)
        self.assertEqual((after["tokens"][0][0], after["tokens"][1][0]), (8, 34))
        # a start square is safe too: red entering onto 0 with yellow there
        st["tokens"][1] = [26, -1, -1, -1]                          # (26 + 26) % 52 = 0, red's start
        after = play(ludo, st, 0, {"token": 1}, 6)
        self.assertEqual((after["tokens"][0][1], after["tokens"][1][0]), (0, 26))
        # all of the other colour's tokens on the square go home together
        st["tokens"][0] = [18, -1, -1, -1]
        st["tokens"][1] = [46, 46, -1, -1]
        after = play(ludo, st, 0, {"token": 0}, 2)
        self.assertEqual(after["tokens"][1][:2], [-1, -1])
        # your own tokens share squares
        st["tokens"][0] = [18, 20, -1, -1]
        st["tokens"][1] = [-1] * 4
        after = play(ludo, st, 0, {"token": 0}, 2)
        self.assertEqual(after["tokens"][0][:2], [20, 20])

    def test_home_needs_the_exact_roll_and_the_win(self):
        st = ludo.new(0, 3)
        st["tokens"][0] = [53, 56, 56, 56]
        self.assertEqual(ludo.moves(roll(st, 0, 4), 0), [{"pass": True}])          # 53 + 4 > 56
        self.assertEqual(ludo.moves(roll(st, 0, 3), 0), [{"token": 0}])
        st["tokens"][1] = [10, 20, -1, 56]
        st["tokens"][2] = [-1, -1, -1, -1]
        end = play(ludo, st, 0, {"token": 0}, 3)
        self.assertTrue(end["over"])
        self.assertEqual(ludo.result(end), {"winner": 0, "places": [0, 1, 2]})
        self.assertEqual(ludo.to_move(end), [])
        self.assertEqual(ludo.score(end, 0), 500 + min(500, 25 * 7))
        self.assertEqual(ludo.score(end, 1), 0)
        # the home column is past the track: no captures there
        st = ludo.new(0, 2)
        st["tokens"][0] = [49, -1, -1, -1]
        after = play(ludo, st, 0, {"token": 0}, 4)
        self.assertEqual(after["tokens"][0][0], 53)

    def test_the_computer_stand_in(self):
        rnd = random.Random(3)
        st = ludo.new(0, 2)
        st["tokens"][0] = [18, 30, -1, -1]
        st["tokens"][1] = [46, -1, -1, -1]                           # on square 20: red's token on 18 can take it with a 2
        self.assertEqual(ludo.computer(roll(st, 0, 2), 0, rnd), {"token": 0})
        st["tokens"][0] = [53, 30, -1, -1]
        st["tokens"][1] = [-1] * 4
        self.assertEqual(ludo.computer(roll(st, 0, 3), 0, rnd), {"token": 0})        # home first
        for seed in range(1, 12):
            rnd, st = random.Random(seed), ludo.new(seed, 2 + seed % 3)
            guard = 0
            while not st["over"] and guard < 4000:
                seat = st["turn"]
                r = roll(st, seat, rnd.randint(1, 6))
                st = ludo.apply(r, seat, ludo.computer(r, seat, rnd))[0]
                guard += 1
            self.assertTrue(st["over"], seed)


class TestSnakesAndLadders(unittest.TestCase):
    def test_boards(self):
        for name, b in snakes.BOARDS.items():
            starts = set(b["ladders"]) | set(b["snakes"])
            ends = set(b["ladders"].values()) | set(b["snakes"].values())
            self.assertFalse(starts & ends, name)                       # never a ladder onto a snake or the other way
            self.assertTrue(all(a < z for a, z in b["ladders"].items()))
            self.assertTrue(all(a > z for a, z in b["snakes"].items()))
            self.assertFalse({1, 100} & (starts | ends))
        self.assertEqual((len(snakes.BOARDS["classic"]["ladders"]), len(snakes.BOARDS["classic"]["snakes"])), (9, 10))
        self.assertEqual((len(snakes.BOARDS["gentle"]["ladders"]), len(snakes.BOARDS["gentle"]["snakes"])), (7, 6))
        self.assertTrue(max(a - z for a, z in snakes.BOARDS["gentle"]["snakes"].items()) <= 12)

    def test_moves_ladders_snakes_and_finishing(self):
        st = snakes.new(4, 3, {"board": "classic"})
        self.assertEqual((st["turn"], st["pos"], st["finish"]), (1, [0, 0, 0], "any"))
        self.assertEqual(snakes.moves(st, 1), [])
        self.assertEqual(snakes.forced(roll(st, 1, 3), 1), {"go": True})
        st = play(snakes, st, 1, {"go": True}, 3)                    # 3 → a ladder to 18
        self.assertEqual((st["pos"][1], st["turn"]), (18, 2))
        st["pos"][2] = 23
        st = play(snakes, st, 2, {"go": True}, 3)                    # 26 → a snake to 17
        self.assertEqual(st["pos"][2], 17)
        for bad in ({"go": 1}, {"go": False}, {"go": True, "x": 1}, {}):
            with self.assertRaises(rules.Illegal):
                snakes.apply(roll(st, 0, 2), 0, bad)
        st["pos"][0] = 97
        end = play(snakes, st, 0, {"go": True}, 6)                   # "any": past 100 wins
        self.assertTrue(end["over"])
        self.assertEqual(snakes.result(end)["places"][0], 0)
        self.assertEqual(snakes.score(end, 0), 500 + min(500, 5 * (100 - 18)))
        ex = snakes.new(0, 2, {"finish": "exact", "board": "gentle"})
        ex["pos"][0] = 98
        stay = play(snakes, ex, 0, {"go": True}, 5)
        self.assertEqual((stay["pos"][0], stay["over"], stay["turn"]), (98, False, 1))
        ex["pos"][0] = 95
        self.assertTrue(play(snakes, ex, 0, {"go": True}, 5)["over"])
        self.assertEqual(snakes.new(0, 2, {"board": "x", "finish": "y"})["board"], "classic")
        self.assertEqual(rules.options_for(snakes, {"board": "gentle", "finish": "exact", "x": 1}),
                         {"board": "gentle", "finish": "exact"})


# ---------------------------------------------------------------------------
# Chess
# ---------------------------------------------------------------------------

KIWIPETE = "r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1"
POS3 = "8/2p5/3p4/KP5r/1R3p1k/8/4P1P1/8 w - - 0 1"
POS4 = "r3k2r/Pppp1ppp/1b3nbN/nP6/BBP1P3/q4N2/Pp1P2PP/R2Q1RK1 w kq - 0 1"
POS5 = "rnbq1k1r/pp1Pbppp/2p5/8/2B5/8/PPP1NnPP/RNBQK2R w KQ - 1 8"
PERFT = [(chess.START, [20, 400, 8902, 197281]), (KIWIPETE, [48, 2039, 97862]), (POS3, [14, 191, 2812, 43238]),
         (POS4, [6, 264, 9467]), (POS5, [44, 1486, 62379])]


def at(fen, seat=0, **extra):
    """A chess state from a FEN, White played by `seat`."""
    p = chess.from_fen(fen)
    turn = seat if p.white else 1 - seat
    st = {"g": "chess", "n": 2, "white": seat, "turn": turn, "fen": fen, "keys": {chess.key(p): 1}, "over": False,
          "winner": None, "reason": None, "ply": 0}
    st.update(extra)
    return st


def mv(a, b, promo=None):
    m = {"from": a, "to": b}
    if promo:
        m["promo"] = promo
    return m


class TestChess(unittest.TestCase):
    def test_perft(self):
        for fen, counts in PERFT:
            for depth, want in enumerate(counts, start=1):
                self.assertEqual(chess.perft(fen, depth), want, f"{fen} depth {depth}")

    def test_start_and_who_is_white(self):
        st = chess.new(7, 2)
        self.assertEqual((st["white"], st["turn"]), (1, 1))
        self.assertEqual(chess.new(8)["white"], 0)
        self.assertEqual(len(chess.moves(st, 1)), 20)
        self.assertEqual(chess.moves(st, 0), [])

    def test_castling(self):
        st = at("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1")
        ms = [chess.canonical(m) for m in chess.moves(st, 0)]
        self.assertIn(chess.canonical(mv("e1", "g1")), ms)
        self.assertIn(chess.canonical(mv("e1", "c1")), ms)
        after = play(chess, st, 0, mv("e1", "g1"))
        self.assertTrue(after["fen"].startswith("r3k2r/8/8/8/8/8/8/R4RK1 b kq -"))
        # through an attacked square, out of check, or after the rook moved: not allowed
        for fen, bad in (("r3k2r/8/8/8/8/8/5r2/R3K2R w KQ - 0 1", mv("e1", "g1")),      # f1 attacked... by the f2 rook
                         ("r3k2r/8/8/8/8/8/4r3/R3K2R w KQ - 0 1", mv("e1", "c1")),      # in check
                         ("r3k2r/8/8/8/8/8/8/R3K2R w Kq - 0 1", mv("e1", "c1"))):       # no right any more
            with self.assertRaises(rules.Illegal, msg=fen):
                chess.apply(at(fen), 0, bad)
        moved = play(chess, st, 0, mv("h1", "h2"))
        self.assertIn(" b Qkq ", moved["fen"])
        taken = play(chess, at("r3k2r/8/8/8/8/8/8/R3K2R b KQkq - 0 1", seat=1), 0, mv("a8", "a1"))
        self.assertIn(" w Kk ", taken["fen"])                         # the captured rook takes its right away too

    def test_en_passant_only_at_once(self):
        st = at("4k3/3p4/8/4P3/8/8/8/4K3 b - - 0 1", seat=1)
        st = play(chess, st, 0, mv("d7", "d5"))
        self.assertIn(" w - d6 ", st["fen"])
        self.assertIn(chess.canonical(mv("e5", "d6")), [chess.canonical(m) for m in chess.moves(st, 1)])
        took = play(chess, st, 1, mv("e5", "d6"))
        self.assertTrue(took["fen"].startswith("4k3/8/3P4/8/8/8/8/4K3 b"))
        later = play(chess, play(chess, st, 1, mv("e1", "e2")), 0, mv("e8", "e7"))
        with self.assertRaises(rules.Illegal):
            chess.apply(later, 1, mv("e5", "d6"))

    def test_promotion_needs_a_choice(self):
        st = at("8/4P3/8/8/8/8/k7/4K3 w - - 0 1")
        with self.assertRaises(rules.Illegal) as e:
            chess.apply(st, 0, mv("e7", "e8"))
        self.assertIn("becomes", str(e.exception))
        for bad in ("k", "p", "x", 1):
            with self.assertRaises(rules.Illegal):
                chess.apply(st, 0, mv("e7", "e8", bad))
        self.assertEqual(sorted(m.get("promo") for m in chess.moves(st, 0) if m["from"] == "e7"), ["b", "n", "q", "r"])
        knight = play(chess, st, 0, mv("e7", "e8", "n"))
        self.assertTrue(knight["fen"].startswith("4N3/"))
        with self.assertRaises(rules.Illegal):
            chess.apply(at("8/8/8/8/8/8/k3P3/4K3 w - - 0 1"), 0, mv("e2", "e3", "q"))       # no promotion there

    def test_checkmate_stalemate_and_scores(self):
        st = chess.new(0)
        for a, b in (("f2", "f3"), ("e7", "e5"), ("g2", "g4"), ("d8", "h4")):
            st = play(chess, st, st["turn"], mv(a, b))
        self.assertEqual((st["over"], st["winner"], st["reason"]), (True, 1, "checkmate"))
        self.assertEqual(chess.result(st), {"winner": 1, "places": [1, 0]})
        self.assertEqual(chess.score(st, 1), 500 + min(500, 10 * 39))
        self.assertEqual(chess.score(st, 0), 0)
        with self.assertRaises(rules.Illegal):
            chess.apply(st, 0, mv("e2", "e3"))
        stale = play(chess, at("7k/8/6Q1/8/8/8/8/K7 w - - 0 1"), 0, mv("g6", "f7"))
        self.assertEqual((stale["over"], stale["winner"], stale["reason"]), (True, -1, "stalemate"))
        self.assertIsNone(chess.result(stale)["winner"])
        self.assertEqual(chess.score(stale, 0), 125)

    def test_a_king_never_left_in_check(self):
        st = at("4k3/8/8/8/8/8/4r3/4K2R w K - 0 1")
        legal = [chess.canonical(m) for m in chess.moves(st, 0)]
        self.assertNotIn(chess.canonical(mv("e1", "g1")), legal)
        self.assertNotIn(chess.canonical(mv("h1", "h8")), legal)          # the rook can't ignore the check
        self.assertIn(chess.canonical(mv("e1", "e2")), legal)              # the king takes

    def test_draws_by_repetition_fifty_moves_and_material(self):
        st = chess.new(0)
        for _ in range(2):
            for a, b in (("g1", "f3"), ("g8", "f6"), ("f3", "g1"), ("f6", "g8")):
                st = play(chess, st, st["turn"], mv(a, b))
        self.assertEqual((st["over"], st["reason"]), (True, "repetition"))
        fifty = at("4k3/8/8/8/8/8/8/R3K3 w - - 99 80")
        st = play(chess, fifty, 0, mv("a1", "a2"))
        self.assertEqual((st["over"], st["reason"]), (True, "fifty"))
        mate100 = play(chess, at("k7/8/1K6/8/8/8/8/7R w - - 99 80"), 0, mv("h1", "h8"))
        self.assertEqual(mate100["reason"], "checkmate")                     # mate on the 100th ply still counts
        bare = play(chess, at("4k3/8/8/8/8/8/3n4/4K3 w - - 0 1"), 0, mv("e1", "d2"))
        self.assertEqual((bare["over"], bare["reason"]), (True, "material"))
        for fen, want in (("4k3/8/8/8/8/8/8/4KB2 w - - 0 1", True), ("4k3/8/8/8/8/8/8/4KN2 w - - 0 1", True),
                          ("4kb2/8/8/8/8/8/8/2B1K3 w - - 0 1", True), ("4k1b1/8/8/8/8/8/8/2B1K3 w - - 0 1", False),
                          ("4k3/8/8/8/8/8/8/3NKN2 w - - 0 1", False), ("4k3/8/8/8/8/8/P7/4K3 w - - 0 1", False)):
            self.assertEqual(chess.insufficient(chess.from_fen(fen)), want, fen)

    def test_shapes(self):
        st = chess.new(0)
        for bad in ({"from": "e2"}, {"from": "e2", "to": "e4", "x": 1}, {"from": "e9", "to": "e4"}, {"from": 52, "to": 36},
                    [], {"from": "e2", "to": "e5"}):
            with self.assertRaises(rules.Illegal, msg=bad):
                chess.apply(st, 0, bad)


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TestJsAndPythonAgree(unittest.TestCase):
    """The JavaScript rules (drawing, the computer, one phone) and the server's (checking every move of a match) are the
    same rules: random games — with the dice as the server would roll them — give the same legal moves, the same move
    forced by each roll, the same state after every move, and the same answer to probes that may or may not be legal."""

    def check(self, game):
        out = subprocess.run(["node", "tests/js/wave8-fuzz.js", game, str(FUZZ[game])], cwd=HERE, capture_output=True,
                             text=True, check=True, timeout=600).stdout
        mod = rules.get(game)
        n_games = plies = probes = 0
        endings = set()
        for line in out.splitlines():
            g = json.loads(line)
            n_games += 1
            st = mod.new(g["seed"], g["players"], g["options"])
            for i, step in enumerate(g["steps"]):
                where = f"{game} seed {g['seed']} step {i}"
                if step.get("probe"):
                    probes += 1
                    t = roll(st, step["seat"], step["dice"]) if step["dice"] else st
                    try:
                        mod.apply(t, step["seat"], step["move"])
                        ok = True
                    except rules.Illegal:
                        ok = False
                    self.assertEqual(ok, step["ok"], f"{where}: {step['move']}")
                    continue
                if step["dice"]:
                    st = roll(st, step["seat"], step["dice"])
                legal = sorted(rules.canonical(m) for m in mod.moves(st, step["seat"]))
                self.assertEqual(hashlib.md5("\n".join(legal).encode()).hexdigest(), step["legal"], f"{where}: legal moves")
                if hasattr(mod, "forced"):
                    f = mod.forced(st, step["seat"])
                    self.assertEqual(rules.canonical(f) if f else None, step["forced"], where)
                st, _ = mod.apply(st, step["seat"], step["move"])
                plies += 1
                self.assertEqual(mod.digest(st), step["digest"], where)
            self.assertEqual((st["over"], st["winner"]), (g["over"], g["winner"]))
            endings.add(g["reason"] if game == "chess" else g["winner"])
        self.assertEqual(n_games, FUZZ[game])
        self.assertGreater(probes, FUZZ[game])
        return endings

    def test_ludo(self):
        self.assertTrue({0, 1, 2, 3} <= self.check("ludo"))

    def test_snakes(self):
        self.assertTrue({0, 1, 2, 3} <= self.check("snakes"))

    def test_chess(self):
        self.assertTrue({"checkmate", "stalemate", "material", "fifty"} <= self.check("chess"))


# ---------------------------------------------------------------------------
# Matches through the API
# ---------------------------------------------------------------------------

class Matches(ApiBase):
    def setUp(self):
        super().setUp()
        self.get("/api/me", DEV)

    def pic(self, mid, who=KABIR):
        r = self.get(f"/api/matches/{mid}/turns", who)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def seats(self, mid):
        return {p["seat"]: WHO[p["id"]] for p in self.pic(mid)["players"]}

    def mover(self, mid):
        p = self.pic(mid)
        return self.seats(mid)[p["toMove"][0]], p


class TestDiceMatches(Matches):
    def test_ludo_three_players_start_with_those_who_joined_dice_and_a_leaver(self):
        r = self.post("/api/matches", {"game": "ludo", "mode": "phones", "opponents": ["u_meera", "u_asha", "u_dev"]}, KABIR)
        self.assertEqual(r.status_code, 201, r.text)
        mid = r.json()["id"]
        self.assertEqual(r.json()["turns"]["maxPlayers"], 4)
        self.post(f"/api/matches/{mid}/accept", None, MEERA)
        self.post(f"/api/matches/{mid}/accept", None, ASHA)
        self.assertEqual(self.post(f"/api/matches/{mid}/start", None, KABIR).status_code, 200)      # Dev never answered
        p = self.pic(mid)
        self.assertEqual([(x["seat"], x["id"]) for x in p["players"]], [(1, "u_kabir"), (2, "u_meera"), (3, "u_asha")])
        self.assertEqual(p["view"]["tokens"], [[-1] * 4] * 3)
        self.assertEqual(p["dice"], 6)
        # rolls are the server's: the same roll again until the move, never one a phone sends
        who, p = self.mover(mid)
        other = next(w for w in (KABIR, MEERA, ASHA) if w is not who)
        self.assertEqual(self.post(f"/api/matches/{mid}/roll", None, other).status_code, 409)
        self.assertEqual(self.post(f"/api/matches/{mid}/move", {"move": {"token": 0}}, who).status_code, 409)
        rolls, guard, left = [], 0, False
        while guard < 2000:
            guard += 1
            p = self.pic(mid)
            if p["over"]:
                break
            who = self.seats(mid)[p["toMove"][0]]
            r = self.post(f"/api/matches/{mid}/roll", None, who)
            self.assertEqual(r.status_code, 200, r.text)
            out = r.json()
            rolls.append(out["roll"])
            if not out["moved"]:
                again = self.post(f"/api/matches/{mid}/roll", None, who).json()
                self.assertEqual(again["roll"], out["roll"])
                self.assertFalse(again["moved"])
                legal = ludo.moves(ludo_state(mid), p["toMove"][0] - 1)          # the server's state holds the roll
                self.assertGreater(len(legal), 1)
                # a phone can't bring a roll of its own along with the move
                bad = self.post(f"/api/matches/{mid}/move", {"move": dict(legal[-1], roll=6)}, who)
                self.assertEqual(bad.status_code, 422, bad.text)
                r = self.post(f"/api/matches/{mid}/move", {"move": legal[-1]}, who)
                self.assertEqual(r.status_code, 200, r.text)
            else:
                self.assertEqual(out["picture"]["number"], self.pic(mid)["number"])
            if not left and len(rolls) > 30:              # Asha leaves: the computer plays her seat from now on
                self.assertEqual(self.post(f"/api/matches/{mid}/resign", None, ASHA).status_code, 200)
                left = True
        p = self.pic(mid)
        self.assertTrue(p["over"], "the match finished")
        self.assertTrue(set(rolls) <= {1, 2, 3, 4, 5, 6})
        self.assertGreater(len(set(rolls)), 3)
        with db.get_conn() as conn:
            rows = conn.execute("SELECT seat, dice FROM match_moves WHERE match_id = ?", (mid,)).fetchall()
        self.assertTrue(all(1 <= r["dice"] <= 6 for r in rows))
        asha = next(x for x in p["players"] if x["id"] == "u_asha")
        self.assertTrue(asha["computer"] and asha["left"])
        self.assertTrue(any(r["seat"] == asha["seat"] for r in rows[35:]), "the computer moved for Asha")
        m = self.get(f"/api/matches/{mid}").json()
        self.assertIn(m["winner"], ("u_kabir", "u_meera", "u_asha"))
        winner = next(x for x in m["players"] if x["id"] == m["winner"]) if m["winner"] != "u_asha" else None
        if winner:
            self.assertGreaterEqual(winner["score"], 500)

    def test_snakes_four_players_every_roll_moves(self):
        mid = self.post("/api/matches", {"game": "snakes", "mode": "phones", "opponents": ["u_meera", "u_asha", "u_dev"],
                                         "options": {"board": "gentle", "finish": "exact"}}, KABIR).json()["id"]
        for who in (MEERA, ASHA, DEV):
            self.post(f"/api/matches/{mid}/accept", None, who)
        p = self.pic(mid)
        self.assertEqual(p["options"], {"board": "gentle", "finish": "exact"})
        self.assertEqual(len(p["players"]), 4)
        n, guard = 0, 0
        while not p["over"] and guard < 2000:
            guard += 1
            who = self.seats(mid)[p["toMove"][0]]
            out = self.post(f"/api/matches/{mid}/roll", None, who).json()
            self.assertTrue(out["moved"], "the only move is played with the roll")
            n += 1
            p = out["picture"]
            self.assertEqual(p["number"], n)
            self.assertEqual(p["moves"][-1]["dice"], out["roll"])
        self.assertTrue(p["over"])
        self.assertEqual(p["view"]["pos"][p["result"]["winner"] - 1], 100)


def ludo_state(mid):
    with db.get_conn() as conn:
        return json.loads(conn.execute("SELECT state FROM matches WHERE id = ?", (mid,)).fetchone()["state"])


class TestChessMatch(Matches):
    def test_two_phones(self):
        mid = self.post("/api/matches", {"game": "chess", "mode": "phones", "opponents": ["u_meera"]}, KABIR).json()["id"]
        self.post(f"/api/matches/{mid}/accept", None, MEERA)
        white, p = self.mover(mid)
        black = MEERA if white is KABIR else KABIR
        self.assertEqual(p["view"]["fen"], chess.START)
        self.assertEqual(self.post(f"/api/matches/{mid}/roll", None, white).status_code, 409)          # no dice
        self.assertEqual(self.post(f"/api/matches/{mid}/move", {"move": mv("e2", "e5")}, white).status_code, 422)
        self.assertEqual(self.post(f"/api/matches/{mid}/move", {"move": mv("e7", "e5")}, black).status_code, 409)
        for who, m in ((white, mv("f2", "f3")), (black, mv("e7", "e5")), (white, mv("g2", "g4"))):
            r = self.post(f"/api/matches/{mid}/move", {"move": m}, who)
            self.assertEqual(r.status_code, 200, r.text)
        r = self.post(f"/api/matches/{mid}/move", {"move": mv("d8", "h4")}, black)
        self.assertTrue(r.json()["over"])
        m = self.get(f"/api/matches/{mid}").json()
        self.assertEqual(m["winner"], black["X-Remote-User-Id"])
        theirs = next(x for x in m["players"] if x["id"] == black["X-Remote-User-Id"])
        self.assertEqual(theirs["score"], 500 + min(500, 390))

    def test_resigning_ends_it(self):
        mid = self.post("/api/matches", {"game": "chess", "mode": "phones", "opponents": ["u_meera"]}, KABIR).json()["id"]
        self.post(f"/api/matches/{mid}/accept", None, MEERA)
        m = self.post(f"/api/matches/{mid}/resign", None, KABIR).json()
        self.assertEqual((m["status"], m["winner"]), ("done", "u_meera"))


if __name__ == "__main__":
    unittest.main()
