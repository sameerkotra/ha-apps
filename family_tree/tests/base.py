"""Shared fixtures: a clean database + media folder per test, a TestClient,
user headers and small API helpers."""
import _env  # noqa: F401  (must come before `app`)

import io
import os
import re
import shutil
import sqlite3
import unittest


from app import config, db, features, graph, media, settings
from app.common import ha_people
from app.main import app
from common_tests.ingress import ingress_client, user_headers

import logging
logging.disable(logging.WARNING)      # keep test output readable (main.py sets INFO)

_ORIG = {
    "ALLOW_ANY_MEDIA_PATH": config.ALLOW_ANY_MEDIA_PATH,
    "ADMIN_NAMES": set(config.ADMIN_NAMES),
}

ADMIN = {"id": "u-admin", "name": "admin", "display": "Admin Person"}
ALICE = {"id": "u-alice", "name": "alice", "display": "Alice A"}
BOB = {"id": "u-bob", "name": "bob", "display": "Bob B"}


def restore_config():
    config.ALLOW_ANY_MEDIA_PATH = _ORIG["ALLOW_ANY_MEDIA_PATH"]
    config.ADMIN_NAMES = set(_ORIG["ADMIN_NAMES"])


def set_app_settings(**values):
    """Change App settings directly (trash_days, max_upload_mb, media_path) —
    no photo-folder check or confirmation; call media.check() yourself.
    They live in the database, so reset_state() puts them back to the defaults
    for the next test."""
    return settings.update(values, None)


def remove_db_files():
    for ext in ("", "-wal", "-shm"):
        p = config.DB_PATH + ext
        if os.path.exists(p):
            os.remove(p)


def all_features_on():
    """Every feature switch on (Admin → App settings → Features), as stored App settings."""
    settings.update({k: True for k in features.KEYS}, None)


def reset_state(setup_media: bool = True, all_features: bool = False):
    """Fresh DB, fresh media folder (no marker), empty graph cache, media re-checked.
    all_features: turn every feature switch on (a new install has the regional and
    internet ones off) — the module tests use that; test_features checks the defaults."""
    restore_config()
    remove_db_files()
    shutil.rmtree(_env.MEDIA_PATH, ignore_errors=True)
    shutil.rmtree(os.path.join(_env.TMP, "elsewhere"), ignore_errors=True)
    os.makedirs(_env.SHARE_DIR, exist_ok=True)
    db.init_db()
    settings.invalidate()           # the cached App settings belonged to the old database
    ha_people.reset()               # no people/phones read from Home Assistant yet
    with graph._lock:
        graph._cache["version"] = None
        graph._cache["graph"] = None
    with media._state_lock:
        media._state.update(online=False, reason="Not checked yet.", checked_at=None, store_id=None, foreign_id=None,
                            path=None)
    if all_features:
        all_features_on()
    if setup_media:
        media.check()


def headers(user=None):
    return user_headers(user or ALICE)


def sql(query, args=()):
    """Direct DB access for set-up/inspection (commits)."""
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(query, args).fetchall()
        conn.commit()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def addon_version() -> str:
    """The version in config.yaml (what the app must report)."""
    with open(os.path.join(_env.ROOT, "config.yaml"), encoding="utf-8") as f:
        return re.search(r'^version:\s*"?([^"\s]+)"?', f.read(), re.M).group(1)


def image_bytes(fmt="JPEG", size=(64, 48), color=(200, 30, 30), mode="RGB", **save_kw):
    from PIL import Image
    img = Image.new(mode, size, color)
    buf = io.BytesIO()
    img.save(buf, fmt, **save_kw)
    return buf.getvalue()


class ApiTestCase(unittest.TestCase):
    """Each test starts from an empty tree with media online and, unless a class
    sets ALL_FEATURES = False, every feature switch on."""
    ALL_FEATURES = True

    def setUp(self):
        reset_state(all_features=self.ALL_FEATURES)
        self.client = ingress_client(app)
        self.addCleanup(restore_config)

    # ---- raw requests ----
    def req(self, method, path, user=None, **kw):
        h = dict(headers(user))
        h.update(kw.pop("headers", {}) or {})
        return self.client.request(method, path, headers=h, **kw)

    def get(self, path, user=None, **kw):
        return self.req("GET", path, user, **kw)

    def post(self, path, json=None, user=None, **kw):
        return self.req("POST", path, user, json=json, **kw)

    def patch(self, path, json=None, user=None, **kw):
        return self.req("PATCH", path, user, json=json, **kw)

    def put(self, path, json=None, user=None, **kw):
        return self.req("PUT", path, user, json=json, **kw)

    def delete(self, path, user=None, **kw):
        return self.req("DELETE", path, user, **kw)

    def ok(self, resp, status=200):
        self.assertEqual(resp.status_code, status, resp.text)
        return resp.json()

    # ---- tree building ----
    def person(self, given, surname=None, gender="unknown", born=None, died=None, user=None, **extra):
        body = {"given_names": given, "gender": gender}
        if surname:
            body["surname"] = surname
        if born is not None:
            body["birth"] = {"date": born if isinstance(born, dict) else {"y": born}}
        if died is not None:
            body["death"] = {"date": died if isinstance(died, dict) else {"y": died}}
        body.update(extra)
        return self.ok(self.post("/api/people", body, user=user), 201)["person"]["id"]

    def family(self, p1=None, p2=None, children=(), relation="birth", kind="married", user=None):
        """A family of existing people; children linked in the given order."""
        body = {"kind": kind}
        if p1:
            body["partner1Id"] = p1
        if p2:
            body["partner2Id"] = p2
        fid = self.ok(self.post("/api/families", body, user=user), 201)["family"]["id"]
        for c in children:
            rel = relation
            if isinstance(c, tuple):
                c, rel = c
            self.ok(self.post(f"/api/families/{fid}/children", {"existingId": c, "relation": rel}, user=user), 201)
        return fid

    def set_me(self, pid, user=None):
        return self.ok(self.put("/api/me/person", {"personId": pid}, user=user))

    def rel(self, a, b):
        return self.ok(self.get(f"/api/relationship?from={a}&to={b}"))

    def label(self, a, b):
        return self.rel(a, b)["label"]

    def detail(self, pid, user=None):
        return self.ok(self.get(f"/api/people/{pid}", user=user))
