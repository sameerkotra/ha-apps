"""The 13 Oct 2026 review's fixes (CT1–CT12, CTP1–CTP7): AI limits, the access key that stays with its address,
AI details for admins, checked estimates, saved_food_id ownership, each person's own calories-sensor switch, the
acting-as log line, requests that don't write, the restore gate, sensor pushes after the answer, the weight list's
range, sums in SQL (same results as before), the warm-up lock, midnight, and the page's error handling.
Every person, food and number here is invented."""
import _env  # noqa: F401  (must be first)

import asyncio
import contextlib
import inspect
import json
import math
import os
import random
import re
import sqlite3
import tempfile
import threading
import time
import unittest
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from app import ai_client, auth, config, db, ha_sync, schemas, settings, tools
from app.routers import logs as logs_router
from test_ai_providers import KEY, FakeAI
from test_app import ADMIN, ALICE, BOB, ApiBase, FakeHA, hdr
from test_tools import call

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "app" / "static"


class AiBase(ApiBase):
    def setUp(self):
        super().setUp()
        self.fake = FakeAI()
        for p in (mock.patch.object(ai_client, "_post", self.fake), mock.patch.object(ai_client, "_sleep", lambda s: None)):
            p.start()
            self.addCleanup(p.stop)
        self.put(ai_provider="openai", ai_model="gpt-4o", ai_api_key=KEY)

    def put(self, **values):
        r = self.c.put("/api/admin/settings", json=values, headers=ADMIN)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def chat(self, who=ALICE):
        return self.c.post("/api/ai/chat", json={"message": "hi"}, headers=who)


class AiLimits(AiBase):
    def test_per_minute_and_per_day(self):
        self.put(ai_per_minute=2, ai_per_day=3)
        self.assertEqual([self.chat().status_code for _ in range(2)], [200, 200])
        r = self.chat()
        self.assertEqual(r.status_code, 429)
        self.assertIn("a minute is the limit", r.json()["detail"])
        self.assertEqual(self.chat(BOB).status_code, 200)           # someone else: their own count
        ai_client._minute.clear()                                   # a minute later
        self.assertEqual(self.chat().status_code, 200)
        ai_client._minute.clear()
        r = self.chat()
        self.assertEqual(r.status_code, 429)
        self.assertIn("AI requests today", r.json()["detail"])
        self.assertEqual(ai_client.used_today("u_alice"), 3)
        # deleting entries (or anything a person can delete) gives nothing back; acting as someone doesn't either
        with db.get_conn() as conn:
            conn.execute("DELETE FROM food_logs")
        self.assertEqual(self.c.post("/api/ai/estimate?as_user=u_bob", json={"description": "x"},
                                     headers=ALICE).status_code, 429)
        self.put(ai_per_day=0, ai_per_minute=0)                     # 0 = no limit
        self.assertEqual(self.chat().status_code, 200)

    def test_at_most_two_at_a_time(self):
        gate, started = threading.Event(), []

        def slow(*a, **k):
            started.append(1)
            gate.wait(5)
            return 200, json.dumps({"choices": [{"message": {"content": "ok"}}]}), {}
        with mock.patch.object(ai_client, "_post", slow), mock.patch.object(ai_client, "SLOT_WAIT", 0.2):
            threads = [threading.Thread(target=lambda: ai_client.chat_blocking("x")) for _ in range(2)]
            for t in threads:
                t.start()
            while len(started) < 2:
                time.sleep(0.01)
            with self.assertRaises(ai_client.AIError) as e:
                ai_client.chat_blocking("third")
            self.assertEqual(e.exception.kind, "busy")
            r = self.chat()
            self.assertEqual(r.status_code, 429)
            self.assertIn("busy", r.json()["detail"])
            gate.set()
            for t in threads:
                t.join()
        self.assertEqual(self.chat().status_code, 200)

    def test_answer_limit_goes_to_every_provider(self):
        self.chat()
        self.assertEqual(self.fake.calls[-1]["body"]["max_tokens"], 2000)
        self.put(ai_provider="ollama", ai_url="http://ollama.lan:11434", ai_model="llama3", ai_max_tokens=900)
        self.chat()
        self.assertEqual(self.fake.calls[-1]["body"]["options"]["num_predict"], 900)
        # an OpenAI-compatible server that refuses max_tokens: asked again without it
        self.put(ai_provider="openai", ai_url="http://lm.lan:1234/v1", ai_model="local", ai_api_key=KEY)
        self.fake.queue = [(400, json.dumps({"error": {"message": "Unsupported parameter: 'max_tokens'"}}), {})]
        self.assertEqual(self.chat().status_code, 200)
        self.assertNotIn("max_tokens", self.fake.calls[-1]["body"])


