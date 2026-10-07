"""Playing together, step 1: Race (SPEC §13): who can be invited, invites (one at a time, notification, expiry,
accept, decline, cancel), the match's sessions (same seed, mode, levels), results and winners (score, tie, fastest,
a player who left), the live numbers (HTTP, long poll, WebSocket), head to head and housekeeping."""
import _env  # noqa: F401  (must be first)

import threading
import time
import unittest
from datetime import timedelta

from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import config, db, games, housekeeping, together
from app.main import app
from base import ASHA, DEV, KABIR, MEERA, ApiBase, hdr

GAME, MODE = "racer", "three"


class Together(ApiBase):
    def invite(self, who=KABIR, to="u_meera", game=GAME, mode=MODE, practice=False, **extra):
        body = {"game": game, "mode": mode, "opponents": [to], "practice": practice, "kind": "race", **extra}
        return self.post("/api/matches", body, who)

    def new_match(self, a=KABIR, b=MEERA, game=GAME, mode=MODE, practice=False):
        r = self.invite(a, b["X-Remote-User-Id"], game, mode, practice)
        self.assertEqual(r.status_code, 201, r.text)
        mid = r.json()["id"]
        r = self.post(f"/api/matches/{mid}/accept", None, b)
        self.assertEqual(r.status_code, 200, r.text)
        return mid

    def session(self, mid, who, game=GAME):
        return self.post("/api/sessions", {"game": game, "matchId": mid}, who)

    def finish(self, mid, who, score, seconds=60, level=1, game=GAME, won=None, sid=None):
        if sid is None:
            r = self.session(mid, who, game)
            self.assertEqual(r.status_code, 201, r.text)
            sid = r.json()["id"]
        self.clock.advance(seconds=seconds)
        body = {"sessionId": sid, "score": score, "level": level, "seconds": seconds}
        if won is not None:
            body["won"] = won
        return self.post("/api/scores", body, who)

    def match(self, mid, who=KABIR):
        r = self.get(f"/api/matches/{mid}", who)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()


class TestPlayers(Together):
    def test_who_can_be_invited_and_why_not(self):
        self.get("/api/me", DEV)
        r = self.get(f"/api/players?game={GAME}", KABIR)
        self.assertEqual(r.status_code, 200)
        people = {p["id"]: p for p in r.json()["players"]}
        self.assertEqual(set(people), {"u_asha", "u_meera", "u_dev"})        # not yourself
        self.assertTrue(all(p["canPlay"] and p["reason"] is None for p in people.values()))
        # children: a game that isn't theirs, no time left, quiet hours; people switched off
        self.make_child(MEERA, allowedGames=["snake"])
        self.make_child(DEV, minutesSchool=0, minutesWeekend=0)
        self.get("/api/me", hdr("u_nisha", "Nisha Rao", "nisha"))
        self.patch("/api/admin/users/u_nisha", {"disabled": True})
        people = {p["id"]: p for p in self.get(f"/api/players?game={GAME}", KABIR).json()["players"]}
        self.assertFalse(people["u_meera"]["canPlay"])
        self.assertIn("isn't one of their games", people["u_meera"]["reason"])
        self.assertFalse(people["u_dev"]["canPlay"])
        self.assertIn("No play time left today", people["u_dev"]["reason"])
        self.assertFalse(people["u_nisha"]["canPlay"])
        self.assertIn("Switched off", people["u_nisha"]["reason"])
        # the ones who can play come first
        order = [p["canPlay"] for p in self.get(f"/api/players?game={GAME}", KABIR).json()["players"]]
        self.assertEqual(order, sorted(order, reverse=True))
        # quiet hours (now is Monday noon UTC)
        self.put("/api/admin/users/u_dev/limits", {"minutesSchool": 60, "quietSchoolFrom": "11:00", "quietSchoolTo": "13:00"}, ASHA)
        people = {p["id"]: p for p in self.get(f"/api/players?game=snake", KABIR).json()["players"]}
        self.assertIn("Quiet hours until 13:00", people["u_dev"]["reason"])

    def test_unknown_or_unraceable_game(self):
        self.assertEqual(self.get("/api/players?game=pinball").status_code, 404)
        # two-player games can't be raced, but they are played live on two phones (step 2)
        for g in ("duel", "snakeduel", "tanks"):
            self.assertEqual(self.get(f"/api/players?game={g}").status_code, 200, g)
        old = games.GAMES["duel"].pop("live")
        try:
            self.assertEqual(self.get("/api/players?game=duel").status_code, 409)       # neither raced nor live
        finally:
            games.GAMES["duel"]["live"] = old
        self.assertEqual(self.get("/api/players").status_code, 422)

    def test_race_list_and_games_flag(self):
        mine = {g["id"]: g["race"] for g in self.get("/api/games").json()["games"]}
        for g in ("snake", "brick", "blocks", "racer", "flap", "mines", "merge", "colours", "cards", "mole",
                  "numbers", "invaders", "rocks", "hop", "bubbles", "gems", "stack", "runner", "lander", "defense"):
            self.assertTrue(mine[g], g)
        for g in ("duel", "tanks", "snakeduel"):
            self.assertFalse(mine[g], g)
        self.assertEqual(together.race_rule("racer"), {"rule": "score"})
        self.assertIsNone(together.race_rule("duel"))
        self.assertIsNone(together.race_rule("nope"))

    def test_a_game_can_choose_its_rule_or_opt_out(self):
        old = games.GAMES["racer"].get("race", "unset")
        try:
            games.GAMES["racer"]["race"] = {"rule": "fastest"}
            self.assertEqual(together.race_rule("racer")["rule"], "fastest")
            games.GAMES["racer"]["race"] = False
            self.assertFalse(together.race_ok("racer"))
            self.assertEqual(self.get("/api/players?game=racer").status_code, 409)
            games.GAMES["racer"]["race"] = {"rule": "nonsense"}
            self.assertFalse(together.race_ok("racer"))
        finally:
            if old == "unset":
                games.GAMES["racer"].pop("race", None)
            else:
                games.GAMES["racer"]["race"] = old


