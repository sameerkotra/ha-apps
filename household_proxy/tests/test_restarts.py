"""Auto-restart (spec/SPEC.md §13.3, §13.9) with a fake clock and a fake smart plug: off → Off for → on with the
configured times; nothing checked while waiting after a restart; up after a restart closes the incident; giving up and
Try again; a crash between off and on → on at start-up; the network guard; an unavailable switch; two sites on one
switch → one sequence; the time window; the gap between incidents; pause; Restart now is admins only."""
import _env  # noqa: F401  (must be first)

import asyncio
import unittest
from unittest import mock

from app import db, restarts, settings, watchdog
from helpers import ADMIN, BOB
from test_monitor import MonitorCase, Port

REAL_HA_CALL = restarts.ha_call          # before any test patches it


class Plug:
    """A smart plug in Home Assistant."""

    def __init__(self, case):
        self.case = case
        self.states = {}
        self.calls = []                  # (service, entity, time)
        self.fail_off = False
        self.fail_on = 0                 # how many turn_on calls fail
        self.unavailable = set()

    def state(self, entity):
        if entity in self.unavailable:
            return "unavailable"
        return self.states.get(entity, "on")

    def call(self, service, entity):
        if service not in ("turn_off", "turn_on") or not entity.startswith("switch."):
            raise ValueError("not allowed")
        self.calls.append((service, entity, self.case.now))
        if service == "turn_off":
            if self.fail_off:
                return False
            self.states[entity] = "off"
            self.case.port_.down()                      # the device loses power
            return True
        if self.fail_on > 0:
            self.fail_on -= 1
            return False
        self.states[entity] = "on"
        if self.case.heals:
            self.case.port_.up()                        # the restart fixed it
        return True


class RestartCase(MonitorCase):
    def setUp(self):
        super().setUp()
        self.plug = Plug(self)
        self.heals = True

        async def fake_sleep(seconds):
            self.now += seconds

        for p in (mock.patch.object(restarts, "ha_state", self.plug.state),
                  mock.patch.object(restarts, "ha_call", self.plug.call),
                  mock.patch.object(restarts, "sleep", fake_sleep),
                  mock.patch.object(restarts, "INLINE", True)):
            p.start()
            self.addCleanup(p.stop)
        restarts._tasks.clear()

    def add_restartable(self, slug="box", port=None, **restart):
        r = {"entity": "switch.box_plug", "confirmed": True, "off_for_s": 5, "wait_after_s": 120, "max_restarts": 3}
        r.update(restart)
        return self.add_checked(slug=slug, name=slug.title(), target=f"127.0.0.1:{(port or self.port_).port}",
                                check={"kind": "tcp", "every_s": 60, "timeout_s": 2, "down_after": 3}, restart=r,
                                alerts={"events": ["down", "restarting", "back_up", "gave_up", "couldnt_restart"]})

    def go_down(self):
        self.port_.up()
        self.tick()
        self.tick()                      # up
        self.port_.down()
        for _ in range(3):
            self.tick()

    def titles(self):
        return [s[1] for s in self.sent]


class Sequence(RestartCase):
    def test_off_wait_on_then_waiting_then_up(self):
        self.add_restartable()
        self.go_down()
        offs = [c for c in self.plug.calls if c[0] == "turn_off"]
        ons = [c for c in self.plug.calls if c[0] == "turn_on"]
        self.assertEqual((len(offs), len(ons)), (1, 1))
        self.assertGreaterEqual(ons[0][2] - offs[0][2], 5)              # Off for
        r = self.row()
        self.assertEqual((r["state"], r["restart_phase"], r["restarts_used"]), ("waiting", None, 1))
        self.assertEqual(self.titles(), ["Box is down", "Box: restarting"])
        self.assertIn("restart 1 of 3", self.sent[1][2])
        checks = self.count_checks()
        self.tick(60)
        self.assertEqual(self.count_checks(), checks)                  # nothing checked while waiting
        self.tick(70)
        r = self.row()
        self.assertEqual(r["state"], "up")
        self.assertEqual(self.titles()[-1], "Box is back up")
        self.assertIn("after 1 restart", self.sent[-1][2])
        with db.get_conn() as conn:
            inc = conn.execute("SELECT * FROM incidents").fetchone()
        self.assertEqual((inc["restarts"], inc["outcome"]), (1, "up_after_restart"))

    def count_checks(self):
        with db.get_conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM checks").fetchone()[0]

    def test_gives_up_then_try_again(self):
        self.heals = False
        s = self.add_restartable(max_restarts=2)
        self.go_down()
        for _ in range(6):
            self.tick(130)
        r = self.row()
        self.assertEqual((r["state"], r["restarts_used"]), ("needs_attention", 2))
        self.assertEqual(sum(1 for c in self.plug.calls if c[0] == "turn_off"), 2)
        self.assertEqual(self.titles().count("Box needs attention"), 1)
        for _ in range(3):
            self.tick(130)
        self.assertEqual(sum(1 for c in self.plug.calls if c[0] == "turn_off"), 2)   # no more restarts
        self.assertEqual(self.c.post(f"/api/admin/sites/{s['id']}/retry", headers=BOB).status_code, 403)
        self.assertEqual(self.c.post(f"/api/admin/sites/{s['id']}/retry", headers=ADMIN).status_code, 200)
        self.tick(1)
        self.assertEqual(sum(1 for c in self.plug.calls if c[0] == "turn_off"), 3)   # the count starts over
        self.assertEqual(self.row()["restarts_used"], 1)
        self.heals = True
        self.plug.call("turn_on", "switch.box_plug")
        self.tick(130)
        self.assertEqual(self.row()["state"], "up")

    def test_never_restart_only_report(self):
        self.add_restartable(max_restarts=0)
        self.go_down()
        self.assertEqual(self.plug.calls, [])
        self.assertEqual(self.row()["state"], "down")


