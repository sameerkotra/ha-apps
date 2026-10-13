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
        self.tick(1)                                          # at once: the gap rule doesn't hold back Try again
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
            conn.execute("INSERT INTO switched_off (entity, site_ids, at) VALUES ('switch.box_plug', '[1]', ?)",
                         (self.now,))
            conn.execute("UPDATE switched_off SET site_ids = (SELECT json_array(id) FROM sites WHERE slug = 'box')")
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

    def test_switch_changed_or_site_deleted_while_off(self):
        s = self.add_restartable()
        self.plug.fail_on = 1000
        self.go_down()                                           # turned off; turning on keeps failing
        body = {"name": "Box", "slug": "box", "use": "monitor", "target": f"127.0.0.1:{self.port_.port}",
                "check": {"kind": "tcp", "every_s": 60, "timeout_s": 2, "down_after": 3}}
        self.c.put(f"/api/admin/sites/{s['id']}", json=body, headers=ADMIN)       # the admin removes the switch
        self.c.delete(f"/api/admin/sites/{s['id']}", headers=ADMIN)               # … and even deletes the site
        self.plug.fail_on = 0
        self.tick(700)
        self.assertEqual(self.plug.calls[-1][:2], ("turn_on", "switch.box_plug"))
        self.assertEqual(self.plug.states["switch.box_plug"], "on")
        with db.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM switched_off").fetchone()[0], 0)

    def test_switch_changed_while_off_site_checked_again(self):
        s = self.add_restartable()
        self.plug.fail_on = 1000
        self.go_down()
        body = {"name": "Box", "slug": "box", "use": "monitor", "target": f"127.0.0.1:{self.port_.port}",
                "check": {"kind": "tcp", "every_s": 60, "timeout_s": 2, "down_after": 3},
                "restart": {"entity": "switch.other_plug", "confirmed": True}}
        self.c.put(f"/api/admin/sites/{s['id']}", json=body, headers=ADMIN)
        self.plug.fail_on = 0
        self.port_.up()
        for _ in range(4):
            self.tick(130)
        self.assertEqual(self.plug.states["switch.box_plug"], "on")
        self.assertEqual(self.row()["state"], "up")

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

    def test_restart_now_on_a_healthy_site(self):
        s = self.add_restartable()
        self.port_.up()
        self.tick()
        self.tick()
        self.heals = False                                      # the device is slow to come back after the restart
        self.c.post(f"/api/admin/sites/{s['id']}/restart", headers=ADMIN)
        self.tick(130)                                          # one failed check after the wait: not "down" yet
        self.assertEqual(sum(1 for c in self.plug.calls if c[0] == "turn_off"), 1)
        self.assertNotIn("Box is down", self.titles())
        self.tick()
        self.tick()                                             # Down after 3 → down, an incident, then a restart
        self.assertIn("Box is down", self.titles())
        with db.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0], 1)

    def test_healthy_site_is_up_again_after_restart_now(self):
        s = self.add_restartable()
        self.port_.up()
        self.tick()
        self.tick()
        self.c.post(f"/api/admin/sites/{s['id']}/restart", headers=ADMIN)
        self.assertEqual(self.row()["state"], "waiting")
        self.tick(130)
        self.assertEqual(self.row()["state"], "up")             # it used to stay "waiting" for good

    def test_one_sequence_per_switch_even_by_hand(self):
        s = self.add_restartable()
        self.assertTrue(restarts._claim("switch.box_plug"))    # an automatic restart is deciding
        try:
            r = self.c.post(f"/api/admin/sites/{s['id']}/restart", headers=ADMIN)
            self.assertEqual(r.status_code, 409)
            self.assertIn("already running", r.text)
        finally:
            restarts._release("switch.box_plug")
        self.assertEqual(self.plug.calls, [])

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


