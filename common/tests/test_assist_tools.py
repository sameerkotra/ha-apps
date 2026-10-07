"""Answering the Household Assistant (common/python/assist_tools.py): the catalogue, argument checks, the
switches, result fitting and links, and both kinds through two real app buses in this process.
HOUSEHOLD_ASSISTANT_SPEC.md §4–7; APP_MESSAGES_SPEC.md §6.6.

Shared: common/tests/test_assist_tools.py, run by the repository's tests (tests/test_assist_tools.py imports it)
and copied into every app that answers the assistant (tests/common_tests/). Standard library only."""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

try:                                            # in an app: its own copies
    from app.common import app_bus, assist_tools
except ImportError:                             # in the repository: common/ itself
    ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    from common.python import app_bus, assist_tools  # noqa: E402

Arg = assist_tools.Arg
USERS = {"u_ann": {"id": "u_ann", "name": "Ann"}, "u_kid": {"id": "u_kid", "name": "Kit", "is_child": True}}


def envelope(kind, data, to="household_demo", frm="household_assistant"):
    now = datetime.now(timezone.utc)
    return {"id": app_bus.new_ulid(now), "v": 1, "from": frm, "to": to, "kind": kind, "kv": 1, "reply_to": None,
            "ref": None, "sent": now.isoformat(), "expires": (now + timedelta(seconds=20)).isoformat(), "data": data}


def demo_catalogue(state):
    cat = assist_tools.Catalogue(
        "demo", targets=[r"/today", r"/items/[a-z0-9]{1,8}"],
        actor=lambda conn, uid: USERS.get(uid) if uid not in state["disabled"] else None,
        enabled=lambda conn: state["on"],
        person_enabled=lambda conn, user: user["id"] not in state["opted_out"],
        panel=lambda: state["panel"])

    @cat.tool("demo.things", "Things on the person's list. Use `when: today` for today's.",
              args={"when": Arg("enum", "which things", values=("today", "all"), required=True),
                    "count": Arg("number", "how many", min=1, max=100),
                    "day": Arg("date", "a day"), "month": Arg("month", "a month"),
                    "word": Arg("string", "a word", max_length=10), "flag": Arg("boolean", "a flag")},
              returns="the things", examples=("What's on today?",), children=True)
    def things(ctx):
        n = int(ctx.args.get("count", 2))
        items = [{"title": f"thing {i}", "who": ctx.user["name"], "nested": {"no": 1}} for i in range(n)]
        return ctx.result(f"{n} things for {ctx.user['name']}.", items=items,
                          links=[ctx.link("Today in Demo", "/today")])

    @cat.tool("demo.items.add", "Adds an item.", args={"text": Arg("string", "the item", required=True)},
              acts=True, returns="the added item")
    def add(ctx):
        ctx.conn.execute("INSERT INTO items (text, by) VALUES (?, ?)", (ctx.args["text"], ctx.user["id"]))
        return ctx.result(f"Added {ctx.args['text']}.", links=[ctx.link("The item", "/items/abc")])

    @cat.tool("demo.big", "A big answer.", args={"text": Arg("boolean", "huge text instead of items")})
    def big(ctx):
        if ctx.args.get("text"):
            return {"text": "é" * 5000 + " end", "items": [], "links": []}
        return ctx.result("Lots.", items=[{"n": i, "pad": "x" * 200} for i in range(80)])

    @cat.tool("demo.refuse", "Always says no.")
    def refuse(ctx):
        return app_bus.Nack("not_found", "thing")

    return cat