class NeverLeftOff(RestartCase):
    def test_crash_between_off_and_on(self):
        self.add_restartable()
        with db.get_conn() as conn:
            conn.execute("UPDATE sites SET state = 'up', restart_phase = 'off', switched_off_at = ?", (self.now,))
        self.plug.states["switch.box_plug"] = "off"
        restarts.recover()                                           # the app starts again
        self.assertEqual(self.row()["state"], "restarting")
        self.tick(1)
        self.assertEqual(self.plug.calls[0][:2], ("turn_on", "switch.box_plug"))
        r = self.row()
        self.assertEqual((r["state"], r["restart_phase"]), ("waiting", None))

    def test_turn_on_retried_then_admins_told(self):
        self.add_restartable()
        self.plug.fail_on = 1000
        self.go_down()
        ons = [c for c in self.plug.calls if c[0] == "turn_on"]
        self.assertGreater(len(ons), 15)                              # every 30 s for 10 minutes
        self.assertLessEqual(ons[1][2] - ons[0][2], 30)
        self.assertIn("Household Proxy: a switch may be off", self.titles())
        self.assertIsNotNone(self.row()["restart_phase"])             # still marked: it keeps being tried
        self.plug.fail_on = 0
        self.tick(1)
        self.assertEqual(self.plug.calls[-1][0], "turn_on")
        self.assertIsNone(self.row()["restart_phase"])

    def test_switch_off_fails(self):
        self.add_restartable()
        self.plug.fail_off = True
        self.go_down()
        r = self.row()
        self.assertEqual((r["state"], r["restart_phase"], r["restarts_used"]), ("down", None, 0))
        self.assertIn("Box: couldn't restart", self.titles())