class TestInvites(Together):
    def test_create_accept(self):
        r = self.invite()
        self.assertEqual(r.status_code, 201, r.text)
        m = r.json()
        self.assertEqual((m["status"], m["game"], m["mode"], m["kind"], m["practice"], m["mine"]), ("invited", GAME, MODE, "race", False, True))
        self.assertEqual([(p["name"], p["seat"], p["invite"]) for p in m["players"]], [("Kabir Rao", 1, "accepted"), ("Meera Rao", 2, "invited")])
        self.assertTrue(0 < m["expiresIn"] <= 300)
        # what each sees on the Games page
        mine = self.get("/api/matches", KABIR).json()
        theirs = self.get("/api/matches", MEERA).json()
        self.assertEqual([x["id"] for x in mine["sent"]], [m["id"]])
        self.assertEqual(mine["waiting"], [])
        self.assertEqual([x["id"] for x in theirs["waiting"]], [m["id"]])
        self.assertEqual(theirs["sent"], [])
        # a third person sees nothing and can't open it
        self.assertEqual(self.get("/api/matches", ASHA).json(), {"waiting": [], "sent": [], "playing": [], "recent": []})
        self.assertEqual(self.get(f"/api/matches/{m['id']}", ASHA).status_code, 404)
        r = self.post(f"/api/matches/{m['id']}/accept", None, MEERA)
        self.assertEqual(r.status_code, 200)
        a = r.json()
        self.assertEqual(a["status"], "playing")
        self.assertTrue(3000 <= a["startsInMs"] <= 4000)                 # the count-in, relative to now
        self.assertEqual(self.match(m["id"], KABIR)["status"], "playing")

    def test_validation(self):
        self.assertEqual(self.post("/api/matches", {}, KABIR).status_code, 404)           # no such game
        for body in ({"game": GAME}, {"game": GAME, "mode": MODE}, {"game": GAME, "mode": MODE, "opponents": "u_meera"},
                     {"game": GAME, "mode": MODE, "opponents": []},
                     {"game": GAME, "mode": MODE, "opponents": ["u_meera", "u_asha"]},
                     {"game": GAME, "mode": "zzz", "opponents": ["u_meera"]},
                     {"game": GAME, "mode": MODE, "opponents": ["u_meera"], "practice": "yes"},
                     {"game": GAME, "mode": MODE, "opponents": ["u_kabir"]},
                     {"game": GAME, "mode": MODE, "opponents": ["u_meera"], "kind": "duo"},
                     {"game": GAME, "mode": MODE, "opponents": ["u_meera"], "rematchOf": 5}):
            self.assertEqual(self.post("/api/matches", body, KABIR).status_code, 422, body)
        self.assertEqual(self.post("/api/matches", {"game": "pinball", "mode": "x", "opponents": ["u_meera"]}).status_code, 404)
        self.assertEqual(self.invite(to="u_nobody").status_code, 404)
        self.assertEqual(self.invite(game="duel", mode="normal").status_code, 409)
        # a game that isn't played turn by turn (SPEC §13.5)
        self.assertEqual(self.post("/api/matches", {"game": GAME, "mode": MODE, "opponents": ["u_meera"], "kind": "turns"}, KABIR).status_code, 409)
        self.assertEqual(self.invite(kind="live").status_code, 409)                      # Lane Racer has no live duel
        self.settings({"disabled_games": ["racer"]})
        self.assertEqual(self.invite().status_code, 409)
        self.settings({"disabled_games": []})
        self.assertEqual(self.invite().status_code, 201)

    def test_children_and_people_who_cannot_play(self):
        self.make_child(MEERA, minutesSchool=0, minutesWeekend=0)
        r = self.invite()
        self.assertEqual(r.status_code, 409)
        self.assertIn("Meera Rao can't play right now", r.json()["detail"])
        # a child who is out of time can't invite either
        self.make_child(KABIR, minutesSchool=0, minutesWeekend=0)
        r = self.invite(who=KABIR, to="u_asha")
        self.assertEqual(r.status_code, 409)
        self.assertIn("No play time left", r.json()["detail"])
        # a child's other games
        self.put("/api/admin/users/u_kabir/limits", {"minutesSchool": 60, "minutesWeekend": 60, "allowedGames": ["snake"]}, ASHA)
        self.assertEqual(self.invite(who=KABIR, to="u_asha").status_code, 409)
        self.assertEqual(self.invite(who=KABIR, to="u_asha", game="snake", mode="walls-normal").status_code, 201)
        # a person switched off can't be invited, or invite
        self.get("/api/me", DEV)
        self.patch("/api/admin/users/u_dev", {"disabled": True})
        self.assertEqual(self.invite(who=ASHA, to="u_dev").status_code, 409)
        self.assertEqual(self.invite(who=DEV, to="u_asha").status_code, 403)

    def test_one_live_invite_at_a_time(self):
        first = self.invite().json()
        r = self.invite(to="u_asha")
        self.assertEqual(r.status_code, 409)
        self.assertIn("already have an invite out", r.json()["detail"])
        # someone else can still invite the same person
        self.assertEqual(self.invite(who=ASHA, to="u_meera").status_code, 201)
        # cancelling frees it; so does an answer; so does time
        self.assertEqual(self.post(f"/api/matches/{first['id']}/cancel", None, KABIR).json()["status"], "cancelled")
        second = self.invite(to="u_asha")
        self.assertEqual(second.status_code, 201)
        self.post(f"/api/matches/{second.json()['id']}/decline", None, ASHA)
        third = self.invite(to="u_meera")
        self.assertEqual(third.status_code, 201)
        self.clock.advance(minutes=6)
        self.assertEqual(self.invite(to="u_asha").status_code, 201)

    def test_decline_cancel_and_who_may(self):
        m = self.invite().json()
        mid = m["id"]
        self.assertEqual(self.post(f"/api/matches/{mid}/accept", None, KABIR).status_code, 409)       # not for the one who invited
        self.assertEqual(self.post(f"/api/matches/{mid}/decline", None, KABIR).status_code, 409)
        self.assertEqual(self.post(f"/api/matches/{mid}/cancel", None, MEERA).status_code, 409)       # only the sender cancels
        self.assertEqual(self.post(f"/api/matches/{mid}/accept", None, ASHA).status_code, 404)
        self.assertEqual(self.post("/api/matches/nope/accept", None, MEERA).status_code, 404)
        self.assertEqual(self.post(f"/api/matches/{mid}/decline", None, MEERA).json()["status"], "declined")
        self.assertEqual(self.post(f"/api/matches/{mid}/accept", None, MEERA).status_code, 409)
        self.assertEqual(self.post(f"/api/matches/{mid}/cancel", None, KABIR).status_code, 409)
        self.assertEqual(self.get("/api/matches", MEERA).json()["waiting"], [])

    def test_expires_after_five_minutes(self):
        mid = self.invite().json()["id"]
        self.clock.advance(minutes=4, seconds=50)
        self.assertEqual(self.match(mid, MEERA)["status"], "invited")
        self.clock.advance(seconds=20)
        r = self.post(f"/api/matches/{mid}/accept", None, MEERA)
        self.assertEqual(r.status_code, 409)
        self.assertIn("run out", r.json()["detail"])
        self.assertEqual(self.match(mid)["status"], "expired")
        self.assertEqual(self.get("/api/matches", MEERA).json()["waiting"], [])

    def test_accepting_needs_you_to_be_allowed_now(self):
        mid = self.invite().json()["id"]
        self.make_child(MEERA, minutesSchool=0, minutesWeekend=0)
        r = self.post(f"/api/matches/{mid}/accept", None, MEERA)
        self.assertEqual(r.status_code, 409)
        self.assertIn("No play time left", r.json()["detail"])
        self.assertEqual(self.match(mid)["status"], "invited")

    def test_practice_is_proposed_and_accepting_agrees(self):
        m = self.invite(practice=True).json()
        self.assertTrue(m["practice"])
        self.assertTrue(self.post(f"/api/matches/{m['id']}/accept", None, MEERA).json()["practice"])
        # the other can't turn a ranked match into Practice by joining: it stays what was proposed
        self.get("/api/me", DEV)
        m2 = self.invite(who=ASHA, to="u_dev").json()
        self.assertFalse(m2["practice"])

    def test_rematch_is_marked(self):
        mid = self.new_match()
        self.finish(mid, KABIR, 10)
        self.finish(mid, MEERA, 20)
        m = self.invite(who=MEERA, to="u_kabir", rematchOf=mid).json()
        self.assertEqual(m["rematchOf"], mid)

    def test_cannot_start_another_while_in_a_match(self):
        self.new_match()
        r = self.invite(who=KABIR, to="u_asha")
        self.assertEqual(r.status_code, 409)
        self.assertIn("already in a match", r.json()["detail"])
        r = self.invite(who=ASHA, to="u_kabir")
        self.assertEqual(r.status_code, 409)
        self.assertIn("already playing a match", r.json()["detail"])