class CatalogueTests(unittest.TestCase):
    def setUp(self):
        self.state = {"on": True, "disabled": set(), "opted_out": set(), "panel": "/a1b2c3d4_household_demo"}
        self.cat = demo_catalogue(self.state)
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("CREATE TABLE items (text TEXT, by TEXT)")

    def call(self, data):
        msg = app_bus.Message(envelope("assist.tool.call", data))
        try:
            return self.cat.on_call(msg, self.conn)
        except app_bus.Nack as n:
            return n

    def assertNack(self, res, reason, detail):
        self.assertIsInstance(res, app_bus.Nack, res)
        self.assertEqual((res.reason, res.detail), (reason, detail))

    def test_list_describes_every_tool(self):
        out = self.cat.on_list(app_bus.Message(envelope("assist.tools.list", {})), self.conn)
        self.assertTrue(out["on"])
        names = [t["name"] for t in out["tools"]]
        self.assertEqual(names, ["demo.things", "demo.items.add", "demo.big", "demo.refuse"])
        t = out["tools"][0]
        self.assertEqual(t["args"]["when"], {"type": "enum", "required": True, "what": "which things",
                                             "values": ["today", "all"]})
        self.assertEqual(t["args"]["count"], {"type": "number", "what": "how many", "min": 1, "max": 100})
        self.assertEqual((t["acts"], t["scope"], t["examples"]), (False, "person", ["What's on today?"]))
        self.assertTrue(out["tools"][1]["acts"])
        self.cat.check()
        self.state["on"] = False
        self.assertFalse(self.cat.on_list(app_bus.Message(envelope("assist.tools.list", {})), self.conn)["on"])

    def test_answer(self):
        res = self.call({"tool": "demo.things", "args": {"when": "today"}, "requested_by": "u_ann",
                         "question": "01J9ABC"})
        self.assertEqual(res["question"], "01J9ABC")
        self.assertEqual(res["tool"], "demo.things")
        self.assertEqual(res["text"], "2 things for Ann.")
        self.assertEqual(res["items"][0], {"title": "thing 0", "who": "Ann", "nested": "{'no': 1}"})
        self.assertEqual(res["links"], [{"label": "Today in Demo", "panel": "/a1b2c3d4_household_demo",
                                         "target": "/today"}])
        self.assertFalse(res["more"])

    def test_link_without_a_known_page(self):
        for panel in (None, "/config", "//evil", "/app/a1b2c3d4_household_demo"):
            self.state["panel"] = panel
            res = self.call({"tool": "demo.things", "args": {"when": "today"}, "requested_by": "u_ann"})
            self.assertEqual(res["links"], [{"label": "Today in Demo", "target": "/today"}], panel)

    def test_links_must_be_the_apps_own_routes(self):
        for target in ("/config", "//x", "/today/../admin", "https://x", "/items/ABC"):
            with self.assertRaises(ValueError):
                self.cat.link("x", target)

    def test_arguments_are_checked(self):
        ok = {"tool": "demo.things", "requested_by": "u_ann"}
        bad = [({}, "when"), ({"when": "tomorrow"}, "when"), ({"when": "today", "count": 0}, "count"),
               ({"when": "today", "count": "3"}, "count"), ({"when": "today", "count": True}, "count"),
               ({"when": "today", "day": "2026-02-30"}, "day"), ({"when": "today", "day": "26-1-1"}, "day"),
               ({"when": "today", "month": "2026-13"}, "month"), ({"when": "today", "word": "x" * 11}, "word"),
               ({"when": "today", "word": "   "}, "word"), ({"when": "today", "flag": "yes"}, "flag")]
        for args, name in bad:
            self.assertNack(self.call(dict(ok, args=args)), "invalid", name)
        self.assertNack(self.call(dict(ok, args=[1])), "invalid", "args")
        good = self.call(dict(ok, args={"when": "all", "count": 3, "day": "2026-02-28", "month": "2026-12",
                                        "word": "  two   words ", "flag": False, "unknown": "ignored"}))
        self.assertEqual(len(good["items"]), 3)

    def test_refusals(self):
        base = {"tool": "demo.things", "args": {"when": "today"}, "requested_by": "u_ann"}
        self.assertNack(self.call(dict(base, tool="demo.nope")), "not_found", "tool")
        self.assertNack(self.call(dict(base, tool="vault.passwords")), "not_found", "tool")
        self.assertNack(self.call(dict(base, requested_by="")), "invalid", "requested_by")
        self.assertNack(self.call(dict(base, requested_by="u_nobody")), "not_allowed", "no_access")
        self.assertNack(self.call(dict(base, question="bad id!")), "invalid", "question")
        self.state["disabled"].add("u_ann")
        self.assertNack(self.call(base), "not_allowed", "no_access")
        self.state["disabled"].clear()
        self.state["opted_out"].add("u_ann")
        self.assertNack(self.call(base), "not_allowed", "person_off")
        self.state["opted_out"].clear()
        self.state["on"] = False
        self.assertNack(self.call(base), "not_allowed", "off")
        self.state["on"] = True
        self.assertNack(self.call(dict(base, tool="demo.refuse", args={})), "not_found", "thing")

    def test_children_only_get_tools_open_to_them(self):
        self.assertIn("text", self.call({"tool": "demo.things", "args": {"when": "today"}, "requested_by": "u_kid"}))
        self.assertNack(self.call({"tool": "demo.big", "requested_by": "u_kid"}), "not_allowed", "child")

    def test_acting_needs_the_tap(self):
        base = {"tool": "demo.items.add", "args": {"text": "milk"}, "requested_by": "u_ann"}
        self.assertNack(self.call(base), "not_allowed", "confirm")
        self.assertNack(self.call(dict(base, confirm="true")), "not_allowed", "confirm")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM items").fetchone()[0], 0)
        res = self.call(dict(base, confirm=True, confirmed_at="2026-10-07T10:00:00Z"))
        self.assertEqual(res["text"], "Added milk.")
        self.assertEqual(self.conn.execute("SELECT text, by FROM items").fetchall(), [("milk", "u_ann")])

    def test_results_fit_in_6_kb(self):
        res = self.call({"tool": "demo.big", "requested_by": "u_ann"})
        self.assertLessEqual(len(json.dumps(res, separators=(",", ":"), ensure_ascii=False).encode()), 6 * 1024)
        self.assertTrue(res["more"])
        self.assertLess(len(res["items"]), 50)
        self.assertEqual(res["items"][0]["n"], 0)                     # the first rows are kept
        res = self.call({"tool": "demo.big", "args": {"text": True}, "requested_by": "u_ann"})
        size = len(json.dumps(res, separators=(",", ":"), ensure_ascii=False).encode())
        self.assertLessEqual(size, 6 * 1024)
        self.assertTrue(res["text"].endswith("…"))
        self.assertTrue(res["more"])

    def test_fit_shortens_text_last(self):
        res = assist_tools.fit({"text": "ü" * 4000, "items": [{"a": "b" * 100}] * 10, "links": [], "more": False})
        self.assertEqual(res["items"], [])
        self.assertLessEqual(len(json.dumps(res, separators=(",", ":"), ensure_ascii=False).encode()), 6 * 1024)
        self.assertTrue(res["text"].endswith("…") and res["more"])
        small = {"text": "ok", "items": [{"a": 1}], "links": [], "more": False}
        self.assertEqual(assist_tools.fit(dict(small)), small)

    def test_bad_catalogues_are_refused(self):
        cat = assist_tools.Catalogue("demo", actor=lambda c, u: None, enabled=lambda c: True)
        for name in ("other.thing", "demo", "Demo.thing", "demo.a.b.c"):
            with self.assertRaises(ValueError, msg=name):
                cat.tool(name, "x")
        with self.assertRaises(ValueError):
            cat.tool("demo.x", "x", args={f"a{i}": Arg("string") for i in range(9)})
        with self.assertRaises(ValueError):
            cat.tool("demo.x", "x", scope="everyone")
        with self.assertRaises(ValueError):
            cat.tool("demo.x", "")
        with self.assertRaises(ValueError):
            Arg("list")
        with self.assertRaises(ValueError):
            Arg("enum")


