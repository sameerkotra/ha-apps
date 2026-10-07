"""Playing together, step 2: live duels (SPEC §13.4) — the relay and referee (`app/live.py`: ordering, duplicates and
gaps, sizes, rates, the actions and ticks a phone may send, checksums, a phone gone quiet or away, pauses, the start
and the delay) and the match around it (`together.py`, the routes: invites for the two-phone modes, the WebSocket and
its HTTP fallback, results that must agree, resigning, children's limits and quiet hours, head to head)."""
import _env  # noqa: F401  (must be first)

import unittest

from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import db, games, live, together
from app.main import app
from base import ASHA, DEV, KABIR, MEERA, ApiBase, hdr

ACTIONS = games.live_actions("snakeduel")


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def room(delay_rtts=(20, 20)):
    """A room for seats 1 and 2 that has started (both ready), at time 0."""
    r = live.Room("m1", "snakeduel", ACTIONS, {"a": 1, "b": 2}, now=0.0)
    r.receive(1, {"t": "ready", "rtt": delay_rtts[0]}, 0.1)
    r.receive(2, {"t": "ready", "rtt": delay_rtts[1]}, 0.2)
    return r


def numbered(r, seat, since=0):
    return r.pull(seat, since)


class TestRelay(unittest.TestCase):
    def test_both_ready_starts_with_a_count_in_and_a_delay_from_the_round_trips(self):
        r = live.Room("m1", "snakeduel", ACTIONS, {"a": 1, "b": 2}, now=0.0)
        self.assertEqual(r.seat_of("b"), 2)
        self.assertIsNone(r.seat_of("c"))
        r.receive(1, {"t": "ready", "rtt": 30}, 1.0)
        self.assertEqual((r.phase, numbered(r, 1)), ("waiting", []))
        r.receive(2, {"t": "ready", "rtt": 50}, 1.5)
        self.assertEqual(r.phase, "countin")
        start = numbered(r, 1)
        self.assertEqual(start, [{"t": "start", "delay": live.choose_delay([30, 50]), "inMs": 3000, "n": 1}])
        self.assertEqual(numbered(r, 2)[0]["t"], "start")
        r.check(4.4)
        self.assertEqual(r.phase, "countin")
        r.check(4.6)
        self.assertEqual(r.phase, "running")

    def test_a_connection_is_woken_for_each_message(self):
        r = room()
        woken = []
        r.wake_with(2, lambda: woken.append(1))
        r.receive(1, {"t": "in", "seq": 1, "upto": r.delay, "ev": []}, 1)
        self.assertEqual(len(woken), 1, "seat 2's connection is woken: there is a message for it")
        r.wake_with(2, None)
        r.receive(1, {"t": "in", "seq": 2, "upto": r.delay + 1, "ev": []}, 1)
        self.assertEqual(len(woken), 1)
        r.wake_with(1, lambda: 1 / 0)                       # a waker that fails never troubles the relay
        self.assertEqual(r.receive(2, {"t": "in", "seq": 1, "upto": r.delay, "ev": []}, 1), [{"t": "ack", "seq": 1}])

    def test_the_delay(self):
        self.assertEqual(live.choose_delay([20, 20]), 2 + live.DELAY_HEADROOM)    # 20 ms one way → 2 ticks + headroom
        self.assertEqual(live.choose_delay([0, 0]), live.DELAY_MIN)
        self.assertEqual(live.choose_delay([4, 6]), max(live.DELAY_MIN, 1 + live.DELAY_HEADROOM))
        self.assertEqual(live.choose_delay([150, 150]), 9 + live.DELAY_HEADROOM)  # 75 ms each way to the app: 150 ms phone to phone → 9 ticks + headroom
        self.assertEqual(live.choose_delay([None, None]), live.choose_delay([60, 60]))
        self.assertEqual(live.choose_delay([600, 900]), live.DELAY_MAX)
        self.assertEqual(live.choose_delay([True, "x"]), live.choose_delay([]))

    def test_inputs_go_to_the_other_phone_in_order_once_each(self):
        r = room()
        d = r.delay
        out = r.receive(1, {"t": "in", "seq": 1, "upto": d + 1, "ev": [[d, "up", 1], [d + 1, "left", 1]]}, 1)
        self.assertEqual(out, [{"t": "ack", "seq": 1}])
        msgs = numbered(r, 2)
        self.assertEqual(msgs[-1], {"t": "in", "seat": 1, "seq": 1, "upto": d + 1, "ev": [[d, "up", 1], [d + 1, "left", 1]],
                                    "n": 2})
        # sent again (a reconnection): acknowledged, not passed on twice
        self.assertEqual(r.receive(1, {"t": "in", "seq": 1, "upto": d + 1, "ev": []}, 1), [{"t": "ack", "seq": 1}])
        self.assertEqual(len(numbered(r, 2)), 2)
        # one missing: the phone is told where the server is
        self.assertEqual(r.receive(1, {"t": "in", "seq": 3, "upto": d + 2, "ev": []}, 1),
                         [{"t": "error", "why": "gap", "inSeq": 1}])
        self.assertEqual(r.receive(1, {"t": "in", "seq": 2, "upto": d + 2, "ev": []}, 1), [{"t": "ack", "seq": 2}])
        # the tick the phone is at goes along to the other phone (it paces itself by it)
        self.assertEqual(r.receive(1, {"t": "in", "seq": 3, "at": 2, "upto": d + 2, "ev": []}, 1), [{"t": "ack", "seq": 3}])
        self.assertEqual(numbered(r, 2)[-1], {"t": "in", "seat": 1, "seq": 3, "at": 2, "upto": d + 2, "ev": [], "n": 4})
        # a phone never gets its own messages back; the one who acknowledges drops them
        self.assertEqual([m["t"] for m in numbered(r, 1)], ["start"])
        self.assertEqual(numbered(r, 2, since=2), [m for m in numbered(r, 2) if m["n"] > 2])
        self.assertEqual(len(r.pull(2, 4)), 0)
        self.assertEqual(len(r.players[2].out), 0, "acknowledged messages are dropped")

    def test_what_a_phone_may_say(self):
        r = room()
        d = r.delay
        bad = [
            ({"t": "in", "seq": 0, "upto": d}, "seq"),
            ({"t": "in", "seq": "1", "upto": d}, "seq"),
            ({"t": "in", "seq": 1, "upto": d - 2, "ev": []}, "upto"),                  # backwards
            ({"t": "in", "seq": 1, "upto": True, "ev": []}, "upto"),
            ({"t": "in", "seq": 1, "upto": d - 1 + live.MAX_INPUT_DELAY + 1, "ev": []}, "ahead"),   # further than the other's word + the most a delay can be
            ({"t": "in", "seq": 1, "upto": d, "at": -1, "ev": []}, "at"),
            ({"t": "in", "seq": 1, "upto": d, "at": d + 1, "ev": []}, "at"),             # a phone is never past its own word
            ({"t": "in", "seq": 1, "upto": d, "at": "1", "ev": []}, "at"),
            ({"t": "in", "seq": 1, "upto": d, "ev": "x"}, "ev"),
            ({"t": "in", "seq": 1, "upto": d, "ev": [[d, "up", 1]] * 33}, "ev"),
            ({"t": "in", "seq": 1, "upto": d, "ev": [[d, "up"]]}, "ev"),
            ({"t": "in", "seq": 1, "upto": d, "ev": [[d + 1, "up", 1]]}, "tick"),      # after upto
            ({"t": "in", "seq": 1, "upto": d, "ev": [[d - 1, "up", 1]]}, "tick"),      # before the last word
            ({"t": "in", "seq": 1, "upto": d + 1, "ev": [[d + 1, "up", 1], [d, "up", 1]]}, "tick"),   # out of order
            ({"t": "in", "seq": 1, "upto": d, "ev": [[d, "up", 1]] * 9}, "too many"),
            ({"t": "in", "seq": 1, "upto": d, "ev": [[d, "fire", 1]]}, "action"),      # Snake Duel has no fire
            ({"t": "in", "seq": 1, "upto": d, "ev": [[d, 5, 1]]}, "action"),
            ({"t": "in", "seq": 1, "upto": d, "ev": [[d, "up", 2]]}, "value"),
            ({"t": "in", "seq": 1, "upto": d, "ev": [[d, "up", True]]}, "value"),
            ({"t": "in", "seq": 1, "upto": d, "ev": [[d, "up", 0.5]]}, "value"),
            ({"t": "in", "seq": 1, "upto": d, "ev": [], "sum": [59, 1]}, "sum"),       # not a checksum tick
            ({"t": "in", "seq": 1, "upto": d, "ev": [], "sum": [60, 1]}, "sum"),       # not yet played
            ({"t": "in", "seq": 1, "upto": d, "ev": [], "sum": "x"}, "sum"),
        ]
        for msg, why in bad:
            out = r.receive(1, msg, 1)
            self.assertEqual(out[0]["t"], "error", msg)
            self.assertEqual(out[0]["why"], why, msg)
        self.assertEqual(r.players[1].seq, 0, "nothing was accepted")
        self.assertEqual(len(numbered(r, 2)), 1, "nothing was passed on")
        # 8 inputs on a tick are fine; a value in Paddle Duel's aim range is fine there
        self.assertEqual(r.receive(1, {"t": "in", "seq": 1, "upto": d, "ev": [[d, "up", 1]] * 8}, 1)[0]["t"], "ack")
        p = live.Room("m2", "duel", games.live_actions("duel"), {"a": 1, "b": 2}, now=0)
        p.receive(1, {"t": "ready"}, 0); p.receive(2, {"t": "ready"}, 0)
        self.assertEqual(p.receive(1, {"t": "in", "seq": 1, "upto": p.delay, "ev": [[p.delay, "aim", 240]]}, 0)[0]["t"], "ack")
        self.assertEqual(p.receive(1, {"t": "in", "seq": 2, "upto": p.delay, "ev": [[p.delay, "aim", 241]]}, 0)[0]["why"], "tick")
        self.assertEqual(p.receive(2, {"t": "in", "seq": 1, "upto": p.delay, "ev": [[p.delay, "aim", 241]]}, 0)[0]["why"], "value")

    def test_not_before_the_start(self):
        r = live.Room("m1", "snakeduel", ACTIONS, {"a": 1, "b": 2}, now=0.0)
        self.assertEqual(r.receive(1, {"t": "in", "seq": 1, "upto": 3, "ev": []}, 1)[0]["why"], "not started")

    def test_junk_and_unknown_messages(self):
        r = room()
        self.assertEqual(r.receive(1, [1, 2], 1)[0]["why"], "not a message")
        self.assertEqual(r.receive(1, "x", 1)[0]["why"], "not a message")
        self.assertEqual(r.receive(1, {"t": "something new"}, 1), [])
        self.assertEqual(r.receive(1, {"t": "ping", "id": 4}, 1), [{"t": "pong", "id": 4}])
        self.assertEqual(r.receive(1, {"t": "ping", "id": "x"}, 1), [{"t": "pong", "id": None}])

    def test_rate_limit_and_closing_a_phone_that_keeps_sending_nonsense(self):
        r = room()
        replies = [r.receive(1, {"t": "ping", "id": i}, 5.0)[0] for i in range(int(live.BURST) + 5)]
        self.assertEqual(replies[-1]["why"], "slow down")
        self.assertEqual(r.receive(1, {"t": "ping", "id": 1}, 6.0)[0]["t"], "pong", "a second later it may again")
        r2 = room()
        for _ in range(live.MAX_BAD):
            r2.receive(2, {"t": "in", "seq": 1, "upto": -1, "ev": []}, 1)
        self.assertFalse(r2.closing(2))
        r2.receive(2, {"t": "in", "seq": 1, "upto": -1, "ev": []}, 1)
        self.assertTrue(r2.closing(2))
        self.assertFalse(r2.closing(1))

    def test_a_reconnection_says_hello_and_hears_the_rest(self):
        r = room()
        d = r.delay
        r.receive(2, {"t": "in", "seq": 1, "upto": d, "ev": []}, 1)
        r.receive(2, {"t": "in", "seq": 2, "upto": d + 1, "ev": []}, 1)
        welcome = r.hello(1, 1, 2)
        self.assertEqual(welcome[0]["t"], "welcome")
        self.assertEqual((welcome[0]["seat"], welcome[0]["inSeq"], welcome[0]["delay"], welcome[0]["phase"]), (1, 0, d, "countin"))
        self.assertEqual([m["n"] for m in r.pull(1, 1)], [2, 3])

    def test_checksums(self):
        r = room()
        r.check(10)
        d = r.delay
        seq = {1: 0, 2: 0}

        def say(seat, upto, sm=None):
            seq[seat] += 1
            msg = {"t": "in", "seq": seq[seat], "upto": upto, "ev": []}
            if sm:
                msg["sum"] = sm
            return r.receive(seat, msg, 11)[0]["t"]
        # both play on, a little at a time (neither can speak further ahead than the other's word + the most a delay can be)
        for u in range(d, 60 + d - 1, d):
            self.assertEqual((say(1, u), say(2, u)), ("ack", "ack"))
        self.assertEqual(r.receive(1, {"t": "in", "seq": seq[1] + 1, "upto": r.players[2].upto + live.MAX_INPUT_DELAY + 1, "ev": []}, 11)[0]["why"], "ahead")
        # equal checksums at 60: nothing happens
        self.assertEqual(say(1, 60 + d - 1, [60, 777]), "ack")
        self.assertEqual(say(2, 60 + d - 1, [60, 777]), "ack")
        self.assertIsNone(r.take_end())
        self.assertEqual(r.phase, "running")
        # different at 120: out of step, both told, no winner
        for u in range(60 + d, 120 + d - 1, d):
            say(1, u); say(2, u)
        say(2, 120 + d - 1, [120, 1])
        say(1, 120 + d - 1, [120, 2])
        self.assertEqual(r.phase, "ended")
        self.assertEqual(r.take_end(), ("out_of_step", None))
        self.assertIsNone(r.take_end(), "once")
        self.assertEqual(numbered(r, 1)[-1], {"t": "end", "reason": "out_of_step", "winner": None, "n": numbered(r, 1)[-1]["n"]})
        self.assertEqual(numbered(r, 2)[-1]["t"], "end")

    def test_a_quiet_phone_pauses_both_and_one_gone_a_minute_has_left(self):
        r = room()
        r.check(4)
        self.assertEqual(r.phase, "running")
        r.receive(1, {"t": "ping", "id": 1}, 12)
        r.check(12.1)                       # seat 2 last heard at 0.2: quiet for 12 s
        self.assertEqual(r.phase, "paused")
        self.assertEqual(numbered(r, 1)[-1]["t"], "pause")
        self.assertEqual((numbered(r, 1)[-1]["by"], numbered(r, 1)[-1]["why"]), (2, "lost"))
        r.receive(1, {"t": "resume"}, 13)    # the other player can't resume it
        self.assertEqual(r.phase, "paused")
        r.receive(2, {"t": "ping", "id": 1}, 20)
        r.check(20)
        self.assertEqual(r.phase, "running", "back: it goes on")
        self.assertEqual(numbered(r, 2)[-1]["t"], "resume")
        r.receive(1, {"t": "ping", "id": 2}, 79)
        r.check(80.5)                        # seat 2 quiet since 20
        self.assertEqual(r.phase, "ended")
        self.assertEqual(r.take_end(), ("left", 1))

    def test_both_gone_nobody_wins(self):
        r = room()
        r.check(61)
        self.assertEqual(r.take_end(), ("left", None))

    def test_a_players_pause_pauses_both_for_at_most_five_minutes(self):
        r = room()
        r.check(4)
        r.receive(2, {"t": "pause"}, 5)
        self.assertEqual((r.phase, r.paused_by, r.pause_why), ("paused", 2, "player"))
        self.assertEqual(numbered(r, 1)[-1], {"t": "pause", "by": 2, "why": "player", "n": numbered(r, 1)[-1]["n"]})
        r.receive(1, {"t": "resume"}, 6)                 # either can resume
        self.assertEqual(r.phase, "running")
        r.receive(1, {"t": "pause"}, 7)
        for t in range(10, 400, 5):                      # both still there, pinging
            r.receive(1, {"t": "ping", "id": t}, t)
            r.receive(2, {"t": "ping", "id": t}, t)
            r.check(t)
        self.assertEqual(r.take_end(), ("timeout", None))

    def test_a_phone_that_never_gets_ready(self):
        r = live.Room("m1", "snakeduel", ACTIONS, {"a": 1, "b": 2}, now=0.0)
        r.receive(1, {"t": "ready", "rtt": 10}, 1)
        r.receive(1, {"t": "ping", "id": 1}, 119)
        r.check(119)
        self.assertEqual(r.phase, "waiting")
        r.check(121)
        self.assertEqual(r.take_end(), ("left", 1))
        r2 = live.Room("m1", "snakeduel", ACTIONS, {"a": 1, "b": 2}, now=0.0)
        r2.check(121)
        self.assertEqual(r2.take_end(), ("left", None))

    def test_an_end_from_elsewhere_reaches_both_phones_once(self):
        r = room()
        r.ended("resigned", 2)
        r.ended("resigned", 2)
        self.assertEqual([m["t"] for m in numbered(r, 1)].count("end"), 1)
        self.assertIsNone(r.take_end(), "the database already has it")
        self.assertEqual(r.picture(1)["phase"], "ended")

    def test_a_phone_that_doesnt_read_its_messages(self):
        r = room()
        old = live.OUTBOX_MAX
        live.OUTBOX_MAX = 5
        try:
            d = r.delay
            for i in range(1, 8):
                r.receive(1, {"t": "in", "seq": i, "upto": d - 1 + i if i < 2 else d, "ev": []}, 1)
            self.assertEqual(r.take_end(), ("left", 1))
        finally:
            live.OUTBOX_MAX = old

    def test_the_play_time_watch_comes_round_every_five_seconds_whoever_asks(self):
        r = room()
        self.assertFalse(r.watch_due(3.0))
        self.assertTrue(r.watch_due(5.1))
        self.assertFalse(r.watch_due(9.0))
        self.assertTrue(r.watch_due(10.2))

    def test_reports(self):
        live.store_report("x", 1, {"tick": 900, "sum": 5, "scores": [10, 20], "levels": [1, 1], "winner": 2})
        live.store_report("x", 2, {"tick": 900, "sum": 5, "scores": [10, "20"], "winner": 2})
        live.store_report("x", 3, "nonsense")
        self.assertEqual(live.REPORTS["x"][1], {"tick": 900, "sum": 5, "scores": [10, 20], "winner": 2, "levels": [1, 1]})
        self.assertIsNone(live.REPORTS["x"][2])
        self.assertIsNone(live.REPORTS["x"][3])
        live.forget("x")
        self.assertNotIn("x", live.REPORTS)


