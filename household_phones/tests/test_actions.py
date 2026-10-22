"""Phone actions (spec/SPEC.md §15.1, §15.3): the payloads sent to Home Assistant for each action (from the Companion
app's documentation), the owner's allow list and the child rules, do-not-monitor hours, iPhones and devices not
monitored, the rate limits, the log, Find my phone's "Found it" and time-out, the Household Assistant's phones.find and
the household_phones_find event. T0 is Friday 2027-01-15 08:00 (UTC)."""
from base import ADMIN, ASHA, BOB, KID, PhonesCase
from fakes import PIXEL_SENSORS

from app import actions, app_messages, db, tools
from app.common import app_bus as bus

SENSORS = PIXEL_SENSORS + ("device_locked", "dnd_sensor", "volume_alarm", "battery_health", "current_version")
EVERY = [0, 1, 2, 3, 4, 5, 6]


class ActionsCase(PhonesCase):
    def setUp(self):
        super().setUp()
        actions.SYNC["on"] = True
        app_messages.FIND_SYNC["on"] = True
        self.addCleanup(lambda: (actions.SYNC.update(on=False), app_messages.FIND_SYNC.update(on=False)))
        self.pid = self.add(sensors=SENSORS)
        self.report(self.pid, value="70")
        self.tick()

    def act(self, action, body=None, who=ASHA, pid=None):
        return self.post(f"/api/phones/{pid or self.pid}/actions/{action}", body or {}, who)

    def payloads(self):
        return [p for _, p in self.ha.notified]

    def consent(self, body, who=ASHA, pid=None):
        return self.put(f"/api/phones/{pid or self.pid}/consent", body, who)


