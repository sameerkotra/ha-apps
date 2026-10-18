"""The fixes from the October 2026 review: crafted KeePass files, changes that fail half-way, re-keying, limits,
and that vaults written by the previous release still open."""
import _env  # noqa: F401

import base64
import gzip
import hashlib
import io
import os
import shutil
import sqlite3
import struct
import threading
import time
import unittest
import xml.etree.ElementTree as ET
import zipfile
from unittest import mock

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from base import ADMIN, KID, NEHA, PW, VIKRAM, ApiTestCase, headers, sql
from common_tests.ingress import ingress_client

from app import config, db, health, items, kdbx, service, sessions, storage
from app.main import app

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
FAST = {"memory": 64 * 1024, "iterations": 3, "parallelism": 2}
# the master passwords of the 2.1.5 fixture (made before 5-word passphrases counted as "fair")
OLD_PW = {"u-admin": "maple-river-candle-orbit-zebra", "u-neha": "harbor-violet-pepper-lantern-quilt"}


def finishes(fn, seconds=20):
    """Run fn in a thread; fail if it's still running after `seconds` (a reader that loops for ever)."""
    out = {}

    def run():
        try:
            fn()
            out["e"] = None
        except BaseException as e:          # noqa: BLE001 — handed back to the test
            out["e"] = e
    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(seconds)
    assert not t.is_alive(), "still running: the reader doesn't stop on this file"
    return out["e"]


def sample_file(password="pw"):
    d = kdbx.Database.create("Sample", FAST)
    d.add_entry(d.root_group, {"Title": "A", "Password": "x"})
    return kdbx.save_bytes(d, password)


def kdbx3_with_blocks(blocks: bytes, password: str = "pw") -> bytes:
    """A KDBX 3.1 file whose (decrypted) hashed-block stream is `blocks` as given."""
    seed, tseed, iv, pskey, start = (os.urandom(32), os.urandom(32), os.urandom(16), os.urandom(32), os.urandom(32))
    plain = start + blocks

    def f(fid, v):
        return bytes([fid]) + struct.pack("<H", len(v)) + v
    header = (struct.pack("<IIHH", kdbx.SIG1, kdbx.SIG2, 1, 3) + f(2, kdbx.CIPHER_AES256) + f(3, struct.pack("<I", 0))
              + f(4, seed) + f(5, tseed) + f(6, struct.pack("<Q", 10)) + f(7, iv) + f(8, pskey) + f(9, start)
              + f(10, struct.pack("<I", 2)) + f(0, b"\r\n\r\n"))
    transformed = kdbx.aes_kdf(kdbx.composite_key(password), tseed, 10)
    key = hashlib.sha256(seed + transformed).digest()
    p = padding.PKCS7(128).padder()
    enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return header + enc.update(p.update(plain) + p.finalize()) + enc.finalize()


class CraftedFiles(unittest.TestCase):
    """V1: a length field that's negative or runs past the end is refused in every reading loop — it used to make
    the reader go backwards and loop for ever (a 21-byte file was enough)."""

    def assertDamaged(self, raw, password="pw"):
        e = finishes(lambda: kdbx.open_bytes(raw, password))
        self.assertIsInstance(e, kdbx.KdbxError)
        self.assertNotIsInstance(e, kdbx.WrongKey)

    def test_outer_header(self):
        raw = struct.pack("<IIHH", kdbx.SIG1, kdbx.SIG2, 0, 4) + b"\x02" + struct.pack("<i", -5) + b"abcd"
        self.assertEqual(len(raw), 21)
        self.assertDamaged(raw)
        self.assertIsInstance(finishes(lambda: kdbx.header_info(raw)), kdbx.KdbxError)
        big = struct.pack("<IIHH", kdbx.SIG1, kdbx.SIG2, 0, 4) + b"\x02" + struct.pack("<i", 2 ** 31 - 1) + b"abcd"
        self.assertDamaged(big)

    def test_kdf_parameters(self):
        self.assertIsInstance(finishes(lambda: kdbx.vd_parse(b"\x00\x01\x42" + struct.pack("<i", -9) + b"x" * 20)),
                              kdbx.KdbxError)

    def test_kdbx4_blocks(self):
        raw = sample_file()
        _fields, pos = kdbx._read_outer_header(raw, 4)
        at = pos + 64 + 32                                  # header, its SHA-256 and HMAC, the block's HMAC
        for size in (-40, -1, 2 ** 31 - 1):
            self.assertDamaged(raw[:at] + struct.pack("<i", size) + raw[at + 4:])

    def test_kdbx4_inner_header(self):
        real = gzip.compress
        for size in (-5, 10 ** 6):
            inner = b"\x01" + struct.pack("<i", size) + b"\x00" * 20
            with mock.patch.object(kdbx.gzip, "compress", lambda data, compresslevel=6: real(inner)):
                raw = sample_file()
            self.assertDamaged(raw)

    def test_kdbx3_blocks(self):
        for size in (-40, -36, 2 ** 31 - 1):
            blocks = struct.pack("<I", 0) + hashlib.sha256(b"").digest() + struct.pack("<i", size) + b"\x00" * 64
            self.assertDamaged(kdbx3_with_blocks(blocks))

    def test_protected_values_without_a_key_are_refused(self):
        root = ET.fromstring('<KeePassFile><Root><Group><UUID>' + base64.b64encode(b"g" * 16).decode() + '</UUID>'
                             '<Entry><String><Key>Password</Key><Value Protected="True">'
                             + base64.b64encode(b"secret").decode() + '</Value></String></Entry></Group></Root>'
                             '</KeePassFile>')
        with self.assertRaises(kdbx.KdbxError):
            kdbx._unprotect(root, None)

    def test_aes_kdf_matches_the_plain_loop(self):
        def plain(key, seed, rounds):
            enc = Cipher(algorithms.AES(seed), modes.ECB()).encryptor()
            for _ in range(rounds):
                key = enc.update(key)
            return hashlib.sha256(key).digest()
        for rounds in (0, 1, 3, 1000, kdbx.AES_KDF_BATCH - 1, kdbx.AES_KDF_BATCH, kdbx.AES_KDF_BATCH + 5, 200_000):
            k, s = os.urandom(32), os.urandom(32)
            self.assertEqual(kdbx.aes_kdf(k, s, rounds), plain(k, s, rounds), rounds)

    def test_argon2_memory_cap(self):
        kdf = {"$UUID": kdbx.KDF_ARGON2ID, "S": b"s" * 32, "I": 2, "M": 512 * 1024 * 1024, "P": 2}
        with self.assertRaises(kdbx.KdbxError):
            kdbx.check_kdf(kdf)
        kdbx.check_kdf(dict(kdf, M=256 * 1024 * 1024))
        self.assertEqual(kdbx.MAX_INFLATED_BYTES, 64 * 1024 * 1024)


