"""Carrying on from the next level (SPEC §14) and favourite games (SPEC §9)."""
import _env  # noqa: F401  (must be first)

import unittest

from base import ASHA, KABIR, MEERA, ApiBase
from app import db, games, progress


class Progress(ApiBase):
    def begin(self, game="mines", mode="boards", h=KABIR, **extra):
        r = self.post("/api/sessions", {"game": game, "mode": mode, "practice": False, **extra}, h)
        return r

    def finish(self, sid, score=100, level=1, seconds=60, h=KABIR, **extra):
        self.clock.advance(seconds=seconds)
        return self.post("/api/scores", {"sessionId": sid, "score": score, "level": level, "seconds": seconds, **extra}, h)

    def progress(self, game="mines", mode="boards", h=KABIR):
        g = next(x for x in self.get("/api/games", h).json()["games"] if x["id"] == game)
        return g["progress"].get(mode)

    def test_cleared_levels_are_saved_from_heartbeats_and_the_result(self):
        with db.get_conn() as conn:
            count = progress.total(conn, "mines", "boards")
        self.assertGreaterEqual(count, 5)
        self.assertEqual(self.progress(), {"cleared": 0, "total": count, "next": 1})
        sid = self.begin().json()["id"]
        self.clock.advance(seconds=30)
        r = self.post(f"/api/sessions/{sid}/beat", {"activeSeconds": 30, "level": 3})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.progress()["cleared"], 2)                  # on level 3: levels 1 and 2 are cleared
        r = self.finish(sid, level=4)
        self.assertEqual(r.json()["progress"]["cleared"], 3)            # ended on level 4: 3 cleared
        self.assertEqual(self.progress()["next"], 4)
        sid = self.begin().json()["id"]                                  # a short game never takes it back
        self.finish(sid, level=1)
        self.assertEqual(self.progress()["cleared"], 3)

    def test_a_won_game_clears_its_last_level(self):
        sid = self.begin().json()["id"]
        r = self.finish(sid, level=2, cleared=2)
        self.assertEqual(r.json()["progress"]["cleared"], 2)
        sid = self.begin().json()["id"]
        r = self.finish(sid, level=2, cleared=9)                        # never more than the level reached
        self.assertEqual(r.json()["progress"]["cleared"], 2)

    def test_start_from_the_next_level(self):
        with db.get_conn() as conn:
            progress.record(conn, "u_kabir", "mines", "boards", 4)
        r = self.begin(startLevel=5)
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual((r.json()["startLevel"], r.json()["progress"]["next"]), (5, 5))
        self.assertEqual(self.begin(startLevel=3).status_code, 201)      # any cleared level again
        self.assertEqual(self.begin(startLevel=6).status_code, 400)      # not open yet
        self.assertEqual(self.begin(startLevel=0).status_code, 422)
        self.assertEqual(self.begin(startLevel="2").status_code, 422)
        self.assertEqual(self.begin("snake", "walls-normal", startLevel=2).status_code, 422)   # doesn't carry on

    def test_practice_counts_and_bests_count_only_games_from_level_1(self):
        sid = self.begin(practice=True).json()["id"]
        self.finish(sid, level=3)
        self.assertEqual(self.progress()["cleared"], 2)                 # Practice still solved the levels
        sid = self.begin(startLevel=3).json()["id"]
        r = self.finish(sid, score=5000, level=4).json()
        self.assertTrue(r["saved"])
        self.assertEqual(r["startLevel"], 3)
        self.assertFalse(r["personalBest"] or r["householdRecord"])
        lb = self.get("/api/leaderboard?game=mines&mode=boards").json()["rows"]
        self.assertEqual(lb, [])                                         # not on the leaderboard
        mine = self.get("/api/scores/mine").json()
        self.assertEqual(mine["recent"][0]["startLevel"], 3)
        self.assertEqual([b for b in mine["bests"] if b["mode"] == "boards"], [])
        sid = self.begin().json()["id"]
        r = self.finish(sid, score=200, level=1).json()
        self.assertTrue(r["personalBest"])
        self.assertEqual(self.get("/api/leaderboard?game=mines&mode=boards").json()["rows"][0]["score"], 200)

    def test_races_and_daily_challenges_never_move_it(self):
        row = {"game": "mines", "mode": "boards", "match_id": "m1", "daily": None}
        self.assertFalse(progress.counts(row))
        self.assertFalse(progress.counts(dict(row, match_id=None, daily="2026-09-21")))
        self.assertTrue(progress.counts(dict(row, match_id=None)))
        self.assertFalse(progress.counts(dict(row, match_id=None, mode="easy")))

    def test_start_over_and_the_admin(self):
        with db.get_conn() as conn:
            progress.record(conn, "u_kabir", "mines", "boards", 4)
            progress.record(conn, "u_kabir", "lights", "climb", 2)
        listed = self.get("/api/progress").json()["progress"]
        self.assertEqual({(p["game"], p["mode"], p["cleared"]) for p in listed},
                         {("mines", "boards", 4), ("lights", "climb", 2)})
        self.assertEqual(self.delete("/api/progress/mines/boards").status_code, 200)
        self.assertEqual(self.progress()["cleared"], 0)
        self.assertEqual(self.delete("/api/progress/snake/walls-normal").status_code, 404)
        self.assertEqual(self.get("/api/admin/users/u_kabir/progress", KABIR).status_code, 403)
        r = self.get("/api/admin/users/u_kabir/progress", ASHA).json()["progress"]
        self.assertEqual([(p["game"], p["cleared"], p["total"], p["next"]) for p in r], [("lights", 2, 5, 3)])
        self.assertEqual(self.delete("/api/admin/users/u_kabir/progress/lights/climb", ASHA).status_code, 200)
        self.assertEqual(self.get("/api/progress").json()["progress"], [])

    def test_all_cleared_starts_again_at_level_1(self):
        with db.get_conn() as conn:
            progress.record(conn, "u_kabir", "lights", "climb", 99)          # capped at the mode's 5 boards
        self.assertEqual(self.progress("lights", "climb"), {"cleared": 5, "total": 5, "next": 1})
        self.assertEqual(progress.next_level(3, 5), 4)
        self.assertEqual(progress.next_level(0, 0), 1)

    def test_a_saved_game_keeps_its_start_level(self):
        with db.get_conn() as conn:
            progress.record(conn, "u_kabir", "lights", "climb", 2)
        sid = self.begin("lights", "climb", startLevel=3).json()["id"]
        self.clock.advance(seconds=20)
        r = self.post(f"/api/sessions/{sid}/save", {"state": {"x": 1}, "stateVersion": 1, "score": 0, "level": 3,
                                                     "seconds": 20})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["saved"]["startLevel"], 3)
        r = self.post("/api/sessions", {"game": "lights", "resume": True})
        self.assertEqual(r.json()["startLevel"], 3)

    def test_every_carrying_mode_exists(self):
        for game, modes in games.CONTINUE_LEVELS.items():
            for mode in modes:
                self.assertIn(mode, games.mode_ids(game), (game, mode))


