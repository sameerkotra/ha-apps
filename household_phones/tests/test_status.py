"""Check-ins, the "please report" schedule and each phone's status (spec/SPEC.md §3, §3.1)."""
from base import ADMIN, ASHA, KID, PhonesCase

from app import ha_source, monitor


def statuses(case, pid):
    return case.row(pid)["status"]


class CheckIns(PhonesCase):
    def test_any_report_counts_unchanged_too(self):
        pid = self.add()
        self.report(pid, etype="state_reported")
        self.assertEqual(self.row(pid)["last_checkin"], self.now)
        self.assertTrue(monitor.reported_events_seen(self.now))
        # a burst of events writes once
        self.advance(0.1)
        self.assertFalse(monitor.checkin(pid, self.now, self.now))
        self.advance(1)
        self.assertTrue(monitor.checkin(pid, self.now, self.now))

    def test_events_of_other_entities_are_ignored(self):
        pid = self.add()
        from fakes import state_event
        monitor.on_state_event(state_event("light.kitchen", "on", at=self.now))
        self.assertIsNone(self.row(pid)["last_checkin"])

    def test_polling_fallback_when_state_reported_never_comes(self):
        pid = self.add()
        eid = self.ha.eid("Asha Pixel", "battery_level")
        from fakes import iso
        self.ha.state[eid] = {"state": "50", "attributes": {}, "last_reported": iso(self.now - 30),
                              "last_updated": iso(self.now - 3600)}
        self.assertEqual(monitor.poll_states(self.ha, self.now), 1)
        self.assertEqual(self.row(pid)["last_checkin"], self.now - 30)
        # once state_reported events arrive, the poll stands down
        self.report(pid, etype="state_reported")
        self.assertEqual(monitor.poll_states(self.ha, self.now), 0)
        self.assertEqual(self.get("/api/admin/phones").json()["checkins"], "events")
        self.advance(31)
        self.assertEqual(self.get("/api/admin/phones").json()["checkins"], "polling")


class Schedule(PhonesCase):
    def test_online_late_two_requests_then_offline(self):
        pid = self.add()
        self.people([{"name": "Ann", "userId": "u-ann", "phones": [{"label": "Ann", "tracker": "x", "service": "mobile_app_ann"}]}])
        self.report(pid)
        self.tick()
        self.assertEqual(statuses(self, pid), "online")
        self.advance(17)                          # interval + 2 minutes: still online
        self.tick()
        self.assertEqual((statuses(self, pid), self.ha.commands), ("online", []))
        self.advance(0.5)
        self.tick()
        self.assertEqual(statuses(self, pid), "late")
        self.assertEqual(self.ha.commands, ["mobile_app_asha_pixel"])
        self.advance(4)
        self.tick()
        self.assertEqual(len(self.ha.commands), 1)                # waits 5 minutes for a reply
        self.advance(1)
        self.tick()
        self.assertEqual(len(self.ha.commands), 2)                # asks once more
        self.advance(4.6)                                         # 27 minutes without a check-in
        self.tick()
        self.assertEqual(statuses(self, pid), "offline")
        self.assertEqual(len(self.ha.commands), 2)                # no third request
        offline = [s for s in self.sent if "Offline" in s[1]]
        self.assertEqual(offline[0][0], "mobile_app_ann")
        self.assertIn("is offline", offline[0][2])
        # it reports: online again, and the counter starts over
        self.report(pid)
        self.tick()
        self.assertEqual((statuses(self, pid), self.row(pid)["requests_sent"]), ("online", 0))

    def test_a_phone_that_reports_on_time_is_never_asked(self):
        pid = self.add()
        for _ in range(8):
            self.report(pid)
            self.tick()
            self.advance(15)
        self.assertEqual(self.ha.commands, [])

    def test_shorter_interval_and_own_interval(self):
        pid = self.add()
        self.report(pid)
        self.setting(interval_min=5)
        self.advance(7.5)
        self.tick()
        self.assertEqual(statuses(self, pid), "late")
        self.put(f"/api/admin/phones/{pid}", {"intervalMin": 15})
        self.tick()
        self.assertEqual(statuses(self, pid), "online")

    def test_answer_to_a_request_is_learnt(self):
        pid = self.add()
        self.report(pid)
        self.advance(18)
        self.tick()
        self.advance(0.2)
        self.report(pid)
        r = self.row(pid)
        self.assertEqual(r["answers_requests"], 1)
        self.assertEqual(r["last_spontaneous"], self.now - 18.2 * 60)

    def test_iphones_are_never_asked(self):
        pid = self.add("Bob iPhone", user_id="u-bob", model="iPhone15,2", manufacturer="Apple")
        self.report(pid, name="Bob iPhone")
        self.advance(40)
        self.tick()
        self.assertEqual((statuses(self, pid), self.ha.commands), ("offline", []))


