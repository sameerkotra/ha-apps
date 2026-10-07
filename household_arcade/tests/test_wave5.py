"""Wave 5 (Bubble Pop, Gem Swap, Tower Stack, Runner, Lander, City Defense): the game table entries, the honest-score
limits (worked out again here from the rules files' own numbers), the level lists (the server's checks and the game
files' checks agree on every level), races between two phones, saving, and the daily pool left as it was."""
import _env  # noqa: F401  (must be first)

import json
import math
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
    "bubbles": ("Bubble Pop", ["classic", "relaxed", "puzzle", "endless"], "classic", "puzzle"),
    "gems": ("Gem Swap", ["timed", "moves", "zen"], "timed", "moves"),
    "stack": ("Tower Stack", ["classic", "fast", "easy", "towers"], "classic", "towers"),
    "runner": ("Runner", ["classic", "easy", "courses"], "classic", "courses"),
    "lander": ("Lander", ["classic", "easy", "levels"], "classic", "levels"),
    "defense": ("City Defense", ["classic", "easy", "waves"], "classic", "waves"),
}
LOGIC = {"bubbles": "BubblesLogic", "gems": "GemsLogic", "stack": "StackLogic", "runner": "RunnerLogic",
         "lander": "LanderLogic", "defense": "DefenseLogic"}


def node(script: str):
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=60, check=True)
    return json.loads(out.stdout)


def constants(gid: str) -> dict:
    """The rules file's numbers (every upper-case member of its exports)."""
    path = os.path.join(GAMES_DIR, f"{gid}-logic.js")
    return node(f"const L=require({json.dumps(path)});const o={{}};"
                "for (const k in L) if (/^[A-Z][A-Z_0-9]*$/.test(k) && typeof L[k] === 'number') o[k]=L[k];"
                "console.log(JSON.stringify(o))")


class TestGameTable(ApiBase):
    def test_entries(self):
        listed = {g["id"]: g for g in self.get("/api/games").json()["games"]}
        at = list(games.GAMES).index("bubbles")
        self.assertEqual(list(games.GAMES)[at:at + 6], list(NEW))
        for gid, (name, modes, default, list_mode) in NEW.items():
            with self.subTest(game=gid):
                g = games.GAMES[gid]
                self.assertEqual(g["name"], name)
                self.assertEqual([m["id"] for m in g["modes"]], modes)
                self.assertEqual(g["default_mode"], default)
                self.assertEqual(g["level_modes"], [list_mode])
                self.assertTrue(g["levels_end"])
                self.assertEqual(g["state_version"], 1)
                self.assertEqual(g["race"], {"rule": "score"})
                self.assertTrue(g["icon"])
                self.assertNotIn("live", g)
                self.assertTrue(listed[gid]["canSave"])
                self.assertTrue(listed[gid]["race"])
                self.assertEqual(listed[gid]["levelModes"], [games.mode_label(gid, list_mode)])
                self.assertGreaterEqual(listed[gid]["levels"], 8)

    def test_switch_off_and_children(self):
        self.settings({"disabled_games": ["lander"]})
        self.assertNotIn("lander", [g["id"] for g in self.get("/api/games").json()["games"]])
        self.assertEqual(self.start("lander", "classic").status_code, 409)
        self.make_child(MEERA, allowedGames=["stack"])
        self.assertEqual(self.start("stack", "classic", MEERA).status_code, 201)
        self.assertEqual(self.start("gems", "timed", MEERA).status_code, 403)

    def test_daily_pool_is_unchanged(self):
        self.assertEqual([g for g, _m in daily.POOL], ["snake", "blocks", "mines", "merge", "sudoku", "wordguess"])
        self.assertFalse(set(NEW) & {g for g, _m in daily.POOL})


