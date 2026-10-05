"""Choosing the documents folder (SPEC §5.6): the first run, the folder browser, the checks on any location
(other install, Household Chat's folder, overlap with an admin shared folder, read-only, missing mount, files
without a marker), and what happens while the folder is missing."""
import _env  # noqa: F401

import json
import os
import unittest
from unittest import mock

from app import db
from app.store import marker, roots
from base import ASHA, DOCS, KABIR, SHARE, ApiBase, person_dir, write


class DocsFolder(ApiBase):
    def inspect(self, path):
        return self.ok(self.post("/api/admin/docs-folder/inspect", {"path": path}, ASHA))

    def test_first_run_uses_the_default_and_admins_confirm(self):
        with open(os.path.join(DOCS, ".household_docs"), encoding="utf-8") as f:
            info = json.load(f)
        with db.get_conn() as conn:
            self.assertEqual(info["install"], roots.install_id(conn))
        for d in ("people", ".trash", ".versions", ".tmp"):
            self.assertTrue(os.path.isdir(os.path.join(DOCS, d)), d)
        self.assertTrue(os.path.isdir(person_dir("Kabir Rao")))          # made on the first visit
        me = self.ok(self.get("/api/me", ASHA))
        self.assertTrue(me["docsFolder"]["ok"])
        self.assertFalse(me["docsFolder"]["confirmed"])                  # the admin's first-run card
        self.assertNotIn("path", self.ok(self.get("/api/me"))["docsFolder"])   # people don't get the path
        self.assertEqual(self.post("/api/admin/docs-folder/choose", {"path": "/share/household_docs"}).status_code, 403)
        out = self.ok(self.post("/api/admin/docs-folder/choose", {"path": "/share/household_docs"}, ASHA))
        self.assertTrue(out["confirmed"])
        self.assertTrue(self.ok(self.get("/api/me", ASHA))["docsFolder"]["confirmed"])
        st = self.ok(self.get("/api/admin/docs-folder", ASHA))
        self.assertEqual((st["path"], st["ok"], st["people"]), ("/share/household_docs", True, 3))
        self.assertIsInstance(st["free"], int)

    def test_choose_another_folder_before_anyone_has_documents(self):
        os.makedirs(os.path.join(SHARE, "nas"))
        out = self.ok(self.post("/api/admin/docs-folder/choose", {"path": "/share/nas/docs"}, ASHA))
        self.assertEqual(out["path"], "/share/nas/docs")
        new = os.path.join(SHARE, "nas", "docs")
        self.assertTrue(os.path.isfile(os.path.join(new, ".household_docs")))
        self.assertTrue(os.path.isdir(os.path.join(new, "people", "Kabir Rao")))
        n = self.note("Hello", text="hi")
        self.assertTrue(self.real(n["id"]).startswith(os.path.realpath(new)))
        self.assertTrue(os.path.isdir(DOCS))                              # the old folder is left as it is
        self.assertEqual(self.post("/api/admin/docs-folder/choose", {"path": "/share/other"}, ASHA).status_code, 409)

    def test_refusals(self):
        self.assertEqual(self.post("/api/admin/docs-folder/inspect", {"path": "/share"}, ASHA).json()["verdict"], "refused")
        self.assertEqual(self.inspect("/etc")["verdict"], "refused")
        self.assertEqual(self.inspect("/share/../etc")["verdict"], "refused")
        self.assertEqual(self.inspect("/share/.hidden")["verdict"], "refused")
        self.assertEqual(self.inspect("/share/missing/docs")["verdict"], "parent_missing")
        write(os.path.join(SHARE, "afile"), "x")
        self.assertEqual(self.inspect("/share/afile")["verdict"], "refused")
        write(os.path.join(SHARE, "full", "something.txt"), "x")
        self.assertEqual(self.inspect("/share/full")["verdict"], "has_files")
        os.makedirs(os.path.join(SHARE, "empty"))
        self.assertTrue(self.inspect("/share/empty")["ok"])
        self.assertEqual(self.inspect("/share/brand_new")["verdict"], "new")
        self.assertEqual(self.inspect("/share/household_docs")["verdict"], "ours")
        # another install's folder
        os.makedirs(os.path.join(SHARE, "theirs"))
        marker.write_marker(os.path.join(SHARE, "theirs"), "another-install")
        self.assertEqual(self.inspect("/share/theirs")["verdict"], "other_install")
        # Household Chat's files folder, inside it, or containing it
        write(os.path.join(SHARE, "household_chat", ".household_chat_store"), "chat-id")
        self.assertEqual(self.inspect("/share/household_chat")["verdict"], "in_chat")
        self.assertEqual(self.inspect("/share/household_chat/docs")["verdict"], "in_chat")
        write(os.path.join(SHARE, "box", "household_chat", ".household_chat_store"), "chat-id")
        self.assertEqual(self.inspect("/share/box")["verdict"], "in_chat")
        # overlap with an admin shared folder (either way round)
        os.makedirs(os.path.join(SHARE, "House", "Papers"))
        with db.get_conn() as conn:
            conn.execute("INSERT INTO roots (id, kind, path, label, created_at) VALUES ('r_s', 'shared', 'House/Papers', 'Papers', 'x')")
        self.assertEqual(self.inspect("/share/House")["verdict"], "overlap")
        self.assertEqual(self.inspect("/share/House/Papers/docs")["verdict"], "overlap")
        # read-only
        os.makedirs(os.path.join(SHARE, "ro"))
        with mock.patch("app.store.marker.os.access", return_value=False):
            self.assertEqual(self.inspect("/share/ro")["verdict"], "read_only")
            self.assertEqual(self.inspect("/share/ro/new")["verdict"], "read_only")
        r = self.post("/api/admin/docs-folder/choose", {"path": "/share/full"}, ASHA)
        self.assertEqual(r.status_code, 409)

    def test_a_link_out_of_share_is_refused(self):
        os.symlink("/tmp", os.path.join(SHARE, "escape"))
        self.assertEqual(self.inspect("/share/escape")["verdict"], "refused")

    def test_missing_storage_stops_writes_and_comes_back(self):
        n = self.note("Plan", text="kept")
        self.ok(self.post("/api/admin/docs-folder/choose", {"path": "/share/household_docs"}, ASHA))
        os.rename(DOCS, DOCS + "-away")                                  # the network drive dropped
        roots.check()
        me = self.ok(self.get("/api/me"))
        self.assertFalse(me["docsFolder"]["ok"])
        self.assertIn("Documents folder not found", me["docsFolder"]["message"])
        self.assertEqual(self.post("/api/docs", {"kind": "note", "name": "New"}).status_code, 503)
        self.assertEqual(self.get(f"/api/docs/{n['id']}").status_code, 503)
        self.assertEqual(self.get("/api/list").status_code, 200)          # the lists still answer
        self.assertFalse(os.path.exists(DOCS))                            # nothing recreated it
        self.scan()
        self.assertIsNone(self.row(n["id"])["gone_at"])                   # the index isn't touched
        os.rename(DOCS + "-away", DOCS)
        roots.check()
        self.assertEqual(self.open(n["id"])["text"], "kept")

    def test_marker_of_another_install_at_start(self):
        with open(os.path.join(DOCS, ".household_docs"), "w", encoding="utf-8") as f:
            json.dump({"install": "someone-else"}, f)
        st = roots.check()
        self.assertFalse(st["ok"])
        self.assertEqual(st["verdict"], "other_install")
        self.assertEqual(self.post("/api/docs", {"kind": "note", "name": "New"}).status_code, 503)

    def test_folder_browser(self):
        os.makedirs(os.path.join(SHARE, "Documents", "House"))
        os.makedirs(os.path.join(SHARE, ".private"))
        write(os.path.join(SHARE, "file.txt"), "x")
        write(os.path.join(SHARE, "household_chat", ".household_chat_store"), "x")
        out = self.ok(self.get("/api/admin/share-dirs", ASHA))
        self.assertEqual(out["path"], "/share")
        names = {f["name"]: f for f in out["folders"]}
        self.assertEqual(sorted(names), ["Documents", "household_chat", "household_docs"])
        self.assertTrue(names["household_docs"]["docs"])
        self.assertTrue(names["household_chat"]["chat"])
        sub = self.ok(self.get("/api/admin/share-dirs?path=/share/Documents", ASHA))
        self.assertEqual([f["name"] for f in sub["folders"]], ["House"])
        self.assertEqual(self.get("/api/admin/share-dirs?path=../..", ASHA).status_code, 404)
        self.assertEqual(self.get("/api/admin/share-dirs?path=nope", ASHA).status_code, 404)
        self.assertEqual(self.get("/api/admin/share-dirs", KABIR).status_code, 403)


if __name__ == "__main__":
    unittest.main()
