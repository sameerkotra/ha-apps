"""The 13 October review's fixes (1.12.4): children's play time that can't be talked down (sessions housekeeping closed,
many short games, midnight, the stop when time runs out), one result per game and one move per turn when two
requests race, request bodies counted as they arrive, the daily challenge's ranked try, the AI level builder's
triggers, invite limits, children's view of other people, the live link's limits, and the faster queries checked
against the old ones on seeded data. Every person here is invented."""
import _env  # noqa: F401  (must be first)

import contextlib
import json
import os
import random
import sqlite3
import threading
import unittest
from datetime import date, timedelta
from unittest import mock

from starlette.websockets import WebSocketDisconnect

from app import (ai_client, config, db, games, housekeeping, level_builder, levels, limits, progress, rules, scores,
                 together)
from app.level_kinds import _sorting
from app.routers import together as together_router
from base import ASHA, DEV, KABIR, MEERA, ApiBase, hdr

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def used(conn_user="u_kabir", d=None):
    with db.get_conn() as conn:
        return limits.used_seconds(conn, conn_user, d or config.today())


class Base(ApiBase):
    def beat(self, sid, active, h=KABIR, **extra):
        return self.post(f"/api/sessions/{sid}/beat", {"activeSeconds": active, **extra}, h)

    def end(self, sid, active, h=KABIR):
        return self.post(f"/api/sessions/{sid}/end", {"activeSeconds": active}, h)

    def score(self, sid, score, seconds, h=KABIR, level=1):
        return self.post("/api/scores", {"sessionId": sid, "score": score, "level": level, "seconds": seconds}, h)


# ---------------------------------------------------------------------------------------------------------------------
# A1, A2: play time
# ---------------------------------------------------------------------------------------------------------------------

class TestPlayTime(Base):
    def test_a_score_for_a_session_housekeeping_closed_still_counts_its_time(self):
        self.make_child(minutesSchool=120)
        sid = self.start().json()["id"]
        self.clock.advance(minutes=20)
        self.beat(sid, 0)                                   # the page says nothing was played
        self.assertEqual(used(), 5 * 60)                    # 20 minutes less 15 of pauses
        self.clock.advance(minutes=30)                      # and then the heartbeats stop
        self.assertEqual(housekeeping.run_blocking()["closed"], 1)
        self.assertEqual(used(), 7 * 60)                    # counted to the last heartbeat and 2 minutes more
        r = self.score(sid, 100, 600)                       # … but the game went on, and its score comes now
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["saved"])
        self.assertEqual(used(), 35 * 60)                   # 50 minutes since it began, less 15 of pauses

    def test_many_short_games_share_one_days_pauses(self):
        self.make_child(minutesSchool=120)
        for _ in range(4):                                   # 14 minutes each, all "paused"
            sid = self.start().json()["id"]
            self.clock.advance(minutes=14)
            self.end(sid, 0)
        # 15 minutes a game, 30 a day: the first two leave out 28, the third 2, the fourth nothing
        self.assertEqual(round(used()), (12 + 14) * 60)

    def test_an_adult_keeps_the_pauses_of_each_game(self):
        for _ in range(4):
            sid = self.start(h=MEERA).json()["id"]
            self.clock.advance(minutes=14)
            self.end(sid, 0, h=MEERA)
        self.assertEqual(used("u_meera"), 0)

    def test_a_game_over_midnight_counts_on_both_days(self):
        self.clock.advance(hours=11, minutes=50)            # Monday 23:50
        sid = self.start().json()["id"]
        self.clock.advance(minutes=20)                      # Tuesday 00:10
        self.end(sid, 20 * 60)
        self.assertEqual(round(used(d=date(2026, 9, 22))), 600)
        self.assertEqual(round(used(d=date(2026, 9, 21))), 600)

    def test_a_game_stopped_when_time_ran_out_cant_claim_the_time_after(self):
        self.make_child(minutesSchool=10)
        sid = self.start().json()["id"]
        self.clock.advance(minutes=10)
        b = self.beat(sid, 600).json()
        self.assertTrue(b["stop"])
        self.clock.advance(minutes=20)                      # a page that kept on playing
        r = self.score(sid, 100, 30 * 60)
        self.assertEqual(r.status_code, 422)
        self.assertIn("seen being played", r.json()["detail"])

    def test_quiet_hours_dont_stop_a_running_game(self):
        self.make_child(quietSchoolFrom="12:01", quietSchoolTo="13:00")
        sid = self.start().json()["id"]
        self.clock.advance(minutes=5)
        b = self.beat(sid, 300).json()
        self.assertTrue(b["playTime"]["quiet"])
        self.assertFalse(b["stop"])

    def test_a_result_cant_claim_more_than_the_heartbeats_saw(self):
        sid = self.start().json()["id"]
        self.clock.advance(seconds=60)
        self.beat(sid, 10)                                  # 50 seconds of it paused
        r = self.score(sid, 100, 60)
        self.assertEqual(r.status_code, 422)
        sid = self.start().json()["id"]
        self.clock.advance(seconds=60)
        self.beat(sid, 10)
        self.clock.advance(seconds=20)
        self.assertEqual(self.score(sid, 100, 30).status_code, 200)


