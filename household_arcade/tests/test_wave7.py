"""Wave 7 (Four in a Row, Tic-tac-toe, Checkers, Reversi, Dots and Boxes, Sea Battle): the game table entries, the
server's rules modules (legal moves, captures and multi-jumps, the end, draws, Reversi's passes, Dots and Boxes' extra
turns, Sea Battle's hidden fleets), the honest-score limits worked out from the rules, and the JavaScript and Python
rules agreeing move by move on thousands of random games (tests/js/turns-fuzz.js)."""
import _env  # noqa: F401  (must be first)

import hashlib
import json
import os
import random
import shutil
import subprocess
import unittest

from app import games, level_kinds, rules, together
from app.rules import checkers, dots, fourrow, reversi, seabattle, tictactoe
from base import ApiBase

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NEW = {"fourrow": "Four in a Row", "tictactoe": "Tic-tac-toe", "checkers": "Checkers", "reversi": "Reversi",
       "dots": "Dots and Boxes", "seabattle": "Sea Battle"}
MODES = ["easy", "medium", "hard", "two", "phones"]
FUZZ_GAMES = 400          # random games per game (6 × 400 = 2,400) compared move by move


def play(mod, st, seat, move):
    st2, _ = mod.apply(st, seat, move)
    return st2


class TestGameTable(ApiBase):
    def test_entries(self):
        listed = {g["id"]: g for g in self.get("/api/games").json()["games"]}
        ids = list(games.GAMES)
        self.assertEqual(ids[ids.index("fourrow"):ids.index("fourrow") + 6], list(NEW))      # (wave 8 comes after)
        for gid, name in NEW.items():
            with self.subTest(game=gid):
                g = games.GAMES[gid]
                self.assertEqual(g["name"], name)
                self.assertEqual([m["id"] for m in g["modes"]], MODES)
                self.assertIn("gentle", g["modes"][0]["label"])
                self.assertEqual(g["default_mode"], "easy")
                self.assertEqual(g["state_version"], 1)
                self.assertTrue(g["unfinished_zero"])
                self.assertEqual(g["level_modes"], [])
                self.assertNotIn(gid, level_kinds.KINDS)
                self.assertFalse(together.race_ok(gid))
                self.assertTrue(together.turns_ok(gid))
                self.assertEqual(games.turn_modes(gid), ["phones"])
                self.assertEqual(rules.get(gid).__name__.split(".")[-1], gid)
                self.assertEqual(listed[gid]["turnModes"], ["phones"])
                self.assertFalse(listed[gid]["race"])
                self.assertTrue(listed[gid]["canSave"])

    def test_honest_limits(self):
        """The most a game can score is a win's base (500) and as much again; a win can come at once (base = most)."""
        for gid in NEW:
            g = games.GAMES[gid]
            mod = rules.get(gid)
            self.assertEqual(mod.BASE, 500)
            self.assertEqual(g["max_score"], 2 * mod.BASE)
            self.assertEqual(g["base"], g["max_score"])
            self.assertIsNone(games.check_score(gid, "hard", 1000, 1, 3))
            self.assertIsNotNone(games.check_score(gid, "hard", 1001, 1, 3))
            self.assertTrue(games.keeps_nothing(gid, 0))

    def test_a_turn_mode_is_never_played_alone(self):
        r = self.post("/api/sessions", {"game": "dots", "mode": "phones"})
        self.assertEqual(r.status_code, 422)
        self.assertIn("Play with someone", r.json()["detail"])
        self.assertEqual(self.post("/api/sessions", {"game": "dots", "mode": "hard"}).status_code, 201)

    def test_scores_against_the_computer(self):
        for gid in NEW:
            r = self.play(700, 30, game=gid, mode="hard")
            self.assertEqual(r.status_code, 200, r.text)
            self.assertTrue(r.json()["saved"])
            r = self.play(0, 30, game=gid, mode="easy")          # a loss keeps nothing
            self.assertEqual(r.json()["reason"], "unfinished")


