"""Regressions for the 13 October 2026 review (both builds; the parts only one build has are skipped in the other):
the Host check everywhere and, with keys off and the port published, only callers inside Home Assistant; a stalled
body is 408; a stream stopped before its first token; a right key beats an address's lockout; Home Assistant's
token under both names (and the page's password) kept from the model server; unknown models looked up at most every
few seconds; a ticket freed whatever goes wrong; the page's slow work off the event loop; /v1 gets the thread count;
no needless timer resets; restarts and deletes in turn; a restore the model server follows; Answer first for keys;
more waiting places for one address on a Docker host; request size limits, cached assets and stale backup copies."""
import _env  # noqa: F401  (must be first)

import asyncio
import os
import re
import socket
import tempfile
import threading
import time
import unittest

import httpx

import harness
from app import config, db, gateway, jobqueue, keys, main, models, network, ollama, server, settings
from app.routers import api

A = harness.ADMIN
GEN = "/api/generate"
V1 = "/v1/chat/completions"


def gen(prompt="hi", model="qwen2.5:3b", **kw):
    return {"model": model, "prompt": prompt, "stream": False, **kw}


def wait_for(cond, timeout=10):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = cond()
        if v:
            return v
        time.sleep(0.02)
    raise AssertionError("timed out")


def fire(h, address, body, results, name, timeout=30.0, path=GEN):
    def go():
        try:
            r = h.gw(address, timeout).post(path, json=body)
            results.append((name, r.status_code, time.monotonic()))
        except httpx.HTTPError as e:
            results.append((name, type(e).__name__, time.monotonic()))
    t = threading.Thread(target=go)
    t.start()
    return t


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.h = harness.get()

    def setUp(self):
        self.h.reset()

    def tearDown(self):
        self.h.wait_idle()


# ------------------------------------------------------------------------------------------------ security

class PublishedPortWithoutKeys(Base):
    """AI-S1: with keys off and 11434 published on the LAN, the Home Assistant app serves only its own network."""

    def setUp(self):
        super().setUp()
        if config.INSIDE_NETWORK is None:
            self.skipTest("the Docker build has no Home Assistant network")
        self.saved = dict(network._cache)

    def tearDown(self):
        network._cache.clear()
        network._cache.update(self.saved)
        super().tearDown()

    def publish(self, port):
        network._cache.update(at=time.monotonic(), value={"known": True, "port": port})

    def test_outsiders_are_refused_only_while_published_and_keys_are_off(self):
        c = self.h.gw("127.0.0.9")
        self.assertEqual(c.post(GEN, json=gen()).status_code, 200)            # not published: as before
        open(harness.LOG, "w").close()
        self.publish(11434)
        r = c.post(GEN, json=gen())
        self.assertEqual(r.status_code, 403)
        self.assertIn("Require an access key", r.json()["error"])
        r = c.post(V1, json={"model": "qwen2.5:3b", "messages": []})
        self.assertEqual(r.json()["error"]["type"], "permission_error")
        self.assertEqual(c.get("/api/version").status_code, 200)              # the watchdog still gets an answer
        self.assertEqual(self.h.requests(GEN), [])
        # Inside Home Assistant's network: served.
        saved = config.INSIDE_NETWORK
        try:
            import ipaddress
            config.INSIDE_NETWORK = (ipaddress.ip_network("127.0.0.0/8"),)
            self.assertEqual(c.post(GEN, json=gen()).status_code, 200)
        finally:
            config.INSIDE_NETWORK = saved
        # With keys on, a computer outside uses a key.
        settings.update({"require_key": True}, None)
        _, key = keys.create("Laptop", None, "u")
        self.assertEqual(c.post(GEN, json=gen(), headers={"Authorization": f"Bearer {key}"}).status_code, 200)
        self.publish(None)
        settings.update({"require_key": False}, None)
        self.assertEqual(c.post(GEN, json=gen()).status_code, 200)

    def test_a_failed_read_keeps_what_was_known(self):
        self.publish(11434)
        saved = config.SUPERVISOR_TOKEN, config.SUPERVISOR_API
        try:
            config.SUPERVISOR_TOKEN, config.SUPERVISOR_API = "t", "http://127.0.0.1:9"     # nothing listens
            self.assertEqual(asyncio.run(network.published_port(force=True)), {"known": True, "port": 11434})
        finally:
            config.SUPERVISOR_TOKEN, config.SUPERVISOR_API = saved
        self.assertEqual(network.cached()["port"], 11434)

    def test_ipv4_mapped_and_bad_addresses(self):
        self.publish(11434)
        self.assertFalse(gateway.outsider_refused("172.30.33.7"))
        self.assertFalse(gateway.outsider_refused("::ffff:172.30.32.2"))
        self.assertTrue(gateway.outsider_refused("192.168.1.10"))
        self.assertTrue(gateway.outsider_refused(None))
        self.assertTrue(gateway.outsider_refused("not an address"))


