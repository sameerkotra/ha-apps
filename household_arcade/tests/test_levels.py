"""Level lists and AI-made levels (SPEC §11): the checks, the built-in levels, sessions that carry their
levels, Admin → Levels, the AI settings, and the builder with a fake model."""
import _env  # noqa: F401  (must be first)

import json
import os
import shutil
import subprocess
import unittest
from unittest import mock

from app import ai_client, db, level_builder, level_data, level_kinds, levels, settings
from base import ASHA, KABIR, ApiBase

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OPEN = ["." * 20] * 20


def brick(name, rows=None):
    return {"name": name, "rows": rows or ["11111111", "22222222", "1.1.1.1.", "11111111"]}


def maze(name, foods=8, walls=None):
    return {"name": name, "foods": foods, "walls": walls or list(OPEN)}


class FakeModel:
    """Stands in for the AI model: hands out prepared answers, one per request."""

    def __init__(self, *answers, error=None):
        self.answers = list(answers)
        self.prompts = []
        self.error = error

    def __call__(self, prompt, cfg):
        self.prompts.append(prompt)
        if self.error:
            raise ai_client.AIError(self.error, "unreachable")
        text = self.answers.pop(0) if self.answers else '{"levels": []}'
        return ai_client.Reply(text=text if isinstance(text, str) else json.dumps(text), raw="",
                               input_tokens=100, output_tokens=50)


def brick_rows(i):
    """A distinct valid layout for each i (no two the same or mirror images of each other)."""
    rows = ["11111111", "11111111", "22222222"]
    bits = format(i, "08b").replace("0", ".").replace("1", "3")
    return rows + [bits if bits.count("3") else "1......."]


class TestChecks(unittest.TestCase):
    def test_builtins_pass_their_own_checks(self):
        for set_id, s in levels.SETS.items():
            prints = set()
            for data in s["builtin"]:
                clean, difficulty, fp = levels.validate(set_id, data)
                self.assertEqual(clean["name"], data["name"])
                self.assertTrue(0 <= difficulty <= 100)
                prints.add(fp)
            self.assertEqual(len(prints), len(s["builtin"]), set_id)   # no two alike

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_builtins_match_the_game_files(self):
        script = ("const B=require('./app/static/games/brick-logic.js');const S=require('./app/static/games/snake-logic.js');"
                  "console.log(JSON.stringify({brick:B.LAYOUTS,snake:S.MAZES}))")
        out = json.loads(subprocess.run(["node", "-e", script], cwd=HERE, capture_output=True, text=True, check=True).stdout)
        self.assertEqual(out["brick"], level_data.BRICK_BUILTIN)
        self.assertEqual(out["snake"], level_data.MAZE_BUILTIN)

    def test_brick_rules(self):
        good = brick("Good one")
        levels.validate("brick", good)
        # empty rows at the bottom are dropped
        clean, _, _ = levels.validate("brick", brick("Trim", good["rows"] + ["........", "........"]))
        self.assertEqual(len(clean["rows"]), 4)
        bad = [
            brick("Too few rows", ["11111111", "11111111"]),
            brick("Too many", ["11111111"] * 11),
            brick("Wide", ["111111111", "11111111", "11111111"]),
            brick("Letters", ["1111x111", "11111111", "11111111"]),
            brick("Few bricks", ["1.......", "1.......", "1.......", "........", "11111111"]),
            brick("Too hard", ["33333333"] * 10),
            {"name": "Extra", "rows": good["rows"], "colour": "red"},
            {"rows": good["rows"]},
            "not a level",
            brick("", good["rows"]), brick("x" * 41, good["rows"]), brick("<script>", good["rows"]),
            brick("Damn wall", good["rows"]),
        ]
        for b in bad:
            with self.subTest(b if isinstance(b, str) else b.get("name")):
                with self.assertRaises(levels.LevelError):
                    levels.validate("brick", b)

    def test_mirror_images_count_as_repeats(self):
        a = brick("Left", ["3.......", "33......", "333.....", "33333333"])
        b = brick("Right", [".......3", "......33", ".....333", "33333333"])
        self.assertEqual(levels.validate("brick", a)[2], levels.validate("brick", b)[2])

    def test_maze_rules(self):
        levels.validate("snake", maze("Open"))
        closed = list(OPEN)
        closed[0], closed[1], closed[2] = "###.................", "#.#.................", "###................."
        blocked = list(OPEN)
        blocked[10] = "......#............."
        crowded = ["#" * 20] * 9 + list(OPEN[9:12]) + ["#" * 20] * 8
        for bad in (maze("Closed room", 8, closed), maze("Blocked start", 8, blocked), maze("Crowded", 8, crowded),
                    maze("Few foods", 3), maze("Many foods", 31), maze("Bool foods", True),
                    maze("Short", 8, OPEN[:19]), {"name": "No foods", "walls": OPEN}):
            with self.subTest(bad.get("name")):
                with self.assertRaises(levels.LevelError):
                    levels.validate("snake", bad)