class FalseAlarms(PhonesCase):
    def test_start_up_grace(self):
        pid = self.add()
        self.report(pid)
        monitor.STARTED["at"] = self.now + 5 * 60            # the app started 5 minutes after the last check-in
        self.advance(30)
        self.tick()
        self.assertEqual(statuses(self, pid), "late")          # never Offline during the grace
        self.assertTrue(self.get("/api/me").json()["starting"])
        self.advance(15 + 12 - 25 + 0.1)                       # one interval + 12 minutes after the start
        self.tick()
        self.assertEqual(statuses(self, pid), "offline")
        self.assertFalse(self.get("/api/me").json()["starting"])

    def test_cant_check_is_not_offline(self):
        pid = self.add()
        self.report(pid)
        self.ha.result = ha_source.CommandResult(False, "cant_check", "Home Assistant couldn't send the request — "
                                                                      "check remote access / Nabu Casa.")
        self.advance(18)
        self.tick()
        self.assertEqual(statuses(self, pid), "cant_check")
        self.advance(30)
        self.tick()
        self.assertEqual(statuses(self, pid), "cant_check")
        self.assertFalse([s for s in self.sent if "Offline" in s[1]])
        page = self.get(f"/api/phones/{pid}", ASHA).json()
        self.assertIn("Nabu Casa", page["reasonText"])
        self.assertFalse([c for c in page["checklist"] if c["id"] == "remote"][0]["ok"])

    def test_classify_notify_answers(self):
        c = ha_source.classify
        self.assertTrue(c(200, b"[]").ok)
        self.assertEqual(c(400, b'{"message": "Service notify.mobile_app_x not found."}').kind, "signed_out")
        self.assertEqual(c(500, b'{"message": "Unregistered device"}').kind, "signed_out")
        self.assertEqual(c(500, b'{"message": "Rate limit exceeded"}').kind, "cant_check")
        self.assertEqual(c(None, b"").kind, "cant_check")

    def test_app_not_reporting_signed_out_and_its_alert(self):
        pid = self.add()
        self.report(pid)
        self.ha.result = ha_source.CommandResult(False, "signed_out", "gone")
        self.advance(18)
        self.tick()
        self.assertEqual((statuses(self, pid), self.row(pid)["status_reason"]), ("app_not_reporting", "signed_out"))
        self.people([{"name": "Asha", "userId": "u-asha", "phones": [{"label": "Tab", "tracker": "t", "service": "mobile_app_asha_tab"}]}])
        self.tick()
        self.assertFalse([s for s in self.sent if "App not reporting" in s[1]])     # not before the Offline time
        self.advance(28)
        self.tick()
        sent = [s for s in self.sent if "App not reporting" in s[1]]
        self.assertEqual(len(sent), 1)
        self.assertIn("stopped reporting — signed out?", sent[0][2])
        self.advance(60)
        self.tick()
        self.assertEqual(len([s for s in self.sent if "App not reporting" in s[1]]), 1)   # once per episode

    def test_needed_sensor_off(self):
        pid = self.add(disabled=("battery_level",))
        self.report(pid, key="interactive", value="on")
        self.advance(40)
        self.tick()
        self.assertEqual((statuses(self, pid), self.row(pid)["status_reason"]), ("app_not_reporting", "sensor_off"))
        items = {c["id"]: c for c in self.get(f"/api/phones/{pid}", ASHA).json()["checklist"]}
        self.assertFalse(items["sensors"]["ok"])
        self.assertIn("Battery level", items["sensors"]["title"])

    def test_answers_requests_but_never_reports_by_itself(self):
        pid = self.add()
        self.set_row(pid, answers_requests=1, last_spontaneous=self.now - 5 * 3600, last_checkin=self.now - 60,
                     first_seen=self.now - 86400)
        self.tick()
        self.assertEqual((statuses(self, pid), self.row(pid)["status_reason"]), ("app_not_reporting", "answers_only"))
        items = {c["id"]: c for c in self.get(f"/api/phones/{pid}", ASHA).json()["checklist"]}
        self.assertFalse(items["battery"]["ok"])

    def test_lost_contact_one_alert_then_only_the_still_silent(self):
        self.people([{"name": "Ann", "userId": "u-ann", "phones": [{"label": "Ann", "tracker": "x", "service": "mobile_app_ann_spare"}]}])
        ids = [self.add(f"Phone {i}", user_id="u-asha") for i in range(4)]
        for i, pid in enumerate(ids):
            self.report(pid, name=f"Phone {i}")
        self.tick()
        self.advance(18)
        self.report(ids[3], name="Phone 3")                     # 3 of 4 go quiet together
        self.tick()
        lost = self.get("/api/admin/phones").json()["lostContact"]
        self.assertEqual((lost["silent"], lost["total"]), (3, 4))
        self.assertEqual(len(self.ha.notes), 1)                  # one alert, as a Home Assistant notification too
        self.assertIn("lost contact with the phones (3 of 4 silent since", self.ha.notes[0][1])
        self.advance(12)
        self.tick()
        self.assertEqual([statuses(self, p) for p in ids[:3]], ["offline"] * 3)
        self.assertFalse([s for s in self.sent if "Offline" in s[1]])     # held while contact is lost
        # contact returns for two of them: only the one still silent is reported
        self.report(ids[0], name="Phone 0")
        self.report(ids[1], name="Phone 1")
        self.tick()
        self.assertIsNone(self.get("/api/admin/phones").json()["lostContact"])
        offline = [s for s in self.sent if "Offline" in s[1]]
        self.assertEqual(len(offline), 1)
        self.assertIn("is offline", offline[0][2])
        self.assertEqual(len([s for s in self.sent if "Lost contact" in s[1]]), 1)

    def test_one_silent_phone_is_not_lost_contact(self):
        a, b = self.add("Phone A"), self.add("Phone B")
        self.report(a, name="Phone A")
        self.report(b, name="Phone B")
        self.advance(18)
        self.report(b, name="Phone B")
        self.tick()
        self.assertIsNone(self.get("/api/admin/phones").json()["lostContact"])


