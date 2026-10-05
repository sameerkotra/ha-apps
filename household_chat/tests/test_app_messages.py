"""Messages from the other household apps (APP_MESSAGES_SPEC §6.3; SPEC §15.11).

- the two handlers (chat.chats.list, chat.card): every check, allowed and refused, nothing written on a nack;
- cards in the app: what the page gets, the link, notifications, search, previews, replies, edit/forward/delete,
  chat downloads, disappearing chats, Admin → Connected apps;
- the messages-table migration (a 2.1.0 database → 2.2.0);
- end to end: the real app (its lifespan, one shared WebSocket) on the fake Home Assistant event bus, with a fake
  Household Docs in this process sending to it.
"""
import io
import json
import logging
import os
import shutil
import sqlite3
import tempfile
import time
import zipfile
from datetime import datetime, timedelta, timezone

from base import ADMIN, LEELA, NISHA, TARUN, ApiTestCase, sql

from app import app_messages, chats, config, db, ha_events, notifier, settings
from app.common import app_bus
from app.main import app
from common_tests.fake_ha_bus import FakeHABus
from common_tests.ingress import ingress_client

CARD = {"title": "Trip 2026", "type": "checklist", "item_id": "n_trip", "owner_name": "Nisha",
        "panel": "/a1b2c3d4_household_docs", "target": "/doc/n_trip", "badge": "Docs"}


def envelope(kind, data, frm="household_docs", **over):
    now = datetime.now(timezone.utc)
    env = {"id": app_bus.new_ulid(now), "v": 1, "from": frm, "to": "household_chat", "kind": kind, "kv": 1,
           "reply_to": None, "ref": None, "sent": now.isoformat(), "expires": (now + timedelta(hours=1)).isoformat(),
           "data": data}
    env.update(over)
    return env


def wait_until(cond, timeout=5.0, what="condition"):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {what}")


class HandlerCase(ApiTestCase):
    HANDLERS = {"chat.card": app_messages.on_card, "chat.chats.list": app_messages.on_chats_list}

    def setUp(self):
        super().setUp()
        self.enable(ADMIN, NISHA, TARUN, LEELA)
        self.hh = self.household()
        self.trip = self.ok(self.post("/api/conversations", {"name": "Trip", "icon": "🧳",
                                                             "memberIds": [TARUN["id"]]}, NISHA), 201)["id"]
        self.dm = self.ok(self.post("/api/conversations/direct", {"userId": TARUN["id"]}, NISHA))["id"]
        self.send(self.dm, "hi", NISHA)

    def call(self, kind, data, frm="household_docs"):
        """Run a handler as the bus does: inside one transaction; a Nack rolls everything back."""
        msg = app_bus.Message(envelope(kind, data, frm))
        with db.get_conn() as conn:
            try:
                result = self.HANDLERS[kind](msg, conn)
            except app_bus.Nack as n:
                conn.rollback()
                return n
        for fn in msg._after:
            fn()
        return result

    def card(self, chat_id, user=NISHA, **over):
        return self.call("chat.card", dict(CARD, requested_by=user["id"], chat_id=chat_id, **over))

    def assertNack(self, res, reason, detail=None):
        self.assertIsInstance(res, app_bus.Nack, res)
        self.assertEqual((res.reason, res.detail), (reason, detail))

    def messages(self, cid, user=NISHA):
        return self.ok(self.get(f"/api/conversations/{cid}/messages", user))["messages"]