class TestSessions(ApiBase):
    def test_built_in_levels_are_seeded_once(self):
        db.init_db()
        with db.get_conn() as conn:
            self.assertEqual(levels.playable_count(conn, "brick"), 10)
            self.assertEqual(levels.playable_count(conn, "snake"), 10)
            want = sum(len(s["builtin"]) for s in levels.SETS.values())
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM levels").fetchone()[0], want)
            db.init_db()                                   # a restart adds nothing
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM levels").fetchone()[0], want)

    def test_a_session_carries_its_levels(self):
        r = self.start("brick", "classic").json()
        self.assertEqual(len(r["levels"]), 10)
        self.assertEqual(r["levels"][0]["name"], "First wall")
        self.assertFalse(r["levelsBuilding"])
        r = self.start("snake", "maze").json()
        self.assertEqual([m["name"] for m in r["levels"]][:2], ["Four posts", "Two bars"])
        self.assertIsNone(self.start("snake", "walls-normal").json()["levels"])
        self.assertEqual(self.get("/api/levels?game=snake&mode=maze").json()["levels"][9]["name"], "Labyrinth")
        self.assertEqual(self.get("/api/levels?game=snake&mode=wrap-fast").status_code, 404)

    def test_level_limit_follows_the_list(self):
        self.assertEqual(self.play(500, 120, "snake", "maze", level=10).status_code, 200)
        r = self.play(500, 120, "snake", "maze", level=11)
        self.assertEqual(r.status_code, 422)
        self.assertIn("at most 10", r.json()["detail"])
        self.assertEqual(self.play(5000, 120, "brick", "classic", level=50).status_code, 200)   # Brick Breaker repeats
        self.assertEqual(self.play(10, 60, "snake", "walls-normal", level=101).status_code, 422)

    def test_a_game_keeps_the_levels_it_started_with(self):
        sid = self.start("snake", "maze").json()["id"]
        with db.get_conn() as conn:
            levels.add(conn, "snake", maze("Extra"), model="m", status="ready")
            self.assertEqual(levels.playable_count(conn, "snake"), 11)
        self.clock.advance(seconds=200)
        r = self.post("/api/scores", {"sessionId": sid, "score": 300, "level": 11, "seconds": 200})
        self.assertEqual(r.status_code, 422)            # that game only had 10
        self.assertEqual(len(self.start("snake", "maze").json()["levels"]), 11)


class AiBase(ApiBase):
    def setUp(self):
        super().setUp()
        self.model = FakeModel()
        p = mock.patch.object(level_builder, "_generate", self.model)
        p.start()
        self.addCleanup(p.stop)

    def ai_on(self, **extra):
        self.settings(dict({"ai_levels_enabled": True, "ai_url": "http://192.168.1.10:11434", "ai_model": "tiny"}, **extra))


