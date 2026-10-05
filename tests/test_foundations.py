"""The shared Home Assistant client, time zone and background-job modules (common/python/ha_client.py,
ha_time.py, housekeeping.py) against the fake Core API (common/tests/fake_ha.py). The apps' own suites
cover how each app uses them; this checks the shared behaviour once. Standard library + Starlette."""
import asyncio
import logging
import os
import sys
import types
import unittest
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# ha_client reads the app's config (`from .. import config`); here that is `common.config`.
fake_config = types.ModuleType("common.config")
fake_config.SUPERVISOR_TOKEN = ""
fake_config.SUPERVISOR_CORE_API = "http://127.0.0.1:9/api"
sys.modules["common.config"] = fake_config

from common.python import ha_client, ha_time, housekeeping  # noqa: E402
from common.tests.fake_ha import FakeHA  # noqa: E402


class HAClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ha = FakeHA(time_zone="Europe/Berlin")
        cls.url = cls.ha.start()

    @classmethod
    def tearDownClass(cls):
        cls.ha.stop()

    def setUp(self):
        fake_config.SUPERVISOR_CORE_API, fake_config.SUPERVISOR_TOKEN = self.url, "tok"
        self.ha.requests.clear()
        self.ha.states.clear()
        self.ha.fail = False
        ha_client._states_cache = None
        ha_client._warned_no_token = False

    def test_request_answers_and_errors(self):
        status, body = ha_client.request("GET", "/config")
        self.assertEqual((status, body), (200, b'{"time_zone": "Europe/Berlin"}'))
        self.assertEqual(self.ha.requests[-1][3], "Bearer tok")       # read from config at call time
        self.assertEqual(ha_client.request("GET", "/nope")[0], 404)
        fake_config.SUPERVISOR_CORE_API = "http://127.0.0.1:9/api"    # nothing listens there
        self.assertEqual(ha_client.request("GET", "/config", timeout=2), (None, b""))
        seen = []
        self.assertEqual(ha_client.request("GET", "/config", timeout=2, on_error=seen.append), (None, b""))
        self.assertEqual(len(seen), 1)
        # an explicit address and token (apps without a config module)
        self.assertEqual(ha_client.request("GET", "/config", base_url=self.url, token="other")[0], 200)
        self.assertEqual(self.ha.requests[-1][3], "Bearer other")

    def test_states(self):
        self.assertTrue(ha_client.post_state("sensor.a", "1", {"unit_of_measurement": "x"}))
        self.assertEqual(self.ha.states["sensor.a"]["state"], "1")
        states = ha_client.fetch_states_blocking()
        self.assertEqual([s["entity_id"] for s in states], ["sensor.a"])
        self.ha.states.clear()
        self.assertEqual(len(ha_client.fetch_states_blocking()), 1)      # cached for 30 s
        self.assertEqual(ha_client.fetch_states_blocking(max_age=0), [])
        self.assertTrue(ha_client.delete_state("sensor.gone"))           # 404 = already gone
        self.ha.fail = True
        self.assertFalse(ha_client.post_state("sensor.a", "1", {}))
        with self.assertLogs("ha_client", "WARNING"):
            self.assertIsNone(ha_client.fetch_states_blocking(max_age=0))

    def test_persons(self):
        self.ha.states.update({"person.ann": {"state": "home", "attributes": {"user_id": "u1", "friendly_name": "Ann"}},
                               "person.dog": {"state": "home", "attributes": {}},
                               "sensor.x": {"state": "1", "attributes": {"user_id": "u9"}}})
        self.assertEqual(ha_client.fetch_persons_blocking(),
                         [{"user_id": "u1", "name": "Ann", "entity_id": "person.ann"}])

    def test_no_token(self):
        fake_config.SUPERVISOR_TOKEN = ""
        self.assertFalse(ha_client.has_token())
        with self.assertLogs("ha_client", "WARNING") as logs:
            self.assertFalse(ha_client.post_state("sensor.a", "1", {}))
            self.assertFalse(ha_client.delete_state("sensor.a"))
            ha_client.warn_no_token_once("x")
        self.assertEqual(len(logs.records), 1)                           # once per process
        self.assertIsNone(ha_client.fetch_states_blocking())
        self.assertIsNone(ha_client.fetch_config_blocking())
        self.assertEqual(self.ha.requests, [])

    def test_config(self):
        self.assertEqual(ha_client.fetch_config_blocking(), {"time_zone": "Europe/Berlin"})
        self.ha.fail = True
        self.assertIsNone(ha_client.fetch_config_blocking())
        log = logging.getLogger("test-config")
        with self.assertLogs(log, "WARNING") as logs:
            self.assertIsNone(ha_client.fetch_config_blocking(log=log))
        self.assertIn("HTTP 500", logs.output[0])


class HATimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ha = FakeHA(time_zone="Asia/Kolkata")
        cls.url = cls.ha.start()

    @classmethod
    def tearDownClass(cls):
        cls.ha.stop()

    def setUp(self):
        fake_config.SUPERVISOR_CORE_API, fake_config.SUPERVISOR_TOKEN = self.url, "tok"
        self.ha.fail = False
        self.ha.time_zone = "Asia/Kolkata"

    def test_zone(self):
        zone = ha_time.Zone(logging.getLogger("test-zone"))
        self.assertEqual(zone.name, "UTC")
        late = datetime(2026, 9, 21, 20, 0, tzinfo=timezone.utc)        # already the 22nd in Kolkata
        self.assertEqual(zone.today(late).isoformat(), "2026-09-21")
        self.assertTrue(zone.set("Asia/Kolkata"))
        self.assertEqual((zone.name, zone.today(late).isoformat()), ("Asia/Kolkata", "2026-09-22"))
        self.assertEqual(zone.now(late).utcoffset().total_seconds(), 5.5 * 3600)
        with self.assertLogs("test-zone", "WARNING"):
            self.assertFalse(zone.set("Not/AZone"))
        self.assertFalse(zone.set(""))
        self.assertEqual(zone.name, "Asia/Kolkata")                     # unchanged
        self.assertIs(ha_time.Zone(tz=timezone.utc).tz, timezone.utc)   # an app's own fallback object

    def test_load(self):
        zone, seen = ha_time.Zone(), []
        self.assertEqual(ha_time.load_blocking(zone, seen.append), "Asia/Kolkata")
        self.assertEqual((zone.name, seen), ("Asia/Kolkata", [{"time_zone": "Asia/Kolkata"}]))
        self.ha.time_zone = "Not/AZone"
        with self.assertLogs("ha_time", "WARNING"):
            self.assertIsNone(ha_time.load_blocking(zone))
        self.assertEqual(zone.name, "Asia/Kolkata")
        log = logging.getLogger("test-load")
        self.ha.time_zone = "Europe/Paris"
        with self.assertLogs(log, "INFO") as logs:
            asyncio.run(ha_time.load(zone, log=log))
        self.assertEqual(logs.output, ["INFO:test-load:Using Home Assistant's time zone: Europe/Paris"])
        self.ha.fail = True
        with self.assertLogs(log, "INFO") as logs:
            asyncio.run(ha_time.load(zone, log=log, details=True))
        self.assertEqual(len(logs.output), 2)                           # why, then which zone stays
        self.assertIn("staying on the Europe/Paris default", logs.output[1])


class JobsTests(unittest.TestCase):
    def test_every_add_and_stop(self):
        calls, log = [], logging.getLogger("test-jobs")

        def blocking(n):
            calls.append(("thread", n))
            if n == 1:
                raise RuntimeError("boom")

        async def later():
            calls.append("later")

        async def own_loop():
            while True:
                calls.append("own")
                await asyncio.sleep(10)

        async def main():
            jobs = housekeeping.Jobs()
            jobs.every("a", 0.01, blocking, tick=True, log=log, error="A failed")
            jobs.every("b", 0.05, later, thread=False, at_start=False, log=log)
            jobs.add("c", own_loop)
            self.assertEqual(jobs.names, ["a", "b", "c"])
            jobs.start()
            self.assertEqual([t.get_name() for t in jobs.tasks], ["a", "b", "c"])
            await asyncio.sleep(0.12)
            tasks = list(jobs.tasks)
            await jobs.stop()
            self.assertTrue(all(t.done() for t in tasks))
            self.assertEqual(jobs.tasks, [])

        with self.assertLogs(log, "ERROR") as logs:
            asyncio.run(main())
        self.assertEqual(logs.output[0].splitlines()[0], "ERROR:test-jobs:A failed")   # with the traceback
        self.assertEqual(sorted(map(str, calls[:2])), sorted(map(str, [("thread", 0), "own"])))   # both at once
        self.assertIn(("thread", 2), calls)                             # carried on after the failure
        self.assertEqual(calls.count("own"), 1)
        self.assertIn(calls.count("later"), (1, 2))                    # b waited one interval first (0.05 s)

    def test_warning_without_traceback(self):
        log = logging.getLogger("test-jobs-2")

        async def fails():
            raise ValueError("nope")

        async def main():
            loop = housekeeping.periodic(10, fails, thread=False, log=log, error="Sync failed", traceback=False)
            task = asyncio.create_task(loop())
            await asyncio.sleep(0.01)
            task.cancel()

        with self.assertLogs(log, "WARNING") as logs:
            asyncio.run(main())
        self.assertEqual(logs.output, ["WARNING:test-jobs-2:Sync failed: nope"])


if __name__ == "__main__":
    unittest.main()
