# Shared file: edit common/tests/test_sensor_publisher.py and run tools/sync_common.py; don't edit this copy. sha256=fc1e56b9c1f78c804fed08e2868b600bd815ff3a6830c60b5e4de657852eb455
"""Tests for the shared sensor publisher (common/python/sensor_publisher.py)."""
import asyncio
import unittest

from app.common import sensor_publisher


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.ok = True
        self.clock = FakeClock()
        self.pub = sensor_publisher.Publisher(post=self.post, delete=self.delete, clock=self.clock)

    def post(self, eid, state, attrs):
        self.calls.append(("POST", eid, state, attrs))
        return self.ok

    def delete(self, eid):
        self.calls.append(("DELETE", eid))
        return self.ok

    def test_posts_only_changes_unless_forced(self):
        self.assertTrue(self.pub.post("sensor.a", "1", {"x": 1}))
        self.assertIsNone(self.pub.post("sensor.a", "1", {"x": 1}))
        self.assertTrue(self.pub.post("sensor.a", "1", {"x": 2}))
        self.assertTrue(self.pub.post("sensor.a", "1", {"x": 2}, force=True))
        self.assertEqual([c[3] for c in self.calls], [{"x": 1}, {"x": 2}, {"x": 2}])

    def test_failed_post_is_not_remembered(self):
        self.ok = False
        self.assertFalse(self.pub.post("sensor.a", "1", {}))
        self.ok = True
        self.assertTrue(self.pub.post("sensor.a", "1", {}))
        self.assertEqual(len(self.calls), 2)

    def test_state_only_key(self):
        pub = sensor_publisher.Publisher(post=self.post, key=sensor_publisher.state_only)
        pub.post("binary_sensor.a", "on", {"n": 1})
        self.assertIsNone(pub.post("binary_sensor.a", "on", {"n": 2}))
        self.assertTrue(pub.post("binary_sensor.a", "off", {"n": 2}))

    def test_publish_counts_and_stops_after_failures(self):
        items = [("sensor.a", "1", {}), ("sensor.b", "2", {}), ("sensor.c", "3", {})]
        self.assertEqual(self.pub.publish(items), {"pushed": 3, "failed": 0})
        self.assertEqual(self.pub.publish(items), {"pushed": 0, "failed": 0})       # nothing changed
        self.ok = False
        self.calls.clear()
        self.assertEqual(self.pub.publish(items, force=True, stop_after=1), {"pushed": 0, "failed": 1})
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.pub.publish(items, force=True), {"pushed": 0, "failed": 3})

    def test_remove_and_forget(self):
        self.pub.post("sensor.a", "1", {})
        self.pub.post("sensor.b", "1", {})
        self.assertEqual(self.pub.remove(["sensor.a"]), {"removed": 1, "failed": 0})
        self.assertEqual(self.pub.names(), ["sensor.b"])
        self.ok = False
        self.assertEqual(self.pub.remove(["sensor.b"]), {"removed": 0, "failed": 1})
        self.assertEqual(self.pub.names(), ["sensor.b"])       # not removed: still remembered
        self.pub.forget()
        self.assertEqual(self.pub.names(), [])

    def test_unchanged_with_max_age(self):
        self.pub.remember("home", "fp")
        self.assertTrue(self.pub.unchanged("home", "fp", max_age=900))
        self.assertFalse(self.pub.unchanged("home", "other", max_age=900))
        self.clock.t += 900
        self.assertFalse(self.pub.unchanged("home", "fp", max_age=900))
        self.assertTrue(self.pub.unchanged("home", "fp"))


class RefreshTests(unittest.TestCase):
    def test_due_first_then_by_interval_and_date(self):
        clock = FakeClock()
        every = [300]
        r = sensor_publisher.Refresh(lambda: every[0], clock=clock)
        self.assertTrue(r.due("2026-09-21"))
        r.done("2026-09-21")
        self.assertFalse(r.due("2026-09-21"))
        self.assertTrue(r.due("2026-09-22"))              # the date changed
        clock.t += 299
        self.assertFalse(r.due("2026-09-21"))
        every[0] = 200                                    # a live setting, read each time
        self.assertTrue(r.due("2026-09-21"))
        r.done("2026-09-21")
        r.reset()
        self.assertTrue(r.due("2026-09-21"))


class RunTests(unittest.TestCase):
    def test_run_survives_a_failed_tick_and_stops_when_cancelled(self):
        ticks = []

        async def tick():
            ticks.append(1)
            if len(ticks) == 1:
                raise RuntimeError("boom")

        async def main():
            task = asyncio.ensure_future(sensor_publisher.run(0, tick))
            while len(ticks) < 3:
                await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        with self.assertLogs("sensor_publisher", "ERROR"):
            asyncio.run(main())
        self.assertGreaterEqual(len(ticks), 3)


if __name__ == "__main__":
    unittest.main()
