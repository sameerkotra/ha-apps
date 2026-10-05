"""Regression tests for the October 2026 security review of Household Docs (each finding's proof, turned
around): rename matching never crosses roots, other apps' stores are left out, Trash is its owner's, the
storage report's duplicates, paths and inherited shares, Activity details, admins and children, tags, request
sizes, opening files safely, restoring from Trash, Home Assistant sensors, AI reading, stale sheet settings and
copies in Kids' space. (Send to chat's member shares: test_connect.py; Chat's card links: Household Chat's
test_app_messages.py.)"""
import _env  # noqa: F401

import json
import os
import shutil
import unittest
from unittest import mock

from app import ai, config, db, ha_sensors, notify
from app.common import sensor_publisher
from app.store import fileio, index, marker, paths, roots
from base import ASHA, DEV, DOCS, KABIR, MEERA, SHARE, ApiBase, hdr, person_dir, read, write

KID = hdr("u_kid", "Ravi Rao", "ravi")


class Base(ApiBase):
    PEOPLE = (ASHA, KABIR, MEERA, DEV)

    def set_inode(self, node_id, path):
        """Make the index think `node_id` had the inode `path` has now (an inode the file system reused)."""
        st = os.stat(path)
        with db.get_conn() as conn:
            conn.execute("UPDATE nodes SET inode = ?, dev = ? WHERE id = ?", (st.st_ino, st.st_dev, node_id))

    def shared_folder(self, name="Family", access=None):
        os.makedirs(os.path.join(SHARE, name), exist_ok=True)
        r = self.post("/api/admin/shared-folders", {"path": f"/share/{name}", "label": name,
                                                    "access": access or {"*": "ro"}}, ASHA)
        return self.ok(r, 201)["id"]


# =====================================================================
# 1. Renames and moves found on disk: same root only, and only when plausible
# =====================================================================
class Matching(Base):
    def test_a_reused_inode_in_another_persons_folder_takes_nothing(self):
        n = self.note("Notes", text="Kabir private v1: bank PIN 1234")
        self.save(n["id"], "Kabir private v2")
        self.clock.advance(minutes=10)
        self.save(n["id"], "Kabir v3 final")
        self.ok(self.share(n["id"], MEERA, "viewer"))
        os.remove(self.real(n["id"]))
        self.scan()
        self.get("/api/me", DEV)
        p = os.path.join(person_dir("Dev Rao"), "Notes.txt")
        write(p, "Dev's own text")
        self.set_inode(n["id"], p)                  # the file system hands Kabir's old inode to Dev's new file
        self.scan()
        row = self.row(n["id"])
        self.assertNotEqual(row["root_id"], self.root_id(DEV))
        self.assertIsNotNone(row["gone_at"])
        self.assertEqual(self.get(f"/api/docs/{n['id']}/versions/1", DEV).status_code, 404)
        self.assertEqual(self.get(f"/api/docs/{n['id']}", MEERA).status_code, 404)
        self.assertEqual(self.ok(self.get("/api/space/shared", MEERA))["items"], [])
        with db.get_conn() as conn:
            dev_file = conn.execute("SELECT * FROM nodes WHERE root_id = ? AND name = 'Notes.txt'",
                                    (self.root_id(DEV),)).fetchone()
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM shares WHERE node_id = ?", (dev_file["id"],)).fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM versions WHERE node_id = ?", (dev_file["id"],)).fetchone()[0], 0)

    def test_the_same_content_in_another_persons_folder_takes_nothing(self):
        n = self.note("Diary", text="secret draft one")
        self.clock.advance(minutes=10)
        self.save(n["id"], "same")
        self.ok(self.share(n["id"], MEERA, "viewer"))
        os.remove(self.real(n["id"]))
        self.scan()
        self.get("/api/me", DEV)
        write(os.path.join(person_dir("Dev Rao"), "Mine.txt"), "same")
        self.scan()
        self.assertNotEqual(self.row(n["id"])["root_id"], self.root_id(DEV))
        self.assertEqual(self.get(f"/api/docs/{n['id']}/versions", DEV).status_code, 404)
        self.assertEqual(self.get(f"/api/docs/{n['id']}/versions/1", DEV).status_code, 404)
        self.assertEqual(self.ok(self.get("/api/space/shared", MEERA))["items"], [])

    def test_a_reused_inode_in_the_same_folder_needs_more_than_the_inode(self):
        n = self.note("Letter", text="to the bank")
        self.ok(self.share(n["id"], MEERA, "viewer"))
        os.remove(self.real(n["id"]))
        self.clock.advance(minutes=1)
        p = os.path.join(person_dir("Kabir Rao"), "Shopping.txt")
        write(p, "something else, longer than the letter", mtime=1_700_000_000)
        self.set_inode(n["id"], p)
        self.scan()
        self.assertEqual(self.row(n["id"])["rel"], "Letter.txt")
        self.assertIsNotNone(self.row(n["id"])["gone_at"])
        self.assertEqual(self.get("/api/search?q=Shopping", MEERA).json()["results"], [])

    def test_a_reused_folder_inode_needs_its_name_its_time_or_its_contents(self):
        f = self.create("folder", "Divorce lawyer")
        self.ok(self.share(f["id"], MEERA, "viewer"))
        shutil.rmtree(self.real(f["id"]))
        g = os.path.join(person_dir("Kabir Rao"), "Holiday")
        os.makedirs(g)
        write(os.path.join(g, "plan.txt"), "beach")
        os.utime(g, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))
        self.set_inode(f["id"], g)
        self.scan()
        self.assertEqual(self.row(f["id"])["rel"], "Divorce lawyer")
        self.assertEqual(self.ok(self.get("/api/search?q=beach", MEERA))["results"], [])

    def test_a_long_gone_node_isnt_matched_any_more(self):
        n = self.note("Recipe", text="flour, eggs, sugar")
        self.ok(self.share(n["id"], MEERA, "viewer"))
        data = read(self.real(n["id"]))
        os.remove(self.real(n["id"]))
        self.scan()
        self.clock.advance(days=2)
        write(os.path.join(person_dir("Kabir Rao"), "Recipe copy.txt"), data)
        self.scan()
        self.assertIsNotNone(self.row(n["id"])["gone_at"])
        self.assertEqual(self.ok(self.get("/api/search?q=flour", MEERA))["results"], [])

    def test_history_from_an_admin_shared_folder_stays_there(self):
        rid = self.shared_folder("House", {"*": "rw"})
        n = self.note("Plan", parent="root:" + rid, text="v1")
        self.clock.advance(minutes=10)
        self.save(n["id"], "v2", MEERA)
        self.assertEqual(len(self.ok(self.get(f"/api/docs/{n['id']}/versions"))["versions"]), 1)
        os.rename(self.real(n["id"]), os.path.join(person_dir("Kabir Rao"), "Plan.txt"))
        self.scan()
        with db.get_conn() as conn:
            mine = conn.execute("SELECT * FROM nodes WHERE root_id = ? AND name = 'Plan.txt'", (self.root_id(),)).fetchone()
        self.assertNotEqual(mine["id"], n["id"])
        self.assertEqual(self.ok(self.get(f"/api/docs/{mine['id']}/versions"))["versions"], [])


