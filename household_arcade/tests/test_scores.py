"""Scores: saving, honest-score checks, practice, short games, personal bests,
household records, the leaderboard, deleting, and Keep scores for."""
import _env  # noqa: F401  (must be first)

import unittest
from datetime import timedelta

from app import db, scores
from base import ASHA, DEV, KABIR, MEERA, ApiBase


class TestSaving(ApiBase):
    def test_saved_with_version_and_flags(self):
        r = self.play(150, seconds=60, level=2)
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body["saved"])
        self.assertTrue(body["personalBest"])
        self.assertTrue(body["householdRecord"])
        with db.get_conn() as conn:
            row = conn.execute("SELECT * FROM scores").fetchone()
        self.assertEqual((row["score"], row["level"], row["seconds"], row["app_version"], row["mode"]),
                         (150, 2, 60, "1.12.0", "walls-normal"))
        # a lower score: neither flag
        body = self.play(100).json()
        self.assertTrue(body["saved"])
        self.assertFalse(body["personalBest"])
        self.assertFalse(body["householdRecord"])
        self.assertEqual(body["best"], 150)
        # someone else beats the record
        body = self.play(200, h=MEERA).json()
        self.assertTrue(body["personalBest"] and body["householdRecord"])

    def test_modes_are_separate(self):
        self.play(300)
        body = self.play(50, mode="wrap-fast").json()
        self.assertTrue(body["personalBest"])
        self.assertTrue(body["householdRecord"])

    def test_practice_not_saved(self):
        body = self.play(500, practice=True).json()
        self.assertFalse(body["saved"])
        self.assertEqual(body["reason"], "practice")
        with db.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM scores").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM play_sessions WHERE ended_at IS NOT NULL").fetchone()[0], 1)

    def test_under_three_seconds_not_saved(self):
        body = self.play(0, seconds=2).json()
        self.assertFalse(body["saved"])
        self.assertEqual(body["reason"], "short")
        self.assertTrue(self.play(10, seconds=3).json()["saved"])

    def test_score_needs_own_open_session_once(self):
        r = self.start()
        sid = r.json()["id"]
        self.clock.advance(seconds=30)
        self.assertEqual(self.post("/api/scores", {"sessionId": sid, "score": 10, "seconds": 30}, MEERA).status_code, 404)
        self.assertEqual(self.post("/api/scores", {"sessionId": "nope", "score": 10, "seconds": 30}).status_code, 404)
        self.assertEqual(self.post("/api/scores", {"score": 10, "seconds": 30}).status_code, 422)
        self.assertEqual(self.post("/api/scores", {"sessionId": sid, "score": 10, "seconds": 30}).status_code, 200)
        self.assertEqual(self.post("/api/scores", {"sessionId": sid, "score": 10, "seconds": 30}).status_code, 409)


