"""Reminders (§9): the per-person 🔔 switch, your own reminder
settings, notify services per user (admins), the daily digest, and the
close-family shortcut. "Now" is patched through config.now; notifications go
to a recording sender, or to a fake Home Assistant for the HTTP paths."""
from base import ADMIN, ALICE, BOB, ApiTestCase, sql
import _env  # noqa: F401

import json
import sqlite3
import unittest
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

from app import config, db, ha_notify, ha_people, reminders
from app.routers import reminders as reminders_router
from fake_ha import FakeHA

TZ = ZoneInfo("America/Chicago")


class Sender:
    """Records (service, title, message); answers `ok` (a bool or {service: bool})."""

    def __init__(self, ok=True):
        self.ok = ok
        self.sent = []

    def __call__(self, service, title, message, data=None):
        self.sent.append((service, title, message))
        return self.ok.get(service, True) if isinstance(self.ok, dict) else self.ok

    @property
    def messages(self):
        return [m for _s, _t, m in self.sent]


class ReminderCase(ApiTestCase):
    NOW = datetime(2026, 9, 25, 8, 5, tzinfo=TZ)        # a Friday, just after 08:00

    def setUp(self):
        super().setUp()
        self.set_now(self.NOW)
        reminders_router._last_test.clear()
        ha_notify._entity_mode.clear()
        ha_notify._targets_cache = None

    def set_now(self, dt):
        if getattr(self, "_now_patch", None):
            self._now_patch.stop()
        self._now_patch = mock.patch.object(config, "now", return_value=dt)
        self._now_patch.start()
        self.addCleanup(self._now_patch.stop)

    # ---- helpers ----
    def remind(self, pid, on=True, user=None):
        return self.ok(self.patch(f"/api/people/{pid}", {"remind": on}, user=user))

    def add_service(self, user=ALICE, service="notify.mobile_app_alice"):
        return self.ok(self.post(f"/api/admin/users/{user['id']}/notify", {"service": service}, user=ADMIN), 201)

    def prefs(self, user=ALICE, **values):
        if values:
            return self.ok(self.put("/api/reminders/prefs", values, user=user))
        return self.ok(self.get("/api/reminders/prefs", user=user))

    def ready(self, user=ALICE, **values):
        """User seen, one service, reminders on (plus any other prefs)."""
        self.get("/api/me", user=user)
        self.add_service(user, f"notify.mobile_app_{user['name']}")
        return self.prefs(user, enabled=True, **values)

    def run_pass(self, sender=None, now=None):
        sender = sender or Sender()
        n = reminders.run_digest_pass_blocking(now=now or self.NOW, sender=sender)
        return n, sender

    def digest(self, **kw):
        """Today's digest text, as if nothing had been sent yet today."""
        sql("DELETE FROM reminder_log")
        _n, s = self.run_pass(**kw)
        return s.messages[0] if s.messages else None


