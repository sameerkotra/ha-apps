"""Admin shared folders (SPEC §9): add / change / rename / Rescan now / Stop sharing; what can't be shared (/share
itself, hidden folders, links out, the documents folder inside or around it, Household Chat's files folder, another
install's documents folder, another shared folder); read only vs read and write for each person and Everyone; the
folder's own .trash and .versions; a missing folder hidden from everyone but admins; a folder later swapped for a
link refused; nothing on disk changes when sharing stops."""
import _env  # noqa: F401

import json
import os
import unittest

from app import db, sharing
from app.store import index
from base import ASHA, DEV, KABIR, MEERA, SHARE, ApiBase, write

HOUSE = os.path.join(SHARE, "Documents", "House")


class SharedBase(ApiBase):
    PEOPLE = (ASHA, KABIR, MEERA, DEV)

    def setUp(self):
        super().setUp()
        write(os.path.join(HOUSE, "Taxes", "2026", "return.txt"), "tax return words")
        write(os.path.join(HOUSE, "Taxes", "notes.md"), "- [ ] file taxes\n- [x] find receipts\n")
        write(os.path.join(HOUSE, "manual.pdf"), b"%PDF-1.4 not really")
        write(os.path.join(HOUSE, ".hidden", "secret.txt"), "hidden")

    def add(self, path="/share/Documents/House", label="House papers", access=None, h=ASHA, code=201):
        r = self.post("/api/admin/shared-folders", {"path": path, "label": label, "access": access or {}}, h)
        self.assertEqual(r.status_code, code, r.text)
        return r.json()

    def house(self, access=None):
        acc = {"u_kabir": "ro", "u_meera": "rw"} if access is None else access
        out = self.add(access=acc)
        return out["id"]

    def listing(self, ref, h=KABIR):
        return self.ok(self.get(f"/api/list?node={ref}", h))