# =====================================================================
# 2. Household Chat's files folder (or another Docs install) inside a shared folder
# =====================================================================
class OtherAppsStores(Base):
    def test_a_chat_store_inside_a_shared_folder_is_left_out(self):
        write(os.path.join(SHARE, "Family", "readme.txt"), "hi")
        rid = self.shared_folder("Family")
        self.scan()
        cdir = os.path.join(SHARE, "Family", "chat")
        write(os.path.join(cdir, "Nisha & Tarun (1a2b3c4d)", "medical-scan.pdf"), b"%PDF-1.4 private dm attachment")
        self.scan()
        hit = self.ok(self.get("/api/search?q=medical", MEERA))["results"]
        self.assertEqual(len(hit), 1)                       # before: an ordinary folder
        nid = hit[0]["id"]
        write(os.path.join(cdir, marker.CHAT_MARKER), "{}")  # it becomes Chat's files folder
        # every use re-checks: the file isn't served, even before the next scan
        self.assertNotEqual(self.get(f"/api/nodes/{nid}/file", MEERA).status_code, 200)
        zip_ = self.get(f"/api/nodes/root:{rid}/zip", MEERA)
        self.assertEqual(zip_.status_code, 200)
        self.assertNotIn(b"medical", zip_.content)
        self.scan()
        self.assertEqual(self.ok(self.get("/api/search?q=medical", MEERA))["results"], [])
        self.assertNotIn("chat", [i["name"] for i in self.ok(self.get(f"/api/list?node=root:{rid}", MEERA))["items"]])
        folders = self.ok(self.get("/api/admin/shared-folders", ASHA))["folders"]
        self.assertIn("Household Chat's files folder is inside", folders[0]["problem"])
        self.assertTrue(folders[0]["exists"])
        # another Docs install's documents folder: the same
        other = os.path.join(SHARE, "Family", "old_docs")
        write(os.path.join(other, marker.MARKER), "{}")
        write(os.path.join(other, "people", "X", "tax.txt"), "tax return 2020")
        self.scan()
        self.assertEqual(self.ok(self.get("/api/search?q=tax", MEERA))["results"], [])

    def test_a_shared_folder_that_ends_up_inside_chats_folder_is_refused_on_every_use(self):
        write(os.path.join(SHARE, "Stuff", "Family", "a.txt"), "x")
        rid = self.shared_folder("Stuff/Family")
        write(os.path.join(SHARE, "Stuff", marker.CHAT_MARKER), "{}")    # Chat's files folder set to /share/Stuff
        self.assertNotEqual(self.get(f"/api/list?node=root:{rid}", MEERA).status_code, 200)
        self.assertIsNone(index.scan_root(rid))
        folders = self.ok(self.get("/api/admin/shared-folders", ASHA))["folders"]
        self.assertIn("Household Chat's files folder", folders[0]["problem"])
        self.assertFalse(folders[0]["exists"])


