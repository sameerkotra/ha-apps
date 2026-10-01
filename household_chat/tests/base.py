import _env  # noqa: F401

import json
import logging
import os
import shutil
import unittest

from fastapi.testclient import TestClient

from app import chats, config, db, files, ha_client, ha_notify, ha_people, notifier, presence, settings
from app.live import hub
from app.main import app

for _n in ("httpx", "httpx2", "files"):
    logging.getLogger(_n).setLevel(logging.WARNING)

ADMIN = {"id": "u-admin", "name": "admin", "display": "Meera"}
NISHA = {"id": "u-nisha", "name": "nisha", "display": "Nisha"}
TARUN = {"id": "u-tarun", "name": "tarun", "display": "Tarun"}
LEELA = {"id": "u-leela", "name": "leela", "display": "Leela"}


def headers(user):
    h = {"X-Remote-User-Id": user["id"]}
    if user.get("name"):
        h["X-Remote-User-Name"] = user["name"]
    if user.get("display"):
        h["X-Remote-User-Display-Name"] = user["display"]
    return h


class FakeHA:
    """Home Assistant's Core API as ha_client.request sees it (installed by ApiTestCase; used only while
    `token` is True, since everything checks ha_client.has_token() first)."""

    def __init__(self):
        self.token = False
        self.people = None            # what POST /api/template renders for ha_people (list), None = 404
        self.notify_services = None   # notify action names GET /api/services lists, None = 404
        self.states = []              # GET /api/states
        self.calls = []
        self.notified = []            # notify actions called directly (admin test sends)

    def request(self, method, path, body=None, timeout=10):
        self.calls.append((method, path))
        if method == "POST" and path == "/template":
            return (404, b"") if self.people is None else (200, json.dumps(self.people).encode())   # rendered text
        if method == "GET" and path == "/services":
            if self.notify_services is None:
                return 404, b""
            return 200, json.dumps([{"domain": "notify", "services": {n: {} for n in self.notify_services}}]).encode()
        if method == "GET" and path == "/states":
            return 200, json.dumps(self.states).encode()
        if method == "POST" and path.startswith("/services/notify/"):
            self.notified.append(path[len("/services/notify/"):])
            return 200, b"[]"
        return None, b""              # no answer


def reset_state():
    hub.reset()
    notifier.reset()
    presence.reset()
    ha_people.reset()
    ha_client._states_cache = None
    ha_notify._targets_cache = None
    chats.message_limit.reset()
    chats.upload_limit.reset()
    for ext in ("", "-wal", "-shm"):
        p = config.DB_PATH + ext
        if os.path.exists(p):
            os.remove(p)
    shutil.rmtree(config.SHARE_DIR, ignore_errors=True)
    shutil.rmtree(config.AVATAR_DIR, ignore_errors=True)
    shutil.rmtree(config.SHARE_ROOT, ignore_errors=True)
    db.init_db()
    settings.invalidate()
    files.reset()
    os.makedirs(config.SHARE_ROOT, exist_ok=True)
    files.check()


def sql(q, args=()):
    with db.get_conn() as conn:
        return [dict(r) for r in conn.execute(q, args).fetchall()]


class Sent:
    """Captures notifications instead of calling Home Assistant."""

    def __init__(self):
        self.items = []

    def __call__(self, services, title, message, data=None):
        self.items.append({"services": services, "title": title, "message": message, "data": data or {}})
        return {s: True for s in services}


class ApiTestCase(unittest.TestCase):
    def setUp(self):
        reset_state()
        self.client = TestClient(app, client=("127.0.0.1", 12345))
        self.sent = Sent()
        self._orig_deliver = notifier.deliver
        notifier.deliver = self.sent
        self._orig_thread = chats.Outbox.flush

        def flush_sync(outbox):
            # run background jobs inline so tests see their effects
            for uids, name, data, eid in outbox.events:
                hub.publish(uids, name, data, eid)
            jobs, outbox.jobs, outbox.events = outbox.jobs, [], []
            for fn, args in jobs:
                fn(*args)
        chats.Outbox.flush = flush_sync
        self.ha = FakeHA()
        self._orig_ha = (ha_client.request, ha_client.has_token)
        ha_client.request = self.ha.request
        ha_client.has_token = lambda: self.ha.token

    def tearDown(self):
        notifier.deliver = self._orig_deliver
        chats.Outbox.flush = self._orig_thread
        ha_client.request, ha_client.has_token = self._orig_ha

    def req(self, method, path, user=ADMIN, **kw):
        h = dict(headers(user))
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

    def delete(self, path, user=ADMIN, json=None, **kw):
        return self.req("DELETE", path, user, json=json, **kw)

    def ok(self, resp, status=200):
        self.assertEqual(resp.status_code, status, resp.text)
        return resp.json()

    # ---- people ----
    def seen(self, user):
        self.get("/api/me", user)

    def enable(self, *users):
        self.seen(ADMIN)
        for u in users:
            self.seen(u)
            self.ok(self.patch(f"/api/admin/people/{u['id']}", {"disabled": False}))

    def household(self):
        return sql("SELECT id FROM conversations WHERE is_household = 1")[0]["id"]

    def personal(self, user):
        return sql("SELECT id FROM conversations WHERE kind = 'personal' AND created_by = ?", (user["id"],))[0]["id"]

    def send(self, cid, text, user=ADMIN, **extra):
        return self.ok(self.post(f"/api/conversations/{cid}/messages", {"body": text, **extra}, user), 201)

    def upload(self, cid, name, data, user=ADMIN, **params):
        q = "&".join(f"{k}={v}" for k, v in {"name": name, **params}.items())
        return self.req("POST", f"/api/conversations/{cid}/uploads?{q}", user, content=data)

    def notify_to(self, user, service="mobile_app_phone"):
        self.ok(self.post(f"/api/admin/people/{user['id']}/notify", {"service": f"notify.{service}"}), 201)
