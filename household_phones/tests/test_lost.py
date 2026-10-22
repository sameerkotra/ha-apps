"""Lost mode (spec/SPEC.md §15.2): who may start it, what each round sends (the ring, the persistent notification
with the screen on, location requests), the place posted only with the owner's consent (zone; coordinates only with
"exact location"), and every way it ends (turned off, the end time, unlocked, the screen used, "I found it")."""
from base import ADMIN, ASHA, BOB, KID, PhonesCase
from test_actions import SENSORS

from app import actions, lost


class Lost(PhonesCase):
    def setUp(self):
        super().setUp()
        actions.SYNC["on"] = True
        self.addCleanup(lambda: actions.SYNC.update(on=False))
        self.pid = self.add(sensors=SENSORS + ("interactive",))
        self.report(self.pid, value="70")
        self.tick()

    def start(self, body=None, who=ASHA, pid=None):
        return self.post(f"/api/phones/{pid or self.pid}/lost", body or {}, who)

    def round(self):
        lost.tick(self.now, self.ha)

    def test_start_sends_notification_ring_screen_and_location(self):
        self.put(f"/api/phones/{self.pid}/consent", {"lostContact": "Ann, 555 0100", "lostMessage": "Reward"}, ASHA)
        r = self.start({"ringMin": 10, "locMin": 5})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["lost"]["on"])
        msgs = self.ha.messages()
        self.assertEqual(msgs[0], "If found, please call Ann, 555 0100 — Reward")
        first = self.ha.notified[0][1]["data"]
        self.assertEqual((first["persistent"], first["sticky"], first["tag"]), (True, "true", f"household_phones_lost_{self.pid}"))
        self.assertEqual(first["actions"][0]["title"], "I found it")
        self.assertTrue(first["actions"][0]["authenticationRequired"])
        self.assertIn("command_volume_level", msgs)
        self.assertIn("command_screen_on", msgs)
        self.assertEqual(msgs[-1], "request_location_update")
        self.assertNotIn("TTS", msgs)                                        # rounds don't speak (fewer notifications)
        # the next rounds: location every 5 minutes, ring every 10
        self.ha.notified.clear()
        self.advance(5)
        self.round()
        self.assertEqual(self.ha.messages(), ["request_location_update"])
        self.ha.notified.clear()
        self.advance(5)
        self.round()
        msgs = self.ha.messages()
        self.assertIn("command_screen_on", msgs)
        self.assertNotIn("If found, please call Ann, 555 0100 — Reward", msgs)     # the notification stays: sent once
        self.assertEqual(len([m for m in msgs if m and m.startswith("This phone is lost")]), 1)
        self.assertEqual(self.start().status_code, 409)                       # already on
        # All phones shows it prominently
        allp = self.get("/api/admin/phones").json()
        self.assertEqual(allp["lostMode"][0]["id"], self.pid)
        self.assertEqual(allp["phones"][0]["lost"]["byName"], "Asha")

    def test_who_may_start_it(self):
        r = self.start(who=ADMIN)
        self.assertEqual(r.status_code, 403)
        self.assertIn("hasn't allowed others to use Lost mode", r.json()["detail"])
        self.assertEqual(self.start(who=BOB).status_code, 404)
        self.put(f"/api/phones/{self.pid}/consent", {"allow": ["find", "lost"]}, ASHA)
        self.assertEqual(self.start(who=ADMIN).status_code, 200)
        self.assertEqual(self.c.delete(f"/api/phones/{self.pid}/lost", headers=BOB).status_code, 404)
        r = self.c.delete(f"/api/phones/{self.pid}/lost", headers=ASHA)
        self.assertFalse(r.json()["lost"]["on"])
        # a child's phone: admins, without the child's consent
        self.put("/api/admin/people/u-kid", {"isChild": True})
        kid = self.add("Kid Phone", user_id="u-kid")
        self.assertEqual(self.start(who=ADMIN, pid=kid).status_code, 200)

    def test_place_only_with_consent_and_coordinates_only_when_exact(self):
        self.start()
        self.assertEqual(len(self.posted), 1)                                  # only the start notice
        self.assertIn("doesn't share their place", self.posted[0]["text"])
        self.c.delete(f"/api/phones/{self.pid}/lost", headers=ASHA)
        self.posted.clear()
        self.put(f"/api/phones/{self.pid}/mine", {"sharePlace": True}, ASHA)
        self.now += 60
        self.start({"keep": True})
        texts = [p["text"] for p in self.posted]
        self.assertEqual(texts[1], "📍 Home")
        self.assertEqual(self.posted[1]["requested_by"], "u-asha")
        self.assertTrue(self.posted[1]["my_room"])
        self.assertIsNone(self.posted[1]["disappear_after"])                  # kept
        self.assertNotIn("1.5", "".join(texts))
        self.advance(5)
        self.round()
        self.assertEqual(len(self.posted), 2)                                  # the same place isn't posted again
        self.c.delete(f"/api/phones/{self.pid}/lost", headers=ASHA)
        self.put(f"/api/phones/{self.pid}/consent", {"lostExact": True}, ASHA)
        self.posted.clear()
        self.now += 60
        self.start({"chat": {"target": "chat", "chatId": "c1", "chatName": "Family"}})
        place = self.posted[1]
        self.assertEqual(place["chat_id"], "c1")
        self.assertEqual(place["disappear_after"], "1d")
        self.assertIn("1.50000, 2.50000", place["text"])
        with __import__("app.db", fromlist=["x"]).get_conn() as conn:          # coordinates are never stored
            dump = "\n".join(str(tuple(r)) for t in ("phones", "history", "actions") for r in conn.execute(f"SELECT * FROM {t}"))
        self.assertNotIn("1.5000", dump)

    def test_ends_at_its_end_time(self):
        self.start({"until": self.now + 1800})
        self.advance(31)
        self.round()
        self.assertIsNone(self.row(self.pid)["lost_since"])
        self.assertIn("clear_notification", self.ha.messages())
        self.assertIn("Lost mode ended", self.posted[-1]["text"])

    def test_ends_when_unlocked(self):
        self.report(self.pid, key="device_locked", value="on")
        self.advance(1)
        self.start()
        self.advance(1)
        self.report(self.pid, key="interactive", value="on")                   # a finder waking the screen: goes on
        self.round()
        self.assertIsNotNone(self.row(self.pid)["lost_since"])
        self.report(self.pid, key="device_locked", value="off")                # unlocked by its owner
        self.round()
        self.assertIsNone(self.row(self.pid)["lost_since"])
        log = self.get(f"/api/phones/{self.pid}", ASHA).json()["actions"]["log"]
        self.assertIn("the phone was unlocked", log[0]["what"])

    def test_without_the_lock_sensor_the_screen_coming_on_ends_it(self):
        pid = self.add("Bob Pixel", user_id="u-bob", sensors=SENSORS[:-5] + ("interactive",))
        self.report(pid, name="Bob Pixel", value="50")
        self.start(pid=pid, who=BOB)
        self.now += 10
        self.report(pid, name="Bob Pixel", key="interactive", value="on")      # the app woke it: not the owner
        lost.tick(self.now, self.ha)
        self.assertIsNotNone(self.row(pid)["lost_since"])
        self.report(pid, name="Bob Pixel", key="interactive", value="off")
        self.advance(3)
        self.report(pid, name="Bob Pixel", key="interactive", value="on")
        lost.tick(self.now, self.ha)
        self.assertIsNone(self.row(pid)["lost_since"])

    def test_i_found_it_on_the_phone(self):
        self.start()
        token = self.row(self.pid)["lost_token"]
        actions.on_notification_action({"data": {"action": f"HP_LOSTEND_{token}"}})
        self.assertIsNone(self.row(self.pid)["lost_since"])
        self.assertIsNone(self.row(self.pid)["find_token"])                     # the ring stopped too

    def test_do_not_monitor_hours_hold_an_admins_lost_mode(self):
        self.put(f"/api/phones/{self.pid}/consent", {"allow": ["lost"]}, ASHA)
        self.assertEqual(self.start(who=ADMIN).status_code, 200)
        self.put(f"/api/phones/{self.pid}/dnm", {"windows": [{"days": [0, 1, 2, 3, 4, 5, 6], "start": "08:00",
                                                               "end": "10:00", "pauseForwarding": False}]}, ASHA)
        self.ha.notified.clear()
        self.advance(15)
        self.round()
        self.assertEqual(self.ha.notified, [])
        self.assertTrue(self.get(f"/api/phones/{self.pid}", ASHA).json()["lost"]["paused"])
