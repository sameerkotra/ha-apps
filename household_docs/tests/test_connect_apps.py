"""Docs ↔ the real Household Chat and Household Todo, end to end (SPEC §17.15–§17.16): the two apps from this
repository run as their own processes (uvicorn), all three on one fake Home Assistant event bus. Docs is this
process (its real app and bus); Chat and Todo are driven through their own HTTP APIs, as people would.

Skipped when the sibling apps aren't next to this one (an app folder copied on its own)."""
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest

import httpx
from base import ASHA, DEV, KABIR, MEERA, ApiBase

from app import app_messages as am
from common_tests.fake_ha_bus import FakeHABus
from common_tests.ingress import identity_headers

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CHAT_DIR, TODO_DIR = os.path.join(REPO, "household_chat"), os.path.join(REPO, "household_todo")
PANEL = "/a1b2c3d4_household_docs"


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def wait_until(cond, timeout=20.0, what="condition"):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


def as_(h):
    """The same person's headers for another app (the same Home Assistant user)."""
    return identity_headers(h["X-Remote-User-Id"], h["X-Remote-User-Name"], h["X-Remote-User-Display-Name"])


class App:
    """One sibling app in its own process, on the fake bus."""

    def __init__(self, folder, tmp, bus_api, ws_url, admin):
        self.port = free_port()
        data = os.path.join(tmp, os.path.basename(folder))
        os.makedirs(os.path.join(data, "share"), exist_ok=True)
        with open(os.path.join(data, "options.json"), "w") as f:
            json.dump({"admin_users": [admin]}, f)
        env = dict(os.environ, DATA_DIR=data, OPTIONS_PATH=os.path.join(data, "options.json"),
                   SHARE_DIR=os.path.join(data, "share", "household_chat"), SHARE_ROOT=os.path.join(data, "share"),
                   SUPERVISOR_TOKEN="t0k", SUPERVISOR_CORE_API=bus_api, SUPERVISOR_CORE_WS=ws_url,
                   BACKGROUND_LOOPS="1", DEV_ADMINS=admin, PYTHONPATH=folder)
        env.pop("DB_PATH", None)
        self.log = open(os.path.join(tmp, os.path.basename(folder) + ".log"), "w")
        self.proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                                      "--port", str(self.port), "--log-level", "warning"],
                                     cwd=folder, env=env, stdout=self.log, stderr=subprocess.STDOUT)
        self.http = httpx.Client(base_url=f"http://127.0.0.1:{self.port}", timeout=10)
        wait_until(self._up, what=f"{os.path.basename(folder)} to start")

    def _up(self):
        if self.proc.poll() is not None:
            raise AssertionError(f"the app stopped: see {self.log.name}")
        try:
            return self.http.get("/api/health").status_code == 200
        except httpx.HTTPError:
            return False

    def call(self, method, path, h, body=None, code=200):
        r = self.http.request(method, path, headers=as_(h), json=body)
        assert r.status_code == code, (method, path, r.status_code, r.text)
        return r.json()

    def stop(self):
        self.http.close()
        self.proc.terminate()
        try:
            self.proc.wait(10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.log.close()


@unittest.skipUnless(os.path.isdir(CHAT_DIR) and os.path.isdir(TODO_DIR), "the sibling apps aren't here")
class RealAppsTests(ApiBase):
    PEOPLE = (ASHA, KABIR, MEERA, DEV)

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for n in ("app_bus", "ha_ws"):
            logging.getLogger(n).setLevel(logging.CRITICAL)
        cls.tmp = tempfile.mkdtemp(prefix="docs-real-apps-")
        cls.fake = FakeHABus(token="t0k")
        cls.bus_api = cls.fake.start()
        cls.chat = App(CHAT_DIR, cls.tmp, cls.bus_api, cls.fake.ws_url, "asha")
        cls.todo = App(TODO_DIR, cls.tmp, cls.bus_api, cls.fake.ws_url, "asha")

    @classmethod
    def tearDownClass(cls):
        cls.chat.stop()
        cls.todo.stop()
        cls.fake.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)
        for n in ("app_bus", "ha_ws"):
            logging.getLogger(n).setLevel(logging.NOTSET)
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        am.start(thread=True, api_base=self.bus_api, ws_url=self.fake.ws_url, token="t0k")
        am.learn_panel(info={"slug": PANEL[1:], "ingress_panel": True})
        # every test starts with a new database: replay the hellos Chat and Todo sent (they answer "who" once an hour)
        for e in self.fake.events():
            if e.get("kind") == "hello" and e.get("from") in (am.CHAT, am.TODO):
                am.bus.default.receive(e)
        wait_until(lambda: am.status()["chat"] and am.status()["todo"], what="Chat's and Todo's hello")

    def tearDown(self):
        am.stop()
        super().tearDown()

    def wait_request(self, rid, h=KABIR):
        out = {}

        def done():
            out["r"] = self.ok(self.get(f"/api/bus/requests/{rid}", h))
            return out["r"]["state"] != "pending"
        wait_until(done, what=f"request {rid}")
        return out["r"]

    def test_send_to_chat_with_member_access(self):
        chat = self.chat
        for h in (ASHA, KABIR, MEERA):
            chat.call("GET", "/api/me", h)
        for h in (KABIR, MEERA):
            chat.call("PATCH", f"/api/admin/people/{h['X-Remote-User-Id']}", ASHA, {"disabled": False})
        trip = chat.call("POST", "/api/conversations", KABIR, {"name": "Trip", "memberIds": ["u_meera"]}, code=201)["id"]
        n = self.note("Trip notes", text="Bring the passports")
        r = self.wait_request(self.ok(self.post(f"/api/nodes/{n['id']}/chat/chats", {}))["request"])
        self.assertEqual(r["state"], "done", r)
        chats = {c["id"]: c for c in r["result"]["chats"]}
        self.assertIn(trip, chats)
        self.assertNotIn("member_ids", json.dumps([e for e in self.fake.events() if e.get("reply_to")]))   # nobody named
        m = self.wait_request(self.ok(self.post(f"/api/nodes/{n['id']}/chat/chats", {"chatId": trip}))["request"])
        self.assertEqual(m["result"]["cantOpen"], [{"id": "u_meera", "name": "Meera Rao", "off": False}])
        self.assertEqual(self.get(f"/api/docs/{n['id']}", MEERA).status_code, 404)
        r = self.wait_request(self.ok(self.post(f"/api/nodes/{n['id']}/chat/card", {
            "chatId": trip, "share": "viewer", "members": m["id"], "memberIds": ["u_meera"]}))["request"])
        self.assertEqual((r["state"], r["result"]["chatId"], r["result"]["shared"]), ("done", trip, 1), r)
        self.assertEqual(self.open(n["id"], MEERA)["role"], "viewer")              # shared after Chat's ack
        msgs = chat.call("GET", f"/api/conversations/{trip}/messages", MEERA)["messages"]
        card = [m for m in msgs if m["kind"] == "card"][-1]
        self.assertEqual((card["card"]["title"], card["card"]["type"], card["card"]["href"], card["card"]["owner"]),
                         ("Trip notes", "note", f"{PANEL}/doc/{n['id']}", "Kabir Rao"))
        self.assertTrue(card["card"]["sharedWithMembers"])
        self.assertNotIn("passports", json.dumps(msgs))
        # Chat says no: a chat Kabir isn't in (Dev's direct chat with Meera)
        chat.call("GET", "/api/me", DEV)
        chat.call("PATCH", "/api/admin/people/u_dev", ASHA, {"disabled": False})
        dm = chat.call("POST", "/api/conversations/direct", DEV, {"userId": "u_meera"})["id"]
        r = self.wait_request(self.ok(self.post(f"/api/nodes/{n['id']}/chat/card", {"chatId": dm}))["request"])
        self.assertEqual((r["state"], r["error"]), ("failed", "That chat isn't there any more, or you're no longer in it."))
        self.assertNotIn("passports", json.dumps(self.fake.events()))

    def test_make_a_todo_list_and_move(self):
        todo = self.todo
        todo.call("GET", "/api/whoami", KABIR)                                 # opened Household Todo once
        c = self.create("checklist", "Weekend")
        self.ok(self.ops(c["id"], [{"op": "add", "text": "Mow"}, {"op": "add", "text": "Edges", "indent": 1},
                                   {"op": "add", "text": "Rake"}]))
        r = self.wait_request(self.ok(self.post(f"/api/docs/{c['id']}/todo", {"new": {"name": "Weekend", "shared": False}}))["request"])
        self.assertEqual((r["state"], r["result"]["sent"], r["result"]["created"]), ("done", 3, True), r)
        got = todo.call("GET", "/api/lists", KABIR)
        lists = {x["name"]: x for x in (got["lists"] if isinstance(got, dict) else got)}
        lid = lists["Weekend"]["id"]
        self.assertEqual(r["result"]["listId"], lid)
        tasks = todo.call("GET", f"/api/lists/{lid}/tasks", KABIR)
        tasks = tasks.get("tasks", tasks) if isinstance(tasks, dict) else tasks
        self.assertEqual([t["title"] for t in tasks], ["Mow", "Rake"])
        # the lists Kabir may add to, as Todo answers them (todo.lists.list)
        r = self.wait_request(self.ok(self.post(f"/api/docs/{c['id']}/todo/lists", {}))["request"])
        self.assertEqual(r["state"], "done", r)
        mine = {x["name"]: x for x in r["result"]["lists"]}
        self.assertEqual((mine["Weekend"]["id"], mine["Weekend"]["kind"]), (lid, "personal"))
        # Move into the same list: the items leave Docs once Todo has acked them
        keys = [i["key"] for i in self.open(c["id"])["items"]]
        r = self.wait_request(self.ok(self.post(f"/api/docs/{c['id']}/todo", {"listId": lid, "keys": [keys[2]], "move": True}))["request"])
        self.assertEqual((r["state"], r["result"]["removed"]), ("done", 1), r)
        self.assertEqual([i["text"] for i in self.open(c["id"])["items"]], ["Mow", "Edges"])
        tasks = todo.call("GET", f"/api/lists/{lid}/tasks", KABIR)
        tasks = tasks.get("tasks", tasks) if isinstance(tasks, dict) else tasks
        self.assertEqual([t["title"] for t in tasks], ["Mow", "Rake", "Rake"])
        # someone Todo doesn't know yet: refused, and nothing leaves Docs
        self.ok(self.share(c["id"], MEERA, "editor"))
        r = self.wait_request(self.ok(self.post(f"/api/docs/{c['id']}/todo", {"new": {"name": "X"}, "move": True}, MEERA))["request"], MEERA)
        self.assertEqual(r["state"], "failed")
        self.assertIn("Open Household Todo once first", r["error"])
        self.assertEqual(len(self.open(c["id"])["items"]), 2)
