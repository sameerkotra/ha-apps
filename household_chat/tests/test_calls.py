"""Voice and video calls (SPEC §15.12): who may call, ringing, joining, signals, ending, notes, notifications."""
import time
from datetime import timedelta

from base import ADMIN, LEELA, NISHA, TARUN, ApiTestCase, sql

from app import calls, config, notifier
from app.live import hub

SDP = "v=0\r\no=- 1 2 IN IP4 127.0.0.1\r\ns=-\r\n"


class CallTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        calls.reset()
        self.enable(ADMIN, NISHA, TARUN, LEELA)
        self.ok(self.put("/api/admin/settings", {"calls_enabled": True}))
        self.notify_to(TARUN, "mobile_app_tarun")
        self.direct = self.ok(self.post("/api/conversations/direct", {"userId": TARUN["id"]}, NISHA))["id"]
        self.events = []
        self._orig_publish = hub.publish

        def publish(uids, name, data, event_id=None):
            self.events.append((sorted(set(uids)), name, data))
            return self._orig_publish(uids, name, data, event_id)
        hub.publish = publish
        self._orig_spawn = calls._spawn
        calls._spawn = lambda fn, *args: fn(*args)       # phone notifications inline

    def tearDown(self):
        hub.publish = self._orig_publish
        calls._spawn = self._orig_spawn
        calls.reset()
        super().tearDown()

    def call_events(self, uid):
        return [d for uids, name, d in self.events if name == "call" and uid in uids]

    def ring(self, caller=NISHA, cid=None, kind="audio"):
        return self.ok(self.post("/api/calls", {"conversationId": cid or self.direct, "kind": kind}, caller), 201)

    def notes(self, cid=None, user=NISHA):
        return [m for m in self.ok(self.get(f"/api/conversations/{cid or self.direct}/messages", user))["messages"]
                if m["kind"] == "call"]

    def states(self, call_id):
        return {r["user_id"]: r["state"] for r in sql("SELECT user_id, state FROM call_members WHERE call_id = ?", (call_id,))}

    # ---------- who may call ----------
    def test_off_by_default_and_where_calls_are_possible(self):
        self.ok(self.put("/api/admin/settings", {"calls_enabled": False}))
        self.assertEqual(self.post("/api/calls", {"conversationId": self.direct}, NISHA).status_code, 403)
        self.assertFalse(self.ok(self.get("/api/me", NISHA))["app"]["callsEnabled"])
        self.ok(self.put("/api/admin/settings", {"calls_enabled": True}))
        me = self.ok(self.get("/api/me", NISHA))
        self.assertTrue(me["app"]["callsEnabled"])
        self.assertEqual(me["app"]["callRingSeconds"], 30)
        r = self.post("/api/calls", {"conversationId": self.personal(NISHA)}, NISHA)
        self.assertEqual(r.status_code, 409)
        self.assertIn("personal room", r.json()["detail"])
        self.assertEqual(self.post("/api/calls", {"conversationId": self.direct}, LEELA).status_code, 404)
        self.assertEqual(self.post("/api/calls", {"conversationId": "nope"}, NISHA).status_code, 404)
        self.assertEqual(self.post("/api/calls", {"conversationId": self.direct, "kind": "film"}, NISHA).status_code, 422)
        # ring time is a setting
        self.assertEqual(self.put("/api/admin/settings", {"calls_ring_seconds": 5}).status_code, 422)
        self.ok(self.put("/api/admin/settings", {"calls_ring_seconds": 45}))
        self.assertEqual(self.ring()["ringSeconds"], 45)

    def test_read_only_chats_and_children(self):
        self.ok(self.patch(f"/api/admin/people/{TARUN['id']}", {"disabled": True}))
        r = self.post("/api/calls", {"conversationId": self.direct}, NISHA)
        self.assertEqual(r.status_code, 409)
        self.assertIn("read-only", r.json()["detail"])
        self.ok(self.patch(f"/api/admin/people/{TARUN['id']}", {"disabled": False}))
        d = self.ok(self.post("/api/conversations/direct", {"userId": LEELA["id"]}, TARUN))["id"]
        for u in (TARUN, LEELA):
            self.ok(self.patch(f"/api/admin/people/{u['id']}", {"isChild": True}))
        self.assertEqual(self.post("/api/calls", {"conversationId": d}, TARUN).status_code, 409)
        self.ok(self.post("/api/calls", {"conversationId": self.direct}, TARUN), 201)       # a child can call an adult

    # ---------- a call that's answered ----------
    def test_ring_answer_signal_end(self):
        c = self.ring()
        self.assertEqual((c["peerId"], c["peerName"], c["kind"], c["group"]), (TARUN["id"], "Tarun", "audio", False))
        self.assertEqual({m["id"]: m["state"] for m in c["members"]}, {NISHA["id"]: "joined", TARUN["id"]: "ringing"})
        ev = self.call_events(TARUN["id"])[-1]
        self.assertEqual((ev["state"], ev["peerId"], ev["peerName"], ev["name"]), ("ringing", NISHA["id"], "Nisha", "Nisha"))
        # the phone rings: Answer opens the app on the call, Decline is a notification action
        push = self.sent.items[-1]
        self.assertEqual((push["title"], push["message"]), ("Nisha", "📞 Nisha is calling"))
        self.assertEqual(push["data"]["tag"], f"hchat_call_{c['id']}")
        self.assertEqual([a["title"] for a in push["data"]["actions"]], ["Answer", "Decline"])
        self.assertEqual(push["data"]["actions"][0]["uri"], config.INGRESS_URL + "/call/" + c["id"])
        # Tarun's page finds the call when it opens; Leela isn't told anything
        cur = self.ok(self.get("/api/calls/current", TARUN))["call"]
        self.assertEqual((cur["id"], cur["role"], cur["state"], cur["peerName"]), (c["id"], "callee", "ringing", "Nisha"))
        self.assertIsNone(self.ok(self.get("/api/calls/current", LEELA))["call"])
        self.assertEqual(self.ok(self.get("/api/calls/current", NISHA))["call"]["role"], "caller")
        self.assertEqual(self.call_events(LEELA["id"]), [])
        self.assertEqual(self.post(f"/api/calls/{c['id']}/answer", user=LEELA).status_code, 404)
        self.assertEqual(self.post(f"/api/calls/{c['id']}/answer", user=NISHA).status_code, 409)
        # no signals before joining
        sig = {"to": NISHA["id"], "type": "offer", "sdp": SDP}
        self.assertEqual(self.post(f"/api/calls/{c['id']}/signal", sig, TARUN).status_code, 409)
        # answered: the joiner is told who to offer to; the other side hears "joined"; the phone's ring is cleared
        a = self.ok(self.post(f"/api/calls/{c['id']}/answer", user=TARUN))
        self.assertEqual([p["id"] for p in a["peers"]], [NISHA["id"]])
        self.assertEqual(self.call_events(NISHA["id"])[-1]["state"], "member")
        self.assertEqual(self.call_events(NISHA["id"])[-1]["memberState"], "joined")
        self.assertEqual(self.call_events(TARUN["id"])[-1]["state"], "joined")
        clear = self.sent.items[-1]
        self.assertEqual((clear["message"], clear["data"]), ("clear_notification", {"tag": f"hchat_call_{c['id']}"}))
        # signals go to one person, and wait for a page that polls
        self.ok(self.post(f"/api/calls/{c['id']}/signal", sig, TARUN))
        got = self.call_events(NISHA["id"])[-1]
        self.assertEqual((got["state"], got["signal"]["type"], got["signal"]["sdp"], got["signal"]["from"]), ("signal", "offer", SDP, TARUN["id"]))
        self.assertEqual(self.call_events(TARUN["id"])[-1]["state"], "joined")      # not to the sender
        self.ok(self.post(f"/api/calls/{c['id']}/signal", {"to": TARUN["id"], "type": "answer", "sdp": SDP}, NISHA))
        self.ok(self.post(f"/api/calls/{c['id']}/signal", {"to": TARUN["id"], "type": "candidate", "candidate": {"candidate": "a"}}, NISHA))
        cur = self.ok(self.get("/api/calls/current", TARUN))["call"]
        self.assertEqual([x["type"] for x in cur["signals"]], ["answer", "candidate"])
        self.assertEqual(self.ok(self.get("/api/calls/current", TARUN))["call"]["signals"], [])      # drained
        self.assertEqual(self.post(f"/api/calls/{c['id']}/signal", {"to": LEELA["id"], "type": "offer", "sdp": SDP}, NISHA).status_code, 409)
        self.assertEqual(self.post(f"/api/calls/{c['id']}/signal", {"to": TARUN["id"], "type": "offer", "sdp": "x" * (calls.MAX_SDP + 1)}, NISHA).status_code, 422)
        # busy while it's on
        r = self.post("/api/calls", {"conversationId": self.direct}, TARUN)
        self.assertEqual(r.status_code, 409)
        self.assertIn("already in a call", r.json()["detail"])
        sql("UPDATE calls SET answered_at = ?", (config.iso(config.utcnow() - timedelta(minutes=4, seconds=5)),))
        calls._calls[c["id"]].answered_at = config.iso(config.utcnow() - timedelta(minutes=4, seconds=5))
        self.ok(self.post(f"/api/calls/{c['id']}/end", user=NISHA))
        self.assertEqual(self.call_events(TARUN["id"])[-1]["state"], "ended")
        self.assertEqual(self.call_events(TARUN["id"])[-1]["outcome"], "answered")
        self.assertIsNone(self.ok(self.get("/api/calls/current", TARUN))["call"])
        self.assertEqual(self.post(f"/api/calls/{c['id']}/end", user=NISHA).status_code, 404)
        # the note: from the caller, not unread for anyone, no notification
        [n] = self.notes()
        self.assertEqual((n["userId"], n["call"]["outcome"], n["call"]["callerId"], n["call"]["kind"]), (NISHA["id"], "answered", NISHA["id"], "audio"))
        self.assertGreaterEqual(n["call"]["seconds"], 245)
        self.assertEqual({m["id"]: m["state"] for m in n["call"]["members"]}, {NISHA["id"]: "left", TARUN["id"]: "left"})
        convs = {x["id"]: x for x in self.ok(self.get("/api/conversations", TARUN))["conversations"]}
        self.assertEqual(convs[self.direct]["unread"], 0)
        self.assertEqual(convs[self.direct]["lastMessage"]["preview"], "📞 Call · 4 min")
        self.assertFalse(any("Missed" in s["message"] for s in self.sent.items))
        row = sql("SELECT * FROM calls")[0]
        self.assertEqual((row["outcome"], row["message_id"], row["callee_id"]), ("answered", n["id"], TARUN["id"]))
        self.assertEqual(self.patch(f"/api/messages/{n['id']}", {"body": "x"}, NISHA).status_code, 403)
        self.ok(self.delete(f"/api/messages/{n['id']}", NISHA))
        self.assertEqual(sql("SELECT * FROM calls"), [])
        self.assertEqual(sql("SELECT * FROM call_members"), [])

    # ---------- calls that aren't answered ----------
    def test_missed_after_ring_time(self):
        c = self.ring()
        calls._calls[c["id"]].ring_until = time.monotonic() - 1
        self.assertEqual(calls.tick(), 1)
        self.assertEqual(self.call_events(NISHA["id"])[-1]["outcome"], "missed")
        [n] = self.notes()
        self.assertEqual(n["call"]["outcome"], "missed")
        self.assertEqual(self.states(c["id"]), {NISHA["id"]: "left", TARUN["id"]: "missed"})
        convs = {x["id"]: x for x in self.ok(self.get("/api/conversations", TARUN))["conversations"]}
        self.assertEqual((convs[self.direct]["unread"], convs[self.direct]["lastMessage"]["preview"]), (1, "📞 Missed call"))
        mine = {x["id"]: x for x in self.ok(self.get("/api/conversations", NISHA))["conversations"]}
        self.assertEqual(mine[self.direct]["unread"], 0)
        self.assertEqual(self.sent.items[-2]["message"], "clear_notification")
        self.assertEqual((self.sent.items[-1]["title"], self.sent.items[-1]["message"]), ("Nisha", "📞 Missed call from Nisha"))
        self.assertEqual(self.sent.items[-1]["data"]["tag"], f"hchat_{self.direct}")

    def test_caller_hangs_up_while_ringing_and_callee_declines(self):
        c = self.ring()
        self.ok(self.post(f"/api/calls/{c['id']}/end", user=NISHA))
        self.assertEqual(self.notes()[-1]["call"]["outcome"], "missed")
        c = self.ring()
        self.assertEqual(self.post(f"/api/calls/{c['id']}/decline", user=NISHA).status_code, 409)
        self.ok(self.post(f"/api/calls/{c['id']}/decline", user=TARUN))
        self.assertEqual(self.notes()[-1]["call"]["outcome"], "declined")
        self.assertEqual(self.call_events(NISHA["id"])[-1]["outcome"], "declined")
        self.assertFalse(any(s["message"].startswith("📞 Missed call") for s in self.sent.items[-2:]))
        convs = {x["id"]: x for x in self.ok(self.get("/api/conversations", TARUN))["conversations"]}
        self.assertEqual(convs[self.direct]["unread"], 1)     # only the first, missed one

    def test_decline_from_the_phone(self):
        c = self.ring()
        action = self.sent.items[-1]["data"]["actions"][1]["action"]
        self.assertTrue(action.startswith("HCHAT_DECLINE_"))
        self.ok(self.put("/api/admin/settings", {"notification_reply": False}))     # not needed for Decline
        self.assertEqual(notifier.handle_action(action, None), "declined")
        self.assertEqual(self.notes()[-1]["call"]["outcome"], "declined")
        self.assertIsNone(self.ok(self.get("/api/calls/current", TARUN))["call"])
        self.assertEqual(notifier.handle_action(action, None), "no call")
        self.assertNotIn(c["id"], calls._calls)

    def test_busy(self):
        d2 = self.ok(self.post("/api/conversations/direct", {"userId": TARUN["id"]}, LEELA))["id"]
        self.ring()
        r = self.post("/api/calls", {"conversationId": d2}, LEELA)
        self.assertEqual(r.status_code, 409)
        self.assertIn("Tarun is on another call", r.json()["detail"])
        notes = self.notes(d2, LEELA)
        self.assertEqual(notes[0]["call"]["outcome"], "busy")
        codes = [self.post("/api/calls", {"conversationId": d2}, LEELA).status_code for _ in range(30)]
        self.assertEqual(codes[-1], 429)

    def test_quiet_hours_and_mute_dont_ring_the_phone(self):
        self.ok(self.put(f"/api/conversations/{self.direct}/me",
                         {"mutedUntil": config.iso(config.utcnow() + timedelta(hours=2))}, TARUN))
        n = len(self.sent.items)
        c = self.ring()
        self.assertEqual(len(self.sent.items), n)
        self.assertEqual(self.call_events(TARUN["id"])[-1]["state"], "ringing")     # open tabs still ring
        self.ok(self.post(f"/api/calls/{c['id']}/end", user=NISHA))
        self.assertEqual(len(self.sent.items), n)
        self.ok(self.put(f"/api/conversations/{self.direct}/me", {"mutedUntil": None}, TARUN))
        self.ok(self.put("/api/me/settings", {"quietStart": "00:00", "quietEnd": "23:59"}, TARUN))
        self.ring()
        self.assertEqual(len(self.sent.items), n)

    def test_failed_disabled_and_restart(self):
        c = self.ring()
        self.ok(self.post(f"/api/calls/{c['id']}/end", {"reason": "no_microphone"}, TARUN))
        ev = self.call_events(NISHA["id"])[-1]
        self.assertEqual((ev["outcome"], ev["reason"]), ("failed", "no_microphone"))
        self.assertEqual(self.post(f"/api/calls/{c['id']}/end", {"reason": "other"}, TARUN).status_code, 422)
        # disabling someone ends their call
        c = self.ring()
        self.ok(self.post(f"/api/calls/{c['id']}/answer", user=TARUN))
        self.ok(self.patch(f"/api/admin/people/{TARUN['id']}", {"disabled": True}))
        self.assertNotIn(c["id"], calls._calls)
        self.assertEqual(self.notes()[-1]["call"]["outcome"], "answered")       # it had connected
        self.ok(self.patch(f"/api/admin/people/{TARUN['id']}", {"disabled": False}))
        # an answered call ends when one side has been gone a minute
        c = self.ring()
        self.ok(self.post(f"/api/calls/{c['id']}/answer", user=TARUN))
        calls.tick()
        self.assertIn(c["id"], calls._calls)
        for m in calls._calls[c["id"]].members.values():
            if m.gone_since is not None:
                m.gone_since -= calls.GONE_SECONDS + 1
        calls.tick()
        self.assertNotIn(c["id"], calls._calls)
        self.assertEqual(self.notes()[-1]["call"]["outcome"], "answered")
        # a restart: a call that was ringing is closed as couldn't connect
        self.ring()
        calls.reset()
        self.assertEqual(calls.close_unfinished(), 1)
        self.assertEqual(self.notes()[-1]["call"]["outcome"], "failed")
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM calls WHERE ended_at IS NULL")[0]["n"], 0)

    def test_disappearing_chats(self):
        self.ok(self.patch(f"/api/conversations/{self.direct}", {"disappearSeconds": 3600}, NISHA))
        c = self.ring()
        self.ok(self.post(f"/api/calls/{c['id']}/end", user=NISHA))
        self.assertIsNotNone(self.notes()[-1]["expiresAt"])

    # ---------- video ----------
    def test_video_call(self):
        c = self.ring(kind="video")
        self.assertEqual(c["kind"], "video")
        self.assertEqual(self.sent.items[-1]["message"], "📹 Nisha is calling (video)")
        self.assertEqual(self.ok(self.get("/api/calls/current", TARUN))["call"]["kind"], "video")
        self.ok(self.post(f"/api/calls/{c['id']}/answer", user=TARUN))
        self.ok(self.post(f"/api/calls/{c['id']}/end", user=TARUN))
        [n] = self.notes()
        self.assertEqual((n["call"]["kind"], n["call"]["outcome"]), ("video", "answered"))
        convs = {x["id"]: x for x in self.ok(self.get("/api/conversations", TARUN))["conversations"]}
        self.assertTrue(convs[self.direct]["lastMessage"]["preview"].startswith("📹 Video call · "))
        c = self.ring(kind="video")
        calls._calls[c["id"]].ring_until = time.monotonic() - 1
        calls.tick()
        self.assertEqual(self.sent.items[-1]["message"], "📹 Missed video call from Nisha")

    # ---------- group calls ----------
    def test_group_call_up_to_four(self):
        self.enable(ADMIN, NISHA, TARUN, LEELA)
        from base import headers
        meera = ADMIN
        hh = self.household()
        self.notify_to(LEELA, "mobile_app_leela")
        c = self.ring(NISHA, hh)
        self.assertTrue(c["group"])
        self.assertEqual(c["name"], "Household")
        self.assertIsNone(c["peerId"])
        self.assertEqual({m["id"]: m["state"] for m in c["members"]},
                         {NISHA["id"]: "joined", TARUN["id"]: "ringing", LEELA["id"]: "ringing", meera["id"]: "ringing"})
        self.assertEqual(self.sent.items[-1]["message"], "📞 Nisha is starting a group call in Household")
        ev = self.call_events(LEELA["id"])[-1]
        self.assertEqual((ev["state"], ev["group"], ev["name"], ev["peerName"]), ("ringing", True, "Household", "Nisha"))
        # Tarun joins: offers to Nisha only; Leela joins next: offers to both
        a = self.ok(self.post(f"/api/calls/{c['id']}/answer", user=TARUN))
        self.assertEqual([p["id"] for p in a["peers"]], [NISHA["id"]])
        a = self.ok(self.post(f"/api/calls/{c['id']}/answer", user=LEELA))
        self.assertEqual({p["id"] for p in a["peers"]}, {NISHA["id"], TARUN["id"]})
        self.assertEqual(self.call_events(TARUN["id"])[-1]["memberState"], "joined")
        # four is the limit: the fifth person would be refused
        a = self.ok(self.post(f"/api/calls/{c['id']}/answer", user=meera))
        self.assertEqual(len(a["peers"]), 3)
        sixth = {"id": "u-six", "name": "six", "display": "Six"}
        self.enable(sixth)
        self.assertEqual(self.ok(self.get("/api/calls/current", sixth))["call"], None)     # wasn't invited (joined later)
        # Tarun leaves: the call goes on for three; the others are told
        self.ok(self.post(f"/api/calls/{c['id']}/end", user=TARUN))
        self.assertIn(c["id"], calls._calls)
        ev = self.call_events(LEELA["id"])[-1]
        self.assertEqual((ev["state"], ev["userId"], ev["memberState"]), ("member", TARUN["id"], "left"))
        self.assertIsNone(self.ok(self.get("/api/calls/current", TARUN))["call"])
        # Tarun is free to take another call; it would be full for him anyway
        d2 = self.ok(self.post("/api/conversations/direct", {"userId": sixth["id"]}, TARUN))["id"]
        self.ok(self.post("/api/calls", {"conversationId": d2}, TARUN), 201)
        # when only one is left the call ends
        self.ok(self.post(f"/api/calls/{c['id']}/end", user=LEELA))
        self.assertIn(c["id"], calls._calls)
        self.ok(self.post(f"/api/calls/{c['id']}/end", user=meera))
        self.assertNotIn(c["id"], calls._calls)
        self.assertEqual(self.call_events(NISHA["id"])[-1]["outcome"], "answered")
        [n] = self.notes(hh)
        self.assertEqual((n["call"]["group"], n["call"]["outcome"]), (True, "answered"))
        self.assertEqual({m["id"]: m["state"] for m in n["call"]["members"]},
                         {NISHA["id"]: "left", TARUN["id"]: "left", LEELA["id"]: "left", meera["id"]: "left"})
        self.assertEqual(sql("SELECT callee_id, is_group FROM calls WHERE id = ?", (c["id"],))[0], {"callee_id": "", "is_group": 1})
        convs = {x["id"]: x for x in self.ok(self.get("/api/conversations", TARUN))["conversations"]}
        self.assertEqual(convs[hh]["unread"], 0)
        self.assertTrue(convs[hh]["lastMessage"]["preview"].startswith("📞 Group call · "))
        # the Calls list says who was in it
        mine = self.ok(self.get("/api/me/calls", NISHA))["calls"][0]
        self.assertEqual((mine["group"], mine["peerName"], sorted(mine["with"])), (True, "Household", ["Leela", "Meera", "Tarun"]))

    def test_group_call_missed_declined_and_full(self):
        hh = self.household()
        self.notify_to(LEELA, "mobile_app_leela")
        # nobody answers: missed for everyone rung, one push each
        c = self.ring(NISHA, hh)
        calls._calls[c["id"]].ring_until = time.monotonic() - 1
        calls.tick()
        [n] = self.notes(hh)
        self.assertEqual(n["call"]["outcome"], "missed")
        self.assertEqual(self.states(c["id"])[LEELA["id"]], "missed")
        missed_pushes = [s for s in self.sent.items if s["message"] == "📞 Missed group call from Nisha"]
        self.assertEqual(sorted(sum((s["services"] for s in missed_pushes), [])), ["mobile_app_leela", "mobile_app_tarun"])
        for u in (TARUN, LEELA, ADMIN):
            convs = {x["id"]: x for x in self.ok(self.get("/api/conversations", u))["conversations"]}
            self.assertEqual(convs[hh]["unread"], 1, u["display"])
        # everyone declines: declined, nothing unread, no pushes
        n_sent = len(self.sent.items)
        c = self.ring(NISHA, hh)
        for u in (TARUN, LEELA, ADMIN):
            self.ok(self.post(f"/api/calls/{c['id']}/decline", user=u))
        self.assertNotIn(c["id"], calls._calls)
        self.assertEqual(self.notes(hh)[-1]["call"]["outcome"], "declined")
        self.assertFalse(any("Missed" in s["message"] for s in self.sent.items[n_sent:]))
        # one answers, the rest are missed when the ringing stops: the call goes on for two
        c = self.ring(NISHA, hh)
        self.ok(self.post(f"/api/calls/{c['id']}/answer", user=TARUN))
        calls._calls[c["id"]].ring_until = time.monotonic() - 1
        calls.tick()
        self.assertIn(c["id"], calls._calls)
        self.assertEqual(self.ok(self.get("/api/calls/current", LEELA))["call"], None)
        self.assertEqual(self.call_events(LEELA["id"])[-1]["reason"], "missed")
        # the caller leaves while it rings for everyone: the call is over, missed for them
        self.ok(self.post(f"/api/calls/{c['id']}/end", user=TARUN))
        self.assertNotIn(c["id"], calls._calls)
        c = self.ring(NISHA, hh)
        self.ok(self.post(f"/api/calls/{c['id']}/end", user=NISHA))
        self.assertNotIn(c["id"], calls._calls)
        self.assertEqual(self.notes(hh)[-1]["call"]["outcome"], "missed")
        # someone in another call isn't rung; a group with nobody free can't be called
        d = self.ok(self.post("/api/conversations/direct", {"userId": LEELA["id"]}, TARUN))["id"]
        self.ring(TARUN, d)
        c = self.ring(NISHA, hh)
        self.assertEqual({m["id"]: m["state"] for m in c["members"]}[TARUN["id"]], "busy")
        self.assertEqual({m["id"]: m["state"] for m in c["members"]}[LEELA["id"]], "busy")
        self.assertEqual({m["id"]: m["state"] for m in c["members"]}[ADMIN["id"]], "ringing")

    def test_a_2_2_database_gets_the_call_kind(self):
        import os
        from app import db
        calls.reset()
        for ext in ("", "-wal", "-shm"):
            if os.path.exists(config.DB_PATH + ext):
                os.remove(config.DB_PATH + ext)
        old = db.SCHEMA.replace(db.NEW_KINDS, db.CARD_KINDS)
        old = old[:old.index("-- voice calls")] + old[old.index("CREATE TABLE IF NOT EXISTS stars ("):]
        conn = db._connect()
        conn.executescript(old)
        db._migrate(conn)
        now = config.now_iso()
        conn.execute("INSERT INTO users (id, name, disabled, created_at) VALUES ('u1', 'Nisha', 0, ?)", (now,))
        conn.execute("INSERT INTO conversations (id, kind, name, folder, created_by, created_at) VALUES "
                     "('c1', 'group', 'G', 'G', 'u1', ?)", (now,))
        conn.execute("INSERT INTO messages (conversation_id, user_id, kind, body, created_at) VALUES ('c1', 'u1', 'card', 'Trip', ?)",
                     (now,))
        conn.commit()
        conn.close()
        db.init_db()
        self.assertIn(db.NEW_KINDS, sql("SELECT sql FROM sqlite_master WHERE name = 'messages'")[0]["sql"])
        self.assertEqual([(m["kind"], m["body"]) for m in sql("SELECT * FROM messages")], [("card", "Trip")])
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM calls")[0]["n"], 0)
        self.assertFalse(db._allow_new_kinds(db._connect()))

    def test_a_2_4_database_gets_call_members(self):
        """Calls from before group calls get their members from the caller and callee columns."""
        from app import db
        now = config.now_iso()
        with db.get_conn() as conn:
            conn.execute("DROP TABLE call_members")
            conn.execute("INSERT INTO calls (id, conversation_id, caller_id, callee_id, started_at, answered_at, ended_at, outcome) "
                         "VALUES ('old1', ?, ?, ?, ?, ?, ?, 'answered')", (self.direct, NISHA["id"], TARUN["id"], now, now, now))
            conn.execute("INSERT INTO calls (id, conversation_id, caller_id, callee_id, started_at, ended_at, outcome) "
                         "VALUES ('old2', ?, ?, ?, ?, ?, 'missed')", (self.direct, NISHA["id"], TARUN["id"], now, now))
        db.init_db()
        self.assertEqual(self.states("old1"), {NISHA["id"]: "left", TARUN["id"]: "left"})
        self.assertEqual(self.states("old2"), {NISHA["id"]: "left", TARUN["id"]: "missed"})


class CallsListAndLinksTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        calls.reset()
        self.enable(ADMIN, NISHA, TARUN, LEELA)
        self.ok(self.put("/api/admin/settings", {"calls_enabled": True}))
        self.notify_to(TARUN, "mobile_app_tarun")
        self.direct = self.ok(self.post("/api/conversations/direct", {"userId": TARUN["id"]}, NISHA))["id"]
        self._orig_spawn = calls._spawn
        calls._spawn = lambda fn, *args: fn(*args)

    def tearDown(self):
        calls._spawn = self._orig_spawn
        calls.reset()
        super().tearDown()

    def ring(self):
        return self.ok(self.post("/api/calls", {"conversationId": self.direct}, NISHA), 201)

    def test_calls_list(self):
        self.assertEqual(self.ok(self.get("/api/me/calls", NISHA))["calls"], [])
        c = self.ring()
        self.ok(self.post(f"/api/calls/{c['id']}/answer", user=TARUN))
        self.ok(self.post(f"/api/calls/{c['id']}/end", user=TARUN))
        c2 = self.ring()
        self.ok(self.post(f"/api/calls/{c2['id']}/decline", user=TARUN))
        c3 = self.ring()
        calls._calls[c3["id"]].ring_until = time.monotonic() - 1
        calls.tick()
        mine = self.ok(self.get("/api/me/calls", NISHA))["calls"]
        self.assertEqual([x["outcome"] for x in mine], ["missed", "declined", "answered"])
        self.assertEqual([x["missed"] for x in mine], [False, False, False])         # Nisha called: nothing missed for her
        self.assertTrue(all(x["outgoing"] and x["peerName"] == "Tarun" and x["canCallBack"] for x in mine))
        theirs = self.ok(self.get("/api/me/calls", TARUN))["calls"]
        self.assertEqual([x["missed"] for x in theirs], [True, False, False])
        self.assertEqual(theirs[-1]["seconds"], 0)
        self.assertEqual(theirs[0]["peerName"], "Nisha")
        self.assertFalse(theirs[0]["outgoing"])
        # a call that's still on isn't listed; someone else never sees them
        self.ring()
        self.assertEqual(len(self.ok(self.get("/api/me/calls", NISHA))["calls"]), 3)
        self.assertEqual(self.ok(self.get("/api/me/calls", LEELA))["calls"], [])
        # no call back into a read-only chat
        self.ok(self.patch(f"/api/admin/people/{TARUN['id']}", {"disabled": True}))
        self.assertFalse(self.ok(self.get("/api/me/calls", NISHA))["calls"][0]["canCallBack"])

    def test_notifications_link_to_the_chat_and_the_call(self):
        self.send(self.direct, "hello", NISHA)
        self.assertEqual(self.sent.items[-1]["data"]["clickAction"], config.INGRESS_URL + "/chat/" + self.direct)
        c = self.ring()
        push = self.sent.items[-1]
        self.assertEqual(push["data"]["clickAction"], config.INGRESS_URL + "/call/" + c["id"])
        self.assertEqual(push["data"]["actions"][0]["uri"], config.INGRESS_URL + "/call/" + c["id"])


