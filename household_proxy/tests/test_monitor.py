"""Monitoring (spec/SPEC.md §13.1, §13.2, §13.4–§13.6, §13.9) with a fake clock and a fake Home Assistant: down only
after N failures, up after one good check, incidents, alerts to exactly the chosen people (and not to someone who
muted the site), the binary sensor, pausing, monitor-only sites, each check kind."""
import _env  # noqa: F401  (must be first)

import asyncio
import socket
import unittest
from unittest import mock

from app import db, sites, watchdog
from app.common import ha_notify, ha_people
from helpers import ADMIN, BOB, CAROL, ProxyCase


class Port:
    """A TCP port on 127.0.0.1 that can be opened (the "device" is up) and closed (down)."""

    def __init__(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        self.port = s.getsockname()[1]
        s.close()
        self.sock = None

    def up(self):
        if self.sock is None:
            self.sock = socket.socket()
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.sock.bind(("127.0.0.1", self.port))
            self.sock.listen(16)

    def down(self):
        if self.sock is not None:
            self.sock.close()
            self.sock = None


class MonitorCase(ProxyCase):
    def setUp(self):
        super().setUp()
        self.now = 1_800_000_000.0
        self.sent = []
        self.posted = {}
        patches = [
            mock.patch.object(watchdog, "clock", lambda: self.now),
            mock.patch.object(watchdog, "INLINE", True),               # a round waits for its checks
            mock.patch.object(watchdog, "send", lambda svc, title, text, data: self.sent.append((svc, title, text, data)) or True),
            mock.patch.object(ha_people, "phones_for", lambda uid: {"u-admin": ["mobile_app_ann"], "u-bob": ["mobile_app_bob"],
                                                                     "u-carol": ["mobile_app_carol"]}.get(uid, [])),
            mock.patch.object(watchdog, "SENSORS", watchdog.sensor_publisher.Publisher(
                post=lambda e, st, a: self.posted.__setitem__(e, (st, a)) or True,
                delete=lambda e: self.posted.pop(e, None) is not None or True)),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        watchdog._refresh["at"] = 0.0
        self.port_ = Port()
        self.addCleanup(self.port_.down)

    def add_checked(self, kind="tcp", **kw):
        body = dict(name="Box", slug="box", use="monitor", target=f"127.0.0.1:{self.port_.port}",
                    check={"kind": kind, "every_s": 60, "timeout_s": 2, "down_after": 3})
        body.update(kw)
        r = self.c.post("/api/admin/sites", json=body, headers=ADMIN)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def tick(self, seconds=61):
        self.now += seconds
        asyncio.run(watchdog.tick())

    def row(self, slug="box"):
        with db.get_conn() as conn:
            return conn.execute("SELECT * FROM sites WHERE slug = ?", (slug,)).fetchone()


class States(MonitorCase):
    def test_down_after_n_failures_then_up_after_one(self):
        self.add_checked()
        self.tick()                                   # schedules the first check within its interval
        self.port_.down()
        for _ in range(2):
            self.tick()
            self.assertIsNone(self.row()["state"])     # one or two lost answers change nothing
        self.tick()
        r = self.row()
        self.assertEqual((r["state"], r["fails_in_row"]), ("down", 3))
        self.assertEqual([s[1] for s in self.sent], ["Box is down"])
        self.assertEqual(self.sent[0][0], "mobile_app_ann")             # every admin, by default
        self.tick()
        self.assertEqual(len(self.sent), 1)                              # nothing repeated while it stays down
        self.port_.up()
        self.tick()
        r = self.row()
        self.assertEqual(r["state"], "up")
        self.assertEqual(self.sent[-1][1], "Box is back up")
        self.assertIn("down for", self.sent[-1][2])
        with db.get_conn() as conn:
            inc = conn.execute("SELECT * FROM incidents").fetchone()
        self.assertEqual((inc["outcome"], inc["cause"]), ("up", "refused"))

    def test_up_straight_away(self):
        self.port_.up()
        self.add_checked()
        self.tick()
        self.tick()
        r = self.row()
        self.assertEqual(r["state"], "up")
        self.assertIsNotNone(r["last_ms"])
        self.assertEqual(self.sent, [])                                  # no "back up" without an incident

    def test_pause_and_resume(self):
        s = self.add_checked()
        self.tick()
        self.c.post(f"/api/admin/sites/{s['id']}/pause", json={"hours": 1}, headers=ADMIN)
        for _ in range(5):
            self.tick()
        with db.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM checks").fetchone()[0], 0)
        self.assertEqual(self.c.get("/api/monitor", headers=ADMIN).json()["sites"][0]["state"], "paused")
        self.tick(3600)
        self.tick()
        with db.get_conn() as conn:
            self.assertGreater(conn.execute("SELECT COUNT(*) FROM checks").fetchone()[0], 0)
        self.c.post(f"/api/admin/sites/{s['id']}/pause", json={}, headers=ADMIN)    # until resumed
        self.assertIsNone(self.c.get("/api/monitor", headers=ADMIN).json()["sites"][0]["pausedUntil"])
        self.c.post(f"/api/admin/sites/{s['id']}/resume", headers=ADMIN)
        self.assertNotEqual(self.c.get("/api/monitor", headers=ADMIN).json()["sites"][0]["state"], "paused")

    def test_turning_checks_off_forgets_the_state(self):
        s = self.add_checked(use="proxy", target=f"http://127.0.0.1:{self.port}", trusted_ok=True,
                             check={"kind": "http"})
        self.tick()
        self.tick()
        self.assertEqual(self.row()["state"], "up")
        body = {"name": "Box", "slug": "box", "target": f"http://127.0.0.1:{self.port}", "trusted_ok": True}
        self.c.put(f"/api/admin/sites/{s['id']}", json=body, headers=ADMIN)
        self.assertIsNone(self.row()["state"])


class Kinds(MonitorCase):
    def test_web_page_statuses(self):
        self.add_checked(use="proxy", target=f"http://127.0.0.1:{self.port}", trusted_ok=True,
                         check={"kind": "http", "path": "/status/404", "down_after": 1})
        self.tick()
        self.tick()
        self.assertEqual(self.row()["state"], "up")                      # below 500 counts as up
        s = sites.get("box")
        body = {"name": "Box", "slug": "box", "target": f"http://127.0.0.1:{self.port}", "trusted_ok": True,
                "check": {"kind": "http", "path": "/status/404", "down_after": 1, "ok_codes": "2xx3xx"}}
        self.c.put(f"/api/admin/sites/{s.id}", json=body, headers=ADMIN)
        self.tick()
        self.tick()
        self.assertEqual((self.row()["state"], self.row()["last_error"]), ("down", "status 404"))

    def test_ping_refused_by_the_container_says_so(self):
        self.add_checked(kind="ping", target="127.0.0.1", check={"kind": "ping", "down_after": 1})
        with mock.patch.object(watchdog, "_icmp_socket", side_effect=PermissionError()):
            self.tick()
            self.tick()
        self.assertIn("use a Port check", self.row()["last_error"])

    def test_ping_reply(self):
        self.add_checked(kind="ping", target="127.0.0.1", check={"kind": "ping"})
        a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)

        class FakeIcmp:
            def setblocking(self, flag):
                a.setblocking(flag)

            def fileno(self):
                return a.fileno()

            def close(self):
                a.close()
                b.close()

        async def sendto(sock, data, addr):
            b.send(bytes([0]) + data[1:])                   # the "device" answers with an echo reply

        async def recv(sock, n):
            return a.recv(n)

        with mock.patch.object(watchdog, "_icmp_socket", lambda family: FakeIcmp()):
            loop = asyncio.new_event_loop()
            try:
                with mock.patch.object(type(loop), "sock_sendto", lambda self, s, d, addr: sendto(s, d, addr)), \
                        mock.patch.object(type(loop), "sock_recv", lambda self, s, n: recv(s, n)):
                    site = sites.get("box")
                    res = loop.run_until_complete(watchdog.check_ping(site, {"check_timeout_s": 2}))
            finally:
                loop.close()
        self.assertTrue(res.ok, res)

    def test_echo_request_checksum(self):
        pkt = watchdog._echo_request(socket.AF_INET, 1)
        self.assertEqual(watchdog._checksum(pkt), 0)


class Alerts(MonitorCase):
    def go_down(self):
        self.tick()
        for _ in range(3):
            self.tick()

    def test_chosen_people_extra_service_events_and_mutes(self):
        self.add_checked(alerts={"people": ["u-bob", "u-carol"], "service": "notify.speaker", "events": ["down"]})
        self.c.post("/api/monitor/box/mute", json={"muted": True}, headers=CAROL)       # Carol can't see it: 404
        self.go_down()
        self.assertEqual(sorted(s[0] for s in self.sent), ["mobile_app_bob", "mobile_app_carol", "speaker"])
        self.port_.up()
        self.tick()
        self.assertEqual(len(self.sent), 3)                                  # "back up" isn't chosen

    def test_extra_services_from_admin_people_reach_alerts(self):
        self.c.get("/api/me", headers=BOB)                                   # Bob is known once he has opened the app
        r = self.c.post("/api/admin/people/u-bob/notify", json={"service": "kitchen_speaker"}, headers=ADMIN)
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(r.json()["notify"], ["notify.kitchen_speaker"])
        self.add_checked(alerts={"people": ["u-bob"], "events": ["down"]})
        self.go_down()
        self.assertEqual(sorted(s[0] for s in self.sent), ["kitchen_speaker", "mobile_app_bob"])

    def test_send_a_test_alert_goes_where_a_real_one_would(self):
        site = self.add_checked(alerts={"people": ["u-bob", "u-carol"], "service": "notify.speaker", "events": ["down"]})
        sid = site["site"]["id"] if "site" in site else site["id"]
        self.c.get("/api/me", headers=CAROL)
        self.c.post("/api/monitor/box/mute", json={"muted": True}, headers=CAROL)
        with db.get_conn() as conn:                                          # Carol muted it (as the Monitor page would)
            conn.execute("INSERT OR IGNORE INTO alert_mutes (site_id, user_id) VALUES (?, 'u-carol')", (sid,))
        calls = []
        with mock.patch.object(ha_notify, "send_notify",
                               lambda svc, title, text, data=None: calls.append((svc, title)) or svc != "speaker"):
            r = self.c.post(f"/api/admin/sites/{sid}/alert-test", headers=ADMIN)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(sorted(c[0] for c in calls), ["mobile_app_bob", "speaker"])
        self.assertEqual(calls[0][1], "Box: test alert")
        res = {x["service"]: x["ok"] for x in r.json()["results"]}
        self.assertEqual(res, {"notify.mobile_app_bob": True, "notify.speaker": False})
        self.assertEqual(self.c.post(f"/api/admin/sites/{sid}/alert-test", headers=BOB).status_code, 403)
        self.assertEqual(self.c.post("/api/admin/sites/9999/alert-test", headers=ADMIN).status_code, 404)

    def test_send_a_test_alert_with_nobody_to_tell(self):
        site = self.add_checked(alerts={"people": ["u-nobody"], "events": ["down"]})
        sid = site["site"]["id"] if "site" in site else site["id"]
        self.assertEqual(self.c.post(f"/api/admin/sites/{sid}/alert-test", headers=ADMIN).status_code, 409)

    def test_alerted_person_gets_no_access(self):
        self.add_checked(alerts={"people": ["u-bob"]})
        self.go_down()
        link = self.sent[0][3]
        self.assertNotIn("url", link)                                        # no sidebar page outside Home Assistant
        self.assertEqual(self.c.get("/api/monitor", headers=BOB).json()["sites"], [])
        self.assertEqual(self.c.post("/api/monitor/box/mute", json={"muted": True}, headers=BOB).status_code, 404)

    def test_mute_for_yourself_only(self):
        self.add_checked(access="everyone", alerts={"people": ["u-admin", "u-bob"]})
        self.assertEqual(self.c.post("/api/monitor/box/mute", json={"muted": True}, headers=BOB).status_code, 200)
        self.go_down()
        self.assertEqual([s[0] for s in self.sent], ["mobile_app_ann"])
        mon = self.c.get("/api/monitor", headers=BOB).json()["sites"][0]
        self.assertTrue(mon["muted"])
        self.assertFalse(self.c.get("/api/monitor", headers=ADMIN).json()["sites"][0]["muted"])

    def test_deep_link_in_alerts(self):
        from app import sidebar
        with mock.patch.object(sidebar, "PAGE", "/local_household_proxy"):
            self.add_checked()
            self.go_down()
        self.assertEqual(self.sent[0][3]["url"], "/local_household_proxy/monitor/box")
        r = self.c.get("/monitor/box", headers=ADMIN, follow_redirects=False)
        self.assertEqual(r.headers["location"], "../../#/monitor/box")


class MonitorOnly(MonitorCase):
    def test_never_proxied_and_not_in_the_launcher(self):
        self.add_checked(access="everyone")
        self.assertEqual(self.c.get("/s/box/", headers=ADMIN).status_code, 404)
        self.assertEqual(self.c.get("/api/sites", headers=BOB).json()["sites"], [])
        self.assertEqual([s["slug"] for s in self.c.get("/api/monitor", headers=BOB).json()["sites"]], ["box"])
        self.assertTrue(self.c.get("/api/me", headers=BOB).json()["monitor"])

    def test_addresses(self):
        for target, kind, ok in (("nas.lan", "ping", True), ("nas.lan:22", "tcp", True), ("nas.lan", "tcp", False),
                                 ("nas.lan", "http", False), ("http://nas.lan", "http", True), ("nas.lan/x", "ping", False)):
            r = self.c.post("/api/admin/sites", headers=ADMIN, json={
                "name": "M", "slug": "m", "use": "monitor", "target": target, "check": {"kind": kind}})
            self.assertEqual(r.status_code == 200, ok, (target, kind, r.text))
            if r.status_code == 200:
                self.c.delete(f"/api/admin/sites/{r.json()['id']}", headers=ADMIN)
        r = self.c.post("/api/admin/sites", headers=ADMIN, json={"name": "M", "use": "monitor", "target": "nas.lan"})
        self.assertEqual(r.status_code, 422)                                 # monitor only needs a check

    def test_form_test_runs_one_check(self):
        self.port_.up()
        r = self.c.post("/api/admin/sites/test", headers=ADMIN, json={
            "name": "M", "use": "monitor", "target": f"127.0.0.1:{self.port_.port}", "check": {"kind": "tcp"}})
        self.assertEqual((r.json()["reachable"], r.json()["verdictText"]), (True, "Answers the check"))


class Sensors(MonitorCase):
    def test_binary_sensor(self):
        self.port_.up()
        s = self.add_checked()
        self.tick()
        self.tick()
        state, attrs = self.posted["binary_sensor.household_proxy_box"]
        self.assertEqual((state, attrs["device_class"], attrs["status"]), ("on", "connectivity", "up"))
        self.assertEqual(attrs["restarts"], 0)
        body = {"name": "Box", "slug": "box", "use": "monitor", "target": f"127.0.0.1:{self.port_.port}",
                "check": {"kind": "tcp", "sensor": False}}
        self.c.put(f"/api/admin/sites/{s['id']}", json=body, headers=ADMIN)
        self.tick()
        self.assertNotIn("binary_sensor.household_proxy_box", self.posted)


class History(MonitorCase):
    def test_strip_uptime_rollup_and_admin_routes(self):
        self.port_.up()
        s = self.add_checked()
        self.tick()
        for i in range(10):
            if i == 5:
                self.port_.down()
            self.tick()
        mon = self.c.get("/api/monitor", headers=ADMIN).json()["sites"][0]
        self.assertEqual(len(mon["strip"]), 48)
        self.assertIn("partial", mon["strip"] + ["partial"])
        h = self.c.get(f"/api/admin/sites/{s['id']}/history", headers=ADMIN).json()
        self.assertEqual(h["uptime"]["day"], 50.0)
        self.assertEqual(len(h["incidents"]), 1)
        self.now += 8 * 86400
        watchdog.housekeeping()
        with db.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM checks").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT SUM(total) FROM check_hours").fetchone()[0], 10)
        self.assertEqual(self.c.get(f"/api/admin/sites/{s['id']}/history", headers=ADMIN).json()["uptime"]["month"], 50.0)
        for path in (f"/api/admin/sites/{s['id']}/history",):
            self.assertEqual(self.c.get(path, headers=BOB).status_code, 403)
        for path in ("check-now", "pause", "resume"):
            self.assertEqual(self.c.post(f"/api/admin/sites/{s['id']}/{path}", json={}, headers=BOB).status_code, 403)

    def test_check_now(self):
        self.port_.up()
        s = self.add_checked()
        r = self.c.post(f"/api/admin/sites/{s['id']}/check-now", headers=ADMIN).json()
        self.assertTrue(r["ok"])
        with db.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM checks").fetchone()[0], 1)


