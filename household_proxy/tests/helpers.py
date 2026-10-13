"""Shared set-up for the proxy tests: the app with its lifespan, people as Home Assistant's ingress proxy sends
them, and a fake site on 127.0.0.1 (tests/fakesite.py) the address check is told to allow."""
import _env  # noqa: F401  (must be first)

import unittest

from app import auth, db, main, netguard, proxy, settings, sites
from common_tests.ingress import identity_headers, ingress_client

import fakesite

INGRESS = "/api/hassio_ingress/TOKEN123"
ADMIN = identity_headers("u-admin", "adminy", "Ann Admin", ingress_path=INGRESS)
BOB = identity_headers("u-bob", "bob", "Bob", ingress_path=INGRESS)
CAROL = identity_headers("u-carol", "carol", "Carol", ingress_path=INGRESS)

_client = None


def client():
    global _client
    if _client is None:
        _client = ingress_client(main.app)
        _client.__enter__()                  # the lifespan: database, jobs
    return _client


def reset_db() -> None:
    with db.get_conn() as conn:
        for t in ("restart_log", "switched_off", "alert_mutes", "site_alert_people", "check_hours", "checks", "incidents", "access_log", "site_rules", "site_headers", "site_people", "sites", "users", "app_settings"):
            conn.execute(f"DELETE FROM {t}")
    sites.invalidate()
    auth.forget_cached()
    settings.REGISTRY.invalidate()            # App settings back to their defaults
    proxy.flush_access_log()                  # lines of an earlier test (their sites are gone: dropped)


class ProxyCase(unittest.TestCase):
    """A fresh database per test, the fake site running, and helpers to add sites."""

    @classmethod
    def setUpClass(cls):
        netguard.ALLOW_LOOPBACK = True
        cls.port = fakesite.start()
        cls.c = client()

    def setUp(self):
        reset_db()
        for who in (ADMIN, BOB, CAROL):
            self.c.get("/api/me", headers=who)

    def add_site(self, **kw) -> dict:
        body = {"name": "Fake", "target": f"http://127.0.0.1:{self.port}", "trusted_ok": True}
        body.update(kw)
        r = self.c.post("/api/admin/sites", json=body, headers=ADMIN)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def get(self, path, who=ADMIN, **kw):
        return self.c.get(path, headers={**who, **kw.pop("headers", {})}, **kw)
