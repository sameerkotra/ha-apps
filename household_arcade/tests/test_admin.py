"""Admin: App settings (validation, applied without a restart), game switches,
Brick Breaker power-ups, Users (switch off, child mark) and notify services.
Invented people only."""
import _env  # noqa: F401  (must be first)

import json
import os
import unittest

from app import config, db, settings
from base import ASHA, KABIR, MEERA, ApiBase


class TestAppSettings(ApiBase):
    def test_defaults(self):
        body = self.get("/api/admin/settings", ASHA).json()
        self.assertEqual(body["values"], {
            "disabled_games": [], "default_look": "modern", "brick_powerups": True, "leaderboard": True,
            "school_days": [1, 2, 3, 4, 5], "limit_warnings": False, "limit_warning_admins": [],
            "notify_records": False, "notify_invites": True, "notify_turns": True, "assistant_answers": True, "ha_sensors": False, "keep_scores_years": 0,
            "show_daily_challenges": False, "sudoku_hints": 3,
            "ai_levels_enabled": False, "ai_provider": "ollama", "ai_url": "", "ai_model": "", "ai_api_key": "",
            "ai_max_output_tokens": 4000, "ai_levels_auto": True, "ai_levels_ahead": 2, "ai_levels_batch": 5,
            "ai_levels_daily_limit": 20, "ai_levels_review": False, "ai_price_in": 0.0, "ai_price_out": 0.0})
        self.assertEqual(body["secretsSet"], {"ai_api_key": False})
        self.assertEqual(body["values"], body["defaults"])
        # meta describes each field for the shared page (common/static/settings.js); none needs a restart
        self.assertEqual(set(body["meta"]), set(body["values"]))
        self.assertTrue(all(m["restartRequired"] is False and m["label"] for m in body["meta"].values()))
        self.assertEqual(body["meta"]["ai_api_key"]["kind"], "secret")
        self.assertEqual([g["id"] for g in body["groups"]], ["games", "looks", "children", "ha", "assistant", "ai"])
        self.assertEqual([g["id"] for g in body["games"]], ["snake", "brick", "blocks", "duel", "racer", "flap", "mines", "merge", "colours", "cards", "mole", "numbers", "tanks", "invaders", "rocks", "hop", "snakeduel", "sudoku", "wordguess", "wordsearch", "bubbles", "gems", "stack", "runner", "lander", "defense", "slide", "lights", "nonogram", "tiles", "codebreak", "typerain", "fourrow", "tictactoe", "checkers", "reversi", "dots", "seabattle", "ludo", "snakes", "carrom", "chess", "arrows", "parking", "watersort", "bolts", "connect", "untangle"])   # only games that exist
        self.assertEqual([a["id"] for a in body["admins"]], ["u_asha"])

    def test_validation_saves_nothing(self):
        for bad in ({"unknown": 1}, {"leaderboard": "yes"}, {"leaderboard": 1}, {"default_look": "sepia"},
                    {"disabled_games": ["pinball"]}, {"school_days": [9]}, {"keep_scores_years": 10},
                    {"keep_scores_years": "1"}, {"ha_sensors": None}):
            r = self.put("/api/admin/settings", bad, ASHA)
            self.assertEqual(r.status_code, 422, bad)
        self.assertEqual(settings.all(), settings.DEFAULTS)
        r = self.put("/api/admin/settings", {"leaderboard": False, "default_look": "sepia"}, ASHA)
        self.assertEqual(r.status_code, 422)
        self.assertTrue(settings.get("leaderboard"))

    def test_stored_with_who_and_when(self):
        self.settings({"default_look": "pixel"})
        with db.get_conn() as conn:
            row = conn.execute("SELECT * FROM app_settings WHERE key = 'default_look'").fetchone()
        self.assertEqual(json.loads(row["value"]), "pixel")
        self.assertEqual(row["updated_by"], "asha")
        self.assertTrue(row["updated_at"].startswith("2026-09-21"))

    def test_game_switch_applies_at_once(self):
        self.play(100, game="brick", mode="classic")
        self.settings({"disabled_games": ["brick"]})
        self.assertEqual([g["id"] for g in self.get("/api/games").json()["games"]], ["snake", "blocks", "duel", "racer", "flap", "mines", "merge", "colours", "cards", "mole", "numbers", "tanks", "invaders", "rocks", "hop", "snakeduel", "sudoku", "wordguess", "wordsearch", "bubbles", "gems", "stack", "runner", "lander", "defense", "slide", "lights", "nonogram", "tiles", "codebreak", "typerain", "fourrow", "tictactoe", "checkers", "reversi", "dots", "seabattle", "ludo", "snakes", "carrom", "chess", "arrows", "parking", "watersort", "bolts", "connect", "untangle"])
        r = self.start("brick", "classic")
        self.assertEqual(r.status_code, 409)
        self.settings({"disabled_games": []})
        g = {x["id"]: x for x in self.get("/api/games").json()["games"]}
        self.assertEqual(g["brick"]["best"], 100)                    # scores were kept

    def test_powerups_switch(self):
        self.settings({"brick_powerups": False})
        brick = [g for g in self.get("/api/games").json()["games"] if g["id"] == "brick"][0]
        self.assertEqual([m["id"] for m in brick["modes"]], ["classic"])
        self.assertEqual(brick["defaultMode"], "classic")
        self.assertEqual(self.start("brick", "powerups").status_code, 422)
        self.assertEqual(self.start("brick", "classic").status_code, 201)

    def test_options_json_only_admin_users(self):
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.yaml")) as f:
            text = f.read()
        self.assertIn("options:\n  admin_users: []", text)