class TestNotification(Together):
    def setUp(self):
        super().setUp()
        self.ha.notify_services = ["meera_phone", "kabir_phone"]
        self.post("/api/admin/users/u_meera/notify", {"service": "notify.meera_phone"}, ASHA)
        self.post("/api/admin/users/u_kabir/notify", {"service": "notify.kabir_phone"}, ASHA)
        self.ha.requests.clear()
        self._panel = config.INGRESS_PANEL
        config.INGRESS_PANEL = "/hassio/ingress/local_household_arcade"

    def tearDown(self):
        config.INGRESS_PANEL = self._panel

    def test_the_invited_phone_gets_join_and_not_now(self):
        mid = self.invite().json()["id"]
        sent = self.ha.notifications()
        self.assertEqual([n for n, _ in sent], ["meera_phone"])
        body = sent[0][1]
        self.assertEqual(body["message"], "Kabir Rao challenges you to Lane Racer.")
        self.assertEqual(body["data"]["url"], f"/hassio/ingress/local_household_arcade#/home/join/{mid}")
        self.assertEqual([(a["title"], a["uri"]) for a in body["data"]["actions"]],
                         [("Join", f"/hassio/ingress/local_household_arcade#/home/join/{mid}"),
                          ("Not now", f"/hassio/ingress/local_household_arcade#/home/decline/{mid}")])

    def test_practice_and_rematch_wording(self):
        self.invite(practice=True)
        self.assertIn("(Practice, nothing is saved)", self.ha.notifications()[0][1]["message"])
        self.ha.requests.clear()
        m = self.invite(who=MEERA, to="u_kabir", rematchOf="x").json()
        self.assertEqual(m["rematchOf"], "x")
        self.assertIn("Meera Rao wants a rematch in Lane Racer", self.ha.notifications()[-1][1]["message"])

    def test_no_actions_without_a_panel_path(self):
        config.INGRESS_PANEL = None
        self.invite()
        self.assertNotIn("actions", self.ha.notifications()[0][1].get("data", {}))

    def test_the_app_setting_and_the_persons_own_choice(self):
        self.settings({"notify_invites": False})
        m = self.invite().json()
        self.assertEqual(self.ha.notifications(), [])
        self.assertEqual(len(self.get("/api/matches", MEERA).json()["waiting"]), 1)     # the banner is still there
        self.post(f"/api/matches/{m['id']}/cancel", None, KABIR)
        self.settings({"notify_invites": True})
        self.put("/api/prefs", {"receiveNotifications": False}, MEERA)
        self.invite()
        self.assertEqual(self.ha.notifications(), [])
        self.put("/api/prefs", {"receiveNotifications": True}, MEERA)

    def test_default_is_on(self):
        self.assertTrue(self.get("/api/admin/settings", ASHA).json()["values"]["notify_invites"])