class ChatsListTests(HandlerCase):
    def test_lists_the_chats_a_person_may_post_in(self):
        other = self.ok(self.post("/api/conversations", {"name": "Not mine", "memberIds": [LEELA["id"]]}, TARUN), 201)["id"]
        quiet = self.ok(self.post("/api/conversations/direct", {"userId": NISHA["id"]}, LEELA))["id"]   # no message yet
        res = self.call("chat.chats.list", {"requested_by": NISHA["id"]})
        ids = [c["id"] for c in res["chats"]]
        self.assertEqual(ids[0], self.personal(NISHA))                         # Chat's own order: My room first
        self.assertEqual(set(ids), {self.personal(NISHA), self.hh, self.trip, self.dm})
        self.assertNotIn(other, ids)
        self.assertNotIn(quiet, ids)                                          # hidden in Chat until a message
        self.assertFalse(res["more"])
        by = {c["id"]: c for c in res["chats"]}
        self.assertEqual(by[self.personal(NISHA)], {"id": self.personal(NISHA), "kind": "personal", "name": "My room",
                                                    "members": 1})
        self.assertEqual(by[self.trip], {"id": self.trip, "kind": "group", "name": "Trip", "icon": "🧳", "members": 2})
        self.assertEqual(by[self.dm]["name"], "Tarun")                        # as Nisha sees it
        self.assertEqual(by[self.dm]["kind"], "direct")
        self.assertTrue(by[self.hh]["household"])
        self.assertEqual(by[self.hh]["members"], 4)
        self.assertFalse(any("member_ids" in c for c in res["chats"]))       # who is in them: one chat at a time

    def test_members_of_one_chat_only_when_asked(self):
        """Security review: the list (kept in Home Assistant's event history) names nobody; the members' ids come
        only for the one chat Docs asks about, and only one the person may post in."""
        res = self.call("chat.chats.list", {"requested_by": NISHA["id"], "chat_id": self.hh})
        self.assertEqual([c["id"] for c in res["chats"]], [self.hh])
        self.assertEqual(sorted(res["chats"][0]["member_ids"]), sorted([ADMIN["id"], TARUN["id"], LEELA["id"]]))
        self.assertFalse(res["more"])
        lonely = self.ok(self.post("/api/conversations", {"name": "Others", "memberIds": [LEELA["id"]]}, TARUN), 201)["id"]
        self.assertNack(self.call("chat.chats.list", {"requested_by": NISHA["id"], "chat_id": lonely}), "not_found", "chat")
        self.assertNack(self.call("chat.chats.list", {"requested_by": NISHA["id"], "chat_id": "0" * 32}), "not_found", "chat")
        for bad in (5, "bad id!", "a" * 65):
            self.assertNack(self.call("chat.chats.list", {"requested_by": NISHA["id"], "chat_id": bad}), "invalid", "chat_id")
        self.ok(self.patch(f"/api/admin/people/{TARUN['id']}", {"disabled": True}))
        self.assertNack(self.call("chat.chats.list", {"requested_by": NISHA["id"], "chat_id": self.dm}), "not_found", "chat")

    def test_read_only_chats_and_disabled_members_left_out(self):
        self.ok(self.patch(f"/api/admin/people/{TARUN['id']}", {"disabled": True}))
        res = self.call("chat.chats.list", {"requested_by": NISHA["id"]})
        ids = [c["id"] for c in res["chats"]]
        self.assertNotIn(self.dm, ids)                                        # read-only now
        trip = self.call("chat.chats.list", {"requested_by": NISHA["id"], "chat_id": self.trip})["chats"][0]
        self.assertEqual((trip["members"], trip["member_ids"]), (1, []))      # Tarun left the group when turned off

    def test_children_direct_chat_is_read_only(self):
        for u in (NISHA, TARUN):
            self.ok(self.patch(f"/api/admin/people/{u['id']}", {"isChild": True}))
        ids = [c["id"] for c in self.call("chat.chats.list", {"requested_by": NISHA["id"]})["chats"]]
        self.assertNotIn(self.dm, ids)
        self.assertIn(self.trip, ids)

    def test_refusals(self):
        self.assertNack(self.call("chat.chats.list", {"requested_by": "u-stranger"}), "not_allowed", "no_access")
        self.ok(self.patch(f"/api/admin/people/{LEELA['id']}", {"disabled": True}))
        self.assertNack(self.call("chat.chats.list", {"requested_by": LEELA["id"]}), "not_allowed", "no_access")
        for bad in ({}, {"requested_by": 5}, {"requested_by": ""}, {"requested_by": "x" * 65}):
            self.assertNack(self.call("chat.chats.list", bad), "invalid", "requested_by")
        for limit in (0, 51, "5", True):
            self.assertNack(self.call("chat.chats.list", {"requested_by": NISHA["id"], "limit": limit}), "invalid", "limit")
        res = self.call("chat.chats.list", {"requested_by": NISHA["id"], "limit": 1})
        self.assertEqual((len(res["chats"]), res["more"]), (1, True))
        db.RESTORING.set()
        try:
            self.assertNack(self.call("chat.chats.list", {"requested_by": NISHA["id"]}), "busy", "restoring")
        finally:
            db.RESTORING.clear()

    def test_answer_stays_under_the_size_limit(self):
        orig = app_messages.MAX_ANSWER
        size = lambda r: len(json.dumps(r, separators=(",", ":"), ensure_ascii=False).encode())   # noqa: E731
        full = size(self.call("chat.chats.list", {"requested_by": NISHA["id"]}))
        try:
            app_messages.MAX_ANSWER = full - 5
            res = self.call("chat.chats.list", {"requested_by": NISHA["id"]})
            self.assertLessEqual(size(res), full - 5)
            self.assertTrue(res["more"])                                      # the least recent chats left out
            one = self.call("chat.chats.list", {"requested_by": NISHA["id"], "chat_id": self.hh})
            app_messages.MAX_ANSWER = size(one) - 5
            one = self.call("chat.chats.list", {"requested_by": NISHA["id"], "chat_id": self.hh})
            self.assertNotIn("member_ids", one["chats"][0])                   # too many to name: none
            app_messages.MAX_ANSWER = 150
            res = self.call("chat.chats.list", {"requested_by": NISHA["id"]})
            self.assertTrue(res["more"])
            self.assertLess(len(res["chats"]), 4)
        finally:
            app_messages.MAX_ANSWER = orig
        # a real worst case: 50 chats of 20 people fit in one event
        now = config.now_iso()
        with db.get_conn() as conn:
            for i in range(19):
                conn.execute("INSERT INTO users (id, name, disabled, created_at) VALUES (?, ?, 0, ?)",
                             (f"{i:032x}", f"Person {i}", now))
            for g in range(50):
                cid = f"{g:032x}"
                conn.execute("INSERT INTO conversations (id, kind, name, folder, created_by, created_at, last_activity_at) "
                             "VALUES (?, 'group', ?, ?, ?, ?, ?)", (cid, "A long group name " * 3, f"g{g}", NISHA["id"], now, now))
                for uid in [NISHA["id"]] + [f"{i:032x}" for i in range(19)]:
                    conn.execute("INSERT INTO members (conversation_id, user_id, role, joined_at) VALUES (?, ?, 'member', ?)",
                                 (cid, uid, now))
        res = self.call("chat.chats.list", {"requested_by": NISHA["id"]})
        env = envelope("ack", {"result": res}, ref="r" * 200)
        self.assertLessEqual(app_bus.event_size(env), app_bus.MAX_EVENT_BYTES)
        self.assertTrue(res["more"])


