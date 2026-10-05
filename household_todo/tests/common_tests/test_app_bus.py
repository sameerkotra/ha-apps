# Shared file: edit common/tests/test_app_bus.py and run tools/sync_common.py; don't edit this copy. sha256=868b468e05b5a93fcd0c8ec34ded055e959cef6baecd5102c87f99147c43e0b4
"""The shared app bus (common/python/app_bus.py, ha_ws.py) against a fake Home Assistant event bus
(common/tests/fake_ha_bus.py), with two or three fake apps in this process. APP_MESSAGES_SPEC.md §3–§6, §9.

Shared: common/tests/test_app_bus.py, run by the repository's tests (tests/test_app_bus.py imports it) and
copied into every app that uses the bus (tests/common_tests/), where it tests the app's own copies.

Standard library only. Time is an injected clock, so retries and expiry don't sleep; only delivery through
the fake WebSocket is waited for (milliseconds)."""
import contextlib
import json
import logging
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone

try:                                            # in an app: its own copies
    from app.common import app_bus, ha_ws
    from common_tests.fake_ha_bus import FakeHABus
except ImportError:                             # in the repository: common/ itself
    ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    from common.python import app_bus, ha_ws  # noqa: E402
    from common.tests.fake_ha_bus import FakeHABus  # noqa: E402

TOKEN = "bus-test-token"
FAST_WS = {"retry_first": 0.05, "retry_max": 0.2, "ping_every": 1, "read_timeout": 5}


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)
        self._lock = threading.Lock()

    def __call__(self):
        with self._lock:
            return self.now

    def advance(self, **kw):
        with self._lock:
            self.now += timedelta(**kw)


def wait_until(cond, timeout=5.0, what="condition"):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {what}")


class BusCase(unittest.TestCase):
    def setUp(self):
        logging.getLogger("app_bus").setLevel(logging.CRITICAL)
        logging.getLogger("ha_ws").setLevel(logging.CRITICAL)
        self.tmp = tempfile.mkdtemp(prefix="bus-test-")
        self.fake = FakeHABus(token=TOKEN)
        self.api = self.fake.start()
        self.clock = Clock()
        self.buses = []

    def tearDown(self):
        for b in self.buses:
            b.stop()
        self.fake.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)
        logging.getLogger("app_bus").setLevel(logging.NOTSET)
        logging.getLogger("ha_ws").setLevel(logging.NOTSET)

    # -- helpers -----------------------------------------------------------------
    def db_path(self, slug):
        return os.path.join(self.tmp, f"{slug}.db")

    def conn_factory(self, slug, style="plain"):
        path = self.db_path(slug)
        if style == "plain":
            return lambda: sqlite3.connect(path, timeout=10)

        @contextlib.contextmanager
        def get_conn():                    # like the apps' db.get_conn
            conn = sqlite3.connect(path, timeout=10)
            conn.row_factory = sqlite3.Row
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
        return get_conn

    def make_bus(self, slug, handlers=None, can=None, wants=(), start=True, style="plain", **opts):
        bus = app_bus.AppBus(api_base=self.api, ws_url=self.fake.ws_url, token=TOKEN, clock=self.clock,
                             outbox_thread=opts.pop("outbox_thread", False), ws_options=FAST_WS, **opts)
        for kind, fn in (handlers or {}).items():
            bus.handler(kind)(fn)
        self.buses.append(bus)
        if start:
            self.start_bus(bus, slug, can, wants, style)
        return bus

    def start_bus(self, bus, slug, can=None, wants=(), style="plain"):
        bus.start(slug, slug.replace("_", " ").title(), "1.0.0", can=can, wants=wants,
                  db=self.conn_factory(slug, style))
        wait_until(lambda: bus.connected, what=f"{slug} connected")

    def rows(self, slug, sql, args=()):
        conn = sqlite3.connect(self.db_path(slug))
        try:
            return conn.execute(sql, args).fetchall()
        finally:
            conn.close()

    def outbox(self, slug, mid):
        r = self.rows(slug, "SELECT state, attempts, result, next_try FROM bus_outbox WHERE id = ?", (mid,))
        return r[0] if r else None

    def state(self, slug, mid):
        r = self.outbox(slug, mid)
        return r[0] if r else None

    def answers(self, to, kind=None):
        return [e for e in self.fake.events() if isinstance(e, dict) and e.get("to") == to and e.get("kind") in ("ack", "nack")
                and (kind is None or e.get("kind") == kind)]

    def pair(self, b_handlers=None, a_can=(), b_can=None, **a_opts):
        """Arcade (sender) and Chat (receiver), each knowing the other."""
        a = self.make_bus("household_arcade", can=list(a_can), wants=["poll.closed"], **a_opts)
        b = self.make_bus("household_chat", handlers=b_handlers or {}, can=b_can)
        for kind in (b.can or []):
            wait_until(lambda k=kind: a.available("household_chat", k), what="arcade sees chat")
        wait_until(lambda: self.rows("household_chat", "SELECT 1 FROM bus_apps WHERE slug='household_arcade'"),
                   what="chat sees arcade")
        return a, b

    def envelope(self, frm, to, kind, data=None, /, **over):
        now = self.clock()
        env = {"id": app_bus.new_ulid(now), "v": 1, "from": frm, "to": to, "kind": kind, "kv": 1,
               "reply_to": None, "ref": None, "sent": now.isoformat(),
               "expires": (now + timedelta(hours=1)).isoformat(), "data": data or {}}
        env.update(over)
        return env


