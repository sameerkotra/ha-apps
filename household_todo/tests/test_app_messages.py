"""Messages from the other household apps (APP_MESSAGES_SPEC §6.4): Household Docs' checklist → Todo list.

- the two handlers (todo.lists.list, todo.items.add): every check, allowed and refused, nothing written on a nack;
- what the tasks look like in the app afterwards (source label, done, checklist items, order, nobody notified);
- Admin → Connected apps;
- end to end: the real app's lifespan on the fake Home Assistant event bus, with a fake Household Docs in this
  process sending to it.

All people and lists here are invented."""
import _env  # noqa: F401  (must be first)

import json
import logging
import os
import shutil
import sqlite3
import tempfile
import time
from datetime import datetime, timedelta, timezone

from test_api import ADMIN, ALICE, BOB, ApiBase

from app import app_messages, config, db
from app.common import app_bus
from app.main import app
from common_tests.fake_ha_bus import FakeHABus
from common_tests.ingress import ingress_client


def envelope(kind, data, frm="household_docs"):
    now = datetime.now(timezone.utc)
    return {"id": app_bus.new_ulid(now), "v": 1, "from": frm, "to": "household_todo", "kind": kind, "kv": 1,
            "reply_to": None, "ref": None, "sent": now.isoformat(), "expires": (now + timedelta(hours=1)).isoformat(),
            "data": data}


def wait_until(cond, timeout=5.0, what="condition"):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {what}")


class HandlerBase(ApiBase):
    HANDLERS = {"todo.items.add": app_messages.on_items_add, "todo.lists.list": app_messages.on_lists_list}

    def call(self, kind, data, frm="household_docs"):
        """Run a handler as the bus does: inside one transaction; a Nack rolls everything back."""
        msg = app_bus.Message(envelope(kind, data, frm))
        with db.get_conn() as conn:
            try:
                return self.HANDLERS[kind](msg, conn)
            except app_bus.Nack as n:
                conn.rollback()
                return n

    def add_items(self, items, **data):
        data.setdefault("requested_by", "u_alice")
        if "list_id" not in data and "new" not in data:
            data["list_id"] = self.shared_id()
        return self.call("todo.items.add", dict(data, items=items))

    def assertNack(self, res, reason, detail=None):
        self.assertIsInstance(res, app_bus.Nack, res)
        self.assertEqual((res.reason, res.detail), (reason, detail))

    def tasks(self, list_id, h=ALICE, show="open"):
        r = self.get(f"/api/lists/{list_id}/tasks", h, params={"show": show})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def counts(self):
        with db.get_conn() as c:
            return tuple(c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("lists", "tasks", "task_items"))


class ListsListTests(HandlerBase):
    def test_lists_a_person_may_add_to(self):
        mine = self.mine_id()
        extra = self.post("/api/lists", {"name": "Garden", "kind": "personal"}).json()["id"]
        bobs = self.post("/api/lists", {"name": "Bob's own", "kind": "personal"}, BOB).json()["id"]
        chores = self.post("/api/lists", {"name": "Chores", "kind": "shared"}, BOB).json()["id"]
        self.add("one open", list_id=chores)
        res = self.call("todo.lists.list", {"requested_by": "u_alice"})
        self.assertFalse(res["more"])
        ids = [x["id"] for x in res["lists"]]
        self.assertEqual(ids, [x["id"] for x in self.lists()])           # the app's own list and order
        self.assertNotIn(bobs, ids)
        self.assertIn(mine, ids)
        self.assertIn(extra, ids)
        by = {x["id"]: x for x in res["lists"]}
        self.assertEqual(by[chores], {"id": chores, "name": "Chores", "kind": "shared", "open": 1})
        self.assertEqual(by[mine]["kind"], "personal")

    def test_maintenance_list_marked(self):
        with db.get_conn() as c:
            c.execute("UPDATE lists SET role = 'maintenance' WHERE id = ?", (self.shared_id(),))
        res = self.call("todo.lists.list", {"requested_by": "u_alice"})
        self.assertTrue([x for x in res["lists"] if x.get("maintenance")])

    def test_refusals(self):
        self.assertNack(self.call("todo.lists.list", {"requested_by": "u_never_opened_todo"}), "not_allowed", "no_access")
        self.assertEqual(self.patch(f"/api/users/{self.uid('Bob')}", {"disabled": True}, ADMIN).status_code, 200)
        self.assertNack(self.call("todo.lists.list", {"requested_by": "u_bob"}), "not_allowed", "no_access")
        for bad in ({}, {"requested_by": 1}, {"requested_by": "x" * 65}):
            self.assertNack(self.call("todo.lists.list", bad), "invalid", "requested_by")

    def test_answer_stays_under_the_size_limit(self):
        for i in range(150):
            self.post("/api/lists", {"name": f"A rather long list name number {i:03d}", "kind": "shared"})
        res = self.call("todo.lists.list", {"requested_by": "u_alice"})
        self.assertTrue(res["more"])
        env = envelope("ack", {"result": res})
        env["ref"] = "r" * 200
        self.assertLessEqual(app_bus.event_size(env), app_bus.MAX_EVENT_BYTES)