class SidebarPageTests(unittest.TestCase):
    def test_from_the_supervisors_answer(self):
        page = assist_tools.sidebar_page
        self.assertEqual(page("family_tree", info={"slug": "a1b2c3d4_family_tree", "ingress_panel": True}),
                         "/a1b2c3d4_family_tree")
        self.assertEqual(page("family_tree", info={"slug": "local_family_tree"}), "/local_family_tree")
        self.assertIsNone(page("family_tree", info={"slug": "a1b2c3d4_family_tree", "ingress_panel": False}))
        for bad in ("a1b2c3d4_household_chat", "x_family_tree", "../family_tree", "A1B2C3D4_family_tree"):
            self.assertIsNone(page("family_tree", info={"slug": bad}), bad)

    def test_from_the_host_name(self):
        page = assist_tools.sidebar_page
        self.assertEqual(page("splitpot", hostname="a1b2c3d4-splitpot"), "/a1b2c3d4_splitpot")
        self.assertEqual(page("family_tree", hostname="local-family-tree"), "/local_family_tree")
        self.assertIsNone(page("family_tree", hostname="somewhere-else"))
        self.assertIsNone(page("family_tree", hostname=""))
        # no token: the Supervisor isn't asked
        self.assertEqual(page("splitpot", token="", hostname="local-splitpot"), "/local_splitpot")


