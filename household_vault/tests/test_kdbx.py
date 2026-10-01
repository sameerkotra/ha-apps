"""The KeePass file format (app/kdbx.py) and pure-Python Argon2."""
import _env  # noqa: F401

import base64
import gzip
import hashlib
import os
import struct
import unittest
import xml.etree.ElementTree as ET

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id

from app import argon2py, kdbx

FAST = {"memory": 64 * 1024, "iterations": 3, "parallelism": 2}


class Argon2(unittest.TestCase):
    def test_rfc9106_vectors(self):
        args = (b"\x01" * 32, b"\x02" * 16, 3, 32, 4, 32)
        kw = {"secret": b"\x03" * 8, "ad": b"\x04" * 12}
        self.assertEqual(argon2py.argon2(*args, argon2py.TYPE_D, **kw).hex(),
                         "512b391b6f1162975371d30919734294f868e3be3984f3c1a13a4db9fabe4acb")
        self.assertEqual(argon2py.argon2(*args, argon2py.TYPE_ID, **kw).hex(),
                         "0d640df58d78766c08c037a34a8b53c9d01ef0452d75b65eb52520e96b01e659")

    def test_matches_cryptography(self):
        for t, m, p in ((1, 8, 1), (2, 64, 1), (3, 64, 2)):
            want = Argon2id(salt=b"s" * 16, length=32, iterations=t, lanes=p, memory_cost=m).derive(b"pw")
            self.assertEqual(argon2py.argon2(b"pw", b"s" * 16, t, m, p), want)

    def test_salsa20_estream_vector(self):
        s = kdbx._Salsa20(b"\x80" + b"\0" * 31, b"\0" * 8)
        self.assertEqual(s.update(b"\0" * 16).hex(), "e3be8fdd8beca2e3ea8ef9475b29a6e7")


def sample(name="Test"):
    d = kdbx.Database.create(name, FAST)
    bank = d.add_group(d.root_group, "Bank")
    e = d.add_entry(bank, {"Title": "SBI", "UserName": "kiran", "Password": "pässwörd 🔑", "URL": "https://x",
                           "otp": "otpauth://totp/x?secret=JBSWY3DPEHPK3PXP"}, {"otp"})
    return d, bank, e


