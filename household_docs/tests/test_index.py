"""The index (SPEC §5.5): files added, changed, renamed (matched by inode, or by size + SHA-256 when the inode
changed), moved, deleted, gone and back, purged after 30 days; one-folder scans when a folder is opened; the
stat on open; hidden entries and links skipped. Ids — and so shares, ticks and favourites — survive."""
import _env  # noqa: F401

import os
import shutil
import time
import unittest

from app import db
from app.store import index
from base import KABIR, MEERA, ApiBase, person_dir, read, write


class Index(ApiBase):
    def setUp(self):
        super().setUp()
        self.home = person_dir("Kabir Rao")

    def names(self, folder=None, h=KABIR):
        q = f"?node={folder}" if folder else ""
        return sorted(i["name"] for i in self.ok(self.get("/api/list" + q, h))["items"])

    def find(self, name):
        with db.get_conn() as conn:
            r = conn.execute("SELECT * FROM nodes WHERE name = ?", (name,)).fetchone()
        return r

    def test_new_files_and_folders_from_outside(self):
        write(os.path.join(self.home, "Bills", "2026", "gas.txt"), "meter reading 4711")
        write(os.path.join(self.home, "photo.jpg"), b"\xff\xd8\xff\xe0jpeg")
        stats = index.scan_root(self.root_id())
        self.assertEqual(stats["new"], 4)
        self.assertEqual(self.names(), ["Bills", "photo.jpg"])
        gas = self.find("gas.txt")
        self.assertEqual(gas["kind"], "note")
        self.assertIsNone(gas["created_by"])
        self.assertEqual(self.find("photo.jpg")["kind"], "file")
        self.assertEqual(self.find("2026")["parent_id"], self.find("Bills")["id"])
        hits = self.ok(self.get("/api/search?q=4711"))["results"]
        self.assertEqual([h["name"] for h in hits], ["gas.txt"])
        again = index.scan_root(self.root_id())
        self.assertEqual((again["new"], again["changed"]), (0, 0))

    def test_changed_outside(self):
        n = self.note("Plan", text="from the app")
        self.assertEqual(self.row(n["id"])["updated_by"], "u_kabir")
        time.sleep(0.01)
        write(self.real(n["id"]), "changed elsewhere, word zebra")
        stats = index.scan_root(self.root_id())
        self.assertEqual(stats["changed"], 1)
        row = self.row(n["id"])
        self.assertIsNone(row["updated_by"])
        self.assertEqual(row["size"], len("changed elsewhere, word zebra"))
        self.assertEqual([h["id"] for h in self.ok(self.get("/api/search?q=zebra"))["results"]], [n["id"]])

    def test_touched_but_unchanged_keeps_the_editor(self):
        n = self.note("Plan", text="same")
        st = os.stat(self.real(n["id"]))
        os.utime(self.real(n["id"]), ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
        index.scan_root(self.root_id())
        self.assertEqual(self.row(n["id"])["updated_by"], "u_kabir")

    def test_rename_and_move_outside_keep_the_id_shares_and_ticks(self):
        f = self.create("folder", "Trip")
        c = self.create("checklist", "Packing", f["id"])
        items = self.ok(self.ops(c["id"], [{"op": "add", "text": "Socks"}]))["items"]
        self.ok(self.ops(c["id"], [{"op": "tick", "key": items[0]["key"]}]))
        self.ok(self.share(c["id"], MEERA, "viewer"))
        self.ok(self.post(f"/api/nodes/{c['id']}/favourite", {"value": True}))
        os.makedirs(os.path.join(self.home, "Archive"))
        os.rename(os.path.join(self.home, "Trip", "Packing.md"), os.path.join(self.home, "Archive", "Packing 2026.md"))
        stats = index.scan_root(self.root_id())
        self.assertEqual(stats["moved"], 1)
        row = self.row(c["id"])
        self.assertEqual((row["rel"], row["gone_at"]), ("Archive/Packing 2026.md", None))
        doc = self.open(c["id"], MEERA)
        self.assertEqual(doc["role"], "viewer")
        self.assertEqual(doc["items"][0]["doneByName"], "Kabir Rao")
        self.assertEqual([i["id"] for i in self.ok(self.get("/api/space/favourites"))["items"]], [c["id"]])

    def test_folder_renamed_outside_keeps_everything_inside(self):
        f = self.create("folder", "House")
        n = self.note("Gas", f["id"], text="x")
        self.ok(self.share(f["id"], MEERA, "editor"))
        os.rename(os.path.join(self.home, "House"), os.path.join(self.home, "Home"))
        index.scan_root(self.root_id())
        self.assertEqual(self.row(f["id"])["rel"], "Home")
        self.assertEqual(self.row(n["id"])["rel"], "Home/Gas.txt")
        self.assertEqual(self.open(n["id"], MEERA)["role"], "editor")

    def test_inode_changed_matched_by_hash(self):
        n = self.note("Recipe", text="flour, eggs, sugar")
        self.ok(self.share(n["id"], MEERA, "viewer"))
        src = self.real(n["id"])
        dst = os.path.join(self.home, "Recipe copy.txt")
        shutil.copyfile(src, dst)                      # copy + delete: a new inode
        os.remove(src)
        index.scan_root(self.root_id())
        self.assertEqual(self.row(n["id"])["rel"], "Recipe copy.txt")
        self.assertEqual(self.open(n["id"], MEERA)["text"], "flour, eggs, sugar")

    def test_deleted_gone_and_back_and_purged(self):
        n = self.note("Temp", text="keep me")
        self.ok(self.share(n["id"], MEERA, "viewer"))
        path = self.real(n["id"])
        data = read(path)
        os.remove(path)
        stats = index.scan_root(self.root_id())
        self.assertEqual(stats["gone"], 1)
        self.assertIsNotNone(self.row(n["id"])["gone_at"])
        self.assertEqual(self.get(f"/api/docs/{n['id']}", MEERA).status_code, 404)
        self.assertNotIn("Temp.txt", self.names())
        write(path, data)                              # back (a restored backup of /share, say)
        stats = index.scan_root(self.root_id())
        self.assertEqual(stats["back"], 1)
        self.assertEqual(self.open(n["id"], MEERA)["text"], "keep me")
        os.remove(path)
        index.scan_root(self.root_id())
        self.clock.advance(days=29)
        self.assertEqual(index.purge_gone(), 0)
        self.clock.advance(days=2)
        self.assertEqual(index.purge_gone(), 1)
        self.assertIsNone(self.row(n["id"]))
        with db.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM shares").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM fts WHERE node_id = ?", (n["id"],)).fetchone()[0], 0)

    def test_opening_a_folder_scans_it(self):
        f = self.create("folder", "Inbox")
        write(os.path.join(self.home, "Inbox", "dropped.txt"), "via Samba")
        self.assertEqual(self.names(f["id"]), ["dropped.txt"])
        os.remove(os.path.join(self.home, "Inbox", "dropped.txt"))
        self.assertEqual(self.names(f["id"]), [])

    def test_moved_to_a_folder_opened_first_keeps_the_id(self):
        a = self.create("folder", "A")
        b = self.create("folder", "B")
        n = self.note("Moving", a["id"], text="me")
        os.rename(os.path.join(self.home, "A", "Moving.txt"), os.path.join(self.home, "B", "Moving.txt"))
        self.assertEqual(self.names(b["id"]), ["Moving.txt"])        # one-folder scan of B finds it moved
        self.assertEqual(self.row(n["id"])["rel"], "B/Moving.txt")
        self.assertEqual(self.names(a["id"]), [])
        with db.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM nodes WHERE name = 'Moving.txt'").fetchone()[0], 1)

    def test_open_stats_the_file_first(self):
        n = self.note("Plan", text="old")
        time.sleep(0.01)
        write(self.real(n["id"]), "new from outside")
        self.assertEqual(self.open(n["id"])["text"], "new from outside")
        self.assertIsNone(self.row(n["id"])["updated_by"])

    def test_hidden_entries_and_links_are_skipped(self):
        write(os.path.join(self.home, ".secret"), "x")
        write(os.path.join(self.home, ".hidden", "inner.txt"), "x")
        outside = os.path.join(_env.os.environ["SHARE_DIR"], "elsewhere")
        write(os.path.join(outside, "other.txt"), "not yours")
        os.symlink(outside, os.path.join(self.home, "link"))
        os.symlink(os.path.join(outside, "other.txt"), os.path.join(self.home, "file-link.txt"))
        index.scan_root(self.root_id())
        self.assertEqual(self.names(), [])

    def test_kind_mismatch_replaced(self):
        n = self.note("Thing")
        os.remove(self.real(n["id"]))
        os.mkdir(os.path.join(self.home, "Thing.txt"))
        index.scan_root(self.root_id())
        self.assertIsNone(self.row(n["id"]))
        self.assertEqual(self.find("Thing.txt")["kind"], "folder")

    def test_depth_limit(self):
        deep = os.path.join(self.home, *[f"d{i}" for i in range(12)])
        write(os.path.join(deep, "deep.txt"), "x")
        index.scan_root(self.root_id())
        self.assertIsNone(self.find("deep.txt"))
        self.assertIsNotNone(self.find("d9"))
        self.assertIsNone(self.find("d10"))

    def test_capped_walk_doesnt_mark_the_rest_gone(self):
        for i in range(5):
            write(os.path.join(self.home, f"f{i}.txt"), str(i))
        index.scan_root(self.root_id())
        old = index.MAX_ENTRIES
        index.MAX_ENTRIES = 2
        try:
            stats = index.scan_root(self.root_id())
        finally:
            index.MAX_ENTRIES = old
        self.assertTrue(stats["capped"])
        self.assertEqual(stats["gone"], 0)

    def test_restoring_pauses_scans(self):
        rid = self.root_id()
        db.RESTORING.set()
        try:
            self.assertIsNone(index.scan_root(rid))
            self.assertEqual(self.get("/api/list").status_code, 503)
        finally:
            db.RESTORING.clear()


if __name__ == "__main__":
    unittest.main()


class Housekeeping(ApiBase):
    def test_tick_scans_checks_and_cleans(self):
        from app import housekeeping
        home = person_dir("Kabir Rao")
        write(os.path.join(home, "found.txt"), "by the scan")
        self.assertTrue(housekeeping.scan_due(force=True))
        self.assertFalse(housekeeping.scan_due())                      # not again before scan_minutes
        self.assertEqual([i["name"] for i in self.ok(self.get("/api/space/mine"))["items"]], ["found.txt"])
        n = self.note("Old")
        self.ok(self.delete(f"/api/nodes/{n['id']}"))
        tmp = os.path.join(_env.os.environ["SHARE_DIR"], "household_docs", ".tmp", "w-left.part")
        write(tmp, "half", mtime=time.time() - 7200)
        self.clock.advance(days=31)
        housekeeping.hourly()
        self.assertIsNone(self.row(n["id"]))
        self.assertFalse(os.path.exists(tmp))
        for i in range(6):                                              # the minute ticks never raise
            housekeeping.tick(i)