class TestAiSettings(AiBase):
    def test_key_is_write_only(self):
        self.settings({"ai_provider": "anthropic", "ai_api_key": "sk-secret", "ai_model": "m"})
        body = self.get("/api/admin/settings", ASHA).json()
        self.assertEqual(body["values"]["ai_api_key"], "")
        self.assertTrue(body["secretsSet"]["ai_api_key"])
        self.assertNotIn("sk-secret", json.dumps(body))
        self.assertEqual(settings.get("ai_api_key"), "sk-secret")
        self.settings({"default_look": "pixel"})       # other changes leave the key alone
        self.assertEqual(settings.get("ai_api_key"), "sk-secret")
        self.settings({"ai_api_key": ""})
        self.assertFalse(self.get("/api/admin/settings", ASHA).json()["secretsSet"]["ai_api_key"])

    def test_bad_values(self):
        for bad in ({"ai_provider": "skynet"}, {"ai_url": "ftp://x"}, {"ai_levels_batch": 0}, {"ai_levels_ahead": 9},
                    {"ai_levels_daily_limit": -1}, {"ai_max_output_tokens": 10}, {"ai_model": "a\nb"},
                    {"ai_levels_enabled": "yes"}):
            self.assertEqual(self.put("/api/admin/settings", bad, ASHA).status_code, 422, bad)

    def test_what_is_missing(self):
        self.assertIn("off", settings.ai_problem())
        self.settings({"ai_levels_enabled": True})
        self.assertIn("model", settings.ai_problem())
        self.settings({"ai_model": "m"})
        self.assertIn("address", settings.ai_problem())
        self.settings({"ai_provider": "openai"})
        self.assertIsNone(settings.ai_problem())       # OpenAI's usual address
        self.settings({"ai_provider": "anthropic"})
        self.assertIn("access key", settings.ai_problem())

    def test_connection_test(self):
        with mock.patch.object(ai_client, "list_models", return_value=["tiny", "big"]), \
                mock.patch.object(ai_client, "generate", return_value=ai_client.Reply(text='{"ok": true}', raw="")):
            r = self.post("/api/admin/ai/test", {"provider": "ollama", "url": "http://192.168.1.10:11434", "model": "tiny"}, ASHA).json()
        self.assertTrue(r["ok"])
        self.assertEqual(r["models"], ["big", "tiny"])
        with mock.patch.object(ai_client, "list_models", side_effect=ai_client.AIError("Couldn't reach Ollama", "unreachable")):
            r = self.post("/api/admin/ai/test", {"provider": "ollama", "url": "http://192.168.1.10:11434"}, ASHA).json()
        self.assertFalse(r["ok"])
        self.assertIn("Couldn't reach", r["error"])
        self.assertEqual(self.post("/api/admin/ai/test", {}, KABIR).status_code, 403)


