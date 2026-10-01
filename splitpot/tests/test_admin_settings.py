"""Admin area — App settings (sensor sync on/off, sync interval, currency;
not app options) and admin-only Users / Storage APIs. The people are
invented data.

Run with the rest: python3 -m unittest discover -s tests"""
import _env  # noqa: F401  (must be first)

import asyncio
import contextlib
import io
import json
import os
import sqlite3
import tempfile
import unittest
import urllib.error
from pathlib import Path

import main
from test_app import ADMIN, ALICE, Base, hdr

IMPOSTOR = hdr("u_eve", "adminy", "eve")   # admin's display name, but not listed
DEFAULTS = {"ha_sync_enabled": True, "sync_interval_minutes": 5, "currency": "USD"}


class FakeHA:
    """Stands in for Home Assistant's Core API (patches main._ha_call and
    sets a SUPERVISOR_TOKEN). Records every call, keeps the posted states,
    and fails the test if a call is made while a DB connection is open."""

    def __init__(self, test, states=None):
        self.test = test
        self.states = dict(states or {})
        self.calls = []
        self.fail_list = False
        self.open_conns = 0

    def __enter__(self):
        self._orig = (main._ha_call, main.SUPERVISOR_TOKEN, main.get_conn)
        orig_get_conn = main.get_conn

        @contextlib.contextmanager
        def counting_get_conn():
            self.open_conns += 1
            try:
                with orig_get_conn() as conn:
                    yield conn
            finally:
                self.open_conns -= 1

        main.get_conn = counting_get_conn
        main._ha_call = self.call
        main.SUPERVISOR_TOKEN = "test-token"
        return self

    def __exit__(self, *exc):
        main._ha_call, main.SUPERVISOR_TOKEN, main.get_conn = self._orig

    def call(self, method, path, body=None, timeout=5):
        self.test.assertEqual(self.open_conns, 0, f"{method} {path} while a DB connection was open")
        self.calls.append((method, path))
        if method == "GET" and path == "states":
            if self.fail_list:
                raise urllib.error.URLError("down")
            return 200, [{"entity_id": e, **v} for e, v in self.states.items()]
        entity = path.split("/", 1)[1]
        if method == "POST":
            self.states[entity] = body
            return 200, body
        if method == "DELETE":
            if entity not in self.states:
                raise urllib.error.HTTPError(path, 404, "Not Found", None, None)
            del self.states[entity]
            return 200, None
        raise AssertionError(method)

    def balance_sensors(self):
        return {e: v for e, v in self.states.items() if e.startswith(main.BALANCE_SENSOR_PREFIX)}

    def count(self, method):
        return len([c for c in self.calls if c[0] == method])


def settings(client, who=ADMIN):
    return client.get("/api/admin/settings", headers=who)