class AwayFromHomeTests(ApiTestCase):
    """Release 2 (§15.13): STUN and relay addresses for the browsers, Cloudflare and coturn credentials."""

    def setUp(self):
        super().setUp()
        calls.reset()
        calls.reset_relay_cache()
        self.enable(ADMIN, NISHA, TARUN)
        self.ok(self.put("/api/admin/settings", {"calls_enabled": True}))
        self.direct = self.ok(self.post("/api/conversations/direct", {"userId": TARUN["id"]}, NISHA))["id"]
        self.cf_calls = []
        self._orig_cf = calls.cf_fetch

        def fake_cf(key_id, token, ttl=calls.CF_TTL):
            self.cf_calls.append((key_id, token, ttl))
            if token == "bad":
                raise OSError("403")
            return [{"urls": ["turn:turn.cloudflare.com:3478?transport=udp", "turns:turn.cloudflare.com:5349"],
                     "username": "cfuser", "credential": "cfpass"}]
        calls.cf_fetch = fake_cf

    def tearDown(self):
        calls.cf_fetch = self._orig_cf
        calls.reset()
        super().tearDown()

    def start(self):
        return self.ok(self.post("/api/calls", {"conversationId": self.direct}, NISHA), 201)

    def test_home_only_by_default_then_stun(self):
        self.assertEqual(self.start()["iceServers"], [])
        calls.reset()
        self.assertEqual(self.put("/api/admin/settings", {"calls_stun": "http://x"}).status_code, 422)
        self.ok(self.put("/api/admin/settings", {"calls_stun": "stun:stun.cloudflare.com:3478"}))
        self.assertEqual(self.start()["iceServers"], [{"urls": "stun:stun.cloudflare.com:3478"}])

    def test_cloudflare_relay(self):
        r = self.put("/api/admin/settings", {"calls_relay": "cloudflare"})
        self.assertEqual(r.status_code, 422)                  # needs the key id and token
        self.ok(self.put("/api/admin/settings", {"calls_relay": "cloudflare", "calls_cf_key_id": "k1", "calls_cf_api_token": "tok"}))
        s = self.ok(self.get("/api/admin/settings"))
        self.assertEqual(s["values"]["calls_cf_api_token"], "")            # never sent to the browser
        self.assertTrue(s["secretsSet"]["calls_cf_api_token"])
        servers = self.start()["iceServers"]
        self.assertEqual(servers[0]["username"], "cfuser")
        self.assertEqual(self.cf_calls, [("k1", "tok", calls.CF_TTL)])
        # cached: a second call within the hour doesn't ask again
        calls.reset()
        self.start()
        self.assertEqual(len(self.cf_calls), 1)
        calls.reset()
        self.start()
        self.assertEqual(len(self.cf_calls), 1)
        # a blank token keeps the saved one; the Remove flag clears it; a failure means no relay, the call goes on
        self.ok(self.put("/api/admin/settings", {"calls_cf_api_token": ""}))
        self.assertTrue(self.ok(self.get("/api/admin/settings"))["secretsSet"]["calls_cf_api_token"])
        self.ok(self.put("/api/admin/settings", {"calls_cf_api_token": "bad"}))
        calls.reset()
        self.assertEqual(self.start()["iceServers"], [])
        self.assertEqual(self.ok(self.get("/api/admin/calls/ice-servers"))["relayError"], "OSError")
        self.assertEqual(self.put("/api/admin/settings", {"clear_calls_cf_api_token": True}).status_code, 422)   # the relay still needs it
        self.ok(self.put("/api/admin/settings", {"clear_calls_cf_api_token": True, "calls_relay": "none"}))
        self.assertFalse(self.ok(self.get("/api/admin/settings"))["secretsSet"]["calls_cf_api_token"])
        # test calling (admins)
        t = self.ok(self.get("/api/admin/calls/ice-servers"))
        self.assertEqual((t["relay"], t["relayOk"], t["iceServers"]), ("none", True, []))
        self.assertEqual(self.get("/api/admin/calls/ice-servers", NISHA).status_code, 403)

    def test_own_turn_server(self):
        self.assertEqual(self.put("/api/admin/settings", {"calls_turn_url": "turn:bad url"}).status_code, 422)
        self.ok(self.put("/api/admin/settings", {"calls_relay": "turn", "calls_turn_url": "turn:home.example.com:3478, turns:home.example.com:443",
                                                 "calls_turn_secret": "s3cret"}))
        c = self.start()
        [srv] = c["iceServers"]
        self.assertEqual(srv["urls"], ["turn:home.example.com:3478", "turns:home.example.com:443"])
        expiry, _, cid = srv["username"].partition(":")
        self.assertEqual(cid, c["id"])
        self.assertGreater(int(expiry), time.time() + 3600)
        import base64, hashlib, hmac
        want = base64.b64encode(hmac.new(b"s3cret", srv["username"].encode(), hashlib.sha1).digest()).decode()
        self.assertEqual(srv["credential"], want)
        self.assertNotIn("s3cret", str(srv))
        t = self.ok(self.get("/api/admin/calls/ice-servers"))
        self.assertTrue(t["relayOk"])
        self.assertEqual(t["iceServers"][0]["username"].split(":")[1], "test")

    def test_secrets_stay_out_of_backups_and_survive_a_restore(self):
        import io, sqlite3, zipfile
        self.ok(self.put("/api/admin/settings", {"calls_relay": "turn", "calls_turn_url": "turn:h.example.com:3478",
                                                 "calls_turn_secret": "s3cret", "calls_stun": "stun:s.example.com"}))
        backup = self.get("/api/admin-storage-download-db").content
        with zipfile.ZipFile(io.BytesIO(backup)) as z:
            raw = z.read("chat.db")
        self.assertNotIn(b"s3cret", raw)
        self.assertIn(b"s.example.com", raw)                     # not a secret: kept
        # restoring that backup over this install keeps the secret it has
        self.ok(self.req("POST", "/api/admin-storage-import-db", ADMIN, content=backup))
        self.assertEqual(sql("SELECT value FROM app_settings WHERE key = 'calls_turn_secret'")[0]["value"], '"s3cret"')
        self.assertEqual(self.start()["iceServers"][1]["urls"], ["turn:h.example.com:3478"])