class TestSessionsAndResults(Together):
    def test_both_phones_get_the_same_game(self):
        mid = self.new_match()
        a, b = self.session(mid, KABIR), self.session(mid, MEERA)
        self.assertEqual((a.status_code, b.status_code), (201, 201), a.text + b.text)
        a, b = a.json(), b.json()
        self.assertEqual(a["seed"], b["seed"])
        self.assertEqual((a["mode"], a["matchId"]), (MODE, mid))
        self.assertEqual(a["levels"], b["levels"])
        self.assertEqual(self.match(mid)["seed"] if "seed" in self.match(mid) else a["seed"], a["seed"])

    def test_level_modes_share_the_level_list(self):
        mid = self.new_match(mode="stages")
        a, b = self.session(mid, KABIR).json(), self.session(mid, MEERA).json()
        self.assertTrue(a["levels"])
        self.assertEqual(a["levels"], b["levels"])
        with db.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT level_count FROM play_sessions WHERE id = ?", (a["id"],)).fetchone()[0], len(a["levels"]))

    def test_session_checks(self):
        m = self.invite().json()["id"]
        self.assertEqual(self.session(m, MEERA).status_code, 409)                  # not started yet
        self.post(f"/api/matches/{m}/accept", None, MEERA)
        self.assertEqual(self.session(m, ASHA).status_code, 404)                   # not in it
        self.assertEqual(self.session("nope", KABIR).status_code, 404)
        self.assertEqual(self.session(m, KABIR, game="snake").status_code, 422)    # another game
        self.assertEqual(self.post("/api/sessions", {"game": GAME, "matchId": 5}, KABIR).status_code, 422)
        self.assertEqual(self.post("/api/sessions", {"game": GAME, "matchId": m, "resume": True}, KABIR).status_code, 422)
        self.assertEqual(self.session(m, KABIR).status_code, 201)
        self.assertEqual(self.session(m, KABIR).status_code, 409)                  # once each

    def test_child_limits_apply_to_the_match_session(self):
        mid = self.new_match()
        self.make_child(MEERA, minutesSchool=0, minutesWeekend=0)
        r = self.session(mid, MEERA)
        self.assertEqual(r.status_code, 409)
        self.assertIn("No play time left", r.json()["detail"])

    def test_the_higher_score_wins_and_each_score_is_saved(self):
        mid = self.new_match()
        sa, sb = self.session(mid, KABIR).json()["id"], self.session(mid, MEERA).json()["id"]
        r = self.finish(mid, KABIR, 400, sid=sa)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["saved"])
        m = self.match(mid, MEERA)
        self.assertEqual(m["status"], "playing")                                   # waits for the other
        self.assertEqual([p["final"] for p in m["players"]], [True, False])
        r = self.finish(mid, MEERA, 700, sid=sb)
        self.assertTrue(r.json()["saved"])
        for who, result in ((KABIR, "lost"), (MEERA, "won")):
            m = self.match(mid, who)
            self.assertEqual((m["status"], m["winner"], m["endReason"]), ("done", "u_meera", "finished"))
            self.assertEqual({p["id"]: p["result"] for p in m["players"]}["u_" + who["X-Remote-User-Id"][2:]], result)
        # saved as normal games
        mine = self.get("/api/scores/mine", MEERA).json()
        self.assertEqual([(b["game"], b["score"]) for b in mine["bests"]], [(GAME, 700)])
        self.assertEqual(self.get(f"/api/leaderboard?game={GAME}&mode={MODE}", MEERA).json()["rows"][0]["score"], 700)
        self.assertEqual(self.get("/api/matches", KABIR).json()["recent"][0]["id"], mid)

    def test_a_tie_is_a_draw(self):
        mid = self.new_match()
        self.finish(mid, KABIR, 300)
        self.finish(mid, MEERA, 300, seconds=45)
        m = self.match(mid)
        self.assertEqual((m["status"], m["winner"]), ("done", None))
        self.assertEqual([p["result"] for p in m["players"]], ["draw", "draw"])

    def test_practice_scores_are_not_saved_but_the_race_is_decided(self):
        mid = self.new_match(practice=True)
        r = self.finish(mid, KABIR, 100)
        self.assertEqual((r.json()["saved"], r.json()["reason"]), (False, "practice"))
        self.finish(mid, MEERA, 50)
        self.assertEqual(self.match(mid)["winner"], "u_kabir")
        with db.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM scores").fetchone()[0], 0)
        self.assertEqual(self.get("/api/against", KABIR).json()["people"], [])      # not counted head to head

    def test_an_impossible_score_loses(self):
        mid = self.new_match()
        r = self.finish(mid, KABIR, 2_999_999, seconds=5)
        self.assertEqual(r.status_code, 422)
        self.finish(mid, MEERA, 40)
        m = self.match(mid)
        self.assertEqual((m["status"], m["winner"]), ("done", "u_meera"))
        self.assertEqual({p["id"]: p["score"] for p in m["players"]}, {"u_kabir": 0, "u_meera": 40})

    def test_a_score_after_the_end_is_refused_once(self):
        mid = self.new_match()
        sid = self.session(mid, KABIR).json()["id"]
        self.assertEqual(self.finish(mid, KABIR, 10, sid=sid).status_code, 200)
        self.assertEqual(self.finish(mid, KABIR, 99, sid=sid).status_code, 409)
        self.assertEqual(self.match(mid)["players"][0]["score"], 10)

    def test_the_one_who_left_loses(self):
        mid = self.new_match()
        sa = self.session(mid, KABIR).json()["id"]
        sb = self.session(mid, MEERA).json()["id"]
        self.finish(mid, KABIR, 120, sid=sa)
        self.assertEqual(self.match(mid, KABIR)["status"], "playing")
        # Meera's phone goes away: no heartbeat for two minutes (housekeeping closes the session), or she leaves
        self.post(f"/api/sessions/{sb}/end", {"activeSeconds": 5}, MEERA)
        m = self.match(mid, KABIR)
        self.assertEqual((m["status"], m["endReason"], m["winner"]), ("done", "left", "u_kabir"))
        self.assertEqual({p["id"]: p["result"] for p in m["players"]}, {"u_kabir": "won", "u_meera": "lost"})

    def test_a_stale_session_counts_as_left(self):
        mid = self.new_match()
        self.session(mid, KABIR)
        sb = self.session(mid, MEERA).json()["id"]
        self.finish(mid, KABIR, 50, sid=self.get_sid(mid, KABIR))
        self.clock.advance(minutes=3)
        with db.get_conn() as conn:
            housekeeping.close_stale_sessions(conn)
            together.housekeeping(conn)
        m = self.match(mid)
        self.assertEqual((m["status"], m["endReason"], m["winner"]), ("done", "left", "u_kabir"))

    def get_sid(self, mid, who):
        with db.get_conn() as conn:
            return conn.execute("SELECT session_id FROM match_players WHERE match_id = ? AND user_id = ?",
                                (mid, who["X-Remote-User-Id"])).fetchone()[0]

    def test_a_player_who_never_started_is_left_after_two_minutes(self):
        mid = self.new_match()
        self.finish(mid, KABIR, 60)
        self.assertEqual(self.match(mid)["status"], "playing")
        self.clock.advance(minutes=3)
        self.assertEqual(self.match(mid)["status"], "done")
        self.assertEqual(self.match(mid)["winner"], "u_kabir")

    def test_both_left_nobody_wins(self):
        mid = self.new_match()
        sa = self.session(mid, KABIR).json()["id"]
        sb = self.session(mid, MEERA).json()["id"]
        self.post(f"/api/sessions/{sa}/end", {"activeSeconds": 1}, KABIR)
        self.post(f"/api/sessions/{sb}/end", {"activeSeconds": 1}, MEERA)
        m = self.match(mid)
        self.assertEqual((m["status"], m["endReason"], m["winner"]), ("done", "left", None))
        self.assertEqual(self.get("/api/against", KABIR).json()["people"], [])

    def test_the_fastest_rule(self):
        old = games.GAMES["racer"].get("race", "unset")
        games.GAMES["racer"]["race"] = {"rule": "fastest"}
        try:
            # both solved: the shorter game wins, whatever the score
            mid = self.new_match()
            self.finish(mid, KABIR, 100, seconds=90, won=True)
            self.finish(mid, MEERA, 10, seconds=60, won=True)
            self.assertEqual(self.match(mid)["winner"], "u_meera")
            # only one solved
            mid = self.new_match()
            self.finish(mid, KABIR, 5, seconds=50, won=False)
            self.finish(mid, MEERA, 1, seconds=80, won=True)
            self.assertEqual(self.match(mid)["winner"], "u_meera")
            # nobody solved: the higher score
            mid = self.new_match()
            self.finish(mid, KABIR, 50, seconds=50)
            self.finish(mid, MEERA, 40, seconds=40)
            self.assertEqual(self.match(mid)["winner"], "u_kabir")
        finally:
            if old == "unset":
                games.GAMES["racer"].pop("race", None)
            else:
                games.GAMES["racer"]["race"] = old

    def test_decide_rules(self):
        p = lambda uid, score, seconds, won=False: {"user_id": uid, "score": score, "seconds": seconds, "won": won}
        games.GAMES["racer"]["race"] = {"rule": "score", "tiebreak": "faster"}
        try:
            self.assertEqual(together.decide("racer", [p("a", 10, 30), p("b", 10, 20)]), ("b", {"b": "won", "a": "lost"}))
            self.assertEqual(together.decide("racer", [p("a", 10, 20), p("b", 10, 20)])[0], None)
            self.assertEqual(together.decide("racer", [p("a", 11, 90), p("b", 10, 20)])[0], "a")
        finally:
            games.GAMES["racer"].pop("race", None)