class SettingsApi(Base):
    def test_admin_only(self):
        self.assertEqual(settings(self.c).status_code, 200)
        for who in (ALICE, IMPOSTOR):
            self.assertEqual(settings(self.c, who).status_code, 403)
            r = self.c.put("/api/admin/settings", headers=who, json={"currency": "EUR"})
            self.assertEqual(r.status_code, 403)
        self.assertEqual(main.get_setting("currency"), "USD")

    def test_shape_and_defaults(self):
        body = settings(self.c).json()
        self.assertEqual(body["values"], DEFAULTS)
        self.assertEqual(body["defaults"], DEFAULTS)
        self.assertEqual(body["meta"], {k: {"restartRequired": False} for k in DEFAULTS})
        self.assertEqual(set(body), {"values", "defaults", "meta"})

    def test_partial_update_keeps_other_keys_and_normalises_currency(self):
        r = self.c.put("/api/admin/settings", headers=ADMIN, json={"sync_interval_minutes": 15})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["values"], {**DEFAULTS, "sync_interval_minutes": 15})
        r = self.c.put("/api/admin/settings", headers=ADMIN, json={"currency": " eur "})
        self.assertEqual(r.json()["values"], {**DEFAULTS, "sync_interval_minutes": 15, "currency": "EUR"})
        with main.get_conn() as conn:
            row = conn.execute("SELECT value, updated_by FROM app_settings WHERE key = 'currency'").fetchone()
        self.assertEqual((json.loads(row["value"]), row["updated_by"]), ("EUR", "Adminy"))
        ev = self.c.get("/api/events", headers=ALICE).json()[0]
        self.assertEqual(ev["type"], "settings_changed")
        self.assertIn("currency USD → EUR", ev["message"])

    def test_unchanged_values_are_not_rewritten(self):
        self.c.put("/api/admin/settings", headers=ADMIN, json=DEFAULTS)
        with main.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM app_settings WHERE key NOT LIKE '\\_%' ESCAPE '\\'").fetchone()[0], 0)
        self.assertNotEqual(self.c.get("/api/events", headers=ALICE).json()[0]["type"], "settings_changed")

    def test_bad_values_rejected_with_readable_message(self):
        bad = [
            {"sync_interval_minutes": 0}, {"sync_interval_minutes": 61}, {"sync_interval_minutes": -5},
            {"sync_interval_minutes": "10"}, {"sync_interval_minutes": 2.5}, {"sync_interval_minutes": True},
            {"sync_interval_minutes": None},
            {"ha_sync_enabled": "false"}, {"ha_sync_enabled": 0}, {"ha_sync_enabled": None},
            {"currency": "EURO"}, {"currency": "US"}, {"currency": "ABC"}, {"currency": 978}, {"currency": ""},
            {"currency": "E1R"}, {"foo": 1}, {"_imported": []}, {"currency": "EUR", "theme": "dark"},
        ]
        for body in bad:
            r = self.c.put("/api/admin/settings", headers=ADMIN, json=body)
            self.assertEqual(r.status_code, 422, body)
            self.assertIsInstance(r.json()["detail"], str)
        r = self.c.put("/api/admin/settings", headers=ADMIN, json={"sync_interval_minutes": 99})
        self.assertIn("Sync interval (minutes)", r.json()["detail"])
        r = self.c.put("/api/admin/settings", headers=ADMIN, json={"foo": 1})
        self.assertIn("Unknown setting: foo", r.json()["detail"])
        r = self.c.put("/api/admin/settings", headers=ADMIN, json=["currency"])
        self.assertEqual(r.status_code, 422)
        for raw in ("Infinity", "NaN"):
            r = self.c.put("/api/admin/settings", headers={**ADMIN, "Content-Type": "application/json"},
                           content=f'{{"sync_interval_minutes": {raw}}}')
            self.assertEqual(r.status_code, 422, raw)
        # a rejected update saves nothing, not even its valid part
        r = self.c.put("/api/admin/settings", headers=ADMIN, json={"currency": "EUR", "sync_interval_minutes": 0})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(settings(self.c).json()["values"], DEFAULTS)