class TestHonestLimits(unittest.TestCase):
    def test_boundaries(self):
        for gid in NEW:
            g = games.GAMES[gid]
            mode = g["default_mode"]
            with self.subTest(game=gid):
                self.assertIsNone(games.check_score(gid, mode, g["base"] + 60 * g["per_second"], 5, 60))
                self.assertIsNotNone(games.check_score(gid, mode, g["base"] + 60 * g["per_second"] + 1, 5, 60))
                self.assertIsNotNone(games.check_score(gid, mode, g["max_score"] + 1, 5, 6 * 3600))
                self.assertIsNone(games.check_score(gid, mode, g["max_score"], 5, 6 * 3600))
                self.assertIsNotNone(games.check_score(gid, mode, 10, 101, 60), "level ≤ 100 without a list")
                self.assertIsNotNone(games.check_score(gid, g["level_modes"][0], 10, 10, 60, level_count=9), "≤ the list")
                self.assertIsNone(games.check_score(gid, g["level_modes"][0], 10, 9, 60, level_count=9))

    @unittest.skipUnless(shutil.which("node"), "Node isn't installed")
    def test_rates_come_from_the_rules(self):
        """Each game's points a second at their fastest, worked out from the rules file's numbers, fit under its
        `per_second` (and not wildly: within 1.5 ×), and one level's or wave's worth fits under `base`."""
        c = {gid: constants(gid) for gid in NEW}
        b = c["bubbles"]
        shot = b["RELOAD"] + 3
        bubbles = (64 * b["DROP_POINTS"] + b["CLEAR_POINTS"]) / ((b["CLEAR_T"] + shot) / 60) + b["DROP_POINTS"] / (shot / 60) \
            + 8 * b["DROP_POINTS"] / (4 * shot / 60)
        gm = c["gems"]
        gems = 64 * (gm["GEM_POINTS"] * gm["MAX_CHAIN"] + gm["STAR_BONUS"] / 5) / (gm["CLEAR_T"] + 8 * gm["CELL"] / gm["FALL_V"]) * 60
        st = c["stack"]
        stack = (st["FLOOR_POINTS"] + st["PERFECT_POINTS"] * st["PERFECT_CAP"]) / (st["SPAWN"] / 60) \
            + st["TOWER_POINTS"] / ((8 * st["SPAWN"] + st["CLEAR_T"]) / 60)
        r = c["runner"]
        runner = r["VMAX"] / 10 * 60 + 5 * r["COIN_POINTS"] * r["VMAX"] / r["SEG"] * 60 \
            + r["COURSE_POINTS"] / ((14 * r["SEG"] / r["VMAX"] + r["CLEAR_T"]) / 60)
        ln = c["lander"]
        fall = math.sqrt(2 * (ln["FLOOR"] - 150 - (ln["START_Y"] + ln["FOOT"])) / 0.04)
        lander = (5 * ln["PAD_POINTS"] + 1000 / 2) / ((fall + ln["LAND_T"]) / 60)
        d = c["defense"]
        wave = (30 + 2 * 8 + 4) * d["MISSILE_POINTS"] + 4 * d["FLIER_POINTS"] + 3 * 15 * d["AMMO_POINTS"] + 6 * d["CITY_POINTS"]
        defense = wave / ((d["START_T"] + 240 + 60 + d["BONUS_T"]) / 60)
        derived = {"bubbles": bubbles, "gems": gems, "stack": stack, "runner": runner, "lander": lander, "defense": defense}
        for gid, rate in derived.items():
            with self.subTest(game=gid, rate=round(rate)):
                self.assertLessEqual(rate, games.GAMES[gid]["per_second"])
                self.assertLessEqual(games.GAMES[gid]["per_second"], rate * 1.5)
        self.assertLessEqual(wave, games.GAMES["defense"]["base"])
        self.assertLessEqual(5 * ln["PAD_POINTS"] + 500, games.GAMES["lander"]["base"])
        self.assertLessEqual(64 * b["DROP_POINTS"] + b["CLEAR_POINTS"], games.GAMES["bubbles"]["base"])
        self.assertLessEqual(40 * gm["MOVE_BONUS"], games.GAMES["gems"]["base"])


