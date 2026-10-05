"""Admin (SPEC §4, §13): App settings, People (access, folder names, counts and sizes, notify services), "How the
app sees you" and "No admin yet", Rescan now, backup and restore."""
import _env  # noqa: F401

import io
import os
import unittest
import zipfile

from app import config, db, settings
from base import ASHA, KABIR, MEERA, NOBODY, SHARE, ApiBase, person_dir, read, write


class Settings(ApiBase):
    def test_get_put_and_validation(self):
        p = self.ok(self.get("/api/admin/settings", ASHA))
        self.assertEqual(p["values"]["versions_kept"], 30)
        self.assertTrue(p["meta"]["docs_path"]["hidden"])
        for key in ("folder_names", "new_people_access", "everyone_shares", "everyone_default_role", "versions_kept",
                    "trash_days", "max_doc_mb", "quota_gb", "scan_minutes", "content_index_mb", "secret_hint",
                    "backup_files"):
            self.assertIn(key, p["values"])
        self.assertEqual(self.get("/api/admin/settings", KABIR).status_code, 403)
        out = self.settings({"versions_kept": 10, "trash_days": 7})
        self.assertEqual((out["values"]["versions_kept"], out["values"]["trash_days"]), (10, 7))
        for bad in ({"versions_kept": 4}, {"trash_days": 400}, {"max_doc_mb": 26}, {"scan_minutes": 1},
                    {"everyone_default_role": "manager"}, {"nope": 1}, {"docs_path": "/share/x"}):
            with self.subTest(bad):
                self.assertEqual(self.put("/api/admin/settings", bad, ASHA).status_code, 422)

    def test_new_people_access(self):
        self.settings({"new_people_access": False})
        me = self.ok(self.get("/api/me", NOBODY))
        self.assertTrue(me["disabled"])
        self.assertIsNone(me["folder"])                            # no folder until they have access
        self.ok(self.patch("/api/admin/users/u_nobody", {"disabled": False}, ASHA))
        self.assertEqual(self.ok(self.get("/api/me", NOBODY))["folder"], "Nobody Else")

    def test_folder_names_setting(self):
        self.settings({"folder_names": "username"})
        self.assertEqual(self.ok(self.get("/api/me", NOBODY))["folder"], "nobody")

    def test_folder_name_clash(self):
        dup = {"X-Remote-User-Id": "u_k2", "X-Remote-User-Name": "kabir2", "X-Remote-User-Display-Name": "Kabir Rao"}
        self.assertEqual(self.ok(self.get("/api/me", dup))["folder"], "Kabir Rao (kabir2)")


