"""Trash (SPEC §7.4): delete to .trash with .item.json, restore (name clashes, the folder gone), what comes back
(shares, ticks, versions), who may restore and empty, and emptying after trash_days."""
import _env  # noqa: F401

import json
import os
import unittest

from app import db
from app.store import trash
from base import DOCS, KABIR, MEERA, ApiBase, person_dir


class Trash(ApiBase):
    def trash_items(self, h=KABIR):
        return self.ok(self.get("/api/space/trash", h))["items"]

    def test_delete_and_restore_keeps_shares_and_history(self):
        f = self.create("folder", "Trip")
        n = self.note("Plan", f["id"], text="one")
        self.save(n["id"], "two")
        self.ok(self.share(n["id"], MEERA, "viewer"))
        self.ok(self.delete(f"/api/nodes/{f['id']}"))
        self.assertFalse(os.path.exists(os.path.join(person_dir("Kabir Rao"), "Trip")))
        [t] = self.trash_items()
        tdir = os.path.join(DOCS, ".trash", "Kabir Rao", t["id"])
        self.assertTrue(os.path.isfile(os.path.join(tdir, "Trip", "Plan.txt")))
        with open(os.path.join(tdir, ".item.json"), encoding="utf-8") as fh:
            item = json.load(fh)
        self.assertEqual((item["originalPath"], item["nodeId"], item["deletedBy"]), ("Trip", f["id"], "u_kabir"))
        self.assertEqual(self.get(f"/api/docs/{n['id']}", MEERA).status_code, 404)
        self.assertEqual(self.get(f"/api/docs/{n['id']}").status_code, 404)
        self.assertEqual(self.ok(self.get("/api/search?q=Plan"))["results"], [])
        # a scan doesn't see trashed items as gone (or new)
        self.scan()
        self.ok(self.post(f"/api/trash/{t['id']}/restore"))
        self.assertEqual(self.open(n["id"], MEERA)["text"], "two")
        self.assertEqual(len(self.ok(self.get(f"/api/docs/{n['id']}/versions"))["versions"]), 1)
        self.assertEqual(self.trash_items(), [])
        self.assertFalse(os.path.exists(tdir))

    def test_restore_with_a_name_clash(self):
        n = self.note("Plan", text="old")
        self.ok(self.delete(f"/api/nodes/{n['id']}"))
        self.note("Plan", text="new one")
        [t] = self.trash_items()
        self.ok(self.post(f"/api/trash/{t['id']}/restore"))
        self.assertEqual(self.row(n["id"])["name"], "Plan (restored).txt")
        n2 = self.note("Again")
        self.ok(self.delete(f"/api/nodes/{n2['id']}"))
        self.note("Again")
        self.note("Again (restored)")
        [t] = self.trash_items()
        self.ok(self.post(f"/api/trash/{t['id']}/restore"))
        self.assertEqual(self.row(n2["id"])["name"], "Again (restored 2).txt")

    def test_restore_when_the_folder_is_gone(self):
        f = self.create("folder", "Old")
        n = self.note("Inside", f["id"])
        self.ok(self.delete(f"/api/nodes/{n['id']}"))
        self.ok(self.delete(f"/api/nodes/{f['id']}"))
        t_note = [t for t in self.trash_items() if t["name"] == "Inside.txt"][0]
        self.ok(self.post(f"/api/trash/{t_note['id']}/restore"))
        self.assertEqual(self.row(n["id"])["rel"], "Inside.txt")          # the top of My docs
        self.assertIsNone(self.row(n["id"])["parent_id"])

    def test_editor_deletes_into_the_owners_trash(self):
        f = self.create("folder", "Shared")
        n = self.note("Theirs", f["id"])
        self.ok(self.share(f["id"], MEERA, "editor"))
        self.ok(self.delete(f"/api/nodes/{n['id']}", MEERA))
        self.assertEqual(self.trash_items(MEERA), [])
        [t] = self.trash_items()
        self.assertEqual(t["deletedByName"], "Meera Rao")
        self.assertEqual(self.post(f"/api/trash/{t['id']}/restore", {}, MEERA).status_code, 404)
        self.ok(self.post(f"/api/trash/{t['id']}/restore"))

    def test_empty_and_purge(self):
        a = self.note("A", text="a")
        self.save(a["id"], "aa")
        b = self.note("B")
        self.ok(self.delete(f"/api/nodes/{a['id']}"))
        self.ok(self.delete(f"/api/nodes/{b['id']}"))
        self.ok(self.post("/api/trash/empty", {}, MEERA))                 # someone else's Trash: nothing
        self.assertEqual(len(self.trash_items()), 2)
        self.clock.advance(days=31)
        c = self.note("C")
        self.ok(self.delete(f"/api/nodes/{c['id']}"))
        self.assertEqual(trash.purge_old(), 2)
        self.assertIsNone(self.row(a["id"]))
        self.assertFalse(os.path.exists(os.path.join(DOCS, ".versions", a["id"])))
        self.assertEqual([t["name"] for t in self.trash_items()], ["C.txt"])
        self.ok(self.post("/api/trash/empty"))
        self.assertEqual(self.trash_items(), [])
        self.assertEqual(os.listdir(os.path.join(DOCS, ".trash", "Kabir Rao")), [])

    def test_admin_delete_persons_docs(self):
        from base import ASHA
        self.note("One")
        self.create("folder", "Two")
        r = self.post(f"/api/admin/users/u_kabir/docs/delete", {"confirm": "wrong"}, ASHA)
        self.assertEqual(r.status_code, 422)
        out = self.ok(self.post(f"/api/admin/users/u_kabir/docs/delete", {"confirm": "Kabir Rao"}, ASHA))
        self.assertEqual(out["moved"], 2)
        self.assertEqual(os.listdir(person_dir("Kabir Rao")), [])
        self.assertEqual(len(self.trash_items()), 2)

    def test_trash_rows_stay_out_of_the_way(self):
        n = self.note("Same")
        self.ok(self.delete(f"/api/nodes/{n['id']}"))
        again = self.note("Same")                                         # the name is free again
        self.assertEqual(again["name"], "Same.txt")
        with db.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM nodes WHERE trash_id IS NOT NULL").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
