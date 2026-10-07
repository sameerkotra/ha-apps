"""Send to Chat and Checklist → Todo (SPEC §17.15–§17.16, APP_MESSAGES_SPEC §6.3–§6.5), with the app's real bus on
the fake Home Assistant event bus and an invented Household Chat and Household Todo in this process (the real apps
are in test_connect_apps.py):

- the requests and their answers, polled as the page does; nacks in plain words; nothing offered when the other
  app isn't there; a list request that isn't answered in time;
- members' shares only after Chat's ack, only for owners and managers, never for children; read-only mode;
- Make a Todo list: a new or existing list, ticked items left out unless included, items under items, long
  checklists in several messages one after the other (the first with `new`, the rest with the list id), Move
  only after the ack, the note on the checklist;
- every envelope: no document content (the chosen checklist items are the one exception);
- Admin → Connected apps; "Open in Docs" (the page path, the redirect, the 🔒 No access answer).
"""
import json
import logging
import os
import shutil
import sqlite3
import tempfile
import time
from datetime import timedelta

from base import ASHA, DEV, KABIR, MEERA, ApiBase, hdr

from app import app_messages as am
from app import db
from app.common import app_bus
from common_tests.fake_ha_bus import FakeHABus

KID = hdr("u_kid", "Ravi Rao", "ravi")
SECRET = "SECRET-CONTENT-4711"


def wait_until(cond, timeout=8.0, what="condition"):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {what}")


class FakeChat:
    """Household Chat as far as Docs can tell: chat.chats.list and chat.card on its own bus."""

    def __init__(self, api, ws_url, tmp):
        self.bus = app_bus.AppBus(api_base=api, ws_url=ws_url, token="t0k", outbox_thread=False)
        self.cards, self.lists = [], []
        self.chats = {"g_trip": {"name": "Trip", "kind": "group", "icon": "🧳", "members": ["u_kabir", "u_meera", "u_stranger"]},
                      "d_meera": {"name": "Meera Rao", "kind": "direct", "members": ["u_kabir", "u_meera"]},
                      "g_all": {"name": "Household", "kind": "group", "members": ["u_kabir", "u_meera", "u_dev", "u_asha"]}}
        self.nack = None
        self.forge = None                      # f(answer) -> answer: what a forged ack would say instead
        self.bus.handler("chat.chats.list")(self.on_list)
        self.bus.handler("chat.card")(self.on_card)
        self.bus.start("household_chat", "Household Chat", "2.2.0", db=lambda: sqlite3.connect(os.path.join(tmp, "chat.db"), timeout=10))

    def on_list(self, msg, conn):
        self.lists.append(msg.data)
        if self.nack:
            raise app_bus.Nack(*self.nack)
        uid, one = msg.data["requested_by"], msg.data.get("chat_id")
        out = [{"id": cid, "kind": c["kind"], "name": c["name"], "members": len(c["members"]),
                **({"member_ids": [m for m in c["members"] if m != uid]} if one else {}),
                **({"icon": c["icon"]} if c.get("icon") else {})}
               for cid, c in self.chats.items() if uid in c["members"] and (one is None or cid == one)]
        if one and not out:
            raise app_bus.Nack("not_found", "chat")
        return {"chats": out, "more": False}

    def on_card(self, msg, conn):
        self.cards.append(msg.data)
        if self.nack:
            raise app_bus.Nack(*self.nack)
        c = self.chats.get(msg.data["chat_id"])
        if c is None or msg.data["requested_by"] not in c["members"]:
            raise app_bus.Nack("not_found", "chat")
        out = {"message_id": f"m{len(self.cards)}", "chat_id": msg.data["chat_id"]}
        if msg.data.get("share_with_members"):
            out["member_ids"] = [m for m in c["members"] if m != msg.data["requested_by"]]
        return self.forge(out) if self.forge else out


class FakeTodo:
    def __init__(self, api, ws_url, tmp):
        self.bus = app_bus.AppBus(api_base=api, ws_url=ws_url, token="t0k", outbox_thread=False)
        self.adds, self.lists = [], {"l_shared": {"name": "House", "kind": "shared", "tasks": []},
                                     "l_mine": {"name": "Mine", "kind": "personal", "tasks": []}}
        self.nack = None
        self.bus.handler("todo.lists.list")(self.on_list)
        self.bus.handler("todo.items.add")(self.on_add)
        self.bus.start("household_todo", "Household Todo", "2.3.0", db=lambda: sqlite3.connect(os.path.join(tmp, "todo.db"), timeout=10))

    def on_list(self, msg, conn):
        return {"lists": [{"id": k, "name": v["name"], "kind": v["kind"], "open": len(v["tasks"])} for k, v in self.lists.items()],
                "more": False}

    def on_add(self, msg, conn):
        self.adds.append(msg.data)
        if self.nack:
            raise app_bus.Nack(*self.nack)
        d = msg.data
        created = False
        if "new" in d:
            lid = f"l_new{len(self.lists)}"
            self.lists[lid] = {"name": d["new"]["name"], "kind": "shared" if d["new"].get("shared") else "personal", "tasks": []}
            created = True
        else:
            lid = d["list_id"]
            if lid not in self.lists:
                raise app_bus.Nack("not_found", "list")
        tasks = [i for i in d["items"] if i.get("level", 0) == 0]
        self.lists[lid]["tasks"] += d["items"]
        return {"list_id": lid, "list_name": self.lists[lid]["name"], "created": created, "tasks": len(tasks),
                "subitems": len(d["items"]) - len(tasks)}


