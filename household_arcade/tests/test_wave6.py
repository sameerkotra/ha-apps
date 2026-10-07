"""Wave 6 (Slide Puzzle, Lights Out, Picture Logic, Tile Match, Code Breaker, Type Rain): the game table entries, the
honest-score limits (worked out again from the rules files' own numbers), Type Rain's stage list (the server's checks
and the game file's agree), races between two phones with each game's winner rule, saving, and the daily pool left as
it was."""
import _env  # noqa: F401  (must be first)

import json
import os
import random
import shutil
import subprocess
import unittest

from app import daily, games, level_kinds, levels, together
from app.level_common import LevelError
from base import KABIR, MEERA, ApiBase

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GAMES_DIR = os.path.join(HERE, "app", "static", "games")
NEW = {
    "slide": ("Slide Puzzle", ["three", "four", "five", "picture", "picture4"], "four"),
    "lights": ("Lights Out", ["little", "classic", "big", "climb"], "classic"),
    "nonogram": ("Picture Logic", ["five", "eight", "ten", "fifteen"], "ten"),
    "tiles": ("Tile Match", ["little", "classic", "big"], "classic"),
    "codebreak": ("Code Breaker", ["little", "classic", "norepeat", "master"], "classic"),
    "typerain": ("Type Rain", ["letters", "easy", "classic", "stages"], "classic"),
}
PUZZLES = ("slide", "lights", "nonogram", "tiles", "codebreak")
RULES = {
    "slide": {"rule": "fastest", "tiebreak": "score"},
    "lights": {"rule": "fastest", "tiebreak": "score"},
    "nonogram": {"rule": "score", "tiebreak": None},
    "tiles": {"rule": "score", "tiebreak": None},
    "codebreak": {"rule": "score", "tiebreak": None},
    "typerain": {"rule": "score", "tiebreak": "faster"},
}


def node(script: str):
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=60, check=True)
    return json.loads(out.stdout)


def constants(gid: str) -> dict:
    path = os.path.join(GAMES_DIR, f"{gid}-logic.js")
    return node(f"const L=require({json.dumps(path)});const o={{}};"
                "for (const k in L) if (/^[A-Z][A-Z_0-9]*$/.test(k) && typeof L[k] === 'number') o[k]=L[k];"
                "console.log(JSON.stringify(o))")


class TestGameTable(ApiBase):
    def test_entries(self):
        listed = {g["id"]: g for g in self.get("/api/games").json()["games"]}
        ids = list(games.GAMES)
        self.assertEqual(ids[ids.index("slide"):ids.index("slide") + 6], list(NEW))      # (waves 7 and 8 come after)
        for gid, (name, modes, default) in NEW.items():
            with self.subTest(game=gid):
                g = games.GAMES[gid]
                self.assertEqual(g["name"], name)
                self.assertEqual([m["id"] for m in g["modes"]], modes)
                self.assertEqual(g["default_mode"], default)
                self.assertEqual(g["state_version"], 1)
                self.assertTrue(g["icon"])
                self.assertNotIn("live", g)
                self.assertTrue(listed[gid]["canSave"])
                self.assertTrue(listed[gid]["race"])
                self.assertEqual(together.race_rule(gid), RULES[gid])
                self.assertTrue(any("gentle" in m["label"].lower() or "little" in m["label"].lower() for m in g["modes"]),
                                "something gentle for small children")
                if gid in PUZZLES:
                    self.assertEqual(g["level_modes"], [])
                    self.assertTrue(g["unfinished_zero"])
                    self.assertNotIn(gid, level_kinds.KINDS)
                else:
                    self.assertEqual(g["level_modes"], ["stages"])
                    self.assertTrue(g["levels_end"])
                    self.assertFalse(g.get("unfinished_zero"))
                    self.assertGreaterEqual(listed[gid]["levels"], 8)

    def test_switch_off_and_children(self):
        self.settings({"disabled_games": ["tiles"]})
        self.assertNotIn("tiles", [g["id"] for g in self.get("/api/games").json()["games"]])
        self.assertEqual(self.start("tiles", "classic").status_code, 409)
        self.make_child(MEERA, allowedGames=["slide"])
        self.assertEqual(self.start("slide", "picture", MEERA).status_code, 201)
        self.assertEqual(self.start("typerain", "letters", MEERA).status_code, 403)

    def test_daily_pool_is_unchanged(self):
        self.assertEqual([g for g, _m in daily.POOL], ["snake", "blocks", "mines", "merge", "sudoku", "wordguess"])
        self.assertFalse(set(NEW) & {g for g, _m in daily.POOL})

    def test_an_unsolved_puzzle_keeps_nothing(self):
        s = self.start("nonogram", "five").json()
        self.clock.advance(seconds=30)
        r = self.post("/api/scores", {"sessionId": s["id"], "score": 0, "level": 1, "seconds": 30})
        self.assertEqual(r.json()["reason"], "unfinished")
        s = self.start("typerain", "classic").json()
        self.clock.advance(seconds=30)
        r = self.post("/api/scores", {"sessionId": s["id"], "score": 0, "level": 1, "seconds": 30})
        self.assertTrue(r.json()["saved"], "Type Rain keeps any score")