def read_response(s: socket.socket) -> bytes:
    """The whole HTTP response: headers, then the body up to its Content-Length (or until the server closes).
    One recv() isn't enough: on a slow machine the headers and the body can arrive in separate packets."""
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = s.recv(4096)
        if not chunk:
            return data
        data += chunk
    head, _, body = data.partition(b"\r\n\r\n")
    m = re.search(rb"(?im)^content-length:\s*(\d+)\s*$", head)
    while m and len(body) < int(m.group(1)):
        chunk = s.recv(4096)
        if not chunk:
            break
        body += chunk
    return head + b"\r\n\r\n" + body


class SlowBody(Base):
    """AI-S2: a body that stalls frees its reading place after READ_TIMEOUT (408)."""

    def test_a_stalled_body_is_408_and_frees_its_place(self):
        saved = gateway.READ_TIMEOUT
        gateway.READ_TIMEOUT = 0.5
        try:
            s = socket.create_connection(("127.0.0.1", self.h.port), source_address=("127.0.0.9", 0), timeout=5)
            s.sendall(b"POST /api/generate HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\n"
                      b"Content-Length: 100\r\n\r\n{\"model\": ")
            wait_for(lambda: gateway.READING["n"] == 1, 3)
            started = time.monotonic()
            data = read_response(s)
            s.close()
            self.assertIn(b" 408 ", data.split(b"\r\n")[0])
            self.assertIn(b"didn't arrive", data)
            self.assertLess(time.monotonic() - started, 3)
            wait_for(lambda: gateway.READING["n"] == 0, 3)
        finally:
            gateway.READ_TIMEOUT = saved
        self.assertEqual(self.h.gw().post(GEN, json=gen()).status_code, 200)


class StreamBeforeTheFirstToken(Base):
    """AI-S3: until Ollama sends its first line (a model loading), the caller going away or the longest run
    passing stops the request at once."""

    def test_a_caller_gone_before_the_first_token_stops_the_request(self):
        res = []
        started = time.monotonic()
        t = fire(self.h, "127.0.0.3", {"model": "qwen2.5:3b", "prompt": "sleep:4"}, res, "gone", timeout=0.4)
        t.join()
        self.assertEqual(res[0][1], "ReadTimeout")
        wait_for(lambda: {"cancelled": GEN} in self.h.log(), 3)
        self.h.wait_idle(3)
        self.assertLess(time.monotonic() - started, 3.5)       # not after the 4 s "model load"

    def test_the_longest_run_counts_from_the_turn_not_the_first_token(self):
        gateway.MINUTE = 0.5                      # max_run_min 1 → half a second
        settings.update({"max_run_min": 1}, None)
        started = time.monotonic()
        r = self.h.gw().post(GEN, json={"model": "qwen2.5:3b", "prompt": "sleep:4"})
        self.assertEqual(r.status_code, 504)
        self.assertIn("longest run", r.json()["error"])
        self.assertLess(time.monotonic() - started, 3.5)
        wait_for(lambda: {"cancelled": GEN} in self.h.log(), 3)


class KeyBeforeLockout(Base):
    """AI-S4: a right key is served even from an address that sent ten wrong ones; old blocks are forgotten."""

    def test_a_right_key_is_served_from_a_blocked_address(self):
        settings.update({"require_key": True}, None)
        _, key = keys.create("Household Assistant", None, "u")
        c = self.h.gw("127.0.0.41")
        try:
            codes = [c.post(GEN, json=gen(), headers={"Authorization": "Bearer wrong"}).status_code for _ in range(11)]
            self.assertEqual(codes[-1], 429)
            self.assertEqual(c.post(GEN, json=gen(), headers={"Authorization": f"Bearer {key}"}).status_code, 200)
            self.assertEqual(c.post(GEN, json=gen(), headers={"Authorization": "Bearer wrong"}).status_code, 429)
            self.assertEqual(c.post(GEN, json=gen()).status_code, 429)
        finally:
            keys.FAILURES.blocked.clear()
            keys.FAILURES.seen.clear()

    def test_ended_blocks_are_pruned(self):
        now = [0.0]
        f = keys.Failures(clock=lambda: now[0])
        f.KEEP = 5
        for i in range(6):
            for _ in range(keys.FAILS_PER_MINUTE):
                f.fail(f"10.0.0.{i}")
        self.assertEqual(len(f.blocked), 6)
        now[0] = 120.0
        for _ in range(keys.FAILS_PER_MINUTE):
            f.fail("10.9.9.9")
        self.assertEqual(list(f.blocked), ["10.9.9.9"])