# ---------------------------------------------------------------------------
class PersonSwitch(ReminderCase):
    def test_off_by_default_everywhere(self):
        a = self.person("Ann", "Smith", remind=True)             # ignored on create
        kid = self.ok(self.post(f"/api/people/{a}/add-child", {"person": {"given_names": "Kid"}}), 201)
        fam = self.ok(self.post("/api/families/quick", {"partners": [{"person": {"given_names": "P1"}},
                                                                     {"person": {"given_names": "P2"}}],
                                                        "children": [{"person": {"given_names": "C"}}]}), 201)
        self.assertTrue(kid and fam)
        rows = sql("SELECT remind FROM people")
        self.assertEqual(len(rows), 5)
        self.assertTrue(all(r["remind"] == 0 for r in rows))
        self.assertFalse(self.detail(a)["remind"])

    def test_toggle_is_history_and_undoable(self):
        a = self.person("Ann", "Smith")
        res = self.remind(a)
        self.assertTrue(res["person"]["remind"])
        batch = sql("SELECT label, user_id FROM batches WHERE id = ?", (res["batchId"],))[0]
        self.assertEqual(batch["label"], "Turned reminders on for Ann Smith")
        self.assertEqual(batch["user_id"], ALICE["id"])
        hist = self.ok(self.get(f"/api/history?personId={a}"))
        self.assertIn("Turned reminders on for Ann Smith", [i["label"] for i in hist["items"]])
        self.ok(self.post(f"/api/history/{res['batchId']}/undo", user=BOB))
        self.assertFalse(self.detail(a)["remind"])
        off = self.remind(a, True)
        self.assertEqual(sql("SELECT label FROM batches WHERE id = ?", (self.remind(a, False)["batchId"],))[0]["label"],
                         "Turned reminders off for Ann Smith")
        self.assertTrue(off["person"]["remind"])

    def test_edit_with_other_fields_is_an_ordinary_edit(self):
        a = self.person("Ann", "Smith")
        res = self.ok(self.patch(f"/api/people/{a}", {"remind": True, "nickname": "Annie"}))
        self.assertEqual(sql("SELECT label FROM batches WHERE id = ?", (res["batchId"],))[0]["label"], "Edited Ann Smith")

    def test_people_filter(self):
        a = self.person("Ann", "Smith")
        b = self.person("Bob", "Smith")
        self.remind(a)
        on = self.ok(self.get("/api/people?remind=on"))
        off = self.ok(self.get("/api/people?remind=off"))
        self.assertEqual([p["id"] for p in on["items"]], [a])
        self.assertEqual([p["id"] for p in off["items"]], [b])
        self.assertTrue(on["items"][0]["remind"])
        self.assertEqual(self.ok(self.get("/api/people"))["total"], 2)
        self.assertEqual(self.get("/api/people?remind=maybe").status_code, 422)

    def test_upcoming_lists_everyone_and_marks_bells(self):
        a = self.person("Ann", "Smith", born={"d": 1, "m": 10, "y": 1960})
        b = self.person("Bob", "Jones", born={"d": 2, "m": 10, "y": 1961})
        c = self.person("Cat", "Jones", born={"d": 3, "m": 10, "y": 1962})
        self.ok(self.post("/api/families", {"partner1Id": b, "partner2Id": c, "kind": "married",
                                            "marriage": {"date": {"d": 4, "m": 10, "y": 2000}}}), 201)
        self.remind(a)
        self.remind(c)
        items = self.ok(self.get("/api/upcoming?days=30&scope=all"))["items"]
        by = {(i["kind"], i["people"][0]["id"]): i for i in items}
        self.assertTrue(by[("birthday", a)]["remind"])
        self.assertFalse(by[("birthday", b)]["remind"])
        ann = [i for i in items if i["kind"] == "anniversary"][0]
        self.assertTrue(ann["remind"])                              # either partner is enough
        self.assertEqual({p["id"]: p["remind"] for p in ann["people"]}, {b: False, c: True})

    def test_trash_keeps_switch(self):
        a = self.person("Ann", "Smith")
        self.remind(a)
        self.ok(self.delete(f"/api/people/{a}"))
        self.ok(self.post(f"/api/people/{a}/restore", user=ADMIN))
        self.assertTrue(self.detail(a)["remind"])

    def test_several_people_at_once(self):
        a = self.person("Ann")
        b = self.person("Bob")
        r = self.ok(self.put("/api/reminders/people", {"ids": [a, b], "remind": True}))
        self.assertEqual(r["count"], 2)
        self.assertEqual(sql("SELECT label FROM batches WHERE id = ?", (r["batchId"],))[0]["label"],
                         "Turned reminders on for 2 people")
        again = self.ok(self.put("/api/reminders/people", {"ids": [a, b], "remind": True}))
        self.assertEqual(again, {"count": 0, "batchId": None})
        self.ok(self.post(f"/api/history/{r['batchId']}/undo"))
        self.assertEqual(sql("SELECT SUM(remind) AS n FROM people")[0]["n"], 0)
        self.assertEqual(self.put("/api/reminders/people", {"ids": [a, "nope"], "remind": True}).status_code, 404)
        self.assertEqual(self.put("/api/reminders/people", {"ids": [], "remind": True}).status_code, 422)

    def test_never_exported(self):
        a = self.person("Ann", "Smith", born={"d": 1, "m": 10, "y": 1960}, deceased=True)
        self.remind(a)
        from test_export import ExportCase
        job = ExportCase.run_export(self, {"living": "full"})
        self.assertEqual(job["status"], "done", job)
        import io
        import zipfile
        z = zipfile.ZipFile(io.BytesIO(self.get(f"/api/export/{job['id']}/file").content))
        text = "\n".join(z.read(n).decode("utf-8") for n in z.namelist() if n.endswith((".html", ".js")))
        self.assertIn("Ann", text)
        self.assertNotIn("remind", text.lower())
        self.assertNotIn("🔔", text)


