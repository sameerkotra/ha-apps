"""What counts as a check-in (spec/SPEC.md §3): only genuine reports from the phone — not Home Assistant's restores,
restarts, unavailable / unknown states, events without a timestamp, tracker rewrites, or noise after the phone said it
was shutting down — and Check now answered only by a report made after it was sent. Plus the Check-in details."""
import json
import threading

from base import ADMIN, ASHA, PhonesCase
from fakes import iso, state_event

from app import app_messages, monitor

PHONE = "Asha Pixel"


class Genuine(PhonesCase):
    def setUp(self):
        super().setUp()
        self.pid = self.add()
        self.report(self.pid, value="80")                     # 08:00: its last genuine report
        self.first = self.now

    def event(self, key="battery_level", value="80", **kw):
        eid = kw.pop("eid", None) or self.ha.eid(PHONE, key)
        monitor.on_state_event(state_event(eid, value, at=kw.pop("at", self.now), **kw))

    def last(self):
        return self.row(self.pid)["last_checkin"]

    def ignored(self):
        return [x["reason"] for x in monitor.diagnostics(self.row(self.pid), self.now)["ignored"]]

    def test_a_genuine_report_counts(self):
        self.advance(1)
        self.event(value="79")
        self.assertEqual(self.last(), self.now)
        self.advance(1)
        self.event(value="79", etype="state_reported")
        self.assertEqual(self.last(), self.now)

    def test_restored_or_added_entities_dont_count(self):
        self.advance(5)
        self.event(old=None)
        self.assertEqual(self.last(), self.first)
        self.assertIn("added or restored by Home Assistant", self.ignored())

    def test_unavailable_and_unknown_dont_count(self):
        for v in ("unavailable", "unknown"):
            self.advance(5)
            self.event(value=v)
            self.event(value=v, etype="state_reported")
        self.assertEqual(self.last(), self.first)
        self.assertEqual(self.ignored()[:2], ["unknown", "unavailable"])
        # nor the way back from unavailable (Home Assistant restoring the value)
        self.advance(1)
        self.event(value="80", old={"state": "unavailable", "attributes": {}})
        self.assertEqual(self.last(), self.first)

    def test_no_timestamp_no_checkin(self):
        self.advance(5)
        self.event(stamps=False)
        self.event(stamps=False, etype="state_reported")
        self.assertEqual(self.last(), self.first)
        self.assertIn("no timestamp", self.ignored())
        self.assertIsNone(monitor.event_time({"state": "1"}, self.now))      # never "now"
        self.assertFalse(monitor.checkin(self.pid, None, self.now))

    def test_tracker_rewrites_dont_count(self):
        tracker = "device_tracker.asha_pixel"
        self.advance(5)
        self.event(eid=tracker, value="home", old={"state": "home", "attributes": {}})        # attributes only
        self.event(eid=tracker, value="home", etype="state_reported")
        self.assertEqual(self.last(), self.first)
        self.event(eid=tracker, value="not_home", old={"state": "home", "attributes": {}})    # it moved: the phone
        self.assertEqual(self.last(), self.now)

    def test_ha_restart_restores_dont_count(self):
        self.advance(5)
        monitor.on_ha_event({"event_type": "homeassistant_stop"})
        self.event(value="80")
        monitor.on_ha_event({"event_type": "homeassistant_start"})
        self.event(value="80", etype="state_reported")
        monitor.on_ha_event({"event_type": "homeassistant_started"})
        self.event(value="81")
        self.advance(1)
        self.event(value="82", etype="state_reported")
        self.assertEqual(self.last(), self.first)
        self.assertEqual(self.ignored(), ["HA restart"])                       # folded into one entry …
        d = monitor.diagnostics(self.row(self.pid), self.now)
        self.assertEqual(d["ignored"][0]["n"], 4)                             # … with the count
        self.assertTrue(d["haRestart"])
        self.advance(2)                                                       # past the grace: reports count again
        self.event(value="83")
        self.assertEqual(self.last(), self.now)

    def test_a_reconnect_means_a_restart(self):
        self.advance(5)
        monitor.on_ws_connect(False)
        self.event(value="70")
        self.assertEqual(self.last(), self.first)
        self.advance(monitor.HA_GRACE_S / 60 + 0.1)
        self.event(value="71")
        self.assertEqual(self.last(), self.now)

    def test_a_powered_off_phone_goes_late_then_offline_despite_noise(self):
        self.tick()
        self.assertEqual(self.row(self.pid)["status"], "online")
        tracker = "device_tracker.asha_pixel"
        noise = [lambda: self.event(eid=tracker, value="home", etype="state_reported"),
                 lambda: self.event(eid=tracker, value="home", old={"state": "home", "attributes": {"x": 1}}),
                 lambda: self.event(value="unavailable"),
                 lambda: self.event(value="80", old=None),
                 lambda: self.event(value="80", stamps=False, etype="state_reported"),
                 lambda: (monitor.on_ws_connect(False), self.event(value="80", etype="state_reported"))]
        seen = {}
        for minute in range(1, 28):
            self.now = self.first + minute * 60
            noise[minute % len(noise)]()
            self.tick()
            seen.setdefault(self.row(self.pid)["status"], minute)
        self.assertEqual(self.last(), self.first)
        self.assertEqual(seen, {"online": 1, "late": 18, "offline": 27})       # Late after I+2, Offline at I+12
        self.assertEqual(len(self.ha.commands), 2)                             # both requests went out, unanswered


