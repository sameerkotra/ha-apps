"""Children and limits: daily minutes (school days, weekends, holidays),
extra time, time counted only while a game runs, quiet hours across midnight
and time zones, allowed games, leaderboard visibility, limit warnings to
parents, play history. Invented people only. NOW is Monday 21 Sep 2026 noon UTC."""
import _env  # noqa: F401  (must be first)

import unittest
from datetime import datetime, timezone

from app import config, db, housekeeping
from base import ASHA, KABIR, MEERA, ApiBase


def at(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=timezone.utc)


class LimitsBase(ApiBase):
    def left(self, h=KABIR):
        return self.get("/api/me", h).json()["playTime"]["leftSeconds"]

    def run_for(self, seconds, h=KABIR, game="snake", mode="walls-normal"):
        """A game that runs `seconds` and is then left without a result."""
        r = self.start(game, mode, h)
        self.assertEqual(r.status_code, 201, r.text)
        sid = r.json()["id"]
        self.clock.advance(seconds=seconds)
        self.assertEqual(self.post(f"/api/sessions/{sid}/end", {"activeSeconds": seconds}, h).status_code, 200)
        return sid


class TestDailyMinutes(LimitsBase):
    def test_not_a_child_no_limits(self):
        self.run_for(5 * 3600)
        me = self.get("/api/me").json()
        self.assertFalse(me["isChild"])
        self.assertIsNone(me["playTime"]["leftSeconds"])
        self.assertEqual(self.start().status_code, 201)

    def test_school_day_limit_and_used_up(self):
        self.make_child(minutesSchool=30, minutesWeekend=60)
        me = self.get("/api/me").json()
        self.assertTrue(me["isChild"])
        self.assertEqual(me["playTime"]["dayType"], "school")
        self.assertEqual(me["playTime"]["leftSeconds"], 30 * 60)
        self.assertEqual(me["limits"]["minutesSchool"], 30)          # a child sees their own limits
        self.run_for(20 * 60)
        self.assertEqual(self.left(), 10 * 60)
        self.run_for(10 * 60)
        self.assertEqual(self.left(), 0)
        r = self.start()
        self.assertEqual(r.status_code, 409)
        self.assertIn("No play time left", r.json()["detail"])
        self.assertFalse(self.get("/api/me").json()["playTime"]["canStart"])
        self.assertEqual(self.start(h=MEERA).status_code, 201)       # others aren't limited
        self.clock.advance(days=1)                                   # a new day
        self.assertEqual(self.left(), 30 * 60)

    def test_weekend_limit(self):
        self.make_child(minutesSchool=30, minutesWeekend=90)
        self.clock.t = at(2026, 9, 26, 10)                           # Saturday
        pt = self.get("/api/me").json()["playTime"]
        self.assertEqual((pt["dayType"], pt["leftSeconds"]), ("weekend", 90 * 60))

    def test_no_limit_on_one_kind_of_day(self):
        self.make_child(minutesSchool=None, minutesWeekend=45)
        self.assertIsNone(self.left())
        self.assertEqual(self.get("/api/me").json()["playTime"]["dayType"], "school")

    def test_school_days_setting(self):
        self.make_child(minutesSchool=30, minutesWeekend=90)
        self.clock.t = at(2026, 9, 25, 10)                           # Friday
        self.assertEqual(self.left(), 30 * 60)
        self.settings({"school_days": [1, 2, 3, 4]})                 # applies at once, no restart
        self.assertEqual(self.left(), 90 * 60)
        self.assertEqual(self.put("/api/admin/settings", {"school_days": [0, 8]}, ASHA).status_code, 422)

    def test_holidays_use_weekend_limits(self):
        self.make_child(minutesSchool=30, minutesWeekend=90)
        r = self.post("/api/admin/holidays", {"from": "2026-09-21", "to": "2026-09-23", "name": "Autumn break"}, ASHA)
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual([x["date"] for x in r.json()["holidays"]], ["2026-09-21", "2026-09-22", "2026-09-23"])
        self.assertEqual(self.left(), 90 * 60)
        self.assertEqual(self.get("/api/me").json()["playTime"]["dayType"], "weekend")
        self.assertEqual(self.delete("/api/admin/holidays/2026-09-21", ASHA).status_code, 200)
        self.assertEqual(self.left(), 30 * 60)
        for bad in ({"from": "21/09/2026"}, {"from": "2026-02-30"}, {"from": "2026-09-10", "to": "2026-09-01"},
                    {"from": "2026-01-01", "to": "2026-12-31"}):
            self.assertEqual(self.post("/api/admin/holidays", bad, ASHA).status_code, 422, bad)
        self.assertEqual(self.delete("/api/admin/holidays/2026-01-01", ASHA).status_code, 404)

    def test_extra_time_today_only(self):
        self.make_child(minutesSchool=30)
        self.run_for(30 * 60)
        self.assertEqual(self.left(), 0)
        r = self.post("/api/admin/users/u_kabir/extra-time", {"minutes": 15}, ASHA)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["playTime"]["extraMinutes"], 15)
        self.assertEqual(self.left(), 15 * 60)
        self.assertEqual(self.start().status_code, 201)
        self.clock.advance(days=1)
        self.assertEqual(self.left(), 30 * 60)
        self.assertEqual(self.post("/api/admin/users/u_kabir/extra-time", {"minutes": 20}, ASHA).status_code, 422)
        self.assertEqual(self.post("/api/admin/users/u_meera/extra-time", {"minutes": 15}, ASHA).status_code, 409)