class SecretsKeptFromTheModelServer(unittest.TestCase):
    """AI-S5: neither of Home Assistant's token names, nor the Docker page's password, reach Ollama."""

    def test_environment(self):
        saved = {k: os.environ.get(k) for k in server.SECRET_ENV}
        try:
            for k in server.SECRET_ENV:
                os.environ[k] = "secret"
            env = server.environment(settings.all())
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        for k in ("SUPERVISOR_TOKEN", "HASSIO_TOKEN", "ADMIN_PASSWORD"):
            self.assertNotIn(k, env)
        self.assertNotIn("secret", env.values())
        self.assertIn("PATH", env)


class UnknownModelLookups(Base):
    """AI-S6: a model the gateway doesn't know makes it ask Ollama again at most every REFRESH_EVERY seconds."""

    def test_at_most_one_lookup_for_a_burst(self):
        open(harness.LOG, "w").close()
        ollama.MODELS.tried_at = 0.0
        c = self.h.gw()
        for _ in range(5):
            self.assertEqual(c.post(GEN, json=gen(model="nope:1b")).status_code, 404)
        tags = [e for e in self.h.log() if e.get("path") == "/api/tags"]
        self.assertEqual(len(tags), 1)
        # A model downloaded meanwhile is found on the next look.
        harness.Harness.seed(harness.DEFAULT_MODELS + ["fresh:1b"])
        ollama.MODELS.tried_at = 0.0
        self.assertEqual(c.post(GEN, json=gen(model="fresh:1b")).status_code, 200)


class TicketAlwaysFreed(Base):
    """AI-S7: whatever fails between entering the queue and the answer, the place is freed."""

    def test_an_error_after_entering_frees_the_queue(self):
        real = gateway.note_model

        def boom(model):
            raise RuntimeError("boom")
        gateway.note_model = boom
        try:
            try:
                status = self.h.gw("127.0.0.61").post(GEN, json=gen()).status_code
            except httpx.TransportError:          # uvicorn may close the connection instead of sending its 500
                status = 500
            self.assertEqual(status, 500)
            self.h.wait_idle(2)
        finally:
            gateway.note_model = real
        self.assertEqual(self.h.gw().post(GEN, json=gen()).status_code, 200)


# ------------------------------------------------------------------------------------------------ performance

class OffTheEventLoop(Base):
    """AI-P1: the status poll's machine/database reads and the settings saves run in worker threads."""

    def spy(self, module, name, seen):
        real = getattr(module, name)

        def wrapper(*a, **kw):
            try:
                asyncio.get_running_loop()
                seen.append((name, "loop"))
            except RuntimeError:
                seen.append((name, "thread"))
            return real(*a, **kw)
        setattr(module, name, wrapper)
        self.addCleanup(setattr, module, name, real)

    def test_status_and_saves(self):
        seen = []
        from app import machine
        self.spy(machine, "summary", seen)
        self.spy(keys, "labels", seen)
        self.spy(settings, "update", seen)
        self.assertEqual(self.h.api.get("api/status", headers=A).status_code, 200)
        self.assertEqual(self.h.api.put("api/admin/settings", json={"queue": 5}, headers=A).status_code, 200)
        self.assertEqual(self.h.api.put("api/server", json={"on": True}, headers=A).status_code, 200)
        self.assertTrue(seen)
        self.assertEqual({where for _, where in seen}, {"thread"}, seen)


class V1Options(Base):
    """AI-P2: /v1 requests carry the thread count and context too (an Ollama that reads them uses them)."""

    def test_v1_gets_num_thread_and_num_ctx(self):
        settings.update({"threads": 3, "context": 4096}, None)
        msg = {"model": "qwen2.5:3b", "messages": [{"role": "user", "content": "hi"}],
               "options": {"num_gpu": 99, "num_thread": 64}}
        self.assertEqual(self.h.gw().post(V1, json=msg).status_code, 200)
        self.assertEqual(self.h.requests(V1)[-1]["options"], {"num_thread": 3, "num_ctx": 4096})
        self.h.wait_ready()


