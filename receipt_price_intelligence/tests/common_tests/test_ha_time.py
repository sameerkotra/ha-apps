# Shared file: edit common/tests/test_ha_time.py and run tools/sync_common.py; don't edit this copy. sha256=2697d499d36a575a259afc81578a476b53cf2ed25bf1e72e936f224aa6ec65e8
"""Tests for the shared Home Assistant time zone helper (common/python/ha_time.py): the zone read from Home
Assistant; inside Home Assistant the Supervisor's TZ while Home Assistant doesn't answer yet, the process
following, and a keeper that asks again until it answers; outside Home Assistant nothing changes."""
import os
import unittest
from datetime import datetime, timezone
from unittest import mock

from app.common import ha_time


class _Stop(Exception):
    pass


def answers(*cfgs):
    """A fake fetch_config_blocking giving these answers in turn (None = Home Assistant didn't answer)."""
    queue = list(cfgs)

    def fetch(**kw):
        return queue.pop(0) if queue else None
    return fetch


class ZoneTests(unittest.TestCase):
    def test_default_and_set(self):
        z = ha_time.Zone(mock.Mock())
        self.assertEqual(z.name, "UTC")
        self.assertTrue(z.set("America/Chicago"))
        late = datetime(2026, 10, 8, 2, 30, tzinfo=timezone.utc)          # 8 Oct in UTC …
        self.assertEqual(z.today(late).isoformat(), "2026-10-07")       # … still 7 Oct in Chicago
        self.assertFalse(z.set("Not/AZone"))
        z.log.warning.assert_called_once()
        self.assertEqual(z.name, "America/Chicago")

    def test_supervisor_zone(self):
        with mock.patch.dict(os.environ, {"TZ": "Asia/Tokyo"}):
            self.assertEqual(ha_time.supervisor_zone(), "Asia/Tokyo")
        with mock.patch.dict(os.environ, {"TZ": "Not/AZone"}):
            self.assertIsNone(ha_time.supervisor_zone())
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(ha_time.supervisor_zone())

    def test_inside_home_assistant_needs_token_and_options(self):
        with mock.patch.dict(os.environ, {"SUPERVISOR_TOKEN": "t"}), \
                mock.patch.object(ha_time, "OPTIONS_FILE", __file__):
            self.assertTrue(ha_time.inside_home_assistant())
        with mock.patch.dict(os.environ, {"SUPERVISOR_TOKEN": "t"}), \
                mock.patch.object(ha_time, "OPTIONS_FILE", "/no/such/options.json"):
            self.assertFalse(ha_time.inside_home_assistant())


class StartTests(unittest.TestCase):
    def setUp(self):
        self.applied = []
        self.kept = []
        p = [mock.patch.object(ha_time, "apply_to_process", side_effect=lambda n: self.applied.append(n) or True),
             mock.patch.object(ha_time, "keep_current", side_effect=lambda z, *a, **k: self.kept.append(z) or True)]
        for x in p:
            x.start()
            self.addCleanup(x.stop)

    def start(self, inside, *cfgs, tz="America/Chicago"):
        z = ha_time.Zone()
        seen = []
        self.log = mock.Mock()
        with mock.patch.object(ha_time.ha_client, "fetch_config_blocking", answers(*cfgs)), \
                mock.patch.object(ha_time, "inside_home_assistant", return_value=inside), \
                mock.patch.dict(os.environ, {"TZ": tz}):
            name = ha_time.start_blocking(z, on_config=seen.append, log=self.log)
        return z, name, seen

    def test_home_assistant_answers(self):
        z, name, seen = self.start(True, {"time_zone": "Europe/Berlin", "currency": "EUR"})
        self.assertEqual((name, z.name, z.from_home_assistant), ("Europe/Berlin", "Europe/Berlin", True))
        self.assertEqual(seen, [{"time_zone": "Europe/Berlin", "currency": "EUR"}])
        self.assertEqual(self.applied, ["Europe/Berlin"])
        self.assertEqual(self.kept, [z])
        self.log.info.assert_called_once_with("Using Home Assistant's time zone: %s", "Europe/Berlin")

    def test_not_answering_yet_uses_the_supervisors_zone_not_utc(self):
        z, name, _ = self.start(True)
        self.assertIn("from the Supervisor", self.log.warning.call_args[0][0])
        self.assertIsNone(name)
        self.assertEqual((z.name, z.from_home_assistant), ("America/Chicago", False))
        self.assertEqual(self.applied, ["America/Chicago"])
        self.assertEqual(self.kept, [z])                              # and it keeps asking

    def test_outside_home_assistant_nothing_changes(self):
        z, name, _ = self.start(False)
        self.assertIn("staying on the %s default", self.log.warning.call_args[0][0])
        self.assertEqual(z.name, "UTC")
        self.assertEqual((self.applied, self.kept), ([], []))


class KeeperTests(unittest.TestCase):
    def run_keeper(self, *cfgs, sleeps=4):
        self.log = mock.Mock()
        z = ha_time.Zone()
        z.set("America/Chicago")
        waits, applied = [], []

        def sleep(s):
            waits.append(s)
            if len(waits) > sleeps:
                raise _Stop
        with mock.patch.object(ha_time.time, "sleep", sleep), \
                mock.patch.object(ha_time.ha_client, "fetch_config_blocking", answers(*cfgs)), \
                mock.patch.object(ha_time, "apply_to_process", side_effect=lambda n: applied.append(n) or True):
            with self.assertRaises(_Stop):
                ha_time._keep_current(z, None, self.log, {})
        return z, waits, applied

    def test_asks_until_home_assistant_answers_then_refreshes(self):
        z, waits, applied = self.run_keeper(None, None, {"time_zone": "Europe/Berlin"}, {"time_zone": "Europe/Berlin"})
        self.log.info.assert_called_once()                         # said once, when the zone changed
        self.assertEqual(waits[:3], [ha_time.RETRY_SECONDS] * 3)
        self.assertEqual(waits[3:], [ha_time.REFRESH_SECONDS] * 2)
        self.assertEqual(z.name, "Europe/Berlin")
        self.assertEqual(applied, ["Europe/Berlin", "Europe/Berlin"])

    def test_slows_down_after_ten_minutes(self):
        z, waits, _ = self.run_keeper(sleeps=ha_time.RETRY_TRIES + 2)
        self.assertEqual(waits[ha_time.RETRY_TRIES - 1], ha_time.RETRY_SECONDS)
        self.assertEqual(waits[ha_time.RETRY_TRIES], ha_time.SLOW_RETRY_SECONDS)
        self.assertEqual(z.name, "America/Chicago")

    def test_keep_current_only_inside_home_assistant(self):
        with mock.patch.object(ha_time, "inside_home_assistant", return_value=False):
            self.assertFalse(ha_time.keep_current(ha_time.Zone()))


class ProcessTests(unittest.TestCase):
    def test_apply_to_process(self):
        with mock.patch.dict(os.environ, {"TZ": "UTC"}), mock.patch.object(ha_time.time, "tzset", create=True) as tzset:
            self.assertTrue(ha_time.apply_to_process("Asia/Tokyo"))
            self.assertEqual(os.environ["TZ"], "Asia/Tokyo")
            tzset.assert_called_once()
            self.assertFalse(ha_time.apply_to_process("Not/AZone"))
            self.assertEqual(os.environ["TZ"], "Asia/Tokyo")


if __name__ == "__main__":
    unittest.main()
