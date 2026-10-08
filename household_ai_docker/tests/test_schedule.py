"""The day/night rules (app/schedule.py) and the schedule loop (server.Schedule) with a fake clock (SPEC.md §6.2)."""
import _env  # noqa: F401  (must be first)

import unittest
from datetime import datetime

import harness
from app import schedule, server, settings

V = {"day_start": "05:00", "day_end": "22:00", "day_unload_min": 10, "night_unload_min": 10, "keep_model": ""}


def at(h, m=0):
    return datetime(2026, 10, 7, h, m)


class Rules(unittest.TestCase):
    def test_daytime_window(self):
        self.assertTrue(schedule.in_daytime(at(5), "05:00", "22:00"))
        self.assertTrue(schedule.in_daytime(at(21, 59), "05:00", "22:00"))
        self.assertFalse(schedule.in_daytime(at(22), "05:00", "22:00"))
        self.assertFalse(schedule.in_daytime(at(4, 59), "05:00", "22:00"))
        # passing midnight
        self.assertTrue(schedule.in_daytime(at(23), "22:00", "06:00"))
        self.assertTrue(schedule.in_daytime(at(3), "22:00", "06:00"))
        self.assertFalse(schedule.in_daytime(at(12), "22:00", "06:00"))
        self.assertTrue(schedule.in_daytime(at(12), "07:00", "07:00"))        # all day

    def test_keep_alive(self):
        kept = "qwen2.5:3b"
        self.assertEqual(schedule.keep_alive("qwen2.5:3b", V, at(12), kept), -1)
        self.assertEqual(schedule.keep_alive("qwen2.5vl:7b", V, at(12), kept), 600)
        self.assertEqual(schedule.keep_alive("qwen2.5:3b", V, at(23), kept), 600)
        self.assertEqual(schedule.keep_alive("qwen2.5:3b", {**V, "night_unload_min": 0}, at(23), kept), 0)
        self.assertEqual(schedule.keep_alive("qwen2.5", V, at(12), "qwen2.5:latest"), -1)

    def test_kept_model(self):
        self.assertEqual(schedule.kept_model(V, ["a:1", "b:2"]), "a:1")
        self.assertIsNone(schedule.kept_model(V, []))
        self.assertIsNone(schedule.kept_model({**V, "keep_model": "none"}, ["a:1"]))
        self.assertEqual(schedule.kept_model({**V, "keep_model": "llama3.2"}, ["a:1"]), "llama3.2:latest")

    def test_night_keep_alive(self):
        self.assertEqual(schedule.night_keep_alive(0, V), 600)
        self.assertEqual(schedule.night_keep_alive(240, V), 360)
        self.assertEqual(schedule.night_keep_alive(3600, V), 0)        # idle longer: unload at once


class Loop(unittest.TestCase):
    """server.Schedule against the fake model server, with its own clock."""

    @classmethod
    def setUpClass(cls):
        cls.h = harness.get()

    def setUp(self):
        self.h.reset()
        self.now = at(4, 59)
        self.s = server.Schedule(harness.gateway.QUEUE, {}, clock=lambda: self.now)
        self.h.run(self.s.tick())                      # night: nothing
        self.h.run(self.s.unload_now())
        self.s.manual_unload = False
        open(harness.LOG, "w").close()

    def loads(self):
        return [(b["model"], b["keep_alive"]) for b in self.h.requests("/api/generate") if "prompt" not in b]

    def test_start_of_the_day_loads_the_kept_model(self):
        self.now = at(5)
        self.h.run(self.s.tick())
        self.assertEqual(self.loads(), [("qwen2.5:3b", -1)])
        # loaded: the next tick does nothing
        self.h.run(self.s.tick())
        self.assertEqual(len(self.loads()), 1)

    def test_no_preload(self):
        settings.update({"preload": False}, None)
        self.now = at(5)
        self.h.run(self.s.tick())
        self.assertEqual(self.loads(), [])

    def test_keep_none(self):
        settings.update({"keep_model": "none"}, None)
        self.now = at(5)
        self.h.run(self.s.tick())
        self.assertEqual(self.loads(), [])

    def test_end_of_the_day_shortens_every_loaded_model(self):
        self.now = at(5)
        self.h.run(self.s.tick())
        self.s.last_used["qwen2.5:3b"] = __import__("time").monotonic() - 240     # used 4 minutes ago
        open(harness.LOG, "w").close()
        self.now = at(22)
        self.h.run(self.s.tick())
        (model, ka), = self.loads()
        self.assertEqual(model, "qwen2.5:3b")
        self.assertTrue(355 <= ka <= 360, ka)

    def test_unload_now_holds_until_the_next_day(self):
        self.now = at(12)
        self.h.run(self.s.tick())
        self.assertEqual(len(self.loads()), 1)
        self.h.run(self.s.unload_now())
        open(harness.LOG, "w").close()
        self.h.run(self.s.tick())
        self.assertEqual(self.loads(), [])                     # not loaded again while unloaded by hand
        self.now = at(23)
        self.h.run(self.s.tick())
        self.now = at(5)
        self.h.run(self.s.tick())
        self.assertEqual(self.loads(), [("qwen2.5:3b", -1)])

    def test_kept_model_comes_back_once_nothing_else_is_loaded(self):
        self.now = at(12)
        self.h.run(self.s.tick())
        self.h.run(server.ollama.set_keep_alive("qwen2.5:3b", 0))         # a vision request displaced it
        self.h.run(server.ollama.set_keep_alive("qwen2.5vl:3b", 600))
        open(harness.LOG, "w").close()
        self.h.run(self.s.tick())
        self.assertEqual(self.loads(), [])                     # the vision model is still loaded
        self.h.run(server.ollama.set_keep_alive("qwen2.5vl:3b", 0))
        open(harness.LOG, "w").close()
        self.h.run(self.s.tick())
        self.assertEqual(self.loads(), [("qwen2.5:3b", -1)])

    def test_describe(self):
        self.now = at(12)
        rows = self.s.describe([{"name": "qwen2.5:3b"}, {"name": "qwen2.5vl:3b"}])
        self.assertIn("kept until 22:00", rows[0]["rule"])
        self.assertIn("10 min after its last use", rows[1]["rule"])


if __name__ == "__main__":
    unittest.main()
