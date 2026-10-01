"""Guest Wi-Fi on the Home Assistant dashboard."""
import base64

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
        self._push = guest_wifi.push_async
        guest_wifi.push_async = lambda delay=1.0: None           # tests call push_blocking themselves
        self.set_up(ADMIN)
        self.set_up(NEHA)
        self.unlock(ADMIN)
        self.ok(self.post(f"/api/admin/users/{NEHA['id']}/notify", {"service": "notify.mobile_app_neha"}, token=False), 201)
        self.hh = self.vaults()["Household"]["id"]
        self.wifi = self.add_item(self.hh, title="Guest network", username="Sharma-Guest", password="sunny-day-42", template="wifi")

    def tearDown(self):
        ha_client.request, ha_client.has_token = self._req, self._tok
        alerts.deliver = self._orig
        guest_wifi.push_async = self._push
        super().tearDown()

    def publish(self, **body):
        return self.put(f"/api/vaults/{self.hh}/items/{self.wifi['id']}/guest-wifi", dict({"security": "WPA"}, **body))

    def test_publish_sensor_page_and_follow_the_item(self):
        self.ok(self.publish())
        self.assertTrue(any("put the guest Wi-Fi (Sharma-Guest)" in m for m in self.sent))
        self.assertTrue(self.ok(self.get(f"/api/vaults/{self.hh}/items/{self.wifi['id']}"))["guestWifi"])
        # the page works without unlocking
        g = self.ok(self.get("/api/guest-wifi", NEHA, token=False))
        self.assertEqual((g["ssid"], g["password"], g["security"]), ("Sharma-Guest", "sunny-day-42", "WPA"))
        self.assertTrue(self.ok(self.get("/api/me", NEHA, token=False))["guestWifi"])
        # the sensor: network name as state, the QR code as its picture, no password attribute unless asked
        guest_wifi.push_blocking()
        m, path, body = self.calls[-1]
        self.assertEqual((m, path, body["state"]), ("POST", "/states/sensor.household_vault_guest_wifi", "Sharma-Guest"))
        self.assertNotIn("password", body["attributes"])
        svg = base64.b64decode(body["attributes"]["entity_picture"].split(",", 1)[1]).decode()
        self.assertEqual(svg, qrcode.svg(qrcode.wifi_text("Sharma-Guest", "sunny-day-42", "WPA")))
        self.ok(self.publish(showPassword=True))
        guest_wifi.push_blocking()
        self.assertEqual(self.calls[-1][2]["attributes"]["password"], "sunny-day-42")
        # changing the item's password changes the published one
        self.ok(self.patch(f"/api/vaults/{self.hh}/items/{self.wifi['id']}", {"password": "rainy-night-7"}))
        self.assertEqual(sql("SELECT password FROM guest_wifi")[0]["password"], "rainy-night-7")
        # trashing the item takes it off the dashboard
        self.ok(self.delete(f"/api/vaults/{self.hh}/items/{self.wifi['id']}"))
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM guest_wifi")[0]["n"], 0)
        self.assertFalse(self.ok(self.get("/api/guest-wifi", NEHA, token=False))["published"])
        guest_wifi.push_blocking()
        self.assertEqual(self.calls[-1][:2], ("DELETE", "/states/sensor.household_vault_guest_wifi"))

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

    def test_ensure_puts_it_back_after_ha_restarts(self):
        self.ok(self.publish())
        ha_client.request = lambda m, p, body=None, timeout=10: (self.calls.append((m, p, body)) or ((404, b"") if m == "GET" else (200, b"{}")))
        guest_wifi.ensure_blocking()
        self.assertEqual([c[0] for c in self.calls[-2:]], ["GET", "POST"])
