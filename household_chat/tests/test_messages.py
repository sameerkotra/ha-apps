from datetime import timedelta

from base import ADMIN, LEELA, NISHA, TARUN, ApiTestCase, sql

from app import chats, config, db, housekeeping


class MessageTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.enable(ADMIN, NISHA, TARUN)
        self.hh = self.household()

    def test_post_read_unread_and_paging(self):
        for i in range(60):
            if i % 25 == 0:
                chats.message_limit.reset()
            self.send(self.hh, f"m{i}", NISHA)
        conv = [c for c in self.ok(self.get("/api/conversations", TARUN))["conversations"] if c["id"] == self.hh][0]
        self.assertEqual(conv["unread"], 60)
        self.assertEqual(conv["lastMessage"]["preview"], "m59")
        page = self.ok(self.get(f"/api/conversations/{self.hh}/messages", TARUN))
        self.assertEqual(len(page["messages"]), 50)
        self.assertTrue(page["moreBefore"])
        older = self.ok(self.get(f"/api/conversations/{self.hh}/messages?before={page['messages'][0]['id']}", TARUN))
        self.assertFalse(older["moreBefore"])
        last = page["messages"][-1]["id"]
        self.ok(self.post(f"/api/conversations/{self.hh}/read", {"upTo": last}, TARUN))
        conv = [c for c in self.ok(self.get("/api/conversations", TARUN))["conversations"] if c["id"] == self.hh][0]
        self.assertEqual(conv["unread"], 0)
        reads = {r["userId"]: r["lastReadId"] for r in self.ok(self.get(f"/api/conversations/{self.hh}/messages", NISHA))["reads"]}
        self.assertEqual(reads[TARUN["id"]], last)
        # the sender's own messages are read by them
        self.assertEqual(reads[NISHA["id"]], last)

    def test_limits(self):
        self.assertEqual(self.post(f"/api/conversations/{self.hh}/messages", {"body": "x" * 8001}, NISHA).status_code, 422)
        self.assertEqual(self.post(f"/api/conversations/{self.hh}/messages", {"body": "  "}, NISHA).status_code, 422)
        for i in range(30):
            self.send(self.hh, str(i), TARUN)
        self.assertEqual(self.post(f"/api/conversations/{self.hh}/messages", {"body": "31"}, TARUN).status_code, 429)

    def test_edit_window_and_delete(self):
        m = self.send(self.hh, "helo", NISHA)
        self.assertEqual(self.patch(f"/api/messages/{m['id']}", {"body": "hello"}, TARUN).status_code, 403)
        e = self.ok(self.patch(f"/api/messages/{m['id']}", {"body": "hello"}, NISHA))
        self.assertEqual(e["body"], "hello")
        self.assertTrue(e["editedAt"])
        with db.get_conn() as conn:
            conn.execute("UPDATE messages SET created_at = ? WHERE id = ?",
                         (config.iso(config.utcnow() - timedelta(minutes=16)), m["id"]))
        self.assertEqual(self.patch(f"/api/messages/{m['id']}", {"body": "late"}, NISHA).status_code, 409)
        # the Household's owner (ADMIN, as the first enabled) may delete anyone's; a member may not
        self.assertEqual(self.delete(f"/api/messages/{m['id']}", TARUN).status_code, 403)
        d = self.ok(self.delete(f"/api/messages/{m['id']}", ADMIN))
        self.assertTrue(d["deleted"])
        self.assertEqual(d["body"], "")
        self.assertEqual(self.ok(self.get("/api/search?q=hello", NISHA))["messages"], [])

    def test_replies_mentions_reactions(self):
        a = self.send(self.hh, "Dinner at 8?", NISHA)
        b = self.send(self.hh, "*Yes* @Nisha", TARUN, replyTo=a["id"], mentions=[NISHA["id"], "u-nobody"])
        self.assertEqual(b["replyTo"]["text"], "Dinner at 8?")
        self.assertEqual(b["mentions"], [NISHA["id"]])
        conv = [c for c in self.ok(self.get("/api/conversations", NISHA))["conversations"] if c["id"] == self.hh][0]
        self.assertEqual(conv["mentionUnread"], 1)
        r = self.ok(self.put(f"/api/messages/{a['id']}/reactions/👍", user=TARUN))
        self.assertEqual(r["reactions"], [{"emoji": "👍", "userIds": [TARUN["id"]]}])
        self.assertEqual(self.put(f"/api/messages/{a['id']}/reactions/abc", user=TARUN).status_code, 422)
        r = self.ok(self.delete(f"/api/messages/{a['id']}/reactions/👍", TARUN))
        self.assertEqual(r["reactions"], [])
        # a reply to another chat's message is refused
        other = self.ok(self.post("/api/conversations", {"name": "G"}, NISHA), 201)["id"]
        self.assertEqual(self.post(f"/api/conversations/{other}/messages", {"body": "x", "replyTo": a["id"]}, NISHA).status_code, 422)

    def test_pins(self):
        ms = [self.send(self.hh, f"p{i}", NISHA) for i in range(11)]
        for m in ms[:10]:
            self.ok(self.put(f"/api/messages/{m['id']}/pin", user=TARUN))
        self.assertEqual(self.put(f"/api/messages/{ms[10]['id']}/pin", user=TARUN).status_code, 409)
        pins = self.ok(self.get(f"/api/conversations/{self.hh}/pins", NISHA))["messages"]
        self.assertEqual(len(pins), 10)
        self.ok(self.delete(f"/api/messages/{ms[0]['id']}/pin", NISHA))
        self.ok(self.delete(f"/api/messages/{ms[1]['id']}", NISHA))      # deleting removes its pin
        self.assertEqual(len(self.ok(self.get(f"/api/conversations/{self.hh}/pins", NISHA))["messages"]), 8)
        texts = [m["body"] for m in self.ok(self.get(f"/api/conversations/{self.hh}/messages", NISHA))["messages"] if m["kind"] == "system"]
        self.assertIn("Tarun pinned a message", texts)

    def test_polls(self):
        p = self.ok(self.post(f"/api/conversations/{self.hh}/polls",
                              {"question": "Biryani or pizza?", "options": ["Biryani", "Pizza"]}, NISHA), 201)
        opts = [o["id"] for o in p["poll"]["options"]]
        self.assertEqual(self.put(f"/api/polls/{p['id']}/vote", {"optionIds": opts}, TARUN).status_code, 422)   # one answer
        self.ok(self.put(f"/api/polls/{p['id']}/vote", {"optionIds": [opts[0]]}, TARUN))
        v = self.ok(self.put(f"/api/polls/{p['id']}/vote", {"optionIds": [opts[1]]}, TARUN))     # change
        self.assertEqual([o["votes"] for o in v["poll"]["options"]], [[], [TARUN["id"]]])
        self.assertEqual(self.post(f"/api/polls/{p['id']}/close", user=TARUN).status_code, 403)
        self.notify_to(NISHA)
        self.ok(self.put(f"/api/polls/{p['id']}/vote", {"optionIds": [opts[0]]}, NISHA))
        self.ok(self.put(f"/api/polls/{p['id']}/vote", {"optionIds": [opts[0]]}, ADMIN))
        self.assertTrue(any("Everyone has voted" in s["message"] for s in self.sent.items), self.sent.items)
        self.ok(self.post(f"/api/polls/{p['id']}/close", user=NISHA))
        self.assertEqual(self.put(f"/api/polls/{p['id']}/vote", {"optionIds": [opts[0]]}, TARUN).status_code, 409)
        # only members vote
        self.enable(LEELA)
        g = self.ok(self.post("/api/conversations", {"name": "G"}, NISHA), 201)["id"]
        p2 = self.ok(self.post(f"/api/conversations/{g}/polls", {"question": "Q", "options": ["a", "b"], "multi": True}, NISHA), 201)
        self.assertEqual(self.put(f"/api/polls/{p2['id']}/vote", {"optionIds": [p2["poll"]["options"][0]["id"]]}, LEELA).status_code, 404)
        # closing on a date
        p3 = self.ok(self.post(f"/api/conversations/{g}/polls", {"question": "Q3", "options": ["a", "b"],
                                                                  "closesAt": config.iso(config.utcnow() + timedelta(hours=1))}, NISHA), 201)
        with db.get_conn() as conn:
            conn.execute("UPDATE polls SET closes_at = ? WHERE message_id = ?", (config.iso(config.utcnow() - timedelta(seconds=1)), p3["id"]))
        self.assertEqual(housekeeping.close_due_polls(), 1)
        self.assertTrue(self.ok(self.get(f"/api/conversations/{g}/messages", NISHA))["messages"][-1]["poll"]["closed"])

    def test_forward(self):
        g = self.ok(self.post("/api/conversations", {"name": "Bills", "memberIds": [TARUN["id"]]}, NISHA), 201)["id"]
        up = self.ok(self.upload(g, "Electricity bill.pdf", b"%PDF-1.4 bill", NISHA), 201)
        m = self.send(g, "September bill", NISHA, attachmentIds=[up["id"]])
        room = self.personal(NISHA)
        self.ok(self.post("/api/messages/forward", {"messageIds": [m["id"]], "conversationIds": [room]}, NISHA))
        got = self.ok(self.get(f"/api/conversations/{room}/messages", NISHA))["messages"][-1]
        self.assertTrue(got["forwarded"])
        self.assertEqual(got["body"], "September bill")
        self.assertEqual(got["attachments"][0]["name"], "Electricity bill.pdf")
        self.assertNotEqual(got["attachments"][0]["id"], up["id"])
        # a copy: deleting the original doesn't break it
        self.ok(self.delete(f"/api/messages/{m['id']}", NISHA))
        self.assertEqual(self.get(f"/api/files/{got['attachments'][0]['id']}", NISHA).status_code, 200)
        # only into chats you're in
        self.assertEqual(self.post("/api/messages/forward", {"messageIds": [got["id"]], "conversationIds": [self.personal(TARUN)]},
                                   NISHA).status_code, 404)
        # polls become text
        p = self.ok(self.post(f"/api/conversations/{self.hh}/polls", {"question": "When?", "options": ["Sat", "Sun"]}, NISHA), 201)
        self.ok(self.post("/api/messages/forward", {"messageIds": [p["id"]], "conversationIds": [room]}, NISHA))
        got = self.ok(self.get(f"/api/conversations/{room}/messages", NISHA))["messages"][-1]
        self.assertEqual(got["kind"], "text")
        self.assertEqual(got["body"], "📊 When?\n- Sat\n- Sun")

    def test_stars_and_reminders(self):
        m = self.send(self.hh, "Electricity bill due Friday", NISHA)
        s = self.ok(self.put(f"/api/messages/{m['id']}/star", user=TARUN))
        self.assertTrue(s["starred"])
        self.assertFalse(self.ok(self.get(f"/api/conversations/{self.hh}/messages", NISHA))["messages"][-1]["starred"])
        self.assertEqual(len(self.ok(self.get("/api/me/starred", TARUN))["messages"]), 1)
        self.assertEqual(self.ok(self.get("/api/me/starred", NISHA))["messages"], [])
        r = self.ok(self.post(f"/api/messages/{m['id']}/remind", {"preset": "1h"}, TARUN))
        self.assertTrue(r["reminder"])
        self.assertEqual(self.post(f"/api/messages/{m['id']}/remind", {"at": "2001-01-01T10:00"}, TARUN).status_code, 422)
        self.notify_to(TARUN)
        with db.get_conn() as conn:
            conn.execute("UPDATE reminders SET due_at = ?", (config.iso(config.utcnow() - timedelta(seconds=1)),))
        self.assertEqual(housekeeping.due_reminders(), 1)
        self.assertEqual(len(self.sent.items), 1)
        self.assertIn("⏰ Reminder: Nisha — Electricity bill due Friday", self.sent.items[0]["message"])
        self.assertEqual(housekeeping.due_reminders(), 0)       # once
        rem = self.ok(self.get("/api/me/reminders", TARUN))["reminders"]
        self.assertTrue(rem[0]["doneAt"])
        # leaving the chat drops stars and reminders
        self.ok(self.post(f"/api/messages/{m['id']}/remind", {"preset": "tomorrow"}, TARUN))
        self.ok(self.post(f"/api/conversations/{self.hh}/leave", user=TARUN))
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM reminders WHERE user_id = ?", (TARUN["id"],))[0]["n"], 0)
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM stars WHERE user_id = ?", (TARUN["id"],))[0]["n"], 0)

    def test_search(self):
        self.send(self.hh, "The *plumber* comes Tuesday", NISHA)
        g = self.ok(self.post("/api/conversations", {"name": "Secret", "memberIds": []}, NISHA), 201)["id"]
        self.send(g, "plumber invoice", NISHA)
        hits = self.ok(self.get("/api/search?q=plumb", TARUN))["messages"]
        self.assertEqual(len(hits), 1)
        self.assertIn("\u0002plumber\u0003", hits[0]["snippet"])
        self.assertEqual(len(self.ok(self.get("/api/search?q=plumber", NISHA))["messages"]), 2)
        self.assertEqual(len(self.ok(self.get(f"/api/search?q=plumber&conversation={g}", NISHA))["messages"]), 1)
        self.assertEqual(self.get(f"/api/search?q=plumber&conversation={g}", TARUN).status_code, 404)
        self.ok(self.get('/api/search?q="*()', NISHA))

    def test_description_and_chat_settings(self):
        self.ok(self.patch(f"/api/conversations/{self.hh}", {"description": "School runs: Mon–Wed Meera"}, ADMIN))
        c = self.ok(self.get(f"/api/conversations/{self.hh}", TARUN))
        self.assertEqual(c["description"], "School runs: Mon–Wed Meera")
        self.assertEqual(self.patch(f"/api/conversations/{self.hh}", {"description": "x"}, TARUN).status_code, 403)
        d = self.ok(self.post("/api/conversations/direct", {"userId": TARUN["id"]}, NISHA))["id"]
        self.ok(self.patch(f"/api/conversations/{d}", {"description": "Us"}, TARUN))
        self.assertEqual(self.patch(f"/api/conversations/{d}", {"name": "x"}, TARUN).status_code, 409)
        c = self.ok(self.put(f"/api/conversations/{self.hh}/me", {"pinned": True, "notify": "mentions"}, TARUN))
        self.assertTrue(c["pinned"])
        self.assertEqual(c["notify"], "mentions")
        convs = self.ok(self.get("/api/conversations", TARUN))["conversations"]
        self.assertEqual(convs[0]["kind"], "personal")
        self.assertEqual(convs[1]["id"], self.hh)

    def test_typing_not_in_personal(self):
        self.assertEqual(self.post(f"/api/conversations/{self.personal(NISHA)}/typing", user=NISHA).status_code, 409)
        self.ok(self.post(f"/api/conversations/{self.hh}/typing", user=NISHA))
