"""Shared test helpers. The people below (Kiran, Neha and Vikram Sharma, and a kid) are invented data."""
import _env  # noqa: F401

import os
import shutil
import unittest

from fastapi.testclient import TestClient

from app import config, db, ha_notify, ha_people, sessions
from app.main import app

ADMIN = {"id": "u-admin", "name": "admin", "display": "Kiran"}
NEHA = {"id": "u-neha", "name": "neha", "display": "Neha"}
VIKRAM = {"id": "u-vikram", "name": "vikram", "display": "Vikram"}
KID = {"id": "u-kid", "name": "kid", "display": "Kid"}
PW = {"u-admin": "maple-river-candle-orbit-zebra", "u-neha": "harbor-violet-pepper-lantern-quilt",
      "u-vikram": "cactus-meadow-violin-rocket-tulip", "u-kid": "banana-yogurt-pirate-comet-igloo"}


def headers(user):
    h = {"X-Remote-User-Id": user["id"]}
    if user.get("name"):
        h["X-Remote-User-Name"] = user["name"]
    if user.get("display"):
        h["X-Remote-User-Display-Name"] = user["display"]
    return h


def reset_state():
    sessions.end_all()
    for ext in ("", "-wal", "-shm"):
        p = config.DB_PATH + ext
        if os.path.exists(p):
            os.remove(p)
    shutil.rmtree(config.VAULT_DIR, ignore_errors=True)
    shutil.rmtree(os.path.join(config.DATA_DIR, "copies"), ignore_errors=True)
    notes = os.path.join(config.DATA_DIR, "restore_notes.json")
    if os.path.exists(notes):
        os.remove(notes)
    db.init_db()


def sql(q, args=()):
    with db.get_conn() as conn:
        return [dict(r) for r in conn.execute(q, args).fetchall()]


class ApiTestCase(unittest.TestCase):
    def setUp(self):
        reset_state()
        ha_people.reset()                      # no Home Assistant people (phones) unless a test sets them
        ha_notify._targets_cache = None
        self.client = TestClient(app, client=("127.0.0.1", 12345))
        self.tokens = {}

    def req(self, method, path, user=ADMIN, token=True, **kw):
        h = dict(headers(user))
        if token and user["id"] in self.tokens:
            h["X-Vault-Session"] = self.tokens[user["id"]]
        h.update(kw.pop("headers", {}) or {})
        return self.client.request(method, path, headers=h, **kw)

    def get(self, path, user=ADMIN, **kw):
        return self.req("GET", path, user, **kw)

    def post(self, path, json=None, user=ADMIN, **kw):
        return self.req("POST", path, user, json=json, **kw)

    def patch(self, path, json=None, user=ADMIN, **kw):
        return self.req("PATCH", path, user, json=json, **kw)

    def put(self, path, json=None, user=ADMIN, **kw):
        return self.req("PUT", path, user, json=json, **kw)

    def delete(self, path, user=ADMIN, **kw):
        return self.req("DELETE", path, user, **kw)

    def ok(self, resp, status=200):
        self.assertEqual(resp.status_code, status, resp.text)
        return resp.json()

    # ---- people ----
    def seen(self, user):
        """The person opens the app once (created, disabled)."""
        self.get("/api/me", user, token=False)

    def set_up(self, user):
        """Admin enables and sets up; the person unlocks with the one-time password and chooses their own."""
        self.seen(user)
        if user is not ADMIN:
            self.seen(ADMIN)
        one = self.ok(self.post(f"/api/users/{user['id']}/setup", user=ADMIN, token=False))["oneTimePassword"]
        r = self.ok(self.post("/api/unlock", {"password": one}, user, token=False))
        self.assertTrue(r["mustChangePassword"])
        self.tokens[user["id"]] = r["session"]
        self.ok(self.post("/api/me/activate", {"newPassword": PW[user["id"]]}, user))
        return one

    def unlock(self, user):
        r = self.ok(self.post("/api/unlock", {"password": PW[user["id"]]}, user, token=False))
        self.tokens[user["id"]] = r["session"]
        return r

    def vaults(self, user=ADMIN):
        return {v["name"]: v for v in self.ok(self.get("/api/vaults", user))["vaults"]}

    def personal(self, user=ADMIN):
        return next(v for v in self.ok(self.get("/api/vaults", user))["vaults"] if v["kind"] == "personal" and v["mine"])

    def add_item(self, vid, user=ADMIN, **body):
        body.setdefault("title", "Item")
        return self.ok(self.post(f"/api/vaults/{vid}/items", body, user), 201)
