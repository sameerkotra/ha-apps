"""Calorie Tracker tests. Run from the app folder:

    python3 -m unittest discover -s tests

Needs requirements-dev.txt (the pinned requirements plus httpx2 for
Starlette's TestClient)."""
import _env  # noqa: F401  (must be first)

import glob
import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import time
import unittest
import urllib.error
from unittest import mock

from starlette.testclient import TestClient

from app import ai_client, config, db, ha_sync, settings
from app.main import app

import logging
for _name in ("httpx", "httpx2"):
    logging.getLogger(_name).setLevel(logging.WARNING)


def hdr(uid, name=None, username=None):
    h = {"X-Remote-User-Id": uid, "X-Remote-User-Display-Name": name or uid}
    if username:
        h["X-Remote-User-Name"] = username
    return h


ADMIN = hdr("u_admin", "Adminy Admin", "adminy")
ALICE = hdr("u_alice", "Alice", "alice")
BOB = hdr("u_bob", "Bob", "bob")


class ApiBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 127.0.0.1 stands in for Home Assistant's ingress proxy, which the
        # require_ha_ingress_auth middleware requires.
        cls._ctx = TestClient(app, client=("127.0.0.1", 12345))
        cls.c = cls._ctx.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls._ctx.__exit__(None, None, None)

    def setUp(self):
        for f in glob.glob(config.DB_PATH + "*"):
            os.remove(f)
        db.init_db()
        for h in (ADMIN, ALICE, BOB):
            self.assertEqual(self.c.get("/api/me", headers=h).status_code, 200)

    def log(self, h, name="Toast", date="2026-09-21", **kw):
        body = {"date": date, "food_name": name, "calories": 100, **kw}
        r = self.c.post("/api/logs", json=body, headers=h)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()


class IngressGate(unittest.TestCase):
    def test_non_ingress_source_is_rejected(self):
        with TestClient(app, client=("172.30.33.7", 5555)) as c:   # another app
            self.assertEqual(c.get("/api/me", headers=ALICE).status_code, 403)

    def test_missing_user_header_is_401(self):
        with TestClient(app, client=("172.30.32.2", 5555)) as c:
            self.assertEqual(c.get("/api/me").status_code, 401)
            self.assertEqual(c.get("/api/health").status_code, 200)


class AdminMatching(ApiBase):
    def test_login_name_and_id_match(self):
        self.assertTrue(self.c.get("/api/me", headers=ADMIN).json()["is_admin"])
        config.ADMIN_USERS.add("u_bob")
        try:
            self.assertTrue(self.c.get("/api/me", headers=BOB).json()["is_admin"])
        finally:
            config.ADMIN_USERS.discard("u_bob")

    def test_display_name_alone_is_not_enough(self):
        # someone whose display name happens to equal a listed entry
        impostor = hdr("u_eve", "adminy", "eve")
        self.assertFalse(self.c.get("/api/me", headers=impostor).json()["is_admin"])
        w = self.c.get("/api/whoami", headers=impostor).json()
        self.assertFalse(w["isAdmin"])
        self.assertTrue(w["displayNameOnly"])
        self.assertEqual(self.c.get("/api/users", headers=impostor).status_code, 403)

    def test_non_admin_as_user_is_ignored(self):
        self.log(ALICE, "Alice's toast")
        r = self.c.get("/api/logs?date=2026-09-21&as_user=u_alice", headers=BOB)
        self.assertEqual(r.json(), [])
        r = self.c.get("/api/logs?date=2026-09-21&as_user=u_alice", headers=ADMIN)
        self.assertEqual([l["food_name"] for l in r.json()], ["Alice's toast"])