# ---------------------------------------------------------------------------
# The match around it
# ---------------------------------------------------------------------------

class Live(ApiBase):
    def setUp(self):
        super().setUp()
        self.clock2 = FakeClock()
        self._old = live.clock
        live.clock = self.clock2
        live.ROOMS.clear()
        live.REPORTS.clear()

    def tearDown(self):
        live.clock = self._old
        super().tearDown() if hasattr(super(), "tearDown") else None

    def invite(self, who=KABIR, to="u_meera", game="snakeduel", mode="phones", **extra):
        return self.post("/api/matches", {"game": game, "mode": mode, "opponents": [to], **extra}, who)

    def new_match(self, game="snakeduel", mode="phones", a=KABIR, b=MEERA, **extra):
        r = self.invite(a, b["X-Remote-User-Id"], game, mode, **extra)
        self.assertEqual(r.status_code, 201, r.text)
        mid = r.json()["id"]
        r = self.post(f"/api/matches/{mid}/accept", None, b)
        self.assertEqual(r.status_code, 200, r.text)
        return mid

    def sessions(self, mid, game="snakeduel"):
        out = {}
        for who in (KABIR, MEERA):
            r = self.post("/api/sessions", {"game": game, "matchId": mid}, who)
            self.assertEqual(r.status_code, 201, r.text)
            out[who["X-Remote-User-Id"]] = r.json()
        return out

    def start(self, mid):
        rm = live.get(mid)
        rm.receive(1, {"t": "ready", "rtt": 20})
        rm.receive(2, {"t": "ready", "rtt": 20})
        self.clock2.t += 3.5
        rm.check()
        return rm

    def score(self, sid, who, score, report=None, seconds=60, level=1):
        self.clock.advance(seconds=seconds)
        body = {"sessionId": sid, "score": score, "level": level, "seconds": seconds}
        if report is not None:
            body["report"] = report
        return self.post("/api/scores", body, who)

    def match(self, mid, who=KABIR):
        r = self.get(f"/api/matches/{mid}", who)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()


