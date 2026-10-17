"""Guest Wi-Fi page (nothing is sent to Home Assistant)."""
from base import ADMIN, NEHA, VIKRAM, ApiTestCase, sql

from app import alerts, guest_wifi, ha_client, qrcode


class GuestWifi(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.calls = []
        self._req, self._tok = ha_client.request, ha_client.has_token
        ha_client.has_token = lambda: True
        ha_client.request = lambda m, p, body=None, timeout=10: (self.calls.append((m, p, body)) or (200, b"{}"))
        self.sent = []
        self._orig = alerts.deliver
        alerts.deliver = lambda services, title, message: self.sent.append(message)
        self.set_up(ADMIN)
        self.set_up(NEHA)
        self.unlock(ADMIN)
        self.ok(self.post(f"/api/admin/users/{NEHA['id']}/notify", {"service": "notify.mobile_app_neha"}), 201)
        self.hh = self.vaults()["Household"]["id"]
        self.wifi = self.add_item(self.hh, title="Guest network", username="Sharma-Guest", password="sunny-day-42", template="wifi")

    def tearDown(self):
        ha_client.request, ha_client.has_token = self._req, self._tok
        alerts.deliver = self._orig
        super().tearDown()

    def publish(self, **body):
        return self.put(f"/api/vaults/{self.hh}/items/{self.wifi['id']}/guest-wifi", dict({"security": "WPA"}, **body))

    def test_publish_page_and_follow_the_item(self):
        self.ok(self.publish())
        self.assertTrue(any("put the guest Wi-Fi (Sharma-Guest)" in m for m in self.sent))
        self.assertTrue(self.ok(self.get(f"/api/vaults/{self.hh}/items/{self.wifi['id']}"))["guestWifi"])
        # the page works without unlocking
        g = self.ok(self.get("/api/guest-wifi", NEHA, token=False))
        self.assertEqual((g["ssid"], g["security"]), ("Sharma-Guest", "WPA"))
        self.assertNotIn("password", g)            # as text only when asked for (security review 2026-10); the QR has it
        self.assertIn("P:sunny-day-42;", qrcode.wifi_text("Sharma-Guest", "sunny-day-42", "WPA"))
        self.ok(self.publish(showPassword=True))
        self.assertEqual(self.ok(self.get("/api/guest-wifi", NEHA, token=False))["password"], "sunny-day-42")
        self.ok(self.publish())
        self.assertTrue(self.ok(self.get("/api/me", NEHA, token=False))["guestWifi"])
        # nothing is ever sent to Home Assistant (security review 2026-10: no sensor any more)
        self.assertEqual([c for c in self.calls if "/states/" in c[1]], [])
        self.assertNotIn("entity", g)
        # changing the item's password changes the published one
        self.ok(self.patch(f"/api/vaults/{self.hh}/items/{self.wifi['id']}", {"password": "rainy-night-7"}))
        self.assertEqual(sql("SELECT password FROM guest_wifi")[0]["password"], "rainy-night-7")
        # trashing the item takes it off the page
        self.ok(self.delete(f"/api/vaults/{self.hh}/items/{self.wifi['id']}"))
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM guest_wifi")[0]["n"], 0)
        self.assertFalse(self.ok(self.get("/api/guest-wifi", NEHA, token=False))["published"])
        self.assertEqual([c for c in self.calls if "/states/" in c[1]], [])

    def test_rules(self):
        pv = self.personal()["id"]
        mine = self.add_item(pv, title="My hotspot", username="Phone", password="x-y-z-1")
        self.assertEqual(self.put(f"/api/vaults/{pv}/items/{mine['id']}/guest-wifi", {"security": "WPA"}).status_code, 409)
        bank = self.add_item(self.hh, title="Bank", username="kiran@bank", password="S3cret-bank")     # not a Wi-Fi item
        self.assertEqual(self.put(f"/api/vaults/{self.hh}/items/{bank['id']}/guest-wifi", {"security": "WPA"}).status_code, 409)
        nopw = self.add_item(self.hh, title="Cafe", username="Cafe", template="wifi")
        self.assertEqual(self.put(f"/api/vaults/{self.hh}/items/{nopw['id']}/guest-wifi", {"security": "WPA"}).status_code, 422)
        self.ok(self.put(f"/api/vaults/{self.hh}/items/{nopw['id']}/guest-wifi", {"security": "nopass"}))
        self.assertEqual(self.ok(self.get("/api/guest-wifi", token=False))["security"], "nopass")
        # someone not set up can't see it
        self.seen(VIKRAM)
        self.assertEqual(self.get("/api/guest-wifi", VIKRAM, token=False).status_code, 403)
        # stop showing
        self.ok(self.delete("/api/guest-wifi"))
        self.assertFalse(self.ok(self.get("/api/guest-wifi", token=False))["published"])

    def test_no_longer_wifi_comes_off(self):
        self.ok(self.publish())
        self.ok(self.patch(f"/api/vaults/{self.hh}/items/{self.wifi['id']}", {"template": None}))
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM guest_wifi")[0]["n"], 0)

    def test_old_sensor_is_removed_once_at_start_up(self):
        """An older version kept a sensor in Home Assistant: start-up deletes it; nothing is ever posted."""
        state = {"exists": True}

        def req(m, p, body=None, timeout=10):
            self.calls.append((m, p, body))
            if m == "DELETE":
                state["exists"] = False
                return 200, b""
            return (200, b"{}") if state["exists"] else (404, b"")
        ha_client.request = req
        self.assertTrue(guest_wifi.remove_old_sensor_blocking())
        self.assertEqual([c[:2] for c in self.calls], [("GET", "/states/sensor.household_vault_guest_wifi"),
                                                        ("DELETE", "/states/sensor.household_vault_guest_wifi")])
        self.calls.clear()
        self.assertTrue(guest_wifi.remove_old_sensor_blocking())
        self.assertEqual([c[0] for c in self.calls], ["GET"])
