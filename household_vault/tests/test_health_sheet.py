"""Download reminder, password health, breach check, 2FA export codes,
the household sheet (SPEC §12.1–§12.3, §12.6)."""
from base import ADMIN, NEHA, ApiTestCase, sql
import _env  # noqa: F401

import base64
import datetime
import hashlib
import json
import sqlite3
import time
import unittest
import urllib.parse
from unittest import mock

from app import config, db, health, items, kdbx, sessions, totp

STRONG = "quartz-lantern-meadow-violet-harbor-tulip"
WEAK = "password1"


def pwned_body(hit_sha1: str, count: int) -> str:
    """A padded range response: the real suffix plus padding lines with count 0."""
    lines = [f"{hit_sha1[5:]}:{count}"] + [f"{'0' * 30}{i:05X}:0" for i in range(5)]
    return "\r\n".join(lines)


class Reminder(ApiTestCase):
    def test_last_download_and_reminder_setting(self):
        self.set_up(ADMIN)
        me = self.ok(self.get("/api/me"))
        self.assertEqual((me["downloadReminderDays"], me["lastDownloadAll"]), (0, None))
        self.assertEqual(self.put("/api/me/settings", {"downloadReminderDays": 45}).status_code, 422)
        self.ok(self.put("/api/me/settings", {"downloadReminderDays": 60}))
        r = self.post("/api/me/download-all", {"password": "maple-river-candle-orbit-zebra", "includeDeleted": False})
        self.assertEqual(r.status_code, 200)
        me = self.ok(self.get("/api/me"))
        self.assertEqual(me["downloadReminderDays"], 60)
        self.assertIsNotNone(me["lastDownloadAll"])
        self.assertIsNone(me["lastMasterChange"])
        self.ok(self.post("/api/me/password", {"current": "maple-river-candle-orbit-zebra", "new": "velvet-harbor-cactus-glacier-violin"}))
        me = self.ok(self.get("/api/me"))
        self.assertGreater(me["lastMasterChange"], me["lastDownloadAll"])   # the downloaded copy is out of date

    def test_migration_adds_columns(self):
        """An older database gets the missing columns on start."""
        conn = sqlite3.connect(config.DB_PATH)
        conn.execute("ALTER TABLE users DROP COLUMN breach_check")
        conn.execute("ALTER TABLE users DROP COLUMN download_reminder_days")
        conn.commit()
        conn.close()
        db.init_db()
        cols = {r["name"] for r in sql("PRAGMA table_info(users)")}
        self.assertTrue({"breach_check", "download_reminder_days"} <= cols)