class Format(unittest.TestCase):
    def test_round_trip_aes_and_chacha(self):
        for cipher in (kdbx.CIPHER_AES256, kdbx.CIPHER_CHACHA20):
            d, bank, e = sample()
            d.cipher = cipher
            raw = kdbx.save_bytes(d, "pw")
            self.assertTrue(kdbx.is_kdbx(raw))
            info = kdbx.header_info(raw)
            self.assertEqual((info["major"], info["cipher"], info["kdf"]["$UUID"]), (4, cipher, kdbx.KDF_ARGON2ID))
            d2 = kdbx.open_bytes(raw, "pw")
            e2 = d2.entries[e.findtext("UUID")]
            self.assertEqual(d2.get_field(e2, "Password"), "pässwörd 🔑")
            self.assertEqual(d2.protected_keys(e2), {"Password", "otp"})
            self.assertEqual(d2.group_path(d2.parent[e2]), ["Bank"])
            with self.assertRaises(kdbx.WrongKey):
                kdbx.open_bytes(raw, "nope")

    def test_protected_values_are_not_in_plain_xml(self):
        d, _b, _e = sample()
        d.kdf = kdbx.kdf_params(FAST)
        raw = kdbx.save_bytes(d, "pw")
        self.assertNotIn(b"kiran", raw)            # everything is encrypted and compressed

    def test_damaged_file(self):
        d, _b, _e = sample()
        raw = bytearray(kdbx.save_bytes(d, "pw"))
        raw[-40] ^= 1
        with self.assertRaises(kdbx.KdbxError):
            kdbx.open_bytes(bytes(raw), "pw")
        with self.assertRaises(kdbx.KdbxError):
            kdbx.open_bytes(b"not a keepass file at all", "pw")

    def test_same_salt_reuses_the_key_and_new_salt_changes_it(self):
        d, _b, _e = sample()
        r1 = kdbx.save_bytes(d, "pw")
        r2 = kdbx.save_bytes(d, "pw")
        self.assertEqual(kdbx.header_info(r1)["kdf"]["S"], kdbx.header_info(r2)["kdf"]["S"])
        self.assertNotEqual(r1[:200], r2[:200])                  # new master seed and IV each time
        r3 = kdbx.save_bytes(d, "other", new_salt=True)
        self.assertNotEqual(kdbx.header_info(r3)["kdf"]["S"], kdbx.header_info(r2)["kdf"]["S"])
        self.assertEqual(kdbx.open_bytes(r3, "other").name, "Test")

    def test_keeps_what_other_apps_wrote(self):
        d, bank, e = sample()
        weird = ET.SubElement(e, "SomethingFromKeePassXC", {"attr": "1"})
        weird.text = "keep me"
        ET.SubElement(d.root.find("Meta"), "CustomIcons").text = ""
        d.binaries.append((False, b"attachment bytes"))
        b = ET.Element("Binary")
        ET.SubElement(b, "Key").text = "scan.pdf"
        ET.SubElement(b, "Value", {"Ref": "0"})
        e.insert(len(list(e)) - 2, b)
        d2 = kdbx.open_bytes(kdbx.save_bytes(d, "pw"), "pw")
        e2 = d2.entries[e.findtext("UUID")]
        self.assertEqual(e2.findtext("SomethingFromKeePassXC"), "keep me")
        self.assertEqual(d2.binaries, [(False, b"attachment bytes")])
        # copying into another database carries the attachment
        other = kdbx.Database.create("Other", FAST)
        d2.copy_entry_into(other, e2, other.root_group)
        o2 = kdbx.open_bytes(kdbx.save_bytes(other, "x"), "x")
        self.assertEqual(o2.binaries, [(False, b"attachment bytes")])

    def test_history_and_deleted_objects(self):
        d, bank, e = sample()
        for i in range(15):
            d.update_entry(e, {"Password": f"p{i}"})
        self.assertEqual(len(d.history(e)), 10)                    # HistoryMaxItems
        self.assertEqual(d.get_field(d.history(e)[-1], "Password"), "p13")
        uid = e.findtext("UUID")
        d.delete_permanently(bank)
        self.assertNotIn(uid, d.entries)
        gone = [x.findtext("UUID") for x in d.root.iter("DeletedObject")]
        self.assertIn(uid, gone)

    def test_keyfile(self):
        d, _b, _e = sample()
        kf = b'<?xml version="1.0"?><KeyFile><Meta><Version>2.0</Version></Meta><Key><Data>' + \
             b"0123456789ABCDEF0123456789ABCDEF0123456789ABCDEF0123456789ABCDEF</Data></Key></KeyFile>"
        raw = kdbx.save_bytes(d, "pw", keyfile=kf)
        self.assertEqual(kdbx.open_bytes(raw, "pw", kf).name, "Test")
        with self.assertRaises(kdbx.WrongKey):
            kdbx.open_bytes(raw, "pw")
        self.assertEqual(kdbx.keyfile_hash(b"x" * 32), b"x" * 32)
        self.assertEqual(kdbx.keyfile_hash(b"a" * 64), bytes.fromhex("a" * 64))
        self.assertEqual(kdbx.keyfile_hash(b"random file"), hashlib.sha256(b"random file").digest())

    def test_argon2d_file(self):
        d, _b, _e = sample()
        params = kdbx.kdf_params(FAST)
        params["$UUID"] = kdbx.KDF_ARGON2D
        raw = kdbx.save_bytes(d, "pw", kdf=params)
        self.assertEqual(kdbx.header_info(raw)["kdf"]["$UUID"], kdbx.KDF_ARGON2D)
        self.assertEqual(kdbx.open_bytes(raw, "pw").name, "Test")

    def test_times(self):
        from datetime import datetime, timezone
        t = datetime(2024, 5, 6, 7, 8, 9, tzinfo=timezone.utc)
        self.assertEqual(kdbx.parse_time(kdbx.fmt_time(t)), t)
        self.assertEqual(kdbx.parse_time("2024-05-06T07:08:09Z"), t)