class TestFourInARow(unittest.TestCase):
    def test_drop_and_win_every_way(self):
        st = fourrow.new(2)                                    # seed 2: seat 0 first
        self.assertEqual(fourrow.to_move(st), [0])
        for col in (0, 6, 1, 6, 2, 6):
            st = play(fourrow, st, st["turn"], {"col": col})
        self.assertEqual(st["board"][35:42], [1, 1, 1, 0, 0, 0, 2])
        st = play(fourrow, st, 0, {"col": 3})
        self.assertEqual((st["over"], st["winner"]), (True, 0))
        self.assertEqual(st["line"], [35, 36, 37, 38])
        self.assertEqual(fourrow.result(st)["winner"], 0)
        self.assertEqual(fourrow.to_move(st), [])
        # up and down, and slanting
        b = [0] * 42
        for r, c in ((5, 0), (4, 1), (3, 2), (2, 3)):
            b[r * 7 + c] = 2
        self.assertEqual(fourrow.line_through(b, 2 * 7 + 3), [17, 23, 29, 35])

    def test_refusals(self):
        st = fourrow.new(2)
        for bad in ({"col": 7}, {"col": -1}, {"col": "3"}, {"col": True}, {"col": 1.5}, {"column": 3}, {"col": 3, "x": 1}):
            with self.assertRaises(rules.Illegal):
                fourrow.apply(st, 0, bad)
        with self.assertRaises(rules.Illegal):
            fourrow.apply(st, 1, {"col": 3})                      # not their move
        for _ in range(6):
            st = play(fourrow, st, st["turn"], {"col": 0})
        self.assertNotIn({"col": 0}, fourrow.moves(st, st["turn"]))
        with self.assertRaises(rules.Illegal):
            fourrow.apply(st, st["turn"], {"col": 0})              # full

    def test_draw_and_score(self):
        st = fourrow.new(2)
        order = [0, 1, 0, 1, 0, 1, 2, 3, 2, 3, 2, 3, 1, 0, 1, 0, 1, 0, 3, 2, 3, 2, 3, 2, 4, 5, 4, 5, 4, 5, 6, 4, 6, 5, 6, 5, 6, 4, 6, 5, 4, 6]
        for col in order:
            if st["over"]:
                break
            st = play(fourrow, st, st["turn"], {"col": col})
        if st["winner"] == -1:
            self.assertEqual(fourrow.score(st, 0), 125)
        st = fourrow.new(2)
        for col in (0, 1, 0, 1, 0, 1, 0):
            st = play(fourrow, st, st["turn"], {"col": col})
        self.assertEqual(fourrow.score(st, 0), 500 + 10 * 35)
        self.assertEqual(fourrow.score(st, 1), 0)

    def test_who_moves_first_comes_from_the_seed(self):
        self.assertEqual(fourrow.new(7)["turn"], 1)
        self.assertEqual(fourrow.new(8)["turn"], 0)


class TestTicTacToe(unittest.TestCase):
    def test_lines_draw_and_refusals(self):
        st = tictactoe.new(4)
        for cell in (0, 3, 1, 4, 2):
            st = play(tictactoe, st, st["turn"], {"cell": cell})
        self.assertEqual((st["winner"], st["line"]), (0, [0, 1, 2]))
        self.assertEqual(tictactoe.score(st, 0), 500 + 25 * 4)
        st = tictactoe.new(4)
        for cell in (0, 1, 2, 4, 3, 5, 7, 6, 8):
            st = play(tictactoe, st, st["turn"], {"cell": cell})
        self.assertEqual((st["over"], st["winner"]), (True, -1))
        self.assertEqual(tictactoe.result(st)["winner"], None)
        self.assertEqual(tictactoe.score(st, 1), 125)
        st = tictactoe.new(4)
        st = play(tictactoe, st, 0, {"cell": 4})
        for bad in ({"cell": 4}, {"cell": 9}, {"cell": None}, {}):
            with self.assertRaises(rules.Illegal):
                tictactoe.apply(st, 1, bad)


def board_with(pieces: dict) -> list:
    b = [0] * 64
    for sq, v in pieces.items():
        b[sq] = v
    return b


