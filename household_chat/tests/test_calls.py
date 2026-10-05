"""Voice calls (SPEC §15.12): who may call, ringing, answering, ending, notes, notifications."""
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

    def ring(self, caller=NISHA, cid=None):
        c = self.ok(self.post("/api/calls", {"conversationId": cid or self.direct}, caller), 201)
        self.ok(self.post(f"/api/calls/{c['id']}/offer", {"sdp": SDP}, caller))
        return c

    def notes(self, cid=None):
        return [m for m in self.ok(self.get(f"/api/conversations/{cid or self.direct}/messages", NISHA))["messages"]
                if m["kind"] == "call"]

    # ---------- who may call ----------
    def test_off_by_default_and_where_calls_are_possible(self):
        self.ok(self.put("/api/admin/settings", {"calls_enabled": False}))
        self.assertEqual(self.post("/api/calls", {"conversationId": self.direct}, NISHA).status_code, 403)
        self.assertFalse(self.ok(self.get("/api/me", NISHA))["app"]["callsEnabled"])
        self.ok(self.put("/api/admin/settings", {"calls_enabled": True}))
        me = self.ok(self.get("/api/me", NISHA))
        self.assertTrue(me["app"]["callsEnabled"])
        self.assertEqual(me["app"]["callRingSeconds"], 30)
        # not in groups, not in my room, not in a chat I'm not in
        r = self.post("/api/calls", {"conversationId": self.household()}, NISHA)
        self.assertEqual(r.status_code, 409)
        self.assertIn("direct chat", r.json()["detail"])
        r = self.post("/api/calls", {"conversationId": self.personal(NISHA)}, NISHA)
        self.assertEqual(r.status_code, 409)
        self.assertIn("personal room", r.json()["detail"])
        self.assertEqual(self.post("/api/calls", {"conversationId": self.direct}, LEELA).status_code, 404)
        self.assertEqual(self.post("/api/calls", {"conversationId": "nope"}, NISHA).status_code, 404)
        # ring time is a setting
        self.assertEqual(self.put("/api/admin/settings", {"calls_ring_seconds": 5}).status_code, 422)
        self.ok(self.put("/api/admin/settings", {"calls_ring_seconds": 45}))
        self.assertEqual(self.ok(self.post("/api/calls", {"conversationId": self.direct}, NISHA), 201)["ringSeconds"], 45)

    def test_read_only_chats_and_children(self):
        self.ok(self.patch(f"/api/admin/people/{TARUN['id']}", {"disabled": True}))
        r = self.post("/api/calls", {"conversationId": self.direct}, NISHA)
        self.assertEqual(r.status_code, 409)
        self.assertIn("read-only", r.json()["detail"])
        self.ok(self.patch(f"/api/admin/people/{TARUN['id']}", {"disabled": False}))
        # two children only while they may message each other
        d = self.ok(self.post("/api/conversations/direct", {"userId": LEELA["id"]}, TARUN))["id"]
        for u in (TARUN, LEELA):
            self.ok(self.patch(f"/api/admin/people/{u['id']}", {"isChild": True}))
        self.assertEqual(self.post("/api/calls", {"conversationId": d}, TARUN).status_code, 409)
        # a child can call an adult
        self.ok(self.post("/api/calls", {"conversationId": self.direct}, TARUN), 201)

    # ---------- a call that's answered ----------
    def test_ring_answer_end(self):
        c = self.ring()
        ev = self.call_events(TARUN["id"])[-1]
        self.assertEqual((ev["state"], ev["peerId"], ev["peerName"]), ("ringing", NISHA["id"], "Nisha"))
        self.assertNotIn("sdp", ev)                     # the offer is fetched, not broadcast
        # the phone rings: Answer opens the app, Decline is a notification action
        push = self.sent.items[-1]
        self.assertEqual((push["title"], push["message"]), ("Nisha", "📞 Nisha is calling"))
        self.assertEqual(push["data"]["tag"], f"hchat_call_{c['id']}")
        self.assertEqual([a["title"] for a in push["data"]["actions"]], ["Answer", "Decline"])
        self.assertEqual(push["data"]["actions"][0]["uri"], config.INGRESS_URL)
        # Tarun's page finds the call when it opens; Leela isn't told anything
        cur = self.ok(self.get("/api/calls/current", TARUN))["call"]
        self.assertEqual((cur["id"], cur["role"], cur["state"], cur["offer"]), (c["id"], "callee", "ringing", SDP))
        self.assertIsNone(self.ok(self.get("/api/calls/current", LEELA))["call"])
        self.assertEqual(self.ok(self.get("/api/calls/current", NISHA))["call"]["role"], "caller")
        self.assertNotIn("offer", self.ok(self.get("/api/calls/current", NISHA))["call"])
        self.assertEqual(self.call_events(LEELA["id"]), [])
        self.assertEqual(self.post(f"/api/calls/{c['id']}/answer", {"sdp": SDP}, LEELA).status_code, 404)
        self.assertEqual(self.post(f"/api/calls/{c['id']}/answer", {"sdp": SDP}, NISHA).status_code, 409)
        # a candidate from the caller while it rings is kept for the person called
        self.ok(self.post(f"/api/calls/{c['id']}/candidate", {"candidate": {"candidate": "a", "sdpMid": "0"}}, NISHA))
        self.assertEqual(self.ok(self.get("/api/calls/current", TARUN))["call"]["candidates"], [{"candidate": "a", "sdpMid": "0"}])
        self.assertEqual(self.post(f"/api/calls/{c['id']}/candidate", {"candidate": {"candidate": "b"}}, TARUN).status_code, 409)
        # answered: the caller gets the answer; the ringing notification is taken off the phone
        self.ok(self.post(f"/api/calls/{c['id']}/answer", {"sdp": SDP + "a=x\r\n"}, TARUN))
        ans = self.call_events(NISHA["id"])[-1]
        self.assertEqual((ans["state"], ans["sdp"]), ("answered", SDP + "a=x\r\n"))
        self.assertEqual([d["state"] for d in self.call_events(TARUN["id"])][-1], "answered")
        self.assertNotIn("sdp", self.call_events(TARUN["id"])[-1])
        clear = self.sent.items[-1]
        self.assertEqual((clear["message"], clear["data"]), ("clear_notification", {"tag": f"hchat_call_{c['id']}"}))
        # candidates now go straight to the other side
        self.ok(self.post(f"/api/calls/{c['id']}/candidate", {"candidate": {"candidate": "c"}}, TARUN))
        self.assertEqual(self.call_events(NISHA["id"])[-1]["candidate"], {"candidate": "c"})
        # busy while it's on
        r = self.post("/api/calls", {"conversationId": self.direct}, TARUN)
        self.assertEqual(r.status_code, 409)
        self.assertIn("already in a call", r.json()["detail"])
        sql("UPDATE calls SET answered_at = ?", (config.iso(config.utcnow() - timedelta(minutes=4, seconds=5)),))
        self.ok(self.post(f"/api/calls/{c['id']}/end", user=NISHA))
        self.assertEqual(self.call_events(TARUN["id"])[-1]["state"], "ended")
        self.assertEqual(self.call_events(TARUN["id"])[-1]["outcome"], "answered")
        self.assertIsNone(self.ok(self.get("/api/calls/current", TARUN))["call"])
        self.assertEqual(self.post(f"/api/calls/{c['id']}/end", user=NISHA).status_code, 404)
        # the note: from the caller, not unread for anyone, no notification
        [n] = self.notes()
        self.assertEqual((n["userId"], n["call"]["outcome"], n["call"]["callerId"]), (NISHA["id"], "answered", NISHA["id"]))
        self.assertGreaterEqual(n["call"]["seconds"], 245)
        convs = {x["id"]: x for x in self.ok(self.get("/api/conversations", TARUN))["conversations"]}
        self.assertEqual(convs[self.direct]["unread"], 0)
        self.assertEqual(convs[self.direct]["lastMessage"]["preview"], "📞 Call · 4 min")
        self.assertFalse(any("Missed" in s["message"] for s in self.sent.items))
        row = sql("SELECT * FROM calls")[0]
        self.assertEqual((row["outcome"], row["message_id"]), ("answered", n["id"]))
        self.assertIsNotNone(row["ended_at"])
        # a call note can't be edited or forwarded; deleting it removes its row
        self.assertEqual(self.patch(f"/api/messages/{n['id']}", {"body": "x"}, NISHA).status_code, 403)
        self.ok(self.delete(f"/api/messages/{n['id']}", NISHA))
        self.assertEqual(sql("SELECT * FROM calls"), [])

    # ---------- calls that aren't answered ----------
    def test_missed_after_ring_time(self):
        c = self.ring()
        calls._calls[c["id"]].ring_until = time.monotonic() - 1
        self.assertEqual(calls.tick(), 1)
        self.assertEqual(self.call_events(NISHA["id"])[-1]["outcome"], "missed")
        [n] = self.notes()
        self.assertEqual(n["call"]["outcome"], "missed")
        convs = {x["id"]: x for x in self.ok(self.get("/api/conversations", TARUN))["conversations"]}
        self.assertEqual((convs[self.direct]["unread"], convs[self.direct]["lastMessage"]["preview"]), (1, "📞 Missed call"))
        mine = {x["id"]: x for x in self.ok(self.get("/api/conversations", NISHA))["conversations"]}
        self.assertEqual(mine[self.direct]["unread"], 0)
        # the ringing notification is cleared, then the missed call is notified like a message
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

    def test_busy_and_offer_never_sent(self):
        d2 = self.ok(self.post("/api/conversations/direct", {"userId": TARUN["id"]}, LEELA))["id"]
        self.ring()
        r = self.post("/api/calls", {"conversationId": d2}, LEELA)
        self.assertEqual(r.status_code, 409)
        self.assertIn("Tarun is on another call", r.json()["detail"])
        notes = [m for m in self.ok(self.get(f"/api/conversations/{d2}/messages", LEELA))["messages"] if m["kind"] == "call"]
        self.assertEqual(notes[0]["call"]["outcome"], "busy")
        # an offer that never comes: dropped without a note
        calls.reset()
        c = self.ok(self.post("/api/calls", {"conversationId": self.direct}, NISHA), 201)
        calls._calls[c["id"]].made -= calls.OFFER_SECONDS + 1
        calls.tick()
        self.assertNotIn(c["id"], calls._calls)
        self.assertEqual(self.notes(), [])

    def test_quiet_hours_and_mute_dont_ring_the_phone(self):
        self.ok(self.put(f"/api/conversations/{self.direct}/me",
                         {"mutedUntil": config.iso(config.utcnow() + timedelta(hours=2))}, TARUN))
        n = len(self.sent.items)
        c = self.ring()
        self.assertEqual(len(self.sent.items), n)
        self.assertEqual(self.call_events(TARUN["id"])[-1]["state"], "ringing")     # open tabs still ring
        self.ok(self.post(f"/api/calls/{c['id']}/end", user=NISHA))
        self.assertEqual(len(self.sent.items), n)                               # no clear, and no missed push (muted)
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
        self.ok(self.post(f"/api/calls/{c['id']}/answer", {"sdp": SDP}, TARUN))
        self.ok(self.patch(f"/api/admin/people/{TARUN['id']}", {"disabled": True}))
        self.assertNotIn(c["id"], calls._calls)
        self.assertEqual(self.notes()[-1]["call"]["outcome"], "failed")
        self.ok(self.patch(f"/api/admin/people/{TARUN['id']}", {"disabled": False}))
        # an answered call ends when one side has been gone a minute
        c = self.ring()
        self.ok(self.post(f"/api/calls/{c['id']}/answer", {"sdp": SDP}, TARUN))
        calls.tick()
        self.assertIn(c["id"], calls._calls)
        for u in calls._calls[c["id"]].gone_since:
            calls._calls[c["id"]].gone_since[u] -= calls.GONE_SECONDS + 1
        calls.tick()
        self.assertNotIn(c["id"], calls._calls)
        self.assertEqual(self.notes()[-1]["call"]["outcome"], "answered")
        # a restart: a call that was ringing is closed as couldn't connect
        self.ring()
        calls.reset()
        self.assertEqual(calls.close_unfinished(), 1)
        self.assertEqual(self.notes()[-1]["call"]["outcome"], "failed")
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM calls WHERE ended_at IS NULL")[0]["n"], 0)

    def test_sizes_and_disappearing_chats(self):
        c = self.ok(self.post("/api/calls", {"conversationId": self.direct}, NISHA), 201)
        self.assertEqual(self.post(f"/api/calls/{c['id']}/offer", {"sdp": "x" * (calls.MAX_SDP + 1)}, NISHA).status_code, 422)
        self.assertEqual(self.post(f"/api/calls/{c['id']}/offer", {"sdp": " "}, NISHA).status_code, 422)
        self.assertEqual(self.post(f"/api/calls/{c['id']}/offer", {"sdp": SDP}, TARUN).status_code, 409)
        self.ok(self.post(f"/api/calls/{c['id']}/end", user=NISHA))
        self.assertEqual(self.notes(), [])                  # never rang: no note
        self.ok(self.patch(f"/api/conversations/{self.direct}", {"disappearSeconds": 3600}, NISHA))
        c = self.ring()
        self.ok(self.post(f"/api/calls/{c['id']}/end", user=NISHA))
        self.assertIsNotNone(self.notes()[-1]["expiresAt"])

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
