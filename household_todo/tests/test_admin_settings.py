"""Admin area: App settings, admin-only guards, notify services from Home
Assistant and per-user notify assignments. Old Configuration-tab values in
options.json are ignored, and there is no household-wide reminder_time —
each person has their own digest time."""
import _env  # noqa: F401  (must be first)

import asyncio
import json
import os
import re
import unittest
from datetime import datetime, timezone
from unittest import mock

from app import config, db, drive_time, geocode, ha_sensors, housekeeping, reminders, settings
from app.common import ha_notify
from app.routers import users as users_router
from common_tests.ingress import ingress_client

from app.main import app
from test_api import ADMIN, ALICE, BOB, ApiBase

UTC = timezone.utc


class SettingsBase(ApiBase):
    def setUp(self):
        super().setUp()
        ha_notify._targets_cache = None
        ha_notify._entity_mode.clear()
        users_router._last_test.clear()
        self.ha.fail = False
        self.ha.notify_services = None
        self.ha.notify_entities = []
        self._home = (geocode.HOME_LATLON, geocode._home_for, geocode._home_failed_at)

    def tearDown(self):
        geocode.HOME_LATLON, geocode._home_for, geocode._home_failed_at = self._home
        config.SUPERVISOR_TOKEN = "test-token"
        super().tearDown()

    def put_settings(self, body, h=ADMIN):
        return self.put("/api/admin/settings", body, h)


# ---------------------------------------------------------------------------
# GET / PUT /api/admin/settings
# ---------------------------------------------------------------------------