class Health(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.set_up(ADMIN)
        self.vid = self.personal()["id"]
        self.hh = self.vaults()["Household"]["id"]

    def test_weak_reused_old(self):
        a = self.add_item(self.vid, title="Bank", password=WEAK)
        self.add_item(self.hh, title="Shop", password=STRONG)
        c = self.add_item(self.vid, title="Mail", password=STRONG)
        self.add_item(self.vid, title="Fine", password=STRONG + "-x")
        self.add_item(self.vid, title="A note", type="note", notes="no password")
        d = self.add_item(self.vid, title="Ancient", password="zebra-orbit-candle-maple-river-quilt")
        # make "Ancient" two years old (created and last changed then)
        ov = sessions.get_open(self.vid)
        with ov.lock:
            e = items.find_entry(ov.db, d["id"])
            old = kdbx.fmt_time(datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=800))
            e.find("Times/LastModificationTime").text = old
            e.find("Times/CreationTime").text = old
            ov.changed()
        r = self.ok(self.get("/api/password-health"))
        self.assertEqual(r["total"], 5)
        byt = {i["title"]: i for i in r["items"]}
        self.assertEqual(byt["Bank"]["issues"], ["weak"])
        self.assertEqual(byt["Shop"]["issues"], ["reused"])
        self.assertEqual(byt["Mail"]["reuseGroup"], byt["Shop"]["reuseGroup"])
        self.assertEqual(byt["Ancient"]["issues"], ["old"])
        self.assertGreater(byt["Ancient"]["ageDays"], 700)
        self.assertNotIn("Fine", byt)
        self.assertEqual(r["counts"], {"breached": 0, "reused": 2, "weak": 1, "old": 1, "no2fa": 0})
        self.assertEqual(len(r["reuseGroups"]), 1)
        # never a password, hash or secret in the payload
        text = json.dumps(r)
        for secret in (WEAK, STRONG, hashlib.sha1(STRONG.encode()).hexdigest().upper()[:10]):
            self.assertNotIn(secret, text)
        # a password change clears "reused"
        self.ok(self.patch(f"/api/vaults/{self.vid}/items/{c['id']}", {"password": "new-" + STRONG}))
        r = self.ok(self.get("/api/password-health"))
        self.assertEqual(r["counts"]["reused"], 0)
        # search filters
        found = self.ok(self.get("/api/search", params={"q": "is:weak"}))["items"]
        self.assertEqual([i["title"] for i in found], ["Bank"])
        found = self.ok(self.get("/api/search", params={"q": "is:old"}))["items"]
        self.assertEqual([i["title"] for i in found], ["Ancient"])
        self.assertEqual(a["title"], "Bank")

    def test_old_counts_from_last_password_change(self):
        d = self.add_item(self.vid, title="Router", password="zebra-orbit-candle-maple-river-quilt")
        ov = sessions.get_open(self.vid)
        # a title change keeps the password's age; history holds the older version
        self.ok(self.patch(f"/api/vaults/{self.vid}/items/{d['id']}", {"title": "Router admin"}))
        with ov.lock:
            e = items.find_entry(ov.db, d["id"])
            long_ago = kdbx.fmt_time(datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=500))
            for h in ov.db.history(e):
                h.find("Times/LastModificationTime").text = long_ago
            e.find("Times/CreationTime").text = long_ago
            ov.changed()
        r = self.ok(self.get("/api/password-health"))
        self.assertEqual([i["issues"] for i in r["items"] if i["title"] == "Router admin"], [["old"]])

    def test_breach_check_switches_and_k_anonymity(self):
        self.add_item(self.vid, title="Bank", password=WEAK)
        self.add_item(self.vid, title="Safe", password=STRONG)
        # off by default: the admin must allow it, then the person turns it on
        self.assertEqual(self.post("/api/password-health/breach-check").status_code, 403)
        self.assertEqual(self.put("/api/me/settings", {"breachCheck": True}).status_code, 403)
        self.ok(self.put("/api/admin/settings", {"allow_breach_check": True}))
        self.assertEqual(self.post("/api/password-health/breach-check").status_code, 403)
        me = self.ok(self.put("/api/me/settings", {"breachCheck": True}))
        self.assertTrue(me["breachCheck"])
        weak_sha = hashlib.sha1(WEAK.encode()).hexdigest().upper()
        seen = []

        def fake_fetch(prefix):
            seen.append(prefix)
            return pwned_body(weak_sha, 2413922) if weak_sha.startswith(prefix) else pwned_body("F" * 40, 3)
        with mock.patch.object(health, "fetch_range", side_effect=fake_fetch):
            self.ok(self.post("/api/password-health/breach-check"))
            for _ in range(100):
                st = self.ok(self.get("/api/password-health"))
                if st["breach"]["state"] == "done":
                    break
                time.sleep(0.05)
        self.assertEqual(st["breach"]["state"], "done")
        self.assertTrue(all(len(p) == 5 for p in seen))          # only 5-character prefixes leave the app
        self.assertEqual(len(seen), 2)
        byt = {i["title"]: i for i in st["items"]}
        self.assertEqual(byt["Bank"]["issues"], ["breached", "weak"])
        self.assertEqual(byt["Bank"]["breachCount"], 2413922)
        self.assertNotIn("Safe", byt)
        self.assertEqual(st["counts"]["breached"], 1)
        self.assertEqual(st["breachChecked"], 2)
        found = self.ok(self.get("/api/search", params={"q": "is:breached"}))["items"]
        self.assertEqual([i["title"] for i in found], ["Bank"])
        # results live with the session: after locking they're gone
        self.ok(self.post("/api/lock"))
        self.unlock(ADMIN)
        st = self.ok(self.get("/api/password-health"))
        self.assertEqual((st["breach"]["state"], st["counts"]["breached"]), ("idle", 0))

    def test_breach_network_error_and_single_check(self):
        self.ok(self.put("/api/admin/settings", {"allow_breach_check": True}))
        self.ok(self.put("/api/me/settings", {"breachCheck": True}))
        self.add_item(self.vid, title="Bank", password=WEAK)
        import urllib.error
        with mock.patch.object(health, "fetch_range", side_effect=urllib.error.URLError("offline")):
            self.ok(self.post("/api/password-health/breach-check"))
            for _ in range(100):
                st = self.ok(self.get("/api/password-health"))
                if st["breach"]["state"] == "done":
                    break
                time.sleep(0.05)
            self.assertEqual(st["breach"]["errors"], 1)
            self.assertEqual(st["counts"]["breached"], 0)
            self.assertEqual(self.ok(self.post("/api/breach/check", {"password": "x"}))["count"], None)
        sha = hashlib.sha1(b"hunter2").hexdigest().upper()
        with mock.patch.object(health, "fetch_range", return_value=pwned_body(sha, 17)) as f:
            self.assertEqual(self.ok(self.post("/api/breach/check", {"password": "hunter2"}))["count"], 17)
            f.assert_called_once_with(sha[:5])
        with mock.patch.object(health, "fetch_range", return_value=pwned_body("A" * 40, 5)):
            self.assertEqual(self.ok(self.post("/api/breach/check", {"password": "hunter2"}))["count"], 0)

    def test_offline_gives_up_early(self):
        import urllib.error
        hashes = {f"{i:05X}" + "0" * 35 for i in range(10)}
        with mock.patch.object(health, "fetch_range", side_effect=urllib.error.URLError("offline")) as f:
            out = health.check_hashes(hashes)
        self.assertEqual(f.call_count, health.GIVE_UP_AFTER)
        self.assertEqual(set(out.values()), {None})
        self.assertEqual(len(out), 10)

    def test_fetch_range_request(self):
        """The real request: padded, prefix only, a plain user agent."""
        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b"ABC:1\r\n"
        with mock.patch("urllib.request.urlopen", return_value=Resp()) as u:
            self.assertEqual(health.fetch_range("5BAA6"), "ABC:1\r\n")
        req = u.call_args[0][0]
        self.assertEqual(req.full_url, "https://api.pwnedpasswords.com/range/5BAA6")
        self.assertEqual(req.get_header("Add-padding"), "true")