class TestActiveTime(LimitsBase):
    def test_paused_time_doesnt_count(self):
        self.make_child(minutesSchool=30)
        sid = self.start().json()["id"]
        self.clock.advance(minutes=10)
        r = self.post(f"/api/sessions/{sid}/beat", {"activeSeconds": 120})       # paused for 8 of the 10 minutes
        self.assertEqual(r.json()["playTime"]["leftSeconds"], 28 * 60)

    def test_capped_by_wall_time_and_never_shrinks(self):
        self.make_child(minutesSchool=30)
        sid = self.start().json()["id"]
        self.clock.advance(seconds=60)
        self.post(f"/api/sessions/{sid}/beat", {"activeSeconds": 5000})
        with db.get_conn() as conn:
            self.assertLessEqual(conn.execute("SELECT active_seconds FROM play_sessions").fetchone()[0], 62)
        self.post(f"/api/sessions/{sid}/beat", {"activeSeconds": 10})
        with db.get_conn() as conn:
            self.assertGreaterEqual(conn.execute("SELECT active_seconds FROM play_sessions").fetchone()[0], 60)
        self.post(f"/api/sessions/{sid}/beat", {"activeSeconds": "lots"})          # ignored, not an error
        self.assertEqual(self.post(f"/api/sessions/{sid}/beat", {}, MEERA).status_code, 404)

    def test_running_game_finishes_when_time_runs_out(self):
        self.make_child(minutesSchool=10)
        self.run_for(9 * 60)
        r = self.start()
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()["playTime"]["leftSeconds"], 60)
        sid = r.json()["id"]
        self.clock.advance(minutes=5)
        beat = self.post(f"/api/sessions/{sid}/beat", {"activeSeconds": 300}).json()
        self.assertEqual(beat["playTime"]["leftSeconds"], 0)
        self.assertFalse(beat["ended"])                               # the game goes on
        res = self.post("/api/scores", {"sessionId": sid, "score": 120, "level": 1, "seconds": 300})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["saved"])
        self.assertEqual(self.start().status_code, 409)               # no new game

    def test_one_game_at_a_time(self):
        a = self.start().json()["id"]
        self.clock.advance(seconds=30)
        self.post(f"/api/sessions/{a}/beat", {"activeSeconds": 30})
        self.start()
        with db.get_conn() as conn:
            open_ = conn.execute("SELECT COUNT(*) FROM play_sessions WHERE ended_at IS NULL").fetchone()[0]
        self.assertEqual(open_, 1)

    def test_stale_sessions_closed_by_housekeeping(self):
        self.make_child(minutesSchool=30)
        sid = self.start().json()["id"]
        self.clock.advance(seconds=90)
        self.post(f"/api/sessions/{sid}/beat", {"activeSeconds": 90})
        self.clock.advance(minutes=30)                               # the tab was closed
        self.assertEqual(housekeeping.run_blocking()["closed"], 1)
        self.assertEqual(self.left(), 30 * 60 - 90)


