"""Unit tests below the HTTP layer: schema and migrations, reminders, sensors,
housekeeping, drive time, notify fallback and hints, backup/restore.
All people, names, phones and addresses in these tests are invented data."""
import _env  # noqa: F401  (must be first)

import glob
import os
import sqlite3
import tempfile
import unittest
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from unittest import mock

from starlette.exceptions import HTTPException

from app import config, db, drive_time, geocode, ha_client, ha_notify, ha_sensors, housekeeping, links, reminders, schedule_logic, settings, taskview
from app.routers import places as places_router
from app.routers import prefs as prefs_router
from fake_ha import FakeHA

UTC = timezone.utc
NOW = datetime(2026, 9, 21, 12, 0, 0, tzinfo=UTC)   # Monday 21 Sep 2026, noon UTC
TODAY = date(2026, 9, 21)


def reset_db():
    for f in glob.glob(config.DB_PATH + "*"):
        os.remove(f)
    db.init_db()


_deferred_links: list[tuple[str, str]] = []


def _insert_link(conn, user_id, service):
    conn.execute("INSERT OR IGNORE INTO user_notify (user_id, service, created_at) VALUES (?, ?, ?)",
                 (user_id, ha_notify.normalize_service(service), config.now_iso()))


def link(key, service):
    """Test shorthand for Admin → Users: give the person whose login name (or
    id) is `key` a notify service. If they don't exist yet, it's applied by
    add_user() when they're created (setUps link first, add users after)."""
    with db.get_conn() as c:
        row = c.execute("SELECT id FROM users WHERE lower(username) = ? OR lower(id) = ?",
                        (key.lower(), key.lower())).fetchone()
        if row:
            _insert_link(c, row["id"], service)
        else:
            _deferred_links.append((key.lower(), service))


def set_setting(**kw):
    settings.update(kw, None)


def drive_on():
    """Turn on the "Drive times" App setting (off for a new install)."""
    set_setting(drive_times_enabled=True)


def add_user(conn, uid, name, username=None, disabled=0):
    conn.execute("INSERT INTO users (id, name, username, created_at, disabled) VALUES (?,?,?,?,?)",
                 (uid, name, username, config.now_iso(), disabled))
    for key, service in list(_deferred_links):
        if key in (uid.lower(), (username or "").lower()):
            _insert_link(conn, uid, service)
            _deferred_links.remove((key, service))


def add_list(conn, lid, name, kind="shared", owner=None):
    conn.execute("INSERT INTO lists (id, name, kind, owner_user_id, created_at, position) VALUES (?,?,?,?,?,0)",
                 (lid, name, kind, owner, config.now_iso()))


def add_task(conn, tid, list_id, title="T", **kw):
    cols = {"id": tid, "list_id": list_id, "title": title, "created_by": kw.pop("created_by", "u1"),
            "created_at": config.now_iso(), "position": kw.pop("position", 0)}
    cols.update(kw)
    conn.execute(f"INSERT INTO tasks ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", list(cols.values()))


class Base(unittest.TestCase):
    ha = None

    @classmethod
    def setUpClass(cls):
        cls.ha = FakeHA()
        config.SUPERVISOR_CORE_API = cls.ha.start()
        config.SUPERVISOR_TOKEN = "test-token"

    @classmethod
    def tearDownClass(cls):
        cls.ha.stop()

    def setUp(self):
        config.utcnow = lambda: NOW
        config.set_timezone("UTC")
        self.ha.requests.clear()
        self.ha.states.clear()
        self.ha.fail = False
        ha_client._warned_no_token = False
        ha_notify._targets_cache = None
        ha_notify._entity_mode.clear()
        _deferred_links.clear()
        self.ha.notify_services = None
        self.ha.notify_entities = []
        reset_db()