class CompatibleWithPrevious(ApiTestCase):
    """Vault files and a whole /data folder written by 2.1.5 (tests/data, made with that release's code) still open,
    and keep working after a re-key, a restore of an older version and a password change."""

    def test_kdbx_files(self):
        for name in ("fixture_2_1_5_aes.kdbx", "fixture_2_1_5_chacha.kdbx"):
            with open(os.path.join(DATA, name), "rb") as f:
                d = kdbx.open_bytes(f.read(), "fixture-pw")
            e = next(iter(d.entries.values()))
            self.assertEqual((d.get_field(e, "Title"), d.get_field(e, "Password")), ("SBI", "new pässwörd"))
            self.assertEqual(d.get_field(d.history(e)[0], "Password"), "pässwörd 🔑")
            self.assertEqual(d.binaries, [(False, b"attachment bytes")])
            self.assertTrue(d.get_field(e, "otp").startswith("otpauth://"))
            again = kdbx.open_bytes(kdbx.save_bytes(d, composite=d.composite), "fixture-pw")      # and saves back
            self.assertEqual(len(again.entries), 1)

    def load_fixture(self):
        sessions.end_all()
        for ext in ("", "-wal", "-shm"):
            if os.path.exists(config.DB_PATH + ext):
                os.remove(config.DB_PATH + ext)
        shutil.rmtree(config.VAULT_DIR, ignore_errors=True)
        with zipfile.ZipFile(os.path.join(DATA, "data_2_1_5.zip")) as z:
            for n in z.namelist():
                dest = config.DB_PATH if n == "vault.db" else os.path.join(config.VAULT_DIR, n.split("/", 1)[1])
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, "wb") as f:
                    f.write(z.read(n))
        db.init_db()
        with db.get_conn() as conn:
            storage.recover(conn)

    def titles(self, vid, user=ADMIN):
        return {i["title"] for i in self.ok(self.get(f"/api/vaults/{vid}/items?recursive=1", user))["items"]}

    def test_whole_data_folder(self):
        self.load_fixture()
        r = self.ok(self.post("/api/unlock", {"password": OLD_PW["u-admin"]}, token=False))
        self.tokens[ADMIN["id"]] = r["session"]
        self.assertEqual(r["couldNotOpen"], [])
        v = self.vaults()
        self.assertTrue(all(x["open"] for x in v.values()), v)
        self.assertEqual(self.titles(self.personal()["id"]), {"Bank"})
        hh = v["Household"]["id"]
        self.assertEqual(self.titles(hh), {"Wi-Fi", "Alarm"})
        self.assertEqual(self.titles(v["Shared"]["id"]), {"Netflix"})
        self.assertEqual(self.titles(v["Chosen"]["id"]), {"Secret"})
        bank = next(i for i in self.ok(self.get("/api/items"))["items"] if i["title"] == "Bank")
        self.assertEqual(self.ok(self.get(f"/api/vaults/{bank['vaultId']}/items/{bank['id']}/secret?field=Password"))["value"],
                         "pässwörd 🔑")
        self.assertTrue(bank["hasTotp"])
        # Neha opens hers with the key ring 2.1.5 wrote
        r = self.ok(self.post("/api/unlock", {"password": OLD_PW["u-neha"]}, NEHA, token=False))
        self.tokens[NEHA["id"]] = r["session"]
        self.assertEqual(self.titles(hh, NEHA), {"Wi-Fi", "Alarm"})
        # an older Household version restores; a re-key keeps the history restorable
        versions = self.ok(self.get(f"/api/vaults/{hh}/versions"))["versions"]
        self.assertGreater(len(versions), 1)
        self.ok(self.post(f"/api/vaults/{hh}/rekey"))
        oldest = min(x["version"] for x in self.ok(self.get(f"/api/vaults/{hh}/versions"))["versions"])
        self.ok(self.post(f"/api/vaults/{hh}/versions/{oldest}/restore"))
        self.add_item(hh, title="After")
        # lock everything and come back: it all opens again, including after a master password change
        sessions.end_all()
        self.unlock_with(ADMIN, OLD_PW["u-admin"])
        self.assertIn("After", self.titles(hh))
        new = "orbit-velvet-harbor-maple-glacier-tulip"
        self.ok(self.post("/api/me/password", {"current": OLD_PW["u-admin"], "new": new}))
        sessions.end_all()
        self.unlock_with(ADMIN, new)
        self.assertEqual(self.titles(self.personal()["id"]), {"Bank"})
        self.unlock_with(NEHA, OLD_PW["u-neha"])
        self.assertIn("After", self.titles(hh, NEHA))

    def unlock_with(self, user, pw):
        r = self.ok(self.post("/api/unlock", {"password": pw}, user, token=False))
        self.tokens[user["id"]] = r["session"]
        self.assertEqual(r["couldNotOpen"], [])


def kdbx_with(entries: list, password="pw", strip_uuid=None) -> bytes:
    d = kdbx.Database.create("Other app", FAST)
    for fields in entries:
        d.add_entry(d.root_group, dict(fields), {"otp"} if "otp" in fields else None)
    if strip_uuid:
        strip_uuid(d)
    return kdbx.save_bytes(d, password)


