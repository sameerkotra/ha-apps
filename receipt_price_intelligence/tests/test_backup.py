"""Backup export and import: the access keys and passwords in App settings never leave in an export, and
importing one keeps the ones this install has. Needs the full requirements."""
import importlib.util
import logging
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HAVE_DEPS = all(importlib.util.find_spec(m) for m in ("sqlalchemy", "fastapi"))
SECRETS = {"model_api_key": "sk-test-not-a-real-key-4321", "imap_password": "pw-test-9876",
           "web_search_api_key": "tvly-test-5555", "web_search_token": "tok-test-7777"}


@unittest.skipUnless(HAVE_DEPS, "needs the app's requirements")
class BackupSecretsTests(unittest.TestCase):
    def setUp(self):
        from app import app_settings
        from app.db import dispose_engine, init_models
        self.dir = tempfile.mkdtemp()
        self.old_env = {k: os.environ.get(k) for k in ("DATABASE_PATH", "IMAGE_PATH")}
        os.environ["DATABASE_PATH"] = os.path.join(self.dir, "t.db")
        os.environ["IMAGE_PATH"] = os.path.join(self.dir, "img")
        dispose_engine()
        app_settings.configure(os.environ["DATABASE_PATH"])
        init_models()
        self.app_settings = app_settings

    def tearDown(self):
        from app.db import dispose_engine
        dispose_engine()
        for k, v in self.old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        if self.old_env["DATABASE_PATH"]:
            self.app_settings.configure(self.old_env["DATABASE_PATH"])

    def test_export_leaves_out_secrets_and_import_keeps_ours(self):
        from app.services import backup
        self.app_settings.update({**SECRETS, "unit_system": "metric"}, "Pat")
        path, name = backup.new_export()
        try:
            self.assertTrue(name.startswith("receipt-price-intelligence-"))
            with open(path, "rb") as f:
                data = f.read()
            for value in SECRETS.values():
                self.assertNotIn(value.encode(), data)
            conn = sqlite3.connect(path)
            rows = dict(conn.execute("SELECT key, value FROM app_settings").fetchall())
            conn.close()
            for key in SECRETS:
                self.assertEqual(rows[key], '""')
            self.assertEqual(rows["unit_system"], '"metric"')
            self.assertEqual(self.app_settings.values()["model_api_key"], SECRETS["model_api_key"])  # live keeps it

            self.app_settings.update({"unit_system": "imperial"}, "Pat")
            logging.disable(logging.INFO)   # backup.py logs its counts dict with a stray "%s" (a known log-text glitch)
            try:
                out = backup.import_backup(path, "abc123", "Pat")
            finally:
                logging.disable(logging.NOTSET)
            path = None                                     # consumed
            self.assertIn("imported", out)
            values = self.app_settings.values()
            self.assertEqual(values["unit_system"], "metric")            # the backup's settings…
            for key, value in SECRETS.items():
                self.assertEqual(values[key], value)                     # …but this install's secrets
        finally:
            if path and os.path.exists(path):
                os.remove(path)

    def test_safety_copy_keeps_the_secrets(self):
        """The copy kept on this install before an import is a full copy (it is what a failed import puts back)."""
        from app.services import backup
        self.app_settings.update({"model_api_key": SECRETS["model_api_key"]}, "Pat")
        dest = os.path.join(self.dir, "safety.db")
        backup.make_snapshot(dest)
        conn = sqlite3.connect(dest)
        value = conn.execute("SELECT value FROM app_settings WHERE key='model_api_key'").fetchone()[0]
        conn.close()
        self.assertEqual(value, '"' + SECRETS["model_api_key"] + '"')
        download = backup.safety_copy_download(dest)                # what Admin → Backup sends
        try:
            with open(download, "rb") as f:
                self.assertNotIn(SECRETS["model_api_key"].encode(), f.read())
        finally:
            os.remove(download)
        with open(dest, "rb") as f:
            self.assertIn(SECRETS["model_api_key"].encode(), f.read())   # the kept copy is unchanged


if __name__ == "__main__":
    unittest.main()