# =====================================================================
# 3. Trash is its owner's alone
# =====================================================================
class TrashIsPrivate(Base):
    def test_deleted_items_leave_search_activity_and_follows_for_others(self):
        f = self.create("folder", "Shared")["id"]
        self.ok(self.share(f, MEERA, "viewer"))
        n = self.note("Salary", f, text="my salary is 98765 zebra")
        self.ok(self.post("/api/follows", {"target": n["id"]}, MEERA), 201)
        self.ok(self.post(f"/api/nodes/{n['id']}/favourite", {"value": True}, MEERA))
        self.ok(self.delete(f"/api/nodes/{n['id']}"))
        self.assertEqual(self.ok(self.get("/api/search?q=zebra&trash=1", MEERA))["results"], [])
        self.assertEqual(self.ok(self.get("/api/search?q=zebra", MEERA))["results"], [])
        acts = self.ok(self.get("/api/activity", MEERA))["items"]
        self.assertNotIn("Salary.txt", json.dumps(acts))
        self.assertEqual(self.get(f"/api/nodes/{n['id']}", MEERA).status_code, 404)
        self.assertEqual(self.ok(self.get("/api/space/favourites", MEERA))["items"], [])
        # the owner still finds it in Trash
        mine = self.ok(self.get("/api/search?q=zebra&trash=1"))["results"]
        self.assertEqual([(r["name"], r["inTrash"]) for r in mine], [("Salary.txt", True)])

    def test_an_admin_shared_folders_trash_is_for_read_and_write_only(self):
        rid = self.shared_folder("House", {"u_kabir": "rw", "u_meera": "ro"})
        n = self.note("Bill", parent="root:" + rid, text="electricity zebra")
        self.ok(self.delete(f"/api/nodes/{n['id']}"))
        self.assertEqual(self.ok(self.get("/api/search?q=zebra&trash=1", MEERA))["results"], [])
        self.assertEqual(len(self.ok(self.get("/api/search?q=zebra&trash=1"))["results"]), 1)
        self.assertNotIn("Bill.txt", json.dumps(self.ok(self.get("/api/activity", MEERA))["items"]))


# =====================================================================
# 4. Storage report: duplicates count only copies you can open
# =====================================================================
class Duplicates(Base):
    def test_no_content_oracle(self):
        self.note("Secret", text="exact secret content 42", h=KABIR)
        before = self.ok(self.get("/api/reports/storage", MEERA))["duplicates"]
        self.note("Guess", text="exact secret content 42", h=MEERA)
        after = self.ok(self.get("/api/reports/storage", MEERA))
        self.assertEqual(before, after["duplicates"])
        self.assertEqual(after["duplicates"], {"groups": 0, "extraBytes": 0})
        self.assertEqual(after["duplicateGroups"], [])
        # copies Meera can open still count
        self.note("Guess 2", text="exact secret content 42", h=MEERA)
        self.assertEqual(self.ok(self.get("/api/reports/storage", MEERA))["duplicates"]["groups"], 1)


# =====================================================================
# 5. Paths and inherited shares name only folders you can open
# =====================================================================
class Paths(Base):
    def test_folders_above_are_trimmed(self):
        top = self.create("folder", "Divorce lawyer")["id"]
        sub = self.create("folder", "Evidence", top)["id"]
        self.ok(self.share(top, DEV, "editor"))
        n = self.note("Letter", sub, text="hi")
        self.ok(self.share(n["id"], MEERA, "viewer"))
        info = self.ok(self.get(f"/api/nodes/{n['id']}", MEERA))
        self.assertEqual(info["path"], "… / Letter.txt")
        self.assertEqual(self.open(n["id"], MEERA)["path"], "… / Letter.txt")
        self.assertEqual(self.ok(self.get(f"/api/nodes/{n['id']}/shares", MEERA))["inherited"], [])
        blob = json.dumps([info, self.ok(self.get(f"/api/nodes/{n['id']}/shares", MEERA))])
        self.assertNotIn("Divorce", blob)
        self.assertNotIn("Evidence", blob)
        # Dev can open the folders: he sees them (and the share they come from)
        self.assertEqual(self.ok(self.get(f"/api/nodes/{n['id']}", DEV))["path"], "… / Divorce lawyer / Evidence / Letter.txt")
        self.assertEqual([s["from"] for s in self.ok(self.get(f"/api/nodes/{n['id']}/shares", DEV))["inherited"]],
                         ["Divorce lawyer"])
        # the owner: the full path
        self.assertTrue(self.ok(self.get(f"/api/nodes/{n['id']}"))["path"].endswith("/Divorce lawyer/Evidence/Letter.txt"))