class FailedChanges(ApiTestCase):
    """V2, V4: a change that fails half-way leaves neither the database nor the open, shared vault half-changed."""

    def setUp(self):
        super().setUp()
        self.set_up(ADMIN)
        self.set_up(NEHA)
        self.hh = self.vaults()["Household"]["id"]
        self.loose = ingress_client(app, raise_server_exceptions=False)

    def loose_req(self, method, path, user=ADMIN, **kw):
        h = dict(headers(user))
        h["X-Vault-Session"] = self.tokens[user["id"]]
        return self.loose.request(method, path, headers=h, **kw)

    def test_bad_totp_in_an_imported_file_doesnt_break_the_vault(self):
        raw = kdbx_with([{"Title": "Odd", "otp": "otpauth://totp/x?secret=JBSWY3DPEHPK3PXP&digits=x"},
                         {"Title": "Fine", "otp": "otpauth://totp/y?secret=JBSWY3DPEHPK3PXP"}])
        r = self.ok(self.req("POST", "/api/import", files={"file": ("o.kdbx", raw)},
                             data={"password": "pw", "target": self.hh, "skipDuplicates": "false"}), 201)
        self.assertEqual(r["items"], 2)
        self.add_item(self.hh, title="Later")                 # Household still saves, for everyone
        listed = {i["title"]: i for i in self.ok(self.get(f"/api/vaults/{self.hh}/items?recursive=1", NEHA))["items"]}
        self.assertFalse(listed["Odd"]["hasTotp"])
        self.assertTrue(listed["Fine"]["hasTotp"])
        r = self.ok(self.req("POST", "/api/import", files={"file": ("o.kdbx", raw)}, data={"password": "pw"}), 201)
        self.add_item(r["vaultId"], title="More")

    def test_refused_change_leaves_the_item_as_it_was(self):
        it = self.add_item(self.hh, title="Alarm", password="1234")
        r = self.patch(f"/api/vaults/{self.hh}/items/{it['id']}", {"title": "Changed", "expires": "not a date"})
        self.assertEqual(r.status_code, 422)
        r = self.patch(f"/api/vaults/{self.hh}/items/{it['id']}", {"title": "Changed", "remindDays": 3})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(self.patch(f"/api/vaults/{self.hh}/items/{it['id']}", {"fields": ["x"]}).status_code, 422)
        self.assertEqual(self.ok(self.get(f"/api/vaults/{self.hh}/items/{it['id']}", NEHA))["title"], "Alarm")

    def test_save_that_fails_puts_the_vault_back(self):
        it = self.add_item(self.hh, title="Alarm", password="1234")
        version = sql("SELECT version FROM vaults WHERE id = ?", (self.hh,))[0]["version"]
        with mock.patch.object(storage, "write_version", side_effect=OSError("disk full")):
            r = self.loose_req("PATCH", f"/api/vaults/{self.hh}/items/{it['id']}", json={"title": "Half"})
        self.assertEqual(r.status_code, 500)
        self.assertEqual(self.ok(self.get(f"/api/vaults/{self.hh}/items/{it['id']}", NEHA))["title"], "Alarm")
        self.assertEqual(sql("SELECT version FROM vaults WHERE id = ?", (self.hh,))[0]["version"], version)
        self.ok(self.patch(f"/api/vaults/{self.hh}/items/{it['id']}", {"title": "Whole"}))      # and it works again
        self.assertEqual(self.ok(self.get(f"/api/vaults/{self.hh}/items/{it['id']}", NEHA))["title"], "Whole")

    def test_too_big_is_413_and_nothing_stays(self):
        with mock.patch.object(storage, "MAX_FILE_BYTES", 100):
            r = self.post(f"/api/vaults/{self.hh}/items", {"title": "Big"})
        self.assertEqual(r.status_code, 413)
        self.assertNotIn("Big", {i["title"] for i in self.ok(self.get(f"/api/vaults/{self.hh}/items"))["items"]})

    def test_commit_failure_after_rekey_keeps_the_vault_openable(self):
        self.add_item(self.hh, title="Alarm", password="1234")
        old_pw = self.ok(self.get(f"/api/vaults/{self.hh}/password"))["password"]
        before = sql("SELECT key_epoch, version FROM vaults WHERE id = ?", (self.hh,))[0]
        files = sorted(os.listdir(storage.vault_dir(self.hh)))
        real = db.Connection.commit
        fail = {"left": 1}

        def commit(conn):
            if conn.vault_edit is not None and fail["left"]:
                fail["left"] -= 1
                raise sqlite3.OperationalError("disk I/O error")
            return real(conn)
        with mock.patch.object(db.Connection, "commit", commit):
            r = self.loose_req("POST", f"/api/vaults/{self.hh}/rekey")
        self.assertEqual(r.status_code, 500)
        self.assertEqual(sql("SELECT key_epoch, version FROM vaults WHERE id = ?", (self.hh,))[0], before)
        self.assertEqual(self.ok(self.get(f"/api/vaults/{self.hh}/password"))["password"], old_pw)
        # every older version is still there and still opens with the password the database knows
        for n in files:
            with open(os.path.join(storage.vault_dir(self.hh), n), "rb") as f:
                kdbx.open_bytes(f.read(), old_pw)
        self.assertFalse([n for n in os.listdir(storage.vault_dir(self.hh)) if n.endswith(storage.REWRITE_SUFFIX)])
        # after everyone locks, the key ring still opens it
        sessions.end_all()
        self.unlock(ADMIN)
        self.unlock(NEHA)
        self.assertIn("Alarm", {i["title"] for i in self.ok(self.get(f"/api/vaults/{self.hh}/items", NEHA))["items"]})
        self.ok(self.post(f"/api/vaults/{self.hh}/rekey"))                 # and a re-key that commits works

    def test_password_change_that_fails_keeps_the_old_password(self):
        real = db.Connection.commit

        def commit(conn):
            if conn.vault_edit is not None:
                raise sqlite3.OperationalError("disk I/O error")
            return real(conn)
        with mock.patch.object(db.Connection, "commit", commit):
            r = self.loose_req("POST", "/api/me/password", json={"current": PW["u-admin"],
                                                                "new": "orbit-velvet-harbor-maple-glacier-tulip"})
        self.assertEqual(r.status_code, 500)
        sessions.end_all()
        self.unlock(ADMIN)                                     # the old master password, the old key ring
        self.assertTrue(self.vaults()["Household"]["open"])

    def test_remembered_vault_isnt_forgotten_for_a_read_error(self):
        s = self.ok(self.post("/api/vaults", {"name": "Bills", "passwordMode": "random"}), 201)
        sessions.end_all()
        real = storage.read_current

        def flaky(conn, vid):
            if vid == s["id"]:
                raise OSError("I/O error")
            return real(conn, vid)
        with mock.patch.object(storage, "read_current", flaky):
            r = self.unlock(ADMIN)
        self.assertEqual(r["couldNotOpen"], ["Bills"])
        self.assertTrue(sql("SELECT 1 FROM vault_keys WHERE vault_id = ? AND user_id = ?", (s["id"], ADMIN["id"])))
        sessions.end_all()
        self.assertEqual(self.unlock(ADMIN)["couldNotOpen"], [])

    def test_failed_import_as_new_vault_leaves_nothing(self):
        raw = kdbx_with([{"Title": "One"}])
        before_dirs = set(os.listdir(config.VAULT_DIR))
        before_open = set(sessions._open)
        with mock.patch.object(service, "save", side_effect=RuntimeError("boom")):
            r = self.loose_req("POST", "/api/import", files={"file": ("o.kdbx", raw)}, data={"password": "pw"})
        self.assertEqual(r.status_code, 500)
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM vaults WHERE kind = 'shared'")[0]["n"], 0)
        self.assertEqual(set(os.listdir(config.VAULT_DIR)), before_dirs)
        self.assertEqual(set(sessions._open), before_open)

    def test_import_one_at_a_time(self):
        from app.routers import vaults as vr
        raw = kdbx_with([{"Title": "One"}])
        vr._IMPORT_SLOT.acquire()
        try:
            with mock.patch.object(vr, "IMPORT_WAIT_S", 0.05):
                r = self.req("POST", "/api/import", files={"file": ("o.kdbx", raw)}, data={"password": "pw"})
        finally:
            vr._IMPORT_SLOT.release()
        self.assertEqual(r.status_code, 429)
        crafted = struct.pack("<IIHH", kdbx.SIG1, kdbx.SIG2, 0, 4) + b"\x02" + struct.pack("<i", -5) + b"abcd"
        r = self.req("POST", "/api/import", files={"file": ("x.kdbx", crafted)}, data={"password": "pw"})
        self.assertEqual(r.status_code, 422)

    def test_imported_ids_are_fixed(self):
        def strip(d):
            es = list(d.entries.values())
            es[0].remove(es[0].find("UUID"))
            es[1].find("UUID").text = "not base64!"
            es[2].find("UUID").text = es[3].findtext("UUID")
            d.reindex()
        raw = kdbx_with([{"Title": t} for t in ("A", "B", "C", "D")], strip_uuid=strip)
        r = self.ok(self.req("POST", "/api/import", files={"file": ("o.kdbx", raw)}, data={"password": "pw"}), 201)
        got = self.ok(self.get(f"/api/vaults/{r['vaultId']}/items?recursive=1"))["items"]
        self.assertEqual(sorted(i["title"] for i in got), ["A", "B", "C", "D"])
        self.assertEqual(len({i["id"] for i in got}), 4)
        # the same file again into the same vault: new ids, so both copies can be found
        self.ok(self.req("POST", "/api/import", files={"file": ("o.kdbx", raw)},
                         data={"password": "pw", "target": r["vaultId"], "skipDuplicates": "false"}), 201)
        got = self.ok(self.get(f"/api/vaults/{r['vaultId']}/items?recursive=1"))["items"]
        self.assertEqual(len({i["id"] for i in got}), 8)