class TestBuilder(AiBase):
    def build(self, game="brick", count=3):
        r = self.post("/api/admin/levels/build", {"game": game, "count": count}, ASHA)
        self.assertEqual(r.status_code, 202, r.text)
        level_builder.run_pending()
        return self.get(f"/api/admin/levels/builds/{r.json()['id']}", ASHA).json()

    def test_refused_until_set_up(self):
        r = self.post("/api/admin/levels/build", {"game": "brick", "count": 3}, ASHA)
        self.assertEqual(r.status_code, 409)
        self.ai_on()
        self.assertEqual(self.post("/api/admin/levels/build", {"game": "pinball", "count": 3}, ASHA).status_code, 404)
        self.assertEqual(self.post("/api/admin/levels/build", {"game": "brick", "count": 21}, ASHA).status_code, 422)
        self.assertEqual(self.post("/api/admin/levels/build", {"game": "brick", "count": 3}, KABIR).status_code, 403)

    def test_good_levels_are_added_after_the_built_in_ones(self):
        self.ai_on()
        self.model.answers = [{"levels": [brick("Moon", brick_rows(i)) for i in (1, 2, 3)]}]
        b = self.build()
        self.assertEqual((b["status"], b["made"], b["rejected"], b["requests"]), ("done", 3, 0, 1))
        self.assertEqual((b["tokensIn"], b["tokensOut"]), (100, 50))
        self.assertIn("level 11", self.model.prompts[0].lower())
        self.assertNotIn("Kabir", self.model.prompts[0])          # nothing about people
        lists = {x["id"]: x for x in self.get("/api/admin/levels", ASHA).json()["lists"]}
        made = [lv for lv in lists["brick"]["levels"] if lv["source"] == "ai"]
        self.assertEqual([lv["number"] for lv in made], [11, 12, 13])
        self.assertEqual({lv["model"] for lv in made}, {"tiny"})
        self.assertEqual(len(self.start("brick", "classic").json()["levels"]), 13)

    def test_bad_answers_are_turned_down_and_asked_again(self):
        self.ai_on()
        self.model.answers = [
            "Sorry, I can't do that.",                                         # no JSON
            {"levels": [brick("Copy", level_data.BRICK_BUILTIN[0]["rows"]),   # a repeat of level 1
                        brick("Bad", ["1"]), brick("Okay", brick_rows(7))]},
            "```json\n" + json.dumps({"levels": [brick("Fine", brick_rows(9))]}) + "\n```",
        ]
        b = self.build(count=2)
        self.assertEqual((b["status"], b["made"], b["rejected"], b["requests"]), ("done", 2, 3, 3))
        self.assertTrue(any("already in the list" in line for line in b["log"]))

    def test_a_model_that_never_gets_it_right_stops(self):
        self.ai_on()
        self.model.answers = ["nope"] * 10
        b = self.build()
        self.assertEqual((b["status"], b["made"], b["requests"]), ("failed", 0, level_builder.MAX_FAILED_REQUESTS))
        self.assertIn("usable", b["error"])

    def test_provider_errors_end_the_build(self):
        self.ai_on()
        self.model.error = "Couldn't reach Ollama at http://192.168.1.10:11434: refused"
        b = self.build()
        self.assertEqual(b["status"], "failed")
        self.assertIn("Couldn't reach", b["error"])

    def test_daily_limit(self):
        self.ai_on(ai_levels_daily_limit=2)
        self.model.answers = [{"levels": [brick("A", brick_rows(1)), brick("B", brick_rows(2))]}]
        b = self.build(count=5)
        self.assertEqual(b["count"], 2)                 # cut to what's left today
        self.assertEqual(b["made"], 2)
        r = self.post("/api/admin/levels/build", {"game": "snake", "count": 1}, ASHA)
        self.assertEqual(r.status_code, 429)
        self.clock.advance(days=1)
        self.assertEqual(self.post("/api/admin/levels/build", {"game": "snake", "count": 1}, ASHA).status_code, 202)

    def test_one_build_at_a_time_per_game(self):
        self.ai_on()
        self.assertEqual(self.post("/api/admin/levels/build", {"game": "brick", "count": 1}, ASHA).status_code, 202)
        r = self.post("/api/admin/levels/build", {"game": "brick", "count": 1}, ASHA)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.post("/api/admin/levels/build", {"game": "snake", "count": 1}, ASHA).status_code, 202)

    def test_mazes(self):
        self.ai_on()
        walls = list(OPEN)
        walls[3] = "....##########......"
        self.model.answers = [{"levels": [maze("Bar", 12, walls), maze("Open floor", 11)]}]
        b = self.build("snake", 2)
        self.assertEqual(b["made"], 2)
        self.assertIn("20 x 20", self.model.prompts[0])
        self.assertEqual(len(self.start("snake", "maze").json()["levels"]), 12)

    def test_review_retire_and_restore(self):
        self.ai_on(ai_levels_review=True)
        self.model.answers = [{"levels": [brick("Check me", brick_rows(5))]}]
        self.build(count=1)
        brick_list = [x for x in self.get("/api/admin/levels", ASHA).json()["lists"] if x["id"] == "brick"][0]
        lv = brick_list["levels"][-1]
        self.assertEqual(lv["status"], "waiting")
        self.assertEqual(len(self.start("brick", "classic").json()["levels"]), 10)       # not played yet
        self.assertEqual(self.post(f"/api/admin/levels/{lv['id']}/approve", {}, KABIR).status_code, 403)
        self.assertEqual(self.post(f"/api/admin/levels/{lv['id']}/approve", {}, ASHA).json()["status"], "ready")
        self.assertEqual(len(self.start("brick", "classic").json()["levels"]), 11)
        first = brick_list["levels"][0]
        self.assertEqual(self.post(f"/api/admin/levels/{first['id']}/retire", {}, ASHA).json()["status"], "retired")
        names = [x["name"] for x in self.start("brick", "classic").json()["levels"]]
        self.assertNotIn("First wall", names)
        self.post(f"/api/admin/levels/{first['id']}/restore", {}, ASHA)
        self.assertEqual(self.start("brick", "classic").json()["levels"][0]["name"], "First wall")

    def test_the_last_level_stays(self):
        with db.get_conn() as conn:
            ids = [r["id"] for r in conn.execute("SELECT id FROM levels WHERE game = 'snake' ORDER BY number")]
        for lid in ids[:-1]:
            self.assertEqual(self.post(f"/api/admin/levels/{lid}/retire", {}, ASHA).status_code, 200)
        self.assertEqual(self.post(f"/api/admin/levels/{ids[-1]}/retire", {}, ASHA).status_code, 409)