class BusBase(ApiBase):
    PEOPLE = (ASHA, KABIR, MEERA, DEV, KID)
    PEERS = ("chat", "todo")

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        logging.getLogger("app_bus").setLevel(logging.CRITICAL)
        logging.getLogger("ha_ws").setLevel(logging.CRITICAL)
        cls.fake = FakeHABus(token="t0k")
        cls.bus_api = cls.fake.start()

    @classmethod
    def tearDownClass(cls):
        cls.fake.stop()
        logging.getLogger("app_bus").setLevel(logging.NOTSET)
        logging.getLogger("ha_ws").setLevel(logging.NOTSET)
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix="docs-peers-")
        self.fake.drop = None
        with self.fake._lock:
            self.fake.fired.clear()
        with db.get_conn() as conn:
            conn.execute("UPDATE users SET is_child = 1 WHERE id = 'u_kid'")
        am.start(thread=True, api_base=self.bus_api, ws_url=self.fake.ws_url, token="t0k")
        am.learn_panel(info={"slug": "a1b2c3d4_household_docs", "ingress_panel": True})     # as the Supervisor says
        self.chat = FakeChat(self.bus_api, self.fake.ws_url, self.tmp) if "chat" in self.PEERS else None
        self.todo = FakeTodo(self.bus_api, self.fake.ws_url, self.tmp) if "todo" in self.PEERS else None
        if self.chat:
            wait_until(lambda: am.bus.available(am.CHAT, "chat.card"), what="Chat's hello")
        if self.todo:
            wait_until(lambda: am.bus.available(am.TODO, "todo.items.add"), what="Todo's hello")

    def tearDown(self):
        for p in (self.chat, self.todo):
            if p:
                p.bus.stop()
        am.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)
        super().tearDown()

    # -- helpers -------------------------------------------------------------
    def wait_request(self, rid, h=KABIR, state=None):
        out = {}

        def done():
            out["r"] = self.ok(self.get(f"/api/bus/requests/{rid}", h))
            return out["r"]["state"] != "pending" if state is None else out["r"]["state"] == state
        wait_until(done, what=f"request {rid}")
        return out["r"]

    def envelopes(self, kind):
        return [e for e in self.fake.events() if e.get("kind") == kind]

    def assert_no_content(self, *needles):
        blob = json.dumps(self.fake.events(), ensure_ascii=False)
        for n in needles:
            self.assertNotIn(n, blob)

    def members(self, nid, chat_id, h=KABIR):
        """Ask Chat who is in one chat (the dialog does it when *Also give the chat's members access* is ticked):
        (request id, the ids of the Docs users who can't open it yet)."""
        req = self.ok(self.post(f"/api/nodes/{nid}/chat/chats", {"chatId": chat_id}, h))
        r = self.wait_request(req["request"], h)
        self.assertEqual(r["state"], "done", r)
        return r["id"], [p["id"] for p in r["result"]["cantOpen"]]

    def checklist(self, name="Weekend", items=("Mow the lawn", "Rake leaves"), h=KABIR, parent=None):
        c = self.create("checklist", name, parent, h)
        ops = [{"op": "add", "text": t.lstrip("> "), **({"indent": 1} if t.startswith("> ") else {})} for t in items]
        for i in range(0, len(ops), 200):
            self.ok(self.ops(c["id"], ops[i:i + 200], h))
        return c