class InitDb(Base):
    def test_seed_once(self):
        with db.get_conn() as c:
            self.assertEqual([r["name"] for r in c.execute("SELECT name FROM lists")], ["Household"])
            types = [r["name"] for r in c.execute("SELECT name FROM task_types ORDER BY position")]
            self.assertEqual(types, ["Appointment", "Doctor appointment", "Errand", "Chore", "Bill"])
            c.execute("DELETE FROM task_types WHERE name = 'Errand'")
            c.execute("DELETE FROM lists")
        db.init_db()   # a second run must not bring anything back
        with db.get_conn() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM task_types").fetchone()[0], 4)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM lists").fetchone()[0], 0)

    def test_ten_tables_and_foreign_keys(self):
        with db.get_conn() as c:
            tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue(db.REQUIRED_TABLES <= tables)
            self.assertEqual(len(db.REQUIRED_TABLES), 14)
            self.assertEqual(c.execute("PRAGMA foreign_keys").fetchone()[0], 1)

    def test_cascade_and_set_null(self):
        with db.get_conn() as c:
            add_user(c, "u1", "Ann")
            add_list(c, "l1", "Shared")
            c.execute("INSERT INTO task_types (id, name, created_at) VALUES ('ty1','X',?)", (config.now_iso(),))
            add_task(c, "t1", "l1", type_id="ty1")
            c.execute("INSERT INTO task_items (id, task_id, text, created_at) VALUES ('i1','t1','milk',?)", (config.now_iso(),))
            c.execute("DELETE FROM task_types WHERE id='ty1'")
            self.assertIsNone(c.execute("SELECT type_id FROM tasks WHERE id='t1'").fetchone()[0])
            c.execute("DELETE FROM lists WHERE id='l1'")
            self.assertEqual(c.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM task_items").fetchone()[0], 0)

    def test_exception_check_constraint(self):
        with db.get_conn() as c:
            c.execute("INSERT INTO schedule_items (id,name,rule,anchor_date,entity_slug,created_at) VALUES ('s1','n','daily','2026-09-21','n',?)", (config.now_iso(),))
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute("INSERT INTO schedule_exceptions (id,item_id,kind,date,to_date,created_at) VALUES ('e1','s1','skip','2026-09-22','2026-09-23',?)", (config.now_iso(),))
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute("INSERT INTO schedule_exceptions (id,item_id,kind,date,created_at) VALUES ('e2','s1','move','2026-09-22',?)", (config.now_iso(),))


class Housekeeping(Base):
    def days_ago(self, n, hour=12):
        return (NOW - timedelta(days=n)).replace(hour=hour).isoformat(timespec="seconds")

    def test_purge_boundary_and_scope(self):
        with db.get_conn() as c:
            add_user(c, "u1", "Ann")
            add_list(c, "l1", "Shared")
            add_list(c, "l2", "Mine", "personal", "u1")
            add_task(c, "d59", "l1", completed=1, completed_at=self.days_ago(59))
            add_task(c, "d60", "l1", completed=1, completed_at=self.days_ago(60))
            add_task(c, "d61", "l1", completed=1, completed_at=self.days_ago(61))
            add_task(c, "p90", "l2", completed=1, completed_at=self.days_ago(90), completion_required=1)
            add_task(c, "open_old", "l1", completed=0, due_date="2020-01-01")
            add_task(c, "noat", "l1", completed=1)   # completed but no completed_at: left alone
            c.execute("INSERT INTO task_items (id, task_id, text, created_at) VALUES ('i1','d61','x',?)", (config.now_iso(),))
        res = housekeeping.run_pass_blocking(TODAY)
        self.assertEqual(res["tasks"], 2)
        with db.get_conn() as c:
            left = {r["id"] for r in c.execute("SELECT id FROM tasks")}
            self.assertEqual(left, {"d59", "d60", "open_old", "noat"})
            self.assertEqual(c.execute("SELECT COUNT(*) FROM task_items").fetchone()[0], 0)

    def test_local_date_cutoff_non_utc(self):
        # Auckland is UTC+12 in September (NZST) -> +13 from 27 Sep (NZDT); use a fixed zone check.
        config.set_timezone("Pacific/Auckland")
        # NOW is 2026-09-21 12:00Z = 2026-09-22 00:00 NZST, so today (local) is the 22nd.
        self.assertEqual(config.today(), date(2026, 9, 22))
        with db.get_conn() as c:
            add_user(c, "u1", "Ann")
            add_list(c, "l1", "S")
            # completed 2026-07-22 12:30Z = 2026-07-23 00:30 NZST -> local date 23 Jul: (22 Sep - 23 Jul) = 61 days -> purged
            add_task(c, "a", "l1", completed=1, completed_at="2026-07-22T12:30:00+00:00")
            # completed 2026-07-23 11:30Z = 2026-07-23 23:30 NZST -> local 23 Jul -> 61 days -> purged
            add_task(c, "b", "l1", completed=1, completed_at="2026-07-23T11:30:00+00:00")
            # completed 2026-07-23 12:30Z = 2026-07-24 00:30 NZST -> local 24 Jul -> 60 days -> kept
            add_task(c, "c", "l1", completed=1, completed_at="2026-07-23T12:30:00+00:00")
        res = housekeeping.run_pass_blocking()
        self.assertEqual(res["tasks"], 2)
        with db.get_conn() as c:
            self.assertEqual({r["id"] for r in c.execute("SELECT id FROM tasks")}, {"c"})

    def test_prune_exceptions(self):
        with db.get_conn() as c:
            c.execute("INSERT INTO schedule_items (id,name,rule,anchor_date,entity_slug,created_at) VALUES ('s1','n','daily','2026-01-01','n',?)", (config.now_iso(),))
            def exc(i, kind, d, to=None):
                c.execute("INSERT INTO schedule_exceptions (id,item_id,kind,date,to_date,created_at) VALUES (?,?,?,?,?,?)",
                          (i, "s1", kind, d, to, config.now_iso()))
            exc("skip_past", "skip", "2026-09-20")
            exc("skip_today", "skip", "2026-09-21")
            exc("add_past", "add", "2026-09-10")
            exc("move_both_past", "move", "2026-09-18", "2026-09-19")
            exc("move_target_future", "move", "2026-09-19", "2026-09-22")
            exc("move_orig_future", "move", "2026-09-30", "2026-09-29")
        res = housekeeping.run_pass_blocking(TODAY)
        self.assertEqual(res["exceptions"], 3)
        with db.get_conn() as c:
            left = {r["id"] for r in c.execute("SELECT id FROM schedule_exceptions")}
        self.assertEqual(left, {"skip_today", "move_target_future", "move_orig_future"})


class Digest(Base):
    def setUp(self):
        super().setUp()
        config.utcnow = lambda: datetime(2026, 9, 21, 8, 5, tzinfo=UTC)   # 08:05, inside the 08:00 window
        link("ann", "mobile_app_ann")
        link("bob", "mobile_app_bob")
        with db.get_conn() as c:
            add_user(c, "u1", "Ann", "ann")
            add_user(c, "u2", "Bob", "bob")
            add_user(c, "u3", "Cy", "cy")
            add_list(c, "shared", "Household")
            add_list(c, "ann_p", "My Tasks", "personal", "u1")
            add_list(c, "bob_p", "My Tasks", "personal", "u2")
            for u in ("u1", "u2", "u3"):
                c.execute("INSERT INTO user_prefs (user_id, notifications_enabled, lead_days, notify_on_assign) VALUES (?,1,1,1)", (u,))
            c.execute("INSERT INTO places (id,name,address,created_at) VALUES ('pl1','Mercy Hospital','1 Main St',?)", (config.now_iso(),))

    def sent(self):
        out = []

        def fake(service, title, message):
            out.append((service, title, message))
            return True
        return out, fake

    def test_content_required_vs_optional(self):
        with db.get_conn() as c:
            add_task(c, "od_req", "shared", "Pay water bill", assigned_to="u1", due_date="2026-09-18", completion_required=1)
            add_task(c, "od_opt", "shared", "Optional past", assigned_to="u1", due_date="2026-09-18", completion_required=0)
            add_task(c, "today", "ann_p", "Dr appointment", due_date="2026-09-21", due_time="14:30", place_id="pl1")
            add_task(c, "tomorrow_opt", "shared", "Optional tomorrow", assigned_to="u1", due_date="2026-09-22")
            add_task(c, "later", "shared", "Way later", assigned_to="u1", due_date="2026-09-30")
            add_task(c, "undated", "ann_p", "No date")
            add_task(c, "done", "ann_p", "Done", due_date="2026-09-21", completed=1, completed_at=config.now_iso())
            add_task(c, "bobs", "bob_p", "Bob's private", due_date="2026-09-21")
        out, fake = self.sent()
        n = reminders.run_digest_pass_blocking(sender=fake)
        self.assertEqual(n, 2)   # Ann and Bob (Cy has no mapping)
        ann = next(m for s, t, m in out if s == "mobile_app_ann")
        self.assertEqual(ann.split("\n"), [
            "• Pay water bill — 3 days overdue",
            "• Dr appointment — today 14:30 @ Mercy Hospital",
            "• Optional tomorrow — tomorrow",
        ])
        self.assertNotIn("Bob's private", ann)
        self.assertNotIn("Optional past", ann)
        bob = next(m for s, t, m in out if s == "mobile_app_bob")
        self.assertEqual(bob, "• Bob's private — today")
        self.assertTrue(all(t == "Household Todo" for s, t, m in out))

    def test_digest_includes_drive_note_when_place_has_one(self):
        drive_on()
        with db.get_conn() as c:
            c.execute("UPDATE places SET drive_minutes = 18 WHERE id = 'pl1'")
            add_task(c, "appt", "ann_p", "Dr appointment", due_date="2026-09-21", due_time="14:30", place_id="pl1")
        out, fake = self.sent()
        reminders.run_digest_pass_blocking(sender=fake)
        ann = next(m for s, t, m in out if s == "mobile_app_ann")
        self.assertIn("Dr appointment — today 14:30 @ Mercy Hospital · ~18 min drive, leave by 14:12", ann)

    def test_truncates_to_five(self):
        with db.get_conn() as c:
            for i in range(7):
                add_task(c, f"t{i}", "ann_p", f"Task {i}", due_date="2026-09-21", position=i)
        out, fake = self.sent()
        reminders.run_digest_pass_blocking(sender=fake)
        msg = next(m for s, t, m in out if s == "mobile_app_ann")
        lines = msg.split("\n")
        self.assertEqual(len(lines), 6)
        self.assertEqual(lines[-1], "+2 more")

    def test_no_empty_digest_and_notes_never_sent(self):
        with db.get_conn() as c:
            add_task(c, "t1", "ann_p", "Only title", notes="SECRET NOTE", due_date="2026-09-21")
        out, fake = self.sent()
        reminders.run_digest_pass_blocking(sender=fake)
        self.assertEqual(len(out), 1)          # Ann only; Bob has nothing due
        self.assertNotIn("SECRET", out[0][2])

    def test_dedupe_and_retry_window(self):
        with db.get_conn() as c:
            add_task(c, "t1", "ann_p", "X", due_date="2026-09-21")
        calls = []
        results = iter([False, True, True])

        def flaky(service, title, message):
            calls.append(service)
            return next(results)
        self.assertEqual(reminders.run_digest_pass_blocking(sender=flaky), 0)   # failed -> not recorded
        with db.get_conn() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM notification_log").fetchone()[0], 0)
        self.assertEqual(reminders.run_digest_pass_blocking(sender=flaky), 1)   # retried next tick
        self.assertEqual(reminders.run_digest_pass_blocking(sender=flaky), 0)   # already sent today
        self.assertEqual(len(calls), 2)

    def test_window(self):
        with db.get_conn() as c:
            add_task(c, "t1", "ann_p", "X", due_date="2026-09-21")
        out, fake = self.sent()
        config.utcnow = lambda: datetime(2026, 9, 21, 7, 59, tzinfo=UTC)
        self.assertEqual(reminders.run_digest_pass_blocking(sender=fake), 0)
        config.utcnow = lambda: datetime(2026, 9, 21, 9, 0, tzinfo=UTC)          # 60 min past -> outside
        self.assertEqual(reminders.run_digest_pass_blocking(sender=fake), 0)
        config.utcnow = lambda: datetime(2026, 9, 21, 8, 59, tzinfo=UTC)
        self.assertEqual(reminders.run_digest_pass_blocking(sender=fake), 1)

    def test_per_user_digest_time(self):
        # Bob picks his own digest time; Ann never picked one
        # (NULL counts as 08:00 — there is no household-wide default).
        with db.get_conn() as c:
            c.execute("UPDATE user_prefs SET digest_time = '20:00' WHERE user_id = 'u2'")
            add_task(c, "a1", "ann_p", "Ann's task", due_date="2026-09-21")
            add_task(c, "b1", "bob_p", "Bob's task", due_date="2026-09-21")
        out, fake = self.sent()
        # 08:05 -> inside Ann's (default) window, outside Bob's custom one
        config.utcnow = lambda: datetime(2026, 9, 21, 8, 5, tzinfo=UTC)
        self.assertEqual(reminders.run_digest_pass_blocking(sender=fake), 1)
        self.assertEqual([s for s, t, m in out], ["mobile_app_ann"])
        # 20:05 -> inside Bob's window; Ann already sent today so she is skipped
        config.utcnow = lambda: datetime(2026, 9, 21, 20, 5, tzinfo=UTC)
        self.assertEqual(reminders.run_digest_pass_blocking(sender=fake), 1)
        self.assertEqual([s for s, t, m in out], ["mobile_app_ann", "mobile_app_bob"])

    def test_migration_fills_unset_digest_time_from_old_reminder_time(self):
        # the household-wide reminder_time is gone. A person who never
        # picked a time (NULL) gets the old household value (an older
        # database may keep it in app_settings), so their digest doesn't jump;
        # someone who did pick one keeps it; the old setting is deleted.
        with db.get_conn() as c:
            c.execute("UPDATE user_prefs SET digest_time = '20:00' WHERE user_id = 'u2'")
            c.execute("INSERT INTO app_settings (key, value, updated_at) VALUES ('reminder_time', '\"09:30\"', 'x')")
            c.execute("INSERT INTO app_settings (key, value, updated_at) VALUES ('_imported', '[]', 'x')")
            c.execute("CREATE TABLE user_notify_pending (user_key TEXT, service TEXT, created_at TEXT)")
            add_task(c, "a1", "ann_p", "Ann's task", due_date="2026-09-21")
        db.init_db()
        with db.get_conn() as c:
            times = {r["user_id"]: r["digest_time"] for r in c.execute("SELECT user_id, digest_time FROM user_prefs")}
            self.assertEqual(times, {"u1": "09:30", "u2": "20:00", "u3": "09:30"})
            self.assertEqual(c.execute("SELECT COUNT(*) FROM app_settings WHERE key != 'drive_times_enabled'").fetchone()[0], 0)
            self.assertIsNone(c.execute("SELECT 1 FROM sqlite_master WHERE name = 'user_notify_pending'").fetchone())
        out, fake = self.sent()
        config.utcnow = lambda: datetime(2026, 9, 21, 9, 35, tzinfo=UTC)
        self.assertEqual(reminders.run_digest_pass_blocking(sender=fake), 1)

    def test_migration_without_old_setting_uses_0800(self):
        with db.get_conn() as c:
            c.execute("UPDATE user_prefs SET digest_time = NULL")
        db.init_db()
        db.init_db()   # idempotent
        with db.get_conn() as c:
            self.assertEqual({r[0] for r in c.execute("SELECT digest_time FROM user_prefs")}, {"08:00"})

    def test_switches_and_disabled(self):
        with db.get_conn() as c:
            add_task(c, "t1", "ann_p", "X", due_date="2026-09-21")
            c.execute("UPDATE user_prefs SET notifications_enabled = 0 WHERE user_id = 'u1'")
        out, fake = self.sent()
        self.assertEqual(reminders.run_digest_pass_blocking(sender=fake), 0)
        with db.get_conn() as c:
            c.execute("UPDATE user_prefs SET notifications_enabled = 1 WHERE user_id = 'u1'")
            c.execute("UPDATE users SET disabled = 1 WHERE id = 'u1'")
        self.assertEqual(reminders.run_digest_pass_blocking(sender=fake), 0)

    def test_lead_days_zero_means_today_and_overdue_only(self):
        with db.get_conn() as c:
            c.execute("UPDATE user_prefs SET lead_days = 0 WHERE user_id = 'u1'")
            add_task(c, "t1", "ann_p", "Tomorrow", due_date="2026-09-22")
        out, fake = self.sent()
        self.assertEqual(reminders.run_digest_pass_blocking(sender=fake), 0)

    def test_no_token_sends_nothing(self):
        with db.get_conn() as c:
            add_task(c, "t1", "ann_p", "X", due_date="2026-09-21")
        out, fake = self.sent()
        config.SUPERVISOR_TOKEN = ""
        try:
            self.assertEqual(reminders.run_digest_pass_blocking(sender=fake), 0)
        finally:
            config.SUPERVISOR_TOKEN = "test-token"

    def test_real_sender_hits_fake_ha(self):
        with db.get_conn() as c:
            add_task(c, "t1", "ann_p", "Real path", due_date="2026-09-21")
        self.assertEqual(reminders.run_digest_pass_blocking(), 1)
        svc, body = self.ha.notifications()[0]
        self.assertEqual(svc, "mobile_app_ann")
        self.assertEqual(body["title"], "Household Todo")
        self.assertIn("Real path", body["message"])
        self.assertEqual(self.ha.requests[-1][3], "Bearer test-token")

    def test_assignment_ping(self):
        out, fake = self.sent()
        self.assertTrue(reminders.send_assignment_ping_blocking("u2", "Ann", "Take out bins", "2026-09-22", sender=fake))
        self.assertEqual(out[-1], ("mobile_app_bob", "Household Todo", "Ann assigned you: Take out bins (due 2026-09-22)"))
        self.assertTrue(reminders.send_assignment_ping_blocking("u2", "Ann", "No date", None, sender=fake))
        self.assertEqual(out[-1][2], "Ann assigned you: No date")
        with db.get_conn() as c:
            c.execute("UPDATE user_prefs SET notify_on_assign = 0 WHERE user_id = 'u2'")
        self.assertFalse(reminders.send_assignment_ping_blocking("u2", "Ann", "x", None, sender=fake))
        self.assertFalse(reminders.send_assignment_ping_blocking("u3", "Ann", "x", None, sender=fake))  # unmapped

    def test_invalid_service_name_refused(self):
        self.assertFalse(ha_notify.send_notify("bad/../name", "t", "m"))
        self.assertEqual(self.ha.notifications(), [])


class WeeklySummary(Base):
    def setUp(self):
        super().setUp()
        config.utcnow = lambda: NOW  # Monday 21 Sep 2026, noon UTC -> ISO weekday 1
        link("ann", "mobile_app_ann")
        link("bob", "mobile_app_bob")
        with db.get_conn() as c:
            add_user(c, "u1", "Ann", "ann")
            add_user(c, "u2", "Bob", "bob")
            add_list(c, "shared", "Household")
            add_list(c, "ann_p", "My Tasks", "personal", "u1")
            for u in ("u1", "u2"):
                c.execute("INSERT INTO user_prefs (user_id, notifications_enabled, lead_days, notify_on_assign) VALUES (?,1,0,1)", (u,))
            c.execute("INSERT INTO user_weekly_prefs (user_id, enabled, day_of_week, time) VALUES ('u1',1,1,'12:00')")

    def sent(self):
        out = []

        def fake(service, title, message):
            out.append((service, title, message))
            return True
        return out, fake

    def test_sends_next_seven_days_not_today_or_overdue(self):
        with db.get_conn() as c:
            add_task(c, "overdue", "shared", "Overdue", assigned_to="u1", due_date="2026-09-18", completion_required=1)
            add_task(c, "today", "ann_p", "Today", due_date="2026-09-21")
            add_task(c, "tom", "ann_p", "Tomorrow", due_date="2026-09-22")
            add_task(c, "in7", "ann_p", "In a week", due_date="2026-09-28")
            add_task(c, "in8", "ann_p", "Too far", due_date="2026-09-29")
        out, fake = self.sent()
        n = reminders.run_weekly_pass_blocking(sender=fake)
        self.assertEqual(n, 1)   # only Ann has weekly summary enabled
        self.assertEqual(out[0][0], "mobile_app_ann")
        self.assertEqual(out[0][2].split("\n"), ["• Tomorrow — tomorrow", "• In a week — in 7 days"])

    def test_wrong_day_of_week_sends_nothing(self):
        with db.get_conn() as c:
            add_task(c, "t1", "ann_p", "Tomorrow", due_date="2026-09-22")
            c.execute("UPDATE user_weekly_prefs SET day_of_week = 2 WHERE user_id = 'u1'")   # Tuesday, today is Monday
        out, fake = self.sent()
        self.assertEqual(reminders.run_weekly_pass_blocking(sender=fake), 0)

    def test_window_and_dedupe(self):
        with db.get_conn() as c:
            add_task(c, "t1", "ann_p", "Tomorrow", due_date="2026-09-22")
        out, fake = self.sent()
        config.utcnow = lambda: datetime(2026, 9, 21, 13, 1, tzinfo=UTC)   # 61 min past 12:00 -> outside
        self.assertEqual(reminders.run_weekly_pass_blocking(sender=fake), 0)
        config.utcnow = lambda: NOW
        self.assertEqual(reminders.run_weekly_pass_blocking(sender=fake), 1)
        self.assertEqual(reminders.run_weekly_pass_blocking(sender=fake), 0)   # already sent today

    def test_own_toggle_and_disabled_gate_it(self):
        # the daily digest switch no longer matters; the weekly toggle and disabling do
        with db.get_conn() as c:
            add_task(c, "t1", "ann_p", "Tomorrow", due_date="2026-09-22")
            c.execute("UPDATE user_prefs SET notifications_enabled = 0 WHERE user_id = 'u1'")
        out, fake = self.sent()
        self.assertEqual(reminders.run_weekly_pass_blocking(sender=fake), 1)
        with db.get_conn() as c:
            c.execute("DELETE FROM weekly_summary_log")
            c.execute("UPDATE user_weekly_prefs SET enabled = 0 WHERE user_id = 'u1'")
        self.assertEqual(reminders.run_weekly_pass_blocking(sender=fake), 0)
        with db.get_conn() as c:
            c.execute("UPDATE user_weekly_prefs SET enabled = 1 WHERE user_id = 'u1'")
            c.execute("UPDATE users SET disabled = 1 WHERE id = 'u1'")
        self.assertEqual(reminders.run_weekly_pass_blocking(sender=fake), 0)

    def test_empty_week_sends_nothing(self):
        out, fake = self.sent()
        self.assertEqual(reminders.run_weekly_pass_blocking(sender=fake), 0)


class TaskReminders(Base):
    def setUp(self):
        super().setUp()
        drive_on()
        config.utcnow = lambda: NOW  # Monday 21 Sep 2026, 12:00 UTC
        link("ann", "mobile_app_ann")
        with db.get_conn() as c:
            add_user(c, "u1", "Ann", "ann")
            add_list(c, "ann_p", "My Tasks", "personal", "u1")
            c.execute("INSERT INTO user_prefs (user_id, notifications_enabled, lead_days, notify_on_assign) VALUES ('u1',1,0,1)")

    def add_offsets(self, *minutes):
        with db.get_conn() as c:
            for i, m in enumerate(minutes):
                c.execute("INSERT INTO user_reminder_offsets (id, user_id, minutes_before, created_at) VALUES (?, 'u1', ?, ?)",
                          (f"o{i}", m, config.now_iso()))

    def sent(self):
        out = []

        def fake(service, title, message):
            out.append((service, title, message))
            return True
        return out, fake

    def test_fires_each_offset_once_and_skips_dateonly_tasks(self):
        self.add_offsets(300, 60)   # 5h and 1h before
        with db.get_conn() as c:
            add_task(c, "t1", "ann_p", "Timed", due_date="2026-09-21", due_time="18:00")   # 5h before=13:00, 1h before=17:00
            add_task(c, "t2", "ann_p", "No time", due_date="2026-09-21")   # date-only: never a task reminder
        out, fake = self.sent()
        self.assertEqual(reminders.run_task_reminder_pass_blocking(sender=fake), 0)   # neither offset due yet at 12:00
        config.utcnow = lambda: datetime(2026, 9, 21, 13, 0, tzinfo=UTC)
        self.assertEqual(reminders.run_task_reminder_pass_blocking(sender=fake), 1)
        self.assertIn("due in 5 hours", out[-1][2])
        self.assertIn("Timed", out[-1][2])
        self.assertEqual(reminders.run_task_reminder_pass_blocking(sender=fake), 0)   # not re-sent on the next tick
        config.utcnow = lambda: datetime(2026, 9, 21, 17, 0, tzinfo=UTC)
        self.assertEqual(reminders.run_task_reminder_pass_blocking(sender=fake), 1)
        self.assertIn("due in 1 hour", out[-1][2])
        self.assertEqual(len(out), 2)   # the date-only task never got a task reminder

    def test_includes_place_and_drive_note(self):
        self.add_offsets(60)
        with db.get_conn() as c:
            c.execute("INSERT INTO places (id, name, address, drive_minutes, created_at) VALUES ('pl1','Clinic','1 Rd',18,?)", (config.now_iso(),))
            add_task(c, "t1", "ann_p", "Checkup", due_date="2026-09-21", due_time="18:00", place_id="pl1")
        out, fake = self.sent()
        config.utcnow = lambda: datetime(2026, 9, 21, 17, 0, tzinfo=UTC)
        self.assertEqual(reminders.run_task_reminder_pass_blocking(sender=fake), 1)
        self.assertIn("@ Clinic · ~18 min drive, leave by 17:42", out[-1][2])

    def test_never_fires_after_due(self):
        self.add_offsets(60)
        with db.get_conn() as c:
            add_task(c, "t1", "ann_p", "Already due", due_date="2026-09-21", due_time="11:00")   # due_dt < now(12:00)
        out, fake = self.sent()
        self.assertEqual(reminders.run_task_reminder_pass_blocking(sender=fake), 0)

    def test_edited_due_time_fires_again(self):
        self.add_offsets(60)
        with db.get_conn() as c:
            add_task(c, "t1", "ann_p", "Moving", due_date="2026-09-21", due_time="13:00")   # 1h before = 12:00 = now
        out, fake = self.sent()
        self.assertEqual(reminders.run_task_reminder_pass_blocking(sender=fake), 1)
        self.assertEqual(reminders.run_task_reminder_pass_blocking(sender=fake), 0)   # dedupe
        with db.get_conn() as c:
            c.execute("UPDATE tasks SET due_time = '15:00' WHERE id = 't1'")   # new 1h-before = 14:00
        config.utcnow = lambda: datetime(2026, 9, 21, 14, 0, tzinfo=UTC)
        self.assertEqual(reminders.run_task_reminder_pass_blocking(sender=fake), 1)

    def test_offsets_work_without_the_digest_and_no_offsets_gate_it(self):
        # "N before" reminders no longer need the daily digest switched on
        self.add_offsets(60)
        with db.get_conn() as c:
            add_task(c, "t1", "ann_p", "X", due_date="2026-09-21", due_time="13:00")
            c.execute("UPDATE user_prefs SET notifications_enabled = 0 WHERE user_id = 'u1'")
        out, fake = self.sent()
        self.assertEqual(reminders.run_task_reminder_pass_blocking(sender=fake), 1)
        with db.get_conn() as c:
            c.execute("DELETE FROM task_reminder_log")
            c.execute("DELETE FROM user_reminder_offsets WHERE user_id = 'u1'")
        self.assertEqual(reminders.run_task_reminder_pass_blocking(sender=fake), 0)


class Sensors(Base):
    def make_item(self, c, iid="s1", name="Trash pickup", rule="weeks:1:1", anchor="2026-09-21", lead=1, slug=None, icon=None):
        c.execute("INSERT INTO schedule_items (id,name,rule,anchor_date,lead_days,icon,entity_slug,created_at) VALUES (?,?,?,?,?,?,?,?)",
                  (iid, name, rule, anchor, lead, icon, slug or schedule_logic.make_slug(c, name), config.now_iso()))

    def state(self, eid="binary_sensor.household_todo_trash_pickup"):
        return self.ha.states.get(eid)

    def test_slug(self):
        with db.get_conn() as c:
            self.assertEqual(schedule_logic.make_slug(c, "Trash pickup!"), "trash_pickup")
            self.assertEqual(schedule_logic.make_slug(c, "  ***  "), "item")
            self.assertEqual(schedule_logic.make_slug(c, "Café run"), "cafe_run")
            self.assertEqual(len(schedule_logic.make_slug(c, "x" * 100)), 40)
            self.make_item(c, "a", "Trash")
            self.make_item(c, "b", "Trash")
            self.assertEqual(c.execute("SELECT entity_slug FROM schedule_items ORDER BY id").fetchall()[1][0], "trash_2")

    def test_sunday_monday_window(self):
        with db.get_conn() as c:
            self.make_item(c)
        results = {}
        for day, hour in [("2026-09-26", 12), ("2026-09-27", 0), ("2026-09-28", 12), ("2026-09-29", 0), ("2026-10-03", 23), ("2026-10-04", 0)]:
            y, m, d = map(int, day.split("-"))
            config.utcnow = lambda y=y, m=m, d=d, hour=hour: datetime(y, m, d, hour, 0, tzinfo=UTC)
            ha_sensors.full_sync_blocking()
            results[f"{day} {hour:02d}h"] = self.state()["state"]
        self.assertEqual(results, {"2026-09-26 12h": "off", "2026-09-27 00h": "on", "2026-09-28 12h": "on",
                                   "2026-09-29 00h": "off", "2026-10-03 23h": "off", "2026-10-04 00h": "on"})

    def test_attributes(self):
        with db.get_conn() as c:
            self.make_item(c, icon="mdi:trash-can")
        ha_sensors.full_sync_blocking()
        s = self.state()
        self.assertEqual(s["state"], "on")   # Mon 21 Sep is itself a pickup day
        a = s["attributes"]
        self.assertEqual(a["friendly_name"], "Trash pickup")
        self.assertEqual(a["icon"], "mdi:trash-can")
        self.assertEqual(a["rule"], "Every Monday")
        self.assertEqual(a["lead_days"], 1)

    def test_biweekly_and_exceptions(self):
        with db.get_conn() as c:
            self.make_item(c, "s1", "Recycling", rule="weeks:2:1", anchor="2026-09-21")
            slug = c.execute("SELECT entity_slug FROM schedule_items").fetchone()[0]
        eid = f"binary_sensor.household_todo_{slug}"
        ha_sensors.full_sync_blocking()
        self.assertEqual(self.ha.states[eid]["state"], "on")                       # today is a pickup day
        self.assertEqual(self.ha.states[eid]["attributes"]["occurs_today"], True)
        # Tuesday after: off; Sunday 4 Oct (day before 5 Oct pickup): on; Sunday 27 Sep: off
        for day, want in [((2026, 9, 22), "off"), ((2026, 9, 27), "off"), ((2026, 10, 4), "on"), ((2026, 10, 5), "on")]:
            config.utcnow = lambda day=day: datetime(*day, 12, 0, tzinfo=UTC)
            ha_sensors.full_sync_blocking()
            self.assertEqual(self.ha.states[eid]["state"], want, day)
        # skip 5 Oct: Sunday 4 and Monday 5 both off; next date is 19 Oct
        with db.get_conn() as c:
            c.execute("INSERT INTO schedule_exceptions (id,item_id,kind,date,created_at) VALUES ('e1','s1','skip','2026-10-05',?)", (config.now_iso(),))
        for day, want in [((2026, 10, 4), "off"), ((2026, 10, 5), "off")]:
            config.utcnow = lambda day=day: datetime(*day, 12, 0, tzinfo=UTC)
            ha_sensors.full_sync_blocking()
            self.assertEqual(self.ha.states[eid]["state"], want, day)
        attrs = self.ha.states[eid]["attributes"]
        self.assertEqual(attrs["next_date"], "2026-10-19")
        self.assertEqual(attrs["skipped_dates"], ["2026-10-05"])   # today counts as "from today on"
        # move 19 Oct to 20 Oct -> on Mon 19 and Tue 20, occurs_today only Tuesday
        with db.get_conn() as c:
            c.execute("INSERT INTO schedule_exceptions (id,item_id,kind,date,to_date,created_at) VALUES ('e2','s1','move','2026-10-19','2026-10-20',?)", (config.now_iso(),))
        for day, want, occurs in [((2026, 10, 18), "off", False), ((2026, 10, 19), "on", False), ((2026, 10, 20), "on", True), ((2026, 10, 21), "off", False)]:
            config.utcnow = lambda day=day: datetime(*day, 12, 0, tzinfo=UTC)
            ha_sensors.full_sync_blocking()
            self.assertEqual(self.ha.states[eid]["state"], want, day)
            self.assertEqual(self.ha.states[eid]["attributes"]["occurs_today"], occurs, day)
        # add Saturday 24 Oct: on Fri 23 + Sat 24
        with db.get_conn() as c:
            c.execute("INSERT INTO schedule_exceptions (id,item_id,kind,date,created_at) VALUES ('e3','s1','add','2026-10-24',?)", (config.now_iso(),))
        for day, want in [((2026, 10, 22), "off"), ((2026, 10, 23), "on"), ((2026, 10, 24), "on"), ((2026, 10, 25), "off")]:
            config.utcnow = lambda day=day: datetime(*day, 12, 0, tzinfo=UTC)
            ha_sensors.full_sync_blocking()
            self.assertEqual(self.ha.states[eid]["state"], want, day)
        config.utcnow = lambda: datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
        ha_sensors.full_sync_blocking()
        self.assertEqual(self.ha.states[eid]["attributes"]["extra_dates"], ["2026-10-24"])

    def test_push_and_delete_single(self):
        with db.get_conn() as c:
            self.make_item(c)
        self.assertTrue(ha_sensors.push_item_blocking("s1"))
        self.assertIn("binary_sensor.household_todo_trash_pickup", self.ha.states)
        self.assertTrue(ha_sensors.delete_entity_blocking("trash_pickup"))
        self.assertNotIn("binary_sensor.household_todo_trash_pickup", self.ha.states)
        self.assertTrue(ha_sensors.delete_entity_blocking("trash_pickup"))   # already gone (404) still ok
        self.assertFalse(ha_sensors.push_item_blocking("missing"))

    def test_switch_off_pushes_nothing_and_startup_cleanup(self):
        with db.get_conn() as c:
            self.make_item(c)
        self.ha.states["binary_sensor.household_todo_trash_pickup"] = {"state": "on", "attributes": {}}
        set_setting(expose_schedule_sensors=False)
        self.assertEqual(ha_sensors.full_sync_blocking(), {"pushed": 0, "failed": 0})
        self.assertFalse(ha_sensors.push_item_blocking("s1"))
        self.assertEqual(ha_sensors.remove_all_entities_blocking(), 1)
        self.assertNotIn("binary_sensor.household_todo_trash_pickup", self.ha.states)

    def test_failure_streak_aborts_after_three(self):
        with db.get_conn() as c:
            for i in range(6):
                self.make_item(c, f"s{i}", f"Item {i}")
        self.ha.fail = True
        res = ha_sensors.full_sync_blocking()
        self.assertEqual(res, {"pushed": 0, "failed": 3})
        self.assertEqual(len([r for r in self.ha.requests if r[0] == "POST"]), 3)

    def test_no_token_does_nothing(self):
        with db.get_conn() as c:
            self.make_item(c)
        config.SUPERVISOR_TOKEN = ""
        try:
            self.assertEqual(ha_sensors.full_sync_blocking(), {"pushed": 0, "failed": 0})
            self.assertEqual(self.ha.requests, [])
        finally:
            config.SUPERVISOR_TOKEN = "test-token"

    def test_timezone_from_ha(self):
        self.ha.time_zone = "America/Chicago"
        import asyncio
        asyncio.run(ha_client.load_timezone())
        self.assertEqual(config.timezone_name(), "America/Chicago")
        self.ha.time_zone = "Not/AZone"
        asyncio.run(ha_client.load_timezone())
        self.assertEqual(config.timezone_name(), "America/Chicago")     # unchanged on a bad name


class ExceptionValidation(Base):
    def item(self):
        return {"id": "s1", "rule": "weeks:1:1", "anchor_date": "2026-09-21"}

    def v(self, excs, kind, d, to=None, reason=None):
        return schedule_logic.validate_exception(self.item(), excs, kind, d, to, reason, TODAY)

    def test_valid_cases(self):
        self.assertIsNone(self.v([], "skip", "2026-10-05"))
        self.assertIsNone(self.v([], "move", "2026-10-05", "2026-10-06"))
        self.assertIsNone(self.v([], "add", "2026-10-10"))

    def test_bad_dates(self):
        self.assertIn("valid date", self.v([], "skip", "nope"))
        self.assertIn("today or later", self.v([], "skip", "2026-09-14"))
        self.assertIn("at most", self.v([], "add", "2028-01-01"))
        self.assertIn("not an occurrence", self.v([], "skip", "2026-10-06"))
        self.assertIn("to_date", self.v([], "move", "2026-10-05"))
        self.assertIn("differ", self.v([], "move", "2026-10-05", "2026-10-05"))
        self.assertIn("kind", self.v([], "delete", "2026-10-05"))
        self.assertIn("reason", self.v([], "skip", "2026-10-05", reason="x" * 61))

    def test_no_double_occurrence(self):
        self.assertIn("already an occurrence", self.v([], "move", "2026-10-05", "2026-10-12"))
        self.assertIn("already an occurrence", self.v([], "add", "2026-10-12"))
        moved = [{"id": "e", "kind": "move", "date": "2026-10-05", "to_date": "2026-10-06", "reason": None}]
        self.assertIn("already an occurrence", self.v(moved, "add", "2026-10-06"))

    def test_skipped_date_cannot_be_reused(self):
        skipped = [{"id": "e", "kind": "skip", "date": "2026-10-05", "to_date": None, "reason": None}]
        self.assertIn("undo", self.v(skipped, "add", "2026-10-05"))
        self.assertIn("undo", self.v(skipped, "move", "2026-10-12", "2026-10-05"))

    def test_replace_same_original_date(self):
        skipped = [{"id": "e", "kind": "skip", "date": "2026-10-05", "to_date": None, "reason": None}]
        self.assertIsNone(self.v(skipped, "move", "2026-10-05", "2026-10-06"))     # skip -> move
        self.assertIsNone(self.v(skipped, "skip", "2026-10-05"))                     # idempotent repeat
        moved = [{"id": "e", "kind": "move", "date": "2026-10-05", "to_date": "2026-10-06", "reason": None}]
        self.assertIsNone(self.v(moved, "move", "2026-10-05", "2026-10-07"))         # change target
        self.assertIsNone(self.v(moved, "move", "2026-10-05", "2026-10-06"))         # same again

    def test_cap(self):
        excs = [{"id": f"e{i}", "kind": "add", "date": (TODAY + timedelta(days=i + 2, weeks=0)).isoformat(), "to_date": None, "reason": None}
                for i in range(50)]
        # those add dates may coincide with Monday occurrences; pick a fresh Saturday
        msg = self.v(excs, "add", "2027-01-02")
        self.assertIn("at most 50", msg)


def add_item(c, iid, name, rule="weeks:1:2,4,5", anchor="2026-09-22", start=None, end=None, assigned=None,
             visibility="household", expose=1, place=None, lead=1, notes=None):
    c.execute("INSERT INTO schedule_items (id,name,notes,rule,anchor_date,lead_days,entity_slug,created_at,"
              "assigned_to,start_time,end_time,place_id,visibility,expose_sensor) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
              (iid, name, notes, rule, anchor, lead, schedule_logic.make_slug(c, name), config.now_iso(),
               assigned, start, end, place, visibility, expose))


def add_exc(c, eid, item, kind, d, to=None):
    c.execute("INSERT INTO schedule_exceptions (id,item_id,kind,date,to_date,created_at) VALUES (?,?,?,?,?,?)",
              (eid, item, kind, d, to, config.now_iso()))


def at(y, m, d, hh=12, mm=0):
    return lambda: datetime(y, m, d, hh, mm, tzinfo=UTC)


class PersonalScheduleSchema(Base):
    """Personal-schedule columns: an older database migrates to exactly the fresh schema,
    and its items stay household, all-day and published."""

    def test_migration_from_01x_keeps_items_unchanged(self):
        with db.get_conn() as c:
            fresh = [(r["name"], r["type"], r["notnull"], r["dflt_value"]) for r in c.execute("PRAGMA table_info(schedule_items)")]
            c.execute("DROP TABLE schedule_reminder_log")
            c.execute("DROP TABLE schedule_exceptions")
            c.execute("DROP TABLE schedule_items")
            c.execute("""CREATE TABLE schedule_items (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, notes TEXT, rule TEXT NOT NULL, anchor_date TEXT NOT NULL,
                lead_days INTEGER NOT NULL DEFAULT 1 CHECK (lead_days BETWEEN 0 AND 7), icon TEXT,
                entity_slug TEXT NOT NULL UNIQUE, created_by TEXT REFERENCES users(id), created_at TEXT NOT NULL)""")
            c.execute("INSERT INTO schedule_items (id,name,rule,anchor_date,lead_days,entity_slug,created_at) "
                      "VALUES ('s1','Trash pickup','weeks:1:1','2026-09-21',1,'trash_pickup',?)", (config.now_iso(),))
        db.init_db()
        with db.get_conn() as c:
            migrated = [(r["name"], r["type"], r["notnull"], r["dflt_value"]) for r in c.execute("PRAGMA table_info(schedule_items)")]
            self.assertEqual(migrated, fresh)
            row = dict(c.execute("SELECT * FROM schedule_items WHERE id='s1'").fetchone())
            self.assertIn("schedule_reminder_log", {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")})
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute("UPDATE schedule_items SET visibility='secret' WHERE id='s1'")
        self.assertEqual((row["assigned_to"], row["start_time"], row["end_time"], row["place_id"], row["visibility"], row["expose_sensor"]),
                         (None, None, None, None, "household", 1))
        ha_sensors.full_sync_blocking()
        st = self.ha.states["binary_sensor.household_todo_trash_pickup"]
        self.assertEqual(st["state"], "on")                                     # all-day rule unchanged: Monday pickup
        self.assertEqual((st["attributes"]["lead_days"], st["attributes"]["next_start"]), (1, None))

    def test_place_deletion_sets_null_and_log_cascades(self):
        with db.get_conn() as c:
            add_user(c, "u1", "Ann")
            c.execute("INSERT INTO places (id,name,address,created_at) VALUES ('pl1','Studio','5 Elm',?)", (config.now_iso(),))
            add_item(c, "s1", "Yoga", start="18:00", end="19:00", assigned="u1", place="pl1")
            c.execute("INSERT INTO schedule_reminder_log (id,item_id,user_id,minutes_before,date,start_time,created_at) "
                      "VALUES ('r1','s1','u1',60,'2026-09-22','18:00',?)", (config.now_iso(),))
            c.execute("DELETE FROM places WHERE id='pl1'")
            self.assertIsNone(c.execute("SELECT place_id FROM schedule_items WHERE id='s1'").fetchone()[0])
            c.execute("DELETE FROM schedule_items WHERE id='s1'")
            self.assertEqual(c.execute("SELECT COUNT(*) FROM schedule_reminder_log").fetchone()[0], 0)

    def test_visibility_rules(self):
        private = {"visibility": "private", "assigned_to": "u1"}
        household = {"visibility": "household", "assigned_to": "u1"}
        self.assertTrue(schedule_logic.is_visible(private, "u1"))
        self.assertFalse(schedule_logic.is_visible(private, "u2"))
        self.assertFalse(schedule_logic.can_edit(private, "u2"))
        self.assertTrue(schedule_logic.is_visible(household, "u2"))
        self.assertTrue(schedule_logic.can_edit(household, "u2"))

    def test_housekeeping_prunes_old_reminder_log(self):
        with db.get_conn() as c:
            add_user(c, "u1", "Ann")
            add_item(c, "s1", "Yoga", start="18:00", end="19:00", assigned="u1")
            for rid, d in (("old", "2026-09-20"), ("today", "2026-09-21")):
                c.execute("INSERT INTO schedule_reminder_log (id,item_id,user_id,minutes_before,date,start_time,created_at) "
                          "VALUES (?,'s1','u1',60,?,'18:00',?)", (rid, d, config.now_iso()))
        housekeeping.run_pass_blocking(TODAY)
        with db.get_conn() as c:
            self.assertEqual([r[0] for r in c.execute("SELECT id FROM schedule_reminder_log")], ["today"])


class TimedSensors(Base):
    """A timed item's sensor is on only from start to end on each effective
    date; lead_days is ignored (SPEC §7.2)."""

    EID = "binary_sensor.household_todo_yoga"

    def setUp(self):
        super().setUp()
        ha_sensors._last_state.clear()
        ha_sensors._cleared_unpublished.clear()
        with db.get_conn() as c:
            add_user(c, "u1", "Ann")
            add_item(c, "s1", "Yoga", start="18:00", end="19:00", assigned="u1", lead=3)   # Tue, Thu & Fri

    def state_at(self, when):
        config.utcnow = when
        ha_sensors.full_sync_blocking()
        return self.ha.states[self.EID]["state"]

    def test_window_and_lead_days_ignored(self):
        self.assertEqual(self.state_at(at(2026, 9, 21, 12)), "off")        # Monday: lead_days 3 would say on
        self.assertEqual(self.state_at(at(2026, 9, 22, 17, 59)), "off")
        self.assertEqual(self.state_at(at(2026, 9, 22, 18, 0)), "on")
        self.assertEqual(self.state_at(at(2026, 9, 22, 18, 59)), "on")
        self.assertEqual(self.state_at(at(2026, 9, 22, 19, 0)), "off")
        a = self.ha.states[self.EID]["attributes"]
        self.assertEqual((a["assigned_to"], a["start_time"], a["end_time"]), ("Ann", "18:00", "19:00"))
        self.assertEqual((a["next_date"], a["occurs_today"]), ("2026-09-22", True))          # date meaning kept
        self.assertEqual((a["next_start"], a["next_end"]), ("2026-09-24T18:00:00+00:00", "2026-09-24T19:00:00+00:00"))

    def test_skip_move_and_extra_dates(self):
        with db.get_conn() as c:
            add_exc(c, "e1", "s1", "skip", "2026-09-24")
            add_exc(c, "e2", "s1", "move", "2026-09-25", "2026-09-26")
            add_exc(c, "e3", "s1", "add", "2026-09-27")
        self.assertEqual(self.state_at(at(2026, 9, 24, 12)), "off")
        self.assertEqual(self.ha.states[self.EID]["attributes"]["next_start"], "2026-09-26T18:00:00+00:00")
        self.assertEqual(self.state_at(at(2026, 9, 24, 18, 30)), "off")    # skipped
        self.assertEqual(self.state_at(at(2026, 9, 25, 18, 30)), "off")    # moved away
        self.assertEqual(self.state_at(at(2026, 9, 26, 18, 30)), "on")     # moved here, same times
        self.assertEqual(self.state_at(at(2026, 9, 27, 18, 30)), "on")     # extra date
        self.assertEqual(self.state_at(at(2026, 9, 28, 18, 30)), "off")

    def test_window_uses_ha_time_zone(self):
        config.set_timezone("Etc/GMT+6")                                    # UTC-6 (POSIX sign)
        self.assertEqual(self.state_at(at(2026, 9, 22, 18, 30)), "off")    # 12:30 local
        self.assertEqual(self.state_at(at(2026, 9, 23, 0, 30)), "on")      # 18:30 on Tue 22 local
        self.assertEqual(self.ha.states[self.EID]["attributes"]["next_start"], "2026-09-22T18:00:00-06:00")

    def test_loop_pushes_only_when_the_window_opens_or_closes(self):
        with db.get_conn() as c:
            add_item(c, "s2", "Trash", rule="weeks:1:1", anchor="2026-09-21")          # all-day: left to full syncs
            add_item(c, "s3", "Hidden", start="18:00", end="19:00", expose=0)
        self.assertEqual(self.state_at(at(2026, 9, 22, 17, 0)), "off")
        posts = lambda: [r for r in self.ha.requests if r[0] == "POST" and r[1].startswith("/api/states/")]  # noqa: E731
        self.ha.requests.clear()
        config.utcnow = at(2026, 9, 22, 17, 30)
        self.assertEqual(ha_sensors.push_timed_changes_blocking(), 0)
        config.utcnow = at(2026, 9, 22, 18, 0)
        self.ha.fail = True
        self.assertEqual(ha_sensors.push_timed_changes_blocking(), 0)       # failed: not recorded ...
        self.ha.fail = False
        self.assertEqual(ha_sensors.push_timed_changes_blocking(), 1)       # ... so retried next tick
        self.assertEqual(self.ha.states[self.EID]["state"], "on")
        config.utcnow = at(2026, 9, 22, 18, 30)
        self.assertEqual(ha_sensors.push_timed_changes_blocking(), 0)
        config.utcnow = at(2026, 9, 22, 19, 0)
        self.assertEqual(ha_sensors.push_timed_changes_blocking(), 1)
        self.assertEqual(self.ha.states[self.EID]["state"], "off")
        self.assertEqual({r[1] for r in posts()}, {"/api/states/" + self.EID})
        self.assertNotIn("binary_sensor.household_todo_hidden", self.ha.states)

    def test_unpublished_item_never_pushed(self):
        with db.get_conn() as c:
            c.execute("UPDATE schedule_items SET expose_sensor = 0")
        self.assertEqual(ha_sensors.full_sync_blocking(), {"pushed": 0, "failed": 0})
        self.assertFalse(ha_sensors.push_item_blocking("s1"))
        # never POSTed; its entity is DELETEd once (in case switching "Publish"
        # off didn't manage to remove it), then left alone
        self.assertEqual([(m, p) for m, p, _, _ in self.ha.requests],
                         [("DELETE", "/api/states/binary_sensor.household_todo_yoga")])
        self.ha.requests.clear()
        ha_sensors.full_sync_blocking()
        self.assertEqual(self.ha.requests, [])
        # published again -> pushed, and a later unpublish is cleaned up again
        with db.get_conn() as c:
            c.execute("UPDATE schedule_items SET expose_sensor = 1")
        self.assertTrue(ha_sensors.push_item_blocking("s1"))
        with db.get_conn() as c:
            c.execute("UPDATE schedule_items SET expose_sensor = 0")
        self.ha.requests.clear()
        ha_sensors.full_sync_blocking()
        self.assertEqual([m for m, *_ in self.ha.requests], ["DELETE"])


class PrivateOnlyByOwner(Base):
    """Only the person an item is for can make it private."""

    def setUp(self):
        super().setUp()
        with db.get_conn() as c:
            add_user(c, "u1", "Ann")
            add_user(c, "u2", "Ben")

    def _create(self, acting, **body):
        from app.routers import schedule as sched
        from fastapi import BackgroundTasks
        return sched.create_item(BackgroundTasks(), body={"name": "Yoga", "rule": "weeks:1:2,4,5",
                                 "anchor_date": "2026-09-22", **body}, acting=acting)

    def test_someone_else_cannot_make_it_private(self):
        ben = {"id": "u2", "name": "Ben", "is_admin": False}
        with self.assertRaises(HTTPException) as cm:
            self._create(ben, assigned_to="u1", visibility="private")
        self.assertEqual(cm.exception.status_code, 422)
        item = self._create(ben, assigned_to="u1")            # household item for Ann: fine
        from app.routers import schedule as sched
        from fastapi import BackgroundTasks
        with self.assertRaises(HTTPException) as cm:
            sched.update_item(item["id"], BackgroundTasks(), body={"visibility": "private"}, acting=ben)
        self.assertEqual(cm.exception.status_code, 422)

    def test_owner_can_make_it_private(self):
        ann = {"id": "u1", "name": "Ann", "is_admin": False}
        item = self._create(ann, assigned_to="u1", visibility="private")
        self.assertEqual(item["visibility"], "private")
        self.assertFalse(item["exposeSensor"])                  # private items default to unpublished


class ScheduleReminders(Base):
    """Digest, weekly and "N before" for schedule items assigned to someone —
    private ones included, and only ever to the assignee (SPEC §8.1)."""

    def setUp(self):
        super().setUp()
        drive_on()
        link("ann", "mobile_app_ann")
        link("bob", "mobile_app_bob")
        with db.get_conn() as c:
            add_user(c, "u1", "Ann", "ann")
            add_user(c, "u2", "Bob", "bob")
            add_list(c, "shared", "Household")
            for u in ("u1", "u2"):
                c.execute("INSERT INTO user_prefs (user_id, notifications_enabled, lead_days, notify_on_assign) VALUES (?,1,1,1)", (u,))
            c.execute("INSERT INTO places (id,name,address,drive_minutes,created_at) VALUES ('pl1','Studio','5 Elm St',12,?)", (config.now_iso(),))
            # Ann's private yoga, Mon & Tue 18:00–19:00 at the studio; a household chore assigned to her;
            # and an unassigned household item nobody is ever notified about
            add_item(c, "yoga", "Yoga", rule="weeks:1:1,2", anchor="2026-09-21", start="18:00", end="19:00",
                     assigned="u1", visibility="private", expose=0, place="pl1", notes="SECRET NOTE")
            add_item(c, "bins", "Bins duty", rule="weeks:1:1", anchor="2026-09-21", assigned="u1")
            add_item(c, "recycling", "Recycling", rule="daily", anchor="2026-09-21")

    def sent(self):
        out = []

        def fake(service, title, message):
            out.append((service, title, message))
            return True
        return out, fake

    def to(self, out, service):
        return [m for s, t, m in out if s == service]

    def test_digest_mixes_items_with_tasks(self):
        config.utcnow = at(2026, 9, 21, 8, 5)
        with db.get_conn() as c:
            add_task(c, "t1", "shared", "Dentist", assigned_to="u1", due_date="2026-09-21", due_time="14:30")
            add_task(c, "t2", "shared", "Bob's thing", assigned_to="u2", due_date="2026-09-21")
        out, fake = self.sent()
        self.assertEqual(reminders.run_digest_pass_blocking(sender=fake), 2)
        self.assertEqual(self.to(out, "mobile_app_ann")[0].split("\n"), [
            "• Bins duty — today",
            "• Dentist — today 14:30",
            "• Yoga — today 18:00–19:00 @ Studio · ~12 min drive, leave by 17:48",
            "• Yoga — tomorrow 18:00–19:00 @ Studio · ~12 min drive, leave by 17:48",
        ])
        self.assertEqual(self.to(out, "mobile_app_bob"), ["• Bob's thing — today"])   # nothing of Ann's
        self.assertNotIn("SECRET", str(out))
        self.assertNotIn("5 Elm", str(out))

    def test_digest_follows_skips_and_moves(self):
        config.utcnow = at(2026, 9, 21, 8, 5)
        with db.get_conn() as c:
            add_exc(c, "e1", "yoga", "skip", "2026-09-21")
            add_exc(c, "e2", "bins", "move", "2026-09-21", "2026-09-22")
        out, fake = self.sent()
        reminders.run_digest_pass_blocking(sender=fake)
        self.assertEqual(self.to(out, "mobile_app_ann")[0].split("\n"), [
            "• Bins duty — tomorrow",
            "• Yoga — tomorrow 18:00–19:00 @ Studio · ~12 min drive, leave by 17:48",
        ])

    def test_weekly_summary(self):
        config.utcnow = at(2026, 9, 21, 12, 5)
        with db.get_conn() as c:
            c.execute("INSERT INTO user_weekly_prefs (user_id, enabled, day_of_week, time) VALUES ('u1',1,1,'12:00')")
            add_task(c, "t1", "shared", "Pay bill", assigned_to="u1", due_date="2026-09-23")
            add_exc(c, "e1", "yoga", "move", "2026-09-28", "2026-09-26")
        out, fake = self.sent()
        self.assertEqual(reminders.run_weekly_pass_blocking(sender=fake), 1)
        self.assertEqual(self.to(out, "mobile_app_ann")[0].split("\n"), [
            "• Yoga — tomorrow 18:00–19:00 @ Studio · ~12 min drive, leave by 17:48",
            "• Pay bill — in 2 days",
            "• Yoga — in 5 days 18:00–19:00 @ Studio · ~12 min drive, leave by 17:48",   # moved from Mon 28
            "• Bins duty — in 7 days",
        ])
        self.assertEqual(self.to(out, "mobile_app_bob"), [])

    def test_n_before_start(self):
        with db.get_conn() as c:
            for u in ("u1", "u2"):
                c.execute("INSERT INTO user_reminder_offsets (id, user_id, minutes_before, created_at) VALUES (?, ?, 60, ?)",
                          (f"o_{u}", u, config.now_iso()))
        out, fake = self.sent()
        run = reminders.run_schedule_reminder_pass_blocking
        config.utcnow = at(2026, 9, 21, 16, 59)
        self.assertEqual(run(sender=fake), 0)
        config.utcnow = at(2026, 9, 21, 17, 0)
        self.assertEqual(run(sender=fake), 1)
        self.assertEqual(out[-1], ("mobile_app_ann", "Household Todo",
                                   "Yoga — starts in 1 hour (18:00–19:00) @ Studio · ~12 min drive, leave by 17:48"))
        self.assertEqual(run(sender=fake), 0)                                # deduplicated
        config.utcnow = at(2026, 9, 21, 18, 0)
        self.assertEqual(run(sender=fake), 0)                                # started: moot
        # changing the times re-arms today's reminder
        with db.get_conn() as c:
            c.execute("UPDATE schedule_items SET start_time='18:30', end_time='19:30' WHERE id='yoga'")
        config.utcnow = at(2026, 9, 21, 18, 5)
        self.assertEqual(run(sender=fake), 1)
        self.assertIn("starts in 1 hour (18:30–19:30)", out[-1][2])
        # moving Tuesday's class to Wednesday moves its reminder too
        with db.get_conn() as c:
            add_exc(c, "e1", "yoga", "move", "2026-09-22", "2026-09-23")
        config.utcnow = at(2026, 9, 22, 17, 30)
        self.assertEqual(run(sender=fake), 0)
        config.utcnow = at(2026, 9, 23, 17, 30)
        self.assertEqual(run(sender=fake), 1)
        self.assertEqual(self.to(out, "mobile_app_bob"), [])                  # never someone else's item
        self.assertNotIn("SECRET", str(out))

    def test_gates_apply(self):
        # the digest off doesn't stop "before it starts" reminders (the reported bug); disabling does
        with db.get_conn() as c:
            c.execute("INSERT INTO user_reminder_offsets (id, user_id, minutes_before, created_at) VALUES ('o1','u1',60,?)", (config.now_iso(),))
            c.execute("UPDATE user_prefs SET notifications_enabled = 0 WHERE user_id = 'u1'")
        out, fake = self.sent()
        config.utcnow = at(2026, 9, 21, 17, 0)
        self.assertEqual(reminders.run_schedule_reminder_pass_blocking(sender=fake), 1)
        config.utcnow = at(2026, 9, 21, 8, 5)
        self.assertEqual(reminders.run_digest_pass_blocking(sender=fake), 0)
        with db.get_conn() as c:
            c.execute("DELETE FROM schedule_reminder_log")
            c.execute("UPDATE user_prefs SET notifications_enabled = 1 WHERE user_id = 'u1'")
            c.execute("UPDATE users SET disabled = 1 WHERE id = 'u1'")
        config.utcnow = at(2026, 9, 21, 17, 0)
        self.assertEqual(reminders.run_schedule_reminder_pass_blocking(sender=fake), 0)

    def test_assignment_ping_detail(self):
        out, fake = self.sent()
        self.assertTrue(reminders.send_assignment_ping_blocking("u2", "Ann", "Yoga", None, sender=fake,
                                                                detail="Every Tue, Thu & Fri, 18:00–19:00"))
        self.assertEqual(out[-1][2], "Ann assigned you: Yoga (Every Tue, Thu & Fri, 18:00–19:00)")


class Serialization(Base):
    def test_task_json_flags(self):
        with db.get_conn() as c:
            add_user(c, "u1", "Ann")
            add_list(c, "l1", "S")
            add_task(c, "req", "l1", due_date="2026-09-18", completion_required=1)
            add_task(c, "opt", "l1", due_date="2026-09-18", completion_required=0)
            add_task(c, "today", "l1", due_date="2026-09-21", completion_required=1)
            add_task(c, "undated", "l1", completion_required=1)
            add_task(c, "doneold", "l1", due_date="2026-09-01", completion_required=1, completed=1, completed_at=config.now_iso())
            rows = c.execute(taskview.TASK_SELECT).fetchall()
            js = {t["id"]: t for t in taskview.serialize_tasks(c, rows, TODAY)}
        self.assertTrue(js["req"]["overdue"]); self.assertFalse(js["req"]["past"])
        self.assertFalse(js["opt"]["overdue"]); self.assertTrue(js["opt"]["past"])
        self.assertFalse(js["today"]["overdue"])
        self.assertFalse(js["undated"]["overdue"]); self.assertFalse(js["undated"]["past"])
        self.assertFalse(js["doneold"]["overdue"]); self.assertFalse(js["doneold"]["past"])

    def test_sorts(self):
        def T(i, **kw):
            base = {"id": i, "position": 0, "createdAt": "2026-01-0%dT00:00:00" % int(i[-1]), "dueDate": None, "dueTime": None,
                    "priority": None, "assigneeName": None, "completedAt": None}
            base.update(kw)
            return base
        tasks = [T("a1", dueDate="2026-09-22", dueTime="09:00"), T("a2", dueDate="2026-09-22"), T("a3"),
                 T("a4", dueDate="2026-09-21", priority="low"), T("a5", priority="high"), T("a6", priority="medium", assigneeName="Zed"),
                 T("a7", assigneeName="Amy")]
        ids = lambda s: [t["id"] for t in taskview.sort_tasks(tasks, s)]  # noqa: E731
        self.assertEqual(ids("due"), ["a4", "a2", "a1", "a3", "a5", "a6", "a7"])
        self.assertEqual(ids("priority"), ["a5", "a6", "a4", "a1", "a2", "a3", "a7"])
        self.assertEqual(ids("assignee"), ["a7", "a6", "a1", "a2", "a3", "a4", "a5"])
        self.assertEqual(ids("created")[0], "a7")


class PrefsDigestTime(Base):
    """A per-user daily-digest time, validated the same way
    as the existing weeklySummary.time field."""

    USER = {"id": "u1", "name": "Ann", "username": "ann", "is_admin": False}

    def setUp(self):
        super().setUp()
        with db.get_conn() as c:
            add_user(c, "u1", "Ann", "ann")
        link("ann", "mobile_app_ann")

    def test_default_is_0800(self):
        # every person has their own time; 08:00 until they pick one
        p = prefs_router.get_prefs(user=self.USER)
        self.assertEqual(p["digestTime"], "08:00")
        p = prefs_router.put_prefs(body={"leadDays": 1}, user=self.USER)   # first save stores it
        self.assertEqual(p["digestTime"], "08:00")
        with db.get_conn() as c:
            self.assertEqual(c.execute("SELECT digest_time FROM user_prefs WHERE user_id='u1'").fetchone()[0], "08:00")

    def test_set_and_persist_custom_time(self):
        p = prefs_router.put_prefs(body={"digestTime": "07:15"}, user=self.USER)
        self.assertEqual(p["digestTime"], "07:15")
        self.assertEqual(prefs_router.get_prefs(user=self.USER)["digestTime"], "07:15")

    def test_invalid_time_rejected(self):
        for bad in ("7:15", "25:00", "07:60", "noon", 715):
            with self.assertRaises(HTTPException) as cm:
                prefs_router.put_prefs(body={"digestTime": bad}, user=self.USER)
            self.assertEqual(cm.exception.status_code, 422, bad)

    def test_null_resets_to_0800(self):
        prefs_router.put_prefs(body={"digestTime": "07:15"}, user=self.USER)
        p = prefs_router.put_prefs(body={"digestTime": None}, user=self.USER)
        self.assertEqual(p["digestTime"], "08:00")

    def test_other_fields_unaffected(self):
        prefs_router.put_prefs(body={"digestTime": "07:15"}, user=self.USER)
        p = prefs_router.put_prefs(body={"leadDays": 3}, user=self.USER)
        self.assertEqual(p["leadDays"], 3)
        self.assertEqual(p["digestTime"], "07:15")   # untouched by an unrelated update


class DriveTimeEstimation(Base):
    """§8n — estimated drive time from home, geocoded/routed in the
    background and cached on each place; a no-op unless both
    home_address and osrm_url are configured and home geocodes."""

    HOME = (45.0, 7.0)   # invented coordinates

    def setUp(self):
        super().setUp()
        drive_on()
        self._orig_home = geocode.HOME_LATLON
        geocode.HOME_LATLON = None

    def tearDown(self):
        geocode.HOME_LATLON = self._orig_home
        super().tearDown()

    def enable(self):
        geocode.HOME_LATLON = self.HOME
        set_setting(osrm_url="http://osrm.example")

    def add_place(self, pid="p1", address="1 Main St"):
        with db.get_conn() as c:
            c.execute(
                "INSERT INTO places (id, name, address, created_at) VALUES (?, 'X', ?, ?)",
                (pid, address, config.now_iso()),
            )

    def test_blank_osrm_url_uses_public_default(self):
        # osrm_url is an App setting; blank has always meant "use the
        # public server", so there is no "routing not configured" state.
        set_setting(osrm_url="")
        self.assertEqual(settings.osrm_url(), "https://router.project-osrm.org")
        geocode.HOME_LATLON = self.HOME
        self.assertTrue(geocode.feature_enabled())

    def test_disabled_without_home(self):
        set_setting(osrm_url="http://osrm.example")   # routing configured, but home never geocoded
        self.add_place()
        self.assertEqual(drive_time.run_pass_blocking(), 0)

    def test_computes_and_caches(self):
        self.enable()
        self.add_place()
        n = drive_time.run_pass_blocking(
            geocode_fn=lambda addr: (45.75, 7.99),
            route_fn=lambda *a, **k: (18, True),
        )
        self.assertEqual(n, 1)
        with db.get_conn() as c:
            row = c.execute("SELECT lat, lon, drive_minutes, drive_checked_at FROM places WHERE id = 'p1'").fetchone()
        self.assertEqual((row["lat"], row["lon"], row["drive_minutes"]), (45.75, 7.99, 18))
        self.assertIsNotNone(row["drive_checked_at"])

    def test_failed_geocode_records_attempt_without_a_result(self):
        self.enable()
        self.add_place()
        n = drive_time.run_pass_blocking(geocode_fn=lambda addr: None, route_fn=lambda *a, **k: (99, True))
        self.assertEqual(n, 1)
        with db.get_conn() as c:
            row = c.execute("SELECT lat, drive_minutes, drive_checked_at FROM places WHERE id = 'p1'").fetchone()
        self.assertIsNone(row["lat"])
        self.assertIsNone(row["drive_minutes"])
        self.assertIsNotNone(row["drive_checked_at"])   # so it isn't retried every tick

    def test_failed_lookup_not_retried_inside_cooldown(self):
        self.enable()
        self.add_place()
        drive_time.run_pass_blocking(geocode_fn=lambda addr: None)
        # Immediately after a failed attempt, the 1-hour retry cooldown means
        # nothing is due yet.
        n = drive_time.run_pass_blocking(geocode_fn=lambda addr: (45.75, 7.99), route_fn=lambda *a, **k: (18, True))
        self.assertEqual(n, 0)

    def test_failed_lookup_retried_after_cooldown(self):
        self.enable()
        self.add_place()
        drive_time.run_pass_blocking(geocode_fn=lambda addr: None)
        with db.get_conn() as c:
            c.execute("UPDATE places SET drive_checked_at = ? WHERE id = 'p1'", (config.days_ago_iso(1),))
        n = drive_time.run_pass_blocking(geocode_fn=lambda addr: (45.75, 7.99), route_fn=lambda *a, **k: (18, True))
        self.assertEqual(n, 1)

    def test_successful_result_refreshed_after_stale_period(self):
        self.enable()
        self.add_place()
        drive_time.run_pass_blocking(geocode_fn=lambda addr: (45.75, 7.99), route_fn=lambda *a, **k: (18, True))
        # Fresh — a second pass right away has nothing due.
        self.assertEqual(drive_time.run_pass_blocking(geocode_fn=lambda addr: (0, 0), route_fn=lambda *a, **k: (1, True)), 0)
        with db.get_conn() as c:
            c.execute("UPDATE places SET drive_checked_at = ? WHERE id = 'p1'", (config.days_ago_iso(31),))
        n = drive_time.run_pass_blocking(geocode_fn=lambda addr: (46.0, 8.0), route_fn=lambda *a, **k: (22, True))
        self.assertEqual(n, 1)
        with db.get_conn() as c:
            self.assertEqual(c.execute("SELECT drive_minutes FROM places WHERE id = 'p1'").fetchone()[0], 22)

    def test_only_places_per_tick_processed_at_once(self):
        self.enable()
        self.add_place("p1", "1 Main St")
        self.add_place("p2", "2 Elm Ave")
        n = drive_time.run_pass_blocking(geocode_fn=lambda addr: (45.75, 7.99), route_fn=lambda *a, **k: (18, True))
        self.assertEqual(n, drive_time.PLACES_PER_TICK)

    def test_editing_address_clears_the_cache(self):
        self.enable()
        with db.get_conn() as c:
            add_user(c, "u1", "Ann")
        p = places_router.create_place(body={"name": "X", "address": "1 Main St"}, acting={"id": "u1"})
        with db.get_conn() as c:
            c.execute("UPDATE places SET lat = 1.0, lon = 2.0, drive_minutes = 5, drive_checked_at = ? WHERE id = ?",
                      (config.now_iso(), p["id"]))
        updated = places_router.update_place(p["id"], body={"address": "2 Elm Ave"}, acting={"id": "u1"})
        self.assertIsNone(updated["driveMinutes"])
        with db.get_conn() as c:
            row = c.execute("SELECT lat, lon, drive_minutes, drive_checked_at FROM places WHERE id = ?", (p["id"],)).fetchone()
        self.assertIsNone(row["lat"]); self.assertIsNone(row["lon"])
        self.assertIsNone(row["drive_minutes"]); self.assertIsNone(row["drive_checked_at"])

    def test_task_place_exposes_drive_minutes(self):
        with db.get_conn() as c:
            add_user(c, "u1", "Ann")
            add_list(c, "l1", "Shared")
            c.execute("INSERT INTO places (id, name, address, drive_minutes, created_at) VALUES ('pl1','Clinic','1 Rd',18,?)", (config.now_iso(),))
            add_task(c, "t1", "l1", "Checkup", due_date="2026-09-21", due_time="14:00", place_id="pl1")
            rows = c.execute(taskview.TASK_SELECT + " WHERE t.id = 't1'").fetchall()
            js = taskview.serialize_tasks(c, rows, TODAY)
        self.assertEqual(js[0]["place"]["driveMinutes"], 18)


class DriveNote(unittest.TestCase):
    """reminders._drive_note — the " · ~N min drive, leave by HH:MM" suffix
    appended to digest/weekly-summary lines and per-task reminder messages."""

    def setUp(self):
        p = mock.patch.object(settings, "drive_times_enabled", lambda: True)
        p.start()
        self.addCleanup(p.stop)

    def test_blank_while_drive_times_are_off(self):
        with mock.patch.object(settings, "drive_times_enabled", lambda: False):
            self.assertEqual(reminders._drive_note("2026-09-21", "14:30", 18), "")

    def test_appends_leave_by_when_place_and_time_and_drive_known(self):
        self.assertEqual(
            reminders._drive_note("2026-09-21", "14:30", 18),
            " · ~18 min drive, leave by 14:12",
        )

    def test_blank_without_due_time(self):
        self.assertEqual(reminders._drive_note("2026-09-21", None, 18), "")

    def test_blank_without_drive_minutes(self):
        self.assertEqual(reminders._drive_note("2026-09-21", "14:30", None), "")

    def test_blank_on_bad_input(self):
        self.assertEqual(reminders._drive_note("not-a-date", "14:30", 18), "")




class _FakeUrlopenResponse:
    """Minimal stand-in for the object urllib.request.urlopen()'s context
    manager yields — just enough of it (read(), used as a `with` block) for
    geocode.py's _geocode_once."""

    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def read(self):
        return self._body


class GeocodeUnitStripping(unittest.TestCase):
    """geocode._strip_unit and geocode_blocking's retry-without-unit-suffix
    fallback (§8n follow-up) — a suite/unit/apartment designator like "#150"
    is common on real addresses (office parks, apartment buildings) but OSM's
    data is usually only granular to the street address, so Nominatim often
    returns zero results for an address that's otherwise entirely correct.
    The addresses are invented data."""

    def test_strips_hash_suite_number(self):
        self.assertEqual(
            geocode._strip_unit("12 Example Way #150, Springfield 12345"),
            "12 Example Way, Springfield 12345",
        )

    def test_strips_word_suite(self):
        self.assertEqual(
            geocode._strip_unit("123 Main St, Suite 200, Springfield 12345"),
            "123 Main St, Springfield 12345",
        )

    def test_strips_apartment_with_no_leading_comma(self):
        self.assertEqual(
            geocode._strip_unit("123 Main St Apt 4B, Springfield"),
            "123 Main St, Springfield",
        )

    def test_returns_none_when_nothing_to_strip(self):
        self.assertIsNone(geocode._strip_unit("123 Main St, Springfield"))

    def test_geocode_blocking_retries_without_unit_when_first_lookup_is_empty(self):
        calls = []

        def fake_urlopen(req, timeout=10):
            calls.append(req.full_url)
            if len(calls) == 1:
                return _FakeUrlopenResponse(b"[]")  # Nominatim found nothing for "#150"
            return _FakeUrlopenResponse(b'[{"lat": "45.5186", "lon": "7.7614"}]')

        with mock.patch("app.geocode.urllib.request.urlopen", side_effect=fake_urlopen),              mock.patch("app.geocode._throttle"):
            result = geocode.geocode_blocking("12 Example Way #150, Springfield 12345")

        self.assertEqual(result, (45.5186, 7.7614))
        self.assertEqual(len(calls), 2)
        self.assertIn("%23150", calls[0])  # first attempt kept the "#150"
        self.assertNotIn("150", urllib.parse.unquote(calls[1]).replace("12345", ""))

    def test_geocode_blocking_returns_none_when_both_attempts_are_empty(self):
        with mock.patch("app.geocode.urllib.request.urlopen", return_value=_FakeUrlopenResponse(b"[]")),              mock.patch("app.geocode._throttle"):
            result = geocode.geocode_blocking("12 Example Way #150, Springfield 12345")
        self.assertIsNone(result)

    def test_geocode_blocking_does_not_retry_when_address_has_no_unit(self):
        calls = []

        def fake_urlopen(req, timeout=10):
            calls.append(req.full_url)
            return _FakeUrlopenResponse(b"[]")

        with mock.patch("app.geocode.urllib.request.urlopen", side_effect=fake_urlopen),              mock.patch("app.geocode._throttle"):
            result = geocode.geocode_blocking("123 Main St, Springfield")

        self.assertIsNone(result)
        self.assertEqual(len(calls), 1)  # nothing to strip, so no second attempt

    def test_geocode_blocking_succeeds_immediately_without_needing_a_retry(self):
        calls = []

        def fake_urlopen(req, timeout=10):
            calls.append(req.full_url)
            return _FakeUrlopenResponse(b'[{"lat": "45.75", "lon": "7.99"}]')

        with mock.patch("app.geocode.urllib.request.urlopen", side_effect=fake_urlopen),              mock.patch("app.geocode._throttle"):
            result = geocode.geocode_blocking("1600 Pennsylvania Ave, Washington, DC")

        self.assertEqual(result, (45.75, 7.99))
        self.assertEqual(len(calls), 1)


class AvoidTolls(Base):
    """§8n avoid_tolls — OSRM's exclude=toll, with a fallback to the fastest
    route when tolls can't be avoided, and re-estimation when the setting
    changes."""

    def setUp(self):
        super().setUp()
        self._orig = geocode.HOME_LATLON
        geocode.HOME_LATLON = (45.0, 7.0)
        set_setting(drive_times_enabled=True, osrm_url="http://osrm.example", avoid_tolls=True)
        with db.get_conn() as c:
            c.execute("INSERT INTO places (id, name, address, created_at) VALUES ('p1', 'X', '1 Main St', ?)",
                      (config.now_iso(),))

    def tearDown(self):
        geocode.HOME_LATLON = self._orig
        super().tearDown()

    def _fake_osrm(self, toll_free_minutes=None, fastest_minutes=15):
        """urlopen stand-in: `exclude=toll` requests answer with
        toll_free_minutes (None = OSRM's HTTP 400 NoRoute), others with
        fastest_minutes. Records every URL asked for."""
        import urllib.error
        urls = []

        def fake(url, timeout=10):
            urls.append(url)
            minutes = toll_free_minutes if "exclude=toll" in url else fastest_minutes
            if minutes is None:
                raise urllib.error.HTTPError(url, 400, "Bad Request", {}, None)
            body = ('{"code": "Ok", "routes": [{"duration": %d}]}' % (minutes * 60)).encode()
            return _FakeUrlopenResponse(body)
        return fake, urls

    def test_route_excludes_tolls_when_asked(self):
        fake, urls = self._fake_osrm(toll_free_minutes=22)
        with mock.patch("app.geocode.urllib.request.urlopen", side_effect=fake):
            self.assertEqual(geocode.route_blocking("http://osrm.example", (1, 2), (3, 4), avoid_tolls=True), (22, True))
        self.assertEqual(len(urls), 1)
        self.assertIn("exclude=toll", urls[0])
        self.assertIn("/route/v1/driving/2,1;4,3", urls[0])   # lon,lat order

    def test_falls_back_to_fastest_route_when_tolls_unavoidable(self):
        fake, urls = self._fake_osrm(toll_free_minutes=None, fastest_minutes=15)
        with mock.patch("app.geocode.urllib.request.urlopen", side_effect=fake):
            self.assertEqual(geocode.route_blocking("http://osrm.example", (1, 2), (3, 4), avoid_tolls=True), (15, False))
        self.assertEqual(len(urls), 2)
        self.assertNotIn("exclude", urls[1])

    def test_no_exclusion_when_not_avoiding(self):
        fake, urls = self._fake_osrm(toll_free_minutes=22, fastest_minutes=15)
        with mock.patch("app.geocode.urllib.request.urlopen", side_effect=fake):
            self.assertEqual(geocode.route_blocking("http://osrm.example", (1, 2), (3, 4), avoid_tolls=False), (15, False))
        self.assertEqual(len(urls), 1)
        self.assertNotIn("exclude", urls[0])

    def test_background_pass_passes_setting_and_records_mode(self):
        seen = []

        def route(url, origin, dest, avoid_tolls=False):
            seen.append(avoid_tolls)
            return (20, True) if avoid_tolls else (14, False)

        drive_time.run_pass_blocking(geocode_fn=lambda a: (45.75, 7.99), route_fn=route)
        with db.get_conn() as c:
            row = c.execute("SELECT drive_minutes, drive_mode, drive_tolls_avoided FROM places WHERE id='p1'").fetchone()
            place = places_router._json(c.execute("SELECT * FROM places WHERE id='p1'").fetchone(), 0)
        self.assertEqual(seen, [True])
        self.assertEqual((row["drive_minutes"], row["drive_mode"], row["drive_tolls_avoided"]), (20, "no_tolls", 1))
        self.assertEqual((place["driveAvoidTolls"], place["driveTollsAvoided"]), (True, True))

        # Nothing to do while the setting is unchanged...
        self.assertEqual(drive_time.run_pass_blocking(geocode_fn=lambda a: (45.75, 7.99), route_fn=route), 0)
        # ...but turning avoid_tolls off re-queues the place straight away.
        set_setting(avoid_tolls=False)
        self.assertEqual(drive_time.run_pass_blocking(geocode_fn=lambda a: (45.75, 7.99), route_fn=route), 1)
        with db.get_conn() as c:
            row = c.execute("SELECT drive_minutes, drive_mode, drive_tolls_avoided FROM places WHERE id='p1'").fetchone()
        self.assertEqual((row["drive_minutes"], row["drive_mode"], row["drive_tolls_avoided"]), (14, "fastest", 0))

    def test_places_computed_before_this_setting_existed_are_redone(self):
        with db.get_conn() as c:
            c.execute("UPDATE places SET drive_minutes = 9, drive_checked_at = ? WHERE id = 'p1'", (config.now_iso(),))
        n = drive_time.run_pass_blocking(geocode_fn=lambda a: (45.75, 7.99), route_fn=lambda *a, **k: (21, True))
        self.assertEqual(n, 1)

    def test_task_place_exposes_tolls_flag(self):
        drive_time.run_pass_blocking(geocode_fn=lambda a: (45.75, 7.99), route_fn=lambda *a, **k: (20, False))
        with db.get_conn() as c:
            add_user(c, "u1", "Ann")
            add_list(c, "l1", "S")
            add_task(c, "t1", "l1", "Dentist", due_date="2026-09-21", due_time="14:30", place_id="p1")
            rows = c.execute(taskview.TASK_SELECT + " WHERE t.id = 't1'").fetchall()
            t = taskview.serialize_tasks(c, rows, TODAY)[0]
        self.assertEqual(t["place"]["driveMinutes"], 20)
        self.assertIs(t["place"]["driveTollsAvoided"], False)


class RecalculateDriveTime(Base):
    """§8n — the manual "🔄 Recalculate drive time" button/endpoint: the
    same geocode+route calls as drive_time.py's background pass, but for
    one place, right away, bypassing every cooldown."""

    HOME = (45.0, 7.0)
    ACTING = {"id": "u1", "name": "Ann", "is_admin": False}

    def setUp(self):
        super().setUp()
        self._orig_home = geocode.HOME_LATLON
        self._orig_geocode_fn = geocode.geocode_blocking
        self._orig_route_fn = geocode.route_blocking
        geocode.HOME_LATLON = None
        drive_on()
        with db.get_conn() as c:
            add_user(c, "u1", "Ann")
            c.execute("INSERT INTO places (id, name, address, created_at) VALUES ('p1', 'X', '1 Main St', ?)", (config.now_iso(),))

    def tearDown(self):
        geocode.HOME_LATLON = self._orig_home
        geocode.geocode_blocking = self._orig_geocode_fn
        geocode.route_blocking = self._orig_route_fn
        super().tearDown()

    def enable(self, geocode_fn=None, route_fn=None):
        geocode.HOME_LATLON = self.HOME
        set_setting(osrm_url="http://osrm.example")
        geocode.geocode_blocking = geocode_fn or (lambda addr: (45.75, 7.99))
        geocode.route_blocking = route_fn or (lambda *a, **k: (18, True))

    def test_disabled_without_feature_configured(self):
        with self.assertRaises(HTTPException) as cm:
            places_router.recalculate_drive_time("p1", acting=self.ACTING)
        self.assertEqual(cm.exception.status_code, 400)

    def test_recalculates_and_returns_updated_place(self):
        self.enable()
        result = places_router.recalculate_drive_time("p1", acting=self.ACTING)
        self.assertEqual(result["driveMinutes"], 18)
        with db.get_conn() as c:
            row = c.execute("SELECT lat, lon, drive_checked_at FROM places WHERE id = 'p1'").fetchone()
        self.assertIsNotNone(row["lat"])
        self.assertIsNotNone(row["drive_checked_at"])

    def test_bypasses_the_background_pass_cooldown(self):
        self.enable()
        with db.get_conn() as c:
            # A very recent successful check would normally make drive_time.py
            # skip this place as "fresh" — the manual endpoint ignores that.
            c.execute("UPDATE places SET drive_minutes = 5, drive_checked_at = ? WHERE id = 'p1'", (config.now_iso(),))
        result = places_router.recalculate_drive_time("p1", acting=self.ACTING)
        self.assertEqual(result["driveMinutes"], 18)

    def test_failed_lookup_raises_502_but_still_records_the_attempt(self):
        self.enable(geocode_fn=lambda addr: None)
        with self.assertRaises(HTTPException) as cm:
            places_router.recalculate_drive_time("p1", acting=self.ACTING)
        self.assertEqual(cm.exception.status_code, 502)
        with db.get_conn() as c:
            row = c.execute("SELECT drive_minutes, drive_checked_at FROM places WHERE id = 'p1'").fetchone()
        self.assertIsNone(row["drive_minutes"])
        self.assertIsNotNone(row["drive_checked_at"])

    def test_unknown_place_404s(self):
        self.enable()
        with self.assertRaises(HTTPException) as cm:
            places_router.recalculate_drive_time("nope", acting=self.ACTING)
        self.assertEqual(cm.exception.status_code, 404)


class Places(Base):
    """§8h — phone numbers and address-dedup (spaces/newlines/case don't
    make two addresses count as different)."""

    ACTING = {"id": "u1", "name": "Ann", "username": None, "is_admin": False, "real": None, "acting": False}

    def setUp(self):
        super().setUp()
        with db.get_conn() as c:
            add_user(c, "u1", "Ann")

    def test_create_with_phone(self):
        p = places_router.create_place(body={"name": "Hospital", "address": "1 Main St", "phone": "303-555-0100"}, acting=self.ACTING)
        self.assertEqual(p["phone"], "303-555-0100")
        self.assertEqual(p["address"], "1 Main St")

    def test_create_without_phone_defaults_to_none(self):
        p = places_router.create_place(body={"name": "School", "address": "9 Oak Lane"}, acting=self.ACTING)
        self.assertIsNone(p["phone"])

    def test_phone_too_long_rejected(self):
        with self.assertRaises(HTTPException) as cm:
            places_router.create_place(body={"name": "X", "address": "1 Rd", "phone": "1" * 31}, acting=self.ACTING)
        self.assertEqual(cm.exception.status_code, 422)

    def test_duplicate_address_variants_rejected(self):
        places_router.create_place(body={"name": "Hospital", "address": "123 Main St"}, acting=self.ACTING)
        for variant in ("123   Main   St", "123 Main St\n", "  123 main st  ", "123\nMain\nSt", "123 MAIN ST"):
            with self.assertRaises(HTTPException) as cm:
                places_router.create_place(body={"name": "Dupe", "address": variant}, acting=self.ACTING)
            self.assertEqual(cm.exception.status_code, 409)
            self.assertIn("Hospital", cm.exception.detail)

    def test_different_address_succeeds(self):
        places_router.create_place(body={"name": "Hospital", "address": "123 Main St"}, acting=self.ACTING)
        p = places_router.create_place(body={"name": "Vet", "address": "456 Elm Ave"}, acting=self.ACTING)
        self.assertEqual(p["address"], "456 Elm Ave")

    def test_update_to_collide_with_another_place_rejected(self):
        places_router.create_place(body={"name": "Hospital", "address": "123 Main St"}, acting=self.ACTING)
        vet = places_router.create_place(body={"name": "Vet", "address": "456 Elm Ave"}, acting=self.ACTING)
        with self.assertRaises(HTTPException) as cm:
            places_router.update_place(vet["id"], body={"address": "123  main  st"}, acting=self.ACTING)
        self.assertEqual(cm.exception.status_code, 409)

    def test_update_to_own_normalized_address_succeeds(self):
        p = places_router.create_place(body={"name": "Hospital", "address": "123 Main St"}, acting=self.ACTING)
        updated = places_router.update_place(p["id"], body={"address": "  123   MAIN   ST  "}, acting=self.ACTING)
        self.assertEqual(updated["address"], "123   MAIN   ST")

    def test_clear_phone_via_null(self):
        p = places_router.create_place(body={"name": "Hospital", "address": "123 Main St", "phone": "303-555-0100"}, acting=self.ACTING)
        updated = places_router.update_place(p["id"], body={"phone": None}, acting=self.ACTING)
        self.assertIsNone(updated["phone"])

    def test_migrate_adds_phone_column_to_existing_db(self):
        # Simulate a pre-phone install: drop the column, then re-run init_db's
        # migration and confirm it comes back without disturbing existing rows.
        with db.get_conn() as c:
            c.execute("INSERT INTO places (id, name, address, created_by, created_at) VALUES ('p1','Old Place','1 Old Rd','u1',?)", (config.now_iso(),))
            c.execute("ALTER TABLE places RENAME TO places_old")
            c.execute("""CREATE TABLE places (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, address TEXT NOT NULL,
                created_by TEXT, created_at TEXT NOT NULL)""")
            c.execute("INSERT INTO places (id, name, address, created_by, created_at) SELECT id, name, address, created_by, created_at FROM places_old")
            c.execute("DROP TABLE places_old")
        db.init_db()
        with db.get_conn() as c:
            cols = {r["name"] for r in c.execute("PRAGMA table_info(places)")}
            self.assertIn("phone", cols)
            self.assertEqual(c.execute("SELECT name, phone FROM places WHERE id='p1'").fetchone()[:], ("Old Place", None))


class BackupRestore(Base):
    def test_backup_validate_import(self):
        with db.get_conn() as c:
            add_user(c, "u1", "Ann")
            add_list(c, "l1", "S")
            add_task(c, "t1", "l1", "Before backup")
        snap = db.backup_to_tempfile()
        try:
            db.validate_backup_file(snap)
            with db.get_conn() as c:
                add_task(c, "t2", "l1", "After backup")
            self.assertEqual(sqlite3.connect(config.DB_PATH).execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 2)
            # restore uses a scratch file INSIDE the data dir (same filesystem as the live DB)
            fd, scratch = tempfile.mkstemp(suffix=".db", dir=config.DATA_DIR)
            os.close(fd)
            with open(snap, "rb") as a, open(scratch, "wb") as b:
                b.write(a.read())
            db.validate_backup_file(scratch)
            db.import_from_tempfile(scratch)
            with db.get_conn() as c:
                self.assertEqual([r["title"] for r in c.execute("SELECT title FROM tasks")], ["Before backup"])
                self.assertEqual(c.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        finally:
            os.remove(snap)

    def test_import_of_older_backup_is_migrated_immediately(self):
        # An older backup has no places.drive_minutes etc. —
        # restoring it must bring the schema up to date right away, not
        # leave every places/tasks query failing until the next restart.
        snap = db.backup_to_tempfile()
        try:
            old = sqlite3.connect(snap)
            old.create_function("normalize_addr", 1, db.normalize_address, deterministic=True)
            for col in ("drive_checked_at", "drive_minutes", "lon", "lat"):
                old.execute(f"ALTER TABLE places DROP COLUMN {col}")
            old.commit(); old.close()
            fd, scratch = tempfile.mkstemp(suffix=".db", dir=config.DATA_DIR)
            os.close(fd)
            with open(snap, "rb") as a, open(scratch, "wb") as b:
                b.write(a.read())
            db.validate_backup_file(scratch)
            db.import_from_tempfile(scratch)
            with db.get_conn() as c:
                cols = {r["name"] for r in c.execute("PRAGMA table_info(places)")}
            self.assertTrue({"lat", "lon", "drive_minutes", "drive_checked_at"} <= cols)
        finally:
            os.remove(snap)

    def test_validation_rejects(self):
        d = tempfile.mkdtemp()
        junk = os.path.join(d, "junk.db")
        with open(junk, "wb") as f:
            f.write(b"this is not a database at all" * 50)
        with self.assertRaises(ValueError):
            db.validate_backup_file(junk)
        other = os.path.join(d, "other.db")
        c = sqlite3.connect(other)
        c.execute("CREATE TABLE users (id TEXT)")
        c.commit(); c.close()
        with self.assertRaises(ValueError) as cm:
            db.validate_backup_file(other)
        self.assertIn("missing tables", str(cm.exception))


class NotifyServiceHints(Base):
    """A wrong notify_targets service makes Home Assistant answer HTTP 400.
    The app should say which service is missing and suggest the real one
    (the Companion app's are notify.mobile_app_<device>)."""

    def setUp(self):
        super().setUp()
        self.ha.notify_services = ["mobile_app_priya_sharma_s_phone", "mobile_app_arjun_phone", "persistent_notification"]

    def test_failure_log_names_the_likely_right_service(self):
        with self.assertLogs("ha_notify", level="WARNING") as logs:
            ok = ha_notify.send_notify("priya_sharma_s_phone", "t", "m")
        self.assertFalse(ok)
        line = "\n".join(logs.output)
        self.assertIn("HTTP 400", line)
        self.assertIn("Service notify.priya_sharma_s_phone not found", line)   # HA's own message
        self.assertIn("did you mean notify.mobile_app_priya_sharma_s_phone", line)

    def test_correct_service_still_works(self):
        self.assertTrue(ha_notify.send_notify("mobile_app_arjun_phone", "t", "m"))

    def test_startup_check_flags_unknown_targets_only(self):
        with db.get_conn() as c:
            for who in ("priya", "arjun"):
                add_user(c, who, who, who)
        link("priya", "priya_sharma_s_phone")
        link("arjun", "mobile_app_arjun_phone")
        with self.assertLogs("ha_notify", level="WARNING") as logs:
            self.assertEqual(ha_notify.check_targets_blocking(), 1)
        self.assertIn("'priya'", "\n".join(logs.output))

    def test_no_hint_when_services_cant_be_read(self):
        self.ha.notify_services = None          # GET /api/services -> 404 in the fake
        self.assertEqual(ha_notify.explain_failure("whatever"), "")
        self.assertEqual(ha_notify.check_targets_blocking(), 0)

    def test_unrelated_name_lists_phone_services(self):
        hint = ha_notify.explain_failure("zzz")
        self.assertIn("notify.mobile_app_arjun_phone", hint)

    def test_notify_entity_is_sent_through_send_message(self):
        # The Companion app's newer style: a notify ENTITY (no action of its own).
        self.ha.notify_services = ["mobile_app_arjun_phone"]
        self.ha.notify_entities = ["priya_sharma_s_phone"]
        self.assertTrue(ha_notify.send_notify("priya_sharma_s_phone", "Household Todo", "Bins"))
        sent = [(p, b) for m, p, b, _ in self.ha.requests if m == "POST"]
        self.assertEqual(sent[-1][0], "/api/services/notify/send_message")
        self.assertEqual(sent[-1][1], {"entity_id": "notify.priya_sharma_s_phone",
                                       "title": "Household Todo", "message": "Bins"})
        # remembered: the next send goes straight to send_message
        self.ha.requests.clear()
        self.assertTrue(ha_notify.send_notify("priya_sharma_s_phone", "t", "m"))
        self.assertEqual([p for m, p, b, _ in self.ha.requests if m == "POST"], ["/api/services/notify/send_message"])

    def test_startup_check_accepts_notify_entities(self):
        self.ha.notify_services = []
        self.ha.notify_entities = ["priya_sharma_s_phone"]
        with db.get_conn() as c:
            for who in ("priya", "typo"):
                add_user(c, who, who, who)
        link("priya", "priya_sharma_s_phone")
        link("typo", "priya_phone_2")
        with self.assertLogs("ha_notify", level="INFO") as logs:
            self.assertEqual(ha_notify.check_targets_blocking(), 1)
        out = "\n".join(logs.output)
        self.assertIn("notify entity notify.priya_sharma_s_phone", out)
        self.assertIn("'typo' won't work", out)
        self.assertTrue(ha_notify._entity_mode["priya_sharma_s_phone"])

    def test_neither_action_nor_entity_fails_with_hint(self):
        self.ha.notify_services = ["mobile_app_priya_sharma_s_phone"]
        with self.assertLogs("ha_notify", level="WARNING") as logs:
            self.assertFalse(ha_notify.send_notify("priya_sharma_s_phone", "t", "m"))
        self.assertIn("did you mean notify.mobile_app_priya_sharma_s_phone", "\n".join(logs.output))


class ConfigOptions(unittest.TestCase):
    def test_services_for_matches_the_user_id_only(self):
        reset_db()
        with db.get_conn() as c:
            add_user(c, "1", "Arjun", "arjun.sharma")
            _insert_link(c, "1", "notify.mobile_app_x")
        self.assertEqual(ha_notify.services_for({"id": "1", "name": "Arjun", "username": "arjun.sharma"}), ["mobile_app_x"])
        # neither the login name nor the display name of someone else matches
        self.assertEqual(ha_notify.services_for({"id": "2", "name": "Arjun", "username": "arjun.sharma"}), [])

    def test_sensor_refresh_default(self):
        reset_db()
        self.assertEqual(settings.sensor_refresh_seconds(), 300)

    def test_osrm_and_nominatim_default_to_public_services(self):
        # §8n — home_address is the only thing that has to be set; these two
        # already point at the public OpenStreetMap services unless overridden.
        reset_db()
        self.assertEqual(settings.osrm_url(), "https://router.project-osrm.org")
        self.assertEqual(settings.nominatim_url(), "https://nominatim.openstreetmap.org")


# ---------------------------------------------------------------------------
# — optional links on tasks and schedule items
# ---------------------------------------------------------------------------

class LinkRule(unittest.TestCase):
    """links.clean_url: the server-side rule both routers use (SPEC §5.1)."""

    def test_accepts_http_and_https_trimmed(self):
        self.assertEqual(links.clean_url("  https://example.com/a?b=1#c  "), "https://example.com/a?b=1#c")
        self.assertEqual(links.clean_url("http://studio.local:8080/timetable"), "http://studio.local:8080/timetable")
        self.assertEqual(links.clean_url("HTTPS://Example.com/X"), "https://Example.com/X")   # scheme lower-cased only

    def test_blank_is_none(self):
        for v in (None, "", "   ", "\t\n"):
            self.assertIsNone(links.clean_url(v))

    def test_rejects_other_schemes_relative_and_junk(self):
        for v in ("javascript:alert(1)", "JavaScript:alert(1)", "data:text/html,hi", "ftp://x", "mailto:a@b.c",
                  "/relative/path", "example.com", "//example.com", "https://", "http:example.com", "https://:80",
                  "https://exa mple.com", "https://example.com/\nx", "http://[::1", 42, ["https://x.com"]):
            with self.assertRaises(ValueError, msg=repr(v)):
                links.clean_url(v)

    def test_length_limit(self):
        base = "https://example.com/"
        ok = base + "a" * (links.MAX_URL_LENGTH - len(base))
        self.assertEqual(links.clean_url(ok), ok)
        with self.assertRaises(ValueError):
            links.clean_url(ok + "a")

    def test_is_web_url(self):
        self.assertTrue(links.is_web_url("https://x.com"))
        self.assertTrue(links.is_web_url("HTTP://x.com"))
        for v in (None, "", "javascript:alert(1)", "ftp://x", 5):
            self.assertFalse(links.is_web_url(v))


class LinkSchema(Base):
    def test_fresh_has_url_columns_and_migration_adds_them(self):
        with db.get_conn() as c:
            fresh = {t: [(r["name"], r["type"], r["notnull"], r["dflt_value"]) for r in c.execute(f"PRAGMA table_info({t})")]
                     for t in ("tasks", "schedule_items")}
            self.assertEqual(fresh["tasks"][-1], ("url", "TEXT", 0, None))
            self.assertEqual(fresh["schedule_items"][-1], ("url", "TEXT", 0, None))
            # make it an older database: no url columns, one row in each table
            add_user(c, "u1", "Ann")
            add_list(c, "l1", "Household")
            add_task(c, "t1", "l1", "Old task")
            add_item(c, "s1", "Old item")
            c.execute("ALTER TABLE tasks DROP COLUMN url")
            c.execute("ALTER TABLE schedule_items DROP COLUMN url")
        db.init_db()
        with db.get_conn() as c:
            for t in ("tasks", "schedule_items"):
                self.assertEqual([(r["name"], r["type"], r["notnull"], r["dflt_value"]) for r in c.execute(f"PRAGMA table_info({t})")],
                                 fresh[t])
            self.assertEqual(tuple(c.execute("SELECT title, url FROM tasks WHERE id='t1'").fetchone()), ("Old task", None))
            self.assertEqual(tuple(c.execute("SELECT name, url FROM schedule_items WHERE id='s1'").fetchone()), ("Old item", None))
        db.init_db()   # idempotent


class LinkNotifications(Base):
    """Single-item notifications end with the link and carry it as notify
    data; digest and weekly get a 🔗 line (SPEC §8.1)."""

    URL = "https://clinic.example/check-in?id=7"

    def setUp(self):
        super().setUp()
        drive_on()
        link("ann", "mobile_app_ann")
        link("bob", "mobile_app_bob")
        with db.get_conn() as c:
            add_user(c, "u1", "Ann", "ann")
            add_user(c, "u2", "Bob", "bob")
            add_list(c, "shared", "Household")
            add_list(c, "ann_p", "My Tasks", "personal", "u1")
            for u in ("u1", "u2"):
                c.execute("INSERT INTO user_prefs (user_id, notifications_enabled, lead_days, notify_on_assign) VALUES (?,1,1,1)", (u,))
                c.execute("INSERT INTO user_reminder_offsets (id, user_id, minutes_before, created_at) VALUES (?, ?, 60, ?)",
                          (f"o_{u}", u, config.now_iso()))
            c.execute("INSERT INTO places (id,name,address,created_at) VALUES ('pl1','Clinic','1 Secret Rd',?)", (config.now_iso(),))

    def sent(self):
        out = []

        def fake(service, title, message, data=None):
            out.append((service, title, message, data))
            return True
        return out, fake

    def test_task_reminder_link_line_and_data(self):
        with db.get_conn() as c:
            add_task(c, "t1", "ann_p", "Checkup", due_date="2026-09-21", due_time="18:00", place_id="pl1",
                     notes="SECRET NOTE", url=self.URL)
        out, fake = self.sent()
        config.utcnow = at(2026, 9, 21, 17, 0)
        self.assertEqual(reminders.run_task_reminder_pass_blocking(sender=fake), 1)
        self.assertEqual(out[-1], ("mobile_app_ann", "Household Todo", f"Checkup — due in 1 hour (18:00) @ Clinic\n{self.URL}",
                                   {"url": self.URL, "clickAction": self.URL}))
        self.assertNotIn("SECRET", str(out))
        self.assertNotIn("1 Secret Rd", str(out))

    def test_without_a_link_the_sender_gets_no_data(self):
        with db.get_conn() as c:
            add_task(c, "t1", "ann_p", "Plain", due_date="2026-09-21", due_time="18:00")
        out = []

        def strict(service, title, message):        # an older-style sender: no data parameter at all
            out.append(message)
            return True
        config.utcnow = at(2026, 9, 21, 17, 0)
        self.assertEqual(reminders.run_task_reminder_pass_blocking(sender=strict), 1)
        self.assertEqual(out, ["Plain — due in 1 hour (18:00)"])
        self.assertTrue(reminders.send_assignment_ping_blocking("u2", "Ann", "No link", None, sender=strict))

    def test_schedule_reminder_link(self):
        with db.get_conn() as c:
            add_item(c, "yoga", "Yoga", rule="weeks:1:1", anchor="2026-09-21", start="18:00", end="19:00", assigned="u1")
            c.execute("UPDATE schedule_items SET url = 'https://studio.example/zoom' WHERE id = 'yoga'")
        out, fake = self.sent()
        config.utcnow = at(2026, 9, 21, 17, 0)
        self.assertEqual(reminders.run_schedule_reminder_pass_blocking(sender=fake), 1)
        self.assertEqual(out[-1][2:], ("Yoga — starts in 1 hour (18:00–19:00)\nhttps://studio.example/zoom",
                                       {"url": "https://studio.example/zoom", "clickAction": "https://studio.example/zoom"}))

    def test_assignment_pings_carry_link(self):
        out, fake = self.sent()
        self.assertTrue(reminders.send_assignment_ping_blocking("u2", "Ann", "Book MOT", "2026-09-22", sender=fake, url=self.URL))
        self.assertEqual(out[-1], ("mobile_app_bob", "Household Todo", f"Ann assigned you: Book MOT (due 2026-09-22)\n{self.URL}",
                                   {"url": self.URL, "clickAction": self.URL}))
        self.assertTrue(reminders.send_assignment_ping_blocking("u2", "Ann", "Yoga", None, sender=fake,
                                                                detail="Every Mon, 18:00–19:00", url="https://studio.example/"))
        self.assertEqual(out[-1][2:], ("Ann assigned you: Yoga (Every Mon, 18:00–19:00)\nhttps://studio.example/",
                                       {"url": "https://studio.example/", "clickAction": "https://studio.example/"}))
        # a stored value that somehow isn't http(s) is never made tappable
        self.assertTrue(reminders.send_assignment_ping_blocking("u2", "Ann", "Odd", None, sender=fake, url="javascript:alert(1)"))
        self.assertEqual(out[-1][2:], ("Ann assigned you: Odd", None))

    def test_digest_link_lines_only_for_shown_entries(self):
        config.utcnow = at(2026, 9, 21, 8, 5)
        with db.get_conn() as c:
            for i in range(7):
                add_task(c, f"t{i}", "ann_p", f"Task {i}", due_date="2026-09-21", position=i,
                         url=f"https://x.example/{i}" if i in (0, 2, 6) else None)
        out, fake = self.sent()
        self.assertEqual(reminders.run_digest_pass_blocking(sender=fake), 1)
        service, title, message, data = out[0]
        self.assertIsNone(data)                                  # a multi-item message isn't tappable
        self.assertEqual(message.split("\n"), [
            "• Task 0 — today", "   🔗 https://x.example/0",
            "• Task 1 — today",
            "• Task 2 — today", "   🔗 https://x.example/2",
            "• Task 3 — today",
            "• Task 4 — today",
            "+2 more",
        ])

    def test_weekly_link_lines_for_tasks_and_items(self):
        config.utcnow = at(2026, 9, 21, 12, 5)
        with db.get_conn() as c:
            c.execute("INSERT INTO user_weekly_prefs (user_id, enabled, day_of_week, time) VALUES ('u1',1,1,'12:00')")
            add_task(c, "t1", "shared", "Pay bill", assigned_to="u1", due_date="2026-09-23", url="https://bank.example/pay")
            add_item(c, "yoga", "Yoga", rule="weeks:1:2", anchor="2026-09-22", start="18:00", end="19:00", assigned="u1",
                     place="pl1", notes="SECRET NOTE")
            c.execute("UPDATE schedule_items SET url = 'https://studio.example/' WHERE id = 'yoga'")
        out, fake = self.sent()
        self.assertEqual(reminders.run_weekly_pass_blocking(sender=fake), 1)
        self.assertEqual(out[0][2].split("\n"), [
            "• Yoga — tomorrow 18:00–19:00 @ Clinic", "   🔗 https://studio.example/",
            "• Pay bill — in 2 days", "   🔗 https://bank.example/pay",
        ])
        self.assertNotIn("SECRET", str(out))

    def test_action_path_sends_data(self):
        with db.get_conn() as c:
            add_task(c, "t1", "ann_p", "Checkup", due_date="2026-09-21", due_time="18:00", url=self.URL)
        config.utcnow = at(2026, 9, 21, 17, 0)
        self.assertEqual(reminders.run_task_reminder_pass_blocking(), 1)
        svc, body = self.ha.notifications()[-1]
        self.assertEqual(svc, "mobile_app_ann")
        self.assertEqual(body, {"title": "Household Todo", "message": f"Checkup — due in 1 hour (18:00)\n{self.URL}",
                                "data": {"url": self.URL, "clickAction": self.URL}})

    def test_action_path_without_data_has_no_data_key(self):
        self.assertTrue(ha_notify.send_notify("mobile_app_ann", "t", "m"))
        self.assertEqual(self.ha.notifications()[-1][1], {"title": "t", "message": "m"})

    def test_entity_path_never_sends_data(self):
        self.ha.notify_services = ["mobile_app_other"]
        self.ha.notify_entities = ["annas_iphone"]
        data = {"url": self.URL, "clickAction": self.URL}
        self.assertTrue(ha_notify.send_notify("annas_iphone", "Household Todo", f"X\n{self.URL}", data=data))
        posts = [(p, b) for m, p, b, _ in self.ha.requests if m == "POST"]
        self.assertEqual(posts[0], ("/api/services/notify/annas_iphone",
                                    {"title": "Household Todo", "message": f"X\n{self.URL}", "data": data}))
        self.assertEqual(posts[-1], ("/api/services/notify/send_message",
                                     {"entity_id": "notify.annas_iphone", "title": "Household Todo", "message": f"X\n{self.URL}"}))
        # remembered as an entity: the next send goes straight to send_message, still without data
        self.ha.requests.clear()
        self.assertTrue(ha_notify.send_notify("annas_iphone", "Household Todo", "Y", data=data))
        posts = [(p, b) for m, p, b, _ in self.ha.requests if m == "POST"]
        self.assertEqual(posts, [("/api/services/notify/send_message",
                                  {"entity_id": "notify.annas_iphone", "title": "Household Todo", "message": "Y"})])


if __name__ == "__main__":
    unittest.main()
