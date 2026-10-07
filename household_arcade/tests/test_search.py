"""The Games page search (SPEC §9): every game's tags, and /api/games sending them."""
import _env  # noqa: F401  (must be first)

import unittest

from base import KABIR, ApiBase
from app import games


class Tags(unittest.TestCase):
    def test_every_game_has_tags_from_the_set(self):
        for gid, g in games.GAMES.items():
            self.assertTrue(g["tags"], gid)
            self.assertLessEqual(set(g["tags"]), set(games.TAG_SET), gid)
            self.assertEqual(len(g["tags"]), len(set(g["tags"])), gid)

    def test_derived_tags(self):
        self.assertIn("turn by turn", games.GAMES["chess"]["tags"])
        self.assertNotIn("turn by turn", games.GAMES["carrom"]["tags"])      # one screen only
        self.assertIn("levels", games.GAMES["arrows"]["tags"])
        self.assertNotIn("levels", games.GAMES["sudoku"]["tags"])
        self.assertIn("gentle", games.GAMES["lights"]["tags"])                # "Little 3 × 3 (gentle)"
        self.assertIn("gentle", games.GAMES["wordsearch"]["tags"])            # "Little ones"
        self.assertNotIn("gentle", games.GAMES["snake"]["tags"])
        self.assertEqual(games.GAMES["ludo"]["tags"][:3], ["board", "dice", "two players"])


class Api(ApiBase):
    def test_games_list_has_tags(self):
        for g in self.get("/api/games", KABIR).json()["games"]:
            self.assertEqual(g["tags"], games.GAMES[g["id"]]["tags"])


if __name__ == "__main__":
    unittest.main()