class RekeyAndVersions(ApiTestCase):
    """V5: only an admin re-keys Household; a re-key keeps the history; bulk deletes are one version."""

    def setUp(self):
        super().setUp()
        self.set_up(ADMIN)
        self.set_up(NEHA)
        self.hh = self.vaults()["Household"]["id"]

    def test_household_rekey_is_admin_only_and_keeps_history(self):
        for t in ("A", "B", "C"):
            self.add_item(self.hh, title=t)
        n = len(sql("SELECT * FROM vault_versions WHERE vault_id = ?", (self.hh,)))
        self.assertEqual(self.post(f"/api/vaults/{self.hh}/rekey", user=NEHA).status_code, 403)
        self.ok(self.post(f"/api/vaults/{self.hh}/rekey"))
        rows = sql("SELECT version FROM vault_versions WHERE vault_id = ? ORDER BY version", (self.hh,))
        self.assertEqual(len(rows), n + 1)
        self.ok(self.post(f"/api/vaults/{self.hh}/versions/{rows[1]['version']}/restore", user=NEHA))
        self.assertEqual({i["title"] for i in self.ok(self.get(f"/api/vaults/{self.hh}/items", NEHA))["items"]}, {"A"})

    def test_batch_delete_is_one_save(self):
        made = [self.add_item(self.hh, title=f"T{i}") for i in range(6)]
        v0 = sql("SELECT version FROM vaults WHERE id = ?", (self.hh,))[0]["version"]
        r = self.ok(self.post(f"/api/vaults/{self.hh}/items/delete", {"items": [x["id"] for x in made[:5]]}, NEHA))
        self.assertEqual(r, {"trashed": 5, "deleted": 0})
        self.assertEqual(sql("SELECT version FROM vaults WHERE id = ?", (self.hh,))[0]["version"], v0 + 1)
        self.assertEqual([i["title"] for i in self.ok(self.get(f"/api/vaults/{self.hh}/items"))["items"]], ["T5"])
        # an unknown id: nothing changes
        self.assertEqual(self.post(f"/api/vaults/{self.hh}/items/delete", {"items": [made[5]["id"], "0" * 32]}).status_code, 404)
        self.assertEqual(sql("SELECT version FROM vaults WHERE id = ?", (self.hh,))[0]["version"], v0 + 1)
        self.assertEqual([i["title"] for i in self.ok(self.get(f"/api/vaults/{self.hh}/items"))["items"]], ["T5"])

    def test_batch_create_is_one_save(self):
        v0 = sql("SELECT version FROM vaults WHERE id = ?", (self.hh,))[0]["version"]
        r = self.ok(self.post(f"/api/vaults/{self.hh}/items/batch", {"items": [
            {"title": "GitHub", "totp": "JBSWY3DPEHPK3PXP"}, {"title": "Mail", "totp": "JBSWY3DPEHPK3PXP"}]}), 201)
        self.assertEqual([i["title"] for i in r["items"]], ["GitHub", "Mail"])
        self.assertEqual(sql("SELECT version FROM vaults WHERE id = ?", (self.hh,))[0]["version"], v0 + 1)
        bad = self.post(f"/api/vaults/{self.hh}/items/batch", {"items": [{"title": "OK"}, {"title": ""}]})
        self.assertEqual(bad.status_code, 422)                                          # all or nothing
        self.assertNotIn("OK", {i["title"] for i in self.ok(self.get(f"/api/vaults/{self.hh}/items"))["items"]})

    def test_versions_kept_by_count_and_by_day(self):
        from datetime import date, timedelta
        today = date(2026, 10, 13)
        rows = []
        v = 100
        for day in range(45):                    # 45 days, two versions a day, the newest first
            for hour in (18, 9):
                d = today - timedelta(days=day)
                rows.append({"version": v, "created_at": f"{d.isoformat()}T{hour:02d}:00:00.000Z"})
                v -= 1
        kept = storage.keep(rows, today)
        newest = {r["version"] for r in rows[:storage.KEEP_VERSIONS]}
        per_day = {rows[2 * d]["version"] for d in range(storage.KEEP_DAYS)}
        self.assertEqual(kept, newest | per_day)
        self.assertEqual(len(kept), 20 + 20)      # days 0–9 are in the newest 20 already

    def test_concurrent_edits_and_rekeys(self):
        ov = sessions.get_open(self.hh)
        errors = []

        def edit():
            for i in range(12):
                try:
                    with db.get_conn() as conn:
                        with service.editing(conn, ov):
                            items.create(ov.db, ov.db.root_group, {"title": f"E{i}"})
                            service.save(conn, ov, ADMIN["id"])
                except Exception as e:           # noqa: BLE001
                    errors.append(e)

        def rekey():
            for _ in range(4):
                try:
                    with db.get_conn() as conn:
                        service.rekey(conn, ov, ADMIN["id"])
                except Exception as e:           # noqa: BLE001
                    errors.append(e)
                time.sleep(0.01)
        ts = [threading.Thread(target=edit), threading.Thread(target=rekey)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(120)
        self.assertEqual(errors, [])
        row = sql("SELECT key_epoch, version FROM vaults WHERE id = ?", (self.hh,))[0]
        self.assertEqual(row["key_epoch"], ov.key_epoch)
        d = kdbx.open_bytes(storage.read_version(self.hh, row["version"]), ov.password)
        self.assertEqual({d.get_field(e, "Title") for e in d.entries.values()}, {f"E{i}" for i in range(12)})
        for r in sql("SELECT version, key_epoch FROM vault_versions WHERE vault_id = ?", (self.hh,)):
            self.assertEqual(r["key_epoch"], ov.key_epoch)
            kdbx.open_bytes(storage.read_version(self.hh, r["version"]), ov.password)
        sessions.end_all()
        self.unlock(NEHA)
        self.assertEqual(len(self.ok(self.get(f"/api/vaults/{self.hh}/items", NEHA))["items"]), 12)


class Leftovers(ApiTestCase):
    """VI5 and start-up recovery: what failed changes and crashes leave behind is tidied."""

    def test_recover(self):
        self.set_up(ADMIN)
        hh = self.vaults()["Household"]["id"]
        self.add_item(hh, title="A")
        d = storage.vault_dir(hh)
        cur = sql("SELECT version FROM vaults WHERE id = ?", (hh,))[0]["version"]
        older = min(r["version"] for r in sql("SELECT version FROM vault_versions WHERE vault_id = ?", (hh,)))
        with open(storage.path_for(hh, cur), "rb") as f:
            current = f.read()
        # a re-key that committed but crashed before moving its file: the row's checksum matches → finished
        side = storage.path_for(hh, older) + storage.REWRITE_SUFFIX
        with open(side, "wb") as f:
            f.write(current)
        with db.get_conn() as conn:
            conn.execute("UPDATE vault_versions SET sha256 = ? WHERE vault_id = ? AND version = ?",
                         (hashlib.sha256(current).hexdigest(), hh, older))
        # one that never committed (checksum doesn't match), a save that never committed, a vault never created
        with open(storage.path_for(hh, cur) + storage.REWRITE_SUFFIX, "wb") as f:
            f.write(b"junk")
        with open(storage.path_for(hh, cur + 5), "wb") as f:
            f.write(b"junk")
        os.makedirs(os.path.join(config.VAULT_DIR, "never-created"))
        with db.get_conn() as conn:
            out = storage.recover(conn)
        self.assertEqual(out, {"finished": 1, "removed": 2, "folders": 1})
        with open(storage.path_for(hh, older), "rb") as f:
            self.assertEqual(f.read(), current)
        self.assertFalse([n for n in os.listdir(d) if not n.endswith(".kdbx") or n == f"{cur + 5}.kdbx"])
        self.assertFalse(os.path.exists(os.path.join(config.VAULT_DIR, "never-created")))

    def test_recent_items_and_audit_log(self):
        from app import main
        self.set_up(ADMIN)
        s = self.ok(self.post("/api/vaults", {"name": "Bills", "passwordMode": "random"}), 201)
        it = self.add_item(s["id"], title="Power")
        self.ok(self.get(f"/api/vaults/{s['id']}/items/{it['id']}"))
        self.assertTrue(sql("SELECT 1 FROM recent_items WHERE vault_id = ?", (s["id"],)))
        self.ok(self.post(f"/api/vaults/{s['id']}/delete", {"confirmName": "Bills"}))
        self.assertFalse(sql("SELECT 1 FROM recent_items WHERE vault_id = ?", (s["id"],)))
        self.assertFalse(os.path.exists(storage.vault_dir(s["id"])))
        with db.get_conn() as conn:
            conn.execute("INSERT INTO audit_log (id, action, created_at) VALUES ('old', 'x', '2020-01-01T00:00:00.000Z')")
            conn.execute("INSERT INTO recent_items (user_id, vault_id, item_id, used_at) VALUES (?, 'gone', 'i', ?)",
                         (ADMIN["id"], config.now_iso()))
        main._daily()
        self.assertFalse(sql("SELECT 1 FROM audit_log WHERE id = 'old'"))
        self.assertTrue(sql("SELECT 1 FROM audit_log"))
        self.assertFalse(sql("SELECT 1 FROM recent_items WHERE vault_id = 'gone'"))
        self.ok(self.put("/api/admin/settings", {"audit_keep_days": 0}))           # 0: kept for ever
        with db.get_conn() as conn:
            conn.execute("INSERT INTO audit_log (id, action, created_at) VALUES ('old', 'x', '2020-01-01T00:00:00.000Z')")
        main._daily()
        self.assertTrue(sql("SELECT 1 FROM audit_log WHERE id = 'old'"))

    def test_failed_create_leaves_no_folder(self):
        self.set_up(ADMIN)
        before = set(os.listdir(config.VAULT_DIR))
        loose = ingress_client(app, raise_server_exceptions=False)
        h = dict(headers(ADMIN), **{"X-Vault-Session": self.tokens[ADMIN["id"]]})
        with mock.patch.object(service, "remember", side_effect=RuntimeError("boom")):
            r = loose.post("/api/vaults", json={"name": "Nope", "passwordMode": "random"}, headers=h)
        self.assertEqual(r.status_code, 500)
        self.assertEqual(set(os.listdir(config.VAULT_DIR)), before)
        self.assertFalse(sql("SELECT 1 FROM vaults WHERE name = 'Nope'"))


class Restore(ApiTestCase):
    def test_a_bad_backup_changes_nothing_and_a_good_one_restores(self):
        """VI1, VP2: unpacked and checked in a staging folder first; streamed, not held in memory."""
        self.set_up(ADMIN)
        hh = self.vaults()["Household"]["id"]
        self.add_item(hh, title="Before")
        backup = self.backup()
        self.assertFalse([n for n in os.listdir(__import__("tempfile").gettempdir())
                          if n.startswith("household-vault-backup-") and n.endswith(".zip")])      # the temp zip went
        self.add_item(hh, title="After")
        buf = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(backup)) as src, zipfile.ZipFile(buf, "w") as out:
            for n in src.namelist():
                out.writestr(n, src.read(n))
            out.writestr(f"vaults/{hh}/999.kdbx", b"not a keepass file")
        files = sorted(os.listdir(storage.vault_dir(hh)))
        r = self.restore(buf.getvalue())
        self.assertEqual(r.status_code, 422)
        self.assertEqual(sorted(os.listdir(storage.vault_dir(hh))), files)
        self.assertFalse(os.path.exists(os.path.join(config.DATA_DIR, ".restore-staging")))
        self.assertEqual({i["title"] for i in self.ok(self.get(f"/api/vaults/{hh}/items"))["items"]}, {"Before", "After"})
        self.ok(self.restore(backup))
        self.assertFalse(os.path.exists(config.VAULT_DIR + ".before-restore"))
        self.unlock(ADMIN)
        self.assertEqual({i["title"] for i in self.ok(self.get(f"/api/vaults/{hh}/items"))["items"]}, {"Before"})