class TotpScan(ApiTestCase):
    # Google's documented sample export (one account, secret "Hello!\xde\xad\xbe\xef")
    SAMPLE = ("otpauth-migration://offline?data=CjEKCkhlbGxvId6tvu8SGEV4YW1wbGU6YWxpY2VAZ29vZ2xlLmNvbRoHRXhhbXBsZSAB"
              "KAEwAhABGAEgACjr4JKkBg%3D%3D")

    def setUp(self):
        super().setUp()
        self.set_up(ADMIN)

    def test_sample_export(self):
        r = self.ok(self.post("/api/totp/parse", {"text": self.SAMPLE}))
        self.assertEqual(r["kind"], "migration")
        self.assertEqual(r["batch"], [1, 1])
        e = r["entries"][0]
        self.assertEqual((e["issuer"], e["account"], e["ok"]), ("Example", "alice@google.com", True))
        self.assertEqual(totp.parse(e["uri"])["secret"], "JBSWY3DPEHPK3PXP")

    def test_plus_signs_survive(self):
        for pad in range(3):                        # line the secret up so its bytes encode to '+' in base64
            secret = b"\xfb\xef\xbe" * 3
            inner = b"\x12" + bytes([4 + pad]) + b"bob+" + b"x" * pad + b"\x0a" + bytes([len(secret)]) + secret + b"\x30\x02"
            payload = b"\x0a" + bytes([len(inner)]) + inner
            raw = base64.b64encode(payload).decode()
            if "+" in raw:
                break
        self.assertIn("+", raw)
        r = totp.describe("otpauth-migration://offline?data=" + raw)          # unencoded '+' (some scanners)
        self.assertTrue(r["entries"][0]["account"].startswith("bob+"))
        r2 = totp.describe("otpauth-migration://offline?data=" + urllib.parse.quote(raw, safe=""))
        self.assertEqual(r2["entries"][0]["uri"], r["entries"][0]["uri"])
        self.assertEqual(totp.parse(r["entries"][0]["uri"])["secret"], base64.b32encode(secret).decode().rstrip("="))

    def test_other_texts(self):
        r = self.ok(self.post("/api/totp/parse", {"text": "otpauth://totp/GitHub:kiran?secret=JBSWY3DPEHPK3PXP&issuer=GitHub"}))
        self.assertEqual((r["kind"], r["issuer"], r["account"]), ("totp", "GitHub", "kiran"))
        r = self.ok(self.post("/api/totp/parse", {"text": "jbsw y3dp ehpk 3pxp"}))
        self.assertEqual(r["kind"], "totp")
        for bad in ("otpauth://hotp/x?secret=JBSWY3DPEHPK3PXP&counter=1", "https://example.com",
                    "WIFI:T:WPA;S:x;P:y;;", "otpauth-migration://offline?data=AAAA", "not base32!"):
            self.assertEqual(self.post("/api/totp/parse", {"text": bad}).status_code, 422, bad)