class LocationsElsewhere(Base):
    """Re-review: search results, the CSV export, Activity and `path:` name only folders you can open."""

    def setUp(self):
        super().setUp()
        top = self.create("folder", "Divorce lawyer")["id"]
        self.sub = self.create("folder", "Evidence", top)["id"]
        self.n = self.note("Letter", self.sub, text="walrus")
        self.ok(self.share(self.n["id"], MEERA, "viewer"))

    def test_search_csv_and_activity(self):
        r = self.ok(self.get("/api/search?q=walrus", MEERA))["results"][0]
        self.assertEqual((r["location"], r["locationParts"]), ("…", ["…"]))
        g = self.ok(self.get("/api/search?q=walrus&group=location", MEERA))
        csv = self.c.get("/api/search/export?q=walrus", headers=MEERA).text
        acts = self.ok(self.get("/api/activity", MEERA))
        for blob in (json.dumps(g), csv, json.dumps(acts)):
            self.assertNotIn("Divorce", blob)
            self.assertNotIn("Evidence", blob)
        self.assertTrue(all(i["location"] == "…" for i in acts["items"]))
        # once Evidence is shared with her she sees it, never the folder above
        self.ok(self.share(self.sub, MEERA, "viewer"))
        r = self.ok(self.get("/api/search?q=walrus", MEERA))["results"][0]
        self.assertEqual(r["locationParts"], ["…", "Evidence"])
        # the owner: everything
        self.assertEqual(self.ok(self.get("/api/search?q=walrus"))["results"][0]["locationParts"],
                         ["My docs", "Divorce lawyer", "Evidence"])

    def test_a_path_filter_cant_test_hidden_folder_names(self):
        for q in ("walrus%20path:Divorce*", "walrus%20path:Evidence", "path:%22Divorce%20lawyer%22"):
            self.assertEqual(self.ok(self.get(f"/api/search?q={q}", MEERA))["results"], [], q)
        self.assertEqual(len(self.ok(self.get("/api/search?q=walrus%20path:Divorce*"))["results"]), 1)
        self.ok(self.share(self.sub, MEERA, "viewer"))
        self.assertEqual(len(self.ok(self.get("/api/search?q=walrus%20path:Evidence", MEERA))["results"]), 1)
        self.assertEqual(self.ok(self.get("/api/search?q=walrus%20path:Divorce*", MEERA))["results"], [])


# =====================================================================
# 6. Activity: old names and private folders
# =====================================================================
class ActivityDetails(Base):
    def test_moves_dont_name_private_folders_and_renames_hide_old_names(self):
        priv = self.create("folder", "Divorce lawyer")["id"]
        shared = self.create("folder", "Family")["id"]
        self.ok(self.share(shared, MEERA, "viewer"))
        n = self.note("Letter", priv, text="x")
        self.ok(self.post(f"/api/nodes/{n['id']}/move", {"parentId": shared}))
        moved = [i for i in self.ok(self.get("/api/activity", MEERA))["items"] if i["action"] == "moved"][0]
        self.assertEqual((moved["detail"]["from"], moved["detail"]["to"]), (None, "Family"))
        self.assertNotIn("Divorce", json.dumps(self.ok(self.get("/api/activity", MEERA))))
        mine = [i for i in self.ok(self.get("/api/activity"))["items"] if i["action"] == "moved"][0]
        self.assertEqual(mine["detail"]["from"], "Divorce lawyer")             # the owner sees it
        # renamed while Meera couldn't open it, then shared: she doesn't learn the old name
        n2 = self.note("Pregnancy test results", priv, text="y")
        self.ok(self.post(f"/api/nodes/{n2['id']}/rename", {"name": "Notes"}))
        self.ok(self.share(n2["id"], MEERA, "viewer"))
        blob = json.dumps(self.ok(self.get("/api/activity", MEERA)))
        self.assertNotIn("Pregnancy", blob)
        self.assertNotIn("acl", blob)
        ren = [i for i in self.ok(self.get("/api/activity", MEERA))["items"] if i["action"] == "renamed"][0]
        self.assertIsNone(ren["detail"]["from"])
        # renamed while she could open it: she saw it then, she sees it now
        n3 = self.note("Old name", shared, text="z")
        self.ok(self.post(f"/api/nodes/{n3['id']}/rename", {"name": "New name"}))
        ren = [i for i in self.ok(self.get("/api/activity", MEERA))["items"] if i["action"] == "renamed"][0]
        self.assertEqual(ren["detail"]["from"], "Old name.txt")

    def test_follow_notifications_say_no_more(self):
        from app import follows
        priv = self.create("folder", "Divorce lawyer")["id"]
        shared = self.create("folder", "Family")["id"]
        self.ok(self.share(shared, MEERA, "viewer"))
        self.ok(self.post("/api/follows", {"target": shared}, MEERA), 201)
        n = self.note("Secret plan", priv, text="x")
        self.ok(self.post(f"/api/nodes/{n['id']}/rename", {"name": "Letter"}))
        self.clock.advance(seconds=5)
        self.ok(self.post(f"/api/nodes/{n['id']}/move", {"parentId": shared}))
        with db.get_conn() as conn:
            f = conn.execute("SELECT * FROM follows WHERE user_id = 'u_meera'").fetchone()
            rows, _label = follows.pending(conn, {"id": "u_meera", "disabled": False}, f)
            text = follows.message(rows, _label, "folder")
        self.assertNotIn("Divorce", text)
        self.assertNotIn("Secret", text)