class Pauses(PhonesCase):
    def test_pause_alerts_keeps_status_and_check_now(self):
        pid = self.add()
        self.report(pid)
        r = self.post(f"/api/phones/{pid}/pause", {"kind": "alerts", "hours": 4, "reason": "on a flight"}, ASHA)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["pause"]["kind"], "alerts")
        self.advance(40)
        self.tick()
        self.assertEqual(statuses(self, pid), "offline")
        self.assertFalse([s for s in self.sent if "Offline" in s[1]])
        row = self.get("/api/admin/phones").json()["phones"][0]
        self.assertTrue(row["label"].startswith("Paused · Offline"))
        self.assertEqual(self.post(f"/api/phones/{pid}/check", None, ADMIN).status_code, 200)
        self.advance(4 * 60)
        self.tick()
        self.assertIsNone(self.row(pid)["pause_kind"])                  # ended by itself

    def test_pause_until_back_online(self):
        pid = self.add()
        self.post(f"/api/phones/{pid}/pause", {"kind": "alerts", "untilOnline": True}, ADMIN)
        self.advance(100)
        self.tick()
        self.assertEqual(self.row(pid)["pause_kind"], "alerts")
        self.report(pid)
        self.assertIsNone(self.row(pid)["pause_kind"])

    def test_pause_watching_hides_details_and_stops_requests(self):
        pid = self.add()
        self.report(pid, value="44")
        self.assertEqual(self.post(f"/api/phones/{pid}/pause", {"kind": "watching", "hours": 30}, ASHA).status_code, 422)
        self.assertEqual(self.post(f"/api/phones/{pid}/pause", {"kind": "watching", "hours": 2}, ADMIN).status_code, 403)
        self.assertEqual(self.post(f"/api/phones/{pid}/pause", {"kind": "watching", "hours": 2}, ASHA).status_code, 200)
        self.advance(60)
        self.tick()
        self.assertEqual((statuses(self, pid), self.ha.commands), ("paused", []))
        admin_row = self.get("/api/admin/phones").json()["phones"][0]
        self.assertTrue(admin_row["hidden"])
        self.assertTrue(admin_row["label"].startswith("Paused by Asha until"))
        for k in ("battery", "network", "lastCheckin"):
            self.assertNotIn(k, admin_row)
        page = self.get(f"/api/phones/{pid}").json()["admin"]
        self.assertIsNone(page["sensors"])
        self.assertEqual(self.post(f"/api/phones/{pid}/check", None, ADMIN).status_code, 409)
        mine = self.get(f"/api/phones/{pid}", ASHA).json()
        self.assertEqual(mine["battery"], 44)                            # the owner still sees everything
        self.assertEqual(self.c.delete(f"/api/phones/{pid}/pause", headers=ADMIN).status_code, 403)

    def test_children_cant_pause_being_watched(self):
        pid = self.add("Kid Phone", user_id="u-kid")
        self.assertEqual(self.put("/api/admin/people/u-kid", {"isChild": True}).status_code, 200)
        self.assertEqual(self.put("/api/admin/people/u-ann", {"isChild": True}).status_code, 409)
        self.assertEqual(self.post(f"/api/phones/{pid}/pause", {"kind": "watching", "hours": 1}, KID).status_code, 403)
        self.assertFalse(self.get("/api/my", KID).json()["adultsMayPause"])
        page = self.get(f"/api/phones/{pid}", KID).json()
        self.assertTrue(page["adminsSee"])                                # still sees what admins see


class History(PhonesCase):
    def test_status_changes_are_kept_and_pruned(self):
        from app import housekeeping
        pid = self.add()
        self.report(pid, value="70")
        self.tick()
        self.advance(40)
        self.tick()
        texts = [h["text"] for h in self.get(f"/api/phones/{pid}").json()["admin"]["history"]]
        self.assertIn("Offline", texts)
        self.advance(8 * 24 * 60)
        out = housekeeping.prune(self.now)
        self.assertGreater(out["history"], 0)
        self.assertEqual(self.get(f"/api/phones/{pid}").json()["admin"]["history"], [])