class NoNeedlessTimerReset(Base):
    """AI-P3: after a /v1 answer the timer is reset only when the schedule's keep_alive isn't the server's own."""

    def resets(self):
        return [b for b in self.h.requests(GEN) if "prompt" not in b]

    def test_skipped_when_equal_to_the_servers_default(self):
        msg = {"model": "qwen2.5:3b", "messages": [{"role": "user", "content": "hi"}]}
        settings.update({"day_start": "00:00", "day_end": "00:00", "keep_model": "none"}, None)
        self.assertEqual(server.SERVER.default_keep_alive, 600)
        self.assertEqual(self.h.gw().post(V1, json=msg).status_code, 200)
        self.h.wait_idle()
        self.assertEqual(self.resets(), [])                  # day rule 10 min = OLLAMA_KEEP_ALIVE 10m
        settings.update({"day_unload_min": 5}, None)
        self.assertEqual(self.h.gw().post(V1, json=msg).status_code, 200)
        self.h.wait_idle()
        self.assertEqual(self.resets(), [{"model": "qwen2.5:3b", "keep_alive": 300}])


# ------------------------------------------------------------------------------------------------ improvements

class RestartInTurn(Base):
    """AI-I1: a server setting restarts the model server after the answer running now, not in the middle of it;
    the restart task is kept."""

    def test_the_running_answer_finishes_first(self):
        res = []
        pid = server.SERVER.proc.pid
        t = fire(self.h, "127.0.0.3", gen("sleep:1.5"), res, "running")
        wait_for(lambda: gateway.QUEUE.running is not None)
        r = self.h.api.put("api/admin/settings", json={"context": 4096}, headers=A)
        self.assertEqual(r.status_code, 200)
        self.assertIsNotNone(server.SERVER._restart)
        time.sleep(0.5)
        self.assertEqual(server.SERVER.proc.pid, pid)           # still the same server while the answer runs
        t.join()
        self.assertEqual(res[0][1], 200)
        wait_for(lambda: server.SERVER.proc is not None and server.SERVER.proc.pid != pid
                 and server.SERVER.state == "ready", 20)
        self.h.wait_ready()
        env = [e["env"] for e in self.h.log() if "env" in e][-1]
        self.assertEqual(env["OLLAMA_CONTEXT_LENGTH"], "4096")

    def test_two_changes_before_it_begins_make_one_restart(self):
        res = []
        t = fire(self.h, "127.0.0.3", gen("sleep:1"), res, "running")
        wait_for(lambda: gateway.QUEUE.running is not None)
        a = self.h.run(_restart_soon())
        b = self.h.run(_restart_soon())
        self.assertIs(a, b)
        t.join()
        wait_for(lambda: a.done(), 20)
        self.h.wait_ready()


async def _restart_soon():
    return server.SERVER.restart_soon(gateway.QUEUE)


class DeleteInTurn(Base):
    """AI-I2: deleting a model waits for the running answer, and has a path of its own."""

    def test_delete_waits_and_pull_ids_dont_clash(self):
        res = []
        t = fire(self.h, "127.0.0.3", gen("sleep:1", model="qwen2.5vl:3b"), res, "running")
        wait_for(lambda: gateway.QUEUE.running is not None)
        r = self.h.api.delete("api/downloaded/qwen2.5vl:3b", headers=A)
        done_at = time.monotonic()
        self.assertEqual(r.status_code, 200, r.text)
        t.join()
        self.assertEqual(res[0][1], 200)
        self.assertLessEqual(res[0][2], done_at)                 # the answer ended before the delete did
        self.assertFalse(ollama.MODELS.has("qwen2.5vl:3b"))
        # A model whose name starts with "pull/" is a model, and a download id is a download.
        self.assertEqual(self.h.api.delete("api/downloaded/pull/x:1", headers=A).status_code, 400)
        self.assertEqual(self.h.api.delete("api/models/pull/nope", headers=A).status_code, 404)
        self.assertEqual(self.h.api.delete("api/models/qwen2.5:3b", headers=A).status_code, 405)