class TestInvitesAndSessions(Live):
    def test_a_two_phone_mode_is_a_live_duel(self):
        for game, mode in (("snakeduel", "phones"), ("duel", "phones"), ("tanks", "together"), ("tanks", "against")):
            r = self.invite(game=game, mode=mode)
            self.assertEqual(r.status_code, 201, r.text)
            m = r.json()
            self.assertEqual((m["kind"], m["rule"], m["live"]), ("live", None, None))
            self.post(f"/api/matches/{m['id']}/cancel", None, KABIR)
        # the kind must fit the mode
        self.assertEqual(self.invite(kind="race").status_code, 409)                      # a duel game isn't raced
        self.assertEqual(self.invite(mode="cpu", kind="live").status_code, 422)
        self.assertEqual(self.invite(game="duel", mode="normal").status_code, 409)       # Paddle Duel isn't raced
        self.assertEqual(self.invite(game="racer", mode="three", kind="live").status_code, 409)
        g = {x["id"]: x for x in self.get("/api/games").json()["games"]}
        self.assertEqual(g["duel"]["liveModes"], ["phones"])
        self.assertEqual(g["tanks"]["liveModes"], ["together", "against"])
        self.assertEqual(g["snake"]["liveModes"], [])

    def test_accepting_opens_the_room_and_the_picture_shows_it(self):
        mid = self.new_match()
        rm = live.get(mid)
        self.assertIsNotNone(rm)
        self.assertEqual((rm.seat_of("u_kabir"), rm.seat_of("u_meera")), (1, 2))
        m = self.match(mid)
        self.assertEqual((m["kind"], m["status"], m["startsInMs"]), ("live", "playing", None))
        self.assertEqual(m["live"]["phase"], "waiting")
        self.assertEqual(set(m["live"]["seats"]), {"1", "2"})

    def test_a_two_phone_mode_is_never_played_alone(self):
        r = self.post("/api/sessions", {"game": "duel", "mode": "phones", "practice": False}, KABIR)
        self.assertEqual(r.status_code, 422)
        self.assertIn("two phones", r.json()["detail"])
        mid = self.new_match()
        s = self.sessions(mid)
        self.assertEqual((s["u_kabir"]["seat"], s["u_meera"]["seat"]), (1, 2))
        self.assertEqual(s["u_kabir"]["seed"], s["u_meera"]["seed"])
        self.assertEqual(s["u_kabir"]["mode"], "phones")
        self.assertEqual(len(s["u_kabir"]["levels"]), len(s["u_meera"]["levels"]))        # the arena list is shared
        self.assertEqual(self.post("/api/sessions", {"game": "snakeduel", "matchId": mid}, KABIR).status_code, 409)

    def test_childrens_limits_and_allowed_games_apply_when_starting(self):
        self.make_child(MEERA, allowedGames=["snake"])
        self.assertEqual(self.invite().status_code, 409)
        self.make_child(MEERA, allowedGames=None, minutesSchool=0, minutesWeekend=0)
        r = self.invite()
        self.assertEqual(r.status_code, 409)
        self.assertIn("no play time left", r.json()["detail"].lower())

    def test_the_invite_notification_has_join_and_not_now(self):
        from app import config
        self.ha.notify_services = ["meera_phone"]
        self.post("/api/admin/users/u_meera/notify", {"service": "notify.meera_phone"}, ASHA)
        self.ha.requests.clear()
        panel, config.INGRESS_PANEL = config.INGRESS_PANEL, "/hassio/ingress/local_household_arcade"
        try:
            mid = self.invite(game="tanks", mode="against").json()["id"]
        finally:
            config.INGRESS_PANEL = panel
        sent = self.ha.notifications()
        self.assertEqual([n for n, _ in sent], ["meera_phone"])
        self.assertEqual(sent[0][1]["message"], "Kabir Rao challenges you to Tank Battle.")
        self.assertEqual([a["title"] for a in sent[0][1]["data"]["actions"]], ["Join", "Not now"])
        self.assertTrue(sent[0][1]["data"]["actions"][0]["uri"].endswith(f"#/home/join/{mid}"))