class TestSettingsApi(SettingsBase):
    def test_admin_only(self):
        self.assertEqual(self.get("/api/admin/settings", ALICE).status_code, 403)
        self.assertEqual(self.put_settings({"sensor_refresh_minutes": 9}, ALICE).status_code, 403)
        self.assertEqual(settings.get("sensor_refresh_minutes"), 5)   # nothing changed
        r = self.get("/api/admin/settings", ADMIN)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["values"], settings.DEFAULTS)
        self.assertEqual(body["defaults"], settings.DEFAULTS)
        self.assertEqual(set(body["meta"]), set(settings.KEYS))
        # meta describes each field for the shared page (common/static/settings.js); none needs a restart
        self.assertTrue(all(m["restartRequired"] is False and m["label"] for m in body["meta"].values()))
        self.assertTrue(body["meta"]["maintenance_profile"]["hidden"])     # edited on Admin → Maintenance
        self.assertEqual(set(body), {"values", "defaults", "meta", "groups", "maintenanceFiles"})
        self.assertNotIn("reminder_time", body["values"])   # each person has their own digest time
        self.assertNotIn("admin_users", body["values"])   # stays on the app's Configuration tab
        self.assertNotIn("notify_targets", body["values"])  # became per-user assignments

    def test_partial_update_keeps_other_keys(self):
        r = self.put_settings({"avoid_tolls": False})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIs(r.json()["values"]["avoid_tolls"], False)
        r = self.put_settings({"sensor_refresh_minutes": 15, "home_address": "  1 Main St  "})
        v = r.json()["values"]
        self.assertEqual((v["avoid_tolls"], v["sensor_refresh_minutes"], v["home_address"]), (False, 15, "1 Main St"))
        self.assertEqual(v["osrm_url"], settings.DEFAULT_OSRM_URL)
        self.assertEqual(self.get("/api/admin/settings", ADMIN).json()["values"], v)
        with db.get_conn() as c:
            row = c.execute("SELECT updated_by FROM app_settings WHERE key = 'avoid_tolls'").fetchone()
        self.assertEqual(row["updated_by"], "adminy")

    def test_bad_values_are_422_and_nothing_is_saved(self):
        bad = [
            {"sensor_refresh_minutes": 0}, {"sensor_refresh_minutes": 1441}, {"sensor_refresh_minutes": "5"},
            {"sensor_refresh_minutes": 5.0}, {"sensor_refresh_minutes": True}, {"sensor_refresh_minutes": float("inf")},
            {"sensor_refresh_minutes": float("nan")}, {"sensor_refresh_minutes": None},
            {"reminder_time": "09:00"},   # no longer a setting (each person has their own)
            {"expose_schedule_sensors": "yes"}, {"expose_schedule_sensors": 1}, {"avoid_tolls": None},
            {"osrm_url": "ftp://osrm.example"}, {"osrm_url": "osrm.example"}, {"nominatim_url": "javascript:alert(1)"},
            {"nominatim_url": "http://"}, {"home_address": "x" * 301}, {"home_address": 5},
            {"notify_targets": []}, {"admin_users": ["me"]}, {"nope": 1},
            # one good key with one bad one: neither is saved
            {"avoid_tolls": False, "sensor_refresh_minutes": 0},
        ]
        for body in bad:
            r = self.c.put("/api/admin/settings", content=json.dumps(body), headers={**ADMIN, "Content-Type": "application/json"})
            self.assertEqual(r.status_code, 422, body)
            self.assertIsInstance(r.json()["detail"], str, body)
        for body in ([1, 2], "x"):
            self.assertEqual(self.put_settings(body).status_code, 422, body)
        self.assertEqual(settings.all(), settings.DEFAULTS)
        with db.get_conn() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM app_settings WHERE key != 'drive_times_enabled'").fetchone()[0], 0)

    def test_readable_messages(self):
        d = self.put_settings({"sensor_refresh_minutes": 5000}).json()["detail"]
        self.assertIn("Sensor refresh", d)
        self.assertIn("1440", d)
        self.assertIn("reminder_time", self.put_settings({"reminder_time": "09:00"}).json()["detail"])
        self.assertIn("http", self.put_settings({"osrm_url": "ftp://x"}).json()["detail"])
        self.assertIn("nope", self.put_settings({"nope": 1}).json()["detail"])

    def test_blank_urls_mean_the_public_default(self):
        v = self.put_settings({"osrm_url": "", "nominatim_url": "https://geo.example/"}).json()["values"]
        self.assertEqual(v["osrm_url"], "")
        self.assertEqual(v["nominatim_url"], "https://geo.example")    # trailing / stripped
        self.assertEqual(settings.osrm_url(), settings.DEFAULT_OSRM_URL)
        self.assertEqual(settings.nominatim_url(), "https://geo.example")

    def test_cache_is_dropped_by_a_restore(self):
        self.put_settings({"sensor_refresh_minutes": 45})
        self.assertEqual(settings.get("sensor_refresh_minutes"), 45)
        with db.get_conn() as c:   # as if a different database file had been swapped in
            c.execute("DELETE FROM app_settings")
        db.init_db()
        self.assertEqual(settings.get("sensor_refresh_minutes"), 5)

    def test_a_read_racing_a_write_never_pins_old_values(self):
        # A background reader loads the rows, an admin saves before it stores
        # them: its (old) result must not be cached over the new value.
        settings.invalidate()
        # (the loader is the shared registry's, common/python/settings_core.py)
        real_load, fired = settings.REGISTRY.load, []

        def load_then_someone_saves(conn):
            values = real_load(conn)
            if not fired:
                fired.append(1)
                settings.update({"sensor_refresh_minutes": 60}, None)
            return values
        with mock.patch.object(settings.REGISTRY, "load", load_then_someone_saves):
            self.assertEqual(settings.all()["sensor_refresh_minutes"], 5)   # that reader's own snapshot
        self.assertEqual(settings.get("sensor_refresh_minutes"), 60)         # but nothing stale was cached

    def test_stored_invalid_value_falls_back_to_default(self):
        with db.get_conn() as c:
            c.execute("INSERT INTO app_settings (key, value, updated_at) VALUES ('sensor_refresh_minutes', '99999', 'x')")
        db.init_db()   # drop the cache
        with self.assertLogs("settings", level="WARNING"):
            self.assertEqual(settings.get("sensor_refresh_minutes"), 5)


# ---------------------------------------------------------------------------
# Every moved setting takes effect without a restart
# ---------------------------------------------------------------------------