class SafetyRules(RestartCase):
    def test_unavailable_switch(self):
        self.add_restartable()
        self.plug.unavailable.add("switch.box_plug")
        self.go_down()
        self.tick()
        self.assertEqual(self.plug.calls, [])
        self.assertEqual(self.titles().count("Box: couldn't restart"), 1)     # told once
        self.assertIn("can't reach the switch", self.row()["blocked_reason"])
        self.plug.unavailable.clear()
        self.tick()
        self.assertEqual(self.plug.calls[0][0], "turn_off")

    def test_reference_address_down(self):
        settings.update({"monitor_reference": f"127.0.0.1:{Port().port}"}, None)    # nothing listens there
        with mock.patch.object(restarts, "reference_down", self.fake_reference(True)):
            self.add_restartable()
            self.go_down()
        self.assertEqual(self.plug.calls, [])
        self.assertIn("network looks down", self.row()["blocked_reason"])
        with mock.patch.object(restarts, "reference_down", self.fake_reference(False)):
            self.tick()
        self.assertEqual(self.plug.calls[0][0], "turn_off")

    def fake_reference(self, down):
        async def f():
            return down
        return f

    def test_reference_check_itself(self):
        p = Port()
        settings.update({"monitor_reference": f"127.0.0.1:{p.port}"}, None)
        self.assertFalse(asyncio.run(restarts.reference_down()))                  # refused: something answered
        settings.update({"monitor_reference": ""}, None)
        self.assertFalse(asyncio.run(restarts.reference_down()))

    def test_most_sites_down_together(self):
        others = [Port(), Port()]
        for o in others:
            o.up()
            self.addCleanup(o.down)
        self.add_restartable()
        for i, o in enumerate(others):
            self.add_checked(slug=f"o{i}", name=f"O{i}", target=f"127.0.0.1:{o.port}",
                             check={"kind": "tcp", "down_after": 3})
        self.port_.up()
        self.tick()
        self.tick()
        self.port_.down()
        others[0].down()                                                         # two of three go down together
        for _ in range(4):
            self.tick()
        self.assertEqual(self.plug.calls, [])
        self.assertIn("most checked sites", self.row()["blocked_reason"])

    def test_two_sites_one_switch(self):
        second = Port()
        self.addCleanup(second.down)
        self.add_restartable()
        self.add_restartable(slug="other", port=second)
        second.up()
        self.port_.up()
        self.tick()
        self.tick()
        self.port_.down()
        second.down()
        for _ in range(3):
            self.tick()
        self.assertEqual([c[0] for c in self.plug.calls], ["turn_off", "turn_on"])
        self.assertEqual(self.row("box")["restarts_used"], 1)
        self.assertEqual(self.row("other")["restarts_used"], 1)

    def test_time_window(self):
        with mock.patch.object(restarts, "_local_hm", lambda now: "12:00"):
            self.add_restartable(window_start="02:00", window_end="06:00")
            self.go_down()
        self.assertEqual(self.plug.calls, [])
        self.assertIn("outside its restart window", self.row()["blocked_reason"])
        self.assertNotIn("Box: couldn't restart", self.titles())                  # expected: not alerted
        with mock.patch.object(restarts, "_local_hm", lambda now: "02:30"):
            self.tick()
        self.assertEqual(self.plug.calls[0][0], "turn_off")

    def test_window_across_midnight(self):
        row = {"window_start": "22:00", "window_end": "02:00"}
        for t, inside in (("23:00", True), ("01:59", True), ("02:00", False), ("12:00", False)):
            with mock.patch.object(restarts, "_local_hm", lambda now, t=t: t):
                self.assertEqual(restarts.in_window(row, 0), inside, t)

    def test_gap_between_incidents(self):
        settings.update({"restart_gap_min": 10}, None)
        self.add_restartable()
        self.go_down()
        self.tick(130)                                     # back up after the restart
        self.assertEqual(self.row()["state"], "up")
        self.port_.down()
        self.heals = True
        for _ in range(3):
            self.tick()                                    # down again within 10 minutes of the restart
        self.assertEqual(sum(1 for c in self.plug.calls if c[0] == "turn_off"), 1)
        self.assertIn("less than 10 minutes", self.row()["blocked_reason"])
        self.tick(600)
        self.assertEqual(sum(1 for c in self.plug.calls if c[0] == "turn_off"), 2)

    def test_no_restart_while_paused(self):
        s = self.add_restartable()
        self.go_down()
        self.plug.calls.clear()
        with db.get_conn() as conn:
            conn.execute("UPDATE sites SET state = 'down', restart_due = 1")
        self.c.post(f"/api/admin/sites/{s['id']}/pause", json={"hours": 1}, headers=ADMIN)
        self.tick()
        self.assertEqual(self.plug.calls, [])


class ByHand(RestartCase):
    def test_restart_now(self):
        s = self.add_restartable()
        self.port_.up()
        self.assertEqual(self.c.post(f"/api/admin/sites/{s['id']}/restart", headers=BOB).status_code, 403)
        r = self.c.post(f"/api/admin/sites/{s['id']}/restart", headers=ADMIN)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual([c[0] for c in self.plug.calls], ["turn_off", "turn_on"])
        self.assertEqual(self.row()["state"], "waiting")
        self.plug.unavailable.add("switch.box_plug")
        self.assertEqual(self.c.post(f"/api/admin/sites/{s['id']}/restart", headers=ADMIN).status_code, 409)

    def test_form_rules(self):
        base = {"name": "M", "use": "monitor", "target": "nas.lan", "check": {"kind": "ping"}}
        for restart, ok in (({"entity": "switch.plug"}, False),                      # not confirmed
                            ({"entity": "switch.plug", "confirmed": True}, True),
                            ({"entity": "light.lamp", "confirmed": True}, False),
                            ({"entity": "switch.plug", "confirmed": True, "window_start": "02:00"}, False),
                            ({"entity": "switch.plug", "confirmed": True, "window_start": "2am",
                              "window_end": "06:00"}, False)):
            r = self.c.post("/api/admin/sites", headers=ADMIN, json={**base, "slug": "m", "restart": restart})
            self.assertEqual(r.status_code == 200, ok, (restart, r.text))
            if r.status_code == 200:
                self.assertEqual(r.json()["restart"]["entity"], "switch.plug")
                self.c.delete(f"/api/admin/sites/{r.json()['id']}", headers=ADMIN)
        r = self.c.post("/api/admin/sites", headers=ADMIN, json={
            "name": "P", "target": "http://192.168.1.10", "trusted_ok": True,
            "restart": {"entity": "switch.plug", "confirmed": True}})
        self.assertEqual(r.status_code, 422)                                          # needs a check

    def test_only_turn_on_and_off_of_switches(self):
        for service, entity in (("toggle", "switch.plug"), ("turn_on", "lock.front_door"),
                                ("turn_off", "switch.plug; light.x")):
            with self.assertRaises(ValueError):
                REAL_HA_CALL(service, entity)


if __name__ == "__main__":
    unittest.main()
