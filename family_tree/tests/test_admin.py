"""Identity & permissions (§4), "This is me", whoami, and admin backup/restore (§11.1)."""
from base import ADMIN, ALICE, BOB, ApiTestCase, addon_version, headers, image_bytes, sql
import _env

import io
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
import zipfile

from fastapi.testclient import TestClient

from app import config, db, media
from app.main import app


class Permissions(ApiTestCase):
    def test_no_user_header_401(self):
        for path in ("/api/people", "/api/whoami", "/api/me", "/api/tree", "/api/history"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 401)
        r = self.client.post("/api/people", json={"given_names": "X"})
        self.assertEqual(r.status_code, 401)

    def test_client_ip_not_allowed_403(self):
        outsider = TestClient(app, client=("10.0.0.5", 1))
        for path in ("/api/people", "/api/whoami", "/api/health"):
            with self.subTest(path=path):
                r = outsider.get(path, headers=headers(ADMIN))
                self.assertEqual(r.status_code, 403)
        r = outsider.post("/api/people", json={"given_names": "X"}, headers=headers(ADMIN))
        self.assertEqual(r.status_code, 403)
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM people")[0]["n"], 0)
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM users")[0]["n"], 0)       # no user row either
        for host in ("172.30.32.2", "::1"):
            ok = TestClient(app, client=(host, 1))
            self.assertEqual(ok.get("/api/whoami", headers=headers(ALICE)).status_code, 200, host)

    def test_security_headers(self):
        r = self.get("/api/health")
        self.assertEqual(r.headers["x-content-type-options"], "nosniff")
        self.assertIn("default-src 'self'", r.headers["content-security-policy"])
        self.assertEqual(r.headers["referrer-policy"], "same-origin")

    def test_users_created_on_first_sight_and_names_updated(self):
        self.ok(self.get("/api/me", user=ALICE))
        u = sql("SELECT * FROM users WHERE id = ?", (ALICE["id"],))[0]
        self.assertEqual((u["name"], u["username"], u["disabled"]), ("Alice A", "alice", 0))
        self.ok(self.get("/api/me", user=dict(ALICE, display="Alice Renamed")))
        self.assertEqual(sql("SELECT name FROM users WHERE id = ?", (ALICE["id"],))[0]["name"], "Alice Renamed")
        # no display name → login name
        self.ok(self.get("/api/me", user={"id": "u-x", "name": "xlogin", "display": None}))
        self.assertEqual(sql("SELECT name FROM users WHERE id = 'u-x'")[0]["name"], "xlogin")

    def test_disabled_user(self):
        self.ok(self.get("/api/me", user=ALICE))
        self.ok(self.patch(f"/api/users/{ALICE['id']}", {"disabled": True}, user=ADMIN))
        for method, path in (("GET", "/api/people"), ("GET", "/api/me"), ("GET", "/api/tree"),
                             ("GET", "/api/history"), ("GET", "/api/trash"), ("GET", "/api/users")):
            with self.subTest(path=path):
                self.assertEqual(self.req(method, path, ALICE).status_code, 403)
        r = self.post("/api/people", {"given_names": "Sneaky"}, user=ALICE)
        self.assertEqual(r.status_code, 403)
        w = self.ok(self.get("/api/whoami", user=ALICE))
        self.assertTrue(w["disabled"])
        self.assertEqual(w["haUserId"], ALICE["id"])
        self.assertNotIn(ALICE["id"], [u["id"] for u in self.ok(self.get("/api/users", user=BOB))])
        admin_view = {u["id"]: u for u in self.ok(self.get("/api/admin/users", user=ADMIN))}
        self.assertTrue(admin_view[ALICE["id"]]["disabled"])
        self.ok(self.patch(f"/api/users/{ALICE['id']}", {"disabled": False}, user=ADMIN))
        self.ok(self.get("/api/people", user=ALICE))

    def test_admin_only_routes(self):
        self.ok(self.get("/api/me", user=BOB))
        zbuf = io.BytesIO()
        with zipfile.ZipFile(zbuf, "w") as z:
            z.writestr("family.db", b"x")
        routes = [
            ("GET", "/api/admin/users", {}),
            ("GET", "/api/admin/storage", {}),
            ("GET", "/api/admin-storage-download-db", {}),
            ("GET", "/api/admin-storage-download-db?media=1", {}),
            ("POST", "/api/admin-storage-import-db", {"files": {"file": ("b.zip", zbuf.getvalue(), "application/zip")}}),
            ("DELETE", "/api/trash", {}),
            # the trash moved into Admin — list and restore are admin-only too
            ("GET", "/api/trash", {}),
            ("POST", "/api/trash/people/x/restore", {}),
            ("POST", "/api/people/x/restore", {}),
            ("POST", "/api/families/x/restore", {}),
            ("GET", "/api/admin/settings", {}),
            ("PUT", "/api/admin/settings", {"json": {"trash_days": 30}}),
            ("PATCH", f"/api/users/{BOB['id']}", {"json": {"disabled": True}}),
            ("POST", "/api/admin/media/check", {}),
            ("POST", "/api/admin/media/adopt", {}),
            ("POST", "/api/admin/media/orphans", {"json": {"ids": []}}),
        ]
        for method, path, kw in routes:
            with self.subTest(path=path, method=method):
                r = self.req(method, path, ALICE, **kw)
                self.assertEqual(r.status_code, 403, r.text)
        self.assertEqual(sql("SELECT disabled FROM users WHERE id = ?", (BOB["id"],))[0]["disabled"], 0)
        self.ok(self.get("/api/admin/users", user=ADMIN))

    def test_admin_cannot_disable_self(self):
        self.ok(self.get("/api/me", user=ADMIN))
        r = self.patch(f"/api/users/{ADMIN['id']}", {"disabled": True}, user=ADMIN)
        self.assertEqual(r.status_code, 409)
        self.ok(self.get("/api/me", user=ADMIN))
        self.assertEqual(self.patch("/api/users/nobody", {"disabled": True}, user=ADMIN).status_code, 404)
        self.assertEqual(self.patch(f"/api/users/{ADMIN['id']}", {"disabled": True, "x": 1}, user=ADMIN).status_code, 422)

    def test_admin_matching(self):
        # by login name (case-insensitive)
        w = self.ok(self.get("/api/whoami", user={"id": "u-9", "name": "ADMIN", "display": "Boss"}))
        self.assertTrue(w["isAdmin"])
        # by user id
        config.ADMIN_NAMES = config.ADMIN_NAMES | {"u-special"}
        w = self.ok(self.get("/api/whoami", user={"id": "u-special", "name": None, "display": "X"}))
        self.assertTrue(w["isAdmin"])
        self.assertFalse(w["nameSent"])
        self.assertEqual(w["adminEntries"], 2)
        # display name "admin" never grants admin
        w = self.ok(self.get("/api/whoami", user={"id": "u-10", "name": "carol", "display": "admin"}))
        self.assertFalse(w["isAdmin"])
        self.assertTrue(w["displayNameOnly"])
        self.assertEqual(self.get("/api/admin/users", user={"id": "u-10", "name": "carol", "display": "admin"}).status_code, 403)

    def test_display_name_only(self):
        config.ADMIN_NAMES = config.ADMIN_NAMES | {"grandma"}
        user = {"id": "u-gm", "name": "gmlogin", "display": "Grandma"}
        w = self.ok(self.get("/api/whoami", user=user))
        self.assertEqual((w["isAdmin"], w["displayNameOnly"]), (False, True))
        self.assertEqual((w["haUsername"], w["haDisplayName"], w["nameSent"]), ("gmlogin", "Grandma", True))
        w = self.ok(self.get("/api/whoami", user=ALICE))
        self.assertEqual((w["isAdmin"], w["displayNameOnly"]), (False, False))
        w = self.ok(self.get("/api/whoami", user=ADMIN))
        self.assertEqual((w["isAdmin"], w["displayNameOnly"]), (True, False))