# =====================================================================
# 7. Admins get no content access as admins
# =====================================================================
class AdminsAndChildren(Base):
    def test_an_admin_may_name_someone_else_and_it_is_told_and_audited(self):
        """Accepted design: an admin names another person (here another account) as a parent; that person reads what
        is made from then on — the child is told and the change is audited."""
        old = self.note("Before", text="old")
        self.clock.advance(seconds=1)
        with mock.patch.object(notify, "send_child_status") as told:
            self.ok(self.patch("/api/admin/users/u_kabir", {"isChild": True, "parents": ["u_dev"]}, ASHA))
        told.assert_called_with("u_kabir")
        self.clock.advance(seconds=1)
        new = self.note("After", text="new")
        self.assertEqual(self.open(new["id"], DEV)["text"], "new")
        self.assertEqual(self.get(f"/api/docs/{old['id']}", DEV).status_code, 404)
        self.assertEqual(self.ok(self.get("/api/me"))["kidsStatus"]["parents"], ["Dev Rao"])
        with db.get_conn() as conn:
            rows = conn.execute("SELECT action, root_id FROM audit_log WHERE actor_id = 'u_asha' AND action LIKE 'child%'").fetchall()
        self.assertEqual({r["action"] for r in rows}, {"child_on", "child_parents_set"})
        self.assertTrue(all(r["root_id"] == self.root_id() for r in rows))

    def test_an_admin_cant_make_themselves_a_parent(self):
        n = self.note("Diary", text="kabir private diary")
        r = self.patch("/api/admin/users/u_kabir", {"isChild": True, "parents": ["u_asha"]}, ASHA)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.get(f"/api/docs/{n['id']}", ASHA).status_code, 404)
        self.assertFalse(self.ok(self.get("/api/me"))["isChild"])          # nothing changed (one transaction)

    def test_a_parent_sees_only_what_was_made_while_a_child(self):
        old = self.note("Diary", text="kabir private diary")
        old_folder = self.create("folder", "Private")
        self.clock.advance(seconds=1)
        with mock.patch.object(notify, "send_child_status") as told:
            self.ok(self.patch("/api/admin/users/u_kabir", {"isChild": True, "parents": ["u_meera"]}, ASHA))
        told.assert_called_with("u_kabir")
        self.clock.advance(seconds=1)
        new = self.note("Homework", text="sums")
        moved_in = self.create("folder", "School")
        self.ok(self.post(f"/api/nodes/{old['id']}/move", {"parentId": moved_in["id"]}))
        self.assertEqual(self.get(f"/api/docs/{old['id']}", MEERA).status_code, 404)
        self.assertEqual(self.get(f"/api/nodes/{old_folder['id']}", MEERA).status_code, 404)
        self.assertEqual(self.open(new["id"], MEERA)["text"], "sums")
        self.assertEqual(self.ok(self.get("/api/search?q=diary", MEERA))["results"], [])
        root = "root:" + self.root_id()
        names = [i["name"] for i in self.ok(self.get(f"/api/list?node={root}", MEERA))["items"]]
        self.assertEqual(sorted(names), ["Homework.txt", "School"])
        self.assertEqual([i["name"] for i in self.ok(self.get(f"/api/list?node={moved_in['id']}", MEERA))["items"]], [])
        z = self.get(f"/api/nodes/{root}/zip", MEERA)
        self.assertEqual(z.status_code, 200)
        self.assertNotIn(b"Diary", z.content)
        self.assertNotIn(b"kabir private", z.content)
        copy = self.post(f"/api/nodes/{moved_in['id']}/copy", {"parentId": None}, MEERA)
        self.ok(copy, 201)
        self.assertEqual(self.ok(self.get("/api/search?q=diary", MEERA))["results"], [])
        # the person is told, in the app too, and it's in the audit log
        st = self.ok(self.get("/api/me"))["kidsStatus"]
        self.assertEqual(st["parents"], ["Meera Rao"])
        self.assertIn("Meera Rao can view what you make in My docs from now on", notify.child_message(st))
        with db.get_conn() as conn:
            acts = [r["action"] for r in conn.execute("SELECT action FROM audit_log WHERE actor_id = 'u_asha'")]
        self.assertIn("child_on", acts)
        self.assertIn("child_parents_set", acts)

    def test_the_folder_browser_doesnt_list_inside_the_documents_folder(self):
        self.create("folder", "Divorce papers")
        r = self.ok(self.get("/api/admin/share-dirs?path=/share/household_docs/people/Kabir Rao", ASHA))
        self.assertEqual(r["folders"], [])
        self.assertEqual(r["sealed"], "docs")
        r = self.ok(self.get("/api/admin/share-dirs?path=/share/household_docs", ASHA))
        self.assertEqual(r["folders"], [])
        top = self.ok(self.get("/api/admin/share-dirs?path=", ASHA))
        self.assertEqual([(f["name"], f["docs"]) for f in top["folders"]], [("household_docs", True)])
        write(os.path.join(SHARE, "chat", marker.CHAT_MARKER), "{}")
        os.makedirs(os.path.join(SHARE, "chat", "Nisha & Tarun"))
        self.assertEqual(self.ok(self.get("/api/admin/share-dirs?path=/share/chat", ASHA))["folders"], [])

    def test_the_copy_check_report_is_admin_only_and_audited(self):
        nas = os.path.join(SHARE, "nas")
        os.makedirs(nas)
        shutil.copytree(DOCS, os.path.join(nas, "household_docs"), ignore=shutil.ignore_patterns(".tmp"))
        job = self.ok(self.post("/api/admin/docs-folder/check", {"path": "/share/nas/household_docs"}, ASHA))
        import time
        for _ in range(500):
            j = self.ok(self.get(f"/api/admin/docs-folder/check/{job['id']}", ASHA))
            if j["phase"] in ("done", "failed"):
                break
            time.sleep(0.01)
        self.assertEqual(self.get(f"/api/admin/docs-folder/check/{job['id']}/report.csv", KABIR).status_code, 403)
        self.assertEqual(self.get(f"/api/admin/docs-folder/check/{job['id']}", KABIR).status_code, 403)
        self.assertEqual(self.get(f"/api/admin/docs-folder/check/{job['id']}/report.csv", ASHA).status_code, 200)
        with db.get_conn() as conn:
            acts = [r["action"] for r in conn.execute("SELECT action FROM audit_log WHERE actor_id = 'u_asha'")]
        self.assertIn("docs_location_checked", acts)
        self.assertIn("docs_location_report_downloaded", acts)