class People(ApiTestCase):
    def test_reset_owner_promotes_a_manager(self):
        """V8: a shared vault whose owner is reset keeps an owner."""
        self.set_up(ADMIN)
        self.set_up(NEHA)
        self.set_up(VIKRAM)
        s = self.ok(self.post("/api/vaults", {"name": "Trip", "passwordMode": "random"}, NEHA), 201)
        self.ok(self.post(f"/api/vaults/{s['id']}/members", {"userId": VIKRAM["id"], "role": "editor"}, NEHA))
        self.ok(self.post(f"/api/vaults/{s['id']}/members", {"userId": ADMIN["id"], "role": "manager"}, NEHA))
        self.ok(self.admin_post(f"/api/users/{NEHA['id']}/reset"))
        roles = {r["user_id"]: r["role"] for r in sql("SELECT user_id, role FROM vault_members WHERE vault_id = ?", (s["id"],))}
        self.assertEqual(roles, {ADMIN["id"]: "owner", VIKRAM["id"]: "editor"})
        self.assertEqual(sql("SELECT owner_user_id FROM vaults WHERE id = ?", (s["id"],))[0]["owner_user_id"], ADMIN["id"])
        # no manager: the longest-standing member
        t = self.ok(self.post("/api/vaults", {"name": "Car", "passwordMode": "random"}, VIKRAM), 201)
        self.ok(self.post(f"/api/vaults/{t['id']}/members", {"userId": ADMIN["id"], "role": "viewer"}, VIKRAM))
        self.ok(self.patch(f"/api/users/{VIKRAM['id']}", {"disabled": True}))
        self.assertEqual(sql("SELECT role FROM vault_members WHERE vault_id = ?", (t["id"],)), [{"role": "owner"}])

    def test_master_password_isnt_kept_in_memory(self):
        """V13: a Personal vault keeps only the composite key; the health facts hold no SHA-1."""
        self.set_up(ADMIN)
        pv = self.personal()["id"]
        ov = sessions.get_open(pv)
        self.assertIsNone(ov.password)
        self.assertEqual(ov.composite, kdbx.composite_key(PW["u-admin"]))
        self.assertTrue(sessions.get_open(self.vaults()["Household"]["id"]).password)     # random: kept (sealed to members)
        self.add_item(pv, title="Bank", password="hunter2-hunter2")
        with ov.lock:
            self.assertNotIn("sha1", health.facts(ov)[0])
        # the wrong password is still refused where it's checked
        self.assertEqual(self.post("/api/me/download-all", {"password": "nope"}).status_code, 403)
        self.assertEqual(self.post("/api/me/download-all", {"password": PW["u-admin"]}).status_code, 200)

    def test_breach_check_stops_at_lock(self):
        self.set_up(ADMIN)
        self.ok(self.put("/api/admin/settings", {"allow_breach_check": True}))
        self.ok(self.put("/api/me/settings", {"breachCheck": True}))
        self.add_item(self.personal()["id"], title="Bank", password="hunter2-hunter2")
        started = threading.Event()
        release = threading.Event()

        def slow(prefix):
            started.set()
            release.wait(10)
            return ""
        with mock.patch.object(health, "fetch_range", slow):
            self.ok(self.post("/api/password-health/breach-check"))
            self.assertTrue(started.wait(10))
            st = self.ok(self.get("/api/password-health/breach-status"))
            self.assertEqual(st["state"], "running")
            token = self.tokens[ADMIN["id"]]
            job = health._jobs[token]
            self.ok(self.post("/api/lock"))
            self.assertNotIn(token, health._jobs)
            self.assertTrue(job["cancel"])
            release.set()

    def test_reminder_titles_go_when_turned_off(self):
        """V11"""
        self.set_up(ADMIN)
        self.add_item(self.personal()["id"], title="Passport", expires="2030-01-01")
        self.assertEqual(sql("SELECT title FROM reminders"), [{"title": "Passport"}])
        self.ok(self.put("/api/admin/settings", {"reminder_titles": False}))
        self.assertEqual(sql("SELECT title FROM reminders"), [{"title": ""}])

    def test_session_caches_and_cap(self):
        """V16: per-session caches go with the session; at most 20 sessions a person."""
        from app.routers import items as items_router, quick
        self.set_up(ADMIN)
        it = self.add_item(self.personal()["id"], title="A", password="p")
        self.ok(self.get(f"/api/vaults/{self.personal()['id']}/items/{it['id']}/secret?field=Password"))
        self.ok(self.post("/api/me/quick-unlock/begin"))
        token = self.tokens[ADMIN["id"]]
        self.assertIn(token, items_router._secret_hits)
        self.assertIn(token, quick._pending)
        self.ok(self.post("/api/lock"))
        self.assertNotIn(token, items_router._secret_hits)
        self.assertNotIn(token, quick._pending)
        first = self.unlock(ADMIN)["session"]
        for _ in range(sessions.MAX_SESSIONS_PER_USER):
            self.unlock(ADMIN)
        self.assertEqual(len(sessions.user_sessions(ADMIN["id"])), sessions.MAX_SESSIONS_PER_USER)
        self.assertNotIn(first, sessions._sessions)

    def test_search_names_the_vault_for_whoever_searches(self):
        """VI6: the shared search index doesn't carry the first searcher's name for the vault."""
        self.set_up(ADMIN)
        self.set_up(NEHA)
        mine = self.personal()["id"]
        self.add_item(mine, title="Broker")
        with db.get_conn() as conn:
            service.add_member(conn, mine, NEHA["id"], "viewer", ADMIN["id"])
        self.ok(self.post(f"/api/vaults/{mine}/open", {"password": PW["u-admin"], "remember": False}, NEHA))
        self.assertEqual(self.ok(self.get("/api/search?q=broker"))["items"][0]["vaultName"], "Personal")
        self.assertEqual(self.ok(self.get("/api/search?q=broker", NEHA))["items"][0]["vaultName"], "Kiran's Personal")
        self.assertEqual(len(self.ok(self.get("/api/search?q=vault:kiran", NEHA))["items"]), 1)

    def test_locked_vault_names_in_the_header(self):
        """VI2: any name survives the X-Locked-Vaults header (percent-encoded UTF-8)."""
        import urllib.parse
        self.set_up(ADMIN)
        name = "Família 家; ok"
        self.ok(self.post("/api/vaults", {"name": name, "passwordMode": "chosen",
                                          "password": "orbit-velvet-harbor-maple-glacier-tulip"}), 201)
        vid = self.vaults()[name]["id"]
        self.ok(self.post(f"/api/vaults/{vid}/forget"))
        sessions.end_all()
        self.unlock(ADMIN)
        r = self.post("/api/me/download-all", {"password": PW["u-admin"]})
        self.assertEqual(r.status_code, 200)
        raw = r.headers["X-Locked-Vaults"]
        raw.encode("latin-1")
        self.assertEqual([urllib.parse.unquote(x) for x in raw.split(";")], [name])

    def test_vault_list_same_as_before(self):
        """VP6: the list built with three queries is the one the per-vault queries built."""
        from app.routers import me
        self.set_up(ADMIN)
        self.set_up(NEHA)
        self.set_up(KID)
        s = self.ok(self.post("/api/vaults", {"name": "Bills", "passwordMode": "random"}), 201)
        self.ok(self.post(f"/api/vaults/{s['id']}/members", {"userId": NEHA["id"], "role": "viewer"}))
        self.ok(self.post("/api/vaults", {"name": "Trip", "passwordMode": "random"}, NEHA), 201)
        self.ok(self.post("/api/vaults", {"name": "Zed", "passwordMode": "chosen",
                                          "password": "orbit-velvet-harbor-maple-glacier-tulip"}, NEHA), 201)
        for user in (ADMIN, NEHA, KID):
            s = next(x for x in sessions._sessions.values() if x.user_id == user["id"])
            with db.get_conn() as conn:
                rows = conn.execute("SELECT v.*, m.role FROM vaults v JOIN vault_members m ON m.vault_id = v.id AND "
                                    "m.user_id = ? WHERE NOT (v.kind = 'emergency' AND v.owner_user_id = ?) ORDER BY "
                                    "CASE v.kind WHEN 'personal' THEN 0 WHEN 'household' THEN 1 WHEN 'shared' THEN 2 "
                                    "ELSE 3 END, v.name COLLATE NOCASE", (user["id"], user["id"])).fetchall()
                old = [me._vault_out(conn, r, s, user["id"]) for r in rows]          # one vault at a time
                self.assertEqual(me._vault_list(conn, user["id"], s), old)

    def test_argon2_tuned_outside_the_transaction(self):
        """VP8: setting someone up doesn't measure Argon2 inside its transaction once the app is tuned."""
        service._tuned.clear()
        service.calibrate()
        with mock.patch.object(service, "_measure", side_effect=AssertionError("measured inside a change")):
            self.set_up(ADMIN)
        service._tuned.clear()