class Ownership(ApiBase):
    def test_cannot_delete_someone_elses_entries(self):
        entry = self.log(ALICE)
        self.assertEqual(self.c.delete(f"/api/logs/{entry['id']}", headers=BOB).status_code, 404)
        food = self.c.post("/api/saved-foods", json={"name": "Oats", "calories": 150}, headers=ALICE).json()
        self.assertEqual(self.c.delete(f"/api/saved-foods/{food['id']}", headers=BOB).status_code, 404)
        self.assertEqual(self.c.put(f"/api/saved-foods/{food['id']}", json={"name": "x", "calories": 1},
                                    headers=BOB).status_code, 404)
        self.assertEqual(self.c.delete(f"/api/logs/{entry['id']}", headers=ALICE).status_code, 200)


class BackupRestore(ApiBase):
    def test_only_admins(self):
        self.assertEqual(self.c.get("/api/admin-storage-download-db", headers=ALICE).status_code, 403)

    def test_round_trip_and_rejections(self):
        self.log(ALICE, "before")
        snap = self.c.get("/api/admin-storage-download-db", headers=ADMIN).content
        self.log(ALICE, "after")
        for junk in (b"not a database", b""):
            r = self.c.post("/api/admin-storage-import-db", headers=ADMIN, files={"file": ("x.db", io.BytesIO(junk))})
            self.assertEqual(r.status_code, 400)
        r = self.c.post("/api/admin-storage-import-db", headers=ADMIN, files={"file": ("x.db", io.BytesIO(snap))})
        self.assertEqual(r.status_code, 200, r.text)
        logs = self.c.get("/api/logs?date=2026-09-21", headers=ALICE).json()
        self.assertEqual([l["food_name"] for l in logs], ["before"])
        leftovers = [f for f in os.listdir(config.DATA_DIR) if f.endswith(".db") and f != os.path.basename(config.DB_PATH)]
        self.assertEqual(leftovers, [])

    def test_older_backup_is_migrated_on_import(self):
        # saved_foods.notes was added after the first release: a backup from
        # before then must be usable straight after a restore, not only
        # after the next restart.
        path = db.backup_to_tempfile()
        try:
            old = sqlite3.connect(path)
            old.execute("ALTER TABLE saved_foods DROP COLUMN notes")
            old.commit(); old.close()
            with open(path, "rb") as f:
                r = self.c.post("/api/admin-storage-import-db", headers=ADMIN, files={"file": ("old.db", f)})
            self.assertEqual(r.status_code, 200, r.text)
            r = self.c.post("/api/saved-foods", json={"name": "Oats", "calories": 150, "notes": "rolled"}, headers=ALICE)
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["notes"], "rolled")
        finally:
            os.remove(path)


class AiWarmup(ApiBase):
    """The Ollama warm-up ("hi") runs only when the model hasn't answered in
    the last few minutes — and never for a cloud provider."""

    def setUp(self):
        super().setUp()
        self.calls = []
        self._orig = (ai_client._sync_warmup, ai_client._last_ok)
        ai_client._sync_warmup = lambda: self.calls.append("warm") or True
        settings.update({"ai_provider": "ollama", "ai_url": "http://ollama.lan:11434", "ai_model": "llama3"}, None)

    def tearDown(self):
        ai_client._sync_warmup, ai_client._last_ok = self._orig

    def _run(self, coro):
        import asyncio
        return asyncio.run(coro)

    def test_skips_warmup_when_model_answered_recently(self):
        ai_client._last_ok = time.time() - 30
        self._run(ai_client.ensure_warm())
        self.assertEqual(self.calls, [])

    def test_warms_up_when_stale_or_never(self):
        ai_client._last_ok = None
        self._run(ai_client.ensure_warm())
        ai_client._last_ok = time.time() - ai_client.WARM_FOR_SECONDS - 1
        self._run(ai_client.ensure_warm())
        self.assertEqual(self.calls, ["warm", "warm"])

    def test_no_warmup_for_cloud_providers_or_without_setup(self):
        ai_client._last_ok = None
        settings.update({"ai_provider": "openai", "ai_url": "", "ai_model": "gpt-4o-mini"}, None)
        self._run(ai_client.ensure_warm())
        settings.update({"ai_provider": ""}, None)
        self._run(ai_client.ensure_warm())
        self.assertEqual(self.calls, [])

    def test_request_size_is_capped(self):
        r = self.c.post("/api/ai/estimate", json={"description": "x" * 5000}, headers=ALICE)
        self.assertEqual(r.status_code, 422)


