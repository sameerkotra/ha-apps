"""Saved games (SPEC §12): save, continue, end, throw away; one per person and game; honest checks across
sessions; and the score of a game that is ended early."""
import _env  # noqa: F401  (must be first)

from app import db
from base import KABIR, MEERA, ApiBase

STATE = {"snake": [{"x": 5, "y": 5}], "mode": "walls-normal", "score": 120}


class SaveHelpers(ApiBase):
    def save(self, sid, score=120, level=2, seconds=60, h=KABIR, state=None, version=1):
        return self.post(f"/api/sessions/{sid}/save", {"state": state or STATE, "stateVersion": version, "score": score,
                                                       "level": level, "seconds": seconds}, h)

    def started(self, game="snake", mode="walls-normal", h=KABIR, **kw):
        r = self.start(game, mode, h, **kw)
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()

    def saved(self, game="snake", h=KABIR):
        return {g["id"]: g for g in self.get("/api/games", h).json()["games"]}[game]["saved"]


class TestSaves(SaveHelpers):
    def test_save_and_continue(self):
        sid = self.started()["id"]
        self.clock.advance(seconds=70)
        r = self.save(sid)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIsNone(r.json()["replaced"])
        sv = self.saved()
        self.assertEqual((sv["mode"], sv["score"], sv["level"], sv["seconds"], sv["canResume"]), ("walls-normal", 120, 2, 60, True))
        with db.get_conn() as conn:                       # no score yet; the session is over
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM scores").fetchone()[0], 0)
            self.assertIsNotNone(conn.execute("SELECT ended_at FROM play_sessions WHERE id = ?", (sid,)).fetchone()[0])
        self.assertEqual(self.post(f"/api/sessions/{sid}/beat", {"activeSeconds": 80}).json()["ended"], True)
        # continue it (even with another mode asked for: the save's mode wins)
        self.clock.advance(hours=5)
        s2 = self.post("/api/sessions", {"game": "snake", "resume": True, "mode": "wrap-fast"}).json()
        self.assertEqual(s2["mode"], "walls-normal")
        self.assertEqual(s2["saved"]["state"], STATE)
        self.assertEqual(s2["saved"]["seconds"], 60)
        self.clock.advance(seconds=30)
        r = self.post("/api/scores", {"sessionId": s2["id"], "score": 300, "level": 3, "seconds": 90})   # 60 + 30
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["saved"])
        self.assertIsNone(self.saved())                   # used up
        with db.get_conn() as conn:
            row = conn.execute("SELECT * FROM scores").fetchone()
            sess = conn.execute("SELECT * FROM play_sessions WHERE id = ?", (s2["id"],)).fetchone()
        self.assertEqual((row["score"], row["seconds"]), (300, 90))
        self.assertLessEqual(sess["active_seconds"], 33)     # only this session's play counts for limits

    def test_time_checks_across_sessions(self):
        sid = self.started()["id"]
        self.clock.advance(seconds=20)
        self.assertEqual(self.save(sid, seconds=200).status_code, 422)     # more than really played
        self.assertEqual(self.save(sid, seconds=20).status_code, 200)
        s2 = self.post("/api/sessions", {"game": "snake", "resume": True}).json()
        self.clock.advance(seconds=10)
        r = self.post("/api/scores", {"sessionId": s2["id"], "score": 50, "level": 1, "seconds": 60})
        self.assertEqual(r.status_code, 422)              # 20 saved + 10 now < 60

    def test_one_per_game_and_the_replaced_score_is_kept(self):
        a = self.started()["id"]
        self.clock.advance(seconds=60)
        self.save(a, score=100)
        b = self.started(mode="wrap-slow")["id"]
        self.clock.advance(seconds=60)
        r = self.save(b, score=40).json()
        self.assertTrue(r["replaced"]["scoreSaved"])
        self.assertEqual(self.saved()["mode"], "wrap-slow")
        with db.get_conn() as conn:
            self.assertEqual([tuple(x) for x in conn.execute("SELECT mode, score FROM scores")], [("walls-normal", 100)])
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM saved_games").fetchone()[0], 1)
        c = self.started("brick", "classic")["id"]        # another game keeps its own save
        self.clock.advance(seconds=60)
        self.assertEqual(self.save(c, score=500, state={"bricks": [], "balls": []}).status_code, 200)
        self.assertEqual(self.saved("brick")["score"], 500)
        self.assertEqual(self.saved()["score"], 40)
        self.assertIsNone(self.saved("snake", MEERA))     # nobody else's

    def test_saving_again_from_a_continued_game_keeps_one_save(self):
        a = self.started()["id"]
        self.clock.advance(seconds=60)
        self.save(a, score=100)
        b = self.post("/api/sessions", {"game": "snake", "resume": True}).json()["id"]
        self.clock.advance(seconds=30)
        r = self.save(b, score=150, seconds=90).json()
        self.assertIsNone(r["replaced"])                 # its own save, carried on
        self.assertEqual(self.saved()["score"], 150)
        with db.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM scores").fetchone()[0], 0)

    def test_end_it_or_throw_it_away(self):
        a = self.started()["id"]
        self.clock.advance(seconds=60)
        self.save(a, score=210)
        r = self.delete("/api/saved/snake?keepScore=true").json()
        self.assertTrue(r["scoreSaved"])
        self.assertIsNone(self.saved())
        b = self.started()["id"]
        self.clock.advance(seconds=60)
        self.save(b, score=90)
        self.assertFalse(self.delete("/api/saved/snake?keepScore=false").json()["scoreSaved"])
        with db.get_conn() as conn:
            self.assertEqual([x[0] for x in conn.execute("SELECT score FROM scores")], [210])
        self.assertEqual(self.delete("/api/saved/snake").status_code, 404)

    def test_practice_saves_keep_no_score(self):
        a = self.started(practice=True)["id"]
        self.clock.advance(seconds=60)
        self.save(a, score=999)
        s2 = self.post("/api/sessions", {"game": "snake", "resume": True}).json()
        self.assertTrue(s2["practice"])
        self.assertFalse(self.delete("/api/saved/snake?keepScore=true").json()["scoreSaved"])

    def test_refused(self):
        self.assertEqual(self.post("/api/sessions", {"game": "snake", "resume": True}).status_code, 404)
        sid = self.started()["id"]
        self.clock.advance(seconds=30)
        self.assertEqual(self.save(sid, version=2).status_code, 422)          # an out-of-date page
        self.assertEqual(self.save(sid, state={"x": "y" * 60000}).status_code, 413)
        self.assertEqual(self.post(f"/api/sessions/{sid}/save", {"state": "nope", "stateVersion": 1, "score": 1, "level": 1, "seconds": 5}).status_code, 422)
        self.assertEqual(self.save(sid, h=MEERA).status_code, 404)           # not hers
        self.post("/api/scores", {"sessionId": sid, "score": 10, "level": 1, "seconds": 30})
        self.assertEqual(self.save(sid).status_code, 409)                    # already over

    def test_an_old_save_format_can_only_be_ended(self):
        a = self.started()["id"]
        self.clock.advance(seconds=60)
        self.save(a, score=70)
        with db.get_conn() as conn:
            conn.execute("UPDATE saved_games SET state_version = 0")
        self.assertFalse(self.saved()["canResume"])
        self.assertEqual(self.post("/api/sessions", {"game": "snake", "resume": True}).status_code, 409)
        self.assertTrue(self.delete("/api/saved/snake?keepScore=true").json()["scoreSaved"])

    def test_a_save_keeps_its_levels(self):
        s = self.started("snake", "maze")
        self.clock.advance(seconds=60)
        self.save(s["id"], state={"maze": True})
        with db.get_conn() as conn:
            conn.execute("UPDATE levels SET status = 'retired' WHERE game = 'snake' AND number = 1")
        s2 = self.post("/api/sessions", {"game": "snake", "resume": True}).json()
        self.assertEqual(len(s2["levels"]), 10)
        self.assertEqual(s2["levels"][0]["name"], "Four posts")

    def test_ending_early_counts(self):
        """Ending a game part-way (End game, or leaving the page) sends its score like a finished game."""
        r = self.play(80, 45, level=2)
        self.assertTrue(r.json()["saved"])

    def test_children_limits_apply_to_continuing(self):
        a = self.started()["id"]
        self.clock.advance(seconds=60)
        self.save(a)
        self.make_child(KABIR, minutesSchool=1, minutesWeekend=1)
        r = self.post("/api/sessions", {"game": "snake", "resume": True})
        self.assertEqual(r.status_code, 409)


