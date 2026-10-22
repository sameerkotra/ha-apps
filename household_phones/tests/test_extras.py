"""Children's bedtime, storage and battery-health alerts, the Home Assistant app's version, one-tap setup with Test
everything, and one view per person (spec/SPEC.md §15.4–§15.8). T0 is Friday 2027-01-15 08:00 (UTC)."""
from base import ADMIN, ASHA, BOB, KID, PhonesCase
from test_actions import SENSORS

from app import actions, bedtime, health, sidebar

EVERY = [0, 1, 2, 3, 4, 5, 6]
PEOPLE = [{"name": "Ann", "userId": "u-ann", "phones": [{"label": "Ann", "tracker": "t1", "service": "mobile_app_ann"}]},
          {"name": "Asha", "userId": "u-asha", "phones": [{"label": "Tab", "tracker": "t3", "service": "mobile_app_asha_tab"}]}]


class Bedtime(PhonesCase):
    def setUp(self):
        super().setUp()
        self.put("/api/admin/people/u-kid", {"isChild": True})
        self.kid = self.add("Kid Phone", user_id="u-kid", sensors=SENSORS)
        self.people(PEOPLE)
        self.report(self.kid, name="Kid Phone", value="60")
        self.tick()

    def set(self, body, who=ADMIN, pid=None):
        return self.put(f"/api/admin/phones/{pid or self.kid}/bedtime", body, who)

    def screen(self, value):
        self.report(self.kid, name="Kid Phone", key="interactive", value=value)

    def round(self):
        return bedtime.tick(self.now, self.ha)

    def test_admins_set_it_on_childrens_phones_only(self):
        r = self.set({"windows": [{"days": EVERY, "start": "08:30", "end": "07:00"}], "parents": ["u-ann"], "dnd": True})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.set({"windows": []}, who=KID).status_code, 403)
        asha = self.add()
        r = self.set({"windows": [{"days": EVERY, "start": "21:00", "end": "07:00"}]}, pid=asha)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.set({"windows": [{"days": [], "start": "21:00", "end": "07:00"}]}).status_code, 422)
        self.assertEqual(self.set({"parents": ["nobody"]}).status_code, 422)
        # the child sees it (transparency), with the checklist item
        page = self.get(f"/api/phones/{self.kid}", KID).json()
        self.assertEqual(page["bedtime"]["text"], "Bedtime every day 08:30–07:00")
        self.assertEqual(page["bedtime"]["parentNames"], ["Ann Admin"])
        self.assertTrue(any(c["id"] == "interactive" and c["ok"] for c in page["checklist"]))
        self.assertTrue(any(x.startswith("Bedtime every day") for x in page["adminsSee"]))

    def test_screen_in_use_at_bedtime_one_quiet_alert_a_night(self):
        self.set({"windows": [{"days": EVERY, "start": "08:30", "end": "07:00"}], "parents": ["u-ann", "u-asha"]})
        self.screen("on")
        self.round()
        self.assertEqual(self.sent, [])                                        # not bedtime yet
        self.advance(31)                                                       # 08:31: bedtime
        self.round()
        self.advance(4)
        self.round()
        self.assertEqual(self.sent, [])                                        # in use for 4 minutes: not yet
        self.advance(2)
        self.assertEqual(self.round()["alerts"], 1)
        self.assertEqual(self.sent[0][1], "📱 Phone in use at bedtime")
        self.assertIn("Kiran's Pixel 8 has been in use for 6 minutes at bedtime", self.sent[0][2])
        self.assertEqual(self.sent_data[0], {"importance": "low", "push": {"interruption-level": "passive"}})
        self.advance(40)
        self.round()
        self.screen("off")
        self.round()
        self.advance(1)
        self.screen("on")
        self.advance(10)
        self.round()
        self.assertEqual(self.round()["alerts"], 0)                            # once per night
        # the next night
        self.advance(24 * 60)
        self.screen("off")
        self.round()
        self.screen("on")
        self.round()
        self.advance(6)
        self.assertEqual(self.round()["alerts"], 1)

    def test_screen_off_and_on_restarts_the_count(self):
        self.set({"windows": [{"days": EVERY, "start": "07:00", "end": "09:00"}], "parents": ["u-ann"]})
        self.screen("on")
        self.round()
        self.advance(4)
        self.screen("off")
        self.round()
        self.screen("on")
        self.round()
        self.advance(4)
        self.assertEqual(self.round()["alerts"], 0)

    def test_do_not_disturb_at_bedtime(self):
        self.set({"windows": [{"days": EVERY, "start": "08:30", "end": "09:00"}], "parents": ["u-ann"], "dnd": True})
        self.round()
        self.assertEqual(self.ha.notified, [])
        self.advance(31)
        self.round()
        self.round()
        self.assertEqual(self.ha.notified, [("mobile_app_kid_phone", {"message": "command_dnd", "data": {"command": "priority_only"}})])
        self.advance(30)
        self.round()
        self.assertEqual(self.ha.notified[-1][1], {"message": "command_dnd", "data": {"command": "off"}})
        log = self.get(f"/api/phones/{self.kid}", KID).json()["actions"]["log"]
        self.assertEqual([x["what"] for x in log], ["Bedtime Do Not Disturb off", "Bedtime Do Not Disturb on"])

    def test_not_a_child_any_more(self):
        self.set({"windows": [{"days": EVERY, "start": "07:00", "end": "09:00"}], "parents": ["u-ann"], "dnd": True})
        self.round()
        self.put("/api/admin/people/u-kid", {"isChild": False})
        self.round()
        self.assertEqual(self.ha.notified[-1][1]["data"]["command"], "off")    # Do Not Disturb taken back off
        self.screen("on")
        self.advance(10)
        self.round()
        self.assertEqual(self.sent, [])


