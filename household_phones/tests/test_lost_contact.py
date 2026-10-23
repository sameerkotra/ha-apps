"""Lost contact with the phones (spec/SPEC.md §3.1, §5): only with evidence that the problem is at home (the "please
report" requests fail), only once each phone has been offline for its own Alert-when-offline time, never inside the
"Don't check for lost contact between" times, and one alert per episode (never again within the cooldown).
T0 is Friday 2027-01-15 08:00 (UTC here)."""
from base import T0, PhonesCase

from app import ha_source

FAIL = ha_source.CommandResult(False, "cant_check", "Home Assistant couldn't reach the push service.")


class LostContact(PhonesCase):
    def setUp(self):
        super().setUp()
        self.people([{"name": "Ann", "userId": "u-ann", "phones": [{"label": "Ann", "tracker": "x",
                                                                    "service": "mobile_app_ann_spare"}]}])

    def phones(self, n, delays=None):
        ids = [self.add(f"Phone {i}", user_id="u-asha") for i in range(n)]
        for i, pid in enumerate(ids):
            if delays:
                self.set_row(pid, offline_alert_after_min=delays[i])
            self.report(pid, name=f"Phone {i}")
        self.tick()
        return ids

    def walk(self, until_min, step=5.0, keep=()):
        """Tick every `step` minutes until T0 + `until_min`; the phones in `keep` (index, id) report each time."""
        end = T0 + until_min * 60
        while self.now + step * 60 <= end + 1e-6:
            self.advance(step)
            for i, pid in keep:
                self.report(pid, name=f"Phone {i}")
            self.tick()

    def lost_alerts(self):
        return [s for s in self.sent if "Lost contact" in s[1]]

    def offline(self):
        return [s for s in self.sent if "Offline" in s[1]]

    def state(self):
        return self.get("/api/admin/phones").json()["lostContact"]

    # ------------------------------------------------------------------ asleep, not lost
    def test_asleep_together_while_requests_go_through(self):
        """All phones idle in deep sleep at once: the requests are sent fine, so Home Assistant's internet and the push
        service work. No lost-contact alert; each phone's Offline alert follows its own delay (2 / 6 / 10 hours)."""
        self.phones(3, delays=(120, 360, 600))
        self.walk(145)                                          # Offline at 08:30 (27 minutes), counting from then
        self.assertEqual(self.offline(), [])
        self.walk(155)
        self.assertEqual(len(self.offline()), 1)
        self.assertIn("has been offline for 2 hours", self.offline()[0][2])
        self.walk(385)
        self.assertEqual(len(self.offline()), 1)
        self.walk(395)
        self.assertEqual(len(self.offline()), 2)
        self.walk(625)
        self.assertEqual(len(self.offline()), 2)
        self.walk(635)
        self.assertEqual(len(self.offline()), 3)
        self.assertIsNone(self.state())
        self.assertEqual((self.lost_alerts(), self.ha.notes), ([], []))

    # ------------------------------------------------------------------ requests failing
    def test_failing_requests_one_alert_for_the_whole_episode(self):
        ids = self.phones(4)
        self.ha.result = FAIL
        self.walk(20, keep=[(3, ids[3])])                       # 3 of 4 quiet, their requests fail
        lost = self.state()
        self.assertEqual((lost["silent"], lost["total"]), (3, 4))
        self.assertEqual(len(self.ha.notes), 1)
        self.assertIn("Home Assistant couldn't send requests to 3 of 4 phones since 08:20 — check Home Assistant's "
                      "internet / Nabu Casa", self.ha.notes[0][1])
        self.walk(4 * 60, keep=[(3, ids[3])])                   # hours later, still one alert
        self.assertEqual(len(self.ha.notes), 1)
        self.assertEqual(len(self.lost_alerts()), 1)
        self.assertEqual(self.offline(), [])

    def test_half_failing_is_lost_contact(self):
        ids = self.phones(4)
        self.ha.result = FAIL
        self.walk(60, keep=[(2, ids[2]), (3, ids[3])])
        self.assertIsNotNone(self.state())                      # half (2 of 4) and the minimum (2): lost
        self.assertEqual(len(self.lost_alerts()), 1)

    def test_one_failing_of_three_is_not_lost_contact(self):
        ids = self.phones(3)
        self.ha.result = FAIL
        self.walk(60, keep=[(1, ids[1]), (2, ids[2])])
        self.assertIsNone(self.state())
        self.assertEqual((self.lost_alerts(), self.ha.notes), ([], []))

    def test_each_phones_delay_is_respected(self):
        """Requests fail for all three (2 / 6 / 10 hour delays): a phone counts once it has been offline (from 08:27)
        for its own delay, so two of three count at 14:27."""
        self.phones(3, delays=(120, 360, 600))
        self.ha.result = FAIL
        self.walk(385)
        self.assertIsNone(self.state())
        self.assertEqual(self.ha.notes, [])
        self.walk(395)
        self.assertEqual(len(self.ha.notes), 1)
        self.assertIn("couldn't send requests to 2 of 3 phones since 08:20", self.ha.notes[0][1])
        self.walk(640)
        self.assertEqual(self.state()["silent"], 3)             # the third joins the same episode: no new alert
        self.assertEqual(len(self.ha.notes), 1)
        self.assertEqual(self.offline(), [])                    # Can't check, not Offline

    # ------------------------------------------------------------------ the quiet window
    def test_quiet_window(self):
        m = self.get("/api/admin/settings").json()["meta"]
        self.assertEqual((m["lost_contact_quiet_start"]["group"], m["lost_contact_quiet_end"]["group"]),
                         ("checkin", "checkin"))
        r = self.put("/api/admin/settings", {"lost_contact_quiet_start": "7pm"})
        self.assertEqual(r.status_code, 422, r.text)
        self.setting(lost_contact_quiet_start="07:00", lost_contact_quiet_end="09:00")
        ids = self.phones(4)
        self.ha.result = FAIL
        self.walk(55, keep=[(3, ids[3])])                       # 08:55: inside the window
        self.assertIsNone(self.state())
        self.assertEqual(self.ha.notes, [])
        self.walk(65, keep=[(3, ids[3])])                       # 09:05: checked again
        self.assertIsNotNone(self.state())
        self.assertEqual(len(self.ha.notes), 1)

    # ------------------------------------------------------------------ no repeats
    def test_an_episode_that_ends_and_starts_again_respects_the_cooldown(self):
        ids = self.phones(3)
        self.ha.result = FAIL
        keep = [(2, ids[2])]
        self.walk(20, keep=keep)
        self.assertEqual(len(self.ha.notes), 1)                 # 08:20
        self.walk(25, keep=keep + [(0, ids[0]), (1, ids[1])])   # they get through for a moment: episode over
        self.assertIsNone(self.state())
        self.walk(45, keep=keep)                                # quiet and failing again at 08:45
        self.assertIsNotNone(self.state())
        self.assertEqual(len(self.ha.notes), 1)                 # within the 30-minute cooldown: no second alert
        self.walk(50, keep=keep + [(0, ids[0]), (1, ids[1])])
        self.assertIsNone(self.state())
        self.walk(70, keep=keep)                                # 09:10: 50 minutes after the first alert
        self.assertEqual(len(self.ha.notes), 2)
        self.assertEqual(len(self.lost_alerts()), 2)