def write_kdbx3(xml_root, password: str, rounds: int = 100) -> bytes:
    """A minimal KDBX 3.1 writer (tests only): AES-KDF, AES-256-CBC, hashed blocks, Salsa20 inner stream."""
    seed, tseed, iv, pskey, start = (os.urandom(32), os.urandom(32), os.urandom(16), os.urandom(32), os.urandom(32))
    stream = kdbx._Salsa20(hashlib.sha256(pskey).digest(), kdbx.SALSA20_IV)
    for el in xml_root.iter("Value"):
        if el.get("Protected") == "True":
            el.text = base64.b64encode(stream.update((el.text or "").encode())).decode()
    body = gzip.compress(ET.tostring(xml_root))
    blocks = struct.pack("<I", 0) + hashlib.sha256(body).digest() + struct.pack("<i", len(body)) + body
    blocks += struct.pack("<I", 1) + b"\0" * 32 + struct.pack("<i", 0)
    plain = start + blocks

    def f(fid, v):
        return bytes([fid]) + struct.pack("<H", len(v)) + v
    header = (struct.pack("<IIHH", kdbx.SIG1, kdbx.SIG2, 1, 3) + f(2, kdbx.CIPHER_AES256) + f(3, struct.pack("<I", 1))
              + f(4, seed) + f(5, tseed) + f(6, struct.pack("<Q", rounds)) + f(7, iv) + f(8, pskey) + f(9, start)
              + f(10, struct.pack("<I", 2)) + f(0, b"\r\n\r\n"))
    transformed = kdbx.aes_kdf(kdbx.composite_key(password), tseed, rounds)
    key = hashlib.sha256(seed + transformed).digest()
    p = padding.PKCS7(128).padder()
    enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return header + enc.update(p.update(plain) + p.finalize()) + enc.finalize()


class Kdbx3Import(unittest.TestCase):
    def test_reads_kdbx3_and_upgrades(self):
        root = ET.fromstring(
            '<KeePassFile><Meta><DatabaseName>Old</DatabaseName><Binaries><Binary ID="0" Compressed="True">'
            + base64.b64encode(gzip.compress(b"att")).decode() + '</Binary></Binaries></Meta><Root><Group>'
            '<UUID>' + base64.b64encode(b"g" * 16).decode() + '</UUID><Name>Old</Name>'
            '<Times><CreationTime>2015-01-02T03:04:05Z</CreationTime></Times>'
            '<Entry><UUID>' + base64.b64encode(b"e" * 16).decode() + '</UUID>'
            '<String><Key>Title</Key><Value>Mail</Value></String>'
            '<String><Key>Password</Key><Value Protected="True">hunter2</Value></String>'
            '<Binary><Key>a.txt</Key><Value Ref="0"/></Binary>'
            '<History><Entry><UUID>' + base64.b64encode(b"e" * 16).decode() + '</UUID>'
            '<String><Key>Password</Key><Value Protected="True">older</Value></String></Entry></History>'
            '</Entry></Group></Root></KeePassFile>')
        raw = write_kdbx3(root, "pw")
        d = kdbx.open_bytes(raw, "pw")
        self.assertEqual(d.source_version, (3, 1))
        e = next(iter(d.entries.values()))
        self.assertEqual(d.get_field(e, "Password"), "hunter2")
        self.assertEqual(d.get_field(d.history(e)[0], "Password"), "older")
        self.assertEqual(d.binaries, [(False, b"att")])
        self.assertEqual(kdbx.parse_time(d.root_group.findtext("Times/CreationTime")).year, 2015)
        with self.assertRaises(kdbx.WrongKey):
            kdbx.open_bytes(raw, "wrong")
        up = kdbx.open_bytes(kdbx.save_bytes(d, "pw", kdf=kdbx.kdf_params(FAST)), "pw")
        self.assertEqual(kdbx.header_info(kdbx.save_bytes(up, "pw"))["major"], 4)
        self.assertEqual(up.binaries, [(False, b"att")])


if __name__ == "__main__":
    unittest.main()