class TimeZone(unittest.TestCase):
    def test_zoneinfo_database_available(self):
        # Needs tzdata (the Dockerfile's `apk add tzdata`); without it every
        # ZoneInfo lookup fails and the app silently runs on UTC.
        before = config.timezone_name()
        try:
            self.assertTrue(config.set_timezone("Europe/Berlin"))
            self.assertEqual(config.timezone_name(), "Europe/Berlin")
            with self.assertLogs("config", "WARNING"):
                self.assertFalse(config.set_timezone("Not/AZone"))
        finally:
            config.set_timezone(before)

    def test_fallback_is_utc(self):
        # Home Assistant's own zone is read at startup; without it the app runs on UTC.
        self.assertEqual(config._DEFAULT_TZ_NAME, "UTC")


class FrontendEscaping(unittest.TestCase):
    """escapeHtml is used inside attribute values (title="…", data-copy="…"),
    so it must escape quotes — this was a stored-XSS hole via saved-food notes."""

    def test_escape_html_escapes_quotes(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        with open(os.path.join(os.path.dirname(__file__), "..", "app", "static", "app.js"), encoding="utf-8") as f:
            src = f.read()
        fn = re.search(r"function escapeHtml\(str\) \{.*?\n\}", src, re.S).group(0)
        script = fn + '\nprocess.stdout.write(escapeHtml(`" onmouseover="alert(1)\' <b>&`));'
        out = subprocess.run([node, "-e", script], capture_output=True, text=True, check=True).stdout
        self.assertEqual(out, "&quot; onmouseover=&quot;alert(1)&#39; &lt;b&gt;&amp;")


# ---------------------------------------------------------------------------
# Admin → App settings: the AI setup and the daily-calories sensor switch
# live in the app_settings table, not on the app's Configuration tab.
# (Provider request shapes, retries and the access key: test_ai_providers.py.)
# ---------------------------------------------------------------------------

DEFAULT_VALUES = {"ai_provider": "", "ai_url": "", "ai_model": "", "ai_max_tokens": 4096,
                  "expose_daily_calories_sensor": True}


class AppSettingsApi(ApiBase):
    def put(self, body, h=ADMIN):
        return self.c.put("/api/admin/settings", json=body, headers=h)

    def test_get_shape_and_defaults(self):
        r = self.c.get("/api/admin/settings", headers=ADMIN)
        self.assertEqual(r.status_code, 200, r.text)
        j = r.json()
        self.assertEqual(j["values"], DEFAULT_VALUES)
        self.assertEqual(j["defaults"], j["values"])
        self.assertEqual(set(j["meta"]), set(settings.DEFAULTS))
        self.assertTrue(all(m == {"restartRequired": False} for m in j["meta"].values()))
        self.assertEqual(j["secrets"], {"ai_api_key": {"saved": False, "hint": ""}})
        self.assertEqual(j["providers"]["openai"]["defaultUrl"], "https://api.openai.com/v1")
        self.assertEqual(j["providers"]["anthropic"]["defaultUrl"], "https://api.anthropic.com")
        self.assertEqual(j["providers"]["ollama"]["defaultUrl"], "")
        self.assertNotIn("ai_api_key", j["values"])

    def test_no_personal_defaults(self):
        text = json.dumps(settings.DEFAULTS)
        self.assertNotIn("192.168", text)
        self.assertNotIn("qwen", text)

    def test_non_admin_is_forbidden(self):
        self.assertEqual(self.c.get("/api/admin/settings", headers=ALICE).status_code, 403)
        self.assertEqual(self.put({"ai_model": "evil"}, h=ALICE).status_code, 403)
        self.assertEqual(self.c.post("/api/admin/settings/test-ai", json={}, headers=ALICE).status_code, 403)
        self.assertEqual(settings.get("ai_model"), "")

    def test_partial_update_keeps_other_keys_and_normalizes(self):
        r = self.put({"ai_provider": "ollama", "ai_url": "  https://ollama.lan:8443/  "})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["values"], {**DEFAULT_VALUES, "ai_provider": "ollama", "ai_url": "https://ollama.lan:8443"})
        r = self.put({"ai_model": "llama3"})
        self.assertEqual(r.json()["values"]["ai_url"], "https://ollama.lan:8443")
        self.assertEqual(r.json()["values"]["ai_model"], "llama3")
        with db.get_conn() as conn:
            rows = {r["key"]: r["updated_by"] for r in conn.execute("SELECT key, updated_by FROM app_settings")}
        self.assertEqual(rows, {"ai_provider": "u_admin", "ai_url": "u_admin", "ai_model": "u_admin"})

    def test_unchanged_value_is_not_written(self):
        self.assertEqual(self.put({"ai_model": "", "ai_api_key": ""}).status_code, 200)
        with db.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM app_settings").fetchone()[0], 0)

    def test_bad_values_are_422_and_store_nothing(self):
        bad = [
            {"ai_provider": "gemini"},
            {"ai_provider": 1},
            {"ai_url": "ftp://ollama.lan:11434"},
            {"ai_url": "ollama.lan:11434"},
            {"ai_url": "not a url"},
            {"ai_url": "http://"},
            {"ai_url": "http://user:secret@ollama.lan:11434"},
            {"ai_url": "http://ollama.lan:99999"},
            {"ai_url": 11434},
            {"ai_url": None},
            {"ai_model": 7},
            {"ai_model": "two words"},
            {"ai_model": "x" * 201},
            {"ai_api_key": "has space"},
            {"ai_api_key": "line\nbreak"},
            {"ai_api_key": 12345},
            {"ai_max_tokens": 10},
            {"ai_max_tokens": 10 ** 7},
            {"ai_max_tokens": "4096"},
            {"ai_max_tokens": 4096.5},
            {"clear_ai_api_key": "yes"},
            {"ai_provider": "ollama"},                          # Ollama needs its address
            {"expose_daily_calories_sensor": "yes"},
            {"expose_daily_calories_sensor": 1},
            {"expose_daily_calories_sensor": None},
            {"trash_days": 7},                                  # unknown key
            {"ai_model": "llama3", "surprise": True},           # unknown key alongside a good one
        ]
        for body in bad:
            with self.subTest(body=body):
                r = self.put(body)
                self.assertEqual(r.status_code, 422, r.text)
                self.assertIsInstance(r.json()["detail"], str)
        for body in ([1, 2], "x"):
            self.assertEqual(self.put(body).status_code, 422)
        self.assertIn("Unknown setting", self.put({"trash_days": 7}).json()["detail"])
        self.assertIn("ai_url", self.put({"ai_url": "ftp://x"}).json()["detail"])
        self.assertIn("Ollama needs its address", self.put({"ai_provider": "ollama"}).json()["detail"])
        self.assertNotIn("has space", self.put({"ai_api_key": "has space"}).json()["detail"])   # never echoed
        with db.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM app_settings").fetchone()[0], 0)

    def test_restore_brings_back_the_backups_settings(self):
        self.put({"ai_provider": "openai", "ai_model": "from-backup"})
        snap = self.c.get("/api/admin-storage-download-db", headers=ADMIN).content
        self.put({"ai_model": "after-backup"})
        self.assertEqual(settings.get("ai_model"), "after-backup")
        r = self.c.post("/api/admin-storage-import-db", headers=ADMIN, files={"file": ("x.db", io.BytesIO(snap))})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(settings.get("ai_model"), "from-backup")
        self.assertEqual(self.c.get("/api/ai/status", headers=ALICE).json()["model"], "from-backup")

    def test_older_backup_without_settings_table_gets_it_on_restore(self):
        self.put({"ai_provider": "openai", "ai_model": "gpt-4o-mini"})
        path = db.backup_to_tempfile()
        try:
            old = sqlite3.connect(path)
            old.execute("DROP TABLE app_settings")
            old.commit(); old.close()
            with open(path, "rb") as f:
                r = self.c.post("/api/admin-storage-import-db", headers=ADMIN, files={"file": ("old.db", f)})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(settings.all()["ai_model"], "")     # defaults, no error
            self.assertEqual(self.put({"ai_model": "mistral"}).status_code, 200)
        finally:
            os.remove(path)


