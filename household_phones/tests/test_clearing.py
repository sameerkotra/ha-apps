"""Clearing the Last notification sensors in Home Assistant (spec/SPEC.md §6.2): on by default, for monitored phones,
forwarded or not; our own overwrite is never a check-in or a notification; a resent cleared value isn't forwarded
twice; the sweep; the setting off."""
from base import ASHA, PhonesCase

from app import clearing, db, housekeeping, monitor
from fakes import PIXEL_SENSORS, state_event

MSGS = "com.google.android.apps.messaging"


def note(text="Your one-time code is 482913.", title="AX-BANK", **extra):
    return {"package": MSGS, "android.title": title, "android.text": text, "friendly_name": "Asha Pixel Last notification",
            "icon": "mdi:bell-ring", **extra}


class Clearing(PhonesCase):
    def setUp(self):
        super().setUp()
        self.pid = self.add()
        self.eid = self.ha.eid("Asha Pixel", "last_notification")

    def notify(self, attrs, state="AX-BANK: Your one-time code"):
        self.report(self.pid, key="last_notification", value=state, attrs=attrs)

    def written(self):
        return [s for e, s in self.ha.set_states if e == self.eid]

    def test_on_by_default_and_no_text_left(self):
        self.notify(note())                                           # forwarding is off: still cleared
        self.assertEqual(self.written(), [{"state": "cleared", "attributes": {
            "friendly_name": "Asha Pixel Last notification", "icon": "mdi:bell-ring", "cleared_by": "Household Phones"}}])
        self.assertNotIn("482913", repr(self.ha.set_states))

    def test_forwarded_then_cleared(self):
        r = self.put(f"/api/phones/{self.pid}/forwarding", {"on": True}, ASHA)
        self.assertEqual(r.status_code, 200, r.text)
        self.notify(note())
        self.assertEqual(len(self.posted), 1)
        self.assertEqual(len(self.written()), 1)

    def test_our_write_is_not_a_report_or_a_notification(self):
        self.put(f"/api/phones/{self.pid}/forwarding", {"on": True}, ASHA)
        before = self.row(self.pid)["last_checkin"]
        self.advance(30)
        monitor.on_state_event(state_event(self.eid, "cleared", {"cleared_by": "Household Phones"}, at=self.now))
        self.assertEqual(self.row(self.pid)["last_checkin"], before)
        self.assertEqual(self.posted, [])
        self.assertEqual(self.written(), [])                          # never clears its own value
        # nor in the fallback poll
        self.ha.state[self.eid] = {"state": "cleared", "attributes": {"cleared_by": "Household Phones"},
                                   "last_updated": "2030-01-01T00:00:00+00:00", "last_reported": "2030-01-01T00:00:00+00:00"}
        monitor._poll_seen.pop(self.eid, None)
        self.assertEqual(monitor.poll_states(self.ha, self.now, force=True), 0)

    def test_a_resent_cleared_value_is_cleared_again_not_forwarded_twice(self):
        self.put(f"/api/phones/{self.pid}/forwarding", {"on": True}, ASHA)
        attrs = note(post_time=1_790_000_000_000)
        self.notify(attrs)
        self.advance(5)
        self.notify(attrs)                                            # the phone sends the same one again
        self.assertEqual(len(self.posted), 1)
        self.assertEqual(len(self.written()), 2)
        self.advance(5)
        self.notify(note(post_time=1_790_000_400_000))                 # a new one with the same words
        self.assertEqual(len(self.posted), 2)

    def test_not_monitored_or_setting_off(self):
        other = self.add("Bob Pixel", "u-bob", monitored=False)
        self.report(other, name="Bob Pixel", key="last_notification", value="hi", attrs=note())
        self.assertEqual(self.ha.set_states, [])
        self.setting(clear_notification_sensor=False)
        self.notify(note())
        self.assertEqual(self.ha.set_states, [])

    def test_removed_notification_too_and_unavailable_left(self):
        cid = self.add("Cara Pixel", "u-cara", sensors=PIXEL_SENSORS + ("last_removed_notification",))
        rid = self.ha.eid("Cara Pixel", "last_removed_notification")
        self.report(cid, name="Cara Pixel", key="last_removed_notification", value="gone", attrs=note())
        self.assertEqual([e for e, _ in self.ha.set_states], [rid])
        self.notify(note(), state="unavailable")
        self.assertEqual(len(self.written()), 0)

    def test_sweep_clears_what_is_left(self):
        self.ha.state[self.eid] = {"state": "an old message", "attributes": note()}
        self.assertEqual(clearing.sweep(self.ha), 1)
        self.assertEqual(self.ha.state[self.eid]["state"], "cleared")
        self.assertEqual(clearing.sweep(self.ha), 0)                  # nothing left
        self.ha.state[self.eid] = {"state": "an old message", "attributes": note()}
        housekeeping.quick(12)
        self.assertEqual(self.ha.state[self.eid]["state"], "cleared")
