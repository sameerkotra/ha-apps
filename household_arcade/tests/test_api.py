"""Access, identity, the start-up data, games list, personal settings and the
security headers. Invented people only."""
import _env  # noqa: F401  (must be first)

import unittest

from starlette.testclient import TestClient

from app import notify, config
from app.main import app
from base import ASHA, DEV, KABIR, MEERA, ApiBase, hdr


class TestAccess(ApiBase):
    def test_no_user_header_is_401(self):
        r = self.c.get("/api/me")
        self.assertEqual(r.status_code, 401)

    def test_not_from_ingress_proxy_is_403(self):
        with TestClient(app, client=("10.0.0.5", 5555)) as other:
            r = other.get("/api/me", headers=ASHA)
        self.assertEqual(r.status_code, 403)

    def test_health_needs_no_user(self):
        self.assertEqual(self.c.get("/api/health").json(), {"status": "ok", "version": "1.8.0"})

    def test_admin_routes_refuse_non_admins(self):
        for method, path in (("get", "/api/admin/settings"), ("get", "/api/admin/users"),
                             ("get", "/api/admin/holidays"), ("get", "/api/admin-storage-download-db"),
                             ("get", "/api/admin/users/u_kabir/history"), ("get", "/api/admin/notify-services")):
            self.assertEqual(getattr(self.c, method)(path, headers=KABIR).status_code, 403, path)
        self.assertEqual(self.patch("/api/admin/users/u_meera", {"isChild": True}, KABIR).status_code, 403)
        self.assertEqual(self.post("/api/admin/users/u_meera/extra-time", {"minutes": 15}, KABIR).status_code, 403)
        self.assertEqual(self.put("/api/admin/settings", {"leaderboard": False}, KABIR).status_code, 403)

    def test_admin_by_id_or_login_any_case_never_display_name(self):
        self.assertTrue(self.get("/api/me", hdr("x1", "Someone", "ASHA")).json()["isAdmin"])
        config.ADMIN_NAMES.add("u_dev")
        try:
            self.assertTrue(self.get("/api/me", DEV).json()["isAdmin"])
        finally:
            config.ADMIN_NAMES.discard("u_dev")
        self.assertFalse(self.get("/api/me", hdr("x2", "asha", "nisha")).json()["isAdmin"])

    def test_security_headers(self):
        r = self.get("/api/me")
        csp = r.headers["content-security-policy"]
        self.assertIn("script-src 'self'", csp)
        self.assertNotIn("unsafe-eval", csp)
        self.assertNotIn("unsafe-inline", csp)
        self.assertNotIn("http", csp)
        self.assertEqual(r.headers["x-content-type-options"], "nosniff")
        self.assertEqual(r.headers["cache-control"], "no-store")
        page = self.c.get("/", headers=KABIR)
        self.assertEqual(page.status_code, 200)
        self.assertIn("no-store", page.headers["cache-control"])
        self.assertIn("content-security-policy", page.headers)

    def test_big_bodies_refused(self):
        r = self.c.post("/api/scores", content=b"x" * 70000, headers={**KABIR, "Content-Type": "application/json"})
        self.assertEqual(r.status_code, 413)


class TestMe(ApiBase):
    def test_me(self):
        me = self.get("/api/me", ASHA).json()
        self.assertTrue(me["isAdmin"])
        self.assertFalse(me["isChild"])
        self.assertEqual(me["name"], "Asha Rao")
        self.assertEqual(me["prefs"], {"look": None, "effectiveLook": "modern", "sound": False, "handedness": "right",
                                       "reduceMotion": False, "receiveNotifications": True, "gamePrefs": {}})
        self.assertEqual([l["id"] for l in me["looks"]], ["modern", "lcd", "neon", "pixel", "paper", "contrast"])
        self.assertTrue(me["leaderboard"])
        self.assertIsNone(me["playTime"]["leftSeconds"])
        self.assertEqual(me["today"], "2026-09-21")
        self.assertEqual(me["version"], "1.8.0")

    def test_whoami_echoes_identity_and_counts_only(self):
        w = self.get("/api/whoami", KABIR).json()
        self.assertEqual(w["haUserId"], "u_kabir")
        self.assertEqual(w["haUsername"], "kabir")
        self.assertEqual(w["haDisplayName"], "Kabir Rao")
        self.assertFalse(w["isAdmin"])
        self.assertEqual(w["adminEntries"], len(config.ADMIN_NAMES))
        self.assertNotIn("asha", str(w).lower())
        self.assertTrue(w["viaIngress"])

    def test_display_name_updates(self):
        self.assertEqual(self.get("/api/me", hdr("u_kabir", "Kabir R.", "kabir")).json()["name"], "Kabir R.")
        rows = self.get("/api/admin/users", ASHA).json()["users"]
        self.assertIn("Kabir R.", [u["name"] for u in rows])