class KeyStaysWithItsAddress(AiBase):
    def test_saving_a_new_address_without_a_key_clears_it(self):
        j = self.put(ai_url="https://other.example.com/v1")
        self.assertFalse(j["secrets"]["ai_api_key"]["saved"])
        self.put(ai_api_key=KEY)
        j = self.put(ai_model="gpt-4o-mini")                         # same address: the key stays
        self.assertTrue(j["secrets"]["ai_api_key"]["saved"])
        j = self.put(ai_url="https://third.example.com/v1", ai_api_key="sk-new-key-000000000")
        self.assertEqual(settings.get("ai_api_key"), "sk-new-key-000000000")     # typed with it: kept
        j = self.put(ai_provider="anthropic", ai_url="")
        self.assertEqual(settings.get("ai_api_key"), "")

    def test_test_connection_never_sends_the_saved_key_elsewhere(self):
        r = self.c.post("/api/admin/settings/test-ai", headers=ADMIN,
                        json={"ai_provider": "openai", "ai_url": "https://evil.example.com/v1", "ai_model": "x"})
        self.assertEqual(r.status_code, 422)
        self.assertIn("type the access key again", r.json()["detail"])
        self.assertEqual(self.fake.calls, [])
        r = self.c.post("/api/admin/settings/test-ai", headers=ADMIN, json={"ai_model": "gpt-4o"})   # same address
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.fake.calls[-1]["headers"]["Authorization"], f"Bearer {KEY}")


class StatusForAdminsOnly(AiBase):
    def test_members_get_no_address_model_or_errors(self):
        self.chat()
        member = self.c.get("/api/ai/status", headers=ALICE).json()
        for key in ("url", "model", "last_error", "last_request"):
            self.assertNotIn(key, member)
        self.assertEqual((member["configured"], member["providerLabel"], member["admin"]),
                         (True, "OpenAI-compatible", False))
        admin = self.c.get("/api/ai/status", headers=ADMIN).json()
        self.assertEqual((admin["url"], admin["model"]), ("https://api.openai.com/v1", "gpt-4o"))
        self.assertNotIn(KEY, json.dumps(admin))


class Estimates(AiBase):
    def answer(self, data):
        self.fake.queue = [(200, json.dumps({"choices": [{"message": {"content": json.dumps(data)}}]}), {})]
        return self.c.post("/api/ai/estimate", json={"description": "toast"}, headers=ALICE)

    def test_checked_like_a_food_entry(self):
        r = self.answer({"food_name": " Toast ", "calories": "80", "protein": 3, "serving_unit": "slice",
                         "note": "n" * 900, "secret": "<script>"})
        self.assertEqual(r.status_code, 200, r.text)
        j = r.json()
        self.assertEqual((j["food_name"], j["calories"], j["serving_size"]), ("Toast", 80.0, 1))
        self.assertNotIn("secret", j)
        self.assertEqual(len(j["note"]), 500)
        for bad in ({"food_name": "x", "calories": -5}, {"food_name": "x", "calories": 1e9}, {"calories": 80},
                    {"food_name": "x"}, {"food_name": "x", "calories": 80, "fat": "lots"}):
            r = self.answer(bad)
            self.assertEqual(r.status_code, 502, bad)
            self.assertIn("didn't look right", r.json()["detail"])


