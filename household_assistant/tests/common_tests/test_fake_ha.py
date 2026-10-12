# Shared file: edit common/tests/test_fake_ha.py and run tools/sync_common.py; don't edit this copy. sha256=30b1d5071c51bc616ebf8e7f0ac6e47121857e85a238ba2649463482c326ee89
"""Tests for the shared fake Home Assistant (common/tests/fake_ha.py): it answers the way real Home Assistant
does, so an app's tests see the same answers its code gets in a real install."""
import json
import unittest
import urllib.error
import urllib.request

from common_tests.fake_ha import FakeHA


class FakeHATests(unittest.TestCase):
    def setUp(self):
        self.ha = FakeHA()
        self.base = self.ha.start()
        self.addCleanup(self.ha.stop)

    def call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"null")

    def test_one_entitys_state(self):
        self.ha.states["sensor.x"] = {"state": "3", "attributes": {"unit": "kg"}}
        self.assertEqual(self.call("GET", "/states/sensor.x"),
                         (200, {"entity_id": "sensor.x", "state": "3", "attributes": {"unit": "kg"}}))
        self.assertEqual(self.call("GET", "/states/sensor.nope")[0], 404)

    def test_other_service_calls_are_accepted(self):
        self.assertEqual(self.call("POST", "/services/logbook/log", {"name": "A", "message": "b"}), (200, []))
        self.assertEqual(self.call("POST", "/services/light/turn_on", {"entity_id": "light.x"}), (200, []))

    def test_switches_change_state(self):
        self.ha.states["switch.plug"] = {"state": "on", "attributes": {"friendly_name": "Plug"}}
        self.assertEqual(self.call("POST", "/services/switch/turn_off", {"entity_id": "switch.plug"}), (200, []))
        self.assertEqual(self.ha.states["switch.plug"], {"state": "off", "attributes": {"friendly_name": "Plug"}})
        self.call("POST", "/services/switch/turn_on", {"entity_id": ["switch.plug", "switch.unknown"]})
        self.assertEqual(self.ha.states["switch.plug"]["state"], "on")
        self.assertNotIn("switch.unknown", self.ha.states)
        self.call("POST", "/services/switch/toggle", {"entity_id": "switch.plug"})
        self.assertEqual(self.call("GET", "/states/switch.plug")[1]["state"], "off")


if __name__ == "__main__":
    unittest.main()
