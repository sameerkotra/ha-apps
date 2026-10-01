"""Smaller features: missing 2FA, tags, recently used across sessions, 2FA import duplicates, lock-when-hidden."""
from base import ADMIN, ApiTestCase, sql

from app import health


class Extras(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.set_up(ADMIN)
        self.pv = self.personal()["id"]

    def test_missing_2fa(self):
        self.assertTrue(health.offers_2fa("github.com") and health.offers_2fa("www.github.com")
                        and health.offers_2fa("accounts.google.com") and health.offers_2fa("kite.zerodha.com"))
        self.assertFalse(health.offers_2fa("notgithub.com") or health.offers_2fa("example.org") or health.offers_2fa(""))
        a = self.add_item(self.pv, title="GitHub", url="https://github.com/login", password="Very-long-unique-pass-1!")
        self.add_item(self.pv, title="GitLab", url="gitlab.com", password="Very-long-unique-pass-2!", totp="JBSWY3DPEHPK3PXP")
        self.add_item(self.pv, title="Local shop", url="shop.example.org", password="Very-long-unique-pass-3!")
        r = self.ok(self.get("/api/password-health"))
        self.assertEqual(r["counts"]["no2fa"], 1)
        self.assertEqual([(x["title"], x["issues"]) for x in r["items"]], [("GitHub", ["no2fa"])])
        found = self.ok(self.get("/api/search?q=is:no2fa"))["items"]
        self.assertEqual([x["id"] for x in found], [a["id"]])

    def test_tags_list_and_filter(self):
        self.add_item(self.pv, title="A", tags=["Bank", "family"])
        self.add_item(self.pv, title="B", tags=["bank"], favourite=True)
        self.add_item(self.vaults()["Household"]["id"], title="C", tags=["Family"])
        tags = self.ok(self.get("/api/tags"))["tags"]
        self.assertEqual([(t["tag"].lower(), t["count"]) for t in tags], [("bank", 2), ("family", 2)])
        got = self.ok(self.get("/api/items?tag=FAMILY"))["items"]
        self.assertEqual(sorted(x["title"] for x in got), ["A", "C"])

    def test_recently_used_survives_locking(self):
        a = self.add_item(self.pv, title="First")
        b = self.add_item(self.pv, title="Second")
        self.ok(self.get(f"/api/vaults/{self.pv}/items/{a['id']}"))
        self.ok(self.get(f"/api/vaults/{self.pv}/items/{b['id']}"))
        self.ok(self.post("/api/lock"))
        self.unlock(ADMIN)
        got = self.ok(self.get("/api/items?recent=true"))["items"]
        self.assertEqual([x["title"] for x in got], ["Second", "First"])
        rows = sql("SELECT * FROM recent_items")
        self.assertEqual(set(rows[0]), {"user_id", "vault_id", "item_id", "used_at"})    # ids only, no titles

    def test_authenticator_import_marks_codes_you_have(self):
        self.add_item(self.pv, title="GitHub", totp="otpauth://totp/GitHub:me?secret=JBSWY3DPEHPK3PXP&issuer=GitHub")
        r = self.ok(self.post("/api/totp/parse", {"text": "otpauth://totp/GitHub:me?secret=JBSWY3DPEHPK3PXP&issuer=GitHub"}))
        self.assertEqual(r["existing"], "GitHub (Personal)")
        r = self.ok(self.post("/api/totp/parse", {"text": "otpauth://totp/Other:me?secret=KRSXG5CTMVRXEZLU"}))
        self.assertIsNone(r["existing"])