POLL = {"requested_by": "u1", "people": ["u2"], "chat": "direct", "question": "When?",
        "options": [{"id": "a", "label": "7 pm"}, {"id": "b", "label": "8 pm"}], "closes_at": "x",
        "close_when_all_voted": True, "multiple": False, "badge": "Arcade"}


class DeliveryTest(BusCase):
    def test_delivery_ack_and_reply_callback(self):
        calls, replies = [], []

        def on_poll(msg, conn):
            calls.append((msg.id, msg.from_app, msg.kind, msg.kv, msg.ref, msg.data["question"]))
            conn.execute("CREATE TABLE IF NOT EXISTS polls (id TEXT)")
            conn.execute("INSERT INTO polls VALUES ('p1')")
            return {"poll_id": "p1", "chat_id": "c1"}

        a, b = self.pair({"poll.create": on_poll})
        a.on_reply("poll.create", lambda m, c: replies.append((m.kind, m.answers, m.reply_to, m.ref, m.result)))
        mid = a.send("household_chat", "poll.create", POLL, ref="duel:1")
        self.assertEqual(self.state("household_arcade", mid), "pending")
        a.run_outbox_once()
        wait_until(lambda: self.state("household_arcade", mid) == "acked", what="ack")
        self.assertEqual(calls, [(mid, "household_arcade", "poll.create", 1, "duel:1", "When?")])
        self.assertEqual(replies, [("ack", "poll.create", mid, "duel:1", {"poll_id": "p1", "chat_id": "c1"})])
        st, attempts, result, _ = self.outbox("household_arcade", mid)
        self.assertEqual((attempts, json.loads(result)), (1, {"result": {"poll_id": "p1", "chat_id": "c1"}}))
        self.assertEqual(self.rows("household_chat", "SELECT id FROM polls"), [("p1",)])
        self.assertEqual(self.rows("household_chat", "SELECT from_app, kind FROM bus_seen WHERE id = ?", (mid,)),
                         [("household_arcade", "poll.create")])
        # the envelope as fired: every field of §3, compact, the event type, the token
        sent = [e for e in self.fake.events() if e["id"] == mid][0]
        self.assertEqual(set(sent), {"id", "v", "from", "to", "kind", "kv", "reply_to", "ref", "sent", "expires",
                                     "data"})
        self.assertEqual((sent["v"], sent["to"], sent["reply_to"]), (1, "household_chat", None))
        posts = [r for r in self.fake.requests if r[1] == "/api/events/household_apps"]
        self.assertTrue(posts and all(r[3] == f"Bearer {TOKEN}" for r in posts))
        ack = self.answers("household_arcade", "ack")[0]
        self.assertEqual((ack["reply_to"], ack["ref"], ack["from"]), (mid, "duel:1", "household_chat"))

    def test_after_commit_runs_after_the_commit_and_only_on_ack(self):
        seen = []

        def on_poll(msg, conn):
            conn.execute("CREATE TABLE IF NOT EXISTS polls (id TEXT)")
            conn.execute("INSERT INTO polls VALUES (?)", (msg.data["question"],))
            # another connection sees the row only once it's committed
            msg.after_commit(lambda: seen.append(self.rows("household_chat", "SELECT id FROM polls")))
            msg.after_commit(lambda: 1 / 0)                     # a failing step is logged, nothing else
            msg.after_commit(lambda: seen.append("second"))
            if msg.data["question"] == "no":
                raise app_bus.Nack("not_allowed")
            return {"ok": True}

        a, b = self.pair({"poll.create": on_poll})
        mid = a.send("household_chat", "poll.create", POLL)
        a.run_outbox_once()
        wait_until(lambda: self.state("household_arcade", mid) == "acked")
        self.assertEqual(seen, [[("When?",)], "second"])
        mid = a.send("household_chat", "poll.create", dict(POLL, question="no"))
        a.run_outbox_once()
        wait_until(lambda: self.state("household_arcade", mid) == "nacked")
        self.assertEqual(seen, [[("When?",)], "second"])          # not run for a nack

    def test_outbox_thread_sends_at_once(self):
        got = threading.Event()
        a, b = self.pair({"poll.create": lambda m, c: got.set()}, outbox_thread=True)
        mid = a.send("household_chat", "poll.create", POLL)
        self.assertTrue(got.wait(5))
        wait_until(lambda: self.state("household_arcade", mid) == "acked")

    def test_get_conn_style_factory(self):
        a = self.make_bus("household_arcade", style="ctx")
        b = self.make_bus("household_chat", handlers={"poll.create": lambda m, c: {"ok": True}}, style="ctx")
        wait_until(lambda: a.available("household_chat", "poll.create"))
        mid = a.send("household_chat", "poll.create", POLL)
        a.run_outbox_once()
        wait_until(lambda: self.state("household_arcade", mid) == "acked")

    def test_logs_never_show_data(self):
        a, b = self.pair({"poll.create": lambda m, c: {"poll_id": "SECRET-RESULT"}})
        logger = logging.getLogger("app_bus")
        logger.setLevel(logging.INFO)
        with self.assertLogs("app_bus", "INFO") as logs:
            mid = a.send("household_chat", "poll.create", dict(POLL, question="SECRET-QUESTION"))
            a.run_outbox_once()
            wait_until(lambda: self.state("household_arcade", mid) == "acked")
        text = "\n".join(logs.output)
        self.assertIn(mid, text)
        self.assertIn("kind=poll.create", text)
        self.assertNotIn("SECRET", text)