class TestHonestScores(ApiBase):
    def check(self, game, mode, score, seconds, level=1):
        return self.play(score, seconds=seconds, game=game, mode=mode, level=level)

    def test_snake_limits(self):
        self.assertEqual(self.check("snake", "walls-fast", 40 * 60 + 100, 60).status_code, 200)
        self.assertEqual(self.check("snake", "walls-fast", 40 * 60 + 101, 60).status_code, 422)
        self.assertEqual(self.check("snake", "wrap-slow", 20_001, 3600).status_code, 422)
        self.assertEqual(self.check("snake", "wrap-slow", 20_000, 3600).status_code, 200)

    def test_brick_limits(self):
        self.assertEqual(self.check("brick", "classic", 400 * 10 + 500, 10).status_code, 200)
        self.assertEqual(self.check("brick", "classic", 400 * 10 + 501, 10).status_code, 422)
        self.assertEqual(self.check("brick", "powerups", 500_001, 6 * 3600).status_code, 422)

    def test_level_seconds_and_types(self):
        self.assertEqual(self.check("brick", "classic", 10, 60, level=101).status_code, 422)
        self.assertEqual(self.check("brick", "classic", 10, 60, level=100).status_code, 200)
        self.assertEqual(self.check("snake", "walls-slow", 10, 6 * 3600 + 1).status_code, 422)
        for bad in ({"score": -1}, {"score": 1.5}, {"score": "10"}, {"score": True}, {"level": -2},
                    {"seconds": -3}, {"seconds": None}):
            r = self.start()
            self.clock.advance(seconds=20)
            body = {"sessionId": r.json()["id"], "score": 10, "level": 1, "seconds": 20, **bad}
            self.assertEqual(self.post("/api/scores", body).status_code, 422, bad)

    def test_nan_and_infinity_refused(self):
        r = self.start()
        self.clock.advance(seconds=20)
        raw = '{"sessionId": "%s", "score": NaN, "level": 1, "seconds": 20}' % r.json()["id"]
        resp = self.c.post("/api/scores", content=raw, headers={**KABIR, "Content-Type": "application/json"})
        self.assertIn(resp.status_code, (400, 422))

    def test_more_seconds_than_wall_time_refused(self):
        r = self.start()
        self.clock.advance(seconds=10)
        resp = self.post("/api/scores", {"sessionId": r.json()["id"], "score": 50, "level": 1, "seconds": 600})
        self.assertEqual(resp.status_code, 422)
        self.assertIn("longer", resp.json()["detail"])
        with db.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM scores").fetchone()[0], 0)
            active = conn.execute("SELECT active_seconds FROM play_sessions").fetchone()[0]
        self.assertLessEqual(active, 12)                  # capped by the wall time

    def test_mode_must_exist(self):
        self.assertEqual(self.start("snake", "classic").status_code, 422)
        self.assertEqual(self.start("tetrominoes", "x").status_code, 404)


class TestLeaderboard(ApiBase):
    def board(self, game="snake", mode="walls-normal", period="all", h=KABIR):
        return self.get(f"/api/leaderboard?game={game}&mode={mode}&period={period}", h)

    def test_best_per_person_once_and_order(self):
        self.play(100, h=KABIR)
        self.play(300, h=KABIR)
        self.play(200, h=MEERA)
        self.play(250, h=ASHA)
        rows = self.board().json()["rows"]
        self.assertEqual([(r["name"], r["score"]) for r in rows],
                         [("Kabir Rao", 300), ("Asha Rao", 250), ("Meera Rao", 200)])
        self.assertEqual([r["rank"] for r in rows], [1, 2, 3])
        self.assertTrue(rows[0]["me"])
        self.assertFalse(self.board().json()["canDelete"])
        self.assertTrue(self.board(h=ASHA).json()["canDelete"])

    def test_top_ten(self):
        for i in range(12):
            self.play(10 + i, h={"X-Remote-User-Id": f"p{i}", "X-Remote-User-Name": f"p{i}",
                                 "X-Remote-User-Display-Name": f"Player {i}"})
        rows = self.board().json()["rows"]
        self.assertEqual(len(rows), 10)
        self.assertEqual(rows[0]["score"], 21)

    def test_this_month(self):
        self.play(500, h=MEERA)                          # September
        self.clock.advance(days=12)                      # 3 October
        self.play(100, h=KABIR)
        self.assertEqual([r["score"] for r in self.board(period="month").json()["rows"]], [100])
        self.assertEqual([r["score"] for r in self.board(period="all").json()["rows"]], [500, 100])

    def test_switched_off_people_hidden_and_cant_play(self):
        self.play(900, h=MEERA)
        self.play(100, h=KABIR)
        self.assertEqual(self.patch("/api/admin/users/u_meera", {"disabled": True}).status_code, 200)
        self.assertEqual([r["name"] for r in self.board().json()["rows"]], ["Kabir Rao"])
        self.assertEqual(self.start(h=MEERA).status_code, 403)
        self.assertTrue(self.play(150).json()["householdRecord"])      # Meera's 900 doesn't count while off
        self.assertTrue(self.get("/api/me", MEERA).json()["disabled"])

    def test_leaderboard_switch(self):
        self.settings({"leaderboard": False})
        self.assertEqual(self.board().status_code, 403)
        self.assertFalse(self.get("/api/me").json()["leaderboard"])

    def test_validation(self):
        self.assertEqual(self.board(mode="sideways").status_code, 422)
        self.assertEqual(self.board(period="year").status_code, 422)
        self.assertEqual(self.board(game="pinball").status_code, 404)
        self.settings({"disabled_games": ["brick"]})
        self.assertEqual(self.board(game="brick", mode="classic").status_code, 404)