class RestoreFollowsTheServerSettings(Base):
    """AI-I3: after a restore, the model server follows the restored context, models loaded and on/off."""

    def backup(self):
        r = self.h.api.get("api/admin-storage-download-db", headers=A)
        self.assertEqual(r.status_code, 200)
        return r.content

    def restore(self, data):
        r = self.h.api.post("api/admin-storage-import-db", headers=A,
                            files={"file": ("household-ai-backup.db", data, "application/octet-stream")})
        self.assertEqual(r.status_code, 200, r.text)

    def test_a_changed_context_restarts_and_server_off_stops(self):
        settings.update({"context": 4096}, None)
        data = self.backup()
        settings.update({"context": 8192}, None)
        pid = server.SERVER.proc.pid
        self.restore(data)
        self.assertEqual(settings.get("context"), 4096)
        wait_for(lambda: server.SERVER.proc is not None and server.SERVER.proc.pid != pid
                 and server.SERVER.state == "ready", 20)
        self.h.wait_ready()
        settings.update({"server_on": False}, None)
        data = self.backup()
        settings.update({"server_on": True}, None)
        try:
            self.restore(data)
            self.assertEqual(server.SERVER.state, "stopped")
            self.assertIn("restored backup", server.SERVER.reason)
        finally:
            settings.update({"server_on": True}, None)
            self.h.run(server.SERVER.start())
            self.h.wait_ready()


class AnswerFirstWithKeys(Base):
    """AI-I4: with keys on, the default Answer first (household_assistant) applies to a key labelled for the
    assistant, and a key can be made Answer first when it is created."""

    def test_label_and_tick(self):
        self.assertTrue(gateway.answers_first("key_x", {"id": "key_x", "label": "Household Assistant"}))
        self.assertTrue(gateway.answers_first("key_x", {"id": "key_x", "label": "household-assistant"}))
        self.assertFalse(gateway.answers_first("key_y", {"id": "key_y", "label": "Receipts"}))
        self.assertFalse(gateway.answers_first("key_y", None))
        r = self.h.api.post("api/keys", json={"label": "Receipts", "answerFirst": True}, headers=A).json()
        self.assertIn(r["id"], settings.get("answer_first"))
        self.assertTrue(gateway.answers_first(r["id"], {"id": r["id"], "label": "Receipts"}))
        self.assertEqual(self.h.api.delete(f"api/keys/{r['id']}", headers=A).status_code, 200)
        self.assertNotIn(r["id"], settings.get("answer_first"))

    def test_the_assistants_key_jumps_the_queue(self):
        settings.update({"require_key": True}, None)
        _, docs = keys.create("Household Docs", None, "u")
        _, assistant = keys.create("Household Assistant", None, "u")
        order = []

        def go(name, key, prompt):
            r = self.h.gw(f"127.0.0.{50 + len(name)}").post(GEN, json=gen(prompt),
                                                          headers={"Authorization": f"Bearer {key}"})
            order.append((name, r.status_code))
        ts = [threading.Thread(target=go, args=("docs-running", docs, "sleep:1"))]
        ts[0].start()
        wait_for(lambda: gateway.QUEUE.running is not None)
        ts.append(threading.Thread(target=go, args=("docs-waiting", docs, "sleep:0.2")))
        ts[1].start()
        wait_for(lambda: len(gateway.QUEUE.waiting) == 1)
        ts.append(threading.Thread(target=go, args=("assistant", assistant, "sleep:0.2")))
        ts[2].start()
        for t in ts:
            t.join()
        self.assertEqual([n for n, _ in order], ["docs-running", "assistant", "docs-waiting"])


class WaitingPlacesPerAddress(unittest.TestCase):
    """DK-I1: without keys, an unknown address gets config.ADDR_PER_CALLER waiting places (6 on a Docker host,
    where every household app shares Home Assistant's address)."""

    def test_per_caller(self):
        self.assertEqual(gateway.per_caller("household_docs", None), jobqueue.PER_CALLER)
        self.assertEqual(gateway.per_caller("key_x", {"id": "key_x"}), jobqueue.PER_CALLER)
        self.assertEqual(gateway.per_caller("addr:192.168.1.10", None), max(jobqueue.PER_CALLER, config.ADDR_PER_CALLER))
        q = jobqueue.RequestQueue(limit=lambda: 50)
        q.enter("a", "m")
        for _ in range(6):
            q.enter("addr:x", "m", per_caller=6)
        with self.assertRaises(jobqueue.Full) as e:
            q.enter("addr:x", "m", per_caller=6)
        self.assertIn("6 requests", str(e.exception))
        q.enter("app", "m", force=True)                        # the app's own jobs are never refused
        self.assertEqual(len(q.waiting), 7)