class OutboxTest(BusCase):
    def test_retry_schedule(self):
        self.assertEqual([app_bus.AppBus.retry_delay(n) for n in range(1, 7)], [30, 120, 600, 1800, 1800, 1800])

    def test_receiver_down_then_up(self):
        calls = []
        a, b = self.pair({"poll.create": lambda m, c: calls.append(m.id)})
        b.stop()
        mid = a.send("household_chat", "poll.create", POLL)
        t0 = self.clock()
        a.run_outbox_once()                                   # attempt 1: nobody listening
        self.assertEqual(self.outbox("household_arcade", mid)[:2], ("pending", 1))
        self.assertEqual(a.run_outbox_once()["sent"], 0)        # not due yet
        for attempt, wait in ((2, 30), (3, 120), (4, 600), (5, 1800)):
            self.clock.advance(seconds=wait - 1)
            self.assertEqual(a.run_outbox_once()["sent"], 0, f"too early for attempt {attempt}")
            self.clock.advance(seconds=1)
            a.run_outbox_once()
            self.assertEqual(self.outbox("household_arcade", mid)[:2], ("pending", attempt))
        self.assertEqual(self.outbox("household_arcade", mid)[3],
                         app_bus._iso(t0 + timedelta(seconds=30 + 120 + 600 + 1800 + 1800)))
        self.assertEqual(calls, [])
        # Chat comes back (same database); the next re-send gets through, the action happens once
        b2 = self.make_bus("household_chat", handlers={"poll.create": lambda m, c: calls.append(m.id)})
        self.clock.advance(seconds=1800)
        a.run_outbox_once()
        wait_until(lambda: self.state("household_arcade", mid) == "acked")
        self.clock.advance(seconds=1800)
        self.assertEqual(a.run_outbox_once()["sent"], 0)        # answered: no more re-sends
        self.assertEqual(calls, [mid])

    def test_restart_resends_outbox(self):
        calls = []
        a, b = self.pair({"poll.create": lambda m, c: calls.append(m.id)})
        b.stop()
        mid = a.send("household_chat", "poll.create", POLL)
        a.run_outbox_once()
        a.stop()                                               # both down; the message waits in Arcade's outbox
        self.make_bus("household_chat", handlers={"poll.create": lambda m, c: calls.append(m.id)})
        self.make_bus("household_arcade")                       # start-up re-sends at once, not after 30 s
        wait_until(lambda: self.state("household_arcade", mid) == "acked")
        self.assertEqual(calls, [mid])

    def test_sender_expiry_tells_the_feature(self):
        replies = []
        a, b = self.pair({"poll.create": lambda m, c: None})
        a.on_reply("duel:", lambda m, c: replies.append((m.kind, m.reason, m.local, m.answers, m.ref, m.reply_to)))
        b.stop()
        mid = a.send("household_chat", "poll.create", POLL, ref="duel:9", expires_in=timedelta(minutes=5))
        a.run_outbox_once()
        self.clock.advance(minutes=5)
        stats = a.run_outbox_once()
        self.assertEqual((stats["expired"], stats["sent"]), (1, 0))
        self.assertEqual(self.state("household_arcade", mid), "expired")
        self.assertEqual(replies, [("nack", "expired", True, "poll.create", "duel:9", mid)])
        a.run_outbox_once()
        self.assertEqual(len(replies), 1)

    def test_rate_limit_30_a_minute_per_app(self):
        calls = []
        a, b = self.pair({"poll.create": lambda m, c: calls.append(m.id)})
        ids = [a.send("household_chat", "poll.create", {"n": i}) for i in range(35)]
        stats = a.run_outbox_once()
        self.assertEqual((stats["sent"], stats["limited"]), (30, 5))
        wait_until(lambda: len(self.rows("household_arcade", "SELECT 1 FROM bus_outbox WHERE state='acked'")) == 30)
        self.assertEqual(len(calls), 30)
        self.clock.advance(seconds=61)
        self.assertEqual(a.run_outbox_once()["sent"], 5)
        wait_until(lambda: all(self.state("household_arcade", i) == "acked" for i in ids))
        self.assertEqual(sorted(calls), sorted(ids))

    def test_old_rows_pruned(self):
        a, b = self.pair({"poll.create": lambda m, c: None})
        mid = a.send("household_chat", "poll.create", POLL)
        a.run_outbox_once()
        wait_until(lambda: self.state("household_arcade", mid) == "acked")
        self.clock.advance(days=7, seconds=1)
        a.run_outbox_once()
        b.run_outbox_once()
        self.assertIsNone(self.outbox("household_arcade", mid))
        self.assertEqual(self.rows("household_chat", "SELECT * FROM bus_seen"), [])


class DuplicateTest(BusCase):
    def test_same_message_twice_acts_once(self):
        calls = []
        a, b = self.pair({"poll.create": lambda m, c: calls.append(m.id) or {"poll_id": "p"}})
        env = self.envelope("household_arcade", "household_chat", "poll.create", POLL)
        self.fake.fire("household_apps", env)
        self.fake.fire("household_apps", env)
        wait_until(lambda: len(self.answers("household_arcade")) == 2)
        self.assertEqual(calls, [env["id"]])
        acks = self.answers("household_arcade")
        self.assertEqual([x["kind"] for x in acks], ["ack", "ack"])
        self.assertEqual([x["data"] for x in acks], [{"result": {"poll_id": "p"}}] * 2)

    def test_lost_ack_resent_and_reacked(self):
        calls, replies = [], []
        a, b = self.pair({"poll.create": lambda m, c: calls.append(m.id)})
        a.on_reply("poll.create", lambda m, c: replies.append(m.kind))
        lose = {"on": True}
        self.fake.drop = lambda t, d: lose["on"] and d.get("kind") == "ack"
        mid = a.send("household_chat", "poll.create", POLL)
        a.run_outbox_once()
        wait_until(lambda: calls == [mid])
        wait_until(lambda: len(self.answers("household_arcade")) == 1)   # fired, but lost
        self.assertEqual(self.state("household_arcade", mid), "pending")
        lose["on"] = False
        self.clock.advance(seconds=30)
        a.run_outbox_once()
        wait_until(lambda: self.state("household_arcade", mid) == "acked")
        self.assertEqual(calls, [mid])
        self.assertEqual(replies, ["ack"])
        # a late duplicate ack changes nothing and calls nobody
        self.fake.fire("household_apps", self.answers("household_arcade")[-1])
        time.sleep(0.1)
        self.assertEqual(replies, ["ack"])

    def test_duplicate_of_nacked_gets_same_nack(self):
        def refuse(m, c):
            raise app_bus.Nack("not_allowed", "not a member")
        a, b = self.pair({"poll.create": refuse})
        env = self.envelope("household_arcade", "household_chat", "poll.create", POLL)
        self.fake.fire("household_apps", env)
        self.fake.fire("household_apps", env)
        wait_until(lambda: len(self.answers("household_arcade")) == 2)
        self.assertEqual([x["data"] for x in self.answers("household_arcade")],
                         [{"reason": "not_allowed", "detail": "not a member"}] * 2)