class Payloads(ActionsCase):
    def test_each_action_sends_the_documented_command(self):
        cases = [
            ("screen_on", {}, {"message": "command_screen_on"}),
            ("flashlight", {"on": True}, {"message": "command_flashlight", "data": {"command": "turn_on"}}),
            ("flashlight", {"on": False}, {"message": "command_flashlight", "data": {"command": "turn_off"}}),
            ("ringer", {"mode": "vibrate"}, {"message": "command_ringer_mode", "data": {"command": "vibrate"}}),
            ("dnd", {"mode": "priority_only"}, {"message": "command_dnd", "data": {"command": "priority_only"}}),
            ("dnd", {"mode": "off"}, {"message": "command_dnd", "data": {"command": "off"}}),
            ("volume", {"stream": "music_stream", "level": 20},
             {"message": "command_volume_level", "data": {"media_stream": "music_stream", "command": 20}}),
            ("brightness", {"level": 200}, {"message": "command_screen_brightness_level", "data": {"command": 200}}),
            ("screen_timeout", {"seconds": 60}, {"message": "command_screen_off_timeout", "data": {"command": 60000}}),
            ("launch_app", {"package": "com.spotify.music"},
             {"message": "command_launch_app", "data": {"package_name": "com.spotify.music"}}),
            ("dashboard", {"path": "/lovelace/0"}, {"message": "command_webview", "data": {"command": "/lovelace/0"}}),
        ]
        for action, body, want in cases:
            self.now += 10                                                  # past each action's 5-second gap
            r = self.act(action, body)
            self.assertEqual(r.status_code, 200, (action, r.text))
            self.assertEqual(self.ha.notified[-1], ("mobile_app_asha_pixel", want), action)
            self.assertTrue(r.json()["ok"])
        self.assertIn("Do Not Disturb access", self.act("ringer", {"mode": "normal"}).json()["needs"] or "")

    def test_bad_parameters(self):
        for action, body in (("volume", {"stream": "music_stream", "level": 500}), ("volume", {"stream": "x", "level": 3}),
                             ("brightness", {"level": 999}), ("screen_timeout", {"seconds": 7}),
                             ("launch_app", {"package": "not a package"}), ("dashboard", {"path": "https://evil"}),
                             ("dashboard", {"path": "/a//b"}), ("ringer", {"mode": "loud"}), ("nope", {})):
            self.assertEqual(self.act(action, body).status_code, 422, action)
        self.assertEqual(self.ha.notified, [])

    def test_find_my_phone_rings_on_the_alarm_stream_and_puts_things_back(self):
        self.report(self.pid, key="volume_alarm", value="3", attrs={"min": 1, "max": 7})
        self.report(self.pid, key="dnd_sensor", value="priority_only")
        r = self.act("find").json()
        self.assertTrue(r["ok"])
        self.assertIn("Do Not Disturb was on", r["text"])
        sent = self.payloads()
        self.assertEqual(sent[0], {"message": "command_dnd", "data": {"command": "off"}})
        self.assertEqual(sent[1], {"message": "command_volume_level", "data": {"media_stream": "alarm_stream", "command": 7}})
        ring = sent[2]
        self.assertEqual({k: ring["data"][k] for k in ("ttl", "priority", "channel", "importance", "persistent", "sticky")},
                         {"ttl": 0, "priority": "high", "channel": "alarm_stream", "importance": "max", "persistent": True,
                          "sticky": "true"})
        self.assertEqual(ring["data"]["tag"], f"household_phones_find_{self.pid}")
        found = ring["data"]["actions"][0]
        self.assertEqual(found["title"], "Found it")
        self.assertEqual(sent[3]["message"], "TTS")
        self.assertEqual(sent[3]["data"]["media_stream"], "alarm_stream_max")
        # "Found it" on the phone: cleared, the volume and Do Not Disturb put back
        self.ha.notified.clear()
        actions.on_notification_action({"event_type": "mobile_app_notification_action", "data": {"action": found["action"]}})
        self.assertEqual(self.payloads(), [
            {"message": "clear_notification", "data": {"tag": f"household_phones_find_{self.pid}"}},
            {"message": "command_volume_level", "data": {"media_stream": "alarm_stream", "command": 3}},
            {"message": "command_dnd", "data": {"command": "priority_only"}}])
        self.assertIsNone(self.row(self.pid)["find_token"])
        # an unknown token does nothing
        self.ha.notified.clear()
        actions.on_notification_action({"data": {"action": found["action"]}})
        actions.on_notification_action({"data": {"action": "HP_FOUND_zzz"}})
        self.assertEqual(self.ha.notified, [])

    def test_find_without_volume_sensor_says_so_and_times_out(self):
        r = self.act("find").json()
        self.assertIn("stays at its maximum", r["text"])
        self.assertNotIn("command_dnd", self.ha.messages())                 # Do Not Disturb wasn't on: left alone
        self.ha.notified.clear()
        self.advance(4)
        actions.tick(self.now, self.ha)
        self.assertEqual(self.ha.notified, [])
        self.advance(2)
        actions.tick(self.now, self.ha)
        self.assertEqual(self.ha.messages(), ["clear_notification"])        # nothing to put back but the notification
        self.assertIn("Find my phone stopped (time's up)", self.get(f"/api/phones/{self.pid}", ASHA).text)

    def test_iphone_only_find_as_a_critical_alert(self):
        ip = self.add("Asha iPhone", model="iPhone15,2", manufacturer="Apple")
        r = self.act("find", pid=ip)
        self.assertEqual(r.status_code, 200, r.text)
        (svc, payload), = self.ha.notified
        self.assertEqual(svc, "mobile_app_asha_iphone")
        self.assertEqual(payload["data"]["push"], {"sound": {"name": "default", "critical": 1, "volume": 1.0}})
        r = self.act("flashlight", {"on": True}, pid=ip)
        self.assertEqual(r.status_code, 409)
        self.assertIn("iPhone", r.json()["detail"])
        view = self.get(f"/api/phones/{ip}", ASHA).json()["actions"]
        self.assertEqual([i["id"] for i in view["items"] if i["may"]], ["find"])