# =====================================================================
# 10–12. Tags, request sizes
# =====================================================================
class TagsAndSizes(Base):
    def test_a_tags_spelling_isnt_borrowed_from_items_you_cant_open(self):
        n = self.note("Private", h=KABIR)
        self.ok(self.put(f"/api/nodes/{n['id']}/tags", {"tags": ["Divorce"]}, KABIR))
        m = self.note("Mine", h=MEERA)
        self.assertEqual(self.ok(self.put(f"/api/nodes/{m['id']}/tags", {"tags": ["divorce"]}, MEERA))["tags"], ["divorce"])
        shared = self.note("Shared", h=KABIR)
        self.ok(self.put(f"/api/nodes/{shared['id']}/tags", {"tags": ["Trip"]}, KABIR))
        self.ok(self.share(shared["id"], MEERA, "viewer"))
        m2 = self.note("Mine 2", h=MEERA)
        self.assertEqual(self.ok(self.put(f"/api/nodes/{m2['id']}/tags", {"tags": ["trip"]}, MEERA))["tags"], ["Trip"])

    def test_importing_a_sheet_bigger_than_the_small_request_limit(self):
        csv = ("name,amount,notes\n" + "".join(f"row {i:04d},{i},{'x' * 60}\n" for i in range(4000))).encode()
        self.assertGreater(len(csv), 256 * 1024)
        r = self.c.post("/api/import?name=Big.csv&format=csv", content=csv, headers=KABIR)
        self.assertEqual(r.status_code, 201, r.text)

    def test_a_chunked_body_cant_get_past_the_limit(self):
        n = self.note("N")
        et = self.open(n["id"])["etag"]
        big = json.dumps({"etag": et, "text": "x" * (30 * 1024 * 1024)}).encode()

        def gen(data):
            for i in range(0, len(data), 1 << 20):
                yield data[i:i + (1 << 20)]
        r = self.c.patch(f"/api/docs/{n['id']}", content=gen(big), headers=dict(KABIR, **{"content-type": "application/json"}))
        self.assertEqual(r.status_code, 413)
        big2 = json.dumps({"name": "x" * (3 * 1024 * 1024)}).encode()
        r = self.c.post(f"/api/nodes/{n['id']}/rename", content=gen(big2),
                        headers=dict(KABIR, **{"content-type": "application/json"}))
        self.assertEqual(r.status_code, 413)
        ok = json.dumps({"name": "Renamed"}).encode()
        r = self.c.post(f"/api/nodes/{n['id']}/rename", content=gen(ok), headers=dict(KABIR, **{"content-type": "application/json"}))
        self.assertEqual(r.status_code, 200, r.text)


# =====================================================================
# Plausible findings
# =====================================================================
class OpeningFilesSafely(Base):
    def outside(self):
        d = os.path.join(config.DATA_DIR, "secret-dir")
        os.makedirs(d, exist_ok=True)
        write(os.path.join(d, "x.txt"), "from /data")
        return d

    def test_a_folder_swapped_for_a_link_isnt_read_through(self):
        f = self.create("folder", "A")
        n = self.note("x", f["id"], text="mine")
        folder_real = self.real(f["id"])
        path = self.real(n["id"])                         # checked before the swap
        shutil.rmtree(folder_real)
        os.symlink(self.outside(), folder_real)
        with self.assertRaises((paths.PathError, OSError)):
            fileio.open_read(path)
        with self.assertRaises((paths.PathError, OSError)):
            fileio.read_with_etag(path)
        self.assertNotEqual(self.get(f"/api/nodes/{n['id']}/file").status_code, 200)
        self.assertNotEqual(self.get(f"/api/docs/{n['id']}").status_code, 200)
        from app.search import regex_worker
        self.assertIsNone(regex_worker._read_text(path, 0, config.share_root()))   # the pattern-search worker too
        self.assertEqual(regex_worker._read_text(os.path.join(self.outside(), "x.txt"), 0, None), "from /data")

    def test_a_write_never_lands_through_a_swapped_folder(self):
        f = self.create("folder", "A")
        n = self.note("x", f["id"], text="mine")
        folder_real = self.real(f["id"])
        path = self.real(n["id"])
        with db.get_conn() as conn:
            root = roots.root_row(conn, self.root_id())
        out = self.outside()
        shutil.rmtree(folder_real)
        os.symlink(out, folder_real)
        with self.assertRaises((paths.PathError, OSError)):
            fileio.write_atomic(root, path, b"overwritten")
        self.assertEqual(read(os.path.join(out, "x.txt")), "from /data")
        self.assertEqual(sorted(os.listdir(out)), ["x.txt"])
        self.assertEqual(os.listdir(os.path.join(DOCS, ".tmp")), [])

    def test_a_linked_tmp_folder_is_refused(self):
        n = self.note("x", text="mine")
        tmp = os.path.join(DOCS, ".tmp")
        shutil.rmtree(tmp)
        os.symlink(self.outside(), tmp)
        etag = self.open(n["id"])["etag"]
        r = self.patch(f"/api/docs/{n['id']}", {"etag": etag, "text": "new"})
        self.assertNotEqual(r.status_code, 200)
        self.assertEqual(sorted(os.listdir(os.path.join(config.DATA_DIR, "secret-dir"))), ["x.txt"])