class TestQuietHours(LimitsBase):
    def setUp(self):
        super().setUp()
        self.make_child(quietSchoolFrom="21:00", quietSchoolTo="07:00",
                        quietWeekendFrom="22:30", quietWeekendTo="08:30")

    def can(self):
        return self.get("/api/me").json()["playTime"]["canStart"]

    def test_school_night_across_midnight(self):
        self.clock.t = at(2026, 9, 21, 20, 59)                       # Monday
        self.assertTrue(self.can())
        self.clock.t = at(2026, 9, 21, 21, 30)
        self.assertFalse(self.can())
        r = self.start()
        self.assertEqual(r.status_code, 409)
        self.assertIn("07:00", r.json()["detail"])
        self.clock.t = at(2026, 9, 22, 6, 59)                        # Tuesday morning
        self.assertFalse(self.can())
        self.clock.t = at(2026, 9, 22, 7, 0)
        self.assertTrue(self.can())

    def test_friday_night_is_a_weekend_night(self):
        self.clock.t = at(2026, 9, 25, 21, 30)                       # Friday: Saturday isn't a school day
        self.assertTrue(self.can())
        self.clock.t = at(2026, 9, 25, 22, 45)
        self.assertFalse(self.can())
        self.clock.t = at(2026, 9, 26, 8, 0)                         # Saturday morning
        self.assertFalse(self.can())
        self.clock.t = at(2026, 9, 26, 8, 30)
        self.assertTrue(self.can())

    def test_sunday_night_is_a_school_night(self):
        self.clock.t = at(2026, 9, 27, 7, 30)                        # Sunday morning: weekend times
        self.assertFalse(self.can())
        self.clock.t = at(2026, 9, 27, 21, 30)                       # Sunday evening: Monday is a school day
        self.assertFalse(self.can())

    def test_holiday_morning_uses_weekend_times(self):
        self.post("/api/admin/holidays", {"from": "2026-09-22"}, ASHA)
        self.clock.t = at(2026, 9, 21, 21, 30)                       # Monday night before a holiday
        self.assertTrue(self.can())
        self.clock.t = at(2026, 9, 22, 7, 30)                        # holiday morning: until 08:30
        self.assertFalse(self.can())

    def test_running_game_finishes_in_quiet_hours(self):
        self.clock.t = at(2026, 9, 21, 20, 55)
        sid = self.start().json()["id"]
        self.clock.t = at(2026, 9, 21, 21, 10)
        res = self.post("/api/scores", {"sessionId": sid, "score": 30, "level": 1, "seconds": 900})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["saved"])
        self.assertFalse(res.json()["playTime"]["canStart"])

    def test_home_assistant_time_zone(self):
        config.set_timezone("Asia/Kolkata")                          # UTC+05:30
        self.clock.t = at(2026, 9, 21, 15, 0)                        # 20:30 there
        self.assertTrue(self.can())
        self.clock.t = at(2026, 9, 21, 15, 45)                       # 21:15 there
        self.assertFalse(self.can())


class TestTimeZoneDay(LimitsBase):
    def test_day_rolls_over_at_local_midnight(self):
        config.set_timezone("Asia/Kolkata")
        self.make_child(minutesSchool=30)
        self.clock.t = at(2026, 9, 21, 18, 15)                       # 23:45 Monday there
        self.run_for(10 * 60)
        self.assertEqual(self.left(), 20 * 60)
        self.clock.t = at(2026, 9, 21, 18, 40)                       # 00:10 Tuesday there
        self.assertEqual(self.left(), 30 * 60)


class TestChildRules(LimitsBase):
    def test_admin_cant_be_a_child(self):
        r = self.patch("/api/admin/users/u_asha", {"isChild": True})
        self.assertEqual(r.status_code, 409)
        self.assertIn("admin", r.json()["detail"])

    def test_listed_admin_marked_earlier_has_no_limits(self):
        self.make_child(minutesSchool=0)
        self.assertEqual(self.start().status_code, 409)
        config.ADMIN_NAMES.add("kabir")
        try:
            me = self.get("/api/me").json()
            self.assertFalse(me["isChild"])
            self.assertEqual(self.start().status_code, 201)
        finally:
            config.ADMIN_NAMES.discard("kabir")

    def test_unmarking_removes_limits(self):
        self.make_child(minutesSchool=0)
        self.patch("/api/admin/users/u_kabir", {"isChild": False})
        self.assertEqual(self.start().status_code, 201)

    def test_allowed_games(self):
        self.make_child(allowedGames=["snake"])
        self.assertEqual([g["id"] for g in self.get("/api/games").json()["games"]], ["snake"])
        r = self.start("brick", "classic")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.start("snake").status_code, 201)

    def test_leaderboard_visibility(self):
        self.play(100, h=MEERA)
        self.make_child(leaderboard="first")
        rows = self.get("/api/leaderboard?game=snake&mode=walls-normal").json()["rows"]
        self.assertEqual(rows[0]["name"], "Meera")
        self.assertEqual(self.get("/api/me").json()["leaderboardNames"], "first")
        self.put("/api/admin/users/u_kabir/limits", {"leaderboard": "hidden"}, ASHA)
        self.assertEqual(self.get("/api/leaderboard?game=snake&mode=walls-normal").status_code, 403)
        self.assertFalse(self.get("/api/me").json()["leaderboard"])
        self.assertEqual(self.get("/api/leaderboard?game=snake&mode=walls-normal", MEERA).json()["rows"][0]["name"],
                         "Meera Rao")

    def test_limits_validation(self):
        self.make_child()
        for bad in ({"minutesSchool": -5}, {"minutesSchool": 2000}, {"minutesSchool": 10.5}, {"minutesSchool": "30"},
                    {"quietSchoolFrom": "21:00"}, {"quietSchoolFrom": "25:00", "quietSchoolTo": "07:00"},
                    {"quietWeekendFrom": "07:00", "quietWeekendTo": "07:00"}, {"allowedGames": ["pinball"]},
                    {"leaderboard": "sometimes"}, {"bedtime": "20:00"}):
            self.assertEqual(self.put("/api/admin/users/u_kabir/limits", bad, ASHA).status_code, 422, bad)
        self.assertEqual(self.put("/api/admin/users/u_meera/limits", {}, ASHA).status_code, 409)
        self.assertEqual(self.put("/api/admin/users/nobody/limits", {}, ASHA).status_code, 404)

    def test_admin_users_page_data(self):
        self.make_child(minutesSchool=40)
        self.run_for(10 * 60)
        users = {u["id"]: u for u in self.get("/api/admin/users", ASHA).json()["users"]}
        k = users["u_kabir"]
        self.assertTrue(k["isChild"])
        self.assertEqual(k["limits"]["minutesSchool"], 40)
        self.assertEqual(k["playTime"]["leftSeconds"], 30 * 60)
        self.assertTrue(users["u_asha"]["isAdmin"])
        self.assertIsNone(users["u_meera"]["limits"])

    def test_play_history(self):
        self.make_child()
        self.run_for(120)
        self.play(40, seconds=60, game="brick", mode="classic")
        self.post("/api/admin/users/u_kabir/extra-time", {"minutes": 30}, ASHA)
        hist = self.get("/api/admin/users/u_kabir/history", ASHA).json()
        self.assertEqual([s["gameName"] for s in hist["sessions"]], ["Brick Breaker", "Snake"])
        self.assertEqual(hist["sessions"][0]["score"], 40)
        self.assertEqual(hist["sessions"][1]["seconds"], 120)
        self.assertEqual(hist["days"], [{"date": "2026-09-21", "minutes": 3}])
        self.assertEqual(hist["extraTime"][0]["minutes"], 30)


