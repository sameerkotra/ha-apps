"""Admin → App settings: validation, storage, secrets, live reads, backups. Needs only pydantic."""
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app_settings  # noqa: E402
from app import config  # noqa: E402


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        os.environ["DATABASE_PATH"] = os.path.join(self.dir, "t.db")
        app_settings.configure(os.environ["DATABASE_PATH"])

    def test_defaults_without_rows(self):
        s = config.get_settings()
        self.assertEqual(s.model_url, "")
        self.assertFalse(s.model_ready)
        self.assertEqual(s.model_api_format, "ollama")
        self.assertEqual(s.overpass_url, "https://overpass-api.de/api/interpreter")
        self.assertEqual(s.database_path, os.environ["DATABASE_PATH"])
        self.assertIs(config.get_settings(), s)            # same object until something changes

    def test_update_applies_at_once(self):
        before = config.get_settings()
        app_settings.update({"model_url": "http://192.0.2.1:11434/", "model_name": "vision", "model_temperature": 0}, "Pat")
        after = config.get_settings()
        self.assertIsNot(before, after)
        self.assertEqual(after.model_url, "http://192.0.2.1:11434")   # trailing slash dropped
        self.assertTrue(after.model_ready)
        self.assertIsInstance(after.model_temperature, float)
        with self.assertRaises(AttributeError):
            after.model_url = "x"

    def test_only_changed_keys_are_stored(self):
        app_settings.update({"unit_system": "metric", "imap_port": 993}, "Pat")   # 993 is the default
        rows = sqlite3.connect(os.environ["DATABASE_PATH"]).execute("SELECT key, updated_by FROM app_settings").fetchall()
        self.assertEqual(rows, [("unit_system", "Pat")])

    def test_bad_values_are_refused_and_nothing_is_written(self):
        bad = [
            {"nope": 1},
            {"model_url": "ftp://x"},
            {"model_timeout_seconds": "300"},
            {"model_timeout_seconds": 5},
            {"model_temperature": float("nan")},
            {"ha_sensors": 1},
            {"default_currency": "dollars"},
            {"import_folder": "/config/x"},
            {"receipt_archive_path": "/share/../config"},
            {"alert_notify_service": "mobile_app_phone"},
            {"shopping_tracker_entity": "sensor.x"},
            {"shopping_list_todo_entity": "list.x"},
            {"web_query_field": "two words"},
            {"model_name": "a\nb"},
            {"map_contact_email": "not-an-email"},
            {"log_level": "TRACE"},
        ]
        for partial in bad:
            with self.subTest(partial):
                with self.assertRaises(app_settings.SettingsError):
                    app_settings.update({"unit_system": "metric", **partial}, "Pat")
        self.assertEqual(config.get_settings().unit_system, "imperial")

    def test_error_names_the_setting(self):
        with self.assertRaises(app_settings.SettingsError) as cm:
            app_settings.update({"model_timeout_seconds": 5}, "Pat")
        self.assertIn("Model timeout (seconds)", str(cm.exception))

    def test_secrets_are_write_only(self):
        app_settings.update({"imap_password": "hunter2", "model_api_key": "k"}, "Pat")
        p = app_settings.payload()
        self.assertEqual(p["values"]["imap_password"], "")
        self.assertTrue(p["secretsSet"]["imap_password"])
        self.assertNotIn("hunter2", repr(p))
        self.assertNotIn("imap_password", config.get_settings().redact_secrets())
        self.assertEqual(config.get_settings().imap_password, "hunter2")
        app_settings.update({"imap_password": ""}, "Pat")              # "" clears
        self.assertFalse(app_settings.payload()["secretsSet"]["imap_password"])

    def test_payload_shape(self):
        p = app_settings.payload()
        self.assertEqual(set(p), {"values", "defaults", "meta", "groups", "secretsSet"})
        self.assertEqual(set(p["values"]), set(p["meta"]))
        groups = {g["id"] for g in p["groups"]}
        for key, m in p["meta"].items():
            with self.subTest(key):
                self.assertIn(m["group"], groups)
                self.assertTrue(m["label"])
                self.assertFalse(m["restartRequired"])
                if m["kind"] == "choice":
                    self.assertIn(p["defaults"][key], [c["value"] for c in m["choices"]])
        self.assertEqual(p["meta"]["model_timeout_seconds"]["min"], 10)

    def test_folders_and_off_values(self):
        out = app_settings.update({"import_folder": "/share/receipts/", "overpass_url": ""}, "Pat")
        self.assertEqual(out["import_folder"], "/share/receipts")
        self.assertEqual(config.get_settings().overpass_url, "")

    def test_bad_stored_row_falls_back_to_default(self):
        conn = sqlite3.connect(os.environ["DATABASE_PATH"])
        app_settings.ensure_table(conn)
        conn.execute("INSERT INTO app_settings VALUES ('web_deals_days', '\"lots\"', 'x', NULL)")
        conn.commit()
        app_settings.invalidate()
        self.assertEqual(config.get_settings().web_deals_days, 3)

    def test_change_hook(self):
        seen = []
        app_settings.on_change(lambda changed, values: seen.append((changed, values["log_level"])))
        app_settings.update({"log_level": "DEBUG"}, "Pat")
        self.assertEqual(seen[-1], (["log_level"], "DEBUG"))
        app_settings._hooks.clear()

    def test_restored_backup_keeps_current_settings_unless_it_has_its_own(self):
        app_settings.update({"unit_system": "metric"}, "Pat")
        rows = app_settings.export_rows(os.environ["DATABASE_PATH"])
        old = os.path.join(self.dir, "old.db")
        sqlite3.connect(old).execute("CREATE TABLE receipts (id TEXT)").connection.commit()
        self.assertTrue(app_settings.adopt_rows(old, rows))
        self.assertEqual(app_settings.export_rows(old)[0][:2], ("unit_system", '"metric"'))
        own = os.path.join(self.dir, "own.db")
        conn = sqlite3.connect(own)
        app_settings.ensure_table(conn)
        conn.execute("INSERT INTO app_settings VALUES ('unit_system', '\"imperial\"', 'x', NULL)")
        conn.commit()
        self.assertFalse(app_settings.adopt_rows(own, rows))

    def test_table_from_before_1_0_0_is_shared(self):
        """0.x kept the Notifications page's settings in app_settings (no updated_by column)."""
        path = os.path.join(self.dir, "old-install.db")
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE app_settings (key VARCHAR(80) PRIMARY KEY, value TEXT NOT NULL, updated_at DATETIME NOT NULL)")
        conn.execute("INSERT INTO app_settings VALUES ('notifications', '{\"x\": 1}', '2026-01-01 00:00:00')")
        conn.commit()
        conn.close()
        self.assertEqual(app_settings.export_rows(path), [])
        app_settings.configure(path)
        self.assertEqual(app_settings.values()["unit_system"], "imperial")
        app_settings.update({"unit_system": "metric"}, "Pat")
        rows = dict(sqlite3.connect(path).execute("SELECT key, value FROM app_settings").fetchall())
        self.assertEqual(rows, {"notifications": '{"x": 1}', "unit_system": '"metric"'})
        self.assertEqual([r[0] for r in app_settings.export_rows(path)], ["unit_system"])
        # a restored file with only the notifications row still takes the current App settings
        other = os.path.join(self.dir, "restored.db")
        c2 = sqlite3.connect(other)
        c2.execute("CREATE TABLE app_settings (key VARCHAR(80) PRIMARY KEY, value TEXT NOT NULL, updated_at DATETIME NOT NULL)")
        c2.execute("INSERT INTO app_settings VALUES ('notifications', '{}', '2026-01-01 00:00:00')")
        c2.commit()
        c2.close()
        self.assertTrue(app_settings.adopt_rows(other, app_settings.export_rows(path)))


if __name__ == "__main__":
    unittest.main()