class StorageAndHealth(PhonesCase):
    def setUp(self):
        super().setUp()
        self.pid = self.add(sensors=SENSORS)
        self.people(PEOPLE)
        self.report(self.pid, value="60")
        self.tick()

    def test_storage_almost_full_once_until_it_recovers(self):
        self.report(self.pid, key="internal_storage", value="12.5", attrs={"unit_of_measurement": "%"})
        health.tick(self.now)
        self.assertEqual(self.sent, [])
        self.report(self.pid, key="internal_storage", value="8.2")
        health.tick(self.now)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0][2], "Asha's Pixel 8 is almost out of storage: 8.2% free.")
        self.assertEqual(self.sent[0][0], "mobile_app_asha_tab")                # the owner's other device, not this one
        self.advance(60)
        health.tick(self.now)
        self.assertEqual(len(self.sent), 1)
        self.report(self.pid, key="internal_storage", value="13")
        health.tick(self.now)
        self.report(self.pid, key="internal_storage", value="7")
        health.tick(self.now)
        self.assertEqual(len(self.sent), 2)
        self.setting(alert_storage=False)
        self.report(self.pid, key="internal_storage", value="20")
        health.tick(self.now)
        self.report(self.pid, key="internal_storage", value="5")
        health.tick(self.now)
        self.assertEqual(len(self.sent), 2)
        page = self.get(f"/api/phones/{self.pid}").json()["admin"]
        self.assertEqual(page["health"]["storageFree"], 5.0)
        self.assertTrue(page["health"]["storageLow"])

    def test_battery_health_once_per_change(self):
        self.report(self.pid, key="battery_health", value="good")
        health.tick(self.now)
        self.assertEqual(self.sent, [])
        self.report(self.pid, key="battery_health", value="overheat")
        health.tick(self.now)
        health.tick(self.now)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("battery health is overheated (it was Good)", self.sent[0][2])
        self.assertEqual(self.sent[0][1], "📱 Battery health")
        self.advance(60)
        self.report(self.pid, key="battery_health", value="dead")
        health.tick(self.now)
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(self.get(f"/api/phones/{self.pid}").json()["admin"]["health"]["batteryHealth"], "dead")

    def test_not_during_do_not_monitor_hours_nor_while_paused(self):
        self.put(f"/api/phones/{self.pid}/dnm", {"windows": [{"days": EVERY, "start": "07:00", "end": "09:00",
                                                               "pauseForwarding": False}]}, ASHA)
        self.report(self.pid, key="internal_storage", value="3")
        health.tick(self.now)
        self.assertEqual(self.sent, [])
        self.put(f"/api/phones/{self.pid}/dnm", {"windows": []}, ASHA)
        self.post(f"/api/phones/{self.pid}/pause", {"kind": "alerts", "hours": 4}, ASHA)
        health.tick(self.now)
        self.assertEqual(self.sent, [])


class Versions(PhonesCase):
    def test_compare_within_the_same_system(self):
        self.assertTrue(health.older(health.parse_version("2026.9.2-full"), health.parse_version("2026.10.1-minimal")))
        self.assertFalse(health.older(health.parse_version("2026.10"), health.parse_version("2026.10.0")))
        self.assertTrue(health.older(health.parse_version("2025.12.4"), health.parse_version("2026.1")))
        self.assertIsNone(health.parse_version("beta"))
        a = self.add(sensors=SENSORS)
        b = self.add("Bob Phone", user_id="u-bob", sensors=SENSORS)
        ios = self.add("Ann iPhone", user_id="u-ann", model="iPhone15,2", manufacturer="Apple")
        self.report(a, key="current_version", value="2026.9.2-full")
        self.report(b, name="Bob Phone", key="current_version", value="2026.10.1-full")
        r = self.get("/api/admin/phones").json()
        rows = {s["id"]: s for s in r["phones"]}
        self.assertTrue(rows[a]["appOutdated"])
        self.assertEqual(rows[a]["appOutdatedWhy"], "another phone has 2026.10.1-full")
        self.assertFalse(rows[b]["appOutdated"])
        self.assertFalse(rows[ios]["appOutdated"])                           # iOS numbers differ: not compared
        mine = self.get(f"/api/phones/{a}", ASHA).json()
        item = next(c for c in mine["checklist"] if c["id"] == "app_version")
        self.assertFalse(item["ok"])
        self.assertTrue(mine["health"]["app"]["outdated"])
        self.assertTrue(self.get("/api/my", ASHA).json()["phones"][0]["appOutdated"])
        # the admin's minimum version
        self.setting(min_app_version="2026.11")
        rows = {s["id"]: s for s in self.get("/api/admin/phones").json()["phones"]}
        self.assertTrue(rows[b]["appOutdated"])
        self.assertIn("minimum 2026.11", rows[b]["appOutdatedWhy"])
        with self.assertRaises(Exception):
            self.setting(min_app_version="new")