class AdminFolders(SharedBase):
    def test_add_list_change_and_stop(self):
        rid = self.house()
        folders = self.ok(self.get("/api/admin/shared-folders", ASHA))["folders"]
        self.assertEqual(len(folders), 1)
        f = folders[0]
        self.assertEqual((f["label"], f["path"], f["exists"], f["everyone"]), ("House papers", "/share/Documents/House", True, "none"))
        self.assertEqual(f["access"], {"u_kabir": "ro", "u_meera": "rw"})
        self.assertEqual(f["files"], 3)                        # indexed straight away; the hidden folder isn't
        self.assertNotIn("tax return", json.dumps(f))
        self.assertEqual(self.get("/api/admin/shared-folders", KABIR).status_code, 403)
        # rename and change access
        out = self.ok(self.patch(f"/api/admin/shared-folders/{rid}", {"label": "Papers", "access": {"*": "ro"}}, ASHA))
        self.assertEqual(out["folders"][0]["label"], "Papers")
        self.assertEqual(out["folders"][0]["everyone"], "ro")
        self.assertEqual(out["folders"][0]["access"], {})
        self.assertEqual([x["label"] for x in self.ok(self.get("/api/shared-folders", DEV))["folders"]], ["Papers"])
        # rescan finds a new file
        write(os.path.join(HOUSE, "new.txt"), "fresh")
        out = self.ok(self.post(f"/api/admin/shared-folders/{rid}/rescan", {}, ASHA))
        self.assertEqual(out["scan"]["new"], 1)
        # stop sharing: forgotten in the app, nothing on disk changes
        before = sorted(os.walk(HOUSE))
        self.ok(self.delete(f"/api/admin/shared-folders/{rid}", ASHA))
        self.assertEqual(sorted(os.walk(HOUSE)), before)
        self.assertEqual(self.ok(self.get("/api/shared-folders", DEV))["folders"], [])
        with db.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM nodes WHERE root_id = ?", (rid,)).fetchone()[0], 0)

    def test_validation(self):
        rid = self.house()
        self.assertEqual(self.post("/api/admin/shared-folders", {"path": "/share/Documents", "label": "x", "access": {}, "extra": 1}, ASHA).status_code, 422)
        os.makedirs(os.path.join(SHARE, "Other"))
        self.add("/share/Other", "house PAPERS", code=409)                       # names are unique
        self.add("/share/Other", "", code=422)
        self.add("/share/Other", "x", {"u_kabir": "admin"}, code=422)
        self.add("/share/Other", "x", {"u_ghost": "ro"}, code=404)
        self.assertEqual(self.patch(f"/api/admin/shared-folders/{rid}", {"path": "/share/x"}, ASHA).status_code, 422)
        self.assertEqual(self.patch("/api/admin/shared-folders/nope", {"label": "y"}, ASHA).status_code, 404)

    def test_what_cant_be_shared(self):
        os.makedirs(os.path.join(SHARE, "chat", "files"))
        write(os.path.join(SHARE, "chat", "files", ".household_chat_store"), "x")
        os.makedirs(os.path.join(SHARE, "elsewhere", "docs"))
        write(os.path.join(SHARE, "elsewhere", "docs", ".household_docs"), '{"install": "other"}')
        outside = os.path.join(os.path.dirname(SHARE.rstrip("/")), "outside_" + os.path.basename(SHARE.rstrip("/")))
        os.makedirs(outside, exist_ok=True)
        if os.path.lexists(os.path.join(SHARE, "link_out")):
            os.remove(os.path.join(SHARE, "link_out"))
        os.symlink(outside, os.path.join(SHARE, "link_out"))
        os.symlink(os.path.join(SHARE, "household_docs"), os.path.join(SHARE, "link_docs"))
        self.add("/share/Documents/House", "House papers")
        cases = {
            "/share": "itself", "/share/": "itself", "/share/../etc": "allowed", "/etc": "inside /share",
            "relative/../..": "allowed", "/share/.hidden": "hidden", "/share/Documents/House/.hidden": "hidden",
            "/share/household_docs": "documents folder", "/share/household_docs/people": "documents folder",
            "/share/link_docs": "documents folder", "/share": "itself", "/share/link_out": "inside /share",
            "/share/chat/files": "Chat", "/share/chat": "Chat", "/share/chat/files/sub": "Chat",
            "/share/elsewhere": "documents folder", "/share/elsewhere/docs": "documents folder",
            "/share/Documents": "overlaps", "/share/Documents/House/Taxes": "overlaps", "/share/nope": "doesn't exist",
            "": "Choose", None: "Choose",
        }
        os.makedirs(os.path.join(SHARE, "chat", "files", "sub"), exist_ok=True)
        for path, why in cases.items():
            with self.subTest(path=path):
                v = self.ok(self.post("/api/admin/shared-folders/inspect", {"path": path}, ASHA))
                self.assertFalse(v["ok"], v)
                self.assertIn(why.lower(), v["message"].lower())
                r = self.post("/api/admin/shared-folders", {"path": path, "label": "X " + str(path), "access": {}}, ASHA)
                self.assertIn(r.status_code, (409, 422), r.text)
        self.assertFalse(self.ok(self.post("/api/admin/shared-folders/inspect", {"path": "/share/Documents/House"}, ASHA))["ok"])
        os.makedirs(os.path.join(SHARE, "Media", "Photos"))
        self.assertTrue(self.ok(self.post("/api/admin/shared-folders/inspect", {"path": "Media/Photos"}, ASHA))["ok"])

    def test_documents_folder_cant_move_onto_a_shared_folder(self):
        self.house()
        v = self.ok(self.post("/api/admin/docs-folder/inspect", {"path": "/share/Documents/House/docs"}, ASHA))
        self.assertEqual(v["verdict"], "overlap")

    def test_browser_marks_shared_folders(self):
        self.house()
        d = self.ok(self.get("/api/admin/share-dirs?path=/share/Documents", ASHA))
        self.assertEqual(d["folders"], [{"name": "House", "docs": False, "chat": False, "shared": "House papers"}])


