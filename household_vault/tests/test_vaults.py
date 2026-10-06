"""Household, shared vaults with the same password or a random one, re-keying, versions, downloads, import (SPEC §5)."""
from base import ADMIN, NEHA, PW, VIKRAM, ApiTestCase, sql
import _env  # noqa: F401

import io
import unittest

from app import db, kdbx, sessions

JOINT_PW = "saffron-lantern-meadow-violin-comet"


class Household(ApiTestCase):
    def test_everyone_active_gets_household(self):
        self.set_up(ADMIN)
        self.set_up(NEHA)
        h = self.vaults()["Household"]
        self.assertEqual({m["id"] for m in h["members"]}, {"u-admin", "u-neha"})
        item = self.add_item(h["id"], title="Wi-Fi", password="home-wifi-pass")
        s = self.ok(self.get(f"/api/vaults/{h['id']}/items/{item['id']}/secret?field=Password", NEHA))
        self.assertEqual(s["value"], "home-wifi-pass")
        # household can't be shared/renamed/deleted/left
        self.assertEqual(self.post(f"/api/vaults/{h['id']}/leave", user=NEHA).status_code, 409)
        self.assertEqual(self.post(f"/api/vaults/{h['id']}/delete", {"confirmName": "Household"}).status_code, 403)

    def test_joining_when_nobody_is_unlocked(self):
        self.set_up(ADMIN)
        self.ok(self.post("/api/lock"))
        self.set_up(NEHA)                 # Household closed: she waits
        self.assertNotIn("Household", self.vaults(NEHA))
        self.unlock(ADMIN)                 # someone who has it unlocks → she's added and it opens for her
        self.assertTrue(self.vaults(NEHA)["Household"]["open"])

    def test_disabling_rekeys(self):
        self.set_up(ADMIN)
        self.set_up(NEHA)
        h = self.vaults()["Household"]
        before = sql("SELECT key_epoch FROM vaults WHERE id = ?", (h["id"],))[0]["key_epoch"]
        self.add_item(h["id"], title="Alarm", password="1234")
        self.ok(self.patch("/api/users/u-neha", {"disabled": True}))
        v = sql("SELECT key_epoch, rekey_pending FROM vaults WHERE id = ?", (h["id"],))[0]
        self.assertEqual((v["key_epoch"], v["rekey_pending"]), (before + 1, 0))
        self.assertEqual(len(sql("SELECT * FROM vault_versions WHERE vault_id = ?", (h["id"],))), 1)   # older purged
        self.assertEqual(sql("SELECT user_id FROM vault_keys WHERE vault_id = ?", (h["id"],)), [{"user_id": "u-admin"}])
        # the current file opens with the new password
        pw = self.ok(self.get(f"/api/vaults/{h['id']}/password"))["password"]
        self.assertEqual(kdbx.open_bytes(self.get(f"/api/vaults/{h['id']}/download").content, pw).name, "Household")

    def test_rekey_later_when_closed(self):
        self.set_up(ADMIN)
        self.set_up(NEHA)
        self.ok(self.post("/api/lock"))
        self.ok(self.post("/api/lock", user=NEHA))
        self.ok(self.patch("/api/users/u-neha", {"disabled": True}))
        hid = sql("SELECT id FROM vaults WHERE kind = 'household'")[0]["id"]
        self.assertEqual(sql("SELECT rekey_pending FROM vaults WHERE id = ?", (hid,))[0]["rekey_pending"], 1)
        self.unlock(ADMIN)
        self.assertEqual(sql("SELECT rekey_pending, key_epoch FROM vaults WHERE id = ?", (hid,))[0],
                         {"rekey_pending": 0, "key_epoch": 2})