class TestGames(ApiBase):
    def test_games_list(self):
        g = self.get("/api/games").json()["games"]
        self.assertEqual([x["id"] for x in g], ["snake", "brick", "blocks", "duel", "racer", "flap", "mines", "merge", "colours", "cards", "mole", "numbers", "tanks", "invaders", "rocks", "hop", "snakeduel", "sudoku", "wordguess", "wordsearch", "bubbles", "gems", "stack", "runner", "lander", "defense", "slide", "lights", "nonogram", "tiles", "codebreak", "typerain", "fourrow", "tictactoe", "checkers", "reversi", "dots", "seabattle", "ludo", "snakes", "carrom", "chess"])
        snake = g[0]
        self.assertEqual([m["id"] for m in snake["modes"]],
                         ["walls-slow", "walls-normal", "walls-fast", "wrap-slow", "wrap-normal", "wrap-fast", "maze"])
        self.assertEqual(snake["defaultMode"], "walls-normal")
        self.assertEqual([m["id"] for m in g[1]["modes"]], ["powerups", "classic"])
        self.assertIsNone(snake["best"])

    def test_best_shown(self):
        self.play(120)
        self.play(80, mode="wrap-fast")
        g = {x["id"]: x for x in self.get("/api/games").json()["games"]}
        self.assertEqual(g["snake"]["best"], 120)
        self.assertEqual(g["snake"]["bestByMode"]["wrap-fast"], 80)
        self.assertIsNone(g["brick"]["best"])
        # for the Games page's large squares: how often played, when, and the level list
        self.assertEqual(g["snake"]["plays"], 2)
        self.assertIsNotNone(g["snake"]["lastPlayed"])
        self.assertEqual((g["brick"]["plays"], g["brick"]["lastPlayed"]), (0, None))
        self.assertEqual(g["snake"]["levels"], 10)
        self.assertEqual(g["snake"]["levelModes"], ["Maze"])
        self.assertEqual(g["flap"]["levelModes"], ["Courses"])


class TestPrefs(ApiBase):
    def test_get_and_put(self):
        r = self.put("/api/prefs", {"look": "lcd", "sound": True, "handedness": "left", "reduceMotion": True,
                                    "receiveNotifications": False})
        self.assertEqual(r.status_code, 200, r.text)
        p = self.get("/api/prefs").json()
        self.assertEqual(p, {"look": "lcd", "effectiveLook": "lcd", "sound": True, "handedness": "left",
                             "reduceMotion": True, "receiveNotifications": False, "gamePrefs": {}})
        self.assertEqual(self.get("/api/prefs", MEERA).json()["look"], None)       # per person

    def test_default_look_applies_until_chosen(self):
        self.settings({"default_look": "neon"})
        self.assertEqual(self.get("/api/prefs").json()["effectiveLook"], "neon")
        self.put("/api/prefs", {"look": "paper"})
        self.assertEqual(self.get("/api/prefs").json()["effectiveLook"], "paper")
        self.put("/api/prefs", {"look": None})
        self.assertEqual(self.get("/api/prefs").json()["effectiveLook"], "neon")

    def test_validation(self):
        for body in ({"look": "sepia"}, {"sound": "yes"}, {"handedness": "both"}, {"colour": "red"},
                     {"reduceMotion": 1}):
            self.assertEqual(self.put("/api/prefs", body).status_code, 422, body)