class TestHonestLimits(unittest.TestCase):
    def test_boundaries(self):
        for gid in NEW:
            g = games.GAMES[gid]
            mode = g["default_mode"]
            with self.subTest(game=gid):
                self.assertIsNone(games.check_score(gid, mode, g["max_score"], 1, 6 * 3600))
                self.assertIsNotNone(games.check_score(gid, mode, g["max_score"] + 1, 1, 6 * 3600))
                self.assertIsNotNone(games.check_score(gid, mode, 10, 101, 60), "level ≤ 100 without a list")
        tr = games.GAMES["typerain"]
        self.assertIsNone(games.check_score("typerain", "classic", tr["base"] + 60 * tr["per_second"], 5, 60))
        self.assertIsNotNone(games.check_score("typerain", "classic", tr["base"] + 60 * tr["per_second"] + 1, 5, 60))
        self.assertIsNotNone(games.check_score("typerain", "stages", 10, 10, 60, level_count=9), "≤ the list")
        self.assertIsNone(games.check_score("typerain", "stages", 10, 9, 60, level_count=9))
        # a solved puzzle can come at once: the whole most is allowed from the first second
        for gid in PUZZLES:
            g = games.GAMES[gid]
            self.assertGreaterEqual(g["base"], g["max_score"], gid)

    @unittest.skipUnless(shutil.which("node"), "Node isn't installed")
    def test_limits_come_from_the_rules(self):
        """The puzzles' most is their top score; Type Rain's points a second at their fastest, worked out from the rules
        file's numbers, fit under `per_second` (within 1.5 ×), and a first word and a stage fit under `base`."""
        for gid in ("slide", "lights", "nonogram", "tiles"):
            self.assertEqual(games.GAMES[gid]["max_score"], constants(gid)["TOP"], gid)
        cb = constants("codebreak")
        most_rows = max(m["rows"] for m in node(f"console.log(JSON.stringify(Object.values(require({json.dumps(os.path.join(GAMES_DIR, 'codebreak-logic.js'))}).MODES)))"))
        self.assertEqual(games.GAMES["codebreak"]["max_score"], cb["ROW_POINTS"] * most_rows + cb["TIME_POINTS"])
        t = constants("typerain")
        word = t["LETTER_POINTS"] * t["MAX_LEN"] * 3
        rate = word / (t["GAP_MIN"] / 60) + t["STAGE_POINTS"] / ((5 * t["GAP_MIN"] + t["CLEAR_T"]) / 60)
        g = games.GAMES["typerain"]
        self.assertLessEqual(rate, g["per_second"])
        self.assertLessEqual(g["per_second"], rate * 1.5)
        self.assertLessEqual(word + t["STAGE_POINTS"], g["base"])