class SharedSwitch(RestartCase):
    """Sites on one switch: one sequence, every site on it marked, none restarted while any of them is paused or outside
    its window — and the switch is never left off."""

    def setUp(self):
        super().setUp()
        self.second = Port()
        self.addCleanup(self.second.down)

    def both(self, **second_restart):
        a = self.add_restartable()
        b = self.add_restartable(slug="other", port=self.second, **second_restart)
        self.second.up()
        self.port_.up()
        self.tick()
        self.tick()
        return a, b

    def offs(self):
        return sum(1 for c in self.plug.calls if c[0] == "turn_off")

    def test_a_sibling_that_was_up_restarts_too_and_waits(self):
        self.both()
        self.port_.down()                       # only box goes down; "other" is up on the same plug
        for _ in range(3):
            self.tick()
        self.assertEqual(self.offs(), 1)
        box, other = self.row("box"), self.row("other")
        self.assertEqual((box["state"], box["restarts_used"]), ("waiting", 1))
        self.assertEqual(other["state"], "waiting")                     # it lost power as well
        self.assertEqual(other["restarts_used"], 0)                     # but nothing of it was down
        self.assertIsNotNone(other["last_restart_at"])
        self.assertEqual(other["last_restart_at"], box["last_restart_at"])
        self.assertIsNone(other["incident_id"])
        self.assertIn("Other: restarting", self.titles())
        self.assertIn("shares a switch with Box", [t for _, title, t, _ in self.sent if title == "Other: restarting"][0])
        with db.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM restart_log").fetchone()[0], 2)
        checks = self.count_checks()
        self.tick(60)
        self.assertEqual(self.count_checks(), checks)                   # neither is checked while it starts
        self.tick(70)
        self.assertEqual((self.row("box")["state"], self.row("other")["state"]), ("up", "up"))

    def test_restart_now_marks_the_siblings(self):
        a, _ = self.both()
        self.c.post(f"/api/admin/sites/{a['id']}/restart", headers=ADMIN)
        self.assertEqual(self.offs(), 1)
        self.assertEqual((self.row("box")["state"], self.row("other")["state"]), ("waiting", "waiting"))

    def test_paused_sibling_holds_back_the_restart(self):
        _, b = self.both()
        self.c.post(f"/api/admin/sites/{b['id']}/pause", json={"hours": 1}, headers=ADMIN)
        self.port_.down()
        for _ in range(4):
            self.tick()
        self.assertEqual(self.offs(), 0)
        self.assertIn("Other, on the same switch, is paused", self.row("box")["blocked_reason"])
        self.assertNotIn("Box: couldn't restart", self.titles())        # maintenance: expected, not alerted
        r = self.c.post(f"/api/admin/sites/{self.row('box')['id']}/restart", headers=ADMIN)
        self.assertEqual(r.status_code, 409)                            # not by hand either
        self.assertIn("paused", r.json()["detail"])
        self.c.post(f"/api/admin/sites/{b['id']}/resume", headers=ADMIN)
        self.tick()
        self.assertEqual(self.offs(), 1)

    def test_sibling_outside_its_window_holds_back_the_restart(self):
        with mock.patch.object(restarts, "_local_hm", lambda now: "12:00"):
            self.both(window_start="02:00", window_end="06:00")
            self.port_.down()
            for _ in range(4):
                self.tick()
        self.assertEqual(self.offs(), 0)
        self.assertIn("Other, on the same switch", self.row("box")["blocked_reason"])
        with mock.patch.object(restarts, "_local_hm", lambda now: "03:00"):
            self.tick()
        self.assertEqual(self.offs(), 1)

    def test_a_sibling_that_gave_up_keeps_its_state(self):
        self.both()
        with db.get_conn() as conn:
            conn.execute("UPDATE sites SET state = 'needs_attention', restart_due = 0 WHERE slug = 'other'")
        self.second.down()
        self.port_.down()
        for _ in range(3):
            self.tick()
        self.assertEqual(self.offs(), 1)
        other = self.row("other")
        self.assertEqual(other["state"], "needs_attention")             # no second "gave up"
        self.assertGreater(other["wait_until"], self.now)              # not checked while it starts again
        self.assertEqual(self.titles().count("Other needs attention"), 0)

    def test_never_left_off_covers_every_site_on_the_switch(self):
        self.both()
        self.plug.fail_on = 1000
        self.port_.down()
        for _ in range(3):
            self.tick()
        with db.get_conn() as conn:
            ids = conn.execute("SELECT site_ids FROM switched_off").fetchone()[0]
        self.assertEqual(sorted(json_ids(ids)), sorted([self.row("box")["id"], self.row("other")["id"]]))
        restarts.recover()                                             # the app starts again
        self.assertEqual(self.row("other")["state"], "restarting")
        self.plug.fail_on = 0
        self.tick(1)
        self.assertEqual(self.plug.calls[-1][0], "turn_on")
        self.assertEqual((self.row("box")["restart_phase"], self.row("other")["restart_phase"]), (None, None))
        self.assertEqual(self.row("other")["state"], "waiting")

    def count_checks(self):
        with db.get_conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM checks").fetchone()[0]


def json_ids(text):
    import json
    return json.loads(text)


