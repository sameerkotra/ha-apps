"""Wave 4: the puzzle and word games' table entries (Sudoku, Word Guess, Word Search), the settings and
per-game preferences they use, scores that keep nothing when a puzzle wasn't solved, and the daily challenge."""
import _env  # noqa: F401  (must be first)

import unittest

from app import daily, db, games, scores, settings
from base import ASHA, DEV, KABIR, MEERA, ApiBase

NEW = ("sudoku", "wordguess", "wordsearch")
TODAY = "2026-09-21"


class TestGameTable(ApiBase):
    def test_the_four_games_are_listed_with_their_modes(self):
        g = {x["id"]: x for x in self.get("/api/games").json()["games"]}
        for gid in NEW:
            self.assertIn(gid, g)
            self.assertEqual(g[gid]["canSave"], True, gid)
            self.assertIsNone(g[gid]["daily"], gid)
        self.assertEqual([m["id"] for m in g["sudoku"]["modes"]], ["easy", "medium", "hard", "expert"])
        self.assertEqual([m["id"] for m in g["wordguess"]["modes"]], ["classic", "easy", "strict"])
        self.assertEqual([m["id"] for m in g["wordsearch"]["modes"]], ["little", "kids", "family", "puzzler", "themes"])
        for gid in NEW:                              # puzzles made from the seed (Word Search's Themes are a list, SPEC §11.9)
            self.assertEqual(games.GAMES[gid]["level_modes"], ["themes"] if gid == "wordsearch" else [])
            self.assertEqual(games.GAMES[gid]["race"]["rule"], "score")

    def test_honest_score_limits(self):
        self.assertIsNone(games.check_score("sudoku", "easy", 10_000, 1, 60, None))
        self.assertIsNotNone(games.check_score("sudoku", "easy", 10_001, 1, 600, None))
        self.assertIsNone(games.check_score("wordguess", "easy", 8_999, 1, 30, None))
        self.assertIsNotNone(games.check_score("wordguess", "classic", 10_000, 1, 30, None))
        self.assertIsNone(games.check_score("wordsearch", "puzzler", 2_100, 1, 20, None))
        self.assertIsNotNone(games.check_score("wordsearch", "puzzler", 2_501, 1, 900, None))
        self.assertIsNotNone(games.check_score("wordsearch", "kids", 2_300, 1, 5, None), "too fast for the points")

    def test_mode_labels(self):
        self.assertEqual(games.mode_label("sudoku", "hard"), "Hard")
        self.assertEqual(games.mode_label("sudoku", "daily-2026-09-21"), "Daily challenge · 2026-09-21")

    def test_a_game_can_be_switched_off(self):
        self.settings({"disabled_games": ["sudoku"]})
        ids = [x["id"] for x in self.get("/api/games").json()["games"]]
        self.assertNotIn("sudoku", ids)
        self.assertEqual(self.start("sudoku", "easy").status_code, 409)


class TestUnfinished(ApiBase):
    def test_an_unsolved_puzzle_keeps_nothing(self):
        for gid, mode in (("sudoku", "easy"), ("wordguess", "classic")):
            r = self.play(0, seconds=60, game=gid, mode=mode)
            self.assertEqual(r.status_code, 200, r.text)
            self.assertFalse(r.json()["saved"], gid)
            self.assertEqual(r.json()["reason"], "unfinished", gid)
        with db.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM scores").fetchone()[0], 0)

    def test_a_solved_one_is_saved_and_ranks_by_score(self):
        r = self.play(9_700, seconds=300, game="sudoku", mode="medium")
        self.assertTrue(r.json()["saved"])
        self.assertTrue(r.json()["personalBest"])
        self.play(9_900, seconds=100, game="sudoku", mode="medium", h=MEERA)
        rows = self.get("/api/leaderboard?game=sudoku&mode=medium&period=all").json()["rows"]
        self.assertEqual([x["name"] for x in rows][:1], ["Meera Rao"])

    def test_other_games_still_keep_partial_scores_and_practice_says_so(self):
        self.assertEqual(self.play(300, seconds=60, game="wordsearch", mode="kids").json()["saved"], True)
        self.assertEqual(self.play(9_000, seconds=60, game="sudoku", mode="easy", practice=True).json()["reason"], "practice")

    def test_keeps_nothing_only_when_the_game_asks(self):
        self.assertTrue(games.keeps_nothing("sudoku", 0))
        self.assertFalse(games.keeps_nothing("sudoku", 5))
        self.assertFalse(games.keeps_nothing("snake", 0))
        self.assertFalse(games.keeps_nothing("wordsearch", 0))