# ---------------------------------------------------------------------------
class Prefs(ReminderCase):
    def test_defaults(self):
        p = self.prefs()
        for k, v in reminders.DEFAULTS.items():
            self.assertEqual(p[k], v, k)
        self.assertEqual((p["services"], p["canEnable"], p["meSet"], p["peopleOn"], p["closeFamily"]),
                         ([], False, False, 0, None))

    def test_needs_a_service_to_turn_on(self):
        r = self.put("/api/reminders/prefs", {"enabled": True})
        self.assertEqual(r.status_code, 409)
        self.assertIn("Admin → Users", r.json()["detail"])
        self.add_service()
        p = self.prefs(enabled=True)
        self.assertTrue(p["enabled"])
        self.assertEqual(p["services"], ["notify.mobile_app_alice"])
        # other settings can be changed without a service
        self.assertEqual(self.prefs(BOB, time="07:30")["time"], "07:30")

    def test_validation(self):
        for body in ({"time": "8am"}, {"time": "24:00"}, {"time": "07:60"}, {"leadDays": 15}, {"leadDays": -1},
                     {"scope": "some"}, {"birthdays": None}, {"colour": "red"}):
            self.assertEqual(self.put("/api/reminders/prefs", body).status_code, 422, body)
        self.assertEqual(self.prefs(time="07:05", leadDays=14)["leadDays"], 14)

    def test_close_scope_needs_me(self):
        r = self.put("/api/reminders/prefs", {"scope": "close"})
        self.assertEqual(r.status_code, 422)
        me = self.person("Alice")
        self.set_me(me)
        self.assertEqual(self.prefs(scope="close")["scope"], "close")

    def test_warning_when_service_removed(self):
        self.ready()
        self.ok(self.delete(f"/api/admin/users/{ALICE['id']}/notify/notify.mobile_app_alice", user=ADMIN))
        p = self.prefs()
        self.assertTrue(p["enabled"])
        self.assertIn("Settings → People → you → Track device", p["warning"])

    def test_disabled_user(self):
        self.get("/api/me", user=BOB)
        self.ok(self.patch(f"/api/users/{BOB['id']}", {"disabled": True}, user=ADMIN))
        self.assertEqual(self.get("/api/reminders/prefs", user=BOB).status_code, 403)

    def test_whoami_and_admin_users(self):
        self.ready()
        a = self.person("Ann")
        self.remind(a)
        w = self.ok(self.get("/api/whoami"))
        self.assertEqual((w["notifyLinked"], w["remindersOn"], w["remindPeople"]), (1, True, 1))
        users = {u["id"]: u for u in self.ok(self.get("/api/admin/users", user=ADMIN))}
        self.assertEqual(users[ALICE["id"]]["notify"], ["notify.mobile_app_alice"])
        self.assertTrue(users[ALICE["id"]]["remindersOn"])
        self.assertEqual(users[ADMIN["id"]]["notify"], [])


# ---------------------------------------------------------------------------
class AdminNotify(ReminderCase):
    def test_admin_only(self):
        self.get("/api/me")
        for method, path, body in (("POST", f"/api/admin/users/{ALICE['id']}/notify", {"service": "notify.x"}),
                                   ("DELETE", f"/api/admin/users/{ALICE['id']}/notify/notify.x", None),
                                   ("POST", f"/api/admin/users/{ALICE['id']}/notify-test", None),
                                   ("GET", "/api/admin/notify-services", None)):
            r = self.req(method, path, ALICE, json=body) if body else self.req(method, path, ALICE)
            self.assertEqual(r.status_code, 403, path)

    def test_add_remove_and_names(self):
        self.get("/api/me")
        self.assertEqual(self.add_service(service="mobile_app_alice")["notify"], ["notify.mobile_app_alice"])
        self.assertEqual(self.add_service(service="notify.mobile_app_alice")["notify"], ["notify.mobile_app_alice"])
        for bad in ("notify.Bad", "notify.a b", "notify.x/../y", "light.kitchen", "", 5):
            r = self.post(f"/api/admin/users/{ALICE['id']}/notify", {"service": bad}, user=ADMIN)
            self.assertEqual(r.status_code, 422, bad)
        for i in range(4):
            self.add_service(service=f"notify.extra_{i}")
        r = self.post(f"/api/admin/users/{ALICE['id']}/notify", {"service": "notify.one_too_many"}, user=ADMIN)
        self.assertEqual(r.status_code, 422)
        out = self.ok(self.delete(f"/api/admin/users/{ALICE['id']}/notify/notify.extra_0", user=ADMIN))
        self.assertNotIn("notify.extra_0", out["notify"])
        self.assertEqual(self.delete(f"/api/admin/users/{ALICE['id']}/notify/notify.extra_0", user=ADMIN).status_code, 404)
        self.assertEqual(self.post("/api/admin/users/nobody/notify", {"service": "notify.x"}, user=ADMIN).status_code, 404)
        row = sql("SELECT added_by, added_at FROM user_notify WHERE service = 'notify.mobile_app_alice'")[0]
        self.assertEqual(row["added_by"], ADMIN["id"])
        self.assertTrue(row["added_at"])

    def test_services_without_home_assistant(self):
        r = self.ok(self.get("/api/admin/notify-services", user=ADMIN))
        self.assertFalse(r["available"])
        self.assertIn("Supervisor token", r["error"])

    def test_test_needs_a_service(self):
        self.get("/api/me")
        self.assertEqual(self.post(f"/api/admin/users/{ALICE['id']}/notify-test", user=ADMIN).status_code, 400)
        self.assertEqual(self.post("/api/reminders/test").status_code, 400)