class SamePassword(ApiTestCase):
    """"Two of us use the same vault": a shared vault with a password we both know."""

    def setUp(self):
        super().setUp()
        self.set_up(ADMIN)
        self.set_up(NEHA)
        self.set_up(VIKRAM)
        v = self.ok(self.post("/api/vaults", {"name": "Kiran & Neha", "passwordMode": "chosen", "password": JOINT_PW}), 201)
        self.vid = v["id"]
        self.item = self.add_item(self.vid, title="Joint account", username="sp", password="bank-pass")

    def test_member_needs_the_password(self):
        self.assertEqual(self.post("/api/vaults", {"name": "Weak", "passwordMode": "chosen", "password": "abc"}).status_code, 422)
        self.ok(self.post(f"/api/vaults/{self.vid}/members", {"userId": "u-neha", "role": "editor"}))
        v = self.vaults(NEHA)["Kiran & Neha"]
        self.assertFalse(v["open"])
        self.assertEqual(self.get(f"/api/vaults/{self.vid}/items", NEHA).status_code, 423)
        self.assertEqual(self.post(f"/api/vaults/{self.vid}/open", {"password": "wrong"}, NEHA).status_code, 403)
        self.ok(self.post(f"/api/vaults/{self.vid}/open", {"password": JOINT_PW, "remember": True}, NEHA))
        self.assertEqual(self.ok(self.get(f"/api/vaults/{self.vid}/items/{self.item['id']}/secret?field=Password",
                                          NEHA))["value"], "bank-pass")
        # remembered: next time it opens with her own master password
        self.ok(self.post("/api/lock", user=NEHA))
        self.assertTrue(self.unlock(NEHA) and self.vaults(NEHA)["Kiran & Neha"]["open"])
        # not a member → 404 even with the password
        self.assertEqual(self.post(f"/api/vaults/{self.vid}/open", {"password": JOINT_PW}, VIKRAM).status_code, 404)

    def test_change_password_and_remove(self):
        self.ok(self.post(f"/api/vaults/{self.vid}/members", {"userId": "u-neha", "role": "editor"}))
        self.ok(self.post(f"/api/vaults/{self.vid}/open", {"password": JOINT_PW, "remember": True}, NEHA))
        new = "orbit-velvet-harbor-maple-glacier"
        self.assertEqual(self.post(f"/api/vaults/{self.vid}/password", {"new": new}, NEHA).status_code, 403)
        self.ok(self.post(f"/api/vaults/{self.vid}/password", {"new": new}))
        self.assertFalse(self.vaults(NEHA)["Kiran & Neha"]["open"])       # she has to type the new one
        self.assertFalse(self.vaults(NEHA)["Kiran & Neha"]["remembered"])
        self.assertEqual(self.post(f"/api/vaults/{self.vid}/open", {"password": JOINT_PW}, NEHA).status_code, 403)
        self.ok(self.post(f"/api/vaults/{self.vid}/open", {"password": new}, NEHA))
        self.assertEqual(len(sql("SELECT * FROM vault_versions WHERE vault_id = ?", (self.vid,))), 1)
        # removing her flags a password change (she knows it)
        self.ok(self.delete(f"/api/vaults/{self.vid}/members/u-neha"))
        self.assertTrue(self.vaults()["Kiran & Neha"]["passwordChangeSuggested"])
        self.assertNotIn("Kiran & Neha", self.vaults(NEHA))

    def test_roles(self):
        self.ok(self.post(f"/api/vaults/{self.vid}/members", {"userId": "u-vikram", "role": "viewer"}))
        self.ok(self.post(f"/api/vaults/{self.vid}/open", {"password": JOINT_PW}, VIKRAM))
        self.ok(self.get(f"/api/vaults/{self.vid}/items", VIKRAM))
        self.assertEqual(self.post(f"/api/vaults/{self.vid}/items", {"title": "x"}, VIKRAM).status_code, 403)
        self.assertEqual(self.post(f"/api/vaults/{self.vid}/members", {"userId": "u-neha"}, VIKRAM).status_code, 403)
        self.ok(self.post(f"/api/vaults/{self.vid}/members", {"userId": "u-vikram", "role": "manager"}))
        self.ok(self.post(f"/api/vaults/{self.vid}/members", {"userId": "u-neha", "role": "editor"}, VIKRAM))
        self.assertEqual(self.delete(f"/api/vaults/{self.vid}/members/u-admin", VIKRAM).status_code, 409)  # owner
        self.ok(self.post(f"/api/vaults/{self.vid}/transfer", {"userId": "u-vikram"}))
        self.assertEqual(self.vaults(VIKRAM)["Kiran & Neha"]["role"], "owner")
        self.ok(self.post(f"/api/vaults/{self.vid}/leave"))
        self.assertNotIn("Kiran & Neha", self.vaults())

    def test_personal_cannot_be_shared(self):
        me = self.personal()
        r = self.post(f"/api/vaults/{me['id']}/members", {"userId": "u-neha", "role": "editor"})
        self.assertEqual(r.status_code, 409)
        self.assertIn("create a shared vault", r.json()["detail"])
        self.assertEqual(self.post(f"/api/vaults/{me['id']}/members", {"userId": "u-neha", "role": "manager"}).status_code, 422)
        self.assertNotIn("Kiran's Personal", self.vaults(NEHA))

    def test_personal_shared_before_the_change_still_works(self):
        from app import service
        me = self.personal()
        self.add_item(me["id"], title="My email", password="mail-pass")
        with db.get_conn() as conn:                 # a membership from before Personal sharing was refused
            service.add_member(conn, me["id"], "u-neha", "editor", "u-admin")
        self.ok(self.post(f"/api/vaults/{me['id']}/members", {"userId": "u-neha", "role": "viewer"}))   # role changes still work
        self.ok(self.post(f"/api/vaults/{me['id']}/members", {"userId": "u-neha", "role": "editor"}))
        v = self.vaults(NEHA)["Kiran's Personal"]
        self.assertFalse(v["open"])
        self.ok(self.post(f"/api/vaults/{me['id']}/open", {"password": PW["u-admin"], "remember": True}, NEHA))
        items = self.ok(self.get(f"/api/vaults/{me['id']}/items?recursive=true", NEHA))["items"]
        self.assertEqual([i["title"] for i in items], ["My email"])
        # she can't open my other vaults
        self.assertEqual(self.get(f"/api/vaults/{self.vid}/items", NEHA).status_code, 404)
        # when I change my master password she has to ask again
        new = "cedar-violin-harbor-rocket-meadow"
        self.ok(self.post("/api/me/password", {"current": PW["u-admin"], "new": new}))
        self.assertFalse(self.vaults(NEHA)["Kiran's Personal"]["open"])
        self.assertFalse(self.vaults(NEHA)["Kiran's Personal"]["remembered"])

    def test_delete_vault(self):
        self.assertEqual(self.post(f"/api/vaults/{self.vid}/delete", {"confirmName": "nope"}).status_code, 422)
        self.ok(self.post(f"/api/vaults/{self.vid}/delete", {"confirmName": "Kiran & Neha"}))
        self.assertNotIn("Kiran & Neha", self.vaults())
        self.assertEqual(sql("SELECT * FROM vault_versions WHERE vault_id = ?", (self.vid,)), [])