class TestSettingsTakeEffect(SettingsBase):
    def _digest_setup(self):
        link_alice = self.post(f"/api/admin/users/{self.uid('Alice')}/notify", {"service": "notify.mobile_app_alice"}, ADMIN)
        self.assertEqual(link_alice.status_code, 201, link_alice.text)
        self.assertEqual(self.put("/api/prefs", {"notificationsEnabled": True}).status_code, 200)
        self.add("Pay rent", list_id=self.mine_id(), due_date="2026-09-21")

    def test_digest_time_is_per_person_only(self):
        # No household-wide reminder_time: 08:00 until the person picks their own.
        self._digest_setup()
        self.assertEqual(self.put_settings({"reminder_time": "09:30"}).status_code, 422)
        self.assertEqual(self.get("/api/prefs").json()["digestTime"], "08:00")
        sent = []
        fake = lambda svc, title, msg: sent.append(svc) or True   # noqa: E731
        self.put("/api/prefs", {"digestTime": "09:30"})
        config.utcnow = lambda: datetime(2026, 9, 21, 8, 5, tzinfo=UTC)
        self.assertEqual(reminders.run_digest_pass_blocking(sender=fake), 0)    # 08:05: not her time any more
        config.utcnow = lambda: datetime(2026, 9, 21, 9, 35, tzinfo=UTC)
        self.assertEqual(reminders.run_digest_pass_blocking(sender=fake), 1)    # 09:35: inside her window
        self.assertEqual(sent, ["mobile_app_alice"])

    def _make_item(self):
        r = self.post("/api/schedule", {"name": "Trash pickup", "rule": "weeks:1:1", "anchor_date": "2026-09-21"}, ADMIN)
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()

    def test_expose_off_removes_sensors_now_and_on_republishes(self):
        item = self._make_item()
        eid = item["entityId"]
        self.assertIn(eid, self.ha.states)
        r = self.put_settings({"expose_schedule_sensors": False})
        self.assertEqual(r.status_code, 200)
        self.assertNotIn(eid, self.ha.states)                       # removed straight away
        self.assertFalse(self.get("/api/schedule").json()[0]["published"])
        self.ha.requests.clear()
        self.post("/api/schedule", {"name": "Recycling", "rule": "weeks:1:2", "anchor_date": "2026-09-22"}, ADMIN)
        self.assertFalse([p for m, p, b, _ in self.ha.requests if m == "POST" and "/states/" in p])   # nothing published
        self.put_settings({"expose_schedule_sensors": True})
        self.assertIn(eid, self.ha.states)                          # re-published
        self.assertEqual(len([k for k in self.ha.states if k.startswith("binary_sensor.household_todo_")]), 2)

    def _run_sensor_loop(self, ticks, on_tick):
        """Drive ha_sensors.loop() for `ticks` ticks with a fake clock;
        on_tick(n) runs between ticks (after tick n)."""
        clock = {"t": 1000.0}

        class FakeTime:
            @staticmethod
            def monotonic():
                return clock["t"]

        count = {"n": 0}
        real_sleep = asyncio.sleep

        async def fake_sleep(_seconds):
            count["n"] += 1
            on_tick(count["n"], clock)
            if count["n"] >= ticks:
                raise asyncio.CancelledError
            await real_sleep(0)

        async def main():
            with mock.patch.object(ha_sensors, "time", FakeTime), mock.patch.object(ha_sensors.asyncio, "sleep", fake_sleep):
                try:
                    await ha_sensors.loop()
                except asyncio.CancelledError:
                    pass
        asyncio.run(main())

    def test_sensor_loop_rereads_refresh_minutes_and_expose_every_tick(self):
        item = self._make_item()
        eid = item["entityId"]
        pushes = []

        def count_pushes():
            return len([1 for m, p, b, _ in self.ha.requests if m == "POST" and p == f"/api/states/{eid}"])

        def on_tick(n, clock):
            pushes.append(count_pushes())
            if n == 1:
                clock["t"] += 120                               # 2 min later: under the default 5 min
            elif n == 2:
                settings.update({"sensor_refresh_minutes": 1}, None)   # now 2 min is over the interval
            elif n == 3:
                settings.update({"expose_schedule_sensors": False}, None)
            elif n == 4:
                settings.update({"expose_schedule_sensors": True}, None)

        self.ha.requests.clear()
        self._run_sensor_loop(5, on_tick)
        # tick1: first full sync; tick2: 2 min < 5 min -> no full sync; tick3: 1-min
        # interval -> full sync; tick4: exposure off -> deleted; tick5: back on -> re-pushed
        self.assertEqual(pushes[0], 1)
        self.assertEqual(pushes[1], 1)
        self.assertEqual(pushes[2], 2)
        deletes = [p for m, p, b, _ in self.ha.requests if m == "DELETE" and p == f"/api/states/{eid}"]
        self.assertEqual(len(deletes), 1)
        self.assertEqual(pushes[4], 3)
        self.assertIn(eid, self.ha.states)

    def test_sensor_loop_starting_with_exposure_off_cleans_up(self):
        item = self._make_item()
        settings.update({"expose_schedule_sensors": False}, None)
        self.ha.states[item["entityId"]] = {"state": "on", "attributes": {}}   # a stale one left in HA
        self._run_sensor_loop(2, lambda n, c: None)
        self.assertNotIn(item["entityId"], self.ha.states)

    def _place(self, pid="p1", address="1 Main St", minutes=12):
        with db.get_conn() as c:
            c.execute("INSERT INTO places (id, name, address, drive_minutes, drive_checked_at, drive_mode, created_at) "
                      "VALUES (?, 'Dentist', ?, ?, ?, 'no_tolls', ?)", (pid, address, minutes, config.now_iso(), config.now_iso()))

    def test_home_address_change_drops_cached_home_and_drive_times(self):
        self.put_settings({"drive_times_enabled": True})
        geocode.HOME_LATLON, geocode._home_for = (1.0, 1.0), ""
        self._place()
        asked = []

        def fake_geocode(addr):
            asked.append(addr)
            return (45.5, 8.0)
        with mock.patch.object(geocode, "geocode_blocking", fake_geocode):
            r = self.put_settings({"home_address": "5 New Rd, Springfield"})
        self.assertEqual(r.status_code, 200)
        with db.get_conn() as c:
            row = c.execute("SELECT drive_minutes, drive_checked_at FROM places WHERE id = 'p1'").fetchone()
        self.assertEqual((row["drive_minutes"], row["drive_checked_at"]), (None, None))   # re-queued
        self.assertEqual(asked, ["5 New Rd, Springfield"])                  # geocoded in the background
        self.assertEqual(geocode.HOME_LATLON, (45.5, 8.0))
        # the next drive-time pass routes from the NEW home
        origins = []
        drive_time.run_pass_blocking(geocode_fn=lambda a: (45.7, 7.9),
                                     route_fn=lambda url, o, d, avoid_tolls=False: origins.append(o) or (20, True))
        self.assertEqual(origins, [(45.5, 8.0)])
        # clearing the address turns the feature off at once
        self.put_settings({"home_address": ""})
        self.assertIsNone(geocode.HOME_LATLON)
        self.assertFalse(geocode.feature_enabled())

    def test_unchanged_home_address_keeps_cache(self):
        self.put_settings({"drive_times_enabled": True})
        with mock.patch.object(geocode, "geocode_blocking", lambda a: (45.5, 8.0)):
            self.put_settings({"home_address": "5 New Rd"})
        self._place()
        with mock.patch.object(geocode, "geocode_blocking", side_effect=AssertionError("no lookup expected")):
            self.put_settings({"home_address": "5 New Rd", "sensor_refresh_minutes": 9})
            self.assertEqual(geocode.ensure_home_blocking(), (45.5, 8.0))
        with db.get_conn() as c:
            self.assertEqual(c.execute("SELECT drive_minutes FROM places").fetchone()[0], 12)

    def test_osrm_url_and_avoid_tolls_used_on_next_request(self):
        self.put_settings({"drive_times_enabled": True})
        geocode.HOME_LATLON = (45.5, 8.0)
        self._place(minutes=None)
        seen = []
        route = lambda url, o, d, avoid_tolls=False: seen.append((url, avoid_tolls)) or (9, avoid_tolls)   # noqa: E731
        self.put_settings({"osrm_url": "https://osrm.home.lan/", "avoid_tolls": False})
        drive_time.run_pass_blocking(geocode_fn=lambda a: (45.7, 7.9), route_fn=route)
        self.assertEqual(seen, [("https://osrm.home.lan", False)])

    def test_nominatim_url_used_on_next_request(self):
        self.put_settings({"nominatim_url": "http://nominatim.home.lan:8080"})
        urls = []

        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b'[{"lat": "1.5", "lon": "2.5"}]'

        def fake_urlopen(req, timeout=10):
            urls.append(req.full_url)
            return Resp()
        with mock.patch("app.geocode.urllib.request.urlopen", fake_urlopen), mock.patch.object(geocode, "_throttle", lambda: None):
            self.assertEqual(geocode.geocode_blocking("1 Main St"), (1.5, 2.5))
        self.assertTrue(urls[0].startswith("http://nominatim.home.lan:8080/search?"))

    def test_url_change_requeues_failed_places(self):
        self._place("ok", "1 Good St", minutes=10)
        self._place("bad", "2 Bad St", minutes=None)
        self.put_settings({"osrm_url": "https://osrm.home.lan"})
        with db.get_conn() as c:
            rows = {r["id"]: r["drive_checked_at"] for r in c.execute("SELECT id, drive_checked_at FROM places")}
        self.assertIsNotNone(rows["ok"])
        self.assertIsNone(rows["bad"])