class TestParentWarnings(LimitsBase):
    def setUp(self):
        super().setUp()
        self.ha.notify_services = ["asha_phone"]
        self.post("/api/admin/users/u_asha/notify", {"service": "notify.asha_phone"}, ASHA)
        self.make_child(minutesSchool=30)

    def beat_to(self, minutes):
        sid = self.start().json()["id"]
        self.clock.advance(minutes=minutes)
        return self.post(f"/api/sessions/{sid}/beat", {"activeSeconds": minutes * 60}).json()

    def test_off_by_default(self):
        self.assertTrue(self.beat_to(26)["playTime"]["warn"])
        self.assertEqual(self.ha.notifications(), [])

    def test_once_a_day_with_add_15_action(self):
        self.settings({"limit_warnings": True})
        old = config.INGRESS_PANEL
        config.INGRESS_PANEL = "/local_household_arcade"
        try:
            self.ha.requests.clear()
            self.assertTrue(self.beat_to(26)["playTime"]["warn"])
            sent = self.ha.notifications()
            self.assertEqual(len(sent), 1)
            name, body = sent[0]
            self.assertEqual(name, "asha_phone")
            self.assertIn("Kabir Rao has 5 minutes", body["message"])
            self.assertEqual(body["data"]["actions"][0]["title"], "Add 15 minutes")
            self.assertTrue(body["data"]["actions"][0]["uri"].endswith("/local_household_arcade/admin/users/u_kabir"))
            self.ha.requests.clear()
            self.beat_to(1)
            self.assertEqual(self.ha.notifications(), [])           # not again today
        finally:
            config.INGRESS_PANEL = old

    def test_only_chosen_admins(self):
        config.ADMIN_NAMES.add("meera")
        try:
            self.get("/api/me", MEERA)
            self.ha.notify_services = ["asha_phone", "meera_phone"]
            self.post("/api/admin/users/u_meera/notify", {"service": "notify.meera_phone"}, ASHA)
            self.settings({"limit_warnings": True, "limit_warning_admins": ["u_meera"]})
            self.assertEqual(self.put("/api/admin/settings", {"limit_warning_admins": ["u_kabir"]}, ASHA).status_code, 422)
            self.ha.requests.clear()
            self.beat_to(27)
            self.assertEqual([n for n, _ in self.ha.notifications()], ["meera_phone"])
        finally:
            config.ADMIN_NAMES.discard("meera")

    def test_admins_see_low_time_children_on_home(self):
        self.beat_to(26)
        low = self.get("/api/me", ASHA).json()["lowTimeChildren"]
        self.assertEqual([(c["id"], c["leftSeconds"]) for c in low], [("u_kabir", 240)])
        self.assertEqual(self.get("/api/me", MEERA).json()["lowTimeChildren"], [])


if __name__ == "__main__":
    unittest.main()