class CardTests(HandlerCase):
    def test_card_posted_as_the_person(self):
        res = self.card(self.trip)
        self.assertEqual(set(res), {"message_id", "chat_id"})
        self.assertEqual(res["chat_id"], self.trip)
        [m] = [x for x in self.messages(self.trip, TARUN) if x["kind"] == "card"]
        self.assertEqual(m["id"], res["message_id"])
        self.assertEqual((m["userId"], m["author"], m["body"]), (NISHA["id"], "Nisha", "Trip 2026"))
        self.assertEqual(m["card"], {"app": "household_docs", "badge": "Docs", "type": "checklist", "itemId": "n_trip",
                                     "title": "Trip 2026", "owner": "Nisha", "href": "/a1b2c3d4_household_docs/doc/n_trip",
                                     "sharedWithMembers": False})
        conv = [c for c in self.ok(self.get("/api/conversations", TARUN))["conversations"] if c["id"] == self.trip][0]
        self.assertEqual(conv["lastMessage"]["preview"], "✅ Trip 2026")
        self.assertEqual(conv["unread"], 1)
        self.assertEqual(sql("SELECT action, user_id FROM audit_log WHERE action = 'card_from_app'"),
                         [{"action": "card_from_app", "user_id": NISHA["id"]}])

    def test_share_with_members_answers_their_ids(self):
        res = self.card(self.hh, share_with_members=True)
        self.assertEqual(sorted(res["member_ids"]), sorted([ADMIN["id"], TARUN["id"], LEELA["id"]]))
        self.ok(self.patch(f"/api/admin/people/{LEELA['id']}", {"disabled": True}))
        res = self.card(self.hh, share_with_members=True)
        self.assertEqual(sorted(res["member_ids"]), sorted([ADMIN["id"], TARUN["id"]]))
        m = self.messages(self.hh)[-1]
        self.assertTrue(m["card"]["sharedWithMembers"])

    def test_defaults_and_each_cards_own_page_path(self):
        res = self.card(self.trip, panel=None, target=None, badge=None, owner_name=None)
        m = [x for x in self.messages(self.trip) if x["id"] == res["message_id"]][0]
        self.assertEqual((m["card"]["href"], m["card"]["owner"], m["card"]["badge"]), (None, None, "household_docs"))
        with db.get_conn() as conn:
            conn.execute("INSERT INTO bus_apps (slug, name, version, can, last_seen) VALUES ('household_docs', "
                         "'Household Docs', '1.0.0', '[]', ?)", (config.now_iso(),))
        first = self.card(self.trip, badge=None)
        self.assertEqual(first["chat_id"], self.trip)
        self.card(self.trip, panel="/local_household_docs", target="/doc/other")
        cards = {x["id"]: x["card"] for x in self.messages(self.trip) if x["kind"] == "card"}
        self.assertIsNone(cards[res["message_id"]]["href"])                  # each card keeps its own (none here)
        self.assertEqual(cards[first["message_id"]]["href"], "/a1b2c3d4_household_docs/doc/n_trip")
        self.assertEqual([c["badge"] for c in cards.values()], ["household_docs", "Docs", "Docs"])
        self.assertEqual(sorted(c["href"] or "" for c in cards.values())[-1], "/local_household_docs/doc/other")

    def test_a_forged_card_cant_redirect_other_cards(self):
        """Security review: one card with a page path of its own (any chat, any sender) never changes another
        card's link, and a path that isn't the sending app's own page is refused."""
        res = self.card(self.trip)
        mine = self.ok(self.post("/api/conversations", {"name": "Solo", "memberIds": [TARUN["id"]]}, LEELA), 201)["id"]
        for panel, target in (("/config", "/users"), ("/a1b2c3d4_household_docs", "//evil.example/x"),
                              ("/a1b2c3d4_household_docsx", "/doc/x"), ("/a1b2c3d4_household_todo", "/doc/x"),
                              ("/a1b2c3d4_household_docs", "/doc/x/../../config"), ("https://evil.example", "/doc/x"),
                              ("/a1b2c3d4_household_docs", "/users")):
            r = self.call("chat.card", dict(CARD, requested_by=LEELA["id"], chat_id=mine, panel=panel, target=target))
            self.assertIsInstance(r, app_bus.Nack, (panel, target))
        self.assertEqual(self.call("chat.card", dict(CARD, requested_by=LEELA["id"], chat_id=mine,
                                                     panel="/1234abcd_household_docs", target="/doc/elsewhere"))["chat_id"], mine)
        cards = {x["id"]: x["card"] for x in self.messages(self.trip) if x["kind"] == "card"}
        self.assertEqual(cards[res["message_id"]]["href"], "/a1b2c3d4_household_docs/doc/n_trip")
        # a row written before (or by hand) with a bad path still gets no link
        with db.get_conn() as conn:
            conn.execute("UPDATE app_cards SET panel = '/config', target = '/users' WHERE message_id = ?", (res["message_id"],))
        cards = {x["id"]: x["card"] for x in self.messages(self.trip) if x["kind"] == "card"}
        self.assertIsNone(cards[res["message_id"]]["href"])
        # another app's cards only link to that app's own page
        self.assertIsNone(chats.card_link("/a1b2c3d4_household_docs", "/doc/x", "household_todo"))
        self.assertEqual(chats.card_link("/a1b2c3d4_household_todo", "/doc/x", "household_todo"), "/a1b2c3d4_household_todo/doc/x")

    def test_refusals_write_nothing(self):
        lonely = self.ok(self.post("/api/conversations", {"name": "Others", "memberIds": [LEELA["id"]]}, TARUN), 201)["id"]
        before = sql("SELECT COUNT(*) AS n FROM messages")[0]["n"]
        self.assertNack(self.card(lonely), "not_found", "chat")
        self.assertNack(self.card("0" * 32), "not_found", "chat")
        self.assertNack(self.card(self.trip, user={"id": "u-stranger"}), "not_allowed", "no_access")
        bad = {"chat_id": [5, "bad id!", "a" * 65, None], "title": ["", "   ", "x" * 201, 7], "type": ["doc", None],
               "item_id": ["", "../x", None, "a" * 65], "owner_name": ["x" * 81, 3],
               "panel": ["household_docs", "/Household", "/a/b", "/" + "a" * 81, "//evil.example", "/config",
                         "/a1b2c3d4_household_todo", "/a1b2c3d4e5f6a7b8c_household_docs"],
               "target": ["doc", "/doc/../admin", "/doc/./x", "/doc/<x>", "/" + "a" * 200, "/doc//x", 5, "/users",
                          "/doc/x/y", "//evil.example"],
               "share_with_members": ["yes", 1], "badge": ["x" * 21, 1]}
        for key, values in bad.items():
            for v in values:
                with self.subTest(key=key, value=v):
                    data = dict(CARD, requested_by=NISHA["id"], chat_id=self.trip)
                    data[key] = v
                    self.assertNack(self.call("chat.card", data), "invalid", key)
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM messages")[0]["n"], before)
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM app_cards")[0]["n"], 0)

    def test_read_only_direct_chats(self):
        self.ok(self.patch(f"/api/admin/people/{TARUN['id']}", {"disabled": True}))
        self.assertNack(self.card(self.dm), "not_allowed", "read_only")
        self.ok(self.patch(f"/api/admin/people/{TARUN['id']}", {"disabled": False}))
        for u in (NISHA, TARUN):
            self.ok(self.patch(f"/api/admin/people/{u['id']}", {"isChild": True}))
        self.assertNack(self.card(self.dm), "not_allowed", "read_only")
        self.ok(self.put("/api/admin/settings", {"children_can_message_each_other": True}))
        self.assertIn("message_id", self.card(self.dm))
        self.ok(self.patch(f"/api/admin/people/{NISHA['id']}", {"disabled": True}))
        self.assertNack(self.card(self.trip), "not_allowed", "no_access")

    def test_rate_limit_and_restore_are_busy(self):
        chats.message_limit.reset()
        for _ in range(30):
            self.assertIn("message_id", self.card(self.trip))
        self.assertNack(self.card(self.trip), "busy", "rate")
        chats.message_limit.reset()
        db.RESTORING.set()
        try:
            self.assertNack(self.card(self.trip), "busy", "restoring")
        finally:
            db.RESTORING.clear()

    def test_personal_room_and_disappearing_chats(self):
        res = self.card(self.personal(NISHA))
        self.assertEqual(self.messages(self.personal(NISHA))[-1]["id"], res["message_id"])
        self.ok(self.patch(f"/api/conversations/{self.trip}", {"disappearSeconds": 86400}, NISHA))
        res = self.card(self.trip)
        m = [x for x in self.messages(self.trip) if x["id"] == res["message_id"]][0]
        self.assertIsNotNone(m["expiresAt"])

    def test_notifications(self):
        self.notify_to(TARUN, "mobile_app_tarun")
        self.card(self.trip)                                           # a group, Tarun's level: direct and mentions
        self.assertEqual(self.sent.items, [])
        self.card(self.dm, title="Fuse box", type="note")
        self.assertEqual(len(self.sent.items), 1)
        self.assertEqual((self.sent.items[0]["title"], self.sent.items[0]["message"]),
                         ("Nisha", "Nisha shared a note “Fuse box”"))
        notifier.reset()
        self.ok(self.put(f"/api/conversations/{self.trip}/me", {"notify": "all"}, TARUN))
        self.card(self.trip, type="sheet", title="Budget")
        self.assertEqual(self.sent.items[-1]["message"], "Nisha shared a sheet “Budget”")
        notifier.reset()
        self.ok(self.put("/api/me/settings", {"notifyPreview": "sender"}, TARUN))
        self.card(self.dm)
        self.assertEqual(self.sent.items[-1]["message"], "New message from Nisha")
        self.notify_to(NISHA, "mobile_app_nisha")
        self.assertEqual([s for s in self.sent.items if "mobile_app_nisha" in s["services"]], [])   # never to the sharer
        notifier.reset()
        n = len(self.sent.items)
        self.card(self.personal(NISHA))
        self.assertEqual(len(self.sent.items), n)

    def test_search_reply_pin_star_react(self):
        mid = self.card(self.trip)["message_id"]
        res = self.ok(self.get("/api/search?q=trip", TARUN))
        hit = [x for x in res["messages"] if x["id"] == mid][0]
        self.assertEqual(hit["kind"], "card")
        reply = self.send(self.trip, "Looks good", TARUN, replyTo=mid)
        self.assertEqual((reply["replyTo"]["kind"], reply["replyTo"]["text"]), ("card", "Trip 2026"))
        self.ok(self.put(f"/api/messages/{mid}/pin", user=TARUN))
        self.ok(self.put(f"/api/messages/{mid}/star", user=TARUN))
        self.ok(self.put(f"/api/messages/{mid}/reactions/👍", user=TARUN))
        m = [x for x in self.messages(self.trip, TARUN) if x["id"] == mid][0]
        self.assertTrue(m["pinned"] and m["starred"] and m["reactions"])
        starred = self.ok(self.get("/api/me/starred", TARUN))["messages"]
        self.assertEqual(starred[0]["card"]["title"], "Trip 2026")

    def test_not_editable_not_forwardable_deletable(self):
        mid = self.card(self.trip)["message_id"]
        self.assertEqual(self.patch(f"/api/messages/{mid}", {"body": "x"}, NISHA).status_code, 403)
        r = self.post("/api/messages/forward", {"messageIds": [mid], "conversationIds": [self.hh]}, NISHA)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.delete(f"/api/messages/{mid}", TARUN).status_code, 403)   # not his, not a manager
        self.ok(self.delete(f"/api/messages/{mid}", NISHA))
        m = [x for x in self.messages(self.trip) if x["id"] == mid][0]
        self.assertTrue(m["deleted"])
        self.assertIsNone(m["card"])
        self.assertEqual(m["body"], "")
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM app_cards")[0]["n"], 0)
        self.assertEqual(self.ok(self.get("/api/search?q=trip", TARUN))["messages"], [])

    def test_chat_download_has_the_card_without_a_link(self):
        self.card(self.trip, title="Trip <2026>")
        r = self.get(f"/api/conversations/{self.trip}/export", NISHA)
        self.assertEqual(r.status_code, 200, r.text)
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            page = z.read([n for n in z.namelist() if n.endswith(".html")][0]).decode()
        self.assertIn("✅ <strong>Trip &lt;2026&gt;</strong>", page)
        self.assertIn("shared from Docs (Nisha&#x27;s)", page)
        self.assertNotIn("household_docs", page)

    def test_connected_apps(self):
        self.assertEqual(self.get("/api/admin/connected-apps", NISHA).status_code, 403)
        out = self.ok(self.get("/api/admin/connected-apps"))
        self.assertFalse(out["on"])                                    # no Home Assistant in the tests
        self.assertEqual(out["apps"], [])