# ---------------------------------------------------------------------------
# The app's configuration: admin_users only; old keys are ignored
# ---------------------------------------------------------------------------

def _top_level_keys(yaml_text: str, section: str) -> list[str]:
    """Keys directly under `section:` in config.yaml (2-space indent) — no
    YAML library in the pinned deps."""
    keys, inside = [], False
    for line in yaml_text.splitlines():
        if line.startswith(section + ":"):
            inside = True
            continue
        if inside:
            if line and not line.startswith(" ") and not line.startswith("#"):
                break
            if line.startswith("  ") and not line.startswith("   ") and ":" in line and not line.strip().startswith(("#", "-")):
                keys.append(line.strip().split(":")[0])
    return keys


class TestConfiguration(SettingsBase):
    ROOT = os.path.join(os.path.dirname(__file__), "..")

    def test_config_yaml_has_only_admin_users(self):
        with open(os.path.join(self.ROOT, "config.yaml"), encoding="utf-8") as f:
            text = f.read()
        self.assertEqual(_top_level_keys(text, "options"), ["admin_users"])
        self.assertEqual(_top_level_keys(text, "schema"), ["admin_users"])
        # the page's asset version (?v=) follows the app version
        version = re.search(r'^version: "([^"]+)"', text, re.M).group(1)
        with open(os.path.join(self.ROOT, "app", "static", "index.html"), encoding="utf-8") as f:
            self.assertEqual(set(re.findall(r"\?v=([0-9.]+)", f.read())), {version})
        with open(os.path.join(self.ROOT, "translations", "en.yaml"), encoding="utf-8") as f:
            self.assertEqual(_top_level_keys(f.read(), "configuration"), ["admin_users"])

    def test_old_options_json_keys_are_ignored(self):
        old = {"admin_users": [], "reminder_time": "07:15", "expose_schedule_sensors": False,
               "sensor_refresh_minutes": 10, "home_address": "1 Main St", "osrm_url": "https://osrm.home.lan",
               "avoid_tolls": False, "notify_targets": [{"user": "alice", "service": "mobile_app_alice"}]}
        path = os.environ["OPTIONS_PATH"]
        with open(path, "w") as f:
            json.dump(old, f)
        try:
            self.assertEqual(config.read_options_file(), old)       # still readable…
            with ingress_client(app):      # …but a start-up uses none of it
                pass
        finally:
            os.remove(path)
        self.assertEqual(settings.all(), settings.DEFAULTS)
        with db.get_conn() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM app_settings WHERE key != 'drive_times_enabled'").fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM user_notify").fetchone()[0], 0)
        self.assertFalse(hasattr(settings, "import_from_options"))


