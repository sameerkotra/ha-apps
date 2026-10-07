"""The lists an AI model can add to for the puzzles (SPEC §11.9): Arrow Release · Picture boards, the Puzzle book of
Car Park, Colour Sort, Bolt Sort, Dot Connect and Untangle, Picture Logic · Picture book, Word Search · Themes and
Tile Match · Layouts — each kind's own checks refuse what the game couldn't play (or couldn't be solved), sessions
carry the list, a model's levels are added after the built-in ones, and the next game carries on (SPEC §14)."""
import _env  # noqa: F401  (must be first)

import unittest
from unittest import mock

from app import games, level_builder, level_kinds, levels, progress, db
from app.level_common import LevelError
from base import ASHA, KABIR, ApiBase
from test_levels import FakeModel

BOOKS = {"arrows": "book", "parking": "book", "watersort": "book", "bolts": "book", "connect": "book",
         "untangle": "book", "nonogram": "pictures", "wordsearch": "themes", "tiles": "layouts"}


def refused(game, data, words=""):
    try:
        levels.validate(game, dict({"name": "Test"}, **data))
    except LevelError as e:
        return words.lower() in str(e).lower()
    return False


class TestKinds(unittest.TestCase):
    def test_every_book_is_a_level_list_that_carries_on(self):
        for game, mode in BOOKS.items():
            with self.subTest(game=game):
                self.assertIn(game, level_kinds.KINDS)
                self.assertEqual(levels.SETS[game]["modes"], [mode])
                self.assertTrue(games.uses_levels(game, mode))
                self.assertIn(mode, games.continue_modes(game))
                self.assertIsNone(games.continue_modes(game)[mode], "the list's length decides")
                self.assertTrue(games.GAMES[game].get("levels_end"))

    def test_car_park(self):
        ok = level_kinds.KINDS["parking"]["builtin"][0]
        self.assertEqual(levels.validate("parking", ok)[0], ok)
        self.assertTrue(refused("parking", {"grid": ["......", "......", "AA....", "......", "......", "......"]}, "two other"))
        self.assertTrue(refused("parking", {"grid": ["......", "..B...", "AAB.CC", "......", "......", "DD...."]}, "can't get out"))
        self.assertTrue(refused("parking", {"grid": ["......", "..B...", "A.B...", "A.....", "......", "CC...."]}, "red car"))
        self.assertTrue(refused("parking", {"grid": ["......", "..BB..", "AAB...", "......", "......", "CC...."]}, "straight"))
        self.assertTrue(refused("parking", {"grid": ["......", ".B....", "AAC...", "..C...", "......", "DD...."]}, "B"))
        # renaming the vehicles makes the same car park
        a = levels.validate("parking", {"name": "A", "grid": ["......", "..BCCD", "AAB..D", "......", "......", "......"]})[2]
        b = levels.validate("parking", {"name": "B", "grid": ["......", "..XYYZ", "AAX..Z", "......", "......", "......"]})[2]
        self.assertEqual(a, b)

    def test_colour_and_bolt_sort(self):
        self.assertTrue(refused("watersort", {"tubes": ["AAB", "BBB", "A", "", ""]}, "exactly 4"))
        self.assertTrue(refused("watersort", {"tubes": ["AAAA", "CCCC", "", ""]}, "without skipping"))
        self.assertTrue(refused("watersort", {"tubes": ["AAAA", "BBBB", ""]}, "already sorted"))
        self.assertTrue(refused("watersort", {"tubes": ["ABCA", "BCAB", "CABC"]}, "more tube"))
        self.assertTrue(refused("watersort", {"tubes": ["ABAB", "BABA", "", "", "", ""]}, "at most 3"))
        self.assertTrue(refused("bolts", {"bolts": ["BCAC", "BBAC", "BACA", ""], "hidden": False}, "can't be sorted"))
        self.assertEqual(levels.validate("bolts", {"name": "Fine", "bolts": ["ABAB", "BABA", ""], "hidden": True})[0]["hidden"], True)
        self.assertTrue(refused("bolts", {"bolts": ["KKKK", "AAAA", ""], "hidden": False}, "characters of"))

    def test_dot_connect(self):
        self.assertTrue(refused("connect", {"grid": ["AABBB", "AACCB", "DDDCB", "EEECB", "FFFCC"]}, "touches itself"))
        self.assertTrue(refused("connect", {"grid": ["AAAAB", "CCCCB", "DDDDB", "EEEEB", "FFFFA"]}, "one line"))
        self.assertTrue(refused("connect", {"grid": ["AAAAA", "BBBBB", "CCCCC", "DDDDD", "EEEEE", "FFFFF"]}, "square"))
        self.assertTrue(refused("connect", {"grid": ["AAAAA", "BBBBB", "CCCCC", "DDDDD", "EEEEF"]}, "at least 3"))

    def test_untangle(self):
        pts = [[0, 0], [8, 0], [8, 8], [0, 8], [4, 4], [4, 0]]
        ok = {"points": pts, "edges": [[0, 5], [5, 1], [1, 2], [2, 3], [3, 0], [0, 4], [4, 2], [5, 4], [1, 4], [3, 4]]}
        levels.validate("untangle", dict(ok, name="Fine"))
        self.assertTrue(refused("untangle", dict(ok, edges=ok["edges"] + [[5, 2]]), "cross"))
        self.assertTrue(refused("untangle", dict(ok, edges=ok["edges"] + [[3, 1]]), "through another point"))
        self.assertTrue(refused("untangle", dict(ok, edges=ok["edges"] + [[0, 5]]), "twice"))
        self.assertTrue(refused("untangle", dict(ok, edges=ok["edges"] + [[2, 9]]), "join points 0-5"))
        self.assertTrue(refused("untangle", dict(ok, points=pts[:5] + [[4, 4]]), "same place"))
        self.assertTrue(refused("untangle", {"points": pts, "edges": [[0, 1], [1, 2], [2, 0], [3, 4], [4, 5], [5, 3]]}, "one drawing"))

    def test_picture_logic(self):
        ok = level_kinds.KINDS["nonogram"]["builtin"][1]
        levels.validate("nonogram", ok)
        self.assertTrue(refused("nonogram", {"picture": ["#####", "#####", "#####", "#####", "#####", "#####"]}, "square"))
        self.assertTrue(refused("nonogram", {"picture": ["#....", ".....", ".....", ".....", "....."]}, "fill 25-80"))
        checker = ["#.#.#.", ".#.#.#", "#.#.#.", ".#.#.#", "#.#.#.", ".#.#.#"]
        self.assertTrue(refused("nonogram", {"picture": checker}, "undecided"))
        long_run = ["#" * 11] + ["#...#....#."] * 10
        self.assertTrue(refused("nonogram", {"picture": long_run}, "more than 9"))

    def test_word_search(self):
        ok = level_kinds.KINDS["wordsearch"]["builtin"][1]
        levels.validate("wordsearch", ok)
        self.assertTrue(refused("wordsearch", dict(ok, words=ok["words"][:8] + ok["words"][:1]), "twice"))
        self.assertTrue(refused("wordsearch", dict(ok, group="puzzler"), "at least 16"))
        self.assertTrue(refused("wordsearch", dict(ok, words=["Fish"] + ok["words"]), "characters of"))
        self.assertTrue(refused("wordsearch", dict(ok, words=["dead"] + ok["words"]), "suitable"))

    def test_tile_match(self):
        ok = level_kinds.KINDS["tiles"]["builtin"][0]
        levels.validate("tiles", ok)
        tower = [{"dx": 0, "dy": 0, "rows": ["####", "####", "####", "####"]}] + [{"dx": 0, "dy": 0, "rows": ["#"]}] * 4
        self.assertTrue(refused("tiles", {"layers": tower}, "two tiles at a time"))
        self.assertTrue(refused("tiles", {"layers": [{"dx": 0, "dy": 0, "rows": ["####", "####", "####", "####"]},
                                                     {"dx": 12, "dy": 0, "rows": ["##"]}]}, "floats"))
        self.assertTrue(refused("tiles", {"layers": [{"dx": 0, "dy": 0, "rows": ["#" * 12] * 10},
                                                     {"dx": 4, "dy": 0, "rows": ["#" * 12, "#" * 12]}]}, "at most 26"))
        self.assertTrue(refused("tiles", {"layers": [{"dx": 0, "dy": 0, "rows": ["#######"]},
                                                     {"dx": 0, "dy": 0, "rows": ["##", "##"]}]}, "even number"))
        self.assertTrue(refused("tiles", {"layers": [{"dx": 0, "dy": 0, "rows": ["####", "####", "####", "####"]},
                                                     {"dx": 0, "dy": 0, "rows": ["#"], "z": 1}]}, "object"))


