"""Quick unlock with a passkey — the server side (SPEC §12.5).
The browser part (WebAuthn PRF + WebCrypto) is exercised by the UI smoke run."""
from base import ADMIN, PW, ApiTestCase, sql

WRAPPED = {"iv": "AAAAAAAAAAAAAAAA", "ct": "c2VjcmV0LWNpcGhlcnRleHQ"}


class QuickUnlock(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.set_up(ADMIN)
        self.unlock(ADMIN)            # a typed unlock (the first one was the one-time password)

    def register(self, **over):
        b = self.ok(self.post("/api/me/quick-unlock/begin"))
        self.assertEqual(len(b["prfSalt"]), 43)
        body = dict({"credentialId": "cred-abcdefgh", "label": "Kiran's phone", "wrapped": WRAPPED,
                     "password": PW[ADMIN["id"]]}, **over)
        return self.post("/api/me/quick-unlock", body), b

    def test_register_needs_the_master_password_and_a_fresh_begin(self):
        r, _ = self.register(password="wrong")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.post("/api/me/quick-unlock", {"credentialId": "cred-abcdefgh", "label": "x", "wrapped": WRAPPED,
                                                            "password": PW[ADMIN["id"]]}).status_code, 409)
        r, b = self.register()
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual([d["label"] for d in r.json()["devices"]], ["Kiran's phone"])
        opts = self.ok(self.get("/api/quick-unlock/options", token=False))
        self.assertTrue(opts["available"])
        c = opts["credentials"][0]
        self.assertEqual((c["credentialId"], c["prfSalt"], c["wrapped"]), ("cred-abcdefgh", b["prfSalt"], WRAPPED))
        # nothing that opens a vault is stored
        row = sql("SELECT * FROM quick_unlock")[0]
        self.assertNotIn(PW[ADMIN["id"]], str(row))

    def test_unlock_counts_failures_and_drops_the_device(self):
        self.register()
        qid = self.ok(self.get("/api/me/quick-unlock"))["devices"][0]["id"]
        r = self.ok(self.post("/api/unlock", {"password": PW[ADMIN["id"]], "quickUnlockId": qid}, token=False))
        self.assertTrue(r["session"])
        self.assertTrue(sql("SELECT last_used FROM quick_unlock")[0]["last_used"])
        for i in range(5):
            self.assertEqual(self.post("/api/unlock", {"password": "wrong", "quickUnlockId": qid}, token=False).status_code,
                             403)
            sql("UPDATE users SET unlock_blocked_until = NULL")
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM quick_unlock")[0]["n"], 0)
        self.assertEqual(self.post("/api/unlock", {"password": PW[ADMIN["id"]], "quickUnlockId": qid}, token=False).status_code, 409)

    def test_fourteen_days_and_master_change(self):
        self.register()
        qid = self.ok(self.get("/api/me/quick-unlock"))["devices"][0]["id"]
        sql("UPDATE users SET last_typed_unlock = '2020-01-01T00:00:00.000Z'")
        opts = self.ok(self.get("/api/quick-unlock/options", token=False))
        self.assertFalse(opts["available"])
        self.assertIn("14 days", opts["reason"])
        self.assertEqual(self.post("/api/unlock", {"password": PW[ADMIN["id"]], "quickUnlockId": qid}, token=False).status_code, 409)
        self.unlock(ADMIN)                                  # typing it again renews the 14 days
        self.assertTrue(self.ok(self.get("/api/quick-unlock/options", token=False))["available"])
        self.ok(self.post("/api/me/password", {"current": PW[ADMIN["id"]], "new": "orchid-canyon-marble-ferry-quilt"}))
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM quick_unlock")[0]["n"], 0)
        self.assertFalse(self.ok(self.get("/api/quick-unlock/options", token=False))["available"])

    def test_remove(self):
        self.register()
        qid = self.ok(self.get("/api/me/quick-unlock"))["devices"][0]["id"]
        self.assertEqual(self.ok(self.delete(f"/api/me/quick-unlock/{qid}"))["devices"], [])
        self.assertEqual(self.delete(f"/api/me/quick-unlock/{qid}").status_code, 404)
