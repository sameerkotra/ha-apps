import io
import os
import zipfile
from datetime import timedelta

from base import ADMIN, NISHA, TARUN, ApiTestCase, sql

from app import config, db, housekeeping, settings


class AdminTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.enable(ADMIN, NISHA, TARUN)
        self.hh = self.household()

    def test_backup_and_restore_with_files(self):
        up = self.ok(self.upload(self.hh, "keep.txt", b"keep me", NISHA), 201)
        self.send(self.hh, "before backup", NISHA, attachmentIds=[up["id"]])
        r = self.get("/api/admin-storage-download-db?includeFiles=true")
        self.assertEqual(r.status_code, 200)
        z = zipfile.ZipFile(io.BytesIO(r.content))
        names = z.namelist()
        self.assertIn("chat.db", names)
        self.assertTrue(any(n.endswith("keep.txt") for n in names))
        self.assertFalse(any("_thumbs" in n for n in names))
        self.send(self.hh, "after backup", NISHA)
        rel = sql("SELECT rel_path FROM attachments WHERE id = ?", (up["id"],))[0]["rel_path"]
        os.remove(os.path.join(config.SHARE_DIR, rel))
        self.assertEqual(self.req("POST", "/api/admin-storage-import-db", NISHA, content=r.content).status_code, 403)
        self.ok(self.req("POST", "/api/admin-storage-import-db", ADMIN, content=r.content))
        bodies = [m["body"] for m in self.ok(self.get(f"/api/conversations/{self.hh}/messages", NISHA))["messages"]]
        self.assertIn("before backup", bodies)
        self.assertNotIn("after backup", bodies)
        self.assertEqual(self.get(f"/api/files/{up['id']}", TARUN).content, b"keep me")
        # without files, just the database
        r2 = self.get("/api/admin-storage-download-db")
        self.assertEqual(zipfile.ZipFile(io.BytesIO(r2.content)).namelist(), ["chat.db"])

    def test_restore_refuses_bad_zips(self):
        self.assertEqual(self.req("POST", "/api/admin-storage-import-db", ADMIN, content=b"junk").status_code, 422)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("chat.db", b"not sqlite")
        self.assertEqual(self.req("POST", "/api/admin-storage-import-db", ADMIN, content=buf.getvalue()).status_code, 422)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("chat.db", b"x")
            z.writestr("files/../../evil.txt", b"x")
        self.assertEqual(self.req("POST", "/api/admin-storage-import-db", ADMIN, content=buf.getvalue()).status_code, 422)

    def test_retention(self):
        old = self.send(self.hh, "old news", NISHA)
        room = self.personal(NISHA)
        note = self.send(room, "old note", NISHA)
        self.send(self.hh, "new", NISHA)
        with db.get_conn() as conn:
            conn.execute("UPDATE messages SET created_at = '2020-01-01T00:00:00.000Z' WHERE id IN (?, ?)", (old["id"], note["id"]))
        self.assertEqual(housekeeping.retention(), 0)          # off by default
        self.ok(self.put("/api/admin/settings", {"message_retention_days": 30}))
        self.assertEqual(housekeeping.retention(), 1)          # personal rooms are left alone
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM messages WHERE id = ?", (note["id"],))[0]["n"], 1)
        self.ok(self.put("/api/admin/settings", {"retention_includes_personal": True}))
        self.assertEqual(housekeeping.retention(), 1)

    def test_people_and_notify_admin(self):
        ppl = self.ok(self.get("/api/admin/people"))["people"]
        self.assertEqual({p["id"] for p in ppl}, {ADMIN["id"], NISHA["id"], TARUN["id"]})
        self.assertEqual(self.post(f"/api/admin/people/{NISHA['id']}/notify", {"service": "notify.Bad Name"}).status_code, 422)
        self.notify_to(NISHA)
        self.assertEqual(self.ok(self.get("/api/me", NISHA))["notifyLinked"], True)
        self.ok(self.delete(f"/api/admin/people/{NISHA['id']}/notify/notify.mobile_app_phone"))
        self.assertEqual(self.patch(f"/api/admin/people/{NISHA['id']}", {"disabled": "no"}).status_code, 422)

    def test_storage_page_is_metadata(self):
        up = self.ok(self.upload(self.hh, "Private scan.pdf", b"%PDF-1", NISHA), 201)
        self.send(self.hh, "secret text", NISHA, attachmentIds=[up["id"]])
        s = self.ok(self.get("/api/admin/storage"))
        self.assertEqual(s["filesBytes"], 6)
        self.assertNotIn("Private scan", str(s))
        self.assertNotIn("secret text", str(s))
        self.ok(self.post("/api/admin/storage/check"))

    def test_whoami_and_me(self):
        w = self.ok(self.get("/api/whoami", NISHA))
        self.assertEqual(w["haUserId"], NISHA["id"])
        self.assertFalse(w["isAdmin"])
        me = self.ok(self.get("/api/me", NISHA))
        self.assertEqual(me["app"]["editWindowMinutes"], 15)
        self.assertTrue(me["personalRoomId"])


class FirstRunTests(ApiTestCase):
    """With admin_users empty nobody is an admin: every page says what to do, and nobody is promoted."""

    def setUp(self):
        super().setUp()
        self._admins = config.ADMIN_NAMES
        config.ADMIN_NAMES = set()

    def tearDown(self):
        config.ADMIN_NAMES = self._admins
        super().tearDown()

    def test_no_admin_flag_and_nobody_promoted(self):
        for user in (ADMIN, NISHA):                       # the first visitor too
            me = self.ok(self.get("/api/me", user))
            self.assertTrue(me["noAdmin"])
            self.assertFalse(me["isAdmin"])
            self.assertTrue(me["disabled"])
            w = self.ok(self.get("/api/whoami", user))
            self.assertTrue(w["noAdmin"])
            self.assertEqual(w["adminEntries"], 0)
            self.assertEqual(w["haUsername"], user["name"])
            self.assertEqual(self.get("/api/admin/settings", user).status_code, 403)
            self.assertEqual(self.patch(f"/api/admin/people/{user['id']}", {"disabled": False}, user).status_code, 403)
        config.ADMIN_NAMES = self._admins
        self.assertFalse(self.ok(self.get("/api/me", ADMIN))["noAdmin"])
        self.assertFalse(self.ok(self.get("/api/whoami", NISHA))["noAdmin"])

    def test_banner_is_on_every_page(self):
        static = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "static")

        def read(name):
            with open(os.path.join(static, name), encoding="utf-8") as f:
                return f.read()
        index = read("index.html")
        # above #app, so it shows on the chat shell, the "no access yet" page and the admin-only view alike
        self.assertLess(index.index('id="setupBanner"'), index.index('id="app"'))
        pages = read("pages.js")
        self.assertIn("function renderSetupBanner()", pages)
        self.assertIn("state.me.noAdmin", pages)
        self.assertIn("HouseholdWhoami.noAdminBanner(", pages)
        self.assertIn('src="common/whoami.js', index)
        shared = read(os.path.join("common", "whoami.js"))          # the banner's text, the same in every app
        for text in ("No admin yet", "add your Home Assistant user name", "admin_users",
                     "Configuration tab, save, and restart the app", "How the app sees you"):
            self.assertIn(text, shared)
        boot = read("main.js")
        self.assertLess(boot.index("renderSetupBanner();"), boot.index("renderDisabled();"))
        self.assertIn(".setup-banner", read("style.css"))
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn('id="setupBanner"', page.text)