class Limits(ApiTestCase):
    def test_chunked_body_is_counted(self):
        """V6: a body without Content-Length is counted as it arrives."""
        self.set_up(ADMIN)

        def chunks():
            for _ in range(7):
                yield b"x" * (1024 * 1024)
        h = dict(headers(ADMIN), **{"Content-Type": "application/json"})
        r = self.client.post("/api/strength", content=chunks(), headers=h)
        self.assertEqual(r.status_code, 413)
        r = self.client.post("/api/strength", content=b"x" * 10, headers=dict(h, **{"Content-Length": str(6 << 20)}))
        self.assertEqual(r.status_code, 413)
        self.ok(self.post("/api/strength", {"password": "x" * 900}))

    def test_static_files_with_a_version_are_cached(self):
        r = self.client.get("/app.js?v=2.2.1", headers=headers(ADMIN))
        self.assertEqual(r.headers.get("cache-control"), "public, max-age=31536000, immutable")
        r = self.client.get("/", headers=headers(ADMIN))
        self.assertIn("no-store", r.headers.get("cache-control", ""))

    def test_packaging(self):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, "Dockerfile")) as f:
            docker = f.read()
        after_from = docker.split("FROM ", 1)[1]
        self.assertIn("ARG BUILD_ARCH", after_from)
        self.assertIn("ARG BUILD_VERSION", after_from)
        self.assertIn('"--no-access-log"', docker)
        with open(os.path.join(here, "config.yaml")) as f:
            self.assertIn("watchdog: http://[HOST]:[PORT:8101]/api/health", f.read())