class TestSudokuHints(ApiBase):
    def test_default_and_practice(self):
        r = self.start("sudoku", "easy")
        self.assertEqual(r.json()["config"], {"hints": 3})
        r = self.start("sudoku", "easy", practice=True)
        self.assertEqual(r.json()["config"], {"hints": None}, "unlimited in Practice")

    def test_the_app_setting(self):
        self.settings({"sudoku_hints": 7})
        self.assertEqual(self.start("sudoku", "hard").json()["config"], {"hints": 7})
        self.settings({"sudoku_hints": 0})
        self.assertEqual(self.start("sudoku", "hard").json()["config"], {"hints": 0})
        for bad in (-1, 21, "3", None, True, 2.5):
            self.assertEqual(self.put("/api/admin/settings", {"sudoku_hints": bad}, ASHA).status_code, 422, bad)
        self.assertEqual(settings.get("sudoku_hints"), 0)
        self.assertEqual(self.put("/api/admin/settings", {"sudoku_hints": 3}, KABIR).status_code, 403)

    def test_settings_list_has_labels(self):
        body = self.get("/api/admin/settings", ASHA).json()
        self.assertEqual(body["values"]["sudoku_hints"], 3)
        self.assertFalse(body["values"]["show_daily_challenges"])
        self.assertIn("sudoku_hints", body["meta"])


class TestGamePrefs(ApiBase):
    def test_remembered_per_person_and_per_game(self):
        self.assertEqual(self.get("/api/prefs").json()["gamePrefs"], {})
        r = self.put("/api/prefs", {"gamePrefs": {"sudoku": {"mistakes": "end"}}})
        self.assertEqual(r.status_code, 200, r.text)
        self.put("/api/prefs", {"gamePrefs": {"sudoku": {"lines": "off"}, "wordguess": {"x": "y"}}})
        self.assertEqual(self.get("/api/prefs").json()["gamePrefs"],
                         {"sudoku": {"mistakes": "end", "lines": "off"}, "wordguess": {"x": "y"}})
        self.assertEqual(self.get("/api/me").json()["prefs"]["gamePrefs"]["sudoku"]["lines"], "off")
        self.assertEqual(self.get("/api/prefs", MEERA).json()["gamePrefs"], {})
        self.put("/api/prefs", {"look": "lcd"})
        self.assertEqual(self.get("/api/prefs").json()["gamePrefs"]["sudoku"]["mistakes"], "end", "other prefs leave it")

    def test_bad_data_is_refused_and_saves_nothing(self):
        for bad in ({"gamePrefs": "x"}, {"gamePrefs": {"Sudoku": {"a": "b"}}}, {"gamePrefs": {"sudoku": "x"}},
                    {"gamePrefs": {"sudoku": {"A B": "x"}}}, {"gamePrefs": {"sudoku": {"a": 5}}},
                    {"gamePrefs": {"sudoku": {"a": "x" * 80}}}, {"gamePrefs": {"sudoku": {f"k{i}": "v" * 20 for i in range(200)}}},
                    {"gamePrefs": {f"g{i}": {"a": "b"} for i in range(60)}}):
            self.assertEqual(self.put("/api/prefs", bad).status_code, 422, str(bad)[:60])
        self.assertEqual(self.get("/api/prefs").json()["gamePrefs"], {})

    def test_a_choice_can_be_changed(self):
        self.put("/api/prefs", {"gamePrefs": {"sudoku": {"mistakes": "end", "lines": "off"}}})
        r = self.put("/api/prefs", {"gamePrefs": {"sudoku": {"lines": "on"}}})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.get("/api/prefs").json()["gamePrefs"], {"sudoku": {"mistakes": "end", "lines": "on"}})
        self.assertEqual(self.put("/api/prefs", {"gamePrefs": {"pinball": {"a": "b"}}}).status_code, 422, "unknown game")

    def test_sudoku_number_lines_three_way_choice_is_kept(self):
        # Rows, columns and boxes ("on", also what an old "On" means), rows and columns only ("rows"), none ("off")
        for choice in ("rows", "off", "on"):
            self.assertEqual(self.put("/api/prefs", {"gamePrefs": {"sudoku": {"lines": choice}}}).status_code, 200)
            self.assertEqual(self.get("/api/me").json()["prefs"]["gamePrefs"]["sudoku"]["lines"], choice)


