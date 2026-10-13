"""A database made by 2.6.1 upgrades cleanly at start-up: the gallery's date sort key is added and filled
from the dates already there, the new indexes appear, computed tithi dates (now kept in memory) are dropped
while hand corrections stay, and the app works on it — including the trash purge that used to fail on
merged people and told-by sources."""
from base import ADMIN, ApiTestCase, remove_db_files, sql
import _env  # noqa: F401

import sqlite3
import unittest

from app import config, db, graph, housekeeping, media, settings
from app.common import db_core

NEW_IN_262 = [("media", "date_sort", "TEXT")]
NEW_INDEXES = ["idx_media_sha", "idx_media_date", "idx_ml_event", "idx_ml_family", "idx_people_photo", "idx_cit_family"]
NOW = "2026-01-01T00:00:00Z"


def make_261_database(path):
    """The schema 2.6.1 left behind: SCHEMA, its migrations (without 2.6.2's), its one extra index."""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(db.SCHEMA)
    db_core.add_missing_columns(conn, [m for m in db.MIGRATIONS if m not in NEW_IN_262])
    conn.execute("CREATE INDEX IF NOT EXISTS idx_media_orig ON media(orig_sha256)")
    q = conn.execute
    for pid, name, merged, deleted in (("p1", "Ravi", None, None), ("p2", "Ravi", "p1", NOW), ("p3", "Sita", None, None),
                                       ("p4", "Teller", None, "2000-01-01T00:00:00Z")):
        q("INSERT INTO people (id, given_names, merged_into, created_at, updated_at, deleted_at) VALUES (?, ?, ?, ?, ?, ?)",
          (pid, name, merged, NOW, NOW, deleted))
    q("UPDATE people SET deleted_at = '2000-01-01T00:00:00Z' WHERE id = 'p1'")          # the kept one, trashed later
    for mid, dt in (("a" * 32, "3 MAR 1950"), ("b" * 32, None), ("c" * 32, "ABT 1920"), ("d" * 32, "not a date")):
        q("INSERT INTO media (id, kind, title, date_text, content_type, size, sha256, created_at) "
          "VALUES (?, 'photo', ?, ?, 'image/jpeg', 1, ?, ?)", (mid, mid[:1], dt, mid, NOW))
    q("INSERT INTO media_links (id, media_id, person_id) VALUES ('l1', ?, 'p3')", ("a" * 32,))
    q("INSERT INTO sources (id, title, kind, told_by_person_id, created_at) VALUES ('s1', 'Talk', 'interview', 'p4', ?)", (NOW,))
    q("INSERT INTO events (id, person_id, type, date_text, date_y, date_m, date_d, created_at, updated_at) "
      "VALUES ('e1', 'p3', 'death', '2 OCT 2000', 2000, 10, 2, ?, ?)", (NOW, NOW))
    q("INSERT INTO event_tithi (event_id, masa, paksha, tithi, source) VALUES ('e1', 7, 'krishna', 5, 'entered')")
    q("INSERT INTO tithi_dates (event_id, year, date, overridden_by, computed_at) VALUES ('e1', 2025, '2025-10-01', NULL, ?)", (NOW,))
    q("INSERT INTO tithi_dates (event_id, year, date, overridden_by, computed_at) VALUES ('e1', 2026, '2026-10-20', 'u-admin', ?)", (NOW,))
    q("INSERT INTO settings (key, value) VALUES ('tree_version', 'old')")
    conn.commit()
    conn.close()


class UpgradeFrom261(ApiTestCase):
    def setUp(self):
        super().setUp()
        remove_db_files()
        make_261_database(config.DB_PATH)
        cols = {r["name"] for r in sql("PRAGMA table_info(media)")}
        self.assertNotIn("date_sort", cols)                     # really the old shape
        db.init_db()
        settings.invalidate()
        with graph._lock:
            graph._cache["version"] = None
            graph._cache["graph"] = None
        media.check()

    def test_columns_backfill_and_indexes(self):
        rows = {r["id"][0]: r["date_sort"] for r in sql("SELECT id, date_sort FROM media")}
        self.assertEqual(rows["a"], media.date_sort("3 MAR 1950"))
        self.assertIsNotNone(rows["a"])
        self.assertIsNone(rows["b"])
        self.assertEqual(rows["c"], media.date_sort("ABT 1920"))
        self.assertIsNone(rows["d"])
        names = {r["name"] for r in sql("SELECT name FROM sqlite_master WHERE type = 'index'")}
        self.assertTrue(set(NEW_INDEXES) <= names, set(NEW_INDEXES) - names)
        # only the hand correction is left in the tithi table
        self.assertEqual(sql("SELECT year, overridden_by FROM tithi_dates"), [{"year": 2026, "overridden_by": "u-admin"}])
        self.assertEqual(sql("PRAGMA foreign_key_check"), [])
        # a second start changes nothing
        db.init_db()
        self.assertEqual({r["id"][0]: r["date_sort"] for r in sql("SELECT id, date_sort FROM media")}, rows)

    def test_the_app_works_on_it(self):
        items = self.ok(self.get("/api/media?sort=date", user=ADMIN))["items"]
        self.assertEqual([m["title"] for m in items], ["c", "a", "d", "b"])
        purged = self.ok(self.delete("/api/trash", user=ADMIN))["purged"]
        self.assertEqual(purged["people"], 3)                   # p1 (others merged into it), p2, the teller
        self.assertNotIn("skipped", purged)
        self.assertEqual(sql("SELECT told_by_person_id FROM sources"), [{"told_by_person_id": None}])
        self.assertEqual(sql("SELECT id FROM people"), [{"id": "p3"}])

    def test_purge_runs_from_housekeeping_too(self):
        counts = housekeeping.purge_expired()
        self.assertGreaterEqual(counts["people"], 2)
        self.assertNotIn("skipped", counts)


if __name__ == "__main__":
    unittest.main()