class TestLiveNumbers(Together):
    def test_state_over_http(self):
        mid = self.new_match()
        r = self.post(f"/api/matches/{mid}/state", {"score": 120, "level": 2, "over": False, "paused": False}, MEERA)
        self.assertEqual(r.status_code, 200, r.text)
        seen = {p["id"]: p for p in self.match(mid, KABIR)["players"]}
        self.assertEqual((seen["u_meera"]["score"], seen["u_meera"]["level"], seen["u_meera"]["over"]), (120, 2, False))
        self.assertTrue(seen["u_meera"]["connected"])
        self.assertFalse(seen["u_kabir"]["connected"] and seen["u_kabir"]["score"])
        self.post(f"/api/matches/{mid}/state", {"score": 5, "level": 1, "paused": True}, MEERA)
        self.assertTrue({p["id"]: p for p in self.match(mid)["players"]}["u_meera"]["paused"])
        # it is only for show: clamped, junk ignored, not a score
        self.post(f"/api/matches/{mid}/state", {"score": 10 ** 12, "level": -4}, MEERA)
        seen = {p["id"]: p for p in self.match(mid)["players"]}["u_meera"]
        self.assertEqual((seen["score"], seen["level"]), (games.GAMES[GAME]["max_score"], 1))
        self.post(f"/api/matches/{mid}/state", {"score": "lots", "level": None}, MEERA)
        self.assertEqual({p["id"]: p for p in self.match(mid)["players"]}["u_meera"]["score"], games.GAMES[GAME]["max_score"])
        self.assertEqual(self.post(f"/api/matches/{mid}/state", {"score": 1}, ASHA).status_code, 404)
        with db.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM scores").fetchone()[0], 0)

    def test_a_long_poll_waits_for_a_change(self):
        mid = self.new_match()
        v = self.match(mid, KABIR)["v"]
        started = time.monotonic()
        # nothing changes: it comes back after the wait with the same picture
        r = self.get(f"/api/matches/{mid}?since={v}&wait=1", KABIR)
        self.assertEqual(r.status_code, 200)
        self.assertGreaterEqual(time.monotonic() - started, 0.9)
        self.assertEqual(r.json()["v"], v)
        # a change comes back at once
        threading.Timer(0.4, lambda: self.post(f"/api/matches/{mid}/state", {"score": 77, "level": 1}, MEERA)).start()
        started = time.monotonic()
        r = self.get(f"/api/matches/{mid}?since={self.match(mid)['v']}&wait=10", KABIR)
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual({p["id"]: p for p in r.json()["players"]}["u_meera"]["score"], 77)

    def test_a_long_poll_without_since_is_a_plain_read(self):
        mid = self.new_match()
        started = time.monotonic()
        self.get(f"/api/matches/{mid}?wait=5", KABIR)
        self.assertLess(time.monotonic() - started, 2)

    def test_ending_the_match_wakes_the_poll(self):
        mid = self.new_match()
        sa = self.session(mid, KABIR).json()["id"]
        sb = self.session(mid, MEERA).json()["id"]
        self.finish(mid, KABIR, 10, sid=sa)
        v = self.match(mid, MEERA)["v"]
        threading.Timer(0.4, lambda: self.finish(mid, MEERA, 20, sid=sb)).start()
        r = self.get(f"/api/matches/{mid}?since={v}&wait=10", KABIR)
        self.assertEqual(r.json()["status"], "done")


