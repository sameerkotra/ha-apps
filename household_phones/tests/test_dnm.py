"""Do-not-monitor hours (spec/SPEC.md §3.1, §9): the owner's windows (days, crossing midnight, several windows, the
household's limit), what happens during one (no requests, alerts or history; admins see one line; forwarding goes on
unless paused) and the grace after it ends. T0 is Friday 2027-01-15 08:00 in Home Assistant's time zone (UTC here)."""
from base import ADMIN, ASHA, KID, PhonesCase

from app import alerts, db, dnm, forwarding

MON, TUE, FRI, SAT, SUN = 0, 1, 4, 5, 6
WEEKDAYS, WEEKENDS, EVERY = [0, 1, 2, 3, 4], [5, 6], [0, 1, 2, 3, 4, 5, 6]


def w(days, start, end, pause=False):
    return {"days": days, "start": start, "end": end, "pauseForwarding": pause}


def at(day, hhmm):
    h, m = hhmm.split(":")
    return day * 1440 + int(h) * 60 + int(m)


HOUSE = [w(WEEKDAYS, "22:00", "07:00"), w(WEEKENDS, "23:00", "09:00")]


class Windows(PhonesCase):
    def test_days_and_crossing_midnight(self):
        on = lambda day, t: dnm.state_at(HOUSE, at(day, t))  # noqa: E731
        self.assertEqual(on(MON, "23:00")["minutes"], 8 * 60)          # Monday night until Tuesday 07:00
        self.assertIsNone(on(MON, "21:59"))
        self.assertIsNone(on(TUE, "07:00"))
        self.assertEqual(on(SAT, "02:00")["minutes"], 5 * 60)          # Friday's window runs into Saturday
        self.assertIsNone(on(SAT, "08:00"))
        self.assertEqual(on(MON, "08:00")["minutes"], 60)              # Sunday's runs into Monday until 09:00
        self.assertEqual(on(SUN, "23:30")["minutes"], 9 * 60 + 30)

    def test_back_to_back_windows_are_joined(self):
        ws = [w([MON], "20:00", "23:00"), w([MON], "23:00", "06:00", pause=True)]
        st = dnm.state_at(ws, at(MON, "21:00"))
        self.assertEqual((st["minutes"], st["pauseForwarding"]), (9 * 60, False))
        self.assertTrue(dnm.state_at(ws, at(TUE, "01:00"))["pauseForwarding"])
        self.assertEqual(dnm.minutes_since_end(ws, at(TUE, "06:30")), 30)
        self.assertIsNone(dnm.minutes_since_end(ws, at(MON, "23:30")))

    def test_hours_a_day_and_validation(self):
        self.assertEqual(dnm.covered_minutes(HOUSE), [11 * 60, 9 * 60, 9 * 60, 9 * 60, 9 * 60, 8 * 60, 10 * 60])
        dnm.check_limit(HOUSE, 12)
        with self.assertRaises(dnm.Invalid):
            dnm.check_limit([w([MON], "08:00", "23:00")], 12)
        with self.assertRaises(dnm.Invalid):                           # overlapping windows count once, joined ones add up
            dnm.check_limit([w([MON], "00:00", "08:00"), w([MON], "08:00", "16:00")], 12)
        for bad in ([w([], "22:00", "07:00")], [w([7], "22:00", "07:00")], [w([MON], "22:00", "22:00")],
                    [w([MON], "10pm", "07:00")], [w([MON], "22:00", "07:00")] * 8, "x"):
            with self.assertRaises(dnm.Invalid):
                dnm.clean(bad)

    def test_summary_text(self):
        self.assertEqual(dnm.summary(HOUSE), "Not monitored weekdays 22:00–07:00 · weekends 23:00–09:00")
        self.assertEqual(dnm.days_text([MON, TUE, 2, FRI]), "Mon–Wed, Fri")
        self.assertEqual(dnm.days_text(EVERY), "every day")


