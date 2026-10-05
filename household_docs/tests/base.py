"""Shared test fixture: a fake Home Assistant, a fresh database and an empty /share per test, and a clock the
tests move by hand. Every person here is invented."""
import _env  # noqa: F401  (must be first)

import glob
import logging
import os
import shutil
import unittest
from datetime import datetime, timedelta, timezone

from app import config, db, settings
from app.common import ha_notify, ha_people
from app.main import app
from app.store import index, moving, roots
from common_tests.fake_ha import FakeHA
from common_tests.ingress import INGRESS_PATH, identity_headers, ingress_client

NOW = datetime(2026, 10, 5, 12, 0, 0, tzinfo=timezone.utc)
logging.getLogger("httpx2").setLevel(logging.WARNING)          # one line per request is noise here
SHARE = os.environ["SHARE_DIR"]
DOCS = os.path.join(SHARE, "household_docs")


def hdr(uid, name=None, username=None):
    return identity_headers(uid, username or uid, name or uid, ingress_path=INGRESS_PATH)


ASHA = hdr("u_asha", "Asha Rao", "asha")          # admin (DEV_ADMINS in _env)
KABIR = hdr("u_kabir", "Kabir Rao", "kabir")
MEERA = hdr("u_meera", "Meera Rao", "meera")
DEV = hdr("u_dev", "Dev Rao", "dev")
NOBODY = hdr("u_nobody", "Nobody Else", "nobody")


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, **kw):
        self.t = self.t + timedelta(**kw)


def person_dir(name):
    return os.path.join(DOCS, "people", name)


def write(path, data, mtime=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb" if isinstance(data, bytes) else "w", **({} if isinstance(data, bytes) else {"encoding": "utf-8", "newline": ""})) as f:
        f.write(data)
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def read(path, binary=False):
    with open(path, "rb" if binary else "r", **({} if binary else {"encoding": "utf-8", "newline": ""})) as f:
        return f.read()


class ApiBase(unittest.TestCase):
    PEOPLE = (ASHA, KABIR, MEERA)

    @classmethod
    def setUpClass(cls):
        cls.ha = FakeHA()
        config.SUPERVISOR_CORE_API = cls.ha.start()
        config.SUPERVISOR_TOKEN = "test-token"
        cls._ctx = ingress_client(app)
        cls.c = cls._ctx.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls._ctx.__exit__(None, None, None)
        cls.ha.stop()

    def setUp(self):
        for f in glob.glob(config.DB_PATH + "*"):
            os.remove(f)
        for name in os.listdir(SHARE):
            p = os.path.join(SHARE, name)
            if os.path.isdir(p) and not os.path.islink(p):
                for dirpath, dirnames, _files in os.walk(p):
                    for d in dirnames:
                        os.chmod(os.path.join(dirpath, d), 0o755)
                shutil.rmtree(p)
            else:
                os.remove(p)
        db.init_db()
        db.RESTORING.clear()
        settings.invalidate()
        roots.reset()
        moving.reset()
        self.clock = Clock(NOW)
        config.utcnow = self.clock
        config.set_timezone("UTC")
        config.SUPERVISOR_TOKEN = "test-token"
        self.ha.requests.clear()
        self.ha.states.clear()
        self.ha.fail = False
        self.ha.people = None
        self.ha.notify_services = None
        self.ha.notify_entities = []
        ha_people.reset()
        ha_notify._targets_cache = None
        ha_notify._entity_mode.clear()
        for h in self.PEOPLE:
            r = self.get("/api/me", h)
            self.assertEqual(r.status_code, 200, r.text)

    # -- requests ------------------------------------------------------------
    def get(self, path, h=KABIR, **kw):
        return self.c.get(path, headers=h, **kw)

    def post(self, path, body=None, h=KABIR, **kw):
        return self.c.post(path, json=body if body is not None else {}, headers=h, **kw)

    def put(self, path, body, h=KABIR):
        return self.c.put(path, json=body, headers=h)

    def patch(self, path, body, h=KABIR):
        return self.c.patch(path, json=body, headers=h)

    def delete(self, path, h=KABIR):
        return self.c.delete(path, headers=h)

    def ok(self, r, code=200):
        self.assertEqual(r.status_code, code, r.text)
        return r.json()

    # -- helpers -------------------------------------------------------------
    def settings(self, body):
        return self.ok(self.put("/api/admin/settings", body, ASHA))

    def create(self, kind, name, parent=None, h=KABIR, text=None):
        body = {"kind": kind, "name": name, "parentId": parent}
        if text is not None:
            body["text"] = text
        return self.ok(self.post("/api/docs", body, h), 201)

    def note(self, name="Note", parent=None, h=KABIR, text=None):
        n = self.create("note", name, parent, h)
        if text is not None:
            self.save(n["id"], text, h)
        return n

    def open(self, nid, h=KABIR):
        return self.ok(self.get(f"/api/docs/{nid}", h))

    def save(self, nid, text, h=KABIR, etag=None, keep_mine=False):
        etag = etag or self.open(nid, h)["etag"]
        return self.ok(self.patch(f"/api/docs/{nid}", {"etag": etag, "text": text, "keepMine": keep_mine}, h))

    def ops(self, nid, ops, h=KABIR):
        return self.post(f"/api/docs/{nid}/checklist", {"ops": ops}, h)

    def share(self, nid, target, role="viewer", h=KABIR, **kw):
        body = {"userId": target["X-Remote-User-Id"] if isinstance(target, dict) else target, "role": role}
        body.update(kw)
        return self.post(f"/api/nodes/{nid}/shares", body, h)

    def uid(self, h):
        return h["X-Remote-User-Id"]

    def real(self, node_id):
        from app.store import nodes
        with db.get_conn() as conn:
            row = nodes.get(conn, node_id, live=False)
            return nodes.real_path(conn, row)[1]

    def row(self, node_id):
        from app.store import nodes
        with db.get_conn() as conn:
            return nodes.get(conn, node_id, live=False)

    def scan(self):
        return index.scan_all()

    def root_id(self, h=KABIR):
        return self.ok(self.get("/api/me", h))["rootId"]