# ---------------------------------------------------------------------------
# Background work never outlives what started it (the suite's old flakiness)
# ---------------------------------------------------------------------------

class TestBackgroundWork(SettingsBase):
    def _fake_loops(self):
        """Replace the four loop coroutines with fakes that record start and
        cancellation."""
        events = []

        def make(name):
            async def fake_loop():
                events.append(("start", name))
                try:
                    await asyncio.sleep(3600)
                except asyncio.CancelledError:
                    events.append(("cancelled", name))
                    raise
            return fake_loop
        patches = [mock.patch.object(mod, "loop", make(name)) for mod, name in
                   ((reminders, "reminders"), (ha_sensors, "ha_sensors"), (housekeeping, "housekeeping"),
                    (drive_time, "drive_time"))]
        return events, patches

    def test_no_loops_under_tests(self):
        self.assertFalse(config.BACKGROUND_LOOPS)            # tests/_env.py sets BACKGROUND_LOOPS=0
        events, patches = self._fake_loops()
        for p in patches:
            p.start()
        try:
            with ingress_client(app):
                pass
        finally:
            for p in patches:
                p.stop()
        self.assertEqual(events, [])

    def test_loops_start_and_are_cancelled_on_shutdown(self):
        events, patches = self._fake_loops()
        for p in patches:
            p.start()
        try:
            with mock.patch.object(config, "BACKGROUND_LOOPS", True):
                with ingress_client(app) as c:
                    self.assertEqual(c.get("/api/health").status_code, 200)
        finally:
            for p in patches:
                p.stop()
        names = {"reminders", "ha_sensors", "housekeeping", "drive_time"}
        self.assertEqual({n for e, n in events if e == "start"}, names)
        self.assertEqual({n for e, n in events if e == "cancelled"}, names)   # nothing left running

    def test_restore_follow_up_work_is_done_when_the_request_returns(self):
        item = self.post("/api/schedule", {"name": "Trash pickup", "rule": "weeks:1:1", "anchor_date": "2026-09-21"}, ADMIN).json()
        snapshot = self.c.get("/api/admin-storage-download-db", headers=ADMIN).content
        self.ha.states.clear()
        self.ha.requests.clear()
        r = self.c.post("/api/admin-storage-import-db", headers=ADMIN, files={"file": ("x.db", snapshot)})
        self.assertEqual(r.status_code, 200, r.text)
        # the sensor re-sync ran as part of the request, not in an untracked thread
        self.assertIn(item["entityId"], self.ha.states)