class TestReorderAndDelete(AiBase):
    def ids(self, game="brick"):
        with db.get_conn() as conn:
            return [(r["id"], r["name"], r["number"]) for r in conn.execute(
                "SELECT id, name, number FROM levels WHERE game = ? ORDER BY number", (game,))]

    def test_move(self):
        lv = self.ids()
        r = self.post(f"/api/admin/levels/{lv[9][0]}/move", {"to": 1}, ASHA)       # Finale first
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["number"], 1)
        names = [n for _, n, _ in self.ids()]
        self.assertEqual(names[:3], ["Finale", "First wall", "Staircase"])
        self.assertEqual([n for _, _, n in self.ids()], list(range(1, 11)))
        self.assertEqual(self.start("brick", "classic").json()["levels"][0]["name"], "Finale")
        self.post(f"/api/admin/levels/{lv[0][0]}/move", {"to": 10}, ASHA)          # First wall last
        self.assertEqual(self.ids()[-1][1], "First wall")
        for bad in ({"to": 0}, {"to": 11}, {"to": "2"}, {}):
            self.assertEqual(self.post(f"/api/admin/levels/{lv[0][0]}/move", bad, ASHA).status_code, 422, bad)
        self.assertEqual(self.post(f"/api/admin/levels/{lv[0][0]}/move", {"to": 2}, KABIR).status_code, 403)

    def test_built_in_levels_keep_their_place_after_a_restart(self):
        lv = self.ids()
        self.post(f"/api/admin/levels/{lv[9][0]}/move", {"to": 1}, ASHA)
        db.init_db()
        self.assertEqual([n for _, n, _ in self.ids()][:2], ["Finale", "First wall"])
        self.assertEqual(len(self.ids()), 10)

    def test_delete(self):
        self.ai_on()
        self.model.answers = [{"levels": [brick("Gone soon", brick_rows(3)), brick("Stays", brick_rows(4))]}]
        self.post("/api/admin/levels/build", {"game": "brick", "count": 2}, ASHA)
        level_builder.run_pending()
        lv = self.ids()
        made = {n: i for i, n, _ in lv}
        self.assertEqual(self.delete(f"/api/admin/levels/{lv[0][0]}", ASHA).status_code, 409)   # built-in
        self.assertEqual(self.delete(f"/api/admin/levels/{made['Gone soon']}", KABIR).status_code, 403)
        self.assertEqual(self.delete(f"/api/admin/levels/{made['Gone soon']}", ASHA).status_code, 200)
        after = self.ids()
        self.assertEqual([n for _, n, _ in after][-1], "Stays")
        self.assertEqual([num for _, _, num in after], list(range(1, 12)))
        self.assertEqual(self.delete(f"/api/admin/levels/{made['Gone soon']}", ASHA).status_code, 404)