class TestWebSocket(Together):
    def ws(self, mid, who=KABIR, **kw):
        return self.c.websocket_connect(f"/api/matches/{mid}/live", headers=who, **kw)

    def next_match(self, ws, until=None, tries=40):
        for _ in range(tries):
            m = ws.receive_json()
            self.assertEqual(m["t"], "match")
            if until is None or until(m):
                return m
        self.fail("the picture never arrived")

    def test_pushes_the_match_and_relays_state_between_two_phones(self):
        mid = self.new_match()
        with self.ws(mid, KABIR) as a, self.ws(mid, MEERA) as b:
            first = self.next_match(a)
            self.assertEqual((first["id"], first["status"]), (mid, "playing"))
            self.assertEqual(first["players"][0]["you"], True)
            self.next_match(b)
            b.send_json({"t": "state", "score": 321, "level": 3, "over": False, "paused": False})
            m = self.next_match(a, lambda m: {p["id"]: p for p in m["players"]}["u_meera"]["score"] == 321)
            seen = {p["id"]: p for p in m["players"]}["u_meera"]
            self.assertEqual((seen["level"], seen["connected"]), (3, True))
            a.send_json({"t": "ping"})
            a.send_json({"t": "state", "score": 9, "level": 1, "over": True})
            m = self.next_match(b, lambda m: {p["id"]: p for p in m["players"]}["u_kabir"]["over"])
            self.assertEqual({p["id"]: p for p in m["players"]}["u_kabir"]["score"], 9)

    def test_the_end_is_pushed(self):
        mid = self.new_match()
        sa = self.session(mid, KABIR).json()["id"]
        sb = self.session(mid, MEERA).json()["id"]
        with self.ws(mid, KABIR) as a:
            self.next_match(a)
            self.finish(mid, KABIR, 5, sid=sa)
            self.finish(mid, MEERA, 8, sid=sb)
            m = self.next_match(a, lambda m: m["status"] == "done")
            self.assertEqual(m["winner"], "u_meera")

    def test_strangers_and_wrong_places_are_turned_away(self):
        mid = self.new_match()
        with self.assertRaises(WebSocketDisconnect):
            with self.ws(mid, ASHA) as w:                       # not in the match
                w.receive_json()
        with self.assertRaises(WebSocketDisconnect):
            with self.c.websocket_connect(f"/api/matches/{mid}/live", headers={}) as w:   # no identity
                w.receive_json()
        with self.assertRaises(WebSocketDisconnect):
            with self.ws("nope", KABIR) as w:
                w.receive_json()
        # a source that isn't the Supervisor's ingress proxy (the middleware doesn't see WebSockets)
        outsider = TestClient(app, client=("10.0.0.5", 5555))
        with self.assertRaises(WebSocketDisconnect):
            with outsider.websocket_connect(f"/api/matches/{mid}/live", headers=KABIR) as w:
                w.receive_json()
        # another site's page
        evil = dict(KABIR, Origin="https://evil.example")
        with self.assertRaises(WebSocketDisconnect):
            with self.c.websocket_connect(f"/api/matches/{mid}/live", headers=evil) as w:
                w.receive_json()
        same = dict(KABIR, Origin="http://testserver")
        with self.c.websocket_connect(f"/api/matches/{mid}/live", headers=same) as w:
            self.assertEqual(w.receive_json()["t"], "match")

    def test_junk_messages_are_ignored(self):
        mid = self.new_match()
        with self.ws(mid, MEERA) as w:
            self.next_match(w)
            w.send_text("not json")
            w.send_json([1, 2])
            w.send_json({"t": "other"})
            w.send_json({"t": "state", "score": 5, "level": 1})
            self.next_match(w, lambda m: {p["id"]: p for p in m["players"]}["u_meera"]["score"] == 5)