class TestResults(Live):
    def report(self, scores, winner, tick=3600, sm=99):
        return {"tick": tick, "sum": sm, "scores": scores, "levels": [3, 3], "winner": winner}

    def test_both_phones_agree_each_score_is_saved_and_the_match_has_a_winner(self):
        mid = self.new_match()
        s = self.sessions(mid)
        self.start(mid)
        rep = self.report([110, 760], 2)
        r = self.score(s["u_kabir"]["id"], KABIR, 110, rep, level=3)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["saved"])
        self.assertEqual(self.match(mid)["status"], "playing", "waiting for the other phone")
        r = self.score(s["u_meera"]["id"], MEERA, 760, rep, level=3)
        self.assertEqual(r.status_code, 200, r.text)
        m = self.match(mid)
        self.assertEqual((m["status"], m["endReason"], m["winner"]), ("done", "finished", "u_meera"))
        res = {p["id"]: (p["result"], p["score"]) for p in m["players"]}
        self.assertEqual(res, {"u_kabir": ("lost", 110), "u_meera": ("won", 760)})
        with db.get_conn() as conn:
            saved = conn.execute("SELECT user_id, score, mode FROM scores ORDER BY user_id").fetchall()
        self.assertEqual([tuple(x) for x in saved], [("u_kabir", 110, "phones"), ("u_meera", 760, "phones")])
        self.assertEqual(live.get(mid).phase, "ended")
        mine = self.get("/api/against", KABIR).json()["people"][0]
        self.assertEqual((mine["name"], mine["games"], mine["losses"]), ("Meera Rao", 1, 1))

    def test_the_honest_limits_know_the_two_phone_modes(self):
        self.assertIsNone(games.check_score("duel", "phones", 1710, 1, 16))           # 7-0 and the match in 16 s
        self.assertIsNone(games.check_score("tanks", "against", 1700, 2, 11))         # every point of a quick match
        self.assertIsNone(games.check_score("tanks", "together", 600, 1, 30, 9))
        self.assertIsNone(games.check_score("snakeduel", "phones", 5300, 10, 20, 10))
        self.assertIsNotNone(games.check_score("snakeduel", "phones", 100, 11, 20, 10))   # past the arena list
        self.assertIsNotNone(games.check_score("tanks", "against", 5000, 2, 11))
        self.assertEqual(games.max_level("tanks", "against", 9), games.MAX_LEVEL)     # its own arenas, not the list

    def test_a_drawn_match(self):
        mid = self.new_match()
        s = self.sessions(mid)
        rep = self.report([300, 300], 0)
        self.score(s["u_kabir"]["id"], KABIR, 300, rep)
        self.score(s["u_meera"]["id"], MEERA, 300, rep)
        m = self.match(mid)
        self.assertEqual((m["endReason"], m["winner"]), ("finished", None))
        self.assertEqual({p["result"] for p in m["players"]}, {"draw"})

    def test_results_that_differ_are_out_of_step_with_no_result(self):
        mid = self.new_match()
        s = self.sessions(mid)
        self.score(s["u_kabir"]["id"], KABIR, 100, self.report([100, 200], 2))
        self.score(s["u_meera"]["id"], MEERA, 200, self.report([100, 200], 2, sm=98))
        m = self.match(mid)
        self.assertEqual((m["status"], m["endReason"], m["winner"]), ("done", "out_of_step", None))
        self.assertEqual({p["result"] for p in m["players"]}, {None})
        self.assertEqual(self.get("/api/against", KABIR).json()["people"], [])

    def test_a_score_that_isnt_the_reported_one_is_out_of_step(self):
        mid = self.new_match()
        s = self.sessions(mid)
        rep = self.report([100, 200], 2)
        self.score(s["u_kabir"]["id"], KABIR, 100, rep)
        self.score(s["u_meera"]["id"], MEERA, 900, rep)
        self.assertEqual(self.match(mid)["endReason"], "out_of_step")

    def test_honest_score_limits_for_each_player(self):
        mid = self.new_match()
        s = self.sessions(mid)
        rep = self.report([100, 9_000_000], 2)
        self.assertEqual(self.score(s["u_kabir"]["id"], KABIR, 100, rep).status_code, 200)
        r = self.score(s["u_meera"]["id"], MEERA, 9_000_000, rep)
        self.assertEqual(r.status_code, 422)
        self.assertEqual(self.match(mid)["endReason"], "out_of_step")
        with db.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM scores WHERE user_id = 'u_meera'").fetchone()[0], 0)

    def test_practice_is_not_saved_but_the_match_is_decided(self):
        mid = self.new_match(practice=True)
        s = self.sessions(mid)
        rep = self.report([500, 100], 1)
        self.assertEqual(self.score(s["u_kabir"]["id"], KABIR, 500, rep).json()["reason"], "practice")
        self.score(s["u_meera"]["id"], MEERA, 100, rep)
        self.assertEqual(self.match(mid)["winner"], "u_kabir")

    def test_one_phones_result_alone_decides_after_a_minute(self):
        mid = self.new_match()
        s = self.sessions(mid)
        rm = self.start(mid)
        self.score(s["u_kabir"]["id"], KABIR, 500, self.report([500, 100], 1), seconds=30)
        self.assertEqual(self.match(mid)["status"], "playing")
        rm.receive(1, {"t": "ping", "id": 1}); rm.receive(2, {"t": "ping", "id": 1})
        self.clock.advance(seconds=61)
        self.assertEqual((self.match(mid)["endReason"], self.match(mid)["winner"]), ("finished", "u_kabir"))

    def test_resigning(self):
        mid = self.new_match()
        self.sessions(mid)
        self.start(mid)
        r = self.post(f"/api/matches/{mid}/resign", None, MEERA)
        self.assertEqual(r.status_code, 200, r.text)
        m = r.json()
        self.assertEqual((m["status"], m["endReason"], m["winner"]), ("done", "resigned", "u_kabir"))
        self.assertEqual(live.get(mid).end, ("resigned", 1))
        self.assertEqual(self.post(f"/api/matches/{mid}/resign", None, MEERA).status_code, 409)
        self.assertEqual(self.post(f"/api/matches/{mid}/resign", None, ASHA).status_code, 404)

    def test_a_race_cant_be_resigned(self):
        r = self.post("/api/matches", {"game": "racer", "mode": "three", "opponents": ["u_meera"], "kind": "race"}, KABIR)
        mid = r.json()["id"]
        self.post(f"/api/matches/{mid}/accept", None, MEERA)
        self.assertEqual(self.post(f"/api/matches/{mid}/resign", None, KABIR).status_code, 409)

    def test_a_phone_gone_for_a_minute_loses(self):
        mid = self.new_match()
        self.sessions(mid)
        rm = self.start(mid)
        for _ in range(7):
            self.clock2.t += 10
            rm.receive(1, {"t": "ping", "id": 1})
        m = self.match(mid)
        self.assertEqual((m["status"], m["endReason"], m["winner"]), ("done", "left", "u_kabir"))

    def test_after_a_restart_the_match_ends_with_no_result(self):
        mid = self.new_match()
        live.ROOMS.clear()
        m = self.match(mid)
        self.assertEqual((m["status"], m["endReason"], m["winner"]), ("done", "timeout", None))

    def test_a_child_out_of_time_ends_it_for_both_as_a_draw(self):
        self.make_child(MEERA, minutesSchool=30, minutesWeekend=30)
        mid = self.new_match()
        s = self.sessions(mid)
        self.start(mid)
        together.live_watch(mid)
        self.assertEqual(self.match(mid)["status"], "playing")
        self.clock.advance(minutes=31)
        self.post(f"/api/sessions/{s['u_meera']['id']}/beat", {"activeSeconds": 31 * 60}, MEERA)
        together.live_watch(mid)
        m = self.match(mid)
        self.assertEqual((m["status"], m["endReason"], m["winner"]), ("done", "time_limit", None))
        self.assertEqual({p["result"] for p in m["players"]}, {"draw"})
        self.assertEqual(live.get(mid).end, ("time_limit", 0))

    def test_quiet_hours_beginning_end_it_too(self):
        self.make_child(MEERA)
        mid = self.new_match()
        self.sessions(mid)
        self.start(mid)
        self.put("/api/admin/users/u_meera/limits", {"quietSchoolFrom": "11:00", "quietSchoolTo": "13:00"}, ASHA)
        together.live_watch(mid)
        self.assertEqual(self.match(mid)["endReason"], "time_limit")

    def test_housekeeping_forgets_rooms_after_the_end(self):
        mid = self.new_match()
        self.post(f"/api/matches/{mid}/resign", None, MEERA)
        self.assertIn(mid, live.ROOMS)
        self.clock2.t += live.FORGET_AFTER + 1
        with db.get_conn() as conn:
            together.housekeeping(conn)
        self.assertNotIn(mid, live.ROOMS)


