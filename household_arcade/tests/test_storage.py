"""Admin → Storage: download a consistent backup, import it back, older
databases migrated on import, wrong files refused. Invented people only."""
import _env  # noqa: F401  (must be first)

import os
import sqlite3
import tempfile
import unittest

from app import config, db, settings
from base import ASHA, KABIR, ApiBase


class TestStorage(ApiBase):
    def download(self):
        r = self.get("/api/admin-storage-download-db", ASHA)
        self.assertEqual(r.status_code, 200)
        self.assertIn("household-arcade-backup-", r.headers["content-disposition"])
        return r.content

    def upload(self, data, h=ASHA):
        return self.c.post("/api/admin-storage-import-db", headers=h,
                           files={"file": ("backup.db", data, "application/octet-stream")})

    def test_round_trip(self):
        self.play(321)
        self.settings({"default_look": "lcd"})
        backup = self.download()
        self.play(999)
        self.settings({"default_look": "neon"})
        r = self.upload(backup)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(settings.get("default_look"), "lcd")           # cache dropped
        rows = self.get("/api/leaderboard?game=snake&mode=walls-normal").json()["rows"]
        self.assertEqual([x["score"] for x in rows], [321])

    def test_admin_only(self):
        self.assertEqual(self.get("/api/admin-storage-download-db", KABIR).status_code, 403)
        self.assertEqual(self.upload(b"x", KABIR).status_code, 403)

    def test_rejects_wrong_files(self):
        self.assertEqual(self.upload(b"not a database at all").status_code, 400)
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        other = sqlite3.connect(path)
        other.execute("CREATE TABLE notes (id INTEGER)")
        other.commit()
        other.close()
        with open(path, "rb") as f:
            r = self.upload(f.read())
        os.remove(path)
        self.assertEqual(r.status_code, 400)
        self.assertIn("doesn't look like a Household Arcade database", r.json()["detail"])
        self.assertEqual(self.get("/api/me").status_code, 200)            # the live database is untouched

    def test_newer_version_refused(self):
        backup = self.download()
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        with open(path, "wb") as f:
            f.write(backup)
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (db.LATEST + 5,))
        conn.commit()
        conn.close()
        with open(path, "rb") as f:
            r = self.upload(f.read())
        os.remove(path)
        self.assertEqual(r.status_code, 400)
        self.assertIn("newer version", r.json()["detail"])

    def test_older_database_is_migrated_on_import(self):
        """A database at schema version 1 (before children and limits) imports
        and is brought up to date straight away: no restart needed."""
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        old = sqlite3.connect(path)
        old.row_factory = sqlite3.Row
        self.assertEqual(db.migrate(old, upto=1), 1)
        old.execute("INSERT INTO users (id, name, username, created_at) VALUES ('u_dev', 'Dev Rao', 'dev', ?)",
                    (config.now_iso(),))
        old.execute("INSERT INTO scores (user_id, game, mode, score, level, seconds, started_at, ended_at, app_version) "
                    "VALUES ('u_dev', 'snake', 'wrap-fast', 777, 3, 200, ?, ?, '1.0.0')", (config.now_iso(), config.now_iso()))
        old.commit()
        cols = {r["name"] for r in old.execute("PRAGMA table_info(users)")}
        self.assertNotIn("is_child", cols)
        old.close()
        with open(path, "rb") as f:
            r = self.upload(f.read())
        os.remove(path)
        self.assertEqual(r.status_code, 200, r.text)
        with db.get_conn() as conn:
            self.assertEqual(db.schema_version(conn), db.LATEST)
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
            self.assertIn("is_child", cols)
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"child_limits", "extra_time", "holidays", "notify_log"} <= tables)
        rows = self.get("/api/leaderboard?game=snake&mode=wrap-fast").json()["rows"]
        self.assertEqual([(x["name"], x["score"]) for x in rows], [("Dev Rao", 777)])
        self.make_child(KABIR, minutesSchool=20)                          # the new tables work at once
        self.assertEqual(self.get("/api/me").json()["playTime"]["leftSeconds"], 20 * 60)

    def test_migrations_run_once(self):
        with db.get_conn() as conn:
            versions = [r[0] for r in conn.execute("SELECT version FROM schema_version ORDER BY version")]
        self.assertEqual(versions, [n for n, _d, _s in db.MIGRATIONS])
        db.init_db()
        with db.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0], len(db.MIGRATIONS))


if __name__ == "__main__":
    unittest.main()