class TestHeadToHead(Together):
    def test_wins_losses_draws_per_person_and_game(self):
        def race(a, b, sa, sb, game=GAME, mode=MODE):
            mid = self.new_match(a, b, game=game, mode=mode)
            self.finish(mid, a, sa, game=game)
            self.finish(mid, b, sb, game=game)
        race(KABIR, MEERA, 300, 100)                       # Kabir wins
        race(MEERA, KABIR, 500, 100)                       # Kabir loses
        race(KABIR, MEERA, 80, 80)                         # draw
        race(KABIR, MEERA, 200, 100, game="snake", mode="walls-normal")
        race(KABIR, ASHA, 10, 90)
        # a match that was only invited, and one in Practice, don't count
        self.invite(who=KABIR, to="u_dev")
        data = self.get("/api/against", KABIR).json()["people"]
        by = {p["name"]: p for p in data}
        self.assertEqual([p["name"] for p in data], ["Meera Rao", "Asha Rao"])               # most games first
        meera = by["Meera Rao"]
        self.assertEqual((meera["games"], meera["wins"], meera["losses"], meera["draws"]), (4, 2, 1, 1))
        per = {g["game"]: g for g in meera["perGame"]}
        self.assertEqual((per[GAME]["games"], per[GAME]["wins"], per[GAME]["losses"], per[GAME]["draws"]), (3, 1, 1, 1))
        self.assertEqual((per["snake"]["games"], per["snake"]["wins"]), (1, 1))
        self.assertEqual(per[GAME]["name"], "Lane Racer")
        self.assertEqual((by["Asha Rao"]["games"], by["Asha Rao"]["losses"]), (1, 1))
        # and from the other side
        theirs = {p["name"]: p for p in self.get("/api/against", MEERA).json()["people"]}["Kabir Rao"]
        self.assertEqual((theirs["games"], theirs["wins"], theirs["losses"], theirs["draws"]), (4, 1, 2, 1))
        self.assertEqual(self.get("/api/against", DEV).json(), {"people": []})