class OverTheBusTests(unittest.TestCase):
    """Both kinds between two real app buses, delivered by calling each other's receive()."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.state = {"on": True, "disabled": set(), "opted_out": set(), "panel": None}
        self.app = app_bus.AppBus()
        self.asst = app_bus.AppBus()
        demo_catalogue(self.state).install(self.app)

        def db(name):
            path = os.path.join(self.tmp, name)

            def connect():
                c = sqlite3.connect(path, isolation_level=None)
                c.execute("CREATE TABLE IF NOT EXISTS items (text TEXT, by TEXT)")
                return c
            return connect
        self.answers = []
        for kind in ("assist.tools.list", "assist.tool.call"):
            self.asst.on_reply(kind, lambda msg, conn: self.answers.append(msg))
        self.app.start("household_demo", "Household Demo", "1.0.0", db=db("app.db"), token="",
                       outbox_thread=False, post=lambda env: self.asst.receive(env) or True)
        self.asst.start("household_assistant", "Household Assistant", "0.1.0", db=db("asst.db"), token="",
                        outbox_thread=False, post=lambda env: self.app.receive(env) or True)
        self.app._send_hello("*")

    def tearDown(self):
        self.app.stop()
        self.asst.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_hello_offers_both_kinds_and_calls_are_answered(self):
        self.assertTrue(self.asst.available("household_demo", "assist.tools.list"))
        self.assertTrue(self.asst.available("household_demo", "assist.tool.call"))
        self.asst.send("household_demo", "assist.tools.list", {}, expires_in=timedelta(minutes=2))
        self.asst.run_outbox_once()
        self.assertEqual(self.answers[-1].kind, "ack")
        self.assertEqual(len(self.answers[-1].result["tools"]), 4)
        self.asst.send("household_demo", "assist.tool.call",
                       {"tool": "demo.things", "args": {"when": "today"}, "requested_by": "u_ann", "question": "q1"},
                       expires_in=timedelta(seconds=20))
        self.asst.run_outbox_once()
        self.assertEqual(self.answers[-1].result["text"], "2 things for Ann.")
        self.asst.send("household_demo", "assist.tool.call",
                       {"tool": "demo.items.add", "args": {"text": "eggs"}, "requested_by": "u_ann"},
                       expires_in=timedelta(seconds=20))
        self.asst.run_outbox_once()
        self.assertEqual((self.answers[-1].kind, self.answers[-1].reason), ("nack", "not_allowed"))


if __name__ == "__main__":
    unittest.main()
