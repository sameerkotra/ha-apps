"""Shared set-up for the app's tests: the app with its lifespan (no background loops), a fake Home Assistant
(fakes.FakeSource), a clock the tests move, people as Home Assistant's ingress proxy sends them, and recorded
notifications."""
import _env  # noqa: F401  (must be first)

import unittest
from unittest import mock

from app import alerts, app_messages, config, db, forwarding, ha_source, monitor, phones, settings
from app.common import ha_notify, ha_people
from common_tests.ingress import identity_headers, ingress_client

from fakes import FakeSource, state_event  # noqa: F401

ADMIN = identity_headers("u-ann", "ann", "Ann Admin")
ASHA = identity_headers("u-asha", "asha", "Asha")
BOB = identity_headers("u-bob", "bob", "Bob")
KID = identity_headers("u-kid", "kid", "Kiran")
T0 = 1_800_000_000.0          # a fixed "now" (2027-01-15 08:00 UTC)

_client = None


def client():
    global _client
    if _client is None:
        _client = ingress_client(main_app())
        _client.__enter__()
    return _client


def main_app():
    from app.main import app
    return app


TABLES = ("actions", "alert_queue", "alert_state", "forwards", "checks", "history", "bus_requests", "phones", "user_notify", "users",
          "app_state", "app_settings")


class PhonesCase(unittest.TestCase):
    def setUp(self):
        self.c = client()
        with db.get_conn() as conn:
            for t in TABLES:
                conn.execute(f"DELETE FROM {t}")
        settings.REGISTRY.invalidate()
        monitor.reset()
        forwarding.reset()
        app_messages.reset()
        phones.LIVE.reset()
        self.now = T0
        p = mock.patch.object(config, "clock", lambda: self.now)
        p.start()
        self.addCleanup(p.stop)
        monitor.STARTED["at"] = T0 - 86400           # past the start-up grace unless a test says otherwise
        self.ha = FakeSource()
        p = mock.patch.object(ha_source, "SOURCE", self.ha)
        p.start()
        self.addCleanup(p.stop)
        # notifications: recorded, never sent
        self.sent = []
        self.sent_data = []
        p = mock.patch.object(ha_notify, "send_notify", lambda svc, title, msg, data=None: self.sent.append((svc, title, msg))
                              or self.sent_data.append(data) or True)
        p.start()
        self.addCleanup(p.stop)
        # forwards: recorded (the data Chat would get), never sent
        self.posted = []
        app_messages.POST["fn"] = lambda data: self.posted.append(data) or True
        self.addCleanup(lambda: app_messages.POST.update(fn=None))
        forwarding.SYNC["on"] = True
        self.addCleanup(lambda: forwarding.SYNC.update(on=False))
        for who in (ADMIN, ASHA, BOB, KID):
            self.c.get("/api/me", headers=who)
        self.people([])

    def people(self, people):
        """Home Assistant's people (ha_people's cache): [{userId, name, phones: [{label, tracker, service}]}]."""
        with ha_people._lock:
            ha_people._cache.update(at=10**12, people=[{"entityId": f"person.{p['name'].lower()}", "state": "home",
                                                         "picture": None, **p} for p in people])

    def add(self, name="Asha Pixel", user_id="u-asha", *, monitored=True, **kw) -> str:
        """A Companion-app device in Home Assistant, discovered: its phone id. `monitored` (the default here): as if
        an admin had added it long ago (no grace after adding); False leaves it under Available devices."""
        self.ha.add_device(name, user_id=user_id, **kw)
        res = phones.refresh(self.ha)
        self.assertTrue(res["ok"])
        with db.get_conn() as conn:
            pid = conn.execute("SELECT id FROM phones WHERE ha_name = ?", (name,)).fetchone()["id"]
            if monitored:
                conn.execute("UPDATE phones SET monitored = 1, kind_confirmed = 1, status_reason = NULL WHERE id = ?",
                             (pid,))
        return pid

    def row(self, pid):
        with db.get_conn() as conn:
            return conn.execute("SELECT * FROM phones WHERE id = ?", (pid,)).fetchone()

    def set_row(self, pid, **cols):
        with db.get_conn() as conn:
            conn.execute("UPDATE phones SET " + ", ".join(f"{k} = ?" for k in cols) + " WHERE id = ?", (*cols.values(), pid))

    def report(self, pid, name="Asha Pixel", key="battery_level", value="80", etype="state_changed", attrs=None):
        """The phone reports (an event on one of its entities) at the current time."""
        monitor.on_state_event(state_event(self.ha.eid(name, key), value, attrs, at=self.now, etype=etype))

    def tick(self):
        return monitor.tick(self.now, self.ha)

    def advance(self, minutes):
        self.now += minutes * 60

    def setting(self, **kw):
        settings.update(kw, None)

    def get(self, path, who=ADMIN):
        return self.c.get(path, headers=who)

    def put(self, path, body, who=ADMIN):
        return self.c.put(path, json=body, headers=who)

    def post(self, path, body=None, who=ADMIN):
        return self.c.post(path, json=body if body is not None else {}, headers=who)