class TestHousekeeping(Together):
    def test_old_invites_expire_and_live_numbers_are_forgotten(self):
        mid = self.invite().json()["id"]
        self.clock.advance(minutes=6)
        with db.get_conn() as conn:
            self.assertEqual(together.housekeeping(conn), 1)
            self.assertEqual(together.get(conn, mid)["status"], "expired")
        self.assertIn(mid, together.LIVE)
        self.clock.advance(minutes=11)
        with db.get_conn() as conn:
            together.housekeeping(conn)
        self.assertNotIn(mid, together.LIVE)

    def test_a_match_nobody_finished_ends_as_timed_out(self):
        mid = self.new_match()
        self.session(mid, KABIR)
        self.session(mid, MEERA)
        self.clock.advance(hours=8)
        with db.get_conn() as conn:
            together.housekeeping(conn)
        self.assertEqual(self.match(mid)["status"], "done")

    def test_keep_scores_for_removes_old_matches(self):
        mid = self.new_match()
        self.finish(mid, KABIR, 5)
        self.finish(mid, MEERA, 6)
        self.settings({"keep_scores_years": 1})
        self.clock.advance(days=400)
        housekeeping.run_blocking()
        with db.get_conn() as conn:
            self.assertIsNone(together.get(conn, mid))
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM match_players").fetchone()[0], 0)


class TestMigration(unittest.TestCase):
    def test_migration_six_tables(self):
        import sqlite3
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        self.assertEqual(db.migrate(conn, upto=5), 5)
        self.assertEqual(db.migrate(conn, upto=6), 6)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"matches", "match_players"} <= tables)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(play_sessions)")}
        self.assertIn("match_id", cols)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(matches)")}
        for c in ("id", "game", "mode", "kind", "seed", "levels", "practice", "status", "created_by", "created_at",
                  "started_at", "ended_at", "end_reason", "winner"):
            self.assertIn(c, cols)


if __name__ == "__main__":
    unittest.main()