class TestAutomatic(AiBase):
    def test_nearing_the_end_builds_the_next_levels(self):
        self.ai_on(ai_levels_batch=2)
        r = self.play(1000, 120, "brick", "classic", level=7)
        self.assertFalse(r.json()["levelsComing"])      # not near the end yet
        r = self.play(2000, 120, "brick", "classic", level=9)   # 10 levels, 2 ahead: from level 9
        self.assertTrue(r.json()["levelsComing"])
        with db.get_conn() as conn:
            builds = conn.execute("SELECT * FROM level_builds").fetchall()
        self.assertEqual([(b["game"], b["requested_by"], b["count"]) for b in builds], [("brick", "auto", 2)])
        self.play(2000, 120, "brick", "powerups", level=10)    # no second build while one waits
        with db.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM level_builds").fetchone()[0], 1)
        self.model.answers = [{"levels": [brick("Next", brick_rows(11)), brick("After", brick_rows(12))]}]
        level_builder.run_pending()
        self.assertEqual(len(self.start("brick", "classic").json()["levels"]), 12)

    def test_heartbeats_report_the_level(self):
        self.ai_on()
        sid = self.start("snake", "maze").json()["id"]
        self.clock.advance(seconds=30)
        self.post(f"/api/sessions/{sid}/beat", {"activeSeconds": 30, "level": 9})
        self.assertTrue(level_builder.building("snake"))

    def test_nothing_when_off_or_manual(self):
        self.play(2000, 120, "brick", "classic", level=10)
        self.ai_on(ai_levels_auto=False)
        self.play(2000, 120, "brick", "classic", level=10)
        with db.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM level_builds").fetchone()[0], 0)

    def test_restart_fails_unfinished_builds(self):
        self.ai_on()
        self.post("/api/admin/levels/build", {"game": "brick", "count": 1}, ASHA)
        db.init_db()
        with db.get_conn() as conn:
            row = conn.execute("SELECT * FROM level_builds").fetchone()
        self.assertEqual(row["status"], "failed")
        self.assertIn("restarted", row["error"])


if __name__ == "__main__":
    unittest.main()


class TestUsage(AiBase):
    def build(self, game="brick", count=3):
        r = self.post("/api/admin/levels/build", {"game": game, "count": count}, ASHA)
        self.assertEqual(r.status_code, 202, r.text)
        level_builder.run_pending()
        return r.json()["id"]

    def test_every_request_is_written_down_and_added_up(self):
        self.ai_on(ai_price_in=3.0, ai_price_out=15.0)
        self.model.answers = [{"levels": [brick("Moon", brick_rows(i)) for i in (1, 2)]}]
        self.build(count=2)
        flap = level_kinds.KINDS["flap"]
        self.model.answers = [{"levels": [dict(flap["example"], name="Sky road"), {"name": "Bad", "gates": 1}]}]
        self.build("flap", count=1)
        with mock.patch.object(ai_client, "list_models", return_value=["tiny"]), \
                mock.patch.object(ai_client, "generate", return_value=ai_client.Reply(text='{"ok": true}', raw="",
                                                                                      input_tokens=10, output_tokens=5)):
            self.post("/api/admin/ai/test", {"provider": "ollama", "url": "http://192.168.1.10:11434", "model": "tiny"}, ASHA)
        u = self.get("/api/admin/ai/usage", ASHA).json()
        t = u["totals"]["today"]
        self.assertEqual((t["calls"], t["failed"], t["levels"]), (3, 0, 3))
        self.assertEqual((t["tokensIn"], t["tokensOut"]), (210, 105))
        self.assertAlmostEqual(t["cost"], 210 / 1e6 * 3 + 105 / 1e6 * 15, places=4)
        self.assertEqual(u["totals"]["all"]["calls"], 3)
        self.assertEqual(len(u["byDay"]), 30)
        self.assertEqual(u["byDay"][-1]["calls"], 3)
        self.assertEqual({g["game"] for g in u["byGame"]}, {"brick", "flap"})
        self.assertEqual([m["model"] for m in u["byModel"]], ["tiny"])
        self.assertEqual(u["recent"][0]["purpose"], "test")
        self.assertEqual(u["recent"][1]["name"], "Flap")
        self.assertEqual(u["recent"][1]["made"], 1)
        self.assertEqual(self.get("/api/admin/ai/usage", KABIR).status_code, 403)

    def test_a_failed_request_counts(self):
        self.ai_on()
        self.model.error = "The model is busy"
        self.build()
        u = self.get("/api/admin/ai/usage?days=7", ASHA).json()
        self.assertEqual((u["totals"]["today"]["calls"], u["totals"]["today"]["failed"]), (1, 1))
        self.assertIsNone(u["totals"]["today"]["cost"])       # no prices set
        self.assertIn("busy", u["recent"][0]["error"])
        self.assertEqual(len(u["byDay"]), 7)