class ChatTests(BusBase):
    def test_chat_list_card_and_member_shares_after_the_ack(self):
        n = self.note("Packing plan", text=f"Bring the passports. {SECRET}")
        req = self.ok(self.post(f"/api/nodes/{n['id']}/chat/chats", {}))
        self.assertEqual(req["expiresIn"], 120)
        r = self.wait_request(req["request"])
        self.assertEqual(r["state"], "done", r)
        chats = {c["id"]: c for c in r["result"]["chats"]}
        self.assertEqual(set(chats), {"g_trip", "d_meera", "g_all"})
        self.assertEqual(chats["g_trip"]["members"], 3)
        self.assertNotIn("memberIds", chats["g_trip"])
        lst = self.envelopes("chat.chats.list")[0]
        self.assertEqual(lst["data"], {"requested_by": "u_kabir", "limit": 50})
        exp = app_bus._parse_time(lst["expires"]) - app_bus._parse_time(lst["sent"])
        self.assertEqual(exp, timedelta(minutes=2))
        # who is in the picked chat: asked for that one chat only
        mr = self.ok(self.post(f"/api/nodes/{n['id']}/chat/chats", {"chatId": "g_trip"}))
        m = self.wait_request(mr["request"])
        self.assertEqual(m["result"]["cantOpen"], [{"id": "u_meera", "name": "Meera Rao", "off": False}])  # a Docs user
        self.assertEqual(m["result"]["notInDocs"], 1)                           # someone who doesn't use Docs
        self.assertNotIn("memberIds", m["result"])
        self.assertEqual(self.envelopes("chat.chats.list")[1]["data"], {"requested_by": "u_kabir", "chat_id": "g_trip"})
        # the card is posted; Chat is slow: nothing is shared until its ack arrives
        self.fake.drop = lambda t, d: d.get("kind") == "chat.card"
        req = self.ok(self.post(f"/api/nodes/{n['id']}/chat/card", {"chatId": "g_trip", "share": "viewer",
                                                                     "members": mr["request"], "memberIds": ["u_meera"]}))
        wait_until(lambda: self.envelopes("chat.card"), what="the card message")
        time.sleep(0.3)
        self.assertEqual(self.ok(self.get(f"/api/bus/requests/{req['request']}"))["state"], "pending")
        self.assertEqual(self.get(f"/api/docs/{n['id']}", MEERA).status_code, 404)
        card = self.envelopes("chat.card")[0]
        self.assertEqual(card["data"], {"requested_by": "u_kabir", "chat_id": "g_trip", "title": "Packing plan", "type": "note",
                                        "item_id": n["id"], "target": f"/doc/{n['id']}", "share_with_members": True,
                                        "badge": "Docs", "owner_name": "Kabir Rao", "panel": "/a1b2c3d4_household_docs"})
        self.fake.drop = None
        self.fake.fire("household_apps", card)                                   # now it arrives
        r = self.wait_request(req["request"])
        self.assertEqual(r["state"], "done", r)
        self.assertEqual((r["result"]["chatId"], r["result"]["shared"]), ("g_trip", 1))
        self.assertEqual(self.open(n["id"], MEERA)["role"], "viewer")            # a normal share now
        shares = self.ok(self.get(f"/api/nodes/{n['id']}/shares"))["shares"]
        self.assertEqual([(s["userId"], s["role"]) for s in shares], [("u_meera", "viewer")])
        self.assert_no_content(SECRET, "passports")

    def test_a_forged_answer_cant_add_shares(self):
        """Security review: Docs shares only with the members the sender confirmed, out of those Chat named for
        that chat before; an ack naming anyone else (or another chat) adds nothing for them."""
        n = self.note("Plan")
        mid, ids = self.members(n["id"], "d_meera")
        self.assertEqual(ids, ["u_meera"])
        self.chat.forge = lambda out: dict(out, member_ids=["u_meera", "u_dev", "u_asha"])
        r = self.wait_request(self.ok(self.post(f"/api/nodes/{n['id']}/chat/card", {
            "chatId": "d_meera", "share": "viewer", "members": mid, "memberIds": ["u_meera", "u_dev"]}))["request"])
        self.assertEqual(r["result"]["shared"], 1)
        self.assertEqual({s["userId"] for s in self.ok(self.get(f"/api/nodes/{n['id']}/shares"))["shares"]}, {"u_meera"})
        # unticked in the dialog: not shared, whatever the answer says
        n2 = self.note("Plan 2")
        mid, ids = self.members(n2["id"], "g_all")
        r = self.wait_request(self.ok(self.post(f"/api/nodes/{n2['id']}/chat/card", {
            "chatId": "g_all", "share": "viewer", "members": mid, "memberIds": ["u_meera"]}))["request"])
        self.assertEqual({s["userId"] for s in self.ok(self.get(f"/api/nodes/{n2['id']}/shares"))["shares"]}, {"u_meera"})
        # an answer about another chat: nobody
        n3 = self.note("Plan 3")
        mid, ids = self.members(n3["id"], "d_meera")
        self.chat.forge = lambda out: dict(out, chat_id="g_all", member_ids=["u_meera"])
        r = self.wait_request(self.ok(self.post(f"/api/nodes/{n3['id']}/chat/card", {
            "chatId": "d_meera", "share": "viewer", "members": mid, "memberIds": ids}))["request"])
        self.assertEqual(r["result"]["shared"], 0)
        self.assertEqual(self.ok(self.get(f"/api/nodes/{n3['id']}/shares"))["shares"], [])
        # the members request must be this person's, for this item and this chat
        self.chat.forge = None
        self.assertEqual(self.post(f"/api/nodes/{n3['id']}/chat/card", {"chatId": "g_all", "share": "viewer",
                                                                        "members": mid, "memberIds": ids}).status_code, 409)
        self.assertEqual(self.post(f"/api/nodes/{n['id']}/chat/card", {"chatId": "d_meera", "share": "viewer",
                                                                       "members": mid, "memberIds": ids}).status_code, 409)
        # a members answer naming more than the one chat asked about isn't used
        self.chat.forge = None
        orig = self.chat.on_list

        def two(msg, conn):
            out = orig(msg, conn)
            if msg.data.get("chat_id"):
                out["chats"].append({"id": "g_all", "kind": "group", "name": "x", "members": 9, "member_ids": ["u_dev"]})
            return out
        self.chat.bus._handlers["chat.chats.list"] = (two, self.chat.bus._handlers["chat.chats.list"][1]) \
            if isinstance(self.chat.bus._handlers.get("chat.chats.list"), tuple) else two
        r = self.wait_request(self.ok(self.post(f"/api/nodes/{n3['id']}/chat/chats", {"chatId": "d_meera"}))["request"])
        self.assertEqual(r["state"], "failed")

    def test_folder_and_file_cards(self):
        f = self.create("folder", "Taxes")
        self.ok(self.post(f"/api/nodes/{f['id']}/chat/card", {"chatId": "d_meera"}))
        up = self.c.post(f"/api/nodes/{f['id']}/upload?name=bill.pdf", content=b"%PDF-1.4 x", headers=KABIR)
        fid = self.ok(up, 201)["id"]
        self.ok(self.post(f"/api/nodes/{fid}/chat/card", {"chatId": "d_meera"}))
        wait_until(lambda: len(self.chat.cards) == 2, what="two cards")
        self.assertEqual([(c["type"], c["target"], c["title"]) for c in self.chat.cards],
                         [("folder", f"/folder/{f['id']}", "Taxes"), ("file", f"/file/{fid}", "bill.pdf")])

    def test_only_owners_and_managers_offer_member_access(self):
        n = self.note("Plan")
        self.ok(self.share(n["id"], MEERA, "editor"))
        r = self.post(f"/api/nodes/{n['id']}/chat/card", {"chatId": "g_all", "share": "viewer", "members": "x",
                                                           "memberIds": []}, MEERA)
        self.assertEqual(r.status_code, 403)
        self.ok(self.post(f"/api/nodes/{n['id']}/chat/card", {"chatId": "g_all"}, MEERA))    # a card alone: fine
        self.ok(self.share(n["id"], DEV, "manager"))
        mid, ids = self.members(n["id"], "g_all", DEV)
        self.assertEqual(ids, ["u_asha"])                                        # Meera has editor already
        req = self.ok(self.post(f"/api/nodes/{n['id']}/chat/card", {"chatId": "g_all", "share": "editor", "members": mid,
                                                                     "memberIds": ids + ["u_meera"]}, DEV))
        r = self.wait_request(req["request"], DEV)
        self.assertEqual(r["result"]["shared"], 1)                               # Asha; Kabir owns it, Meera had more
        roles = {s["userId"]: s["role"] for s in self.ok(self.get(f"/api/nodes/{n['id']}/shares"))["shares"]}
        self.assertEqual(roles, {"u_meera": "editor", "u_dev": "manager", "u_asha": "editor"})
        # someone who can't see it can't send it
        self.assertEqual(self.post(f"/api/nodes/{self.note('Mine')['id']}/chat/card", {"chatId": "g_all"}, MEERA).status_code, 404)

    def test_manager_loses_the_right_before_the_ack(self):
        n = self.note("Plan")
        self.ok(self.share(n["id"], DEV, "manager"))
        mid, ids = self.members(n["id"], "g_all", DEV)
        self.fake.drop = lambda t, d: d.get("kind") == "chat.card"
        req = self.ok(self.post(f"/api/nodes/{n['id']}/chat/card", {"chatId": "g_all", "share": "viewer", "members": mid,
                                                                     "memberIds": ids}, DEV))
        wait_until(lambda: self.envelopes("chat.card"), what="the card")
        self.ok(self.share(n["id"], DEV, "viewer"))                              # demoted meanwhile
        self.fake.drop = None
        self.fake.fire("household_apps", self.envelopes("chat.card")[0])
        r = self.wait_request(req["request"], DEV)
        self.assertEqual((r["state"], r["result"]["shared"]), ("done", 0))
        self.assertIn("no longer share", r["result"]["notShared"][0])
        self.assertEqual({s["userId"] for s in self.ok(self.get(f"/api/nodes/{n['id']}/shares"))["shares"]}, {"u_dev"})

    def test_nacks_in_plain_words(self):
        n = self.note("Plan")
        req = self.ok(self.post(f"/api/nodes/{n['id']}/chat/card", {"chatId": "g_nope"}))
        r = self.wait_request(req["request"])
        self.assertEqual((r["state"], r["error"]), ("failed", "That chat isn't there any more, or you're no longer in it."))
        self.chat.nack = ("not_allowed", "read_only")
        r = self.wait_request(self.ok(self.post(f"/api/nodes/{n['id']}/chat/card", {"chatId": "g_trip"}))["request"])
        self.assertEqual(r["error"], "You can't post in that chat right now.")
        self.chat.nack = ("not_allowed", "no_access")
        r = self.wait_request(self.ok(self.post(f"/api/nodes/{n['id']}/chat/chats", {}))["request"])
        self.assertIn("open it once first", r["error"])
        self.chat.nack = ("invalid", "title")
        r = self.wait_request(self.ok(self.post(f"/api/nodes/{n['id']}/chat/card", {"chatId": "g_trip"}))["request"])
        self.assertEqual(r["error"], "Household Chat refused it (title).")

    def test_list_not_answered_in_time(self):
        n = self.note("Plan")
        orig = am.LIST_EXPIRES
        am.LIST_EXPIRES = timedelta(seconds=1)
        try:
            self.fake.drop = lambda t, d: d.get("kind") == "chat.chats.list"
            req = self.ok(self.post(f"/api/nodes/{n['id']}/chat/chats", {}))
            time.sleep(1.2)
            am.run_outbox()                                                       # the housekeeping tick
            r = self.wait_request(req["request"])
        finally:
            am.LIST_EXPIRES = orig
        self.assertEqual((r["state"], r["error"]), ("failed", "Household Chat didn't answer in time — is it running?"))

    def test_refused_for_children_and_in_read_only_mode(self):
        kid_note = self.note("Kid's", h=KID)
        self.assertEqual(self.post(f"/api/nodes/{kid_note['id']}/chat/chats", {}, KID).status_code, 403)
        self.assertEqual(self.post(f"/api/nodes/{kid_note['id']}/chat/card", {"chatId": "g_all"}, KID).status_code, 403)
        self.assertFalse(self.ok(self.get("/api/me", KID))["apps"]["chat"])
        n = self.note("Plan")
        self.ok(self.post("/api/admin/docs-folder/read-only", {"on": True}, ASHA))
        self.assertEqual(self.post(f"/api/nodes/{n['id']}/chat/card", {"chatId": "g_all", "share": "viewer", "members": "x",
                                                                         "memberIds": []}).status_code, 423)
        self.ok(self.post(f"/api/nodes/{n['id']}/chat/card", {"chatId": "g_all"}))     # a card alone changes nothing here

    def test_validation(self):
        n = self.note("Plan")
        for body in ({}, {"chatId": "bad id"}, {"chatId": "g_all", "share": "manager"}, {"chatId": "g_all", "x": 1},
                     {"chatId": "g_all", "share": "viewer"}, {"chatId": "g_all", "share": "viewer", "members": "x"},
                     {"chatId": "g_all", "share": "viewer", "members": "x", "memberIds": [5]}):
            self.assertEqual(self.post(f"/api/nodes/{n['id']}/chat/card", body).status_code, 422, body)
        self.assertEqual(self.get("/api/bus/requests/nope").status_code, 404)
        req = self.ok(self.post(f"/api/nodes/{n['id']}/chat/chats", {}))
        self.assertEqual(self.get(f"/api/bus/requests/{req['request']}", MEERA).status_code, 404)   # someone else's

    def test_me_connected_apps_and_admin_card(self):
        me = self.ok(self.get("/api/me"))
        self.assertEqual(me["apps"], {"chat": True, "todo": True, "panel": "/a1b2c3d4_household_docs"})
        out = self.ok(self.get("/api/admin/connected-apps", ASHA))
        self.assertTrue(out["on"] and out["connected"])
        self.assertEqual(sorted((a["slug"], a["active"]) for a in out["apps"]), [("household_chat", True), ("household_todo", True)])
        self.assertEqual(self.get("/api/admin/connected-apps").status_code, 403)
        hello = [e for e in self.fake.events() if e.get("kind") == "hello" and e.get("from") == "household_docs"][0]
        self.assertEqual((hello["data"]["can"], hello["data"]["wants"]),
                         (["assist.tool.call", "assist.tools.list"],                 # the Household Assistant asks
                          ["chat.card", "chat.chats.list", "todo.items.add", "todo.lists.list"]))