class MigrationTests(ApiTestCase):
    """A database from 2.1.0 (messages.kind without 'card') becomes 2.2.0's, keeping everything."""

    def old_database(self):
        for ext in ("", "-wal", "-shm"):
            if os.path.exists(config.DB_PATH + ext):
                os.remove(config.DB_PATH + ext)
        old = db.SCHEMA.replace(db.NEW_KINDS, db.OLD_KINDS)
        start = old.index("-- a card another household app posted")
        old = old[:start] + old[old.index("CREATE TABLE IF NOT EXISTS stars ("):]
        conn = db._connect()
        conn.executescript(old)
        db._migrate(conn)                                   # the columns 2.1.0 added with ALTER TABLE
        now = config.now_iso()
        for u in (NISHA, TARUN):
            conn.execute("INSERT INTO users (id, name, disabled, created_at) VALUES (?, ?, 0, ?)", (u["id"], u["display"], now))
        conn.execute("INSERT INTO conversations (id, kind, name, folder, created_by, created_at) VALUES "
                     "('c1', 'group', 'G', 'G', ?, ?)", (NISHA["id"], now))
        for u in (NISHA, TARUN):
            conn.execute("INSERT INTO members (conversation_id, user_id, role, joined_at) VALUES ('c1', ?, 'member', ?)",
                         (u["id"], now))
        ins = ("INSERT INTO messages (conversation_id, user_id, kind, body, reply_to, created_at, expires_at, announcement, "
               "pinned_at) VALUES ('c1', ?, ?, ?, ?, ?, ?, ?, ?)")
        conn.execute(ins, (NISHA["id"], "text", "pineapple pizza", None, now, None, 0, now))       # 1
        conn.execute(ins, (None, "system", '{"event": "created"}', None, now, None, 0, None))       # 2
        conn.execute(ins, (TARUN["id"], "poll", "Where?", 1, now, None, 1, None))                  # 3
        conn.execute(ins, (TARUN["id"], "file", "", None, now, "2099-01-01T00:00:00.000Z", 0, None))  # 4
        conn.execute(ins, (NISHA["id"], "text", "gone soon", None, now, None, 0, None))            # 5
        conn.execute("DELETE FROM messages WHERE id = 5")                                          # the counter stays at 5
        conn.execute("INSERT INTO polls (message_id, question) VALUES (3, 'Where?')")
        conn.execute("INSERT INTO poll_options (message_id, position, text) VALUES (3, 0, 'Here')")
        conn.execute("INSERT INTO reactions (message_id, user_id, emoji, created_at) VALUES (1, ?, '👍', ?)", (TARUN["id"], now))
        conn.execute("INSERT INTO stars (user_id, message_id, created_at) VALUES (?, 1, ?)", (TARUN["id"], now))
        conn.execute("INSERT INTO attachments (id, message_id, conversation_id, rel_path, original_name, mime, size, sha256, "
                     "created_at) VALUES ('a1', 4, 'c1', 'G/x.txt', 'x.txt', 'text/plain', 1, 'h', ?)", (now,))
        conn.execute("UPDATE conversations SET last_message_id = 4")
        conn.commit()
        rows = [dict(r) for r in conn.execute("SELECT * FROM messages ORDER BY id")]
        cols = [r[1] for r in conn.execute("PRAGMA table_info(messages)")]
        conn.close()
        return rows, cols

    def test_rebuild_keeps_everything(self):
        rows, cols = self.old_database()
        db.init_db()
        conn = db._connect()
        try:
            sql_text = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'messages'").fetchone()[0]
            self.assertIn(db.NEW_KINDS, sql_text)
            self.assertEqual([r[1] for r in conn.execute("PRAGMA table_info(messages)")], cols)   # same columns, same order
            self.assertEqual([dict(r) for r in conn.execute("SELECT * FROM messages ORDER BY id")], rows)
            self.assertEqual(conn.execute("SELECT seq FROM sqlite_sequence WHERE name = 'messages'").fetchone()[0], 5)
            names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE tbl_name = 'messages'")}
            self.assertTrue({"idx_msg_conv", "idx_msg_pinned", "idx_msg_expires", "messages_ai", "messages_ad",
                             "messages_au"} <= names)
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual({r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE name LIKE 'bus_%' AND type = 'table'")},
                             {"bus_outbox", "bus_seen", "bus_apps"})
            # the search index still finds old messages, and new ones are indexed by the recreated triggers
            self.assertEqual(conn.execute("SELECT rowid FROM messages_fts WHERE messages_fts MATCH 'pineapple'").fetchall()[0][0], 1)
            conn.execute("INSERT INTO messages (conversation_id, user_id, kind, body, created_at) VALUES ('c1', ?, 'card', 'Mango list', ?)",
                         (NISHA["id"], config.now_iso()))
            new_id = conn.execute("SELECT MAX(id) FROM messages").fetchone()[0]
            self.assertEqual(new_id, 6)                                    # 5 is never reused
            self.assertEqual(conn.execute("SELECT rowid FROM messages_fts WHERE messages_fts MATCH 'mango'").fetchall()[0][0], 6)
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO messages (conversation_id, kind, body, created_at) VALUES ('c1', 'nope', '', 'x')")
            # the tables that point at messages still cascade
            conn.execute("DELETE FROM messages WHERE id = 1")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM reactions").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM stars").fetchone()[0], 0)
            conn.execute("DELETE FROM messages WHERE id = 3")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM poll_options").fetchone()[0], 0)
            conn.commit()
            self.assertFalse(db._allow_card_messages(conn))               # done once
        finally:
            conn.close()
        db.init_db()                                                      # and again: nothing changes

    def test_old_backup_restored_is_migrated(self):
        self.old_database()
        src = config.DB_PATH + ".old"
        shutil.copy(config.DB_PATH, src)
        db.init_db()
        self.enable(ADMIN)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.write(src, "chat.db")
        os.remove(src)
        r = self.req("POST", "/api/admin-storage-import-db", content=buf.getvalue())
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn(db.NEW_KINDS, sql("SELECT sql FROM sqlite_master WHERE name = 'messages'")[0]["sql"])

    def test_existing_foreign_key_problems_dont_block_it(self):
        self.old_database()
        conn = db._connect()
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("INSERT INTO stars (user_id, message_id, created_at) VALUES ('u', 999, 'x')")   # an orphan from long ago
        conn.commit()
        conn.close()
        db.init_db()
        self.assertIn(db.NEW_KINDS, sql("SELECT sql FROM sqlite_master WHERE name = 'messages'")[0]["sql"])

    def test_a_failure_changes_nothing(self):
        self.old_database()
        orig = db.NEW_KINDS
        try:
            db.NEW_KINDS = "CHECK (kind IN ('text','card'))"              # the copy fails half-way (a poll row)
            with self.assertRaises(sqlite3.IntegrityError):
                db.init_db()
        finally:
            db.NEW_KINDS = orig
        conn = db._connect()
        try:
            self.assertIn(db.OLD_KINDS, conn.execute("SELECT sql FROM sqlite_master WHERE name = 'messages'").fetchone()[0])
            self.assertIsNone(conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'messages_new'").fetchone())
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 4)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE tbl_name = 'messages' AND type = 'trigger'").fetchone()[0], 3)
        finally:
            conn.close()
        db.init_db()                                                      # and the real one then works
        self.assertIn(db.NEW_KINDS, sql("SELECT sql FROM sqlite_master WHERE name = 'messages'")[0]["sql"])

    def test_unexpected_table_is_left_alone(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, kind TEXT)")
        with self.assertRaises(RuntimeError):
            db._allow_card_messages(conn)