# ---------------------------------------------------------------------------
# Admin-only guards
# ---------------------------------------------------------------------------

class TestAdminGuards(SettingsBase):
    def test_every_admin_route_is_403_for_others(self):
        bob = self.uid("Bob")
        calls = [
            ("GET", "/api/admin/settings", None), ("PUT", "/api/admin/settings", {"sensor_refresh_minutes": 9}),
            ("GET", "/api/admin/users", None), ("GET", "/api/admin/notify-services", None),
            ("PUT", f"/api/admin/users/{bob}/notify", {"services": []}),
            ("POST", f"/api/admin/users/{bob}/notify", {"service": "notify.x"}),
            ("DELETE", f"/api/admin/users/{bob}/notify/notify.x", None),
            ("POST", f"/api/admin/users/{bob}/notify/test", {}),
            ("PATCH", f"/api/users/{bob}", {"disabled": True}),
            ("GET", "/api/admin-storage-download-db", None),
            ("POST", "/api/schedule/sync", {}),
        ]
        for method, path, body in calls:
            r = self.c.request(method, path, json=body, headers=ALICE)
            self.assertEqual(r.status_code, 403, (method, path, r.text))
        r = self.c.post("/api/admin-storage-import-db", files={"file": ("x.db", b"junk")}, headers=ALICE)
        self.assertEqual(r.status_code, 403)

    def test_member_list_stays_open_for_pickers_but_minimal(self):
        r = self.get("/api/users", ALICE)
        self.assertEqual(r.status_code, 200)
        self.assertEqual([u["name"] for u in r.json()], ["Adminy", "Alice", "Bob"])
        self.assertEqual(set(r.json()[0]), {"id", "name", "disabled"})    # no login names, no notify services
        admin_view = self.get("/api/admin/users", ADMIN).json()["users"]
        self.assertEqual(set(admin_view[0]), {"id", "name", "username", "disabled", "createdAt", "notify", "ha"})


# ---------------------------------------------------------------------------
# Notify services from Home Assistant
# ---------------------------------------------------------------------------

class TestNotifyServices(SettingsBase):
    def services_calls(self):
        return len([1 for m, p, b, _ in self.ha.requests if m == "GET" and p == "/api/services"])

    def test_lists_sorted_notify_services_and_entities(self):
        self.ha.notify_services = ["mobile_app_zed", "mobile_app_ann", "persistent_notification", "send_message"]
        self.ha.notify_entities = ["kitchen_speaker", "mobile_app_ann"]
        r = self.get("/api/admin/notify-services", ADMIN)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json(), {
            "available": True, "error": None,
            "services": ["notify.mobile_app_ann", "notify.mobile_app_zed", "notify.persistent_notification"],
            "entities": ["notify.kitchen_speaker"],
        })
        auth = [a for m, p, b, a in self.ha.requests if p == "/api/services"]
        self.assertEqual(auth, ["Bearer test-token"])      # the app's Supervisor token

    def test_short_cache_and_refresh(self):
        self.ha.notify_services = ["mobile_app_ann"]
        self.get("/api/admin/notify-services", ADMIN)
        self.ha.notify_services = ["mobile_app_ann", "mobile_app_new"]
        self.assertEqual(self.get("/api/admin/notify-services", ADMIN).json()["services"], ["notify.mobile_app_ann"])
        self.assertEqual(self.services_calls(), 1)
        r = self.get("/api/admin/notify-services?refresh=1", ADMIN).json()
        self.assertEqual(r["services"], ["notify.mobile_app_ann", "notify.mobile_app_new"])
        self.assertEqual(self.services_calls(), 2)
        # expired after 60 s
        ha_notify._targets_cache = (ha_notify._targets_cache[0] - 61, ha_notify._targets_cache[1])
        self.get("/api/admin/notify-services", ADMIN)
        self.assertEqual(self.services_calls(), 3)

    def test_clear_error_when_ha_unreachable(self):
        def unavailable(path):
            r = self.get(path, ADMIN)
            self.assertEqual(r.status_code, 200)      # the page degrades to typing a name
            body = r.json()
            self.assertEqual((body["available"], body["services"], body["entities"]), (False, [], []))
            self.assertIn("Home Assistant", body["error"])
            return body["error"]
        self.ha.fail = True
        self.assertIn("HTTP 500", unavailable("/api/admin/notify-services"))
        self.assertIsNone(ha_notify._targets_cache)          # a failure isn't cached
        config.SUPERVISOR_TOKEN = ""
        self.assertIn("Supervisor token", unavailable("/api/admin/notify-services?refresh=1"))
        config.SUPERVISOR_TOKEN = "test-token"
        saved = config.SUPERVISOR_CORE_API
        config.SUPERVISOR_CORE_API = "http://127.0.0.1:9/api"      # nothing listens there
        try:
            self.assertIn("didn't answer", unavailable("/api/admin/notify-services?refresh=1"))
        finally:
            config.SUPERVISOR_CORE_API = saved