class WithFakeHA(ReminderCase):
    def setUp(self):
        super().setUp()
        self.ha = FakeHA()
        url = self.ha.start()
        self.addCleanup(self.ha.stop)
        for name, value in (("SUPERVISOR_TOKEN", "t0ken"), ("SUPERVISOR_CORE_API", url)):
            p = mock.patch.object(config, name, value)
            p.start()
            self.addCleanup(p.stop)

    def test_services_list(self):
        self.ha.notify_services = ["mobile_app_alice", "send_message", "persistent_notification"]
        self.ha.notify_entities = ["alice_pixel"]
        r = self.ok(self.get("/api/admin/notify-services", user=ADMIN))
        self.assertTrue(r["available"])
        self.assertEqual(r["services"], ["notify.mobile_app_alice", "notify.persistent_notification"])
        self.assertEqual(r["entities"], ["notify.alice_pixel"])

    def test_admin_test_and_own_test(self):
        self.ha.notify_services = ["mobile_app_alice"]
        self.get("/api/me")
        self.add_service()
        r = self.ok(self.post(f"/api/admin/users/{ALICE['id']}/notify-test", user=ADMIN))
        self.assertEqual(r["results"], {"notify.mobile_app_alice": True})
        self.assertEqual(self.post(f"/api/admin/users/{ALICE['id']}/notify-test", user=ADMIN).status_code, 429)
        r = self.ok(self.post("/api/reminders/test"))
        self.assertEqual(r["results"], {"notify.mobile_app_alice": True})
        sent = self.ha.notifications()
        self.assertEqual(len(sent), 2)
        self.assertEqual(sent[0][0], "mobile_app_alice")
        self.assertEqual(sent[0][1]["title"], "Family Tree")
        self.assertEqual(self.ha.requests[-1][3], "Bearer t0ken")

    def test_wrong_service_explains(self):
        self.ha.notify_services = ["mobile_app_alices_phone"]
        self.get("/api/me")
        self.add_service(service="notify.mobile_app_alice")
        r = self.post("/api/reminders/test")
        self.assertEqual(r.status_code, 502)
        self.assertIn("notify.mobile_app_alices_phone", r.json()["detail"])

    def test_entity_fallback(self):
        self.ha.notify_services = []
        self.ha.notify_entities = ["alice_pixel"]
        self.get("/api/me")
        self.add_service(service="notify.alice_pixel")
        self.ok(self.post("/api/reminders/test"))
        self.assertEqual(self.ha.notifications()[-1][0], "send_message")
        self.assertEqual(self.ha.notifications()[-1][1]["entity_id"], "notify.alice_pixel")

    def test_digest_through_home_assistant(self):
        self.ha.notify_services = ["mobile_app_alice"]
        a = self.person("Ann", "Smith", born={"d": 25, "m": 9, "y": 1960})
        self.remind(a)
        self.ready()
        n = reminders.run_digest_pass_blocking(now=self.NOW)
        self.assertEqual(n, 1)
        (svc, body), = self.ha.notifications()
        self.assertEqual((svc, body["title"], body["message"]), ("mobile_app_alice", "Family Tree", "🎂 Ann Smith turns 66 today"))