class LogEntries(ApiBase):
    def test_saved_food_must_be_your_own(self):
        r = self.c.post("/api/saved-foods", json={"name": "Bob's bar", "calories": 200}, headers=BOB)
        bobs = r.json()["id"]
        for sid in (bobs, 999999):
            r = self.c.post("/api/logs", headers=ALICE, json={"date": "2026-09-21", "food_name": "x", "calories": 1,
                                                              "saved_food_id": sid})
            self.assertEqual(r.status_code, 422)
            self.assertIn("saved_food_id", r.json()["detail"])
        self.log(BOB, "Bar", saved_food_id=bobs)
        r = self.c.post("/api/logs?as_user=u_bob", headers=ADMIN,
                        json={"date": "2026-09-21", "food_name": "x", "calories": 1, "saved_food_id": bobs})
        self.assertEqual(r.status_code, 200)                       # acting as Bob: Bob's saved food

    def test_names_of_only_spaces_and_order(self):
        for path, body in (("/api/logs", {"date": "2026-09-21", "food_name": "   ", "calories": 1}),
                           ("/api/saved-foods", {"name": "  ", "calories": 1})):
            self.assertEqual(self.c.post(path, json=body, headers=ALICE).status_code, 422)
        ids = [self.log(ALICE, f"Item {i}")["id"] for i in range(5)]   # the same second
        listed = [x["id"] for x in self.c.get("/api/logs?date=2026-09-21", headers=ALICE).json()]
        self.assertEqual(listed, ids)

    def test_invalid_month_is_422(self):
        for month in ("2026-13", "2026-00"):
            r = self.c.get(f"/api/summary/month?month={month}", headers=ALICE)
            self.assertEqual(r.status_code, 422, month)
        self.assertEqual(self.c.get("/api/summary/month?month=2026-12", headers=ALICE).status_code, 200)

    def test_acting_as_writes_are_logged(self):
        with self.assertLogs("auth", "INFO") as logs:
            self.c.post("/api/logs?as_user=u_bob", headers=ADMIN,
                        json={"date": "2026-09-21", "food_name": "x", "calories": 1})
        self.assertTrue(any("acting as Bob (u_bob): POST /api/logs" in m for m in logs.output))
        with self.assertNoLogs("auth", "INFO"):
            self.c.get("/api/logs?date=2026-09-21&as_user=u_bob", headers=ADMIN)     # reading: not logged

    def test_every_route_honours_as_user_only_for_admins(self):
        """A non-admin's as_user is ignored on every data route, reading and writing."""
        self.log(BOB, "Bob's lunch")
        reads = ("/api/logs?date=2026-09-21", "/api/summary?date=2026-09-21", "/api/summary/month?month=2026-09",
                 "/api/history", "/api/goals", "/api/weight", "/api/weight/history", "/api/saved-foods")
        for path in reads:
            sep = "&" if "?" in path else "?"
            mine = self.c.get(path, headers=ALICE).json()
            self.assertEqual(self.c.get(f"{path}{sep}as_user=u_bob", headers=ALICE).json(), mine, path)
        writes = (("POST", "/api/logs", {"date": "2026-09-21", "food_name": "x", "calories": 5}),
                  ("PUT", "/api/goals", {"calorie_goal": 1234}),
                  ("POST", "/api/weight", {"date": "2026-09-21", "weight_kg": 60}),
                  ("POST", "/api/saved-foods", {"name": "Mine", "calories": 5}))
        for method, path, body in writes:
            self.assertEqual(self.c.request(method, f"{path}?as_user=u_bob", json=body, headers=ALICE).status_code, 200)
        with db.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM food_logs WHERE user_id = 'u_bob'").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT calorie_goal FROM goals WHERE user_id = 'u_bob'").fetchone()[0], 2000)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM weight_logs WHERE user_id = 'u_alice'").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM saved_foods WHERE user_id = 'u_alice'").fetchone()[0], 1)