class TestSavedPuzzles(ApiBase):
    """Saving a puzzle to finish later; ending a saved one keeps a score only when something was solved."""
    def save(self, sid, score, game_state=None, h=KABIR):
        return self.post(f"/api/sessions/{sid}/save", {"state": game_state or {"mode": "easy"}, "stateVersion": 1, "score": score,
                                                       "level": 1, "seconds": 60}, h)

    def test_ending_a_saved_sudoku_with_nothing_solved_keeps_no_score(self):
        sid = self.start("sudoku", "easy").json()["id"]
        self.clock.advance(seconds=70)
        self.assertEqual(self.save(sid, 0).status_code, 200)
        r = self.delete("/api/saved/sudoku?keepScore=true").json()
        self.assertFalse(r["scoreSaved"]); self.assertEqual(r["reason"], "unfinished")
        with db.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM scores").fetchone()[0], 0)

    def test_a_removed_game_leaves_old_rows_harmless(self):
        """Solitaire was in 1.5.0 and is gone: its old scores, saved games and sessions mustn't break any page."""
        self.play(300, seconds=60, game="wordsearch", mode="kids")
        with db.get_conn() as conn:
            conn.execute("INSERT INTO scores (user_id, game, mode, score, level, seconds, started_at, ended_at, app_version) "
                         "SELECT user_id, 'solitaire', 'draw1', 500, 1, 60, started_at, ended_at, '1.5.0' FROM scores LIMIT 1")
        for path in ("/api/games", "/api/scores/mine", "/api/leaderboard?game=wordsearch&mode=kids&period=all",
                     "/api/me"):
            self.assertEqual(self.get(path).status_code, 200, path)
        self.assertNotIn("solitaire", [g["id"] for g in self.get("/api/games").json()["games"]])
        self.assertIn(self.start("solitaire", "draw1").status_code, (400, 404, 422))

    def test_continuing_a_saved_word_search(self):
        sid = self.start("wordsearch", "family").json()["id"]
        self.clock.advance(seconds=70)
        self.save(sid, 300)
        r = self.post("/api/sessions", {"game": "wordsearch", "resume": True})
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(r.json()["mode"], "family"); self.assertEqual(r.json()["saved"]["score"], 300)
        self.assertEqual(r.json()["config"], {"hints": 3}, "the config comes with a continued game too")


def on(test):
    test.settings({"show_daily_challenges": True})


class TestDailyOff(ApiBase):
    def test_everything_is_hidden_and_refused(self):
        self.assertFalse(self.get("/api/me").json()["dailyChallenges"])
        r = self.get("/api/daily")
        self.assertEqual(r.status_code, 404)
        self.assertIn("turned off", r.json()["detail"])
        self.assertEqual(self.get("/api/daily/board?game=sudoku").status_code, 404)
        self.assertEqual(self.start_daily("sudoku").status_code, 404)
        for x in self.get("/api/games").json()["games"]:
            self.assertIsNone(x["daily"])
        with db.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM daily_challenges").fetchone()[0], 0, "nothing is picked while off")

    def start_daily(self, game, h=KABIR, practice=False):
        return self.post("/api/sessions", {"game": game, "mode": "x", "daily": True, "practice": practice}, h)