class TestUsers(ApiBase):
    def test_list_and_switch_off(self):
        users = self.get("/api/admin/users", ASHA).json()["users"]
        self.assertEqual(sorted(u["name"] for u in users), ["Asha Rao", "Kabir Rao", "Meera Rao"])
        r = self.patch("/api/admin/users/u_kabir", {"disabled": True})
        self.assertTrue(r.json()["disabled"])
        self.assertEqual(self.start().status_code, 403)
        self.assertEqual(self.patch("/api/admin/users/u_kabir", {"disabled": False}).status_code, 200)
        self.assertEqual(self.start().status_code, 201)

    def test_cant_switch_self_off(self):
        self.assertEqual(self.patch("/api/admin/users/u_asha", {"disabled": True}).status_code, 409)

    def test_validation(self):
        self.assertEqual(self.patch("/api/admin/users/u_kabir", {"disabled": "yes"}).status_code, 422)
        self.assertEqual(self.patch("/api/admin/users/u_kabir", {"role": "x"}).status_code, 422)
        self.assertEqual(self.patch("/api/admin/users/nobody", {"disabled": True}).status_code, 404)

    def test_get_one(self):
        r = self.get("/api/admin/users/u_meera", ASHA)
        self.assertEqual(r.json()["name"], "Meera Rao")
        self.assertEqual(self.get("/api/admin/users/nobody", ASHA).status_code, 404)


class TestNotifyServices(ApiBase):
    def test_add_remove_test(self):
        self.ha.notify_services = ["mobile_app_kabirs_phone"]
        r = self.post("/api/admin/users/u_kabir/notify", {"service": "notify.mobile_app_kabirs_phone"}, ASHA)
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(r.json()["notify"], ["notify.mobile_app_kabirs_phone"])
        self.assertEqual(self.post("/api/admin/users/u_kabir/notify", {"service": "notify.Bad Name"}, ASHA).status_code, 422)
        t = self.post("/api/admin/users/u_kabir/notify/test", {}, ASHA)
        self.assertEqual(t.status_code, 200, t.text)
        self.assertEqual(self.ha.notifications()[-1][0], "mobile_app_kabirs_phone")
        self.assertEqual(self.post("/api/admin/users/u_kabir/notify/test", {}, ASHA).status_code, 429)
        d = self.delete("/api/admin/users/u_kabir/notify/notify.mobile_app_kabirs_phone", ASHA)
        self.assertEqual(d.json()["notify"], [])
        self.assertEqual(self.delete("/api/admin/users/u_kabir/notify/notify.mobile_app_kabirs_phone", ASHA).status_code, 404)

    def test_no_service_is_400(self):
        self.assertEqual(self.post("/api/admin/users/u_meera/notify/test", {}, ASHA).status_code, 400)

    def test_list_services(self):
        self.ha.notify_services = ["mobile_app_a", "family"]
        r = self.get("/api/admin/notify-services", ASHA).json()
        self.assertEqual(r["services"], ["notify.family", "notify.mobile_app_a"])
        config.SUPERVISOR_TOKEN = ""
        from app.common import ha_notify
        ha_notify._targets_cache = None
        r = self.get("/api/admin/notify-services?refresh=1", ASHA).json()
        self.assertFalse(r["available"])

    def test_phones_from_home_assistant_people(self):
        self.ha.people = [{"entity_id": "person.meera", "name": "Meera", "user_id": "u_meera", "state": "home",
                           "phones": [{"name": "Meera Phone", "label": "Meera's phone", "tracker": "device_tracker.x"}]}]
        users = {u["id"]: u for u in self.get("/api/admin/users?refresh=1", ASHA).json()["users"]}
        self.assertEqual(users["u_meera"]["ha"]["phones"][0]["service"], "notify.mobile_app_meera_phone")


if __name__ == "__main__":
    unittest.main()
