"""The chat files folder as an App setting (SPEC §5.3.1): checked at start-up and every 5 minutes, set up
when an admin chooses it, and "not connected" (never written into) when it isn't there or isn't ours."""
import io
import json
import os
import shutil
import zipfile

from base import ADMIN, NISHA, TARUN, ApiTestCase, sql

from app import config, files, housekeeping, settings


class StorageTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.enable(ADMIN, NISHA, TARUN)
        self.hh = self.household()
        self.root = files.configured()
        self.share = os.path.dirname(self.root)

    def tearDown(self):
        shutil.rmtree(os.path.join(self.share, "nas"), ignore_errors=True)
        super().tearDown()

    def _file(self, name="Lease.pdf", data=b"%PDF-1.4 x"):
        up = self.ok(self.upload(self.hh, name, data, NISHA), 201)
        m = self.send(self.hh, "", NISHA, attachmentIds=[up["id"]])
        return up, m

    def _store_id(self):
        return json.loads(sql("SELECT value FROM app_settings WHERE key = 'files_store_id'")[0]["value"])

    def test_first_start_creates_what_the_app_needs(self):
        for sub in ("_thumbs", "_deleted", "README.txt", files.MARKER):
            self.assertTrue(os.path.exists(os.path.join(self.root, sub)), sub)
        self.assertEqual(files.read_marker(self.root), self._store_id())
        self.assertTrue(self.ok(self.get("/api/me", NISHA))["files"]["online"])
        st = self.ok(self.get("/api/admin/settings"))["storage"]
        self.assertTrue(st["online"])
        self.assertEqual(st["path"], self.root)

    def test_older_install_adopts_the_existing_folder(self):
        up, _ = self._file()
        os.remove(os.path.join(self.root, files.MARKER))
        sql("DELETE FROM app_settings WHERE key = 'files_store_id'")
        files.reset()
        self.assertTrue(files.check()["online"])
        self.assertEqual(self.get(f"/api/files/{up['id']}", TARUN).content, b"%PDF-1.4 x")

    def test_not_connected_waits_and_catches_up(self):
        up, m = self._file()
        os.remove(os.path.join(self.root, files.MARKER))          # e.g. the NAS isn't mounted
        st = files.check()
        self.assertFalse(st["online"])
        self.assertIn("missing", st["reason"])
        self.assertFalse(self.ok(self.get("/api/me", NISHA))["files"]["online"])
        # nothing is written or marked missing; uploads and downloads say why
        self.assertEqual(self.upload(self.hh, "b.txt", b"hi", NISHA).status_code, 503)
        self.assertEqual(self.get(f"/api/files/{up['id']}", TARUN).status_code, 503)
        housekeeping.nightly()
        self.assertEqual(sql("SELECT missing FROM attachments WHERE id = ?", (up["id"],))[0]["missing"], 0)
        # a delete meanwhile waits
        self.ok(self.delete(f"/api/messages/{m['id']}", NISHA))
        self.assertEqual(files.pending_jobs(), 1)
        rel = sql("SELECT rel_path FROM attachments WHERE id = ?", (up["id"],))
        self.assertTrue(any(f == "Lease.pdf" for _, _, fs in os.walk(os.path.join(self.root, f"Household ({self.hh[:8]})"))
                            for f in fs))
        # renaming a group waits too
        g = self.ok(self.post("/api/conversations", {"name": "Trip", "memberIds": [TARUN["id"]]}, NISHA), 201)["id"]
        self.ok(self.patch(f"/api/conversations/{g}", {"name": "Goa trip"}, NISHA))
        self.assertTrue(sql("SELECT folder FROM conversations WHERE id = ?", (g,))[0]["folder"].startswith("Trip"))
        # back: the waiting delete runs and the folder name follows
        with open(os.path.join(self.root, files.MARKER), "w") as f:
            f.write(self._store_id())
        self.assertTrue(files.check()["online"])
        self.assertEqual(files.pending_jobs(), 0)
        deleted = [f for _, _, fs in os.walk(os.path.join(self.root, "_deleted")) for f in fs]
        self.assertIn("Lease.pdf", deleted)
        self.assertTrue(sql("SELECT folder FROM conversations WHERE id = ?", (g,))[0]["folder"].startswith("Goa trip"))
        del rel

    def test_other_install_or_missing_folder_is_not_connected(self):
        with open(os.path.join(self.root, files.MARKER), "w") as f:
            f.write("someone-else")
        st = files.check()
        self.assertFalse(st["online"])
        self.assertIn("another Household Chat", st["reason"])
        # "Use this folder" takes it over
        self.ok(self.post("/api/admin/files-storage/check", {"useThisFolder": True}))
        self.assertTrue(files.is_online())
        shutil.rmtree(self.root)
        self.assertFalse(files.check()["online"])
        self.assertFalse(os.path.exists(self.root))               # never re-created by a routine check

    def test_path_rules(self):
        for bad in ("share/x", self.share, self.share + "/../etc", "/etc/household_chat", self.share + "/a/./b"):
            self.assertEqual(self.post("/api/admin/settings/check-files-path", {"path": bad}).status_code, 422, bad)
            self.assertEqual(self.put("/api/admin/settings", {"files_path": bad}).status_code, 422, bad)
        self.assertEqual(self.post("/api/admin/settings/check-files-path", {"path": self.root}, NISHA).status_code, 403)
        r = self.ok(self.post("/api/admin/settings/check-files-path", {"path": self.root + "/"}))
        self.assertEqual(r["verdict"], "current")
        r = self.ok(self.post("/api/admin/settings/check-files-path", {"path": self.share + "/nas/chat"}))
        self.assertEqual(r["verdict"], "missing_parent")
        self.assertTrue(r["refused"])

    def test_change_to_a_new_folder_creates_it(self):
        os.makedirs(self.share + "/nas")
        new = self.share + "/nas/household_chat"
        r = self.ok(self.post("/api/admin/settings/check-files-path", {"path": new}))
        self.assertEqual(r["verdict"], "new")
        self.assertFalse(os.path.exists(new))                      # checking has no side effects
        out = self.ok(self.put("/api/admin/settings", {"files_path": new}))
        self.assertTrue(out["storage"]["online"])
        for sub in ("_thumbs", "_deleted", "README.txt", files.MARKER):
            self.assertTrue(os.path.exists(os.path.join(new, sub)), sub)
        up, _ = self._file("a.txt", b"new place")
        self.assertTrue(sql("SELECT rel_path FROM attachments WHERE id = ?", (up["id"],)))
        self.assertTrue(any("a.txt" in fs for _, _, fs in os.walk(new)))

    def test_change_with_files_needs_confirm_and_never_moves(self):
        up, _ = self._file()
        os.makedirs(self.share + "/nas")
        new = self.share + "/nas/household_chat"
        r = self.ok(self.post("/api/admin/settings/check-files-path", {"path": new}))
        self.assertEqual(r["verdict"], "no_marker")
        self.assertTrue(r["needsConfirm"])
        self.assertEqual(self.put("/api/admin/settings", {"files_path": new}).status_code, 409)
        self.assertEqual(settings.get("files_path"), self.root)
        self.ok(self.put("/api/admin/settings", {"files_path": new, "confirm": True}))
        self.assertTrue(files.is_online())
        self.assertTrue(os.path.isfile(os.path.join(new, files.MARKER)))
        self.assertEqual(sql("SELECT missing FROM attachments WHERE id = ?", (up["id"],))[0]["missing"], 1)
        self.assertTrue(any("Lease.pdf" in fs for _, _, fs in os.walk(self.root)))    # the old files stay put
        # the old folder is this install's: switching back is safe, and the file is found again
        r = self.ok(self.post("/api/admin/settings/check-files-path", {"path": self.root}))
        self.assertEqual(r["verdict"], "this")
        self.ok(self.put("/api/admin/settings", {"files_path": self.root}))
        self.assertEqual(sql("SELECT missing FROM attachments WHERE id = ?", (up["id"],))[0]["missing"], 0)

    def test_refused_folders(self):
        os.makedirs(self.share + "/nas/other")
        with open(self.share + "/nas/other/" + files.MARKER, "w") as f:
            f.write("someone-else")
        r = self.ok(self.post("/api/admin/settings/check-files-path", {"path": self.share + "/nas/other"}))
        self.assertEqual(r["verdict"], "other")
        self.assertEqual(self.put("/api/admin/settings", {"files_path": self.share + "/nas/other", "confirm": True}).status_code, 409)
        # a folder shared into a group can't also be the chat's folder
        os.makedirs(self.share + "/nas/Docs")
        self.ok(self.post("/api/admin/folders", {"path": "nas/Docs", "chats": [{"id": self.hh, "mode": "ro"}]}), 201)
        r = self.ok(self.post("/api/admin/settings/check-files-path", {"path": self.share + "/nas/Docs"}))
        self.assertEqual(r["verdict"], "shared")
        self.assertEqual(self.put("/api/admin/settings", {"files_path": self.share + "/nas"}).status_code, 409)
        self.assertEqual(settings.get("files_path"), self.root)

    def test_restore_keeps_this_installs_folder(self):
        self._file()
        backup = self.get("/api/admin-storage-download-db?includeFiles=true").content
        os.makedirs(self.share + "/nas")
        new = self.share + "/nas/household_chat"
        self.ok(self.put("/api/admin/settings", {"files_path": new, "confirm": True}))
        store = self._store_id()
        r = self.ok(self.req("POST", "/api/admin-storage-import-db", ADMIN, content=backup))
        self.assertEqual(r["files"], 2)          # Lease.pdf and README.txt, never the marker
        self.assertEqual(settings.get("files_path"), new)
        self.assertEqual(self._store_id(), store)
        self.assertTrue(files.is_online())
        self.assertTrue(any("Lease.pdf" in fs for _, _, fs in os.walk(new)))
        # a backup with files can't be restored while the folder isn't connected
        os.remove(os.path.join(new, files.MARKER))
        files.check()
        self.assertEqual(self.req("POST", "/api/admin-storage-import-db", ADMIN, content=backup).status_code, 503)
        self.assertEqual(self.get("/api/admin-storage-download-db?includeFiles=true").status_code, 503)
        self.assertEqual(len(zipfile.ZipFile(io.BytesIO(self.get("/api/admin-storage-download-db").content)).namelist()), 1)
        s = self.ok(self.get("/api/admin/storage"))
        self.assertFalse(s["storage"]["online"])
        self.assertIsNone(s["deletedBytes"])

    def test_storage_event_when_it_changes(self):
        from app.live import hub
        seen = []
        orig = hub.publish_all
        hub.publish_all = lambda name, data, *a, **k: seen.append(name)
        try:
            os.remove(os.path.join(self.root, files.MARKER))
            files.check()
            files.check()
        finally:
            hub.publish_all = orig
        self.assertEqual(seen.count("storage"), 1)
        self.assertEqual(config.SHARE_ROOT, self.share)