class People(ApiBase):
    def test_list_counts_and_sizes_only(self):
        self.note("A", text="12345")
        self.note("B", text="678")
        self.create("folder", "F")
        users = {u["id"]: u for u in self.ok(self.get("/api/admin/users", ASHA))["users"]}
        k = users["u_kabir"]
        self.assertEqual((k["docCount"], k["docSize"], k["folder"]), (2, 8, "Kabir Rao"))
        self.assertTrue(users["u_asha"]["isAdmin"])
        self.assertNotIn("12345", str(users))
        self.assertEqual(self.get("/api/admin/users", KABIR).status_code, 403)

    def test_rename_folder(self):
        n = self.note("A", text="x")
        self.ok(self.share(n["id"], MEERA, "viewer"))
        out = self.ok(self.patch("/api/admin/users/u_kabir", {"folder": "Kabir"}, ASHA))
        self.assertEqual(out["folder"], "Kabir")
        self.assertTrue(os.path.isfile(os.path.join(person_dir("Kabir"), "A.txt")))
        self.assertEqual(self.open(n["id"], MEERA)["text"], "x")
        self.assertEqual(self.patch("/api/admin/users/u_kabir", {"folder": "Meera Rao"}, ASHA).status_code, 422)
        self.assertEqual(self.patch("/api/admin/users/u_kabir", {"folder": "bad/name"}, ASHA).status_code, 422)
        self.assertEqual(self.patch("/api/admin/users/u_none", {"disabled": True}, ASHA).status_code, 404)
        self.assertEqual(self.patch("/api/admin/users/u_kabir", {"isRobot": True}, ASHA).status_code, 422)

    def test_notify_services(self):
        self.ha.notify_services = ["mobile_app_tablet"]
        out = self.ok(self.post("/api/admin/users/u_kabir/notify", {"service": "notify.mobile_app_tablet"}, ASHA), 201)
        self.assertEqual(out["notify"], ["notify.mobile_app_tablet"])
        self.assertEqual(self.post("/api/admin/users/u_kabir/notify", {"service": "bad name"}, ASHA).status_code, 422)
        res = self.ok(self.post("/api/admin/users/u_kabir/notify/test", {}, ASHA))["results"]
        self.assertTrue(res[0]["ok"])
        self.assertEqual(self.post("/api/admin/users/u_kabir/notify/test", {}, ASHA).status_code, 429)
        self.ok(self.delete("/api/admin/users/u_kabir/notify/notify.mobile_app_tablet", ASHA))
        self.assertEqual(self.post("/api/admin/users/u_meera/notify/test", {}, ASHA).status_code, 409)
        avail = self.ok(self.get("/api/admin/notify-services", ASHA))
        self.assertEqual(avail["services"], ["notify.mobile_app_tablet"])

    def test_people_from_home_assistant(self):
        self.ha.people = [{"entity_id": "person.lena", "name": "Lena", "user_id": "u_lena", "state": "home", "phones": []}]
        self.ok(self.get("/api/admin/users?refresh=1", ASHA))
        people = self.ok(self.get("/api/people"))["people"]
        self.assertIn("Lena", [p["name"] for p in people])
        n = self.note("For Lena")
        self.ok(self.share(n["id"], "u_lena", "viewer"))             # before she ever opened the app


class Whoami(ApiBase):
    def setUp(self):
        super().setUp()
        self._admins = set(config.ADMIN_NAMES)

    def tearDown(self):
        config.ADMIN_NAMES.clear()
        config.ADMIN_NAMES.update(self._admins)
        super().tearDown()

    def test_whoami_and_no_admin(self):
        w = self.ok(self.get("/api/whoami"))
        self.assertEqual(w["haUserId"], "u_kabir")
        self.assertFalse(w["isAdmin"])
        self.assertEqual(w["adminEntries"], 1)
        labels = [r["label"] for r in w["extras"]]
        self.assertEqual(labels, ["Access", "Your folder", "Files in your folder", "Phone linked for notifications"])
        self.assertTrue(self.ok(self.get("/api/whoami", ASHA))["isAdmin"])
        config.ADMIN_NAMES.clear()
        me = self.ok(self.get("/api/me", ASHA))
        self.assertTrue(me["noAdmin"])
        self.assertFalse(me["isAdmin"])
        self.assertEqual(self.get("/api/admin/settings", ASHA).status_code, 403)
        self.assertEqual(self.ok(self.get("/api/whoami", ASHA))["adminEntries"], 0)

    def test_display_name_never_makes_an_admin(self):
        config.ADMIN_NAMES.add("kabir rao")
        w = self.ok(self.get("/api/whoami"))
        self.assertFalse(w["isAdmin"])
        self.assertTrue(w["displayNameOnly"])

    def test_banner_and_scripts_on_the_page(self):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        index = read(os.path.join(here, "app", "static", "index.html"))
        self.assertIn('id="noAdminBanner"', index)
        self.assertIn('src="common/whoami.js', index)
        js = read(os.path.join(here, "app", "static", "app.js"))
        self.assertIn('HouseholdWhoami.fillNoAdminBanner($("#noAdminBanner")', js)


class Rescan(ApiBase):
    def test_rescan_now(self):
        write(os.path.join(person_dir("Kabir Rao"), "dropped.txt"), "x")
        out = self.ok(self.post("/api/admin/rescan", {}, ASHA))
        self.assertEqual(out["new"], 1)
        self.assertEqual(self.post("/api/admin/rescan", {}, KABIR).status_code, 403)


