"""Discovery from Home Assistant's registries, owners, device kinds, the phone page's sensors, replaced and unused
devices (spec/SPEC.md §2, §5)."""
from base import ADMIN, ASHA, BOB, PhonesCase

from app import phones


class Discovery(PhonesCase):
    def test_only_companion_app_devices_are_found(self):
        self.ha.devices.append({"id": "hue1", "config_entries": ["other"], "identifiers": [["hue", "x"]],
                                "manufacturer": "Signify", "model": "Bridge", "name": "Hue"})
        pid = self.add(monitored=False)
        r = self.get("/api/admin/phones").json()
        self.assertEqual(r["phones"], [])                        # not monitored until an admin adds it
        self.assertEqual([(a["id"], a["kindGuess"]) for a in r["available"]], [(pid, "Phone")])
        p = self.row(pid)
        self.assertEqual(p["monitored"], 0)
        self.assertEqual((p["manufacturer"], p["model"], p["os_name"], p["os_version"], p["app_version"]),
                         ("Google", "Pixel 8", "Android", "15", "2026.10.1-full"))
        self.assertEqual(phones.LIVE.default_service[pid], "mobile_app_asha_pixel")

    def test_device_without_entry_but_mobile_app_identifier(self):
        self.ha.devices.append({"id": "d9", "config_entries": [], "identifiers": [["mobile_app", "d9"]],
                                "manufacturer": "Samsung", "model": "SM-S918B", "name": "Galaxy"})
        phones.refresh(self.ha)
        r = self.get("/api/admin/phones").json()
        self.assertEqual(len(r["available"]), 1)

    def test_owner_from_registration_then_from_tracked_device(self):
        pid = self.add("Asha Pixel", user_id="u-asha")
        self.assertEqual(self.row(pid)["owner_id"], "u-asha")
        # no user id in the registration: the person who tracks the device's device_tracker
        self.people([{"name": "Bob", "userId": "u-bob", "phones": [{"label": "Bob phone", "tracker": "device_tracker.bob_phone",
                                                                    "service": "mobile_app_bob_phone"}]}])
        pid2 = self.add("Bob Phone", user_id=None)
        self.assertEqual(self.row(pid2)["owner_id"], "u-bob")
        r = self.get(f"/api/phones/{pid2}", BOB).json()
        self.assertTrue(r["mine"])
        self.assertEqual(r["name"], "Bob's Pixel 8")
        # an admin corrects it; discovery then leaves it alone
        self.assertEqual(self.put(f"/api/admin/phones/{pid2}", {"ownerId": "u-asha"}).status_code, 200)
        phones.refresh(self.ha)
        self.assertEqual(self.row(pid2)["owner_id"], "u-asha")
        self.assertEqual(self.get(f"/api/phones/{pid2}", BOB).status_code, 404)

    def test_kinds_guessed_none_monitored(self):
        tab = self.add("Family Tab", model="SM-X710", manufacturer="Samsung", user_id="u-ann", monitored=False)
        watch = self.add("Asha Watch", model="Pixel Watch 3", user_id="u-asha", monitored=False)
        ipad = self.add("Kitchen iPad", model="iPad13,1", manufacturer="Apple", user_id="u-ann", monitored=False)
        self.assertEqual([self.row(x)["kind"] for x in (tab, watch, ipad)], ["tablet", "ignored", "tablet"])
        self.assertEqual([self.row(x)["monitored"] for x in (tab, watch, ipad)], [0, 0, 0])
        self.assertEqual(self.row(ipad)["os_name"], "iOS")
        r = self.get("/api/admin/phones").json()
        self.assertEqual((r["phones"], r["tablets"]), ([], []))
        self.assertEqual([a["id"] for a in r["available"]][-1], watch)          # watches and the like last
        self.assertEqual({a["kindGuess"] for a in r["available"]}, {"Tablet", "Watch or other device"})
        # the kind is chosen when adding it to monitoring: phone or tablet only
        self.assertEqual(self.post(f"/api/admin/phones/{watch}/monitor", {"kind": "ignored"}).status_code, 422)
        self.assertEqual(self.post(f"/api/admin/phones/{tab}/monitor", {"kind": "phone"}).status_code, 200)
        self.assertEqual((self.row(tab)["kind"], self.row(tab)["monitored"], self.row(tab)["kind_confirmed"]), ("phone", 1, 1))
        # guessing off: everything is shown as a phone
        self.setting(guess_kind=False)
        w2 = self.add("Bob Watch", model="Galaxy Watch6", manufacturer="Samsung", user_id="u-bob", monitored=False)
        self.assertEqual(self.row(w2)["kind"], "phone")
        self.assertEqual(phones.guess_kind("Pixel Tablet", "Pixel Tablet", "Google"), "tablet")
        self.assertEqual(phones.guess_kind("Pixel", "Pixel 8", "Google"), "phone")

    def test_sensors_grouped_disabled_listed_and_private_parts_hidden(self):
        pid = self.add(disabled=("internal_storage",))
        self.ha.state[self.ha.eid("Asha Pixel", "battery_level")] = {"state": "62", "attributes": {"unit_of_measurement": "%"}}
        self.ha.state[self.ha.eid("Asha Pixel", "last_notification")] = {"state": "Your code is 123456",
                                                                       "attributes": {"android.text": "Your code is 123456"}}
        phones.refresh(self.ha)
        page = self.get(f"/api/phones/{pid}").json()["admin"]
        groups = {g["id"]: g for g in page["sensors"]["groups"]}
        self.assertIn("battery", groups)
        bl = [s for s in groups["battery"]["sensors"] if s["name"] == "Battery Level"][0]
        self.assertEqual((bl["value"], bl["unit"]), ("62", "%"))
        self.assertIn("Internal Storage", page["sensors"]["off"])
        text = str(page)
        self.assertNotIn("123456", text)                        # a notification's text is never shown
        self.assertIn("not shown", text)
        self.assertIn("not shared by the owner", text)          # the place, until the owner shares it
        self.assertNotIn("1.5", str(phones.LIVE.states))        # no coordinates kept
        self.put(f"/api/phones/{pid}/mine", {"sharePlace": True}, ASHA)
        page = self.get(f"/api/phones/{pid}").json()["admin"]
        self.assertEqual(page["zone"], "Home")

    def test_registry_failure_keeps_what_is_known(self):
        pid = self.add()
        self.ha.fail_registries = True
        self.assertFalse(phones.refresh(self.ha)["ok"])
        self.assertEqual(self.row(pid)["in_registry"], 1)

    def test_removed_from_home_assistant(self):
        pid = self.add()
        self.ha.devices.clear()
        phones.refresh(self.ha)
        self.assertEqual(self.row(pid)["in_registry"], 0)