class ThisIsMe(ApiTestCase):
    def test_claim_conflict_unclaim(self):
        a = self.person("Ann", "Smith")
        b = self.person("Ben")
        r = self.set_me(a, user=ALICE)
        self.assertEqual(r, {"mePersonId": a, "mePersonName": "Ann Smith"})
        me = self.ok(self.get("/api/me", user=ALICE))
        self.assertEqual((me["mePersonId"], me["mePersonName"]), (a, "Ann Smith"))
        self.assertEqual(self.detail(a)["claimedBy"], {"id": ALICE["id"], "name": ALICE["display"]})
        r = self.put("/api/me/person", {"personId": a}, user=BOB)
        self.assertEqual(r.status_code, 409)
        self.assertIn(ALICE["display"], r.json()["detail"])
        self.set_me(a, user=ALICE)                        # re-claiming your own is fine
        self.set_me(b, user=ALICE)                        # switching frees the old one
        self.set_me(a, user=BOB)
        self.assertEqual(self.ok(self.put("/api/me/person", {"personId": None}, user=BOB)),
                         {"mePersonId": None, "mePersonName": None})
        self.assertIsNone(self.ok(self.get("/api/me", user=BOB))["mePersonId"])
        self.assertIsNone(self.detail(a)["claimedBy"])

    def test_claim_deleted_or_unknown_404(self):
        a = self.person("Ann")
        self.ok(self.delete(f"/api/people/{a}"))
        self.assertEqual(self.put("/api/me/person", {"personId": a}).status_code, 404)
        self.assertEqual(self.put("/api/me/person", {"personId": "nope"}).status_code, 404)

    def test_admin_sets_someone_elses_me(self):
        a = self.person("Ann")
        self.ok(self.get("/api/me", user=BOB))
        self.ok(self.patch(f"/api/users/{BOB['id']}", {"mePersonId": a}, user=ADMIN))
        self.assertEqual(self.ok(self.get("/api/me", user=BOB))["mePersonId"], a)
        self.ok(self.get("/api/me", user=ALICE))
        self.assertEqual(self.patch(f"/api/users/{ALICE['id']}", {"mePersonId": a}, user=ADMIN).status_code, 409)
        users = {u["id"]: u for u in self.ok(self.get("/api/users"))}
        self.assertEqual(users[BOB["id"]]["mePersonName"], "Ann")

    def test_me_deleted_person_is_ignored(self):
        a = self.person("Ann")
        self.person("Ben")
        self.set_me(a)
        self.ok(self.delete(f"/api/people/{a}"))
        self.assertIsNone(self.ok(self.get("/api/me"))["mePersonName"])
        t = self.ok(self.get("/api/tree"))
        self.assertIsNone(t["meId"])
        self.assertNotEqual(t["focus"], a)


