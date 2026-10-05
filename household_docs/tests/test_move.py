"""Moving the documents folder (SPEC §5.7): read-only mode blocks every write (people and background jobs), the
copy check finds missing / different / extra files (quick and deep), the switch keeps node ids, shares, tags,
ticks and history by relative path and leaves the old folder untouched apart from its marker, and switch back."""
import _env  # noqa: F401

import hashlib
import json
import os
import shutil
import time
import unittest

from app import db, housekeeping
from app.store import marker, trash, versions
from base import ASHA, DOCS, KABIR, MEERA, SHARE, ApiBase, person_dir, read, write

NAS = os.path.join(SHARE, "nas")
NEW = os.path.join(NAS, "household_docs")


def tree(base, skip_marker=True):
    """{rel: sha256 or 'dir'} for everything under base."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(base):
        rel_dir = os.path.relpath(dirpath, base)
        for d in dirnames:
            out[os.path.normpath(os.path.join(rel_dir, d))] = "dir"
        for f in filenames:
            rel = os.path.normpath(os.path.join(rel_dir, f))
            if skip_marker and rel == marker.MARKER:
                continue
            with open(os.path.join(dirpath, f), "rb") as fh:
                out[rel] = hashlib.sha256(fh.read()).hexdigest()
    return out


class MoveBase(ApiBase):
    def setUp(self):
        super().setUp()
        os.makedirs(NAS, exist_ok=True)

    def ro(self, on, h=ASHA):
        return self.post("/api/admin/docs-folder/read-only", {"on": on}, h)

    def check(self, path="/share/nas/household_docs", deep=False):
        job = self.ok(self.post("/api/admin/docs-folder/check", {"path": path, "deep": deep}, ASHA))
        for _ in range(500):
            job = self.ok(self.get(f"/api/admin/docs-folder/check/{job['id']}", ASHA))
            if job["phase"] in ("done", "failed"):
                return job
            time.sleep(0.01)
        self.fail("the check didn't finish")

    def copy(self):
        shutil.copytree(DOCS, NEW, symlinks=True, copy_function=shutil.copy2,
                        ignore=shutil.ignore_patterns(".tmp"))

    def switch(self, path="/share/nas/household_docs", job=None, **kw):
        body = {"path": path, "jobId": job["id"]}
        body.update(kw)
        return self.post("/api/admin/docs-folder/switch", body, ASHA)

    def sample(self):
        f = self.create("folder", "Trip")
        n = self.note("Plan", f["id"], text="one")
        self.clock.advance(minutes=10)
        self.save(n["id"], "two")                     # a version
        c = self.create("checklist", "Packing", f["id"])
        items = self.ok(self.ops(c["id"], [{"op": "add", "text": "Passports"}, {"op": "add", "text": "Tickets"}]))["items"]
        self.ok(self.ops(c["id"], [{"op": "tick", "key": items[0]["key"]}]))
        self.ok(self.share(f["id"], MEERA, "editor"))
        self.ok(self.post(f"/api/nodes/{n['id']}/favourite", {"value": True}))
        self.ok(self.put(f"/api/nodes/{n['id']}/tags", {"tags": ["travel"], "color": "blue"}))
        gone = self.note("Old", text="bye")
        self.ok(self.delete(f"/api/nodes/{gone['id']}"))
        return f, n, c


class ReadOnlyMode(MoveBase):
    def test_only_admins_switch_it_and_everyone_sees_the_banner(self):
        self.assertEqual(self.ro(True, KABIR).status_code, 403)
        st = self.ok(self.ro(True))
        self.assertTrue(st["readOnly"]["on"])
        me = self.ok(self.get("/api/me"))
        self.assertEqual(me["readOnly"]["byName"], "Asha Rao")
        self.assertNotIn("target", me["readOnly"])
        self.ok(self.ro(False))
        self.assertIsNone(self.ok(self.get("/api/me"))["readOnly"])
        with db.get_conn() as conn:
            actions = [r["action"] for r in conn.execute("SELECT action FROM audit_log ORDER BY created_at, rowid")]
        self.assertIn("read_only_on", actions)
        self.assertIn("read_only_off", actions)

    def test_every_write_is_refused(self):
        f, n, c = self.sample()
        [t] = self.ok(self.get("/api/space/trash"))["items"]
        before = tree(DOCS, skip_marker=False)
        self.ok(self.ro(True))
        doc = self.open(n["id"])
        self.assertFalse(doc["canEdit"])
        self.assertTrue(doc["readOnlyMode"])
        self.assertFalse(self.open(c["id"])["canTick"])
        self.assertFalse(self.ok(self.get(f"/api/list?node={f['id']}"))["canEdit"])
        attempts = [
            self.patch(f"/api/docs/{n['id']}", {"etag": doc["etag"], "text": "three"}),
            self.post("/api/docs", {"kind": "note", "name": "New"}),
            self.post("/api/docs", {"kind": "folder", "name": "New"}),
            self.ops(c["id"], [{"op": "add", "text": "Hats"}]),
            self.post(f"/api/nodes/{n['id']}/rename", {"name": "Renamed"}),
            self.post(f"/api/nodes/{n['id']}/move", {"parentId": None}),
            self.post(f"/api/nodes/{n['id']}/copy", {}),
            self.delete(f"/api/nodes/{n['id']}"),
            self.post(f"/api/trash/{t['id']}/restore"),
            self.post("/api/trash/empty"),
            self.c.post("/api/nodes/mine/upload?name=x.bin", content=b"x", headers=KABIR),
            self.post(f"/api/docs/{n['id']}/versions/1/restore"),
            self.post("/api/quick-note", {}),
            self.post(f"/api/docs/{n['id']}/convert"),
            self.post(f"/api/docs/{n['id']}/pdf", {}),
            self.post(f"/api/admin/users/{self.uid(KABIR)}/docs/delete", {"confirm": "Kabir Rao"}, ASHA),
            self.post("/api/admin/docs-folder/choose", {"path": "/share/household_docs"}, ASHA),
        ]
        for r in attempts:
            self.assertEqual(r.status_code, 423, f"{r.request.method} {r.request.url}: {r.text}")
        self.assertIn("Documents are being moved", attempts[0].json()["detail"])
        # people can still read, search and download
        self.assertEqual(self.open(n["id"], MEERA)["text"], "two")
        self.assertTrue(self.ok(self.get("/api/search?q=Plan"))["results"])
        self.assertEqual(self.get(f"/api/nodes/{n['id']}/file").content, b"two")
        # background jobs that write pause: Trash clean-up, version pruning, temp files; a scan writes nothing
        self.clock.advance(days=400)
        write(os.path.join(DOCS, ".tmp", "w-old.part"), b"x", mtime=1)
        before = tree(DOCS, skip_marker=False)
        self.assertEqual(trash.purge_old(), 0)
        self.assertEqual(versions.prune_replaced(), 0)
        self.assertEqual(housekeeping.remove_stale_tmp(0), 0)
        housekeeping.hourly()
        self.scan()
        self.assertEqual(tree(DOCS, skip_marker=False), before)
        # a new person's folder waits too
        self.ok(self.get("/api/me", {**KABIR, "X-Remote-User-Id": "u_new", "X-Remote-User-Name": "newbie",
                                     "X-Remote-User-Display-Name": "New Person"}))
        self.assertFalse(os.path.exists(person_dir("New Person")))
        self.ok(self.ro(False))
        self.save(n["id"], "three")
        self.assertEqual(read(self.real(n["id"])), "three")
        self.assertEqual(trash.purge_old(), 1)


class CopyCheck(MoveBase):
    def test_target_rules(self):
        self.sample()
        bad = {"/share/household_docs": "current", "/share/household_docs/people/x": "nested", "/share": "refused"}
        for path, verdict in bad.items():
            r = self.ok(self.post("/api/admin/docs-folder/target", {"path": path}, ASHA))
            self.assertFalse(r["ok"], path)
            self.assertEqual(r["verdict"], verdict, path)
        other = os.path.join(SHARE, "other")
        write(os.path.join(other, ".household_docs"), json.dumps({"install": "someone-else"}))
        self.assertEqual(self.ok(self.post("/api/admin/docs-folder/target", {"path": "/share/other"}, ASHA))["verdict"],
                         "other_install")
        self.assertEqual(self.post("/api/admin/docs-folder/check", {"path": "/share/other"}, ASHA).status_code, 409)
        r = self.ok(self.post("/api/admin/docs-folder/target", {"path": "/share/nas/household_docs"}, ASHA))
        self.assertTrue(r["ok"])
        self.assertEqual(r["copy"]["rsync"], 'rsync -a "/share/household_docs/" "/share/nas/household_docs/"')
        self.assertEqual(r["copy"]["sambaTo"], "\\\\homeassistant\\share\\nas\\household_docs")
        self.assertEqual(self.post("/api/admin/docs-folder/target", {"path": "/share/nas/x"}, KABIR).status_code, 403)

    def test_check_finds_missing_different_and_extra(self):
        f, n, c = self.sample()
        self.copy()
        job = self.check()
        r = job["result"]
        self.assertEqual((r["missingCount"], r["differentCount"], r["extraCount"], r["problems"]), (0, 0, 0, 0))
        self.assertEqual(r["from"], r["to"])
        self.assertTrue(r["marker"]["present"] and r["marker"]["ours"])
        self.assertEqual(r["hidden"], {"people": True, ".trash": True, ".versions": True})
        self.assertIsInstance(r["free"], int)
        kabir = os.path.join(NEW, "people", "Kabir Rao")
        os.remove(os.path.join(kabir, "Trip", "Plan.txt"))                     # missing
        with open(os.path.join(kabir, "Trip", "Packing.md"), "a", encoding="utf-8") as fh:
            fh.write("- [ ] More\n")                                        # different size
        same = os.path.join(kabir, "Trip", "Twin.txt")
        write(os.path.join(person_dir("Kabir Rao"), "Trip", "Twin.txt"), "aaaa", mtime=1_700_000_000)
        write(same, "bbbb", mtime=1_700_000_000)                              # same size and time, other content
        write(os.path.join(kabir, "Extra.txt"), "new")                         # extra
        shutil.rmtree(os.path.join(NEW, ".versions"))
        os.remove(os.path.join(NEW, marker.MARKER))
        quick = self.check()["result"]
        self.assertEqual([x["path"] for x in quick["missing"] if x["path"].startswith("people/")],
                         ["people/Kabir Rao/Trip/Plan.txt"])
        self.assertIn(".versions", [x["path"] for x in quick["missing"]])
        self.assertEqual([(x["path"], x["why"]) for x in quick["different"]], [("people/Kabir Rao/Trip/Packing.md", "size")])
        self.assertEqual([x["path"] for x in quick["extra"]], ["people/Kabir Rao/Extra.txt"])
        self.assertGreaterEqual(quick["missingCount"], 1)                     # the .versions files are missing too
        self.assertFalse(quick["hidden"][".versions"])
        self.assertFalse(quick["marker"]["present"])
        deep = self.check(deep=True)
        self.assertIn(("people/Kabir Rao/Trip/Twin.txt", "content (SHA-256)"),
                      [(x["path"], x["why"]) for x in deep["result"]["different"]])
        self.assertEqual(deep["progress"], 1.0)
        rep = self.get(f"/api/admin/docs-folder/check/{deep['id']}/report.csv", ASHA)
        self.assertEqual(rep.status_code, 200)
        self.assertIn("attachment", rep.headers["content-disposition"])
        text = rep.content.decode("utf-8-sig")
        self.assertIn("Missing in the new location,people/Kabir Rao/Trip/Plan.txt", text)
        self.assertIn("Only in the new location,people/Kabir Rao/Extra.txt", text)
        # a quick check of a copy that didn't keep modified times sees them as different; a deep one doesn't
        shutil.rmtree(NEW)
        shutil.copytree(DOCS, NEW, copy_function=shutil.copyfile, ignore=shutil.ignore_patterns(".tmp"))
        later = time.time() + 3600
        for dirpath, _d, files in os.walk(NEW):
            for name in files:
                os.utime(os.path.join(dirpath, name), (later, later))
        self.assertGreater(self.check()["result"]["differentCount"], 0)
        self.assertEqual(self.check(deep=True)["result"]["differentCount"], 0)

    def test_lists_are_capped_with_a_full_report(self):
        for i in range(230):
            write(os.path.join(person_dir("Kabir Rao"), f"f{i:03}.txt"), "x")
        os.makedirs(NEW)
        r = self.check()["result"]
        self.assertGreater(r["missingCount"], 230)        # the files, and the folders above them
        self.assertEqual(len(r["missing"]), 200)


class Switch(MoveBase):
    def test_switch_keeps_everything_and_leaves_the_old_folder(self):
        f, n, c = self.sample()
        self.ok(self.ro(True))
        self.copy()
        old_tree = tree(DOCS)
        job = self.check()
        out = self.ok(self.switch(job=job))
        self.assertEqual(out["path"], "/share/nas/household_docs")
        self.assertEqual(out["previous"], "/share/household_docs")
        self.assertIsNone(out["folder"]["readOnly"])
        self.assertEqual(out["folder"]["path"], "/share/nas/household_docs")
        self.assertTrue(out["folder"]["previous"]["exists"])
        # the old folder: untouched apart from its marker's moved_to
        self.assertEqual(tree(DOCS), old_tree)
        with open(os.path.join(DOCS, marker.MARKER), encoding="utf-8") as fh:
            old = json.load(fh)
        self.assertEqual(old["moved_to"], "/share/nas/household_docs")
        with open(os.path.join(NEW, marker.MARKER), encoding="utf-8") as fh:
            self.assertNotIn("moved_to", json.load(fh))
        # same ids, shares, favourites, tags, ticks, history, Trash
        doc = self.open(n["id"], MEERA)
        self.assertEqual(doc["text"], "two")
        self.assertTrue(self.real(n["id"]).startswith(os.path.realpath(NEW)))
        self.assertEqual(len(self.ok(self.get(f"/api/docs/{n['id']}/versions"))["versions"]), 1)
        self.assertEqual(self.ok(self.get(f"/api/docs/{n['id']}/versions/1"))["text"], "one")
        self.assertTrue(self.ok(self.get(f"/api/nodes/{n['id']}"))["favourite"])
        self.assertEqual(self.ok(self.get(f"/api/nodes/{n['id']}/tags"))["tags"], ["travel"])
        items = self.open(c["id"])["items"]
        self.assertEqual(items[0]["doneByName"], "Kabir Rao")
        [t] = self.ok(self.get("/api/space/trash"))["items"]
        self.ok(self.post(f"/api/trash/{t['id']}/restore"))
        # writes go to the new folder now
        self.save(n["id"], "three")
        self.assertEqual(read(os.path.join(NEW, "people", "Kabir Rao", "Trip", "Plan.txt")), "three")
        self.assertEqual(read(os.path.join(person_dir("Kabir Rao"), "Trip", "Plan.txt")), "two")
        with db.get_conn() as conn:
            actions = [r["action"] for r in conn.execute("SELECT action FROM audit_log")]
        for a in ("docs_location_checked", "docs_location_switched", "read_only_on", "read_only_off"):
            self.assertIn(a, actions)

    def test_problems_need_a_typed_confirmation(self):
        f, n, c = self.sample()
        self.copy()
        os.remove(os.path.join(NEW, "people", "Kabir Rao", "Trip", "Plan.txt"))
        job = self.check()
        self.assertEqual(job["result"]["problems"], 1)
        self.assertEqual(self.switch(job=job).status_code, 409)
        self.assertEqual(self.switch(job=job, force=True, confirm="2").status_code, 422)
        self.assertEqual(self.switch(job=job, force=True, confirm="1", path="/share/nas/other").status_code, 409)
        self.assertEqual(self.post("/api/admin/docs-folder/switch", {"path": "/share/nas/household_docs", "jobId": job["id"]}, KABIR).status_code, 403)
        self.ok(self.switch(job=job, force=True, confirm="1"))
        self.assertEqual(self.get(f"/api/docs/{n['id']}").status_code, 404)      # gone in the new place
        self.assertEqual(self.ok(self.get(f"/api/docs/{c['id']}"))["kind"], "checklist")

    def test_switch_back_while_the_old_folder_exists(self):
        f, n, c = self.sample()
        self.copy()
        self.ok(self.switch(job=self.check()))
        self.save(n["id"], "changed in the new place")
        st = self.ok(self.get("/api/admin/docs-folder", ASHA))
        self.assertEqual(st["previous"]["path"], "/share/household_docs")
        back = self.check(path="/share/household_docs")          # the old folder still has the old text
        self.assertIn("people/Kabir Rao/Trip/Plan.txt", [x["path"] for x in back["result"]["different"]])
        self.assertEqual(self.switch(path="/share/household_docs", job=back).status_code, 409)
        problems = back["result"]["problems"]
        self.ok(self.switch(path="/share/household_docs", job=back, force=True, confirm=str(problems)))
        self.assertEqual(self.open(n["id"])["text"], "two")       # what the old folder holds
        self.assertTrue(self.real(n["id"]).startswith(os.path.realpath(DOCS)))
        with open(os.path.join(DOCS, marker.MARKER), encoding="utf-8") as fh:
            self.assertNotIn("moved_to", json.load(fh))
        st = self.ok(self.get("/api/admin/docs-folder", ASHA))
        self.assertEqual(st["previous"]["path"], "/share/nas/household_docs")
        shutil.rmtree(NEW)
        self.assertFalse(self.ok(self.get("/api/admin/docs-folder", ASHA))["previous"]["exists"])


if __name__ == "__main__":
    unittest.main()