class PhonesFromHomeAssistant(WithFakeHA):
    """Phones come from Home Assistant (Settings → People → Track device); admins add only extras."""

    def set_people(self):
        self.ha.notify_services = ["mobile_app_pixel_7", "mobile_app_bob_s_phone_2", "family_speaker"]
        self.ha.people = [
            {"entity_id": "person.alice", "name": "Alice", "user_id": ALICE["id"], "state": "home", "picture": None,
             "phones": [{"name": "Pixel 7", "label": "Alice's phone", "tracker": "device_tracker.pixel_7"},
                        {"name": "Old Tablet", "label": "Old Tablet", "tracker": "device_tracker.old_tablet"}]},
            {"entity_id": "person.bob", "name": "Bob", "user_id": BOB["id"], "state": "not_home", "picture": None,
             "phones": [{"name": "Bob's Phone", "label": "Bob's Phone", "tracker": "device_tracker.bobs_phone"}]},
            {"entity_id": "person.kid", "name": "Kid", "user_id": None, "state": "home", "picture": None, "phones": []},
        ]
        ha_people.refresh_blocking(True)

    def test_phone_linked_in_home_assistant_is_used(self):
        for u in (ALICE, BOB, ADMIN):
            self.get("/api/me", user=u)
        self.assertEqual(self.ok(self.get("/api/whoami"))["notifyLinked"], 0)
        self.set_people()
        self.assertEqual(self.ok(self.get("/api/whoami"))["notifyLinked"], 1)
        self.assertEqual(ha_people.phones_for(ALICE["id"]), ["mobile_app_pixel_7"])      # the tablet has no action
        self.assertEqual(ha_people.phones_for(BOB["id"]), ["mobile_app_bob_s_phone_2"])  # HA numbered it
        p = self.prefs(enabled=True)                                                     # no admin step needed
        self.assertEqual((p["enabled"], p["canEnable"], p["services"]), (True, True, ["notify.mobile_app_pixel_7"]))
        self.ha.requests.clear()
        self.assertEqual(self.ok(self.post("/api/reminders/test"))["results"], {"notify.mobile_app_pixel_7": True})
        self.assertEqual([n for n, _ in self.ha.notifications()], ["mobile_app_pixel_7"])
        self.assertEqual(self.ok(self.post("/api/reminders/test", user=BOB))["results"],
                         {"notify.mobile_app_bob_s_phone_2": True})

    def test_digest_goes_to_the_phone(self):
        self.set_people()
        a = self.person("Ann", "Smith", born={"d": 25, "m": 9, "y": 1960})
        self.remind(a)
        self.get("/api/me")
        self.prefs(enabled=True)
        self.ha.requests.clear()
        self.assertEqual(reminders.run_digest_pass_blocking(now=self.NOW), 1)
        self.assertEqual([n for n, _ in self.ha.notifications()], ["mobile_app_pixel_7"])

    def test_no_person_linked(self):
        self.set_people()
        self.get("/api/me", user=ADMIN)
        self.assertEqual(self.ok(self.get("/api/whoami", user=ADMIN))["notifyLinked"], 0)
        r = self.put("/api/reminders/prefs", {"enabled": True}, user=ADMIN)
        self.assertEqual(r.status_code, 409)
        self.assertIn("Settings → People → you → Track device", r.json()["detail"])
        r = self.post("/api/reminders/test", user=ADMIN)
        self.assertEqual(r.status_code, 400)
        self.assertIn("Track device", r.json()["detail"])
        r = self.post(f"/api/admin/users/{ADMIN['id']}/notify-test", user=ADMIN)
        self.assertEqual(r.status_code, 400)
        self.assertIn("no phone linked in Home Assistant", r.json()["detail"])
        admin = {u["id"]: u for u in self.ok(self.get("/api/admin/users", user=ADMIN))}[ADMIN["id"]]["ha"]
        self.assertEqual(admin, {"known": True, "person": None, "personName": None, "phones": []})

    def test_admin_sees_phones_and_adds_extras(self):
        self.get("/api/me")
        self.set_people()
        users = {u["id"]: u for u in self.ok(self.get("/api/admin/users", user=ADMIN))}
        a = users[ALICE["id"]]["ha"]
        self.assertEqual((a["known"], a["person"], a["personName"]), (True, "person.alice", "Alice"))
        self.assertEqual([(p["label"], p["service"], p["tracker"]) for p in a["phones"]],
                         [("Alice's phone", "notify.mobile_app_pixel_7", "device_tracker.pixel_7"),
                          ("Old Tablet", None, "device_tracker.old_tablet")])
        self.assertEqual(users[ALICE["id"]]["notify"], [])                   # extras only
        self.add_service(service="notify.family_speaker")                    # an extra, added in this app
        self.ha.requests.clear()
        r = self.ok(self.post(f"/api/admin/users/{ALICE['id']}/notify-test", user=ADMIN))
        self.assertEqual(set(r["results"]), {"notify.mobile_app_pixel_7", "notify.family_speaker"})
        self.assertEqual(sorted(n for n, _ in self.ha.notifications()), ["family_speaker", "mobile_app_pixel_7"])
        reminders_router._last_test.clear()
        r = self.ok(self.post(f"/api/admin/users/{ALICE['id']}/notify-test", {"service": "notify.mobile_app_pixel_7"},
                              user=ADMIN))
        self.assertEqual(r["results"], {"notify.mobile_app_pixel_7": True})
        # the phone can't be removed here (it's Home Assistant's), only extras
        self.assertEqual(self.delete(f"/api/admin/users/{ALICE['id']}/notify/notify.mobile_app_pixel_7",
                                     user=ADMIN).status_code, 404)

    def test_follows_home_assistant_and_survives_it_being_down(self):
        self.get("/api/me")
        self.assertFalse(self.ok(self.get("/api/admin/users", user=ADMIN))[0]["ha"]["known"])   # never read yet
        self.set_people()
        self.ha.fail = True
        ha_people.refresh_blocking(True)                                    # HA down: the last answer stays
        self.ha.fail = False
        self.assertEqual(ha_people.phones_for(ALICE["id"]), ["mobile_app_pixel_7"])
        self.ha.people[0]["phones"] = []                                     # phone removed in HA
        users = {u["id"]: u for u in self.ok(self.get("/api/admin/users?refresh=1", user=ADMIN))}
        self.assertEqual(users[ALICE["id"]]["ha"]["phones"], [])
        self.assertEqual(ha_people.phones_for(ALICE["id"]), [])
        self.assertEqual(self.post("/api/reminders/test").status_code, 400)