class SetupAndTestEverything(PhonesCase):
    def setUp(self):
        super().setUp()
        self.pid = self.add(sensors=SENSORS)
        self.report(self.pid, value="60")
        self.tick()

    def test_setup_links(self):
        old = sidebar.PAGE
        self.addCleanup(lambda: setattr(sidebar, "PAGE", old))
        sidebar.PAGE = "/a1b2c3d4_household_phones"
        s = self.get(f"/api/phones/{self.pid}", ASHA).json()["setup"]
        self.assertEqual(s["appLink"], "homeassistant://navigate/a1b2c3d4_household_phones")
        self.assertEqual(s["myLink"], "https://my.home-assistant.io/redirect/supervisor_ingress/?addon=a1b2c3d4_household_phones")
        self.assertTrue(any("Manage sensors" in x for x in s["steps"]))
        sidebar.PAGE = None
        s = self.get(f"/api/phones/{self.pid}", ASHA).json()["setup"]
        self.assertIsNone(s["appLink"])
        self.assertTrue(s["myLink"].endswith("addon=household_phones"))

    def test_test_everything(self):
        r = self.post(f"/api/phones/{self.pid}/test-everything", {}, ADMIN)
        self.assertEqual(r.status_code, 403)                                  # the owner's own
        r = self.post(f"/api/phones/{self.pid}/test-everything", {}, ASHA)
        self.assertEqual(r.status_code, 200, r.text)
        out = r.json()
        ids = [i["id"] for i in out["items"]]
        self.assertIn("checklist:app", ids)
        self.assertEqual(ids[-3:], ["check", "forward", "notification"])
        self.assertIsNotNone(out["check"])
        self.assertEqual(self.ha.commands, ["mobile_app_asha_pixel"])         # the Check now
        self.assertIsNone(next(i for i in out["items"] if i["id"] == "forward")["ok"])   # forwarding is off
        self.assertTrue(next(i for i in out["items"] if i["id"] == "notification")["ok"])
        self.assertEqual(self.ha.notified[-1][1]["message"], "Test everything: this phone gets notifications from "
                                                             "Household Phones.")
        self.assertEqual(self.post(f"/api/phones/{self.pid}/test-everything", {}, ASHA).status_code, 429)
        # with forwarding on: a test forward into the owner's My room
        self.set_row(self.pid, fwd_on=1)
        self.now += 31
        out = self.post(f"/api/phones/{self.pid}/test-everything", {}, ASHA).json()
        fwd = next(i for i in out["items"] if i["id"] == "forward")
        self.assertTrue(fwd["ok"])
        self.assertEqual((self.posted[-1]["requested_by"], self.posted[-1]["my_room"]), ("u-asha", True))
        log = self.get(f"/api/phones/{self.pid}", ASHA).json()["actions"]["log"]
        self.assertEqual(log[0]["what"], "Test everything")


class OnePerPerson(PhonesCase):
    def test_my_phone_lists_every_device_and_all_phones_groups_by_person(self):
        a1 = self.add()
        a2 = self.add("Asha Tab", model="SM-X710", manufacturer="Samsung")
        self.set_row(a2, kind="tablet")
        b = self.add("Bob Phone", user_id="u-bob")
        n = self.add("Hall Tablet", user_id=None)
        for pid, name in ((a1, "Asha Pixel"), (a2, "Asha Tab"), (b, "Bob Phone"), (n, "Hall Tablet")):
            self.report(pid, name=name)
        self.tick()
        mine = self.get("/api/my", ASHA).json()["phones"]
        self.assertEqual([(s["id"], s["kind"]) for s in mine], [(a1, "phone"), (a2, "tablet")])
        self.assertTrue(all("battery" in s and s["actionsOn"] for s in mine))
        people = self.get("/api/admin/phones").json()["people"]
        self.assertEqual([(g["owner"], sorted(g["phones"])) for g in people],
                         [("Asha", sorted([a1, a2])), ("Bob", [b]), ("No owner", [n])])
