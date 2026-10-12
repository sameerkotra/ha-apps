"""The app as it runs inside Home Assistant: with a Supervisor token, start-up and every call to Home Assistant's Core
API (time zone, people, sensors, notifications, switches, the logbook) go through the shared ha_client — against a
fake Home Assistant. Without a token those paths are skipped, so the other tests can't catch a missing setting."""
import _env  # noqa: F401  (must be first)

import unittest
from unittest import mock

from app import config, main, restarts, sidebar, watchdog
from common_tests.fake_ha import FakeHA
from common_tests.ingress import ingress_client


class InsideHomeAssistant(unittest.TestCase):
    def setUp(self):
        self.ha = FakeHA(time_zone="Europe/Berlin")
        url = self.ha.start()
        self.addCleanup(self.ha.stop)
        for p in (mock.patch.object(config, "SUPERVISOR_TOKEN", "test-token"),
                  mock.patch.object(config, "SUPERVISOR_CORE_API", url),
                  mock.patch.object(sidebar, "load_blocking", lambda: None)):
            p.start()
            self.addCleanup(p.stop)

    def test_config_has_what_the_shared_modules_need(self):
        for name in ("SUPERVISOR_TOKEN", "SUPERVISOR_CORE_API", "DATA_DIR", "DB_PATH", "ADMIN_USERS", "ZONE"):
            self.assertTrue(hasattr(config, name), name)

    def test_start_up_and_every_call(self):
        with ingress_client(main.app) as c:                       # the lifespan runs: time zone, people, …
            self.assertEqual(c.get("/api/health").json(), {"status": "ok"})
        paths = [p for _, p, _, _ in self.ha.requests]
        self.assertIn("/api/config", paths)
        self.assertTrue(all(a == "Bearer test-token" for _, _, _, a in self.ha.requests))
        self.assertTrue(watchdog.SENSORS.post("binary_sensor.household_proxy_x", "on", {"device_class": "connectivity"}))
        self.assertTrue(watchdog.send("mobile_app_ann", "Box is down", "Not answering.", {}))
        self.ha.states["switch.box_plug"] = {"state": "on", "attributes": {"friendly_name": "Box plug"}}
        restarts.ha_state("switch.box_plug")
        restarts.ha_call("turn_off", "switch.box_plug")
        restarts.ha_logbook("Household Proxy", "restarted Box", "switch.box_plug")
        found = restarts.switches()
        self.assertEqual(found[0]["entity_id"], "switch.box_plug")
        calls = [(m, p) for m, p, _, _ in self.ha.requests]
        for want in (("POST", "/api/states/binary_sensor.household_proxy_x"),
                     ("POST", "/api/services/notify/mobile_app_ann"), ("GET", "/api/states/switch.box_plug"),
                     ("POST", "/api/services/switch/turn_off"), ("POST", "/api/services/logbook/log"),
                     ("GET", "/api/states")):
            self.assertIn(want, calls)
        self.assertEqual([b for m, p, b, _ in self.ha.requests if p == "/api/services/switch/turn_off"],
                         [{"entity_id": "switch.box_plug"}])


if __name__ == "__main__":
    unittest.main()