class RelayUsageTests(ApiTestCase):
    """Relay usage (§15.13): the phones report what went through the relay; App settings sums it per month."""

    def setUp(self):
        super().setUp()
        calls.reset()
        self.enable(ADMIN, NISHA, TARUN, LEELA)
        self.ok(self.put("/api/admin/settings", {"calls_enabled": True, "calls_relay": "turn",
                                                 "calls_turn_url": "turn:h.example.com:3478", "calls_turn_secret": "s"}))
        self.direct = self.ok(self.post("/api/conversations/direct", {"userId": TARUN["id"]}, NISHA))["id"]

    def tearDown(self):
        calls.reset()
        super().tearDown()

    def test_usage_is_counted_per_month(self):
        u = self.ok(self.get("/api/admin/calls/usage"))
        self.assertEqual((u["bytes"], u["relayedCalls"], u["calls"], u["relay"], u["freeBytes"]), (0, 0, 0, "turn", None))
        c = self.ok(self.post("/api/calls", {"conversationId": self.direct}, NISHA), 201)
        self.ok(self.post(f"/api/calls/{c['id']}/answer", user=TARUN))
        # each phone reports its own relayed bytes; the highest figure per phone counts
        self.ok(self.post(f"/api/calls/{c['id']}/usage", {"relayBytes": 5_000_000}, NISHA))
        self.ok(self.post(f"/api/calls/{c['id']}/usage", {"relayBytes": 8_000_000}, NISHA))
        self.ok(self.post(f"/api/calls/{c['id']}/usage", {"relayBytes": 7_000_000}, NISHA))
        self.ok(self.post(f"/api/calls/{c['id']}/usage", {"relayBytes": 2_000_000}, TARUN))
        self.assertEqual(self.post(f"/api/calls/{c['id']}/usage", {"relayBytes": 1}, LEELA).status_code, 404)
        self.assertEqual(self.post(f"/api/calls/{c['id']}/usage", {"relayBytes": -1}, NISHA).status_code, 422)
        self.ok(self.post(f"/api/calls/{c['id']}/end", user=TARUN))
        # the last report comes after the call ended (the page sends it as it closes)
        self.ok(self.post(f"/api/calls/{c['id']}/usage", {"relayBytes": 9_000_000}, NISHA))
        u = self.ok(self.get("/api/admin/calls/usage"))
        self.assertEqual((u["bytes"], u["relayedCalls"], u["calls"]), (11_000_000, 1, 1))
        self.assertEqual(u["month"], config.local_now().strftime("%Y-%m"))
        self.assertEqual(u["months"], [{"month": u["month"], "bytes": 11_000_000, "calls": 1}])
        self.assertEqual(self.get("/api/admin/calls/usage", NISHA).status_code, 403)
        # a report an hour after the end is refused; Cloudflare's free allowance is shown with that relay
        sql("UPDATE calls SET ended_at = ?", (config.iso(config.utcnow() - timedelta(hours=2)),))
        self.assertEqual(self.post(f"/api/calls/{c['id']}/usage", {"relayBytes": 1}, NISHA).status_code, 409)
        self.ok(self.put("/api/admin/settings", {"calls_relay": "cloudflare", "calls_cf_key_id": "k", "calls_cf_api_token": "t"}))
        self.assertEqual(self.ok(self.get("/api/admin/calls/usage"))["freeBytes"], 1000 * 10 ** 9)
        # a deleted note takes the call's usage with it
        [n] = [m for m in self.ok(self.get(f"/api/conversations/{self.direct}/messages", NISHA))["messages"] if m["kind"] == "call"]
        self.ok(self.delete(f"/api/messages/{n['id']}", NISHA))
        self.assertEqual(self.ok(self.get("/api/admin/calls/usage"))["bytes"], 0)