class ItemsAddTests(HandlerBase):
    def test_new_personal_list_with_tasks_and_checklist_items(self):
        before = {x["id"] for x in self.lists()}
        res = self.add_items([{"text": "Passports"}, {"text": "Tickets", "done": True},
                              {"text": "Print", "level": 1}, {"text": "Check in", "level": 1, "done": True},
                              {"text": "  Charger  "}],
                             new={"name": " Trip 2026 "}, list_id=None, source="Docs")
        self.assertEqual((res["list_name"], res["created"], res["tasks"], res["subitems"]), ("Trip 2026", True, 3, 2))
        self.assertEqual(len(res["ids"]), 5)
        lst = [x for x in self.lists() if x["id"] not in before][0]
        self.assertEqual((lst["id"], lst["name"], lst["kind"], lst["ownerUserId"]), (res["list_id"], "Trip 2026", "personal", "u_alice"))
        self.assertNotIn(lst["id"], [x["id"] for x in self.lists(BOB)])         # private, like one made in the app
        open_tasks = self.tasks(lst["id"])
        self.assertEqual([t["title"] for t in open_tasks], ["Passports", "Charger"])
        self.assertTrue(all(t["source"] == "Docs" for t in open_tasks))
        with db.get_conn() as c:
            self.assertEqual({r[0] for r in c.execute("SELECT created_by FROM tasks WHERE list_id = ?", (lst["id"],))}, {"u_alice"})
        [done] = self.tasks(lst["id"], show="completed")
        self.assertEqual((done["title"], done["completed"], done["completedByName"]), ("Tickets", True, "Alice"))
        self.assertEqual([(i["text"], i["done"]) for i in done["items"]], [("Print", False), ("Check in", True)])
        self.assertEqual(res["ids"][0], open_tasks[0]["id"])
        self.assertEqual(res["ids"][2], done["items"][0]["id"])

    def test_new_shared_list_and_existing_lists(self):
        res = self.add_items([{"text": "Paint"}], new={"name": "House", "shared": True}, list_id=None)
        self.assertEqual(next(x for x in self.lists(BOB) if x["id"] == res["list_id"])["kind"], "shared")
        self.add("Already here")
        res = self.add_items([{"text": "Later"}])
        self.assertEqual((res["list_id"], res["created"]), (self.shared_id(), False))
        self.assertEqual([t["title"] for t in self.tasks(self.shared_id())], ["Already here", "Later"])   # at the end
        res = self.add_items([{"text": "Mine"}], list_id=self.mine_id())
        self.assertEqual(res["tasks"], 1)

    def test_source_defaults_to_the_senders_name(self):
        with db.get_conn() as c:
            c.execute("INSERT INTO bus_apps (slug, name, version, can, last_seen) VALUES ('household_docs', "
                      "'Household Docs', '1.0.0', '[]', ?)", (config.now_iso(),))
        self.add_items([{"text": "A"}])
        self.assertEqual(self.tasks(self.shared_id())[0]["source"], "Docs")
        res = self.call("todo.items.add", {"requested_by": "u_alice", "list_id": self.shared_id(),
                                           "items": [{"text": "C"}]}, frm="household_other")
        self.assertEqual(res["tasks"], 1)
        self.assertEqual(self.tasks(self.shared_id())[-1]["source"], "household_other"[:20])

    def test_a_first_level_1_item_becomes_a_task(self):
        res = self.add_items([{"text": "Orphan", "level": 1}, {"text": "Child", "level": 1}])
        self.assertEqual((res["tasks"], res["subitems"]), (1, 1))
        [t] = self.tasks(self.shared_id())
        self.assertEqual((t["title"], [i["text"] for i in t["items"]]), ("Orphan", ["Child"]))

    def test_refusals_write_nothing(self):
        bobs = self.post("/api/lists", {"name": "Bob's", "kind": "personal"}, BOB).json()["id"]
        before = self.counts()
        self.assertNack(self.add_items([{"text": "x"}], list_id=bobs), "not_found", "list")
        self.assertNack(self.add_items([{"text": "x"}], list_id="nope"), "not_found", "list")
        self.assertNack(self.add_items([{"text": "x"}], requested_by="u_stranger"), "not_allowed", "no_access")
        self.assertNack(self.add_items([{"text": "x"}], requested_by=5), "invalid", "requested_by")
        self.assertNack(self.add_items([{"text": "x"}], new={"name": "N"}, list_id=self.shared_id()), "invalid", "list_id")  # both
        self.assertNack(self.call("todo.items.add", {"requested_by": "u_alice", "items": [{"text": "x"}]}), "invalid", "list_id")
        cases = [
            ("items", None), ("items", []), ("items", [{"text": "x"}] * 201), ("items", ["x"]),
            ("text", [{"text": ""}]), ("text", [{"text": "  "}]), ("text", [{"text": "x" * 201}]), ("text", [{"text": 3}]),
            ("done", [{"text": "x", "done": "yes"}]), ("level", [{"text": "x", "level": 2}]),
            ("level", [{"text": "x", "level": True}]),
        ]
        for detail, items in cases:
            with self.subTest(detail=detail, items=str(items)[:40]):
                self.assertNack(self.add_items(items), "invalid", detail)
        for detail, new in (("new", "Trip"), ("name", {"name": ""}), ("name", {"name": "x" * 61}),
                            ("shared", {"name": "x", "shared": "no"})):
            self.assertNack(self.add_items([{"text": "x"}], new=new, list_id=None), "invalid", detail)
        self.assertNack(self.add_items([{"text": "x"}], list_id=7), "invalid", "list_id")
        self.assertNack(self.add_items([{"text": "x"}], source=""), "invalid", "source")
        self.assertNack(self.add_items([{"text": "x"}], source="x" * 21), "invalid", "source")
        # a valid list made by `new` is rolled back too when a later check fails
        self.assertNack(self.add_items([{"text": "t"}] + [{"text": "s", "level": 1}] * 101,
                                       new={"name": "Big"}, list_id=None), "invalid", "too_many_subitems")
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.patch(f"/api/users/{self.uid('Bob')}", {"disabled": True}, ADMIN).status_code, 200)
        self.assertNack(self.add_items([{"text": "x"}], requested_by="u_bob"), "not_allowed", "no_access")

    def test_acting_as_does_not_apply(self):
        """An admin's "act as" is a page thing: requested_by is always who it is."""
        alices = self.mine_id()
        self.assertNack(self.add_items([{"text": "x"}], requested_by="adm", list_id=alices), "not_found", "list")

    def test_limits_and_the_answer_size(self):
        res = self.add_items([{"text": "t"}] + [{"text": f"s{i}", "level": 1} for i in range(100)])
        self.assertEqual((res["tasks"], res["subitems"]), (1, 100))
        res = self.add_items([{"text": f"Task {i}"} for i in range(200)])
        self.assertEqual(res["tasks"], 200)
        size = len(json.dumps(res, separators=(",", ":")).encode())
        if "ids" in res:
            self.assertLessEqual(size, app_messages.MAX_ANSWER)
        env = envelope("ack", {"result": res})
        env["ref"] = "r" * 200
        self.assertLessEqual(app_bus.event_size(env), app_bus.MAX_EVENT_BYTES)
        orig = app_messages.MAX_ANSWER
        try:
            app_messages.MAX_ANSWER = 200
            res = self.add_items([{"text": f"Task {i}"} for i in range(20)])
            self.assertNotIn("ids", res)
            self.assertEqual(res["tasks"], 20)
        finally:
            app_messages.MAX_ANSWER = orig

    def test_nobody_is_notified(self):
        self.ha.requests.clear()
        self.add_items([{"text": "Quiet"}])
        self.assertEqual([r for r in self.ha.requests if "/services/notify" in r[1]], [])

    def test_restore_in_progress_is_busy(self):
        db._import_lock.acquire()
        try:
            self.assertNack(self.add_items([{"text": "x"}]), "busy", "restoring")
            self.assertNack(self.call("todo.lists.list", {"requested_by": "u_alice"}), "busy", "restoring")
        finally:
            db._import_lock.release()

    def test_connected_apps(self):
        self.assertEqual(self.get("/api/admin/connected-apps", ALICE).status_code, 403)
        out = self.get("/api/admin/connected-apps", ADMIN).json()
        self.assertFalse(out["on"])                                 # the tests run without background loops
        self.assertEqual(out["apps"], [])