class NackTest(BusCase):
    def nack_for(self, b_handlers, env_over=None, kind="poll.create", data=None):
        a, b = self.pair(b_handlers)
        env = self.envelope("household_arcade", "household_chat", kind, data if data is not None else POLL,
                            **(env_over or {}))
        self.fake.fire("household_apps", env)
        wait_until(lambda: [x for x in self.answers("household_arcade") if x["reply_to"] == env["id"]])
        ans = [x for x in self.answers("household_arcade") if x["reply_to"] == env["id"]][0]
        return ans, env

    def test_handler_nacks_roll_back_its_writes(self):
        for reason in ("not_found", "not_allowed", "invalid"):
            with self.subTest(reason):
                def h(m, c, reason=reason):
                    c.execute("CREATE TABLE IF NOT EXISTS polls (id TEXT)")
                    c.execute("INSERT INTO polls VALUES ('x')")
                    return app_bus.Nack(reason)
                ans, env = self.nack_for({"poll.create": h})
                self.assertEqual((ans["kind"], ans["data"]), ("nack", {"reason": reason}))
                self.assertEqual(self.rows("household_chat", "SELECT name FROM sqlite_master WHERE name='polls'"), [])
                self.assertEqual(len(self.rows("household_chat", "SELECT 1 FROM bus_seen WHERE id=?", (env["id"],))), 1)
                self.tearDown()
                self.setUp()

    def test_unsupported_kind(self):
        ans, env = self.nack_for({"poll.create": lambda m, c: None}, kind="todo.create")
        self.assertEqual(ans["data"], {"reason": "unsupported_kind"})

    def test_unsupported_version(self):
        ans, env = self.nack_for({"poll.create": lambda m, c: None}, {"kv": 2})
        self.assertEqual(ans["data"], {"reason": "unsupported_version"})
        ans, env = self.nack_for({"poll.create": lambda m, c: None}, {"v": 2})
        self.assertEqual(ans["data"], {"reason": "unsupported_version", "detail": "envelope"})

    def test_handler_with_more_versions(self):
        seen = []
        a, b = self.pair()
        b.handler("poll.create", versions=(1, 2))(lambda m, c: seen.append(m.kv))
        env = self.envelope("household_arcade", "household_chat", "poll.create", POLL, kv=2)
        self.fake.fire("household_apps", env)
        wait_until(lambda: self.answers("household_arcade"))
        self.assertEqual((seen, self.answers("household_arcade")[0]["kind"]), ([2], "ack"))

    def test_invalid_envelope(self):
        for field, value in (("kind", "Poll Create"), ("kv", "1"), ("sent", "yesterday"), ("expires", None),
                             ("data", []), ("ref", "x" * 201), ("reply_to", "bad id!")):
            with self.subTest(field):
                ans, env = self.nack_for({"poll.create": lambda m, c: None}, {field: value})
                self.assertEqual(ans["data"], {"reason": "invalid", "detail": field})
                self.tearDown()
                self.setUp()

    def test_receiver_expiry(self):
        calls = []
        past = (self.clock() - timedelta(seconds=1)).isoformat()
        ans, env = self.nack_for({"poll.create": lambda m, c: calls.append(1)}, {"expires": past})
        self.assertEqual(ans["data"], {"reason": "expired"})
        self.assertEqual(calls, [])

    def test_busy_is_retried_not_remembered(self):
        state = {"busy": True}
        calls = []

        def h(m, c):
            if state["busy"]:
                raise app_bus.Nack("busy")
            calls.append(m.id)
        a, b = self.pair({"poll.create": h})
        mid = a.send("household_chat", "poll.create", POLL)
        a.run_outbox_once()
        wait_until(lambda: self.answers("household_arcade", "nack"))
        self.assertEqual(self.answers("household_arcade", "nack")[0]["data"], {"reason": "busy"})
        time.sleep(0.05)
        self.assertEqual(self.state("household_arcade", mid), "pending")   # sender keeps trying
        self.assertEqual(self.rows("household_chat", "SELECT * FROM bus_seen"), [])
        state["busy"] = False
        self.clock.advance(seconds=30)
        a.run_outbox_once()
        wait_until(lambda: self.state("household_arcade", mid) == "acked")
        self.assertEqual(calls, [mid])

    def test_receive_limit_answers_busy(self):
        a, b = self.pair({"poll.create": lambda m, c: None})
        b.configure(receive_limit=3)
        envs = [self.envelope("household_arcade", "household_chat", "poll.create", {"n": i}) for i in range(5)]
        for e in envs:
            self.fake.fire("household_apps", e)
        wait_until(lambda: len(self.answers("household_arcade")) == 5)
        kinds = [x["kind"] for x in self.answers("household_arcade")]
        self.assertEqual(kinds, ["ack"] * 3 + ["nack"] * 2)
        self.assertEqual(self.answers("household_arcade", "nack")[0]["data"]["reason"], "busy")

    def test_unaddressed_and_own_messages_ignored(self):
        calls = []
        a, b = self.pair({"poll.create": lambda m, c: calls.append(1)})
        for env in (self.envelope("household_arcade", "household_todo", "poll.create", POLL),
                    self.envelope("household_arcade", "*", "poll.create", POLL),
                    self.envelope("household_chat", "household_chat", "poll.create", POLL),
                    self.envelope("household_arcade", "household_chat", "poll.create", POLL, id=None),
                    self.envelope("Bad Slug", "household_chat", "poll.create", POLL)):
            self.fake.fire("household_apps", env)
        self.fake.fire("household_apps", "not a dict")
        time.sleep(0.15)
        self.assertEqual((calls, self.answers("household_arcade")), ([], []))