def _rand_level(gid: str, rnd: random.Random) -> dict:
    """A level of each kind with every field in or just outside its range (about half are good)."""
    if gid == "bubbles":
        rows = []
        for _ in range(rnd.randint(2, 9)):
            rows.append("".join(rnd.choice("..123456" if rnd.random() < 0.3 else "1234") for _ in range(8)))
        return {"name": "Try", "rows": rows, "colours": rnd.randint(2, 7), "drop": rnd.randint(3, 13)}
    if gid == "gems":
        locks = ["".join("L" if rnd.random() < rnd.choice([0, 0.05, 0.2]) else "." for _ in range(8)) for _ in range(8)]
        moves = rnd.randint(9, 41)
        return {"name": "Try", "moves": moves, "goal": rnd.randint(250, moves * 95), "kinds": rnd.randint(3, 7), "locks": locks}
    if gid == "stack":
        return {"name": "Try", "floors": rnd.randint(7, 40), "width": rnd.randint(38, 162), "speed": round(rnd.uniform(0.9, 3.6), 2),
                "ramp": round(rnd.uniform(0, 0.07), 3), "grow": rnd.random() < 0.5}
    if gid == "runner":
        pattern, gap = "", 0
        for _ in range(rnd.randint(9, 60)):
            if gap <= 0 and rnd.random() < 0.5:
                pattern += rnd.choice("bBphf")
                gap = rnd.randint(2, 8)
            else:
                pattern += rnd.choice("......co")
                gap -= 1
        return {"name": "Try", "speed": round(rnd.uniform(2.9, 7.1), 2), "ramp": round(rnd.uniform(0, 1.1), 2), "pattern": pattern}
    if gid == "lander":
        h = [rnd.randint(0, 150)]
        for _ in range(12):
            h.append(h[-1] if rnd.random() < 0.3 else rnd.randint(0, 152))
        pads = "".join(rnd.choice("..12345") if rnd.random() < 0.4 else "." for _ in range(12))
        for i, ch in enumerate(pads):
            if ch != "." and rnd.random() < 0.8:
                h[i + 1] = h[i]
        return {"name": "Try", "heights": h, "pads": pads, "start": rnd.randint(8, 232), "drift": round(rnd.uniform(-1.1, 1.1), 2),
                "gravity": round(rnd.uniform(0.009, 0.041), 3), "wind": round(rnd.uniform(-0.011, 0.011), 3),
                "fuel": rnd.randint(140, 1010)}
    return {"name": "Try", "missiles": rnd.randint(3, 31), "speed": round(rnd.uniform(0.25, 1.45), 2), "splits": rnd.randint(0, 9),
            "fliers": rnd.randint(0, 5), "ammo": rnd.randint(5, 16)}


class TestLevelLists(unittest.TestCase):
    def test_kinds(self):
        for gid, (_n, _m, _d, list_mode) in NEW.items():
            with self.subTest(game=gid):
                self.assertIn(gid, level_kinds.KINDS)
                self.assertEqual(level_kinds.KINDS[gid]["modes"], [list_mode])
                self.assertIn(gid, levels.SETS)
                self.assertEqual(games.max_level(gid, list_mode, 12), 12)

    def test_cross_field_checks(self):
        bad = {
            "bubbles": [{"rows": ["11111111", "........", "22222222"]}, {"rows": ["11", "22"]}, {"colours": 3, "rows": ["44444444", "11111111", "22222222"]}],
            "gems": [{"goal": 2000, "moves": 20}, {"moves": 10, "locks": ["LLLLLL..", *["........"] * 7]}],
            "stack": [{"floors": 60, "speed": 3, "ramp": 0.06}],
            "runner": [{"pattern": "..b.b..b......"}, {"pattern": "..c..c..b......."}],
            "lander": [{"pads": "..1........."}, {"pads": "............"}, {"gravity": 0.04, "fuel": 200}],
            "defense": [{"missiles": 30, "splits": 8, "fliers": 4, "ammo": 6}, {"missiles": 4, "splits": 5}, {"speed": 1.2, "missiles": 21}],
        }
        for gid, changes in bad.items():
            base = level_kinds.KINDS[gid]["builtin"][0]
            for change in changes:
                with self.subTest(game=gid, change=change):
                    with self.assertRaises(LevelError):
                        levels.validate(gid, dict(base, **change))

    @unittest.skipUnless(shutil.which("node"), "Node isn't installed")
    def test_the_game_files_agree_with_the_server(self):
        """Random levels (good and bad): what the server keeps the game plays, and what it turns down the game skips."""
        rnd = random.Random(5)
        for gid in NEW:
            with self.subTest(game=gid):
                kept, cands = [], []
                for _ in range(300):
                    lv = _rand_level(gid, rnd)
                    try:
                        clean, _d, _p = levels.validate(gid, lv)
                        kept.append(True)
                        cands.append(clean)
                    except LevelError:
                        kept.append(False)
                        cands.append(lv)
                self.assertGreater(sum(kept), 20, "enough good ones to compare")
                self.assertGreater(len(kept) - sum(kept), 20, "and bad ones")
                path = os.path.join(GAMES_DIR, f"{gid}-logic.js")
                got = node(f"const L=require({json.dumps(path)});const c=JSON.parse(process.argv[1]);"
                           "console.log(JSON.stringify(c.map(x=>L.usableLevels([x])!==L.LEVELS)))".replace(
                               "process.argv[1]", json.dumps(json.dumps(cands))))
                self.assertEqual(got, kept)


