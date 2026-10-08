"""Settings from the environment on a Docker host: the time zone (TZ), the ports, no Home Assistant."""
import _env  # noqa: F401  (must be first)

import importlib
import os
import unittest
from datetime import datetime, timezone
from unittest import mock

from app import config


class DockerConfig(unittest.TestCase):
    def reload(self, **env):
        with mock.patch.dict(os.environ, env):
            return importlib.reload(config)

    def tearDown(self):
        importlib.reload(config)

    def test_time_zone_from_tz(self):
        c = self.reload(TZ="Asia/Tokyo")
        self.assertEqual(c.ZONE.name, "Asia/Tokyo")
        noon = datetime(2026, 7, 1, 18, 0, tzinfo=timezone.utc)
        self.assertEqual(c.ZONE.now(noon).hour, 3)
        self.assertEqual(self.reload(TZ="Not/AZone").ZONE.name, "UTC")

    def test_ports_and_no_home_assistant(self):
        c = self.reload(WEB_PORT="9000", PUBLIC_GATEWAY_PORT="11500")
        self.assertEqual((c.WEB_PORT, c.PUBLIC_GATEWAY_PORT), (9000, 11500))
        self.assertEqual(c.SUPERVISOR_TOKEN, "")
        self.assertTrue(c.STANDALONE)


if __name__ == "__main__":
    unittest.main()
