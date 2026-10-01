"""One file per person in /data/copies, locked with their master password (SPEC §12.10)."""
import io
import os
import pathlib
import zipfile

from base import ADMIN, NEHA, PW, ApiTestCase, sql

from app import config, copies, kdbx


class PersonalCopies(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.set_up(ADMIN)
        self.set_up(NEHA)
        self.me = self.personal()["id"]
        self.hh = self.vaults()["Household"]["id"]
        self.add_item(self.me, title="My email", password="mail-pass", totp="JBSWY3DPEHPK3PXP")
        self.add_item(self.hh, title="Wi-Fi", password="wifi-pass")

    def path(self, name):
        return os.path.join(config.DATA_DIR, "copies", name)

    def titles(self, name, password):
        d = kdbx.open_bytes(pathlib.Path(self.path(name)).read_bytes(), password)
        out = {}
        for e in d.entries.values():
            g = d.parent.get(e)
            folder = g.findtext("Name") if g is not None and g is not d.root_group else ""
            out[d.get_field(e, "Title")] = folder
        return out

    def turn_on(self):
        self.ok(self.put("/api/admin/settings", {"personal_copies": True}))
        copies.flush()

    def test_off_by_default_then_one_file_each(self):
        copies.flush()
        self.assertFalse(os.path.exists(self.path("admin.kdbx")))
        self.assertIsNone(self.ok(self.get("/api/me"))["personalCopy"])
        self.turn_on()
        mine = self.titles("admin.kdbx", PW["u-admin"])
        self.assertEqual(mine["My email"], "")                 # Personal at the top
        self.assertEqual(mine["Wi-Fi"], "Household")           # every other vault as a folder
        self.assertEqual(self.titles("neha.kdbx", PW["u-neha"]), {"Wi-Fi": "Household"})
        with self.assertRaises(kdbx.WrongKey):
            self.titles("neha.kdbx", PW["u-admin"])            # only its owner can open it
        pc = self.ok(self.get("/api/me"))["personalCopy"]
        self.assertEqual((pc["file"], pc["stale"]), ("/data/copies/admin.kdbx", False))
        # the 2FA code is in KeePass's fields too
        d = kdbx.open_bytes(pathlib.Path(self.path("admin.kdbx")).read_bytes(), PW["u-admin"])
        e = next(x for x in d.entries.values() if d.get_field(x, "Title") == "My email")
        self.assertEqual(d.get_field(e, "TimeOtp-Secret-Base32"), "JBSWY3DPEHPK3PXP")

    def test_follows_every_change(self):
        self.turn_on()
        self.add_item(self.hh, title="Netflix", password="n")
        copies.flush()
        self.assertIn("Netflix", self.titles("admin.kdbx", PW["u-admin"]))
        self.assertIn("Netflix", self.titles("neha.kdbx", PW["u-neha"]))
        # Neha is away: her copy is marked stale and written at her next unlock
        self.ok(self.post("/api/lock", {}, NEHA))
        from app import sessions
        self.add_item(self.hh, title="Bank", password="b")
        copies.flush()
        self.assertNotIn("Bank", self.titles("neha.kdbx", PW["u-neha"]))
        self.assertEqual(sql("SELECT stale FROM user_copies WHERE user_id = 'u-neha'")[0]["stale"], 1)
        self.unlock(NEHA)
        copies.flush()
        self.assertIn("Bank", self.titles("neha.kdbx", PW["u-neha"]))
        self.assertTrue(sessions)

    def test_shared_vault_removal_password_change_disable_and_off(self):
        self.turn_on()
        v = self.ok(self.post("/api/vaults", {"name": "Finance"}), 201)
        self.add_item(v["id"], title="Tax portal", password="tax")
        self.ok(self.post(f"/api/vaults/{v['id']}/members", {"userId": "u-neha", "role": "editor"}))
        copies.flush()
        self.assertEqual(self.titles("neha.kdbx", PW["u-neha"])["Tax portal"], "Finance")
        self.ok(self.delete(f"/api/vaults/{v['id']}/members/u-neha"))
        copies.flush()
        self.assertNotIn("Tax portal", self.titles("neha.kdbx", PW["u-neha"]))
        # a new master password: the copy opens with it (and not the old one)
        new = "cedar-violin-harbor-rocket-meadow"
        self.ok(self.post("/api/me/password", {"current": PW["u-admin"], "new": new}))
        copies.flush()
        self.assertIn("My email", self.titles("admin.kdbx", new))
        with self.assertRaises(kdbx.WrongKey):
            self.titles("admin.kdbx", PW["u-admin"])
        # the admin backup carries the copies, and restoring brings them back
        z = zipfile.ZipFile(io.BytesIO(self.get("/api/admin-storage-download-db").content))
        self.assertIn("copies/admin.kdbx", z.namelist())
        # disabling someone deletes their file; turning the setting off deletes all
        self.ok(self.patch("/api/users/u-neha", {"disabled": True}))
        self.assertFalse(os.path.exists(self.path("neha.kdbx")))
        self.ok(self.put("/api/admin/settings", {"personal_copies": False}))
        self.assertFalse(os.path.exists(self.path("admin.kdbx")))
        self.assertEqual(sql("SELECT * FROM user_copies"), [])

    def test_restore_keeps_copies(self):
        self.turn_on()
        backup = self.get("/api/admin-storage-download-db").content
        os.remove(self.path("admin.kdbx"))
        self.ok(self.req("POST", "/api/admin-storage-import-db", files={"file": ("b.zip", backup, "application/zip")}))
        self.assertTrue(os.path.exists(self.path("admin.kdbx")))
        self.assertEqual({r["stale"] for r in sql("SELECT stale FROM user_copies")}, {1})
