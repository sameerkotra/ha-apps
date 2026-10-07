"""The model server as a child process (SPEC.md §3, §8 step 2): its environment, start, watch, restart after an
exit (and the out-of-memory message), stop."""
import _env  # noqa: F401  (must be first)

import json
import os
import signal
import time
import unittest

import harness
from app import config, server, settings


def wait_for(cond, timeout=15):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return
        time.sleep(0.05)
    raise AssertionError(f"timed out: {server.SERVER.status()}")


class Process(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.h = harness.get()

    def setUp(self):
        self.h.reset()
        self.backoff = server.BACKOFF_FIRST
        server.BACKOFF_FIRST = 0.2

    def tearDown(self):
        server.BACKOFF_FIRST = self.backoff
        wait_for(lambda: server.SERVER.state == "ready")
        self.h.wait_ready()

    def test_environment(self):
        env = server.environment({**settings.all(), "max_loaded": 2, "context": 4096, "night_unload_min": 7})
        self.assertEqual(env["OLLAMA_HOST"], f"127.0.0.1:{config.OLLAMA_PORT}")
        self.assertEqual(env["OLLAMA_MODELS"], config.MODELS_DIR)
        self.assertEqual((env["OLLAMA_MAX_LOADED_MODELS"], env["OLLAMA_NUM_PARALLEL"], env["OLLAMA_KEEP_ALIVE"],
                          env["OLLAMA_CONTEXT_LENGTH"]), ("2", "1", "7m", "4096"))
        self.assertNotIn("SUPERVISOR_TOKEN", env)
        cmd = server.command()
        if os.path.basename(cmd[0]) == "nice":
            self.assertEqual(cmd[1:3], ["-n", "10"])          # Home Assistant stays responsive
        self.assertEqual(cmd[-1], "serve")

    def test_a_missing_llama_server_is_reported(self):
        old_bin, old_path = config.OLLAMA_BIN, server.LLAMA_SERVER
        try:
            config.OLLAMA_BIN = "ollama"
            server.LLAMA_SERVER = os.path.join(config.DATA_DIR, "no-such-llama-server")
            self.assertIn("no-such-llama-server", server.image_problem())
            self.assertIn("rebuild", server.SERVER.status()["problem"])
            server.LLAMA_SERVER = __file__                     # any file that exists
            self.assertIsNone(server.image_problem())
        finally:
            config.OLLAMA_BIN, server.LLAMA_SERVER = old_bin, old_path
        self.assertIsNone(server.SERVER.status()["problem"])     # the tests' stand-in: not checked

    def test_the_started_process_got_the_environment(self):
        self.h.run(server.SERVER.restart())
        wait_for(lambda: server.SERVER.state == "ready")
        env = next(e["env"] for e in self.h.log() if "env" in e)
        self.assertEqual(env["OLLAMA_NUM_PARALLEL"], "1")
        self.assertEqual(env["OLLAMA_HOST"], f"127.0.0.1:{config.OLLAMA_PORT}")

    def test_restarts_after_an_exit(self):
        pid = server.SERVER.proc.pid
        os.killpg(os.getpgid(pid), signal.SIGTERM)
        wait_for(lambda: server.SERVER.proc.pid != pid and server.SERVER.state == "ready")
        self.assertTrue(any("exit code" in l for l in server.SERVER.log))

    def test_killed_for_lack_of_memory_says_so(self):
        server.BACKOFF_FIRST = 1.5
        pid = server.SERVER.proc.pid
        os.killpg(os.getpgid(pid), signal.SIGKILL)
        wait_for(lambda: server.SERVER.state == "stopped")
        self.assertIn("lack of memory", server.SERVER.reason)

    def test_stop_and_start(self):
        self.h.run(server.SERVER.stop("Turned off by an admin"))
        st = server.SERVER.status()
        self.assertEqual((st["state"], st["reason"], st["on"]), ("stopped", "Turned off by an admin", False))
        self.h.run(server.SERVER.start())
        wait_for(lambda: server.SERVER.state == "ready")
        self.assertEqual(server.SERVER.version, "0.40.0")


if __name__ == "__main__":
    unittest.main()