class CheckNow(PhonesCase):
    def setUp(self):
        super().setUp()
        self.pid = self.add()
        self.report(self.pid, value="62")
        self.advance(3)

    def start(self):
        r = self.post(f"/api/phones/{self.pid}/check", None, ASHA)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["check"], self.now

    def state(self, cid):
        return self.get(f"/api/checks/{cid}", ASHA).json()["state"]

    def test_a_report_from_before_the_request_doesnt_answer_it(self):
        cid, started = self.start()
        self.now += 2
        # made 5 s before the request, arriving now: a check-in, but not the answer
        monitor.on_state_event(state_event(self.ha.eid(PHONE, "battery_level"), "61", at=started - 5))
        self.assertEqual(self.row(self.pid)["last_checkin"], started - 5)
        self.assertEqual(self.state(cid), "waiting")
        # one made the same second the request went out: not an answer either
        monitor.on_state_event(state_event(self.ha.eid(PHONE, "battery_level"), "60", at=started))
        self.assertEqual(self.state(cid), "waiting")
        # a stale one (older than the last check-in): nothing
        monitor.on_state_event(state_event(self.ha.eid(PHONE, "battery_level"), "59", at=started - 120,
                                           etype="state_reported"))
        self.assertEqual(self.state(cid), "waiting")
        reasons = [x["reason"] for x in monitor.diagnostics(self.row(self.pid), self.now)["ignored"]]
        self.assertIn("before the request (not an answer to Check now)", reasons)
        self.assertIn("older than the last check-in", reasons)
        # Home Assistant's own noise: nothing
        monitor.on_state_event(state_event(self.ha.eid(PHONE, "battery_level"), "unavailable", at=self.now))
        monitor.on_state_event(state_event(self.ha.eid(PHONE, "battery_level"), "60", at=self.now, old=None))
        self.assertEqual(self.state(cid), "waiting")
        # the phone's answer
        self.now += 2
        self.report(self.pid, value="60", etype="state_reported")
        self.assertEqual(self.state(cid), "online")

    def test_a_powered_off_phone_gets_no_reply(self):
        cid, started = self.start()
        self.now += 10
        monitor.on_ws_connect(False)
        self.report(self.pid, value="62", etype="state_reported")
        self.now += 61
        self.assertEqual(self.state(cid), "no_reply")