def _rand_stage(rnd: random.Random) -> dict:
    shortest = rnd.choice([1, 1, 2, 3, 3, 4, 5, 6, 8, 11])
    longest = shortest if rnd.random() < 0.3 else rnd.randint(max(1, shortest - 2), 11)
    return {"name": "Try", "words": rnd.randint(4, 41), "speed": round(rnd.uniform(0.1, 1.5), 3), "gap": rnd.randint(38, 202),
            "shortest": shortest, "longest": longest}


class TestStageList(unittest.TestCase):
    def test_kind(self):
        kind = level_kinds.KINDS["typerain"]
        self.assertEqual(kind["modes"], ["stages"])
        self.assertIn("typerain", levels.SETS)
        self.assertEqual(games.max_level("typerain", "stages", 12), 12)

    def test_cross_field_checks(self):
        base = level_kinds.KINDS["typerain"]["builtin"][2]
        for change in ({"shortest": 5, "longest": 4}, {"shortest": 2, "longest": 3}, {"shortest": 1, "longest": 4},
                       {"longest": 10, "speed": 0.6}, {"speed": 1.4}):
            with self.subTest(change=change):
                with self.assertRaises(LevelError):
                    levels.validate("typerain", dict(base, **change))
        levels.validate("typerain", dict(base, shortest=1, longest=1, speed=1.2))

    @unittest.skipUnless(shutil.which("node"), "Node isn't installed")
    def test_the_game_file_agrees_with_the_server(self):
        """Random stages (good and bad): what the server keeps the game plays, and what it turns down the game skips."""
        rnd = random.Random(6)
        kept, cands = [], []
        for _ in range(400):
            lv = _rand_stage(rnd)
            try:
                clean, _d, _p = levels.validate("typerain", lv)
                kept.append(True)
                cands.append(clean)
            except LevelError:
                kept.append(False)
                cands.append(lv)
        self.assertGreater(sum(kept), 30, "enough good ones to compare")
        self.assertGreater(len(kept) - sum(kept), 30, "and bad ones")
        path = os.path.join(GAMES_DIR, "typerain-logic.js")
        got = node(f"const L=require({json.dumps(path)});const c={json.dumps(cands)};"
                   "console.log(JSON.stringify(c.map(x=>L.usableLevels([x])!==L.LEVELS)))")
        self.assertEqual(got, kept)