class TransactionTest(BusCase):
    def test_failing_handler_acks_nothing_and_is_retried(self):
        state = {"fail": True}
        calls = []

        def h(m, c):
            c.execute("CREATE TABLE IF NOT EXISTS polls (id TEXT)")
            c.execute("INSERT INTO polls VALUES (?)", (m.id,))
            calls.append(m.id)
            if state["fail"]:
                raise RuntimeError("crash halfway")
            return {"poll_id": "p"}
        a, b = self.pair({"poll.create": h})
        mid = a.send("household_chat", "poll.create", POLL)
        a.run_outbox_once()
        wait_until(lambda: len(calls) == 1)
        time.sleep(0.1)
        self.assertEqual(self.answers("household_arcade"), [])            # no ack, no nack
        self.assertEqual(self.rows("household_chat", "SELECT * FROM bus_seen"), [])
        self.assertEqual(self.rows("household_chat", "SELECT name FROM sqlite_master WHERE name='polls'"), [])
        self.assertEqual(self.state("household_arcade", mid), "pending")
        state["fail"] = False
        self.clock.advance(seconds=30)
        a.run_outbox_once()
        wait_until(lambda: self.state("household_arcade", mid) == "acked")
        self.assertEqual(self.rows("household_chat", "SELECT id FROM polls"), [(mid,)])

    def test_bad_handler_return_is_a_failure(self):
        a, b = self.pair({"poll.create": lambda m, c: "ok"})
        a.send("household_chat", "poll.create", POLL)
        a.run_outbox_once()
        time.sleep(0.15)
        self.assertEqual(self.answers("household_arcade"), [])

    def test_too_large_answer_is_nacked(self):
        a, b = self.pair({"poll.create": lambda m, c: {"blob": "x" * 9000}})
        mid = a.send("household_chat", "poll.create", POLL)
        a.run_outbox_once()
        wait_until(lambda: self.state("household_arcade", mid) == "nacked")
        self.assertEqual(json.loads(self.outbox("household_arcade", mid)[2]),
                         {"reason": "invalid", "detail": "answer too large"})


class ReplyRoutingTest(BusCase):
    def test_answers_reach_matching_callbacks(self):
        got = {"kind": [], "ref": [], "other": [], "closed": []}
        a, b = self.pair({"poll.create": lambda m, c: {"poll_id": "p1"}})
        a.on_reply("poll.create", lambda m, c: got["kind"].append(m.kind))
        a.on_reply("duel:", lambda m, c: got["ref"].append((m.kind, m.ref)))
        a.on_reply("todo:", lambda m, c: got["other"].append(m.kind))
        a.on_reply("poll.closed", lambda m, c: got["closed"].append((m.reply_to, m.ref, m.data["winner"])))
        mid = a.send("household_chat", "poll.create", POLL, ref="duel:7")
        a.run_outbox_once()
        wait_until(lambda: self.state("household_arcade", mid) == "acked")
        # Chat answers later with poll.closed (an answer kind Arcade has no handler for)
        cid = b.send("household_arcade", "poll.closed", {"poll_id": "p1", "winner": "a", "votes": {},
                                                          "reason": "time"}, ref="duel:7", reply_to=mid)
        b.run_outbox_once()
        wait_until(lambda: self.state("household_chat", cid) == "acked")
        self.assertEqual(got["kind"], ["ack"])
        self.assertEqual(got["ref"], [("ack", "duel:7"), ("poll.closed", "duel:7")])
        self.assertEqual(got["closed"], [(mid, "duel:7", "a")])
        self.assertEqual(got["other"], [])

    def test_failing_reply_callback_undoes_and_answer_comes_again(self):
        state = {"fail": True}
        seen = []

        def cb(m, c):
            seen.append(m.kind)
            if state["fail"]:
                raise RuntimeError("feature failed")
        a, b = self.pair({"poll.create": lambda m, c: None})
        a.on_reply("poll.create", cb)
        mid = a.send("household_chat", "poll.create", POLL)
        a.run_outbox_once()
        wait_until(lambda: seen == ["ack"])
        time.sleep(0.05)
        self.assertEqual(self.state("household_arcade", mid), "pending")
        state["fail"] = False
        self.clock.advance(seconds=30)
        a.run_outbox_once()                                     # re-sent; Chat re-acks without acting again
        wait_until(lambda: self.state("household_arcade", mid) == "acked")
        self.assertEqual(seen, ["ack", "ack"])

    def test_answer_from_another_app_is_ignored(self):
        replies = []
        a, b = self.pair({"poll.create": lambda m, c: None})
        a.on_reply("poll.create", lambda m, c: replies.append(m))
        b.stop()
        mid = a.send("household_chat", "poll.create", POLL)
        a.run_outbox_once()
        self.fake.fire("household_apps", self.envelope("household_todo", "household_arcade", "ack", {},
                                                       reply_to=mid))
        time.sleep(0.15)
        self.assertEqual((self.state("household_arcade", mid), replies), ("pending", []))

    def test_unsolicited_kind_without_callback_is_unsupported(self):
        a, b = self.pair({"poll.create": lambda m, c: None})
        env = self.envelope("household_chat", "household_arcade", "poll.closed", {"poll_id": "p"}, ref="duel:1")
        self.fake.fire("household_apps", env)
        wait_until(lambda: self.answers("household_chat"))
        self.assertEqual(self.answers("household_chat")[0]["data"], {"reason": "unsupported_kind"})


