"""People, setup, unlocking, sessions (SPEC §4)."""
from base import ADMIN, NEHA, PW, VIKRAM, ApiTestCase, sql
import _env  # noqa: F401

import unittest

from app import ha_client, sessions


class Users(ApiTestCase):
    def test_new_people_are_disabled(self):
        self.seen(NEHA)
        self.assertEqual(self.ok(self.get("/api/me", NEHA, token=False))["disabled"], True)
        self.assertEqual(self.post("/api/unlock", {"password": "x"}, NEHA).status_code, 403)
        w = self.ok(self.get("/api/whoami", NEHA))
        self.assertEqual((w["disabled"], w["status"]), (True, "none"))
        ha_client.sync_users([{"user_id": "u-new", "name": "New", "entity_id": "person.new"}])
        self.assertEqual(sql("SELECT disabled, status FROM users WHERE id = 'u-new'"), [{"disabled": 1, "status": "none"}])

    def test_admin_setup_and_first_unlock(self):
        self.seen(NEHA)
        self.assertEqual(self.post("/api/users/u-neha/setup", user=NEHA).status_code, 403)   # not an admin
        one = self.ok(self.post("/api/users/u-neha/setup"))["oneTimePassword"]
        self.assertEqual(len(one.split("-")), 5)
        self.assertEqual(self.post("/api/users/u-neha/setup").status_code, 409)              # only once
        users = {u["id"]: u for u in self.ok(self.get("/api/admin/users"))["users"]}
        self.assertEqual((users["u-neha"]["status"], users["u-neha"]["disabled"]), ("temporary", False))
        r = self.ok(self.post("/api/unlock", {"password": one}, NEHA))
        self.assertTrue(r["mustChangePassword"])
        self.tokens["u-neha"] = r["session"]
        # nothing else works until she chooses her own
        self.assertEqual(self.get("/api/vaults", NEHA).status_code, 409)
        for bad in ("short", one, "password1234567"):
            self.assertEqual(self.post("/api/me/activate", {"newPassword": bad}, NEHA).status_code, 422, bad)
        self.ok(self.post("/api/me/activate", {"newPassword": PW["u-neha"]}, NEHA))
        self.assertEqual(sql("SELECT status FROM users WHERE id = 'u-neha'")[0]["status"], "active")
        self.assertIn("Household", self.vaults(NEHA))
        # the one-time password no longer works; older versions were purged
        self.ok(self.post("/api/lock", user=NEHA))
        self.assertEqual(self.post("/api/unlock", {"password": one}, NEHA, token=False).status_code, 403)
        personal = sql("SELECT id FROM vaults WHERE kind = 'personal' AND owner_user_id = 'u-neha'")[0]["id"]
        self.assertEqual(len(sql("SELECT * FROM vault_versions WHERE vault_id = ?", (personal,))), 1)
        self.unlock(NEHA)

    def test_wrong_passwords_back_off(self):
        self.set_up(ADMIN)
        for _ in range(4):
            self.assertEqual(self.post("/api/unlock", {"password": "nope"}, token=False).status_code, 403)
        self.assertEqual(self.post("/api/unlock", {"password": "nope"}, token=False).status_code, 403)
        r = self.post("/api/unlock", {"password": PW["u-admin"]}, token=False)
        self.assertEqual(r.status_code, 429)
        self.ok(self.post("/api/users/u-admin/unblock"))
        self.unlock(ADMIN)
        self.assertEqual(sql("SELECT unlock_failures FROM users WHERE id = 'u-admin'")[0]["unlock_failures"], 0)

    def test_sessions(self):
        self.set_up(ADMIN)
        self.ok(self.get("/api/vaults"))
        # another person can't use my token
        self.seen(NEHA)
        self.set_up(NEHA)
        self.assertEqual(self.get("/api/vaults", NEHA, headers={"X-Vault-Session": self.tokens["u-admin"]}).status_code, 401)
        # idle timeout (the person's own setting)
        self.ok(self.put("/api/me/settings", {"autoLockMinutes": 1}))
        s = sessions._sessions[self.tokens["u-admin"]]
        s.last -= 61
        self.assertEqual(self.get("/api/vaults").status_code, 401)
        self.unlock(ADMIN)
        # lock and lock everywhere
        second = self.ok(self.post("/api/unlock", {"password": PW["u-admin"]}, token=False))["session"]
        self.ok(self.post("/api/lock-everywhere"))
        self.assertEqual(self.get("/api/vaults", headers={"X-Vault-Session": second}).status_code, 401)
        self.assertEqual(self.get("/api/vaults").status_code, 401)
        self.unlock(ADMIN)
        self.ok(self.post("/api/lock"))
        self.assertEqual(self.get("/api/vaults").status_code, 401)
        # when nobody's unlocked, vaults are closed in memory
        self.ok(self.post("/api/lock-everywhere", user=NEHA))
        self.assertEqual(sessions.stats(), {"sessions": 0, "openVaults": 0})

    def test_disable_reset_enable(self):
        self.set_up(ADMIN)
        self.set_up(NEHA)
        self.ok(self.patch("/api/users/u-neha", {"disabled": True}))
        self.assertEqual(self.get("/api/vaults", NEHA).status_code, 403)
        self.assertNotIn("u-neha", [m["id"] for m in self.vaults()["Household"]["members"]])
        self.ok(self.patch("/api/users/u-neha", {"disabled": False}))
        self.unlock(NEHA)
        # she gets Household back once someone who has it is unlocked (the admin is)
        self.assertIn("Household", self.vaults(NEHA))
        # reset: Personal wiped, she must be set up again
        pid = sql("SELECT id FROM vaults WHERE kind='personal' AND owner_user_id='u-neha'")[0]["id"]
        self.ok(self.post("/api/users/u-neha/reset"))
        self.assertEqual(sql("SELECT status FROM users WHERE id='u-neha'")[0]["status"], "none")
        self.assertEqual(sql("SELECT * FROM vaults WHERE id = ?", (pid,)), [])
        self.assertEqual(self.post("/api/unlock", {"password": PW["u-neha"]}, NEHA, token=False).status_code, 409)
        self.set_up(NEHA)
        self.assertIn("Household", self.vaults(NEHA))

    def test_change_master_password(self):
        self.set_up(ADMIN)
        other = self.ok(self.post("/api/unlock", {"password": PW["u-admin"]}, token=False))["session"]
        self.assertEqual(self.post("/api/me/password", {"current": "wrong", "new": "x"}).status_code, 403)
        new = "velvet-harbor-cactus-glacier-violin"
        self.ok(self.post("/api/me/password", {"current": PW["u-admin"], "new": new}))
        self.assertEqual(self.get("/api/vaults", headers={"X-Vault-Session": other}).status_code, 401)  # other device
        self.ok(self.post("/api/lock"))
        PW["u-admin"], old = new, PW["u-admin"]
        try:
            r = self.unlock(ADMIN)
            self.assertIn("Household", [v["name"] for v in r["vaults"]])         # key ring still works
            self.assertEqual(self.post("/api/unlock", {"password": old}, token=False).status_code, 403)
        finally:
            PW["u-admin"] = old

    def test_whoami_and_admin_routes(self):
        self.seen(VIKRAM)
        self.assertEqual(self.get("/api/admin/users", VIKRAM).status_code, 403)
        self.assertEqual(self.get("/api/admin/settings", VIKRAM).status_code, 403)
        w = self.ok(self.get("/api/whoami"))
        self.assertTrue(w["isAdmin"])
        s = self.ok(self.put("/api/admin/settings", {"clipboard_clear_seconds": 10}))
        self.assertEqual(s["values"]["clipboard_clear_seconds"], 10)
        self.assertEqual(self.put("/api/admin/settings", {"clipboard_clear_seconds": -1}).status_code, 422)
        self.assertEqual(self.put("/api/admin/settings", {"nope": 1}).status_code, 422)

    def test_notices(self):
        self.set_up(ADMIN)
        self.set_up(NEHA)
        self.ok(self.post("/api/lock", user=NEHA))
        v = self.ok(self.post("/api/vaults", {"name": "Finance"}), 201)
        self.ok(self.post(f"/api/vaults/{v['id']}/members", {"userId": "u-neha", "role": "viewer"}))
        r = self.unlock(NEHA)
        self.assertTrue(any("gave you access to Finance" in n["text"] for n in r["notices"]))

    def test_ingress_only(self):
        from fastapi.testclient import TestClient
        from app.main import app
        c = TestClient(app, client=("10.0.0.5", 1))
        self.assertEqual(c.get("/api/me", headers={"X-Remote-User-Id": "u-admin"}).status_code, 403)
        r = self.client.get("/api/health")
        self.assertIn("default-src 'self'", r.headers["content-security-policy"])
        self.assertEqual(r.headers["cache-control"], "no-store")



class Packaging(unittest.TestCase):
    def test_versions_match(self):
        import os
        import re
        from app.routers.admin import APP_VERSION
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cfg = open(os.path.join(root, "config.yaml")).read()
        self.assertEqual(re.search(r'^version: "([^"]+)"', cfg, re.M).group(1), APP_VERSION)
        html = open(os.path.join(root, "app", "static", "index.html")).read()
        self.assertEqual(set(re.findall(r"\?v=([0-9.]+)", html)), {APP_VERSION})


if __name__ == "__main__":
    unittest.main()