class Consent(ActionsCase):
    def test_defaults_admins_may_find_and_screen_on_only(self):
        self.assertEqual(self.act("find", who=ADMIN).status_code, 200)
        self.assertEqual(self.act("screen_on", who=ADMIN).status_code, 200)
        r = self.act("flashlight", {"on": True}, who=ADMIN)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["detail"], "Asha hasn't allowed others to use Flashlight on this phone.")
        self.assertEqual(self.act("find", who=BOB).status_code, 404)        # not an admin, not the owner
        self.now += 31
        self.assertEqual(self.act("flashlight", {"on": True}).status_code, 200)     # the owner may use everything

    def test_only_the_owner_chooses(self):
        r = self.consent({"allow": ["find", "flashlight"]}, ADMIN)
        self.assertEqual(r.status_code, 403)
        r = self.consent({"allow": ["flashlight"], "fromHa": True, "lostContact": "Ann 555", "lostMessage": "Reward"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.act("find", who=ADMIN).status_code, 403)
        self.assertEqual(self.act("flashlight", {"on": True}, who=ADMIN).status_code, 200)
        row = self.row(self.pid)
        self.assertEqual((row["act_from_ha"], row["lost_contact"], row["lost_message"]), (1, "Ann 555", "Reward"))
        self.assertEqual(self.consent({"allow": ["fly"]}).status_code, 422)
        page = r.json()
        self.assertTrue(any("Admins may use: Flashlight" in x for x in page["adminsSee"]))
        self.assertIn("Phone actions (Asha): Others may use: Flashlight", self.c.get(f"/api/phones/{self.pid}",
                                                                                   headers=ADMIN).text)

    def test_child_phone_admins_decide(self):
        self.put("/api/admin/people/u-kid", {"isChild": True})
        kid = self.add("Kid Phone", user_id="u-kid")
        self.assertEqual(self.consent({"allow": ["flashlight"]}, KID, pid=kid).status_code, 403)
        self.assertEqual(self.act("flashlight", {"on": True}, who=ADMIN, pid=kid).status_code, 403)
        self.assertEqual(self.consent({"allow": ["find", "flashlight"]}, ADMIN, pid=kid).status_code, 200)
        self.assertEqual(self.act("flashlight", {"on": True}, who=ADMIN, pid=kid).status_code, 200)
        page = self.get(f"/api/phones/{kid}", KID).json()
        self.assertFalse(page["actions"]["canEditConsent"])
        self.assertTrue(page["actions"]["child"])
        self.assertTrue(any("Admins may use: Find my phone, Flashlight" in x for x in page["adminsSee"]))

    def test_not_monitored_off_in_the_household_and_watching_paused(self):
        other = self.add("Spare", monitored=False)
        self.assertEqual(self.act("find", pid=other).status_code, 409)
        self.setting(actions_allowed=False)
        self.assertEqual(self.act("find").status_code, 403)
        self.setting(actions_allowed=True)
        self.post(f"/api/phones/{self.pid}/pause", {"kind": "watching", "hours": 2}, ASHA)
        r = self.act("find", who=ADMIN)
        self.assertEqual(r.status_code, 409)
        self.assertIn("Paused by Asha", r.json()["detail"])
        self.assertEqual(self.act("find").status_code, 200)                  # the owner still may

    def test_do_not_monitor_hours_only_the_owners_find(self):
        self.put(f"/api/phones/{self.pid}/dnm", {"windows": [{"days": EVERY, "start": "07:00", "end": "09:00",
                                                               "pauseForwarding": False}]}, ASHA)
        r = self.act("find", who=ADMIN)
        self.assertEqual(r.status_code, 409)
        self.assertIn("do-not-monitor hours until 09:00", r.json()["detail"])
        self.assertEqual(self.act("screen_on").status_code, 409)             # not even the owner's other actions
        self.assertEqual(self.act("find").status_code, 200)                  # the owner's own Find my phone runs
        self.assertEqual(len(self.ha.notified), 3)


class Limits(ActionsCase):
    def test_find_once_per_30_seconds_and_per_hour(self):
        self.assertEqual(self.act("find").status_code, 200)
        self.now += 10
        r = self.act("find", who=ADMIN)
        self.assertEqual(r.status_code, 429)
        self.assertIn("at most once every 30 seconds", r.json()["detail"])
        self.setting(find_per_hour=2)
        self.now += 31
        self.assertEqual(self.act("find").status_code, 200)
        self.now += 31
        self.assertEqual(self.act("find").status_code, 429)
        self.now += 3600
        self.assertEqual(self.act("find").status_code, 200)

    def test_other_actions_per_hour(self):
        self.setting(actions_per_hour=2)
        self.assertEqual(self.act("screen_on").status_code, 200)
        self.assertEqual(self.act("screen_on").status_code, 429)             # 5 seconds apart
        self.now += 6
        self.assertEqual(self.act("flashlight", {"on": True}).status_code, 200)
        self.now += 6
        self.assertEqual(self.act("screen_on").status_code, 429)
        self.assertEqual(self.act("find").status_code, 200)                  # Find my phone has its own limit


class Log(ActionsCase):
    def test_every_use_is_logged_for_the_owner(self):
        self.act("screen_on", who=ADMIN)
        self.ha.notify_result = __import__("app.ha_source", fromlist=["x"]).CommandResult(False, "cant_check", "Nope.")
        self.now += 6
        r = self.act("flashlight", {"on": True})
        self.assertFalse(r.json()["ok"])
        self.assertEqual(r.json()["text"], "Nope.")
        log = self.get(f"/api/phones/{self.pid}", ASHA).json()["actions"]["log"]
        self.assertEqual([(x["what"], x["by"], x["ok"], x["result"]) for x in log],
                         [("Flashlight on", "Asha", False, "Nope."), ("Screen on", "Ann Admin", True, "sent")])
        with db.get_conn() as conn:
            hist = [r["text"] for r in conn.execute("SELECT text FROM history WHERE kind = 'action' ORDER BY at")]
        self.assertEqual(hist, ["Screen on by Ann Admin — sent", "Flashlight on by Asha — Nope."])


class FromAssistantAndHomeAssistant(ActionsCase):
    def call(self, args, who="u-asha", confirm=True):
        msg = bus.Message({"id": "01", "from": "household_assistant", "to": "household_phones", "kind": "assist.tool.call",
                           "data": {"tool": "phones.find", "args": args, "requested_by": who, "confirm": confirm}})
        with db.get_conn() as conn:
            return tools.tools.on_call(msg, conn)

    def test_assistant_same_rights_as_the_button(self):
        with self.assertRaises(bus.Nack):
            self.call({"person": "me"}, confirm=False)                        # it acts: the assistant confirms first
        self.assertTrue(self.call({"person": "me"})["text"].startswith("Ringing Asha's Pixel 8 now"))
        self.assertEqual(self.call({"person": "asha"}, who="u-bob")["text"],
                         "No phone you can ring matches that in Household Phones.")
        self.consent({"allow": []})
        r = self.call({"person": "asha"}, who="u-ann")
        self.assertIn("hasn't allowed others to use Find my phone", r["text"])

    def event(self, phone, user=None):
        app_messages.on_find_event({"event_type": "household_phones_find", "data": {"phone": phone},
                                    "context": {"user_id": user} if user else {}})

    def test_event_with_and_without_a_person(self):
        self.event(self.pid, "u-asha")
        self.assertEqual(self.ha.notified, [])                                 # the App setting is off by default
        self.setting(ha_find_event=True)
        self.event(self.pid)                                                   # nobody: the owner hasn't allowed it
        self.assertEqual(self.ha.notified, [])
        self.assertEqual(self.ha.events[-1], ("household_phones_find_result", {"phone": self.pid, "result": "refused"}))
        self.event("asha_s_pixel_8", "u-asha")                                 # the owner, by the entity-id name
        self.assertTrue(self.ha.notified)
        self.assertEqual(self.ha.events[-1][1]["result"], "sent")
        log = self.get(f"/api/phones/{self.pid}", ASHA).json()["actions"]["log"]
        self.assertEqual((log[0]["by"], log[0]["via"]), ("Asha", "home_assistant"))
        self.consent({"fromHa": True})
        self.now += 31
        self.ha.notified.clear()
        self.event(self.pid)
        self.assertTrue(self.ha.notified)
        self.assertEqual(self.get(f"/api/phones/{self.pid}", ASHA).json()["actions"]["log"][0]["by"], "Home Assistant")
        self.now += 31
        self.ha.notified.clear()
        self.event(self.pid, "u-bob")                                          # Bob may not, even though HA may
        self.assertEqual(self.ha.notified, [])