class NotThereTests(BusBase):
    PEERS = ()

    def test_nothing_offered_without_the_other_apps(self):
        me = self.ok(self.get("/api/me"))
        self.assertEqual((me["apps"]["chat"], me["apps"]["todo"]), (False, False))
        n = self.note("Plan")
        r = self.post(f"/api/nodes/{n['id']}/chat/chats", {})
        self.assertEqual(r.status_code, 409)
        self.assertIn("isn't available", r.json()["detail"])
        c = self.checklist()
        r = self.post(f"/api/docs/{c['id']}/todo", {"new": {"name": "X"}})
        self.assertEqual(r.status_code, 409)
        with db.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT state FROM todo_sends").fetchone()["state"], "failed")

    def test_bus_off_outside_home_assistant(self):
        am.stop()
        n = self.note("Plan")
        r = self.post(f"/api/nodes/{n['id']}/chat/chats", {})
        self.assertEqual(r.status_code, 409)
        self.assertIn("inside Home Assistant", r.json()["detail"])
        self.assertEqual(self.ok(self.get("/api/admin/connected-apps", ASHA)), {"on": False, "connected": False, "apps": []})


class TodoTests(BusBase):
    def test_make_a_todo_list_keep_and_move(self):
        c = self.checklist(items=("Mow the lawn", "> Edges", "Rake leaves", "Clean gutters"))
        doc = self.open(c["id"])
        keys = [i["key"] for i in doc["items"]]
        self.ok(self.ops(c["id"], [{"op": "tick", "key": keys[2]}]))           # Rake leaves: ticked → not sent
        note = self.note("Secret note", text=SECRET)
        # the lists Todo has
        req = self.ok(self.post(f"/api/docs/{c['id']}/todo/lists", {}))
        r = self.wait_request(req["request"])
        self.assertEqual([x["name"] for x in r["result"]["lists"]], ["House", "Mine"])
        # a new list, keep in Docs
        req = self.ok(self.post(f"/api/docs/{c['id']}/todo", {"new": {"name": "Weekend", "shared": True}}))
        self.assertEqual((req["parts"], req["items"]), (1, 3))
        r = self.wait_request(req["request"])
        self.assertEqual(r["state"], "done", r)
        self.assertEqual((r["result"]["listName"], r["result"]["sent"], r["result"]["created"]), ("Weekend", 3, True))
        add = self.todo.adds[0]
        self.assertEqual(add, {"requested_by": "u_kabir", "new": {"name": "Weekend", "shared": True}, "source": "Docs",
                               "items": [{"text": "Mow the lawn", "done": False, "level": 0}, {"text": "Edges", "done": False, "level": 1},
                                         {"text": "Clean gutters", "done": False, "level": 0}]})
        self.assertEqual(len(self.open(c["id"])["items"]), 4)                   # kept in Docs
        # the note on the checklist
        sends = self.open(c["id"])["todoSends"]
        self.assertEqual((sends[0]["listName"], sends[0]["items"], sends[0]["state"]), ("Weekend", 3, "done"))
        self.assertEqual(sends[0]["by"], "You")
        # move chosen items (ticked included) to an existing list: they leave Docs only after the ack
        self.fake.drop = lambda t, d: d.get("kind") == "todo.items.add"
        req = self.ok(self.post(f"/api/docs/{c['id']}/todo", {"listId": "l_shared", "listKind": "shared", "listName": "House",
                                                               "keys": [keys[2], keys[3]], "includeTicked": True, "move": True}))
        wait_until(lambda: self.envelopes("todo.items.add")[1:], what="the second add")
        time.sleep(0.3)
        self.assertEqual(len(self.open(c["id"])["items"]), 4)                   # not yet acked: still here
        self.fake.drop = None
        self.fake.fire("household_apps", self.envelopes("todo.items.add")[1])
        r = self.wait_request(req["request"])
        self.assertEqual((r["state"], r["result"]["removed"]), ("done", 2), r)
        self.assertEqual([i["text"] for i in self.open(c["id"])["items"]], ["Mow the lawn", "Edges"])
        self.assertEqual(self.todo.adds[-1]["items"], [{"text": "Rake leaves", "done": True, "level": 0},
                                                        {"text": "Clean gutters", "done": False, "level": 0}])
        self.assertEqual(self.todo.adds[-1]["list_id"], "l_shared")
        self.assert_no_content(SECRET)
        self.assertEqual(note["name"], "Secret note.txt")

    def test_a_sub_item_without_its_task_becomes_a_task(self):
        c = self.checklist(items=("A", "> a1", "B", "> b1"))
        keys = [i["key"] for i in self.open(c["id"])["items"]]
        r = self.wait_request(self.ok(self.post(f"/api/docs/{c['id']}/todo", {"new": {"name": "X"}, "keys": [keys[0], keys[3]]}))["request"])
        self.assertEqual(r["state"], "done")
        self.assertEqual(self.todo.adds[0]["items"], [{"text": "A", "done": False, "level": 0}, {"text": "b1", "done": False, "level": 0}])

    def test_long_checklists_go_in_parts_one_after_the_other(self):
        items = [f"Item {i:03d} " + "x" * 150 for i in range(120)]
        c = self.checklist(items=items)
        req = self.ok(self.post(f"/api/docs/{c['id']}/todo", {"new": {"name": "Big", "shared": False}, "move": True}))
        self.assertGreater(req["parts"], 2)
        r = self.wait_request(req["request"])
        self.assertEqual((r["state"], r["result"]["sent"], r["result"]["removed"]), ("done", 120, 120), r)
        adds = self.envelopes("todo.items.add")
        self.assertEqual(len(adds), req["parts"])
        self.assertIn("new", adds[0]["data"])
        lid = self.todo.adds[0] and [k for k in self.todo.lists if self.todo.lists[k]["name"] == "Big"][0]
        for e in adds[1:]:
            self.assertNotIn("new", e["data"])
            self.assertEqual(e["data"]["list_id"], lid)
        for e in adds:
            self.assertLessEqual(app_bus.event_size(e), app_bus.MAX_EVENT_BYTES)
        # one after the other: each part was sent after the previous one's ack
        fired = self.fake.events()
        for prev, nxt in zip(adds, adds[1:]):
            ack = [i for i, e in enumerate(fired) if e.get("kind") == "ack" and e.get("reply_to") == prev["id"]][0]
            self.assertLess(ack, fired.index(nxt))
        self.assertEqual([t["text"] for t in self.todo.lists[lid]["tasks"]], items)
        self.assertEqual(self.open(c["id"])["items"], [])
        self.assertEqual(self.todo.adds[0]["new"], {"name": "Big", "shared": False})

    def test_nack_leaves_items_and_says_why(self):
        c = self.checklist()
        self.todo.nack = ("not_allowed", "no_access")
        r = self.wait_request(self.ok(self.post(f"/api/docs/{c['id']}/todo", {"new": {"name": "X"}, "move": True}))["request"])
        self.assertEqual(r["state"], "failed")
        self.assertIn("Open Household Todo once first", r["error"])
        self.assertEqual(len(self.open(c["id"])["items"]), 2)
        sends = self.open(c["id"])["todoSends"]
        self.assertEqual(sends[0]["state"], "failed")
        self.assertIn("Open Household Todo once first", sends[0]["error"])
        with db.get_conn() as conn:                                              # the texts aren't kept
            self.assertNotIn("Mow", conn.execute("SELECT parts FROM todo_sends").fetchone()["parts"])
        self.todo.nack = ("not_found", "list")
        r = self.wait_request(self.ok(self.post(f"/api/docs/{c['id']}/todo", {"listId": "l_gone"}))["request"])
        self.assertEqual(r["error"], "That Todo list isn't there any more (or it's someone else's personal list).")

    def expire_pending(self, kind):
        """Todo never answered: the outbox's message is past its expiry at the next housekeeping tick."""
        with db.get_conn() as conn:
            conn.execute("UPDATE bus_outbox SET expires = '2000-01-01T00:00:00+00:00' WHERE kind = ? AND state = 'pending'",
                         (kind,))
        am.run_outbox()

    def test_no_answer_until_expiry_leaves_the_items(self):
        c = self.checklist()
        self.fake.drop = lambda t, d: d.get("kind") == "todo.items.add"
        req = self.ok(self.post(f"/api/docs/{c['id']}/todo", {"new": {"name": "X"}, "move": True}))
        wait_until(lambda: self.envelopes("todo.items.add"), what="the add")
        self.assertEqual(self.ok(self.get(f"/api/bus/requests/{req['request']}"))["state"], "pending")
        self.expire_pending("todo.items.add")
        r = self.wait_request(req["request"])
        self.assertEqual((r["state"], r["error"]), ("failed", "Household Todo didn't answer in time — is it running?"))
        self.assertEqual(len(self.open(c["id"])["items"]), 2)                  # Move: nothing left the checklist
        self.assertEqual(self.open(c["id"])["todoSends"][0]["state"], "failed")
        self.assertEqual(self.todo.adds, [])

    def test_a_late_part_stops_the_rest(self):
        items = [f"Item {i:03d} " + "x" * 150 for i in range(80)]
        c = self.checklist(items=items)
        # Todo answers the first part, then goes away: the second part expires, the rest is never sent
        self.fake.drop = lambda t, d: d.get("kind") == "todo.items.add" and bool(self.todo.adds)
        req = self.ok(self.post(f"/api/docs/{c['id']}/todo", {"new": {"name": "Big"}, "move": True}))
        self.assertGreaterEqual(req["parts"], 3)
        wait_until(lambda: len(self.envelopes("todo.items.add")) >= 2, what="the second part")
        time.sleep(0.3)
        self.expire_pending("todo.items.add")
        r = self.wait_request(req["request"])
        self.assertEqual(r["state"], "failed")
        first = len(self.todo.adds[0]["items"])
        self.assertIn(f"Sent {first} items, then", r["error"])
        self.assertEqual(len(self.envelopes("todo.items.add")), 2)              # nothing after the late part
        # only the acked part moved (removed by the outbox pump, which may finish just after the request failed)
        wait_until(lambda: [i["text"] for i in self.open(c["id"])["items"]] == items[first:], what="the acked part removed")
        time.sleep(0.3)
        self.assertEqual([i["text"] for i in self.open(c["id"])["items"]], items[first:])

    def test_move_waits_and_checks_rights_at_the_ack(self):
        c = self.checklist()
        self.ok(self.share(c["id"], MEERA, "editor"))
        self.fake.drop = lambda t, d: d.get("kind") == "todo.items.add"
        req = self.ok(self.post(f"/api/docs/{c['id']}/todo", {"new": {"name": "X"}, "move": True}, MEERA))
        wait_until(lambda: self.envelopes("todo.items.add"), what="the add")
        self.ok(self.share(c["id"], MEERA, "viewer"))                           # can't edit any more
        self.fake.drop = None
        self.fake.fire("household_apps", self.envelopes("todo.items.add")[0])
        wait_until(lambda: self.open(c["id"])["todoSends"][0]["state"] != "sending", what="the send to finish")
        self.assertEqual(len(self.open(c["id"])["items"]), 2)                   # sent, but nothing removed
        sends = self.open(c["id"], MEERA)["todoSends"]
        self.assertIn("the items stay in Docs", sends[0]["error"])
        self.assertEqual(sends[0]["removed"], 0)

    def test_who_may_and_what(self):
        c = self.checklist()
        self.ok(self.share(c["id"], MEERA, "viewer"))
        self.assertEqual(self.post(f"/api/docs/{c['id']}/todo", {"new": {"name": "X"}, "move": True}, MEERA).status_code, 403)
        r = self.wait_request(self.ok(self.post(f"/api/docs/{c['id']}/todo", {"new": {"name": "X"}}, MEERA))["request"], MEERA)
        self.assertEqual(r["state"], "done")
        self.assertEqual(self.todo.adds[-1]["requested_by"], "u_meera")
        # a personal list's name is only for the person who sent to it
        self.ok(self.post(f"/api/docs/{c['id']}/todo", {"listId": "l_mine", "listKind": "personal", "listName": "Mine"}))
        wait_until(lambda: self.open(c["id"])["todoSends"][0]["state"] == "done")
        self.assertEqual(self.open(c["id"])["todoSends"][0]["listName"], "Mine")
        self.assertIsNone(self.open(c["id"], MEERA)["todoSends"][0]["listName"])
        n = self.note("Not a list")
        self.assertEqual(self.post(f"/api/docs/{n['id']}/todo", {"new": {"name": "X"}}).status_code, 422)
        for body in ({}, {"new": {"name": ""}}, {"new": {"name": "x" * 61}}, {"new": {"name": "X"}, "listId": "l"},
                     {"new": {"name": "X"}, "move": "yes"}, {"new": {"name": "X"}, "keys": ["nope"]}):
            self.assertIn(self.post(f"/api/docs/{c['id']}/todo", body).status_code, (409, 422), body)
        self.ok(self.ops(c["id"], [{"op": "untickAll"}]))
        for k in [i["key"] for i in self.open(c["id"])["items"]]:
            self.ok(self.ops(c["id"], [{"op": "tick", "key": k}]))
        r = self.post(f"/api/docs/{c['id']}/todo", {"new": {"name": "X"}})
        self.assertEqual(r.status_code, 422)
        self.assertIn("Include ticked items", r.json()["detail"])

    def test_too_many_under_one_task(self):
        c = self.checklist(items=["Task"] + [f"> sub {i}" for i in range(101)])
        r = self.post(f"/api/docs/{c['id']}/todo", {"new": {"name": "X"}})
        self.assertEqual(r.status_code, 422)
        self.assertIn("more than 100", r.json()["detail"])

    def test_parts_unit(self):
        items = [{"text": f"t{i}", "done": False, "level": 0} for i in range(450)]
        parts = am.todo_parts(items)
        self.assertEqual(sum(parts, []), items)
        for p in parts:
            self.assertLessEqual(len(p), am.TODO_MAX_ITEMS)
            self.assertLessEqual(len(json.dumps(p, separators=(",", ":")).encode()), am.MAX_DATA - 400)
        orig, am.TODO_MAX_ITEMS = am.TODO_MAX_ITEMS, 10                          # the count limit too
        try:
            self.assertEqual([len(p) for p in am.todo_parts(items[:25])], [10, 10, 5])
        finally:
            am.TODO_MAX_ITEMS = orig
        parts = am.todo_parts([{"text": "T", "done": False, "level": 0}] + [{"text": "s", "done": False, "level": 1}] * 50
                              + [{"text": "U", "done": False, "level": 0}] * 160)
        self.assertEqual([x["level"] for x in parts[0][:51]], [0] + [1] * 50)    # a task stays with its sub-items
        self.assertTrue(all(p[0]["level"] == 0 for p in parts))
        self.assertEqual(am.clean_text("a  b\n c"), "a b c")
        self.assertEqual(len(am.clean_text("x" * 300)), 200)