class TestNewGames(SaveHelpers):
    """The second batch of games (Falling Blocks, Paddle Duel, Lane Racer, Flap): play, scores within their
    limits, impossible ones refused, and saving to finish later."""
    CASES = {  # game: (mode, a possible result after 120 s, an impossible one)
        "blocks": ("rising", (40_000, 4), (2_000_000, 4)),
        "duel": ("hard", (6_000, 2), (900_000, 2)),
        "racer": ("four", (3_500, 2), (60_000, 2)),
        "flap": ("moving", (60, 6), (400, 6)),
    }

    def test_each_game_plays_scores_and_saves(self):
        from app import games
        for game, (mode, ok, bad) in self.CASES.items():
            with self.subTest(game=game):
                self.assertEqual(games.GAMES[game]["state_version"], 1)
                self.assertIsNone(games.check_score(game, mode, ok[0], ok[1], 120))
                self.assertIsNotNone(games.check_score(game, mode, bad[0], bad[1], 120))
                sid = self.started(game, mode)["id"]
                self.clock.advance(seconds=125)
                r = self.post("/api/scores", {"sessionId": sid, "score": ok[0], "level": ok[1], "seconds": 120})
                self.assertEqual(r.status_code, 200, r.text)
                self.assertTrue(r.json()["saved"])
                sid = self.started(game, mode)["id"]
                self.clock.advance(seconds=125)
                r = self.post("/api/scores", {"sessionId": sid, "score": bad[0], "level": bad[1], "seconds": 120})
                self.assertEqual(r.status_code, 422, r.text)
                # save one to finish later, then continue it
                sid = self.started(game, mode)["id"]
                self.clock.advance(seconds=70)
                r = self.save(sid, score=ok[0] // 4, level=1, seconds=60, state={"game": game})
                self.assertEqual(r.status_code, 200, r.text)
                self.assertTrue(self.saved(game)["canResume"])
                s2 = self.post("/api/sessions", {"game": game, "resume": True}).json()
                self.assertEqual((s2["mode"], s2["saved"]["state"]), (mode, {"game": game}))