class GuestWifiStaysInTheApp(ApiTestCase):
    """V9 (security review 2026-10): the guest Wi-Fi is never sent to Home Assistant — no sensor, no state."""

    def test_nothing_posted_to_home_assistant(self):
        from app import guest_wifi, ha_client
        self.set_up(ADMIN)
        hh = self.vaults()["Household"]["id"]
        wifi = self.add_item(hh, title="Guest", username="Guest-Net", password="sunny-day-42", template="wifi")
        calls = []
        with mock.patch.object(ha_client, "has_token", lambda: True), \
                mock.patch.object(ha_client, "request", lambda m, p, body=None, timeout=10: (calls.append((m, p)) or (200, b"{}"))):
            self.ok(self.put(f"/api/vaults/{hh}/items/{wifi['id']}/guest-wifi", {"security": "WPA", "showPassword": True}))
            self.ok(self.patch(f"/api/vaults/{hh}/items/{wifi['id']}", {"password": "rainy-night-7"}))
            self.ok(self.delete("/api/guest-wifi"))
        self.assertEqual([c for c in calls if "/states/" in c[1]], [])
        self.assertFalse(hasattr(guest_wifi, "push_blocking"))
        self.assertFalse(hasattr(guest_wifi, "ensure_blocking"))


class AdminRoutesNeedTheAdmin(ApiTestCase):
    """Security review 2026-10 (#2): everything on Home Assistant's origin can send requests with the admin's
    identity, so admin routes need the admin's unlocked session once they are set up, and the ones that hand
    out access or the data need their master password again — counted like an unlock."""

    def test_before_set_up_the_people_routes_work_without_a_session(self):
        self.seen(ADMIN)
        self.seen(NEHA)
        self.ok(self.get("/api/admin/users", token=False))
        one = self.ok(self.post(f"/api/users/{NEHA['id']}/setup", token=False))["oneTimePassword"]
        self.assertEqual(len(one.split("-")), 6)                       # six words now (#4)
        # but nothing that needs a master password: there is none yet
        self.assertEqual(self.post("/api/admin-storage-download-db", {}, token=False).status_code, 403)
        self.assertEqual(self.post(f"/api/users/{NEHA['id']}/reset", {}, token=False).status_code, 403)
        self.assertEqual(self.req("POST", "/api/admin-storage-import-db", token=False,
                                  files={"file": ("b.zip", b"PK junk", "application/zip")}).status_code, 403)

    def test_once_set_up_every_admin_route_needs_the_unlocked_session(self):
        self.set_up(ADMIN)
        self.seen(NEHA)
        self.ok(self.get("/api/admin/users"))
        for method, path in (("GET", "/api/admin/users"), ("GET", "/api/admin/settings"), ("PATCH", f"/api/users/{NEHA['id']}"),
                             ("POST", f"/api/users/{NEHA['id']}/setup"), ("POST", f"/api/users/{NEHA['id']}/unblock"),
                             ("POST", "/api/admin-storage-download-db")):
            r = self.req(method, path, token=False, json={"disabled": True} if method == "PATCH" else {})
            self.assertEqual(r.status_code, 401, (method, path, r.text))
        self.ok(self.post("/api/lock"))
        self.assertEqual(self.get("/api/admin/users").status_code, 401)
        # a GET can't fetch the backup any more (a link anyone on the origin could follow)
        self.unlock(ADMIN)
        self.assertIn(self.get("/api/admin-storage-download-db").status_code, (404, 405))

    def test_master_password_for_setup_reset_backup_and_restore(self):
        self.set_up(ADMIN)
        self.seen(NEHA)
        wrong = {"password": "not-it"}
        self.assertEqual(self.post(f"/api/users/{NEHA['id']}/setup", wrong).status_code, 403)
        self.assertEqual(self.post(f"/api/users/{NEHA['id']}/setup").status_code, 403)      # no password at all
        self.assertEqual(self.post("/api/admin-storage-download-db", wrong).status_code, 403)
        self.assertEqual(self.req("POST", "/api/admin-storage-import-db", files={"file": ("b.zip", b"PK junk", "application/zip")},
                                  data=wrong).status_code, 403)
        # every wrong one is counted like a wrong unlock (and blocks like one)
        self.assertEqual(sql("SELECT unlock_failures FROM users WHERE id = 'u-admin'")[0]["unlock_failures"], 4)
        self.ok(self.admin_post(f"/api/users/{NEHA['id']}/reset"))
        self.assertEqual(sql("SELECT unlock_failures FROM users WHERE id = 'u-admin'")[0]["unlock_failures"], 4)
        self.assertEqual(self.post(f"/api/users/{NEHA['id']}/reset", wrong).status_code, 403)
        self.assertEqual(self.post(f"/api/users/{NEHA['id']}/reset", wrong).status_code, 429)   # blocked now
        self.assertEqual(self.admin_post(f"/api/users/{NEHA['id']}/setup").status_code, 429)
        sql("UPDATE users SET unlock_failures = 0, unlock_blocked_until = NULL WHERE id = 'u-admin'")
        self.ok(self.admin_post(f"/api/users/{NEHA['id']}/setup"))
        self.assertTrue(self.backup().startswith(b"PK"))
        self.assertEqual(self.restore(b"PK junk").status_code, 422)             # the right password: checked next