class OwnerSetsThem(PhonesCase):
    def setUp(self):
        super().setUp()
        self.pid = self.add()

    def put_dnm(self, windows, who=ASHA):
        return self.put(f"/api/phones/{self.pid}/dnm", {"windows": windows}, who)

    def test_only_the_owner_within_the_limit(self):
        r = self.put_dnm(HOUSE)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["dnm"]["windows"], HOUSE)
        self.assertEqual(r.json()["dnm"]["maxHours"], 12)
        self.assertEqual(self.put_dnm([], ADMIN).status_code, 403)              # admins can't change them
        res = self.put_dnm([w(EVERY, "08:00", "23:00")])
        self.assertEqual(res.status_code, 422)
        self.assertIn("At most 12 hours a day", res.json()["detail"])
        self.setting(dnm_max_hours=16)
        self.assertEqual(self.put_dnm([w(EVERY, "08:00", "23:00")]).status_code, 200)
        self.setting(dnm_allowed=False)
        self.assertEqual(self.put_dnm(HOUSE).status_code, 403)

    def test_admins_see_that_they_exist(self):
        self.put_dnm(HOUSE)
        text = "Not monitored weekdays 22:00–07:00 · weekends 23:00–09:00"
        self.assertEqual(self.get("/api/admin/phones").json()["phones"][0]["dnmText"], text)
        self.assertEqual(self.get(f"/api/phones/{self.pid}").json()["admin"]["dnmText"], text)
        see = " ".join(self.get(f"/api/phones/{self.pid}", ASHA).json()["adminsSee"])
        self.assertIn(text, see)
        self.assertIn("can't change them", see)
        texts = [h["text"] for h in self.get(f"/api/phones/{self.pid}").json()["admin"]["history"]]
        self.assertTrue(any(t.startswith("Do-not-monitor hours set by Asha") for t in texts))

    def test_children_cant(self):
        kid = self.add("Kid Phone", user_id="u-kid")
        self.put(f"/api/phones/{kid}/dnm", {"windows": HOUSE}, KID)
        self.put("/api/admin/people/u-kid", {"isChild": True})
        self.assertEqual(self.put(f"/api/phones/{kid}/dnm", {"windows": HOUSE}, KID).status_code, 403)
        self.assertFalse(self.get(f"/api/phones/{kid}", KID).json()["dnm"]["allowed"])