class SendChecksTest(BusCase):
    def test_size_limit(self):
        a, b = self.pair({"poll.create": lambda m, c: None})
        with self.assertRaises(app_bus.TooLarge):
            a.send("household_chat", "poll.create", {"question": "x" * 8200})
        self.assertEqual(self.rows("household_arcade", "SELECT * FROM bus_outbox"), [])
        # just under the limit is fine; the whole event is measured, multi-byte characters included
        probe = a._envelope("household_chat", "poll.create", {"q": ""})
        room = app_bus.MAX_EVENT_BYTES - app_bus.event_size(probe)
        mid = a.send("household_chat", "poll.create", {"q": "x" * room})
        body = self.rows("household_arcade", "SELECT body FROM bus_outbox WHERE id=?", (mid,))[0][0]
        self.assertLessEqual(len(body.encode()), app_bus.MAX_EVENT_BYTES)
        with self.assertRaises(app_bus.TooLarge):
            a.send("household_chat", "poll.create", {"q": "é" * room})

    def test_not_available_and_bad_arguments(self):
        a, b = self.pair({"poll.create": lambda m, c: None})
        with self.assertRaises(app_bus.NotAvailable):
            a.send("household_chat", "message.post", {})            # Chat can't
        with self.assertRaises(app_bus.NotAvailable):
            a.send("household_docs", "poll.create", {})             # not installed
        for args, kw in ((("*", "poll.create", {}), {}), (("household_arcade", "poll.create", {}), {}),
                         (("household_chat", "ack", {}), {}), (("household_chat", "hello", {}), {}),
                         (("household_chat", "poll.create", []), {}),
                         (("household_chat", "poll.create", {"t": object()}), {}),
                         (("household_chat", "poll.create", {}), {"ref": "r" * 201}),
                         (("household_chat", "poll.create", {}), {"expires_in": timedelta(days=8)}),
                         (("household_chat", "poll.create", {}), {"expires_in": timedelta(0)})):
            with self.subTest(args=args[:2], kw=list(kw)):
                with self.assertRaises((ValueError, app_bus.BusError)):
                    a.send(*args, **kw)
        self.assertEqual(self.rows("household_arcade", "SELECT * FROM bus_outbox"), [])
        with self.assertRaises(app_bus.BusError):
            app_bus.AppBus().send("household_chat", "poll.create", {})
        with self.assertRaises(ValueError):
            app_bus.Nack("nope")

    def test_kind_names(self):
        for kind in ("poll.create", "chat.card", "chat.chats.list", "todo.items.add", "hello"):
            self.assertTrue(app_bus.KIND_RE.match(kind), kind)
        for kind in ("Poll.create", "a.b.c.d", "chat..card", "chat.card.", "1chat.card", "chat card", "x" * 33):
            self.assertFalse(app_bus.KIND_RE.match(kind), kind)
        bus = app_bus.AppBus()
        bus.handler("todo.items.add")(lambda m, c: None)
        with self.assertRaises(ValueError):
            bus.handler("todo.items.add.more")

    def test_ulids(self):
        now = self.clock()
        ids = [app_bus.new_ulid(now + timedelta(milliseconds=i)) for i in range(50)]
        self.assertTrue(all(len(i) == 26 and app_bus.ID_RE.match(i) for i in ids))
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(len(set(app_bus.new_ulid(now) for _ in range(1000))), 1000)

    def test_migrate_is_idempotent(self):
        conn = sqlite3.connect(":memory:")
        app_bus.migrate(conn)
        app_bus.migrate(conn)
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertEqual(names, {"bus_outbox", "bus_seen", "bus_apps"})