class OldOllamaRowsMigrate(ApiBase):
    """Older databases stored the AI server as ollama_url / ollama_model rows.
    The migration turns them into ai_provider=ollama, ai_url, ai_model, so the
    install (or a restored backup) keeps using the same Ollama server."""

    def _old_rows(self, conn, url=True):
        rows = [("ollama_model", json.dumps("llama3:8b"))]
        if url:
            rows.append(("ollama_url", json.dumps("http://10.0.0.9:11434")))
        for key, value in rows:
            conn.execute("INSERT INTO app_settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, ?)",
                         (key, value, "2026-01-01T00:00:00+00:00", "u_admin"))

    def test_init_db_migrates_the_rows(self):
        with db.get_conn() as conn:
            self._old_rows(conn)
        db.init_db()
        self.assertEqual((settings.ai_provider(), settings.get("ai_url"), settings.get("ai_model")),
                         ("ollama", "http://10.0.0.9:11434", "llama3:8b"))
        self.assertTrue(settings.ai_configured())
        with db.get_conn() as conn:
            keys = {r[0] for r in conn.execute("SELECT key FROM app_settings")}
            by = conn.execute("SELECT updated_by FROM app_settings WHERE key='ai_url'").fetchone()[0]
        self.assertEqual(keys, {"ai_provider", "ai_url", "ai_model"})
        self.assertEqual(by, "u_admin")
        db.init_db()                                       # idempotent
        self.assertEqual(settings.get("ai_model"), "llama3:8b")

    def test_existing_ai_values_win_and_model_only_row(self):
        with db.get_conn() as conn:
            self._old_rows(conn, url=False)
            conn.execute("INSERT INTO app_settings (key, value, updated_at) VALUES ('ai_model', ?, 'x')",
                         (json.dumps("kept"),))
        db.init_db()
        self.assertEqual((settings.ai_provider(), settings.get("ai_url"), settings.get("ai_model")),
                         ("ollama", "", "kept"))
        self.assertFalse(settings.ai_configured())          # the address still has to be typed

    def test_restoring_an_older_backup_migrates_it(self):
        path = db.backup_to_tempfile()
        try:
            old = sqlite3.connect(path)
            self._old_rows(old)
            old.commit(); old.close()
            with open(path, "rb") as f:
                r = self.c.post("/api/admin-storage-import-db", headers=ADMIN, files={"file": ("old.db", f)})
            self.assertEqual(r.status_code, 200, r.text)
            st = self.c.get("/api/ai/status", headers=ALICE).json()
            self.assertEqual((st["provider"], st["url"], st["model"], st["configured"]),
                             ("ollama", "http://10.0.0.9:11434", "llama3:8b", True))
        finally:
            os.remove(path)