class EndToEndTests(HandlerBase):
    """The real app's lifespan with its bus on the fake Home Assistant event bus, and a fake Household Docs."""

    def setUp(self):
        super().setUp()
        logging.getLogger("app_bus").setLevel(logging.CRITICAL)
        logging.getLogger("ha_ws").setLevel(logging.CRITICAL)
        self.fake = FakeHABus(token="t0k")
        api = self.fake.start()
        self.saved = (config.SUPERVISOR_TOKEN, config.SUPERVISOR_CORE_API, config.SUPERVISOR_CORE_WS, config.BACKGROUND_LOOPS)
        self.tmp = tempfile.mkdtemp(prefix="docs-bus-")
        self.docs = app_bus.AppBus(api_base=api, ws_url=self.fake.ws_url, token="t0k", outbox_thread=False)
        self.replies = []
        for kind in ("todo.lists.list", "todo.items.add"):
            self.docs.on_reply(kind, lambda m, c: self.replies.append(m))
        self.docs.start("household_docs", "Household Docs", "1.0.0",
                        db=lambda: sqlite3.connect(os.path.join(self.tmp, "docs.db"), timeout=10))
        self.api = api

    def tearDown(self):
        self.docs.stop()
        app_messages.stop()
        self.fake.stop()
        config.SUPERVISOR_TOKEN, config.SUPERVISOR_CORE_API, config.SUPERVISOR_CORE_WS, config.BACKGROUND_LOOPS = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)
        logging.getLogger("app_bus").setLevel(logging.NOTSET)
        logging.getLogger("ha_ws").setLevel(logging.NOTSET)
        super().tearDown()

    def ask(self, kind, data, **kw):
        mid = self.docs.send("household_todo", kind, data, **kw)
        self.docs.run_outbox_once()
        wait_until(lambda: any(r.reply_to == mid for r in self.replies), what=f"answer to {kind}")
        return [r for r in self.replies if r.reply_to == mid][0]

    def test_docs_sends_and_todo_answers(self):
        self.ha.requests.clear()
        # the app as it starts in Home Assistant: its own bus connection (the fake one)
        config.SUPERVISOR_TOKEN, config.SUPERVISOR_CORE_WS = "t0k", self.fake.ws_url
        config.SUPERVISOR_CORE_API = self.api
        app_messages.start()
        try:
            wait_until(lambda: self.docs.available("household_todo", "todo.items.add"), what="Todo's hello")
            self.assertTrue(self.docs.available("household_todo", "todo.lists.list"))
            ans = self.ask("todo.lists.list", {"requested_by": "u_alice"}, expires_in=timedelta(minutes=2))
            self.assertEqual(ans.kind, "ack")
            self.assertIn(self.shared_id(), [x["id"] for x in ans.result["lists"]])
            ans = self.ask("todo.items.add", {"requested_by": "u_alice", "new": {"name": "Weekend"}, "source": "Docs",
                                              "items": [{"text": "Mow"}, {"text": "Edges", "level": 1}]}, ref="todo:n1")
            self.assertEqual((ans.kind, ans.ref, ans.result["list_name"], ans.result["tasks"], ans.result["subitems"]),
                             ("ack", "todo:n1", "Weekend", 1, 1))
            lid = ans.result["list_id"]
            # a second batch to the list the first one made
            ans = self.ask("todo.items.add", {"requested_by": "u_alice", "list_id": lid, "items": [{"text": "Rake"}]})
            self.assertEqual([t["title"] for t in self.tasks(lid)], ["Mow", "Rake"])
            # the same message again (a lost ack): answered again, done once
            env = [e for e in self.fake.events() if e.get("kind") == "todo.items.add"][-1]
            self.fake.fire("household_apps", env)
            wait_until(lambda: len([e for e in self.fake.events() if e.get("reply_to") == env["id"]]) == 2)
            self.assertEqual([t["title"] for t in self.tasks(lid)], ["Mow", "Rake"])
            # refused
            ans = self.ask("todo.items.add", {"requested_by": "u_bob", "list_id": lid, "items": [{"text": "x"}]})
            self.assertEqual((ans.kind, ans.reason, ans.data.get("detail")), ("nack", "not_found", "list"))
            out = self.get("/api/admin/connected-apps", ADMIN).json()
            self.assertTrue(out["on"] and out["connected"])
            self.assertEqual([(a["slug"], a["can"]) for a in out["apps"]], [("household_docs", [])])
            self.assertEqual([r for r in self.ha.requests if "/services/notify" in r[1]], [])
            app_messages.run_outbox()                              # what the housekeeping loop runs
        finally:
            app_messages.stop()
        wait_until(lambda: self.fake.subscribers() == 1, what="Todo's socket closed")

    def test_lifespan_starts_and_stops_the_bus(self):
        config.SUPERVISOR_TOKEN, config.SUPERVISOR_CORE_WS, config.BACKGROUND_LOOPS = "t0k", self.fake.ws_url, True
        orig_api = config.SUPERVISOR_CORE_API
        config.SUPERVISOR_CORE_API = self.api
        from app import drive_time, ha_sensors, housekeeping, maint_files, reminders
        from app.common import ha_people
        loops = [(m, m.loop) for m in (reminders, ha_sensors, housekeeping, drive_time, ha_people, maint_files)]

        async def idle():
            import asyncio
            await asyncio.Event().wait()
        try:
            for m, _ in loops:
                m.loop = idle                                       # only the bus, not the other loops
            with ingress_client(app):
                wait_until(lambda: self.docs.available("household_todo", "todo.items.add"), what="hello at start-up")
                self.assertTrue(app_bus.default.started)
            self.assertFalse(app_bus.default.started)
            wait_until(lambda: self.fake.subscribers() == 1, what="closed at shutdown")
        finally:
            for m, fn in loops:
                m.loop = fn
            config.SUPERVISOR_CORE_API = orig_api