class BackupRestore(ApiTestCase):
    def download(self, with_media=False, user=ADMIN):
        r = self.get("/api/admin-storage-download-db" + ("?media=1" if with_media else ""), user=user)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.headers["content-type"], "application/zip")
        self.assertIn("family-tree-backup-", r.headers["content-disposition"])
        return r.content

    def restore(self, data, user=ADMIN):
        return self.post("/api/admin-storage-import-db", files={"file": ("backup.zip", data, "application/zip")}, user=user)

    def rezip(self, data, drop=(), add=None, replace=None):
        src = zipfile.ZipFile(io.BytesIO(data))
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as z:
            for info in src.infolist():
                if info.filename in drop:
                    continue
                body = src.read(info.filename)
                if replace and info.filename in replace:
                    body = replace[info.filename]
                z.writestr(info.filename, body)
            for name, body in (add or {}).items():
                z.writestr(name, body)
        return out.getvalue()

    def names(self):
        return sorted(p["name"] for p in self.ok(self.get("/api/people"))["items"])

    def test_db_only_download(self):
        self.person("Ann")
        self.upload_photo(self.person("Ben"))
        data = self.download()
        z = zipfile.ZipFile(io.BytesIO(data))
        self.assertEqual(sorted(z.namelist()), ["backup.json", "family.db"])
        meta = json.loads(z.read("backup.json"))
        self.assertEqual((meta["app"], meta["version"], meta["withMedia"]), ("family_tree", addon_version(), False))
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            f.write(z.read("family.db"))
        try:
            c = sqlite3.connect(f.name)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM people").fetchone()[0], 2)
            c.close()
        finally:
            os.remove(f.name)
        # temp files are cleaned up
        self.assertEqual([n for n in os.listdir(config.DATA_DIR) if n.endswith((".zip", ".tmp")) or
                          (n.endswith(".db") and n != "family.db")], [])

    def upload_photo(self, pid):
        r = self.post(f"/api/people/{pid}/photo", files={"file": ("p.jpg", image_bytes(), "image/jpeg")})
        return self.ok(r, 201)["mediaId"]

    def test_download_with_media(self):
        mid = self.upload_photo(self.person("Ann"))
        z = zipfile.ZipFile(io.BytesIO(self.download(with_media=True)))
        base = f"media/{mid[:2]}/{mid}/"
        self.assertEqual(sorted(z.namelist()), sorted(["backup.json", "family.db", base + "original",
                                                       base + "thumb1024.jpg", base + "thumb256.jpg"]))
        self.assertTrue(json.loads(z.read("backup.json"))["withMedia"])
        with open(media.file_path(mid), "rb") as f:
            self.assertEqual(z.read(base + "original"), f.read())
        # the scratch zip on the share is removed after sending
        exports = os.path.join(_env.MEDIA_PATH, ".exports")
        self.assertEqual(os.listdir(exports) if os.path.isdir(exports) else [], [])

    def test_restore_round_trip(self):
        a = self.person("Ann", "Smith")
        data = self.download()
        self.ok(self.patch(f"/api/people/{a}", {"surname": "Jones"}))
        self.person("Zed")
        self.assertEqual(self.names(), ["Ann Jones", "Zed"])
        r = self.ok(self.restore(data))
        self.assertEqual(r, {"ok": True, "mediaRestored": 0, "mediaMissing": 0})
        self.assertEqual(self.names(), ["Ann Smith"])
        h = self.ok(self.get("/api/history"))
        self.assertEqual(h["items"][0]["label"], "Restored from backup")
        self.assertEqual(h["items"][0]["userId"], ADMIN["id"])
        self.assertEqual(h["total"], 2)
        # the tree keeps working after the restore
        self.ok(self.patch(f"/api/people/{a}", {"nickname": "Annie"}))
        self.assertTrue(media.is_online())

    def test_restore_with_media_round_trip(self):
        pid = self.person("Ann")
        mid = self.upload_photo(pid)
        data = self.download(with_media=True)
        shutil.rmtree(os.path.join(_env.MEDIA_PATH, "media"))
        r = self.ok(self.restore(data))
        self.assertEqual(r, {"ok": True, "mediaRestored": 3, "mediaMissing": 0})
        self.assertEqual(self.get(f"/api/media/{mid}/file?size=256").status_code, 200)
        self.assertEqual(self.detail(pid)["photo"], mid)

    def test_db_only_restore_reports_missing_media(self):
        pid = self.person("Ann")
        self.upload_photo(pid)
        data = self.download()
        shutil.rmtree(os.path.join(_env.MEDIA_PATH, "media"))
        r = self.ok(self.restore(data))
        self.assertEqual(r["mediaMissing"], 1)
        self.assertEqual(r["mediaRestored"], 0)

    def test_restore_keeps_this_installs_media_store(self):
        self.person("Ann")
        data = self.download()
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            f.write(zipfile.ZipFile(io.BytesIO(data)).read("family.db"))
        c = sqlite3.connect(f.name)
        c.execute("UPDATE settings SET value = 'other-install' WHERE key = 'media_store_id'")
        c.commit()
        c.close()
        with open(f.name, "rb") as fh:
            other_db = fh.read()
        os.remove(f.name)
        self.ok(self.restore(self.rezip(data, replace={"family.db": other_db})))
        self.assertTrue(media.is_online())
        with db.get_conn() as conn:
            self.assertNotEqual(db.get_setting(conn, "media_store_id"), "other-install")

    def assert_rejected(self, data, fragment=None, status=400):
        before = self.names()
        r = self.restore(data)
        self.assertEqual(r.status_code, status, r.text)
        if fragment:
            self.assertIn(fragment, r.json()["detail"])
        self.assertEqual(self.names(), before)
        return r

    def test_rejected_zips(self):
        self.person("Ann")
        good = self.download()
        self.assert_rejected(b"this is not a zip", "isn't a Family Tree backup")
        self.assert_rejected(self.rezip(good, drop=("family.db",)), "no family.db")
        self.assert_rejected(self.rezip(good, add={"../evil": b"x"}), "unsafe path")
        self.assert_rejected(self.rezip(good, add={"/etc/evil": b"x"}), "unsafe path")
        self.assert_rejected(self.rezip(good, add={"media\\ab\\x": b"x"}), "unsafe path")
        self.assert_rejected(self.rezip(good, add={"notes.txt": b"x"}), "Unexpected file")
        self.assert_rejected(self.rezip(good, add={"media/ab/cd" + "0" * 30 + "/original": b"x"}), "Unexpected file")
        self.assert_rejected(self.rezip(good, replace={"family.db": b"not a database at all" * 100}), "valid SQLite")
        empty = tempfile.mktemp(suffix=".db", dir=_env.TMP)
        c = sqlite3.connect(empty)
        c.execute("CREATE TABLE people (id TEXT)")
        c.commit()
        c.close()
        with open(empty, "rb") as f:
            self.assert_rejected(self.rezip(good, replace={"family.db": f.read()}), "missing tables")
        os.remove(empty)
        self.assertEqual(self.ok(self.get("/api/history"))["items"][0]["label"], "Added Ann")

    def test_rejected_media_type_mismatch(self):
        pid = self.person("Ann")
        mid = self.upload_photo(pid)
        good = self.download(with_media=True)
        name = f"media/{mid[:2]}/{mid}/original"
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
        shutil.rmtree(os.path.join(_env.MEDIA_PATH, "media"))
        self.assert_rejected(self.rezip(good, replace={name: svg}), "isn't the type")
        self.assertFalse(os.path.exists(os.path.join(_env.MEDIA_PATH, "media")))     # nothing written
        # a thumbnail must be a JPEG even when the original is a PNG
        self.assert_rejected(self.rezip(good, replace={f"media/{mid[:2]}/{mid}/thumb256.jpg": image_bytes("PNG")}),
                             "isn't the type")
        # a file for a photo the database doesn't know
        stranger = "ab" + "1" * 30
        self.assert_rejected(self.rezip(good, add={f"media/ab/{stranger}/original": image_bytes()}), "doesn't know")

    def test_restore_non_admin_403(self):
        self.person("Ann")
        data = self.download()
        self.assertEqual(self.restore(data, user=ALICE).status_code, 403)


    def test_restore_rejects_database_with_path_like_media_id(self):
        self.person("Ann")
        good = self.download()
        z = zipfile.ZipFile(io.BytesIO(good))
        tmp = tempfile.mktemp(suffix=".db", dir=_env.TMP)
        with open(tmp, "wb") as f:
            f.write(z.read("family.db"))
        c = sqlite3.connect(tmp)
        c.execute("INSERT INTO media (id, kind, content_type, size, sha256, created_at, deleted_at) "
                  "VALUES ('../..', 'photo', 'image/jpeg', 1, 'x', '2020-01-01T00:00:00Z', '2020-01-01T00:00:00Z')")
        c.commit()
        c.close()
        with open(tmp, "rb") as f:
            self.assert_rejected(self.rezip(good, replace={"family.db": f.read()}), "invalid photo id")
        os.remove(tmp)


if __name__ == "__main__":
    unittest.main()