class TestBooksInPlay(ApiBase):
    def test_sessions_carry_the_list_and_the_next_game_carries_on(self):
        for game, mode in BOOKS.items():
            with self.subTest(game=game):
                r = self.start(game, mode)
                self.assertEqual(r.status_code, 201, r.text)
                body = r.json()
                builtin = level_kinds.KINDS[game]["builtin"]
                self.assertEqual(body["levels"], builtin)
                self.clock.advance(seconds=90)
                done = self.post("/api/scores", {"sessionId": body["id"], "score": 500, "level": 2, "seconds": 90,
                                                 "cleared": 2}, KABIR)
                self.assertEqual(done.status_code, 200, done.text)
                g = next(x for x in self.get("/api/games").json()["games"] if x["id"] == game)
                self.assertEqual(g["progress"][mode], {"cleared": 2, "total": len(builtin), "next": 3})
                self.assertEqual(g["levels"], len(builtin))
                too_far = self.post("/api/scores", {"sessionId": self.start(game, mode).json()["id"], "score": 10,
                                                    "level": len(builtin) + 1, "seconds": 5}, KABIR)
                self.assertEqual(too_far.status_code, 422, "the level can't pass the list")

    def test_a_model_adds_boards_after_the_built_in_ones(self):
        model = FakeModel({"levels": [
            {"name": "Long jam", "grid": ["BB..C.", "....C.", "AA..C.", "..DDD.", "......", "......"]},
            {"name": "Stuck", "grid": ["......", "..B...", "AAB.CC", "......", "......", "DD...."]},   # can't get out
        ]})
        with mock.patch.object(level_builder, "_generate", model):
            self.settings({"ai_levels_enabled": True, "ai_url": "http://192.168.1.10:11434", "ai_model": "tiny"})
            r = self.post("/api/admin/levels/build", {"game": "parking", "count": 1}, ASHA)
            self.assertEqual(r.status_code, 202, r.text)
            level_builder.run_pending()
        self.assertIn("6 squares by 6", model.prompts[0])
        self.assertIn("car park 7", model.prompts[0].lower())
        got = self.start("parking", "book").json()["levels"]
        self.assertEqual(len(got), 7)
        self.assertEqual(got[-1]["name"], "Long jam")
        with db.get_conn() as conn:
            self.assertEqual(progress.total(conn, "parking", "book"), 7)


if __name__ == "__main__":
    unittest.main()
