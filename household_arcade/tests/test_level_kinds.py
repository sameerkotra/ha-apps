"""Level lists for every game (SPEC §11.9): each kind's built-in levels pass its own checks, match the game
file's `LEVELS`, differ from each other; bad levels are refused; the prompt is complete; the game table
plays them."""
import _env  # noqa: F401  (must be first)

import json
import os
import shutil
import subprocess
import unittest

from app import games, level_builder, level_kinds, levels
from app.level_common import LevelError

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GAMES_DIR = os.path.join(HERE, "app", "static", "games")


class TestLevelKinds(unittest.TestCase):
    def test_every_game_has_a_level_list(self):
        # Sudoku and Word Guess (and wave 6's Slide Puzzle, Lights Out and Code Breaker) make their puzzles from the seed,
        # so they have no level list; wave 7's board games have no levels at all (the computer's level is the mode), nor
        # have wave 8's (Ludo, Snakes and Ladders, Carrom, Chess). Word Search, Picture Logic, Tile Match and waves 9-10
        # have a list beside their seeded modes (Themes, Picture book, Layouts, Picture boards, Puzzle book).
        generated = {"sudoku", "wordguess", "slide", "lights", "codebreak",
                     "fourrow", "tictactoe", "checkers", "reversi", "dots", "seabattle", "ludo", "snakes", "carrom", "chess"}
        self.assertEqual(set(levels.SETS), set(games.GAME_IDS) - generated)
        for gid in generated:
            self.assertEqual(games.GAMES[gid]["level_modes"], [])

    def test_kinds_are_complete(self):
        for gid, kind in level_kinds.KINDS.items():
            with self.subTest(game=gid):
                self.assertEqual(kind["game"], gid)
                for key in ("label", "noun", "modes", "fields", "difficulty", "about", "target", "example", "builtin"):
                    self.assertIn(key, kind)
                self.assertNotIn("name", kind["fields"])
                self.assertTrue(set(kind["modes"]) <= set(games.mode_ids(gid)), "modes exist")
                self.assertEqual(sorted(kind["modes"]), sorted(games.GAMES[gid].get("level_modes", [])),
                                 "games.py level_modes match")
                self.assertGreaterEqual(len(kind["builtin"]), 5)

    def test_builtins_valid_unique_and_ordered(self):
        for gid, kind in level_kinds.KINDS.items():
            with self.subTest(game=gid):
                prints = set()
                for data in kind["builtin"]:
                    clean, difficulty, fp = levels.validate(gid, data)
                    self.assertEqual(clean, data, "built-ins are already clean")
                    self.assertTrue(0 <= difficulty <= 100)
                    self.assertNotIn(fp, prints, "no repeats")
                    prints.add(fp)
                levels.validate(gid, kind["example"])

    def test_bad_levels_refused(self):
        for gid, kind in level_kinds.KINDS.items():
            with self.subTest(game=gid):
                good = kind["builtin"][0]
                for bad in ([], dict(good, extra=1), {k: v for k, v in good.items() if k != "name"},
                            dict(good, name="x" * 80)):
                    with self.assertRaises(LevelError):
                        levels.validate(gid, bad)
                for f, check in kind["fields"].items():
                    wrongs = (None, "?", 1) if getattr(check, "describe", "") == "true or false" else \
                        (None, "?", -10 ** 9, 10 ** 9, True)
                    for wrong in wrongs:
                        if wrong == good[f]:
                            continue
                        try:
                            levels.validate(gid, dict(good, **{f: wrong}))
                        except LevelError:
                            continue
                        self.fail(f"{gid}.{f} accepted {wrong!r}")

    def test_prompt(self):
        for gid in level_kinds.KINDS:
            with self.subTest(game=gid):
                p = level_builder.prompt(gid, 3, 11, [dict(levels.SETS[gid]["builtin"][0], number=10, difficulty=5)])
                self.assertIn("exactly 3", p)
                self.assertIn('"levels"', p)
                for f in level_kinds.KINDS[gid]["fields"]:
                    self.assertIn(f'"{f}"', p)

    def test_level_count_limits_the_level(self):
        for gid, kind in level_kinds.KINDS.items():
            with self.subTest(game=gid):
                mode = kind["modes"][0]
                self.assertTrue(games.uses_levels(gid, mode))
                if games.GAMES[gid].get("levels_end"):
                    self.assertEqual(games.max_level(gid, mode, 12), 12)

    @unittest.skipUnless(shutil.which("node"), "Node isn't installed")
    def test_game_files_have_the_same_builtins(self):
        for gid, kind in level_kinds.KINDS.items():
            with self.subTest(game=gid):
                path = os.path.join(GAMES_DIR, f"{gid}-logic.js")
                out = subprocess.run(["node", "-e", f"const L=require({json.dumps(path)});"
                                      "console.log(JSON.stringify({levels: L.LEVELS, usable: typeof L.usableLevels}))"],
                                     capture_output=True, text=True, timeout=30, check=True)
                got = json.loads(out.stdout)
                self.assertEqual(got["levels"], kind["builtin"])
                self.assertEqual(got["usable"], "function")


if __name__ == "__main__":
    unittest.main()
