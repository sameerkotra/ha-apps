"""The app's config module has what the shared Home Assistant client reads from it (app/common/ha_client.py
looks up `config.<NAME>` when it runs, so a missing name only shows once there is a Supervisor token — on a real
install, at start-up)."""
import _env  # noqa: F401  (must be first)

import os
import re
import unittest

from app import config
from app.common import ha_client, ha_time

COMMON = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "common")


class SharedClientNeeds(unittest.TestCase):
    def test_every_config_name_the_shared_code_reads_exists(self):
        names = set()
        for n in os.listdir(COMMON):
            if n.endswith(".py"):
                with open(os.path.join(COMMON, n), encoding="utf-8") as f:
                    names |= set(re.findall(r"_config\(\)\.([A-Z_]+)", f.read()))
        self.assertIn("SUPERVISOR_CORE_API", names)
        for name in sorted(names):
            self.assertTrue(hasattr(config, name), f"app/config.py has no {name}")
        self.assertEqual(config.SUPERVISOR_CORE_API, "http://supervisor/core/api")

    def test_reading_the_time_zone_with_a_token_fails_softly(self):
        old = config.SUPERVISOR_CORE_API
        config.SUPERVISOR_CORE_API = "http://127.0.0.1:9/core/api"      # nothing listens there
        try:
            zone = ha_time.Zone()
            self.assertIsNone(ha_time.load_blocking(zone, token="a-token", timeout=2))
            self.assertEqual(zone.name, "UTC")
        finally:
            config.SUPERVISOR_CORE_API = old
        self.assertTrue(callable(ha_client.fetch_config_blocking))


if __name__ == "__main__":
    unittest.main()