class ReplacedAndUnused(PhonesCase):
    def test_replaced_device_takes_everything_over(self):
        old = self.add("Asha Old", model="Pixel 6")
        self.put(f"/api/admin/phones/{old}", {"name": "Asha's phone", "intervalMin": 10})
        self.put(f"/api/phones/{old}/forwarding", {"on": True}, ASHA)
        self.post(f"/api/phones/{old}/pause", {"kind": "alerts", "hours": 1}, ASHA)
        self.advance(5)
        new = self.add("Asha New", model="Pixel 9")
        page = self.get(f"/api/phones/{new}", ASHA).json()
        self.assertEqual([r["id"] for r in page["replaceable"]], [old])
        self.assertEqual(self.post(f"/api/phones/{new}/replaces", {"old": old}, BOB).status_code, 404)
        r = self.post(f"/api/phones/{new}/replaces", {"old": old}, ASHA)
        self.assertEqual(r.status_code, 200, r.text)
        n, o = self.row(new), self.row(old)
        self.assertEqual((n["name"], n["interval_min"], n["fwd_on"], n["owner_id"]), ("Asha's phone", 10, 1, "u-asha"))
        self.assertEqual((o["retired"], o["replaced_by"], o["fwd_on"]), (1, new, 0))
        texts = [h["text"] for h in self.get(f"/api/phones/{new}").json()["admin"]["history"]]
        self.assertTrue(any("Alerts paused" in t for t in texts))          # the old phone's history moved over
        self.assertTrue(any(t.startswith("Replaces") for t in texts))
        ids = [p["id"] for p in self.get("/api/admin/phones").json()["phones"]]
        self.assertNotIn(old, ids)

    def test_not_used_lately_keep_and_remove(self):
        pid = self.add()
        self.report(pid)
        self.advance(31 * 24 * 60)
        r = self.get("/api/admin/phones").json()
        self.assertEqual([u["id"] for u in r["unused"]], [pid])
        self.post(f"/api/admin/phones/{pid}/keep")
        self.assertEqual(self.get("/api/admin/phones").json()["unused"], [])
        self.assertEqual(self.c.delete(f"/api/admin/phones/{pid}", headers=ADMIN).status_code, 200)
        self.assertIsNone(self.row(pid))
        self.assertEqual(self.c.delete(f"/api/admin/phones/{pid}", headers=ASHA).status_code, 403)


if __name__ == "__main__":
    import unittest
    unittest.main()