class Backup(ApiBase):
    def download(self):
        r = self.get("/api/admin/backup", ASHA)
        self.assertEqual(r.status_code, 200, r.text)
        return r.content

    def restore(self, data, **params):
        q = "?" + "&".join(f"{k}={v}" for k, v in params.items()) if params else ""
        return self.c.post("/api/admin/restore" + q, content=data, headers=ASHA)

    def test_database_only_backup_and_restore(self):
        n = self.note("Plan", text="kept on disk")
        self.ok(self.share(n["id"], MEERA, "viewer"))
        data = self.download()
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            self.assertEqual(z.namelist(), ["docs.db"])
        # lose the sharing, then restore
        self.ok(self.delete(f"/api/nodes/{n['id']}/shares/u_meera"))
        self.assertEqual(self.get(f"/api/docs/{n['id']}", MEERA).status_code, 404)
        self.ok(self.restore(data))
        self.assertEqual(self.open(n["id"], MEERA)["text"], "kept on disk")
        self.assertEqual(self.get("/api/admin/backup", KABIR).status_code, 403)

    def test_backup_leaves_out_the_access_key_and_restore_keeps_ours(self):
        key = "sk-test-not-a-real-key-4321"
        settings.REGISTRY.update({"ai_api_key": key}, "test")
        data = self.download()
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            raw = z.read("docs.db")
        self.assertNotIn(key.encode(), raw)
        self.assertEqual(settings.get("ai_api_key"), key)                # the live database keeps it
        self.ok(self.restore(data))
        settings.invalidate()
        self.assertEqual(settings.get("ai_api_key"), key)                # restoring doesn't wipe it

    def test_backup_with_files_and_restore_rules(self):
        self.settings({"backup_files": True})
        n = self.note("Plan", text="in the zip")
        data = self.download()
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = z.namelist()
        self.assertIn("files/people/Kabir Rao/Plan.txt", names)
        self.assertNotIn("files/.household_docs", names)
        self.assertEqual(self.restore(data).status_code, 409)              # the folder isn't empty
        os.remove(self.real(n["id"]))
        self.ok(self.restore(data, replaceFiles="true"))
        self.assertEqual(read(os.path.join(person_dir("Kabir Rao"), "Plan.txt")), "in the zip")
        self.assertEqual(self.open(n["id"])["text"], "in the zip")

    def test_restore_keeps_this_installs_folder(self):
        with db.get_conn() as conn:
            iid = db.state_get(conn, "install_id")
        data = self.download()
        os.makedirs(os.path.join(SHARE, "nas"))
        with db.get_conn() as conn:
            conn.execute("DELETE FROM nodes")
        self.ok(self.post("/api/admin/docs-folder/choose", {"path": "/share/nas/docs"}, ASHA))
        self.ok(self.restore(data))
        settings.invalidate()
        self.assertEqual(settings.get("docs_path"), "/share/nas/docs")
        with db.get_conn() as conn:
            self.assertEqual(db.state_get(conn, "install_id"), iid)
            paths = [r[0] for r in conn.execute("SELECT path FROM roots")]
        self.assertTrue(all(p.startswith("nas/docs/people/") for p in paths), paths)

    def test_bad_backups(self):
        self.assertEqual(self.restore(b"not a zip").status_code, 422)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("other.txt", "x")
        self.assertEqual(self.restore(buf.getvalue()).status_code, 422)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("docs.db", "garbage")
        self.assertEqual(self.restore(buf.getvalue()).status_code, 422)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("docs.db", "x")
            z.writestr("files/../../escape.txt", "x")
        self.assertEqual(self.restore(buf.getvalue()).status_code, 422)
        self.assertFalse(os.path.exists(os.path.join(SHARE, "escape.txt")))



if __name__ == "__main__":
    unittest.main()