# ---------------------------------------------------------------------------
class Digest(ReminderCase):
    def test_nothing_until_both_switches_are_on(self):
        a = self.person("Ann", "Smith", born={"d": 25, "m": 9, "y": 1960})
        self.get("/api/me")
        self.add_service()
        self.assertEqual(self.run_pass()[0], 0)              # user's reminders off
        self.prefs(enabled=True)
        self.assertEqual(self.run_pass()[0], 0)              # Ann's 🔔 off
        self.remind(a)
        n, s = self.run_pass()
        self.assertEqual(n, 1)
        self.assertEqual(s.sent, [("mobile_app_alice", "Family Tree", "🎂 Ann Smith turns 66 today")])

    def test_birthday_wording(self):
        me = self.person("Alice", "Smith", gender="female", born={"d": 1, "m": 1, "y": 1990})
        mum = self.person("Mary", "Smith", gender="female", born={"d": 25, "m": 9, "y": 1960})
        self.family(None, mum, children=[me])
        bob = self.person("Bob", "Jones", born={"d": 26, "m": 9})                 # year unknown
        cat = self.person("Cat", "Jones", born={"d": 1, "m": 10, "y": 2000})
        for p in (mum, bob, cat):
            self.remind(p)
        self.set_me(me)
        self.ready(leadDays=7)
        self.assertEqual(self.digest(), "\n".join([
            "🎂 Mary Smith (your mother) turns 66 today",
            "🎂 It's Bob Jones's birthday tomorrow",
            "🎂 Cat Jones turns 26 on Thu 1 Oct",
        ]))

    def test_relationship_in_your_language(self):
        me = self.person("Alice", gender="female", born={"d": 1, "m": 1, "y": 1990})
        mum = self.person("Mary", gender="female", born={"d": 25, "m": 9, "y": 1960})
        self.family(None, mum, children=[me])
        self.remind(mum)
        self.set_me(me)
        self.ready()
        self.ok(self.put("/api/me/kin-lang", {"kinLang": "te"}))
        self.assertEqual(self.digest(), "🎂 Mary (your Amma) turns 66 today")

    def test_lead_days_window(self):
        a = self.person("Ann", born={"d": 2, "m": 10, "y": 1960})       # 7 days away
        self.remind(a)
        self.ready(leadDays=6)
        self.assertIsNone(self.digest())
        self.prefs(leadDays=7)
        self.assertEqual(self.digest(), "🎂 Ann turns 66 on Fri 2 Oct")

    def test_anniversaries(self):
        a = self.person("Ravi", born={"d": 1, "m": 1, "y": 1970})
        b = self.person("Priya", born={"d": 1, "m": 1, "y": 1972})
        c = self.person("Old", born={"d": 1, "m": 1, "y": 1940})
        d = self.person("Gone", born={"d": 1, "m": 1, "y": 1940}, deceased=True)
        e = self.person("Ex1")
        f = self.person("Ex2")
        for x, y, ended in ((a, b, None), (c, d, None), (e, f, "divorced")):
            fid = self.ok(self.post("/api/families", {"partner1Id": x, "partner2Id": y, "kind": "married",
                                                      "marriage": {"date": {"d": 25, "m": 9, "y": 2001}}}), 201)["family"]["id"]
            if ended:
                self.ok(self.patch(f"/api/families/{fid}", {"ended": ended}))
        for p in (b, c, e):                                    # one partner of each couple
            self.remind(p)
        self.ready()
        self.assertEqual(self.digest(), "💍 Ravi & Priya — 25th anniversary today 🎉 Silver wedding anniversary")
        self.prefs(anniversaries=False)
        self.assertIsNone(self.digest())

    def test_anniversary_without_year(self):
        a = self.person("Ravi")
        b = self.person("Priya")
        self.ok(self.post("/api/families", {"partner1Id": a, "partner2Id": b, "kind": "married",
                                            "marriage": {"date": {"d": 25, "m": 9}}}), 201)
        self.remind(a)
        self.ready()
        self.assertEqual(self.digest(), "💍 Ravi & Priya — anniversary today")

    def test_remembrance_is_opt_in(self):
        g = self.person("Venkat", born={"d": 25, "m": 9, "y": 1926}, died={"d": 26, "m": 9, "y": 2001})
        self.remind(g)
        self.ready(leadDays=1)
        self.assertIsNone(self.digest())
        self.prefs(remembrance=True)
        self.assertEqual(self.digest(), "🕯 Remembering Venkat, born 100 years ago today\n"
                                        "🕯 Remembering Venkat, who died 25 years ago tomorrow")
        self.prefs(birthdays=False)                            # remembrance is its own switch
        self.assertIn("born 100 years ago", self.digest())

    def test_remembrance_without_years(self):
        g = self.person("Venkat", born={"d": 25, "m": 9}, deceased=True)
        self.remind(g)
        self.ready(remembrance=True)
        self.assertEqual(self.digest(), "🕯 Remembering Venkat — their birthday, today")

    def test_content_rule(self):
        a = self.person("Ann", "Smith", born={"d": 25, "m": 9, "y": 1960},
                        biography="SECRET BIO", nickname="Annie")
        self.ok(self.patch(f"/api/people/{a}", {"birth": {"date": {"d": 25, "m": 9, "y": 1960}, "place": "Hyderabad"}}))
        self.ok(self.post(f"/api/people/{a}/stories", {"title": "T", "body": "STORY BODY"}), 201)
        self.remind(a)
        self.ready()
        msg = self.digest()
        for secret in ("SECRET", "Hyderabad", "STORY", "Annie"):
            self.assertNotIn(secret, msg)

    def test_trashed_people_never(self):
        a = self.person("Ann", born={"d": 25, "m": 9, "y": 1960})
        self.remind(a)
        self.ready()
        self.ok(self.delete(f"/api/people/{a}"))
        self.assertIsNone(self.digest())

    def test_disabled_user_never(self):
        a = self.person("Ann", born={"d": 25, "m": 9, "y": 1960})
        self.remind(a)
        self.ready()
        self.ok(self.patch(f"/api/users/{ALICE['id']}", {"disabled": True}, user=ADMIN))
        self.assertEqual(self.run_pass()[0], 0)

    def test_once_a_day_in_the_hour_after_the_time(self):
        a = self.person("Ann", born={"d": 25, "m": 9, "y": 1960})
        self.remind(a)
        self.ready(time="08:00")
        early = datetime(2026, 9, 25, 7, 59, tzinfo=TZ)
        late = datetime(2026, 9, 25, 9, 0, tzinfo=TZ)
        self.assertEqual(self.run_pass(now=early)[0], 0)
        self.assertEqual(self.run_pass(now=late)[0], 0)
        self.assertEqual(self.run_pass()[0], 1)
        self.assertEqual(self.run_pass(now=datetime(2026, 9, 25, 8, 30, tzinfo=TZ))[0], 0)   # logged
        self.assertEqual(sql("SELECT user_id, sent_on FROM reminder_log"), [{"user_id": ALICE["id"], "sent_on": "2026-09-25"}])
        self.prefs(time="21:15")
        self.assertEqual(self.run_pass(now=datetime(2026, 9, 26, 21, 20, tzinfo=TZ))[0], 0)  # nothing on the 26th

    def test_each_user_their_own_time(self):
        a = self.person("Ann", born={"d": 25, "m": 9, "y": 1960})
        self.remind(a)
        self.ready(ALICE, time="08:00")
        self.ready(BOB, time="18:30")
        n, s = self.run_pass()
        self.assertEqual([x[0] for x in s.sent], ["mobile_app_alice"])
        n, s = self.run_pass(now=datetime(2026, 9, 25, 18, 45, tzinfo=TZ))
        self.assertEqual([x[0] for x in s.sent], ["mobile_app_bob"])

    def test_failed_send_is_retried(self):
        a = self.person("Ann", born={"d": 25, "m": 9, "y": 1960})
        self.remind(a)
        self.ready()
        self.assertEqual(self.run_pass(sender=Sender(ok=False))[0], 0)
        self.assertEqual(sql("SELECT * FROM reminder_log"), [])
        self.assertEqual(self.run_pass()[0], 1)

    def test_every_service_gets_it(self):
        a = self.person("Ann", born={"d": 25, "m": 9, "y": 1960})
        self.remind(a)
        self.ready()
        self.add_service(ALICE, "notify.alice_tablet")
        n, s = self.run_pass(sender=Sender(ok={"alice_tablet": False}))
        self.assertEqual(n, 1)                                 # one success is enough to log it
        self.assertEqual(sorted(x[0] for x in s.sent), ["alice_tablet", "mobile_app_alice"])

    def test_no_services_no_send(self):
        a = self.person("Ann", born={"d": 25, "m": 9, "y": 1960})
        self.remind(a)
        self.ready()
        self.ok(self.delete(f"/api/admin/users/{ALICE['id']}/notify/notify.mobile_app_alice", user=ADMIN))
        self.assertEqual(self.run_pass()[0], 0)

    def test_long_lists_are_cut(self):
        for i in range(reminders.MAX_LINES + 3):
            self.remind(self.person(f"P{i:02d}", born={"d": 25, "m": 9, "y": 1960}))
        self.ready()
        lines = self.digest().split("\n")
        self.assertEqual(len(lines), reminders.MAX_LINES + 1)
        self.assertEqual(lines[-1], "…and 3 more")

    def test_29_february(self):
        a = self.person("Leap", born={"d": 29, "m": 2, "y": 2000})
        self.remind(a)
        self.ready()
        self.assertEqual(self.digest(now=datetime(2027, 2, 28, 8, 5, tzinfo=TZ)), "🎂 Leap turns 27 today")
        self.assertIsNone(self.digest(now=datetime(2028, 2, 28, 8, 5, tzinfo=TZ)))
        self.assertEqual(self.digest(now=datetime(2028, 2, 29, 8, 5, tzinfo=TZ)), "🎂 Leap turns 28 today")

    def test_scope_close(self):
        me = self.person("Alice", born={"d": 1, "m": 1, "y": 1990})
        mum = self.person("Mum", gender="female", born={"d": 25, "m": 9, "y": 1960})
        far = self.person("Stranger", born={"d": 25, "m": 9, "y": 1961})
        self.family(None, mum, children=[me])
        self.remind(mum)
        self.remind(far)
        self.set_me(me)
        self.ready()
        self.assertEqual(self.digest().count("\n"), 1)         # both: scope "all" is the default
        self.prefs(scope="close")
        self.assertEqual(self.digest(), "🎂 Mum (your mother) turns 66 today")
        self.set_me(None)                                      # close without "me": nothing, never everyone
        self.assertIsNone(self.digest())
        self.assertIn("This is me", self.prefs()["warning"])