def old_strip(conn, site_id, now, hours=24, buckets=48):
    """The Monitor page's 24-hour strip as 0.6.3 computed it (one row per check, parsed in Python)."""
    size = hours * 3600 / buckets
    start = now - hours * 3600
    counts = [[0, 0] for _ in range(buckets)]
    for r in conn.execute("SELECT at, ok FROM checks WHERE site_id = ? AND at >= ?", (site_id, watchdog.iso(start))):
        t = watchdog.epoch(r["at"])
        if t is None:
            continue
        i = min(buckets - 1, max(0, int((t - start) / size)))
        counts[i][0] += r["ok"]
        counts[i][1] += 1
    return ["" if t == 0 else "up" if ok == t else "down" if ok == 0 else "partial" for ok, t in counts]


class Faster(MonitorCase):
    def test_strip_counted_by_sqlite_matches_the_old_way(self):
        import random
        s = self.add_checked()
        rnd = random.Random(7)
        with db.get_conn() as conn:
            rows = [(s["id"], watchdog.iso(self.now - rnd.uniform(-60, 26 * 3600)), int(rnd.random() < 0.7))
                    for _ in range(3000)]
            rows += [(s["id"], watchdog.iso(self.now - 86400 + d), 0) for d in (-1, 0, 1, 1799, 1800, 1801)]
            conn.executemany("INSERT INTO checks (site_id, at, ok) VALUES (?, ?, ?)", rows)
            for frac in (0.0, 0.25, 0.5, 0.999):
                with mock.patch.object(watchdog, "clock", lambda f=frac: self.now + f):
                    self.assertEqual(watchdog.strip(conn, s["id"]), old_strip(conn, s["id"], self.now + frac), frac)
            self.assertIn("partial", watchdog.strip(conn, s["id"]))

    def test_strip_cache_dropped_when_a_check_is_recorded(self):
        s = self.add_checked()
        with db.get_conn() as conn:
            first = watchdog.strip_cached(conn, s["id"])
        self.port_.up()
        self.tick()
        self.tick()
        with db.get_conn() as conn:
            self.assertNotEqual(watchdog.strip_cached(conn, s["id"]), first)

    def test_monitor_page_never_reads_the_icons_bytes(self):
        s = self.add_checked()
        from app import sites as sites_mod
        sites_mod.set_favicon(s["id"], b"\x89PNG" + b"x" * 50000, "image/png")
        seen = []
        real = db._connect

        def spy():
            conn = real()
            conn.set_trace_callback(seen.append)
            return conn
        with mock.patch.object(db, "_connect", spy):
            self.assertEqual(self.get("/api/monitor").status_code, 200)
            self.get("/api/sites")
            sites_mod.invalidate()
            sites_mod.all_sites()
        self.assertTrue(seen)
        for q in seen:
            self.assertNotRegex(q, r"(?i)select\s+\*\s+from\s+sites\b")             # every column: the icon too

    def test_a_slow_check_doesnt_hold_up_the_round(self):
        self.add_checked()
        self.add_checked(slug="two", name="Two")
        gate = {}

        async def slow(site, row):
            await gate["ev"].wait()
            return watchdog.Result(True, 1)

        async def go():
            gate["ev"] = asyncio.Event()
            with mock.patch.object(watchdog, "INLINE", False), mock.patch.object(watchdog, "run_check", slow):
                self.now += 61
                await watchdog.tick()
                self.now += 61
                await asyncio.wait_for(watchdog.tick(), 2)          # the round ends; the checks still run
                running = set(watchdog._running)
                gate["ev"].set()
                await asyncio.gather(*list(watchdog._tasks))
                return running
        running = asyncio.run(go())
        self.assertEqual(len(running), 2)
        self.assertEqual(watchdog._running, set())
        self.assertEqual((self.row("box")["state"], self.row("two")["state"]), ("up", "up"))

    def test_nothing_to_do_without_checked_sites(self):
        with mock.patch.object(watchdog, "publish_sensors") as pub:
            self.tick()
        pub.assert_not_called()

    def test_paused_is_worked_out_when_asked(self):
        s = self.add_checked(use="proxy", target=f"http://127.0.0.1:{self.port_.port}", trusted_ok=True,
                             access="everyone")
        self.c.post(f"/api/admin/sites/{s['id']}/pause", json={"hours": 1}, headers=ADMIN)
        self.assertEqual(self.get("/api/sites").json()["sites"][0]["state"], "paused")
        self.now += 3601                                         # nothing saved since: the cache still holds it
        self.assertNotEqual(self.get("/api/sites").json()["sites"][0]["state"], "paused")


class PeopleWrites(MonitorCase):
    def test_a_known_person_is_written_only_when_their_name_changes(self):
        with db.get_conn() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS t_writes (n INTEGER)")
            conn.execute("DELETE FROM t_writes")
            conn.execute("CREATE TRIGGER IF NOT EXISTS t_users_w AFTER UPDATE ON users BEGIN "
                         "INSERT INTO t_writes VALUES (1); END")
        try:
            for _ in range(3):
                self.get("/api/me")
            with db.get_conn() as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM t_writes").fetchone()[0], 0)
            from common_tests.ingress import identity_headers
            from helpers import INGRESS
            self.get("/api/me", identity_headers("u-admin", "adminy", "Ann A. Admin", ingress_path=INGRESS))
            with db.get_conn() as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM t_writes").fetchone()[0], 1)
                self.assertEqual(conn.execute("SELECT name FROM users WHERE id = 'u-admin'").fetchone()[0],
                                 "Ann A. Admin")
        finally:
            with db.get_conn() as conn:
                conn.execute("DROP TRIGGER IF EXISTS t_users_w")
                conn.execute("DROP TABLE IF EXISTS t_writes")


if __name__ == "__main__":
    unittest.main()