class TestCheckers(unittest.TestCase):
    def st(self, pieces, turn=0, first=0):
        s = checkers.new(first)            # seed even → first 0
        s["first"], s["turn"], s["board"] = first, turn, board_with(pieces)
        return s

    def test_start(self):
        st = checkers.new(2)
        self.assertEqual(checkers.pieces(st, 0), 12)
        self.assertEqual(checkers.pieces(st, 1), 12)
        self.assertTrue(all((sq // 8 + sq % 8) % 2 == 1 for sq in range(64) if st["board"][sq]))
        self.assertEqual(sorted(m["path"][0] for m in checkers.moves(st, 0)), [40, 42, 42, 44, 44, 46, 46])
        self.assertEqual(len(checkers.moves(st, 0)), 7)

    def test_captures_are_compulsory(self):
        # a dark man on 42 (row 5) can jump the light man on 33 to 24; the steps elsewhere aren't allowed
        st = self.st({42: 1, 33: 2, 46: 1, 10: 2})
        self.assertEqual(checkers.moves(st, 0), [{"path": [42, 24]}])
        with self.assertRaises(rules.Illegal):
            checkers.apply(st, 0, {"path": [46, 37]})
        st2 = play(checkers, st, 0, {"path": [42, 24]})
        self.assertEqual(st2["board"][33], 0)
        self.assertEqual(st2["board"][24], 1)
        self.assertEqual(st2["quiet"], 0)

    def test_multi_jumps_must_carry_on(self):
        # dark man on 56 jumps 49 → 42, then 35 → 28: one move of two jumps; stopping half-way is refused
        st = self.st({56: 1, 49: 2, 35: 2, 7: 2})
        self.assertEqual(checkers.moves(st, 0), [{"path": [56, 42, 28]}])
        with self.assertRaises(rules.Illegal):
            checkers.apply(st, 0, {"path": [56, 42]})
        st2 = play(checkers, st, 0, {"path": [56, 42, 28]})
        self.assertEqual((st2["board"][49], st2["board"][35], st2["board"][28]), (0, 0, 1))

    def test_a_choice_of_captures_and_branching_jumps(self):
        st = self.st({42: 1, 33: 2, 35: 2, 0: 2})
        paths = sorted(m["path"] for m in checkers.moves(st, 0))
        self.assertEqual(paths, [[42, 24], [42, 28]])

    def test_crowning_ends_the_move_and_kings_move_both_ways(self):
        st = self.st({17: 1, 10: 2, 26: 2, 60: 2})
        # 17 jumps 10 → 3 (the far row): crowned, the move ends even though 3 could jump on as a king
        paths = [m["path"] for m in checkers.moves(st, 0)]
        self.assertIn([17, 3], paths)
        st2 = play(checkers, st, 0, {"path": [17, 3]})
        self.assertEqual(st2["board"][3], 3)                     # a king of seat 0
        king = self.st({35: 3, 63: 2})
        self.assertEqual(sorted(m["path"][1] for m in checkers.moves(king, 0)), [26, 28, 42, 44])

    def test_no_move_loses_and_quiet_draw(self):
        st = self.st({42: 1, 33: 2})
        st2 = play(checkers, st, 0, {"path": [42, 24]})
        self.assertEqual((st2["over"], st2["winner"]), (True, 0))  # no light pieces left
        kings = self.st({0 + 1: 3, 62: 4}, turn=0)
        kings["quiet"] = checkers.DRAW_PLIES - 1
        st3 = play(checkers, kings, 0, checkers.moves(kings, 0)[0])
        self.assertEqual((st3["over"], st3["winner"]), (True, -1))

    def test_refusals(self):
        st = checkers.new(2)
        for bad in ({"path": [40]}, {"path": [40, 33, 26]}, {"path": "40,33"}, {"path": [40, 32]}, {"from": 40},
                    {"path": [17, 26]}, {"path": [99, 33]}):
            with self.assertRaises(rules.Illegal):
                checkers.apply(st, 0, bad)
        with self.assertRaises(rules.Illegal):
            checkers.apply(st, 1, {"path": [17, 26]})              # not their move


class TestReversi(unittest.TestCase):
    def test_start_flips_and_refusals(self):
        st = reversi.new(2)
        self.assertEqual(sorted(m["cell"] for m in reversi.moves(st, 0)), [19, 26, 37, 44])
        st2 = play(reversi, st, 0, {"cell": 19})
        self.assertEqual((reversi.discs(st2, 0), reversi.discs(st2, 1)), (4, 1))
        self.assertEqual(st2["turn"], 1)
        for bad in ({"cell": 0}, {"cell": 27}, {"cell": 64}, {"cell": 19.0}):
            with self.assertRaises(rules.Illegal):
                reversi.apply(st, 0, bad)

    def test_a_player_with_no_place_passes_and_the_end(self):
        b = [0] * 64
        b[0], b[1], b[3] = 1, 2, 2         # seat 0 at 0 traps 1 by playing 2; then seat 1 has nowhere: passes
        b[63] = 2
        st = reversi.new(2)
        st["board"], st["turn"] = b, 0
        st2 = play(reversi, st, 0, {"cell": 2})
        self.assertEqual(st2["board"][:4], [1, 1, 1, 2])
        self.assertEqual(reversi._cells(st2["board"], 1), [])
        self.assertEqual(st2["passed"], 1)
        self.assertEqual(st2["turn"], 0)                          # seat 1 passed: seat 0 again
        self.assertFalse(st2["over"])

    def test_game_over_when_neither_can_place(self):
        b = [1] * 64
        b[0] = 0
        b[1] = 2
        st = reversi.new(2)
        st["board"], st["turn"] = b, 0
        # seat 0 at 0 traps nothing (no seat-0 disc beyond 1? 2 is seat 0's) → plays and the board is full
        st2 = play(reversi, st, 0, {"cell": 0})
        self.assertTrue(st2["over"])
        self.assertEqual(st2["winner"], 0)
        self.assertEqual(reversi.score(st2, 0), 500 + min(500, 5 * 64))

    def test_random_games_end_by_the_rules(self):
        rnd = random.Random(5)
        for seed in range(1, 30):
            st, passes = reversi.new(seed), 0
            while not st["over"]:
                seat = st["turn"]
                st = play(reversi, st, seat, rnd.choice(reversi.moves(st, seat)))
                passes += st["passed"] >= 0
            a, z = reversi.discs(st, 0), reversi.discs(st, 1)
            self.assertEqual(st["winner"], 0 if a > z else 1 if z > a else -1)
            self.assertFalse(reversi._cells(st["board"], 0) or reversi._cells(st["board"], 1))


class TestDots(unittest.TestCase):
    def test_sizes_and_numbering(self):
        for size, n in (("3", 24), ("4", 40), ("5", 60)):
            st = dots.new(1, 2, {"size": size})
            self.assertEqual(len(st["edges"]), n)
        self.assertEqual(dots.new(1, 2, {"size": "9"})["size"], 4)       # an unknown size: the default
        self.assertEqual(dots.box_edges(3, 0, 0), (0, 3, 12, 13))
        self.assertEqual(dots.edge_boxes(3, 0), [0])
        self.assertEqual(dots.edge_boxes(3, 3), [0, 3])
        self.assertEqual(dots.edge_boxes(3, 13), [0, 1])

    def test_a_box_gives_another_turn(self):
        st = dots.new(2, 2, {"size": "3"})
        for e, seat in ((0, 0), (3, 1), (12, 0)):
            st = play(dots, st, seat, {"edge": e})
        self.assertEqual(st["turn"], 1)
        st = play(dots, st, 1, {"edge": 13})                      # the fourth side of box 0
        self.assertEqual(st["boxes"][0], 2)
        self.assertEqual(st["turn"], 1)                           # again
        st = play(dots, st, 1, {"edge": 1})
        self.assertEqual(st["turn"], 0)
        with self.assertRaises(rules.Illegal):
            dots.apply(st, 0, {"edge": 13})
        with self.assertRaises(rules.Illegal):
            dots.apply(st, 0, {"edge": 24})

    def test_two_boxes_at_once_and_the_end(self):
        rnd = random.Random(3)
        for seed in range(1, 20):
            st = dots.new(seed, 2, {"size": "4"})
            while not st["over"]:
                st = play(dots, st, st["turn"], rnd.choice(dots.moves(st, st["turn"])))
            a, z = st["boxes"].count(1), st["boxes"].count(2)
            self.assertEqual(a + z, 16)
            self.assertEqual(st["winner"], 0 if a > z else 1 if z > a else -1)
        # one line closing two boxes: boxes 0 and 1 share line 13, the only side either still lacks
        st = dots.new(2, 2, {"size": "3"})
        for e in (0, 3, 12, 1, 4, 14):
            st["edges"][e] = 1
        st["turn"] = 0
        st = play(dots, st, 0, {"edge": 13})
        self.assertEqual(st["boxes"][:2], [1, 1])
        self.assertEqual(st["turn"], 0)


class TestSeaBattle(unittest.TestCase):
    FLEET = [[0, 0, 5, 0], [2, 0, 4, 0], [4, 0, 3, 0], [6, 0, 3, 0], [8, 0, 2, 0]]

    def placed(self, seed=2):
        st = seabattle.new(seed, 2, {"fleet": "classic"})
        st = play(seabattle, st, 1, {"place": self.FLEET})
        self.assertEqual(seabattle.to_move(st), [0])
        st = play(seabattle, st, 0, {"place": [[r, 5, n, 0] if n < 5 else [r, 5, n, 0] for r, _, n, _ in self.FLEET]})
        return st

    def test_placing(self):
        st = seabattle.new(2, 2, {"fleet": "classic"})
        self.assertEqual(seabattle.to_move(st), [0, 1])            # both place, in any order
        bad = [
            self.FLEET[:4],                                          # a ship short
            [[0, 0, 5, 0], [0, 2, 4, 1], [4, 0, 3, 0], [6, 0, 3, 0], [8, 0, 2, 0]],   # overlap
            [[0, 6, 5, 0]] + self.FLEET[1:],                         # sticks out
            [[0, 0, 5, 0], [2, 0, 4, 0], [4, 0, 4, 0], [6, 0, 3, 0], [8, 0, 2, 0]],   # not the fleet
            [[0, 0, 5, 2]] + self.FLEET[1:],
            "ships",
        ]
        for place in bad:
            with self.assertRaises(rules.Illegal):
                seabattle.apply(st, 0, {"place": place})
        with self.assertRaises(rules.Illegal):
            seabattle.apply(st, 0, {"shot": 3})                     # not before both have placed
        st = play(seabattle, st, 0, {"place": self.FLEET})
        with self.assertRaises(rules.Illegal):
            seabattle.apply(st, 0, {"place": self.FLEET})           # once
        small = seabattle.new(2, 2, {"fleet": "small"})
        self.assertEqual((small["size"], small["fleet"]), (8, [4, 3, 3, 2]))

    def test_shots_hits_sunk_and_the_end(self):
        st = self.placed(seed=2)                                    # seed 2: seat 0 fires first
        self.assertEqual(seabattle.to_move(st), [0])
        st, rec = seabattle.apply(st, 0, {"shot": 80})               # (8,0): seat 1's ship of 2
        self.assertEqual(rec, {"shot": 80, "hit": True, "sunk": 0, "cells": []})
        self.assertEqual(st["turn"], 1)                             # a hit doesn't give another shot
        st, rec = seabattle.apply(st, 1, {"shot": 99})
        self.assertFalse(rec["hit"])
        with self.assertRaises(rules.Illegal):
            seabattle.apply(st, 0, {"shot": 80})                    # already fired there
        st, rec = seabattle.apply(st, 0, {"shot": 81})
        self.assertEqual((rec["sunk"], rec["cells"]), (2, [80, 81]))
        # sink the rest
        cells = [c for ship in st["ships"][1] for c in ship if c not in (80, 81)]
        other = iter(c for c in range(100) if c not in st["shots"][1])
        for c in cells:
            st = play(seabattle, st, 1, {"shot": next(other)}) if st["turn"] == 1 else st
            st = play(seabattle, st, 0, {"shot": c})
        self.assertEqual((st["over"], st["winner"]), (True, 0))
        self.assertGreater(seabattle.score(st, 0), 500)

    def test_the_view_never_shows_the_other_fleet(self):
        st = self.placed()
        for seat in (0, 1):
            v = seabattle.view(st, seat)
            text = json.dumps(v)
            self.assertEqual(v["ships"], st["ships"][seat])
            self.assertNotIn("theirShips", v)
            for ship in st["ships"][1 - seat]:
                self.assertNotIn(json.dumps(ship), text)
        rec = {"place": self.FLEET}
        self.assertEqual(seabattle.visible(rec, 0, 1), {"placed": True})
        self.assertEqual(seabattle.visible(rec, 1, 1), rec)
        st["over"], st["winner"] = True, 0
        self.assertEqual(seabattle.view(st, 0)["theirShips"], st["ships"][1])      # shown once it's over

    def test_computer_plays_by_the_results_only(self):
        rnd = random.Random(2)
        st = seabattle.new(5, 2, {"fleet": "small"})
        for seat in (0, 1):
            st = play(seabattle, st, seat, seabattle.computer(st, seat, rnd))
        n = 0
        while not st["over"] and n < 200:
            st = play(seabattle, st, st["turn"], seabattle.computer(st, st["turn"], rnd))
            n += 1
        self.assertTrue(st["over"])


class TestComputerStandIns(unittest.TestCase):
    def test_every_game_plays_itself_to_the_end(self):
        for gid, mod in rules.REGISTRY.items():
            if gid not in NEW:
                continue
            for seed in range(1, 15):
                rnd, st = random.Random(seed), mod.new(seed, 2, {})
                guard = 0
                while mod.to_move(st) and guard < 1000:
                    seat = mod.to_move(st)[0]
                    st = play(mod, st, seat, mod.computer(st, seat, rnd))
                    guard += 1
                self.assertIsNotNone(mod.result(st), gid)


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TestJsAndPythonAgree(unittest.TestCase):
    """The JavaScript rules (drawing, the computer, one screen) and the server's rules (checking every move of a match)
    are the same rules: random games — every move, the legal moves before it, the state after it, and probes that may
    or may not be legal — give the same answers in both."""

    def check(self, game):
        out = subprocess.run(["node", "tests/js/turns-fuzz.js", game, str(FUZZ_GAMES)], cwd=HERE, capture_output=True,
                             text=True, check=True, timeout=300).stdout
        mod = rules.get(game)
        n_games = plies = probes = 0
        endings = set()
        for line in out.splitlines():
            g = json.loads(line)
            n_games += 1
            st = mod.new(g["seed"], 2, g["options"])
            for i, step in enumerate(g["steps"]):
                where = f"{game} seed {g['seed']} step {i}"
                if step.get("probe"):
                    probes += 1
                    try:
                        mod.apply(st, step["seat"], step["move"])
                        ok = True
                    except rules.Illegal:
                        ok = False
                    self.assertEqual(ok, step["ok"], f"{where}: {step['move']}")
                    continue
                legal = sorted(rules.canonical(m) for m in mod.moves(st, step["seat"]))
                self.assertEqual(hashlib.md5("\n".join(legal).encode()).hexdigest(), step["legal"], f"{where}: legal moves")
                st, _ = mod.apply(st, step["seat"], step["move"])
                plies += 1
                self.assertEqual(mod.digest(st), step["digest"], where)
            self.assertEqual((st["over"], st["winner"]), (g["over"], g["winner"]))
            endings.add(g["winner"])
        self.assertEqual(n_games, FUZZ_GAMES)
        self.assertGreater(probes, FUZZ_GAMES)
        self.assertTrue({0, 1} <= endings, f"{game}: both players won some")
        return plies

    def test_fourrow(self):
        self.check("fourrow")

    def test_tictactoe(self):
        self.check("tictactoe")

    def test_checkers(self):
        self.check("checkers")

    def test_reversi(self):
        self.check("reversi")

    def test_dots(self):
        self.check("dots")

    def test_seabattle(self):
        self.check("seabattle")


if __name__ == "__main__":
    unittest.main()