# ---------------------------------------------------------------------------
class CloseFamily(ReminderCase):
    def build(self):
        self.me = self.person("Me")
        self.mum = self.person("Mum", gender="female")
        self.dad = self.person("Dad", gender="male")
        self.gran = self.person("Gran", gender="female", deceased=True)
        self.aunt = self.person("Aunt", gender="female")
        self.cousin = self.person("Cousin")
        self.cousin_kid = self.person("CousinKid")
        self.family(self.dad, self.mum, children=[self.me])
        self.family(None, self.gran, children=[self.mum, self.aunt])
        self.family(None, self.aunt, children=[self.cousin])
        self.family(self.cousin, None, children=[self.cousin_kid])

    def test_needs_me(self):
        self.assertFalse(self.ok(self.get("/api/reminders/close-family"))["meSet"])
        self.assertEqual(self.post("/api/reminders/close-family").status_code, 422)

    def test_turns_on_close_family_in_one_batch(self):
        self.build()
        self.set_me(self.me)
        self.remind(self.mum)
        pre = self.ok(self.get("/api/reminders/close-family"))
        # me, mum, dad (1); gran, aunt (2); cousin (3) — living and deceased; cousin's child is 4 steps away
        self.assertEqual((pre["total"], pre["off"]), (6, 5))
        self.assertEqual(self.prefs()["closeFamily"], {"total": 6, "off": 5})
        r = self.ok(self.post("/api/reminders/close-family"))
        self.assertEqual(r["count"], 5)
        on = {x["id"] for x in sql("SELECT id FROM people WHERE remind = 1")}
        self.assertEqual(on, {self.me, self.mum, self.dad, self.gran, self.aunt, self.cousin})
        self.assertEqual(sql("SELECT label FROM batches WHERE id = ?", (r["batchId"],))[0]["label"],
                         "Turned reminders on for close family (5 people)")
        self.assertEqual(self.ok(self.post("/api/reminders/close-family")), {"count": 0, "batchId": None})
        self.ok(self.post(f"/api/history/{r['batchId']}/undo"))
        self.assertEqual({x["id"] for x in sql("SELECT id FROM people WHERE remind = 1")}, {self.mum})