class EndToEndTests(ApiTestCase):
    """The real app (lifespan, ha_events' WebSocket shared with the bus) and a fake Household Docs, both on the
    fake Home Assistant event bus."""

    def setUp(self):
        super().setUp()
        logging.getLogger("app_bus").setLevel(logging.CRITICAL)
        logging.getLogger("ha_ws").setLevel(logging.CRITICAL)
        self.enable(ADMIN, NISHA, TARUN)
        self.hh = self.household()
        self.notify_to(TARUN, "mobile_app_tarun")
        self.dm = self.ok(self.post("/api/conversations/direct", {"userId": TARUN["id"]}, NISHA))["id"]
        self.fake = FakeHABus(token="t0k")
        api = self.fake.start()
        self.saved = (config.SUPERVISOR_TOKEN, config.SUPERVISOR_CORE_API, config.SUPERVISOR_CORE_WS)
        config.SUPERVISOR_TOKEN, config.SUPERVISOR_CORE_API, config.SUPERVISOR_CORE_WS = "t0k", api, self.fake.ws_url
        self.tmp = tempfile.mkdtemp(prefix="docs-bus-")
        self.docs = app_bus.AppBus(api_base=api, ws_url=self.fake.ws_url, token="t0k", outbox_thread=False)
        self.replies = []
        for kind in ("chat.chats.list", "chat.card"):
            self.docs.on_reply(kind, lambda m, c: self.replies.append(m))
        self.docs.start("household_docs", "Household Docs", "1.0.0", wants=["chat.card"],
                        db=lambda: sqlite3.connect(os.path.join(self.tmp, "docs.db"), timeout=10))
        self.actions = []
        self._orig_action = notifier.handle_action
        notifier.handle_action = lambda action, text: self.actions.append(action) or "ok"

    def tearDown(self):
        notifier.handle_action = self._orig_action
        self.docs.stop()
        app_messages.stop()
        ha_events.stop()
        self.fake.stop()
        config.SUPERVISOR_TOKEN, config.SUPERVISOR_CORE_API, config.SUPERVISOR_CORE_WS = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)
        logging.getLogger("app_bus").setLevel(logging.NOTSET)
        logging.getLogger("ha_ws").setLevel(logging.NOTSET)
        super().tearDown()

    def ask(self, kind, data, **kw):
        mid = self.docs.send("household_chat", kind, data, **kw)
        self.docs.run_outbox_once()
        wait_until(lambda: any(r.reply_to == mid for r in self.replies), what=f"answer to {kind}")
        return [r for r in self.replies if r.reply_to == mid][0]

    def test_docs_sends_and_chat_answers(self):
        with ingress_client(app) as client:
            self.client = client
            wait_until(lambda: self.docs.available("household_chat", "chat.card"), what="Chat's hello")
            self.assertTrue(self.docs.available("household_chat", "chat.chats.list"))
            self.assertEqual(self.fake.subscribers(), 3)        # Chat: ONE socket for buttons + bus; Docs: its own
            # the chats Nisha may post in
            ans = self.ask("chat.chats.list", {"requested_by": NISHA["id"]}, expires_in=timedelta(minutes=2))
            self.assertEqual(ans.kind, "ack")
            self.assertIn(self.dm, [c["id"] for c in ans.result["chats"]])
            # a card, shared with the members
            ans = self.ask("chat.card", dict(CARD, requested_by=NISHA["id"], chat_id=self.dm, share_with_members=True),
                           ref="card:n_trip")
            self.assertEqual((ans.kind, ans.ref, ans.result["chat_id"], ans.result["member_ids"]),
                             ("ack", "card:n_trip", self.dm, [TARUN["id"]]))
            msgs = self.ok(self.get(f"/api/conversations/{self.dm}/messages", TARUN))["messages"]
            self.assertEqual(msgs[-1]["card"]["href"], "/a1b2c3d4_household_docs/doc/n_trip")
            self.assertEqual(self.sent.items[-1]["message"], "Nisha shared a checklist “Trip 2026”")
            # the same message again (a lost ack): answered again, posted once
            env = [e for e in self.fake.events() if e.get("kind") == "chat.card"][0]
            self.fake.fire("household_apps", env)
            wait_until(lambda: len([e for e in self.fake.events() if e.get("reply_to") == env["id"]]) == 2)
            self.assertEqual(sql("SELECT COUNT(*) AS n FROM app_cards")[0]["n"], 1)
            # refused: someone who isn't in the chat
            ans = self.ask("chat.card", dict(CARD, requested_by=ADMIN["id"], chat_id=self.dm))
            self.assertEqual((ans.kind, ans.reason, ans.data.get("detail")), ("nack", "not_found", "chat"))
            self.assertEqual(sql("SELECT COUNT(*) AS n FROM app_cards")[0]["n"], 1)
            # Admin → Connected apps
            out = self.ok(self.get("/api/admin/connected-apps"))
            self.assertTrue(out["on"] and out["connected"])
            self.assertEqual([(a["slug"], a["name"], a["active"]) for a in out["apps"]],
                             [("household_docs", "Household Docs", True)])
            # the phone's buttons still arrive on the same connection
            self.fake.fire("mobile_app_notification_action", {"action": "HCHAT_READ_x"})
            wait_until(lambda: self.actions == ["HCHAT_READ_x"], what="button on the shared socket")
            # the housekeeping tick runs the outbox
            from app import housekeeping
            housekeeping.tick(1)
        # shutdown closes the one socket and leaves no bus threads
        wait_until(lambda: self.fake.subscribers() == 1, what="Chat's socket closed")
        self.assertFalse(app_bus.default.started)