# ---------------------------------------------------------------------------------------------------------------------
# A3: two requests at once
# ---------------------------------------------------------------------------------------------------------------------

def together_at_once(fn, n=4):
    out = []
    lock = threading.Lock()

    def go():
        r = fn()
        with lock:
            out.append(r)
    threads = [threading.Thread(target=go) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return out


class TestRaces(Base):
    def test_a_score_sent_twice_at_once_is_kept_once(self):
        sid = self.start().json()["id"]
        self.clock.advance(seconds=60)
        codes = sorted(r.status_code for r in together_at_once(lambda: self.score(sid, 100, 60)))
        self.assertEqual(codes, [200, 409, 409, 409])
        with db.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM scores").fetchone()[0], 1)

    def turn_match(self):
        mid = self.post("/api/matches", {"game": "tictactoe", "mode": "phones", "opponents": ["u_meera"]}).json()["id"]
        self.post(f"/api/matches/{mid}/accept", None, MEERA)
        p = self.get(f"/api/matches/{mid}/turns", KABIR).json()
        mover = KABIR if p["toMove"] == [p["seat"]] else MEERA
        return mid, mover

    def test_one_move_per_turn_when_two_arrive_together(self):
        mid, mover = self.turn_match()
        cells = iter(range(9))
        results = together_at_once(lambda: self.post(f"/api/matches/{mid}/move", {"move": {"cell": next(cells)}, "n": 0}, mover))
        self.assertEqual(sorted(r.status_code for r in results), [200, 409, 409, 409])
        with db.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM match_moves WHERE match_id = ?", (mid,)).fetchone()[0], 1)

    def test_a_move_needs_the_number_of_moves_seen(self):
        mid, mover = self.turn_match()
        r = self.post(f"/api/matches/{mid}/move", {"move": {"cell": 4}}, mover)
        self.assertEqual(r.status_code, 422)
        self.assertIn("n", r.json()["detail"])
        self.assertEqual(self.post(f"/api/matches/{mid}/move", {"move": {"cell": 4}, "n": True}, mover).status_code, 422)
        self.assertEqual(self.post(f"/api/matches/{mid}/move", {"move": {"cell": 4}, "n": 0}, mover).status_code, 200)

    def test_only_a_clash_of_move_numbers_reads_as_another_move_first(self):
        from app import turns
        mid, _mover = self.turn_match()

        class Failing:                                   # a database that fails for another reason
            def __init__(self, conn):
                self.conn = conn

            def execute(self, sql, *a):
                if sql.startswith("INSERT INTO match_moves"):
                    raise sqlite3.OperationalError("disk I/O error")
                return self.conn.execute(sql, *a)

        with db.get_conn() as conn:
            m = conn.execute("SELECT * FROM matches WHERE id = ?", (mid,)).fetchone()
            st, seat = turns.state_of(m), turns.to_move_seats(m)[0]
            with self.assertRaises(sqlite3.OperationalError):          # not "another move came first"
                turns._store(Failing(conn), m, st, seat, {"cell": 0})
            conn.execute("INSERT INTO match_moves (match_id, number, seat, move, at) VALUES (?, 1, 1, '{}', 'x')", (mid,))
            class Clash(Failing):                        # the other phone's move took number 1 meanwhile
                def execute(self, sql, *a):
                    if sql.startswith("SELECT COUNT(*) FROM match_moves"):
                        return self.conn.execute("SELECT 0")
                    return self.conn.execute(sql, *a)

            with self.assertRaises(turns.TurnsError) as e:
                turns._store(Clash(conn), m, st, seat, {"cell": 0})
            self.assertEqual(e.exception.status, 409)
            conn.rollback()


# ---------------------------------------------------------------------------------------------------------------------
# A4, A11, A12: request and message sizes, the watchdog
# ---------------------------------------------------------------------------------------------------------------------

class TestSizes(Base):
    def test_a_body_without_a_content_length_is_counted(self):
        def chunks():
            yield b'{"game": "snake", "mode": "'
            for _ in range(20):
                yield b"x" * 4096
            yield b'"}'
        r = self.c.post("/api/sessions", content=chunks(), headers=dict(KABIR, **{"Content-Type": "application/json"}))
        self.assertEqual(r.status_code, 413)
        r = self.c.post("/api/sessions", content=b"{" + b" " * (70 * 1024) + b"}",
                        headers=dict(KABIR, **{"Content-Type": "application/json"}))
        self.assertEqual(r.status_code, 413)
        self.assertEqual(self.start().status_code, 201)            # an ordinary one still works

    def test_versioned_files_are_kept_for_good_and_the_page_never(self):
        r = self.c.get(f"/games/kit.js?v={config.APP_VERSION}", headers=KABIR)
        self.assertEqual(r.headers.get("cache-control"), "public, max-age=31536000, immutable")
        r = self.c.get(f"/games/parking-worker.js?v={config.APP_VERSION}", headers=KABIR)
        self.assertEqual(r.status_code, 200)
        self.assertIn("immutable", r.headers.get("cache-control", ""))
        self.assertIn("no-store", self.c.get("/", headers=KABIR).headers.get("cache-control", ""))
        self.assertNotIn("immutable", self.c.get("/games/kit.js", headers=KABIR).headers.get("cache-control", ""))
        self.assertEqual(self.c.get("/api/health").headers.get("cache-control"), "no-store")

    def test_dockerfile_caps_websocket_messages_and_config_has_a_watchdog(self):
        with open(os.path.join(HERE, "Dockerfile")) as f:
            docker = f.read()
        self.assertIn('"--ws-max-size", "65536"', docker)
        self.assertIn("FROM python:3.12-alpine3.22", docker)
        with open(os.path.join(HERE, "config.yaml")) as f:
            self.assertIn("watchdog: http://[HOST]:[PORT:8104]/api/health", f.read())


class TestLiveLinkLimits(Base):
    def race(self):
        mid = self.post("/api/matches", {"game": "racer", "mode": "three", "opponents": ["u_meera"], "kind": "race"}).json()["id"]
        self.post(f"/api/matches/{mid}/accept", None, MEERA)
        return mid

    def test_a_person_has_a_few_live_links_at_most(self):
        mid = self.race()
        with contextlib.ExitStack() as stack:
            for _ in range(together_router.WS_PER_USER):
                w = stack.enter_context(self.c.websocket_connect(f"/api/matches/{mid}/live", headers=KABIR))
                self.assertEqual(w.receive_json()["t"], "match")
            with self.assertRaises(WebSocketDisconnect):
                with self.c.websocket_connect(f"/api/matches/{mid}/live", headers=KABIR) as w:
                    w.receive_json()
            with self.c.websocket_connect(f"/api/matches/{mid}/live", headers=MEERA) as w:     # someone else may
                self.assertEqual(w.receive_json()["t"], "match")
        with self.c.websocket_connect(f"/api/matches/{mid}/live", headers=KABIR) as w:         # and again once closed
            self.assertEqual(w.receive_json()["t"], "match")

    def test_a_flood_of_messages_closes_the_link(self):
        mid = self.race()
        with self.c.websocket_connect(f"/api/matches/{mid}/live", headers=KABIR) as w:
            w.receive_json()
            for i in range(together_router.WS_RATE * 3):
                w.send_json({"t": "state", "score": i})
            with self.assertRaises(WebSocketDisconnect):
                for _ in range(200):
                    w.receive_json()


# ---------------------------------------------------------------------------------------------------------------------
# A6: automatic level builds
# ---------------------------------------------------------------------------------------------------------------------

class TestAutoBuilds(Base):
    def test_a_heartbeat_level_is_the_one_the_app_counted(self):
        sid = self.start("snake", "maze").json()["id"]
        self.clock.advance(seconds=20)
        with mock.patch.object(level_builder, "maybe_auto") as auto:
            self.beat(sid, 20, level=9)                           # a forged "nearly at the end"
        self.assertEqual(auto.call_args[0][1], 2)                 # the app holds level 2 (one a heartbeat)

    def test_one_automatic_build_per_list_in_a_while(self):
        self.settings({"ai_levels_enabled": True, "ai_url": "http://192.168.1.10:11434", "ai_model": "tiny"})
        with db.get_conn() as conn:
            conn.execute("INSERT INTO level_builds (id, game, requested_by, count, status, created_at) "
                         "VALUES ('b1', 'snake', 'auto', 5, 'done', ?)", (config.now_iso(),))
            count = levels.playable_count(conn, "snake")
        with mock.patch.object(level_builder, "request_build") as req:
            level_builder.maybe_auto("snake", count, count)
            req.assert_not_called()
            self.clock.advance(minutes=31)
            level_builder.maybe_auto("snake", count, count)
            req.assert_called_once()

    def test_a_level_in_an_unexpected_shape_is_turned_down_not_the_build(self):
        self.settings({"ai_levels_enabled": True, "ai_url": "http://192.168.1.10:11434", "ai_model": "tiny"})
        answer = ai_client.Reply(text=json.dumps({"levels": [{"name": "Odd"}]}), raw="", input_tokens=1, output_tokens=1)
        with mock.patch.object(level_builder, "_generate", return_value=answer), \
                mock.patch.object(levels, "add", side_effect=TypeError("unhashable type: 'list'")):
            b = level_builder.request_build("hop", 1, "test")
            out = level_builder.run_build(b["id"])
        self.assertGreaterEqual(out["rejected"], 1)
        self.assertNotIn("stopped unexpectedly", out["error"] or "")
        self.assertTrue(any("turned down" in line for line in out["log"]))

    def test_a_lane_kind_that_isnt_text_is_a_level_error(self):
        from app.level_common import LevelError
        from app.level_kinds import hop
        with self.assertRaises(LevelError):
            hop.Lane()({"kind": ["road"], "pattern": "." * 13, "speed": 1, "dir": 1}, "lanes[0]")


# ---------------------------------------------------------------------------------------------------------------------
# A7, A10, AI4, AI5: playing together
# ---------------------------------------------------------------------------------------------------------------------

class TestInvites(Base):
    def turns_invite(self, to, who=KABIR, game="tictactoe"):
        return self.post("/api/matches", {"game": game, "mode": "phones", "opponents": [to]}, who)

    def test_open_turn_invites_are_capped(self):
        for _ in range(together.MAX_OPEN_PER_PERSON):
            self.assertEqual(self.turns_invite("u_meera").status_code, 201)
        r = self.turns_invite("u_meera")
        self.assertEqual(r.status_code, 409)
        self.assertIn("waiting already", r.json()["detail"])
        people = [f"u_p{i}" for i in range(7)]
        for p in people:
            self.get("/api/me", hdr(p))
        made = together.MAX_OPEN_PER_PERSON
        for p in people:
            for _ in range(together.MAX_OPEN_PER_PERSON):
                r = self.turns_invite(p)
                if made < together.MAX_OPEN_INVITES:
                    self.assertEqual(r.status_code, 201, r.text)
                    made += 1
                else:
                    self.assertEqual(r.status_code, 409)
        self.assertEqual(made, together.MAX_OPEN_INVITES)

    def test_invite_notifications_are_spaced_out(self):
        self.assertTrue(together._push_ok("u_kabir", "u_meera"))
        self.assertFalse(together._push_ok("u_kabir", "u_meera"))
        self.assertTrue(together._push_ok("u_kabir", "u_asha"))
        together._pushed[("u_kabir", "u_meera")] -= together.INVITE_PUSH_EVERY + 1
        self.assertTrue(together._push_ok("u_kabir", "u_meera"))

    def test_accepting_rechecks_the_inviter(self):
        mid = self.post("/api/matches", {"game": "racer", "mode": "three", "opponents": ["u_meera"], "kind": "race"}).json()["id"]
        self.make_child(KABIR, minutesSchool=0)                 # the inviter has no time left now
        r = self.post(f"/api/matches/{mid}/accept", None, MEERA)
        self.assertEqual(r.status_code, 409)
        self.assertIn("invite has ended", r.json()["detail"])
        m = self.get(f"/api/matches/{mid}", MEERA).json()
        self.assertEqual((m["status"], m["winner"]), ("cancelled", None))

    def test_a_child_sees_names_as_on_their_leaderboard_and_not_others_limits(self):
        self.make_child(MEERA, minutesSchool=0)
        self.make_child(KABIR, leaderboard="first")
        names = {p["id"]: p for p in self.get("/api/players?game=racer", KABIR).json()["players"]}
        self.assertEqual(names["u_meera"]["name"], "Meera")
        self.assertEqual(names["u_asha"]["name"], "Asha")
        self.assertEqual(names["u_meera"]["reason"], "Can't play right now.")
        adult = {p["id"]: p for p in self.get("/api/players?game=racer", ASHA).json()["players"]}
        self.assertEqual(adult["u_meera"]["name"], "Meera Rao")
        self.assertEqual(adult["u_meera"]["reason"], "No play time left today.")
        mid = self.post("/api/matches", {"game": "racer", "mode": "three", "opponents": ["u_kabir"], "kind": "race"}, ASHA).json()["id"]
        waiting = self.get("/api/matches", KABIR).json()["waiting"][0]
        self.assertEqual({p["id"]: p["name"] for p in waiting["players"]}, {"u_asha": "Asha", "u_kabir": "Kabir Rao"})
        self.assertEqual({p["id"]: p["name"] for p in self.get(f"/api/matches/{mid}", KABIR).json()["players"]}["u_asha"], "Asha")

    def test_a_match_of_a_game_this_version_doesnt_have(self):
        with db.get_conn() as conn:
            conn.execute("INSERT INTO matches (id, game, mode, kind, seed, status, created_by, created_at, started_at) "
                         "VALUES ('old', 'solitaire', 'klondike', 'turns', 1, 'playing', 'u_kabir', ?, ?)",
                         (config.now_iso(), config.now_iso()))
            conn.execute("INSERT INTO match_players (match_id, user_id, seat, invite_status) VALUES ('old', 'u_kabir', 1, 'accepted')")
            conn.execute("INSERT INTO match_players (match_id, user_id, seat, invite_status) VALUES ('old', 'u_meera', 2, 'accepted')")
        r = self.get("/api/matches", KABIR)
        self.assertEqual(r.status_code, 200, r.text)
        m = self.get("/api/matches/old", KABIR).json()
        self.assertEqual((m["status"], m["endReason"], m["known"], m["icon"]), ("done", "unavailable", False, "🎮"))

    def test_four_seats_never_look_like_the_next_change(self):
        together.LIVE.clear()
        for uid in ("a", "b", "c", "d"):
            together.mark_seen("m4", uid)
        all_on = together.signature("m4")
        together.touch("m4")
        self.assertGreater(together.signature("m4"), all_on)
        self.assertEqual(together.signature("m4") % 16, 15)


class TestLivePlumbing(Base):
    def test_set_state_reads_the_match_once_a_second_at_most(self):
        mid = self.post("/api/matches", {"game": "racer", "mode": "three", "opponents": ["u_meera"], "kind": "race"}).json()["id"]
        self.post(f"/api/matches/{mid}/accept", None, MEERA)
        together.LIVE.pop(mid, None)
        real = db.get_conn
        calls = []
        with mock.patch.object(together.db, "get_conn", side_effect=lambda: calls.append(1) or real()):
            for i in range(20):
                together.set_state(mid, "u_kabir", {"score": i})
            together.set_state(mid, "u_dev", {"score": 99})          # not in the match: ignored
        self.assertEqual(len(calls), 1)
        self.assertEqual(together.LIVE[mid]["players"]["u_kabir"]["score"], 19)
        self.assertNotIn("u_dev", together.LIVE[mid]["players"])

    def test_the_games_page_list_matches_the_old_one(self):
        def old_mine(conn, user_id):                  # 1.12.3's, which settled every match ever played
            out = {"waiting": [], "sent": [], "playing": [], "recent": []}
            for mid in [r["match_id"] for r in conn.execute("SELECT match_id FROM match_players WHERE user_id = ?", (user_id,))]:
                together.settle(conn, mid)
            for m in conn.execute("SELECT m.* FROM matches m JOIN match_players p ON p.match_id = m.id WHERE p.user_id = ? "
                                  "ORDER BY m.created_at DESC", (user_id,)).fetchall():
                if m["status"] == "invited":
                    mp = conn.execute("SELECT invite_status FROM match_players WHERE match_id = ? AND user_id = ?",
                                      (m["id"], user_id)).fetchone()
                    if m["created_by"] == user_id or (mp and mp["invite_status"] == "accepted"):
                        out["sent"].append(together.view(conn, m, user_id))
                    elif mp and mp["invite_status"] == "invited":
                        out["waiting"].append(together.view(conn, m, user_id))
                elif m["status"] == "playing":
                    me = next(p for p in together._players(conn, m["id"]) if p["user_id"] == user_id)
                    if not me["finished_at"]:
                        out["playing"].append(together.view(conn, m, user_id))
                elif m["status"] == "done" and len(out["recent"]) < together.RECENT_N:
                    out["recent"].append(together.view(conn, m, user_id))
            return out
        self.get("/api/me", DEV)
        for i in range(13):                                   # finished races
            mid = self.post("/api/matches", {"game": "racer", "mode": "three", "opponents": ["u_meera"], "kind": "race"}).json()["id"]
            self.post(f"/api/matches/{mid}/accept", None, MEERA)
            for who in (KABIR, MEERA):
                s = self.post("/api/sessions", {"game": "racer", "matchId": mid}, who).json()["id"]
                self.clock.advance(seconds=30)
                self.score(s, 10 + i, 30, h=who)
            self.clock.advance(minutes=1)
        self.post("/api/matches", {"game": "tictactoe", "mode": "phones", "opponents": ["u_kabir"]}, ASHA)       # waiting
        self.post("/api/matches", {"game": "fourrow", "mode": "phones", "opponents": ["u_dev"]}, KABIR)         # sent
        t = self.post("/api/matches", {"game": "tictactoe", "mode": "phones", "opponents": ["u_dev"]}, KABIR).json()["id"]
        self.post(f"/api/matches/{t}/accept", None, DEV)                                                       # playing
        with db.get_conn() as conn:
            together.LIVE.clear()                  # (the live part changes with the clock; the lists shouldn't)
            new = together.mine(conn, "u_kabir")
            together.LIVE.clear()
            old = old_mine(conn, "u_kabir")
        self.assertEqual(new, old)
        self.assertEqual([len(new[k]) for k in ("waiting", "sent", "playing", "recent")], [1, 1, 1, together.RECENT_N])


# ---------------------------------------------------------------------------------------------------------------------
# A5 is in test_wave4; A8 heartbeats' levels in test_progress. A9: the connection test's key
# ---------------------------------------------------------------------------------------------------------------------

class TestAiKey(Base):
    def test_the_saved_key_only_goes_to_the_saved_address(self):
        self.settings({"ai_provider": "anthropic", "ai_api_key": "sk-saved", "ai_model": "m"})
        seen = []
        with mock.patch.object(ai_client, "list_models", side_effect=lambda cfg, *a, **k: seen.append(cfg.api_key) or []), \
                mock.patch.object(ai_client, "generate", side_effect=AssertionError("no model is asked here")):
            self.post("/api/admin/ai/test", {"provider": "anthropic", "url": "https://evil.example", "model": ""}, ASHA)
            self.post("/api/admin/ai/test", {"provider": "openai", "url": "", "model": ""}, ASHA)
            self.post("/api/admin/ai/test", {"provider": "anthropic", "url": "", "model": ""}, ASHA)
            self.post("/api/admin/ai/test", {"provider": "anthropic", "url": "https://evil.example", "apiKey": "typed", "model": ""}, ASHA)
        self.assertEqual(seen, ["", "", "sk-saved", "typed"])


# ---------------------------------------------------------------------------------------------------------------------
# AP1: last_seen
# ---------------------------------------------------------------------------------------------------------------------

class TestLastSeen(Base):
    def last_seen(self, uid="u_kabir"):
        with db.get_conn() as conn:
            return conn.execute("SELECT last_seen, name FROM users WHERE id = ?", (uid,)).fetchone()

    def test_written_only_when_stale_or_changed(self):
        first = self.last_seen()["last_seen"]
        self.clock.advance(seconds=30)
        self.get("/api/me")
        self.assertEqual(self.last_seen()["last_seen"], first)
        self.clock.advance(seconds=31)
        self.get("/api/me")
        self.assertNotEqual(self.last_seen()["last_seen"], first)
        self.get("/api/me", hdr("u_kabir", "Kabir R", "kabir"))         # a new display name: at once
        self.assertEqual(self.last_seen()["name"], "Kabir R")

    def test_the_dependency_is_not_a_coroutine(self):
        import inspect
        from app import auth
        self.assertFalse(inspect.iscoroutinefunction(auth.get_current_user))
        self.assertFalse(inspect.iscoroutinefunction(auth.require_admin))


# ---------------------------------------------------------------------------------------------------------------------
# AP5, AP6, AP7, AP8, AI1: queries and housekeeping
# ---------------------------------------------------------------------------------------------------------------------

def seed_scores(conn, n=400, seed=3):
    rnd = random.Random(seed)
    for i in range(n):
        conn.execute("INSERT INTO scores (user_id, game, mode, score, level, seconds, started_at, ended_at, app_version, "
                     "start_level) VALUES (?, 'snake', 'walls-normal', ?, 1, 60, ?, ?, 'x', ?)",
                     (rnd.choice(["u_kabir", "u_meera", "u_asha", "u_dev"]), rnd.randint(0, 30) * 10,
                      "2026-09-01T00:00:00+00:00", f"2026-09-{rnd.randint(1, 21):02d}T0{rnd.randint(0, 9)}:00:00+00:00",
                      rnd.choice([1, 1, 1, 3])))


class TestQueries(Base):
    def test_leaderboard_matches_the_old_loop(self):
        def old(conn, period, viewer, names):
            sql = ("SELECT s.id, s.user_id, s.score, s.level, s.seconds, s.ended_at, u.name FROM scores s JOIN users u ON "
                   "u.id = s.user_id WHERE s.game = 'snake' AND s.mode = 'walls-normal' AND u.disabled = 0 AND s.start_level = 1")
            args = []
            if period == "month":
                sql += " AND s.ended_at >= ?"
                args.append(scores._month_start_utc())
            out, seen = [], set()
            for r in conn.execute(sql + " ORDER BY s.score DESC, s.ended_at ASC, s.id ASC", args):
                if r["user_id"] in seen:
                    continue
                seen.add(r["user_id"])
                out.append({"rank": len(out) + 1, "scoreId": r["id"], "score": r["score"], "level": r["level"],
                            "seconds": r["seconds"], "at": r["ended_at"],
                            "name": scores.first_name(r["name"]) if names == "first" else r["name"],
                            "me": r["user_id"] == viewer["id"]})
            return out[:scores.TOP_N]
        self.get("/api/me", DEV)
        with db.get_conn() as conn:
            seed_scores(conn)
            conn.execute("UPDATE users SET disabled = 1 WHERE id = 'u_dev'")
            for period in ("all", "month"):
                for names in ("full", "first"):
                    viewer = {"id": "u_meera"}
                    self.assertEqual(scores.leaderboard(conn, "snake", "walls-normal", period, viewer, names),
                                     old(conn, period, viewer, names))

    def test_progress_for_all_games_matches_one_game_at_a_time(self):
        rnd = random.Random(5)
        with db.get_conn() as conn:
            for game, modes in games.CONTINUE_LEVELS.items():
                for mode in modes:
                    if rnd.random() < 0.6:
                        progress.record(conn, "u_kabir", game, mode, rnd.randint(1, 300))
            many = progress.for_games(conn, "u_kabir", games.CONTINUE_LEVELS)
            for game in games.CONTINUE_LEVELS:
                self.assertEqual(many[game], progress.for_game(conn, "u_kabir", game), game)
            counts = levels.playable_counts(conn)
            for s in levels.SETS:
                self.assertEqual(counts.get(s, 0), levels.playable_count(conn, s), s)

    def test_built_in_levels_are_checked_once_per_version(self):
        with db.get_conn() as conn:
            self.assertFalse(levels.seed_builtins(conn))                  # init_db already did it
            conn.execute("UPDATE app_meta SET value = 'older' WHERE key = 'builtin_levels'")
            self.assertTrue(levels.seed_builtins(conn))
            self.assertFalse(levels.seed_builtins(conn))
            self.assertTrue(levels.seed_builtins(conn, force=True))

    def test_keep_scores_keeps_the_best_from_level_one(self):
        self.settings({"keep_scores_years": 1})
        old_ = (config.utcnow() - timedelta(days=800)).isoformat(timespec="seconds")
        with db.get_conn() as conn:
            for score, start in ((100, 1), (500, 3), (80, 1)):
                conn.execute("INSERT INTO scores (user_id, game, mode, score, level, seconds, started_at, ended_at, "
                             "app_version, start_level) VALUES ('u_kabir', 'snake', 'walls-normal', ?, 1, 60, ?, ?, 'x', ?)",
                             (score, old_, old_, start))
            self.assertEqual(scores.best(conn, "snake", "walls-normal", "u_kabir"), 100)
            scores.prune(conn)
            left = [r[0] for r in conn.execute("SELECT score FROM scores")]
            self.assertEqual(left, [100])
            self.assertEqual(scores.best(conn, "snake", "walls-normal", "u_kabir"), 100)

    def test_working_records_are_pruned_whatever_keep_scores_says(self):
        long_ago = (config.utcnow() - timedelta(days=120)).isoformat(timespec="seconds")
        recent = (config.utcnow() - timedelta(days=5)).isoformat(timespec="seconds")
        with db.get_conn() as conn:
            for sid, at in (("old", long_ago), ("new", recent)):
                conn.execute("INSERT INTO play_sessions (id, user_id, game, mode, started_at, last_beat_at, ended_at) "
                             "VALUES (?, 'u_kabir', 'snake', 'walls-normal', ?, ?, ?)", (sid, at, at, at))
                conn.execute("INSERT INTO notify_log (kind, user_id, sent_on, created_at) VALUES ('limit5', 'u_kabir', ?, ?)",
                             (at[:10], at))
                conn.execute("INSERT INTO extra_time (user_id, date, minutes, created_at) VALUES ('u_kabir', ?, 15, ?)", (at[:10], at))
                conn.execute("INSERT INTO daily_challenges (date, game, mode, seed) VALUES (?, 'snake', 'walls-normal', 1)", (at[:10],))
                conn.execute("INSERT INTO matches (id, game, mode, kind, seed, status, created_by, created_at, ended_at) "
                             "VALUES (?, 'tictactoe', 'phones', 'turns', 1, 'done', 'u_kabir', ?, ?)", ("m" + sid, at, at))
                conn.execute("INSERT INTO match_moves (match_id, number, seat, move, at) VALUES (?, 1, 1, '{}', ?)", ("m" + sid, at))
            conn.execute("INSERT INTO play_sessions (id, user_id, game, mode, started_at, last_beat_at) "
                         "VALUES ('open', 'u_kabir', 'snake', 'walls-normal', ?, ?)", (long_ago, long_ago))
            out = housekeeping.prune_working_records(conn)
            self.assertEqual(out, {"sessions": 1, "moves": 1, "notify_log": 1, "extra_time": 1, "daily": 1})
            self.assertEqual({r[0] for r in conn.execute("SELECT id FROM play_sessions")}, {"new", "open"})
            self.assertEqual({r[0] for r in conn.execute("SELECT id FROM matches")}, {"mold", "mnew"})   # head to head stays

    def test_chess_repetition_memory_starts_again_after_a_pawn_move_or_capture(self):
        chess = rules.get("chess")
        st = chess.new(0)
        white = st["turn"]
        st, _ = chess.apply(st, white, {"from": "g1", "to": "f3"})
        self.assertEqual(len(st["keys"]), 2)
        st, _ = chess.apply(st, 1 - white, {"from": "e7", "to": "e5"})        # a pawn move
        self.assertEqual(len(st["keys"]), 1)

    def test_old_snapshots_are_swept(self):
        import tempfile
        import time
        stale = os.path.join(tempfile.gettempdir(), db.SNAPSHOT_PREFIX + "x.db")
        fresh = os.path.join(tempfile.gettempdir(), db.SNAPSHOT_PREFIX + "y.db")
        for p in (stale, fresh):
            with open(p, "w") as f:
                f.write("x")
        old_t = time.time() - housekeeping.SNAPSHOT_MAX_AGE - 60
        os.utime(stale, (old_t, old_t))
        try:
            self.assertGreaterEqual(housekeeping.sweep_snapshots(), 1)
            self.assertFalse(os.path.exists(stale))
            self.assertTrue(os.path.exists(fresh))
        finally:
            for p in (stale, fresh):
                if os.path.exists(p):
                    os.remove(p)


# ---------------------------------------------------------------------------------------------------------------------
# AI6: saved games continued twice, ending a save while switched off, the sorting search
# ---------------------------------------------------------------------------------------------------------------------

class TestSavedTwice(Base):
    def save_one(self):
        sid = self.start().json()["id"]
        self.clock.advance(seconds=60)
        self.beat(sid, 60)
        r = self.post(f"/api/sessions/{sid}/save", {"state": {"x": 1}, "stateVersion": games.GAMES["snake"]["state_version"],
                                                    "score": 50, "level": 1, "seconds": 60})
        self.assertEqual(r.status_code, 200, r.text)

    def test_a_save_continued_twice_scores_once(self):
        self.save_one()
        a = self.post("/api/sessions", {"game": "snake", "resume": True}).json()["id"]
        b = self.post("/api/sessions", {"game": "snake", "resume": True}).json()["id"]      # another tab
        self.clock.advance(seconds=30)
        self.beat(b, 30)
        ra = self.score(a, 80, 90)
        self.assertEqual(ra.status_code, 409)
        self.assertIn("somewhere else", ra.json()["detail"])
        self.assertEqual(self.score(b, 90, 90).status_code, 200)
        with db.get_conn() as conn:
            self.assertEqual([r[0] for r in conn.execute("SELECT score FROM scores")], [90])

    def test_ending_a_save_needs_the_person_switched_on(self):
        self.save_one()
        self.patch("/api/admin/users/u_kabir", {"disabled": True})
        self.assertEqual(self.delete("/api/saved/snake").status_code, 403)


class TestSortingSearch(unittest.TestCase):
    def test_iterative_search_answers_like_the_recursive_one(self):
        import sys

        def old(tubes, pour_all, budget=120_000):           # 1.12.3's recursive search
            work = [list(t) for t in tubes]
            seen = set()
            count = [0]

            def dfs():
                if _sorting.is_sorted(work):
                    return True
                count[0] += 1
                if count[0] > budget:
                    return False
                key = "|".join(sorted(",".join(map(str, t)) for t in work))
                if key in seen:
                    return False
                seen.add(key)
                for a, A in enumerate(work):
                    if not A:
                        continue
                    if len(A) == _sorting.CAP and _sorting._run(A) == _sorting.CAP:
                        continue
                    for b, B in enumerate(work):
                        if a == b or len(B) >= _sorting.CAP or (B and B[-1] != A[-1]):
                            continue
                        if not B and _sorting._run(A) == len(A):
                            continue
                        n = min(_sorting._run(A), _sorting.CAP - len(B)) if pour_all else 1
                        for _ in range(n):
                            B.append(A.pop())
                        if dfs():
                            return True
                        for _ in range(n):
                            A.append(B.pop())
                return False
            return dfs()

        limit = sys.getrecursionlimit()
        rnd = random.Random(11)
        try:
            sys.setrecursionlimit(max(limit, 20000))
            for _ in range(60):
                colours = rnd.randint(2, 5)
                balls = [c for c in range(colours) for _ in range(_sorting.CAP)]
                rnd.shuffle(balls)
                tubes = [balls[i:i + _sorting.CAP] for i in range(0, len(balls), _sorting.CAP)] + [[] for _ in range(rnd.randint(1, 2))]
                for pour_all in (True, False):
                    budget = rnd.choice([50, 500, 5000])
                    self.assertEqual(_sorting.solvable(tubes, pour_all, budget), old(tubes, pour_all, budget), (tubes, pour_all, budget))
        finally:
            sys.setrecursionlimit(limit)
        self.assertEqual(sys.getrecursionlimit(), limit)          # the search no longer changes it


class TestBusKey(Base):
    def test_signing_key_is_an_app_setting_and_reaches_the_bus(self):
        body = self.get("/api/admin/settings", ASHA).json()
        self.assertIn("bus_signing_key", body["values"])
        self.settings({"bus_signing_key": "shared-secret"})
        self.assertEqual(self.get("/api/admin/settings", ASHA).json()["values"]["bus_signing_key"], "")    # never shown
        from app import app_messages
        from app.common import app_bus
        with mock.patch.object(app_bus, "start") as start, mock.patch.object(app_bus.default, "started", False):
            app_messages.start()
        key = start.call_args.kwargs["signing_key"]
        self.assertEqual(key(), "shared-secret")