# ---------------------------------------------------------------------------
class Upgrade(ReminderCase):
    def test_old_database_gets_the_column_off(self):
        a = self.person("Ann")
        conn = sqlite3.connect(config.DB_PATH)
        try:
            conn.execute("ALTER TABLE people DROP COLUMN remind")
            conn.execute("DROP TABLE reminder_prefs")
            conn.execute("DROP TABLE user_notify")
            conn.commit()
        finally:
            conn.close()
        db.init_db()
        self.assertEqual(sql("SELECT remind FROM people WHERE id = ?", (a,)), [{"remind": 0}])
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM reminder_prefs"), [{"n": 0}])

    def test_undo_of_a_change_made_before_the_column_existed(self):
        a = self.person("Ann")
        res = self.ok(self.patch(f"/api/people/{a}", {"nickname": "Annie"}))
        # rewrite the snapshots as an older version made them (no `remind`)
        for ch in sql("SELECT id, before, after FROM changes WHERE batch_id = ?", (res["batchId"],)):
            before, after = json.loads(ch["before"]), json.loads(ch["after"])
            before.pop("remind", None)
            after.pop("remind", None)
            sql("UPDATE changes SET before = ?, after = ? WHERE id = ?", (json.dumps(before), json.dumps(after), ch["id"]))
        self.ok(self.post(f"/api/history/{res['batchId']}/undo"))
        self.assertIsNone(self.detail(a)["nickname"])


if __name__ == "__main__":
    unittest.main()
