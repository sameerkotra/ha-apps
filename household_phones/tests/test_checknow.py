"""Check now (spec/SPEC.md §4): the live result, joining, the rate limit, who may check, and the triggers from Home
Assistant."""
import json

from base import ADMIN, ASHA, BOB, PhonesCase

from app import checknow, ha_source, monitor


class CheckNow(PhonesCase):
    def setUp(self):
        super().setUp()
        self.pid = self.add()
        self.report(self.pid, key="battery_level", value="62")
        self.report(self.pid, key="is_charging", value="on")
        self.report(self.pid, key="wifi_connection", value="Home")
        self.advance(3)

    def start(self, who=ASHA):
        r = self.post(f"/api/phones/{self.pid}/check", None, who)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["check"]

    def view(self, cid, who=ASHA):
        return self.get(f"/api/checks/{cid}", who).json()

    def test_online_with_the_reply_time_and_details(self):
        cid = self.start()
        self.assertEqual(self.ha.commands, ["mobile_app_asha_pixel"])
        self.assertEqual(self.view(cid)["state"], "waiting")
        self.now += 4
        self.report(self.pid, key="battery_level", value="62")
        v = self.view(cid)
        self.assertEqual((v["state"], v["text"]), ("online", "replied in 4 s"))
        self.assertEqual((v["battery"], v["charging"], v["network"]), (62, True, "Wi-Fi Home"))
        self.assertIsNone(v["forward"])                                      # forwarding is off: nothing said
        self.assertEqual(self.row(self.pid)["answers_requests"], 1)

    def test_no_reply_then_watched_as_overdue(self):
        cid = self.start()
        self.now += 61
        v = self.view(cid)
        self.assertEqual(v["state"], "no_reply")
        self.assertIn("no reply; last seen", v["text"])
        self.assertIn("battery 62%", v["text"])
        self.assertIsNotNone(self.row(self.pid)["overdue_from"])
        self.tick()
        self.assertEqual(self.row(self.pid)["status"], "late")
        self.advance(5)
        self.tick()
        self.assertEqual(len(self.ha.commands), 2)                          # asked again
        self.advance(5.1)
        self.tick()
        self.assertEqual(self.row(self.pid)["status"], "offline")

    def test_cant_ask(self):
        self.ha.result = ha_source.CommandResult(False, "cant_check", "Home Assistant couldn't send the request.")
        v = self.view(self.start())
        self.assertEqual(v["state"], "cant_ask")
        self.assertIn("couldn't send", v["text"])

    def test_iphone(self):
        ip = self.add("Bob iPhone", user_id="u-bob", model="iPhone15,2", manufacturer="Apple")
        self.report(ip, name="Bob iPhone")
        r = self.post(f"/api/phones/{ip}/check", None, BOB).json()
        v = self.view(r["check"], BOB)
        self.assertEqual(v["state"], "cant_ask")
        self.assertTrue(v["text"].startswith("Can't ask an iPhone — last check-in"))
        self.assertNotIn("mobile_app_bob_iphone", self.ha.commands)

    def test_join_and_rate_limit(self):
        cid = self.start()
        self.now += 10
        self.assertEqual(self.start(ADMIN), cid)                            # within 30 s: joins
        self.assertEqual(len(self.ha.commands), 1)
        self.setting(check_per_hour=3)
        for _ in range(2):
            self.now += 31
            self.start()
        self.now += 31
        r = self.post(f"/api/phones/{self.pid}/check", None, ASHA)
        self.assertEqual(r.status_code, 429)
        self.now += 3600
        self.start()
        log = self.get(f"/api/phones/{self.pid}", ASHA).json()["log"]
        self.assertEqual(log[-1]["by"], "Asha")                             # the owner sees who checked

    def test_who_may_check(self):
        self.assertEqual(self.post(f"/api/phones/{self.pid}/check", None, BOB).status_code, 404)
        cid = self.start()
        self.assertEqual(self.get(f"/api/checks/{cid}", BOB).status_code, 404)
        # Bob is in the chat Asha's phone forwards into
        self.put(f"/api/phones/{self.pid}/forwarding", {"on": True, "target": "chat", "chatId": "c1", "chatName": "Phone codes",
                                                        "chatKind": "group"}, ASHA)
        self.set_row(self.pid, fwd_members=json.dumps(["u-bob"]))
        my = self.get("/api/my", BOB).json()
        self.assertEqual([o["id"] for o in my["others"]], [self.pid])
        self.assertEqual(self.get(f"/api/checks/{cid}", BOB).status_code, 200)
        self.assertEqual(self.get(f"/api/phones/{self.pid}", BOB).status_code, 404)      # not the details
        v = self.view(cid, BOB)
        self.assertEqual(v["forward"], "nothing forwarded in the last 24 h")
        self.setting(check_by_chat_members=False)
        self.assertEqual(self.get(f"/api/checks/{cid}", BOB).status_code, 404)

    def test_forward_noted_on_the_card(self):
        self.put(f"/api/phones/{self.pid}/forwarding", {"on": True}, ASHA)
        cid = self.start()
        self.now += 2
        self.report(self.pid, key="last_notification", value="x", attrs={
            "package": "com.google.android.apps.messaging", "android.title": "AX-BANK",
            "android.text": "Your one-time code is 482913. Do not share it."})
        v = self.view(cid)
        self.assertIn("into 📌 My room", v["forward"])
        self.assertEqual(v["forwardNow"], "a message was just forwarded into 📌 My room")

    def test_from_home_assistant_event(self):
        from app import app_messages
        self.setting(ha_sensors=True)
        import threading
        done = threading.Event()
        orig = checknow.start

        def wrapped(*a, **k):
            try:
                return orig(*a, **k)
            finally:
                done.set()
        from unittest import mock
        with mock.patch.object(checknow, "start", wrapped):
            app_messages.on_check_event({"data": {"phone": self.pid}})
            self.assertTrue(done.wait(5))
        last = self.get(f"/api/phones/{self.pid}", ASHA).json()["log"][0]
        self.assertEqual((last["by"], last["via"]), ("Home Assistant", "home_assistant"))

    def test_check_page_route(self):
        r = self.get(f"/api/check/{self.pid}/latest", ASHA).json()
        self.assertIsNone(r["check"])
        self.start()
        self.assertEqual(self.get(f"/api/check/{self.pid}/latest", ASHA).json()["check"]["state"], "waiting")
        _ = monitor
