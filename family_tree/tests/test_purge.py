"""The trash purge (daily, and Admin → Trash → Empty trash) with the references that have no
ON DELETE rule of their own: sources told by a person, a source's scan, people merged into
someone, and soft-deleted sources with citations. One row the database refuses must not
keep the rest of the trash from emptying."""
from base import ADMIN, ApiTestCase, image_bytes, sql

import unittest

from app import db, housekeeping


class Purge(ApiTestCase):
    def empty(self):
        return self.ok(self.delete("/api/trash", user=ADMIN))["purged"]

    def source(self, **body):
        return self.ok(self.post("/api/sources", {"title": "Letter", **body}), 201)["source"]["id"]

    def test_person_who_told_a_source(self):
        teller = self.person("Granny")
        sid = self.source(kind="interview", toldBy=teller)
        self.ok(self.delete(f"/api/people/{teller}"))
        purged = self.empty()
        self.assertEqual(purged["people"], 1)
        self.assertNotIn("skipped", purged)
        self.assertEqual(sql("SELECT told_by_person_id FROM sources WHERE id = ?", (sid,)), [{"told_by_person_id": None}])
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM people")[0]["n"], 0)

    def test_photo_used_as_a_source_scan(self):
        pid = self.person("Ann")
        mid = self.ok(self.post(f"/api/people/{pid}/photo", files={"file": ("p.jpg", image_bytes(), "image/jpeg")}), 201)["mediaId"]
        sid = self.source(kind="certificate", mediaId=mid)
        self.ok(self.delete(f"/api/media/{mid}"))
        purged = self.empty()
        self.assertEqual(purged["media"], 1)
        self.assertEqual(sql("SELECT media_id FROM sources WHERE id = ?", (sid,)), [{"media_id": None}])
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM media")[0]["n"], 0)

    def test_soft_deleted_source_with_citations_is_listed_and_purged(self):
        pid = self.person("Ann")
        sid = self.source()
        self.ok(self.post("/api/citations", {"sourceId": sid, "personId": pid}), 201)
        self.ok(self.delete(f"/api/sources/{sid}"))
        items = self.ok(self.get("/api/trash", user=ADMIN))["items"]
        self.assertEqual([(i["entity"], i["id"], i["name"]) for i in items], [("sources", sid, "Letter")])
        # restorable from the trash like everything else
        self.ok(self.post(f"/api/trash/sources/{sid}/restore", user=ADMIN))
        self.assertEqual(sql("SELECT deleted_at FROM sources WHERE id = ?", (sid,)), [{"deleted_at": None}])
        self.ok(self.delete(f"/api/sources/{sid}"))
        purged = self.empty()
        self.assertEqual(purged["sources"], 1)
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM sources")[0]["n"], 0)
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM citations")[0]["n"], 0)
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM people")[0]["n"], 1)

    def test_person_others_were_merged_into(self):
        keep = self.person("Ravi", "Rao")
        other = self.person("Ravi", "Rao")
        self.ok(self.post(f"/api/people/{keep}/merge", {"otherId": other, "choose": {}}))
        self.assertEqual(sql("SELECT merged_into FROM people WHERE id = ?", (other,)), [{"merged_into": keep}])
        self.ok(self.delete(f"/api/people/{keep}"))
        # the kept person is purged first (newest first or not, the order mustn't matter)
        sql("UPDATE people SET deleted_at = '2000-01-01T00:00:00Z' WHERE id = ?", (keep,))
        purged = self.empty()
        self.assertEqual(purged["people"], 2)
        self.assertNotIn("skipped", purged)
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM people")[0]["n"], 0)

    def test_merged_person_still_in_trash_when_kept_one_goes(self):
        keep = self.person("Ravi", "Rao")
        other = self.person("Ravi", "Rao")
        self.ok(self.post(f"/api/people/{keep}/merge", {"otherId": other, "choose": {}}))
        self.ok(self.delete(f"/api/people/{keep}"))
        sql("UPDATE people SET deleted_at = '2000-01-01T00:00:00Z' WHERE id = ?", (keep,))
        sql("UPDATE people SET deleted_at = '2999-01-01T00:00:00Z' WHERE id = ?", (other,))
        self.assertEqual(housekeeping.purge(older_than_days=30)["people"], 1)
        self.assertEqual(sql("SELECT id, merged_into FROM people"), [{"id": other, "merged_into": None}])

    def test_one_refused_row_does_not_block_the_rest(self):
        stuck, gone = self.person("Stuck"), self.person("Gone")
        fid = self.family(self.person("Keeper"), None)
        # a reference the purge doesn't know about (as a future table might add)
        sql("CREATE TABLE pinned (person_id TEXT REFERENCES people(id))")
        sql("INSERT INTO pinned VALUES (?)", (stuck,))
        for p in (stuck, gone):
            self.ok(self.delete(f"/api/people/{p}"))
        self.ok(self.delete(f"/api/families/{fid}"))
        purged = self.empty()
        self.assertEqual((purged["people"], purged["families"], purged["skipped"]), (1, 1, 1))
        left = {(i["entity"], i["id"]) for i in self.ok(self.get("/api/trash", user=ADMIN))["items"]}
        self.assertEqual(left, {("people", stuck)})
        # the refused person is untouched (savepoint), the purge is committed
        self.assertEqual(len(sql("SELECT id FROM people WHERE id = ?", (stuck,))), 1)
        self.assertEqual(sql("SELECT id FROM people WHERE id = ?", (gone,)), [])

    def test_daily_purge_runs_with_everything_at_once(self):
        teller = self.person("Teller")
        keep, other = self.person("Ravi", "Rao"), self.person("Ravi", "Rao")
        self.ok(self.post(f"/api/people/{keep}/merge", {"otherId": other, "choose": {}}))
        sid = self.source(toldBy=teller)
        for p in (teller, keep):
            self.ok(self.delete(f"/api/people/{p}"))
        self.ok(self.delete(f"/api/sources/{sid}"))
        sql("UPDATE people SET deleted_at = '2000-01-01T00:00:00Z' WHERE deleted_at IS NOT NULL")
        sql("UPDATE sources SET deleted_at = '2000-01-01T00:00:00Z'")
        counts = housekeeping.purge_expired()
        self.assertEqual((counts["people"], counts["sources"]), (3, 1))
        with db.get_conn() as conn:
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])


if __name__ == "__main__":
    unittest.main()