class DiscoveryTest(BusCase):
    def test_hello_who_and_availability(self):
        a = self.make_bus("household_arcade", can=[], wants=["poll.closed"])
        b = self.make_bus("household_chat", handlers={"poll.create": lambda m, c: None,
                                                      "message.post": lambda m, c: None})
        self.assertEqual(b.can, ["message.post", "poll.create"])
        wait_until(lambda: a.available("household_chat", "poll.create"))
        wait_until(lambda: self.rows("household_chat", "SELECT 1 FROM bus_apps WHERE slug='household_arcade'"))
        self.assertTrue(a.available("household_chat", "message.post"))
        self.assertFalse(a.available("household_chat", "poll.cancel"))
        self.assertFalse(a.available("household_todo", "poll.create"))
        [chat] = a.apps()
        self.assertEqual((chat["slug"], chat["name"], chat["version"], chat["can"], chat["active"]),
                         ("household_chat", "Household Chat", "1.0.0", ["message.post", "poll.create"], True))
        hello = [e for e in self.fake.events() if e["kind"] == "hello" and e["from"] == "household_chat"][0]
        self.assertEqual(hello["to"], "*")
        self.assertEqual(hello["data"], {"name": "Household Chat", "version": "1.0.0",
                                         "can": ["message.post", "poll.create"], "wants": []})
        self.assertTrue([e for e in self.fake.events() if e["kind"] == "who" and e["from"] == "household_chat"])
        # Arcade answered Chat's hello with its own hello, addressed to Chat
        self.assertTrue([e for e in self.fake.events() if e["kind"] == "hello" and e["from"] == "household_arcade"
                         and e["to"] == "household_chat"])
        # 24 hours without a hello: not available, and sending is refused at once
        self.clock.advance(hours=24, seconds=1)
        self.assertFalse(a.available("household_chat", "poll.create"))
        self.assertFalse(a.apps()[0]["active"])
        with self.assertRaises(app_bus.NotAvailable):
            a.send("household_chat", "poll.create", POLL)
        # the six-hourly hello brings it back
        n = len(self.fake.events())
        b.run_outbox_once()
        wait_until(lambda: a.available("household_chat", "poll.create"))
        self.assertTrue([e for e in self.fake.events()[n:] if e["kind"] == "hello" and e["to"] == "*"])

    def test_hello_answers_rate_limited(self):
        a = self.make_bus("household_arcade")
        b = self.make_bus("household_chat", handlers={"poll.create": lambda m, c: None})
        wait_until(lambda: a.available("household_chat", "poll.create"))

        def answers_to_chat():
            return [e for e in self.fake.events() if e["kind"] == "hello" and e["from"] == "household_arcade"
                    and e["to"] == "household_chat"]
        wait_until(lambda: len(answers_to_chat()) == 1)
        for _ in range(3):                                      # Chat asks again and again within the hour
            self.fake.fire("household_apps", self.envelope("household_chat", "*", "who"))
            self.fake.fire("household_apps", self.envelope("household_chat", "*", "hello",
                                                           {"name": "Household Chat", "version": "1.0.0",
                                                            "can": ["poll.create"], "wants": []}))
        time.sleep(0.15)
        self.assertEqual(len(answers_to_chat()), 1)
        self.clock.advance(hours=1)
        self.fake.fire("household_apps", self.envelope("household_chat", "*", "who"))
        wait_until(lambda: len(answers_to_chat()) == 2)

    def test_three_apps_find_each_other(self):
        a = self.make_bus("household_arcade")
        b = self.make_bus("household_chat", handlers={"poll.create": lambda m, c: None})
        c = self.make_bus("household_todo", handlers={"task.create": lambda m, c: None})
        wait_until(lambda: a.available("household_chat", "poll.create") and a.available("household_todo", "task.create"))
        wait_until(lambda: b.available("household_todo", "task.create") and c.available("household_chat", "poll.create"))
        self.assertEqual([x["slug"] for x in c.apps()], ["household_arcade", "household_chat"])


class LifecycleTest(BusCase):
    def bus_threads(self):
        return [t for t in threading.enumerate() if t.name.startswith(("bus-", "ha-ws"))]

    def test_stop_leaves_no_threads(self):
        before = self.bus_threads()
        a = self.make_bus("household_arcade", outbox_thread=True)
        b = self.make_bus("household_chat", handlers={"poll.create": lambda m, c: None}, outbox_thread=True)
        self.assertGreaterEqual(len(self.bus_threads()), len(before) + 4)
        a.stop()
        b.stop()
        self.assertEqual(self.bus_threads(), before)
        self.assertFalse(a.started or a.connected)
        wait_until(lambda: self.fake.subscribers() == 0, what="fake sees the sockets close")
        with self.assertRaises(app_bus.BusError):
            a.send("household_chat", "poll.create", {})
        self.start_bus(a, "household_arcade")                   # can start again
        a.stop()
        self.assertEqual(self.bus_threads(), before)

    def test_without_token_stays_quiet(self):
        bus = app_bus.AppBus(api_base=self.api, ws_url=self.fake.ws_url, token="", outbox_thread=False)
        self.buses.append(bus)
        bus.start("household_arcade", "Arcade", "1", db=self.conn_factory("household_arcade"))
        self.assertFalse(bus.connected)
        self.assertFalse(bus.available("household_chat", "poll.create"))
        self.assertEqual(self.fake.subscribers(), 0)

    def test_module_level_api_is_the_default_bus(self):
        self.assertIs(app_bus.send.__self__, app_bus.default)
        for name in ("start", "stop", "available", "send", "handler", "on_reply", "run_outbox_once", "apps"):
            self.assertTrue(callable(getattr(app_bus, name)), name)