# ---------------------------------------------------------------------------
# Per-user assignments, reminders and the admin test-send
# ---------------------------------------------------------------------------

class TestAssignments(SettingsBase):
    def notify_url(self, name="Alice"):
        return f"/api/admin/users/{self.uid(name)}/notify"

    def test_crud(self):
        url = self.notify_url()
        r = self.post(url, {"service": "notify.mobile_app_alice"}, ADMIN)
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(r.json()["notify"], ["notify.mobile_app_alice"])
        self.assertEqual(self.post(url, {"service": "notify.mobile_app_alice"}, ADMIN).json()["notify"],
                         ["notify.mobile_app_alice"])                          # adding again is harmless
        r = self.post(url, {"service": "alice_tablet"}, ADMIN)                  # bare name gets the prefix
        self.assertEqual(r.json()["notify"], ["notify.alice_tablet", "notify.mobile_app_alice"])
        r = self.delete(f"{url}/notify.alice_tablet", ADMIN)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["notify"], ["notify.mobile_app_alice"])
        self.assertEqual(self.delete(f"{url}/notify.alice_tablet", ADMIN).status_code, 404)
        r = self.put(url, {"services": ["notify.a", "notify.b", "notify.a"]}, ADMIN)
        self.assertEqual(r.json()["notify"], ["notify.a", "notify.b"])
        self.assertEqual(self.put(url, {"services": []}, ADMIN).json()["notify"], [])
        self.assertEqual(self.post("/api/admin/users/nope/notify", {"service": "notify.x"}, ADMIN).status_code, 404)
        with db.get_conn() as c:
            row = c.execute("SELECT created_by FROM user_notify").fetchone()
        self.assertIsNone(row)

    def test_service_names_are_validated(self):
        url = self.notify_url()
        for bad in ("light.kitchen", "notify.Bad", "notify.a-b", "notify.", "notify.x/../y", "notify.a b", "", 5, None,
                    "notify.x?y=1"):
            self.assertEqual(self.post(url, {"service": bad}, ADMIN).status_code, 422, bad)
        self.assertEqual(self.put(url, {"services": ["notify.ok", "no pe"]}, ADMIN).status_code, 422)
        self.assertEqual(self.put(url, {"services": "notify.ok"}, ADMIN).status_code, 422)
        self.assertEqual(self.put(url, {"services": [f"notify.s{i}" for i in range(11)]}, ADMIN).status_code, 422)
        self.assertEqual(self.get("/api/admin/users", ADMIN).json()["users"][1]["notify"], [])

    def test_assignment_enables_reminders_without_restart(self):
        self.assertFalse(self.get("/api/prefs").json()["canNotify"])
        self.post(self.notify_url(), {"service": "notify.mobile_app_alice"}, ADMIN)
        self.assertTrue(self.get("/api/prefs").json()["canNotify"])
        self.assertTrue(self.get("/api/whoami").json()["notifyLinked"])

    def test_reminders_go_to_every_assigned_service(self):
        self.ha.notify_services = ["mobile_app_alice", "alice_watch"]
        self.put(self.notify_url(), {"services": ["notify.mobile_app_alice", "notify.alice_watch"]}, ADMIN)
        self.assertEqual(self.put("/api/prefs", {"notificationsEnabled": True}).status_code, 200)
        self.add("Pay rent", list_id=self.mine_id(), due_date="2026-09-21")
        config.utcnow = lambda: datetime(2026, 9, 21, 8, 5, tzinfo=UTC)
        self.ha.requests.clear()
        self.assertEqual(reminders.run_digest_pass_blocking(), 1)
        self.assertEqual(sorted(s for s, b in self.ha.notifications()), ["alice_watch", "mobile_app_alice"])
        self.assertIn("Pay rent", self.ha.notifications()[0][1]["message"])
        self.assertEqual(reminders.run_digest_pass_blocking(), 0)    # logged once, not repeated

    def test_one_failing_service_still_counts_as_sent(self):
        self.ha.notify_services = ["mobile_app_alice"]            # alice_watch doesn't exist in HA
        self.put(self.notify_url(), {"services": ["notify.mobile_app_alice", "notify.alice_watch"]}, ADMIN)
        self.put("/api/prefs", {"notificationsEnabled": True})
        self.add("Pay rent", list_id=self.mine_id(), due_date="2026-09-21")
        config.utcnow = lambda: datetime(2026, 9, 21, 8, 5, tzinfo=UTC)
        with self.assertLogs("ha_notify", level="WARNING"):
            self.assertEqual(reminders.run_digest_pass_blocking(), 1)
        self.assertEqual(reminders.run_digest_pass_blocking(), 0)

    def test_assignment_ping_uses_the_table(self):
        self.post(self.notify_url("Bob"), {"service": "notify.mobile_app_bob"}, ADMIN)
        self.put("/api/prefs", {"notificationsEnabled": True}, BOB)
        self.add("Mow the lawn", assigned_to=self.uid("Bob"))
        self.assertEqual([s for s, b in self.ha.notifications()], ["mobile_app_bob"])

    def test_admin_test_send(self):
        url = self.notify_url()
        r = self.post(f"{url}/test", {}, ADMIN)
        self.assertEqual(r.status_code, 400)
        self.assertIn("no extra notify service", r.json()["detail"])
        self.put(url, {"services": ["notify.mobile_app_alice", "notify.alice_watch"]}, ADMIN)
        self.ha.requests.clear()
        r = self.post(f"{url}/test", {}, ADMIN)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["results"], {"notify.alice_watch": True, "notify.mobile_app_alice": True})
        sent = self.ha.notifications()
        self.assertEqual(sorted(s for s, b in sent), ["alice_watch", "mobile_app_alice"])
        self.assertIn("Test notification", sent[0][1]["message"])
        self.assertEqual(self.post(f"{url}/test", {}, ADMIN).status_code, 429)
        users_router._last_test.clear()
        # just one of them
        self.ha.requests.clear()
        r = self.post(f"{url}/test", {"service": "notify.alice_watch"}, ADMIN)
        self.assertEqual(r.json()["results"], {"notify.alice_watch": True})
        self.assertEqual([s for s, b in self.ha.notifications()], ["alice_watch"])
        users_router._last_test.clear()
        self.assertEqual(self.post(f"{url}/test", {"service": "notify.someone_else"}, ADMIN).status_code, 422)
        # Home Assistant refuses: 502 with its explanation
        self.ha.notify_services = ["mobile_app_alice_phone"]
        r = self.post(f"{url}/test", {"service": "notify.alice_watch"}, ADMIN)
        self.assertEqual(r.status_code, 502)
        self.assertIn("didn't accept", r.json()["detail"])

    def test_admin_test_send_works_before_the_person_turns_reminders_on(self):
        self.post(self.notify_url(), {"service": "notify.mobile_app_alice"}, ADMIN)
        self.assertFalse(self.get("/api/prefs").json()["notificationsEnabled"])
        self.assertEqual(self.post(f"{self.notify_url()}/test", {}, ADMIN).status_code, 200)

    def test_startup_check_reads_the_table(self):
        self.ha.notify_services = ["mobile_app_alice"]
        self.put(self.notify_url(), {"services": ["notify.mobile_app_alice", "notify.alice_typo"]}, ADMIN)
        with self.assertLogs("ha_notify", level="WARNING") as logs:
            self.assertEqual(ha_notify.check_targets_blocking(), 1)
        self.assertIn("'Alice'", "\n".join(logs.output))

    def test_whoami_counts_people_not_names(self):
        self.post(self.notify_url(), {"service": "notify.a"}, ADMIN)
        self.post(self.notify_url(), {"service": "notify.b"}, ADMIN)
        r = self.get("/api/whoami", BOB).json()
        self.assertEqual(r["notifyEntries"], 1)
        self.assertNotIn("notify.a", json.dumps(r))


if __name__ == "__main__":
    unittest.main()