class DuringAWindow(PhonesCase):
    """Friday 07:00–09:00 (and T0 is Friday 08:00)."""

    def setUp(self):
        super().setUp()
        self.people([{"name": "Ann", "userId": "u-ann", "phones": [{"label": "Ann", "tracker": "x",
                                                                    "service": "mobile_app_ann"}]}])
        self.pid = self.add()
        self.report(self.pid, value="44")
        self.tick()
        self.advance(-60)                                       # set up at 07:00, as the window starts
        r = self.put(f"/api/phones/{self.pid}/dnm", {"windows": [w([FRI], "07:00", "09:00")]}, ASHA)
        self.assertEqual(r.status_code, 200, r.text)
        self.advance(60)

    def history_count(self):
        with db.get_conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM history WHERE phone_id = ? AND kind = 'status'",
                                (self.pid,)).fetchone()[0]

    def test_no_requests_alerts_or_history(self):
        before = self.history_count()
        self.advance(55)                                        # 08:55, silent for 55 minutes
        self.tick()
        self.assertEqual(self.row(self.pid)["status"], "dnm")
        self.assertEqual(self.ha.commands, [])
        self.assertEqual(self.sent, [])
        self.assertEqual(self.history_count(), before)
        self.report(self.pid)                                   # reports during the window aren't written
        self.assertLess(self.row(self.pid)["last_checkin"], self.now - 3000)
        self.assertEqual(alerts.send("battery", self.pid, "x")["skipped"], "do-not-monitor hours")
        with db.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM alert_queue").fetchone()[0], 0)

    def test_admins_see_only_the_line_check_now_says_why(self):
        row = self.get("/api/admin/phones").json()["phones"][0]
        self.assertTrue(row["hidden"])
        self.assertEqual(row["label"], "Not monitored (until 09:00)")
        for k in ("battery", "network", "lastCheckin"):
            self.assertNotIn(k, row)
        self.assertIsNone(self.get(f"/api/phones/{self.pid}").json()["admin"]["sensors"])
        r = self.post(f"/api/phones/{self.pid}/check", None, ADMIN)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["detail"], "Not monitored right now — Asha set do-not-monitor hours until 09:00.")
        r = self.post(f"/api/phones/{self.pid}/check", None, ASHA)
        self.assertEqual(r.json()["detail"], "Not monitored right now — you set do-not-monitor hours until 09:00.")
        self.assertEqual(self.ha.commands, [])
        mine = self.get(f"/api/phones/{self.pid}", ASHA).json()
        self.assertEqual(mine["battery"], 44)                   # the owner still sees everything
        self.assertEqual(mine["dnm"]["active"]["untilText"], "09:00")
        self.assertEqual(self.get("/api/admin/phones").json()["summary"]["paused"], 1)

    def test_grace_after_the_window(self):
        self.advance(61)                                        # 09:01: silent since 07:00
        self.tick()
        self.assertEqual(self.row(self.pid)["status"], "late")  # asked again, but not Offline at once
        self.assertTrue(self.ha.commands)
        self.advance(20)                                        # 09:21: still within interval + 12 minutes
        self.tick()
        self.assertEqual(self.row(self.pid)["status"], "late")
        self.assertFalse([s for s in self.sent if "Offline" in s[1]])
        self.advance(7)                                         # 09:28
        self.tick()
        self.assertEqual(self.row(self.pid)["status"], "offline")
        self.assertEqual(len([s for s in self.sent if "Offline" in s[1]]), 1)

    def test_no_lost_contact_when_shared_hours_end(self):
        others = [self.add(f"Phone {i}", user_id="u-asha") for i in range(2)]
        for i, pid in enumerate(others):
            self.set_row(pid, dnm_windows=self.row(self.pid)["dnm_windows"], last_checkin=self.now - 3600)
        self.advance(65)
        self.tick()
        self.assertIsNone(self.get("/api/admin/phones").json()["lostContact"])
        self.assertFalse([s for s in self.sent if "Lost contact" in s[1]])

    def test_no_restart_alert_afterwards(self):
        self.set_row(self.pid, last_reboot="2027-01-14T10:00:00+00:00")
        self.ha.state[self.ha.eid("Asha Pixel", "last_reboot")] = {"state": "2027-01-15T07:30:00+00:00", "attributes": {}}
        from app import phones
        phones.refresh(self.ha)
        self.tick()
        self.advance(61)
        self.report(self.pid)
        self.tick()
        self.assertFalse([s for s in self.sent if "Restarted" in s[1]])

    def test_setting_off_means_no_window(self):
        self.setting(dnm_allowed=False)
        self.advance(25)
        self.tick()
        self.assertNotEqual(self.row(self.pid)["status"], "dnm")
        self.assertTrue(self.ha.commands)


class ForwardingDuringAWindow(PhonesCase):
    def setUp(self):
        super().setUp()
        self.pid = self.add()
        self.put(f"/api/phones/{self.pid}/forwarding", {"on": True}, ASHA)

    def notify(self, text):
        self.report(self.pid, key="last_notification", value="x",
                    attrs={"package": "com.google.android.apps.messaging", "android.title": "BANK", "android.text": text})

    def test_goes_on_unless_the_window_pauses_it(self):
        self.put(f"/api/phones/{self.pid}/dnm", {"windows": [w([FRI], "07:00", "09:00")]}, ASHA)
        self.notify("Your code is 482913")
        self.assertEqual(len(self.posted), 1)                   # the owner's own choice: it keeps forwarding
        self.put(f"/api/phones/{self.pid}/dnm", {"windows": [w([FRI], "07:00", "09:00", pause=True)]}, ASHA)
        self.advance(3)
        self.notify("Your code is 771122")
        self.assertEqual(len(self.posted), 1)
        self.assertEqual(forwarding.handle(self.pid, {"state": "x", "attributes": {
            "package": "com.google.android.apps.messaging", "android.text": "code 554433"}}, self.now),
            "paused (do-not-monitor hours)")
        self.advance(60)                                        # 09:04: the window is over
        self.notify("Your code is 990011")
        self.assertEqual(len(self.posted), 2)


if __name__ == "__main__":
    import unittest
    unittest.main()