class TestMineAndDelete(ApiBase):
    def test_mine(self):
        self.play(100, seconds=60)
        self.play(150, seconds=120, mode="wrap-fast")
        self.play(20, seconds=30, practice=True)
        data = self.get("/api/scores/mine").json()
        self.assertEqual({(b["mode"], b["score"]) for b in data["bests"]}, {("walls-normal", 100), ("wrap-fast", 150)})
        self.assertEqual([r["score"] for r in data["recent"]], [150, 100])
        self.assertEqual(data["totals"]["games"], 2)
        self.assertEqual(data["totals"]["secondsThisWeek"], 210)      # practice time counts as played

    def test_last_twenty(self):
        for i in range(23):
            self.play(i + 1, seconds=5)
        self.assertEqual(len(self.get("/api/scores/mine").json()["recent"]), 20)

    def test_delete_own_admin_any_not_others(self):
        mine = self.play(100).json()["scoreId"]
        theirs = self.play(200, h=MEERA).json()["scoreId"]
        self.assertEqual(self.delete(f"/api/scores/{theirs}").status_code, 404)
        self.assertEqual(self.delete(f"/api/scores/{mine}").status_code, 200)
        self.assertEqual(self.delete(f"/api/scores/{theirs}", ASHA).status_code, 200)
        self.assertEqual(self.get("/api/leaderboard?game=snake&mode=walls-normal").json()["rows"], [])


class TestKeepScoresFor(ApiBase):
    def test_prune_keeps_bests(self):
        self.play(500)                    # Kabir's best, old
        self.play(100)                    # old, not a best → removed
        self.play(50, h=MEERA)            # Meera's only score → her best, kept
        self.clock.advance(days=400)
        self.play(200)                    # recent
        self.settings({"keep_scores_years": 1})
        with db.get_conn() as conn:
            removed = scores.prune(conn)
            left = sorted(r[0] for r in conn.execute("SELECT score FROM scores"))
        self.assertEqual(removed, 1)
        self.assertEqual(left, [50, 200, 500])

    def test_forever_keeps_all(self):
        self.play(100)
        self.clock.advance(days=4000)
        with db.get_conn() as conn:
            self.assertEqual(scores.prune(conn), 0)

    def test_choices(self):
        r = self.put("/api/admin/settings", {"keep_scores_years": 3}, ASHA)
        self.assertEqual(r.status_code, 422)


class TestRecordNotifications(ApiBase):
    def test_off_by_default(self):
        self.play(100)
        self.assertEqual(self.ha.notifications(), [])

    def test_household_told_except_scorer_and_opted_out(self):
        self.ha.notify_services = ["kabir_phone", "meera_phone", "asha_phone", "dev_phone"]
        self.get("/api/me", DEV)
        for uid, svc in (("u_kabir", "kabir_phone"), ("u_meera", "meera_phone"), ("u_asha", "asha_phone"),
                         ("u_dev", "dev_phone")):
            self.post(f"/api/admin/users/{uid}/notify", {"service": f"notify.{svc}"}, ASHA)
        self.put("/api/prefs", {"receiveNotifications": False}, DEV)
        self.settings({"notify_records": True})
        self.ha.requests.clear()
        self.play(1240, seconds=60)
        sent = self.ha.notifications()
        self.assertEqual(sorted(n for n, _ in sent), ["asha_phone", "meera_phone"])
        self.assertIn("Kabir Rao set a new Snake record: 1,240", sent[0][1]["message"])
        self.ha.requests.clear()
        self.play(10, seconds=60)                       # not a record
        self.assertEqual(self.ha.notifications(), [])

    def test_child_with_hidden_leaderboard_not_told(self):
        self.ha.notify_services = ["meera_phone"]
        self.post("/api/admin/users/u_meera/notify", {"service": "notify.meera_phone"}, ASHA)
        self.make_child(MEERA, leaderboard="hidden")
        self.settings({"notify_records": True})
        self.ha.requests.clear()
        self.play(100)
        self.assertEqual(self.ha.notifications(), [])


if __name__ == "__main__":
    unittest.main()
