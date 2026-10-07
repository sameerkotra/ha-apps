"""The gateway on 11434 (SPEC.md §4, §6.1, §8 step 3), against the fake model server, over real sockets.

Callers send from different loopback addresses: 127.0.0.2 is the assistant, 127.0.0.3 Docs, 127.0.0.4 Receipt
(their host names resolve there, harness.HOSTS); anything else is an unknown app.
"""
import _env  # noqa: F401  (must be first)

import json
import threading
import time
import unittest

import httpx

import harness
from app import db, gateway, keys, settings

GEN = "/api/generate"


def gen(prompt="hi", model="qwen2.5:3b", **kw):
    return {"model": model, "prompt": prompt, "stream": False, **kw}


def usage_rows():
    with db.get_conn() as conn:
        return [tuple(r) for r in conn.execute("SELECT caller, model, requests, input_tokens, output_tokens "
                                               "FROM model_usage ORDER BY caller, model").fetchall()]


def clear_usage():
    with db.get_conn() as conn:
        conn.execute("DELETE FROM model_usage")
        conn.execute("DELETE FROM model_callers")


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.h = harness.get()

    def setUp(self):
        self.h.reset()
        clear_usage()

    def tearDown(self):
        self.h.wait_idle()


class Routes(Base):
    def test_without_keys_any_internal_caller_is_served_and_counted(self):
        r = self.h.gw("127.0.0.9").post(GEN, json=gen())
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["response"], "ok")
        self.assertIn(("addr:127.0.0.9", "qwen2.5:3b", 1, 5, 3), usage_rows())

    def test_model_management_is_never_passed_on(self):
        c = self.h.gw()
        for method, path in (("POST", "/api/pull"), ("DELETE", "/api/delete"), ("POST", "/api/create"),
                             ("POST", "/api/copy"), ("POST", "/api/push"), ("GET", "/api/ps"),
                             ("POST", "/api/blobs/sha256:x")):
            r = c.request(method, path, json={"model": "qwen2.5:3b"})
            self.assertEqual(r.status_code, 404, path)
        self.assertEqual([e for e in self.h.log() if e.get("path") in ("/api/pull", "/api/delete")], [])

    def test_version_tags_and_v1_models(self):
        c = self.h.gw()
        self.assertEqual(c.get("/api/version").json()["version"], "0.40.0")
        self.assertEqual({m["name"] for m in c.get("/api/tags").json()["models"]}, {"qwen2.5:3b", "qwen2.5vl:3b"})
        self.assertEqual(len(c.get("/v1/models").json()["data"]), 2)

    def test_a_model_not_downloaded_is_404_with_words(self):
        r = self.h.gw().post(GEN, json=gen(model="llama3.2:3b"))
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()["error"], "Download llama3.2:3b in Household AI first.")
        r = self.h.gw().post("/v1/chat/completions", json={"model": "nope", "messages": []})
        self.assertEqual(r.status_code, 404)
        self.assertIn("Download nope in Household AI first", r.json()["error"]["message"])

    def test_options_are_capped_and_keep_alive_replaced(self):
        settings.update({"day_start": "00:00", "day_end": "00:00", "threads": 3}, None)    # all day
        r = self.h.gw().post(GEN, json=gen(keep_alive="24h", options={"num_ctx": 100000, "num_predict": 99999,
                                                                      "num_thread": 64, "temperature": 0.2}))
        self.assertEqual(r.status_code, 200)
        body = self.h.requests(GEN)[-1]
        self.assertEqual(body["options"], {"num_ctx": 8192, "num_predict": 2048, "num_thread": 3,
                                           "temperature": 0.2})
        self.assertEqual(body["keep_alive"], -1)             # the kept model (first text model) in the day
        r = self.h.gw().post(GEN, json=gen(model="qwen2.5vl:3b", options={"num_ctx": 4096, "num_predict": 100}))
        body = self.h.requests(GEN)[-1]
        self.assertEqual((body["options"]["num_ctx"], body["options"]["num_predict"]), (4096, 100))
        self.assertEqual(body["keep_alive"], 600)            # another model: 10 min in the day

    def test_night_keep_alive_for_every_model(self):
        settings.update({"day_start": "00:00", "day_end": "00:01", "night_unload_min": 3}, None)
        from app import config
        if config.now().hour == 0 and config.now().minute == 0:
            self.skipTest("midnight")
        self.h.gw().post(GEN, json=gen())
        self.assertEqual(self.h.requests(GEN)[-1]["keep_alive"], 180)

    def test_v1_max_tokens_capped_and_tokens_counted(self):
        r = self.h.gw("127.0.0.3").post("/v1/chat/completions", json={
            "model": "qwen2.5:3b", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 50000})
        self.assertEqual(r.status_code, 200, r.text)
        body = self.h.requests("/v1/chat/completions")[-1]
        self.assertEqual(body["max_tokens"], 2048)
        self.assertNotIn("keep_alive", body)
        self.assertIn(("household_docs", "qwen2.5:3b", 1, 7, 4), usage_rows())
        # The model's timer is reset after a /v1 answer (an empty generate with the schedule's keep_alive).
        self.assertTrue(any("prompt" not in b and "keep_alive" in b for b in self.h.requests(GEN)))

    def test_v1_stream_counts_tokens_and_hides_the_usage_chunk_it_added(self):
        r = self.h.gw("127.0.0.3").post("/v1/chat/completions", json={
            "model": "qwen2.5:3b", "stream": True, "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(r.status_code, 200)
        self.assertNotIn('"usage"', r.text)
        self.assertIn("[DONE]", r.text)
        self.assertEqual(self.h.requests("/v1/chat/completions")[-1]["stream_options"], {"include_usage": True})
        self.h.wait_idle()
        self.assertIn(("household_docs", "qwen2.5:3b", 1, 7, 4), usage_rows())
        r = self.h.gw("127.0.0.3").post("/v1/chat/completions", json={
            "model": "qwen2.5:3b", "stream": True, "stream_options": {"include_usage": True},
            "messages": [{"role": "user", "content": "hi"}]})
        self.assertIn('"usage"', r.text)                                # asked for it: kept

    def test_native_stream_and_chat_and_embed(self):
        r = self.h.gw().post(GEN, json={"model": "qwen2.5:3b", "prompt": "hi"})      # streams by default
        lines = [json.loads(l) for l in r.text.splitlines() if l.strip()]
        self.assertEqual(len(lines), 4)
        self.assertTrue(lines[-1]["done"])
        r = self.h.gw().post("/api/chat", json={"model": "qwen2.5:3b", "stream": False,
                                                "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(r.json()["message"]["content"], "ok")
        r = self.h.gw().post("/api/embed", json={"model": "qwen2.5:3b", "input": "hi"})
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("num_predict", self.h.requests("/api/embed")[-1]["options"])
        self.h.wait_idle()
        self.assertIn(("addr:127.0.0.9", "qwen2.5:3b", 3, 12, 6), usage_rows())

    def test_show_passes(self):
        r = self.h.gw().post("/api/show", json={"model": "qwen2.5vl:3b"})
        self.assertIn("vision", r.json()["capabilities"])

    def test_bodies_over_32_mb_are_refused(self):
        big = b'{"model":"qwen2.5:3b","prompt":"' + b"x" * (32 * 1024 * 1024) + b'"}'
        r = self.h.gw().post(GEN, content=big, headers={"content-type": "application/json"})
        self.assertEqual(r.status_code, 413)

    def test_bad_json(self):
        r = self.h.gw().post(GEN, content=b"{nope", headers={"content-type": "application/json"})
        self.assertEqual(r.status_code, 400)
        r = self.h.gw().post(GEN, json={"prompt": "hi"})
        self.assertEqual(r.status_code, 400)


class Callers(Base):
    def test_an_app_restarting_on_a_new_address_keeps_its_name_and_usage(self):
        self.h.gw("127.0.0.2").post(GEN, json=gen())
        try:
            harness.HOSTS["a1b2c3d4-household-assistant"] = ["127.0.0.12"]
            self.h.refresh_callers()
            self.h.gw("127.0.0.12").post(GEN, json=gen())
        finally:
            harness.HOSTS["a1b2c3d4-household-assistant"] = ["127.0.0.2"]
        self.assertIn(("household_assistant", "qwen2.5:3b", 2, 10, 6), usage_rows())


class Keys(Base):
    def test_keys_on(self):
        settings.update({"require_key": True}, None)
        c = self.h.gw("127.0.0.21")
        r = c.post(GEN, json=gen())
        self.assertEqual(r.status_code, 401)
        self.assertIn("needs an access key", r.json()["error"])
        kid, key = keys.create("Docs", None, "u-admin")
        r = c.post(GEN, json=gen(), headers={"Authorization": f"Bearer {key}"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn((kid, "qwen2.5:3b", 1, 5, 3), usage_rows())
        self.assertEqual(c.get("/api/version").status_code, 200)            # the watchdog needs no key
        keys.revoke(kid)
        r = c.post(GEN, json=gen(), headers={"Authorization": f"Bearer {key}"})
        self.assertEqual(r.status_code, 401)
        self.assertIn("isn't valid", r.json()["error"])

    def test_a_key_limited_to_some_models(self):
        settings.update({"require_key": True}, None)
        kid, key = keys.create("Receipt", ["qwen2.5vl:3b"], "u-admin")
        c = self.h.gw("127.0.0.22")
        hdr = {"Authorization": f"Bearer {key}"}
        self.assertEqual(c.post(GEN, json=gen(), headers=hdr).status_code, 403)
        self.assertEqual(c.post(GEN, json=gen(model="qwen2.5vl:3b"), headers=hdr).status_code, 200)
        self.assertEqual([m["name"] for m in c.get("/api/tags", headers=hdr).json()["models"]], ["qwen2.5vl:3b"])

    def test_ten_wrong_keys_in_a_minute_block_the_address(self):
        settings.update({"require_key": True}, None)
        c = self.h.gw("127.0.0.23")
        codes = [c.post(GEN, json=gen(), headers={"Authorization": "Bearer wrong"}).status_code for _ in range(11)]
        self.assertEqual(codes[:10], [401] * 10)
        self.assertEqual(codes[10], 429)
        keys.FAILURES.blocked.clear()

    def test_bearer_key_with_v1(self):
        settings.update({"require_key": True}, None)
        _, key = keys.create("x", None, "u")
        r = self.h.gw("127.0.0.24").post("/v1/chat/completions", headers={"Authorization": f"Bearer {key}"},
                                         json={"model": "qwen2.5:3b", "messages": [{"role": "user", "content": "x"}]})
        self.assertEqual(r.status_code, 200)
        r = self.h.gw("127.0.0.24").post("/v1/chat/completions", json={"model": "qwen2.5:3b", "messages": []})
        self.assertEqual(r.json()["error"]["type"], "authentication_error")


def fire(h, address, body, results, name, timeout=30.0, path=GEN):
    """A request in a thread; appends (name, status, finished at) to results."""
    def go():
        try:
            r = h.gw(address, timeout).post(path, json=body)
            results.append((name, r.status_code, time.monotonic(), r.headers.get("retry-after")))
        except httpx.HTTPError as e:
            results.append((name, type(e).__name__, time.monotonic(), None))
    t = threading.Thread(target=go)
    t.start()
    return t


def wait_for(cond, timeout=10):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return
        time.sleep(0.02)
    raise AssertionError("timed out")


class Queue(Base):
    def test_a_third_waiting_request_from_one_caller_is_503_and_the_rest_wait(self):
        res = []
        ts = [fire(self.h, "127.0.0.4", gen("sleep:1.5", model="qwen2.5vl:3b"), res, "run")]
        wait_for(lambda: gateway.QUEUE.running is not None)
        ts += [fire(self.h, "127.0.0.4", gen("sleep:0"), res, f"w{i}") for i in range(2)]
        wait_for(lambda: len(gateway.QUEUE.waiting) == 2)
        ts.append(fire(self.h, "127.0.0.4", gen("x"), res, "third"))
        for t in ts:
            t.join()
        by = {r[0]: r for r in res}
        self.assertEqual(by["third"][1], 503)
        self.assertTrue(10 <= int(by["third"][3]) <= 60)
        self.assertEqual([by[n][1] for n in ("run", "w0", "w1")], [200, 200, 200])

    def test_the_queue_length_setting(self):
        settings.update({"queue": 1}, None)
        res = []
        ts = [fire(self.h, "127.0.0.31", gen("sleep:1"), res, "run")]
        wait_for(lambda: gateway.QUEUE.running is not None)
        ts.append(fire(self.h, "127.0.0.32", gen("x"), res, "w"))
        wait_for(lambda: len(gateway.QUEUE.waiting) == 1)
        ts.append(fire(self.h, "127.0.0.33", gen("x"), res, "full"))
        for t in ts:
            t.join()
        self.assertEqual({r[0]: r[1] for r in res}, {"run": 200, "w": 200, "full": 503})

    def test_the_assistant_jumps_the_queue(self):
        res = []
        ts = [fire(self.h, "127.0.0.3", gen("sleep:1"), res, "docs-running")]
        wait_for(lambda: gateway.QUEUE.running is not None)
        ts.append(fire(self.h, "127.0.0.3", gen("sleep:0.3"), res, "docs-waiting"))
        wait_for(lambda: len(gateway.QUEUE.waiting) == 1)
        ts.append(fire(self.h, "127.0.0.2", gen("sleep:0.3"), res, "assistant"))
        for t in ts:
            t.join()
        order = [r[0] for r in sorted(res, key=lambda r: r[2])]
        self.assertEqual(order, ["docs-running", "assistant", "docs-waiting"])

    def test_waiting_behind_a_long_job_is_not_turned_away(self):
        res = []
        ts = [fire(self.h, "127.0.0.4", gen("sleep:3", model="qwen2.5vl:3b"), res, "vision")]
        wait_for(lambda: gateway.QUEUE.running is not None)
        ts.append(fire(self.h, "127.0.0.2", gen("hi"), res, "assistant"))
        for t in ts:
            t.join()
        self.assertEqual({r[0]: r[1] for r in res}, {"vision": 200, "assistant": 200})

    def test_a_caller_gone_while_waiting_is_dropped_and_never_runs(self):
        res = []
        ts = [fire(self.h, "127.0.0.4", gen("sleep:2"), res, "running")]
        wait_for(lambda: gateway.QUEUE.running is not None)
        ts.append(fire(self.h, "127.0.0.3", gen("never-runs"), res, "gone", timeout=0.4))
        wait_for(lambda: any(r[0] == "gone" for r in res))
        wait_for(lambda: not gateway.QUEUE.waiting, 3)
        for t in ts:
            t.join()
        self.assertEqual({r[0]: r[1] for r in res}, {"running": 200, "gone": "ReadTimeout"})
        self.assertNotIn("never-runs", [b.get("prompt") for b in self.h.requests(GEN)])

    def test_a_caller_gone_while_running_has_its_request_stopped(self):
        res = []
        t = fire(self.h, "127.0.0.3", gen("sleep:3"), res, "gone", timeout=0.5)
        t.join()
        self.assertEqual(res[0][1], "ReadTimeout")
        wait_for(lambda: {"cancelled": GEN} in self.h.log(), 3)
        self.h.wait_idle(2)

    def test_a_streamed_answer_whose_caller_goes_is_stopped(self):
        res = []
        t = fire(self.h, "127.0.0.3", {"model": "qwen2.5:3b", "prompt": "sleep:3"}, res, "gone", timeout=0.5)
        t.join()
        wait_for(lambda: {"cancelled": GEN} in self.h.log(), 5)
        self.h.wait_idle(5)

    def test_a_run_past_the_longest_run_is_504(self):
        gateway.MINUTE = 0.5                      # max_run_min 1 → half a second
        settings.update({"max_run_min": 1}, None)
        r = self.h.gw().post(GEN, json=gen("sleep:3"))
        self.assertEqual(r.status_code, 504)
        self.assertIn("longest run", r.json()["error"])
        wait_for(lambda: {"cancelled": GEN} in self.h.log(), 3)


class ServerOff(Base):
    def test_off_is_503_with_words(self):
        from app import server
        self.h.run(server.SERVER.stop("Turned off by an admin"))
        try:
            r = self.h.gw().post(GEN, json=gen())
            self.assertEqual(r.status_code, 503)
            self.assertIn("turn it on", r.json()["error"])
            self.assertEqual(self.h.gw().get("/api/version").status_code, 200)     # the watchdog still gets an answer
        finally:
            self.h.run(server.SERVER.start())
            self.h.wait_ready()


if __name__ == "__main__":
    unittest.main()