class VersionsCopiedSafely(Base):
    def test_a_version_is_copied_from_the_checked_file_never_through_a_swapped_folder(self):
        from app.store import versions
        f = self.create("folder", "A")
        n = self.note("x", f["id"], text="mine v1")
        with db.get_conn() as conn:
            root = roots.root_row(conn, self.root_id())
            node = nodes_get(conn, n["id"])
        path = self.real(n["id"])
        # normal: copied from the open file into .versions through its folder's descriptor
        with db.get_conn() as conn:
            self.assertEqual(versions.snapshot(conn, root, node, path, "u_meera", force=True), 1)
            self.assertEqual(versions.read(conn, root, n["id"], 1), b"mine v1")
        # a folder on the way swapped for a link to /data: nothing is copied from there
        out = os.path.join(config.DATA_DIR, "secret-v")
        write(os.path.join(out, "x.txt"), "from /data")
        folder_real = self.real(f["id"])
        shutil.rmtree(folder_real)
        os.symlink(out, folder_real)
        with db.get_conn() as conn:
            self.assertIsNone(versions.snapshot(conn, root, node, path, "u_dev", force=True))
            self.assertIsNone(versions.keep_replaced(conn, root, dict(node, kind="file"), path, "u_dev"))
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM versions").fetchone()[0], 1)
        vdir = os.path.join(DOCS, ".versions", n["id"])
        self.assertEqual(sorted(os.listdir(vdir)), ["1.txt"])
        self.assertNotIn("from /data", "".join(read(os.path.join(vdir, x)) for x in os.listdir(vdir)))


def nodes_get(conn, nid):
    from app.store import nodes
    return nodes.get(conn, nid)


class RestoreFromTrash(Base):
    def test_restore_goes_back_into_the_same_folder_never_a_new_one_at_the_old_path(self):
        priv = self.create("folder", "Private")
        n = self.note("Diary", priv["id"], text="secret")
        self.ok(self.delete(f"/api/nodes/{n['id']}"))
        self.ok(self.post(f"/api/nodes/{priv['id']}/rename", {"name": "Old"}))
        imposter = self.create("folder", "Private")
        self.ok(self.share(imposter["id"], "*", "viewer"))
        tid = self.ok(self.get("/api/space/trash"))["items"][0]["id"]
        self.ok(self.post(f"/api/trash/{tid}/restore"))
        self.assertEqual(self.row(n["id"])["parent_id"], priv["id"])
        self.assertEqual(self.row(n["id"])["rel"], "Old/Diary.txt")
        self.assertEqual(self.get(f"/api/docs/{n['id']}", MEERA).status_code, 404)

    def test_restore_to_the_top_when_its_folder_is_gone(self):
        priv = self.create("folder", "Private")
        n = self.note("Diary", priv["id"], text="secret")
        self.ok(self.delete(f"/api/nodes/{n['id']}"))
        self.ok(self.delete(f"/api/nodes/{priv['id']}"))
        imposter = self.create("folder", "Private")
        self.ok(self.share(imposter["id"], "*", "viewer"))
        tid = [t for t in self.ok(self.get("/api/space/trash"))["items"] if t["name"] == "Diary.txt"][0]["id"]
        self.ok(self.post(f"/api/trash/{tid}/restore"))
        self.assertEqual((self.row(n["id"])["parent_id"], self.row(n["id"])["rel"]), (None, "Diary.txt"))
        self.assertEqual(self.get(f"/api/docs/{n['id']}", MEERA).status_code, 404)