class RandomPassword(ApiTestCase):
    def test_opens_automatically_and_rekeys_on_removal(self):
        self.set_up(ADMIN)
        self.set_up(NEHA)
        v = self.ok(self.post("/api/vaults", {"name": "Finance"}), 201)
        self.add_item(v["id"], title="Tax portal", password="tax")
        self.ok(self.post(f"/api/vaults/{v['id']}/members", {"userId": "u-neha", "role": "editor"}))
        self.assertTrue(self.vaults(NEHA)["Finance"]["open"])                 # no typing
        pw1 = self.ok(self.get(f"/api/vaults/{v['id']}/password", NEHA))["password"]
        self.assertEqual(kdbx.open_bytes(self.ok_download(v["id"]), pw1).name, "Finance")
        self.ok(self.delete(f"/api/vaults/{v['id']}/members/u-neha"))
        pw2 = self.ok(self.get(f"/api/vaults/{v['id']}/password"))["password"]
        self.assertNotEqual(pw1, pw2)
        with self.assertRaises(kdbx.WrongKey):
            kdbx.open_bytes(self.ok_download(v["id"]), pw1)
        # manual re-key keeps working for the owner
        self.ok(self.post(f"/api/vaults/{v['id']}/rekey"))
        self.ok(self.post("/api/lock"))
        self.unlock(ADMIN)
        self.assertTrue(self.vaults()["Finance"]["open"])

    def ok_download(self, vid, user=ADMIN):
        r = self.get(f"/api/vaults/{vid}/download", user)
        self.assertEqual(r.status_code, 200)
        return r.content