class TestDaily(ApiBase):
    def setUp(self):
        super().setUp()
        on(self)

    def start_daily(self, game, h=KABIR, practice=False):
        return self.post("/api/sessions", {"game": game, "mode": "x", "daily": True, "practice": practice}, h)

    def todays(self, h=KABIR):
        r = self.get("/api/daily", h)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_three_games_the_same_seed_for_everyone(self):
        a, b = self.todays(KABIR), self.todays(MEERA)
        self.assertEqual(a["date"], TODAY)
        self.assertEqual(len(a["challenges"]), 3)
        self.assertEqual([(c["game"], c["mode"], c["seed"]) for c in a["challenges"]],
                         [(c["game"], c["mode"], c["seed"]) for c in b["challenges"]])
        self.assertEqual(a, self.todays(KABIR), "asking again doesn't change the picks")
        self.assertEqual(self.get("/api/me").json()["dailyChallenges"], True)
        for c in a["challenges"]:
            self.assertFalse(c["played"]); self.assertIsNone(c["score"])
            self.assertTrue(c["name"] and c["icon"] and c["modeLabel"])
        self.assertEqual(a["daysPlayed"], 0)
        games_by_id = {x["id"]: x for x in self.get("/api/games").json()["games"]}
        picked = {c["game"] for c in a["challenges"]}
        for gid, x in games_by_id.items():
            self.assertEqual(x["daily"] is not None, gid in picked, gid)
        with db.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM daily_challenges WHERE date = ?", (TODAY,)).fetchone()[0], 3)

    def test_the_seed_and_picks_depend_on_the_date_only(self):
        self.assertEqual(daily.seed_for("2026-09-21", "sudoku"), daily.seed_for("2026-09-21", "sudoku"))
        self.assertNotEqual(daily.seed_for("2026-09-21", "sudoku"), daily.seed_for("2026-09-22", "sudoku"))
        self.assertNotEqual(daily.seed_for("2026-09-21", "sudoku"), daily.seed_for("2026-09-21", "snake"))
        self.assertEqual(daily.pick("2026-09-21", list(daily.POOL)), daily.pick("2026-09-21", list(daily.POOL)))
        days = {tuple(daily.pick(f"2026-10-{d:02d}", list(daily.POOL))) for d in range(1, 29)}
        self.assertGreater(len(days), 5, "the picks vary day to day")
        self.assertEqual(len(daily.pick("2026-09-21", list(daily.POOL)[:2])), 2)
        first = self.todays()["challenges"]
        self.clock.advance(days=1)
        second = self.todays()
        self.assertEqual(second["date"], "2026-09-22")
        self.assertNotEqual([c["seed"] for c in first], [c["seed"] for c in second["challenges"]])

    def test_games_switched_off_are_never_picked(self):
        keep = ["wordguess", "sudoku"]
        off = [g for g in games.GAME_IDS if g not in keep]
        self.settings({"disabled_games": off})
        for d in range(1, 8):
            self.clock.advance(days=1)
            got = {c["game"] for c in self.todays()["challenges"]}
            self.assertTrue(got <= set(keep), got)
            self.assertEqual(len(got), 2)

    def test_a_game_switched_off_after_the_pick_is_hidden_but_the_day_stays(self):
        first = self.todays()["challenges"]
        gid = first[0]["game"]
        self.settings({"disabled_games": [gid]})
        self.assertNotIn(gid, [c["game"] for c in self.todays()["challenges"]])
        self.assertIn(self.start_daily(gid).status_code, (404, 409), "a game that is off can't be started")
        self.settings({"disabled_games": []})
        self.assertEqual([c["game"] for c in self.todays()["challenges"]], [c["game"] for c in first])

    def test_one_ranked_try_then_practice(self):
        c = self.todays()["challenges"][0]
        gid = c["game"]
        r = self.start_daily(gid)
        self.assertEqual(r.status_code, 201, r.text)
        body = r.json()
        self.assertEqual((body["mode"], body["seed"], body["daily"], body["best"], body["record"]), (c["mode"], c["seed"], TODAY, None, None))
        sid = body["id"]
        # started and not finished: the ranked try is used
        r = self.start_daily(gid)
        self.assertEqual(r.status_code, 409)
        self.assertIn("already had your try", r.json()["detail"])
        self.assertEqual(self.start_daily(gid, practice=True).status_code, 201, "Practice is always open")
        self.assertEqual(self.start_daily(gid, h=MEERA).status_code, 201, "someone else has their own try")
        self.assertEqual(self.get("/api/daily").json()["challenges"][0]["played"], True)
        self.assertTrue(sid)

    def test_a_daily_score_is_kept_apart_from_the_usual_scores(self):
        c = self.todays()["challenges"][0]
        gid = c["game"]
        mode = c["mode"]
        r = self.start_daily(gid)
        self.clock.advance(seconds=60)
        res = self.post("/api/scores", {"sessionId": r.json()["id"], "score": 120 if gid != "sudoku" else 9_000, "level": 1, "seconds": 60})
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertTrue(body["saved"])
        self.assertFalse(body["personalBest"]); self.assertFalse(body["householdRecord"])
        self.assertIsNone(body["best"])
        with db.get_conn() as conn:
            row = conn.execute("SELECT mode, score FROM scores").fetchone()
        self.assertEqual(row["mode"], f"daily-{TODAY}")
        # not in the usual leaderboard, not a personal best, not a record
        lb = self.get(f"/api/leaderboard?game={gid}&mode={mode}&period=all").json()["rows"]
        self.assertEqual(lb, [])
        mine = self.get("/api/scores/mine").json()
        self.assertEqual(mine["bests"], [], "a daily isn't a personal best")
        self.assertEqual(mine["recent"][0]["modeLabel"], f"Daily challenge · {TODAY}")
        with db.get_conn() as conn:
            self.assertIsNone(scores.best(conn, gid, mode))
        # but the challenge shows it
        card = self.todays()["challenges"][0]
        self.assertEqual((card["played"], card["score"]), (True, body_score(res, gid)))
        self.assertEqual(self.todays()["daysPlayed"], 1)
        board = self.get(f"/api/daily/board?game={gid}").json()
        self.assertEqual([x["name"] for x in board["rows"]], ["Kabir Rao"])
        self.assertEqual(board["date"], TODAY)
        self.assertEqual(board["days"][0]["days"], 1)
        self.assertEqual(board["canDelete"], False)
        self.assertEqual(self.get(f"/api/daily/board?game={gid}", ASHA).json()["canDelete"], True)
        # a normal game of the same mode afterwards is that person's first: a best and a record
        again = self.play(130, seconds=60, game=gid, mode=mode if gid != "sudoku" else "easy")
        if gid != "sudoku":
            self.assertTrue(again.json()["personalBest"])

    def test_a_practice_replay_is_not_saved(self):
        gid = self.todays()["challenges"][0]["game"]
        r = self.start_daily(gid, practice=True)
        self.clock.advance(seconds=30)
        res = self.post("/api/scores", {"sessionId": r.json()["id"], "score": 100, "level": 1, "seconds": 30})
        self.assertFalse(res.json()["saved"]); self.assertEqual(res.json()["reason"], "practice")
        self.assertFalse(self.todays()["challenges"][0]["played"], "Practice doesn't use the ranked try")

    def test_a_daily_can_not_be_put_aside(self):
        gid = self.todays()["challenges"][0]["game"]
        r = self.start_daily(gid)
        self.clock.advance(seconds=10)
        res = self.post(f"/api/sessions/{r.json()['id']}/save", {"state": {}, "stateVersion": 1, "score": 0, "level": 1, "seconds": 10})
        self.assertEqual(res.status_code, 409)
        self.assertIn("daily challenge", res.json()["detail"])

    def test_only_todays_games_can_be_started(self):
        picked = {c["game"] for c in self.todays()["challenges"]}
        other = next(g for g in games.GAME_IDS if g not in picked)
        r = self.start_daily(other)
        self.assertEqual(r.status_code, 404)
        self.assertIn("isn't one of today's challenges", r.json()["detail"])
        self.assertEqual(self.post("/api/sessions", {"game": other, "mode": "x", "daily": "yes"}).status_code, 422)
        self.assertEqual(self.post("/api/sessions", {"game": other, "mode": "x", "daily": 1}).status_code, 422)

    def test_scores_stay_when_it_is_switched_off_and_on(self):
        gid = self.todays()["challenges"][0]["game"]
        r = self.start_daily(gid)
        self.clock.advance(seconds=60)
        self.post("/api/scores", {"sessionId": r.json()["id"], "score": 9_000 if gid == "sudoku" else 110, "level": 1, "seconds": 60})
        self.settings({"show_daily_challenges": False})
        self.assertEqual(self.get("/api/daily").status_code, 404)
        self.assertEqual(self.get(f"/api/daily/board?game={gid}").status_code, 404)
        with db.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM scores WHERE mode LIKE 'daily-%'").fetchone()[0], 1)
        self.settings({"show_daily_challenges": True})
        self.assertTrue(self.todays()["challenges"][0]["played"])
        self.assertEqual(len(self.get(f"/api/daily/board?game={gid}").json()["rows"]), 1)

    def test_leaderboard_switched_off_or_hidden_for_a_child(self):
        gid = self.todays()["challenges"][0]["game"]
        self.settings({"leaderboard": False})
        self.assertEqual(self.get(f"/api/daily/board?game={gid}").status_code, 403)
        self.settings({"leaderboard": True})
        self.make_child(MEERA, leaderboard="hidden")
        self.assertEqual(self.get(f"/api/daily/board?game={gid}", MEERA).status_code, 403)
        self.assertEqual(self.get("/api/daily/board?game=pinball").status_code, 404)
        self.assertEqual(self.get(f"/api/daily/board?game={gid}&date=soon").status_code, 422)

    def test_a_childs_game_limits_apply(self):
        today = self.todays()["challenges"]
        gid = today[0]["game"]
        allowed = [g for g in games.GAME_IDS if g != gid]
        self.make_child(MEERA, allowedGames=allowed)
        mine = [c["game"] for c in self.todays(MEERA)["challenges"]]
        self.assertNotIn(gid, mine)
        self.assertEqual(self.start_daily(gid, h=MEERA).status_code, 404)
        games_for_meera = {x["id"]: x for x in self.get("/api/games", MEERA).json()["games"]}
        self.assertNotIn(gid, games_for_meera, "the game isn't one of hers at all")
        self.assertIn(gid, [c["game"] for c in self.todays(KABIR)["challenges"]])

    def test_mode_helpers(self):
        self.assertTrue(daily.is_daily_mode("daily-2026-09-21"))
        self.assertFalse(daily.is_daily_mode("easy"))
        self.assertEqual(daily.day_of("daily-2026-09-21"), "2026-09-21")
        self.assertIsNone(daily.day_of("daily-soon")); self.assertIsNone(daily.day_of("easy"))
        for gid, mode in daily.POOL:
            self.assertTrue(games.exists(gid) and mode in games.mode_ids(gid), (gid, mode))


def body_score(res, gid):
    return 9_000 if gid == "sudoku" else 120


if __name__ == "__main__":
    unittest.main()