class SensorsFollowAccess(Base):
    def setUp(self):
        super().setUp()
        ha_sensors.reset()
        ha_sensors.REFRESH = sensor_publisher.Refresh(ha_sensors.REPOST_SECONDS, clock=lambda: 1000.0)

    def test_a_sensor_stops_when_its_maker_loses_access(self):
        c = self.create("checklist", "Medicines")
        self.ok(self.ops(c["id"], [{"op": "add", "text": "Insulin"}]))
        self.ok(self.share(c["id"], MEERA, "editor"))
        eid = self.ok(self.put(f"/api/nodes/{c['id']}/ha-sensor", {}, MEERA))["sensor"]["entityId"]
        ha_sensors.tick()
        self.assertIn(eid, self.ha.states)
        self.ok(self.delete(f"/api/nodes/{c['id']}/shares/u_meera"))
        self.ok(self.ops(c["id"], [{"op": "add", "text": "Something new"}]))
        with mock.patch.object(ha_sensors, "_tell_lost") as told:
            out = ha_sensors.tick()
        told.assert_called_once_with("u_meera", "Medicines")
        self.assertEqual(out["accessLost"], 1)
        self.assertNotIn(eid, self.ha.states)
        with db.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM ha_sensors").fetchone()[0], 0)


class AiReading(Base):
    def test_only_the_owner_or_a_manager_lets_the_ai_read_a_folder(self):
        self.settings({"ai_enabled": True, "ai_provider": "openai", "ai_url": "http://127.0.0.1:9/v1",
                       "ai_model": "m", "ai_api_key": "k"})
        f = self.create("folder", "Photos")
        self.ok(self.share(f["id"], MEERA, "editor"))
        self.assertFalse(self.ok(self.get(f"/api/ai/folder/{f['id']}", MEERA))["canChange"])
        self.assertEqual(self.put(f"/api/ai/folder/{f['id']}", {"on": True}, MEERA).status_code, 403)
        self.ok(self.share(f["id"], DEV, "manager"))
        self.assertTrue(self.ok(self.put(f"/api/ai/folder/{f['id']}", {"on": True}, DEV))["on"])
        self.assertTrue(self.ok(self.get(f"/api/ai/folder/{f['id']}", MEERA))["canChange"])     # she may turn it off
        self.assertFalse(self.ok(self.put(f"/api/ai/folder/{f['id']}", {"on": False}, MEERA))["on"])
        self.assertTrue(self.ok(self.put(f"/api/ai/folder/{f['id']}", {"on": True}))["on"])
        # an admin shared folder: Read and write is enough
        rid = self.shared_folder("Scans", {"u_meera": "rw"})
        self.assertTrue(self.ok(self.put(f"/api/ai/folder/root:{rid}", {"on": True}, MEERA))["on"])
        with db.get_conn() as conn:
            self.assertTrue(ai.folder_opted_in(conn, "root:" + rid))


class StaleSheetSettings(Base):
    def test_a_stale_save_doesnt_overwrite_someone_elses_tab_settings(self):
        it = self.ok(self.post("/api/docs", {"kind": "sheet", "name": "Budget"}), 201)
        self.ok(self.share(it["id"], MEERA, "editor"))
        old = self.open(it["id"])["etag"]
        tab = self.open(it["id"])["sheet"]["tabs"][0]["name"]
        theirs = self.open(it["id"], MEERA)["etag"]
        self.ok(self.patch(f"/api/docs/{it['id']}", {"etag": theirs, "cells": [],
                                                     "tabs": [{"name": tab, "cols": {"A": 300}}]}, MEERA))
        r = self.patch(f"/api/docs/{it['id']}", {"etag": old, "cells": [{"tab": tab, "ref": "B2", "was": None, "now": {"v": 5}}],
                                                 "tabs": [{"name": tab, "cols": {"A": 50}, "freeze": {"r": 1, "c": 0}}]})
        self.assertEqual(r.status_code, 409, r.text)
        d = r.json()["detail"]
        self.assertEqual(d["tabsNotSaved"], [tab])
        self.assertEqual(d["conflicts"], [])
        sheet = self.open(it["id"])["sheet"]["tabs"][0]
        self.assertEqual(sheet["cols"], {"A": 300})                 # Meera's widths stay
        self.assertEqual(sheet["cells"]["B2"], {"v": 5})            # the cell was still saved
        self.ok(self.patch(f"/api/docs/{it['id']}", {"etag": d["etag"], "cells": [], "tabs": [{"name": tab, "cols": {"A": 50}}]}))
        self.assertEqual(self.open(it["id"])["sheet"]["tabs"][0]["cols"], {"A": 50})


class KidsCopies(Base):
    PEOPLE = (ASHA, KABIR, MEERA, DEV, KID)

    def test_a_child_cant_make_a_sheet_by_copying(self):
        with db.get_conn() as conn:
            conn.execute("UPDATE users SET is_child = 1, child_since = ? WHERE id = 'u_kid'", (config.now_iso(),))
        s = self.ok(self.post("/api/docs", {"kind": "sheet", "name": "Budget"}), 201)
        f = self.create("folder", "Mixed")
        self.ok(self.post("/api/docs", {"kind": "sheet", "name": "Inner", "parentId": f["id"]}), 201)
        n = self.note("Plain")
        for it in (s, f, n):
            self.ok(self.share(it["id"], "u_kid", "viewer"))
        self.assertEqual(self.post(f"/api/nodes/{s['id']}/copy", {"parentId": None}, KID).status_code, 403)
        self.assertEqual(self.post(f"/api/nodes/{f['id']}/copy", {"parentId": None}, KID).status_code, 403)
        self.ok(self.post(f"/api/nodes/{n['id']}/copy", {"parentId": None}, KID), 201)


if __name__ == "__main__":
    unittest.main()