class TestRaces(ApiBase):
    def race(self, game, mode):
        r = self.post("/api/matches", {"game": game, "mode": mode, "opponents": ["u_meera"], "kind": "race"}, KABIR)
        self.assertEqual(r.status_code, 201, r.text)
        mid = r.json()["id"]
        self.assertEqual(self.post(f"/api/matches/{mid}/accept", None, MEERA).status_code, 200)
        return mid

    def test_every_game_is_raced_by_score(self):
        for gid in NEW:
            self.assertEqual(together.race_rule(gid), {"rule": "score", "tiebreak": None})
            self.assertTrue(together.race_ok(gid))
            self.assertIn(gid, together.RACE_DEFAULT)
            self.assertEqual(self.get(f"/api/players?game={gid}").status_code, 200)

    def test_a_race_in_a_level_mode_shares_seed_and_levels_and_the_higher_score_wins(self):
        for i, (gid, (_n, _m, _d, list_mode)) in enumerate(NEW.items()):
            with self.subTest(game=gid):
                mid = self.race(gid, list_mode)
                a = self.post("/api/sessions", {"game": gid, "matchId": mid}, KABIR).json()
                b = self.post("/api/sessions", {"game": gid, "matchId": mid}, MEERA).json()
                self.assertEqual((a["seed"], a["mode"]), (b["seed"], list_mode))
                self.assertTrue(a["levels"])
                self.assertEqual(a["levels"], b["levels"])
                self.assertEqual(a["levels"][0]["name"], level_kinds.KINDS[gid]["builtin"][0]["name"])
                self.clock.advance(seconds=90)
                ra = self.post("/api/scores", {"sessionId": a["id"], "score": 300 + i, "level": 2, "seconds": 90}, KABIR)
                rb = self.post("/api/scores", {"sessionId": b["id"], "score": 500 + i, "level": 3, "seconds": 90}, MEERA)
                self.assertEqual((ra.status_code, rb.status_code), (200, 200), ra.text + rb.text)
                m = self.get(f"/api/matches/{mid}").json()
                self.assertEqual((m["status"], m["winner"]), ("done", "u_meera"))

    def test_an_impossible_score_loses(self):
        mid = self.race("lander", "classic")
        a = self.post("/api/sessions", {"game": "lander", "matchId": mid}, KABIR).json()
        b = self.post("/api/sessions", {"game": "lander", "matchId": mid}, MEERA).json()
        self.clock.advance(seconds=10)
        self.post("/api/scores", {"sessionId": a["id"], "score": 50_000, "level": 2, "seconds": 10}, KABIR)
        self.post("/api/scores", {"sessionId": b["id"], "score": 400, "level": 1, "seconds": 10}, MEERA)
        self.assertEqual(self.get(f"/api/matches/{mid}").json()["winner"], "u_meera")


class TestSaves(ApiBase):
    def test_save_and_continue_a_wave5_game(self):
        for gid in NEW:
            with self.subTest(game=gid):
                s = self.start(gid, NEW[gid][3]).json()
                self.clock.advance(seconds=40)
                state = {"mode": NEW[gid][3], "score": 120}
                r = self.post(f"/api/sessions/{s['id']}/save", {"state": state, "stateVersion": 1, "score": 120, "level": 2,
                                                                "seconds": 40})
                self.assertEqual(r.status_code, 200, r.text)
                s2 = self.post("/api/sessions", {"game": gid, "resume": True}).json()
                self.assertEqual(s2["saved"]["state"], state)
                self.assertEqual(s2["levels"], s["levels"])
                s3 = self.start(gid, NEW[gid][2]).json()
                self.clock.advance(seconds=10)
                self.assertEqual(self.post(f"/api/sessions/{s3['id']}/save", {"state": state, "stateVersion": 2, "score": 1,
                                                                             "level": 1, "seconds": 5}).status_code, 422)

if __name__ == "__main__":
    unittest.main()