class WhatPeopleSee(SharedBase):
    def test_access_per_person_and_everyone(self):
        rid = self.house()
        ref = "root:" + rid
        self.assertEqual(self.ok(self.get("/api/shared-folders"))["folders"],
                         [{"rootId": rid, "label": "House papers", "mode": "ro", "exists": True, "kids": False}])
        self.assertEqual(self.ok(self.get("/api/shared-folders", MEERA))["folders"][0]["mode"], "rw")
        self.assertEqual(self.ok(self.get("/api/shared-folders", DEV))["folders"], [])
        self.assertEqual(self.ok(self.get("/api/shared-folders", ASHA))["folders"], [])      # admins: no content access
        self.assertEqual(self.get(f"/api/list?node={ref}", DEV).status_code, 404)
        self.assertEqual(self.get(f"/api/list?node={ref}", ASHA).status_code, 404)
        data = self.listing(ref)
        self.assertEqual(sorted(i["name"] for i in data["items"]), ["Taxes", "manual.pdf"])
        self.assertEqual((data["mode"], data["canEdit"], data["role"]), ("ro", False, "viewer"))
        self.assertEqual(data["crumbs"][0]["space"], "folders")
        self.assertTrue(all(i["rootKind"] == "shared" and i["rootLabel"] == "House papers" for i in data["items"]))
        taxes = next(i for i in data["items"] if i["name"] == "Taxes")
        self.assertEqual(taxes["parentRef"], ref)
        inner = self.listing(taxes["id"])
        self.assertEqual(inner["crumbs"][1], {"id": ref, "name": "House papers"})
        # Everyone rw beats Kabir's own ro; Dev gets in through Everyone
        self.ok(self.patch(f"/api/admin/shared-folders/{rid}", {"access": {"u_kabir": "ro", "*": "rw"}}, ASHA))
        self.assertEqual(self.listing(ref)["mode"], "rw")
        self.assertEqual(self.listing(ref, DEV)["mode"], "rw")
        # another person's folder can't be named as a place; your own is My docs
        self.assertEqual(self.get(f"/api/list?node=root:{self.root_id(MEERA)}").status_code, 404)
        self.assertEqual(self.post("/api/docs", {"kind": "note", "name": "x", "parentId": "root:" + self.root_id(MEERA)}).status_code, 404)
        self.assertEqual(self.ok(self.get(f"/api/list?node=root:{self.root_id(KABIR)}"))["crumbs"][0]["space"], "mine")
        self.assertEqual(self.get("/api/list?node=root:nope").status_code, 404)
        # a turned-off person sees nothing
        self.ok(self.patch("/api/admin/users/u_dev", {"disabled": True}, ASHA))
        self.assertEqual(self.get(f"/api/list?node={ref}", DEV).status_code, 404)

    def test_read_only_people(self):
        rid = self.house()
        ref = "root:" + rid
        items = {i["name"]: i for i in self.listing(ref)["items"]}
        notes = next(i for i in self.listing(items["Taxes"]["id"])["items"] if i["name"] == "notes.md")
        self.assertEqual(self.open(notes["id"])["canEdit"], False)
        self.assertEqual(self.get(f"/api/nodes/{items['manual.pdf']['id']}/file").status_code, 200)
        for r in (self.post("/api/docs", {"kind": "note", "name": "x", "parentId": ref}),
                  self.post("/api/docs", {"kind": "note", "name": "x", "parentId": items["Taxes"]["id"]}),
                  self.post(f"/api/nodes/{items['manual.pdf']['id']}/rename", {"name": "y.pdf"}),
                  self.delete(f"/api/nodes/{items['manual.pdf']['id']}"),
                  self.post(f"/api/nodes/{items['manual.pdf']['id']}/move", {"parentId": items["Taxes"]["id"]}),
                  self.ops(notes["id"], [{"op": "untickAll"}]),
                  self.c.post(f"/api/nodes/{ref}/upload?name=a.txt", content=b"x", headers=KABIR)):
            self.assertEqual(r.status_code, 403, r.text)
        # but a copy into My docs works (viewers may)
        c = self.ok(self.post(f"/api/nodes/{items['Taxes']['id']}/copy", {}), 201)
        self.assertEqual(c["rootKind"], "person")
        self.assertTrue(os.path.isfile(os.path.join(SHARE, "household_docs", "people", "Kabir Rao", "Taxes", "2026", "return.txt")))
        # no per-item sharing in admin shared folders
        self.assertEqual(self.share(items["manual.pdf"]["id"], DEV, "viewer", h=MEERA).status_code, 403)

    def test_read_and_write_people(self):
        rid = self.house()
        ref = "root:" + rid
        items = {i["name"]: i for i in self.listing(ref, MEERA)["items"]}
        n = self.create("note", "Insurance", ref, h=MEERA)
        self.assertTrue(os.path.isfile(os.path.join(HOUSE, "Insurance.txt")))
        self.assertEqual(n["parentRef"], ref)
        self.save(n["id"], "first", MEERA)
        self.clock.advance(minutes=10)
        self.save(n["id"], "second", MEERA)
        self.assertTrue(os.path.isdir(os.path.join(HOUSE, ".versions", n["id"])))   # its own .versions
        f = self.create("folder", "Car", ref, h=MEERA)
        self.ok(self.post(f"/api/nodes/{n['id']}/move", {"parentId": f["id"]}, MEERA))
        self.assertTrue(os.path.isfile(os.path.join(HOUSE, "Car", "Insurance.txt")))
        self.ok(self.post(f"/api/nodes/{n['id']}/move", {"parentId": ref}, MEERA))       # back to the top
        self.assertTrue(os.path.isfile(os.path.join(HOUSE, "Insurance.txt")))
        self.assertEqual(self.post(f"/api/nodes/{n['id']}/move", {"parentId": None}, MEERA).status_code, 200)
        my = self.create("folder", "Mine", h=MEERA)
        self.assertEqual(self.post(f"/api/nodes/{n['id']}/move", {"parentId": my["id"]}, MEERA).status_code, 422)
        self.assertEqual(self.post(f"/api/nodes/{my['id']}/move", {"parentId": ref}, MEERA).status_code, 422)
        self.ok(self.post(f"/api/nodes/{items['manual.pdf']['id']}/rename", {"name": "Manual (car).pdf"}, MEERA))
        # delete → the folder's own hidden .trash; restore by anyone with Read and write
        tid = self.ok(self.delete(f"/api/nodes/{n['id']}", MEERA))["trashId"]
        self.assertFalse(os.path.exists(os.path.join(HOUSE, "Insurance.txt")))
        self.assertTrue(os.path.isdir(os.path.join(HOUSE, ".trash", "_shared", tid)))
        mine = self.ok(self.get("/api/space/trash", MEERA))
        self.assertEqual(mine["items"], [])
        self.assertEqual(mine["folders"][0]["label"], "House papers")
        self.assertEqual(mine["folders"][0]["items"][0]["name"], "Insurance.txt")
        self.assertEqual(self.ok(self.get("/api/space/trash", KABIR))["folders"], [])             # read only
        self.assertEqual(self.post(f"/api/trash/{tid}/restore", {}, KABIR).status_code, 404)
        self.assertEqual(self.post(f"/api/trash/{tid}/restore", {}, DEV).status_code, 404)
        self.ok(self.post(f"/api/trash/{tid}/restore", {}, MEERA))
        self.assertTrue(os.path.isfile(os.path.join(HOUSE, "Insurance.txt")))
        # the hidden folders never show
        self.assertNotIn(".trash", [i["name"] for i in self.listing(ref, MEERA)["items"]])

    def test_missing_folder_is_hidden_but_marked_for_admins(self):
        rid = self.house({"u_kabir": "ro", "u_asha": "ro"})
        os.rename(HOUSE, HOUSE + "-away")
        self.assertEqual(self.ok(self.get("/api/shared-folders"))["folders"], [])
        self.assertEqual(self.ok(self.get("/api/shared-folders", ASHA))["folders"][0]["exists"], False)
        self.assertEqual(self.get(f"/api/list?node=root:{rid}").status_code, 404)
        f = self.ok(self.get("/api/admin/shared-folders", ASHA))["folders"][0]
        self.assertFalse(f["exists"])
        self.assertIn("Not found", f["problem"])
        self.scan()
        r = self.ok(self.get("/api/search?q=return"))
        self.assertEqual(r["results"], [])                                  # nothing shows while it's away
        with db.get_conn() as conn:                                          # and nothing was marked gone
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM nodes WHERE root_id = ? AND gone_at IS NOT NULL",
                                          (rid,)).fetchone()[0], 0)
        os.rename(HOUSE + "-away", HOUSE)
        self.scan()
        self.assertEqual([x["name"] for x in self.ok(self.get("/api/search?q=return"))["results"]], ["return.txt"])

    def test_swapped_for_a_link_is_refused(self):
        rid = self.house()
        os.rename(HOUSE, HOUSE + "-real")
        os.symlink(os.path.join(SHARE, "household_docs"), HOUSE)
        self.assertEqual(self.get(f"/api/list?node=root:{rid}").status_code, 422)
        f = self.ok(self.get("/api/admin/shared-folders", ASHA))["folders"][0]
        self.assertFalse(f["exists"])
        self.assertIn("link", f["problem"])
        self.assertIsNone(index.scan_root(rid))
        os.remove(HOUSE)
        os.symlink(HOUSE + "-real", HOUSE)                                  # even a link to the same place
        self.assertEqual(self.get(f"/api/list?node=root:{rid}").status_code, 422)

    def test_search_sees_shared_folders_by_access(self):
        self.house()
        self.assertEqual([x["name"] for x in self.ok(self.get("/api/search?q=return"))["results"]], ["return.txt"])
        self.assertEqual(self.ok(self.get("/api/search?q=return", DEV))["results"], [])
        r = self.ok(self.get("/api/search?q=return"))["results"][0]
        self.assertEqual(r["location"], "House papers › Taxes › 2026")
        self.assertEqual(r["role"], "viewer")

    def test_a_file_moved_in_from_outside_is_new_there(self):
        """Security review: a file moved from a person's folder into an admin shared folder outside the app is a
        new item there — no shares, versions or history come with it (they never cross roots)."""
        rid = self.house()
        n = self.note("Moving", text="x")
        self.ok(self.share(n["id"], DEV, "viewer"))
        os.rename(self.real(n["id"]), os.path.join(HOUSE, "Moving.txt"))
        self.scan()
        row = self.row(n["id"])
        self.assertNotEqual(row["root_id"], rid)
        self.assertIsNotNone(row["gone_at"])                                 # gone from Kabir's folder
        with db.get_conn() as conn:
            new = conn.execute("SELECT * FROM nodes WHERE root_id = ? AND name = 'Moving.txt'", (rid,)).fetchone()
            self.assertIsNotNone(new)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM shares WHERE node_id = ?", (new["id"],)).fetchone()[0], 0)
            self.assertIsNone(new["created_by"])
            self.assertIsNone(sharing.role_of(conn, {"id": "u_dev"}, new))
        self.assertEqual(self.get(f"/api/docs/{n['id']}", DEV).status_code, 404)


if __name__ == "__main__":
    unittest.main()