class Files(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.set_up(ADMIN)
        self.set_up(NEHA)
        self.me = self.personal()

    def test_versions_and_restore(self):
        a = self.add_item(self.me["id"], title="One")
        self.add_item(self.me["id"], title="Two")
        vs = self.ok(self.get(f"/api/vaults/{self.me['id']}/versions"))
        self.assertEqual(vs["versions"][0]["version"], vs["current"])
        older = vs["versions"][1]["version"]
        self.ok(self.post(f"/api/vaults/{self.me['id']}/versions/{older}/restore"))
        titles = [i["title"] for i in self.ok(self.get(f"/api/vaults/{self.me['id']}/items"))["items"]]
        self.assertEqual(titles, ["One"])
        self.assertIsNotNone(a)
        self.assertEqual(self.get(f"/api/vaults/{self.me['id']}/versions", NEHA).status_code, 404)

    def test_download_all(self):
        self.add_item(self.me["id"], title="Mine", password="p1")
        h = self.vaults()["Household"]
        f = self.ok(self.post(f"/api/vaults/{h['id']}/folders", {"name": "Utilities"}), 201)
        self.add_item(h["id"], title="Power", password="p2", folderId=f["id"])
        self.assertEqual(self.post("/api/me/download-all", {"password": "wrong"}).status_code, 403)
        r = self.post("/api/me/download-all", {"password": PW["u-admin"]})
        self.assertEqual(r.status_code, 200)
        d = kdbx.open_bytes(r.content, PW["u-admin"])
        paths = {d.get_field(e, "Title"): d.group_path(d.parent[e]) for e in d.entries.values()}
        self.assertEqual(paths, {"Mine": [], "Power": ["Household", "Utilities"]})
        self.assertIn("downloaded_all", [x["action"] for x in sql("SELECT action FROM audit_log")])

    def test_import(self):
        src = kdbx.Database.create("KeePassXC db", {"memory": 64 * 1024, "iterations": 2, "parallelism": 1})
        g = src.add_group(src.root_group, "Email")
        src.add_entry(g, {"Title": "Webmail", "UserName": "me", "Password": "gpw"})
        raw = kdbx.save_bytes(src, "imp")
        files = {"file": ("old.kdbx", io.BytesIO(raw), "application/octet-stream")}
        self.assertEqual(self.req("POST", "/api/import", files=files, data={"password": "bad"}).status_code, 403)
        files = {"file": ("old.kdbx", io.BytesIO(raw), "application/octet-stream")}
        r = self.ok(self.req("POST", "/api/import", files=files, data={"password": "imp"}), 201)
        v = self.vaults()["KeePassXC db"]
        self.assertTrue(v["open"])
        items = self.ok(self.get(f"/api/vaults/{v['id']}/items?recursive=true"))["items"]
        self.assertEqual([(i["title"], i["folderPath"]) for i in items], [("Webmail", ["Email"])])
        # the same file again: everything's a duplicate, so nothing is added (and no empty folder is left)
        files = {"file": ("old.kdbx", io.BytesIO(raw), "application/octet-stream")}
        again = self.ok(self.req("POST", "/api/import", files=files, data={"password": "imp", "target": self.me["id"],
                                                                           "name": "From laptop"}), 201)
        self.assertEqual((again["items"], again["skipped"]), (0, 1))
        self.assertEqual(self.ok(self.get(f"/api/vaults/{self.me['id']}/folders"))["folders"], [])
        # into a folder of Personal, duplicates allowed
        files = {"file": ("old.kdbx", io.BytesIO(raw), "application/octet-stream")}
        self.ok(self.req("POST", "/api/import", files=files, data={"password": "imp", "target": self.me["id"],
                                                                   "name": "From laptop", "skipDuplicates": "false"}), 201)
        items = self.ok(self.get(f"/api/vaults/{self.me['id']}/items?recursive=true"))["items"]
        self.assertEqual([(i["title"], i["folderPath"]) for i in items], [("Webmail", ["From laptop", "Email"])])
        self.assertEqual(r["items"], 1)
        bad = {"file": ("x.kdbx", io.BytesIO(b"nope"), "application/octet-stream")}
        self.assertEqual(self.req("POST", "/api/import", files=bad, data={"password": "x"}).status_code, 422)

    def test_import_csv_exports(self):
        chrome = "name,url,username,password,note\nWebmail,https://mail.google.com,me@example.com,gpw,\nBank,https://bank.in,kiran,bpw,branch 12\n"
        r = self.ok(self.req("POST", "/api/import", files={"file": ("Chrome Passwords.csv", io.BytesIO(chrome.encode()), "text/csv")},
                             data={"target": self.me["id"], "name": "Chrome"}), 201)
        self.assertEqual((r["items"], r["skipped"], r["format"]), (2, 0, "csv"))
        items = {i["title"]: i for i in self.ok(self.get(f"/api/vaults/{self.me['id']}/items?recursive=true"))["items"]}
        self.assertEqual(items["Webmail"]["folderPath"], ["Chrome"])
        self.assertEqual(self.ok(self.get(f"/api/vaults/{self.me['id']}/items/{items['Bank']['id']}"))["notes"], "branch 12")
        # Bitwarden's export, overlapping: Webmail is a duplicate; folders and TOTP come across
        bw = ("folder,favorite,type,name,notes,fields,reprompt,login_uri,login_username,login_password,login_totp\n"
              "Email,,login,Webmail,,,0,https://mail.google.com,me@example.com,gpw,\n"
              "Work,1,login,Jira,,,0,https://jira.example.com,kiran,jpw,JBSWY3DPEHPK3PXP\n"
              ",,note,Just a note,text,,0,,,,\n")
        r = self.ok(self.req("POST", "/api/import", files={"file": ("bitwarden.csv", io.BytesIO(bw.encode()), "text/csv")},
                             data={"target": "new", "name": "From Bitwarden"}), 201)
        self.assertEqual((r["items"], r["skipped"]), (1, 1))
        v = self.vaults()["From Bitwarden"]
        got = self.ok(self.get(f"/api/vaults/{v['id']}/items?recursive=true"))["items"]
        self.assertEqual([(i["title"], i["folderPath"], i["favourite"], i["hasTotp"]) for i in got], [("Jira", ["Work"], True, True)])
        bad = "a,b,c\n1,2,3\n"
        self.assertEqual(self.req("POST", "/api/import", files={"file": ("x.csv", io.BytesIO(bad.encode()), "text/csv")},
                                  data={"target": "new"}).status_code, 422)

    def test_backup_and_restore(self):
        self.add_item(self.me["id"], title="Before")
        r = self.get("/api/admin-storage-download-db")
        self.assertEqual(r.status_code, 200)
        backup = r.content
        self.assertNotIn(b"Before", backup)                 # everything in it is encrypted
        self.ok(self.patch("/api/users/u-neha", {"disabled": True}))
        r = self.ok(self.req("POST", "/api/admin-storage-import-db",
                             files={"file": ("b.zip", io.BytesIO(backup), "application/zip")}))
        self.assertTrue(any(u["action"] == "user_disabled" for u in r["undone"]))
        self.assertEqual(sessions.stats()["sessions"], 0)
        self.unlock(ADMIN)
        self.assertEqual([i["title"] for i in self.ok(self.get(f"/api/vaults/{self.me['id']}/items"))["items"]], ["Before"])
        bad = self.req("POST", "/api/admin-storage-import-db", files={"file": ("b.zip", io.BytesIO(b"PK junk"), "application/zip")})
        self.assertEqual(bad.status_code, 422)


if __name__ == "__main__":
    unittest.main()