class AiNotSetUp(ApiBase):
    """A fresh install has no AI: AI features say so, everything else works,
    and nothing is sent anywhere."""

    def test_ai_routes_say_not_set_up(self):
        with mock.patch("app.ai_client._post") as post:
            st = self.c.get("/api/ai/status", headers=ALICE).json()
            self.assertFalse(st["configured"])
            self.assertEqual(st["message"], settings.NOT_SET_UP)
            self.assertIn("AI isn't set up — an admin can set it up in Admin → App settings", st["message"])
            self.assertIsNone(st["privacy"])
            for path, body in (("/api/ai/estimate", {"description": "toast"}), ("/api/ai/chat", {"message": "hi"}),
                               ("/api/ai/warmup", None)):
                r = self.c.post(path, json=body, headers=ALICE)
                self.assertEqual(r.status_code, 503, path)
                self.assertEqual(r.json()["detail"], settings.NOT_SET_UP)
            post.assert_not_called()
        self.log(ALICE, "still works")                     # the rest of the app is unaffected

    def test_ui_has_the_notice_places(self):
        with open(os.path.join(os.path.dirname(__file__), "..", "app", "static", "index.html"), encoding="utf-8") as f:
            html = f.read()
        self.assertEqual(html.count('class="ai-notice"'), 4)    # Food Log, Saved Foods, AI Assistant, App settings
        with open(os.path.join(os.path.dirname(__file__), "..", "app", "static", "app.js"), encoding="utf-8") as f:
            js = f.read()
        self.assertIn("AI isn't set up — an admin can set it up in Admin → App settings.", js)