class TestNotificationLinks(ApiBase):
    """Where a phone notification opens (SPEC §7.3): the app's sidebar page with the route as a sub-path, which the
    app answers with a relative redirect to its own #/ route; the page is learnt from the Supervisor or HOSTNAME."""

    def test_the_page_from_the_supervisor_or_the_host_name(self):
        from app import panel
        old = config.INGRESS_PANEL
        try:
            self.assertEqual(panel.learn({"slug": "a1b2c3d4_household_arcade", "ingress_panel": True}), "/a1b2c3d4_household_arcade")
            self.assertEqual(config.INGRESS_PANEL, "/a1b2c3d4_household_arcade")
            self.assertIsNone(panel.learn({"slug": "a1b2c3d4_household_arcade", "ingress_panel": False}), "no sidebar page")
            self.assertIsNone(panel.learn({"slug": "../evil"}))
            self.assertEqual(panel.learn({}, hostname="local-household-arcade"), None, "an answer without a slug: nothing")
            self.assertEqual(panel.learn(None, hostname="local-household-arcade"), "/local_household_arcade")
            self.assertEqual(panel.learn(None, hostname="a1b2c3d4-household-arcade"), "/a1b2c3d4_household_arcade")
            self.assertIsNone(panel.learn(None, hostname="household-arcade"))
            self.assertIsNone(panel.learn(None, hostname="zz-household-arcade"))
            config.INGRESS_PANEL = "/local_household_arcade"
            self.assertEqual(self.get("/api/me").json()["panel"], "/local_household_arcade")
            self.assertEqual(notify._link("/leaderboard"), {"url": "/local_household_arcade/leaderboard",
                                                            "clickAction": "/local_household_arcade/leaderboard"})
            config.INGRESS_PANEL = None
            self.assertIsNone(notify._link("/leaderboard"))
            self.assertIsNone(self.get("/api/me").json()["panel"])
        finally:
            config.INGRESS_PANEL = old

    def test_a_sub_path_redirects_to_the_route_inside_ingress(self):
        for path, where in (("/leaderboard", "../#/leaderboard"), ("/home/join/abc-123", "../../../#/home/join/abc-123"),
                            ("/admin/users/u_kabir", "../../../#/admin/users/u_kabir"), ("/scores/snake", "../../#/scores/snake")):
            r = self.c.get(path, headers=KABIR, follow_redirects=False)
            self.assertEqual((r.status_code, r.headers.get("location")), (307, where), path)
            self.assertEqual(r.headers.get("cache-control"), "no-store")
        for path in ("/home/x/y/z", "/home/bad%20arg", "/home/" + "a" * 65, "/nope", "/admin/..%2F..%2Fetc"):
            self.assertEqual(self.c.get(path, headers=KABIR, follow_redirects=False).status_code, 404, path)
        # the static files and the shell itself are untouched
        for path in ("/", "/index.html", "/style.css", "/app.js", "/games/kit.js", "/common/ui.js"):
            self.assertEqual(self.c.get(path, headers=KABIR, follow_redirects=False).status_code, 200, path)


class TestStaticShell(ApiBase):
    def test_index_loads_the_contract_scripts_in_order(self):
        html = self.c.get("/", headers=KABIR).text
        order = ["common/backnav.js", "common/whoami.js", "games/kit.js", "games/sound.js", "games/registry.js", "games/snake-logic.js",
                 "games/snake.js", "games/brick-logic.js", "games/brick.js", "games/blocks-logic.js", "games/blocks.js",
                 "games/duel-logic.js", "games/duel.js", "games/racer-logic.js", "games/racer.js", "games/flap-logic.js",
                 "games/flap.js", "games/mines-logic.js", "games/mines.js", "games/merge-logic.js", "games/merge.js", "games/colours-logic.js", "games/colours.js", "games/cards-logic.js", "games/cards.js", "games/mole-logic.js", "games/mole.js", "games/numbers-logic.js", "games/numbers.js", "games/tanks-logic.js", "games/tanks.js", "games/invaders-logic.js", "games/invaders.js", "games/rocks-logic.js", "games/rocks.js", "games/hop-logic.js", "games/hop.js", "games/snakeduel-logic.js", "games/snakeduel.js", "games/sudoku-logic.js", "games/sudoku.js", "games/wordguess-words.js", "games/wordguess-logic.js", "games/wordguess.js", "games/wordsearch-words.js", "games/wordsearch-logic.js", "games/wordsearch.js", "games/bubbles-logic.js", "games/bubbles.js", "games/gems-logic.js", "games/gems.js", "games/stack-logic.js", "games/stack.js", "games/runner-logic.js", "games/runner.js", "games/lander-logic.js", "games/lander.js", "games/defense-logic.js", "games/defense.js", "games/slide-logic.js", "games/slide.js", "games/lights-logic.js", "games/lights.js", "games/nonogram-logic.js", "games/nonogram.js", "games/tiles-logic.js", "games/tiles.js", "games/codebreak-logic.js", "games/codebreak.js", "games/typerain-words.js", "games/typerain-logic.js", "games/typerain.js", "games/boardkit.js", "games/fourrow-logic.js", "games/fourrow.js", "games/tictactoe-logic.js", "games/tictactoe.js", "games/checkers-logic.js", "games/checkers.js", "games/reversi-logic.js", "games/reversi.js", "games/dots-logic.js", "games/dots.js", "games/seabattle-logic.js", "games/seabattle.js", "app.js", "play.js", "admin.js"]
        pos = [html.index(f'src="{name}?v=1.8.0"') for name in order]
        self.assertEqual(pos, sorted(pos))
        self.assertNotIn("<script>", html)            # no inline script (CSP)
        self.assertNotIn("onclick=", html)
        self.assertNotIn('src="/', html)              # relative URLs only (ingress)
        self.assertNotIn('href="/', html)


if __name__ == "__main__":
    unittest.main()