class SensorSwitchPerPerson(ApiBase):
    def setUp(self):
        super().setUp()
        self.ha = FakeHA()
        for p in (mock.patch("app.common.ha_client.urllib.request.urlopen", self.ha),
                  mock.patch.object(config, "SUPERVISOR_TOKEN", "test-token")):
            p.start()
            self.addCleanup(p.stop)
        ha_sync._published.clear()
        self.today = str(config.today())

    def test_off_for_new_people_and_on_when_they_choose(self):
        self.assertFalse(self.c.get("/api/me", headers=ALICE).json()["publishSensor"])
        self.log(ALICE, date=self.today, calories=300)
        asyncio.run(ha_sync.push_all())
        self.assertEqual(self.ha.calls, [])                          # nobody chose to publish
        r = self.c.put("/api/me/sensor", json={"publishSensor": True}, headers=ALICE)
        self.assertEqual((r.status_code, r.json()["publishSensor"]), (200, True))
        self.assertEqual(self.ha.of("POST"), [("sensor.calorie_tracker_alice_daily_calories", 300)])
        self.ha.calls.clear()
        asyncio.run(ha_sync.push_all())
        self.assertEqual([e for e, _ in self.ha.of("POST")], ["sensor.calorie_tracker_alice_daily_calories"])
        self.ha.calls.clear()
        self.c.put("/api/me/sensor", json={"publishSensor": False}, headers=ALICE)
        self.assertEqual(self.ha.of("DELETE"), [("sensor.calorie_tracker_alice_daily_calories", None)])
        self.assertEqual(self.c.put("/api/me/sensor?as_user=u_bob", json={"publishSensor": True},
                                    headers=ADMIN).json()["publishSensor"], True)
        with db.get_conn() as conn:                                  # always the signed-in person, never "acting as"
            self.assertEqual(conn.execute("SELECT publish_sensor FROM users WHERE id = 'u_bob'").fetchone()[0], 0)

    def test_household_switch_stays_the_master(self):
        self.c.put("/api/me/sensor", json={"publishSensor": True}, headers=ALICE)
        self.c.put("/api/admin/settings", json={"expose_daily_calories_sensor": False}, headers=ADMIN)
        self.ha.calls.clear()
        self.log(ALICE, date=self.today)
        asyncio.run(ha_sync.push_all())
        self.assertEqual(self.ha.of("POST"), [])
        self.assertFalse(self.c.get("/api/me", headers=ALICE).json()["sensor"])

    def test_upgrade_keeps_what_was_published(self):
        for global_on, expected in ((True, 1), (False, 0)):
            with self.subTest(global_on=global_on):
                for f in __import__("glob").glob(config.DB_PATH + "*"):
                    os.remove(f)
                db.init_db()
                with db.get_conn() as conn:                          # a 2.3.1 database
                    conn.execute("INSERT INTO users (id, name) VALUES ('u_old', 'Old')")
                    conn.execute("INSERT INTO app_settings (key, value, updated_at) VALUES "
                                 "('expose_daily_calories_sensor', ?, 'x')", (json.dumps(global_on),))
                    conn.execute("ALTER TABLE users DROP COLUMN publish_sensor")
                db.init_db()
                with db.get_conn() as conn:
                    self.assertEqual(conn.execute("SELECT publish_sensor FROM users WHERE id = 'u_old'").fetchone()[0],
                                     expected)
                self.c.get("/api/me", headers=hdr("u_new", "New"))
                with db.get_conn() as conn:
                    self.assertEqual(conn.execute("SELECT publish_sensor FROM users WHERE id = 'u_new'").fetchone()[0], 0)

    def test_log_changes_push_after_the_answer(self):
        self.assertFalse(inspect.iscoroutinefunction(logs_router.create_log))
        with mock.patch.object(ha_sync, "push_for_user_blocking") as push:
            self.log(ALICE, date=self.today)
            self.log(ALICE, date="2020-01-01")                       # not today: no push
        push.assert_called_once_with("u_alice")

    def test_midnight(self):
        from zoneinfo import ZoneInfo
        now = datetime(2026, 3, 1, 23, 59, 0, tzinfo=ZoneInfo("Europe/Berlin"))
        self.assertEqual(ha_sync.seconds_to_midnight(now), 65)
        ticks = []

        async def sleep(s):
            ticks.append(s)
            if len(ticks) > 2:
                raise asyncio.CancelledError

        with mock.patch.object(ha_sync, "periodic_sync", mock.AsyncMock()) as sync:
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(ha_sync.midnight_loop(sleep=sleep))
        self.assertEqual(sync.await_count, 2)
        self.assertIn('jobs.every("ha_sync", 900, ha_sync.periodic_sync, thread=False, at_start=True',
                      (ROOT / "app" / "main.py").read_text(encoding="utf-8"))