class SharedConnectionTest(BusCase):
    """The app's own WebSocket (e.g. Chat's notification buttons) carries the bus too: one connection."""

    def test_bus_on_the_apps_connection(self):
        actions, calls = [], []
        ws = ha_ws.HAWebSocket(self.fake.ws_url, TOKEN, name="ha-ws-shared", **FAST_WS)
        ws.subscribe("mobile_app_notification_action", lambda e: actions.append(e["data"]))
        b = app_bus.AppBus(api_base=self.api, ws_url="ws://127.0.0.1:9/never", token=TOKEN, clock=self.clock,
                           outbox_thread=False)
        b.handler("poll.create")(lambda m, c: calls.append(m.id) or {"ok": True})
        self.buses.append(b)
        b.start("household_chat", "Household Chat", "1.0.0", db=self.conn_factory("household_chat"), ws=ws)
        self.assertFalse(b.connected)                           # the app hasn't started its connection yet
        ws.start()
        try:
            wait_until(lambda: b.connected, what="shared connection")
            a = self.make_bus("household_arcade")
            wait_until(lambda: a.available("household_chat", "poll.create"), what="chat's hello")
            self.assertEqual(self.fake.subscribers(), 3)        # the shared socket's two + Arcade's own
            mid = a.send("household_chat", "poll.create", POLL)
            a.run_outbox_once()
            wait_until(lambda: self.state("household_arcade", mid) == "acked")
            self.fake.fire("mobile_app_notification_action", {"action": "X"})
            wait_until(lambda: actions == [{"action": "X"}])
            self.assertEqual(calls, [mid])
            # stopping the bus leaves the app's connection (and its other subscription) running
            b.stop()
            self.assertTrue(ws.running and ws.connected.is_set())
            self.fake.fire("mobile_app_notification_action", {"action": "Y"})
            wait_until(lambda: len(actions) == 2)
            self.fake.fire("household_apps", self.envelope("household_arcade", "household_chat", "poll.create", POLL))
            time.sleep(0.15)
            self.assertEqual(calls, [mid])                      # nobody handles bus messages any more
            self.assertEqual(ws._on_connect, [])
        finally:
            ws.stop()

    def test_already_connected_says_hello_at_once(self):
        ws = ha_ws.HAWebSocket(self.fake.ws_url, TOKEN, name="ha-ws-shared", **FAST_WS)
        ws.start()
        try:
            wait_until(lambda: ws.connected.is_set())
            a = self.make_bus("household_arcade")
            b = app_bus.AppBus(api_base=self.api, token=TOKEN, clock=self.clock, outbox_thread=False)
            b.handler("todo.items.add")(lambda m, c: None)
            self.buses.append(b)
            b.start("household_todo", "Household Todo", "1.0.0", db=self.conn_factory("household_todo"), ws=ws)
            wait_until(lambda: a.available("household_todo", "todo.items.add"), what="hello on start")
            wait_until(lambda: self.fake.subscribers() == 2, what="subscribed on the running connection")
            env = self.envelope("household_arcade", "household_todo", "todo.items.add", {})
            self.fake.fire("household_apps", env)
            wait_until(lambda: [x for x in self.answers("household_arcade") if x["reply_to"] == env["id"]])
        finally:
            ws.stop()

    def test_without_token_the_shared_connection_is_untouched(self):
        ws = ha_ws.HAWebSocket(self.fake.ws_url, TOKEN, name="ha-ws-shared", **FAST_WS)
        b = app_bus.AppBus(api_base=self.api, token="", outbox_thread=False)
        self.buses.append(b)
        b.start("household_chat", "Household Chat", "1.0.0", db=self.conn_factory("household_chat"), ws=ws)
        self.assertEqual((ws._subs, ws._on_connect), ({}, []))
        b.stop()


class HAWebSocketTest(BusCase):
    def test_several_subscriptions_and_reconnect(self):
        got = {"a": [], "b": []}
        ws = ha_ws.HAWebSocket(self.fake.ws_url, TOKEN, name="ha-ws-test", **FAST_WS)
        ws.subscribe("type_a", lambda e: got["a"].append(e["data"]))
        connects = []
        ws.on_connect(connects.append)
        self.assertTrue(ws.start())
        self.assertFalse(ws.start())                            # already running
        try:
            wait_until(lambda: self.fake.subscribers() == 1)
            ws.subscribe("type_b", lambda e: got["b"].append(e["data"]))   # while connected
            wait_until(lambda: self.fake.subscribers() == 2)
            self.fake.fire("type_a", {"n": 1})
            self.fake.fire("type_b", {"n": 2})
            self.fake.fire("type_c", {"n": 3})
            wait_until(lambda: got == {"a": [{"n": 1}], "b": [{"n": 2}]})
            self.fake.drop_connections()                        # HA restarts
            wait_until(lambda: ws.connections == 2 and self.fake.subscribers() == 2, what="reconnect")
            self.fake.fire("type_a", {"n": 4})
            wait_until(lambda: got["a"] == [{"n": 1}, {"n": 4}])
            self.assertEqual(connects, [True, False])
        finally:
            ws.stop()
        self.assertFalse(ws.running)

    def test_wrong_token_never_connects(self):
        ws = ha_ws.HAWebSocket(self.fake.ws_url, "wrong", name="ha-ws-test", **FAST_WS)
        ws.subscribe("type_a", lambda e: None)
        ws.start()
        try:
            time.sleep(0.3)
            self.assertFalse(ws.connected.is_set())
            self.assertEqual(self.fake.subscribers(), 0)
        finally:
            ws.stop()
        self.assertFalse(ws.running)

    def test_callback_error_keeps_connection(self):
        got = []

        def boom(e):
            got.append(e["data"])
            raise RuntimeError("x")
        ws = ha_ws.HAWebSocket(self.fake.ws_url, TOKEN, name="ha-ws-test", **FAST_WS)
        ws.subscribe("t", boom)
        ws.start()
        try:
            wait_until(lambda: self.fake.subscribers() == 1)
            self.fake.fire("t", {"n": 1})
            self.fake.fire("t", {"n": 2})
            wait_until(lambda: len(got) == 2)
            self.assertEqual(ws.connections, 1)
        finally:
            ws.stop()

    def test_fake_bus_rejects_wrong_token(self):
        import urllib.error
        import urllib.request
        req = urllib.request.Request(f"{self.api}/events/household_apps", data=b"{}", method="POST",
                                     headers={"Authorization": "Bearer nope", "Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(cm.exception.code, 401)
        self.assertEqual(self.fake.fired, [])


if __name__ == "__main__":
    unittest.main()
