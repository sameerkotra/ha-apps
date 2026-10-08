"""The morning briefing (app/briefing.py; HOUSEHOLD_ASSISTANT_SPEC.md §16): each person's own settings; it asks the
apps as the person — only the tools they may use — and needs no AI model; parts with nothing in them are left out;
it shows in their questions and goes to their phones from Home Assistant; it is sent once a day, from the chosen time
until three hours after it, on the chosen days; never for someone the assistant is off for.

All people, tasks and amounts here are invented."""
import _env  # noqa: F401  (must be first)

from datetime import datetime
from unittest import mock

from test_assistant import ADMIN, ALICE, BOB, Household

from app import ai_client, briefing, config, db, settings


def at(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=config.ZONE.tz)


class BriefingTests(Household):
    BILLS = True

    def phones(self, uid):
        return {"u_alice": [{"service": "mobile_app_alice_phone", "label": "Alice's phone"}]}.get(uid, [])

    def send_now(self, who=ALICE):
        sent = []
        with mock.patch.object(briefing, "phones", self.phones), \
                mock.patch.object(briefing.ha_notify, "send_to_services",
                                  side_effect=lambda svcs, title, msg, data=None: sent.append((svcs, title, msg, data))
                                  or {s: True for s in svcs}), \
                mock.patch.object(ai_client, "generate", side_effect=AssertionError("no model is needed")):
            r = self.c.post("/api/briefing/send", headers=who)
        return r, sent

    def test_settings_are_each_persons_own(self):
        b = self.c.get("/api/briefing", headers=ALICE).json()
        self.assertEqual((b["on"], b["time"], b["days"]), (False, "07:30", "every"))
        self.assertEqual([p["tool"] for p in b["parts"]], ["todo.tasks", "finance.bills"])
        self.assertEqual(b["phones"], [])
        r = self.c.put("/api/briefing", json={"on": True, "time": "06:45", "days": "weekdays"}, headers=ALICE)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["on"], r.json()["time"], r.json()["days"]), (True, "06:45", "weekdays"))
        self.assertFalse(self.c.get("/api/briefing", headers=BOB).json()["on"])
        for bad in ({"on": True, "time": "7:30", "days": "every"}, {"on": True, "time": "25:00", "days": "every"},
                    {"on": True, "time": "07:30", "days": "sundays"}):
            self.assertEqual(self.c.put("/api/briefing", json=bad, headers=ALICE).status_code, 422, bad)
        self.assertEqual(self.c.put("/api/briefing", json={"on": "yes", "time": "07:30", "days": "every"},
                                    headers=ALICE).status_code, 422)

    def test_send_now_asks_the_apps_as_the_person(self):
        r, sent = self.send_now()
        self.assertEqual(r.status_code, 200, r.text)
        q = r.json()["question"]
        self.assertEqual((q["text"], q["state"]), ("Morning briefing", "done"))
        self.assertIn("📋 2 tasks today: Bins, Call plumber.", q["answer"])
        self.assertNotIn("💳", q["answer"])                          # no bills: left out
        self.assertEqual(sorted(s["app"] for s in q["sources"]), ["finance", "household_todo"])   # every app asked
        self.assertEqual(sorted(c["tool"] for c in q["shared"]), ["finance.bills", "todo.tasks"])
        self.assertEqual(self.apps["todo"].calls[-1], {"tool": "todo.tasks", "by": "u_alice", "args": {"when": "today"}})
        self.assertEqual(self.apps["finance"].calls[-1]["args"], {"days": 3})
        (svcs, title, msg, _data), = sent
        self.assertEqual(svcs, ["mobile_app_alice_phone"])
        self.assertTrue(title.startswith("🌅 Morning briefing · "))
        self.assertEqual(msg, "📋 2 tasks today: Bins, Call plumber.")
        self.assertEqual(r.json()["sent"], {"mobile_app_alice_phone": True})
        hist = self.c.get("/api/history", headers=ALICE).json()["questions"]
        self.assertEqual(hist[0]["id"], q["id"])                     # in her questions
        self.assertEqual(self.c.get("/api/history", headers=BOB).json()["questions"], [])

    def test_parts_with_something_in_them(self):
        self.apps["finance"].bills = [("2026-10-09", "WATER CO", 42.0)]
        r, sent = self.send_now()
        self.assertIn("💳 Coming up: 2026-10-09 WATER CO 42.00.", r.json()["question"]["answer"])
        self.assertEqual(sent[0][2].splitlines()[1], "💳 Coming up: 2026-10-09 WATER CO 42.00.")
        r, sent = self.send_now(BOB)                                  # no phone: made, nothing sent
        self.assertEqual((r.json()["phones"], sent), ([], []))

    def test_only_what_the_person_may_use(self):
        self.apps["finance"].person_off.add("u_alice")
        r, _ = self.send_now()
        call = next(c for c in r.json()["question"]["shared"] if c["tool"] == "finance.bills")
        self.assertEqual(call["state"], "nack")                       # Finance said no: nothing from it
        self.assertNotIn("💳", r.json()["question"]["answer"])
        with db.get_conn() as conn:
            conn.execute("UPDATE assist_apps SET enabled = 0 WHERE app = 'finance'")
        self.assertEqual([p["tool"] for p in self.c.get("/api/briefing", headers=ALICE).json()["parts"]],
                         ["todo.tasks"])

    def test_who_may_have_one(self):
        self.c.put("/api/admin/people/u_bob", json={"enabled": False}, headers=ADMIN)
        self.assertEqual(self.c.put("/api/briefing", json={"on": True, "time": "07:30", "days": "every"},
                                    headers=BOB).status_code, 403)
        self.assertEqual(self.send_now(BOB)[0].status_code, 403)
        self.assertIn("turned the assistant off", self.c.get("/api/briefing", headers=BOB).json()["why"])
        self.c.put("/api/admin/people/u_bob", json={"enabled": True, "isChild": True}, headers=ADMIN)
        settings.update({"children_may_ask": False}, None)
        self.assertEqual(self.send_now(BOB)[0].status_code, 403)

    def test_sent_once_a_day_at_the_time(self):
        for who in (ALICE, BOB):
            self.c.put("/api/briefing", json={"on": True, "time": "07:30", "days": "weekdays"}, headers=who)
        self.c.put("/api/admin/people/u_bob", json={"enabled": False}, headers=ADMIN)
        sent = []

        def check(now):
            with mock.patch.object(config, "now", return_value=now), \
                    mock.patch.object(briefing, "send", side_effect=lambda u: sent.append((u["id"], now)) or {"sent": {}}):
                n = briefing.check()
                for t in list(briefing.threading.enumerate()):
                    if t.name.startswith("briefing-"):
                        t.join(5)
                return n
        self.assertEqual(check(at(2026, 10, 7, 7, 29)), 0)            # Wednesday, a minute early
        self.assertEqual(check(at(2026, 10, 7, 7, 30)), 1)            # Alice only: Bob's assistant is off
        self.assertEqual(check(at(2026, 10, 7, 7, 31)), 0)            # once a day
        self.assertEqual([u for u, _ in sent], ["u_alice"])
        self.assertEqual(check(at(2026, 10, 10, 7, 30)), 0)           # Saturday: weekdays only
        self.assertEqual(check(at(2026, 10, 12, 10, 31)), 0)          # Monday, more than 3 hours late
        self.assertEqual(check(at(2026, 10, 13, 9, 0)), 1)            # Tuesday, started late but in time
        self.c.put("/api/briefing", json={"on": False, "time": "07:30", "days": "weekdays"}, headers=ALICE)
        self.assertEqual(check(at(2026, 10, 14, 7, 30)), 0)