class Sheet(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.set_up(ADMIN)
        self.set_up(NEHA)
        self.hh = self.vaults()["Household"]["id"]

    def test_mark_print_and_never_printed(self):
        wifi = self.add_item(self.hh, title="Home Wi-Fi", username="Sharma 5G", password="correct;horse:battery",
                             fields=[{"name": "Router admin", "value": "admin-pw", "protected": True}])
        card = self.add_item(self.hh, title="Joint card", type="card", cardholder="K Sharma",
                             card={"number": "4111111111111111", "cvv": "987", "pin": "4321"}, expiry="12/29")
        self.add_item(self.hh, title="Not on the sheet", password="x")
        self.assertEqual(sorted(wifi["sheetFields"]), ["Password", "Router admin", "UserName"])
        self.assertEqual(sorted(card["sheetFields"]), ["Cardholder", "Expiry"])
        self.assertEqual(wifi["sheet"], {"include": False, "fields": [], "wifi": None})
        base = f"/api/vaults/{self.hh}/items"
        for bad in (["Number"], ["CVV"], ["PIN"], ["otp"], ["Nope"]):
            self.assertEqual(self.put(f"{base}/{card['id']}/sheet", {"include": True, "fields": bad}).status_code, 422, bad)
        self.ok(self.put(f"{base}/{wifi['id']}/sheet", {"include": True, "fields": ["UserName", "Password", "Router admin"], "wifi": "WPA"}))
        self.ok(self.put(f"{base}/{card['id']}/sheet", {"include": True, "fields": ["Cardholder", "Expiry"]}))
        d = self.ok(self.get(f"{base}/{wifi['id']}"))
        self.assertEqual(d["sheet"], {"include": True, "fields": ["UserName", "Password", "Router admin"], "wifi": "WPA"})
        # Neha (another Household member) prints it
        s = self.ok(self.get(f"/api/vaults/{self.hh}/sheet", NEHA))
        self.assertEqual([i["title"] for i in s["items"]], ["Home Wi-Fi", "Joint card"])
        w = s["items"][0]
        self.assertEqual(w["wifi"], {"ssid": "Sharma 5G", "password": "correct;horse:battery", "security": "WPA"})
        self.assertIn({"name": "Router admin", "value": "admin-pw"}, w["fields"])
        text = json.dumps(s)
        for never in ("4111111111111111", "987", "4321"):
            self.assertNotIn(never, text)
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM audit_log WHERE action = 'printed_sheet'")[0]["n"], 1)
        # stored inside the encrypted file (KeePass CustomData), so it travels with downloads
        ov = sessions.get_open(self.hh)
        with ov.lock:
            cd = ov.db.custom_data(items.find_entry(ov.db, wifi["id"]))
        self.assertEqual(cd[items.SHEET_KEY], "1")
        # taking it off
        self.ok(self.put(f"{base}/{wifi['id']}/sheet", {"include": False}))
        self.assertEqual([i["title"] for i in self.ok(self.get(f"/api/vaults/{self.hh}/sheet"))["items"]], ["Joint card"])

    def test_which_vaults(self):
        mine = self.personal()["id"]
        it = self.add_item(mine, title="Locker", password="1234-5678")
        self.ok(self.put(f"/api/vaults/{mine}/items/{it['id']}/sheet", {"include": True, "fields": ["Password"]}))
        self.assertEqual(self.ok(self.get(f"/api/vaults/{mine}/sheet"))["vault"], "Personal")
        # a shared vault: no sheet
        v = self.ok(self.post("/api/vaults", {"name": "Finance"}), 201)
        it2 = self.add_item(v["id"], title="Broker", password="p")
        self.assertEqual(self.put(f"/api/vaults/{v['id']}/items/{it2['id']}/sheet", {"include": True}).status_code, 409)
        self.assertEqual(self.get(f"/api/vaults/{v['id']}/sheet").status_code, 409)
        # someone else's Personal vault shared with me: not mine to print
        self.ok(self.post(f"/api/vaults/{mine}/members", {"userId": "u-neha", "role": "viewer"}))
        self.ok(self.post(f"/api/vaults/{mine}/open", {"password": "maple-river-candle-orbit-zebra", "remember": False}, NEHA))
        self.assertEqual(self.get(f"/api/vaults/{mine}/sheet", NEHA).status_code, 409)
        # Wi-Fi needs a password unless it's an open network
        it3 = self.add_item(mine, title="Guest Wi-Fi", username="Guest")
        self.assertEqual(self.put(f"/api/vaults/{mine}/items/{it3['id']}/sheet", {"include": True, "wifi": "WPA"}).status_code, 422)
        self.ok(self.put(f"/api/vaults/{mine}/items/{it3['id']}/sheet", {"include": True, "wifi": "nopass"}))


if __name__ == "__main__":
    unittest.main()