class Flapping(RestartCase):
    """A site that keeps going down: no restart on a single failed check after a restart, a growing wait between
    incidents' restarts, and at most a set number of automatic restarts a day."""

    def offs(self):
        return sum(1 for c in self.plug.calls if c[0] == "turn_off")

    def test_down_after_failures_needed_again_after_a_restart(self):
        self.heals = False
        self.add_restartable(max_restarts=3)
        self.go_down()
        self.assertEqual(self.offs(), 1)
        self.tick(130)                                     # the wait is over: one failed check
        self.assertEqual(self.offs(), 1)
        self.assertEqual(self.row()["state"], "waiting")
        self.tick()                                        # two
        self.assertEqual(self.offs(), 1)
        self.tick()                                        # three in a row: the next restart
        self.assertEqual(self.offs(), 2)

    def test_up_for_one_check_doesnt_reset_everything(self):
        settings.update({"restart_gap_min": 0, "max_restarts_per_day": 3}, None)
        self.add_restartable()
        self.go_down()                                     # restart 1 (heals)
        for _ in range(6):
            self.tick(130)                                 # up once …
            self.port_.down()                              # … and down again: a new incident each time
            for _ in range(3):
                self.tick()                                # (each restart brings it back: the plug heals it)
        self.assertEqual(self.offs(), 3)                   # the most per day, whatever the incidents
        self.assertIn("most automatic restarts per day", self.row()["blocked_reason"])
        self.assertIn("Box: couldn't restart", self.titles())          # people are told
        # Restart now still works, and Try again starts the count over
        self.assertEqual(self.c.post(f"/api/admin/sites/{self.row()['id']}/restart", headers=ADMIN).status_code, 200)

    def test_the_wait_between_incidents_grows(self):
        settings.update({"restart_gap_min": 10}, None)
        self.assertEqual([restarts.required_gap(n) for n in (0, 1, 2, 3, 4)], [600, 600, 1200, 2400, 4800])
        settings.update({"restart_gap_min": 0}, None)
        self.assertEqual([restarts.required_gap(n) for n in (1, 2, 3)], [0, 600, 1200])     # at least 5 minutes
        settings.update({"restart_gap_min": 1440}, None)
        self.assertEqual(restarts.required_gap(5), 12 * 3600)                                 # at most 12 hours

    def test_try_again_clears_the_daily_count(self):
        s = self.add_restartable(max_restarts=1)
        with db.get_conn() as conn:
            conn.executemany("INSERT INTO restart_log (site_id, at, manual) VALUES (?, ?, 0)",
                             [(s["id"], self.now - 100 * i) for i in range(6)])
            conn.execute("UPDATE sites SET state = 'needs_attention' WHERE id = ?", (s["id"],))
        self.assertTrue(restarts.try_again(s["id"]))
        with db.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM restart_log").fetchone()[0], 0)


class CheckNowDuringARestart(RestartCase):
    def test_check_now_while_waiting_isnt_recorded(self):
        s = self.add_restartable()
        self.heals = False
        self.go_down()
        self.assertEqual(self.row()["state"], "waiting")
        before = self.row()
        r = self.c.post(f"/api/admin/sites/{s['id']}/check-now", headers=ADMIN).json()
        self.assertFalse(r["ok"])
        self.assertFalse(r["recorded"])
        after = self.row()
        self.assertEqual((after["state"], after["fails_in_row"], after["restarts_used"], after["restart_due"]),
                         (before["state"], before["fails_in_row"], before["restarts_used"], before["restart_due"]))
        self.assertEqual(sum(1 for c in self.plug.calls if c[0] == "turn_off"), 1)    # no extra restart

    def test_check_started_before_a_restart_is_dropped(self):
        s = self.add_restartable()
        self.port_.up()
        self.tick()
        self.tick()
        started = self.now
        self.now += 1
        self.c.post(f"/api/admin/sites/{s['id']}/restart", headers=ADMIN)     # meanwhile, an admin restarts it
        self.tick(130)
        res = watchdog.Result(False, None, "refused")
        self.assertEqual(watchdog.apply(s["id"], res, self.now, started), [])
        self.assertFalse(res.recorded)
        self.assertEqual(self.row()["fails_in_row"], 0)

    def test_check_now_while_a_scheduled_check_runs(self):
        s = self.add_restartable()
        self.port_.up()
        watchdog._running.add(s["id"])
        try:
            r = self.c.post(f"/api/admin/sites/{s['id']}/check-now", headers=ADMIN).json()
        finally:
            watchdog._running.discard(s["id"])
        self.assertTrue(r["ok"])
        self.assertFalse(r["recorded"])


if __name__ == "__main__":
    unittest.main()