class NoAdminYet(ApiBase):
    """With admin_users empty nobody is admin (never the first visitor) and
    every page shows how to become one."""

    def test_flag_and_banner(self):
        saved = set(config.ADMIN_USERS)
        config.ADMIN_USERS.clear()
        try:
            j = self.c.get("/api/me", headers=ALICE).json()
            self.assertTrue(j["no_admins"])
            self.assertFalse(j["is_admin"])
            self.assertEqual(j["username"], "alice")
            j = self.c.get("/api/me", headers=hdr("u_x", "X")).json()   # no login name sent: the id
            self.assertEqual(j["username"], "u_x")
            self.assertTrue(self.c.get("/api/whoami", headers=ALICE).json()["noAdmins"])
            self.assertEqual(self.c.get("/api/admin/settings", headers=ADMIN).status_code, 403)
        finally:
            config.ADMIN_USERS.update(saved)
        self.assertFalse(self.c.get("/api/me", headers=ALICE).json()["no_admins"])
        with open(os.path.join(os.path.dirname(__file__), "..", "app", "static", "index.html"), encoding="utf-8") as f:
            html = f.read()
        banner = html.split('id="noAdminBanner"', 1)[1].split("</div>", 1)[0]
        self.assertIn("No admin yet", banner)
        self.assertIn("<code>admin_users</code>", banner)
        self.assertIn("restart the app", banner)
        self.assertNotIn("add-on", banner)
        self.assertIn("How the app sees you", banner)


class AddonOptions(ApiBase):
    """admin_users is the only thing read from options.json; anything else in
    it (switch_admins, ollama_url, …) is ignored."""

    def test_only_admin_users_is_read(self):
        self.assertEqual(os.path.dirname(config.OPTIONS_PATH), config.DATA_DIR)
        with open(config.OPTIONS_PATH, "w") as f:
            json.dump({"ollama_url": "http://10.9.9.9:11434", "ollama_model": "llama3",
                       "expose_daily_calories_sensor": False, "switch_admins": ["someone"],
                       "admin_users": ["jane.doe"]}, f)
        try:
            self.assertEqual(config._load(), {"admin_users": ["jane.doe"]})
            with TestClient(app, client=("127.0.0.1", 12345)) as c:      # runs the startup (lifespan)
                j = c.get("/api/admin/settings", headers=ADMIN).json()
            self.assertEqual(j["values"], DEFAULT_VALUES)
            self.assertTrue(ha_sync.exposed())
            with db.get_conn() as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM app_settings").fetchone()[0], 0)
        finally:
            os.remove(config.OPTIONS_PATH)