class OneTimePasswordsExpire(ApiTestCase):
    """Security review 2026-10 (#4): six words, and a one-time password nobody used within 48 hours is void."""

    def test_unused_setup_expires_used_one_does_not(self):
        from app import service
        self.set_up(ADMIN)
        self.seen(NEHA)
        self.seen(VIKRAM)
        one = self.ok(self.admin_post(f"/api/users/{NEHA['id']}/setup"))["oneTimePassword"]
        self.ok(self.admin_post(f"/api/users/{VIKRAM['id']}/setup"))
        with db.get_conn() as conn:
            self.assertEqual(service.expire_setups(conn), 0)                 # fresh: nothing happens
        # Neha uses hers in time
        r = self.ok(self.post("/api/unlock", {"password": one}, NEHA, token=False))
        self.tokens[NEHA["id"]] = r["session"]
        self.ok(self.post("/api/me/activate", {"newPassword": PW[NEHA["id"]]}, NEHA))
        sql("UPDATE users SET setup_at = '2000-01-01T00:00:00.000Z' WHERE id IN ('u-neha', 'u-vikram')")
        with db.get_conn() as conn:
            self.assertEqual(service.expire_setups(conn), 1)
        self.assertEqual(sql("SELECT status FROM users WHERE id = 'u-vikram'")[0]["status"], "none")
        self.assertEqual(sql("SELECT status FROM users WHERE id = 'u-neha'")[0]["status"], "active")
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM vaults WHERE owner_user_id = 'u-vikram'")[0]["n"], 0)
        self.assertTrue(any(a["action"] == "setup_expired" for a in sql("SELECT action FROM audit_log WHERE user_id = 'u-vikram'")))
        # an older database: set up before the column existed → dated from the audit log
        self.ok(self.admin_post(f"/api/users/{VIKRAM['id']}/setup"))
        sql("UPDATE users SET setup_at = NULL WHERE id = 'u-vikram'")
        sql("UPDATE audit_log SET created_at = '2000-01-01T00:00:00.000Z' WHERE action = 'user_setup' AND user_id = 'u-vikram'")
        with db.get_conn() as conn:
            self.assertEqual(service.expire_setups(conn), 1)
        self.assertEqual(sql("SELECT status FROM users WHERE id = 'u-vikram'")[0]["status"], "none")


if __name__ == "__main__":
    unittest.main()
