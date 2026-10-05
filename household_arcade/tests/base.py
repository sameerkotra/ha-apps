"""Shared test fixture: a fake Home Assistant, a fresh database per test and a
clock the tests move by hand. Every person here is invented."""
import _env  # noqa: F401  (must be first)

import glob
import os
import unittest
from datetime import datetime, timedelta, timezone

from app import config, db, ha_sensors, settings
from app.common import ha_notify, ha_people
from app.main import app
from common_tests.fake_ha import FakeHA
from common_tests.ingress import INGRESS_PATH, identity_headers, ingress_client

# Monday 21 September 2026, noon UTC
NOW = datetime(2026, 9, 21, 12, 0, 0, tzinfo=timezone.utc)


def hdr(uid, name=None, username=None):
    """Login name and display name default to the id; always through ingress."""
    return identity_headers(uid, username or uid, name or uid, ingress_path=INGRESS_PATH)


ASHA = hdr("u_asha", "Asha Rao", "asha")          # admin (DEV_ADMINS in _env)
KABIR = hdr("u_kabir", "Kabir Rao", "kabir")
MEERA = hdr("u_meera", "Meera Rao", "meera")
DEV = hdr("u_dev", "Dev Rao", "dev")


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, **kw):
        self.t = self.t + timedelta(**kw)


class ApiBase(unittest.TestCase):
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
        db.init_db()
        settings.invalidate()
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
        ha_sensors._last.clear()
        for h in (ASHA, KABIR, MEERA):
            self.assertEqual(self.get("/api/me", h).status_code, 200)

    # -- helpers ---------------------------------------------------------
    def get(self, path, h=KABIR, **kw):
        return self.c.get(path, headers=h, **kw)

    def post(self, path, body=None, h=KABIR, **kw):
        return self.c.post(path, json=body if body is not None else {}, headers=h, **kw)

    def put(self, path, body, h=KABIR):
        return self.c.put(path, json=body, headers=h)

    def patch(self, path, body, h=ASHA):
        return self.c.patch(path, json=body, headers=h)

    def delete(self, path, h=KABIR):
        return self.c.delete(path, headers=h)

    def settings(self, body):
        r = self.put("/api/admin/settings", body, ASHA)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def start(self, game="snake", mode="walls-normal", h=KABIR, practice=False):
        r = self.post("/api/sessions", {"game": game, "mode": mode, "practice": practice}, h)
        return r

    def play(self, score, seconds=60, game="snake", mode="walls-normal", h=KABIR, level=1, practice=False):
        """Start a game, let `seconds` pass and send the result."""
        r = self.start(game, mode, h, practice)
        self.assertEqual(r.status_code, 201, r.text)
        self.clock.advance(seconds=seconds)
        return self.post("/api/scores", {"sessionId": r.json()["id"], "score": score, "level": level,
                                         "seconds": seconds}, h)

    def make_child(self, h=KABIR, **limits):
        uid = h["X-Remote-User-Id"]
        r = self.patch(f"/api/admin/users/{uid}", {"isChild": True})
        self.assertEqual(r.status_code, 200, r.text)
        if limits:
            r = self.put(f"/api/admin/users/{uid}/limits", limits, ASHA)
            self.assertEqual(r.status_code, 200, r.text)
        return uid