class _HAResponse:
    def __init__(self, status=200):
        self.status = status

    def read(self):
        return b"{}"

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeHA:
    """Stands in for urllib.request.urlopen inside app.ha_sync: records
    (method, entity_id, state) for every Core API /states call."""

    def __init__(self, fail=False, delete_status=200):
        self.calls = []
        self.fail = fail
        self.delete_status = delete_status

    def __call__(self, req, timeout=None):
        entity = req.full_url.rsplit("/states/", 1)[1]
        body = json.loads(req.data) if req.data else None
        self.calls.append((req.get_method(), entity, body and body["state"]))
        if self.fail:
            raise urllib.error.URLError("HA down")
        if req.get_method() == "DELETE" and self.delete_status >= 400:
            raise urllib.error.HTTPError(req.full_url, self.delete_status, "x", {}, None)
        return _HAResponse()

    def of(self, method):
        return sorted((e, st) for m, e, st in self.calls if m == method)


class DailyCaloriesSensor(ApiBase):
    """expose_daily_calories_sensor (an App setting): read live by every push; turning it on publishes everyone now, off removes the
    sensors from Home Assistant."""
    ENTITIES = ["sensor.calorie_tracker_adminy_admin_daily_calories",
                "sensor.calorie_tracker_alice_daily_calories",
                "sensor.calorie_tracker_bob_daily_calories"]

    def setUp(self):
        super().setUp()
        self.ha = FakeHA()
        patches = [mock.patch("app.ha_sync.urllib.request.urlopen", self.ha),
                   mock.patch.object(config, "SUPERVISOR_TOKEN", "test-token")]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        ha_sync._published.clear()
        ha_sync._removal_pending = False
        self.today = str(config.today())

    def put(self, value, h=ADMIN):
        return self.c.put("/api/admin/settings", json={"expose_daily_calories_sensor": value}, headers=h)

    def test_log_change_pushes_only_while_on(self):
        self.log(ALICE, date=self.today, calories=250)
        self.assertEqual(self.ha.of("POST"), [("sensor.calorie_tracker_alice_daily_calories", 250)])
        self.assertEqual(self.put(False).status_code, 200)
        self.ha.calls.clear()
        self.log(ALICE, date=self.today, calories=100)
        self.assertEqual(self.ha.calls, [])
        self.log(BOB, date=self.today)
        self.assertEqual(self.ha.calls, [])

    def test_turning_off_removes_every_sensor(self):
        self.log(ALICE, date=self.today)
        self.ha.calls.clear()
        r = self.put(False)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["values"]["expose_daily_calories_sensor"])
        self.assertEqual(self.ha.of("DELETE"), [(e, None) for e in self.ENTITIES])
        self.assertEqual(self.ha.of("POST"), [])

    def test_turning_off_also_removes_a_renamed_users_old_sensor(self):
        self.log(ALICE, date=self.today)
        self.c.get("/api/me", headers=hdr("u_alice", "Alice Smith", "alice"))   # renamed in HA
        self.ha.calls.clear()
        self.put(False)
        deleted = [e for e, _ in self.ha.of("DELETE")]
        self.assertIn("sensor.calorie_tracker_alice_daily_calories", deleted)
        self.assertIn("sensor.calorie_tracker_alice_smith_daily_calories", deleted)

    def test_turning_on_publishes_everyone_immediately(self):
        self.put(False)
        self.log(ALICE, date=self.today, calories=300)
        self.log(ALICE, date="2020-01-01", calories=999)     # not today: not counted
        self.ha.calls.clear()
        self.assertEqual(self.put(True).status_code, 200)
        self.assertEqual(self.ha.of("POST"), [
            ("sensor.calorie_tracker_adminy_admin_daily_calories", 0),
            ("sensor.calorie_tracker_alice_daily_calories", 300),
            ("sensor.calorie_tracker_bob_daily_calories", 0),
        ])
        self.assertEqual(self.ha.of("DELETE"), [])

    def test_unchanged_value_triggers_nothing(self):
        self.put(True)
        self.assertEqual(self.ha.calls, [])

    def test_periodic_job_follows_the_setting(self):
        import asyncio
        asyncio.run(ha_sync.periodic_sync())
        self.assertEqual(len(self.ha.of("POST")), 3)
        self.put(False)
        self.ha.calls.clear()
        asyncio.run(ha_sync.periodic_sync())
        self.assertEqual(self.ha.calls, [])                  # off and nothing pending: no calls

    def test_failed_removal_is_retried_by_the_periodic_job(self):
        import asyncio
        self.ha.fail = True
        with self.assertLogs("ha_sync", "WARNING"):
            self.put(False)
        self.assertTrue(ha_sync._removal_pending)
        self.ha.fail = False
        self.ha.calls.clear()
        asyncio.run(ha_sync.periodic_sync())
        self.assertEqual(self.ha.of("DELETE"), [(e, None) for e in self.ENTITIES])
        self.assertFalse(ha_sync._removal_pending)
        self.ha.calls.clear()
        asyncio.run(ha_sync.periodic_sync())
        self.assertEqual(self.ha.calls, [])

    def test_already_gone_counts_as_removed(self):
        self.ha.delete_status = 404
        self.put(False)
        self.assertEqual(len(self.ha.of("DELETE")), 3)
        self.assertFalse(ha_sync._removal_pending)

    def test_no_db_connection_held_during_ha_calls(self):
        open_conns = []
        real_connect = db._connect

        def tracking_connect():
            conn = real_connect()
            open_conns.append(conn)
            return conn

        def urlopen(req, timeout=None):
            for conn in open_conns:
                with self.assertRaises(sqlite3.ProgrammingError):   # closed
                    conn.execute("SELECT 1")
            return self.ha(req, timeout)

        with mock.patch.object(db, "_connect", tracking_connect), \
                mock.patch("app.ha_sync.urllib.request.urlopen", urlopen):
            import asyncio
            asyncio.run(ha_sync.push_all())
            asyncio.run(ha_sync.push_for_user("u_alice", "alice", "Alice"))
            self.put(False)
        self.assertEqual(len(self.ha.of("DELETE")), 3)

    def test_only_admins_can_change_it(self):
        self.assertEqual(self.put(False, h=ALICE).status_code, 403)
        self.assertTrue(settings.get("expose_daily_calories_sensor"))
        self.assertEqual(self.ha.calls, [])