class Fallback(PhonesCase):
    def setUp(self):
        super().setUp()
        self.pid = self.add()
        self.sensors = [e for e in self.ha.state if e.startswith("sensor.")]

    def stamp(self, ts, eids=None):
        for e in eids or self.sensors:
            self.ha.state[e] = {"state": "5", "attributes": {}, "last_reported": iso(ts), "last_updated": iso(ts)}

    def test_a_batch_report_counts(self):
        """The Companion app sends every sensor in one batch: all of them moving to the same instant is a normal
        report (a phone left locked re-sends unchanged values). Only Home Assistant's restart grace is skipped."""
        self.stamp(self.now - 600)
        self.assertEqual(monitor.poll_states(self.ha, self.now), 1)            # the first look: its newest report
        self.advance(5)
        self.stamp(self.now - 10)                                              # every sensor at the same instant
        self.assertEqual(monitor.poll_states(self.ha, self.now), 1)
        self.assertEqual(self.row(self.pid)["last_checkin"], self.now - 10)

    def test_no_checkins_during_the_restart_grace(self):
        self.stamp(self.now - 600)
        monitor.poll_states(self.ha, self.now)
        before = self.row(self.pid)["last_checkin"]
        monitor.on_ha_event({"event_type": "homeassistant_started"})
        self.advance(1)
        self.stamp(self.now - 5, self.sensors[:1])
        self.assertEqual(monitor.poll_states(self.ha, self.now), 0)
        self.assertEqual(self.row(self.pid)["last_checkin"], before)

    def test_unavailable_doesnt_count(self):
        self.stamp(self.now - 600)
        monitor.poll_states(self.ha, self.now)
        self.advance(1)
        e = self.sensors[0]
        self.ha.state[e] = {"state": "unavailable", "attributes": {}, "last_reported": iso(self.now)}
        self.assertEqual(monitor.poll_states(self.ha, self.now), 0)

    def test_runs_only_while_state_reported_events_dont_come(self):
        self.stamp(self.now - 600)
        monitor.poll_states(self.ha, self.now, force=True)                     # the starting point
        self.stamp(self.now - 30, self.sensors[:1])
        monitor.REPORTED["last"] = self.now                                    # Home Assistant sends them
        self.assertEqual(monitor.poll_states(self.ha, self.now), 0)

        class WS:
            connected = threading.Event()                                      # … but the connection is down
        old = app_messages.WS["client"]
        app_messages.WS["client"] = WS()
        self.addCleanup(lambda: app_messages.WS.update(client=old))
        self.assertEqual(monitor.poll_states(self.ha, self.now), 1)
        WS.connected.set()
        self.advance(1)
        self.stamp(self.now - 1, self.sensors[:1])
        self.assertEqual(monitor.poll_states(self.ha, self.now), 0)


class Shutdown(PhonesCase):
    def test_after_shutting_down_the_next_report_means_it_is_back(self):
        pid = self.add()
        self.report(pid, value="80")
        self.tick()
        self.advance(1)
        off = self.now
        self.report(pid, key="last_update", value="android.intent.action.ACTION_SHUTDOWN")
        self.now += 30
        self.report(pid, key="battery_level", value="79")                     # its last words: still count
        self.assertEqual((self.row(pid)["shutdown_at"], self.row(pid)["last_checkin"]), (off, off + 30))
        start = self.now
        statuses = {}
        for minute in range(1, 29):                                            # off: nothing comes from it
            self.now = start + minute * 60
            self.tick()
            statuses.setdefault(self.row(pid)["status"], minute)
        self.assertEqual(statuses, {"online": 1, "late": 18, "offline": 27})
        d = monitor.diagnostics(self.row(pid), self.now)
        self.assertEqual(d["shutdown"]["at"], off)
        self.assertEqual(d["counted"][1]["kind"], "shutting down")
        # back on and left locked: an unchanged re-send is enough, and the shutdown is over
        self.report(pid, key="battery_level", value="79", etype="state_reported")
        self.assertEqual((self.row(pid)["last_checkin"], self.row(pid)["shutdown_at"]), (self.now, None))
        self.tick()
        self.assertEqual(self.row(pid)["status"], "online")


class Details(PhonesCase):
    def test_the_admin_sees_where_check_ins_came_from(self):
        pid = self.add()
        self.report(pid, key="battery_level", value="80")
        for i in range(25):
            self.advance(1)
            self.report(pid, key="battery_level", value=str(70 - i), etype="state_reported")
        self.advance(1)
        self.report(pid, key="last_notification", value="Your code is 424242",
                    attrs={"package": "com.google.android.apps.messaging", "android.text": "Your code is 424242"})
        self.advance(1)
        self.report(pid, key="last_notification", value="unavailable")
        self.report(pid, key="wifi_connection", value="unknown")
        d = self.get(f"/api/phones/{pid}").json()["admin"]["checkinDetails"]
        self.assertEqual(d["last"]["text"], "Last check-in 08:26 — from Last notification sensor (changed)")
        self.assertEqual(len(d["counted"]), monitor.RING_N)
        self.assertEqual(d["counted"][1]["name"], "Battery Level")
        self.assertEqual(d["counted"][1]["kind"], "reported, unchanged")
        self.assertEqual([(x["name"], x["reason"]) for x in d["ignored"]],
                         [("Wifi Connection", "unknown"), ("Last notification sensor", "unavailable")])
        whole = json.dumps(self.get(f"/api/phones/{pid}").json())
        self.assertNotIn("424242", whole)                                     # never a notification's text
        self.assertNotIn("com.google.android.apps.messaging", json.dumps(d))
        self.assertNotIn("checkinDetails", self.get(f"/api/phones/{pid}", ASHA).text)   # admins only
        self.assertIsNotNone(ADMIN)