class RequestsDontWrite(ApiBase):
    def test_ordinary_requests_write_nothing(self):
        stmts = []
        real = db._connect

        def connect():
            c = real()
            c.set_trace_callback(stmts.append)
            return c
        with mock.patch.object(db, "_connect", connect):
            for _ in range(3):
                self.assertEqual(self.c.get("/api/me", headers=ALICE).status_code, 200)
            writes = [s for s in stmts if s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))]
            self.assertEqual(writes, [])
            self.c.get("/api/me", headers=hdr("u_alice", "Alice Smith", "alice"))     # renamed in Home Assistant
            writes = [s for s in stmts if s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))]
            self.assertEqual(len(writes), 1)
            self.assertIn("UPDATE users SET name", writes[0])
        self.assertFalse(inspect.iscoroutinefunction(auth.get_current_user))
        self.assertFalse(inspect.iscoroutinefunction(auth.get_acting_user))

    def test_a_new_person_gets_their_row_and_goals(self):
        self.c.get("/api/me", headers=hdr("u_zoe", "Zoe"))
        with db.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT name FROM users WHERE id = 'u_zoe'").fetchone()[0], "Zoe")
            self.assertIsNotNone(conn.execute("SELECT 1 FROM goals WHERE user_id = 'u_zoe'").fetchone())

    def test_after_a_restore_the_rows_are_written_again(self):
        snap = db.backup_to_tempfile()
        try:
            with db.get_conn() as conn:
                conn.execute("DELETE FROM users WHERE id = 'u_alice'")
            snap2 = db.backup_to_tempfile()
            with open(snap2, "rb") as f:
                self.assertEqual(self.c.post("/api/admin-storage-import-db", headers=ADMIN,
                                             files={"file": ("b.db", f)}).status_code, 200)
            self.log(ALICE)                                          # Alice's row is back before her entry
        finally:
            os.remove(snap)
            os.remove(snap2)


class RestoreGate(ApiBase):
    def test_requests_wait_for_a_restore_then_give_up_politely(self):
        with mock.patch.object(db, "GATE_TIMEOUT", 0.2):
            with db.GATE.pause():
                r = self.c.get("/api/logs?date=2026-09-21", headers=ALICE)
            self.assertEqual(r.status_code, 503)
            self.assertIn("being restored", r.json()["detail"])
        self.assertEqual(self.c.get("/api/logs?date=2026-09-21", headers=ALICE).status_code, 200)

    def test_restore_waits_for_running_work(self):
        order = []
        done = threading.Event()

        def worker():
            with db.get_conn() as conn:
                conn.execute("SELECT 1")
                order.append("work")
                time.sleep(0.3)
            done.set()
        t = threading.Thread(target=worker)
        t.start()
        time.sleep(0.05)
        snap = db.backup_to_tempfile()
        try:
            db.import_from_tempfile(snap)
            order.append("restore")
        finally:
            if os.path.exists(snap):                                 # (the restore moves it into place)
                os.remove(snap)
            t.join()
        self.assertEqual(order, ["work", "restore"])

    def test_nested_connections_in_one_thread_dont_wait_for_themselves(self):
        with db.get_conn() as outer:
            with db.get_conn() as inner:
                inner.execute("SELECT 1")
            outer.execute("SELECT 1")