class Favourites(ApiBase):
    def test_mark_unmark_and_order(self):
        self.assertEqual(self.get("/api/me").json()["favourites"], [])
        self.assertEqual(self.put("/api/favourites/mines", {"on": True}).json()["favourites"], ["mines"])
        self.assertEqual(self.put("/api/favourites/snake", {"on": True}).json()["favourites"], ["mines", "snake"])
        self.assertEqual(self.put("/api/favourites/mines", {"on": True}).json()["favourites"], ["mines", "snake"])
        r = self.get("/api/games").json()
        self.assertEqual(r["favourites"], ["mines", "snake"])
        self.assertTrue(next(g for g in r["games"] if g["id"] == "snake")["favourite"])
        self.assertFalse(next(g for g in r["games"] if g["id"] == "brick")["favourite"])
        self.assertEqual(self.put("/api/favourites/mines", {"on": False}).json()["favourites"], ["snake"])
        self.assertEqual(self.get("/api/me", MEERA).json()["favourites"], [])          # each person their own

    def test_refused_and_switched_off(self):
        self.assertEqual(self.put("/api/favourites/nope", {"on": True}).status_code, 404)
        self.assertEqual(self.put("/api/favourites/snake", {"on": "yes"}).status_code, 422)
        self.put("/api/favourites/snake", {"on": True})
        self.settings({"disabled_games": ["snake"]})
        r = self.get("/api/games").json()
        self.assertEqual(r["favourites"], [])                             # not shown while switched off
        self.assertEqual(self.get("/api/me").json()["favourites"], ["snake"])      # but kept


if __name__ == "__main__":
    unittest.main()
