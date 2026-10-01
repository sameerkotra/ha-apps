"""Folders, items, secrets, TOTP, search and the generator (SPEC §9.3, §9.4)."""
from base import ADMIN, NEHA, PW, ApiTestCase
import _env  # noqa: F401

import json
import unittest

from app import passwords, totp

SECRETS = ["bank-pass-123", "4111111111111111", "987", "4321", "JBSWY3DPEHPK3PXP", "very-secret-value"]


class Folders(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.set_up(ADMIN)
        self.vid = self.personal()["id"]

    def folders(self):
        return {f["name"]: f for f in self.ok(self.get(f"/api/vaults/{self.vid}/folders"))["folders"]}

    def test_create_nest_rename_move_delete_restore(self):
        bank = self.ok(self.post(f"/api/vaults/{self.vid}/folders", {"name": "Banking"}), 201)["id"]
        india = self.ok(self.post(f"/api/vaults/{self.vid}/folders", {"name": "India", "parentId": bank}), 201)["id"]
        f = self.folders()
        self.assertEqual((f["India"]["parentId"], f["India"]["depth"]), (bank, 1))
        self.assertEqual(self.post(f"/api/vaults/{self.vid}/folders", {"name": "  "}).status_code, 422)
        item = self.add_item(self.vid, title="SBI", folderId=india)
        self.assertEqual(item["folderPath"], ["Banking", "India"])
        self.ok(self.patch(f"/api/vaults/{self.vid}/folders/{india}", {"name": "India banks"}))
        self.assertEqual(self.patch(f"/api/vaults/{self.vid}/folders/{bank}", {"parentId": india}).status_code, 422)
        self.ok(self.patch(f"/api/vaults/{self.vid}/folders/{india}", {"toRoot": True}))
        self.assertEqual(self.folders()["India banks"]["parentId"], None)
        # deleting a folder with items moves it to the Trash; restore puts it back
        r = self.ok(self.delete(f"/api/vaults/{self.vid}/folders/{india}"))
        self.assertEqual(r["result"], "trashed")
        self.assertNotIn("India banks", self.folders())
        trash = self.ok(self.get(f"/api/vaults/{self.vid}/items?trash=true"))
        self.assertEqual([i["title"] for i in trash["items"]], ["SBI"])
        self.ok(self.post(f"/api/vaults/{self.vid}/restore/{india}"))
        self.assertIn("India banks", self.folders())
        # an empty folder goes for good
        self.assertEqual(self.ok(self.delete(f"/api/vaults/{self.vid}/folders/{bank}"))["result"], "deleted")

    def test_items_by_folder(self):
        a = self.ok(self.post(f"/api/vaults/{self.vid}/folders", {"name": "A"}), 201)["id"]
        self.add_item(self.vid, title="root item")
        self.add_item(self.vid, title="in A", folderId=a)
        self.assertEqual([i["title"] for i in self.ok(self.get(f"/api/vaults/{self.vid}/items"))["items"]], ["root item"])
        self.assertEqual([i["title"] for i in self.ok(self.get(f"/api/vaults/{self.vid}/items?folder={a}"))["items"]], ["in A"])
        self.assertEqual(len(self.ok(self.get(f"/api/vaults/{self.vid}/items?recursive=true"))["items"]), 2)


class Items(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.set_up(ADMIN)
        self.set_up(NEHA)
        self.vid = self.personal()["id"]
        self.hid = self.vaults()["Household"]["id"]

    def test_login_card_note_and_secrets(self):
        login = self.add_item(self.vid, title="SBI", username="kiran", password=SECRETS[0], url="onlinesbi.sbi",
                              totp=SECRETS[4], tags=["bank"], favourite=True,
                              fields=[{"name": "Security answer", "value": SECRETS[5], "protected": True},
                                      {"name": "Branch", "value": "Guntur"}])
        self.assertEqual((login["type"], login["host"], login["favourite"], login["tags"]), ("login", "onlinesbi.sbi", True, ["bank"]))
        card = self.add_item(self.vid, type="card", title="Visa", cardholder="Kiran K", expiry="12/29",
                             card={"number": "4111 1111 1111 1111", "cvv": SECRETS[2], "pin": SECRETS[3]})
        self.assertEqual((card["cardLast4"], card["card"]["cvv"]), ("1111", {"set": True}))
        note = self.add_item(self.vid, type="note", title="Locker", notes="Top shelf")
        self.assertEqual(note["type"], "note")
        # nothing secret in any list, detail or search payload
        payloads = [self.get(f"/api/vaults/{self.vid}/items").text, self.get("/api/items").text,
                    self.get(f"/api/vaults/{self.vid}/items/{login['id']}").text,
                    self.get(f"/api/vaults/{self.vid}/items/{card['id']}").text, self.get("/api/search?q=s").text,
                    self.get(f"/api/vaults/{self.vid}/items/{login['id']}/history").text]
        for p in payloads:
            for s in SECRETS:
                # short ones (CVV, PIN) as a JSON string, so a random hex id containing "987" isn't a false alarm
                self.assertNotIn(s if len(s) > 5 else f'"{s}"', p)
        get = lambda iid, f: self.ok(self.get(f"/api/vaults/{self.vid}/items/{iid}/secret?field={f}"))["value"]
        self.assertEqual(get(login["id"], "Password"), SECRETS[0])
        self.assertEqual(get(login["id"], "Security answer"), SECRETS[5])
        self.assertEqual(get(card["id"], "Number"), "4111111111111111")
        self.assertIn("JBSWY3DPEHPK3PXP", get(login["id"], "totpUri"))
        code = self.ok(self.get(f"/api/vaults/{self.vid}/items/{login['id']}/totp"))
        self.assertEqual(len(code["code"]), 6)
        d = self.ok(self.get(f"/api/vaults/{self.vid}/items/{login['id']}"))
        self.assertEqual({f["name"]: f["value"] for f in d["fields"]}, {"Security answer": None, "Branch": "Guntur"})

    def test_update_history_and_conflict(self):
        it = self.add_item(self.hid, title="Router", username="admin", password="old")
        upd = self.ok(self.patch(f"/api/vaults/{self.hid}/items/{it['id']}", {"password": "new", "ifModified": it["modified"]}))
        self.assertEqual(upd["historyCount"], 1)
        h = self.ok(self.get(f"/api/vaults/{self.hid}/items/{it['id']}/history"))["history"]
        self.assertTrue(h[0]["passwordChanged"])
        # Neha edits from an old copy → 409 with the current version
        r = self.patch(f"/api/vaults/{self.hid}/items/{it['id']}", {"username": "x", "ifModified": "stale"}, NEHA)
        self.assertEqual(r.status_code, 409)
        self.assertIn("changed this item", json.dumps(r.json()))
        # untouched fields stay; protected fields not sent stay
        self.ok(self.patch(f"/api/vaults/{self.hid}/items/{it['id']}", {"notes": "hello"}, NEHA))
        self.assertEqual(self.ok(self.get(f"/api/vaults/{self.hid}/items/{it['id']}/secret?field=Password"))["value"], "new")
        self.assertEqual(self.patch(f"/api/vaults/{self.hid}/items/{it['id']}", {"title": " "}).status_code, 422)
        self.assertEqual(self.post(f"/api/vaults/{self.hid}/items", {"title": "x", "totp": "not base32 !!"}).status_code, 422)

    def test_delete_restore_empty(self):
        it = self.add_item(self.vid, title="Old")
        self.assertEqual(self.ok(self.delete(f"/api/vaults/{self.vid}/items/{it['id']}"))["result"], "trashed")
        self.assertEqual(self.ok(self.get(f"/api/vaults/{self.vid}/items"))["items"], [])
        self.assertEqual(self.ok(self.get(f"/api/vaults/{self.vid}/folders"))["trashCount"], 1)
        self.ok(self.post(f"/api/vaults/{self.vid}/restore/{it['id']}"))
        self.assertEqual(len(self.ok(self.get(f"/api/vaults/{self.vid}/items"))["items"]), 1)
        self.ok(self.delete(f"/api/vaults/{self.vid}/items/{it['id']}"))
        self.ok(self.post(f"/api/vaults/{self.vid}/trash/empty"))
        self.assertEqual(self.ok(self.get(f"/api/vaults/{self.vid}/folders"))["trashCount"], 0)

    def test_move_and_copy(self):
        a = self.add_item(self.vid, title="Netflix", password="nf")
        f = self.ok(self.post(f"/api/vaults/{self.hid}/folders", {"name": "Streaming"}), 201)["id"]
        self.ok(self.post(f"/api/vaults/{self.vid}/items/move", {"items": [a["id"]], "toVaultId": self.hid,
                                                                  "toFolderId": f, "duplicate": True}))
        self.assertEqual(len(self.ok(self.get(f"/api/vaults/{self.vid}/items"))["items"]), 1)
        moved = self.ok(self.get(f"/api/vaults/{self.hid}/items?folder={f}"))["items"]
        self.assertEqual([m["title"] for m in moved], ["Netflix"])
        self.assertEqual(self.ok(self.get(f"/api/vaults/{self.hid}/items/{moved[0]['id']}/secret?field=Password",
                                          NEHA))["value"], "nf")
        self.ok(self.post(f"/api/vaults/{self.vid}/items/move", {"items": [a["id"]], "toVaultId": self.hid}))
        self.assertEqual(self.ok(self.get(f"/api/vaults/{self.vid}/items"))["items"], [])
        self.assertEqual(self.ok(self.get(f"/api/vaults/{self.vid}/folders"))["trashCount"], 1)
        # Neha can't move my Personal items (she can't even see it)
        self.assertEqual(self.post(f"/api/vaults/{self.vid}/items/move", {"items": [a["id"]], "toVaultId": self.hid},
                                   NEHA).status_code, 404)

    def test_favourites_and_recent(self):
        a = self.add_item(self.vid, title="A", favourite=True)
        b = self.add_item(self.vid, title="B")
        self.assertEqual([i["title"] for i in self.ok(self.get("/api/items?favourites=true"))["items"]], ["A"])
        self.ok(self.get(f"/api/vaults/{self.vid}/items/{b['id']}"))
        self.ok(self.get(f"/api/vaults/{self.vid}/items/{a['id']}"))
        self.assertEqual([i["title"] for i in self.ok(self.get("/api/items?recent=true"))["items"]], ["A", "B"])


class Search(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.set_up(ADMIN)
        vid = self.personal()["id"]
        hid = self.vaults()["Household"]["id"]
        bank = self.ok(self.post(f"/api/vaults/{vid}/folders", {"name": "Banking"}), 201)["id"]
        self.add_item(vid, title="State Bank of India", username="kiran", url="https://onlinesbi.sbi", folderId=bank,
                      tags=["bank"], password="zzsecret", notes="nominee is Neha")
        self.add_item(vid, title="Netflix", username="family@example.com", url="https://www.netflix.com", favourite=True,
                      totp="JBSWY3DPEHPK3PXP")
        self.add_item(hid, title="Airtel Xstream net", url="airtel.in", tags=["utility"])
        self.add_item(hid, type="card", title="Visa", card={"number": "4111111111111234"})
        self.add_item(vid, title="Café Coffee Day")

    def titles(self, q, **kw):
        qs = "&".join(f"{k}={v}" for k, v in kw.items())
        return [i["title"] for i in self.ok(self.get(f"/api/search?q={q}&{qs}"))["items"]]

    def test_matching(self):
        self.assertEqual(self.titles("net"), ["Netflix", "Airtel Xstream net"])     # title start ranks first
        self.assertEqual(self.titles("sbi"), ["State Bank of India"])              # website host
        self.assertEqual(self.titles("bank india"), ["State Bank of India"])       # every word must match
        self.assertEqual(self.titles("banking"), ["State Bank of India"])          # folder name
        self.assertEqual(self.titles("cafe"), ["Café Coffee Day"])                 # accents
        self.assertEqual(self.titles("1234"), ["Visa"])                            # card last 4
        self.assertEqual(self.titles('"coffee day"'), ["Café Coffee Day"])
        self.assertEqual(self.titles("zzsecret"), [])                              # never passwords
        self.assertEqual(self.titles("nominee"), [])                               # notes off by default
        self.ok(self.put("/api/me/settings", {"searchNotes": True}))
        self.assertEqual(self.titles("nominee"), ["State Bank of India"])

    def test_filters(self):
        self.assertEqual(self.titles("tag:utility"), ["Airtel Xstream net"])
        self.assertEqual(self.titles("has:2fa"), ["Netflix"])
        self.assertEqual(self.titles("type:card"), ["Visa"])
        self.assertEqual(self.titles("vault:household"), ["Airtel Xstream net", "Visa"])
        self.assertEqual(self.titles("is:favourite"), ["Netflix"])
        self.assertEqual(self.titles("folder:bank"), ["State Bank of India"])
        vid = self.personal()["id"]
        self.assertEqual(sorted(self.titles("", vault=vid)), ["Café Coffee Day", "Netflix", "State Bank of India"])

    def test_locked_vaults_reported(self):
        v = self.ok(self.post("/api/vaults", {"name": "Joint", "passwordMode": "chosen",
                                              "password": "saffron-lantern-meadow-violin-comet"}), 201)
        self.add_item(v["id"], title="Hidden")
        self.ok(self.post(f"/api/vaults/{v['id']}/forget"))
        self.ok(self.post("/api/lock"))
        self.unlock(ADMIN)
        r = self.ok(self.get("/api/search?q=hidden"))
        self.assertEqual((r["items"], r["lockedVaults"]), ([], ["Joint"]))


class Tools(unittest.TestCase):
    def test_totp_rfc6238(self):
        for alg, secret, want in (("SHA1", "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ", "94287082"),
                                  ("SHA256", "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQGEZA", "46119246"),
                                  ("SHA512", "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQGEZDGNA",
                                   "90693936")):
            p = {"secret": secret, "digits": 8, "period": 30, "algorithm": alg}
            self.assertEqual(totp.code(p, 59)["code"], want)
        p = totp.parse("otpauth://totp/GitHub:kiran?secret=JBSWY3DPEHPK3PXP&issuer=GitHub&digits=6")
        self.assertEqual((p["issuer"], p["account"]), ("GitHub", "kiran"))
        with self.assertRaises(totp.BadOtp):
            totp.parse("otpauth://hotp/x?secret=JBSWY3DPEHPK3PXP")

    def test_generator_and_strength(self):
        pw = passwords.generate(24, symbols=False)
        self.assertEqual(len(pw), 24)
        self.assertTrue(pw.isalnum())
        self.assertEqual(len(passwords.passphrase(6).split("-")), 6)
        self.assertLess(passwords.strength("password123")["score"], 2)
        self.assertLess(passwords.strength("qwerty123456")["score"], 2)
        self.assertGreaterEqual(passwords.strength("maple-river-candle-orbit-zebra")["score"], 3)
        self.assertIsNotNone(passwords.check_master("Summer2024!", 12))
        self.assertIsNone(passwords.check_master(passwords.passphrase(6), 12))


class Api(ApiTestCase):
    def test_generate_endpoint(self):
        self.set_up(ADMIN)
        r = self.ok(self.post("/api/generate", {"kind": "passphrase", "words": 5}))
        self.assertEqual(len(r["password"].split("-")), 5)
        self.assertIn("score", r["strength"])


if __name__ == "__main__":
    unittest.main()


class KeePassTotp(ApiTestCase):
    """Every TOTP is also written as KeePass's own fields (TimeOtp-*), so KeePass 2.47+ shows the code."""

    def setUp(self):
        super().setUp()
        self.set_up(ADMIN)
        self.vid = self.personal()["id"]

    def entry(self, title):
        from app import kdbx
        data = self.get(f"/api/vaults/{self.vid}/download").content
        d = kdbx.open_bytes(data, PW[ADMIN["id"]])
        e = next(x for x in d.entries.values() if d.get_field(x, "Title") == title)
        return d.get_fields(e), d.protected_keys(e)

    def test_written_updated_and_removed(self):
        it = self.add_item(self.vid, title="GitHub",
                           totp="otpauth://totp/GitHub:kiran?secret=JBSWY3DPEHPK3PXP&digits=8&period=60&algorithm=SHA256")
        f, prot = self.entry("GitHub")
        self.assertTrue(f["otp"].startswith("otpauth://"))
        self.assertEqual((f["TimeOtp-Secret-Base32"], f["TimeOtp-Length"], f["TimeOtp-Period"], f["TimeOtp-Algorithm"]),
                         ("JBSWY3DPEHPK3PXP", "8", "60", "HMAC-SHA-256"))
        self.assertIn("TimeOtp-Secret-Base32", prot)
        # KeePass defaults (6 digits, 30 s, SHA-1) aren't written; stale ones go
        self.ok(self.patch(f"/api/vaults/{self.vid}/items/{it['id']}", {"totp": "jbsw y3dp ehpk 3pxp"}))
        f, _ = self.entry("GitHub")
        self.assertEqual(f["TimeOtp-Secret-Base32"], "JBSWY3DPEHPK3PXP")
        self.assertFalse({"TimeOtp-Length", "TimeOtp-Period", "TimeOtp-Algorithm"} & set(f))
        # removing the 2FA removes every copy
        self.ok(self.patch(f"/api/vaults/{self.vid}/items/{it['id']}", {"totp": ""}))
        f, _ = self.entry("GitHub")
        self.assertFalse({"otp", "TimeOtp-Secret-Base32"} & set(f))
        # the names can't be used as custom fields, and they never show as custom fields
        self.assertEqual(self.patch(f"/api/vaults/{self.vid}/items/{it['id']}",
                                    {"fields": [{"name": "TimeOtp-Secret-Base32", "value": "x"}]}).status_code, 422)

    def test_older_files_get_the_fields_on_open(self):
        from app import db, kdbx, service, sessions, storage
        self.add_item(self.vid, title="Old", totp="JBSWY3DPEHPK3PXP")
        ov = sessions.get_open(self.vid)
        with ov.lock:
            e = next(x for x in ov.db.entries.values() if ov.db.get_field(x, "Title") == "Old")
            for k in ("TimeOtp-Secret-Base32",):
                ov.db.remove_field(e, k)
            data = kdbx.save_bytes(ov.db, ov.password)
        with db.get_conn() as conn:
            storage.write_version(conn, self.vid, data, None, ov.key_epoch)
        self.assertNotIn("TimeOtp-Secret-Base32", self.entry("Old")[0])        # an older file without them
        self.ok(self.post("/api/lock", {}))
        sessions.close(self.vid)
        self.unlock(ADMIN)
        f, _ = self.entry("Old")
        self.assertEqual(f["TimeOtp-Secret-Base32"], "JBSWY3DPEHPK3PXP")
        d = self.ok(self.get(f"/api/vaults/{self.vid}/items"))
        self.assertTrue(next(i for i in d["items"] if i["title"] == "Old")["hasTotp"])
        self.assertTrue(service)                   # imported for the open path above

    def test_keepass_only_entry_and_download_all(self):
        from app import kdbx, sessions
        ov = sessions.get_open(self.vid)
        with ov.lock:
            e = ov.db.add_entry(ov.db.root_group, {"Title": "From KeePass", "TimeOtp-Secret-Base32": "JBSWY3DPEHPK3PXP",
                                                   "TimeOtp-Length": "8"}, {"TimeOtp-Secret-Base32"})
            iid = e.findtext("UUID")
        items_ = self.ok(self.get(f"/api/vaults/{self.vid}/items"))["items"]
        it = next(i for i in items_ if i["title"] == "From KeePass")
        self.assertTrue(it["hasTotp"])
        self.assertEqual(len(self.ok(self.get(f"/api/vaults/{self.vid}/items/{it['id']}/totp"))["code"]), 8)
        self.add_item(self.vid, title="Mail", totp="JBSWY3DPEHPK3PXP")
        data = self.post("/api/me/download-all", {"password": PW[ADMIN["id"]]}).content
        d = kdbx.open_bytes(data, PW[ADMIN["id"]])
        e = next(x for x in d.entries.values() if d.get_field(x, "Title") == "Mail")
        self.assertEqual(d.get_field(e, "TimeOtp-Secret-Base32"), "JBSWY3DPEHPK3PXP")
        self.assertTrue(iid)