class TestTheLink(Live):
    def ws(self, mid, who=KABIR, **kw):
        return self.c.websocket_connect(f"/api/matches/{mid}/live", headers=who, **kw)

    def recv(self, w, t, tries=60):
        for _ in range(tries):
            m = w.receive_json()
            if m.get("t") == t:
                return m
        self.fail(f"no {t}")

    def test_two_phones_over_websockets(self):
        mid = self.new_match()
        self.sessions(mid)
        with self.ws(mid, KABIR) as a, self.ws(mid, MEERA) as b:
            self.assertEqual(a.receive_json()["t"], "match")
            a.send_json({"t": "hello", "since": 0})
            b.send_json({"t": "hello", "since": 0})
            w = self.recv(a, "welcome")
            self.assertEqual((w["seat"], w["inSeq"], w["phase"]), (1, 0, "waiting"))
            self.assertEqual(self.recv(b, "welcome")["seat"], 2)
            a.send_json({"t": "ping", "id": 9})
            self.assertEqual(self.recv(a, "pong")["id"], 9)
            a.send_json({"t": "ready", "rtt": 30})
            b.send_json({"t": "ready", "rtt": 30})
            st = self.recv(a, "start")
            self.assertEqual((st["inMs"], st["n"]), (3000, 1))
            d = st["delay"]
            self.assertEqual(self.recv(b, "start")["delay"], d)
            a.send_json({"t": "in", "seq": 1, "upto": d, "ev": [[d, "left", 1]], "seat": 2})     # a seat field changes nothing
            self.assertEqual(self.recv(a, "ack")["seq"], 1)
            got = self.recv(b, "in")
            self.assertEqual((got["seat"], got["seq"], got["ev"], got["n"]), (1, 1, [[d, "left", 1]], 2))
            b.send_json({"t": "pause"})
            self.assertEqual(self.recv(a, "pause")["by"], 2)
            a.send_json({"t": "resume"})
            self.recv(b, "resume")
            b.send_text("not json")
            b.send_json({"t": "in", "seq": 1, "upto": d, "ev": [[d, "fire", 1]]})          # Snake Duel has no fire
            self.assertEqual(self.recv(b, "error")["why"], "not a message")
            self.assertEqual(self.recv(b, "error")["why"], "action")
            m = self.recv(a, "match")
            self.assertEqual(m["kind"], "live")

    def test_a_reconnection_picks_up_where_it_left_off(self):
        mid = self.new_match()
        self.sessions(mid)
        rm = self.start(mid)
        d = rm.delay
        rm.receive(2, {"t": "in", "seq": 1, "upto": d, "ev": [[d, "up", 1]]})
        rm.receive(2, {"t": "in", "seq": 2, "upto": d + 1, "ev": []})
        with self.ws(mid, KABIR) as a:
            a.send_json({"t": "hello", "since": 1})                 # it has the start already
            self.assertEqual(self.recv(a, "welcome")["inSeq"], 0)
            self.assertEqual([self.recv(a, "in")["seq"], self.recv(a, "in")["seq"]], [1, 2])
            a.send_json({"t": "in", "seq": 1, "upto": d, "ev": []})
            self.recv(a, "ack")
        with self.ws(mid, KABIR) as a:
            a.send_json({"t": "hello", "since": 3})
            self.assertEqual(self.recv(a, "welcome")["inSeq"], 1, "it sends again from seq 2")
            a.send_json({"t": "in", "seq": 1, "upto": d, "ev": []})                       # again: just acknowledged
            self.assertEqual(self.recv(a, "ack")["seq"], 1)

    def test_the_end_is_pushed(self):
        mid = self.new_match()
        self.sessions(mid)
        self.start(mid)
        with self.ws(mid, KABIR) as a:
            a.send_json({"t": "hello", "since": 1})
            self.recv(a, "welcome")
            self.post(f"/api/matches/{mid}/resign", None, MEERA)
            e = self.recv(a, "end")
            self.assertEqual((e["reason"], e["winner"]), ("resigned", 1))

    def test_who_may_connect(self):
        mid = self.new_match()
        with self.assertRaises(WebSocketDisconnect):
            with self.ws(mid, ASHA) as w:                            # not in the match
                w.receive_json()
        with self.assertRaises(WebSocketDisconnect):
            with self.c.websocket_connect(f"/api/matches/{mid}/live", headers={}) as w:
                w.receive_json()
        outsider = TestClient(app, client=("10.0.0.5", 5555))
        with self.assertRaises(WebSocketDisconnect):
            with outsider.websocket_connect(f"/api/matches/{mid}/live", headers=KABIR) as w:
                w.receive_json()
        with self.assertRaises(WebSocketDisconnect):
            with self.c.websocket_connect(f"/api/matches/{mid}/live", headers=dict(KABIR, Origin="https://evil.example")) as w:
                w.receive_json()

    def test_a_message_too_big_closes_the_connection(self):
        mid = self.new_match()
        with self.ws(mid, KABIR) as a:
            a.receive_json()
            a.send_text("x" * (live.MAX_MSG_BYTES + 1))
            with self.assertRaises(WebSocketDisconnect):
                for _ in range(50):
                    a.receive_json()

    def test_the_http_fallback(self):
        mid = self.new_match()
        self.sessions(mid)
        url = f"/api/matches/{mid}/live"
        r = self.post(url, {"since": 0, "msgs": [{"t": "hello", "since": 0}, {"t": "ready", "rtt": 40}], "wait": 0}, KABIR)
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["msgs"][0]["t"], "welcome")
        self.assertEqual(body["match"]["kind"], "live")
        r = self.post(url, {"since": 0, "msgs": [{"t": "ready", "rtt": 40}], "wait": 0, "v": None}, MEERA)
        self.assertIn("start", [m["t"] for m in r.json()["msgs"]])
        d = live.get(mid).delay
        r = self.post(url, {"since": 1, "msgs": [{"t": "in", "seq": 1, "upto": d, "ev": [[d, "down", 1]]}], "wait": 0}, MEERA)
        self.assertEqual(r.json()["msgs"][0], {"t": "ack", "seq": 1})
        r = self.post(url, {"since": 1, "msgs": [], "wait": 2}, KABIR)
        ins = [m for m in r.json()["msgs"] if m["t"] == "in"]
        self.assertEqual((ins[0]["seat"], ins[0]["ev"]), (2, [[d, "down", 1]]))
        # the checks
        for body in ({"since": -1}, {"since": "1"}, {"msgs": "x"}, {"msgs": [{}] * 65}, {"wait": "soon"}, {"wait": True}):
            self.assertEqual(self.post(url, body, KABIR).status_code, 422, body)
        big = self.post(url, {"msgs": [{"t": "ping", "pad": "x" * live.MAX_MSG_BYTES}]}, KABIR).json()["msgs"]
        self.assertEqual(big[0]["why"], "too big")
        self.assertEqual(self.post(url, {}, ASHA).status_code, 404)
        self.get("/api/me", DEV)
        race = self.post("/api/matches", {"game": "racer", "mode": "three", "opponents": ["u_dev"], "kind": "race"}, ASHA)
        self.assertEqual(self.post(f"/api/matches/{race.json()['id']}/live", {}, ASHA).status_code, 404)


if __name__ == "__main__":
    unittest.main()