class TestRaces(ApiBase):
    def race(self, game, mode):
        r = self.post("/api/matches", {"game": game, "mode": mode, "opponents": ["u_meera"], "kind": "race"}, KABIR)
        self.assertEqual(r.status_code, 201, r.text)
        mid = r.json()["id"]
        self.assertEqual(self.post(f"/api/matches/{mid}/accept", None, MEERA).status_code, 200)
        a = self.post("/api/sessions", {"game": game, "matchId": mid}, KABIR).json()
        b = self.post("/api/sessions", {"game": game, "matchId": mid}, MEERA).json()
        self.assertEqual((a["seed"], a["mode"]), (b["seed"], mode))
        return mid, a, b

    def finish(self, mid, a, b, ra, rb):
        """ra / rb: (score, seconds, won) for Kabir and Meera; returns the match."""
        self.clock.advance(seconds=max(ra[1], rb[1]) + 1)
        for s, (score, sec, won), h in ((a, ra, KABIR), (b, rb, MEERA)):
            r = self.post("/api/scores", {"sessionId": s["id"], "score": score, "level": 1, "seconds": sec, "won": won}, h)
            self.assertEqual(r.status_code, 200, r.text)
        m = self.get(f"/api/matches/{mid}").json()
        self.assertEqual(m["endReason"], "finished")       # (both finished within the 2 minutes a player may be quiet)
        return m

    def test_every_game_can_be_raced(self):
        for gid in NEW:
            with self.subTest(game=gid):
                self.assertTrue(together.race_ok(gid))
                self.assertEqual(self.get(f"/api/players?game={gid}").status_code, 200)

    def test_slide_and_lights_solved_first_wins_and_fewer_moves_break_a_tie(self):
        for gid, mode in (("slide", "four"), ("lights", "classic")):
            with self.subTest(game=gid):
                mid, a, b = self.race(gid, mode)
                m = self.finish(mid, a, b, (9_800, 60, True), (9_950, 100, True))     # Kabir faster, Meera fewer moves
                self.assertEqual((m["status"], m["winner"]), ("done", "u_kabir"))
                mid, a, b = self.race(gid, mode)
                m = self.finish(mid, a, b, (9_800, 90, True), (9_950, 90, True))      # the same second: fewer moves wins
                self.assertEqual(m["winner"], "u_meera")
                mid, a, b = self.race(gid, mode)
                m = self.finish(mid, a, b, (0, 30, False), (9_000, 100, True))        # solved beats not solved
                self.assertEqual(m["winner"], "u_meera")

    def test_picture_logic_tile_match_and_code_breaker_go_by_the_score(self):
        for gid, mode in (("nonogram", "five"), ("tiles", "little"), ("codebreak", "little")):
            with self.subTest(game=gid):
                mid, a, b = self.race(gid, mode)
                m = self.finish(mid, a, b, (9_900, 100, True), (9_850, 60, True))
                self.assertEqual(m["winner"], "u_kabir", "the higher score (fewer counted seconds / rows) wins")
                mid, a, b = self.race(gid, mode)
                m = self.finish(mid, a, b, (0, 100, False), (0, 60, False))
                self.assertIsNone(m["winner"], "neither solved: a draw")

    def test_type_rain_same_stages_and_a_tie_goes_to_the_shorter_game(self):
        mid, a, b = self.race("typerain", "stages")
        self.assertTrue(a["levels"])
        self.assertEqual(a["levels"], b["levels"])
        self.assertEqual(a["levels"][0]["name"], level_kinds.KINDS["typerain"]["builtin"][0]["name"])
        m = self.finish(mid, a, b, (1_500, 80, False), (1_500, 70, False))
        self.assertEqual(m["winner"], "u_meera")
        mid, a, b = self.race("typerain", "classic")
        m = self.finish(mid, a, b, (2_000, 80, False), (1_500, 70, False))
        self.assertEqual(m["winner"], "u_kabir")

    def test_decide(self):
        p = lambda uid, score, sec, won: {"user_id": uid, "score": score, "seconds": sec, "won": won}  # noqa: E731
        self.assertEqual(together.decide("slide", [p("a", 9_000, 50, True), p("b", 9_990, 51, True)])[0], "a")
        self.assertEqual(together.decide("slide", [p("a", 9_000, 50, True), p("b", 9_990, 50, True)])[0], "b")
        self.assertEqual(together.decide("slide", [p("a", 9_000, 50, True), p("b", 9_000, 50, True)])[0], None)
        # Sudoku's fastest rule without a tiebreak is unchanged
        games.GAMES["_t"] = {"race": {"rule": "fastest"}}
        try:
            self.assertIsNone(together.decide("_t", [p("a", 9_000, 50, True), p("b", 9_990, 50, True)])[0])
        finally:
            del games.GAMES["_t"]


class TestSaves(ApiBase):
    def test_save_and_continue_a_wave6_game(self):
        for gid, (_n, _m, default) in NEW.items():
            with self.subTest(game=gid):
                s = self.start(gid, default).json()
                self.clock.advance(seconds=40)
                state = {"mode": default, "updates": 2400}
                r = self.post(f"/api/sessions/{s['id']}/save", {"state": state, "stateVersion": 1, "score": 0, "level": 1,
                                                                "seconds": 40})
                self.assertEqual(r.status_code, 200, r.text)
                s2 = self.post("/api/sessions", {"game": gid, "resume": True}).json()
                self.assertEqual(s2["saved"]["state"], state)
                self.assertEqual(s2["mode"], default)


if __name__ == "__main__":
    unittest.main()
