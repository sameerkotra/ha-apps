"""Emergency access (SPEC §12.4)."""
from base import ADMIN, NEHA, VIKRAM, ApiTestCase, sql

from app import alerts


class Emergency(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.sent = []
        self._orig = alerts.deliver
        alerts.deliver = lambda services, title, message: self.sent.append((tuple(services), message))
        self.set_up(ADMIN)
        self.set_up(NEHA)
        self.unlock(ADMIN)
        for u in (ADMIN, NEHA):
            self.ok(self.post(f"/api/admin/users/{u['id']}/notify", {"service": f"notify.mobile_app_{u['name']}"},
                              ADMIN, token=False), 201)
        self.pv = self.personal()["id"]
        self.will = self.add_item(self.pv, title="Will and bank details", username="kiran", password="bank-pw-123")
        self.other = self.add_item(self.pv, title="Private diary", password="diary-pw")

    def tearDown(self):
        alerts.deliver = self._orig
        super().tearDown()

    def msgs(self, user):
        return [m for s, m in self.sent if f"mobile_app_{user['name']}" in s]

    def mark(self, item, include=True):
        return self.ok(self.put(f"/api/vaults/{self.pv}/items/{item['id']}/emergency", {"include": include}))

    def em_vault(self, user):
        return next((v for v in self.ok(self.get("/api/vaults", user))["vaults"] if v["kind"] == "emergency"), None)

    def grant_neha(self, wait=3):
        self.mark(self.will)
        self.ok(self.post("/api/emergency/contacts", {"userId": NEHA["id"], "waitDays": wait}))
        self.ok(self.post(f"/api/emergency/{ADMIN['id']}/request", user=NEHA))
        sql("UPDATE emergency_contacts SET requested_at = '2020-01-01T00:00:00.000Z'")
        return self.ok(self.get("/api/emergency", NEHA))

    def test_request_wait_then_granted_read_only(self):
        self.mark(self.will)
        self.assertTrue(self.ok(self.get(f"/api/vaults/{self.pv}/items/{self.will['id']}"))["emergency"])
        r = self.ok(self.post("/api/emergency/contacts", {"userId": NEHA["id"], "waitDays": 3}))
        self.assertEqual(r["mine"]["itemCount"], 1)
        self.assertEqual([(c["name"], c["status"], c["waitDays"]) for c in r["mine"]["contacts"]], [("Neha", "ready", 3)])
        self.assertTrue(any("made you an emergency contact" in m for m in self.msgs(NEHA)))
        # the owner never sees their own mirror in lists or search
        self.assertIsNone(self.em_vault(ADMIN))
        titles = [x["title"] for x in self.ok(self.get("/api/search?q=will"))["items"]]
        self.assertEqual(titles, ["Will and bank details"])
        # Neha asks
        r = self.ok(self.post(f"/api/emergency/{ADMIN['id']}/request", user=NEHA))
        t = r["trustedBy"][0]
        self.assertEqual((t["status"], t["vaultId"]), ("requested", None))
        self.assertTrue(t["releaseAt"])
        self.assertTrue(any("asked for emergency access" in m for m in self.msgs(ADMIN)))
        self.assertIsNone(self.em_vault(NEHA))
        # nothing released before the wait is over
        self.ok(self.get("/api/emergency", NEHA))
        self.assertIsNone(self.em_vault(NEHA))
        # …and after it, released
        sql("UPDATE emergency_contacts SET requested_at = '2020-01-01T00:00:00.000Z'")
        r = self.ok(self.get("/api/emergency", NEHA))
        self.assertEqual(r["trustedBy"][0]["status"], "granted")
        ev = self.em_vault(NEHA)
        self.assertEqual((ev["role"], ev["open"]), ("viewer", True))
        self.assertIn("emergency items", ev["name"])
        listed = self.ok(self.get(f"/api/vaults/{ev['id']}/items?recursive=true", NEHA))["items"]
        self.assertEqual([x["title"] for x in listed], ["Will and bank details"])       # not the diary
        secret = self.ok(self.get(f"/api/vaults/{ev['id']}/items/{listed[0]['id']}/secret?field=Password", NEHA))
        self.assertEqual(secret["value"], "bank-pw-123")
        self.assertEqual(self.post(f"/api/vaults/{ev['id']}/items", {"title": "x"}, NEHA).status_code, 403)
        self.assertTrue(any("now have" in m for m in self.msgs(NEHA)))
        self.assertTrue(any("now has your emergency items" in m for m in self.msgs(ADMIN)))
        # it opens again at her next unlock
        self.unlock(NEHA)
        self.assertTrue(self.em_vault(NEHA)["open"])

    def test_mirror_follows_the_personal_vault(self):
        r = self.grant_neha()
        ev = self.em_vault(NEHA)
        self.ok(self.patch(f"/api/vaults/{self.pv}/items/{self.will['id']}", {"title": "Will (updated)"}))
        titles = lambda: [x["title"] for x in self.ok(self.get(f"/api/vaults/{ev['id']}/items?recursive=true", NEHA))["items"]]
        self.assertEqual(titles(), ["Will (updated)"])
        self.mark(self.other)
        self.assertEqual(sorted(titles()), ["Private diary", "Will (updated)"])
        self.mark(self.other, False)
        self.assertEqual(titles(), ["Will (updated)"])
        # unchanged Personal saves don't make new Emergency versions
        v1 = sql("SELECT version FROM vaults WHERE id = ?", (ev["id"],))[0]["version"]
        self.add_item(self.pv, title="Another unmarked")
        self.assertEqual(sql("SELECT version FROM vaults WHERE id = ?", (ev["id"],))[0]["version"], v1)
        self.assertEqual(r["trustedBy"][0]["status"], "granted")

    def test_deny_and_ask_again_later(self):
        self.mark(self.will)
        self.ok(self.post("/api/emergency/contacts", {"userId": NEHA["id"], "waitDays": 1}))
        self.ok(self.post(f"/api/emergency/{ADMIN['id']}/request", user=NEHA))
        r = self.ok(self.post(f"/api/emergency/contacts/{NEHA['id']}/deny"))
        self.assertEqual(r["mine"]["contacts"][0]["status"], "denied")
        self.assertTrue(any("denied your request" in m for m in self.msgs(NEHA)))
        self.assertEqual(self.post(f"/api/emergency/{ADMIN['id']}/request", user=NEHA).status_code, 429)
        sql("UPDATE emergency_contacts SET requested_at = '2020-01-01T00:00:00.000Z', decided_at = '2020-01-02T00:00:00.000Z'")
        self.ok(self.get("/api/emergency", NEHA))                 # a denied request is never released
        self.assertIsNone(self.em_vault(NEHA))
        r = self.ok(self.post(f"/api/emergency/{ADMIN['id']}/request", user=NEHA))
        self.assertEqual(r["trustedBy"][0]["status"], "requested")

    def test_approve_now(self):
        self.mark(self.will)
        self.ok(self.post("/api/emergency/contacts", {"userId": NEHA["id"], "waitDays": 30}))
        self.assertEqual(self.post(f"/api/emergency/contacts/{NEHA['id']}/approve").status_code, 409)
        self.ok(self.post(f"/api/emergency/{ADMIN['id']}/request", user=NEHA))
        self.ok(self.post(f"/api/emergency/contacts/{NEHA['id']}/approve"))
        self.assertEqual(self.em_vault(NEHA)["role"], "viewer")

    def test_revoke_rekeys_and_others_still_work(self):
        self.set_up(VIKRAM)
        self.unlock(ADMIN)
        self.mark(self.will)
        self.ok(self.post("/api/emergency/contacts", {"userId": VIKRAM["id"], "waitDays": 2}))
        self.grant_neha()
        ev = self.em_vault(NEHA)
        epoch = sql("SELECT key_epoch FROM vaults WHERE id = ?", (ev["id"],))[0]["key_epoch"]
        self.ok(self.delete(f"/api/emergency/contacts/{NEHA['id']}"))
        self.assertIsNone(self.em_vault(NEHA))
        self.assertEqual(self.get(f"/api/vaults/{ev['id']}/items", NEHA).status_code, 404)
        self.assertGreater(sql("SELECT key_epoch FROM vaults WHERE id = ?", (ev["id"],))[0]["key_epoch"], epoch)
        # Vikram's sealed copy followed the new password: when he's released, it opens
        self.ok(self.post(f"/api/emergency/{ADMIN['id']}/request", user=VIKRAM))
        self.ok(self.post(f"/api/emergency/contacts/{VIKRAM['id']}/approve"))
        self.unlock(VIKRAM)
        ev2 = self.em_vault(VIKRAM)
        self.assertTrue(ev2["open"])
        self.assertEqual(len(self.ok(self.get(f"/api/vaults/{ev2['id']}/items?recursive=true", VIKRAM))["items"]), 1)

    def test_rules_personal_only_active_people_disable_and_reset(self):
        hh = self.vaults()["Household"]
        wifi = self.add_item(hh["id"], title="Wi-Fi")
        self.assertEqual(self.put(f"/api/vaults/{hh['id']}/items/{wifi['id']}/emergency", {"include": True}).status_code, 403)
        self.assertEqual(self.post("/api/emergency/contacts", {"userId": ADMIN["id"]}).status_code, 422)
        self.seen(VIKRAM)
        self.assertEqual(self.post("/api/emergency/contacts", {"userId": VIKRAM["id"]}).status_code, 409)
        self.assertEqual(self.post("/api/emergency/contacts", {"userId": NEHA["id"], "waitDays": 31}).status_code, 422)
        self.grant_neha()
        # disabling a contact revokes them
        self.ok(self.patch(f"/api/users/{NEHA['id']}", {"disabled": True}, token=False))
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM emergency_contacts")[0]["n"], 0)
        # resetting the owner removes their Emergency vault
        self.ok(self.post(f"/api/users/{ADMIN['id']}/reset", token=False))
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM vaults WHERE kind = 'emergency'")[0]["n"], 0)

    def test_owner_disabled_puts_requests_on_hold(self):
        self.mark(self.will)
        self.ok(self.post("/api/emergency/contacts", {"userId": NEHA["id"], "waitDays": 1}))
        self.ok(self.patch(f"/api/users/{ADMIN['id']}", {"disabled": True}, token=False))
        self.ok(self.post(f"/api/emergency/{ADMIN['id']}/request", user=NEHA))
        sql("UPDATE emergency_contacts SET requested_at = '2020-01-01T00:00:00.000Z'")
        r = self.ok(self.get("/api/emergency", NEHA))
        self.assertEqual((r["trustedBy"][0]["status"], r["trustedBy"][0]["onHold"]), ("requested", True))
        self.assertIsNone(self.em_vault(NEHA))                 # the owner couldn't deny it, so nothing is released
        self.ok(self.patch(f"/api/users/{ADMIN['id']}", {"disabled": False}, token=False))
        self.ok(self.get("/api/emergency", NEHA))
        self.unlock(NEHA)
        ev = self.em_vault(NEHA)
        self.assertTrue(ev and ev["open"])
        self.assertEqual([x["title"] for x in self.ok(self.get(f"/api/vaults/{ev['id']}/items?recursive=true", NEHA))["items"]],
                         ["Will and bank details"])

    def test_the_mirror_is_read_only_for_everyone(self):
        self.set_up(VIKRAM)
        self.unlock(ADMIN)
        self.mark(self.will)
        ev = self.ok(self.get("/api/emergency"))["mine"]["vaultId"]
        self.assertEqual(self.post(f"/api/vaults/{ev}/members", {"userId": VIKRAM["id"], "role": "manager"}).status_code, 403)
        self.assertEqual(self.post(f"/api/vaults/{self.pv}/items/move", {"items": [self.other["id"]], "toVaultId": ev,
                                                                         "duplicate": True}).status_code, 403)
        self.assertEqual(self.post(f"/api/vaults/{ev}/items", {"title": "sneaky"}).status_code, 403)
        # copies don't carry the mark into someone else's emergency access
        hh = self.vaults()["Household"]["id"]
        self.ok(self.post(f"/api/vaults/{self.pv}/items/move", {"items": [self.will["id"]], "toVaultId": hh, "duplicate": True}))
        copy = next(x for x in self.ok(self.get(f"/api/vaults/{hh}/items"))["items"] if x["title"] == "Will and bank details")
        self.assertFalse(self.ok(self.get(f"/api/vaults/{hh}/items/{copy['id']}"))["emergency"])

if __name__ == "__main__":
    import unittest
    unittest.main()