class AdminOnlyGuards(ApiBase):
    """The pages moved under Admin (Users, Storage) keep their API paths and
    stay admin-only."""

    def test_users_and_storage_apis_are_admin_only(self):
        self.assertEqual(self.c.get("/api/users", headers=ALICE).status_code, 403)
        self.assertEqual(self.c.put("/api/users/u_bob", json={"enabled": False}, headers=ALICE).status_code, 403)
        self.assertEqual(self.c.get("/api/admin-storage-download-db", headers=ALICE).status_code, 403)
        r = self.c.post("/api/admin-storage-import-db", headers=ALICE, files={"file": ("x.db", io.BytesIO(b"x"))})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.c.get("/api/users", headers=ADMIN).status_code, 200)


class SidebarLayout(unittest.TestCase):
    def test_sidebar_has_admin_not_storage_or_users(self):
        with open(os.path.join(os.path.dirname(__file__), "..", "app", "static", "index.html"), encoding="utf-8") as f:
            html = f.read()
        nav = html.split('<nav class="side-nav">', 1)[1].split("</nav>", 1)[0]
        self.assertIn('data-tab="admin"', nav)
        self.assertNotIn('data-tab="storage"', nav)
        self.assertNotIn('data-tab="users"', nav)


if __name__ == "__main__":
    unittest.main()