class LinkTests(BusBase):
    PEERS = ()

    def test_panel_from_the_supervisor_and_hostname(self):
        self.assertEqual(am.learn_panel(info={"slug": "local_household_docs", "ingress_panel": True}), "/local_household_docs")
        self.assertIsNone(am.learn_panel(info={"slug": "a1b2c3d4_household_docs", "ingress_panel": False}))
        self.assertIsNone(am.learn_panel(info={"slug": "Bad Slug"}))
        self.assertEqual(am.learn_panel(info=None, hostname="a1b2c3d4-household-docs"), "/a1b2c3d4_household_docs")
        self.assertIsNone(am.learn_panel(info=None, hostname="a1b2c3d4-household-chat"))
        self.assertIsNone(am.learn_panel(info=None, hostname=""))
        self.assertEqual(self.ok(self.get("/api/me"))["apps"]["panel"], None)

    def test_notifications_open_the_sidebar_page_never_hassio_ingress(self):
        from app import config
        before = config.INGRESS_URL
        try:
            am.learn_panel(info={"slug": "a1b2c3d4_household_docs", "ingress_panel": True})
            self.assertEqual(config.INGRESS_URL, "/a1b2c3d4_household_docs")
            am.learn_panel(info={"slug": "a1b2c3d4_household_docs", "ingress_panel": False})   # no sidebar page
            self.assertEqual(config.INGRESS_URL, "/app/a1b2c3d4_household_docs")
            am.learn_panel(info={"slug": "Bad Slug"})                                          # keeps the last one
            self.assertEqual(config.INGRESS_URL, "/app/a1b2c3d4_household_docs")
            self.assertNotIn("/hassio/ingress", config.INGRESS_URL)
        finally:
            config.INGRESS_URL = before
            am.learn_panel(info=None, hostname="")

    def test_deep_link_redirect_and_no_access(self):
        n = self.note("Private plan", text="hush")
        r = self.c.get(f"/doc/{n['id']}", headers=MEERA, follow_redirects=False)
        self.assertEqual((r.status_code, r.headers["location"]), (307, f"../#/doc/{n['id']}"))
        self.assertEqual(self.c.get("/folder/abc", headers=MEERA, follow_redirects=False).headers["location"], "../#/folder/abc")
        self.assertEqual(self.c.get("/doc/..%2Fx", headers=MEERA, follow_redirects=False).status_code, 404)
        self.assertEqual(self.c.get("/other/abc", headers=MEERA, follow_redirects=False).status_code, 404)
        r = self.c.get("/common/ui.js", headers=MEERA)                      # the app's own two-part paths still load
        self.assertEqual((r.status_code, r.headers["content-type"].split(";")[0]), (200, "text/javascript"))
        # someone without access: the same answer as for something that doesn't exist — no name, no owner
        a = self.get(f"/api/docs/{n['id']}", MEERA)
        b = self.get("/api/docs/0123456789abcdef0123456789abcdef", MEERA)
        self.assertEqual((a.status_code, a.json()), (b.status_code, b.json()))
        for r in (a, self.get(f"/api/nodes/{n['id']}", MEERA)):
            self.assertNotIn("Private", r.text)
            self.assertNotIn("Kabir", r.text)
