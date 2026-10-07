"""What Arcade answers the Household Assistant (app/tools.py; HOUSEHOLD_ASSISTANT_SPEC §4.2, §7.2).

- arcade.scores: one game's leaderboard, or each game's record holder; only while the leaderboard is on; not for
  children; the person's switch and the admin's;
- arcade.mine: the person's own bests (children too);
- Arcade joins the bus in its lifespan and offers both kinds in its hello; the catalogue fits.

All people here are invented."""
from datetime import datetime, timedelta, timezone

from base import ASHA, KABIR, MEERA, ApiBase

from app import app_messages, db, tools
from app.common import app_bus


def call(tool, uid, **args):
    now = datetime.now(timezone.utc)
    env = {"id": app_bus.new_ulid(now), "v": 1, "from": "household_assistant", "to": "household_arcade",
           "kind": "assist.tool.call", "kv": 1, "reply_to": None, "ref": None, "sent": now.isoformat(),
           "expires": (now + timedelta(seconds=20)).isoformat(),
           "data": {"tool": tool, "args": args, "requested_by": uid, "question": "q1"}}
    with db.get_conn() as conn:
        try:
            return tools.tools.on_call(app_bus.Message(env), conn)
        except app_bus.Nack as n:
            return n


class ToolTests(ApiBase):
    def setUp(self):
        super().setUp()
        self.play(120, h=KABIR)
        self.play(300, h=MEERA)
        self.play(90, h=MEERA)

    def assertNack(self, res, reason, detail):
        self.assertIsInstance(res, app_bus.Nack, res)
        self.assertEqual((res.reason, res.detail), (reason, detail))

    def test_one_games_leaderboard(self):
        for name in ("snake", "Snake", " SNAKE "):
            res = call("arcade.scores", "u_kabir", game=name)
            self.assertIn("Meera Rao leads with 300", res["text"])
            self.assertEqual([(r["rank"], r["name"], r["score"]) for r in res["items"]],
                             [(1, "Meera Rao", 300), (2, "Kabir Rao", 120)])
            self.assertEqual(res["links"][0]["target"], "/leaderboard")
        self.assertNack(call("arcade.scores", "u_kabir", game="chess pro"), "not_found", "game")

    def test_records_across_games(self):
        res = call("arcade.scores", "u_kabir")
        self.assertEqual(res["items"], [{"game": "Snake", "record": 300, "by": "Meera Rao", "mode": res["items"][0]["mode"]}])
        self.assertIn("Most records: Meera Rao (1)", res["text"])
        res = call("arcade.scores", "u_kabir", period="month")
        self.assertIn("Ask about one game", res["text"])

    def test_leaderboard_off_and_game_off(self):
        self.settings({"leaderboard": False})
        self.assertEqual(call("arcade.scores", "u_kabir", game="snake")["text"],
                         "The leaderboard is switched off in Household Arcade.")
        self.settings({"leaderboard": True, "disabled_games": ["snake"]})
        self.assertNack(call("arcade.scores", "u_kabir", game="snake"), "not_found", "game")
        self.assertEqual(call("arcade.scores", "u_kabir")["items"], [])

    def test_mine(self):
        res = call("arcade.mine", "u_meera")
        self.assertIn("Meera Rao has played 2 games", res["text"])
        self.assertEqual(res["items"][0]["best"], 300)
        self.assertEqual(res["links"][0]["target"], "/scores")
        res = call("arcade.mine", "u_asha")
        self.assertIn("hasn't finished a game", res["text"])

    def test_children_only_get_their_own(self):
        self.make_child(KABIR)
        self.assertNack(call("arcade.scores", "u_kabir"), "not_allowed", "child")
        self.assertIn("Kabir Rao has played 1 games", call("arcade.mine", "u_kabir")["text"])

    def test_switches(self):
        self.settings({"assistant_answers": False})
        self.assertNack(call("arcade.mine", "u_kabir"), "not_allowed", "off")
        self.settings({"assistant_answers": True})
        me = self.get("/api/me").json()
        self.assertTrue(me["assistant"])
        self.assertTrue(me["prefs"]["assistantOk"])
        r = self.put("/api/prefs", {"assistantOk": False})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(r.json()["assistantOk"])
        self.assertNack(call("arcade.mine", "u_kabir"), "not_allowed", "person_off")
        self.assertIn("text", call("arcade.mine", "u_meera"))
        self.assertEqual(self.put("/api/prefs", {"assistantOk": "no"}).status_code, 422)
        self.assertNack(call("arcade.mine", "u_nobody"), "not_allowed", "no_access")

    def test_catalogue_and_bus(self):
        tools.tools.check()
        self.assertEqual([t["name"] for t in tools.tools.spec()], ["arcade.scores", "arcade.mine"])
        self.assertTrue(app_bus.default.started)                 # the lifespan started it
        self.assertEqual(app_bus.default.slug, app_messages.SLUG)
        self.assertEqual(app_bus.default.can, ["assist.tool.call", "assist.tools.list"])
