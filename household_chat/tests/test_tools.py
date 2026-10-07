"""What Chat answers the Household Assistant (app/tools.py; HOUSEHOLD_ASSISTANT_SPEC §4.2, §7.2).

- chat.unread: the person's own unread counts, names and counts only (never message text);
- the switches: the admin's *Answer the Household Assistant* and the person's own;
- the catalogue fits on the bus and Chat says it can answer in its hello.

All people and chats here are invented."""
import json
from datetime import datetime, timedelta, timezone

from base import ADMIN, LEELA, NISHA, TARUN, ApiTestCase, sql

from app import config, db, settings, tools
from app.common import app_bus


def call(data):
    now = datetime.now(timezone.utc)
    env = {"id": app_bus.new_ulid(now), "v": 1, "from": "household_assistant", "to": "household_chat",
           "kind": "assist.tool.call", "kv": 1, "reply_to": None, "ref": None, "sent": now.isoformat(),
           "expires": (now + timedelta(seconds=20)).isoformat(), "data": data}
    with db.get_conn() as conn:
        try:
            return tools.tools.on_call(app_bus.Message(env), conn)
        except app_bus.Nack as n:
            return n


class UnreadTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.enable(ADMIN, NISHA, TARUN, LEELA)
        self.trip = self.ok(self.post("/api/conversations", {"name": "Trip", "memberIds": [TARUN["id"]]}, NISHA), 201)["id"]
        self.dm = self.ok(self.post("/api/conversations/direct", {"userId": TARUN["id"]}, NISHA))["id"]
        self.send(self.trip, "The secret plan for Saturday", NISHA)
        self.send(self.trip, "and the tickets", NISHA)
        self.send(self.dm, "private words", NISHA)
        self.old_url = config.INGRESS_URL
        config.INGRESS_URL = "/a1b2c3d4_household_chat"

    def tearDown(self):
        config.INGRESS_URL = self.old_url
        super().tearDown()

    def ask(self, user=TARUN):
        return call({"tool": "chat.unread", "args": {}, "requested_by": user["id"], "question": "q1"})

    def test_counts_names_and_links_never_text(self):
        res = self.ask()
        self.assertIn("3 unread messages in 2 chats", res["text"])
        self.assertIn("Trip (2)", res["text"])
        by = {i["chat"]: i for i in res["items"]}
        self.assertEqual(by["Trip"]["unread"], 2)
        self.assertEqual(by["Nisha"]["unread"], 1)
        self.assertEqual({tuple(sorted(i)) for i in res["items"]}, {("chat", "kind", "mentions", "muted", "unread")})
        self.assertEqual(sorted(l["target"] for l in res["links"]), sorted([f"/chat/{self.trip}", f"/chat/{self.dm}"]))
        self.assertTrue(all(l["panel"] == "/a1b2c3d4_household_chat" for l in res["links"]))
        blob = json.dumps(res)
        for words in ("secret plan", "tickets", "private words"):
            self.assertNotIn(words, blob)

    def test_only_the_asking_persons_own_chats(self):
        res = self.ask(LEELA)
        self.assertEqual(res["items"], [])
        self.assertEqual(res["text"], "No unread messages in Household Chat.")
        self.assertEqual(res["links"], [{"label": "Open Household Chat", "panel": "/a1b2c3d4_household_chat"}])
        self.assertEqual(self.ask(NISHA)["items"], [])               # the sender has nothing unread

    def test_no_link_page_outside_the_sidebar(self):
        config.INGRESS_URL = "/app/a1b2c3d4_household_chat"
        self.assertTrue(all("panel" not in l for l in self.ask()["links"]))

    def test_switches(self):
        settings.update({"assistant_answers": False})
        res = self.ask()
        self.assertEqual((res.reason, res.detail), ("not_allowed", "off"))
        settings.update({"assistant_answers": True})
        me = self.ok(self.get("/api/me", TARUN))
        self.assertTrue(me["settings"]["assistantOk"])
        self.assertTrue(me["app"]["assistant"])
        self.ok(self.put("/api/me/settings", {"assistantOk": False}, TARUN))
        res = self.ask()
        self.assertEqual((res.reason, res.detail), ("not_allowed", "person_off"))
        self.assertIn("text", self.ask(NISHA))                         # only Tarun turned it off
        self.ok(self.put("/api/me/settings", {"assistantOk": True}, TARUN))
        self.assertIn("text", self.ask())

    def test_disabled_and_unknown_people(self):
        sql("UPDATE users SET disabled = 1 WHERE id = ?", (TARUN["id"],))
        res = self.ask()
        self.assertEqual((res.reason, res.detail), ("not_allowed", "no_access"))
        res = call({"tool": "chat.unread", "requested_by": "u_nobody"})
        self.assertEqual((res.reason, res.detail), ("not_allowed", "no_access"))
        res = call({"tool": "chat.search", "requested_by": TARUN["id"]})
        self.assertEqual((res.reason, res.detail), ("not_found", "tool"))


class CatalogueTests(ApiTestCase):
    def test_fits_and_is_offered(self):
        tools.tools.check()
        self.assertEqual([t["name"] for t in tools.tools.spec()], ["chat.unread"])
        self.assertIn("assist.tool.call", app_bus.default._handlers)
        self.assertIn("assist.tools.list", app_bus.default._handlers)