class WarmUpOnce(ApiBase):
    def test_many_requests_one_warm_up(self):
        settings.update({"ai_provider": "ollama", "ai_url": "http://ollama.lan:11434", "ai_model": "llama3"}, None)
        calls = []

        def warm():
            calls.append(1)
            time.sleep(0.1)
            ai_client._last_ok = time.time()
            return True
        with mock.patch.object(ai_client, "_sync_warmup", warm):
            ai_client._last_ok = None
            threads = [threading.Thread(target=ai_client.ensure_warm_blocking) for _ in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        self.assertEqual(len(calls), 1)


class WeightList(ApiBase):
    def test_limit_and_days(self):
        today = config.today()
        for i in range(10):
            self.c.post("/api/weight", headers=ALICE, json={"date": str(today - timedelta(days=i)), "weight_kg": 70 + i})
        whole = self.c.get("/api/weight", headers=ALICE).json()
        self.assertEqual([w["date"] for w in whole], sorted(w["date"] for w in whole))      # oldest first, as before
        newest = self.c.get("/api/weight?limit=3", headers=ALICE).json()
        self.assertEqual(newest, whole[-3:])
        self.assertEqual(self.c.get("/api/weight?days=2", headers=ALICE).json(), whole[-2:])
        self.assertEqual(self.c.get("/api/weight?limit=0", headers=ALICE).status_code, 422)


# ---- sums in SQL: the same numbers as the old Python loops, on a seeded month ----
def old_history(rows, granularity, today, goal):
    """2.3.1's /api/history from raw (date, calories) rows."""
    from app.routers.logs import DEFAULT_GOALS  # noqa: F401
    if granularity == "daily":
        n, start = 14, today - timedelta(days=13)
    elif granularity == "weekly":
        n, start = 8, today - timedelta(days=8 * 7 - 1)
    else:
        n, start = 6, config.add_months(today.replace(day=1), -5)
    by_day = {}
    for d, cal in rows:
        d = date.fromisoformat(d)
        if start <= d <= today:
            by_day.setdefault(d, []).append(cal)
    by_day = {d: math.fsum(v) for d, v in by_day.items()}      # exact sums: see same_or_tie
    out = []
    for i in range(n):
        if granularity == "daily":
            d = start + timedelta(days=i)
            out.append(by_day.get(d, 0.0))
        elif granularity == "weekly":
            ws = start + timedelta(days=i * 7)
            we = min(ws + timedelta(days=6), today)
            k = (we - ws).days + 1
            out.append(math.fsum(by_day.get(ws + timedelta(days=j), 0.0) for j in range(k)) / k)
        else:
            ms = config.add_months(start, i).replace(day=1)
            me = min(ms.replace(day=monthrange(ms.year, ms.month)[1]), today)
            k = (me - ms).days + 1
            out.append(math.fsum(v for d, v in by_day.items() if ms <= d <= me) / k)
    return out


def same_or_tie(test, got: float, exact: float, label) -> None:
    """`got` is `exact` rounded to 0.1 — or, when `exact` sits on a .x5 tie, either neighbour: the database adds up
    more carefully (compensated) than the old Python loop did, so the two may round a tie differently; anything
    else must be identical."""
    if got == round(exact, 1):
        return
    tie = abs(abs(exact * 10 - math.floor(exact * 10)) - 0.5) < 1e-6
    test.assertTrue(tie and abs(got - exact) <= 0.05 + 1e-9, f"{label}: {got} vs {exact}")


class SqlSumsMatch(ApiBase):
    def test_history_and_month_summary(self):
        rnd = random.Random(3)
        today = config.today()
        rows = []
        with db.get_conn() as conn:
            for i in range(600):
                d = str(today - timedelta(days=rnd.randint(0, 200)))
                cal, p, c, f = (rnd.randint(1, 90000) / 100 for _ in range(4))
                conn.execute("INSERT INTO food_logs (user_id, date, food_name, calories, protein, carbs, fat) "
                             "VALUES ('u_alice', ?, 'x', ?, ?, ?, ?)", (d, cal, p, c, f))
                rows.append((d, cal, p, c, f))
        for g in ("daily", "weekly", "monthly"):
            got = [p["calories"] for p in self.c.get(f"/api/history?granularity={g}", headers=ALICE).json()["points"]]
            want = old_history([(r[0], r[1]) for r in rows], g, today, 2000)
            self.assertEqual(len(got), len(want))
            for i, (a, b) in enumerate(zip(got, want)):
                same_or_tie(self, a, b, (g, i))
        month = today.strftime("%Y-%m")
        j = self.c.get(f"/api/summary/month?month={month}", headers=ALICE).json()
        mine = [r for r in rows if r[0].startswith(month)]
        for k, idx in (("calories", 1), ("protein", 2), ("carbs", 3), ("fat", 4)):
            same_or_tie(self, j["totals"][k], math.fsum(r[idx] for r in mine), k)


class AssistantNames(ApiBase):
    def test_non_ascii_food_names_match_whatever_their_case(self):
        with mock.patch.object(config, "today", return_value=date(2026, 9, 21)):
            self.c.post("/api/saved-foods", headers=ALICE, json={"name": "Crème Brûlée", "calories": 300})
            self.c.put("/api/admin/settings", headers=ADMIN, json={"assistant_answers": True})
            res = call("u_alice", tool="calorie.food.log", confirm=True, food="CRÈME BRÛLÉE")
        self.assertIn("(300 kcal)", res["text"])


class SharedPieces(ApiBase):
    def test_body_cap_and_caching_and_signing_key(self):
        r = self.c.post("/api/logs", headers={**ALICE, "Content-Type": "application/json"}, content=b" " * (2 << 20))
        self.assertEqual(r.status_code, 413)
        r = self.c.get(f"/app.js?v={config.APP_VERSION}")
        self.assertEqual(r.headers["cache-control"], "public, max-age=31536000, immutable")
        self.assertEqual(self.c.get("/api/health").headers["cache-control"], "no-store")
        from app import app_messages
        self.assertEqual(app_messages._signing_key(), "")
        self.c.put("/api/admin/settings", headers=ADMIN, json={"bus_signing_key": "k" * 20})
        self.assertEqual(app_messages._signing_key(), "k" * 20)
        self.assertNotIn("k" * 20, self.c.get("/api/admin/settings", headers=ADMIN).text)

    def test_backup_snapshots_are_named_for_the_sweep(self):
        path = db.backup_to_tempfile()
        try:
            self.assertTrue(os.path.basename(path).startswith(db.SNAPSHOT_PREFIX))
        finally:
            os.remove(path)
        self.assertFalse(hasattr(ha_sync, "_sync_delete_state"))      # unused, removed


class PageHandling(unittest.TestCase):
    """The page's script and form, read as text (the behaviour was checked in a browser)."""

    def setUp(self):
        self.js = (STATIC / "app.js").read_text(encoding="utf-8")
        self.html = (STATIC / "index.html").read_text(encoding="utf-8")

    def test_saving_buttons_show_the_servers_message(self):
        for anchor in ('$("#addFoodBtn").addEventListener("click", withErrors(',
                       '$("#sfSaveBtn").addEventListener("click", withErrors(',
                       '$("#addWeightBtn").addEventListener("click", withErrors(',
                       '$("#saveGoalsBtn").addEventListener("click", withErrors(',
                       '$all(".use-btn").forEach(btn => btn.addEventListener("click", withErrors(',
                       '$all(".del-log-btn").forEach(btn => btn.addEventListener("click", withErrors(',
                       '$all(".del-sf-btn").forEach(btn => btn.addEventListener("click", withErrors(',
                       '$all(".del-w-btn").forEach(btn => btn.addEventListener("click", withErrors('):
            self.assertIn(anchor, self.js)

    def test_plus_log_uses_today_and_refreshes_the_list(self):
        use = self.js[self.js.index('$all(".use-btn")'):]
        use = use[:use.index("})));")]
        self.assertIn("const day = todayStr();", use)
        self.assertIn("date: day,", use)
        self.assertIn("loadFoodLog()", use)

    def test_delete_asks_and_redraws_the_chart(self):
        dele = self.js[self.js.index('$all(".del-log-btn")'):]
        dele = dele[:dele.index("})));")]
        self.assertIn("confirm(", dele)
        self.assertIn("loadHistory()", dele)

    def test_date_change_skips_the_picker_and_charts_redraw(self):
        body = self.js[self.js.index("async function refreshDateScoped"):]
        body = body[:body.index("\n}\n")]
        self.assertNotIn("loadSavedFoodPicker", body)
        self.assertIn('window.addEventListener("resize"', self.js)

    def test_form_limits_match_the_server(self):
        def attrs(el_id):
            m = re.search(rf'<input id="{el_id}"[^>]*>', self.html)
            self.assertIsNotNone(m, el_id)
            return dict(re.findall(r'(min|max|maxlength)="([^"]+)"', m.group(0)))
        f = schemas.FoodLogCreate.model_fields
        self.assertEqual(attrs("foodName")["maxlength"], str(f["food_name"].metadata[1].max_length))
        self.assertEqual(attrs("foodCalories"), {"min": "0", "max": "50000"})
        self.assertEqual(attrs("foodProtein"), {"min": "0", "max": "5000"})
        self.assertEqual(attrs("weightValue")["max"], "700")
        self.assertEqual(attrs("goalCalories"), {"min": "0", "max": "20000"})
        self.assertEqual(attrs("sfServingUnit")["maxlength"], "40")


if __name__ == "__main__":
    unittest.main()