# ------------------------------------------------------------------------------------------------ every app

class SizesCachingAndSweeps(Base):
    def test_request_bodies_over_the_limit(self):
        r = self.h.api.post("api/keys", content=b"{" + b" " * (2 << 20) + b"}", headers={**A, "content-type": "application/json"})
        self.assertEqual(r.status_code, 413)

        def chunks():
            yield b"x" * 10
        r = self.h.api.post(api.RESTORE_PATH, content=chunks(), headers=A)
        self.assertEqual(r.status_code, 411)

    def test_versioned_assets_are_cached_and_the_page_isnt(self):
        c = self.h.api
        r = c.get(f"app.js?v={config.APP_VERSION.split('-')[0]}")
        self.assertEqual(r.status_code, 200)
        self.assertIn("immutable", r.headers["cache-control"])
        self.assertNotIn("immutable", c.get("app.js").headers.get("cache-control", ""))
        self.assertEqual(c.get("/").headers["cache-control"], "no-cache, no-store, must-revalidate")
        with open(os.path.join(os.path.dirname(main.__file__), "static", "index.html"), encoding="utf-8") as f:
            html = f.read()
        self.assertNotRegex(html, r'(src|href)="(?![^"]*\?v=)[^"]+\.(js|css)"')

    def test_backup_copies_left_behind_are_swept(self):
        old = os.path.join(tempfile.gettempdir(), db.BACKUP_PREFIX + "abandoned.db")
        new = os.path.join(tempfile.gettempdir(), db.BACKUP_PREFIX + "fresh.db")
        part = os.path.join(config.DATA_DIR, db.RESTORE_PREFIX + "cut.db")
        for p in (old, new, part):
            open(p, "wb").close()
        past = time.time() - 7 * 3600
        os.utime(old, (past, past))
        os.utime(part, (past, past))
        main.housekeeping()
        self.assertFalse(os.path.exists(old))
        self.assertFalse(os.path.exists(part))
        self.assertTrue(os.path.exists(new))
        os.remove(new)
        path = db.backup_to_tempfile()
        try:
            self.assertTrue(os.path.basename(path).startswith(db.BACKUP_PREFIX))
        finally:
            os.remove(path)


class Registries(unittest.TestCase):
    """DK-S3: on a Docker host downloads come only from Ollama's library and Hugging Face (ALLOW_ANY_REGISTRY=1
    lifts it); the Home Assistant app allows any registry."""

    def test_registry_of(self):
        self.assertEqual(models.registry_of("qwen2.5:3b"), "registry.ollama.ai")
        self.assertEqual(models.registry_of("someone/model"), "registry.ollama.ai")
        self.assertEqual(models.registry_of("hf.co/bartowski/x:Q4_K_M"), "hf.co")
        self.assertEqual(models.registry_of("localhost:5000/m:1"), "localhost:5000")

    def test_the_allow_list(self):
        saved = config.ALLOWED_REGISTRIES
        try:
            config.ALLOWED_REGISTRIES = frozenset({"registry.ollama.ai", "hf.co", "huggingface.co"})
            models.check_registry("qwen2.5:3b")
            models.check_registry("hf.co/org/repo:Q4")
            for name in ("ghcr.io/someone/model:1", "localhost:5000/m:1", "evil.example.com/m"):
                with self.assertRaises(ValueError) as e:
                    models.check_registry(name)
                self.assertIn("ALLOW_ANY_REGISTRY", str(e.exception))
                self.assertRaises(ValueError, asyncio.run, models.start_pull(name))
            config.ALLOWED_REGISTRIES = None
            models.check_registry("ghcr.io/someone/model:1")
        finally:
            config.ALLOWED_REGISTRIES = saved


class ConfigNames(unittest.TestCase):
    def test_each_build_sets_what_the_shared_code_reads(self):
        for name in ("ALLOWED_HOSTS", "HOST_HINT", "INSIDE_NETWORK", "ADDR_PER_CALLER", "ALLOWED_REGISTRIES"):
            self.assertTrue(hasattr(config, name), name)
        self.assertIsInstance(config.ALLOWED_HOSTS, frozenset)
        self.assertGreaterEqual(config.ADDR_PER_CALLER, jobqueue.PER_CALLER)


if __name__ == "__main__":
    unittest.main()