class CurrencyUsedImmediately(Base):
    def test_config_activity_log_and_sensors_use_new_currency(self):
        self.assertEqual(self.c.get("/api/config", headers=ALICE).json(), {"currency": "USD"})
        self.expense(amount=30)
        r = self.c.put("/api/admin/settings", headers=ADMIN, json={"currency": "EUR"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.c.get("/api/config", headers=ALICE).json(), {"currency": "EUR"})
        self.expense(amount=12)
        msgs = [e["message"] for e in self.c.get("/api/events", headers=ALICE).json() if e["type"] == "expense_added"]
        self.assertIn("(€12.00)", msgs[0])
        self.assertIn("($30.00)", msgs[1])   # older entries keep the text they were written with
        with main.get_conn() as conn:
            states = main.balance_sensor_states(conn)
        self.assertEqual({s[2]["unit_of_measurement"] for s in states}, {"EUR"})
        self.assertIn("sensor.splitpot_balance_ann", [s[0] for s in states])

    def test_sensor_push_uses_new_currency(self):
        self.expense(amount=30)
        with FakeHA(self) as ha:
            main.push_balances_to_ha()
            self.assertEqual({v["attributes"]["unit_of_measurement"] for v in ha.balance_sensors().values()}, {"USD"})
            # changing the currency pushes right away with the new unit
            posts = ha.count("POST")
            self.c.put("/api/admin/settings", headers=ADMIN, json={"currency": "GBP"})
            self.assertGreater(ha.count("POST"), posts)
            self.assertEqual({v["attributes"]["unit_of_measurement"] for v in ha.balance_sensors().values()}, {"GBP"})
            posts = ha.count("POST")
            self.c.put("/api/admin/settings", headers=ADMIN, json={"sync_interval_minutes": 7})
            self.assertEqual(ha.count("POST"), posts)   # interval-only change doesn't need a push


class SensorSyncSwitch(Base):
    """ha_sync_enabled is an App setting, applied live."""

    def test_admin_only(self):
        for who in (ALICE, IMPOSTOR):
            r = self.c.put("/api/admin/settings", headers=who, json={"ha_sync_enabled": False})
            self.assertEqual(r.status_code, 403)
        self.assertTrue(main.get_setting("ha_sync_enabled"))

    def test_off_removes_sensors_and_stops_pushing_on_pushes_at_once(self):
        with FakeHA(self, {"sensor.splitpot_balance_old_name": {"state": 1},   # left over from a renamed user
                           "sensor.other_thing": {"state": 2}}) as ha:
            self.expense(amount=30)                                  # instant push after a change
            self.assertEqual(set(ha.balance_sensors()) - {"sensor.splitpot_balance_old_name"},
                             {"sensor.splitpot_balance_ann", "sensor.splitpot_balance_ben", "sensor.splitpot_balance_cat"})
            self.assertTrue(self.c.get("/api/whoami", headers=ALICE).json()["sensorSyncEnabled"])

            r = self.c.put("/api/admin/settings", headers=ADMIN, json={"ha_sync_enabled": False})
            self.assertEqual((r.status_code, r.json()["values"]["ha_sync_enabled"]), (200, False))
            self.assertEqual(ha.balance_sensors(), {})              # every balance sensor gone, stale one too
            self.assertIn("sensor.other_thing", ha.states)          # nothing else touched
            w = self.c.get("/api/whoami", headers=ALICE).json()
            self.assertEqual((w["sensorSyncEnabled"], w["haConnected"]), (False, True))

            posts = ha.count("POST")
            self.expense(amount=12)
            e = self.expense(amount=5)
            self.c.put(f"/api/expenses/{e['id']}", headers=ALICE, json={"description": "x", "amount": 6, "paidBy": "a"})
            self.c.put("/api/admin/settings", headers=ADMIN, json={"currency": "EUR"})
            self.assertEqual(main.push_balances_to_ha(), 0)
            self.assertEqual(ha.count("POST"), posts)               # nothing pushed while off

            r = self.c.put("/api/admin/settings", headers=ADMIN, json={"ha_sync_enabled": True})
            self.assertEqual(r.status_code, 200)
            sensors = ha.balance_sensors()
            self.assertEqual(len(sensors), 3)                       # pushed straight away
            self.assertEqual({v["attributes"]["unit_of_measurement"] for v in sensors.values()}, {"EUR"})
            ev = [x["message"] for x in self.c.get("/api/events", headers=ALICE).json() if x["type"] == "settings_changed"]
            self.assertIn("sensor sync on", ev[0])
            self.assertIn("sensor sync off", ev[-1])

    def test_removal_without_state_list_uses_known_users_and_404_is_fine(self):
        with FakeHA(self, {"sensor.splitpot_balance_ann": {"state": 1}}) as ha:
            ha.fail_list = True
            with self.assertLogs("splitpot", "WARNING"):
                removed = main.remove_balance_sensors()
            self.assertEqual(ha.balance_sensors(), {})
            self.assertEqual(removed, 4)   # Ann deleted; Ben, Cat, Xavier answered 404 = gone

    def test_no_home_assistant_connection(self):
        calls = []
        orig = main._ha_call
        main._ha_call = lambda *a, **k: calls.append(a)
        try:
            self.expense(amount=30)
            self.c.put("/api/admin/settings", headers=ADMIN, json={"ha_sync_enabled": False})
            self.c.put("/api/admin/settings", headers=ADMIN, json={"ha_sync_enabled": True})
            self.assertEqual(calls, [])
        finally:
            main._ha_call = orig
        w = self.c.get("/api/whoami", headers=ALICE).json()
        self.assertEqual((w["sensorSyncEnabled"], w["haConnected"]), (False, False))
        self.assertTrue(settings(self.c).json()["values"]["ha_sync_enabled"])

    def test_loop_follows_the_switch(self):
        """Fake clock: on at start (push at t=10); switched off at t=40 ->
        sensors removed once on the next tick, no pushes while off; back on
        at t=160 -> pushed on the next tick, then every interval."""
        self.expense(amount=30)
        t = [0.0]
        log = []

        async def fake_sleep(seconds):
            t[0] += seconds
            if 40 <= t[0] < 70 and main.get_setting("ha_sync_enabled"):
                main.update_settings({"ha_sync_enabled": False}, "test")
            if t[0] >= 160 and not main.get_setting("ha_sync_enabled"):
                main.update_settings({"ha_sync_enabled": True, "sync_interval_minutes": 1}, "test")
            if t[0] > 300:
                raise _Stop

        with FakeHA(self) as ha:
            orig_push, orig_remove = main.push_balances_to_ha, main.remove_balance_sensors
            main.push_balances_to_ha = lambda: log.append(("push", t[0])) or orig_push()
            main.remove_balance_sensors = lambda: log.append(("remove", t[0])) or orig_remove()
            try:
                with self.assertRaises(_Stop):
                    asyncio.run(main.periodic_sync(sleep=fake_sleep, clock=lambda: t[0]))
            finally:
                main.push_balances_to_ha, main.remove_balance_sensors = orig_push, orig_remove
            self.assertEqual(log, [("push", 10), ("remove", 40), ("push", 160), ("push", 220), ("push", 280)])
            self.assertEqual(len(ha.balance_sensors()), 3)

    def test_loop_removes_sensors_at_startup_when_off(self):
        main.update_settings({"ha_sync_enabled": False}, "test")
        t = [0.0]

        async def fake_sleep(seconds):
            t[0] += seconds
            if t[0] > 200:
                raise _Stop

        with FakeHA(self, {"sensor.splitpot_balance_ann": {"state": 3}}) as ha:
            with self.assertRaises(_Stop):
                asyncio.run(main.periodic_sync(sleep=fake_sleep, clock=lambda: t[0]))
            self.assertEqual(ha.balance_sensors(), {})
            self.assertEqual(ha.count("GET"), 1)          # removed once, then idle
            self.assertEqual(ha.count("POST"), 0)


class _Stop(Exception):
    pass


class SyncLoopInterval(Base):
    def test_next_run_uses_current_setting(self):
        self.assertEqual(main.sync_interval_seconds(), 300)
        self.assertEqual(main.seconds_until_next_sync(1000.0, 1000.0), 300)
        self.assertEqual(main.seconds_until_next_sync(1000.0, 1100.0), 200)
        self.c.put("/api/admin/settings", headers=ADMIN, json={"sync_interval_minutes": 1})
        self.assertEqual(main.sync_interval_seconds(), 60)
        self.assertEqual(main.seconds_until_next_sync(1000.0, 1000.0), 60)
        self.assertEqual(main.seconds_until_next_sync(1000.0, 1100.0), 0)

    def test_loop_picks_up_a_change_without_restart(self):
        """Fake clock + sleep: first push at t=10 (startup delay) with the
        5-minute default; at t=40 an admin sets 1 minute, so the next push is
        at t=70 (not t=310) and every minute after that."""
        t = [0.0]
        pushes, sleeps = [], []
        changed = []

        async def fake_sleep(seconds):
            sleeps.append(seconds)
            t[0] += seconds
            if t[0] >= 40 and not changed:
                changed.append(True)
                main.update_settings({"sync_interval_minutes": 1}, "test")
            if t[0] > 200:
                raise _Stop

        orig = main.push_balances_to_ha
        main.push_balances_to_ha = lambda: pushes.append(t[0])
        try:
            with self.assertRaises(_Stop):
                asyncio.run(main.periodic_sync(sleep=fake_sleep, clock=lambda: t[0]))
        finally:
            main.push_balances_to_ha = orig
        self.assertEqual(pushes, [10, 70, 130, 190])
        self.assertLessEqual(max(sleeps), main.SYNC_TICK_SECONDS)

    def test_loop_survives_a_failed_cycle(self):
        t = [0.0]
        calls = []

        async def fake_sleep(seconds):
            t[0] += seconds
            if t[0] > 700:
                raise _Stop

        def flaky():
            calls.append(t[0])
            raise sqlite3.OperationalError("database is locked")

        orig = main.push_balances_to_ha
        main.push_balances_to_ha = flaky
        try:
            with self.assertLogs("splitpot", "ERROR"), self.assertRaises(_Stop):
                asyncio.run(main.periodic_sync(sleep=fake_sleep, clock=lambda: t[0]))
        finally:
            main.push_balances_to_ha = orig
        self.assertEqual(calls, [10, 310, 610])


ADDON_DIR = Path(__file__).resolve().parent.parent


def top_level_keys(yaml_text, section):
    """Keys directly under a top-level `section:` of config.yaml (enough of
    a YAML reader for this file; PyYAML isn't a pinned dependency)."""
    keys, inside = [], False
    for line in yaml_text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith(" "):
            inside = line.split(":", 1)[0] == section
            continue
        if inside and line.startswith("  ") and not line.startswith("   ") and not line.strip().startswith("-"):
            keys.append(line.strip().split(":", 1)[0])
    return keys


class OnlyAdminUsersOption(Base):
    """config.yaml only has admin_users, and keys named like the App
    settings in options.json are ignored (App settings live in SQLite)."""

    def test_config_yaml_has_only_admin_users(self):
        text = (ADDON_DIR / "config.yaml").read_text()
        self.assertEqual(top_level_keys(text, "options"), ["admin_users"])
        self.assertEqual(top_level_keys(text, "schema"), ["admin_users"])
        tr = (ADDON_DIR / "translations" / "en.yaml").read_text()
        self.assertEqual(top_level_keys(tr, "configuration"), ["admin_users"])
        settings_lines = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
        for key in ("ha_sync_enabled", "sync_interval_minutes", "currency"):
            self.assertNotIn(key, settings_lines)
            self.assertNotIn(key, tr)

    def test_old_options_json_values_are_ignored(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w") as f:
            json.dump({"admin_users": ["Someone"], "ha_sync_enabled": False,
                       "sync_interval_minutes": 42, "currency": "EUR"}, f)
        self.addCleanup(os.remove, path)
        orig = main.HA_OPTIONS_FILE
        main.HA_OPTIONS_FILE = Path(path)
        try:
            self.assertEqual(main.load_ha_options(), {"admin_users": ["Someone"]})
            main.init_db()   # a "startup" against that options.json
        finally:
            main.HA_OPTIONS_FILE = orig
        self.assertFalse(hasattr(main, "import_legacy_options"))
        self.assertEqual(main.settings_all(), DEFAULTS)
        self.assertEqual(settings(self.c).json()["values"], DEFAULTS)
        with main.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM app_settings").fetchone()[0], 0)


class NoAdminYet(Base):
    """First run: admin_users is empty, so nobody is an administrator and
    every page shows the "No admin yet" banner. Nobody is promoted
    automatically, not even the first visitor."""

    def setUp(self):
        super().setUp()
        self._admins = set(main.ADMIN_NAMES)
        main.ADMIN_NAMES.clear()
        self.addCleanup(lambda: (main.ADMIN_NAMES.clear(), main.ADMIN_NAMES.update(self._admins)))

    def test_flag_set_for_everyone_and_nobody_promoted(self):
        for who in (ADMIN, ALICE, ADMIN):   # the first visitor, then others, then again
            w = self.c.get("/api/whoami", headers=who).json()
            self.assertTrue(w["noAdminYet"])
            self.assertFalse(w["isAdmin"])
            self.assertEqual(w["adminEntries"], 0)
            self.assertEqual(settings(self.c, who).status_code, 403)
        self.assertEqual(self.c.get("/api/admin/users", headers=ADMIN).status_code, 403)

    def test_flag_clears_once_an_admin_is_listed(self):
        main.ADMIN_NAMES.add("adminy")
        for who in (ADMIN, ALICE):
            self.assertFalse(self.c.get("/api/whoami", headers=who).json()["noAdminYet"])

    def test_banner_is_on_the_page_shell_and_filled_from_whoami(self):
        page = self.c.get("/", headers=ALICE).text
        banner = page[page.index('id="noAdminBanner"'):]
        banner = banner[:banner.index("</div>")]
        for text in ("No admin yet", 'id="noAdminName"', "<code>admin_users</code>",
                     "Configuration tab", "restart the app", 'id="noAdminWhoami"', "How the app sees you"):
            self.assertIn(text, banner)
        # outside every .view, so it shows on every page
        self.assertLess(page.index('id="noAdminBanner"'), page.index('id="view-dashboard"'))
        js = (ADDON_DIR / "public" / "app.js").read_text()
        self.assertIn("who.noAdminYet", js)
        self.assertIn("$('#noAdminWhoami').addEventListener('click', openWhoami)", js)
        self.assertIn("renderNoAdminBanner(who);", js)


class AdminOnlyUsersAndStorage(Base):
    def test_manage_users_is_admin_only(self):
        for who in (ALICE, IMPOSTOR):
            self.assertEqual(self.c.get("/api/admin/users", headers=who).status_code, 403)
            self.assertEqual(self.c.patch("/api/users/b", headers=who, json={"disabled": True}).status_code, 403)
        r = self.c.get("/api/admin/users", headers=ADMIN)
        self.assertEqual(r.status_code, 200)
        ann = next(u for u in r.json() if u["id"] == "a")
        self.assertEqual((ann["name"], ann["groupCount"], ann["disabled"]), ("Ann", 1, False))
        self.assertIn("haEntityId", ann)
        r = self.c.patch("/api/users/b", headers=ADMIN, json={"disabled": True})
        self.assertEqual((r.status_code, r.json()["disabled"]), (200, True))
        self.assertEqual(self.c.patch("/api/users/nobody", headers=ADMIN, json={"disabled": True}).status_code, 404)

    def test_pickers_and_balances_still_work_for_everyone(self):
        users = self.c.get("/api/users", headers=ALICE)
        self.assertEqual(users.status_code, 200)
        self.assertEqual({u["name"] for u in users.json()}, {"Ann", "Ben", "Cat", "Xavier"})
        self.assertNotIn("haEntityId", users.json()[0])
        r = self.c.post("/api/groups", headers=ALICE, json={"name": "Trip", "memberIds": ["a", "x"]})
        self.assertEqual(r.status_code, 201)
        self.expense(amount=30)
        self.assertEqual(self.c.get(f"/api/groups/{self.gid}", headers=ALICE).status_code, 200)
        self.assertEqual(self.c.get("/api/dashboard", headers=ALICE).status_code, 200)
        self.assertEqual(self.c.get("/api/config", headers=ALICE).status_code, 200)

    def test_storage_is_admin_only(self):
        for who in (ALICE, IMPOSTOR):
            self.assertEqual(self.c.get("/api/admin/backup-db", headers=who).status_code, 403)
            r = self.c.post("/api/admin/import-db", headers=who, files={"file": ("x.db", io.BytesIO(b"junk"))})
            self.assertEqual(r.status_code, 403)
        self.assertEqual(self.c.get("/api/admin/backup-db", headers=ADMIN).status_code, 200)


class RestoreKeepsOrRestoresSettings(Base):
    def _restore(self, path):
        with open(path, "rb") as f:
            r = self.c.post("/api/admin/import-db", headers=ADMIN, files={"file": ("b.db", f)})
        self.assertEqual(r.status_code, 200, r.text)

    def test_backup_settings_restored_and_settingless_backup_keeps_current(self):
        main.update_settings({"currency": "GBP"}, "Adminy")
        snap = self.c.get("/api/admin/backup-db", headers=ADMIN).content
        path = os.path.join(os.environ["DATA_DIR"], "s.db")
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        with open(path, "wb") as f:
            f.write(snap)
        main.update_settings({"currency": "EUR"}, "Adminy")
        self._restore(path)
        self.assertEqual(main.get_setting("currency"), "GBP")   # the backup's own settings
        self.assertEqual(self.c.get("/api/config", headers=ALICE).json()["currency"], "GBP")

        # a backup of an older database has no app_settings table: current settings stay
        main.update_settings({"currency": "CHF", "sync_interval_minutes": 20}, "Adminy")
        with open(path, "wb") as f:
            f.write(snap)
        old = sqlite3.connect(path)
        old.execute("DROP TABLE app_settings")
        old.commit(); old.close()
        self._restore(path)
        self.assertEqual(main.settings_all(), {**DEFAULTS, "sync_interval_minutes": 20, "currency": "CHF"})


if __name__ == "__main__":
    unittest.main()
